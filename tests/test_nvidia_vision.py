import base64
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
import pymupdf

import invoice_verifier.nvidia_vision as vision


class FakeResponse:
    def __init__(self, answer=None):
        self.answer = answer

    def raise_for_status(self):
        return None

    def json(self):
        answer = self.answer if self.answer is not None else {
            "decision": "APPROVE", "decision_reasons": ["All critical fields match"],
            "vendor_name": "BARAKAT", "location": "Test shop",
            "invoice_number": "INV-1", "order_number": "PO-1",
            "invoice_date": "2026-09-02", "currency": "AED", "net_amount": 100,
            "vat_amount": 5, "final_amount_due": 105, "vat_rate_percent": 5,
            "checks": {
                "invoice_number": {"expected": "INV-1", "found": "INV-1",
                                   "match": True, "evidence": "invoice header"}
            },
        }
        content = answer if isinstance(answer, str) else "```json\n" + json.dumps(answer) + "\n```"
        return {"choices": [{"message": {"content": content}}]}


class NvidiaVisionTests(unittest.TestCase):
    @staticmethod
    def record():
        return SimpleNamespace(
            excel_row=2, supplier="BARAKAT", brand="Subway", location="Test shop",
            order_number="PO-1", invoice_number="INV-1", currency="AED",
            po_amount=100.0, invoice_date="2026-09-02", payment_status="FULL",
            amount_to_pay=105.0, received_qty=10.0, tax_code="UAE_VAT_INC_5%",
            unique_reference="ref-1", record_id="1",
        )

    def test_extracts_validated_json_without_cache(self):
        with TemporaryDirectory() as folder:
            image_path = Path(folder) / "invoice.png"
            Image.new("RGB", (120, 80), "white").save(image_path)
            with patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"}), \
                    patch("invoice_verifier.nvidia_vision.requests.post", return_value=FakeResponse()) as post:
                first = vision.extract_invoice_json(image_path, self.record())
                second = vision.extract_invoice_json(image_path, self.record())
            self.assertEqual("INV-1", first["invoice_number"])
            self.assertEqual("APPROVE", first["decision"])
            self.assertEqual(first, second)
            self.assertEqual(2, post.call_count)
            prompt = post.call_args.kwargs["json"]["messages"][1]["content"][0]["text"]
            self.assertIn('\"invoice_number\": \"INV-1\"', prompt)
            self.assertIn('\"finance_amount_to_pay\": 105.0', prompt)
            self.assertIn("AED", prompt)
            self.assertIn("Return exactly these keys", prompt)

    def test_pdf_sends_only_first_and_last_pages(self):
        with TemporaryDirectory() as folder:
            pdf_path = Path(folder) / "three-pages.pdf"
            document = pymupdf.open()
            for color in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
                page = document.new_page(width=300, height=400)
                page.draw_rect(page.rect, color=color, fill=color)
            document.save(pdf_path)
            document.close()

            data_url = vision._document_images(pdf_path)[0]
            image_bytes = base64.b64decode(data_url.split(",", 1)[1])
            with Image.open(io.BytesIO(image_bytes)) as image:
                self.assertGreater(image.width, image.height)
                left = image.getpixel((image.width // 4, image.height // 2))
                right = image.getpixel((3 * image.width // 4, image.height // 2))
            self.assertGreater(left[0], left[1] + left[2])
            self.assertGreater(right[2], right[0] + right[1])

    def test_recovers_labeled_markdown_response(self):
        response = """**Invoice Verification Report**

**Decision:** APPROVE

**Reasons:**
* Invoice number matches.
* Total amount matches.

**Vendor Information:**
* **Vendor Name:** Barakat Quality Plus (L.L.C.)
* **Invoice Number:** INV-1
* **Order Number:** PO-1
* **Invoice Date:** 2026-09-02
* **Currency:** AED
* **VAT Rate:** 5%
* **Subtotal:** 100.00
* **Total Amount:** 105.00

**Checks:**
* **Supplier:** Matched
* **Invoice Number:** Matched
* **Order Number:** Matched
* **Invoice Date:** Matched
* **Amount to Pay:** Matched
* **PO Amount:** Matched
* **Currency:** Matched
* **VAT:** Matched
"""
        expected = vision._excel_context(self.record())
        result = vision._validate(vision._plain_text_result(response, expected))
        self.assertEqual("APPROVE", result["decision"])
        self.assertEqual("INV-1", result["invoice_number"])
        self.assertEqual(105.0, result["total_amount"])
        self.assertTrue(result["checks"]["invoice_number"]["match"])

    def test_incomplete_placeholder_response_retries_with_last_page(self):
        placeholder = {
            "decision": "APPROVE", "decision_reasons": ["short reason"],
            "vendor_name": None, "location": None, "invoice_number": None,
            "order_number": None, "invoice_date": None, "currency": None,
            "net_amount": None, "vat_amount": None, "vat_rate_percent": None,
            "final_amount_due": None, "corrected_amount_to_pay": None,
            "checks": {},
        }
        complete = FakeResponse().answer or {
            "decision": "APPROVE", "decision_reasons": ["Invoice fields match"],
            "vendor_name": "BARAKAT", "location": "Test shop",
            "invoice_number": "INV-1", "order_number": "PO-1",
            "invoice_date": "2026-09-02", "currency": "AED", "net_amount": 100,
            "vat_amount": 5, "vat_rate_percent": 5, "final_amount_due": 105,
            "corrected_amount_to_pay": None,
            "checks": {},
        }
        with TemporaryDirectory() as folder:
            image_path = Path(folder) / "invoice.png"
            Image.new("RGB", (120, 80), "white").save(image_path)
            with patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"}), \
                    patch("invoice_verifier.nvidia_vision.requests.post", side_effect=[
                        FakeResponse(placeholder), FakeResponse(complete),
                    ]) as post:
                result = vision.extract_invoice_json(image_path, self.record())
        self.assertEqual(2, post.call_count)
        self.assertEqual("APPROVE", result["decision"])
        self.assertEqual(105.0, result["final_amount_due"])
        retry_prompt = post.call_args_list[1].kwargs["json"]["messages"][1]["content"][0]["text"]
        self.assertIn("SECOND PASS", retry_prompt)

    def test_repairs_minor_json_syntax_error_locally(self):
        malformed = ('{"decision" "APPROVE", "decision_reasons": ["All fields match"], '
                     '"checks": {}, "confidence": {}}')
        result = vision._validate(vision._json_from_text(malformed))
        self.assertEqual("APPROVE", result["decision"])

    def test_repairs_unstructured_vision_answer_with_json_model(self):
        repaired = {
            "decision": "APPROVE", "decision_reasons": ["All values match"],
            "vendor_name": "BARAKAT", "location": "Test shop",
            "invoice_number": "INV-1", "order_number": "PO-1",
            "invoice_date": "2026-09-02", "currency": "AED", "net_amount": 100,
            "vat_amount": 5, "final_amount_due": 105, "vat_rate_percent": 5,
            "checks": {},
        }
        with TemporaryDirectory() as folder:
            image_path = Path(folder) / "invoice.png"
            Image.new("RGB", (120, 80), "white").save(image_path)
            with patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"}), \
                    patch("invoice_verifier.nvidia_vision.requests.post", side_effect=[
                        FakeResponse("The document values are consistent and it should be accepted."),
                        FakeResponse("The last page confirms the same values but this is not JSON."),
                        FakeResponse(repaired),
                    ]) as post:
                result = vision.extract_invoice_json(image_path, self.record())
        self.assertEqual("APPROVE", result["decision"])
        self.assertEqual(3, post.call_count)
        repair_payload = post.call_args_list[2][1]["json"]
        self.assertEqual(vision.REPAIR_MODEL, repair_payload["model"])
        self.assertEqual({"type": "json_object"}, repair_payload["response_format"])


if __name__ == "__main__":
    unittest.main()
