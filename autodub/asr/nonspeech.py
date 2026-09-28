"""Human-confirmed non-speech (SFX, insects, hits). Never invents ASR text."""
from __future__ import annotations

import json
import math
import hashlib
import os
import threading
from ..utils import log
from pathlib import Path
from typing import Iterable, List, Sequence

from .long_audio import atomic_json
from .merge import merge_time_ranges

# weak_fragment_alignment is 1–2 leftover glyphs with extra speech marks, not a
# 1.2s VAD hole. Human SFX ack may drop that fragment; it must not invent text.
ACKABLE_REASONS = frozenset({
    "unresolved_speech_gap",
    "suspicious_chunk",
    "weak_fragment_alignment",
    # The recognizer may emit a glyph for an effect without any word clock.
    # A human can explicitly confirm that interval is not dialogue as well.
    "missing_speech_marks",
})
_FILE = "non_speech.json"
_STORE_LOCK = threading.RLock()


def _finite_span(start, end):
    try:
        a = float(start)
        b = float(end)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(a) or not math.isfinite(b) or b <= a:
        return None
    return round(a, 3), round(b, 3)


def compact_ranges(rows: Sequence[dict], limit: int = 0) -> List[dict]:
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            log("[REVIEW] Bỏ qua decision không phải object.", "warn")
            continue
        span = _finite_span(row.get("start"), row.get("end"))
        if not span:
            log("[REVIEW] Bỏ qua decision có mốc không hợp lệ.", "warn")
            continue
        reason = str(row.get("reason") or "unresolved_speech_gap")
        if reason not in ACKABLE_REASONS:
            reason = "unresolved_speech_gap"
        from .review import normalize_resolution
        item = {"start": span[0], "end": span[1], "reason": reason,
                "resolution": normalize_resolution(row)}
        if row.get("source_id"):
            item["source_id"] = str(row["source_id"])
        note = str(row.get("note") or "").strip()
        if note:
            item["note"] = note[:200]
        out.append(item)
        if limit and len(out) >= limit:
            break
    # Last explicit decision for an exact region wins. Different sources never merge.
    exact = {(r.get("source_id",""),r["start"],r["end"]):r for r in out}
    # Keep decision boundaries so editing/revoking one region does not leave a
    # previously merged broad confirmation behind. CoverageIndex unions spans
    # for queries without destroying the persisted human decisions.
    return sorted(exact.values(),key=lambda r:(r.get("source_id",""),r["resolution"],r["start"],r["end"]))


def resolve_dir(anchor) -> Path:
    path = Path(anchor).resolve()
    if path.is_file():
        path = path.parent
    if path.name.startswith("caption-review-"):
        path = path.parent
    return path


def non_speech_path(anchor) -> Path:
    return resolve_dir(anchor) / _FILE


def _read_json(path, default, strict=False):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        log(f"[REVIEW] Không đọc được {path.name}: {exc}", "warn")
        backup = path.with_suffix(path.suffix + '.bak')
        if backup.is_file():
            try:
                data = json.loads(backup.read_text(encoding='utf-8-sig'))
                log(f"[REVIEW] Dùng bản lưu hợp lệ {backup.name}.", "warn")
                return data
            except (OSError, ValueError):
                pass
        if strict:
            raise ValueError(f"Review state lỗi; giữ nguyên file để phục hồi: {path}") from exc
        return default


def source_context(anchor):
    path=resolve_dir(anchor)/'review_source.json'
    data = _read_json(path, {})
    if path.exists() and (not isinstance(data,dict) or not data.get('source_id')):
        return dict(source_id='invalid-source-context',require_asr=True,allow_legacy=False)
    return data if isinstance(data,dict) else {}


def bind_source(anchor, media_path, span=None):
    """Stat identity is O(1); never hash an hours-long movie on review/reload."""
    media = Path(media_path).resolve()
    stat = media.stat()
    identity = dict(path=os.path.normcase(str(media)),size=stat.st_size,mtime_ns=stat.st_mtime_ns,
                    span=span or {})
    source_id = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:24]
    root = resolve_dir(anchor)
    with _STORE_LOCK:
        old = source_context(root)
        if old.get('source_id') == source_id:
            return old
        root.mkdir(parents=True,exist_ok=True)
        data = dict(source_id=source_id,identity=identity,
                    legacy_source_id=old.get('legacy_source_id',source_id),
                    require_asr=bool(old),allow_legacy=not bool(old))
        atomic_json(root/'review_source.json',data)
        return data


def _decision_rows(payload, strict=False):
    if isinstance(payload,dict):
        rows = payload.get('ranges',payload.get('gaps',[]))
    else:
        rows = payload
    if not isinstance(rows,list):
        log('[REVIEW] Schema decisions không hợp lệ; giữ file gốc.', 'warn')
        if strict:
            raise ValueError('Review decisions phải là danh sách.')
        return []
    return compact_ranges(rows)


def load_non_speech(anchor) -> List[dict]:
    rows = _decision_rows(_read_json(non_speech_path(anchor), []))
    context = source_context(anchor)
    current = context.get('source_id')
    if not current:
        return rows
    return [r for r in rows if r.get('source_id',context.get('legacy_source_id'))==current]


def save_non_speech(anchor, ranges: Iterable[dict], extra=None) -> List[dict]:
    # Serialize the complete read/merge/atomic-write transaction, not just rename.
    with _STORE_LOCK:
        path = non_speech_path(anchor)
        previous = _read_json(path, [], strict=True)
        rows = _decision_rows(previous,strict=True)
        context = source_context(anchor)
        current = context.get('source_id')
        for row in rows:
            if current and not row.get('source_id'):
                row['source_id']=context.get('legacy_source_id',current)
        incoming = compact_ranges(list(ranges or []))
        for row in incoming:
            if current:
                row['source_id']=current
        merged = compact_ranges(rows+incoming)
        payload = dict(version=2,kind='human_non_speech',ranges=merged)
        if extra:
            payload.update({k:v for k,v in extra.items() if k not in {'version','kind','ranges'}})
        if path.exists():
            atomic_json(path.with_suffix(path.suffix+'.bak'),previous)
        atomic_json(path,payload)
        # The response is based on the committed file, never an optimistic cache.
        if _read_json(path,None,strict=True)!=payload:
            raise OSError('Review readback không khớp nội dung đã lưu.')
        return load_non_speech(anchor)


def effective_review(anchor, rows, emit_log=False):
    from .review import review_state,log_review_state
    source_id=source_context(anchor).get('source_id',str(resolve_dir(anchor)))
    state=review_state(rows,load_non_speech(anchor),source_id)
    if emit_log:
        log_review_state(state)
    return state


def save_latest_review(anchor, rows, review_dir=''):
    root=resolve_dir(anchor)
    with _STORE_LOCK:
        context=source_context(root)
        atomic_json(root/'review_latest.json',dict(version=1,source_id=context.get('source_id',''),
                    rows=rows,review_dir=str(review_dir)))
        if context.get('require_asr'):
            context['require_asr']=False
            atomic_json(root/'review_source.json',context)


def load_latest_review(anchor):
    root=resolve_dir(anchor)
    context=source_context(root)
    snapshot=root/'review_latest.json'
    data=_read_json(snapshot,{})
    if isinstance(data,dict) and isinstance(data.get('rows'),list):
        if data.get('source_id','')==context.get('source_id',''):
            return data
    if snapshot.exists() and (not isinstance(data,dict) or not isinstance(data.get('rows'),list)):
        # A present-but-corrupt latest state is not a legacy project. Falling
        # back to an older empty report could silently permit missing speech.
        return dict(rows=[dict(start=0,end=.001,reason='review_state_corrupt',withheld=True)],
                    review_dir='',source_id=context.get('source_id',''))
    if context.get('require_asr') or (context and not context.get('allow_legacy')):
        return {}
    # One-time legacy discovery on restart; subsequent loads use the small pointer.
    dirs=sorted(root.glob('caption-review-*'),key=lambda p:p.stat().st_mtime,reverse=True)
    for directory in dirs:
        if not (directory/'source.srt').is_file():
            continue
        rows=_read_json(directory/'review.json',None)
        if not isinstance(rows,list):
            rows=_read_json(directory/'unresolved.json',None)
            if isinstance(rows,list):
                rows=[dict(r,withheld=True) for r in rows if isinstance(r,dict)]
        if isinstance(rows,list):
            save_latest_review(root,rows,directory.resolve())
            return dict(rows=rows,review_dir=str(directory.resolve()))
    return {}


def mark_review_prepared(anchor, rows, review_dir):
    with _STORE_LOCK:
        save_latest_review(anchor, rows, review_dir)
        path=resolve_dir(anchor)/'review_latest.json'
        data=_read_json(path,{},strict=True)
        data['prepared']=True
        atomic_json(path,data)


def covered_by(start: float, end: float, accepted: Sequence[dict],
               min_ratio: float = 0.9, pad: float = 0.05) -> bool:
    span = _finite_span(start, end)
    if not span:
        return False
    a, b = span
    duration = b - a
    windows = merge_time_ranges(
        [(item["start"] - pad, item["end"] + pad) for item in compact_ranges(accepted)],
        merge_gap=0,
    )
    hit = 0.0
    for lo, hi in windows:
        x, y = max(a, lo), min(b, hi)
        if y > x:
            hit += y - x
    return hit / duration >= min_ratio


def filter_withheld_rows(rows: Sequence[dict], accepted: Sequence[dict]) -> List[dict]:
    from .review import review_state
    return review_state(rows,accepted)['items']


def remaining_gaps(rows: Sequence[dict], accepted: Sequence[dict]) -> List[dict]:
    from .screen_pack import compact_review_gaps
    from .review import review_state
    return compact_review_gaps(review_state(rows,accepted)['effective_blockers'])
