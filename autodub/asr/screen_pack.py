"""Word-boundary display packing. Speech clocks and review evidence stay separate."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
import re
import threading

from .. import speechmap
from ..srt_utils import Segment, repair_asr_punctuation
from .common import _clean, _is_cjk, caption_options

HARD = frozenset("。！？.!?…")
SOFT = frozenset("，,、；;：:")
PARTICLES = frozenset("吗呢吧啊呀啦了的着么")
PROTECTED = frozenset(
    "活下去 哥们 来一根 同学们 设墙 戒严令 松江 伪人 伟人 好运".split()
)
_FALLBACK = PROTECTED | frozenset(
    "据说 这个 世界 掺杂 他们 不再 只是 伪装 成长 进化 寻找 那些 完美 容器 请找出 或者 你们 同学 亲人 甚至 自己 谢谢 抽烟 车祸 为什么 大概 因为 刚刚 颁布 最近 条子 这里 听到 这话 忽然 想起 北边 出现 大量 骚乱 新闻 不少 已经 南边 网络 流传 省界 封锁 眼看 车流 松动 男人 用力 一口 回到 发动机 启动 然而 老师 讥讽 嘲笑 其他 觉醒 远程 天赋 暂定 大家 现在 然后 一起 往前 只有 没有 不错".split()
)
_ATOMIC = re.compile(
    r"(?:暂定|淡定)\s*为\s*[A-Za-zＡ-Ｚａ-ｚ]\s*级|"
    r"(?:[+-]?\d+(?:[.,]\d+)*|[一二三四五六七八九十百千万两]+)\s*"
    r"(?:公里|千米|厘米|毫米|公斤|小时|分钟|秒钟|万元|米|秒|天|年|岁|元|个|根|级|度)|"
    r"[A-Za-z]+(?:[-'][A-Za-z]+)*\d*"
)
_LOCAL = threading.local()


@lru_cache(maxsize=1)
def _jieba():
    try:
        import jieba
    except ImportError:
        return None
    jieba.setLogLevel(30)
    tokenizer = jieba.Tokenizer()
    return tokenizer


def _lexicon_freq(lexer):
    if lexer is None:
        return None
    if not getattr(lexer, "initialized", True):
        lexer.initialize()
    return lexer.FREQ


def _supported_short_fragment(lexical, lexer=None):
    """Keep aligned 1-2 character speech. Drop only glyphs the lexicon rejects."""
    if not lexical:
        return False
    if lexical in _FALLBACK:
        return True
    if lexical[:1] in "我你他她它是否有无不没对好走来去":
        return True
    if all(c in "嗯啊嘿喂哦" for c in lexical):
        return True
    if any(c.isdigit() for c in lexical):
        return True
    freq = _lexicon_freq(lexer if lexer is not None else _jieba())
    if freq is not None:
        if freq.get(lexical, 0) > 0:
            return True
        return all(
            (c.isascii() and c.isalnum()) or freq.get(c, 0) > 0 for c in lexical
        )
    return all("\u3400" <= c <= "\u9fff" or (c.isascii() and c.isalnum()) for c in lexical)


def word_spans(text, protected_words=(), use_jieba=True):
    """Return lossless character spans; explicit phrases override segmentation."""
    words = sorted(PROTECTED | frozenset(protected_words), key=lambda w: (-len(w), w))
    pattern = re.compile("|".join(re.escape(w) for w in words if w))
    lexer = _jieba() if use_jieba else None
    spans, i = [], 0
    while i < len(text):
        match = pattern.match(text, i) or _ATOMIC.match(text, i)
        if match:
            spans.append((i, match.end()))
            i = match.end()
            continue
        if not "\u3400" <= text[i] <= "\u9fff":
            spans.append((i, i + 1))
            i += 1
            continue
        end = i + 1
        while end < len(text) and "\u3400" <= text[end] <= "\u9fff":
            if pattern.match(text, end) or _ATOMIC.match(text, end):
                break
            end += 1
        if lexer is not None:
            pos = i
            for word in lexer.cut(text[i:end], HMM=False):
                spans.append((pos, pos + len(word)))
                pos += len(word)
        else:
            while i < end:
                size = next(
                    (
                        n
                        for n in range(min(4, end - i), 1, -1)
                        if text[i : i + n] in _FALLBACK
                    ),
                    1,
                )
                spans.append((i, i + size))
                i += size
        i = end
    return spans


def last_review():
    """Thread-local diagnostics for CLI/GUI, copied so callers cannot mutate them."""
    return [dict(row) for row in getattr(_LOCAL, "review", [])]


class CaptionReviewRequired(ValueError):
    """Packing cannot establish complete display text with trustworthy clocks."""
    status = 'REVIEW_REQUIRED'


def compact_review_gaps(rows, limit=80):
    """Keep only start/end/reason so the GUI can seek without huge diagnostics."""
    import math
    gaps = []
    for row in rows or []:
        try:
            start = float(row.get("start") or 0)
            end = float(row.get("end") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            continue
        gaps.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "reason": str(row.get("reason") or "unknown"),
        })
        if len(gaps) >= max(limit * 4, limit):
            break
    gaps.sort(key=lambda item: (item["start"], item["end"]))
    merged = []
    for item in gaps:
        if merged and item["start"] <= merged[-1]["end"] + 0.8:
            merged[-1]["end"] = max(merged[-1]["end"], item["end"])
            if item["reason"] == "suspicious_chunk":
                merged[-1]["reason"] = "suspicious_chunk"
        else:
            merged.append(dict(item))
        if len(merged) >= limit:
            break
    for item in merged:
        item["play_start"] = round(max(0.0, item["start"] - 1.5), 3)
        item["play_end"] = round(item["end"] + 1.0, 3)
    return merged


def load_review_gaps(review_dir):
    """Read unresolved.json from a caption-review-* folder."""
    import json
    from pathlib import Path
    path = Path(review_dir) / "unresolved.json"
    if not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return compact_review_gaps(rows if isinstance(rows, list) else [])


# Gaps in VAD coverage are warnings when a pack exists. Abort only when spoken
# source was dropped or the pack is empty while speech remains uncovered.
_ABORT_WITHHELD = frozenset(
    {
        "invalid_source_clock",
        "invalid_clock",
        "missing_speech_marks",
        "overlapping_clock",
        "weak_fragment_alignment",
    }
)


def ensure_complete(cues, source, output_parent, review=None, repair_report=None):
    """Persist all evidence before stopping translation of an incomplete pack."""
    import json
    from pathlib import Path
    import tempfile
    from ..srt_utils import save_srt_file

    import math
    rows = list(last_review() if review is None else review)
    from .nonspeech import effective_review, save_latest_review
    for row in rows:
        reason = row.get('reason')
        row['repair_action'] = ('asr_confirmed_gap' if reason == 'unresolved_speech_gap'
                                else 'timeline_review' if reason == 'overlapping_clock'
                                else 'context_text_review' if reason == 'isolated_unknown_fragment'
                                else 'review')
    def valid_clock(s):
        return math.isfinite(s.start) and math.isfinite(s.end) and 0 <= s.start < s.end
    for s in source:
        if not valid_clock(s):
            rows.append(dict(reason='invalid_source_clock', text=s.text,
                             start=s.start if math.isfinite(s.start) else 0,
                             end=s.end if math.isfinite(s.end) else 0,
                             raw_start=str(s.start),raw_end=str(s.end),withheld=True))
    for s in cues:
        if not valid_clock(s):
            rows.append(dict(reason='invalid_clock', text=s.text,
                             start=s.start if math.isfinite(s.start) else 0,
                             end=s.end if math.isfinite(s.end) else 0,
                             raw_start=str(s.start),raw_end=str(s.end),withheld=True))
    state = effective_review(output_parent, rows, emit_log=True)
    raw_rows = rows
    rows = state['items']
    blocked = state['effective_blockers']
    if not blocked:
        save_latest_review(output_parent, raw_rows)
        return
    rows = blocked + [r for r in rows if r not in blocked]
    root = Path(tempfile.mkdtemp(prefix="caption-review-", dir=output_parent))
    from .long_audio import atomic_json, atomic_srt
    atomic_json(root/'review.json', rows)
    atomic_json(root/'unresolved.json', [r for r in rows if r.get('withheld')])
    atomic_srt(root/'source.srt', [s for s in source if valid_clock(s)])
    atomic_srt(root/'packed.needs-review.srt', [s for s in cues if valid_clock(s)])
    sm = speechmap.get_active()
    if sm is not None:
        sm.save(str(root / "speechmap.json"))
    from collections import Counter
    from .merge import merge_time_ranges
    blocked = [r for r in rows if r.get("withheld")]
    reasons = dict(Counter(r.get("reason", "unknown") for r in blocked))
    intervals = merge_time_ranges([(r.get("start", 0), r.get("end", 0))
                                  for r in blocked], merge_gap=0)
    total = sum(b-a for a,b in intervals)
    longest = max((b-a for a,b in intervals), default=0)
    summary = dict(rule='any_withheld_region', blocked_regions=len(blocked),
                   blocked_union_s=total, longest_blocked_union_s=longest, reasons=reasons,
                   screen_thresholds=caption_options(),
                   speech_gap_thresholds_s=sorted({r['threshold_s'] for r in blocked if 'threshold_s' in r}))
    if repair_report:
        summary['repair_failure_counts'] = dict(Counter(
            r.get('reason','unknown') for r in repair_report.get('attempts',[])
            + repair_report.get('clock_attempts',[]) if r.get('reason') != 'fixed'))
        summary['speech_coverage'] = repair_report.get('coverage')
    if repair_report:
        summary['coverage_breakdown'] = repair_report.get('coverage_breakdown')
    atomic_json(root/'summary.json', summary)
    save_latest_review(output_parent, raw_rows, root.resolve())
    error = CaptionReviewRequired(
        f"Phụ đề Trung đã nhận dạng nhưng còn lỗi cần kiểm tra. "
        f"Caption review required: {len(blocked)} blocked regions; "
        f"total={total:.2f}s, longest={longest:.2f}s; reasons={reasons}. "
        f"Rule: any withheld text/clock conflict or speech gap exceeding its recorded threshold. "
        f"Chưa dịch/TTS. "
        f"Sửa nguồn hoặc nhận dạng lại. Bản nguồn và báo cáo: {root.resolve()}"
    )
    error.review_dir = str(root.resolve())
    error.source_srt = str((root/'source.srt').resolve())
    error.gaps = compact_review_gaps(blocked)
    raise error


@dataclass
class Unit:
    text: str
    start: float
    end: float
    source: Segment
    letters: tuple = ()


def _notice(review, reason, text, start, end, withheld=False, **extra):
    review.append(
        dict(
            reason=reason,
            text=text,
            start=start,
            end=end,
            needs_review=True,
            withheld=withheld,
            **extra,
        )
    )


def _reconcile_windows(segs, sm, review):
    """Old packing can borrow one mark from the next cue. Rejoin balanced runs."""
    if sm is None or sm.empty:
        return segs
    out, i = [], 0
    while i < len(segs):
        seg = segs[i]
        count = sum(c.isalnum() for c in _clean(seg.text))
        marks = sm.window(seg.start + 1e-6, seg.end - 1e-6)
        if count > 2 and marks and len(marks) != count:
            for j in range(i + 1, min(i + 4, len(segs))):
                if segs[j].end - seg.start > 8:
                    break
                if not sm.window(segs[j].start + 1e-6, segs[j].end - 1e-6):
                    break
                count += sum(c.isalnum() for c in _clean(segs[j].text))
                window = sm.window(seg.start + 1e-6, segs[j].end - 1e-6)
                if count == len(window):
                    text = "".join(_clean(s.text) for s in segs[i : j + 1])
                    review.append(
                        dict(
                            reason="reconciled_neighbor_windows",
                            text=text,
                            start=seg.start,
                            end=segs[j].end,
                            needs_review=False,
                            withheld=False,
                        )
                    )
                    seg = replace(seg, text=text, end=segs[j].end)
                    i = j
                    break
        out.append(seg)
        i += 1
    return out


def _alnum_text(text: str) -> str:
    return "".join(c for c in _clean(text) if c.isalnum())


def _fragment_absorbed_by_neighbor(seg, segs, pad: float = 0.25) -> bool:
    """True when a 1–2 glyph leftover is already inside an overlapping cue."""
    fragment = _alnum_text(seg.text)
    if not fragment:
        return False
    a, b = float(seg.start) - pad, float(seg.end) + pad
    for other in segs:
        if other is seg:
            continue
        other_text = _alnum_text(other.text)
        if len(other_text) <= len(fragment) or fragment not in other_text:
            continue
        if float(other.end) <= a or float(other.start) >= b:
            continue
        return True
    return False


def _units(segs, sm, review):
    out = []
    segs = _reconcile_windows(segs, sm, review)
    for seg in segs:
        text = _clean(seg.text)
        spoken = [i for i, c in enumerate(text) if c.isalnum()]
        if not spoken:
            continue
        a, b = max(0.0, float(seg.start)), float(seg.end)
        if b <= a:
            _notice(review, "invalid_clock", text, a, b, True)
            continue
        marks = sm.window(a + 1e-6, b - 1e-6) if sm and not sm.empty else []
        if sm and not sm.empty and not marks:
            _notice(review, "missing_speech_marks", text, a, b, True)
            continue
        if marks and len(marks) != len(spoken):
            # Extra marks on a 1-2 character cue is the punctuation-clock
            # failure mode. Fewer marks than letters still has observed speech;
            # keep the words and allocate inside those clocks.
            extra_marks = len(marks) > len(spoken)
            if extra_marks and len(spoken) <= 2:
                if _fragment_absorbed_by_neighbor(seg, segs):
                    _notice(
                        review,
                        "absorbed_overlapping_fragment",
                        text,
                        a,
                        b,
                        False,
                        characters=len(spoken),
                        marks=len(marks),
                    )
                    continue
                _notice(
                    review,
                    "weak_fragment_alignment",
                    text,
                    a,
                    b,
                    True,
                    characters=len(spoken),
                    marks=len(marks),
                )
                continue
            _notice(
                review,
                "approximate_character_alignment",
                text,
                a,
                b,
                characters=len(spoken),
                marks=len(marks),
            )
        times = {}
        for j, pos in enumerate(spoken):
            if marks:
                # Fractional allocation is within observed speech only, never a hole.
                lo, hi = (
                    j * len(marks) / len(spoken),
                    (j + 1) * len(marks) / len(spoken),
                )
                k, last = int(lo), min(len(marks) - 1, max(int(lo), int(hi - 1e-9)))
                x, y = marks[k]
                st = x + (y - x) * (lo - k)
                x, y = marks[last]
                en = x + (y - x) * min(1.0, hi - last)
                times[pos] = (max(a, st), min(b, en))
            else:
                times[pos] = (
                    a + (b - a) * j / len(spoken),
                    a + (b - a) * (j + 1) / len(spoken),
                )
        cursor = a
        for i, c in enumerate(text):
            st, en = times.get(i, (cursor, cursor))
            out.append(Unit(c, st, en, seg))
            cursor = en
    return out


def _tokens(units, gap, review, protected_words):
    raw = "".join(u.text for u in units)
    cleaned = repair_asr_punctuation(raw)
    # The existing repair removes punctuation only; retain each surviving clock.
    aligned, pos = [], 0
    for ch in cleaned:
        while pos < len(units) and units[pos].text != ch:
            pos += 1
        if pos >= len(units):
            raise ValueError("Punctuation repair changed source characters")
        aligned.append(units[pos])
        pos += 1

    # ct-punc may put punctuation INSIDE a dictionary word (寻，找 / 人。类).
    # Segment lexical text first. Keep punctuation at word edges, not inside words.
    def lexical_char(i, c):
        if c.isalnum() or c.isspace():
            return True
        before, after = cleaned[i - 1 : i] if i else "", cleaned[i + 1 : i + 2]
        return (
            (c in ".," and before.isdigit() and after.isdigit())
            or (c in "+-" and after.isdigit())
            or (
                c in "-'"
                and before.isascii()
                and before.isalpha()
                and after.isascii()
                and after.isalpha()
            )
        )

    positions = [i for i, c in enumerate(cleaned) if lexical_char(i, c)]
    lexical = "".join(cleaned[i] for i in positions)
    tokens = []

    def append_token(chunk_units, chunk_text):
        spoken_c = [u for u in chunk_units if u.text.isalnum()]
        if not spoken_c:
            if tokens:
                tokens[-1].text += chunk_text
            return
        token = Unit(
            chunk_text, spoken_c[0].start, spoken_c[-1].end, spoken_c[0].source,
            tuple((u.start, u.end) for u in spoken_c),
        )
        if (
            tokens
            and chunk_text in PARTICLES
            and token.start - tokens[-1].end <= 0.12
            and tokens[-1].text.rstrip()[-1:] not in HARD
        ):
            tokens[-1].text += chunk_text
            tokens[-1].end = token.end
            tokens[-1].letters += token.letters
        else:
            tokens.append(token)

    for a, b in word_spans(lexical, protected_words):
        start = positions[a]
        end = positions[b] if b < len(positions) else len(cleaned)
        part = aligned[start:end]
        text = lexical[a:b] + cleaned[positions[b - 1] + 1 : end]
        spoken = [u for u in part if u.text.isalnum()]
        if not spoken:
            if tokens:
                tokens[-1].text += text
            continue
        holes = [y.start - x.end for x, y in zip(spoken, spoken[1:])]
        if holes and max(holes) > gap + 1e-6:
            # Jieba glued 上面 across a pause. Keep both characters; do not
            # drop the word or draw a cue through the silence.
            _notice(
                review,
                "word_crosses_pause",
                text,
                spoken[0].start,
                spoken[-1].end,
                False,
                hole=max(holes),
            )
            groups, current, last = [], [], None
            for unit in part:
                if unit.text.isalnum() and last is not None:
                    if unit.start - last.end > gap + 1e-6:
                        groups.append(current)
                        current = []
                current.append(unit)
                if unit.text.isalnum():
                    last = unit
            if current:
                groups.append(current)
            for chunk in groups:
                body = "".join(u.text for u in chunk if u.text.isalnum())
                if chunk and part and chunk[-1] is part[-1]:
                    last_al = max(
                        (i for i, u in enumerate(chunk) if u.text.isalnum()),
                        default=-1,
                    )
                    body += "".join(u.text for u in chunk[last_al + 1 :])
                append_token(chunk, body)
            continue
        append_token(part, text)
    return tokens


def _size(text):
    return sum(c.isalnum() for c in text)


def _bounded_tokens(tokens, hard_chars, hard_duration, review):
    """Word boundaries are preferred; oversized words split at observed letters.

    Never stretch a character or invent a timestamp to satisfy a display limit.
    An indivisible observation longer than the limit stays explicit in review.
    """
    for token in tokens:
        positions = [i for i, c in enumerate(token.text) if c.isalnum()]
        if len(positions) <= hard_chars and token.end-token.start <= hard_duration+1e-6:
            yield token
            continue
        clocks = token.letters
        if len(clocks) != len(positions):
            _notice(review, 'token_exceeds_screen_limit', token.text,
                    token.start, token.end, True)
            continue
        start = 0
        while start < len(positions):
            end = start + 1
            while (end < len(positions) and end-start < hard_chars
                   and clocks[end][1]-clocks[start][0] <= hard_duration+1e-6):
                end += 1
            lo = 0 if start == 0 else positions[start]
            hi = positions[end] if end < len(positions) else len(token.text)
            piece = Unit(token.text[lo:hi], clocks[start][0], clocks[end-1][1],
                         token.source, clocks[start:end])
            if piece.end-piece.start > hard_duration+1e-6:
                _notice(review, 'token_exceeds_screen_limit', piece.text,
                        piece.start, piece.end, True)
            else:
                yield piece
            start = end
        _notice(review, 'split_oversized_word', token.text, token.start, token.end, False)


def _clause_boundary(tokens, i):
    if i and tokens[i - 1].text.strip() == "好运":
        # A valediction closes a speech phrase even when ct-punc misses its stop.
        prefix = tokens[i - 1].source.text.split("好运", 1)[0]
        if re.search(r"(?:祝.{0,4}|你们|你)$", prefix):
            return True
    if i == 0 or tokens[i].text.strip() not in ("他们", "我们", "你们"):
        return False
    before = "".join(t.text for t in tokens[max(0, i - 8) : i])
    subject = tokens[i].text.strip()
    # Repeated subject after a complete predicate is useful when ct-punc is absent.
    return subject in before and _size(before.rsplit(subject, 1)[-1]) >= 4


def _pack_burst(tokens, max_chars, min_chars, max_duration, hard_chars, hard_duration):
    """Shortest path over WORD boundaries, with soft length/pause/punctuation costs."""
    n = len(tokens)
    best, edges = [float("inf")] * (n + 1), [None] * (n + 1)
    best[0] = 0.0
    for i in range(n):
        count, internal_penalty = 0, 0.0
        for j in range(i, n):
            count += _size(tokens[j].text)
            duration = tokens[j].end - tokens[i].start
            if j > i and (count > hard_chars or duration > hard_duration + 1e-6):
                break
            if j > i:
                left = tokens[j - 1].text.rstrip()[-1:]
                pause = max(0.0, tokens[j].start - tokens[j - 1].end)
                internal_penalty += 4.5 if left in SOFT and count >= min_chars else 0
                internal_penalty += max(0.0, pause - 0.12) * 12
            target = min(8.0, max_chars)
            cost = 2.0 + ((count - target) / target) ** 2 * 3
            cost += max(0, min_chars - count) * 2.5
            cost += max(0, count - max_chars) * 1.5
            cost += max(0.0, duration - max_duration) * 3
            end_punct = tokens[j].text.rstrip()[-1:]
            pause = max(0.0, tokens[j + 1].start - tokens[j].end) if j + 1 < n else 0.0
            reward = 3.5 if end_punct in SOFT and count >= min_chars else 0.0
            reward += 2.5 if pause >= 0.18 and count >= min_chars else 0.0
            candidate = best[i] + cost + internal_penalty - reward
            if candidate < best[j + 1]:
                best[j + 1], edges[j + 1] = candidate, i
    if edges[n] is None:
        raise ValueError("No valid word boundary within screen limits")
    spans, end = [], n
    while end:
        start = edges[end]
        spans.append((start, end))
        end = start
    out = []
    for a, b in reversed(spans):
        first, last = tokens[a], tokens[b - 1]
        out.append(
            replace(
                first.source,
                index=0,
                start=first.start,
                end=last.end,
                text="".join(t.text for t in tokens[a:b]).strip(),
                hard_boundary=False,
            )
        )
    return out


def pack(
    segs,
    *,
    max_chars=None,
    min_chars=None,
    max_duration=None,
    hard_max_chars=None,
    hard_max_duration=None,
    gap=None,
    speech_map=None,
    review=None,
    protected_words=(),
):
    opts = caption_options()
    max_chars = max(4, int(max_chars if max_chars is not None else opts["max_chars"]))
    min_chars = max(
        1,
        min(max_chars, int(min_chars if min_chars is not None else opts["min_chars"])),
    )
    max_duration = max(
        0.1, float(max_duration if max_duration is not None else opts["max_duration"])
    )
    hard_chars = max(
        max_chars,
        int(hard_max_chars if hard_max_chars is not None else opts["hard_max_chars"]),
    )
    hard_duration = max(
        max_duration,
        float(
            hard_max_duration
            if hard_max_duration is not None
            else opts["hard_max_duration"]
        ),
    )
    gap = max(0.12, float(gap if gap is not None else opts["gap"]))
    diagnostics = []
    sm = speech_map if speech_map is not None else speechmap.get_active()
    result, group = [], []

    def flush_group():
        if not group:
            return
        group_start = len(result)
        tokens = _tokens(
            _units(group, sm, diagnostics), gap, diagnostics, protected_words
        )
        tokens = list(_bounded_tokens(tokens, hard_chars, hard_duration, diagnostics))
        burst = []

        def flush_burst():
            if burst:
                text = "".join(t.text for t in burst).strip()
                lexical = "".join(c for c in text if c.isalnum())
                # Isolated 1-2 character cues are kept when every glyph is in the
                # lexicon (vocatives, names, particles). Missing clocks still
                # withhold in _units; unknown glyphs remain review material.
                if 1 <= len(lexical) <= 2 and not _supported_short_fragment(
                    lexical, _jieba()
                ):
                    _notice(
                        diagnostics,
                        "isolated_unknown_fragment",
                        text,
                        burst[0].start,
                        burst[-1].end,
                        True,
                    )
                    burst.clear()
                    return
                result.extend(
                    _pack_burst(
                        burst,
                        max_chars,
                        min_chars,
                        max_duration,
                        hard_chars,
                        hard_duration,
                    )
                )
                burst.clear()

        for i, token in enumerate(tokens):
            if _size(token.text) > hard_chars:
                flush_burst()
                _notice(
                    diagnostics,
                    "token_exceeds_screen_limit",
                    token.text,
                    token.start,
                    token.end,
                    False,
                )
                burst.append(token)
                flush_burst()
                continue
            if token.end - token.start > hard_duration:
                flush_burst()
                _notice(
                    diagnostics,
                    "token_exceeds_screen_limit",
                    token.text,
                    token.start,
                    token.end,
                    False,
                )
                burst.append(token)
                flush_burst()
                continue
            if burst and (
                token.start - burst[-1].end > gap + 1e-6
                or burst[-1].text.rstrip()[-1:] in HARD
                or (
                    burst[-1].text.rstrip()[-1:] in SOFT
                    and sum(_size(t.text) for t in burst) >= min_chars
                )
                or _clause_boundary(tokens, i)
            ):
                flush_burst()
            burst.append(token)
        flush_burst()
        if len(result) > group_start:
            result[group_start].hard_boundary = group[0].hard_boundary
        group.clear()

    previous = None
    for s in sorted(segs or [], key=lambda s: (s.start, s.end)):
        if previous and (
            s.hard_boundary
            or s.scene != previous.scene
            or s.chapter != previous.chapter
            or s.speaker != previous.speaker
        ):
            flush_group()
        if _is_cjk(s.text):
            group.append(s)
        else:
            flush_group()
            if _size(s.text):
                result.append(replace(s))
        previous = s
    flush_group()
    result.sort(key=lambda s: (s.start, s.end))
    valid = []
    for s in result:
        if valid:
            hole = s.start - valid[-1].end
            if hole < 0 or (
                hole <= 0.10
                and not s.hard_boundary
                and s.speaker == valid[-1].speaker
                and s.scene == valid[-1].scene
            ):
                s.start = valid[-1].end
        if s.end <= s.start:
            _notice(diagnostics, "overlapping_clock", s.text, s.start, s.end, True)
            continue
        s.index = len(valid) + 1
        valid.append(s)
    _LOCAL.review = diagnostics
    if review is not None:
        review.extend(diagnostics)
    return valid
