"""Live 8-request Gemini turn-tracking repro. Does not change production selectors."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autodub.translate import browser as B
from playwright.sync_api import sync_playwright

OUT = ROOT / "_tmp" / "gemini_turn_tracking_repro"
N = 8
INSPECT_JS = r"""() => {
  const root = document.querySelector('infinite-scroller[data-test-id="chat-history-container"]')
    || document.querySelector('infinite-scroller.chat-history')
    || document.querySelector('chat-window-content');
  const collect = [];
  const visit = (el) => {
    if (!el) return;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'user-query' || tag === 'model-response' || tag === 'pending-response') {
      collect.push(el);
      return;
    }
    const kids = el.children || [];
    for (let i = 0; i < kids.length; i++) visit(kids[i]);
  };
  if (root) visit(root);
  const rec = (el, i) => {
    const tag = (el.tagName || '').toLowerCase();
    const content = el.querySelector(
      '[id^="user-query-content-"], [id^="message-content-id-r_"], [id^="model-response-message-content"], .model-response-text, div.markdown');
    const parent = el.parentElement;
    const inner = (el.innerText || '');
    const textContent = (el.textContent || '');
    const childInner = content ? (content.innerText || '') : '';
    const childContent = content ? (content.textContent || '') : '';
    return {
      i, tag, id: el.id || '',
      content_id: content ? (content.id || '') : '',
      content_tag: content ? (content.tagName || '').toLowerCase() : '',
      parent_tag: parent ? parent.tagName.toLowerCase() : '',
      parent_id: parent ? (parent.id || parent.getAttribute('data-test-id') || '') : '',
      parent_cls: parent ? (parent.className || '').toString().slice(0, 80) : '',
      inner_len: inner.length,
      content_len: textContent.length,
      child_inner_len: childInner.length,
      child_content_len: childContent.length,
      child_inner_preview: childInner.slice(0, 80),
      inner_preview: inner.slice(0, 80),
    };
  };
  return {
    root: root ? ((root.tagName || '').toLowerCase() + ':'
      + (root.getAttribute('data-test-id') || root.id || '')) : '',
    scroll_top: root ? (root.scrollTop || 0) : 0,
    scroll_height: root ? (root.scrollHeight || 0) : 0,
    client_height: root ? (root.clientHeight || 0) : 0,
    mounted: collect.length,
    user_count: collect.filter((e) => e.tagName.toLowerCase() === 'user-query').length,
    model_count: collect.filter((e) => e.tagName.toLowerCase() === 'model-response').length,
    pending_count: collect.filter((e) => e.tagName.toLowerCase() === 'pending-response').length,
    generating: !!(root && root.querySelector('pending-response, thinking-dots-animation')),
    nodes: collect.map(rec),
  };
}"""


def _norm(msg: str) -> str:
    return re.sub(r"\s+", " ", str(msg or "").strip())


def _hash(msg: str) -> str:
    return hashlib.sha256(_norm(msg).encode("utf-8")).hexdigest()[:16]


def _prompts():
    rows = []
    for i in range(1, N + 1):
        body = '{"probe":"repro-%02d","ok":true,"n":%d}' % (i, i)
        rows.append("Return exactly this JSON object and nothing else:\n" + body)
    return rows


def _inspect(page) -> dict:
    try:
        data = page.evaluate(INSPECT_JS)
    except Exception as exc:
        return {"error": str(exc)[:200]}
    return data if isinstance(data, dict) else {"error": "not-dict"}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    profile = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "browser_profile")
    prompts = _prompts()
    records = []
    with sync_playwright() as p:
        ctx = B._launch(p, profile, "msedge")
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(60000)
            page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
            if B._visible_locator(page, B._INPUT_CANDIDATES, timeout=25.0) is None:
                raise RuntimeError("Gemini composer not found; log in in the opened window.")
            page = B._mo_chat_moi(page, "https://gemini.google.com/app")
            B._visible_locator(page, B._INPUT_CANDIDATES, timeout=20.0)
            seen_user_ids, seen_model_ids = [], []
            for i, prompt in enumerate(prompts, 1):
                request_id = uuid.uuid4().hex[:12]
                prompt_hash = _hash(prompt)
                before_dom = _inspect(page)
                before_snap = B._snapshot(page)
                rec = {
                    "request_id": request_id,
                    "prompt_hash": prompt_hash,
                    "n": i,
                    "user_count_before": before_snap.get("user_count"),
                    "model_count_before": before_snap.get("model_count"),
                    "scroll_top_before": before_dom.get("scroll_top"),
                    "scroll_height_before": before_dom.get("scroll_height"),
                    "mounted_before": before_dom.get("mounted"),
                    "node_ids_before": [
                        (n.get("tag"), n.get("id"), n.get("content_id"))
                        for n in (before_dom.get("nodes") or [])
                    ],
                }
                print(json.dumps({"phase": "before", **{k: rec[k] for k in (
                    "n", "request_id", "prompt_hash", "user_count_before",
                    "model_count_before", "scroll_top_before", "scroll_height_before")}},
                    ensure_ascii=False), flush=True)
                trace = {"request_id": request_id, "prompt_hash": prompt_hash}
                t0 = time.monotonic()
                try:
                    raw = B._ask_once(page, prompt, 90, trace=trace)
                    err = ""
                except Exception as exc:
                    raw, err = "", "%s:%s" % (getattr(exc, "kind", type(exc).__name__), exc)
                after_dom = _inspect(page)
                after_snap = None
                try:
                    after_snap = B._snapshot(page)
                except Exception as snap_exc:
                    after_snap = {"error": str(snap_exc)[:200]}
                users = (after_snap or {}).get("users") or []
                models = (after_snap or {}).get("models") or []
                user_ids = [(n.get("tag"), n.get("id"), n.get("content_id"))
                            for n in (after_dom.get("nodes") or []) if n.get("tag") == "user-query"]
                model_ids = [(n.get("tag"), n.get("id"), n.get("content_id"))
                             for n in (after_dom.get("nodes") or []) if n.get("tag") == "model-response"]
                recycled_users = [x for x in seen_user_ids if x and x not in user_ids]
                recycled_models = [x for x in seen_model_ids if x and x not in model_ids]
                if user_ids:
                    seen_user_ids = user_ids
                if model_ids:
                    seen_model_ids = model_ids
                rec.update({
                    "elapsed_ms": round((time.monotonic() - t0) * 1000),
                    "error": err,
                    "states": list(trace.get("states") or []),
                    "send_ack": bool(trace.get("send_ack")),
                    "user_count_after_send": trace.get("user_count_after_send"),
                    "user_count_after_response": (after_snap or {}).get("user_count"),
                    "model_count_after": (after_snap or {}).get("model_count"),
                    "latest_user_node_id": (user_ids[-1] if user_ids else None),
                    "latest_model_node_id": (model_ids[-1] if model_ids else None),
                    "scroll_top": after_dom.get("scroll_top"),
                    "scroll_height": after_dom.get("scroll_height"),
                    "generation_signal": bool(after_dom.get("generating") or after_dom.get("pending_count")),
                    "response_text_length": len(raw or ""),
                    "raw_preview": (raw or "")[:160],
                    "count_dropped": (
                        isinstance(rec.get("user_count_before"), int)
                        and isinstance((after_snap or {}).get("user_count"), int)
                        and (after_snap or {}).get("user_count") < rec["user_count_before"]
                    ),
                    "recycled_user_ids": recycled_users,
                    "recycled_model_ids": recycled_models,
                    "mounted_after": after_dom.get("mounted"),
                    "inspect": after_dom,
                    "users_preview": [str(u.get("text") or "")[:80] for u in users],
                    "models_chars": [m.get("chars") for m in models],
                    "parent_tags": sorted({n.get("parent_tag") for n in (after_dom.get("nodes") or [])}),
                })
                records.append(rec)
                (OUT / "results.json").write_text(
                    json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps({
                    "phase": "after", "n": i, "request_id": request_id,
                    "prompt_hash": prompt_hash, "error": err,
                    "states": rec["states"][-8:],
                    "user_count_before": rec["user_count_before"],
                    "user_count_after_response": rec["user_count_after_response"],
                    "model_count_before": rec["model_count_before"],
                    "model_count_after": rec["model_count_after"],
                    "latest_user_node_id": rec["latest_user_node_id"],
                    "latest_model_node_id": rec["latest_model_node_id"],
                    "scroll_top": rec["scroll_top"],
                    "scroll_height": rec["scroll_height"],
                    "generation_signal": rec["generation_signal"],
                    "response_text_length": rec["response_text_length"],
                    "count_dropped": rec["count_dropped"],
                    "recycled_user_ids": recycled_users,
                    "recycled_model_ids": recycled_models,
                    "parent_tags": rec["parent_tags"],
                }, ensure_ascii=False), flush=True)
        finally:
            ctx.close()
    counts = [r.get("user_count_after_response") for r in records]
    monotonic = all(isinstance(a, int) and isinstance(b, int) and b >= a
                    for a, b in zip(counts, counts[1:]))
    summary = {
        "requests": len(records),
        "errors": sum(1 for r in records if r.get("error")),
        "count_non_monotonic": (not monotonic) or any(r.get("count_dropped") for r in records),
        "any_recycle": any(r.get("recycled_user_ids") or r.get("recycled_model_ids") for r in records),
        "parent_tags": sorted({t for r in records for t in (r.get("parent_tags") or []) if t}),
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if records else 1


if __name__ == "__main__":
    raise SystemExit(main())
