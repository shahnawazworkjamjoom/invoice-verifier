import sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from invoice_verifier.ocr.amount_reader import extract_amount
root=Path(__file__).resolve().parent
manifest=json.loads((root/'manifest_all.json').read_text())
if len(sys.argv)>1:
    chosen={int(n) for n in sys.argv[1].split(',')}
    manifest=[e for e in manifest if e['row'] in chosen]
manifest.sort(key=lambda x:(x['row']<101,x['row']))
with (root/'candidate.jsonl').open('w') as out:
    for e in manifest:
        p=Path(e['path']); start=time.perf_counter()
        try: result=extract_amount(p.read_bytes(),p.name)
        except Exception as exc: result={'error':str(exc)}
        result.update(row=e['row'],seconds=time.perf_counter()-start)
        out.write(json.dumps(result)+'\n'); out.flush()
        print(e['row'],result.get('amount_total'),round(result['seconds'],2),result.get('vendor_name'),result.get('error',''),flush=True)
