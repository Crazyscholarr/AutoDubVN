"""Đọc/ghi SRT và mô hình dữ liệu Segment.

Toàn bộ thời gian bên trong chương trình dùng đơn vị GIÂY (float) cho tiện tính toán.
Chỉ khi ghi ra file .srt mới đổi sang định dạng HH:MM:SS,mmm.
"""
from __future__ import annotations

import re
import math
from dataclasses import dataclass, field, replace
from typing import List, Optional

from . import speechmap


@dataclass
class Segment:
    """Một dòng thoại/phụ đề."""
    index: int
    start: float          # giây
    end: float            # giây
    text: str
    speaker: Optional[str] = None   # ví dụ "SPEAKER_00" (sau khi diarize)
    # Các trường điền dần trong pipeline:
    audio_path: Optional[str] = None    # file wav TTS của dòng này
    voice: Optional[str] = None         # giọng đã gán
    placed_start: Optional[float] = None  # thời điểm đặt thực tế trên timeline
    speed: float = 1.0                    # hệ số tăng tốc đã áp dụng
    voice_duration: Optional[float] = None # độ dài voice sau khi tăng tốc (giây)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


# Ngắt mặc định chỉ ở dấu kết câu mạnh. Không tách ở dấu phẩy/， vì dễ làm
# câu bị vụn, subtitle/voice có cảm giác chạy nhanh hơn hình.
SPLIT_PUNCTUATION = ".!?。？！…"
_ELLIPSIS_RE = re.compile(r"(?:\.{3,}|…+)")
_SOURCE_TERMINAL_PUNCT = set(".!?。？！…")
_SOURCE_CLOSERS = set("\"'”’)]}»」』》")


_LEADING_TRANSLATION_META_RE = re.compile(
    r"^\s*[>\-*`_\u2022\u2013\u2014]*\s*(?:"
    r"(?:[\(\[\{]\s*)?(?:\u2264|<=)\s*\d{1,4}\s*(?:[\)\]\}:.,;-]+)?"
    r"|(?:[\(\[\{]\s*)?(?:max[_\s-]*chars|target|limit)\s*[:=]?\s*"
    r"(?:\u2264|<=)?\s*\d{1,4}\s*(?:[\)\]\}:.,;-]+)?"
    r")\s*",
    re.IGNORECASE,
)


def split_text_on_punctuation(text: str,
                              punctuation: str = SPLIT_PUNCTUATION) -> List[str]:
    """Split dialogue into readable clauses, keeping punctuation on each part."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return []
    out: List[str] = []
    buf: List[str] = []
    closers = set("\"'”’)]}»")
    punc = set(punctuation)
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        buf.append(ch)
        should_split = ch in punc
        if ch in ".,":  # do not split decimal/grouped numbers like 1.5 or 1,000
            prev_c = raw[i - 1] if i > 0 else ""
            next_c = raw[i + 1] if i + 1 < n else ""
            if prev_c.isdigit() and next_c.isdigit():
                should_split = False
        if should_split:
            while i + 1 < n and raw[i + 1] in punc:
                i += 1
                buf.append(raw[i])
            while i + 1 < n and raw[i + 1] in closers:
                i += 1
                buf.append(raw[i])
            part = "".join(buf).strip()
            if part:
                out.append(part)
            buf = []
            while i + 1 < n and raw[i + 1].isspace():
                i += 1
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out or [raw]


def split_segment_on_punctuation(seg: Segment,
                                 punctuation: str = SPLIT_PUNCTUATION,
                                 min_child_duration: float = 0.0) -> List[Segment]:
    """Split one translated segment into sequential timed child segments."""
    parts = split_text_on_punctuation(seg.text, punctuation)
    if len(parts) <= 1:
        return [replace(seg)]

    weights = [max(1, len(re.sub(r"\s+", "", p))) for p in parts]
    total = float(sum(weights)) or 1.0
    dur = max(0.01, seg.duration)
    min_child_duration = max(0.0, float(min_child_duration or 0.0))
    if min_child_duration > 0:
        grouped_parts: List[str] = []
        grouped_weights: List[int] = []
        buf: List[str] = []
        wbuf = 0
        for part, weight in zip(parts, weights):
            buf.append(part)
            wbuf += weight
            est = dur * (wbuf / total)
            if est >= min_child_duration:
                grouped_parts.append(" ".join(buf))
                grouped_weights.append(wbuf)
                buf, wbuf = [], 0
        if buf:
            if grouped_parts:
                grouped_parts[-1] = f"{grouped_parts[-1]} {' '.join(buf)}"
                grouped_weights[-1] += wbuf
            else:
                grouped_parts.append(" ".join(buf))
                grouped_weights.append(wbuf)
        parts, weights = grouped_parts, grouped_weights
        if len(parts) <= 1:
            return [replace(seg)]

    # Ưu tiên mốc thời gian THẬT của từng ký tự (bản đồ thoại). Chỉ khi không
    # có mới chia theo tỉ lệ - cách chia tỉ lệ coi cả ô là băng nói liên tục nên
    # ranh giới hay rơi vào quãng lặng, làm voice/sub lệch khỏi hình.
    bounds = speechmap.slice_window(seg.start, seg.end, weights)
    out: List[Segment] = []
    if bounds:
        for (a, b), part in zip(bounds, parts):
            child = replace(seg)
            child.start = a
            child.end = max(a + 0.01, b)
            child.text = part
            child.audio_path = None
            child.placed_start = None
            child.voice_duration = None
            child.speed = 1.0
            out.append(child)
        return out

    cur = seg.start
    for idx, (part, w) in enumerate(zip(parts, weights)):
        if idx == len(parts) - 1:
            end = seg.end
        else:
            end = min(seg.end, cur + dur * (w / total))
        child = replace(seg)
        child.start = cur
        child.end = max(cur + 0.01, end)
        child.text = part
        child.audio_path = None
        child.placed_start = None
        child.voice_duration = None
        child.speed = 1.0
        out.append(child)
        cur = child.end
    return out


def split_segments_on_punctuation(segments: List[Segment],
                                  punctuation: str = SPLIT_PUNCTUATION,
                                  min_child_duration: float = 0.0) -> List[Segment]:
    """Split translated subtitles by punctuation and renumber them."""
    out: List[Segment] = []
    for seg in segments:
        out.extend(split_segment_on_punctuation(
            seg, punctuation, min_child_duration=min_child_duration))
    for i, seg in enumerate(out, 1):
        seg.index = i
    return out


def _compact_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _looks_cjk(text: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff"
               for c in (text or "")[:16])


def _join_source_text(left: str, right: str) -> str:
    left = re.sub(r"\s+", " ", (left or "").strip())
    right = re.sub(r"\s+", " ", (right or "").strip())
    if not left:
        return right
    if not right:
        return left
    if _ends_with_ellipsis(left) or _starts_with_ellipsis(right):
        return left + right
    if _looks_cjk(left) and _looks_cjk(right):
        return left + right
    return left + " " + right


# Bộ chấm câu của ASR (ct-punc) hay đặt dấu vào giữa từ ghép và giữa mệnh đề,
# cho ra những dòng cụt như "个妇女多嘴" hay "季秋这里只？". Dịch từng dòng cụt
# như vậy thì model buộc phải tự ghép lại theo nghĩa rồi rải nội dung sang các
# dòng khác, làm lời thoại trôi khỏi mốc thời gian của nó. Ba bảng dưới đây
# nhận ra chỗ bị cắt để gộp lại trước khi dịch.

# Hậu tố / lượng từ: không bao giờ mở đầu một mệnh đề.
_CJK_NEVER_STARTS = set("们的了着过个儿子头里吧呢吗啊呀嘛"
                        "员右下中前后上边间样种次遍")
# Hư từ, giới từ, đại từ: không bao giờ kết thúc một mệnh đề.
_CJK_NEVER_ENDS = set("的地得很太最不没我你他她它这那一是在和跟与把被让给"
                      "从对向为就也都还又更才只很挺蛮而但却因所以及于")


def _strip_trailing_punct(text: str) -> str:
    s = (text or "").rstrip()
    while s and (s[-1] in _SOURCE_CLOSERS or s[-1] in _SOURCE_TERMINAL_PUNCT
                 or s[-1] in "，,、；;：:"):
        s = s[:-1].rstrip()
    return s


def _looks_cut_mid_clause(text: str) -> bool:
    """Dòng kết thúc bằng hư từ thì câu chưa xong, dù ASR có chấm câu."""
    s = _strip_trailing_punct(text)
    return bool(s) and s[-1] in _CJK_NEVER_ENDS


def _source_unit_complete(text: str) -> bool:
    s = (text or "").rstrip()
    while s and s[-1] in _SOURCE_CLOSERS:
        s = s[:-1].rstrip()
    if not s or s[-1] not in _SOURCE_TERMINAL_PUNCT:
        return False
    return not _looks_cut_mid_clause(s)


def _starts_like_continuation(text: str) -> bool:
    s = (text or "").lstrip()
    if not s:
        return False
    if _starts_with_ellipsis(s):
        return True
    if re.match(r"^[,;:，、；：)\]}\u2013\u2014]", s):
        return True
    return s[0] in _CJK_NEVER_STARTS


def repair_asr_punctuation(text: str) -> str:
    """Bỏ dấu phẩy mà ASR chèn vào giữa một từ ghép.

    "他，们快不行了" hay "拦不，住我" là dấu phẩy đặt sai chứ không phải chỗ
    ngắt hơi. Để nguyên thì model dịch hiểu sai ranh giới câu.
    """
    s = text or ""
    if not s:
        return s
    # Dấu phẩy đứng ngay trước một hậu tố, hoặc ngay sau một hư từ.
    s = re.sub(r"[，,]\s*(?=[" + "".join(_CJK_NEVER_STARTS) + r"])", "", s)
    s = re.sub(r"(?<=[" + "".join(_CJK_NEVER_ENDS) + r"])\s*[，,]", "", s)
    # Dấu kết câu chèn vào giữa từ ghép ("他。们") thì còn tai hại hơn dấu phẩy,
    # vì bước tách câu sẽ cắt đúng chỗ đó. Chỉ gỡ khi chữ đứng sau là hậu tố
    # thuần, không bao giờ mở đầu được một câu.
    s = re.sub(r"[。．.？?！!]\s*(?=[们的了着过儿])", "", s)
    return s


def prepare_source_segments_for_translation(
    segments: List[Segment],
    split_on_punctuation: bool = True,
    merge_fragments: bool = True,
    max_chars: int = 180,
    min_chars: int = 24,
    max_gap: float = 1.2,
    max_duration: float = 18.0,
) -> List[Segment]:
    """Make source subtitle rows semantic enough for translation.

    ASR often cuts mid-phrase ("năng" / "lực", "Nguy" / "Ma"). Translating
    those fragments line-by-line makes Gemini produce broken Vietnamese. This
    pass first splits rows that contain multiple full sentences, then merges
    adjacent unfinished fragments into a single translation unit.
    """
    work: List[Segment] = []
    for seg in segments:
        if _looks_cjk(seg.text):
            fixed = repair_asr_punctuation(seg.text)
            if fixed != seg.text:
                seg = replace(seg)
                seg.text = fixed
        if split_on_punctuation:
            work.extend(split_segment_on_punctuation(seg))
        else:
            work.append(replace(seg))
    if not merge_fragments:
        for i, seg in enumerate(work, 1):
            seg.index = i
        return work

    max_chars = max(40, int(max_chars or 180))
    min_chars = max(1, int(min_chars or 24))
    max_gap = max(0.0, float(max_gap or 0.0))
    max_duration = max(1.0, float(max_duration or 18.0))

    out: List[Segment] = []
    buf: Optional[Segment] = None

    def flush() -> None:
        nonlocal buf
        if buf is not None and (buf.text or "").strip():
            out.append(buf)
        buf = None

    for raw in work:
        txt = re.sub(r"\s+", " ", (raw.text or "").strip())
        if not txt:
            continue
        seg = replace(raw)
        seg.text = txt
        seg.audio_path = None
        seg.placed_start = None
        seg.voice_duration = None
        seg.speed = 1.0
        if buf is None:
            buf = seg
            continue

        same_speaker = (buf.speaker or "") == (seg.speaker or "")
        gap = max(0.0, seg.start - buf.end)
        combined = _join_source_text(buf.text, seg.text)
        combined_len = _compact_len(combined)
        combined_dur = max(buf.end, seg.end) - min(buf.start, seg.start)
        unfinished = not _source_unit_complete(buf.text)
        continuation = _starts_like_continuation(seg.text)
        short_unfinished = unfinished and _compact_len(buf.text) < min_chars
        # ASR punctuation is not reliable on very short bursts.  Treat a tiny
        # terminal-looking row as a fragment as well; otherwise a 200-500 ms
        # recognition such as "被同。" is translated into a full Vietnamese
        # sentence and later has no realistic TTS slot.  The duration guard
        # keeps genuine short replies (for example "Ai?" lasting 1 s) intact.
        tiny_limit = 6 if _looks_cjk(buf.text) else 10
        hard_tiny_fragment = (
            buf.duration <= 0.75
            and _compact_len(buf.text) <= min(min_chars, tiny_limit)
        )
        can_merge = (
            same_speaker
            and gap <= max_gap
            and combined_len <= max_chars
            and (combined_dur <= max_duration or short_unfinished
                 or continuation or hard_tiny_fragment)
        )

        if can_merge and (unfinished or continuation or hard_tiny_fragment):
            buf.text = combined
            buf.end = max(buf.end, seg.end)
            continue

        flush()
        buf = seg

    flush()
    for i, seg in enumerate(out, 1):
        seg.index = i
    return out


def _starts_with_ellipsis(text: str) -> bool:
    return bool(_ELLIPSIS_RE.match((text or "").lstrip()))


def _ends_with_ellipsis(text: str) -> bool:
    return bool(_ELLIPSIS_RE.search((text or "").rstrip()[-6:]))


def _strip_leading_translation_meta(text: str) -> str:
    """Drop prompt metadata that Gemini may echo before the actual subtitle."""
    out = text or ""
    for _ in range(4):
        cleaned = _LEADING_TRANSLATION_META_RE.sub("", out)
        if cleaned == out:
            break
        out = cleaned
    return out


def normalize_vi_subtitle_text(text: str) -> str:
    """Remove ASR/translation continuation ellipses from Vietnamese subtitle text."""
    raw = re.sub(r"\s+", " ", _strip_leading_translation_meta(text).strip())
    if not raw:
        return ""

    pieces: List[str] = []
    last = 0
    for m in _ELLIPSIS_RE.finditer(raw):
        pieces.append(raw[last:m.start()])
        prev = next((c for c in reversed(raw[:m.start()]) if not c.isspace()), "")
        nxt = next((c for c in raw[m.end():] if not c.isspace()), "")
        if prev and nxt and nxt.isupper() and prev not in ".!?":
            pieces.append(". ")
        else:
            pieces.append(" ")
        last = m.end()
    pieces.append(raw[last:])

    out = "".join(pieces)
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"([(\[{“‘])\s+", r"\1", out)
    out = re.sub(r"\s+([)\]}»”’])", r"\1", out)
    return out.strip(" \t\r\n,;:")


def _join_continuation_text(left: str, right: str) -> str:
    left = re.sub(r"\s+", " ", (left or "").strip())
    right = re.sub(r"\s+", " ", (right or "").strip())
    if not left:
        return normalize_vi_subtitle_text(right)
    if not right:
        return normalize_vi_subtitle_text(left)
    return normalize_vi_subtitle_text(left + " " + right)


def split_vi_text_naturally(text: str,
                            max_chars: int = 125,
                            min_chars: int = 24) -> List[str]:
    """Split Vietnamese translated text without treating ellipses as sentence ends."""
    raw = normalize_vi_subtitle_text(text)
    if not raw:
        return []

    placeholders: List[str] = []

    def hide_ellipsis(match: re.Match) -> str:
        placeholders.append(match.group(0))
        return f"\uE000{len(placeholders) - 1}\uE001"

    protected = _ELLIPSIS_RE.sub(hide_ellipsis, raw)
    candidates: List[str] = []
    buf: List[str] = []
    closers = set("\"'”’)]}»")
    i = 0
    while i < len(protected):
        ch = protected[i]
        buf.append(ch)
        if ch in ".!?":
            prev_c = protected[i - 1] if i > 0 else ""
            next_c = protected[i + 1] if i + 1 < len(protected) else ""
            if not (prev_c.isdigit() and next_c.isdigit()):
                while i + 1 < len(protected) and protected[i + 1] in closers:
                    i += 1
                    buf.append(protected[i])
                part = "".join(buf).strip()
                if part:
                    candidates.append(part)
                buf = []
                while i + 1 < len(protected) and protected[i + 1].isspace():
                    i += 1
        i += 1
    tail = "".join(buf).strip()
    if tail:
        candidates.append(tail)

    def restore(s: str) -> str:
        def repl(match: re.Match) -> str:
            return placeholders[int(match.group(1))]
        return re.sub(r"\uE000(\d+)\uE001", repl, s).strip()

    candidates = [restore(p) for p in candidates if restore(p)]
    if not candidates:
        candidates = [raw]

    expanded: List[str] = []
    for part in candidates:
        expanded.extend(_split_long_vi_candidate(part, max_chars, min_chars))
    candidates = expanded or candidates

    merged: List[str] = []
    soft_min_chars = min(max_chars * 0.45, 56)
    for part in candidates:
        if merged and len(merged[-1]) < soft_min_chars \
                and len(merged[-1]) + 1 + len(part) <= max_chars:
            merged[-1] = merged[-1] + " " + part
        else:
            merged.append(part)

    # Do not leave a tiny orphan sentence after an almost-full line (for
    # example a trailing "Mĩ.").  Allow a small soft overflow instead of
    # creating a subtitle whose timing and audio are both unusably short.
    soft_max_chars = max_chars + max(12, min_chars)
    i = 0
    while len(merged) > 1 and i < len(merged):
        if _compact_len(merged[i]) >= min_chars:
            i += 1
            continue
        choices = []
        if i > 0:
            text = normalize_vi_subtitle_text(merged[i - 1] + " " + merged[i])
            if len(text) <= soft_max_chars:
                choices.append((max(0, len(text) - max_chars), 0, text))
        if i + 1 < len(merged):
            text = normalize_vi_subtitle_text(merged[i] + " " + merged[i + 1])
            if len(text) <= soft_max_chars:
                choices.append((max(0, len(text) - max_chars), 1, text))
        if not choices:
            i += 1
            continue
        _, direction, text = min(choices, key=lambda item: (item[0], item[1]))
        if direction == 0:
            merged[i - 1] = text
            del merged[i]
            i = max(0, i - 1)
        else:
            merged[i] = text
            del merged[i + 1]
    return merged


def _split_long_vi_candidate(text: str,
                             max_chars: int = 125,
                             min_chars: int = 24) -> List[str]:
    """Break very long Vietnamese cues on soft pauses when no hard sentence end exists."""
    raw = normalize_vi_subtitle_text(text)
    if len(raw) <= max_chars:
        return [raw] if raw else []

    max_chars = max(40, int(max_chars or 125))
    min_chars = max(1, int(min_chars or 24))
    soft_breaks = set(",;:\u060c\u3001\uff0c\uff1b\uff1a")
    units: List[str] = []
    buf: List[str] = []
    i = 0
    while i < len(raw):
        m = _ELLIPSIS_RE.match(raw, i)
        if m:
            buf.append(m.group(0))
            i = m.end()
            part = "".join(buf).strip()
            if len(part) >= min_chars:
                units.append(part)
                buf = []
            while i < len(raw) and raw[i].isspace():
                i += 1
            continue

        ch = raw[i]
        buf.append(ch)
        if ch in soft_breaks and len("".join(buf).strip()) >= min_chars:
            units.append("".join(buf).strip())
            buf = []
            while i + 1 < len(raw) and raw[i + 1].isspace():
                i += 1
        i += 1
    tail = "".join(buf).strip()
    if tail:
        units.append(tail)

    if len(units) <= 1:
        units = raw.split(" ")

    out: List[str] = []
    cur = ""
    for unit in units:
        unit = unit.strip()
        if not unit:
            continue
        sep = "" if not cur or unit.startswith(("...", "\u2026")) else " "
        if cur and len(cur) + len(sep) + len(unit) > max_chars:
            out.append(cur.strip())
            cur = unit
        else:
            cur = f"{cur}{sep}{unit}" if cur else unit
    if cur.strip():
        out.append(cur.strip())
    return out or [raw]


def _split_balanced_words(text: str, pieces: int) -> List[str]:
    """Split a cue into roughly even chunks when timing is too wide."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    pieces = max(1, int(pieces or 1))
    if pieces <= 1 or not raw:
        return [raw] if raw else []

    words = raw.split(" ")
    if len(words) <= pieces:
        return [w for w in words if w]

    out: List[str] = []
    start = 0
    for i in range(pieces):
        remaining_words = len(words) - start
        remaining_pieces = pieces - i
        take = max(1, math.ceil(remaining_words / remaining_pieces))
        out.append(" ".join(words[start:start + take]).strip())
        start += take
    return [p for p in out if p]


def polish_translated_segments(segments: List[Segment],
                               max_chars: int = 125,
                               min_chars: int = 24,
                               min_gap: float = 0.04) -> List[Segment]:
    """Reflow translated subtitles by meaning after ellipsis continuations."""
    groups: List[List[Segment]] = []
    cur: List[Segment] = []
    max_group_duration = 12.0
    max_group_chars = max(int(max_chars or 125) * 3, int(max_chars or 125) + int(min_chars or 24))
    for seg in segments:
        txt = (seg.text or "").strip()
        if not cur:
            cur = [seg]
            continue
        prev_txt = cur[-1].text or ""
        joined_chars = sum(len(s.text or "") for s in cur) + len(txt)
        joined_duration = max(seg.end, cur[-1].end) - min(s.start for s in cur)
        ellipsis_join = _ends_with_ellipsis(prev_txt) or _starts_with_ellipsis(txt)
        if ellipsis_join and joined_chars <= max_group_chars and joined_duration <= max_group_duration:
            cur.append(seg)
        else:
            groups.append(cur)
            cur = [seg]
    if cur:
        groups.append(cur)

    out: List[Segment] = []
    for group in groups:
        if len(group) == 1:
            text = group[0].text or ""
            start, end = group[0].start, group[0].end
            template = group[0]
        else:
            text = ""
            for seg in group:
                text = _join_continuation_text(text, seg.text)
            start = min(s.start for s in group)
            end = max(s.end for s in group)
            template = group[0]
        parts = split_vi_text_naturally(text, max_chars, min_chars)
        duration = max(0.01, end - start)
        if duration > max_group_duration:
            target_parts = int(math.ceil(duration / max_group_duration))
            while len(parts) < target_parts:
                longest_idx, longest = max(enumerate(parts), key=lambda x: len(x[1]))
                if len(longest) < max(min_chars * 2, 48):
                    break
                split_count = min(target_parts - len(parts) + 1, 3)
                pieces = _split_balanced_words(longest, split_count)
                if len(pieces) <= 1:
                    break
                parts = parts[:longest_idx] + pieces + parts[longest_idx + 1:]

        if len(parts) <= 1:
            child = replace(template)
            child.start, child.end = start, end
            child.text = normalize_vi_subtitle_text(parts[0] if parts else text)
            child.audio_path = None
            child.placed_start = None
            child.voice_duration = None
            child.speed = 1.0
            out.append(child)
            continue

        weights = [max(1, len(re.sub(r"\s+", "", p))) for p in parts]
        total = float(sum(weights)) or 1.0
        duration = max(0.01, end - start)

        # Ranh giới lấy theo BẢN ĐỒ THOẠI khi có: mỗi dòng con bắt đầu đúng lúc
        # ký tự tương ứng của bản gốc được nói ra. Đây là chỗ sinh ra phần lớn
        # độ lệch "voice trượt khỏi hình" trước đây, vì một ô 12-15 giây gom từ
        # nhiều mảnh ASR bị chia đều theo số ký tự, bất kể bên trong có 2-3 giây
        # im lặng.
        bounds = speechmap.slice_window(start, end, weights)
        if bounds:
            for (a, b), part in zip(bounds, parts):
                child = replace(template)
                child.start = a
                child.end = max(a + 0.05, b)
                child.text = normalize_vi_subtitle_text(part)
                child.audio_path = None
                child.placed_start = None
                child.voice_duration = None
                child.speed = 1.0
                out.append(child)
            continue

        # Gaps are part of the fixed source window, not extra time.  The old
        # code allocated 100% of the window to speech and then inserted gaps,
        # collapsing the last child to 10 ms and cascading later timestamps.
        gap = min(
            max(0.0, min_gap),
            duration / (2.0 * max(1, len(parts) - 1)),
        )
        speech_duration = max(0.0, duration - gap * (len(parts) - 1))
        floor = min(0.12, speech_duration / (2.0 * len(parts)))
        weighted_duration = max(0.0, speech_duration - floor * len(parts))
        allocations = [
            floor + weighted_duration * (weight / total)
            for weight in weights
        ]
        cursor = start
        for idx, (part, allocation) in enumerate(zip(parts, allocations)):
            child = replace(template)
            child.start = cursor
            if idx == len(parts) - 1:
                child.end = end
            else:
                child.end = min(end, cursor + allocation)
            child.text = normalize_vi_subtitle_text(part)
            child.audio_path = None
            child.placed_start = None
            child.voice_duration = None
            child.speed = 1.0
            out.append(child)
            cursor = min(end, child.end + gap)
    for i, seg in enumerate(out, 1):
        # Each independent group remains anchored to its original source
        # window.  Globally pushing it behind the previous group makes a tiny
        # overlap accumulate into seconds/minutes of drift over a long video.
        seg.index = i
    return out


def reanchor_translated_segments(vi_segments: List[Segment],
                                 src_segments: List[Segment]) -> int:
    """Đặt lại MỐC THỜI GIAN cho bản dịch cũ theo bản đồ thoại (không đổi chữ).

    Dùng khi tái sử dụng file .vi.srt đã chia sẵn từ những lần chạy trước: chữ
    thì tốt (có thể đã sửa tay) nhưng mốc thời gian được sinh ra bằng cách chia
    theo tỉ lệ ký tự nên đang lệch. Hàm gom các dòng Việt về đúng ô câu gốc rồi
    chia lại ô đó theo mốc ký tự thật.

    Trả về số dòng đã đổi mốc (0 = không có bản đồ, hoặc không có gì để sửa).
    """
    if not vi_segments or not src_segments or speechmap.get_active() is None:
        return 0
    doi = 0
    vi_sorted = sorted(vi_segments, key=lambda s: s.start)
    j = 0
    for i, src in enumerate(src_segments):
        win_end = src_segments[i + 1].start if i + 1 < len(src_segments) else src.end
        win_end = max(win_end, src.end)
        con: List[Segment] = []
        while j < len(vi_sorted) and vi_sorted[j].start < src.start - 0.05:
            j += 1
        k = j
        while k < len(vi_sorted) and vi_sorted[k].start < win_end:
            con.append(vi_sorted[k])
            k += 1
        if len(con) < 2:
            j = k
            continue
        weights = [max(1, _compact_len(c.text)) for c in con]
        bounds = speechmap.slice_window(src.start, max(src.end, con[-1].end),
                                       weights)
        if bounds:
            for child, (a, b) in zip(con, bounds):
                if abs(child.start - a) > 0.02 or abs(child.end - b) > 0.02:
                    doi += 1
                child.start, child.end = a, max(a + 0.05, b)
                child.placed_start = None
                child.voice_duration = None
                child.speed = 1.0
        j = k
    return doi


_TIME_RE = re.compile(
    r"(?P<h>\d+):(?P<m>\d{1,2}):(?P<s>\d{1,2})[,.](?P<ms>\d{1,3})"
)


def timestamp_to_seconds(ts: str) -> float:
    m = _TIME_RE.search(ts.strip())
    if not m:
        raise ValueError(f"Timestamp không hợp lệ: {ts!r}")
    h = int(m.group("h"))
    mi = int(m.group("m"))
    s = int(m.group("s"))
    ms = int(m.group("ms").ljust(3, "0"))
    return h * 3600 + mi * 60 + s + ms / 1000.0


def seconds_to_timestamp(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    ms_total = int(round(sec * 1000))
    h, rem = divmod(ms_total, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(content: str) -> List[Segment]:
    """Đọc chuỗi SRT thành danh sách Segment. Bỏ qua block hỏng thay vì crash."""
    content = content.replace("\r\n", "\n").replace("\r", "\n").strip("﻿\n ")
    if not content:
        return []
    blocks = re.split(r"\n\s*\n", content)
    segments: List[Segment] = []
    auto_idx = 0
    for block in blocks:
        lines = [l for l in block.split("\n") if l.strip() != ""]
        if not lines:
            continue
        # Dòng chỉ số (tùy chọn)
        idx_line = 0
        idx = None
        if re.fullmatch(r"\d+", lines[0].strip()):
            idx = int(lines[0].strip())
            idx_line = 1
        if idx_line >= len(lines) or "-->" not in lines[idx_line]:
            continue  # block hỏng
        try:
            start_str, end_str = lines[idx_line].split("-->")
            start = timestamp_to_seconds(start_str)
            end = timestamp_to_seconds(end_str)
        except ValueError:
            continue
        text = " ".join(lines[idx_line + 1:]).strip()
        auto_idx += 1
        segments.append(
            Segment(index=idx if idx is not None else auto_idx,
                    start=start, end=end, text=text)
        )
    return segments


def format_srt(segments: List[Segment], use_placed: bool = False) -> str:
    """Xuất danh sách Segment ra chuỗi SRT.

    use_placed=True: ghi theo thời gian đã đặt lại (placed_start + duration thực).
    """
    out = []
    for i, seg in enumerate(segments, start=1):
        if use_placed and seg.placed_start is not None:
            start = seg.placed_start
            try:
                voice_duration = float(seg.voice_duration or 0.0)
            except (TypeError, ValueError):
                voice_duration = 0.0
            end = seg.placed_start + (
                voice_duration if voice_duration > 0.01 else seg.duration)
        else:
            start, end = seg.start, seg.end
        out.append(str(i))
        out.append(f"{seconds_to_timestamp(start)} --> {seconds_to_timestamp(end)}")
        out.append(seg.text)
        out.append("")
    return "\n".join(out).strip() + "\n"


def load_srt_file(path: str) -> List[Segment]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return parse_srt(f.read())


def save_srt_file(path: str, segments: List[Segment], use_placed: bool = False) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(format_srt(segments, use_placed=use_placed))
