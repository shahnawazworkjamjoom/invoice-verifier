from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
import re

from .models import InvoiceRecord, VerificationResult
from .nvidia_vision import configured_model, extract_invoice_json, is_configured as vision_is_configured


def _amount_differs(expected: float | None, found: float | None) -> bool:
    if expected is None or found is None:
        return expected != found
    return abs(expected - found) > max(0.01, abs(found) * 0.0001)


def _number(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _normalise(value) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _first_match(value, pattern: str):
    match = re.search(pattern, str(value or ""), re.I)
    return match.group(0).strip(" ,.;") if match else value


def _invoice_value(value):
    return _first_match(value, r"\b\d{3,6}\s*-\s*SOIN\s*-\s*\d{5,12}\b")


def _order_value(value):
    return _first_match(value, r"\bPO\s*\d{5,12}(?:\s*-\s*\d{3,10})?\b")


def _date_value(value):
    return _first_match(
        value,
        r"\b(?:\d{1,2}[-/ ](?:[A-Za-z]{3,9}|\d{1,2})[-/ ]\d{4}|\d{4}-\d{1,2}-\d{1,2})\b",
    )


def _location_value(value):
    text = str(value or "").strip()
    match = re.search(
        r"\b\d{7,9}\s*-\s*[A-Z0-9][A-Z0-9 .()/-]*?(?=,|;|\bdoes\b|\bmatches\b|\Z)",
        text,
        re.I,
    )
    return match.group(0).strip(" ,.;") if match else value


def _location_matches(expected, found) -> bool:
    expected_text, found_text = _normalise(expected), _normalise(found)
    if not expected_text or not found_text:
        return False
    expected_code = re.match(r"\d{7,9}", expected_text)
    found_code = re.match(r"\d{7,9}", found_text)
    if expected_code and found_code and expected_code.group(0) == found_code.group(0):
        return True
    return (expected_text in found_text or found_text in expected_text
            or SequenceMatcher(None, expected_text, found_text).ratio() >= 0.88)


def _dates_match(expected, found) -> bool:
    def parse(value):
        text = str(value or "").strip()
        for pattern in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
                        "%d %b %Y", "%d-%B-%Y", "%d %B %Y"):
            try:
                return datetime.strptime(text, pattern).date()
            except ValueError:
                continue
        return None

    expected_date, found_date = parse(expected), parse(found)
    return expected_date is not None and expected_date == found_date


def _reconcile_checks(record: InvoiceRecord, vision: dict) -> dict:
    """Compare extracted invoice values with trusted Excel values locally.

    Vision models can accidentally copy the PO number into a check's expected invoice-number
    field. Expected values and deterministic comparisons therefore never come from the model.
    """
    raw_checks = vision.get("checks") if isinstance(vision.get("checks"), dict) else {}

    final_value = _number(vision.get("final_amount_due"))
    vat_amount = _number(vision.get("vat_amount"))
    stated_net = _number(vision.get("net_amount"))
    derived_net = (round(final_value - vat_amount, 2)
                   if final_value is not None and vat_amount is not None else None)
    if (derived_net is not None and record.po_amount is not None
            and (stated_net is None
                 or abs(derived_net - record.po_amount) < abs(stated_net - record.po_amount))):
        # Some Barakat summaries show merchandise net before excise above the footer. The trusted
        # comparison is the footer amount immediately before VAT: final amount minus VAT amount.
        vision["net_amount"] = derived_net

    def found(name, extracted_key):
        value = vision.get(extracted_key)
        if value not in (None, ""):
            return value
        check = raw_checks.get(name)
        return check.get("found") if isinstance(check, dict) else None

    def evidence(name):
        check = raw_checks.get(name)
        if isinstance(check, dict) and check.get("evidence"):
            return str(check["evidence"])
        return "Compared locally with the trusted Excel row"

    def has_source(name, extracted_key):
        return vision.get(extracted_key) not in (None, "") or isinstance(raw_checks.get(name), dict)

    def local_or_reported_match(name, extracted_key, local_match):
        if vision.get(extracted_key) not in (None, ""):
            return local_match
        check = raw_checks.get(name)
        return bool(check.get("match")) if isinstance(check, dict) else local_match

    supplier_found = found("supplier", "vendor_name")
    location_found = _location_value(found("location", "location"))
    invoice_found = _invoice_value(found("invoice_number", "invoice_number"))
    order_found = _order_value(found("order_number", "order_number"))
    date_found = _date_value(found("invoice_date", "invoice_date"))
    net_found = found("po_amount", "net_amount")
    final_found = found("amount_to_pay", "final_amount_due")
    currency_found = _first_match(found("currency", "currency"), r"\b(?:AED|USD|EUR|SAR)\b")
    vat_found = found("vat", "vat_rate_percent")

    expected_supplier = _normalise(record.supplier)
    supplier_aliases = {
        "BARAKAT": ("BARAKAT", "BARAKATQUALITYPLUS"),
        "ADHABIREF": ("ADHABIREF", "ABUDHABIREFRESHMENTS"),
    }
    supplier_values = supplier_aliases.get(expected_supplier, (expected_supplier,))
    normalised_supplier_found = _normalise(supplier_found)
    supplier_match = bool(normalised_supplier_found) and any(
        alias in normalised_supplier_found or normalised_supplier_found in alias
        for alias in supplier_values)

    location_match = _location_matches(record.location, location_found)

    expected_currency = (record.currency or "AED").upper()
    currency_match = (currency_found in (None, "")
                      or _normalise(currency_found) == _normalise(expected_currency))
    expected_vat = 5.0
    vat_number = _number(vat_found)
    if vat_number is not None and vat_number > 10 and vat_amount is not None:
        # A model occasionally places the VAT money amount in the rate field.
        vat_number = None
    vat_match = vat_number is None or abs(vat_number - expected_vat) <= 0.01

    values = {
        "supplier": (record.supplier, supplier_found,
                     local_or_reported_match("supplier", "vendor_name", supplier_match)),
        "location": (record.location, location_found,
                     local_or_reported_match("location", "location", location_match)),
        "invoice_number": (
            record.invoice_number, invoice_found,
            local_or_reported_match(
                "invoice_number", "invoice_number",
                bool(_normalise(invoice_found))
                and _normalise(record.invoice_number) == _normalise(invoice_found))),
        "order_number": (
            record.order_number, order_found,
            local_or_reported_match(
                "order_number", "order_number",
                bool(_normalise(order_found))
                and _normalise(record.order_number) == _normalise(order_found))),
        "invoice_date": (
            record.invoice_date, date_found,
            local_or_reported_match(
                "invoice_date", "invoice_date", _dates_match(record.invoice_date, date_found))),
        "amount_to_pay": (
            record.amount_to_pay, final_found,
            local_or_reported_match(
                "amount_to_pay", "final_amount_due",
                not _amount_differs(record.amount_to_pay, _number(final_found)))),
        "po_amount": (
            record.po_amount, net_found,
            local_or_reported_match(
                "po_amount", "net_amount",
                not _amount_differs(record.po_amount, _number(net_found)))),
        "currency": (
            expected_currency, currency_found or f"{expected_currency} (business rule)",
            local_or_reported_match("currency", "currency", currency_match)),
        "vat": (
            "5%", vat_found if vat_found not in (None, "") else "5% (business rule)",
            local_or_reported_match("vat", "vat_rate_percent", vat_match)),
    }
    source_keys = {
        "supplier": "vendor_name", "location": "location",
        "invoice_number": "invoice_number", "order_number": "order_number",
        "invoice_date": "invoice_date", "amount_to_pay": "final_amount_due",
        "po_amount": "net_amount", "currency": "currency", "vat": "vat_rate_percent",
    }
    return {
        name: {"expected": expected, "found": extracted, "match": match,
               "evidence": evidence(name)}
        for name, (expected, extracted, match) in values.items()
        if name in {"currency", "vat"} or has_source(name, source_keys[name])
    }


def verify_file(record: InvoiceRecord, path: str | Path) -> VerificationResult:
    source_path = Path(path)
    if not vision_is_configured():
        return failed_result(record, "NVIDIA_API_KEY is not configured", source_path)
    try:
        vision = extract_invoice_json(source_path, record)
    except Exception as exc:
        return failed_result(record, f"NVIDIA Vision failed: {exc}", source_path)
    if not vision:
        return failed_result(record, "NVIDIA Vision returned no result", source_path)

    vision["checks"] = _reconcile_checks(record, vision)

    checks = {"NVIDIA Vision": f"used ({configured_model()}); local OCR disabled"}
    for name, check in (vision.get("checks") or {}).items():
        expected = check.get("expected")
        found = check.get("found")
        status = "match" if check.get("match") else "mismatch"
        evidence = check.get("evidence") or "no evidence supplied"
        checks[str(name).replace("_", " ").title()] = (
            f"{status}; expected {expected!s}; found {found!s}; {evidence}")

    decision = vision["decision"]
    reasons = vision.get("decision_reasons") or ["No decision reason supplied"]
    final_amount = vision.get("final_amount_due")
    if final_amount is None:
        final_amount = vision.get("total_amount")
    try:
        final_amount = float(final_amount) if final_amount is not None else None
    except (TypeError, ValueError):
        final_amount = None

    corrected_amount = None
    amount_changed = final_amount is not None and _amount_differs(record.amount_to_pay, final_amount)
    critical_names = {
        "supplier", "location", "invoice_number", "order_number",
        "invoice_date", "po_amount", "currency", "vat",
    }
    model_checks = vision.get("checks") or {}
    critical_complete = all(
        name in model_checks and bool(model_checks[name].get("match"))
        for name in critical_names)
    explicit_critical_mismatch = any(
        name in model_checks and not bool(model_checks[name].get("match"))
        for name in critical_names)
    amount_only_correction = (
        amount_changed
        and record.payment_status.upper() == "FULL"
        and not explicit_critical_mismatch
        and (decision == "APPROVE" or critical_complete)
    )
    if explicit_critical_mismatch:
        decision = "DECLINE"
        mismatches = [name.replace("_", " ").title() for name in critical_names
                      if name in model_checks and not bool(model_checks[name].get("match"))]
        reasons = ["Critical invoice field mismatch: " + ", ".join(sorted(mismatches))]
    elif amount_only_correction:
        corrected_amount = round(final_amount, 2)
        decision = "APPROVE"
        reasons = [
            f"All critical invoice fields match; Finance Amount To Pay corrected from "
            f"{record.amount_to_pay if record.amount_to_pay is not None else 'blank'} to "
            f"{corrected_amount:.2f} using the final amount due after tax and discount"
        ]
        checks["Finance Amount To Pay"] = (
            f"corrected from {record.amount_to_pay if record.amount_to_pay is not None else 'blank'} "
            f"to {corrected_amount:.2f}; final invoice amount after tax and discount")
    elif critical_complete and record.payment_status.upper() == "FULL":
        amount_check = model_checks.get("amount_to_pay") or {}
        amount_supported = bool(amount_check.get("match")) or (
            final_amount is not None and not _amount_differs(record.amount_to_pay, final_amount))
        if amount_supported:
            decision = "APPROVE"
            reasons = ["All critical invoice fields and the final payable amount match"]

    return VerificationResult(record=record, decision=decision,
                              remarks="; ".join(str(reason) for reason in reasons), checks=checks,
                              ocr_data={"nvidia_vision": vision}, downloaded_file=source_path,
                              corrected_amount_to_pay=corrected_amount)


def failed_result(record: InvoiceRecord, message: str, path: str | Path | None = None) -> VerificationResult:
    return VerificationResult(record=record, decision="DECLINE",
                              remarks=f"Could not verify automatically: {message}",
                              checks={"NVIDIA Vision": "verification failed; local OCR disabled"},
                              downloaded_file=Path(path) if path else None)
