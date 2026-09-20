"""Reuse an existing Vietnamese SRT when ASR/CapCut recut changes cue count.

keep_source_timing equal count is 1:1 by index: speechmap/TTS may rewrite
clocks without changing which sentence is which. Clock-align is for a ±1
recut (different counts). Aligning a same-count remapped clock onto the
previous cue would pair the wrong Vietnamese onto the source.
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Sequence, Tuple

from ..semantic import copy_metadata
from ..srt_utils import Segment, normalize_vi_subtitle_text
from .cjk_residue import apply_residual_repairs, leftover_cjk_indices, repair_residual_cjk

_LATIN_RE = re.compile(r"[A-Za-zÀ-ỹ]")
# Same-count VI whose clocks were rewritten (TTS atrim / speechmap) still
# pairs 1:1. Reject only a globally different timeline.
_INDEX_PAIR_MAX_MEDIAN = 30.0

# Start-time slack for CapCut screen-pack jitter.
_START_TOL = 0.45
# Keep reuse only when most cues still sit on the same clocks.
_MIN_KEEP_RATIO = 0.90


def cue_overlap(a: Segment, b: Segment) -> float:
    return max(0.0, min(float(a.end), float(b.end)) - max(float(a.start), float(b.start)))


def align_vi_to_source(
    src_segs: Sequence[Segment],
    vi_segs: Sequence[Segment],
    start_tol: float = _START_TOL,
    min_keep_ratio: float = _MIN_KEEP_RATIO,
) -> Optional[List[List[int]]]:
    """Map each source cue to zero or more VI cues by time.

    Returns None when clocks have diverged (caller must full-translate).
    """
    if not src_segs or not vi_segs:
        return None
    n, m = len(src_segs), len(vi_segs)
    used = [False] * m
    assigned: List[List[int]] = [[] for _ in range(n)]
    j0 = 0
    for i, src in enumerate(src_segs):
        while j0 < m and vi_segs[j0].end <= src.start - start_tol:
            j0 += 1
        best = None
        best_score = -1.0
        k = j0
        while k < m and vi_segs[k].start <= src.end + start_tol:
            if not used[k]:
                vi = vi_segs[k]
                ov = cue_overlap(src, vi)
                ds = abs(src.start - vi.start)
                de = abs(src.end - vi.end)
                if ov > 0.0 or ds <= start_tol:
                    score = ov * 2.0 + (start_tol - min(ds, start_tol))
                    if de <= start_tol:
                        score += 0.25
                    if score > best_score:
                        best_score = score
                        best = k
            k += 1
        if best is None:
            continue
        assigned[i].append(best)
        used[best] = True
        k = best + 1
        while k < m and not used[k] and vi_segs[k].start < src.end - 0.02:
            vi = vi_segs[k]
            ov = cue_overlap(src, vi)
            vdur = max(0.05, vi.end - vi.start)
            if ov / vdur >= 0.6:
                assigned[i].append(k)
                used[k] = True
                k += 1
            else:
                break
    matched = sum(1 for row in assigned if row)
    need = max(1, int(math.ceil(min_keep_ratio * min(n, m))))
    if matched < need:
        return None
    return assigned


def _spoken_text(vi: Segment) -> str:
    text = vi.text or ""
    if vi.semantic_group is None:
        text = normalize_vi_subtitle_text(text)
    return text


def _index_pairing_ok(src_segs: Sequence[Segment], vi_segs: Sequence[Segment]) -> bool:
    """Keep-source-timing: equal cue count means the i-th VI is the i-th source.

    Clock overlap can fail after speechmap snap or TTS writeback even though
    the Vietnamese file is still the translation of this ASR. Zip only a
    globally uniform shift (picture-clock remap, TTS atrim). An interior recut
    has outlier start-deltas and must clock-align. Reject a 60s-shifted file
    from another video.
    """
    n = len(src_segs)
    if n < 2 or len(vi_segs) != n:
        return False
    latin = sum(1 for vi in vi_segs if _LATIN_RE.search(vi.text or ""))
    if latin < int(0.8 * n):
        return False
    signed = [float(src.start) - float(vi.start) for src, vi in zip(src_segs, vi_segs)]
    absd = sorted(abs(x) for x in signed)
    if absd[n // 2] >= _INDEX_PAIR_MAX_MEDIAN:
        return False
    med = sorted(signed)[n // 2]
    outliers = sum(1 for x in signed if abs(x - med) > 1.5)
    return outliers == 0


def hydrate_equal_length(
    src_segs: List[Segment],
    vi_segs: List[Segment],
    rows: Optional[List[Dict]] = None,
) -> int:
    """Copy a matching-length VI file onto source cues after residue repair."""
    originals = [s.text for s in src_segs]
    repaired = apply_residual_repairs(vi_segs, sources=originals)
    rows = rows if rows is not None else []
    for i, (src, vi) in enumerate(zip(src_segs, vi_segs)):
        src.text = _spoken_text(vi)
        copy_metadata(vi, src)
        if i < len(rows):
            rows[i]["vi"] = src.text
            copy_metadata(src, rows[i])
    return repaired


def apply_aligned_vi(
    src_segs: List[Segment],
    vi_segs: List[Segment],
    assigned: List[List[int]],
    rows: Optional[List[Dict]] = None,
) -> int:
    """Write matched VI text onto current source clocks. Unmatched keep source CJK."""
    rows = rows if rows is not None else []
    copied = 0
    for i, idxs in enumerate(assigned):
        if not idxs:
            continue
        pieces = []
        for j in idxs:
            text = _spoken_text(vi_segs[j]).strip()
            if text:
                pieces.append(text)
        if not pieces:
            continue
        src = src_segs[i]
        original = src.text
        joined = " ".join(pieces)
        names = tuple(dict.fromkeys(
            name
            for j in idxs
            for name in (
                getattr(vi_segs[j], "allowed_source_names", ()) or ()
            )
            if name
        ))
        src.text = repair_residual_cjk(joined, names, source=original)
        copy_metadata(vi_segs[idxs[0]], src)
        # A source cue may absorb several old VI cues. Metadata from only the
        # first cue loses a keep-source name present in later text and makes
        # the TTS CJK gate falsely retranslate it.
        src.allowed_source_names = names
        if i < len(rows):
            rows[i]["vi"] = src.text
            copy_metadata(src, rows[i])
        copied += 1
    return copied


def reuse_translated_cues(
    src_segs: List[Segment],
    vi_segs: List[Segment],
    rows: Optional[List[Dict]] = None,
) -> Tuple[bool, List[int], int, int]:
    """Hydrate source cues from an existing VI file.

    Returns (ok, dirty_indices, copied_count, repaired_count).
    ok=False → clocks diverged. dirty_indices still contain leftover CJK.
    """
    if not src_segs or not vi_segs:
        return False, list(range(len(src_segs or ()))), 0, 0
    same_len = len(vi_segs) == len(src_segs)
    if same_len and _index_pairing_ok(src_segs, vi_segs):
        # Zip by index before clock-align. A 1-cue picture-clock remap still
        # overlaps well — align would copy vi[i-1] onto src[i]. Interior recut
        # fails pairing and falls through to align.
        repaired = hydrate_equal_length(src_segs, vi_segs, rows)
        return True, leftover_cjk_indices(src_segs), len(src_segs), repaired
    assigned = align_vi_to_source(src_segs, vi_segs)
    if assigned is not None:
        copied = apply_aligned_vi(src_segs, vi_segs, assigned, rows)
        return True, leftover_cjk_indices(src_segs), copied, 0
    return False, list(range(len(src_segs))), 0, 0
