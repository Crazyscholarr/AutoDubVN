"""Dump Gemini conversation DOM after generation completes."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

TREE_JS = r"""() => {
  const textOf = (el) => {
    if (!el) return '';
    let inner = '', content = '';
    try { inner = (el.innerText || '').trim(); } catch (e) {}
    try { content = (el.textContent || '').trim(); } catch (e) {}
    return { inner, content, innerLen: inner.length, contentLen: content.length };
  };
  const vis = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const b = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return b.width > 0 && b.height > 0
      && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  const summarize = (el, depth, maxDepth, maxChildren) => {
    if (!el || depth > maxDepth) return null;
    const t = textOf(el);
    const b = el.getBoundingClientRect ? el.getBoundingClientRect() : {width:0,height:0};
    const kids = [];
    const children = Array.from(el.children || []);
    for (const c of children.slice(0, maxChildren)) {
      kids.push(summarize(c, depth + 1, maxDepth, maxChildren));
    }
    return {
      tag: (el.tagName || '').toLowerCase(),
      id: (el.id || '').slice(0, 80),
      role: (el.getAttribute && el.getAttribute('role')) || '',
      testid: (el.getAttribute && (el.getAttribute('data-test-id') || el.getAttribute('data-testid'))) || '',
      aria: (el.getAttribute && el.getAttribute('aria-label')) || '',
      cls: ((el.className && el.className.toString) ? el.className.toString() : '').slice(0, 140),
      visible: vis(el),
      box: [Math.round(b.width), Math.round(b.height)],
      shadow: !!el.shadowRoot,
      shadowMode: el.shadowRoot ? el.shadowRoot.mode : '',
      innerLen: t.innerLen,
      contentLen: t.contentLen,
      preview: t.inner.slice(0, 80) || t.content.slice(0, 80),
      childCount: children.length,
      children: kids,
    };
  };
  const root = document.querySelector('infinite-scroller[data-test-id="chat-history-container"]')
    || document.querySelector('chat-window-content')
    || document.querySelector('chat-window');
  const custom = {};
  const scope = root || document.body;
  scope.querySelectorAll('*').forEach((el) => {
    const tag = (el.tagName || '').toLowerCase();
    if (tag.includes('-')) custom[tag] = (custom[tag] || 0) + 1;
  });
  const pending = document.querySelector('pending-response');
  const stop = [];
  document.querySelectorAll('button, [role="button"]').forEach((n) => {
    const lab = ((n.getAttribute('aria-label') || '') + ' ' + (n.innerText || '')).trim();
    if (/stop|dừng|ngừng|gửi|send/i.test(lab) && vis(n)) {
      stop.push({
        tag: (n.tagName || '').toLowerCase(),
        lab: lab.slice(0, 80),
        testid: n.getAttribute('data-test-id') || '',
        cls: ((n.className && n.className.toString) ? n.className.toString() : '').slice(0, 80),
      });
    }
  });
  const oldCounts = {};
  const sels = [
    'model-response', 'response-container', 'pending-response', 'user-query',
    'div.markdown', '.model-response-text', 'message-content',
    'assistant-messages-primary', 'chat-window-content',
    '[id^="user-query-content-"]', '[id^="model-response-message-content"]',
    '[id^="message-content-id-r_"]', 'code-block', 'pre', 'markdown-content',
    '.markdown', 'message-content.model-response-text',
  ];
  for (const sel of sels) {
    try { oldCounts[sel] = document.querySelectorAll(sel).length; }
    catch (e) { oldCounts[sel] = -1; }
  }
  const resp = document.querySelector('response-container');
  const pendingInfo = pending ? summarize(pending, 0, 3, 8) : null;
  const respInfo = resp ? summarize(resp, 0, 4, 10) : null;
  return {
    url: location.href,
    hasRoot: !!root,
    rootTag: root ? (root.tagName || '').toLowerCase() : '',
    rootTestId: root && root.getAttribute ? (root.getAttribute('data-test-id') || '') : '',
    rootChildTags: root ? Array.from(root.children).map((c) => (c.tagName || '').toLowerCase()) : [],
    rootTree: root ? summarize(root, 0, 3, 12) : null,
    custom,
    oldCounts,
    pending: !!pending,
    pendingInfo,
    respInfo,
    stop,
  };
}"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    profile = str(HERE / "browser_profile")
    out_path = HERE / "_tmp" / "gemini_dom_after.json"
    token = "autodubvn_probe_r9x"
    prompt = (
        f'Return exactly this JSON object and nothing else:\n'
        f'{{"probe":"{token}","ok":true}}'
    )

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile,
            channel="msedge",
            headless=False,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
            no_viewport=True,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(20000)
        if "gemini.google.com" not in (page.url or ""):
            page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # New chat if possible to isolate.
        try:
            page.evaluate(
                """() => {
                  const nodes = document.querySelectorAll('button, a, [role="button"]');
                  for (const n of nodes) {
                    const lab = ((n.getAttribute('aria-label')||'')+' '+(n.innerText||'')).trim();
                    if (/^(new chat|chat mới|đoạn chat mới)$/i.test(lab)
                        || (n.getAttribute('data-test-id')||'') === 'new-chat-button') {
                      n.click(); return true;
                    }
                  }
                  return false;
                }"""
            )
            page.wait_for_timeout(1200)
        except Exception:
            pass

        box = page.locator("div[role='textbox'][contenteditable='true']").last
        box.click(timeout=8000)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.insert_text(prompt)
        page.wait_for_timeout(500)
        page.evaluate(
            """() => {
              const btn = document.querySelector('button.send-button, [data-test-id="send-button"]');
              if (btn) { btn.click(); return 'css'; }
              const nodes = document.querySelectorAll('button, [role="button"]');
              for (const n of nodes) {
                const lab = ((n.getAttribute('aria-label')||'')+' '+(n.innerText||'')).toLowerCase();
                if ((/gửi|send message|\\bsend\\b/.test(lab)) && !/stop|mic|flash/.test(lab)) {
                  n.click(); return 'label';
                }
              }
              return '';
            }"""
        )

        dumps = []
        found = None
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            data = page.evaluate(TREE_JS)
            dumps.append({
                "t": round(time.monotonic(), 2),
                "pending": data.get("pending"),
                "oldCounts": data.get("oldCounts"),
                "rootChildTags": data.get("rootChildTags"),
                "custom": data.get("custom"),
                "respPreview": (data.get("respInfo") or {}).get("preview"),
                "respInner": (data.get("respInfo") or {}).get("innerLen"),
                "stop": data.get("stop"),
            })
            blob = json.dumps(data, ensure_ascii=False)
            if token in blob and not data.get("pending"):
                found = data
                break
            # also accept token in response-container even if pending gone
            if token in ((data.get("respInfo") or {}).get("preview") or ""):
                found = data
                if not data.get("pending"):
                    break
            page.wait_for_timeout(500)

        if found is None:
            found = page.evaluate(TREE_JS)
        page.screenshot(path=str(HERE / "_tmp" / "gemini_dom_after.png"), full_page=False)
        out_path.write_text(
            json.dumps({"timeline": dumps[-8:], "final": found}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("pending_final", found.get("pending"), flush=True)
        print("oldCounts", json.dumps(found.get("oldCounts"), ensure_ascii=False), flush=True)
        print("custom", json.dumps(found.get("custom"), ensure_ascii=False), flush=True)
        print("rootChildTags", found.get("rootChildTags"), flush=True)
        print("respInner", (found.get("respInfo") or {}).get("innerLen"), flush=True)
        print("respPreview", (found.get("respInfo") or {}).get("preview"), flush=True)
        print("stop", found.get("stop"), flush=True)
        print("respTree", json.dumps(found.get("respInfo"), ensure_ascii=False, indent=2)[:5000], flush=True)
        print("rootTree tags/previews:", flush=True)
        root = found.get("rootTree") or {}
        for c in (root.get("children") or []):
            print(" ", c.get("tag"), "inner", c.get("innerLen"), "prev", (c.get("preview") or "")[:80], flush=True)
        print("wrote", out_path, flush=True)
        page.wait_for_timeout(800)
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
