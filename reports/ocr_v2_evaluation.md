# Invoice OCR v2 evaluation

Source: PurchaseOrderAttachment-09032026-0424PM.xlsx (73 attachments) plus the five additional invoice PDFs supplied by the user. All 78 files have distinct SHA-256 hashes.

## Measured results

- Previous reader: 817.2 seconds (13.62 minutes).
- New reader: 554.2 seconds (9.24 minutes).
- Time reduction: 32.2%.
- Printed totals returned: 65/78 before; 74/78 after.
- On 25 visually checked cases: 11 correct outcomes before; 23 after. This includes two attachments with no final total, where no amount is the correct outcome.

Timing is from one complete sequential run of each version on this computer, including file reading and the first model initialization, excluding network downloads. The final amount-in-words consistency check was replayed on the complete OCR outputs and its measured time added; the recognition pass was not rerun for this text-only check. Development trial runs are excluded. The visual checks were selected for vendor coverage and known difficult cases, not as a random or held-out accuracy sample. Remaining returned totals have not all been independently confirmed. These results do not establish accuracy on unseen invoices. Excel Amount To Pay was not used as a training label or supplied to the OCR reader.

## Vendors used for tuning and evaluation

| Vendor | Invoices evaluated | Visually checked |
|---|---:|---:|
| Barakat Quality Plus | 65 | 15 |
| Abu Dhabi Refreshments | 10 | 7 |
| Dubai Refreshment | 1 | 1 |
| Mohebi Logistics | 1 | 1 |
| M.H. Enterprises | 1 | 1 |

This is a rebuilt extraction pipeline using existing pretrained PP-OCRv4 weights in RapidOCR 1.4.4 and ONNX Runtime 1.29.0. No neural-network weights were trained or fine-tuned. Vendor tuning means summary-label and table-layout rules, orientation handling, and extraction tests.

## Barakat pages and attachments needing review

The 65 Barakat attachments comprise 37 two-page PDFs, 26 one-page PDFs, and two image files. The reader checks every PDF page for the final amount due. It does not substitute Total net amount or add up line items.

- Excel row 40, invoice 1102-SOIN-07848236: attachment contains only the page marked Page 1 of 2, with no final total.
- Excel row 72, invoice 1102-SOIN-07840041: a single photo of Page 1 of 2, with no final total.

These two rows must decline until complete invoices are supplied.

Excel row 45, invoice 1102-SOIN-07863078: blurred digits were read as 488.56 while the invoice prints Four Hundred Eighty Six and 56/100. The new reader rejects the conflicting final-amount readings without calculating a replacement.

Excel row 43, invoice 1102-SOIN-07848190, has a heavily blurred second page. The printed gross total is 465.89, but the new OCR cannot confirm it and declines. The old reader incorrectly returned the 443.70 subtotal. A clearer scan or manual review is needed.

## Implementation

- Fast summary-area OCR on every page, followed by full-page processing if no total is found.
- Spatial grouping keeps the final-total label associated with its amount.
- Scanned PDFs with faulty hidden text are read from the visible scan.
- Bounded image dimensions and two ONNX threads per operation reduce unnecessary work.
- Sideways-page rotation, label-only spelling normalization, and time-limited fallback.
- Conflicting totals return no amount, including conflicts between final digits and a clearly recognized amount in words. Subtotal/VAT values do not change the final amount. Missing totals are not reconstructed.
- Approval still requires exact numeric equality with Excel. Export never changes Amount To Pay.
- No persistent invoice or OCR-result cache in the application. Automatic approval review blocked deletion of the temporary evaluation folder (tmp/ocr_assessment), reporting "blocked by policy". Evaluation downloads and raw OCR scratch files remain there; the application never loads them as a cache.

## Visually checked cases

| Source | Vendor | Printed final total | Previous OCR | New OCR |
|---|---|---:|---:|---:|
| Excel row 2 | Barakat Quality Plus | 447.77 | 447.77 | 447.77 |
| Excel row 3 | Barakat Quality Plus | 395.45 | Not found | 395.45 |
| Excel row 4 | Barakat Quality Plus | 72.26 | 72.26 | 72.26 |
| Excel row 5 | Barakat Quality Plus | 108.88 | 108.88 | 108.88 |
| Excel row 6 | Abu Dhabi Refreshments | 1198.26 | Not found | 1198.26 |
| Excel row 9 | Barakat Quality Plus | 293.21 | 1102.0 | 293.21 |
| Excel row 10 | Abu Dhabi Refreshments | 899.21 | Not found | 899.21 |
| Excel row 13 | Barakat Quality Plus | 238.61 | Not found | 238.61 |
| Excel row 22 | Barakat Quality Plus | 187.94 | 178.98 | 187.94 |
| Excel row 28 | Abu Dhabi Refreshments | 137.61 | 137.61 | 137.61 |
| Excel row 30 | Abu Dhabi Refreshments | 684.47 | Not found | 684.47 |
| Excel row 34 | Abu Dhabi Refreshments | 727.54 | 727.54 | 727.54 |
| Excel row 40 | Barakat Quality Plus | Not found | Not found | Not found |
| Excel row 41 | Barakat Quality Plus | 310.77 | Not found | 310.77 |
| Excel row 43 | Barakat Quality Plus | 465.89 | 443.7 | Not found |
| Excel row 44 | Barakat Quality Plus | 195.75 | Not found | 195.75 |
| Excel row 45 | Barakat Quality Plus | 486.56 | Not found | Not found |
| Excel row 54 | Abu Dhabi Refreshments | 1222.73 | Not found | 1222.73 |
| Excel row 57 | Barakat Quality Plus | 349.32 | Not found | 349.32 |
| Excel row 72 | Barakat Quality Plus | Not found | Not found | Not found |
| PO202607-57898_INVOICE.pdf | Abu Dhabi Refreshments | 50.17 | 50.17 | 50.17 |
| PO202602-16940_INVOICE.pdf | Dubai Refreshment | 94.47 | 94.47 | 94.47 |
| PO202511-76534_INVOICE.pdf | Mohebi Logistics | 1569.91 | 1569.91 | 1569.91 |
| PO202510-71780_INVOICE.pdf | M.H. Enterprises | 673.58 | Not found | 673.58 |
| PO202510-71771_INVOICE.pdf | Barakat Quality Plus | 328.73 | 328.73 | 328.73 |
