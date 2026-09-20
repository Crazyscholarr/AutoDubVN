"""Strategy-independent caption evaluation.

Coverage is always subtitle ∩ supplied VAD ranges. Callers must pass the same
speech intervals when comparing two transcripts. This module does not run ASR,
does not pack screen cues, and does not decide REVIEW_REQUIRED.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from ..srt_utils import Segment
from .merge import find_uncovered_speech_ranges, merge_time_ranges, speech_coverage_report

MEGA_CUE_S = 30.0
SIGNIFICANT_GAP_S = 1.2


def _finite(value) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def clip_speech_ranges(ranges: Sequence[Tuple[float, float]], start: float, end: float
                       ) -> List[Tuple[float, float]]:
    """Keep VAD overlapping [start, end) and shift to local 0."""
    out = []
    for a, b in ranges:
        lo, hi = max(float(a), start), min(float(b), end)
        if hi > lo:
            out.append((lo - start, hi - start))
    return out


def clip_segments(segments: Sequence[Segment], start: float, end: float) -> List[Segment]:
    out = []
    for i, seg in enumerate(segments, 1):
        lo, hi = max(seg.start, start), min(seg.end, end)
        if hi - lo < 0.05:
            continue
        out.append(Segment(i, lo - start, hi - start, seg.text))
    return out


def covered_ranges(segments: Sequence[Segment], duration: float) -> List[Tuple[float, float]]:
    return merge_time_ranges([
        (max(0.0, s.start), min(duration, s.end)) for s in segments if s.end > s.start
    ], merge_gap=0)


def interval_diff(old_covered: Sequence[Tuple[float, float]],
                  new_covered: Sequence[Tuple[float, float]],
                  speech: Sequence[Tuple[float, float]],
                  duration: float) -> List[dict]:
    """Speech intervals covered by old clocks but not by new clocks."""
    old = merge_time_ranges([(max(0.0, a), min(duration, b)) for a, b in old_covered], merge_gap=0)
    new = merge_time_ranges([(max(0.0, a), min(duration, b)) for a, b in new_covered], merge_gap=0)
    speech_u = merge_time_ranges([(max(0.0, a), min(duration, b)) for a, b in speech], merge_gap=0)
    lost = []
    ni = 0
    for rs, re in speech_u:
        cursor = rs
        while ni < len(new) and new[ni][1] <= rs:
            ni += 1
        j = ni
        holes = []
        while j < len(new) and new[j][0] < re:
            cs, ce = new[j]
            if cs > cursor:
                holes.append((cursor, cs))
            cursor = max(cursor, ce)
            j += 1
        if re > cursor:
            holes.append((cursor, re))
        for hs, he in holes:
            for oa, ob in old:
                lo, hi = max(hs, oa), min(he, ob)
                if hi - lo >= 0.05:
                    lost.append(dict(start=lo, end=hi, duration=hi - lo))
    return lost


def simulate_mega_windows(segments: Sequence[Segment],
                          windows: Sequence[Tuple[float, float]]) -> List[Segment]:
    """Replace every cue overlapping a window with one stretched cue (old metric)."""
    remaining = list(segments)
    extra = []
    for ws, we in windows:
        hit = [s for s in remaining if s.end > ws and s.start < we]
        if not hit:
            continue
        text = "".join(s.text or "" for s in hit)
        extra.append(Segment(0, min(s.start for s in hit), max(s.end for s in hit), text))
        remaining = [s for s in remaining if s not in hit]
    return remaining + extra


def evaluate_captions(segments: Sequence[Segment],
                      speech_ranges: Sequence[Tuple[float, float]],
                      duration: float, *,
                      min_gap: float = SIGNIFICANT_GAP_S,
                      mega_cue_s: float = MEGA_CUE_S) -> dict:
    segs = [s for s in segments if s is not None]
    naive = speech_coverage_report(segs, speech_ranges, duration)
    mega = [s for s in segs if (s.end - s.start) > mega_cue_s]
    honest_segs = [s for s in segs if (s.end - s.start) <= mega_cue_s]
    honest = speech_coverage_report(honest_segs, speech_ranges, duration)
    all_holes = find_uncovered_speech_ranges(
        honest_segs, speech_ranges, duration, min_gap=0, edge_pad=0, subtitle_pad=0)
    significant = [(a, b) for a, b in all_holes if b - a >= min_gap - 1e-9]
    subthreshold = [(a, b) for a, b in all_holes if b - a < min_gap - 1e-9]
    invalid = []
    for s in sorted(segs, key=lambda x: (x.start, x.end)):
        if not _finite(s.start) or not _finite(s.end) or s.start >= s.end or s.start < -0.05:
            invalid.append(dict(start=s.start, end=s.end, text=(s.text or "")[:40]))
        elif s.end > duration + 2.0:
            invalid.append(dict(start=s.start, end=s.end, text=(s.text or "")[:40],
                                reason="out_of_range"))
        previous = s
    texts = {}
    duplicates = 0
    for s in segs:
        key = (s.text or "").strip()
        if not key:
            continue
        bucket = texts.setdefault(key, [])
        if any(abs(other.start - s.start) < 0.4 for other in bucket):
            duplicates += 1
        bucket.append(s)
    naive_pct = float(naive["speech_coverage_percent"])
    honest_pct = float(honest["speech_coverage_percent"])
    unresolved = float(honest["unresolved_speech_s"])
    sig_s = sum(b - a for a, b in significant)
    sub_s = sum(b - a for a, b in subthreshold)
    return dict(
        duration_s=float(duration),
        cue_count=len(segs),
        total_text_chars=sum(len(s.text or "") for s in segs),
        mega_cue_count=len(mega),
        mega_cues=[dict(start=s.start, end=s.end, chars=len(s.text or ""),
                        duration=s.end - s.start) for s in mega],
        speech_duration_s=float(naive["speech_duration_s"]),
        naive_speech_coverage_percent=naive_pct,
        honest_speech_coverage_percent=honest_pct,
        inflation_pp=naive_pct - honest_pct,
        naive_unresolved_speech_s=float(naive["unresolved_speech_s"]),
        honest_unresolved_speech_s=unresolved,
        subtitle_covered_speech_s=float(honest["subtitle_covered_speech_s"]),
        subthreshold_gap_count=len(subthreshold),
        subthreshold_gap_s=sub_s,
        significant_gap_count=len(significant),
        significant_gap_s=sig_s,
        significant_gaps=[dict(start=a, end=b, duration=b - a) for a, b in significant],
        invalid_timestamps=invalid,
        duplicate_near_count=duplicates,
        timeline_ok=not invalid,
        contradiction=dict(
            unresolved_speech_s=unresolved,
            significant_gap_s=sig_s,
            subthreshold_gap_s=sub_s,
            explained_s=sig_s + sub_s,
            note=("Hard blockers count significant VAD holes and packing withheld rows. "
                  "Subthreshold holes (< min_gap) still reduce speech coverage."),
        ),
    )


def compare_transcripts(old: Sequence[Segment], new: Sequence[Segment],
                        speech_ranges: Sequence[Tuple[float, float]],
                        duration: float, *, min_gap: float = SIGNIFICANT_GAP_S) -> dict:
    old_eval = evaluate_captions(old, speech_ranges, duration, min_gap=min_gap)
    new_eval = evaluate_captions(new, speech_ranges, duration, min_gap=min_gap)
    lost = interval_diff(covered_ranges(old, duration), covered_ranges(new, duration),
                         speech_ranges, duration)
    honest_old = [s for s in old if s.end - s.start <= MEGA_CUE_S]
    honest_lost = interval_diff(
        covered_ranges(honest_old, duration), covered_ranges(new, duration),
        speech_ranges, duration)
    return dict(
        old=old_eval,
        new=new_eval,
        naive_coverage_delta_pp=(new_eval["naive_speech_coverage_percent"]
                                 - old_eval["naive_speech_coverage_percent"]),
        honest_coverage_delta_pp=(new_eval["honest_speech_coverage_percent"]
                                  - old_eval["honest_speech_coverage_percent"]),
        old_covered_new_missing_s=sum(row["duration"] for row in lost),
        honest_old_covered_new_missing_s=sum(row["duration"] for row in honest_lost),
        old_covered_new_missing=lost[:40],
        honest_old_covered_new_missing=honest_lost[:40],
    )


def gate_slice(result: dict, *, reference_honest_pct: Optional[float] = None,
               coverage_tolerance_pp: float = 1.0) -> Tuple[str, str]:
    """Return PASS/FAIL and a reason. Does not relax REVIEW_REQUIRED."""
    if not result.get("timeline_ok"):
        return "FAIL", "invalid_timestamps"
    if result.get("mega_cue_count"):
        return "FAIL", "mega_cue"
    if reference_honest_pct is not None:
        delta = reference_honest_pct - result["honest_speech_coverage_percent"]
        if delta > coverage_tolerance_pp:
            return "FAIL", f"honest_coverage_regression_{delta:.2f}pp"
    return "PASS", ""
