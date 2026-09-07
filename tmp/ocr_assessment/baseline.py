import sys,json,time,base64
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from invoice_verifier.ocr.ocr_service import extract_document
root=Path(__file__).resolve().parent
manifest=json.loads((root/'manifest_all.json').read_text())
manifest.sort(key=lambda x:(x['row']<101,x['row']))
with (root/'baseline.jsonl').open('w') as out:
    for e in manifest:
        p=Path(e['path']); start=time.perf_counter()
        try: result=extract_document(base64.b64encode(p.read_bytes()),p.name,'invoice',amount_only=True)
        except Exception as exc: result={'error':str(exc)}
        result.update(row=e['row'],seconds=time.perf_counter()-start)
        out.write(json.dumps(result)+'\n'); out.flush()
        print(e['row'], result.get('amount_total'),round(result['seconds'],2),result.get('ocr_method'),flush=True)
