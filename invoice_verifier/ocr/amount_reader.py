"""Invoice amount OCR v2: spatial totals, bounded CPU work, no result cache.

Vendor rules select printed totals. They never receive Excel payment amounts.
Model weights are pretrained PP-OCRv4; this module does not train a model.
"""
from __future__ import annotations

import re
import threading
from decimal import Decimal

VERSION = "amount-ocr-v2"
_engine = None
_engine_lock = threading.Lock()
MONEY = r"(?<![\d.,+\-])(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?![\d.,%])"


def vendor_name(text):
    compact = re.sub(r"[^A-Z]", "", text.upper())
    for marker, name in (("BARAKAT", "Barakat Quality Plus"),
                         ("ABUDHABIREFRESHMENTS", "Abu Dhabi Refreshments"),
                         ("DUBAIREFRESHMENT", "Dubai Refreshment"),
                         ("MOHEBI", "Mohebi Logistics"),
                         ("MHENTERPRISES", "M.H. Enterprises")):
        if marker in compact:
            return name
    return "Unknown"


def select_total(text):
    """Select explicit payable labels; ambiguous amounts are not guessed."""
    vendor = vendor_name(text)
    compact_text = re.sub(r"[^A-Z]", "", text.upper())
    if vendor == "Unknown" and all(label in compact_text for label in
                                  ("TOTALSALESAMOUNT", "INVOICEDISCOUNT", "TOTALNETAMOUNT")):
        vendor = "Abu Dhabi Refreshments"
    candidates = []
    for line in text.splitlines():
        compact = re.sub(r"[ \t:()/_-]", "", line.upper())
        # Normalize common letter confusions in labels only, never in digits.
        compact = compact.replace("TOLAL", "TOTAL").replace("T0TAL", "TOTAL")
        compact = compact.replace("ARNOUNT", "AMOUNT").replace("AM0UNT", "AMOUNT")
        rank = 0
        if re.search(r"(?:TOTALAMOUNTDUE|AMOUNTPAYABLE|NETPAYABLE|BALANCEDUE|GRANDNETTOTAL|GRANDTOTAL)", compact):
            rank = 3
        elif "TOTALGROSS" in compact:
            rank = 3
        elif vendor == "Abu Dhabi Refreshments" and re.search(r"NET(?:IN)?AMOUNT", compact):
            rank = 3
        elif vendor == "Mohebi Logistics" and "TOTALDIRHAMS" in compact:
            rank = 3
        elif vendor == "M.H. Enterprises" and "TOTALAMOUNT" in compact:
            rank = 3
        elif re.match(r"^GROSSTOTAL", compact):
            rank = 2
        if not rank:
            continue
        numbers = re.findall(MONEY, line)
        if not numbers:
            continue
        # A table summary must contain several amounts, unlike a plain label.
        table_summary = ((vendor == "Mohebi Logistics" and "TOTALDIRHAMS" in compact)
                         or (vendor == "M.H. Enterprises" and "TOTALAMOUNT" in compact
                             and "DUE" not in compact))
        if table_summary and len(numbers) < 3:
            continue
        if table_summary:
            net, tax, gross = [Decimal(n.replace(",", "")) for n in numbers[-3:]]
            if net + tax != gross:
                continue
        value = Decimal(numbers[-1].replace(",", ""))
        if value > 0:
            candidates.append((rank, value, line.strip()))
    if not candidates:
        return {"amount_total": None, "vendor_name": vendor, "amount_evidence": "No explicit final total found"}
    rank = max(c[0] for c in candidates)
    selected = [c for c in candidates if c[0] == rank]
    if len({c[1] for c in selected}) != 1:
        return {"amount_total": None, "vendor_name": vendor, "amount_evidence": "Conflicting printed final totals"}
    return {"amount_total": str(selected[0][1]), "vendor_name": vendor, "amount_evidence": selected[0][2]}


def spatial_text(boxes):
    """Join labels and amounts on the same physical row, preserving columns."""
    words = []
    for box, text, confidence, *_ in boxes:
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        words.append((min(xs), sum(ys) / len(ys), max(ys)-min(ys), str(text), float(confidence)))
    rows = []
    for word in sorted(words, key=lambda w: w[1]):
        compatible = [r for r in rows if abs(r[0][1]-word[1]) <= max(4, min(r[0][2], word[2])*.65)]
        if compatible:
            min(compatible, key=lambda r: abs(r[0][1]-word[1])).append(word)
        else:
            rows.append([word])
    text = "\n".join("  ".join(w[3] for w in sorted(row)) for row in rows)
    aligned = _dubai_summary_total(boxes, text)
    return text + ("\n" + aligned if aligned else "")


def _dubai_summary_total(boxes, text):
    """Align the complete five-row receipt summary across skewed columns.

    Require every label and exactly five right-column amounts in order, with
    consistent vertical displacement. Never fill a missing total from an item
    row, a subtotal, or arithmetic.
    """
    if vendor_name(text) != "Dubai Refreshment":
        return None
    labels = ("TOTALVALUEBEFORETAX", "EXCISETAX", "NETVALUEBEFOREVATAED",
              "VATAED", "TOTALGROSSAED")
    entries = []
    for box, value, *_ in boxes:
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        entries.append((min(xs), max(xs), sum(ys)/4, max(ys)-min(ys), str(value)))
    matched = []
    for label in labels:
        found = [e for e in entries if re.sub(r"[^A-Z]", "", e[4].upper()) == label]
        if len(found) != 1:
            return None
        matched.append(found[0])
    if any(a[2] >= b[2] for a, b in zip(matched, matched[1:])):
        return None
    height = max(e[3] for e in matched)
    right = max(e[1] for e in matched)
    amounts = sorted((e for e in entries if e[0] > right
                      and matched[0][2]-height*1.5 <= e[2] <= matched[-1][2]+height*.5
                      and re.fullmatch(MONEY, e[4].strip())), key=lambda e: e[2])
    if len(amounts) != len(labels):
        return None
    offsets = [a[2]-label[2] for a, label in zip(amounts, matched)]
    if max(abs(d) for d in offsets) > height*1.5 or max(offsets)-min(offsets) > height*.65:
        return None
    return "TOTAL/GROSS(AED)  " + amounts[-1][4].strip()


def _recognize(image):
    global _engine
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    # Serial inference avoids concurrent use of the mutable RapidOCR pipeline.
    with _engine_lock:
        if _engine is None:
            _engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        boxes, _ = _engine(np.asarray(image))
    return boxes or []


def _sideways(boxes):
    if not boxes:
        return False
    count = 0
    for box, *_ in boxes:
        count += abs(box[1][1]-box[0][1]) > abs(box[1][0]-box[0][0])
    tall = sum(max(p[1] for p in b[0])-min(p[1] for p in b[0]) >
               (max(p[0] for p in b[0])-min(p[0] for p in b[0]))*1.5
               for b in boxes if len(str(b[1])) > 3)
    return count > len(boxes)*.35 or tall > len(boxes)*.25


def read_image(image):
    from PIL import Image, ImageOps
    image = ImageOps.exif_transpose(image).convert("RGB")
    preview = image.copy()
    preview.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
    boxes = _recognize(preview)
    initial_text = spatial_text(boxes)
    initial = select_total(initial_text)
    if _sideways(boxes) or (initial["vendor_name"] == "M.H. Enterprises" and initial["amount_total"] is None):
        # Rotate only sideways pages; labels determine which direction is readable.
        choices = []
        for degrees in (90, 270):
            rotated = preview.rotate(degrees, expand=True)
            found = _recognize(rotated)
            text = spatial_text(found)
            parsed = select_total(text)
            choices.append((parsed["amount_total"] is not None, len(text), found, text, parsed, degrees))
            if parsed["amount_total"] is not None:
                break
        _, _, boxes, text, parsed, degrees = max(choices, key=lambda c: c[:2])
        image = image.rotate(degrees, expand=True)
    else:
        text = spatial_text(boxes)
        parsed = select_total(text)
    # Retry only when the actual target is missing, not because unrelated text is short.
    if parsed["amount_total"] is None and "Conflicting" not in parsed["amount_evidence"] and not _sideways(boxes):
        from .ocr_service import _preprocess_pil
        retry = _recognize(_preprocess_pil(image))
        retry_text = spatial_text(retry)
        retry_parsed = select_total(retry_text)
        if retry_parsed["amount_total"] is not None:
            boxes, text, parsed = retry, retry_text, retry_parsed
    if parsed["amount_total"] is None and "Conflicting" not in parsed["amount_evidence"]:
        from .ocr_service import _ocr_tesseract
        fallback_text, fallback_confidence = _ocr_tesseract(image, timeout=5)
        fallback = select_total(fallback_text)
        if fallback["amount_total"] is not None:
            fallback.update(raw_text=fallback_text[:20000], ocr_confidence=fallback_confidence,
                            ocr_method=VERSION+"-tesseract")
            return fallback
    parsed.update(raw_text=text[:20000], ocr_confidence=round(sum(float(b[2]) for b in boxes)/len(boxes), 3) if boxes else 0,
                  ocr_method=VERSION)
    return parsed


def read_summary(image):
    """Fast first pass over the area containing payable summaries in these layouts."""
    from PIL import Image, ImageOps
    image = ImageOps.exif_transpose(image).convert("RGB")
    w, h = image.size
    crop = image.crop((int(w*.40), int(h*.30), w, int(h*.93)))
    crop.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
    boxes = _recognize(crop)
    if _sideways(boxes):
        return None
    text = spatial_text(boxes)
    result = select_total(text)
    if result["amount_total"] is None:
        return None
    result.update(raw_text=text[:20000], ocr_method=VERSION+"-summary",
                  ocr_confidence=round(sum(float(b[2]) for b in boxes)/len(boxes),3) if boxes else 0)
    return result


def validate_result(result):
    """Check another printing of the final amount, never subtotal/VAT or Excel."""
    if result.get("amount_total") is None:
        return result
    small = dict(zip(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), range(20)))
    small.update(dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10))))
    pattern = r"\bAED\s*[*:\-]*\s*([A-Za-z -]+?)\s+and\s+(\d{1,2})\s*/\s*100\b"
    for match in re.finditer(pattern, result.get("raw_text", ""), re.I):
        words = match.group(1).lower().replace("-", " ").split()
        if not words or any(w not in small and w not in ("and", "hundred", "thousand", "million") for w in words):
            continue
        total, group = 0, 0
        for word in words:
            if word in small:
                group += small[word]
            elif word == "hundred":
                group = (group or 1)*100
            elif word in ("thousand", "million"):
                total += group * (1000 if word == "thousand" else 1000000)
                group = 0
        written = Decimal(total + group) + Decimal(match.group(2))/100
        if written != Decimal(result["amount_total"]):
            return {**result, "amount_total": None,
                    "amount_evidence": "Final amount digits conflict with amount in words; clearer scan or manual review required"}
    return result


def extract_amount(raw, filename=""):
    import io
    from PIL import Image
    if raw[:4] != b"%PDF" and not filename.lower().endswith(".pdf"):
        with Image.open(io.BytesIO(raw)) as image:
            return validate_result(read_summary(image) or read_image(image))
    import pymupdf
    results = []
    with pymupdf.open(stream=raw, filetype="pdf") as document:
        for page in document:
            text = page.get_text("text", sort=True)
            # Scanned PDFs often have an inaccurate hidden OCR text layer.
            # Read the visible scan whenever a large image covers the page.
            scanned = any(pymupdf.Rect(info["bbox"]).get_area() > page.rect.get_area()*.5
                          for info in page.get_image_info())
            parsed = select_total(text)
            if not scanned and parsed["amount_total"] is not None:
                parsed.update(raw_text=text[:20000], ocr_method=VERSION+"-pdf-text", ocr_confidence=1.0)
            else:
                # Cap pixel dimensions: some scanner PDFs declare enormous pages.
                # Fixed 300 DPI can otherwise allocate more than 100 million pixels.
                zoom = min(200/72, 2600/max(page.rect.width, page.rect.height))
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                                      colorspace=pymupdf.csRGB, alpha=False)
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                try:
                    parsed = read_summary(image) or {"amount_total":None,"vendor_name":"Unknown","raw_text":""}
                finally:
                    image.close()
            results.append(parsed)
        if not any(r.get("amount_total") is not None for r in results):
            results = []
            for page in document:
                zoom = min(200/72, 2600/max(page.rect.width, page.rect.height))
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
                with Image.frombytes("RGB", (pix.width, pix.height), pix.samples) as image:
                    results.append(read_image(image))
    found = [r for r in results if r.get("amount_total") is not None]
    if found and len({Decimal(r["amount_total"]) for r in found}) == 1:
        return validate_result(found[-1])
    return {"amount_total": None, "ocr_method": VERSION, "ocr_confidence": 0.0,
            "amount_evidence": "Conflicting page totals" if found else "No explicit final total found",
            "vendor_name": next((r["vendor_name"] for r in results if r["vendor_name"] != "Unknown"), "Unknown"),
            "raw_text": "\n".join(r.get("raw_text", "") for r in results)[:20000]}
