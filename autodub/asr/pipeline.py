"""Điều phối backend ASR, vá lỗ hổng, và hàm transcribe công khai."""
from __future__ import annotations

import os
import tempfile
import time
from typing import List, Optional, Tuple

from .. import speechmap
from ..srt_utils import Segment
from ..utils import log, ffprobe_duration
from .common import _max_chars_for, _take_last_marks, set_caption_options
from .detect import (
    _nonsilent_ranges, _slice_audio, confirm_speech_holes,
    drop_hallucinations,
)
from .funasr import _asr_funasr, _asr_sensevoice, FunASRResultError
from .merge import (
    apply_corrections, apply_crop_to_hole, build_initial_prompt, clip_to_uncovered,
    coverage_report, find_gaps, find_uncovered_speech_ranges, merge_new_segments,
    normalize_segments, stitch_split_utterances,
)
from .whisper import _asr_faster_whisper, _asr_whisperx


def _alignment_tokens(text, mark_count):
    """Map observed marks onto words. CJK prefers characters, then jieba words."""
    from .common import _is_cjk
    if mark_count <= 0 or not (text or "").strip():
        return None, ""
    if not _is_cjk(text):
        tokens = text.split()
        return (tokens, " ") if len(tokens) == mark_count else (None, " ")
    positions = [i for i, c in enumerate(text) if c.isalnum()]
    if not positions:
        return None, ""
    char_tokens = [
        text[i:(positions[j + 1] if j + 1 < len(positions) else len(text))]
        for j, i in enumerate(positions)
    ]
    if len(char_tokens) == mark_count:
        return char_tokens, ""
    from .screen_pack import word_spans
    spans = word_spans("".join(text[i] for i in positions))
    if len(spans) != mark_count:
        return None, ""
    tokens = []
    for start, end in spans:
        start_i = positions[start]
        end_i = positions[end] if end < len(positions) else len(text)
        tokens.append(text[start_i:end_i])
    return tokens, ""


def _target_segments(segments, marks, offset, start, end):
    """Trim context text only with an exact observed token/text alignment."""
    from dataclasses import replace
    kept = []
    for s in segments:
        a, b = s.start + offset, s.end + offset
        if start-.05 <= a < b <= end+.05:
            kept.append(replace(s, start=max(start,a), end=min(end,b)))
            continue
        aligned = [(x,y) for x,y in marks if x >= a-.001 and y <= b+.001]
        tokens, sep = _alignment_tokens(s.text, len(aligned))
        if not aligned or not tokens:
            continue
        selected = [(token,x,y) for token,(x,y) in zip(tokens,aligned)
                    if x >= start-.001 and y <= end+.001]
        if selected:
            kept.append(replace(s, start=max(start,selected[0][1]),
                                end=min(end,selected[-1][2]),
                                text=sep.join(t for t,x,y in selected)))
    return kept


def _dispatch(audio_path, backend, language, model_size, device, compute_type,
              batch_size, beam_size, initial_prompt=None, direct=False) -> Tuple[List[Segment], str]:
    b = (backend or "").lower().replace("_", "-")
    if b in ("paraformer", "funasr"):
        if direct:
            return _asr_funasr(audio_path, language, device, direct=True)
        return _asr_funasr(audio_path, language, device)
    if b == "sensevoice":
        return _asr_sensevoice(audio_path, language, device)
    if b == "faster-whisper":
        return _asr_faster_whisper(audio_path, language, model_size, device,
                                   compute_type, beam_size, initial_prompt)
    if b == "whisperx":
        return _asr_whisperx(audio_path, language, model_size, device,
                             compute_type, batch_size)
    raise ValueError(f"Backend ASR không hợp lệ: {backend!r} "
                     "(chọn: paraformer | faster-whisper | sensevoice | whisperx)")


def _rescue_gaps(audio_path: str, segs: List[Segment], duration: float,
                 backend: str, language: Optional[str], model_size: str,
                 device: str, compute_type: str, batch_size: int, beam_size: int,
                 min_gap: float, max_rounds: int, silence_db: float,
                 audio_gap_rescue: bool = True,
                  speech_gap_seconds: float = 1.2,
                  speech_silence_db: float = -42.0,
                  speech_min_silence: float = 0.35,
                  initial_prompt: Optional[str] = None,
                  marks_out: Optional[List[Tuple[float, float]]] = None,
                  fallback_backend: Optional[str] = None,
                  report_out: Optional[dict] = None,
                  force_retry: bool = False,
                  focus: Optional[Tuple[float, float]] = None,
                  max_slice: float = 20.0,
                  ) -> List[Segment]:
    """Bounded contextual recognition; only accepted cues contribute marks."""
    from collections import Counter
    from dataclasses import replace
    from .funasr import cached_funasr_speech_ranges
    from .attempts import Attempts
    ledger = Attempts(audio_path)
    report = report_out if report_out is not None else {}
    report.update(attempts=[], rounds=[], speech_ranges=None)
    try:
        speech_ranges = cached_funasr_speech_ranges(audio_path, duration) if audio_gap_rescue else None
    except InterruptedError:
        raise
    except Exception as exc:
        log(f"Speech VAD unavailable: {exc}", "warn")
        speech_ranges = None
    confirmed = speech_ranges is not None
    report['speech_ranges'] = speech_ranges
    report['detector'] = 'fsmn-vad' if confirmed else 'non_silent_candidates'
    if speech_ranges is None and audio_gap_rescue:
        try:
            speech_ranges = _nonsilent_ranges(audio_path, duration, speech_silence_db, speech_min_silence)
        except InterruptedError:
            raise
        except Exception as exc:
            log(f"Non-silent detection unavailable: {exc}", "warn")
            speech_ranges = None
    if speech_ranges is not None:
        segs = stitch_split_utterances(segs, speech_ranges, duration, speech_gap_seconds)
    energy_cache = {}

    def _speech_holes(current):
        holes = find_uncovered_speech_ranges(current, speech_ranges, duration,
            min_gap=speech_gap_seconds, edge_pad=0, subtitle_pad=0)
        pending, known_ok = [], []
        for gs, ge in holes:
            key = (round(gs, 3), round(ge, 3))
            cached = energy_cache.get(key)
            if cached is True:
                known_ok.append((gs, ge))
            elif cached is False:
                continue
            else:
                pending.append((gs, ge))
        if pending:
            kept, dropped = confirm_speech_holes(
                audio_path, pending, speech_silence_db)
            for gs, ge, db in dropped:
                energy_cache[(round(gs, 3), round(ge, 3))] = False
                log(f"VAD hole {gs:.3f}-{ge:.3f}s mean {db:.1f} dB <= {speech_silence_db} - not speech",
                    "info")
            for gs, ge in kept:
                energy_cache[(round(gs, 3), round(ge, 3))] = True
                known_ok.append((gs, ge))
        return known_ok

    def scan(current):
        if speech_ranges is not None:
            # No context in target intervals. Padding belongs only in the crop.
            holes = _speech_holes(current)
        else:
            holes = find_gaps(current, duration, min_gap=min_gap, edge_pad=0)
        if focus:
            fa, fb = float(focus[0]), float(focus[1])
            clipped = []
            for a, b in holes:
                x, y = max(a, fa), min(b, fb)
                if y - x >= 0.4:
                    clipped.append((x, y))
            if not clipped and fb - fa >= 0.4:
                has_text = any((s.text or "").strip() and s.end > fa and s.start < fb
                               for s in current)
                if not has_text:
                    clipped = [(max(0.0, fa), min(duration, fb))]
                else:
                    for a, b in find_gaps(current, duration, min_gap=0.4, edge_pad=0):
                        x, y = max(a, fa), min(b, fb)
                        if y - x >= 0.4:
                            clipped.append((x, y))
            holes = clipped
        return holes

    def missing(current):
        return sum(b-a for a,b in scan(current))

    def accept_cover(current, gs, ge, text, row=None):
        covered = apply_crop_to_hole(current, gs, ge, text)
        if covered is None or missing(covered) >= missing(current) - .05:
            return None
        if speech_ranges is not None:
            covered = stitch_split_utterances(
                covered, speech_ranges, duration, speech_gap_seconds)
        if row is not None:
            row['reason'] = 'fixed'
            row['repair_action'] = 'crop_neighbor'
        return covered

    def cached_crop_texts(gs, ge):
        texts = []
        for row in report.get('attempts') or []:
            text = (row.get('text') or '').strip()
            if text and abs(float(row.get('start', -1)) - gs) < 0.05:
                texts.append(text)
        for blob in ledger.data.values() if isinstance(ledger.data, dict) else ():
            if not isinstance(blob, dict):
                continue
            text = (blob.get('text') or '').strip()
            if not text:
                continue
            try:
                if abs(float(blob.get('start', -1)) - gs) < 0.05:
                    texts.append(text)
            except (TypeError, ValueError):
                continue
        return sorted(set(texts), key=len, reverse=True)

    seen, unavailable = set(), set()
    with tempfile.TemporaryDirectory(prefix="autodub_gap_") as tmpdir:
        for rnd in range(1, max_rounds + 1):
            # Never merge over an existing subtitle. Bound each ASR target to 20s.
            gaps = []
            slice_s = max(4.0, float(max_slice or 20.0))
            for a,b in scan(segs):
                while b-a > slice_s:
                    gaps.append((a,a+slice_s)); a += slice_s
                if b>a:
                    gaps.append((a,b))
            if not gaps:
                break
            before_s = missing(segs)
            failures, fixed, attempted = Counter(), 0, 0
            for gi, (gs, ge) in enumerate(gaps):
                covered_now = False
                for text in cached_crop_texts(gs, ge):
                    covered = accept_cover(segs, gs, ge, text)
                    if covered is not None:
                        segs = covered
                        fixed += 1
                        covered_now = True
                        report['attempts'].append(dict(
                            start=gs, end=ge, engine='crop_neighbor',
                            reason='fixed', text=text, cached=True))
                        break
                if covered_now:
                    continue
                strategies = [(backend, .35, False), (backend, 1.0, False)]
                if confirmed and backend in ('paraformer', 'funasr'):
                    strategies.append((backend, 1.0, True))
                if confirmed and ge-gs >= speech_gap_seconds and fallback_backend and fallback_backend != backend:
                    strategies.append((fallback_backend, 1.0, False))
                placed_now = False
                for engine, padding, direct in strategies:
                    if engine in unavailable:
                        continue
                    cs, ce = max(0.0, gs-padding), min(duration, ge+padding)
                    fingerprint = (round(cs,3), round(ce,3), engine, model_size,
                                   device, compute_type, batch_size, beam_size, initial_prompt, direct)
                    attempt_key = ledger.key(cs,ce,engine,language,model_size,device,
                                             compute_type,batch_size,beam_size,initial_prompt,direct)
                    if (not force_retry) and ledger.failed(attempt_key):
                        report.setdefault('skipped_previous_failures',[]).append(attempt_key)
                        continue
                    if fingerprint in seen:
                        continue
                    seen.add(fingerprint)
                    attempted += 1
                    row = dict(start=gs, end=ge, crop_start=cs, crop_end=ce, engine=engine,
                               padding=padding, direct=direct, attempt_count=sum(1 for r in report['attempts']
                               if abs(r['start']-gs)<.01 and abs(r['end']-ge)<.01)+1)
                    report['attempts'].append(row)
                    started = time.monotonic()
                    piece = os.path.join(tmpdir, 'region.wav')
                    _take_last_marks()
                    try:
                        _slice_audio(audio_path, cs, ce, piece)
                        args = (piece, engine, language, model_size, device, compute_type,
                                batch_size, beam_size, initial_prompt)
                        sub_segs, _ = _dispatch(*args, True) if direct else _dispatch(*args)
                        if engine == 'faster-whisper':
                            from .whisper import LAST_TELEMETRY
                            row['whisper'] = LAST_TELEMETRY.get()
                        local_marks = _take_last_marks(cs)
                        accepted = []
                        row['text'] = ' '.join(s.text.strip() for s in sub_segs if s.text.strip())[:500]
                        occupied = [(old.start, old.end) for old in segs]
                        for s in _target_segments(sub_segs, local_marks, cs, gs, ge):
                            placed = clip_to_uncovered(
                                s.start, s.end, gs, ge, occupied + [(a.start, a.end) for a in accepted])
                            if not s.text.strip() or placed is None:
                                continue
                            accepted.append(replace(s, start=placed[0], end=placed[1]))
                        candidate = merge_new_segments(segs, accepted)
                        if missing(candidate) < missing(segs)-.05:
                            new_ids = {id(s) for s in candidate} - {id(s) for s in segs}
                            kept = [s for s in accepted if id(s) in new_ids]
                            if marks_out is not None:
                                marks_out.extend((a,b) for a,b in local_marks
                                    if any(a >= s.start-.001 and b <= s.end+.001 for s in kept))
                            segs = candidate
                            if speech_ranges is not None:
                                segs = stitch_split_utterances(
                                    segs, speech_ranges, duration, speech_gap_seconds)
                            row['reason'] = 'fixed'
                            fixed += 1
                            placed_now = True
                            break
                        row['reason'] = 'ASR_EMPTY' if not sub_segs else 'conflict_or_context'
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        row['reason'] = getattr(exc, 'reason', 'engine_error')
                        row['error'] = str(exc)[:1500]
                        if isinstance(exc, (ImportError, ModuleNotFoundError)):
                            unavailable.add(engine)
                    finally:
                        row['wall_s'] = time.monotonic()-started
                        ledger.record(attempt_key,row)
                        _take_last_marks()
                    failures[row['reason']] += 1
                if placed_now:
                    continue
                for text in cached_crop_texts(gs, ge):
                    covered = accept_cover(segs, gs, ge, text)
                    if covered is not None:
                        segs = covered
                        fixed += 1
                        report['attempts'].append(dict(
                            start=gs, end=ge, engine='crop_neighbor',
                            reason='fixed', text=text, cached=True, after_all_pads=True))
                        break
            remaining = scan(segs)
            summary = dict(round=rnd, detected=len(gaps), fixed=fixed,
                failed=len(gaps)-fixed, remaining=len(remaining), attempts=attempted,
                unresolved_s=sum(b-a for a,b in remaining), failures=dict(failures))
            report['rounds'].append(summary)
            log(f"ASR repair: {summary}", "info")
            if not remaining:
                break
            if before_s-summary['unresolved_s'] < .05 or not attempted:
                break
    if speech_ranges is not None:
        segs = stitch_split_utterances(segs, speech_ranges, duration, speech_gap_seconds)
    report['remaining'] = scan(segs)
    report['confirmed_speech'] = confirmed
    report['unavailable_engines'] = sorted(unavailable)
    return segs


def withheld_vad_gaps(
    audio_path: str,
    cues: List[Segment],
    duration: float,
    speech_gap_seconds: float = 1.2,
    speech_silence_db: float = -42.0,
    repair_report: Optional[dict] = None,
    *,
    stitch: bool = False,
    subtitle_report=None,
    log_coverage: bool = False,
) -> Tuple[List[Segment], List[dict]]:
    """Withhold VAD speech holes ≥ ``speech_gap_seconds`` after silence confirm.

    ``stitch=True`` matches a fresh transcribe (merge split utterances first).
    Reuse of an existing SRT must pass ``stitch=False`` so keep-source clocks
    are not recut before the 1.2s gate.
    """
    repair_report = repair_report if repair_report is not None else {}
    review: List[dict] = []
    if not audio_path or not os.path.isfile(audio_path) or float(duration or 0) <= 0:
        return list(cues or []), review
    vad = repair_report.get("speech_ranges")
    if "speech_ranges" not in repair_report:
        from .funasr import cached_funasr_speech_ranges
        try:
            vad = cached_funasr_speech_ranges(audio_path, duration)
        except InterruptedError:
            raise
        except Exception as exc:
            vad = None
            repair_report["detector_error"] = str(exc)[:500]
        repair_report["speech_ranges"] = vad
    if not vad:
        return list(cues or []), review
    coverage = list(cues or [])
    if stitch:
        coverage = stitch_split_utterances(
            coverage, vad, duration, speech_gap_seconds)
    from .merge import speech_coverage_report
    metric = speech_coverage_report(coverage, vad, duration)
    repair_report["coverage"] = metric
    if log_coverage:
        log(f"Speech coverage (FSMN VAD): {metric}", "info")
    unresolved = find_uncovered_speech_ranges(
        coverage, vad, duration, min_gap=speech_gap_seconds,
        edge_pad=0, subtitle_pad=0)
    unresolved, silent = confirm_speech_holes(
        audio_path, unresolved, speech_silence_db)
    if silent:
        repair_report["silent_vad_holes"] = [
            dict(start=a, end=b, mean_db=db) for a, b, db in silent]
        if log_coverage:
            log(f"Dropped {len(silent)} VAD holes at or below "
                f"{speech_silence_db} dB", "info")
    all_unresolved = find_uncovered_speech_ranges(
        coverage, vad, duration, min_gap=0, edge_pad=0, subtitle_pad=0)
    if subtitle_report is not None:
        repair_report["coverage_breakdown"] = dict(
            subtitle_media=subtitle_report, speech=metric,
            significant_gap_s=sum(b - a for a, b in unresolved),
            subthreshold_gap_s=sum(
                b - a for a, b in all_unresolved if b - a < speech_gap_seconds),
            threshold_s=speech_gap_seconds, all_unresolved=all_unresolved)
    review = [
        dict(reason="unresolved_speech_gap", start=a, end=b,
             withheld=True, needs_review=True, threshold_s=speech_gap_seconds,
             detector="fsmn-vad")
        for a, b in unresolved
    ]
    return coverage if stitch else list(cues or []), review


def transcribe(
    audio_path: str,
    backend: str = "paraformer",
    language: Optional[str] = None,
    model_size: str = "large-v3",
    device: str = "cuda",
    compute_type: str = "float16",
    batch_size: int = 8,
    beam_size: int = 5,
    max_chars_per_line: int = 84,
    # chống mất đoạn
    rescue_gaps: bool = True,
    min_gap_seconds: float = 25.0,
    max_rescue_rounds: int = 2,
    silence_db: float = -45.0,
    audio_gap_rescue: bool = True,
    speech_gap_seconds: float = 1.2,
    speech_silence_db: float = -42.0,
    speech_min_silence: float = 0.35,
    min_coverage: float = 0.35,
    fallback_backend: Optional[str] = "faster-whisper",
    filter_hallucinations: bool = True,
    corrections=None,
    vocab_hint: Optional[str] = None,
    caption_style: Optional[str] = None,
    screen_max_chars: Optional[int] = None,
    screen_min_chars: Optional[int] = None,
    screen_max_duration: Optional[float] = None,
    screen_hard_max_chars: Optional[int] = None,
    screen_hard_max_duration: Optional[float] = None,
    screen_gap: Optional[float] = None,
    funasr_merge_length_s: Optional[float] = None,
) -> Tuple[List[Segment], str]:
    """Nhận diện phụ đề với 3 lớp chống mất đoạn. Trả về (segments, ngôn_ngữ)."""
    set_caption_options({
        "caption_style": caption_style,
        "screen_max_chars": screen_max_chars,
        "screen_min_chars": screen_min_chars,
        "screen_max_duration": screen_max_duration,
        "screen_hard_max_chars": screen_hard_max_chars,
        "screen_hard_max_duration": screen_hard_max_duration,
        "screen_gap": screen_gap,
        "funasr_merge_length_s": funasr_merge_length_s,
    })
    duration = ffprobe_duration(audio_path)
    from .long_audio import LAST_RUN, save_working
    LAST_RUN.set(None)
    initial_prompt = build_initial_prompt(corrections, vocab_hint)
    if initial_prompt:
        log(f"Mớm từ vựng cho ASR: {initial_prompt[:110]}"
            + ("..." if len(initial_prompt) > 110 else ""), "info")

    def _try(bk: str, dev: str, ctype: str):
        return _dispatch(audio_path, bk, language, model_size, dev, ctype,
                         batch_size, beam_size, initial_prompt)

    # --- Lớp 1: chạy engine chính, tự hạ cấp CPU nếu GPU lỗi ---
    active_backend = backend
    active_device = device
    active_compute_type = compute_type
    speechmap.clear_active()
    _take_last_marks()                       # xoá mốc còn sót của lần chạy trước
    try:
        segs, lang = _try(backend, device, compute_type)
    except InterruptedError:
        raise
    except Exception as e:
        if LAST_RUN.get() is not None:
            # Resume chunk checkpoints instead of recognizing the entire file
            # again with a fallback backend after one failed chunk.
            raise
        log(f"Backend '{backend}' lỗi trên {device} ({e}).", "warn")
        primary_error = e
        recovered = False
        if not isinstance(e, FunASRResultError) and not str(device or "").lower().startswith("cpu"):
            try:
                log("Thử lại bằng CPU...", "info")
                segs, lang = _try(backend, "cpu", "int8")
                active_device = "cpu"
                active_compute_type = "int8"
                recovered = True
            except Exception as e2:
                primary_error = e2
        if not recovered:
            if not fallback_backend or fallback_backend == backend:
                raise primary_error
            log(f"Chuyển sang backend dự phòng '{fallback_backend}' "
                f"vì backend chính vẫn lỗi ({primary_error}).", "warn")
            try:
                segs, lang = _try(fallback_backend, "cpu", "int8")
                active_backend = fallback_backend
                active_device = "cpu"
                active_compute_type = "int8"
            except Exception:
                segs, lang = _try(fallback_backend, device, compute_type)
                active_backend = fallback_backend

    # Bản đồ thoại phải dựng NGAY, trước normalize_segments: chính hàm đó đã
    # chia/gộp dòng theo tỉ lệ ký tự nên cần mốc thật để chia đúng chỗ.
    marks = _take_last_marks()
    if marks:
        speechmap.set_active(speechmap.SpeechMap(marks))

    max_chars = _max_chars_for(lang, max_chars_per_line)
    raw_segments = list(segs)
    save_working(audio_path, raw_segments)
    segs = normalize_segments(segs, max_chars)

    # --- Lớp 2: kiểm tra độ phủ ---
    rep = coverage_report(segs, duration)
    log(f"Subtitle/media coverage: {rep['lines']} dòng | có sub {rep['covered_s']/60:.1f}p "
        f"/ {rep['duration_s']/60:.1f}p ({rep['ratio']*100:.1f}%) | "
        f"dòng cuối ở {rep['last_end_s']/60:.1f}p", "info")

    repair_report = {}
    # --- Lớp 3: vá lỗ hổng ---
    if rescue_gaps and duration > 0:
        segs = _rescue_gaps(audio_path, raw_segments, duration, active_backend, language or lang,
                            model_size, active_device, active_compute_type, batch_size,
                            beam_size, min_gap_seconds, max_rescue_rounds,
                            silence_db, audio_gap_rescue, speech_gap_seconds,
                            speech_silence_db, speech_min_silence, initial_prompt,
                            marks_out=marks, fallback_backend=fallback_backend,
                            report_out=repair_report)
        if marks:
            speechmap.set_active(speechmap.SpeechMap(marks))
        raw_segments = list(segs)
        save_working(audio_path, raw_segments)
        segs = normalize_segments(segs, max_chars)
        rep = coverage_report(segs, duration)
        log(f"Sau khi vá: {rep['lines']} dòng | phủ {rep['ratio']*100:.1f}% | "
            f"dòng cuối ở {rep['last_end_s']/60:.1f}p", "ok")


    from .common import caption_style_is_screen
    from .screen_pack import last_review
    if rescue_gaps and caption_style_is_screen() and any(r.get('withheld') for r in last_review()):
        from .repair_clocks import rescue_caption_clocks
        raw_segments = rescue_caption_clocks(audio_path, raw_segments, marks,
            last_review(), duration, active_backend, language or lang, model_size, active_device,
            active_compute_type, batch_size, beam_size, initial_prompt, fallback_backend,
            max_chars, repair_report, _dispatch, _target_segments)
        speechmap.set_active(speechmap.SpeechMap(marks))
        segs = normalize_segments(raw_segments, max_chars)
        rep = coverage_report(segs, duration)

    # --- Lớp 4: bỏ câu bịa (Whisper "điền" quảng cáo kênh vào đoạn nhạc) ---
    if filter_hallucinations:
        segs, removed = drop_hallucinations(segs)
        if removed:
            log(f"Đã bỏ {len(removed)} dòng nghi là câu BỊA (đoạn nhạc/im lặng):",
                "warn")
            for line in removed[:5]:
                print(f"      - {line}")
            if len(removed) > 5:
                print(f"      ... và {len(removed) - 5} dòng nữa")
            rep = coverage_report(segs, duration)

    from .common import caption_style_is_screen
    from .screen_pack import ensure_complete, last_review
    review = last_review() if caption_style_is_screen() else []
    long_run = LAST_RUN.get()
    if long_run:
        repair_report['long_audio'] = long_run
        review += [dict(row, withheld=True, needs_review=True)
                   for row in long_run.get('unresolved', [])]
    save_working(audio_path, raw_segments)
    if audio_gap_rescue and 'speech_ranges' not in repair_report:
        from .funasr import cached_funasr_speech_ranges
        try:
            repair_report['speech_ranges'] = cached_funasr_speech_ranges(audio_path, duration)
        except InterruptedError:
            raise
        except Exception as exc:
            repair_report['speech_ranges'] = None
            repair_report['detector_error'] = str(exc)[:500]
    coverage_cues, vad_review = withheld_vad_gaps(
        audio_path, raw_segments or segs, duration,
        speech_gap_seconds, speech_silence_db,
        repair_report=repair_report, stitch=True,
        subtitle_report=rep, log_coverage=True)
    if repair_report.get("speech_ranges") is not None:
        raw_segments = coverage_cues
        save_working(audio_path, raw_segments)
    review += vad_review
    if repair_report:
        import json
        from pathlib import Path
        # Unique evidence per run, including successful runs.
        fd, name = tempfile.mkstemp(prefix='asr-repair-', suffix='.json',
                                   dir=os.path.dirname(os.path.abspath(audio_path)))
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(repair_report, f, ensure_ascii=False, indent=2)
        log(f"ASR repair report: {name}", "info")
    ensure_complete(segs, raw_segments, os.path.dirname(os.path.abspath(audio_path)),
                    review=review, repair_report=repair_report)

    # --- Lớp 5: sửa các lỗi nghe nhầm lặp lại (bảng trong config.yaml) ---
    if corrections:
        nfix = apply_corrections(segs, corrections)
        if nfix:
            log(f"Đã sửa {nfix} dòng theo bảng asr.corrections.", "ok")

    # Cảnh báo to nếu vẫn thiếu nghiêm trọng
    if duration > 0 and rep["tail_ratio"] < 0.9:
        log(f"CẢNH BÁO: dòng cuối chỉ ở {rep['last_end_s']/60:.1f} phút trong khi "
            f"video dài {rep['duration_s']/60:.1f} phút - phụ đề CÓ THỂ BỊ CẮT CỤT. "
            "Hãy kiểm tra file .src.srt trước khi lồng tiếng.", "err")
    elif duration > 0 and rep["ratio"] < min_coverage:
        log(f"CẢNH BÁO: chỉ {rep['ratio']*100:.1f}% thời lượng có lời. Nếu video "
            "thoại liên tục thì đây là dấu hiệu MẤT ĐOẠN - thử đổi "
            "asr.backend sang 'faster-whisper' hoặc 'paraformer'.", "warn")

    sm = speechmap.get_active()
    if sm is not None and not sm.empty:
        log(f"Bản đồ thoại: {len(sm)} mốc thời gian ký tự - dùng để chia lại phụ "
            "đề đúng lúc nhân vật nói (không nội suy qua quãng lặng).", "ok")
    else:
        log("Backend này không trả mốc thời gian từng ký tự - các bước chia lại "
            "phụ đề sẽ phải chia theo tỉ lệ, dễ lệch tiếng/hình hơn.", "warn")

    log(f"Hoàn tất: {len(segs)} dòng phụ đề (backend={backend}, lang={lang}).", "ok")
    return segs, lang
