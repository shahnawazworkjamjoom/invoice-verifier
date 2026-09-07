"""Invoice amount OCR v3: spatial totals, bounded CPU work, no result cache.

Vendor rules select printed totals. They never receive Excel payment amounts.
Model weights are pretrained (rapidocr 3.x: en det PP-OCRv4 mobile +
en rec PP-OCRv5 mobile, legacy 1.4.4 fallback); this module does not
train a model.
"""
from __future__ import annotations

import re
import threading
from decimal import Decimal

VERSION = "amount-ocr-v3"
_engine = None
_engine_lock = threading.Lock()
_ENGINE_KIND = None


def _build_engine():
    """Prefer rapidocr 3.x (en PP-OCRv5 mobile, receipt-tuned detection).

    Falls back to legacy rapidocr-onnxruntime 1.4.4 when the new package
    is unavailable. Returns (engine, kind) where kind is 'v3' or 'legacy'.
    """
    global _ENGINE_KIND
    try:
        from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR
        engine = RapidOCR(params={
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.lang_type": LangDet.EN,
            "Det.model_type": ModelType.MOBILE,
            "Det.ocr_version": OCRVersion.PPOCRV4,
            # Faint thermal/dot-matrix print needs higher recall.
            "Det.box_thresh": 0.4,
            "Det.thresh": 0.25,
            "Det.limit_side_len": 960,
            "Det.unclip_ratio": 1.8,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.lang_type": LangRec.EN,
            "Rec.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
        })
        _ENGINE_KIND = "v3"
        return engine
    except Exception:
        from rapidocr_onnxruntime import RapidOCR as LegacyRapidOCR
        _ENGINE_KIND = "legacy"
        return LegacyRapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
MONEY = r"(?<![\d.,+\-])(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?![\d.,%])"


def _normalize_amount_line(line):
    """Fix common thermal/scan merges without touching true digits.

    - '230.185%' -> '230.18 5%' (net amount glued to VAT rate).
    - '928 31' on a total line -> '928.31' (dot lost to blur).
    - '115,44' -> '115.44' (comma read as decimal separator).
    Only applied to the working copy used for number search.
    Space/comma fixes run only when no standard dotted amount is
    already present, so '16 74' across two valid amounts is never
    rewritten into '16.74'.
    """
    fixed = re.sub(r"(\d+\.\d{2})(5\s?%)(?![\d])", r"\1 \2", line)
    fixed = re.sub(r"(\d+\.\d{2})(\d\s?%)(?![\d])", r"\1 \2", fixed)
    if re.search(MONEY, fixed):
        return fixed
    if re.search(r"TOTAL|GROSS|NET|VAT|AMOUNT|DUE|PAYABLE", fixed.upper()):
        fixed = re.sub(r"(?<![\d.])(\d{2,4})[ \t]+(\d{2})(?![\d.])", r"\1.\2", fixed)
        if not re.search(MONEY, fixed):
            fixed = re.sub(r"(?<!\d)(\d{1,4}),(\d{2})(?!\d)", r"\1.\2", fixed)
    return fixed


def _clean_amount_token(token):
    """Normalize one OCR box value to dotted decimals for Dubai columns."""
    value = (token or "").strip().strip("|lI:;")
    if re.fullmatch(r"\d{2,4}[ \t]+\d{2}", value):
        value = re.sub(r"[ \t]+", ".", value)
    if re.fullmatch(r"\d{1,4},\d{2}", value):
        value = value.replace(",", ".")
    match = re.search(MONEY, value)
    return match.group(0) if match else None


def _fuzzy_label(entries, canonical, used):
    """Match one Dubai summary label allowing 1-2 OCR letter errors."""
    best, best_score = None, 0
    second = 0
    try:
        from rapidfuzz import fuzz as _fuzz

        def _score(a, b):
            return _fuzz.ratio(a, b)
    except Exception:
        import difflib as _difflib

        def _score(a, b):
            return _difflib.SequenceMatcher(None, a, b).ratio() * 100
    for entry in entries:
        if id(entry) in used:
            continue
        compact = re.sub(r"[^A-Z]", "", entry[4].upper())
        # Exact matches and common single-letter confusions win immediately.
        normalized = compact.replace("0", "O").replace("1", "I").replace("5", "S")
        target = canonical.replace("0", "O").replace("1", "I").replace("5", "S")
        if compact == canonical or normalized == target:
            return entry, 100
        score = _score(compact, canonical)
        if score > best_score:
            second, best, best_score = best_score, entry, score
        elif score > second:
            second = score
    if best is not None and best_score >= 88 and (best_score - second) >= 3:
        return best, best_score
    return None, 0


def vendor_name(text):
    compact = re.sub(r"[^A-Z]", "", text.upper())
    for marker, name in (("BARAKAT", "Barakat Quality Plus"),
                         ("ABUDHABIREFRESHMENTS", "Abu Dhabi Refreshments"),
                         ("DUBAIREFRESHMENT", "Dubai Refreshment"),
                         ("DUBRIREFRESHMENT", "Dubai Refreshment"),
                         ("DUBAREFRESHMENT", "Dubai Refreshment"),
                         ("MOHEBI", "Mohebi Logistics"),
                         ("MHENTERPRISES", "M.H. Enterprises")):
        if marker in compact:
            return name
    # Thermal receipts often misread DUBAI as DUBRI; fall back to layout markers.
    if "REFRESHMENT" in compact and ("PEPSIDRC" in compact or "CREDITINVOICENO" in compact):
        return "Dubai Refreshment"
    if "PEPSIDRC" in compact and "TAXINVOICE" in compact:
        return "Dubai Refreshment"
    return "Unknown"


def _looks_like_dubai(text, entries=None):
    """Dubai thermal layout even when the header is misread (DUBRI)."""
    if vendor_name(text) == "Dubai Refreshment":
        return True
    compact = re.sub(r"[^A-Z]", "", text.upper())
    markers = ("TOTALVALUEBEFORETAX", "EXCISETAX", "NETVALUEBEFOREVAT", "TOTALGROSS")
    hits = sum(1 for marker in markers if marker in compact)
    if hits >= 3:
        return True
    if entries is not None:
        joined = " ".join(re.sub(r"[^A-Z]", "", str(value).upper()) for _, _, _, _, value in
                          [(e[0], e[1], e[2], e[3], e[4]) for e in entries])
        if sum(1 for marker in markers if marker in joined) >= 3:
            return True
    return False


def select_total(text):
    """Select explicit payable labels; ambiguous amounts are not guessed."""
    vendor = vendor_name(text)
    compact_text = re.sub(r"[^A-Z]", "", text.upper())
    if vendor == "Unknown" and all(label in compact_text for label in
                                  ("TOTALSALESAMOUNT", "INVOICEDISCOUNT", "TOTALNETAMOUNT")):
        vendor = "Abu Dhabi Refreshments"
    candidates = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
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
        numbers = re.findall(MONEY, _normalize_amount_line(line))
        evidence = line.strip()
        # A table summary must contain several amounts, unlike a plain label.
        table_summary = ((vendor == "Mohebi Logistics" and "TOTALDIRHAMS" in compact)
                         or (vendor == "M.H. Enterprises" and "TOTALAMOUNT" in compact
                             and "DUE" not in compact))
        if table_summary and len(numbers) < 3 and index + 1 < len(lines):
            # Wrapped footer: 'Total Dirhams ... Sixty-Four' on one row and
            # '27.000 3,537.75 176.89 3,714.64' on the next. Joining is safe
            # here because the net+tax==gross arithmetic check below must pass.
            combined = line + "  " + lines[index + 1]
            combined_numbers = re.findall(MONEY, _normalize_amount_line(combined))
            if len(combined_numbers) >= 3:
                numbers, evidence = combined_numbers, combined.strip()
        if not numbers:
            continue
        if table_summary and len(numbers) < 3:
            continue
        if table_summary:
            net, tax, gross = [Decimal(n.replace(",", "")) for n in numbers[-3:]]
            if abs((net + tax) - gross) > Decimal("0.01"):
                continue
        value = Decimal(numbers[-1].replace(",", ""))
        if value > 0:
            candidates.append((rank, value, evidence))
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
    entries_early = []
    for box, value, *_ in boxes:
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        entries_early.append((min(xs), max(xs), sum(ys)/4, max(ys)-min(ys), str(value)))
    if not _looks_like_dubai(text, entries_early):
        return None
    labels = ("TOTALVALUEBEFORETAX", "EXCISETAX", "NETVALUEBEFOREVATAED",
              "VATAED", "TOTALGROSSAED")
    entries = []
    for box, value, *_ in boxes:
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        entries.append((min(xs), max(xs), sum(ys)/4, max(ys)-min(ys), str(value)))
    matched = []
    used = set()
    for label in labels:
        found, _ = _fuzzy_label(entries, label, used)
        if found is None:
            return None
        used.add(id(found))
        matched.append(found)
    if any(a[2] >= b[2] for a, b in zip(matched, matched[1:])):
        return None
    height = max(e[3] for e in matched)
    right = max(e[1] for e in matched)
    cleaned = []
    for entry in entries:
        if entry[0] > right and matched[0][2]-height*2.0 <= entry[2] <= matched[-1][2]+height*.8:
            amount = _clean_amount_token(entry[4])
            if amount:
                cleaned.append((entry[0], entry[1], entry[2], entry[3], amount))
    amounts = sorted(cleaned, key=lambda e: e[2])
    if len(amounts) != len(labels):
        return None
    offsets = [a[2]-label[2] for a, label in zip(amounts, matched)]
    if max(abs(d) for d in offsets) > height*2.0 or max(offsets)-min(offsets) > height*1.0:
        return None
    return "TOTAL/GROSS(AED)  " + amounts[-1][4].strip()


def _recognize(image):
    global _engine
    import numpy as np
    # Serial inference avoids concurrent use of the mutable RapidOCR pipeline.
    with _engine_lock:
        if _engine is None:
            _engine = _build_engine()
        engine, kind = _engine, _ENGINE_KIND
        if kind == "v3":
            out = engine(np.asarray(image))
            boxes, txts, scores = out.boxes, out.txts, out.scores
            if boxes is None or len(boxes) == 0:
                return []
            adapted = []
            for box, text, conf in zip(boxes, txts, scores):
                pts = [[float(v) for v in pt] for pt in box]
                adapted.append([pts, str(text), float(conf)])
            return adapted
        boxes, _ = engine(np.asarray(image))
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


def _remove_blue_ink(image):
    """Suppress blue ballpoint ticks/signatures that cover thermal print."""
    try:
        import cv2
        import numpy as np
        from PIL import Image as _Image
        arr = np.asarray(image.convert("RGB"))
        hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
        # Blue pen: H ~90-130, moderate saturation/value.
        mask = cv2.inRange(hsv, np.array([85, 60, 40]), np.array([135, 255, 255]))
        mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
        cleaned = arr.copy()
        cleaned[mask > 0] = (255, 255, 255)
        return _Image.fromarray(cleaned)
    except Exception:
        return image


def _enhance_thermal(image):
    """High-contrast variant for faint thermal/dot-matrix receipts."""
    try:
        import cv2
        import numpy as np
        from PIL import Image as _Image
        arr = np.asarray(image.convert("RGB"))
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        gray = cv2.bilateralFilter(gray, 5, 50, 50)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return _Image.fromarray(binary).convert("RGB")
    except Exception:
        return image


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
    if parsed["amount_total"] is None and "Conflicting" not in parsed["amount_evidence"]:
        # Upside-down phone scans (e.g. MH page 2 of 2) are not sideways;
        # their glyphs are inverted, so OCR returns short garbage text.
        # Try 180 degrees once and keep it only when it yields a total.
        upside = preview.rotate(180, expand=True)
        found = _recognize(upside)
        upside_text = spatial_text(found)
        upside_parsed = select_total(upside_text)
        if upside_parsed["amount_total"] is not None:
            boxes, text, parsed = found, upside_text, upside_parsed
            image = image.rotate(180, expand=True)
    # Retry only when the actual target is missing, not because unrelated text is short.
    if parsed["amount_total"] is None and "Conflicting" not in parsed["amount_evidence"] and not _sideways(boxes):
        from .ocr_service import _preprocess_pil
        for variant in (_preprocess_pil(image), _enhance_thermal(image), _remove_blue_ink(image)):
            try:
                retry = _recognize(variant)
            except Exception:
                continue
            retry_text = spatial_text(retry)
            retry_parsed = select_total(retry_text)
            if retry_parsed["amount_total"] is not None:
                boxes, text, parsed = retry, retry_text, retry_parsed
                break
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
