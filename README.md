# KOJ Invoice Verifier

A small Windows Tkinter application that reads the existing purchase-order Excel format, downloads every linked invoice, and sends the invoice images plus that invoice's Excel-row data to NVIDIA Vision for one of two outcomes:

- `APPROVE`: all required invoice evidence matches.
- `DECLINE`: required invoice evidence differs or cannot be confirmed.

The application never writes to the uploaded Excel workbook. **Export result** creates a separate `.xlsx` copy with the exact original columns and sheet structure. It populates the existing **Approver Action** cells and, only when necessary, corrects the existing **Finance Amount To Pay** cell to the invoice's final amount due after discount, VAT, and other charges. No columns are added.

## Performance

Invoices are processed strictly one at a time. The app downloads and verifies one invoice, completes its result, and only then starts the next invoice. NVIDIA results are not cached, so every verification uses the current invoice and Excel values.

## NVIDIA Vision verification

The application uses NVIDIA Vision as the complete active verification path. The local OCR implementation remains in `invoice_verifier/ocr` for future use, but the application never calls it. The default model is the smaller free endpoint `meta/llama-3.2-11b-vision-instruct`; the text-only `nvidia/nemotron-3.5-lightning-30b-a3b` model cannot read invoice images. Temporary timeouts, rate limits, and NVIDIA server errors are retried up to three times before the row is declined as a technical failure.

For multi-page PDFs, only the first and last pages are rendered at a 1500-pixel page width and combined into one image. This captures invoice identity/header data and final totals while skipping middle line-item pages. Each request contains exactly one invoice and its matching Excel row.

The compact vision prompt requests only the decision, reasons, supplier/location, invoice/order numbers, invoice date, net amount, VAT, final amount due, and field checks. The application first repairs malformed JSON locally and can also parse a labeled response. Only if both local recovery methods fail does `nvidia/nemotron-3.5-lightning-30b-a3b` make a second formatting request; it does not run OCR or make a new invoice decision.

If the first vision result is incomplete or reports a critical mismatch, the app performs one focused confirmation using only the last invoice page. Placeholder/null output can never be approved. For Barakat summaries, the PO comparison uses the labeled footer net before VAT (including excise), and Finance Amount To Pay uses the final amount due after VAT.

Create a fresh NVIDIA API key and expose it to the application as the `NVIDIA_API_KEY` Windows user environment variable. Do not store API keys in source files. You can optionally override the model with `NVIDIA_VISION_MODEL`. Restart the application after setting the variable. The status line will show **NVIDIA Vision ready** when enabled. Verification will not start without the key because local OCR fallback is disabled.

Invoices and their matching Excel-row values are sent to NVIDIA's hosted API. Confirm that this processing is permitted by your company's invoice-data and vendor-data policies before enabling it.

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

## Verification fields

The model receives supplier, brand, location, order number, invoice number, invoice date, PO amount, Finance Amount To Pay, payment status, received quantity, and tax code from the matching Excel row. It compares those values with the invoice images and returns structured JSON containing the decision, short reasons, final amount due, extracted invoice fields, and field-level evidence. Currency is fixed to AED and VAT is fixed to 5%, so missing printed currency/tax text alone does not cause a decline. Received quantity is informational unless invoice and receipt units are directly comparable.

Finance Amount To Pay is the only auto-correctable field. It must use the invoice's final payable amount after discounts, excise, VAT, and other charges—not the subtotal or total net amount. If this amount alone is wrong, the app corrects the existing cell and approves. Supplier, location, invoice number, order number, invoice date, PO net amount, currency, or VAT mismatches still decline.
