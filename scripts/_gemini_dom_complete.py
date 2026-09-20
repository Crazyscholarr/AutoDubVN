"""Wait until Gemini generation is visually complete, then dump innerText vs textContent."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

SNAP_JS = r"""() => {
  const read = (el) => {
    if (!el) return null;
    const b = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    const visible = b.width > 0 && b.height > 0 && s.visibility !== 'hidden'
      && s.display !== 'none' && s.opacity !== '0';
    return {
      tag: (el.tagName || '').toLowerCase(),
      id: (el.id || '').slice(0, 90),
      testid: (el.getAttribute('data-test-id') || el.getAttribute('data-testid') || ''),
      cls: ((el.className && el.className.toString) ? el.className.toString() : '').slice(0, 160),
      visible,
      box: [Math.round(b.width), Math.round(b.height)],
      shadow: !!el.shadowRoot,
      inner: (el.innerText || '').trim(),
      content: (el.textContent || '').trim(),
    };
  };
  const first = (sel) => { try { return document.querySelector(sel); } catch (e) { return null; } };
  const all = (sel) => { try { return Array.from(document.querySelectorAll(sel)); } catch (e) { return []; } };
  const stops = [];
  all('button, [role="button"]').forEach((n) => {
    const lab = ((n.getAttribute('aria-label') || '') + ' ' + (n.innerText || '')).trim();
    if (/stop generating|stop response|ngừng tạo|dừng tạo|dừng phản hồi/i.test(lab)) {
      const b = n.getBoundingClientRect();
      const s = getComputedStyle(n);
      if (b.width > 1 && b.height > 1 && s.visibility !== 'hidden' && s.display !== 'none') {
        stops.push(lab.slice(0, 80));
      }
    }
  });
  const sels = [
    'model-response',
    'model-response-content',
    'message-content',
    '.model-response-text',
    '[id^="model-response-message-content"]',
    '[id^="message-content-id-r_"]',
    'div.markdown',
    '.markdown',
    'response-container',
    'structured-content-container',
    'pending-response',
    'user-query',
    'infinite-scroller[data-test-id="chat-history-container"]',
  ];
  const nodes = {};
  const counts = {};
  for (const sel of sels) {
    const list = all(sel);
    counts[sel] = list.length;
    nodes[sel] = list.slice(0, 2).map(read);
  }
  const custom = {};
  const root = first('infinite-scroller[data-test-id="chat-history-container"]');
  (root || document.body).querySelectorAll('*').forEach((el) => {
    const tag = (el.tagName || '').toLowerCase();
    if (tag.includes('-')) custom[tag] = (custom[tag] || 0) + 1;
  });
  return {
    generating: stops.length > 0,
    stops,
    counts,
    custom,
    rootChildren: root ? Array.from(root.children).map((c) => (c.tagName || '').toLowerCase()) : [],
    nodes,
  };
}"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    profile = str(HERE / "browser_profile")
    token = "autodubvn_final_k7"
    prompt = f'Return exactly:\n{{"probe":"{token}","ok":true}}'
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile, channel="msedge", headless=False,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"], no_viewport=True,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(20000)
        page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
        page.wait_for_timeout(1800)
        box = page.locator("div[role='textbox'][contenteditable='true']").last
        box.click(timeout=8000)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.insert_text(prompt)
        page.wait_for_timeout(400)
        page.evaluate(
            """() => {
              const btn = document.querySelector('button.send-button, [data-test-id="send-button"]');
              if (btn) { btn.click(); return; }
              const nodes = document.querySelectorAll('button, [role="button"]');
              for (const n of nodes) {
                const lab = ((n.getAttribute('aria-label')||'')+' '+(n.innerText||'')).toLowerCase();
                if (/gửi|send message|\\bsend\\b/.test(lab) && !/stop|mic|flash/.test(lab)) {
                  n.click(); return;
                }
              }
            }"""
        )
        deadline = time.monotonic() + 50
        last = None
        complete = None
        while time.monotonic() < deadline:
            last = page.evaluate(SNAP_JS)
            mr = (last.get("nodes") or {}).get("model-response") or []
            md = (last.get("nodes") or {}).get("div.markdown") or []
            inner = (mr[0]["inner"] if mr else "") or (md[0]["inner"] if md else "")
            content = (mr[0]["content"] if mr else "") or (md[0]["content"] if md else "")
            blob = inner + "\n" + content
            print(
                f"t={time.monotonic():.0f} gen={last.get('generating')} "
                f"model={last['counts'].get('model-response')} md={last['counts'].get('div.markdown')} "
                f"inner={len(inner)} content={len(content)} "
                f"preview={(inner or content)[:60]!r}",
                flush=True,
            )
            if (not last.get("generating")
                    and token in blob
                    and (len(inner) > 20 or len(content) > 20)
                    and last["counts"].get("pending-response", 1) == 0):
                complete = last
                break
            page.wait_for_timeout(700)
        data = complete or last
        page.screenshot(path=str(HERE / "_tmp" / "gemini_dom_complete.png"))
        # Playwright locator.inner_text vs evaluate
        pw = {}
        for sel in ["model-response", "div.markdown", ".model-response-text",
                    "message-content", "response-container"]:
            loc = page.locator(sel)
            try:
                n = loc.count()
            except Exception as e:
                pw[sel] = {"count_err": str(e)}
                continue
            rec = {"count": n}
            if n:
                try:
                    rec["pw_inner_text"] = (loc.last.inner_text(timeout=1500) or "")[:200]
                    rec["pw_inner_len"] = len(loc.last.inner_text(timeout=1500) or "")
                except Exception as e:
                    rec["pw_inner_err"] = str(e)[:200]
                try:
                    rec["pw_text_content"] = (loc.last.text_content(timeout=1500) or "")[:200]
                    rec["pw_text_len"] = len(loc.last.text_content(timeout=1500) or "")
                except Exception as e:
                    rec["pw_text_err"] = str(e)[:200]
                try:
                    rec["js"] = page.evaluate(
                        """sel => {
                          const el = document.querySelector(sel);
                          if (!el) return null;
                          return {inner: (el.innerText||'').trim().slice(0,200),
                                  content: (el.textContent||'').trim().slice(0,200),
                                  innerLen: (el.innerText||'').trim().length,
                                  contentLen: (el.textContent||'').trim().length};
                        }""",
                        sel,
                    )
                except Exception as e:
                    rec["js_err"] = str(e)[:200]
            pw[sel] = rec
        out = HERE / "_tmp" / "gemini_dom_complete.json"
        slim_nodes = {}
        for sel, rows in (data.get("nodes") or {}).items():
            slim_nodes[sel] = [
                {**{k: v for k, v in r.items() if k not in {"inner", "content"}},
                 "inner_len": len(r.get("inner") or ""),
                 "content_len": len(r.get("content") or ""),
                 "inner_preview": (r.get("inner") or "")[:120],
                 "content_preview": (r.get("content") or "")[:120]}
                for r in rows
            ]
        payload = {
            "generating": data.get("generating"),
            "stops": data.get("stops"),
            "counts": data.get("counts"),
            "custom": data.get("custom"),
            "rootChildren": data.get("rootChildren"),
            "nodes": slim_nodes,
            "playwright": pw,
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"counts": data.get("counts"), "generating": data.get("generating"),
                          "pw": {k: {kk: vv for kk, vv in rec.items() if kk != "js"}
                                 for k, rec in pw.items()}},
                         ensure_ascii=False, indent=2), flush=True)
        print("wrote", out, flush=True)
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
