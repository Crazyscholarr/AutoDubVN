"""Lần cuối làm sạch SRT Việt: đủ nghĩa từng nhịp đọc, không xé mốc thời gian.

Phụ đề gốc cắt kiểu màn hình nên bản dịch 1-1 hay ra mảnh cụt / chủ ngữ treo
('tôi' / 'đã bị...'). Pass này giữ nguyên start/end, chỉ sửa chữ:

1. glossary bắn đích (环 → điểm);
2. gom cụm ý rồi chia lại tiếng Việt theo ngữ pháp;
3. (tuỳ chọn) AI chỉnh cửa sổ còn ngắt xấu, có validator + fallback.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from .srt_utils import Segment
from .utils import log
from .vi_reflow import reflow_options, reflow_spoken_vi, tidy_cue


_NUM_VONG_RE = re.compile(
    r"(?i)(?<![A-Za-zÀ-ỹ])(\d+(?:[.,]\d+)?)\s*vòng\b"
)
_WORD_VONG_RE = re.compile(
    r"(?i)\b(không|một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười|mươi|linh)\s+vòng\b"
)


def apply_vi_dub_glossary(text: str) -> str:
    """Trong bắn đích, 环 = điểm (không phải vòng chạy/vòng hoa)."""
    raw = str(text or "")
    if not raw:
        return ""
    out = _NUM_VONG_RE.sub(r"\1 điểm", raw)
    out = _WORD_VONG_RE.sub(r"\1 điểm", out)
    return out


def finalize_spoken_vi_cues(
    segments: List[Segment],
    cfg: Optional[Dict[str, Any]] = None,
    ask: Optional[Callable[[str], str]] = None,
) -> int:
    """Giữ mốc thời gian, sửa chữ Việt. Trả số dòng đổi."""
    if not segments:
        return 0
    from .translate.cjk_residue import apply_residual_repairs
    repaired = apply_residual_repairs(segments)
    if all(s.semantic_group is not None for s in segments):
        # Sentence-first path already passed text/ownership gates. Do not apply
        # the legacy glossary/reflow afterwards and invalidate those guarantees.
        return repaired
    before = [str(s.text or "") for s in segments]
    # The glossary is specific to target shooting. Applying it to every film
    # turns running laps/competition rounds into points. Require local evidence,
    # including one neighbouring cue when the shooting phrase was split.
    for i, seg in enumerate(segments):
        context = " ".join(before[max(0, i - 1):i + 2])
        if (re.search(r"(?i)\b(bắn|trúng|xạ thủ)\b", context)
                and not re.search(r"(?i)\b(chạy|đua|vòng quanh|vòng thi)\b", before[i])):
            seg.text = apply_vi_dub_glossary(seg.text or "")
    changed = reflow_spoken_vi(segments, cfg)
    opt = reflow_options(cfg)
    if ask is not None and opt["beautify"] not in {"false", "off", "0", "no"}:
        from .vi_beautify import beautify_windows
        extra = beautify_windows(segments, ask, cfg)
        if extra:
            log(f"AI đã chỉnh {extra} dòng SRT Việt (giữ mốc).", "ok")
            changed += extra
    for seg in segments:
        # tidy giữ dấu phẩy cuối mệnh đề; normalize cũ hay cắt mất dấu đó.
        seg.text = tidy_cue(seg.text or "")
    n = sum(1 for seg, old in zip(segments, before) if (seg.text or "") != old)
    return (n if n else changed) + repaired
