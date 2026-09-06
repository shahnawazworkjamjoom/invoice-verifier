# KOJ Invoice Verifier

A small Windows Tkinter application that reads the existing purchase-order Excel format, downloads every linked invoice, and uses local OCR for one of two outcomes:

- `APPROVE`: the OCR final bill amount exactly equals Excel Finance Amount To Pay.
- `DECLINE`: the amounts differ or either amount cannot be confirmed.

The application never writes to the uploaded Excel workbook. **Export result** creates a separate `.xlsx` copy with the exact original columns and sheet structure. It updates only the existing **Approver Action** cells. **Finance Amount To Pay is never changed**, and no columns are added.

## Performance

Each run downloads all selected invoices first, with up to four simultaneous downloads. Files are mapped by Excel row, then OCR verification runs one invoice at a time in row order. A failed download declines only its own row. Stop cancels queued downloads and waits for active work before cleanup.

Downloads live in a temporary batch folder that is deleted after completion, cancellation, or failure. No persistent download or OCR-result cache is used. Each new run downloads and reads invoices again. Open Invoice opens the original attachment URL in your browser.

The verification OCR mode skips date recovery and line-item parsing. It uses the printed final total and does not reconstruct a missing total from subtotal and VAT. PDF handling is unchanged.

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
