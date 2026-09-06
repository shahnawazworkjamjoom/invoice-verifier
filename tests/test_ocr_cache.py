from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from invoice_verifier.models import InvoiceRecord
from invoice_verifier.verifier import verify_file


class OcrActiveTests(unittest.TestCase):
    def test_local_ocr_is_the_active_verification_path(self):
        record = InvoiceRecord(
            excel_row=2, supplier="BARAKAT", brand="", location="",
            order_number="PO-1", invoice_number="INV-1", currency="AED",
            po_amount=100, invoice_date="2026-09-02", attachment_url="",
            unique_reference="test", record_id="1", payment_status="FULL",
            amount_to_pay=105, received_qty=None, tax_code="5%",
        )
        extracted = {
            "order_number": "PO-1", "amount_total": 105,
            "raw_text": "Customer reference PO-1",
            "ocr_method": "rapidocr", "ocr_confidence": 0.95,
        }
        with TemporaryDirectory() as folder:
            source = Path(folder) / "invoice.pdf"
            source.write_bytes(b"placeholder")
            with patch("invoice_verifier.verifier.extract_document", return_value=extracted) as ocr:
                result = verify_file(record, source)
        self.assertEqual("APPROVE", result.decision)
        self.assertIn("NVIDIA Vision on hold", result.checks["Local OCR"])
        ocr.assert_called_once()


if __name__ == "__main__":
    unittest.main()
