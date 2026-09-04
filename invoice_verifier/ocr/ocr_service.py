# -*- coding: utf-8 -*-
"""Free, best-accuracy OCR pipeline.

Strategy (no paid API, Windows compatible):
1. PDF with text layer -> PyMuPDF / pdfplumber direct text (100% accurate).
2. Scanned PDF / image -> render to image -> preprocess (Pillow/OpenCV)
   -> RapidOCR-onnxruntime (primary) -> pytesseract (fallback).
3. Parse with ocr_parser.

All heavy imports are lazy + optional so the module installs even
when OCR libs are missing (user can still manually fill the preview).
"""
import base64
import io
import logging
import os
import re
import threading

from . import ocr_parser

_logger = logging.getLogger(__name__)
_rapidocr_engine = None
_rapidocr_lock = threading.Lock()


def _decode_file(file_b64):
    if not file_b64:
        return b''
    if isinstance(file_b64, str):
        file_b64 = file_b64.encode()
    try:
        return base64.b64decode(file_b64)
    except Exception:
        return b''


def _is_pdf(filename, data):
    if filename and filename.lower().endswith('.pdf'):
        return True
    return data[:4] == b'%PDF'


def _pdf_text_pymupdf(data):
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=data, filetype='pdf')
        texts = []
        for page in doc:
            texts.append(page.get_text('text') or '')
        doc.close()
        return '\n'.join(texts)
    except Exception as e:
        _logger.info('PyMuPDF text extract failed: %s', e)
        return ''


def _pdf_text_pdfplumber(data):
    try:
        import pdfplumber
        texts = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                texts.append(page.extract_text() or '')
        return '\n'.join(texts)
    except Exception as e:
        _logger.info('pdfplumber extract failed: %s', e)
        return ''


def _pdf_to_images(data, dpi=300):
    """Render PDF pages to PIL images via PyMuPDF (no poppler needed)."""
    images = []
    try:
        import fitz
        doc = fitz.open(stream=data, filetype='pdf')
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            img_data = pix.tobytes('png')
            from PIL import Image
            images.append(Image.open(io.BytesIO(img_data)).convert('RGB'))
        doc.close()
    except Exception as e:
        _logger.warning('PDF render failed: %s', e)
    return images


def _load_image(data):
    try:
        from PIL import Image
        # Odoo imports Pillow with a restricted plugin set before this module is
        # loaded. Explicitly register WebP so purchase orders uploaded in that
        # format are recognized inside the Odoo worker as well as standalone.
        try:
            from PIL import WebPImagePlugin  # noqa: F401
        except Exception:
            pass
        return Image.open(io.BytesIO(data)).convert('RGB')
    except Exception as e:
        _logger.warning('Image load failed: %s', e)
        return None


def _preprocess_pil(img):
    """Upscale + grayscale + denoise to boost OCR accuracy."""
    try:
        from PIL import Image
        w, h = img.size
        # upscale small images to ~2500px width
        if w < 2000:
            scale = 2000 / max(w, 1)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        try:
            import cv2
            import numpy as np
            arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
            arr = cv2.medianBlur(arr, 3)
            _, arr = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            from PIL import Image as _I
            return _I.fromarray(arr).convert('RGB')
        except Exception:
            return img.convert('L').convert('RGB')
    except Exception:
        return img


def _ocr_rapidocr(img):
    global _rapidocr_engine
    try:
        from rapidocr_onnxruntime import RapidOCR
        import numpy as np
        # Model creation is expensive and does not improve accuracy when
        # repeated. Reuse the same immutable model weights for every page.
        if _rapidocr_engine is None:
            with _rapidocr_lock:
                if _rapidocr_engine is None:
                    _rapidocr_engine = RapidOCR()
        engine = _rapidocr_engine
        result, _ = engine(np.array(img))
        if not result:
            return '', 0.0, False
        texts, confs, vertical_boxes = [], [], 0
        for item in result:
            # item: [box, text, conf]
            if len(item) >= 3:
                texts.append(str(item[1]))
                try:
                    confs.append(float(item[2]))
                except Exception:
                    pass
                try:
                    box = item[0]
                    dx = abs(float(box[1][0]) - float(box[0][0]))
                    dy = abs(float(box[1][1]) - float(box[0][1]))
                    vertical_boxes += int(dy > dx)
                except Exception:
                    pass
        avg = sum(confs) / len(confs) if confs else 0.0
        is_sideways = bool(result) and vertical_boxes / len(result) > 0.55
        return '\n'.join(texts), avg, is_sideways
    except Exception as e:
        _logger.info('RapidOCR not available/failed: %s', e)
        return '', 0.0, False


def _reading_order_score(text):
    """Prefer a rotation whose header, item table and totals read top-to-bottom."""
    value = (text or '').upper()
    score = len(value) / 1000.0
    header = min((value.find(k) for k in ('TAX INVOICE', 'TAXINVOICE') if value.find(k) >= 0), default=-1)
    items = min((value.find(k) for k in ('ITEM DESCRIPTION', 'DESCRIPTION', 'SALES') if value.find(k) >= 0), default=-1)
    total = min((value.find(k) for k in ('GROSS TOTAL', 'TOTAL AMOUNT', 'TOTAL NET') if value.find(k) >= 0), default=-1)
    if 0 <= header < items:
        score += 8
    if 0 <= items < total:
        score += 8
    if header >= 0 and header < len(value) * .35:
        score += 3
    return score


def _ocr_tesseract(img):
    try:
        import pytesseract
        # allow explicit binary path via env (Windows default install)
        custom_bin = os.environ.get('TESSERACT_CMD')
        if custom_bin:
            pytesseract.pytesseract.tesseract_cmd = custom_bin
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config='--psm 6')
        words = []
        confs = []
        for i, w in enumerate(data.get('text', [])):
            w = (w or '').strip()
            if not w:
                continue
            words.append(w)
            try:
                c = float(data['conf'][i])
                if c >= 0:
                    confs.append(c)
            except Exception:
                pass
        text = pytesseract.image_to_string(img, config='--psm 6')
        avg = (sum(confs) / len(confs) / 100.0) if confs else 0.0
        return text or ' '.join(words), avg
    except Exception as e:
        _logger.info('Tesseract not available/failed: %s', e)
        return '', 0.0


def _ocr_image(img):
    # Try the original/upscaled image first. Aggressive thresholding can erase
    # faint dot-matrix invoices (notably the Abu Dhabi Refreshments layout).
    w, h = img.size
    original = img
    if w < 2000:
        from PIL import Image
        scale = 2000 / max(w, 1)
        original = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    text, conf, sideways = _ocr_rapidocr(original)
    method = 'rapidocr'
    upper_text = (text or '').upper()
    scrambled_layout = ('ITEM DESCRIPTION' in upper_text and 'TAX INVOICE' in upper_text
                         and upper_text.find('ITEM DESCRIPTION') < upper_text.find('TAX INVOICE'))
    if sideways or scrambled_layout:
        candidates = [(text, conf, original)]
        for degrees in (90, 270):
            rotated = original.rotate(degrees, expand=True)
            rotated_text, rotated_conf, _ = _ocr_rapidocr(rotated)
            candidates.append((rotated_text, rotated_conf, rotated))
        text, conf, original = max(candidates, key=lambda item: _reading_order_score(item[0]))
        method = 'rapidocr-auto-rotate'
    if len((text or '').strip()) < 200 or conf < 0.70:
        processed = _preprocess_pil(img)
        t_processed, c_processed, _ = _ocr_rapidocr(processed)
        if len((t_processed or '').strip()) * max(c_processed, .25) > len((text or '').strip()) * max(conf, .25):
            text, conf = t_processed, c_processed
    if len((text or '').strip()) < 20:
        t2, c2 = _ocr_tesseract(original)
        if len((t2 or '').strip()) > len((text or '').strip()):
            text, conf, method = t2, c2, 'tesseract'
    # Faint dot-matrix forms can lose the right-hand header even when the body
    # OCR is otherwise excellent. Re-read that region at higher contrast so
    # invoice dates and numbers remain available to the parser.
    upper = (text or '').upper()
    if 'INVOICE' in upper and not re.search(r'INVOICE\s*DATE', upper):
        from PIL import Image, ImageEnhance
        # Downsampling first suppresses the dot-matrix texture, then enlarging
        # the crop presents clean glyphs to the recognizer.
        aspect = original.height / max(original.width, 1)
        header_source = original.resize((713, int(713 * aspect)), Image.LANCZOS)
        ow, oh = header_source.size
        header = header_source.crop((int(ow * .45), int(oh * .12), ow, int(oh * .33)))
        header = header.resize((int(ow * 1.1), int(oh * .42)), Image.LANCZOS)
        header = ImageEnhance.Contrast(header).enhance(2.0)
        header_text, header_conf, _ = _ocr_rapidocr(header)
        if header_text:
            text = (text or '') + '\n' + header_text
            conf = max(conf, header_conf)
    if not (text or '').strip():
        method = 'none'
    return text or '', conf, method


def extract_document(file_b64, filename='', doc_type='lpo'):
    """Main entry. Returns dict ready for preview wizard."""
    raw = _decode_file(file_b64)
    full_text, method, confidence = '', 'none', 0.0

    if not raw:
        parsed = ocr_parser.parse_document_text('', doc_type)
        parsed.update({'ocr_method': 'none', 'ocr_confidence': 0.0})
        return parsed

    if _is_pdf(filename, raw):
        # 1) try digital text layer (fast + exact)
        t1 = _pdf_text_pymupdf(raw)
        if len((t1 or '').strip()) < 200:
            t2 = _pdf_text_pdfplumber(raw)
            if len((t2 or '').strip()) > len((t1 or '').strip()):
                t1 = t2
        if len((t1 or '').strip()) >= 200 or any(
                k in (t1 or '').lower() for k in ('total', 'subtotal', 'vat', 'tax', 'qty', 'quantity')):
            full_text, method, confidence = t1, 'pdf-text', 1.0
        else:
            # 2) scanned PDF -> OCR each page
            pages = _pdf_to_images(raw)
            chunks, confs = [], []
            for pg in pages[:10]:
                tx, cf, md = _ocr_image(pg)
                chunks.append(tx)
                confs.append(cf)
                method = md
            full_text = '\n'.join(chunks)
            confidence = sum(confs) / len(confs) if confs else 0.0
            method = 'ocr-' + method
    else:
        img = _load_image(raw)
        if img is not None:
            full_text, confidence, md = _ocr_image(img)
            method = 'ocr-' + md

    parsed = ocr_parser.parse_document_text(full_text or '', doc_type)
    parsed.update({'ocr_method': method, 'ocr_confidence': round(float(confidence or 0.0), 3)})
    return parsed


def fuzzy_score(a, b):
    a, b = (a or '').strip().lower(), (b or '').strip().lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 100.0
    try:
        from rapidfuzz import fuzz
        return float(fuzz.token_set_ratio(a, b))
    except Exception:
        # lightweight fallback
        import difflib
        return float(difflib.SequenceMatcher(None, a, b).ratio() * 100)
