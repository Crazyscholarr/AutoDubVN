"""Durable adaptive recognition. Never promote a suspicious parent as success."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path

from ..srt_utils import Segment, save_srt_file
from ..utils import log

LAST_RUN = ContextVar('asr_long_audio_run', default=None)
VERSION = 2
# loudnorm of the same source can drift a couple of seconds vs a prior extract.
_REEXTRACT_DURATION_SLACK = 2.0
_BOUNDARY_SLACK = 0.05
_DEPTH0 = re.compile(r'^chunk_\d{4}$')
# Empty speech under SFX (punches, hits) must keep splitting below min_seconds.
_EMPTY_SPEECH_LEAF_S = 12.0


def _identity_signature(identity, ignore=()):
    skipped = {'version', *ignore}
    return {k: v for k, v in identity.items() if k not in skipped}


def _wildcard_language(value):
    return value in (None, '', 'auto')


def _clocks_match(left, right, slack=_BOUNDARY_SLACK):
    try:
        return abs(float(left) - float(right)) <= slack
    except (TypeError, ValueError):
        return left == right


def compatible_checkpoint(prior, identity):
    """Reuse raw FunASR dumps after a parser bump or a same-job re-extract."""
    if not isinstance(prior, dict) or not isinstance(identity, dict):
        return False
    if _identity_signature(prior) == _identity_signature(identity):
        return True
    ignore = {'audio_sha256', 'duration'}
    if _wildcard_language(prior.get('language')) or _wildcard_language(identity.get('language')):
        ignore.add('language')
    if _identity_signature(prior, ignore) != _identity_signature(identity, ignore):
        return False
    try:
        return abs(float(prior.get('duration')) - float(identity.get('duration'))) <= _REEXTRACT_DURATION_SLACK
    except (TypeError, ValueError):
        return prior.get('duration') == identity.get('duration')


def depth0_healthy_plan(root):
    plan = []
    for path in sorted(Path(root).glob('chunk_*.json')):
        if not _DEPTH0.fullmatch(path.stem):
            continue
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if state.get('state') != 'healthy':
            continue
        try:
            plan.append((float(state['start']), float(state['end']), path.stem))
        except (TypeError, ValueError, KeyError):
            continue
    plan.sort(key=lambda row: (row[0], row[1]))
    return plan


def plan_covers_duration(plan, duration, slack=_REEXTRACT_DURATION_SLACK):
    if not plan:
        return False
    if abs(plan[0][0]) > _BOUNDARY_SLACK:
        return False
    if abs(plan[-1][1] - float(duration)) > slack:
        return False
    return all(_clocks_match(plan[i][1], plan[i + 1][0]) for i in range(len(plan) - 1))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False,
                      default=lambda x: x.tolist() if hasattr(x, 'tolist') else str(x))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def working_path(audio):
    p = Path(audio).resolve()
    return (p.parent.parent / (p.parent.parent.name + '.asr.working.srt')
            if p.parent.name == '_tmp' else p.with_suffix('.asr.working.srt'))


def save_working(audio, segments):
    path = working_path(audio)
    atomic_srt(path, segments)
    return str(path)


def atomic_srt(path, segments):
    path = Path(path)
    fd, temp = tempfile.mkstemp(dir=path.parent, suffix='.srt')
    os.close(fd)
    try:
        save_srt_file(temp, sorted(segments, key=lambda s: (s.start, s.end)))
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def union_seconds(ranges):
    end, total = -math.inf, 0.0
    for a, b in sorted(ranges):
        total += max(0, b - max(a, end))
        end = max(end, b)
    return total


def safe_boundary(target, start, end, speech, radius=5.0):
    if speech is None:
        return target
    candidates = []
    intervals = [(max(start, a), min(end, b)) for a, b in speech if b > start and a < end]
    previous = start
    for a, b in intervals + [(end, end)]:
        if a - previous >= .15:
            candidate = min(max(target, previous + .05), a - .05)
            if start + 1 < candidate < end - 1 and abs(candidate-target) <= radius:
                candidates.append(candidate)
        previous = max(previous, b)
    return min(candidates, key=lambda x: abs(x-target)) if candidates else target


def diagnostics(raw, normalized, start, end, speech):
    span = end-start
    speech_s = None if speech is None else union_seconds(
        (max(start,a), min(end,b)) for a,b in speech if a < end and b > start)
    chars = sum(len(s.text.strip()) for s in normalized.segments)
    marks = normalized.marks
    timestamp_s = union_seconds(marks)
    reasons = []
    if normalized.status not in ('ASR_VALID', 'ASR_EMPTY'):
        reasons.append(normalized.status)
    if not normalized.segments and speech_s != 0:
        reasons.append('empty_without_confirmed_silence')
    if span >= 60 and normalized.segments:
        longest = max(s.end-s.start for s in normalized.segments)
        if longest > 30 and (len(marks) < span*.2 or longest > span*.5):
            reasons.append('oversized_cue_or_sparse_timestamps')
        if speech_s is not None and speech_s > 30:
            if chars < speech_s*.2:
                reasons.append('too_little_text_for_confirmed_speech')
            if timestamp_s < speech_s*.35:
                reasons.append('low_timestamp_coverage')
    records = raw if isinstance(raw, (list,tuple)) else [raw]
    records = [r for r in records if isinstance(r,dict)]
    return dict(raw_type=type(raw).__name__, text_length=chars,
                raw_text_length=sum(len(str(r.get('text',''))) for r in records),
                raw_timestamp_count=sum(len(r.get('timestamp') or []) for r in records),
                raw_sentence_count=sum(len(r.get('sentence_info') or []) for r in records),
                timestamp_count=len(marks), normalized_segment_count=len(normalized.segments),
                speech_duration_s=speech_s, timestamp_duration_s=timestamp_s,
                duration_s=span, reasons=reasons,
                status='CHUNK_SUSPICIOUS' if reasons else 'CHUNK_HEALTHY')


def owned_result(result, start, end):
    """Assign boundary context by observed token midpoint, preserving token clocks."""
    from dataclasses import replace
    from .funasr import _aligned_text_tokens
    segments, marks = [], []
    for segment in result.segments:
        local=[(a,b) for a,b in result.marks if segment.start-.001 <= a < b <= segment.end+.001]
        tokens=_aligned_text_tokens(segment.text,len(local))
        if tokens is not None:
            keep=[(text,a,b) for text,(a,b) in zip(tokens,local) if start <= (a+b)/2 < end]
            if keep:
                segments.append(replace(segment,start=keep[0][1],end=keep[-1][2],
                                        text=''.join(text for text,a,b in keep)))
                marks.extend((a,b) for text,a,b in keep)
        elif start <= (segment.start+segment.end)/2 < end:
            segments.append(segment)
            marks.extend(local)
    return segments, marks


def recognize(model, audio, language, duration, chunk_seconds, overlap,
              generate, extract, normalize, speech=None, model_identity=None,
              min_seconds=60, max_depth=4):
    from .common import caption_options, _set_last_marks
    digest = hashlib.sha256()
    with open(audio, 'rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            digest.update(block)
    identity = dict(version=VERSION, audio_sha256=digest.hexdigest(), duration=duration,
                    model=model_identity, language=language, options=caption_options(),
                    chunk_seconds=chunk_seconds, overlap=overlap,
                    min_seconds=min_seconds, max_depth=max_depth)
    # Once the media changes, the same-duration re-extract exception must not
    # reuse checkpoints belonging to the previous movie. Legacy first binding
    # retains its existing checkpoint key.
    from .nonspeech import source_context
    context = source_context(Path(audio).resolve().parent)
    if context and not context.get('allow_legacy', True):
        identity['review_source_id'] = context['source_id']
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    root = Path(audio).resolve().parent / 'asr_chunks' / key
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(root/'identity.json', identity)
    # Reparse raw evidence after a parser upgrade or a loudnorm re-extract
    # of the same job (audio SHA256 changes, timestamps stay usable).
    prior_roots = []
    for candidate in root.parent.glob('*/identity.json'):
        if candidate.parent == root:
            continue
        try:
            prior = json.loads(candidate.read_text(encoding='utf-8'))
            if compatible_checkpoint(prior, identity):
                prior_roots.append(candidate.parent)
        except (OSError, ValueError):
            continue
    accepted, marks, unresolved = [], [], []
    report = dict(checkpoint_dir=str(root), identity=identity, chunks=[], unresolved=unresolved)
    LAST_RUN.set(report)

    def publish():
        report['working_srt'] = save_working(audio, accepted)
        atomic_json(root/'run.json', report)

    def visit(a, b, depth, name):
        path = root / (name+'.json')
        state = None
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding='utf-8'))
                if not _clocks_match(state['start'], a) or not _clocks_match(state['end'], b):
                    log(f"ASR checkpoint {name} {state.get('start')}-{state.get('end')} "
                        f"khác plan {a}-{b} — không dùng state này", "info")
                    state = None
            except (OSError, ValueError, KeyError):
                state = None
        if state and state.get('state') == 'split':
            split = state['split']
            visit(a, split, depth+1, name+'a')
            visit(split, b, depth+1, name+'b')
            return
        if not state or state.get('state') != 'healthy':
            cs, ce = max(0,a-overlap), min(duration,b+overlap)
            started = time.monotonic()
            raw = None
            reused_raw = False
            for previous in [root] + prior_roots:
                raw_path = previous/(name+'.raw.json')
                meta_path = previous/(name+'.meta.json')
                try:
                    meta=json.loads(meta_path.read_text(encoding='utf-8'))
                    if _clocks_match(meta['start'], a) and _clocks_match(meta['end'], b):
                        raw=json.loads(raw_path.read_text(encoding='utf-8'))
                        reused_raw=True
                        break
                except (OSError,ValueError,KeyError):
                    continue
            if not reused_raw:
                with tempfile.TemporaryDirectory(prefix='asr-chunk-', dir=root) as td:
                    piece = str(Path(td)/'audio.wav')
                    extract(audio, piece, cs, ce-cs)
                    raw = generate(model, piece)
            # Persist raw output BEFORE parsing, including suspicious parents.
            atomic_json(root/(name+'.raw.json'), raw)
            result = normalize(raw, offset=cs, duration_hint=ce-cs)
            diag = diagnostics(raw, result, cs, ce, speech)
            diag.update(chunk=name, depth=depth, start=a, end=b,
                        wall_s=time.monotonic()-started, reused_raw=reused_raw)
            diag['rtf'] = diag['wall_s']/(ce-cs)
            report['chunks'].append(diag)
            atomic_json(root/(name+'.meta.json'), diag)
            log(f"ASR chunk {name}: {diag}", 'info')
            if diag['reasons']:
                empty_speech = 'empty_without_confirmed_silence' in diag['reasons']
                can_split = depth < max_depth and b-a >= 2*min_seconds
                can_empty_split = (
                    empty_speech
                    and depth < max_depth + 6
                    and b-a >= 2 * _EMPTY_SPEECH_LEAF_S
                )
                if can_split or can_empty_split:
                    if can_empty_split and not can_split:
                        log(f"ASR chunk {name}: {b-a:.1f}s rỗng dù có tiếng — "
                            "tách nhỏ hơn (hiệu ứng/nắm tay có thể che thoại)", "info")
                    split = safe_boundary((a+b)/2,a,b,speech)
                    atomic_json(path, dict(state='split', start=a, end=b, split=split))
                    publish()
                    visit(a,split,depth+1,name+'a')
                    visit(split,b,depth+1,name+'b')
                else:
                    unresolved.append(dict(start=a,end=b,reason='suspicious_chunk', diagnostics=diag))
                    atomic_json(path, dict(state='unresolved', start=a,end=b, diagnostics=diag))
                    publish()
                return
            # Ownership by cue midpoint; retain the full observed timing/text.
            segs, kept_marks = owned_result(result,a,b)
            state = dict(state='healthy', start=a,end=b,
                         segments=[asdict(s) for s in segs], marks=kept_marks)
            atomic_json(path, state)
        else:
            log(f"ASR resume: {name} đã có checkpoint hợp lệ", 'info')
        atomic_srt(root/(name+'.srt'), [Segment(**row) for row in state['segments']])
        for row in state['segments']:
            seg = Segment(**row)
            if not any(s.text == seg.text and abs(s.start-seg.start)<.25 and abs(s.end-seg.end)<.25 for s in accepted):
                accepted.append(seg)
        marks.extend(tuple(pair) for pair in state['marks'])
        publish()

    # Do not overwrite a prior working transcript with an empty startup file.
    if not working_path(audio).exists():
        save_working(audio, [])
    if speech is not None:
        atomic_json(root/'speech_ranges.json',
                    [(float(a), float(b)) for a, b in speech])

    def persist_plan(plan):
        atomic_json(root/'chunk_plan.json', dict(
            ranges=[dict(start=a, end=b, name=n) for a, b, n in plan]))

    def walk_from(start, idx, planned):
        while start < duration:
            end = min(duration, start + chunk_seconds)
            if end < duration:
                end = safe_boundary(end, start, duration, speech)
            name = f'chunk_{idx:04d}'
            visit(start, end, 0, name)
            planned.append((start, end, name))
            start, idx = end, idx + 1
        return planned

    frozen = None
    plan_path = root / 'chunk_plan.json'
    if plan_path.exists():
        try:
            payload = json.loads(plan_path.read_text(encoding='utf-8'))
            frozen = [(float(row['start']), float(row['end']), row['name'])
                      for row in payload.get('ranges') or []]
        except (OSError, ValueError, TypeError, KeyError):
            frozen = None
    if not frozen:
        frozen = depth0_healthy_plan(root)
    if plan_covers_duration(frozen, duration):
        log(f"ASR resume: khóa {len(frozen)} mảnh chunk_plan, không tính lại VAD boundary", "info")
        for a, b, name in frozen:
            visit(a, b, 0, name)
        persist_plan(frozen)
    else:
        planned = []
        start, idx = 0.0, 1
        if frozen:
            for a, b, name in frozen:
                visit(a, b, 0, name)
                planned.append((a, b, name))
            start, idx = frozen[-1][1], int(frozen[-1][2].split('_')[1]) + 1
        planned = walk_from(start, idx, planned)
        if plan_covers_duration(planned, duration):
            persist_plan(planned)
    _set_last_marks(sorted(set(marks)))
    publish()
    return sorted(accepted,key=lambda s:(s.start,s.end)), language or 'zh'
