# KOJ Invoice Verifier

A small Windows Tkinter application that reads the existing purchase-order Excel format, downloads every linked invoice, and uses local OCR for one of two outcomes:

- `APPROVE`: the OCR final bill amount exactly equals Excel Finance Amount To Pay.
- `DECLINE`: the amounts differ or either amount cannot be confirmed.

The application never writes to the uploaded Excel workbook. **Export result** creates a separate `.xlsx` copy with the exact original columns and sheet structure. It updates only the existing **Approver Action** cells. **Finance Amount To Pay is never changed**, and no columns are added.

## Performance

Each run downloads all selected invoices first, with up to four simultaneous downloads. Files are mapped by Excel row, then OCR verification runs one invoice at a time in row order. A failed download declines only its own row. Stop cancels queued downloads and waits for active work before cleanup.

Downloads live in a temporary batch folder that is deleted after completion, cancellation, or failure. No persistent download or OCR-result cache is used. Each new run downloads and reads invoices again. Open Invoice opens the original attachment URL in your browser.

The dedicated `amount-ocr-v2` reader skips date recovery and line-item parsing. It groups recognized words by their position so final-total labels stay attached to the correct amount. It has layout rules for Barakat Quality Plus, Abu Dhabi Refreshments, Dubai Refreshment, Mohebi Logistics, and M.H. Enterprises, plus common payable-total labels.

Scanned PDFs are read from their visible images even if they contain a faulty hidden text layer. Rendered pages are capped at 2600 pixels on the longest side, and the initial OCR pass uses at most 1800 pixels. A fast pass reads the summary area on every page, including Barakat page 2. If no total is found, the reader examines full pages, rotates sideways scans, and tries contrast and time-limited Tesseract fallback. Clean text PDFs retain direct text extraction. Multiple conflicting final totals decline.

The reader uses existing pretrained weights through RapidOCR 3.x (English det PP-OCRv4 mobile + English rec PP-OCRv5 mobile, receipt-tuned detection, legacy 1.4.4 fallback), with two ONNX threads per operation. These are extraction and runtime improvements, not neural-network training. It never receives the Excel amount and never reconstructs a missing final total from subtotal, VAT, or line items.

## Local OCR verification

The active path is local RapidOCR, with Tesseract available as a fallback. NVIDIA Vision code remains in `invoice_verifier/nvidia_vision.py` but is on hold and is not called by the application.

OCR reads the final bill amount due after discounts, VAT, and other charges. Only this amount is compared with the existing Excel Finance Amount To Pay. Order numbers and other fields do not affect the decision.

The decision rules are:

- Exact numeric amount match: approve (240.6 and 240.60 are equal).
- Any difference: decline, with no tolerance or rounding before comparison.
- Missing, invalid, or unreadable amounts: decline.

No NVIDIA API key is required while OCR mode is active.

## Setup

From PowerShell in this folder:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run with:

```powershell
.\run.bat
```

Click **Upload Excel**, then **Verify all** or select rows and use **Verify selected**. Downloaded invoices are temporary; generated reports are saved wherever you choose.

Export preserves the original workbook structure and amounts, changing only the existing Approver Action cells.

## Evaluation

See [the OCR v2 evaluation](reports/ocr_v2_evaluation.md) for the 78-invoice benchmark, vendor coverage, manually checked cases, and remaining scan limitations.
