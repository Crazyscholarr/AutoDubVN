"""Tiện ích và trạng thái dùng chung cho các backend ASR."""
from __future__ import annotations

import re
import os
from typing import Any, Dict, List, Optional, Tuple

from ..srt_utils import Segment

_MODEL_CACHE = {}
_TAG_RE = re.compile(r"<\|[^|]*\|>")

# Ngôn ngữ không dùng khoảng trắng -> giới hạn ký tự mỗi dòng phải nhỏ hơn
_CJK = {"zh", "ja", "yue", "ko"}
_CJK_TERMINAL = set("\u3002\uff01\uff1f!?\u2026")

# MỐC THỜI GIAN TỪNG KÝ TỰ (nuôi autodub/speechmap.py)
_LAST_MARKS: List[Tuple[float, float]] = []

# Cắt phụ đề kiểu màn hình (CapCut/JianYing): ~8-16 chữ Hán / ~1.8-2.8 giây,
# ngắt khi im lặng. Số liệu SRT CapCut thật: TB 1.8s / 9 chữ, trần ~4.3s / 22 chữ.
_DEFAULT_CAPTION = {
    "style": "screen",
    "max_chars": 16,
    "min_chars": 4,
    "max_duration": 2.8,
    "hard_max_chars": 22,
    "hard_max_duration": 4.3,
    "gap": 0.32,
    "funasr_merge_length_s": 3.0,
}
_CAPTION: Dict[str, Any] = dict(_DEFAULT_CAPTION)


def source_reuse_path(cfg, keep_timing, asr_path, source_path):
    """An explicit reuse_existing=false must allow recognition after a review stop."""
    if not cfg.get('reuse_existing', True):
        return ''
    path = asr_path if keep_timing else source_path
    return path if os.path.isfile(path) else ''


def _clean(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _reindex(segs: List[Segment]) -> List[Segment]:
    for i, s in enumerate(segs, 1):
        s.index = i
    return segs


def caption_options() -> Dict[str, Any]:
    return dict(_CAPTION)


def caption_style_is_screen() -> bool:
    return str(_CAPTION.get("style") or "screen") == "screen"


def reset_caption_options() -> Dict[str, Any]:
    _CAPTION.clear()
    _CAPTION.update(_DEFAULT_CAPTION)
    return caption_options()


def _as_style(value: Any) -> str:
    raw = str(value or "screen").strip().lower()
    if raw in ("screen", "capcut", "jianying", "true", "1", "yes", "on"):
        return "screen"
    if raw in ("sentence", "cau", "asr", "false", "0", "no", "off"):
        return "sentence"
    return "screen"


def _as_num(value: Any, fallback: float, caster=float):
    try:
        if value is None or value == "":
            return caster(fallback)
        return caster(value)
    except (TypeError, ValueError):
        return caster(fallback)


def set_caption_options(cfg: Optional[Dict[str, Any]] = None,
                        **overrides) -> Dict[str, Any]:
    """Đọc mục asr: trong config.yaml (và kwargs) vào trạng thái lần chạy."""
    reset_caption_options()
    src: Dict[str, Any] = {}
    if isinstance(cfg, dict):
        src.update(cfg)
    src.update({k: v for k, v in overrides.items() if v is not None})
    style = _as_style(src.get("caption_style", _DEFAULT_CAPTION["style"]))
    _CAPTION["style"] = style
    _CAPTION["max_chars"] = max(4, _as_num(
        src.get("screen_max_chars"), _DEFAULT_CAPTION["max_chars"], int))
    _CAPTION["min_chars"] = max(1, _as_num(
        src.get("screen_min_chars"), _DEFAULT_CAPTION["min_chars"], int))
    _CAPTION["max_duration"] = max(0.6, _as_num(
        src.get("screen_max_duration"), _DEFAULT_CAPTION["max_duration"]))
    _CAPTION["hard_max_chars"] = max(
        int(_CAPTION["max_chars"]),
        _as_num(src.get("screen_hard_max_chars"),
                _DEFAULT_CAPTION["hard_max_chars"], int))
    _CAPTION["hard_max_duration"] = max(
        float(_CAPTION["max_duration"]),
        _as_num(src.get("screen_hard_max_duration"),
                _DEFAULT_CAPTION["hard_max_duration"]))
    _CAPTION["gap"] = max(0.12, _as_num(
        src.get("screen_gap"), _DEFAULT_CAPTION["gap"]))
    default_merge = 3.0 if style == "screen" else 15.0
    _CAPTION["funasr_merge_length_s"] = max(0.5, _as_num(
        src.get("funasr_merge_length_s"), default_merge))
    return caption_options()


def _max_chars_for(lang: Optional[str], default: int) -> int:
    if (lang or "").lower()[:2] in _CJK:
        if caption_style_is_screen():
            return int(_CAPTION.get("max_chars") or 14)
        return 34
    return default


def _is_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff" for c in s[:10])


def _set_last_marks(marks: List[Tuple[float, float]]) -> None:
    global _LAST_MARKS
    _LAST_MARKS = list(marks or [])


def _take_last_marks(offset: float = 0.0) -> List[Tuple[float, float]]:
    """Lấy (và xoá) mốc của lượt nhận diện vừa rồi, dịch về mốc tuyệt đối."""
    global _LAST_MARKS
    out, _LAST_MARKS = _LAST_MARKS, []
    if offset:
        out = [(a + offset, b + offset) for a, b in out]
    return out
