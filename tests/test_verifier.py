from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from invoice_verifier.models import InvoiceRecord
from invoice_verifier.verifier import verify_file


def record() -> InvoiceRecord:
    return InvoiceRecord(
        excel_row=2, supplier="BARAKAT", brand="Subway", location="11500103-MAZYAD MALL",
        order_number="PO202609-75957", invoice_number="1102-SOIN-07863123",
        currency="AED", po_amount=240.63, invoice_date="02-Sep-2026",
        attachment_url="https://example.invalid/invoice.pdf", unique_reference="test",
        record_id="1", payment_status="FULL", amount_to_pay=240.63,
        received_qty=33, tax_code="UAE_VAT_INC_5%",
    )


def ocr_result(order="PO202609-75957", amount=252.68, raw_text=None):
    return {
        "order_number": order,
        "amount_total": amount,
        "raw_text": raw_text if raw_text is not None else f"Customer reference {order}",
        "ocr_method": "ocr-rapidocr",
        "ocr_confidence": 0.968,
    }


class VerifierTests(unittest.TestCase):
    def run_with(self, extracted, source_record=None):
        source_record = source_record or record()
        with TemporaryDirectory() as folder:
            source = Path(folder) / "invoice.pdf"
            source.write_bytes(b"placeholder")
            with patch("invoice_verifier.verifier.extract_document", return_value=extracted) as ocr:
                result = verify_file(source_record, source)
            ocr.assert_called_once()
            return result

    def test_different_amount_declines_without_correction(self):
        result = self.run_with(ocr_result())
        self.assertEqual("DECLINE", result.decision)
        self.assertIsNone(result.corrected_amount_to_pay)
        self.assertEqual(240.63, result.record.amount_to_pay)

    def test_exact_amount_approves_regardless_of_order(self):
        for order in ("PO202609-75957", "WRONG", "", None):
            with self.subTest(order=order):
                result = self.run_with(ocr_result(order=order, amount=240.63))
                self.assertEqual("APPROVE", result.decision)
                self.assertIsNone(result.corrected_amount_to_pay)
                self.assertNotIn("Order Number", result.checks)

    def test_no_tolerance_or_rounding(self):
        for amount in (240.64, 240.62, 240.631, 240.629, "240.6300000001"):
            with self.subTest(amount=amount):
                result = self.run_with(ocr_result(amount=amount))
                self.assertEqual("DECLINE", result.decision)
                self.assertIsNone(result.corrected_amount_to_pay)

    def test_equivalent_number_formatting_matches(self):
        source = record()
        source.amount_to_pay = 1240.6
        result = self.run_with(ocr_result(amount="1,240.600"), source)
        self.assertEqual("APPROVE", result.decision)

    def test_invalid_final_amount_declines(self):
        for amount in (None, "", 0, -1, "invalid", "NaN", "Infinity"):
            with self.subTest(amount=amount):
                result = self.run_with(ocr_result(amount=amount))
                self.assertEqual("DECLINE", result.decision)
                self.assertIn("final amount", result.remarks)

    def test_invalid_excel_amount_declines(self):
        for amount in (None, "", 0, -1, "invalid", "NaN", "Infinity"):
            with self.subTest(amount=amount):
                source = record()
                source.amount_to_pay = amount
                result = self.run_with(ocr_result(amount=240.63), source)
                self.assertEqual("DECLINE", result.decision)
                self.assertIn("missing or invalid", result.remarks)

    def test_ocr_failure_declines(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "invoice.pdf"
            source.write_bytes(b"placeholder")
            with patch("invoice_verifier.verifier.extract_document", side_effect=RuntimeError("failed")):
                result = verify_file(record(), source)
        self.assertEqual("DECLINE", result.decision)
        self.assertIn("OCR failed", result.remarks)


if __name__ == "__main__":
    unittest.main()
