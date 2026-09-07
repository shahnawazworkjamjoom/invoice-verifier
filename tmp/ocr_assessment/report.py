import json
from pathlib import Path
from collections import Counter
from decimal import Decimal
root=Path(__file__).resolve().parent
manifest=json.loads((root/'manifest_all.json').read_text())
baseline={r['row']:r for r in map(json.loads,(root/'baseline.jsonl').read_text().splitlines())}
candidate={r['row']:r for r in map(json.loads,(root/'candidate.jsonl').read_text().splitlines())}
gold=json.loads((root/'ground_truth.json').read_text())
assert len(baseline)==len(candidate)==len(manifest)==78, 'Benchmark is not finished'
def normalize(v):
    if v in (None,0,0.0,'0','0.00'): return None
    return Decimal(str(v))
rows=[]
for e in manifest:
    n=e['row']; b=baseline[n]; c=candidate[n]
    rows.append({'row':n if n<100 else None,'source':Path(e['path']).name if n>=100 else 'Workbook attachment',
                 'invoice':e['invoice'],'vendor':e['vendor'],'sha256':e['sha256'],
                 'baseline_amount':b.get('amount_total'),'new_amount':c.get('amount_total'),
                 'baseline_seconds':b['seconds'],'new_seconds':c['seconds'],
                 'manually_checked':str(n) in gold,'printed_total':gold.get(str(n)),
                 'error':c.get('error')})
btime=sum(r['seconds'] for r in baseline.values()); ctime=sum(r['seconds'] for r in candidate.values())
checked=[r for r in rows if r['manually_checked']]
old_ok=sum(normalize(r['baseline_amount'])==normalize(r['printed_total']) for r in checked)
new_ok=sum(normalize(r['new_amount'])==normalize(r['printed_total']) for r in checked)
summary={'invoice_count':78,'baseline_seconds':btime,'new_seconds':ctime,'time_reduction_percent':100*(1-ctime/btime),
         'manually_checked':len(checked),'baseline_correct_checked':old_ok,'new_correct_checked':new_ok,
         'baseline_totals_returned':sum(normalize(r['baseline_amount']) is not None for r in rows),
         'new_totals_returned':sum(normalize(r['new_amount']) is not None for r in rows)}
output=Path(__file__).resolve().parents[2]/'reports'; output.mkdir(exist_ok=True)
(output/'ocr_v2_evaluation.json').write_text(json.dumps({'summary':summary,'invoices':rows},indent=2))
lines=['# Invoice OCR v2 evaluation','',
       'Source: PurchaseOrderAttachment-09032026-0424PM.xlsx (73 attachments) plus the five additional invoice PDFs supplied by the user. All 78 files have distinct SHA-256 hashes.','',
       '## Measured results','',
       f'- Previous reader: {btime:.1f} seconds ({btime/60:.2f} minutes).',
       f'- New reader: {ctime:.1f} seconds ({ctime/60:.2f} minutes).',
       f'- Time reduction: {100*(1-ctime/btime):.1f}%.',
       f'- Printed totals returned: {summary["baseline_totals_returned"]}/78 before; {summary["new_totals_returned"]}/78 after.',
       f'- On {len(checked)} visually checked cases: {old_ok} correct outcomes before; {new_ok} after. This includes two attachments with no final total, where no amount is the correct outcome.','',
       'Timing is from one complete sequential run of each version on this computer, including file reading and the first model initialization, excluding network downloads. The final amount-in-words consistency check was replayed on the complete OCR outputs and its measured time added; the recognition pass was not rerun for this text-only check. Development trial runs are excluded. The visual checks were selected for vendor coverage and known difficult cases, not as a random or held-out accuracy sample. Remaining returned totals have not all been independently confirmed. These results do not establish accuracy on unseen invoices. Excel Amount To Pay was not used as a training label or supplied to the OCR reader.','',
       '## Vendors used for tuning and evaluation','',
       '| Vendor | Invoices evaluated | Visually checked |','|---|---:|---:|']
for vendor,count in Counter(r['vendor'] for r in rows).items():
    lines.append(f'| {vendor} | {count} | {sum(r["vendor"]==vendor for r in checked)} |')
lines += ['', 'This is a rebuilt extraction pipeline using existing pretrained PP-OCRv4 weights in RapidOCR 1.4.4 and ONNX Runtime 1.29.0. No neural-network weights were trained or fine-tuned. Vendor tuning means summary-label and table-layout rules, orientation handling, and extraction tests.', '',
          '## Barakat pages and attachments needing review','',
          'The 65 Barakat attachments comprise 37 two-page PDFs, 26 one-page PDFs, and two image files. The reader checks every PDF page for the final amount due. It does not substitute Total net amount or add up line items.', '',
          '- Excel row 40, invoice 1102-SOIN-07848236: attachment contains only the page marked Page 1 of 2, with no final total.',
          '- Excel row 72, invoice 1102-SOIN-07840041: a single photo of Page 1 of 2, with no final total.',
          'These two rows must decline until complete invoices are supplied.',
          'Excel row 45, invoice 1102-SOIN-07863078: blurred digits were read as 488.56 while the invoice prints Four Hundred Eighty Six and 56/100. The new reader rejects the conflicting final-amount readings without calculating a replacement.',
          'Excel row 43, invoice 1102-SOIN-07848190, has a heavily blurred second page. The printed gross total is 465.89, but the new OCR cannot confirm it and declines. The old reader incorrectly returned the 443.70 subtotal. A clearer scan or manual review is needed.', '',
          '## Implementation','',
          '- Fast summary-area OCR on every page, followed by full-page processing if no total is found.',
          '- Spatial grouping keeps the final-total label associated with its amount.',
          '- Scanned PDFs with faulty hidden text are read from the visible scan.',
          '- Bounded image dimensions and two ONNX threads per operation reduce unnecessary work.',
          '- Sideways-page rotation, label-only spelling normalization, and time-limited fallback.',
          '- Conflicting totals return no amount, including conflicts between final digits and a clearly recognized amount in words. Subtotal/VAT values do not change the final amount. Missing totals are not reconstructed.',
          '- Approval still requires exact numeric equality with Excel. Export never changes Amount To Pay.',
          '- No persistent invoice or OCR-result cache in the application. Evaluation downloads and raw OCR scratch files are removed after this report is produced.', '',
          '## Visually checked cases','',
          '| Source | Vendor | Printed final total | Previous OCR | New OCR |', '|---|---|---:|---:|---:|']
for r in checked:
    source=f'Excel row {r["row"]}' if r['row'] is not None else r['source']
    def display(v): return str(v) if normalize(v) is not None else 'Not found'
    lines.append(f'| {source} | {r["vendor"]} | {display(r["printed_total"])} | {display(r["baseline_amount"])} | {display(r["new_amount"])} |')
(output/'ocr_v2_evaluation.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(summary,indent=2))
