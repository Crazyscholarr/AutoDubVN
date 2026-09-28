"""Shared semantic boundaries for translation, validation and speech synthesis."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

from .srt_utils import Segment

METADATA_FIELDS = ("semantic_group", "hard_boundary", "scene", "chapter", "allowed_source_names")


def without_allowed_names(text, names):
    for name in sorted(names or (), key=len, reverse=True):
        text = text.replace(name, "")
    return text


def copy_metadata(source, target):
    for field in METADATA_FIELDS:
        value = getattr(source, field)
        if isinstance(target, dict):
            target[field] = value
        else:
            setattr(target, field, value)


def restore_metadata(segments, cache_path):
    """Restore ownership by exact clocks, or unchanged text after TTS clocks move.

    Text may differ after a local leftover-CJK repair; sentence ownership is
    about source grouping, not the Vietnamese glyphs. Conversely, TTS/picture
    clock writeback may change every timestamp while preserving the exact
    translated cue sequence. Requiring either exact clocks or exact text keeps
    that metadata without attaching it to an unrelated same-length SRT.
    """
    try:
        data = json.loads(Path(str(cache_path) + ".semantic-cues.json").read_text(encoding="utf-8"))
        if len(data) != len(segments):
            return False
        ids_match = all(s.index == row["cue"][0]
                        for s, row in zip(segments, data))
        if not ids_match:
            return False
        clocks_match = all(
            [round(s.start, 3), round(s.end, 3)] == row["cue"][1:3]
            for s, row in zip(segments, data)
        )
        text_match = all(
            " ".join(str(s.text or "").split())
            == " ".join(str(row["cue"][3] or "").split())
            for s, row in zip(segments, data)
        )
        if not clocks_match and not text_match:
            return False
        for s, row in zip(segments, data):
            for field in METADATA_FIELDS:
                val = row[field]
                if field == "allowed_source_names" and isinstance(val, list):
                    val = tuple(val)
                setattr(s, field, val)
        return True
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return False


def sync_cue_text(segments, cache_path):
    """Keep sidecar spoken text aligned after a local residue repair."""
    path = Path(str(cache_path) + ".semantic-cues.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if len(data) != len(segments):
            return False
        for s, row in zip(segments, data):
            if [s.index, round(s.start, 3), round(s.end, 3)] != row["cue"][:3]:
                return False
            row["cue"][3] = s.text
        tmp = str(path) + ".tmp"
        Path(tmp).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        Path(tmp).replace(path)
        return True
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return False


def enabled(cfg):
    """Configured application calls use semantic translation; bare API stays compatible."""
    return cfg is not None and cfg.get("semantic_translation", True) is not False


def boundary(left, right, max_gap=1.4):
    if getattr(right, "hard_boundary", False):
        return True
    if right.start - left.end > max_gap:
        return True
    if left.speaker and right.speaker and left.speaker != right.speaker:
        return True
    for key in ("scene", "chapter"):
        a, b = getattr(left, key, None), getattr(right, key, None)
        if a is not None and b is not None and a != b:
            return True
    return False


def groups(segments, cfg=None, *, speech=False):
    """Conservative punctuation boundaries; limits bound requests and TTS latency."""
    cfg = cfg or {}
    gap = max(0.0, float(cfg.get("max_group_gap_ms", 1400))) / 1000
    max_cues = max(1, min(30, int(cfg.get("semantic_group_max_cues", 6))))
    max_seconds = max(1.0, min(60.0, float(cfg.get("semantic_group_max_seconds", 20))))
    spans, lo = [], 0
    for i in range(1, len(segments)):
        a, b = segments[i - 1], segments[i]
        aid, bid = getattr(a, "semantic_group", None), getattr(b, "semantic_group", None)
        # Explicit translated sentence ownership survives text-only redistribution.
        known_end = aid is not None and bid is not None and aid != bid
        owned = aid is not None and bid is not None and aid == bid
        complete = bool(re.search(r'[.!?。？！]["”’\')\]」』]*$', a.text.strip()))
        if not speech and re.search(r"[\u3400-\u9fff]", a.text):
            # ASR sometimes emits punctuation inside Chinese words (整。/晚).
            # Treat punctuation alone as soft; a clear new-topic opener is needed.
            complete = complete and bool(re.match(
                r"(?:但是|然而|第二天|与此同时|这时|随后|另一方面)", b.text.strip()))
        over_cues = i - lo >= max_cues
        over_time = b.end - segments[lo].start > max_seconds
        if speech and owned:
            over_cues = False
            over_time = False
        split = (boundary(a, b, gap) or known_end
                 or (complete and not owned)
                 or over_cues or over_time)
        if speech and a.voice and b.voice and a.voice != b.voice:
            split = True
        if split:
            spans.append((lo, i))
            lo = i
    if segments:
        spans.append((lo, len(segments)))
    return spans


def batches(segments, cfg=None):
    cfg = cfg or {}
    limit = max(6, min(30, int(cfg.get("semantic_batch_cues", 30))))
    seconds = max(20.0, min(60.0, float(cfg.get("semantic_batch_seconds", 60))))
    try:
        max_chars = int(cfg.get("semantic_batch_chars", 2400) or 2400)
    except (TypeError, ValueError):
        max_chars = 2400
    max_chars = max(400, min(8000, max_chars))
    result, lo, hi = [], 0, 0

    def span_chars(a, b):
        return sum(len((segments[i].text or "").strip()) for i in range(a, b))

    for a, b in groups(segments, cfg):
        if hi > lo and (b - lo > limit
                        or segments[b - 1].end - segments[lo].start > seconds
                        or span_chars(lo, b) > max_chars):
            result.append((lo, hi))
            lo = a
        hi = b
    if hi > lo:
        result.append((lo, hi))
    return result


def speech_segments(segments, cfg=None):
    """Create independent audio units. Never stretch or merge display cue clocks."""
    units = []
    for lo, hi in groups(segments, cfg, speech=True):
        first, last = segments[lo], segments[hi - 1]
        unit = replace(first, end=last.end,
                       text=" ".join(s.text.strip() for s in segments[lo:hi]),
                       audio_path=None, placed_start=None, voice_duration=None)
        units.append(unit)
    return units


def _caption_weight(text):
    compact = re.sub(r"\s+", "", text or "")
    return max(1, len(compact))


def stamp_display_cues_to_speech(display, units, cfg=None):
    """Map on-screen cues onto the spoken TTS interval of each group.

    Semantic TTS reads a group as one continuous clip. Chinese screen-pack
    gaps inside that group are not spoken, so captions must follow compact
    Vietnamese length along [placed_start, placed_start + voice_duration].
    """
    if not display or not units:
        return 0
    stamped = 0
    for (lo, hi), unit in zip(groups(display, cfg, speech=True), units):
        try:
            spoken_start = float(unit.placed_start)
            spoken_dur = float(unit.voice_duration)
        except (TypeError, ValueError):
            continue
        if spoken_dur <= 0.01:
            continue
        cues = display[lo:hi]
        if not cues:
            continue
        spoken_end = spoken_start + spoken_dur
        weights = [_caption_weight(cue.text) for cue in cues]
        total = float(sum(weights))
        edges = [spoken_start]
        acc = 0.0
        for weight in weights[:-1]:
            acc += weight
            edges.append(spoken_start + spoken_dur * (acc / total))
        edges.append(spoken_end)
        for i in range(1, len(edges) - 1):
            lo_edge = edges[i - 1] + 0.04
            hi_edge = spoken_end - 0.04 * (len(edges) - 1 - i)
            edges[i] = max(lo_edge, min(hi_edge, edges[i]))
        speed = getattr(unit, "speed", None) or 1.0
        for i, cue in enumerate(cues):
            start, end = edges[i], edges[i + 1]
            if end <= start:
                end = min(spoken_end, start + 0.04)
            cue.start = round(start, 3)
            cue.end = round(end, 3)
            if cue.end <= cue.start:
                cue.end = round(min(spoken_end, cue.start + 0.04), 3)
            cue.placed_start = cue.start
            cue.voice_duration = round(max(0.04, cue.end - cue.start), 3)
            cue.speed = speed
            stamped += 1
        last = cues[-1]
        last.end = round(spoken_end, 3)
        if last.end <= last.start:
            last.end = round(last.start + 0.04, 3)
        last.voice_duration = round(max(0.04, last.end - last.start), 3)
    return stamped
