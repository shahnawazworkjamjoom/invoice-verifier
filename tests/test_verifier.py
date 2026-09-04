from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from invoice_verifier.models import InvoiceRecord
from invoice_verifier.verifier import verify_file


def record() -> InvoiceRecord:
    return InvoiceRecord(
        excel_row=2, supplier="BARAKAT", brand="Subway", location="Test shop",
        order_number="PO202609-75686", invoice_number="1102-SOIN-07863229",
        currency="AED", po_amount=426.44, invoice_date="02-Sep-2026",
        attachment_url="https://example.invalid/invoice.pdf", unique_reference="test",
        record_id="1", payment_status="FULL", amount_to_pay=447.77,
        received_qty=76, tax_code="UAE_VAT_INC_5%",
    )


def vision_result(decision="APPROVE", reason="All critical fields match"):
    return {
        "decision": decision,
        "decision_reasons": [reason],
        "checks": {
            "invoice_number": {
                "expected": "1102-SOIN-07863229", "found": "1102-SOIN-07863229",
                "match": decision == "APPROVE", "evidence": "Visible in invoice header",
            }
        },
    }


class VerifierTests(unittest.TestCase):
    def run_with(self, model_result, source_record=None):
        source_record = source_record or record()
        with TemporaryDirectory() as folder:
            source = Path(folder) / "invoice.pdf"
            source.write_bytes(b"placeholder")
            with patch("invoice_verifier.verifier.vision_is_configured", return_value=True), \
                    patch("invoice_verifier.verifier.extract_invoice_json",
                          return_value=model_result) as extract:
                result = verify_file(source_record, source)
            extract.assert_called_once_with(source, result.record)
            return result

    def test_uses_excel_expected_values_and_approves_vat_amount_correction(self):
        source_record = record()
        source_record.location = "11500085-TWOFOUR 54"
        source_record.order_number = "PO202609-76269"
        source_record.invoice_number = "1102-SOIN-07868103"
        source_record.invoice_date = "03-Sep-2026"
        source_record.po_amount = 10.50
        source_record.amount_to_pay = 10.50
        model_result = {
            "decision": "DECLINE",
            "decision_reasons": [
                "Invoice date is 03-Sep-2026, but the expected date is 03-Sep-2026"
            ],
            "vendor_name": "Barakat Quality Plus (L.L.C.)",
            "location": "11500085-TWOFOUR 54",
            "invoice_number": "1102-SOIN-07868103",
            "order_number": "PO202609-76269",
            "invoice_date": "03-Sep-2026",
            "currency": "AED",
            "net_amount": 10.50,
            "vat_rate_percent": 5,
            "final_amount_due": 11.03,
            "checks": {
                "invoice_number": {
                    "expected": "PO202609-76269", "found": "1102-SOIN-07868103",
                    "match": False, "evidence": "Invoice header",
                }
            },
        }
        result = self.run_with(model_result, source_record)
        self.assertEqual("APPROVE", result.decision)
        self.assertEqual(11.03, result.corrected_amount_to_pay)
        self.assertIn("expected 1102-SOIN-07868103", result.checks["Invoice Number"])
        self.assertIn("match", result.checks["Invoice Date"])

    def test_uses_footer_net_and_updates_barakat_final_amount(self):
        source_record = record()
        source_record.location = "11500103-MAZYAD MALL"
        source_record.order_number = "PO202609-75957"
        source_record.invoice_number = "1102-SOIN-07863123"
        source_record.invoice_date = "02-Sep-2026"
        source_record.po_amount = 240.63
        source_record.amount_to_pay = 240.63
        model_result = {
            "decision": "APPROVE", "decision_reasons": ["Invoice fields match"],
            "vendor_name": "The invoice supplier is Barakat Quality Plus (L.L.C.)",
            "location": ("The invoice's location, 11500103-MAZAYAD MALL-AUH, does not match "
                         "the expected location, 11500103-MAZYAD MALL"),
            "invoice_number": ("The invoice number, 1102-SOIN-07863123, matches the expected "
                               "invoice number"),
            "order_number": "The customer reference is PO202609-75957 and it matches",
            "invoice_date": "The invoice date is 02-Sep-2026 and matches the expected date",
            "currency": "The invoice total is denominated in AED and matches",
            "net_amount": 239.55, "vat_amount": 12.05,
            "vat_rate_percent": 12.05, "final_amount_due": 252.68,
            "checks": {},
        }
        result = self.run_with(model_result, source_record)
        self.assertEqual("APPROVE", result.decision)
        self.assertEqual(252.68, result.corrected_amount_to_pay)
        self.assertIn("match; expected 240.63; found 240.63", result.checks["Po Amount"])
        self.assertIn("match", result.checks["Vat"])

    def test_uses_llm_approve_decision_without_local_rules(self):
        result = self.run_with(vision_result())
        self.assertEqual("APPROVE", result.decision)
        self.assertIn("All critical fields match", result.remarks)
        self.assertIn("local OCR disabled", result.checks["NVIDIA Vision"])

    def test_uses_llm_decline_decision(self):
        result = self.run_with(vision_result("DECLINE", "Invoice amount differs"))
        self.assertEqual("DECLINE", result.decision)
        self.assertIn("Critical invoice field mismatch", result.remarks)

    def test_amount_only_mismatch_is_corrected_and_approved(self):
        model_result = vision_result("DECLINE", "Finance Amount To Pay differs")
        model_result["final_amount_due"] = 252.68
        model_result["total_amount"] = 252.68
        model_result["checks"] = {
            name: {"expected": "expected", "found": "found", "match": True,
                   "evidence": "invoice evidence matches"}
            for name in ("supplier", "location", "invoice_number", "order_number",
                         "invoice_date", "po_amount", "currency", "vat")
        }
        model_result["checks"]["amount_to_pay"] = {
            "expected": 240.63, "found": 252.68, "match": False,
            "evidence": "final amount due includes VAT",
        }
        result = self.run_with(model_result)
        self.assertEqual("APPROVE", result.decision)
        self.assertEqual(252.68, result.corrected_amount_to_pay)
        self.assertIn("corrected", result.remarks)

    def test_approved_result_corrects_amount_when_optional_checks_are_omitted(self):
        model_result = vision_result("APPROVE", "Critical invoice fields match")
        model_result["final_amount_due"] = 252.68
        model_result["total_amount"] = 252.68
        result = self.run_with(model_result)
        self.assertEqual("APPROVE", result.decision)
        self.assertEqual(252.68, result.corrected_amount_to_pay)

    def test_overrides_format_decline_when_every_critical_check_matches(self):
        model_result = vision_result("DECLINE", "Invoice date is not in the expected format")
        model_result["final_amount_due"] = 447.77
        model_result["total_amount"] = 447.77
        model_result["checks"] = {
            name: {"expected": "02-Sep-2026", "found": "02-Sep-2026", "match": True,
                   "evidence": "Invoice evidence matches"}
            for name in ("supplier", "location", "invoice_number", "order_number",
                         "invoice_date", "po_amount", "currency", "vat", "amount_to_pay")
        }
        result = self.run_with(model_result)
        self.assertEqual("APPROVE", result.decision)
        self.assertIsNone(result.corrected_amount_to_pay)
        self.assertIn("All critical invoice fields", result.remarks)

    def test_declines_when_api_key_is_missing_without_calling_model(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "invoice.pdf"
            source.write_bytes(b"placeholder")
            with patch("invoice_verifier.verifier.vision_is_configured", return_value=False), \
                    patch("invoice_verifier.verifier.extract_invoice_json") as extract:
                result = verify_file(record(), source)
        self.assertEqual("DECLINE", result.decision)
        self.assertIn("NVIDIA_API_KEY", result.remarks)
        extract.assert_not_called()

    def test_declines_when_vision_request_fails(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "invoice.pdf"
            source.write_bytes(b"placeholder")
            with patch("invoice_verifier.verifier.vision_is_configured", return_value=True), \
                    patch("invoice_verifier.verifier.extract_invoice_json",
                          side_effect=RuntimeError("rate limited")):
                result = verify_file(record(), source)
        self.assertEqual("DECLINE", result.decision)
        self.assertIn("rate limited", result.remarks)


if __name__ == "__main__":
    unittest.main()
