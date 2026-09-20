"""Chia lại chữ Việt trong đúng timestamp gốc theo ngữ pháp, không theo ranh Trung.

Dịch 1-1 theo ô SRT Trung hay để chủ ngữ/liên từ treo cuối cue. Module này:

* gom các ô liền mạch thành một cụm ý;
* ghép chữ Việt của cụm;
* cắt lại tại điểm ngữ pháp đẹp, cân với thời lượng từng ô;
* giữ nguyên số cue, index, start/end.

Không gọi AI. Bước beautify (nếu bật) nằm ở ``vi_beautify``.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .srt_utils import Segment, seconds_to_timestamp
from .utils import log


_WORD_RE = re.compile(r"\S+")
_STRONG_RE = re.compile(r"[.!?…。！？]+$")
_SOFT_RE = re.compile(r"[,;:，、]+$")
_STRIP_PUNCT_RE = re.compile(r"^[\"'“‘(\[]+|[\"'”’)\].,;:!?…。！？，、]+$")
_NUMBER_RE = re.compile(r"^\d+(?:[.,]\d+)?$")
_CJK_TAIL_RE = re.compile(
    r"(我|你|他|她|它|我们|你们|他们|咱们|俺|因为|所以|但是|而且|如果|与|和)$"
)
_MULTI_AUX = {
    "có thể", "không thể", "có phải", "phải không", "được chưa",
}
_COMPOUNDS = _MULTI_AUX | {
    "tiếp theo", "bởi vì", "tại vì", "sau đó", "kể từ", "thế là",
    "vậy là", "trước khi", "cũng như", "cũng đã",
}
_FROZEN = _COMPOUNDS | {
    "tâm thần", "đạo đức", "đạo đức giả", "bố mẹ", "thân mến",
    "cú sốc", "giải phóng", "ma tính", "thế gian", "nuốt chửng",
    "chủ nhân", "tột cùng", "biết chưa",
    "lớn lên", "sống sót", "may mắn", "chúng ta", "cô ấy", "một mình",
    "thế giới", "tìm kiếm", "vật chứa", "về nhà", "hoàn hảo",
}
_SUBJECTS = {
    "tôi", "ta", "tao", "mình", "anh", "chị", "em", "hắn", "hắn ta",
    "cô", "cậu", "họ", "nó", "chúng", "chúng tôi", "chúng ta", "chúng mày",
    "người", "kẻ", "ai", "bạn", "mày", "ông", "bà", "đức",
}
_CONJS = {
    "và", "với", "hay", "hoặc", "nhưng", "mà", "nên", "vì", "nếu", "thì",
    "để", "khi", "rằng", "do", "bởi", "nên", "tuy", "dù", "hoặc là",
    "còn", "nên", "nên là", "bởi vì", "tại vì",
}
_AUX = {
    "đã", "đang", "sẽ", "vẫn", "cũng", "lại", "bị", "được", "muốn",
    "cần", "phải", "hãy", "đừng", "chớ", "sắp", "vừa", "mới",
}
_NEG = {"không", "chưa", "chẳng", "chả"}
_PREP = {
    "của", "với", "cho", "từ", "đến", "về", "trong", "ngoài", "trên",
    "dưới", "ở", "tại", "vào", "ra", "lên", "xuống", "sang", "qua",
    "tới", "bằng", "như",
}
_VOCATIVE = {"ơi", "à", "ạ", "nhé", "nhỉ", "hả", "ư"}
_UNITS = {
    "điểm", "lần", "người", "năm", "tháng", "ngày", "giờ", "phút",
    "giây", "tuổi", "km", "kg", "mét", "phần", "tỷ", "triệu",
}
_FUNCTION_START = _SUBJECTS | _CONJS | _AUX | _NEG | _PREP | {"hãy", "đừng"}


def reflow_options(tr: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = tr if isinstance(tr, dict) else {}
    mode = cfg.get("vi_beautify", "auto")
    beautify = str("auto" if mode is None else mode).strip().lower()
    if beautify in {"1", "yes", "on"}:
        beautify = "true"
    if beautify in {"0", "no", "off"}:
        beautify = "false"
    window = max(4, int(cfg.get("vi_beautify_window", 10) or 10))
    return {
        "enabled": cfg.get("vi_reflow", True) is not False,
        "max_gap": float(cfg.get("vi_reflow_max_gap", 0.70) or 0.70),
        "dangling_gap": float(cfg.get("vi_reflow_dangling_gap", 1.15) or 1.15),
        "max_cues": int(cfg.get("vi_reflow_max_cues", 8) or 8),
        "max_chars": int(cfg.get("vi_reflow_max_chars", 240) or 240),
        "max_duration": float(cfg.get("vi_reflow_max_duration", 14.0) or 14.0),
        "complete_gap": float(cfg.get("vi_reflow_complete_gap", 0.32) or 0.32),
        "target_cps": float(cfg.get("chars_per_sec", 18.0) or 18.0),
        "max_cps": float(cfg.get("max_cps", 22.0) or 22.0),
        "min_cue_duration": float(cfg.get("min_cue_duration", 0.08) or 0.08),
        "max_chars_per_line": int(cfg.get("max_chars_per_line", 42) or 42),
        "max_lines_per_cue": int(cfg.get("max_lines_per_cue", 2) or 2),
        "beautify": beautify,
        "window": window,
        "overlap": min(window - 1, max(0, int(cfg.get("vi_beautify_overlap", 2)))),
        "bad_threshold": float(cfg.get("vi_beautify_threshold", 8.0) or 8.0),
    }


def tidy_cue(text: str) -> str:
    """Gom khoảng trắng, giữ dấu phẩy/chấm cuối câu (normalize cũ hay cắt mất)."""
    out = re.sub(r"\s+", " ", str(text or "").strip())
    out = re.sub(r"\s+([,.;:!?…])", r"\1", out)
    out = re.sub(r"([(\[{“‘])\s+", r"\1", out)
    return out.strip(" \t\r\n")


def tokens(text: str) -> List[str]:
    return _WORD_RE.findall(tidy_cue(text))


def join_tokens(words: Sequence[str]) -> str:
    return tidy_cue(" ".join(words))


def core(word: str) -> str:
    raw = _STRIP_PUNCT_RE.sub("", str(word or ""))
    folded = unicodedata.normalize("NFC", raw).lower()
    return folded


def _bare(text: str) -> str:
    return tidy_cue(text).rstrip("\"'”’)]}»」』》")


def ends_strong(text: str) -> bool:
    raw = _bare(text)
    return bool(raw) and bool(_STRONG_RE.search(raw))


def _last_core(text: str) -> str:
    words = tokens(text)
    return core(words[-1]) if words else ""


def _first_core(text: str) -> str:
    words = tokens(text)
    return core(words[0]) if words else ""


def _pair_left(words: Sequence[str], index: int) -> str:
    a = core(words[index])
    if index <= 0:
        return a
    return core(words[index - 1]) + " " + a


def dangling_end(text: str) -> bool:
    """True khi ô chưa kết thúc ý (chủ ngữ/liên từ/trợ động từ treo)."""
    raw = _bare(text)
    if not raw or ends_strong(raw):
        return False
    words = tokens(raw)
    last = core(words[-1]) if words else ""
    pair = _pair_left(words, len(words) - 1) if words else last
    last_tok = words[-1] if words else ""
    if last in _VOCATIVE and _SOFT_RE.search(last_tok):
        return False
    if pair in _MULTI_AUX or last in _SUBJECTS or last in _CONJS:
        return True
    if last in _AUX or last in _NEG or last in _PREP or last in _VOCATIVE:
        return True
    stripped = re.sub(r"[，。！？、…,.\s]+$", "", raw)
    if _CJK_TAIL_RE.search(stripped):
        return True
    return False


def glue_pair(left: str, right: str) -> bool:
    """True khi hai ô đang cắt giữa cụm cố định (tiếp theo, có thể, ...)."""
    a, b = _last_core(left), _first_core(right)
    if not a or not b:
        return False
    pair = a + " " + b
    if pair in _COMPOUNDS or pair in _FROZEN:
        return True
    if _NUMBER_RE.match(a) and b in _UNITS:
        return True
    if b in {"chưa", "hả", "nhỉ", "chứ", "đâu"} and not ends_strong(left):
        return True
    # Bản dịch cũ hay copy 1-3 chữ sang cue sau.
    if a == b and not ends_strong(left):
        return True
    return False


def bad_break_score(left: str, right: str) -> float:
    """Điểm ngắt xấu giữa hai cue liền kề. 0 = ổn."""
    if not left or not right:
        return 0.0
    if ends_strong(left):
        # "này? Hay" là đẹp nếu Hay bắt đầu mệnh đề mới — không phạt.
        return 0.0
    score = 0.0
    last = _last_core(left)
    first = _first_core(right)
    left_words = tokens(left)
    pair = _pair_left(left_words, len(left_words) - 1) if left_words else last
    if last in _SUBJECTS and first in (_AUX | _NEG | {"có", "là", "đang", "đã", "sẽ"}):
        score += 12
    if last in _SUBJECTS and not _SOFT_RE.search(left_words[-1] if left_words else ""):
        score += 10
    if last in _CONJS:
        score += 14
    if last in _AUX or pair in _MULTI_AUX:
        score += 12
    if last in _NEG:
        score += 11
    if last in _PREP:
        score += 9
    if last in _VOCATIVE and _SOFT_RE.search(left_words[-1] if left_words else ""):
        return 0.0
    if last in _VOCATIVE:
        score += 6
    if glue_pair(left, right):
        score += 16
    if dangling_end(left):
        score += 4
    return score


def detect_bad_windows(segments: Sequence[Segment],
                       window: int = 10,
                       overlap: int = 2,
                       threshold: float = 8.0) -> List[Tuple[int, int]]:
    """Các cửa sổ [start, end) còn ngắt xấu sau reflow, để gửi AI."""
    n = len(segments)
    window = max(2, int(window))
    overlap = min(window - 1, max(0, int(overlap)))
    if n < 2:
        return []
    flagged = [False] * n
    for i in range(n - 1):
        gap = max(0.0, segments[i + 1].start - segments[i].end)
        if gap > 1.4:
            continue
        if (segments[i].speaker and segments[i + 1].speaker
                and segments[i].speaker != segments[i + 1].speaker):
            continue
        if bad_break_score(segments[i].text or "", segments[i + 1].text or "") >= threshold:
            flagged[i] = flagged[i + 1] = True
    spans: List[Tuple[int, int]] = []
    i = 0
    while i < n:
        if not flagged[i]:
            i += 1
            continue
        start = i
        while i < n and flagged[i]:
            i += 1
        lo = max(0, start - 1)
        hi = min(n, i + 1)
        while hi - lo > window:
            spans.append((lo, lo + window))
            lo = lo + window - overlap
        if hi > lo:
            spans.append((lo, hi))
    merged: List[Tuple[int, int]] = []
    for span in spans:
        if (merged and span[0] <= merged[-1][1]
                and span[1] - merged[-1][0] <= window):
            merged[-1] = (merged[-1][0], max(merged[-1][1], span[1]))
        else:
            merged.append(span)
    return [(a, b) for a, b in merged if b - a >= 2]


def group_cues(segments: Sequence[Segment],
               max_gap: float = 0.70,
               dangling_gap: float = 1.15,
               max_cues: int = 8,
               max_chars: int = 240,
               max_duration: float = 14.0,
               complete_gap: float = 0.32) -> List[Tuple[int, int]]:
    """Trả list (start, end) exclusive của các cụm ý."""
    n = len(segments)
    if n <= 1:
        return [(0, n)] if n else []
    groups: List[Tuple[int, int]] = []
    start = 0

    def _hard_limit(i: int) -> bool:
        cues = i - start
        chars = sum(len(segments[k].text or "") for k in range(start, i))
        dur = segments[i - 1].end - segments[start].start
        return cues >= max_cues or chars >= max_chars or dur >= max_duration

    for i in range(1, n):
        prev, cur = segments[i - 1], segments[i]
        gap = max(0.0, cur.start - prev.end)
        speaker_change = bool(
            prev.speaker and cur.speaker and prev.speaker != cur.speaker)
        hanging = dangling_end(prev.text or "")
        glued = glue_pair(prev.text or "", cur.text or "")
        split = False
        if speaker_change:
            split = True
        elif _hard_limit(i):
            split = True
        elif (hanging or glued) and gap <= dangling_gap and not _hard_limit(i + 1):
            split = False
        elif (len(tokens(prev.text or "")) <= 2 and not ends_strong(prev.text or "")
              and gap <= dangling_gap and not _hard_limit(i + 1)):
            split = False
        elif gap > max_gap:
            split = True
        elif ends_strong(prev.text or "") and not glued:
            split = True
        elif not hanging and not glued:
            split = True
        if split:
            groups.append((start, i))
            start = i
    groups.append((start, n))
    return groups


def _group_has_bad_break(segments: Sequence[Segment], threshold: float = 8.0) -> bool:
    for i in range(len(segments) - 1):
        gap = max(0.0, segments[i + 1].start - segments[i].end)
        if gap > 1.4:
            continue
        if (segments[i].speaker and segments[i + 1].speaker
                and segments[i].speaker != segments[i + 1].speaker):
            continue
        if bad_break_score(segments[i].text or "",
                           segments[i + 1].text or "") >= threshold:
            return True
        if glue_pair(segments[i].text or "", segments[i + 1].text or ""):
            return True
        if len(tokens(segments[i].text or "")) <= 1 and not ends_strong(
                segments[i].text or ""):
            return True
    return False


def _left_role(words: Sequence[str], index: int) -> str:
    token = words[index]
    punct = _STRONG_RE.search(token) or _SOFT_RE.search(token)
    last = core(token)
    pair = _pair_left(words, index)
    if pair in _MULTI_AUX:
        return "aux"
    if last in _SUBJECTS:
        return "subject"
    if last in _CONJS:
        return "conj"
    if last in _AUX:
        return "aux"
    if last in _NEG:
        return "neg"
    if last in _PREP:
        return "prep"
    if last in _VOCATIVE:
        return "vocative"
    if punct and _STRONG_RE.search(token):
        return "strong"
    if punct and _SOFT_RE.search(token):
        return "comma"
    return "word"


def _right_role(words: Sequence[str], index: int) -> str:
    if index + 1 >= len(words):
        return "end"
    nxt = core(words[index + 1])
    nxt2 = core(words[index + 2]) if index + 2 < len(words) else ""
    pair = (nxt + " " + nxt2).strip()
    if pair in _MULTI_AUX or nxt in _AUX:
        return "aux"
    if nxt in _NEG:
        return "neg"
    if nxt in _CONJS:
        return "conj"
    if nxt in _SUBJECTS:
        return "subject"
    if nxt in _PREP:
        return "prep"
    return "word"


def _break_score(words: Sequence[str], index: int, target: int) -> float:
    """Điểm cắt sau token ``index``. Cao hơn = đẹp hơn."""
    if index < 0 or index >= len(words) - 1:
        return -1.0e9
    token = words[index]
    nxt = words[index + 1]
    left_role = _left_role(words, index)
    right_role = _right_role(words, index)
    score = 0.0
    if _STRONG_RE.search(token):
        score += 100.0
        if right_role == "conj":
            score += 18.0
    elif _SOFT_RE.search(token):
        score += 46.0
        if left_role == "vocative":
            score += 18.0
    if left_role == "subject" and right_role in {"aux", "neg", "word"}:
        score -= 96.0
    if left_role == "conj":
        score -= 102.0
    if left_role == "aux":
        score -= 96.0
    if left_role == "neg":
        score -= 90.0
    if left_role == "prep":
        score -= 86.0
    if left_role == "subject" and not _SOFT_RE.search(token) and not _STRONG_RE.search(token):
        score -= 55.0
    if (core(token).replace(",", "").replace(".", "").isdigit()
            or _NUMBER_RE.match(core(token))) and core(nxt) in _UNITS:
        score -= 130.0
    if _frozen_break(words, index):
        score -= 125.0
    if token[:1].isupper() and nxt[:1].isupper() and not (
            _STRONG_RE.search(token) or _SOFT_RE.search(token)):
        score -= 40.0
    if left_role == "vocative":
        score += 28.0
    score -= abs(index - target) * 2.15
    left_n = index + 1
    right_n = len(words) - index - 1
    if left_n == 1 and left_role not in {"strong", "word"}:
        score -= 20.0
    if right_n == 1 and left_role != "strong":
        score -= 12.0
    return score


def _frozen_break(words: Sequence[str], index: int) -> bool:
    if index < 0 or index + 1 >= len(words):
        return False
    a, b = core(words[index]), core(words[index + 1])
    pair = a + " " + b
    if pair in _FROZEN:
        return True
    if index + 2 < len(words) and pair + " " + core(words[index + 2]) in _FROZEN:
        return True
    if index >= 1 and core(words[index - 1]) + " " + pair in _FROZEN:
        return True
    return False


def stitch_tokens(texts: Sequence[str]) -> List[str]:
    """Ghép nguyên token: từ lặp có thể là lời thoại có chủ ý."""
    return [word for text in texts for word in tokens(text)]


def split_tokens_for_durations(words: Sequence[str],
                               durations: Sequence[float]) -> List[str]:
    """Chia token thành ``len(durations)`` phần, ưu tiên ngữ pháp rồi thời lượng."""
    n = len(durations)
    m = len(words)
    if n <= 0:
        return []
    if m == 0:
        return [""] * n
    if n == 1 or m == 1:
        parts = [""] * n
        parts[0] = join_tokens(words)
        return parts

    durs = [max(0.08, float(d or 0.0)) for d in durations]
    used = 0
    cuts: List[int] = []
    for k in range(n - 1):
        rest_parts = n - k
        rest_tokens = m - used
        min_take = 1
        max_take = max(min_take, rest_tokens - (rest_parts - 1))
        rem_dur = sum(durs[k:]) or 1.0
        want = rest_tokens * (durs[k] / rem_dur)
        take = max(min_take, min(max_take, int(round(want))))
        target = used + take - 1
        best_i, best_s = used, -1.0e18
        for i in range(used, used + max_take):
            s = _break_score(words, i, target)
            if s > best_s:
                best_s, best_i = s, i
        cuts.append(best_i)
        used = best_i + 1

    parts: List[str] = []
    prev = 0
    for cut in cuts:
        parts.append(join_tokens(words[prev:cut + 1]))
        prev = cut + 1
    parts.append(join_tokens(words[prev:]))
    while len(parts) < n:
        parts.append("")
    return _fill_empty(parts, list(words))


def _fill_empty(parts: List[str], words: List[str]) -> List[str]:
    """Cue rỗng thì lấy 1 từ từ hàng xóm — không để ô trống."""
    if not any(not p for p in parts) or not words:
        return parts
    bags = [tokens(p) for p in parts]
    for i, bag in enumerate(bags):
        if bag:
            continue
        if i > 0 and len(bags[i - 1]) > 1:
            bags[i] = [bags[i - 1].pop()]
        elif i + 1 < len(bags) and bags[i + 1]:
            bags[i] = [bags[i + 1].pop(0)]
        else:
            bags[i] = [words[min(i, len(words) - 1)]]
    return [join_tokens(bag) for bag in bags]


def _cap_first(text: str) -> str:
    raw = tidy_cue(text)
    if not raw:
        return raw
    for i, ch in enumerate(raw):
        if ch.isalpha():
            return raw[:i] + ch.upper() + raw[i + 1:]
    return raw


def _lower_first_function(text: str) -> str:
    raw = tidy_cue(text)
    words = tokens(raw)
    if not words:
        return raw
    head = core(words[0])
    if head not in _FUNCTION_START:
        return raw
    if words[0][:1].isupper() and words[0][:1].islower() is False:
        # Tên riêng 1 chữ hoa + phần còn lại thường: "Tôi" là đại từ.
        if head in _SUBJECTS | _CONJS | _AUX | _NEG | _PREP:
            lowered = words[0][0].lower() + words[0][1:]
            return join_tokens([lowered] + words[1:])
    return raw


def apply_caps(parts: Sequence[str]) -> List[str]:
    out: List[str] = []
    for i, part in enumerate(parts):
        text = tidy_cue(part)
        if i == 0:
            out.append(text)
            continue
        prev = out[-1]
        if ends_strong(prev):
            out.append(_cap_first(text))
        else:
            out.append(_lower_first_function(text))
    return out


def redistribute_group(segments: List[Segment]) -> int:
    """Chia lại chữ trong group, giữ start/end. Trả số dòng đổi."""
    if len(segments) <= 1:
        if segments:
            cleaned = tidy_cue(segments[0].text or "")
            if cleaned != (segments[0].text or ""):
                segments[0].text = cleaned
                return 1
        return 0
    words = stitch_tokens(s.text or "" for s in segments)
    if not words:
        return 0
    durs = [max(0.08, s.duration) for s in segments]
    parts = apply_caps(split_tokens_for_durations(words, durs))
    if any(not part for part in parts) or not content_equivalent(
            [s.text or "" for s in segments], parts):
        return 0
    changed = 0
    for seg, part in zip(segments, parts):
        text = tidy_cue(part)
        if text != (seg.text or ""):
            seg.text = text
            changed += 1
    return changed


def content_fingerprint(texts: Iterable[str]) -> Tuple[str, Tuple[str, ...], Tuple[str, ...]]:
    """Chuẩn hoá để so nội dung: chữ, số, tên."""
    joined = tidy_cue(" ".join(str(t or "") for t in texts)).lower()
    letters = re.sub(r"[^0-9a-zà-ỹ]+", "", joined)
    numbers = tuple(re.findall(r"\d+(?:[.,]\d+)?", joined))
    names = tuple(re.findall(r"\b[A-ZÀ-Ỹ][\wà-ỹ]+\b", " ".join(str(t or "") for t in texts)))
    return letters, numbers, names


def content_equivalent(before: Sequence[str], after: Sequence[str],
                       min_ratio: float = 0.88) -> bool:
    """Require identical Unicode words/numbers in order, allowing case/punctuation.

    ``min_ratio`` remains for call compatibility; fuzzy acceptance can silently
    remove a negation or repeat dialogue and is never safe for cue redistribution.
    """
    def fingerprint(texts):
        joined = unicodedata.normalize("NFC", " ".join(texts)).casefold()
        return re.findall(r"\d+(?:[.,]\d+)*|[^\W_]+", joined, re.UNICODE)
    return fingerprint(before) == fingerprint(after)


def hard_boundary(left: Segment, right: Segment) -> bool:
    """AI must not move dialogue across a pause or known speaker change."""
    from .semantic import boundary
    return (boundary(left, right) or bool(
        left.semantic_group is not None and right.semantic_group is not None
        and left.semantic_group != right.semantic_group))


def _lcs_ratio(a: str, b: str) -> float:
    if a == b:
        return 1.0
    # LCS độ dài cho chuỗi ngắn; nếu quá dài thì so prefix+set.
    if len(a) > 4000 or len(b) > 4000:
        return 1.0 if a[:200] == b[:200] and abs(len(a) - len(b)) < max(20, 0.1 * len(a)) else 0.0
    la, lb = len(a), len(b)
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur.append(prev[j - 1])
            else:
                cur.append(1 + min(prev[j], cur[-1], prev[j - 1]))
        prev = cur
    dist = prev[-1]
    return 1.0 - dist / max(la, lb, 1)


def validate_window(original: Sequence[Segment], updated: Sequence[Dict[str, Any]]
                    ) -> Optional[str]:
    """None nếu hợp lệ; ngược lại mô tả lỗi."""
    if len(updated) != len(original):
        return "sai số cue"
    for src, row in zip(original, updated):
        if not isinstance(row, dict):
            return "sai schema"
        raw_index = row.get("index")
        if isinstance(raw_index, bool) or not isinstance(raw_index, (str, int)):
            return "sai index"
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            return "thiếu index"
        if index != src.index:
            return "đổi index"
        start = str(row.get("start") or "")
        end = str(row.get("end") or "")
        if start != seconds_to_timestamp(src.start):
            return "đổi start"
        if end != seconds_to_timestamp(src.end):
            return "đổi end"
        text = row.get("text") if "text" in row else row.get("vi")
        if not isinstance(text, str):
            return "sai kiểu text"
        if re.search(r"[`*_]|^\s*(?:#{1,6}\s|>\s)|\[[^\]]+\]\([^)]+\)", text):
            return "markdown trong cue"
        if text != text.strip():
            return "khoảng trắng đầu/cuối"
        if "  " in text:
            return "hai khoảng trắng"
        if not text.strip():
            return "cue rỗng"
    before = [s.text or "" for s in original]
    after = [str(row.get("text") if "text" in row else row.get("vi") or "")
             for row in updated]
    if not content_equivalent(before, after):
        return "đổi nội dung"
    lo = 0
    for i in range(1, len(original) + 1):
        if i == len(original) or hard_boundary(original[i - 1], original[i]):
            if not content_equivalent(before[lo:i], after[lo:i]):
                return "chuyển chữ qua khoảng nghỉ/người nói"
            lo = i
    return None


def reflow_spoken_vi(segments: List[Segment],
                     cfg: Optional[Dict[str, Any]] = None) -> int:
    """Gom cụm + chia lại chữ. Giữ mốc. Trả số dòng đổi."""
    opt = reflow_options(cfg)
    if not segments or not opt["enabled"]:
        return 0
    groups = group_cues(
        segments,
        max_gap=opt["max_gap"],
        dangling_gap=opt["dangling_gap"],
        max_cues=opt["max_cues"],
        max_chars=opt["max_chars"],
        max_duration=opt["max_duration"],
        complete_gap=opt["complete_gap"],
    )
    changed = 0
    for lo, hi in groups:
        subset = segments[lo:hi]
        if hi - lo > 1 and not _group_has_bad_break(subset, opt["bad_threshold"]):
            for seg in subset:
                cleaned = tidy_cue(seg.text or "")
                if cleaned != (seg.text or ""):
                    seg.text = cleaned
                    changed += 1
            continue
        changed += redistribute_group(subset)
    if changed:
        log(f"Đã chia lại {changed} dòng SRT Việt theo ngữ pháp "
            f"(giữ mốc, {len(groups)} cụm ý).", "info")
    return changed


def prompt_group_hint(chunk: Sequence[Segment]) -> str:
    """Ghi chú cho prompt dịch: những dòng nguồn là mảnh cùng một câu."""
    if len(chunk) < 2:
        return ""
    bits = []
    for a, b in group_cues(chunk):
        if b - a < 2:
            continue
        ids = [str(k) for k in range(a + 1, b + 1)]
        bits.append("[" + "]+[".join(ids) + "]")
    if not bits:
        return ""
    return (
        "Các dòng nguồn sau là MẢNH CÙNG MỘT CÂU (timestamp màn hình, không "
        "phải ranh ngữ pháp tiếng Việt): "
        + "; ".join(bits)
        + ". Hãy dịch theo câu tiếng Việt tự nhiên (được sắp xếp lại từ), "
        "vẫn trả ĐÚNG từng dòng [k] cho ô thời gian đó. "
        "Ưu tiên ngắt sau dấu phẩy/mệnh đề/lời gọi; CẤM ngắt giữa chủ-vị, "
        "liên từ-mệnh đề, trợ động từ-động từ. Không lặp chữ nối giữa các dòng.\n"
    )
