"""Single review decision model.

ASR -> raw diagnostics (requires_review) -> persisted user decisions -> effective
items -> GUI/Translation/TTS gate. Diagnostic labels never grant/deny permission.
Raw evidence is retained; blocking is derived, never a persisted authority.
"""
from __future__ import annotations

import hashlib
import json
from bisect import bisect_left, bisect_right
from collections import defaultdict

NON_BLOCKING = frozenset({'CONFIRMED_NOISE', 'CONFIRMED_EFFECT', 'IGNORED', 'RESOLVED'})
RESOLUTIONS = NON_BLOCKING | {'UNREVIEWED', 'CONFIRMED_SPEECH'}


def normalize_resolution(row):
    value = str(row.get('resolution') or '').upper()
    if value in RESOLUTIONS:
        return value
    if value:
        return 'UNREVIEWED'  # unknown schema values must never grant permission
    if row.get('confirmed') is False:
        return 'UNREVIEWED'
    kind = str(row.get('type') or '').lower()
    if kind in {'effect', 'sound_effect'}:
        return 'CONFIRMED_EFFECT'
    if kind == 'speech':
        return 'CONFIRMED_SPEECH'
    return 'CONFIRMED_NOISE'  # legacy non_speech.json was explicit confirmation


def issue_id(row, source_id=''):
    identity = [source_id, round(float(row.get('start') or 0), 3),
                round(float(row.get('end') or 0), 3), str(row.get('reason') or 'unknown')]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]


class CoverageIndex:
    """Union intervals once; coverage queries use prefix sums and binary search."""
    def __init__(self, ranges):
        merged = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        self.starts = [a for a, _ in merged]
        self.ends = [b for _, b in merged]
        self.prefix = [0.]
        for a, b in merged:
            self.prefix.append(self.prefix[-1] + b - a)

    def covers(self, start, end):
        if end <= start:
            return False
        left = bisect_right(self.ends, start)
        right = bisect_left(self.starts, end)
        if left >= right:
            return False
        hit = self.prefix[right] - self.prefix[left]
        hit -= max(0., start - self.starts[left])
        hit -= max(0., self.ends[right-1] - end)
        return hit / (end-start) >= .9

    def intersects(self, start, end):
        i = bisect_right(self.ends, start)
        return i < len(self.starts) and self.starts[i] < end


def review_state(rows, decisions=(), source_id=''):
    from .nonspeech import ACKABLE_REASONS, compact_ranges
    by_resolution = defaultdict(list)
    for row in compact_ranges(decisions):
        resolution=normalize_resolution(row)
        pad=0 if resolution in {'UNREVIEWED','CONFIRMED_SPEECH'} else .05
        by_resolution[resolution].append((row['start']-pad,row['end']+pad))
    indexes = {key: CoverageIndex(value) for key, value in by_resolution.items()}
    # Explicit speech must not be silently masked by an older no-speech claim.
    precedence = ['CONFIRMED_SPEECH','UNREVIEWED','RESOLVED','IGNORED','CONFIRMED_EFFECT','CONFIRMED_NOISE']
    unique = {}
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item['reason'] = str(item.get('reason') or 'unknown')
        from .nonspeech import _finite_span
        span = _finite_span(item.get('start'),item.get('end'))
        if span:
            item['start'],item['end'] = span
        else:
            # Bad diagnostic clocks remain blocking evidence; don't crash UI.
            item.update(start=0.,end=.001,reason='invalid_source_clock',requires_review=True)
        identifier = issue_id(item,source_id)
        required = bool(item.get('requires_review',item.get('raw_withheld',item.get('withheld',False))))
        if identifier in unique:
            unique[identifier]['requires_review'] |= required
            continue
        item.update(issue_id=identifier,requires_review=required,raw_withheld=required)
        unique[identifier] = item
    items = []
    for item in sorted(unique.values(),key=lambda r:(r['start'],r['end'],r['reason'])):
        item['raw_withheld'] = item['requires_review']
        resolution = 'UNREVIEWED'
        explicit=False
        if item.get('reason') in ACKABLE_REASONS:
            for value in precedence:
                index = indexes.get(value)
                query=(index.intersects if value in {'UNREVIEWED','CONFIRMED_SPEECH'} else index.covers) if index else None
                if query and query(item['start'],item['end']):
                    resolution = value
                    explicit=True
                    break
        blocking = item['requires_review'] and resolution not in NON_BLOCKING
        item.update(resolution=resolution,blocking=blocking,withheld=blocking,
                    needs_review=blocking,
                    resolution_source='persisted_user_decision' if explicit else 'raw_diagnostic')
        if resolution in {'CONFIRMED_NOISE','CONFIRMED_EFFECT'}:
            item['human_ack']='non_speech'
        else:
            item.pop('human_ack',None)
        items.append(item)
    blockers = [r for r in items if r['blocking']]
    return dict(items=items,total_issues=len(items),resolved=sum(r['requires_review'] and not r['blocking'] for r in items),
                unresolved=len(blockers),effective_blockers=blockers,
                can_continue_translation=not blockers,can_continue_tts=not blockers)


def log_review_state(state):
    from ..utils import log
    log('[QUALITY_GATE] raw_issues=%d resolved=%d unresolved=%d effective_blockers=%d translation_allowed=%s' %
        (state['total_issues'],state['resolved'],state['unresolved'],len(state['effective_blockers']),
         str(state['can_continue_translation']).lower()), 'warn' if state['unresolved'] else 'info')
    for item in state['effective_blockers'][:3]:
        log('[QUALITY_GATE] issue_id=%s type=%s resolution=%s reason=requires_user_review' %
            (item['issue_id'],item['reason'],item['resolution']), 'warn')
