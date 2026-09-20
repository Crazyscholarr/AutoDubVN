"""One-shot Gemini DOM probe for response-detector repair. Not production."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

DUMP_JS = r"""() => {
  const MARK = '__GEMINI_CONV_SNAPSHOT__';
  const textOf = (el) => {
    if (!el) return '';
    let inner = '';
    let content = '';
    try { inner = (el.innerText || '').trim(); } catch (e) {}
    try { content = (el.textContent || '').trim(); } catch (e) {}
    return inner || content;
  };
  const deepText = (el) => {
    let t = textOf(el);
    let shadow = false;
    if (!t && el && el.shadowRoot) {
      shadow = true;
      t = textOf(el.shadowRoot);
      if (!t) {
        const nodes = el.shadowRoot.querySelectorAll('*');
        for (const n of nodes) {
          const s = textOf(n);
          if (s.length > t.length) t = s;
        }
      }
    }
    return { text: t, shadow };
  };
  const vis = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const b = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return b.width > 0 && b.height > 0
      && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  const attrs = (el) => {
    if (!el || !el.attributes) return {};
    const out = {};
    for (const a of el.attributes) {
      const name = a.name || '';
      if (!name) continue;
      if (name === 'class' || name === 'id' || name === 'role' || name.startsWith('aria-')
          || name.startsWith('data-') || name === 'slot') {
        out[name] = (a.value || '').slice(0, 180);
      }
    }
    return out;
  };
  const chain = (el, limit) => {
    const rows = [];
    let n = el;
    let i = 0;
    while (n && n !== document.body && i < (limit || 8)) {
      rows.push({
        tag: (n.tagName || '').toLowerCase(),
        id: n.id || '',
        role: n.getAttribute && n.getAttribute('role') || '',
        testid: n.getAttribute && (n.getAttribute('data-test-id') || n.getAttribute('data-testid') || ''),
        cls: ((n.className && n.className.toString) ? n.className.toString() : '').slice(0, 160),
      });
      n = n.parentElement;
      i += 1;
    }
    return rows;
  };
  const OLD = [
    'model-response',
    'message-content.model-response-text',
    '.model-response-text',
    'response-container',
    "[data-message-author-role='model']",
    'div.markdown',
    'user-query',
    "[data-message-author-role='user']",
    '.query-text',
    '.user-query-bubble-with-background',
  ];
  const EXTRA = [
    '#chat-history',
    'infinite-scroller',
    'chat-window',
    '[role="main"]',
    'main',
    "[id^='model-response-message-content']",
    "[id^='message-content-id-r_']",
    "[id^='user-query-content-']",
    'message-content',
    '[data-test-id="model-response"]',
    '[data-message-author="model"]',
    'code-block',
    'pre',
    '[data-test-id="stop-icon-button"]',
  ];
  const counts = {};
  const samples = {};
  const allSels = OLD.concat(EXTRA);
  for (const sel of allSels) {
    let nodes = [];
    try { nodes = Array.from(document.querySelectorAll(sel)); } catch (e) { counts[sel] = -1; continue; }
    counts[sel] = nodes.length;
    samples[sel] = nodes.slice(0, 3).map((el) => {
      const d = deepText(el);
      return {
        tag: (el.tagName || '').toLowerCase(),
        id: (el.id || '').slice(0, 80),
        visible: vis(el),
        box: (() => { const b = el.getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)]; })(),
        shadow: !!el.shadowRoot,
        shadowMode: el.shadowRoot ? el.shadowRoot.mode : '',
        attrs: attrs(el),
        chars_inner: (el.innerText || '').trim().length,
        chars_content: (el.textContent || '').trim().length,
        chars_deep: d.text.length,
        preview: d.text.slice(0, 100),
      };
    });
  }
  const roots = {};
  for (const sel of ['#chat-history', 'infinite-scroller', 'chat-window', '[role="main"]', 'main']) {
    const el = document.querySelector(sel);
    if (!el) continue;
    roots[sel] = {
      tag: (el.tagName || '').toLowerCase(),
      id: el.id || '',
      childTags: Array.from(el.children).slice(0, 30).map((c) => (c.tagName || '').toLowerCase()),
      childCount: el.children.length,
    };
  }
  const custom = {};
  document.querySelectorAll('*').forEach((el) => {
    const tag = (el.tagName || '').toLowerCase();
    if (tag.includes('-')) custom[tag] = (custom[tag] || 0) + 1;
  });
  const interesting = Object.keys(custom).filter((t) =>
    /query|response|message|chat|turn|content|conversation|markdown|code|thought|bard|gemini|scroller/i.test(t)
  ).sort();
  const customHits = {};
  for (const t of interesting) customHits[t] = custom[t];

  const frags = ['{"ok":true}', '"translated_sentences"', '"ok": true', '"ok":true'];
  const hits = [];
  const seen = new Set();
  const scanRoot = document.querySelector('#chat-history, infinite-scroller, [role="main"], main') || document.body;
  const walk = [scanRoot];
  const walked = new Set();
  while (walk.length && hits.length < 8) {
    const el = walk.pop();
    if (!el || walked.has(el)) continue;
    walked.add(el);
    if (el.shadowRoot) walk.push(el.shadowRoot);
    if (el.children) {
      for (const c of el.children) walk.push(c);
    }
    const t = textOf(el);
    if (!t || t.length < 8) continue;
    let matched = '';
    for (const f of frags) {
      if (t.includes(f)) { matched = f; break; }
    }
    if (!matched) continue;
    const key = (el.tagName || '') + '|' + (el.id || '') + '|' + t.length;
    if (seen.has(key)) continue;
    seen.add(key);
    hits.push({
      fragment: matched,
      tag: (el.tagName || '').toLowerCase(),
      id: (el.id || '').slice(0, 80),
      visible: vis(el),
      shadowHost: !!el.shadowRoot,
      chars: t.length,
      preview: t.slice(0, 120),
      parents: chain(el, 10),
    });
  }
  return {
    mark: MARK,
    url: location.href,
    title: document.title,
    counts,
    samples,
    roots,
    customHits,
    jsonHits: hits,
    hasClosedShadowHint: !!document.querySelector('model-response, message-content, user-query'),
  };
}"""

OLD_SELS = [
    "model-response",
    "message-content.model-response-text",
    ".model-response-text",
    "response-container",
    "[data-message-author-role='model']",
    "div.markdown",
]


def main() -> int:
    from playwright.sync_api import sync_playwright

    profile = str(HERE / "browser_profile")
    out_path = HERE / "_tmp" / "gemini_dom_probe.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print("profile:", profile, flush=True)

    with sync_playwright() as p:
        last = None
        ctx = None
        for ch in ("msedge", "chrome", None):
            try:
                kw = dict(
                    headless=False,
                    args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
                    ignore_default_args=["--enable-automation"],
                    no_viewport=True,
                )
                if ch:
                    kw["channel"] = ch
                ctx = p.chromium.launch_persistent_context(profile, **kw)
                print("launched", ch or "chromium", flush=True)
                break
            except Exception as e:
                last = e
                print("launch fail", ch, e, flush=True)
        if ctx is None:
            raise RuntimeError(last)

        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(15000)
            if "gemini.google.com" not in (page.url or ""):
                page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            dump = page.evaluate(DUMP_JS)
            print("url:", dump.get("url"), flush=True)
            print("title:", dump.get("title"), flush=True)
            print("\nOLD SELECTOR COUNTS:", flush=True)
            for sel in OLD_SELS:
                print(f"  {sel}: {dump.get('counts', {}).get(sel)}", flush=True)
            print("\nEXTRA COUNTS:", flush=True)
            counts = dump.get("counts") or {}
            for sel, n in counts.items():
                if sel not in OLD_SELS:
                    print(f"  {sel}: {n}", flush=True)
            print("\nCUSTOM ELEMENTS:", json.dumps(dump.get("customHits"), ensure_ascii=False, indent=2), flush=True)
            print("\nROOTS:", json.dumps(dump.get("roots"), ensure_ascii=False, indent=2), flush=True)
            print("\nJSON HITS:", json.dumps(dump.get("jsonHits"), ensure_ascii=False, indent=2)[:4000], flush=True)
            print("\nSAMPLES model-response:", json.dumps((dump.get("samples") or {}).get("model-response"), ensure_ascii=False, indent=2)[:2500], flush=True)
            print("\nSAMPLES message-content-id:", json.dumps((dump.get("samples") or {}).get("[id^='message-content-id-r_']"), ensure_ascii=False, indent=2)[:2500], flush=True)

            # Tiny live send if composer exists.
            box = page.locator("div[role='textbox'][contenteditable='true']").last
            composer_ok = False
            try:
                composer_ok = box.count() > 0 and box.is_visible(timeout=2000)
            except Exception:
                composer_ok = False
            result = {"composer_ok": composer_ok, "reply": "", "error": ""}
            if composer_ok:
                prompt = 'Return exactly:\n{"ok":true}'
                try:
                    box.click(timeout=5000)
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Delete")
                    page.keyboard.insert_text(prompt)
                    page.wait_for_timeout(400)
                    clicked = page.evaluate(
                        """() => {
                          const btn = document.querySelector('button.send-button, [data-test-id="send-button"]');
                          if (btn) { btn.click(); return 'css'; }
                          const nodes = document.querySelectorAll('button, [role="button"]');
                          for (const n of nodes) {
                            const lab = ((n.getAttribute('aria-label')||'')+' '+(n.innerText||'')).toLowerCase();
                            if (/gửi|send message|\\bsend\\b/.test(lab) && !/stop|mic|flash/.test(lab)) {
                              n.click(); return 'label';
                            }
                          }
                          return '';
                        }"""
                    )
                    print("send click:", clicked, flush=True)
                    deadline = time.monotonic() + 25
                    while time.monotonic() < deadline:
                        page.wait_for_timeout(400)
                        dump2 = page.evaluate(DUMP_JS)
                        hits = dump2.get("jsonHits") or []
                        for h in hits:
                            if '{"ok":true}' in (h.get("preview") or "") or '{"ok": true}' in (h.get("preview") or ""):
                                result["reply"] = h
                                break
                        if result["reply"]:
                            break
                    dump = dump2
                    result["after_counts"] = {k: dump.get("counts", {}).get(k) for k in OLD_SELS}
                    print("LIVE TEST reply hit:", json.dumps(result["reply"], ensure_ascii=False)[:1500], flush=True)
                    print("after old counts:", result["after_counts"], flush=True)
                except Exception as e:
                    result["error"] = str(e)
                    print("live send error:", e, flush=True)

            payload = {"dump": dump, "live": result}
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print("wrote", out_path, flush=True)
            page.screenshot(path=str(HERE / "_tmp" / "gemini_dom_probe.png"), full_page=False)
            print("screenshot", HERE / "_tmp" / "gemini_dom_probe.png", flush=True)
            page.wait_for_timeout(1500)
        finally:
            ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
