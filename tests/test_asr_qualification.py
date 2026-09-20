"""Predefined 10-run qualification against frozen artifacts. Never cherry-picks."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from autodub.asr.evaluate import (
    clip_segments,
    clip_speech_ranges,
    compare_transcripts,
    evaluate_captions,
    gate_slice,
)
from autodub.srt_utils import Segment, load_srt_file

HERE = Path(__file__).resolve().parents[1]
FILM = HERE / "output" / "一亩灵田修长生 [BV1GAY56VEwU]"
TMP = FILM / "_tmp"
DOCS = HERE / "docs" / "ASR_10_RUN_QUALIFICATION.md"
FORENSICS = HERE / "docs" / "asr_gap_forensics.json"

RUNS = [
    dict(run=1, name="5m speech", start=0.0, end=300.0),
    dict(run=2, name="10m dense", start=0.0, end=600.0),
    dict(run=3, name="10m mid mixed", start=1800.0, end=2400.0),
    dict(run=4, name="10m chunk boundary", start=540.0, end=660.0),
    dict(run=5, name="early problematic", start=0.0, end=600.0),
    dict(run=6, name="mid film", start=2400.0, end=3000.0),
    dict(run=7, name="late film", start=5400.0, end=6000.0),
    dict(run=8, name="30m continuous", start=5400.0, end=7200.0),
    dict(run=9, name="60m representative", start=0.0, end=3600.0),
    dict(run=10, name="FULL 120m", start=0.0, end=None),
]


def _load_speech(repair_path: Path):
    payload = json.loads(repair_path.read_text(encoding="utf-8"))
    ranges = payload.get("speech_ranges") or []
    return [(float(a), float(b)) for a, b in ranges], payload


def _duration(speech, fallback):
    if not speech:
        return fallback
    return max(fallback, max(b for a, b in speech))


class Qualification(unittest.TestCase):
    def test_write_ten_run_report_from_frozen_artifacts(self):
        working = FILM / f"{FILM.name}.asr.working.srt"
        recovered = FILM / f"{FILM.name}.asr.recovered.srt"
        repair = TMP / "asr-repair-9pp2j99f.json"
        review = TMP / "caption-review-j4qw_ep6" / "summary.json"
        if not working.exists() or not repair.exists():
            self.skipTest("film artifacts missing")
        speech, repair_payload = _load_speech(repair)
        current = load_srt_file(str(working))
        old = load_srt_file(str(recovered)) if recovered.exists() else []
        duration = float(
            (repair_payload.get("coverage") or {}).get("media_duration_s")
            or _duration(speech, 7217.45)
        )
        full_new = evaluate_captions(current, speech, duration)
        full_old = evaluate_captions(old, speech, duration) if old else None
        compared = (compare_transcripts(old, current, speech, duration)
                    if old else None)
        forensics = json.loads(FORENSICS.read_text(encoding="utf-8")) if FORENSICS.exists() else {}
        mega_windows = [(c["start"], c["end"])
                        for c in forensics.get("oversized_source_cues") or []]
        mega_eval = (evaluate_captions(
            [Segment(i + 1, a, b, "mega") for i, (a, b) in enumerate(mega_windows)],
            speech, duration) if mega_windows else None)
        blockers = {}
        if review.exists():
            blockers = json.loads(review.read_text(encoding="utf-8"))
        rows = []
        for spec in RUNS:
            start, end = spec["start"], spec["end"]
            if end is None:
                end = duration
                segs = current
                vad = speech
                span = duration
                ref = None if full_old is None else full_old["honest_speech_coverage_percent"]
            else:
                segs = clip_segments(current, start, end)
                vad = clip_speech_ranges(speech, start, end)
                span = end - start
                ref = None
                if old:
                    old_slice = evaluate_captions(
                        clip_segments(old, start, end), vad, span)
                    ref = old_slice["honest_speech_coverage_percent"]
            result = evaluate_captions(segs, vad, span)
            status, reason = gate_slice(result, reference_honest_pct=ref)
            if spec["run"] == 10:
                if blockers.get("reasons", {}).get("missing_speech_marks"):
                    status, reason = "FAIL", "missing_speech_marks_inserts"
                if not (TMP / "asr_chunks").exists():
                    status, reason = "FAIL", "missing_checkpoint"
            rows.append(dict(
                run=spec["run"], input=spec["name"],
                start=start, end=end, strategy="A-adaptive-10min",
                runtime="artifact",
                speech_cov=result["honest_speech_coverage_percent"],
                missing_s=result["honest_unresolved_speech_s"],
                significant_s=result["significant_gap_s"],
                mega=result["mega_cue_count"],
                timeline_ok=result["timeline_ok"],
                blockers=blockers.get("blocked_regions") if spec["run"] == 10 else result["significant_gap_count"],
                checkpoint="present" if (TMP / "asr_chunks").exists() else "missing",
                result=status, fail_reason=reason,
                cues=result["cue_count"],
            ))
        pass_count = sum(1 for row in rows if row["result"] == "PASS")
        full = next(row for row in rows if row["run"] == 10)
        accepted = (
            pass_count >= 8
            and full["result"] == "PASS"
            and not any(row["fail_reason"] in (
                "invalid_timestamps", "mega_cue", "missing_checkpoint")
                        for row in rows)
        )
        doc = _render(full_new, full_old, compared, blockers, rows,
                      pass_count, accepted, repair_payload, forensics, mega_eval)
        DOCS.parent.mkdir(parents=True, exist_ok=True)
        DOCS.write_text(doc, encoding="utf-8")
        self.assertTrue(DOCS.exists())
        self.assertGreater(full_new["subthreshold_gap_s"], 700)
        self.assertEqual(full_new["mega_cue_count"], 0)
        self.assertLess(full_new["inflation_pp"], 0.5)
        if full_old:
            self.assertGreater(full_old["inflation_pp"], 5)
            self.assertGreater(full_old["mega_cue_count"], 0)
        self.assertFalse(accepted)
        self.assertEqual(full["result"], "FAIL")
        self.assertTrue(forensics)
        old_cov = forensics["coverage"]["speech_coverage_percent"]
        self.assertGreater(old_cov, 92)
        self.assertGreater(
            old_cov - full_new["honest_speech_coverage_percent"], 5)
        self.assertGreater(mega_eval["naive_speech_coverage_percent"], 40)


def _render(current, old, compared, blockers, rows, pass_count, accepted, repair,
            forensics=None, mega_eval=None):
    forensics = forensics or {}
    old_recorded = forensics.get("coverage") or {}
    old_line = "n/a"
    if old:
        old_line = (
            f"naive {old['naive_speech_coverage_percent']:.2f}% "
            f"(mega cues {old['mega_cue_count']}, inflation "
            f"{old['inflation_pp']:.2f} pp) → honest "
            f"{old['honest_speech_coverage_percent']:.2f}%"
        )
    elif old_recorded:
        old_line = (
            f"recorded naive {old_recorded.get('speech_coverage_percent'):.2f}% "
            f"on {forensics.get('source_segments')} cues with "
            f"{len(forensics.get('oversized_source_cues') or [])} mega cues "
            f"(file asr.recovered.srt not present; same VAD duration "
            f"{old_recorded.get('speech_duration_s'):.2f}s)"
        )
    cmp_line = "n/a"
    if compared:
        cmp_line = (
            f"naive delta {compared['naive_coverage_delta_pp']:.2f} pp; "
            f"honest delta {compared['honest_coverage_delta_pp']:.2f} pp; "
            f"honest-old-covered-new-missing "
            f"{compared['honest_old_covered_new_missing_s']:.1f}s"
        )
    mega_line = "n/a"
    if mega_eval:
        mega_line = (
            f"{mega_eval['naive_speech_coverage_percent']:.2f}% of all speech "
            "painted by the two 30-minute clocks"
        )
    bd = repair.get("coverage_breakdown") or {}
    lines = [
        "# ASR 10-run qualification — Strategy A",
        "",
        "Date: 2026-09-17. Film: `一亩灵田修长生 [BV1GAY56VEwU]`.",
        "",
        "This is **not** an acceptance of Strategy A. Golden evaluator numbers",
        "were recomputed from frozen artifacts with one function:",
        "`autodub.asr.evaluate.evaluate_captions`.",
        "",
        "Runs 1–9 are slices of the 11:07 working SRT against the same FSMN VAD",
        "as the full film. They are not ten new FunASR jobs. Run 10 is the",
        "already-finished 120-minute GUI job. Code changes after 11:07 are not",
        "re-inferred on the full film in this round.",
        "",
        "## Golden recalculation: 92.57% vs 86.68%",
        "",
        f"- OLD recovered SRT: {old_line}",
        f"- CURRENT working SRT: naive {current['naive_speech_coverage_percent']:.2f}% "
        f"(mega {current['mega_cue_count']}) → honest "
        f"{current['honest_speech_coverage_percent']:.2f}%",
        f"- Same-VAD compare: {cmp_line}",
        f"- Mega-cue-only replay on current VAD: {mega_line}",
        f"- OLD forensics unresolved: "
        f"{old_recorded.get('unresolved_speech_s', 'n/a')}s "
        f"(subthreshold {forensics.get('subthreshold_gap_seconds')}, "
        f"significant {forensics.get('significant_gap_seconds')})",
        "",
        "Same evaluator, same VAD (`asr-repair-9pp2j99f.json` speech_ranges).",
        "OLD 92.57% used two source cues 0.05–1801s and 5398–7201s. CURRENT has",
        "zero mega cues, so naive = honest = 86.68%. The 5.89 pp gap equals",
        "~377s, which matches extra subthreshold holes (848.95 − 357.24) minus",
        "significant holes CURRENT already filled (118.42 − 4.36).",
        "That is metric inflation + visible inter-sentence gaps, not a 6 pp",
        "loss of spoken sentences.",
        "",
        "## 853s unresolved vs 5.46s blockers",
        "",
        f"- Honest unresolved speech: {current['honest_unresolved_speech_s']:.2f}s",
        f"- Subthreshold gaps (<1.2s): {current['subthreshold_gap_count']} / "
        f"{current['subthreshold_gap_s']:.2f}s",
        f"- Significant VAD gaps (≥1.2s): {current['significant_gap_count']} / "
        f"{current['significant_gap_s']:.2f}s",
        f"- Repair remaining after energy confirm: {repair.get('remaining')}",
        f"- Energy-dropped significant holes: "
        f"{(bd.get('significant_gap_s') if bd else 'n/a')}",
        f"- Review blockers: {blockers.get('blocked_regions')} / "
        f"{blockers.get('blocked_union_s')}s reasons={blockers.get('reasons')}",
        "",
        "The ~848s are inter-cue holes below the 1.2s gate. The 11:07 abort was",
        "`missing_speech_marks` on three crop inserts (`哇`, `江送 天`, `手有`),",
        "not 853s of missing dialogue. Coverage was not relaxed to hide this.",
        "",
        "## Strategy A defects addressed in code (not yet re-run on 120m)",
        "",
        "1. Crop insert without neighbor match / speech marks is removed.",
        "2. Neighbor cover waits until pad 0.35 and pad 1.0 have both run.",
        "3. Chunk plan is frozen so VAD jitter does not redo healthy chunks.",
        "4. Truncated `audio16k` (size ≤1024) is unlinked; extract writes `.partial`.",
        "5. Caption clock rescue stops after 4 attempts with 0 fixes.",
        "",
        "## 10-run matrix",
        "",
        "| Run | Input | Strategy | Runtime | Speech Cov | Missing s | Blockers | Checkpoint | Result |",
        "|-----|-------|----------|---------|------------|-----------|----------|------------|--------|",
    ]
    for row in rows:
        lines.append(
            f"| {row['run']} | {row['input']} | {row['strategy']} | "
            f"{row['runtime']} | {row['speech_cov']:.2f}% | "
            f"{row['missing_s']:.1f} | {row['blockers']} | "
            f"{row['checkpoint']} | {row['result']}"
            f"{(' ('+row['fail_reason']+')') if row['fail_reason'] else ''} |"
        )
    lines += [
        "",
        f"PASS COUNT: {pass_count}/10",
        "",
        "HARD FAILURES:",
        "- Run 10 GUI job: `missing_speech_marks` inserts; all 13 chunks",
        "  `reused_raw=False` after a 0-byte extract even though SHA matched.",
        "- Runtime of runs 1–9 is not a new wall-clock measurement.",
        "",
        "FINAL DECISION:",
        "",
        "ACCEPT" if accepted else "REJECT",
        "",
        "WHY A IS NOT DEFAULT:",
        "Full 120-minute job still fails a hard gate. Coverage 86.7% honest is",
        "not a 6 pp recall regression versus 92.57% naive mega-cue coverage, but",
        "Strategy A is not accepted until a new full-film run after the crop/",
        "checkpoint fixes scores ≥8/10 on this same matrix, including run 10.",
        "",
        "No threshold was loosened. Translation/TTS was not started.",
        "",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    unittest.main()
