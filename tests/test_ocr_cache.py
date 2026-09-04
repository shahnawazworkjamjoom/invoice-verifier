from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from invoice_verifier.models import InvoiceRecord
from invoice_verifier.verifier import verify_file


class OcrDisabledTests(unittest.TestCase):
    def test_local_ocr_code_is_retained_but_not_called(self):
        record = InvoiceRecord(
            excel_row=2, supplier="BARAKAT", brand="", location="",
            order_number="PO-1", invoice_number="INV-1", currency="AED",
            po_amount=100, invoice_date="2026-09-02", attachment_url="",
            unique_reference="test", record_id="1", payment_status="FULL",
            amount_to_pay=105, received_qty=None, tax_code="5%",
        )
        model_result = {
            "decision": "APPROVE", "decision_reasons": ["Match"], "checks": {}
        }
        with TemporaryDirectory() as folder:
            source = Path(folder) / "invoice.pdf"
            source.write_bytes(b"placeholder")
            with patch("invoice_verifier.ocr.extract_document") as ocr, \
                    patch("invoice_verifier.verifier.vision_is_configured", return_value=True), \
                    patch("invoice_verifier.verifier.extract_invoice_json", return_value=model_result):
                result = verify_file(record, source)
        self.assertEqual("APPROVE", result.decision)
        ocr.assert_not_called()


if __name__ == "__main__":
    unittest.main()
