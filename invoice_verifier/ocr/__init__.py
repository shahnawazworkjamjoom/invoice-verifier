"""OCR implementation cloned from the Odoo lpo_invoice_ocr module."""

from .ocr_service import extract_document, fuzzy_score

__all__ = ["extract_document", "fuzzy_score"]
