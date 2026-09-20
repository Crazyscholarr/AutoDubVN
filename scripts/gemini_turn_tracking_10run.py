"""10 predefined short Gemini requests through production _ask_once."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autodub.translate import browser as B
from autodub.translate.jsonutil import classify_response_shape, extract_object
from playwright.sync_api import sync_playwright

OUT = ROOT / "_tmp" / "gemini_turn_10run"
REQUESTS = [
    "Return exactly this JSON object and nothing else:\n"
    '{"probe":"ten-%02d","ok":true,"n":%d}' % (i, i)
    for i in range(1, 11)
]
REQUIRED = (
    "SEND_ACKNOWLEDGED",
    "CURRENT_USER_TURN_IDENTIFIED",
    "MODEL_RESPONSE_NODE_IDENTIFIED",
    "RESPONSE_EXTRACTED",
)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    profile = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "browser_profile")
    submits = []
    original_submit = B._submit

    def counted_submit(page):
        submits.append(B.prompt_hash(getattr(page, "_turn_prompt", "") or ""))
        return original_submit(page)

    records = []
    hard = []
    with sync_playwright() as p:
        ctx = B._launch(p, profile, "msedge")
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(60000)
            page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
            if B._visible_locator(page, B._INPUT_CANDIDATES, timeout=25.0) is None:
                raise RuntimeError("Gemini composer not found")
            page = B._mo_chat_moi(page, "https://gemini.google.com/app")
            B._visible_locator(page, B._INPUT_CANDIDATES, timeout=20.0)
            B._submit = counted_submit
            fresh = "--fresh-chat" in sys.argv
            for i, prompt in enumerate(REQUESTS, 1):
                if fresh and i > 1:
                    page = B._mo_chat_moi(page, "https://gemini.google.com/app")
                    B._visible_locator(page, B._INPUT_CANDIDATES, timeout=20.0)
                page._turn_prompt = prompt
                trace = {}
                t0 = time.monotonic()
                raw, err, kind = "", "", ""
                try:
                    raw = B._ask_once(page, prompt, 90, trace=trace)
                except B.GeminiResponseError as exc:
                    err, kind = str(exc.kind), exc.kind
                    raw = ""
                except Exception as exc:
                    err, kind = str(exc)[:200], "DETECTOR_CRASH"
                    hard.append("crash:%d:%s" % (i, err))
                elapsed = round((time.monotonic() - t0) * 1000)
                shape = classify_response_shape(raw) if raw else "EMPTY_RESPONSE"
                parsed = None
                try:
                    parsed = extract_object(raw) if raw else None
                except Exception:
                    parsed = None
                probe = (parsed or {}).get("probe") if isinstance(parsed, dict) else None
                expected = "ten-%02d" % i
                states = list(trace.get("states") or [])
                missing = [s for s in REQUIRED if s not in states and s != "SEND_ACKNOWLEDGED"]
                if "SEND_ACKNOWLEDGED" not in states and "SEND_ACK_RECOVERED" not in states:
                    missing.append("SEND_ACK")
                wrong = bool(parsed) and probe != expected
                if wrong:
                    hard.append("wrong_turn:%d got %s" % (i, probe))
                if kind == "DETECTOR_CRASH":
                    pass
                rec = {
                    "n": i, "request_id": trace.get("request_id"),
                    "prompt_hash": trace.get("prompt_hash") or B.prompt_hash(prompt),
                    "elapsed_ms": elapsed, "error": err, "kind": kind,
                    "states": states, "shape": shape, "probe": probe,
                    "chars": len(raw or ""), "missing_states": missing,
                    "wrong_turn": wrong,
                    "send_ack_ms": trace.get("send_ack_ms"),
                    "turn_identification_ms": trace.get("turn_identification_ms"),
                    "response_start_ms": trace.get("response_start_ms"),
                    "generation_ms": trace.get("generation_ms"),
                    "duplicate_guard": trace.get("duplicate_guard"),
                    "raw_preview": (raw or "")[:160],
                }
                rec["pass"] = (not err and not missing and not wrong
                               and isinstance(parsed, dict) and parsed.get("ok") is True
                               and probe == expected and len(raw) > 0)
                records.append(rec)
                (OUT / "results.json").write_text(
                    json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps({k: rec[k] for k in (
                    "n", "pass", "kind", "probe", "chars", "missing_states",
                    "wrong_turn", "states")}, ensure_ascii=False), flush=True)
        finally:
            B._submit = original_submit
            ctx.close()
    hashes = [r["prompt_hash"] for r in records]
    if len(hashes) != len(set(hashes)):
        hard.append("duplicate_prompt_hash_in_plan")
    # More than one submit for the same successful consume is a duplicate send.
    sent = [h for h in submits if h]
    if len(sent) != len(set(sent)) and any(sent.count(h) > 1 for h in set(sent)):
        hard.append("duplicate_send")
    passed = sum(1 for r in records if r.get("pass"))
    summary = {
        "passed": passed, "total": len(records),
        "hard_failures": hard,
        "submits": len(submits),
        "status": "ACCEPTED" if passed >= 8 and len(records) == 10 and not hard else "REJECTED",
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary["status"] == "ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
