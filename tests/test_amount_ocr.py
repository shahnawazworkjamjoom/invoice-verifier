import unittest
from unittest.mock import patch
from PIL import Image
from invoice_verifier.ocr.ocr_parser import parse_document_text
from invoice_verifier.ocr.ocr_service import _ocr_image


class AmountOcrTests(unittest.TestCase):
    def test_amount_mode_skips_line_items_and_uses_printed_total(self):
        with patch('invoice_verifier.ocr.ocr_parser.extract_lines') as lines:
            result = parse_document_text('Subtotal 100.00\nVAT 5.00\nGrand Total 105.00', 'invoice', amount_only=True)
        lines.assert_not_called()
        self.assertEqual(105, result['amount_total'])

    def test_does_not_invent_missing_final_total(self):
        result = parse_document_text('Subtotal 100.00\nVAT 5.00', 'invoice', amount_only=True)
        self.assertEqual(0, result['amount_total'])

    def test_skips_header_recovery_for_amount_only(self):
        text = 'INVOICE\n' + 'Description item text\n' * 20 + 'Grand Total 105.00'
        with patch('invoice_verifier.ocr.ocr_service._ocr_rapidocr', return_value=(text, .98, False)) as ocr:
            _ocr_image(Image.new('RGB', (2000, 100)), amount_only=True)
        ocr.assert_called_once()
