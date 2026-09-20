"""Rescue withheld caption clocks without changing recognized source words."""
import os
import tempfile
import time

from .. import speechmap
from ..utils import log
from .common import _take_last_marks
from .detect import _slice_audio
from .merge import normalize_segments
from .screen_pack import last_review

ZERO_FIX_STOP = 4


def rescue_caption_clocks(audio_path, source, marks, review, duration, backend,
                          language, model_size, device, compute_type, batch_size,
                          beam_size, prompt, fallback, max_chars, report, dispatch,
                          target_segments):
    """Each source window gets at most one contextual and one direct attempt.

    Accept only the exact original spoken text with observed timestamps and a
    pack free of withheld fragments. No invented alignment or dropped words.
    """
    lexical = lambda text: ''.join(c.lower() for c in text if c.isalnum())
    from .attempts import Attempts
    ledger = Attempts(audio_path)
    # Clock conflicts and unknown words are review/timeline tasks, not missing
    # speech. Recognition rescue belongs to confirmed uncovered VAD gaps.
    blocked = sorted((r for r in review if r.get('withheld') and
                      r.get('reason') in ('missing_speech_marks','weak_fragment_alignment','word_crosses_pause')),
                     key=lambda r:r['start'])
    if report.get('clock_attempts') and not any(r.get('reason') == 'fixed' for r in report['clock_attempts']):
        report['clock_stop_reason'] = 'previous_pass_zero_fixes'
        return source
    source = sorted(source, key=lambda s: (s.start,s.end))
    # Whole source cues plus a neighbor supply complete words across cue edges.
    windows = []
    for row in blocked:
        indices = [i for i,s in enumerate(source) if s.start < row['end'] and s.end > row['start']]
        if not indices:
            continue
        lo, hi = max(0,min(indices)-1), min(len(source),max(indices)+2)
        if windows and lo <= windows[-1][1] and source[hi-1].end-source[windows[-1][0]].start <= 28:
            windows[-1] = (windows[-1][0],max(hi,windows[-1][1]))
        elif (lo,hi) not in windows:
            windows.append((lo,hi))
    unavailable = set(report.get('unavailable_engines',[]))
    rows = report.setdefault('clock_attempts',[])
    consumed = set()
    repaired = []
    current_marks = list(marks)
    original_sm = speechmap.get_active()
    unproductive = 0
    try:
        with tempfile.TemporaryDirectory(prefix='asr-clock-') as td:
            for lo,hi in windows:
                if unproductive >= 12:
                    report['clock_stop_reason'] = '12_consecutive_windows_without_improvement'
                    break
                if any(i in consumed for i in range(lo,hi)):
                    continue
                group = source[lo:hi]
                start,end = group[0].start, max(s.end for s in group)
                if end-start > 28 or end <= start:
                    continue
                cs,ce = max(0,start-.5), min(duration,end+.5)
                expected = lexical(''.join(s.text for s in group))
                strategies = [(backend,False)]
                if backend in ('paraformer','funasr'):
                    strategies.append((backend,True))
                # Do not dispatch Whisper for display/timeline warnings.
                piece = os.path.join(td,'clock.wav')
                try:
                    _slice_audio(audio_path,cs,ce,piece)
                except InterruptedError:
                    raise
                except Exception as exc:
                    rows.append(dict(start=start,end=end,reason='extraction_error',error=str(exc)[:500]))
                    continue
                for engine,direct in strategies:
                    if engine in unavailable:
                        continue
                    key=ledger.key(cs,ce,engine,language,model_size,device,compute_type,
                                   batch_size,beam_size,prompt,direct)
                    if ledger.failed(key):
                        continue
                    row = dict(start=start,end=end,engine=engine,direct=direct)
                    rows.append(row)
                    started=time.monotonic()
                    _take_last_marks()
                    try:
                        args=(piece,engine,language,model_size,device,compute_type,batch_size,beam_size,prompt)
                        subs,_ = dispatch(*args,True) if direct else dispatch(*args)
                        fresh_marks = _take_last_marks(cs)
                        proposed = target_segments(subs,fresh_marks,cs,start,end)
                        if not proposed or lexical(''.join(s.text for s in proposed)) != expected:
                            row['reason'] = 'text_disagreement'
                            continue
                        if not fresh_marks:
                            row['reason'] = 'ASR_TEXT_NO_TIMESTAMP'
                            continue
                        kept_marks = [(a,b) for a,b in fresh_marks if a >= start-.001 and b <= end+.001]
                        speechmap.set_active(speechmap.SpeechMap(kept_marks))
                        normalize_segments(proposed,max_chars)
                        if any(r.get('withheld') for r in last_review()):
                            row['reason'] = 'clock_review_unresolved'
                            continue
                        current_marks = [(a,b) for a,b in current_marks if b <= start or a >= end]
                        current_marks.extend(kept_marks)
                        repaired.extend(proposed)
                        consumed.update(range(lo,hi))
                        row['reason'] = 'fixed'
                        break
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        row['reason'] = getattr(exc,'reason','engine_error')
                        row['error'] = str(exc)[:1500]
                        if isinstance(exc,ImportError):
                            unavailable.add(engine)
                    finally:
                        row['wall_s']=time.monotonic()-started
                        ledger.record(key,row)
                        _take_last_marks()
                unproductive = 0 if any(i in consumed for i in range(lo,hi)) else unproductive + 1
                if (len(rows) >= ZERO_FIX_STOP
                        and not any(r.get('reason') == 'fixed' for r in rows)):
                    report['clock_stop_reason'] = 'zero_fixes_roi'
                    break
                if len(rows) % 10 == 0:
                    log(f"Caption clock rescue: {len(rows)} attempts, "
                        f"{sum(r.get('reason')=='fixed' for r in rows)} fixed windows", "info")
    finally:
        speechmap.set_active(original_sm)
    marks[:] = sorted(set(current_marks))
    report['unavailable_engines'] = sorted(unavailable)
    log(f"Caption clock rescue: {len(rows)} attempts, "
        f"{sum(r.get('reason')=='fixed' for r in rows)} fixed windows", "info")
    return sorted([s for i,s in enumerate(source) if i not in consumed]+repaired,
                  key=lambda s:(s.start,s.end))
