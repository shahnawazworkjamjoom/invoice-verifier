import sys, json, time, hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from invoice_verifier.workbook import read_records
from invoice_verifier.downloader import InvoiceDownloader
root=Path(__file__).resolve().parent
records=read_records(r'D:\MyData\Downloads\PurchaseOrderAttachment-09032026-0424PM.xlsx')
def fetch(r):
    dl=InvoiceDownloader(root/'invoices')
    try:
        p=dl.download(r.attachment_url, f'row-{r.excel_row}')
        return {'row':r.excel_row,'vendor':r.supplier,'invoice':r.invoice_number,'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    except Exception as e:
        return {'row':r.excel_row,'vendor':r.supplier,'error':str(e)}
    finally: dl.session.close()
t=time.perf_counter(); out=[]
with ThreadPoolExecutor(max_workers=4) as pool:
    for fut in as_completed([pool.submit(fetch,r) for r in records]):
        out.append(fut.result())
        print('downloaded',len(out),'of',len(records),flush=True)
(root/'manifest.json').write_text(json.dumps(sorted(out,key=lambda x:x['row']),indent=2))
print('Seconds',time.perf_counter()-t,'failures',sum('error'in x for x in out),flush=True)
