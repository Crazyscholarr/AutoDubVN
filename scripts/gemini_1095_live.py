import json,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autodub.translate import browser as B
from autodub.translate.jsonutil import extract_object
out=Path('_tmp/gemini_1095_live');out.mkdir(exist_ok=True)
records=[]
with B.phien_gemini_trinh_duyet('browser_profile',wait_reply=90,reset_every=3) as ask:
    for i in range(1,7):
        prompt='Translate this subtitle into Vietnamese. Return only JSON with id and text. '+json.dumps({'id':i,'text':'我们明天再见。'},ensure_ascii=False)
        start=time.monotonic()
        try:
            raw=ask(prompt);obj=extract_object(raw)
            record=dict(n=i,ok=obj.get('id')==i and bool(obj.get('text')),raw=raw,seconds=round(time.monotonic()-start,2))
        except Exception as exc:
            record=dict(n=i,ok=False,error=str(exc),seconds=round(time.monotonic()-start,2))
        records.append(record)
        (out/'results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(record,ensure_ascii=False),flush=True)
        if not record['ok']:break
