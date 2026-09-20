"""Live, bounded reproduction against the application's persistent Gemini profile."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autodub.translate import browser as B
from playwright.sync_api import sync_playwright


class StopRepro(BaseException):
    pass


def main():
    out = ROOT / '_tmp' / 'gemini_response_repro'
    out.mkdir(parents=True, exist_ok=True)
    original = B._wait_reply
    records = []
    with sync_playwright() as p:
        ctx = B._launch(p, str(ROOT / 'browser_profile'), 'msedge')
        try:
            page = ctx.pages[0]
            page.goto('https://gemini.google.com/app', wait_until='domcontentloaded')
            B._visible_locator(page, B._INPUT_CANDIDATES)

            def observed(*args, **kwargs):
                reply = original(*args, **kwargs)
                snap = B._snapshot(page)
                dom = page.evaluate('''() => {
                  const selectors = ['model-response', 'message-content.model-response-text',
                    '.model-response-text', 'response-container', 'message-content'];
                  return Object.fromEntries(selectors.map(sel => {
                    const nodes = [...document.querySelectorAll(sel)];
                    return [sel, {count: nodes.length, last: nodes.slice(-1).map(el => ({
                      tag: el.tagName.toLowerCase(), id: el.id, role: el.getAttribute('role'),
                      aria: el.getAttribute('aria-label'), shadow: !!el.shadowRoot,
                      inner_chars: (el.innerText || '').length,
                      content_chars: (el.textContent || '').length,
                      preview: (el.innerText || el.textContent || '').slice(0, 100),
                      parents: [el.parentElement, el.parentElement?.parentElement].filter(Boolean)
                        .map(p => ({tag: p.tagName.toLowerCase(), id: p.id})),
                      html: el.outerHTML.slice(0, 1800)
                    }))}];
                  }));
                }''')
                rec = {'reply': reply, 'snapshot': snap, 'dom': dom}
                records.append(rec)
                page.screenshot(path=str(out / f'request-{len(records)}.png'))
                (out / 'result.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({'request': len(records), 'reply': reply,
                                  'user_count': snap['user_count'], 'model_count': snap['model_count'],
                                  'last_text': snap['models'][-1]['text'] if snap['models'] else ''}, ensure_ascii=False), flush=True)
                if not reply:
                    raise StopRepro('Stopped before the old retry layer can duplicate a turn')
                return reply

            B._wait_reply = observed
            for _ in range(2):
                B._ask_once(page, 'Return exactly:\n{"ok":true}', 8)
        except StopRepro as exc:
            print(str(exc), flush=True)
        finally:
            ctx.close()


if __name__ == '__main__':
    main()
