"""20 live semantic batches through production turn-tracking _ask_once."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autodub.translate import browser as B
from autodub.translate import semantic as S
from autodub.translate import jsonutil as J
from autodub.srt_utils import load_srt_file
from playwright.sync_api import sync_playwright


def main() -> int:
    job = next(p for p in (ROOT / "output").iterdir() if p.is_dir() and "BV1r" in p.name)
    source = next(job.glob("*.src.srt"))
    cues = load_srt_file(str(source))
    windows = [(lo, lo + 1) for lo in range(80, 100)]
    assert len(windows) == 20 and len(cues) >= windows[-1][1]
    out = ROOT / "_tmp" / ("gemini_turn_soak_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    out.mkdir(parents=True)
    print("ARTIFACT_DIR=" + str(out), flush=True)
    results, metrics, summary, glossary = [], S.response_metrics(), "", {}
    counters = dict(duplicate_send=0, wrong_turn=0, send_false_negative=0,
                    response_false_negative=0, parse_failure=0, cache_loss=0)
    submits = []
    original_submit = B._submit

    def counted_submit(page):
        submits.append(getattr(page, "_turn_hash", ""))
        return original_submit(page)

    B._submit = counted_submit
    cache_path = str(out / "soak_cache.json")
    try:
        with sync_playwright() as p:
            ctx = B._launch(p, sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "browser_profile"), "msedge")
            try:
                page = ctx.pages[0]
                page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
                B._visible_locator(page, B._INPUT_CANDIDATES)
                page = B._mo_chat_moi(page, "https://gemini.google.com/app")
                for batch, (lo, hi) in enumerate(windows, 1):
                    target = cues[lo:hi]
                    ids = {cue.index: batch for cue in target}
                    payload = dict(
                        project_style=dict(name_policy="han_viet", film_hint="", name_hint=""),
                        glossary=glossary, previous_summary=summary,
                        context_before=S.rows(cues[max(0, lo - 2):lo]),
                        target_cues=S.rows(target, ids),
                        context_after=S.rows(cues[hi:hi + 2]))
                    rec = dict(batch=batch, source_ids=[cue.index for cue in target], attempts=[])

                    def ask(prompt):
                        page._turn_hash = B.prompt_hash(prompt)
                        trace = {}
                        raw = B._ask_once(page, prompt, 240, trace=trace)
                        attempt = len(rec["attempts"]) + 1
                        (out / ("batch-%d-attempt-%d.txt" % (batch, attempt))).write_text(raw, encoding="utf-8")
                        shape = J.classify_response_shape(raw)
                        trace.update(shape=shape, chars=len(raw), preview=raw[:180])
                        rec["attempts"].append(trace)
                        return raw

                    warnings = []
                    obj, kind = S.request(
                        ask, S.TRANSLATE, payload,
                        S.translated_validator(target, ids, glossary, "han_viet"),
                        warnings, metrics=metrics, cache_path=cache_path)
                    rec.update(kind=kind, result="PASS" if obj is not None else "FAIL",
                               warnings=warnings,
                               send_ack_ms=(rec["attempts"][-1].get("send_ack_ms") if rec["attempts"] else None),
                               turn_identification_ms=(rec["attempts"][-1].get("turn_identification_ms") if rec["attempts"] else None),
                               response_start_ms=(rec["attempts"][-1].get("response_start_ms") if rec["attempts"] else None),
                               generation_ms=(rec["attempts"][-1].get("generation_ms") if rec["attempts"] else None))
                    if kind == "SEND_ACK_TIMEOUT":
                        counters["send_false_negative"] += 1
                    if kind in {"RESPONSE_START_TIMEOUT", "RESPONSE_EXTRACTION_FAILURE",
                                "RESPONSE_COMPLETION_TIMEOUT"}:
                        counters["response_false_negative"] += 1
                    if kind in {"MALFORMED_JSON", "TRUNCATED_JSON", "INVALID_JSON"}:
                        counters["parse_failure"] += 1
                    if obj:
                        got_ids = [row.get("id") for row in obj.get("translated_sentences") or []]
                        if got_ids and got_ids != [cue.index for cue in target]:
                            counters["wrong_turn"] += 1
                            rec["wrong_turn"] = got_ids
                        summary = obj["updated_summary"]
                        for entity in obj["new_entities"]:
                            if not entity["needs_review"] and entity["confidence"] >= .9:
                                glossary[entity["source"]] = dict(vi=entity["vi"], locked=True)
                    results.append(rec)
                    (out / "results.json").write_text(
                        json.dumps(dict(source=str(source), metrics=metrics, counters=counters,
                                        results=results), ensure_ascii=False, indent=2),
                        encoding="utf-8")
                    print(json.dumps(dict(batch=batch, result=rec["result"], kind=kind,
                                          metrics=metrics, counters=counters),
                                     ensure_ascii=False), flush=True)
                    if kind.startswith("RESPONSE_") or kind == "SEND_ACK_TIMEOUT":
                        if sum(1 for r in results if r["result"] == "FAIL") >= 3:
                            break
            finally:
                ctx.close()
    finally:
        B._submit = original_submit
    sent = [h for h in submits if h]
    if any(sent.count(h) > 1 for h in set(sent)):
        counters["duplicate_send"] = sum(sent.count(h) - 1 for h in set(sent) if sent.count(h) > 1)
    passed = sum(1 for r in results if r["result"] == "PASS")
    status = "ACCEPTED" if (
        len(results) == 20 and passed == 20
        and counters["duplicate_send"] == 0 and counters["wrong_turn"] == 0
        and counters["cache_loss"] == 0) else "REJECTED"
    summary_out = dict(passed=passed, total=len(results), counters=counters, status=status)
    (out / "summary.json").write_text(json.dumps(summary_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY " + json.dumps(summary_out, ensure_ascii=False), flush=True)
    return 0 if status == "ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
