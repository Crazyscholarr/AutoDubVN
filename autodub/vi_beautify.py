"""Cửa sổ AI chỉnh ngắt cue Việt — chỉ text, fallback nếu JSON/nội dung lệch."""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

from .srt_utils import Segment, seconds_to_timestamp
from .utils import log
from .vi_reflow import (
    content_equivalent, detect_bad_windows, reflow_options, tidy_cue,
    validate_window, hard_boundary,
)


AskFn = Callable[[str], str]

_BEAUTIFY_SYSTEM = (
    "Bạn là chuyên gia biên tập phụ đề tiếng Việt cho phim. "
    "KHÔNG dịch lại, KHÔNG sáng tác. "
    "Chỉ điều chỉnh phân bố từ giữa các cue LIỀN KỀ cho câu tiếng Việt ngắt "
    "tự nhiên, đúng ngữ pháp, dễ đọc.\n"
    "QUY TẮC: giữ nguyên index, start, end, số lượng cue; không thêm/bỏ thông tin; "
    "không đổi tên riêng, số, ý nghĩa, thứ tự thoại; không chuyển chữ qua khoảng "
    "nghỉ dài hoặc đổi người nói; không cue rỗng; không khoảng trắng đầu/cuối; "
    "không hai khoảng trắng; câu mới viết hoa; không kết thúc cue ở chủ ngữ/"
    "liên từ/trợ động từ nếu tránh được.\n"
    "Ưu tiên ngắt sau câu hoàn chỉnh, dấu câu, mệnh đề, trạng ngữ, lời gọi.\n"
    "OUTPUT chỉ JSON array, đúng schema, không giải thích, không markdown."
)


def _payload(segments: Sequence[Segment]) -> List[Dict[str, str]]:
    rows = []
    for seg in segments:
        rows.append({
            "index": str(seg.index),
            "start": seconds_to_timestamp(seg.start),
            "end": seconds_to_timestamp(seg.end),
            "text": tidy_cue(seg.text or ""),
        })
    return rows


def _parse_rows(raw: str, expected: int) -> Optional[List[Dict[str, Any]]]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
        text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if isinstance(data, dict):
        data = data.get("cues") or data.get("items") or list(data.values())
    if not isinstance(data, list) or len(data) != expected:
        return None
    rows: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            return None
        rows.append(item)
    return rows


def _apply_rows(segments: List[Segment], rows: Sequence[Dict[str, Any]]) -> int:
    changed = 0
    for seg, row in zip(segments, rows):
        text = tidy_cue(str(row.get("text") if "text" in row else row.get("vi") or ""))
        if text and text != (seg.text or ""):
            seg.text = text
            changed += 1
    return changed


def beautify_windows(segments: List[Segment],
                     ask: AskFn,
                     cfg: Optional[Dict[str, Any]] = None) -> int:
    """Chỉ gửi cửa sổ còn ngắt xấu. Lỗi JSON/nội dung → giữ bản rule."""
    opt = reflow_options(cfg)
    mode = opt["beautify"]
    if mode in {"false", "off", "0", "no"} or not segments or ask is None:
        return 0
    if mode == "true":
        spans = []
        step = max(2, opt["window"] - opt["overlap"])
        i = 0
        n = len(segments)
        while i < n:
            hi = min(n, i + opt["window"])
            if hi - i >= 2:
                spans.append((i, hi))
            if hi >= n:
                break
            i += step
    else:
        spans = detect_bad_windows(
            segments, window=opt["window"], overlap=opt["overlap"],
            threshold=opt["bad_threshold"])
    # Even auto windows include context neighbours; split at hard boundaries
    # before prompting so the model never redistributes across speakers/pauses.
    bounded = []
    for lo, hi in spans:
        start = lo
        for i in range(lo + 1, hi):
            if hard_boundary(segments[i - 1], segments[i]):
                if i - start >= 2:
                    bounded.append((start, i))
                start = i
        if hi - start >= 2:
            bounded.append((start, hi))
    spans = list(dict.fromkeys(bounded))
    if not spans:
        return 0
    log(f"Còn {len(spans)} cửa sổ SRT Việt ngắt chưa đẹp → hỏi AI chỉnh chữ "
        "(giữ mốc thời gian).", "info")
    changed = 0
    applied_upto = 0
    for lo, hi in spans:
        window = segments[lo:hi]
        payload = _payload(window)
        prompt = (
            _BEAUTIFY_SYSTEM
            + "\n\nINPUT:\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
            + "\n\nTrả về JSON array ĐÚNG "
            + str(len(payload))
            + " object, mỗi object có index, start, end, text. Chỉ sửa text."
        )
        snapshot = [s.text for s in window]
        try:
            raw = ask(prompt)
        except InterruptedError:
            raise
        except Exception as exc:
            log(f"Cửa sổ cue {window[0].index}-{window[-1].index}: AI lỗi "
                f"({exc}); giữ bản chia ngữ pháp.", "warn")
            continue
        rows = _parse_rows(raw, len(window))
        if not rows:
            log(f"Cửa sổ cue {window[0].index}-{window[-1].index}: JSON không "
                "hợp lệ; giữ bản chia ngữ pháp.", "warn")
            continue
        reason = validate_window(window, rows)
        if reason:
            log(f"Cửa sổ cue {window[0].index}-{window[-1].index}: {reason} "
                "→ giữ bản chia ngữ pháp.", "warn")
            continue
        keep_head = max(0, applied_upto - lo)
        if keep_head:
            # Overlap: giữ bản cửa sổ trước, chỉ áp phần cue mới.
            for j in range(keep_head):
                rows[j]["text"] = snapshot[j]
        applied = _apply_rows(window, rows)
        after = [s.text for s in window]
        if not content_equivalent(snapshot, after):
            for seg, old in zip(window, snapshot):
                seg.text = old
            log(f"Cửa sổ cue {window[0].index}-{window[-1].index}: nội dung "
                "lệch sau khi áp; hoàn tác.", "warn")
            continue
        changed += applied
        applied_upto = max(applied_upto, hi)
    return changed
