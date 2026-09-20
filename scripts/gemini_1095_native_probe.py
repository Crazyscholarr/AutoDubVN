import json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autodub.translate import browser as B
from playwright.sync_api import sync_playwright
out=Path('_tmp/gemini_1095_native');out.mkdir(exist_ok=True)
with sync_playwright() as p:
 ctx=B._launch(p,'browser_profile','msedge')
 page=ctx.pages[0]
 page.goto('https://gemini.google.com/app',wait_until='domcontentloaded')
 B._visible_locator(page,B._INPUT_CANDIDATES,timeout=25)
 records=[]
 for i in range(1,4):
  prompt='Dịch sang tiếng Việt, chỉ trả bản dịch: 我们明天再见。' + (' 谢谢。'*i)
  box=B._visible_locator(page,B._INPUT_CANDIDATES,timeout=10)
  B._put_text(page,box,prompt)
  button=B._visible_locator(page,B._SEND_CANDIDATES,timeout=5)
  button.click(timeout=5000)
  try:
   raw=B._wait_reply(page,0,40,msg=prompt,trace={'send_ack':True})
   records.append(dict(n=i,raw=raw))
  except Exception as exc:
   records.append(dict(n=i,error=str(exc)))
   page.screenshot(path=str(out/'error.png'))
   break
  print(records[-1],flush=True)
 (out/'results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')
 ctx.close()
