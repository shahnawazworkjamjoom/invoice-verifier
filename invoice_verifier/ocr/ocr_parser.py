# -*- coding: utf-8 -*-
"""Heuristic text parser for LPO / Invoice documents.

Takes raw OCR (or digital-PDF) text and extracts:
vendor, document number, date, line items, subtotal, tax, total.
Designed to be forgiving: works even when OCR is noisy.
"""
import re
from datetime import date

AMOUNT_RE = r'[\d,]+\.\d{1,2}|[\d,]+'

DATE_PATTERNS = [
    r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})',
    r'(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
    r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4})',
]

LINE_AMOUNT_RE = re.compile(r'^[\d,]+(?:\.\d{1,3})?$')

DOC_NUMBER_LABELS = [
    # Explicit "PO No: 2024/PO-12" / "LPO No: X" first (avoids matching bare "PURCHASE ORDER" header)
    r'(?:LPO|PO)\s*(?:No\.?|Number|#|N°)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{1,30})',
    r'(?:Invoice|INV|Bill)\s*(?:No\.?|Number|#|N°)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{1,30})',
    r'(?:LPO|Purchase\s*Order|Order)\s*(?:No|Number|#|N°)?\s*[:\-]?\s*([A-Z0-9\-/]+)',
    r'(?:Invoice|INV|Bill)\s*(?:No|Number|#|N°)?\s*[:\-]?\s*([A-Z0-9\-/]+)',
]

TOTAL_LABELS = {
    'amount_untaxed': [
        r'sub[ \t\-]?total(?:[ \t]*\(?(?:excl|before|without)[^\)\n]*\)?)?[ \t]*[:\-]?[ \t]*(' + AMOUNT_RE + r')',
        r'amount[ \t]*(?:before|excl)[^\n:]*[:\-]?[ \t]*(' + AMOUNT_RE + r')',
        r'total[ \t]*before[ \t]*tax[ \t]*[:\-]?[ \t]*(' + AMOUNT_RE + r')',
        r'net[ \t]*amount[ \t]*[:\-]?[ \t]*(' + AMOUNT_RE + r')',
    ],
    'amount_tax': [
        r'\b(?:VAT|GST|TAX)\b(?![ \t]*IN\b)(?:[ \t]*\(?\d*\.?\d*[ \t]*%\)?)?[ \t]*(?:amount)?[ \t]*[:\-]?[ \t]*(' + AMOUNT_RE + r')',
        r'\btax[ \t]*amount[ \t]*[:\-]?[ \t]*(' + AMOUNT_RE + r')',
    ],
    'amount_total': [
        r'grand[ \t]*total[ \t]*[:\-]?[ \t]*(' + AMOUNT_RE + r')',
        r'(?:net[ \t]*payable|amount[ \t]*payable|balance[ \t]*due|total[ \t]*(?:incl|inc|with)[^\n:]*?)[ \t]*[:\-]?[ \t]*(' + AMOUNT_RE + r')',
        r'(?<!sub[ \t\-])\btotal\b[ \t]*[:\-]?[ \t]*(' + AMOUNT_RE + r')',
    ],
}


def _parse_amount(value):
    if not value:
        return 0.0
    try:
        cleaned = str(value).replace(',', '').replace(' ', '').strip()
        cleaned = re.sub(r'[^\d.\-]', '', cleaned)
        if cleaned.count('.') > 1:
            parts = cleaned.split('.')
            cleaned = ''.join(parts[:-1]) + '.' + parts[-1]
        return float(cleaned) if cleaned not in ('', '.', '-', '-.') else 0.0
    except Exception:
        return 0.0


def _find_first(patterns, text, group=1):
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                return m.group(group).strip()
            except IndexError:
                return m.group(0).strip()
    return ''


def extract_discount(text):
    m = re.search(r'\bdiscounts?[ \t]*[:\-]?[ \t]*(?:\r?\n[ \t]*)?(' + AMOUNT_RE + r')', text, re.IGNORECASE)
    return _parse_amount(m.group(1)) if m else 0.0


def extract_totals(text):
    res = {'amount_untaxed': 0.0, 'amount_tax': 0.0, 'amount_total': 0.0}
    for key, patterns in TOTAL_LABELS.items():
        found = _find_first(patterns, text)
        # strip trailing junk like currency codes
        if found:
            m = re.search(AMOUNT_RE, found)
            if m:
                found = m.group(0)
        res[key] = _parse_amount(found)
    # Plain "Total" vs "Grand total": when both exist and differ,
    # plain Total is the pre-discount subtotal -> map to untaxed.
    m_plain = re.search(r'(?m)^\s*Total\s*[:\-]?[ \t]*(?:\r?\n[ \t]*)?(' + AMOUNT_RE + r')[ \t]*$', text, re.IGNORECASE)
    m_grand = re.search(r'grand[ \t]*total[ \t]*[:\-]?[ \t]*(?:\r?\n[ \t]*)?(' + AMOUNT_RE + r')', text, re.IGNORECASE)
    if m_plain and m_grand:
        plain = _parse_amount(m_plain.group(1))
        grand = _parse_amount(m_grand.group(1))
        if plain and grand and abs(plain - grand) > 0.005:
            # e.g. Total 22141.00, Discounts 141.00, Grand total 22000.00
            res['amount_untaxed'] = plain
            res['amount_total'] = grand
    discount = extract_discount(text)
    if discount:
        res['discount'] = discount
    # Fallback: if untaxed is 0 but total exists, keep 0 (user fixes in preview)
    return res


def _labelled_amount(text, labels):
    for label in labels:
        match = re.search(r'(?m)^[ \t]*(?:' + label + r')(?:[ \t]*\([A-Z]{3}\))?[ \t]*[:\-]?[ \t]*(?:\r?\n[ \t]*)?(' + AMOUNT_RE + r')', text, re.I)
        if match:
            return _parse_amount(match.group(1))
    return 0.0


def extract_doc_number(text, doc_type='lpo'):
    if doc_type == 'invoice':
        explicit = _find_first([
            r'(?:INV[O0]ICE|INV|Tax[ \t]*Inv)[ \t]*(?:No\.?|Number|#)[ \t]*[:#\.\-]?[ \t]*(?:\r?\n[ \t]*)?([A-Z0-9][A-Z0-9\-/]{3,40})',
            r'CREDIT[ \t]*INVOICE[ \t]*(?:No\.?)?[ \t]*#?[ \t]*(?:\r?\n[ \t]*)?([A-Z0-9][A-Z0-9\-/]{3,40})',
            r'(?m)^Inv(?:oice)?[ \t]*No[ \t]*:[ \t]*$\s*([A-Z0-9][A-Z0-9\-/]{3,40})',
            r'(?m)^Inv(?:oice|olce|oice)[ \t]*$\s*([A-Z0-9][A-Z0-9\-/]{3,40})',
        ], text)
    else:
        explicit = _find_first([
            r'PO[ \t]*(?:No\.?|Number|#)[ \t]*[:\-]?[ \t]*(?:\r?\n[ \t]*)?(?:Vendor[ \t]*(?:\r?\n[ \t]*)?)?(PO[A-Z0-9\-/]{4,40})',
            r'\bLPO[ \t]*[:#\.\-]?[ \t]*(?:\r?\n[ \t]*)?([A-Z0-9][A-Z0-9\-/]{4,40})',
        ], text)
    if explicit:
        value = re.sub(r'^[:\-\s#]+', '', explicit).strip()
        # RapidOCR commonly confuses the capital I in the purchasing team's
        # CI invoice prefix with a lowercase l.
        if doc_type == 'invoice' and re.match(r'^Cl-', value):
            value = 'CI-' + value[3:]
        return value[:64]
    if doc_type == 'invoice':
        pats = [DOC_NUMBER_LABELS[1], DOC_NUMBER_LABELS[0]]
    else:
        pats = [DOC_NUMBER_LABELS[0], DOC_NUMBER_LABELS[1]]
    val = _find_first(pats, text)
    # clean common OCR noise
    val = re.sub(r'^[:\-\s#]+', '', val).strip()
    return val[:64]


def extract_date(text, doc_type='lpo'):
    labels = (r'(?:Order[ \t]*Date|PO[ \t]*Date)',) if doc_type == 'lpo' else (
        r'(?:Tax[ \t]*Inv[o0]ice[ \t]*Date|Date[ \t]*of[ \t]*Inv[o0]ice|Inv[o0]ice[ \t]*Date)', r'(?m)^Date')
    val = ''
    for label in labels:
        val = _find_first([label + r'[ \t]*[:\-]?[ \t]*(?:\r?\n[ \t]*)?(' +
                           r'\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}(?:[ \t]+|-)['
                           r'A-Za-z]{3,9}(?:[ \t]+|-)\d{2,4}' + r')'], text)
        if val:
            break
    val = val or _find_first(DATE_PATTERNS, text)
    # normalize to YYYY-MM-DD when possible, else return raw for wizard
    numeric_formats = ['%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d.%m.%Y']
    numeric_formats += (['%m/%d/%Y', '%m/%d/%y', '%d/%m/%Y', '%d/%m/%y'] if doc_type == 'lpo'
                        else ['%d/%m/%Y', '%d/%m/%y', '%m/%d/%Y', '%m/%d/%y'])
    numeric_formats += ['%m-%d-%Y']
    for fmt in numeric_formats:
        try:
            from datetime import datetime
            dt = datetime.strptime(val.strip(), fmt)
            return dt.date().isoformat()
        except Exception:
            continue
    # try dd Mon yyyy
    try:
        from datetime import datetime
        for fmt in ('%d %b %Y', '%d %B %Y', '%d %b %y', '%d %B %y', '%d-%b-%Y', '%d-%B-%Y'):
            try:
                return datetime.strptime(val.strip(), fmt).date().isoformat()
            except Exception:
                continue
    except Exception:
        pass
    return val


def extract_vendor(text, doc_type='lpo'):
    """Best-effort: look for Vendor/Supplier/Seller/From/Billed by lines."""
    if doc_type == 'lpo':
        val = _find_first([
            r'PO[ \t]*No\.[ \t]*(?:\r?\n[ \t]*)?Vendor[ \t]*(?:\r?\n[ \t]*)?PO[A-Z0-9\-/]+[ \t]*(?:\r?\n[ \t]*)?([^\n]{3,120})',
        ], text)
        if val:
            return val.strip()[:120]
    else:
        known_issuers = (
            (r'BARAKAT[ \t]*QUALITY[ \t]*PLUS', 'Barakat Quality Plus (L.L.C.)'),
            (r'MOHEBI|mohebilogistics', 'MOHEBI LOGISTICS LLC'),
            (r'DUBAI[ \t]*REFRESHMENT', 'DUBAI REFRESHMENTS P.J.S.C.'),
            (r'ABU[ \t]*DHABI[ \t]*REFRESHMENTS', 'ABU DHABI REFRESHMENTS CO. L.L.C.'),
            (r'M\.?H\.?[ \t]*ENTERPRISES', 'M.H. ENTERPRISES L.L.C.'),
        )
        for pattern, issuer in known_issuers:
            if re.search(pattern, text, re.I):
                return issuer
        # Prefer the document issuer near the top over customer/company fields
        # that occur later in invoice bodies and signature blocks.
        for line in text.splitlines()[:35]:
            s = line.strip(' :.-')
            if 4 < len(s) < 120 and re.search(r'(?:L\.?L\.?C|PJSC|P\.J\.S\.C|LOGISTICS|REFRESHMENTS|ENTERPRISES|QUALITY[ \t]*PLUS)', s, re.I):
                if not re.search(r'KOJ[ \t]*FOOD|SUBWAY|invoice[ \t]*to|bill.?to|ship.?to', s, re.I):
                    return s[:120]
    pats = [
        r'(?:Supplier[ \t]*Name|Vendor[ \t]*Name|Seller[ \t]*Name)[ \t]*[:\-]?[ \t]*([^\n]{2,80})',
        r'(?:Supplier|Vendor|Seller|Sold[ \t]*by)[ \t]*[:\-]?[ \t]*([^\n]{3,80})',
        r'(?:Billed[ \t]*by|Company)[ \t]*[:\-]?[ \t]*([^\n]{3,80})',
    ]
    val = _find_first(pats, text)
    if val:
        val = re.sub(r'^(Name|Code)\s*[:\-]?\s*', '', val.strip(), flags=re.IGNORECASE)
        return re.sub(r'\s{2,}', ' ', val).strip()[:120]
    # Issuer/company names near the top of invoices.
    for line in text.splitlines()[:25]:
        s = line.strip(' :.-')
        if 4 < len(s) < 120 and re.search(r'\b(?:L\.?L\.?C|PJSC|P\.J\.S\.C|LOGISTICS|REFRESHMENTS|ENTERPRISES|QUALITY PLUS)\b', s, re.I):
            if not re.search(r'KOJ FOOD|SUBWAY|invoice to|bill.?to|ship.?to', s, re.I):
                return s[:120]
    # fallback: first non-empty line that looks like a company (has letters, not totals)
    for line in text.splitlines()[:12]:
        s = line.strip()
        if 3 < len(s) < 80 and re.search(r'[A-Za-z]{3,}', s):
            if not re.search(r'(lpo|invoice|purchase|total|tax|date|number|order)', s, re.I):
                return s[:120]
    return ''


HEADER_SKIP = (
    'product name', 'product code', 's.no', 's no', 'hsn code',
    'requisitioner', 'ship via', 'f.o.b', 'shipping terms',
    'supplier name', 'supplier code', 'gstin', 'ship to',
    'terms and conditions', 'authorized signatory',
)

SUMMARY_SKIP = (
    'subtotal', 'grand total', 'discount', 'vat', ' gst ',
    ' tax ', 'balance due', 'amount payable', 'page ',
)


def _parse_table_line(s):
    """9-col PO table: [S.No] [Code] Product Name HSN Qty Units Rate Tax% Amount.

    e.g. '1 105 Surf Excel 5 kg 34019011 20 nos 600.00 5% 12600.00'
    Returns dict or None.
    """
    m = re.search(
        r'^(?:(?P<sno>\d+)\s+)?(?:(?P<code>\d{2,10})\s+)?'
        r'(?P<desc>.+?)\s+'
        r'(?P<hsn>\d{6,10})\s+'
        r'(?P<qty>\d+(?:\.\d+)?)\s+'
        r'(?P<unit>nos\.?|pcs|kg|g|units?|box|pkt|set|mtr|ltr)\s+'
        r'(?P<price>[\d,]+\.\d{2})\s+'
        r'(?P<tax>\d{1,2}(?:\.\d+)?\s*%?)\s+'
        r'(?P<amt>[\d,]+\.\d{2})\s*$',
        s, re.IGNORECASE)
    if not m:
        return None
    desc = m.group('desc').strip(' -–—|')
    desc = re.sub(r'^(\d+\s+){1,2}', '', desc).strip()
    if len(desc) < 2 or re.match(r'^[\d\s.,#\-]+$', desc):
        return None
    qty = _parse_amount(m.group('qty'))
    price = _parse_amount(m.group('price'))
    amt = _parse_amount(m.group('amt'))
    if qty <= 0 or price <= 0 or amt <= 0:
        return None
    # sanity: amount should be roughly qty*rate (+tax/discount tolerance 25%)
    expected = qty * price
    if expected > 0 and abs(amt - expected) / expected > 0.35:
        # still accept (tax-inclusive amounts like 20*600=12000 -> 12600)
        # but reject wild mismatches (e.g. HSN mis-split)
        if abs(amt - expected) / expected > 1.0:
            return None
    return {
        'product_name': re.sub(r'\s{2,}', ' ', desc)[:200],
        'product_code': (m.group('code') or '')[:32],
        'hsn_code': (m.group('hsn') or '')[:32],
        'unit': (m.group('unit') or '')[:16],
        'tax_rate': _parse_amount(m.group('tax')),
        'quantity': qty,
        'price_unit': price,
        'document_amount': amt,
        'price_subtotal': amt,
    }


def _extract_vertical_table(text):
    """Parse OCR output where every cell is returned on its own line.

    Table-focused OCR engines commonly return the Thendral layout as a vertical
    stream: code, description, HSN, quantity, unit, rate, tax and amount.  Work
    backwards from the strongly typed amount/tax/rate tail so a missing serial
    number (as on the first row in the sample) does not shift later rows.
    """
    tokens = [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines()]
    tokens = [token for token in tokens if token]
    rows = []
    amount_pat = re.compile(r'^[\d,]+\.\d{2}$')
    tax_pat = re.compile(r'^\d{1,2}(?:\.\d+)?\s*%$')
    unit_pat = re.compile(r'^(?:nos\.?|pcs|kg|g|units?|box|pkt|set|mtr|ltr)$', re.I)
    integer_pat = re.compile(r'^\d+(?:\.\d+)?$')
    hsn_pat = re.compile(r'^\d{6,10}$')

    for i in range(7, len(tokens)):
        if not amount_pat.match(tokens[i]) or not tax_pat.match(tokens[i - 1]):
            continue
        if not amount_pat.match(tokens[i - 2]) or not unit_pat.match(tokens[i - 3]):
            continue
        if not integer_pat.match(tokens[i - 4]) or not hsn_pat.match(tokens[i - 5]):
            continue
        desc = tokens[i - 6].strip(' -–—|')
        code = tokens[i - 7] if re.fullmatch(r'\d{2,10}', tokens[i - 7]) else ''
        if not code or len(desc) < 2 or not re.search(r'[A-Za-z]', desc):
            continue
        qty = _parse_amount(tokens[i - 4])
        price = _parse_amount(tokens[i - 2])
        amount = _parse_amount(tokens[i])
        if qty <= 0 or price <= 0 or amount <= 0:
            continue
        rows.append({
            'product_name': desc[:200],
            'product_code': code[:32],
            'hsn_code': tokens[i - 5][:32],
            'unit': tokens[i - 3][:16],
            'tax_rate': _parse_amount(tokens[i - 1]),
            'quantity': qty,
            'price_unit': price,
            'document_amount': amount,
            'price_subtotal': amount,
        })
    return rows


def _extract_subway_po_lines(text):
    """Extract the purchasing team's digital Subway PO layout."""
    tokens = [re.sub(r'\s+', ' ', value).strip() for value in text.splitlines() if value.strip()]
    header = next((i for i, value in enumerate(tokens) if value.lower() == 'vendor no. item'), -1)
    if header < 0:
        return []
    cursor = header + 1
    while cursor < len(tokens) and tokens[cursor].lower() not in ('total',):
        cursor += 1
    cursor += 1
    rows, description = [], []
    qty_unit = re.compile(r'^(\d+(?:\.\d+)?)\s+(.+)$', re.I)
    while cursor < len(tokens):
        value = tokens[cursor]
        if value.lower() in ('net total', 'grand net total'):
            break
        match = qty_unit.match(value)
        if (match and description and cursor + 3 < len(tokens)
                and LINE_AMOUNT_RE.match(tokens[cursor + 1])
                and LINE_AMOUNT_RE.match(tokens[cursor + 2])
                and re.fullmatch(r'[A-Z0-9][A-Z0-9\-/]{1,30}', tokens[cursor + 3], re.I)):
            rows.append({
                'product_name': ' '.join(description)[:200],
                'product_code': tokens[cursor + 3][:32],
                'hsn_code': '',
                'unit': match.group(2)[:32],
                'tax_rate': 0.0,
                'quantity': _parse_amount(match.group(1)),
                'price_unit': _parse_amount(tokens[cursor + 1]),
                'document_amount': _parse_amount(tokens[cursor + 2]),
                'price_subtotal': _parse_amount(tokens[cursor + 2]),
            })
            description = []
            cursor += 4
            continue
        description.append(value)
        cursor += 1
    return rows


def _numeric_token(value):
    return bool(re.fullmatch(r'-?[\d,]+(?:\.\d{1,3})?', value.strip()))


def _tax_rate_from_token(value):
    digits = re.sub(r'[^\d]', '', value.split('%')[0])
    for width in (2, 1):
        if len(digits) >= width:
            candidate = int(digits[-width:])
            if candidate in (0, 5, 10, 15, 18, 20):
                return float(candidate)
    return 0.0


def _extract_mohebi_invoice_lines(text):
    tokens = [re.sub(r'\s+', ' ', value).strip() for value in text.splitlines() if value.strip()]
    starts = [i for i, value in enumerate(tokens)
              if re.fullmatch(r'\d{1,3}[A-Z]{2,5}\d{5,}', value, re.I)]
    rows = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else min(len(tokens), start + 15)
        block = tokens[start:end]
        match = re.fullmatch(r'(\d{1,3})([A-Z]{2,5}\d{5,})', block[0], re.I)
        unit_idx = next((i for i, value in enumerate(block[2:], 2)
                         if re.fullmatch(r'(?:CS|PC|PCS|CASE|EA)', value, re.I)), -1)
        if not match or unit_idx < 0 or unit_idx + 5 >= len(block):
            continue
        qty = _parse_amount(block[unit_idx + 1])
        price = _parse_amount(block[unit_idx + 2])
        gross = (_parse_amount(block[unit_idx + 5])
                 if unit_idx + 5 < len(block) and _numeric_token(block[unit_idx + 5])
                 else qty * price)
        tax_match = next((re.search(r'(\d{1,2}(?:\.\d+)?)%', v) for v in block[unit_idx + 3:]
                          if '%' in v), None)
        rows.append({
            'product_name': block[1][:200], 'product_code': match.group(2)[:32],
            'hsn_code': '', 'unit': block[unit_idx][:16],
            'tax_rate': _tax_rate_from_token(tax_match.group(0)) if tax_match else 0.0,
            'quantity': qty, 'price_unit': price, 'document_amount': gross,
            'price_subtotal': gross,
        })
    return rows


def _extract_barakat_invoice_lines(text):
    tokens = [re.sub(r'\s+', ' ', value).strip() for value in text.splitlines() if value.strip()]
    codes = [i for i, value in enumerate(tokens) if re.fullmatch(r'4\d{5}', value)]
    rows = []
    for pos, start in enumerate(codes):
        end = codes[pos + 1] if pos + 1 < len(codes) else min(len(tokens), start + 18)
        block = tokens[start:end]
        if len(block) < 8:
            continue
        qty_idx, unit, qty = -1, '', 0.0
        for i, value in enumerate(block[1:], 1):
            combined = re.fullmatch(r'([A-Za-z]{2,5})(\d+[.,]\d{3})', value, re.I)
            if combined:
                raw_qty = combined.group(2)
                if raw_qty.lower().startswith('r'):
                    raw_qty = '1.' + raw_qty[1:]
                elif ',' in raw_qty and '.' not in raw_qty:
                    raw_qty = raw_qty.replace(',', '.')
                qty_idx, unit, qty = i, combined.group(1), _parse_amount(raw_qty)
                break
            merged_one = re.fullmatch(r'([A-Za-z]{2,5})[ \t]+r?(\d{3})', value, re.I)
            if merged_one:
                qty_idx, unit, qty = i, merged_one.group(1), 1.0
                break
            if re.fullmatch(r'[A-Za-z0-9]{2,5}', value) and i + 1 < len(block) and re.fullmatch(r'\d+[.,]\d{3}', block[i + 1]):
                raw_qty = block[i + 1].replace(',', '.')
                qty_idx, unit, qty = i + 1, value, _parse_amount(raw_qty)
                break
        if qty_idx < 0 or qty_idx + 1 >= len(block):
            # Some scans drop the unit and quantity for a 1-unit row. When the
            # first two monetary values are equal, the only consistent quantity
            # is one.
            numbers = [(i, _parse_amount(v)) for i, v in enumerate(block[1:], 1) if _numeric_token(v)]
            if len(numbers) >= 2 and abs(numbers[0][1] - numbers[1][1]) < .01:
                qty_idx, unit, qty = numbers[0][0] - 1, '', 1.0
            else:
                continue
        tax_idx = next((i for i, value in enumerate(block[qty_idx + 2:], qty_idx + 2) if '%' in value), -1)
        tax_rate = _tax_rate_from_token(block[tax_idx]) if tax_idx >= 0 else 0.0
        list_price = _parse_amount(block[qty_idx + 1]) if qty_idx + 1 < len(block) else 0.0
        net = 0.0
        if tax_idx >= 0:
            embedded = re.match(r'^([\d,]+\.\d{2})\s*5%$', block[tax_idx])
            net = _parse_amount(embedded.group(1)) if embedded else next(
                (_parse_amount(v) for v in reversed(block[qty_idx + 1:tax_idx]) if _numeric_token(v)), 0.0)
        # A merged cell such as ``Pcs2.000`` can OCR as ``Pc82.000``. Resolve
        # the ambiguity from the row arithmetic rather than accepting qty 82.
        if qty > 50 and net and list_price:
            inferred_qty = net / list_price
            if .5 <= inferred_qty <= 50 and abs(inferred_qty - round(inferred_qty)) < .05:
                qty = float(round(inferred_qty))
        price = round(net / qty, 4) if net and qty else list_price
        tail_values = block[(tax_idx + 1 if tax_idx >= 0 else qty_idx + 2):]
        if tail_values and re.fullmatch(r'\d{1,2}', tail_values[-1]) and _parse_amount(tail_values[-1]) <= 30:
            tail_values = tail_values[:-1]
        numeric_tail = [_parse_amount(value) for value in tail_values if _numeric_token(value)]
        amount = numeric_tail[-1] if numeric_tail else qty * price
        description = block[1] if len(block) > 1 and re.search(r'[A-Za-z]', block[1]) else block[0]
        rows.append({
            'product_name': description[:200], 'product_code': block[0], 'hsn_code': '',
            'unit': unit[:16], 'tax_rate': tax_rate, 'quantity': qty,
            'price_unit': price, 'document_amount': amount, 'price_subtotal': amount,
        })
    return rows


def _extract_mh_invoice_lines(text):
    """Extract the sideways M.H. Enterprises invoice after auto-rotation."""
    tokens = [re.sub(r'\s+', ' ', value).strip() for value in text.splitlines() if value.strip()]
    code_positions = {}
    for i, value in enumerate(tokens):
        if re.fullmatch(r'FNFD\d+', value, re.I):
            code_positions.setdefault(value.upper(), []).append(i)
    rows = []
    for code, positions in code_positions.items():
        if len(positions) < 2:
            continue
        first, second = positions[-2], positions[-1]
        preceding = [v for v in tokens[max(0, first - 3):first]
                     if not re.fullmatch(r'\d{1,2}', v)]
        middle = tokens[first + 1:second]
        product_name = ' '.join(preceding[-1:] + middle).strip() or code
        tail = tokens[second + 1:second + 16]
        unit = tail[0] if tail else ''
        numerics = [(i, _parse_amount(v)) for i, v in enumerate(tail) if _numeric_token(v)]
        if len(numerics) < 5:
            continue
        quantity = numerics[0][1]
        price_entry = next(((i, v) for i, v in numerics[1:] if '.' in tail[i] and v > 0), None)
        if not price_entry:
            continue
        price_idx, price = price_entry
        tax_idx = next((i for i in range(price_idx + 1, len(tail)) if tail[i] in ('5', '5%')), -1)
        if tax_idx >= 0:
            vat = _parse_amount(tail[tax_idx + 1]) if tax_idx + 1 < len(tail) else 0.0
            net = next((value for idx, value in reversed(numerics) if idx < tax_idx), 0.0)
            amount = (_parse_amount(tail[tax_idx + 2]) if tax_idx + 2 < len(tail)
                      and _numeric_token(tail[tax_idx + 2]) else round(net + vat, 2))
        else:
            amount = numerics[-1][1]
        rows.append({
            'product_name': product_name[:200], 'product_code': code[:32], 'hsn_code': '',
            'unit': unit[:32], 'tax_rate': 5.0 if tax_idx >= 0 else 0.0,
            'quantity': quantity, 'price_unit': price, 'document_amount': amount,
            'price_subtotal': amount,
        })
    return rows


def _extract_refreshment_invoice_lines(text):
    tokens = [re.sub(r'\s+', ' ', value).strip() for value in text.splitlines() if value.strip()]
    try:
        start = next(i for i, value in enumerate(tokens) if value.upper() == 'SALES') + 1
        end = next(i for i, value in enumerate(tokens[start:], start) if value.upper() == 'TOTAL')
    except StopIteration:
        return []
    block = tokens[start:end]
    tax_positions = [i for i, value in enumerate(block) if re.fullmatch(r'\d{1,2}%?', value) and '%' in value]
    rows, previous = [], 0
    for tax_idx in tax_positions:
        if tax_idx < 6 or tax_idx + 2 >= len(block):
            continue
        qty_idx = tax_idx - 5
        if not _numeric_token(block[qty_idx]):
            continue
        prefix = block[previous:qty_idx]
        code_idx = next((i for i, value in enumerate(prefix) if re.fullmatch(r'\d{2,6}', value)), -1)
        if code_idx < 0 and prefix:
            code_idx = 0
        code = prefix[code_idx] if prefix else ''
        desc = ' '.join(prefix[:code_idx] + prefix[code_idx + 1:]).strip() or code
        qty, price = _parse_amount(block[qty_idx]), _parse_amount(block[qty_idx + 1])
        net = _parse_amount(block[tax_idx - 1])
        expected = qty * price + _parse_amount(block[tax_idx - 3]) + _parse_amount(block[tax_idx - 2])
        if net > expected * 10 and expected:
            net /= 100.0
        if net and qty:
            price = round(net / qty, 4)
        amount = _parse_amount(block[tax_idx + 2])
        rows.append({
            'product_name': desc[:200], 'product_code': code[:32], 'hsn_code': '',
            'unit': '', 'tax_rate': _parse_amount(block[tax_idx]), 'quantity': qty,
            'price_unit': price, 'document_amount': amount or net, 'price_subtotal': amount or net,
        })
        previous = tax_idx + 3
    # Single-line Abu Dhabi layout: code and description are merged, followed
    # by unit, qty, price, gross, discount, excise, VAT and total.
    if not rows and len(block) >= 9:
        unit_idx = next((i for i, value in enumerate(block) if value.upper() in ('CASE', 'PCS', 'PC')), -1)
        if unit_idx > 0 and unit_idx + 7 < len(block):
            head = block[unit_idx - 1]
            code_match = re.match(r'^([A-Z]+\d+[A-Z]?)(.*)$', head)
            qty = _parse_amount(block[unit_idx + 1])
            net = (_parse_amount(block[unit_idx + 3]) - _parse_amount(block[unit_idx + 4])
                   + _parse_amount(block[unit_idx + 5]))
            rows.append({
                'product_name': (code_match.group(2).strip() if code_match else head)[:200],
                'product_code': (code_match.group(1) if code_match else '')[:32],
                'hsn_code': '', 'unit': block[unit_idx], 'tax_rate': 5.0,
                'quantity': qty,
                'price_unit': round(net / qty, 4) if qty else _parse_amount(block[unit_idx + 2]),
                'document_amount': round(net, 2),
                'price_subtotal': round(net, 2),
            })
    return rows


def extract_lines(text):
    """Parse tabular lines like: 'Product name  2  150.00  300.00' or with x separator."""
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if len(s) < 5:
            continue
        low = s.lower()
        if any(k in low for k in HEADER_SKIP) and any(
                k in low for k in ('product', 'quantity', 'rate', 's.no', 'hsn', 'requisitioner', 'supplier', 'ship')):
            continue
        if any(k in low for k in SUMMARY_SKIP):
            continue
        # 1) structured 9-col table first (Thendral-style PO)
        row = _parse_table_line(s)
        if row:
            lines.append(row)
            continue
        # pattern: description ... qty ... price ... amount
        # e.g. "Cement Bag 50kg  100  25.50  2550.00" or "Cement 2 x 150.00 = 300.00"
        m = re.search(
            r'^(?P<desc>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s*(?:x|×|\*|pcs|qty)?\s+(?P<price>[\d,]+\.\d{1,2})\s+(?P<amt>[\d,]+\.\d{1,2})\s*$',
            s, re.IGNORECASE)
        if m:
            desc = m.group('desc').strip(' -–—|')
            if len(desc) < 2 or re.match(r'^[\d\s.,#\-]+$', desc):
                continue
            qty = _parse_amount(m.group('qty'))
            price = _parse_amount(m.group('price'))
            amt = _parse_amount(m.group('amt'))
            if qty <= 0 or price <= 0:
                continue
            lines.append({
                'product_name': re.sub(r'\s{2,}', ' ', desc)[:200],
                'quantity': qty,
                'price_unit': price,
                'price_subtotal': amt or round(qty * price, 2),
            })
            continue
        # looser: desc qty price (compute subtotal)
        m2 = re.search(
            r'^(?P<desc>[A-Za-z].{2,}?)\s{2,}(?P<qty>\d+(?:\.\d+)?)\s+(?P<price>[\d,]+\.\d{1,2})\s*$',
            s)
        if m2:
            desc = m2.group('desc').strip()
            qty = _parse_amount(m2.group('qty'))
            price = _parse_amount(m2.group('price'))
            if qty > 0 and price > 0 and len(desc) >= 2:
                lines.append({
                    'product_name': desc[:200],
                    'quantity': qty,
                    'price_unit': price,
                    'price_subtotal': round(qty * price, 2),
                })
    # When OCR placed each table cell on a separate line, the row parser above
    # cannot see columns. Prefer the purpose-built vertical parser in that case.
    for structured in (_extract_subway_po_lines(text), _extract_mohebi_invoice_lines(text),
                       _extract_barakat_invoice_lines(text), _extract_refreshment_invoice_lines(text),
                       _extract_mh_invoice_lines(text), _extract_vertical_table(text)):
        if len(structured) > len(lines):
            lines = structured
    # de-dup consecutive duplicates from OCR repetition
    deduped = []
    for ln in lines:
        if deduped and deduped[-1]['product_name'] == ln['product_name'] and deduped[-1]['quantity'] == ln['quantity']:
            continue
        deduped.append(ln)
    return deduped[:100]


def parse_document_text(text, doc_type='lpo'):
    text = text or ''
    lines = extract_lines(text)
    totals = extract_totals(text)
    # Common labels in the purchasing team's real PO and invoice formats.
    strong_untaxed = _labelled_amount(text, [
        r'(?<!Grand[ \t])Net[ \t]*Total', r'Total[ \t]*net[ \t]*amount', r'Net[ \t]*value[ \t]*before[ \t]*VAT'])
    strong_tax = _labelled_amount(text, [
        r'Total[ \t]*VAT(?:[ \t]*amount)?', r'VAT[ \t]*\(AED\)'])
    strong_total = _labelled_amount(text, [
        r'Grand[ \t]*Net[ \t]*Total', r'Total[ \t]*amount[ \t]*due', r'Total/Gross[ \t]*\(AED\)',
        r'Gross[ \t]*Total(?:[ \t]*AED)?', r'Total[ \t]*Net[ \t]*Amount'])
    totals['amount_untaxed'] = strong_untaxed or totals['amount_untaxed']
    totals['amount_tax'] = strong_tax or totals['amount_tax']
    totals['amount_total'] = strong_total or totals['amount_total']
    if re.search(r'M\.?H\.?[ \t]*ENTERPRISES', text, re.I) and lines:
        net_sum = round(sum(line['quantity'] * line['price_unit'] for line in lines), 2)
        gross_sum = round(sum(line.get('document_amount', 0.0) for line in lines), 2)
        totals['amount_untaxed'] = net_sum
        totals['amount_tax'] = round(gross_sum - net_sum, 2)
        totals['amount_total'] = gross_sum
    mohebi_summary = re.search(r'Total[ \t]*Dirhams[^\n]*(?:\r?\n[^\d\n]*)*\r?\n[\d,.]+\s*\r?\n(' + AMOUNT_RE + r')\s*\r?\n(' + AMOUNT_RE + r')\s*\r?\n(' + AMOUNT_RE + r')', text, re.I)
    if mohebi_summary:
        totals['amount_untaxed'] = _parse_amount(mohebi_summary.group(1))
        totals['amount_tax'] = _parse_amount(mohebi_summary.group(2))
        totals['amount_total'] = _parse_amount(mohebi_summary.group(3))
    totals['discount'] = totals.get('discount') or _labelled_amount(text, [r'Invoice[ \t]*Discount'])
    lines_sum = round(sum(l.get('price_subtotal', 0.0) for l in lines), 2)
    # The Abu Dhabi Refreshments credit invoice prints "Total net amount" as
    # the VAT-inclusive balance.  Its true before-VAT value is the balance
    # minus VAT (discount and excise are already folded into that balance).
    if (doc_type == 'invoice' and re.search(r'ABU[ \t]*DHABI[ \t]*REFRESHMENTS', text, re.I)
            and totals['amount_total'] and totals['amount_tax']):
        totals['amount_untaxed'] = round(totals['amount_total'] - totals['amount_tax'], 2)
    # If only total found, assume untaxed=total-tax
    if totals['amount_total'] and not totals['amount_untaxed'] and totals['amount_tax']:
        totals['amount_untaxed'] = round(totals['amount_total'] - totals['amount_tax'], 2)
    # If no subtotal label (e.g. plain "Total" mapped to grand), use lines sum
    if not totals['amount_untaxed'] and lines_sum:
        totals['amount_untaxed'] = lines_sum
    if totals.get('discount') and totals['amount_untaxed'] and totals['amount_total']:
        # discount already folded into tax diff in extract_totals; keep as is
        pass
    return {
        'vendor_name': extract_vendor(text, doc_type),
        'doc_number': extract_doc_number(text, doc_type),
        'doc_date': extract_date(text, doc_type),
        'lines': lines,
        'amount_untaxed': totals['amount_untaxed'],
        'amount_tax': totals['amount_tax'],
        'amount_total': totals['amount_total'] or round(totals['amount_untaxed'] + totals['amount_tax'], 2),
        'discount': totals.get('discount', 0.0),
        'raw_text': text[:20000],
    }
