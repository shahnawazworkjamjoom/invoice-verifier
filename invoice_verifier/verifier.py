from __future__ import annotations

import base64
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import InvoiceRecord, VerificationResult
from .ocr.ocr_service import extract_document


def _number(value) -> Decimal | None:
    """Parse money without rounding or allowing a comparison tolerance."""
    try:
        number = Decimal(str(value).replace(",", "").strip())
        return number if number.is_finite() and number > 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def verify_file(record: InvoiceRecord, path: str | Path) -> VerificationResult:
    source_path = Path(path)
    try:
        encoded = base64.b64encode(source_path.read_bytes())
        ocr = extract_document(encoded, source_path.name, "invoice", amount_only=True)
    except Exception as exc:
        return failed_result(record, f"OCR failed: {exc}", source_path)

    final_amount = _number(ocr.get("amount_total"))
    expected_amount = _number(record.amount_to_pay)
    method = ocr.get("ocr_method") or "unknown"
    checks = {
        "Local OCR": f"used ({method}); NVIDIA Vision on hold",
        "Final Amount Due": str(final_amount) if final_amount is not None else "not found",
        "Finance Amount To Pay": str(expected_amount) if expected_amount is not None else "missing or invalid",
    }
    if ocr.get("vendor_name") not in (None, "", "Unknown"):
        checks["Invoice Vendor"] = ocr["vendor_name"]
    if ocr.get("amount_evidence"):
        checks["Amount Evidence"] = ocr["amount_evidence"]
    if final_amount is None:
        decision = "DECLINE"
        remarks = "OCR could not find a valid final amount due"
    elif expected_amount is None:
        decision = "DECLINE"
        remarks = "Excel Finance Amount To Pay is missing or invalid"
    elif expected_amount == final_amount:
        decision = "APPROVE"
        remarks = "Invoice final amount exactly matches Excel Finance Amount To Pay"
    else:
        decision = "DECLINE"
        remarks = (
            f"Amount mismatch: Excel Finance Amount To Pay {expected_amount}; "
            f"invoice final amount {final_amount} (exact match required)"
        )

    return VerificationResult(
        record=record, decision=decision, remarks=remarks, checks=checks,
        ocr_data=ocr, downloaded_file=source_path,
    )


def failed_result(record: InvoiceRecord, message: str, path: str | Path | None = None) -> VerificationResult:
    return VerificationResult(
        record=record, decision="DECLINE",
        remarks=f"Could not verify automatically: {message}",
        checks={"Local OCR": "verification failed; NVIDIA Vision on hold"},
        downloaded_file=Path(path) if path else None,
    )
