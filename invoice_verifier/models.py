from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class InvoiceRecord:
    excel_row: int
    supplier: str
    brand: str
    location: str
    order_number: str
    invoice_number: str
    currency: str
    po_amount: float | None
    invoice_date: str
    attachment_url: str
    unique_reference: str
    record_id: str
    payment_status: str
    amount_to_pay: float | None
    received_qty: float | None
    tax_code: str
    source: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationResult:
    record: InvoiceRecord
    decision: str
    remarks: str
    checks: dict[str, str] = field(default_factory=dict)
    ocr_data: dict[str, Any] = field(default_factory=dict)
    downloaded_file: Path | None = None
    corrected_amount_to_pay: float | None = None
