# KOJ Invoice Verifier

A small Windows Tkinter application that reads the existing purchase-order Excel format, downloads every linked invoice, and uses local OCR for one of two outcomes:

- `APPROVE`: the OCR final bill amount exactly equals Excel Finance Amount To Pay.
- `DECLINE`: the amounts differ or either amount cannot be confirmed.

The application never writes to the uploaded Excel workbook. **Export result** creates a separate `.xlsx` copy with the exact original columns and sheet structure. It updates only the existing **Approver Action** cells. **Finance Amount To Pay is never changed**, and no columns are added.

## Performance

Invoices are processed strictly one at a time. The app downloads and verifies one invoice, completes its result, and only then starts the next invoice.

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

Click **Upload Excel**, then **Verify all** or select rows and use **Verify selected**. Downloaded invoices are cached in `data/downloads`; generated reports are saved wherever you choose.

Export preserves the original workbook structure and amounts, changing only the existing Approver Action cells.
