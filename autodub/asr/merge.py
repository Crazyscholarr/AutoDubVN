"""Gộp/tách phụ đề, độ phủ, và sửa lỗi nghe nhầm lặp lại."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from .. import speechmap
from ..srt_utils import Segment
from ..utils import log
from .common import (
    _CJK_TERMINAL,
    _clean,
    _is_cjk,
    _reindex,
    caption_options,
    caption_style_is_screen,
)


def split_long(text: str, start: float, end: float, max_chars: int) -> List[Segment]:
    """Tách đoạn dài theo dấu câu.

    Mốc thời gian lấy từ BẢN ĐỒ THOẠI (mốc từng ký tự ASR nghe được) nếu có;
    chỉ khi không có mới chia theo tỉ lệ số ký tự như trước.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [Segment(0, start, end, text)]
    parts = [p for p in re.split(r"(?<=[.!?\u3002\uff01\uff1f,\uff0c\u3001\uff1b;])\s*", text) if p.strip()]
    if len(parts) <= 1:
        return [Segment(0, start, end, text)]
    # Gộp các mảnh quá ngắn lại cho tự nhiên
    merged: List[str] = []
    for p in parts:
        if merged and len(merged[-1]) + len(p) <= max_chars:
            merged[-1] += " " + p if not _is_cjk(p) else p
        else:
            merged.append(p)
    weights = [max(1, len(p.strip())) for p in merged]
    bounds = speechmap.slice_window(start, end, weights)
    if bounds:
        return [Segment(0, a, b, p.strip()) for (a, b), p in zip(bounds, merged)]
    total = sum(weights) or 1
    segs, t, dur = [], start, max(0.01, end - start)
    for p, w in zip(merged, weights):
        d = dur * (w / total)
        segs.append(Segment(0, t, t + d, p.strip()))
        t += d
    return segs


def pack_screen_cues(segs: List[Segment],
                     max_chars: Optional[int] = None,
                     min_chars: Optional[int] = None,
                     max_duration: Optional[float] = None,
                     hard_max_chars: Optional[int] = None,
                     hard_max_duration: Optional[float] = None,
                     gap: Optional[float] = None, *,
                     speech_map=None, review=None, protected_words=()) -> List[Segment]:
    """Public screen API; word packing and diagnostics live in screen_pack."""
    from .screen_pack import pack
    return pack(segs, max_chars=max_chars, min_chars=min_chars,
                max_duration=max_duration, hard_max_chars=hard_max_chars,
                hard_max_duration=hard_max_duration, gap=gap,
                speech_map=speech_map, review=review, protected_words=protected_words)


def merge_cjk_sentence_fragments(segs: List[Segment],
                                 max_chars: int = 34,
                                 max_gap: float = 0.85) -> List[Segment]:
    """Gom các mảnh tiếng Trung/Nhật/Hàn bị ASR cắt vụn giữa cụm.

    FunASR đôi khi trả timestamp kiểu karaoke, ví dụ một câu bị tách thành
    "周" rồi "末，老手...". Nếu để nguyên, Gemini dịch từng dòng riêng và tên
    người/cụm nghĩa bị xé sang hai subtitle. Ta gom liên tiếp tới khi gặp dấu
    kết câu mạnh; dấu phẩy/đốn hiệu chỉ là chỗ nghỉ, chưa phải hết ý.
    """
    merged: List[Segment] = []
    buf: Optional[Segment] = None

    def flush():
        nonlocal buf
        if buf is not None:
            merged.append(buf)
            buf = None

    for s in sorted(segs, key=lambda x: (x.start, x.end)):
        txt = (s.text or "").strip()
        if not txt:
            continue
        if not _is_cjk(txt):
            flush()
            merged.append(s)
            continue
        if buf is None:
            buf = Segment(s.index, s.start, s.end, txt, speaker=s.speaker)
            continue

        gap = max(0.0, s.start - buf.end)
        joined = buf.text + txt
        prev_done = buf.text[-1] in _CJK_TERMINAL
        too_long = len(joined) > max(max_chars, 1) * 2
        if prev_done or gap > max_gap or too_long:
            flush()
            buf = Segment(s.index, s.start, s.end, txt, speaker=s.speaker)
        else:
            buf.text = joined
            buf.end = max(buf.end, s.end)
            if not buf.speaker:
                buf.speaker = s.speaker

    flush()
    return _reindex(merged)


def is_speakable(text: str) -> bool:
    """Có gì để ĐỌC không? Dòng chỉ còn dấu câu ("." , "?" , "…") là rác:
    ASR sinh ra ở đoạn im lặng, TTS không đọc được (edge-tts trả "No audio was
    received"), mà giữ lại thì tốn thêm 5 lần thử lại rồi câm tiếng."""
    return any(c.isalnum() for c in (text or ""))


def normalize_segments(segs: List[Segment], max_chars: int) -> List[Segment]:
    """Làm sạch, tách dòng dài, sắp theo thời gian, đánh lại số thứ tự."""
    out: List[Segment] = []
    for s in segs:
        txt = _clean(s.text)
        if not is_speakable(txt):
            continue
        st, en = float(s.start), float(s.end)
        if en <= st:
            en = st + max(0.4, len(txt) * 0.06)
        if caption_style_is_screen() and _is_cjk(txt):
            # Packer tự cắt theo mốc ký tự; split_long trước sẽ xé cửa sổ
            # thời gian rồi lệch bản đồ thoại.
            out.append(replace(s, index=0, start=st, end=en, text=txt))
        else:
            for sub in split_long(txt, st, en, max_chars):
                sub.speaker = s.speaker
                out.append(sub)
    out.sort(key=lambda x: (x.start, x.end))
    if caption_style_is_screen():
        # CapCut không đợi hết câu。 — gộp merge_cjk sẽ phá nhịp màn hình.
        out = pack_screen_cues(out)
    else:
        out = merge_cjk_sentence_fragments(out, max_chars=max_chars)
    return _reindex(out)


def find_gaps(segs: List[Segment], total_duration: float,
              min_gap: float = 25.0, edge_pad: float = 0.3) -> List[Tuple[float, float]]:
    """Tìm các khoảng thời gian KHÔNG có phụ đề, dài hơn min_gap giây.

    Đây là công cụ phát hiện 'mất đoạn giữa'. Trả về list (start, end).
    """
    if total_duration <= 0:
        return []
    gaps: List[Tuple[float, float]] = []
    cursor = 0.0
    for s in sorted(segs, key=lambda x: x.start):
        if s.start - cursor >= min_gap:
            gaps.append((max(0.0, cursor - edge_pad), s.start + edge_pad))
        cursor = max(cursor, s.end)
    if total_duration - cursor >= min_gap:
        gaps.append((max(0.0, cursor - edge_pad), total_duration))
    return gaps


def merge_time_ranges(ranges: List[Tuple[float, float]],
                      merge_gap: float = 0.4) -> List[Tuple[float, float]]:
    """Merge overlapping or near-adjacent time ranges."""
    out: List[Tuple[float, float]] = []
    for start, end in sorted((float(s), float(e)) for s, e in ranges if e > s):
        if not out or start > out[-1][1] + merge_gap:
            out.append((start, end))
        else:
            out[-1] = (out[-1][0], max(out[-1][1], end))
    return out


def parse_silencedetect_intervals(stderr: str, duration: float) -> List[Tuple[float, float]]:
    """Convert ffmpeg silencedetect logs to non-silent audio intervals."""
    if duration <= 0:
        return []
    events: List[Tuple[float, float]] = []
    for m in re.finditer(r"silence_(start|end):\s*([0-9.]+)", stderr or ""):
        events.append((float(m.group(2)), 1.0 if m.group(1) == "start" else 0.0))
    events.sort(key=lambda x: (x[0], x[1]))

    intervals: List[Tuple[float, float]] = []
    cursor = 0.0
    in_silence = False
    for t, kind in events:
        t = max(0.0, min(duration, t))
        if kind == 1.0 and not in_silence:
            if t > cursor:
                intervals.append((cursor, t))
            in_silence = True
        elif kind == 0.0 and in_silence:
            cursor = t
            in_silence = False
    if not in_silence and cursor < duration:
        intervals.append((cursor, duration))
    return merge_time_ranges(intervals, merge_gap=0.15)


def find_uncovered_speech_ranges(segs: List[Segment],
                                 speech_ranges: List[Tuple[float, float]],
                                 total_duration: float,
                                 min_gap: float = 1.2,
                                 edge_pad: float = 0.35,
                                 subtitle_pad: float = 0.2) -> List[Tuple[float, float]]:
    """Find non-silent audio ranges that have no subtitle coverage."""
    if total_duration <= 0:
        return []
    covered = merge_time_ranges([
        (max(0.0, s.start - subtitle_pad), min(total_duration, s.end + subtitle_pad))
        for s in segs
    ], merge_gap=0)

    # Use the same VAD union as speech_coverage_report before applying the
    # significance threshold; adjacent VAD fragments are one logical gap.
    speech_ranges = merge_time_ranges([(max(0,a), min(total_duration,b))
                                      for a,b in speech_ranges], merge_gap=0)

    holes: List[Tuple[float, float]] = []
    ci = 0
    for rs, re_ in speech_ranges:
        rs, re_ = max(0.0, rs), min(total_duration, re_)
        cursor = rs
        while ci < len(covered) and covered[ci][1] <= rs:
            ci += 1
        j = ci
        while j < len(covered) and covered[j][0] < re_:
            cs, ce = covered[j]
            if cs > cursor and cs - cursor >= min_gap:
                holes.append((max(0.0, cursor - edge_pad), min(total_duration, cs + edge_pad)))
            cursor = max(cursor, ce)
            if cursor >= re_:
                break
            j += 1
        if re_ - cursor >= min_gap:
            holes.append((max(0.0, cursor - edge_pad), min(total_duration, re_ + edge_pad)))
    # Positive merge padding may bridge a short existing subtitle. Context is
    # applied by the recognizer, not by widening the missing-speech target.
    return merge_time_ranges(holes, merge_gap=0)


_PARTICLE_CHARS = set("呀啊呢吧吗嘛啦咯哇哦嗯呃")


def clip_to_uncovered(start, end, gap_start, gap_end, occupied, min_dur=0.05):
    """Clip a fill to the gap, then shrink away from cues already covering it."""
    x = max(float(gap_start), float(start))
    y = min(float(gap_end), float(end))
    if y - x < min_dur:
        return None
    for os_, oe in occupied:
        os_, oe = float(os_), float(oe)
        if oe <= x or os_ >= y:
            continue
        left, right = (x, min(y, os_)), (max(x, oe), y)
        left_ok = left[1] - left[0] >= min_dur
        right_ok = right[1] - right[0] >= min_dur
        if left_ok and right_ok:
            x, y = max((left, right), key=lambda part: part[1] - part[0])
        elif left_ok:
            x, y = left
        elif right_ok:
            x, y = right
        else:
            return None
    return (x, y) if y - x >= min_dur else None


def _lexical_chars(text):
    return [c for c in (text or "") if c.isalnum()]


def _ends_with_terminal(text):
    stripped = (text or "").strip()
    return bool(stripped) and stripped[-1] in _CJK_TERMINAL


def _is_particle_cue(text):
    chars = _lexical_chars(text)
    return bool(chars) and all(c in _PARTICLE_CHARS for c in chars)


def _alnum_key(text):
    return "".join(c.casefold() if c.isascii() else c for c in (text or "") if c.isalnum())


def _fuzzy_find(haystack: str, needle: str) -> int:
    """Index of needle (or a long prefix) in haystack; one CJK substitution allowed."""
    if not needle or not haystack:
        return -1
    found = haystack.find(needle)
    if found >= 0:
        return found
    for size in range(len(needle), 1, -1):
        piece = needle[:size]
        found = haystack.find(piece)
        if found >= 0:
            return found
        if size < 3:
            continue
        for i in range(0, len(haystack) - size + 1):
            window = haystack[i:i + size]
            if sum(a != b for a, b in zip(window, piece)) <= 1:
                return i
    return -1


def _left_in_crop(crop: str, left: str) -> bool:
    if not crop or not left:
        return False
    for size in range(len(left), 1, -1):
        if _fuzzy_find(crop, left[-size:]) >= 0:
            return True
    return False


def _right_in_crop(crop: str, right: str) -> bool:
    return bool(crop and right and _fuzzy_find(crop, right) >= 0)


def _usable_crop_fill(text: str) -> bool:
    if not is_speakable(text) or not _is_cjk(text):
        return False
    cjk = [c for c in text if "\u4e00" <= c <= "\u9fff"]
    latin = [c for c in text if c.isascii() and c.isalpha()]
    if latin and len(latin) >= len(cjk):
        return False
    return 1 <= len(_alnum_key(text)) <= 8


def apply_crop_to_hole(segs: List[Segment], gs: float, ge: float, crop_text: str,
                       slack: float = 0.12) -> Optional[List[Segment]]:
    """Cover a VAD hole with existing neighbor text when the crop is that cue.

    FunASR often stamps the next sentence after a 1–3 s hole. Re-recognizing
    the hole returns the neighbor (麦里有灵力 / 麦粒有灵力). Extending that
    cue uses no new words. Crops that match neither neighbor are refused —
    inserting 哇/手有 without speech marks becomes missing_speech_marks.
    """
    gs, ge = float(gs), float(ge)
    if ge - gs < 0.05 or not (crop_text or "").strip():
        return None
    current = list(segs)
    left = max((s for s in current if s.end <= gs + slack),
               key=lambda s: s.end, default=None)
    right = min((s for s in current if s.start >= ge - slack),
                key=lambda s: s.start, default=None)
    crop_k = _alnum_key(crop_text)
    left_k = _alnum_key(left.text) if left else ""
    right_k = _alnum_key(right.text) if right else ""
    right_hit = _right_in_crop(crop_k, right_k)
    left_hit = _left_in_crop(crop_k, left_k)
    floor = left.end if left is not None else 0.0

    if (right is not None and right_hit and abs(right.start - ge) <= slack
            and gs >= floor - 1e-6 and right.start - gs >= 0.05):
        log(f"Kéo câu sau vào lỗ ASR {gs:.3f}-{ge:.3f}s: {right.text}", "info")
        return _reindex([replace(right, start=gs) if s is right else s for s in current])

    ceiling = right.start if right is not None else ge
    if (left is not None and left_hit and not right_hit
            and abs(left.end - gs) <= slack
            and ge <= ceiling + 1e-6 and ge - left.end >= 0.05):
        log(f"Kéo câu trước vào lỗ ASR {gs:.3f}-{ge:.3f}s: {left.text}", "info")
        return _reindex([replace(left, end=ge) if s is left else s for s in current])

    return None


def stitch_split_utterances(segs: List[Segment],
                            speech_ranges: List[Tuple[float, float]],
                            duration: float,
                            min_gap: float = 1.2,
                            max_gap: float = 3.5) -> List[Segment]:
    """Cover a VAD hole by joining two source cues that are one utterance.

    FunASR often splits mid-phrase ("来人把肉带" / "上光虎。") or parks a
    particle ("多少年" / "呀。") on the far side of a 1–3 s hole. Extending
    those existing clocks uses no new text. Sentence-final 。！？ stays a hole
    unless the right cue is only a particle.
    """
    if not segs or not speech_ranges or duration <= 0:
        return segs
    current = sorted(segs, key=lambda s: (s.start, s.end))
    changed = True
    while changed:
        changed = False
        holes = find_uncovered_speech_ranges(
            current, speech_ranges, duration,
            min_gap=min_gap, edge_pad=0, subtitle_pad=0)
        for gs, ge in holes:
            if ge - gs > max_gap + 1e-6:
                continue
            left = max((s for s in current if s.end <= gs + 0.05),
                       key=lambda s: s.end, default=None)
            right = min((s for s in current if s.start >= ge - 0.05),
                        key=lambda s: s.start, default=None)
            if left is None or right is None or left is right:
                continue
            if right.start - left.end > (ge - gs) + 2.0 + 1e-6:
                continue
            if any(left.end < s.start < right.start or left.end < s.end < right.start
                   for s in current if s is not left and s is not right):
                continue
            if not _is_cjk(left.text) or not _is_cjk(right.text):
                continue
            if _ends_with_terminal(left.text) and not _is_particle_cue(right.text):
                continue
            joined = (left.text or "").rstrip() + (right.text or "").lstrip()
            merged = replace(left, end=right.end, text=joined)
            current = [merged if s is left else s for s in current if s is not right]
            log(f"Ghép mảnh câu ASR {gs:.3f}-{ge:.3f}s: {left.text} + {right.text}", "info")
            changed = True
            break
    return paint_short_speech_holes(_reindex(current), speech_ranges, duration,
                                    max_hole=min_gap)


_MEGA_CUE_S = 30.0
_PAINT_MIN_HOLE = 0.04
_PAINT_ABUT_S = 0.05


def _speech_overlap(speech: List[Tuple[float, float]], gs: float, ge: float) -> float:
    painted = 0.0
    for rs, re in speech:
        lo, hi = max(rs, gs), min(re, ge)
        if hi > lo:
            painted += hi - lo
    return painted


def paint_short_speech_holes(segs: List[Segment],
                             speech_ranges: List[Tuple[float, float]],
                             duration: float,
                             max_hole: float = 1.2) -> List[Segment]:
    """Paint subthreshold uncovered VAD with neighboring clocks. No new text.

    FunASR sentence clocks often leave 0.05–0.85s of speech between cues, and
    40–200ms of trailing/leading VAD on an otherwise recognized sentence.
    Those holes never enter ≥1.2s repair, so honest coverage stalls ~86% on
    dense 快漫 even when every sentence was recognized. Splitting a short gap
    or snapping a cue onto abutting VAD is subtitle packing, not a mega-cue.
    """
    if not segs or not speech_ranges or duration <= 0:
        return segs
    speech = merge_time_ranges(
        [(max(0.0, a), min(duration, b)) for a, b in speech_ranges], merge_gap=0)
    out = sorted(segs, key=lambda s: (s.start, s.end))
    for i in range(len(out) - 1):
        left, right = out[i], out[i + 1]
        gs, ge = left.end, right.start
        hole = ge - gs
        if hole <= _PAINT_MIN_HOLE or hole > float(max_hole) + 1e-6:
            continue
        if _speech_overlap(speech, gs, ge) < _PAINT_MIN_HOLE:
            continue
        mid = gs + hole / 2.0
        if (mid - left.start) > _MEGA_CUE_S or (right.end - mid) > _MEGA_CUE_S:
            continue
        out[i] = replace(left, end=mid)
        out[i + 1] = replace(right, start=mid)

    changed = True
    guard = 0
    while changed and guard < 4000:
        changed = False
        guard += 1
        holes = find_uncovered_speech_ranges(
            out, speech, duration, min_gap=0, edge_pad=0, subtitle_pad=0)
        for gs, ge in holes:
            hole = ge - gs
            if hole < _PAINT_MIN_HOLE or hole > float(max_hole) + 1e-6:
                continue
            left = max((s for s in out if s.end <= gs + _PAINT_ABUT_S),
                       key=lambda s: s.end, default=None)
            right = min((s for s in out if s.start >= ge - _PAINT_ABUT_S),
                        key=lambda s: s.start, default=None)
            left_ok = (left is not None
                       and gs - left.end <= _PAINT_ABUT_S
                       and (ge - left.start) <= _MEGA_CUE_S)
            right_ok = (right is not None
                        and right.start - ge <= _PAINT_ABUT_S
                        and (right.end - gs) <= _MEGA_CUE_S)
            if left_ok and (not right_ok or (gs - left.end) <= (right.start - ge)):
                ceiling = right.start if right is not None else duration
                new_end = min(ge, ceiling)
                if (new_end - left.end >= _PAINT_MIN_HOLE
                        and (new_end - left.start) <= _MEGA_CUE_S):
                    out = [replace(left, end=new_end) if s is left else s for s in out]
                    changed = True
                    break
            if right_ok:
                floor = left.end if left is not None else 0.0
                new_start = max(gs, floor)
                if (right.start - new_start >= _PAINT_MIN_HOLE
                        and (right.end - new_start) <= _MEGA_CUE_S):
                    out = [replace(right, start=new_start) if s is right else s
                           for s in out]
                    changed = True
                    break
    return _reindex(out)


def coverage_report(segs: List[Segment], total_duration: float) -> dict:
    """Tính độ phủ: tổng thời lượng có lời / độ dài audio."""
    if total_duration <= 0:
        return {"ok": False, "covered_s": 0.0, "ratio": 0.0, "last_end_s": 0.0,
                "duration_s": 0.0, "lines": len(segs)}
    # Gộp các đoạn chồng nhau trước khi cộng
    covered = 0.0
    cur_s = cur_e = None
    for s in sorted(segs, key=lambda x: x.start):
        if cur_e is None:
            cur_s, cur_e = s.start, s.end
        elif s.start <= cur_e:
            cur_e = max(cur_e, s.end)
        else:
            covered += cur_e - cur_s
            cur_s, cur_e = s.start, s.end
    if cur_e is not None:
        covered += cur_e - cur_s
    last_end = max((s.end for s in segs), default=0.0)
    return {
        "lines": len(segs),
        "duration_s": round(total_duration, 1),
        "covered_s": round(covered, 1),
        "ratio": round(covered / total_duration, 4),
        "last_end_s": round(last_end, 1),
        "tail_ratio": round(last_end / total_duration, 4),
    }


def speech_coverage_report(segs, speech_ranges, duration):
    """Measure intersection with VAD, never subtitle duration/media duration."""
    ranges = merge_time_ranges([(max(0, a), min(duration, b))
                                for a, b in speech_ranges], merge_gap=0)
    covered = merge_time_ranges([(max(0, s.start), min(duration, s.end))
                                 for s in segs], merge_gap=0)
    total = sum(b-a for a,b in ranges)
    matched = sum(max(0, min(b,d)-max(a,c)) for a,b in ranges for c,d in covered)
    return dict(media_duration_s=duration, speech_duration_s=total,
                subtitle_covered_speech_s=matched, unresolved_speech_s=max(0,total-matched),
                speech_coverage_percent=100*matched/total if total else 100.0)


def merge_new_segments(base: List[Segment], extra: List[Segment],
                       tolerance: float = 0.4) -> List[Segment]:
    """Ghép phụ đề vá thêm vào bộ gốc, bỏ trùng lặp gần nhau.

    Gom theo nội dung đã rửa rồi so start trong cùng nhóm — O(n) thay vì
    quét toàn bộ gốc cho mỗi câu vá (video dài, rescue nhiều vòng).
    """
    out = list(base)
    buckets: Dict[str, List[float]] = {}
    for b in out:
        buckets.setdefault(_clean(b.text), []).append(b.start)
    for e in extra:
        key = _clean(e.text)
        dup = any(abs(start - e.start) < tolerance for start in buckets.get(key, ()))
        if not dup:
            out.append(e)
            buckets.setdefault(key, []).append(e.start)
    out.sort(key=lambda x: (x.start, x.end))
    return _reindex(out)


def apply_corrections(segs: List[Segment], rules) -> int:
    """Thay thế theo bảng `asr.corrections` trong config.yaml.

    ASR nghe sai DANH XƯNG/TÊN RIÊNG theo kiểu lặp lại y hệt suốt cả bộ phim
    ("tiểu soái ca" -> "tiểu xoái ta"). Khai một lần, cả 44 tập đều đúng.

    Dạng khai (đều được):
        corrections:
          "tiểu xoái ta": "tiểu soái ca"        # thay chuỗi, KHÔNG phân biệt hoa/thường
          "re:\\bxoái\\b": "soái"                # bắt đầu bằng 're:' -> biểu thức chính quy
    """
    if not rules:
        return 0
    pairs = list(rules.items()) if isinstance(rules, dict) else [
        (k, v) for item in rules for k, v in
        (item.items() if isinstance(item, dict) else [(item[0], item[1])])]
    def _keep_case(right: str):
        """Giữ chữ hoa đầu câu: 'Tiểu Xoái Ta' -> 'Tiểu soái ca', không thành
        'tiểu soái ca' làm hỏng chữ hoa đầu dòng."""
        def _f(m):
            return (right[:1].upper() + right[1:]) if m.group(0)[:1].isupper() else right
        return _f

    compiled = []
    for wrong, right in pairs:
        wrong, right = str(wrong), str(right)
        try:
            if wrong.startswith("re:"):
                # luật regex: dùng thẳng chuỗi thay thế để \1, \2... vẫn chạy
                compiled.append((re.compile(wrong[3:], re.I), right))
            else:
                compiled.append((re.compile(re.escape(wrong), re.I), _keep_case(right)))
        except re.error as e:
            log(f"Bỏ qua luật sửa lỗi sai cú pháp {wrong!r}: {e}", "warn")
    changed = 0
    for s in segs:
        before = s.text
        for rx, right in compiled:
            s.text = rx.sub(right, s.text)
        if s.text != before:
            changed += 1
    return changed


def build_initial_prompt(rules, extra: Optional[str] = None) -> Optional[str]:
    """Mớm sẵn cho Whisper các từ ĐÚNG (vế phải của bảng sửa lỗi) + gợi ý riêng."""
    words = []
    if isinstance(rules, dict):
        words = [str(v) for v in rules.values() if not str(v).startswith("re:")]
    parts = [w for w in words if w.strip()]
    if extra:
        parts.append(str(extra).strip())
    if not parts:
        return None
    return ". ".join(dict.fromkeys(parts))[:900]      # Whisper giới hạn 224 token
