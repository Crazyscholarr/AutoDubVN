"""Chạy pipeline 4 bước (ASR -> dịch -> TTS -> render) trong luồng nền.

Đây là bản GUI của main.py: cùng các bước nhưng đọc/ghi dữ liệu qua project
của giao diện (rows), có tiến độ %, có huỷ giữa chừng và có cắt đoạn (span).
"""
from __future__ import annotations
from ..semantic import copy_metadata, restore_metadata, sync_cue_text

import hashlib
import math
import os
import json
import shutil
import time
import traceback
from dataclasses import replace
from typing import Dict, List, Optional

from .. import srt_utils, overlays, speechmap
from ..timeline import MAX_START_DRIFT_SECONDS, resolve_sync_mode, summarize
from ..srt_utils import Segment
from ..utils import ffprobe_duration, start_process_log
from ..media_clock import apply_picture_clock, refresh_project_clocks, sidecar_path
from ..video.sync_check import SyncCheckFailed, assert_sync_allows_render
from .state import (HERE, STATE, PROJECTS, _LOCK, current_cancel_event,
                    bump_rev, _log, _progress, _find)
from .helpers import (
    _call_filtered, _find_existing_dub_audio, _fmt_span_range, _fmt_span_time,
)
from .config_api import _load_cfg, _translation_api_params
from .projects import (get_project, _save_project_state, _active_media_span, _run_stem_for_project,
                       _project_rows_for_span, _segments_from_rows,
                       _rows_from_segments, _sync_project_rows_for_span,
                       _load_local_rows_from_srt, _polish_project_vi,
                       _finalize_project_vi,
                       _split_project_vi_on_punctuation,
                       _prepare_project_src_for_translation,
                       _render_project_for_span,
                       _load_existing_segments_into_project)
from .render import render_with_layers, render_with_layers_chunked


def _translate_with_source_retry(segs, translate, on_retry):
    """Retry from immutable source clocks/text; validated work resumes through cache."""
    from ..translate.cache import TranslationIncomplete
    source = [replace(s) for s in segs]
    for attempt in range(2):
        try:
            translate(segs)
            return
        except TranslationIncomplete as exc:
            # The translator mutates completed cues before it raises. Feeding
            # those Vietnamese cues back as Chinese input corrupts context/cache.
            segs[:] = [replace(s) for s in source]
            if attempt:
                raise
            on_retry(exc)


def _asr_transcribe_kwargs(a: Dict) -> Dict:
    """Tham số ASR lấy từ config.yaml; thiếu key thì dùng mặc định an toàn."""
    return {
        "backend": a.get("backend", "paraformer"),
        "language": a.get("source_language"),
        "model_size": a.get("model_size", "large-v3"),
        "device": a.get("device", "cuda"),
        "compute_type": a.get("compute_type", "float16"),
        "batch_size": a.get("batch_size", 16),
        "rescue_gaps": a.get("rescue_gaps", True),
        "min_gap_seconds": a.get("min_gap_seconds", 25),
        "max_rescue_rounds": a.get("max_rescue_rounds", 2),
        "silence_db": a.get("silence_db", -45),
        "audio_gap_rescue": a.get("audio_gap_rescue", True),
        "speech_gap_seconds": a.get("speech_gap_seconds", 1.2),
        "speech_silence_db": a.get("speech_silence_db", -42.0),
        "speech_min_silence": a.get("speech_min_silence", 0.35),
        "min_coverage": a.get("min_coverage", 0.35),
        "fallback_backend": a.get("fallback_backend", "faster-whisper"),
        "hub": a.get("hub"),
        "filter_hallucinations": a.get("filter_hallucinations"),
        "corrections": a.get("corrections"),
        "vocab_hint": a.get("vocab_hint"),
        "caption_style": a.get("caption_style"),
        "screen_max_chars": a.get("screen_max_chars"),
        "screen_min_chars": a.get("screen_min_chars"),
        "screen_max_duration": a.get("screen_max_duration"),
        "screen_hard_max_chars": a.get("screen_hard_max_chars"),
        "screen_hard_max_duration": a.get("screen_hard_max_duration"),
        "screen_gap": a.get("screen_gap"),
        "funasr_merge_length_s": a.get("funasr_merge_length_s"),
    }


def _tts_retry_kwargs(tc: Dict) -> Dict:
    """Số lần thử lại TTS lấy từ config, không để mặc định cứng trong code."""
    retries = tc.get("max_retries", 4)
    delay = tc.get("retry_delay", 1.2)
    try:
        retries = max(1, int(retries or 4))
    except (TypeError, ValueError):
        retries = 4
    try:
        delay = max(0.2, float(delay or 1.2))
    except (TypeError, ValueError):
        delay = 1.2
    return {"max_retries": retries, "retry_base_delay": delay}


_TTS_SIGNATURE_KEYS = (
    "engine", "voice_mode", "narrator_voice", "narrator_pitch", "base_rate",
    "max_speed", "min_gap", "max_overhang_seconds", "sync_offset_seconds",
    "lock_av", "max_start_drift_seconds", "trim_overflow",
)


def _tts_input_signature(local_rows: List[Dict], opt: Dict, tc: Dict,
                         tr: Dict, duration: float) -> str:
    """Fingerprint exactly the project state that produced ``dub.wav``.

    Render is intentionally a read-only consumer of a successful TTS run.
    Binding text, clocks, placement and effective voice settings prevents a
    manually edited subtitle (or changed voice) from being muxed with stale
    audio while still looking like a successful export.
    """
    cues = []
    for row in local_rows or []:
        cues.append([
            round(float(row.get("start", 0.0) or 0.0), 3),
            round(float(row.get("end", 0.0) or 0.0), 3),
            str(row.get("vi") or row.get("src") or ""),
            (None if row.get("placed") is None
             else round(float(row.get("placed") or 0.0), 3)),
            (None if row.get("voice_dur") is None
             else round(float(row.get("voice_dur") or 0.0), 3)),
            (None if row.get("speed") is None
             else round(float(row.get("speed") or 0.0), 4)),
        ])
    settings = {
        key: opt.get(key, tc.get(key))
        for key in _TTS_SIGNATURE_KEYS
    }
    settings["sync_mode"] = resolve_sync_mode(opt, tc)
    settings["trim_silence"] = tc.get("trim_silence", True)
    settings["semantic_groups"] = tc.get(
        "semantic_groups", tr.get("semantic_translation", True))
    settings["vieneu_voice"] = (
        opt.get("narrator_voice")
        if str(settings.get("engine") or "").lower() == "vieneu"
        else tc.get("vieneu_voice")
    )
    settings["vieneu_voices"] = tc.get("vieneu_voices")
    settings["vieneu_options"] = tc.get("vieneu_options")
    settings["capcut_options"] = tc.get("capcut_options")
    payload = {
        "version": 1,
        "duration": round(float(duration or 0.0), 3),
        "cues": cues,
        "settings": settings,
    }
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _assert_render_ready(local_rows: List[Dict], opt: Dict, tc: Dict,
                         tr: Dict, duration: float, report: Dict) -> None:
    """Require render text/settings and sync evidence from the same TTS run."""
    expected = str(opt.get("last_tts_signature") or "")
    if not expected:
        raise RuntimeError(
            "Chưa có dấu xác nhận giọng đọc cho phụ đề hiện tại. "
            "Hãy chạy 'Dựng giọng đọc' trước khi xuất video."
        )
    current = _tts_input_signature(local_rows, opt, tc, tr, duration)
    if current != expected:
        raise RuntimeError(
            "Phụ đề, timestamp hoặc cấu hình giọng đã thay đổi sau lần dựng "
            "giọng gần nhất. Hãy dựng lại giọng đọc rồi mới xuất video."
        )
    checked = str((report or {}).get("tts_signature") or "")
    effective_report = report or {}
    if checked != expected:
        effective_report = {
            "verdict": "fail",
            "block_render": True,
            "message": (
                "Chưa có kết quả kiểm tra khớp hình cho đúng track giọng hiện "
                "tại. Hãy chạy lại Dựng giọng đọc/Kiểm tra khớp hình."
            ),
        }
    assert_sync_allows_render(
        effective_report, force_export=bool(opt.get("force_export")))


def _tu_dich_lai_dong_tieng_trung(stage_name: str, segs: List[Segment],
                                  local_rows: List[Dict], dich_nhom,
                                  enabled: bool = True,
                                  raise_on_fail: bool = True,
                                  attempts: int = 2,
                                  log=_log) -> bool:
    """Tự dịch lại đúng những dòng còn tiếng Trung, thay vì dừng cả pipeline.

    Dòng còn tiếng Trung sinh ra khi một lô dịch (nhất là chế độ browser) trả
    thiếu vài dòng lẻ - trước đây tới bước TTS/render mới bị chặn bằng
    RuntimeError và người dùng phải tự chạy lại bước Dịch. Giờ gom đúng các
    dòng bẩn, gọi lại provider đang cấu hình (qua `dich_nhom`) tối đa
    `attempts` lần; sửa được dòng nào thì ghi ngược vào `local_rows` để call
    site lưu lại project/file.

    Trả về True nếu có dòng được sửa. Nếu vẫn còn dòng bẩn:
      - raise_on_fail=True  -> ném RuntimeError (chốt chặn như hành vi cũ,
        để không bao giờ đọc to tiếng Trung trong video);
      - raise_on_fail=False -> chỉ cảnh báo rồi đi tiếp (dùng ngay sau bước
        Dịch, vì bước TTS phía sau sẽ thử thêm lần nữa rồi mới chặn).
    """
    from dataclasses import replace
    from ..translate.cjk_residue import apply_residual_repairs, leftover_cjk_indices
    from ..translate.parse import _contains_cjk

    def _dirty() -> List[int]:
        return leftover_cjk_indices(segs)

    def _liet_ke(indices: List[int]) -> str:
        sample = ", ".join(str(segs[i].index) for i in indices[:8])
        more = "" if len(indices) <= 8 else f", ... +{len(indices) - 8}"
        return sample + more

    def _row(i: int) -> Dict:
        return local_rows[i] if i < len(local_rows) else {}

    def _sources(indices=None):
        idxs = range(len(segs)) if indices is None else indices
        return [str(_row(i).get("src") or "").strip() for i in idxs]

    def _retry_source(i: int) -> str:
        src = str(_row(i).get("src") or "").strip()
        current = (segs[i].text or "").strip()
        if src and _contains_cjk(src):
            return src
        return current

    def _ghi_nguoc(indices=None) -> None:
        for i in (indices if indices is not None else range(len(segs))):
            if i < len(local_rows):
                local_rows[i]["vi"] = segs[i].text
                copy_metadata(segs[i], local_rows[i])

    changed = False
    repaired = apply_residual_repairs(segs, sources=_sources())
    if repaired:
        _ghi_nguoc()
        changed = True
        log(f"Đã thay {repaired} từ Trung còn sót trong câu Việt trước khi {stage_name}.",
            "ok")

    if not _dirty():
        return changed

    if enabled:
        for attempt in range(1, max(1, int(attempts)) + 1):
            dirty = _dirty()
            if not dirty:
                break
            log(f"Còn {len(dirty)} dòng tiếng Trung trước khi {stage_name} "
                f"(dòng {_liet_ke(dirty)}) -> tự dịch lại, "
                f"lần {attempt}/{attempts}...", "warn")
            work = [replace(segs[i], text=_retry_source(i)) for i in dirty]
            try:
                dich_nhom(work)
            except Exception as e:
                log(f"Tự dịch lại thất bại: {str(e)[:200]}", "warn")
            else:
                for piece, i in zip(work, dirty):
                    segs[i].text = piece.text
                    copy_metadata(piece, segs[i])
            apply_residual_repairs(
                [segs[i] for i in dirty], sources=_sources(dirty))
            _ghi_nguoc(dirty)
            if len(_dirty()) < len(dirty):
                changed = True

    still = _dirty()
    if still:
        if raise_on_fail:
            note = (" Chương trình đã tự dịch lại nhưng vẫn chưa sạch."
                    if enabled else "")
            raise RuntimeError(
                f"Bản dịch còn tiếng Trung ở dòng {_liet_ke(still)}.{note} "
                f"Hãy chạy lại bước Dịch trước khi {stage_name}, hoặc sửa tay "
                "các dòng này trong bảng Sửa từng dòng.")
        log(f"Vẫn còn {len(still)} dòng tiếng Trung (dòng {_liet_ke(still)}) - "
            "tạm giữ tiếng gốc, bước sau sẽ tự dịch lại lần nữa.", "warn")
    elif changed:
        log(f"Đã tự dịch lại xong các dòng còn tiếng Trung trước khi {stage_name}.", "ok")
    return changed


def _hydrate_vi_onto_source(segs: List[Segment], vi_segments: List[Segment],
                            local_rows: List[Dict]) -> int:
    """Copy a matching-length VI file onto source cues after a local residue repair."""
    from ..translate.reuse import hydrate_equal_length
    return hydrate_equal_length(segs, vi_segments, local_rows)


def _gan_metadata_dich(segs: List[Segment], local_rows: List[Dict],
                       cache_path: str) -> bool:
    """Gắn lại semantic_group từ sidecar để finalize không chia lại 1-1."""
    if not segs or not cache_path:
        return False
    if not restore_metadata(segs, cache_path):
        return False
    for row, s in zip(local_rows, segs):
        copy_metadata(s, row)
    return True


def _bao_dam_ban_do_thoai(asr_mod, out_dir: str, stem: str, pr: Dict,
                          span: Dict, effective_duration: float):
    """Nạp/dựng BẢN ĐỒ THOẠI cho job hiện tại (mốc thời gian từng ký tự).

    Người dùng GUI hay bấm riêng từng bước (chỉ Dịch, chỉ Dựng giọng đọc), nên
    bước nào cũng phải tự bảo đảm có bản đồ - nếu không, đúng bước đó sẽ chia
    lại phụ đề theo tỉ lệ và làm voice lệch khỏi hình.
    """
    if speechmap.get_active() is not None:
        want = os.path.abspath(speechmap.default_path(out_dir, stem))
        bound = speechmap.active_path()
        if bound is None or os.path.abspath(bound) == want:
            return speechmap.get_active()
        speechmap.clear_active()
    try:
        return asr_mod.ensure_speech_map(
            out_dir, stem, video_path=pr.get("video"),
            trim_start=span["start"] if span.get("enabled") else 0.0,
            trim_duration=effective_duration if span.get("enabled") else None)
    except Exception as e:
        _log(f"Không nạp được bản đồ thoại: {e}", "warn")
        return None


def _kiem_tra_khop_hinh(vid_mod, pr: Dict, span: Dict, segs: List[Segment],
                        dub_wav: Optional[str], out_dir: str, stem: str,
                        duration: float, job_id: int, places=None,
                        native_dub_duration: Optional[float] = None) -> Dict:
    """Cắt 3 đoạn mẫu + đo lệch, lưu vào project để GUI hiện nút nghe."""
    if not dub_wav or not os.path.exists(dub_wav):
        raise RuntimeError(
            "Chưa có track giọng Việt. Hãy dựng giọng đọc trước khi kiểm tra khớp.")
    if not segs:
        raise RuntimeError("Chưa có phụ đề Việt để đối chiếu mốc hình.")
    max_drift = None
    if places:
        max_drift = max((getattr(p, "drift", 0.0) for p in places), default=0.0)
    _log("Đang cắt 3 đoạn mẫu đầu/giữa/cuối để nghe khớp hình (thường dưới 1 phút)...",
         "step")
    report = vid_mod.check_dub_sync(
        pr["video"], dub_wav, segs,
        out_dir=out_dir,
        duration=duration,
        source_offset=float(span.get("start") or 0.0),
        stem=stem,
        make_previews=True,
        placements_max_drift=max_drift,
        native_dub_duration=native_dub_duration,
    )
    compact = {k: report.get(k) for k in (
        "verdict", "ok", "block_render", "max_drift_s", "duration_short_s",
        "speech_hit_ratio", "speech_checked", "speech_hits", "early_votes",
        "message", "previews", "checked_at", "report_path",
        "native_dub_duration_s", "dub_duration_s")}
    tts_signature = str(
        pr.setdefault("options", {}).get("last_tts_signature") or "")
    if tts_signature:
        compact["tts_signature"] = tts_signature
        report["tts_signature"] = tts_signature
    pr.setdefault("options", {})["last_sync_check"] = compact
    try:
        _save_project_state(pr)
    except Exception:
        pass
    bump_rev(job_id)
    kind = "ok" if report.get("verdict") == "ok" else (
        "warn" if report.get("verdict") == "warn" else "err")
    _log(report.get("message") or "Đã kiểm tra khớp hình.", kind)
    return report


def _keo_dong_ho_hinh(pr: Dict, segs: List[Segment], span: Dict,
                      out_dir: str, stem: str, audio_wav: str,
                      reset: bool) -> Dict:
    """Kéo timestamp sang PTS hình — một lần, chống nhân đôi."""
    wav = audio_wav if audio_wav and os.path.exists(audio_wav) else None
    return apply_picture_clock(
        segs,
        video_path=pr.get("video") or "",
        pr=pr,
        audio_wav=wav,
        trust_wav=bool(wav) and not span.get("enabled"),
        sidecar=sidecar_path(out_dir, stem),
        speechmap_path=speechmap.default_path(out_dir, stem),
        reset=reset,
        log_fn=_log,
    )


def _ghi_moc_sau_keo(local_rows: List[Dict], segs: List[Segment]) -> None:
    for row, s in zip(local_rows or [], segs or []):
        row["start"] = round(float(s.start), 3)
        row["end"] = round(float(s.end), 3)
        if s.end <= s.start:
            row["end"] = round(row["start"] + 0.01, 3)
        row.pop("placed", None)


def ack_caption_review(job_id: int, ranges, continue_pipeline: bool = False) -> Dict:
    """Record human-confirmed non-speech, then optionally resume without re-ASR."""
    from pathlib import Path
    from ..asr.nonspeech import load_non_speech, remaining_gaps, save_non_speech
    from ..asr.screen_pack import load_review_gaps
    from .helpers import _path_under

    from .review import review_for_job
    from ..asr.review import log_review_state
    job = _find(job_id)
    if not job:
        return {"ok": False, "error": "no job", "code": 404}
    pr = get_project(job_id)
    state = review_for_job(job, pr)
    root = state['review_dir']
    if not root:
        return {"ok": False, "error": "Chưa có thư mục kiểm tra hợp lệ.", "code": 400}
    # Explicit decisions are committed first; failure propagates to HTTP (no false success).
    acked = save_non_speech(root, ranges or [], extra={"job_id": job_id})
    state = review_for_job(job, pr)
    log_review_state(state)
    remaining = [dict(start=r['start'],end=r['end'],reason=r['reason'],issue_id=r['issue_id'])
                 for r in state['effective_blockers']]
    payload = {"ok": True, "acked": acked, "remaining": remaining, "review_dir": root,
               "continue_pipeline": False,
               **{k:state[k] for k in ('total_issues','resolved','unresolved','effective_blockers',
                                      'can_continue_translation','can_continue_tts')}}
    _log(f"Đã lưu quyết định; còn {state['unresolved']} blocker hiệu lực.",
         "warn" if remaining else "ok")
    if not continue_pipeline:
        return payload
    if remaining:
        payload.update(ok=False, code=409,
                       error="Còn đoạn chưa xác nhận. Nghe hết rồi mới tiếp tục dịch.")
        return payload
    if state.get('prepared'):
        payload['continue_pipeline'] = True
        return payload
    pr = get_project(job_id)
    if not pr:
        return {"ok": False, "error": "Chưa có dự án video.", "code": 404}
    span = _active_media_span(pr)
    stem, _raw = _run_stem_for_project(pr, span)
    out_dir = os.path.join(HERE, "output", stem)
    os.makedirs(out_dir, exist_ok=True)
    packed = Path(root) / "packed.needs-review.srt"
    source = Path(root) / "source.srt"
    packed_segs = srt_utils.load_srt_file(str(packed)) if packed.is_file() else []
    source_segs = srt_utils.load_srt_file(str(source)) if source.is_file() else []
    # Empty is a valid filtered result, not permission to resurrect raw ASR
    # hallucinations. Raw fallback is only for legacy artifacts without a pack.
    chosen = packed if packed.is_file() else source
    chosen_segs = packed_segs if packed.is_file() else source_segs
    if not chosen_segs:
        return {"ok": False, "error": "Không có phụ đề Trung để tiếp tục.", "code": 400}
    asr_srt = os.path.join(out_dir, f"{stem}.asr.srt")
    src_srt = os.path.join(out_dir, f"{stem}.src.srt")
    shutil.copy2(chosen, asr_srt)
    shutil.copy2(chosen, src_srt)
    smap = Path(root) / "speechmap.json"
    if smap.is_file():
        shutil.copy2(smap, speechmap.default_path(out_dir, stem))
    # Review artifacts use clip-local clocks. Preserve edits only for an
    # unambiguous, unchanged cue; positional matching can reuse a wrong translation.
    def cue_key(row):
        return (round(float(row['start']), 3), round(float(row['end']), 3),
                row.get('src', ''))

    previous = {}
    for row in _project_rows_for_span(pr, span):
        previous.setdefault(cue_key(row), []).append(row)
    local_rows = _load_local_rows_from_srt(src_srt)
    for index, row in enumerate(local_rows):
        matches = previous.get(cue_key(row), [])
        if len(matches) == 1:
            local_rows[index] = dict(matches[0])
    _sync_project_rows_for_span(pr, span, local_rows)
    from ..asr.nonspeech import mark_review_prepared
    mark_review_prepared(root, state['items'], root)
    bump_rev(job_id)
    with _LOCK:
        job["status"] = "chờ"
        job["note"] = "Đã xác nhận tạp âm, không phải thoại."
        job.pop("result_status", None)
        job.pop("review_gaps", None)
    _log(f"Giữ {len(chosen_segs)} dòng Trung đã nhận dạng; các đoạn tạp âm để trống, "
         "không bịa chữ. Tiếp tục dịch/TTS.", "ok")
    payload.update(continue_pipeline=True, source_srt=src_srt, lines=len(chosen_segs))
    return payload


def _expand_rerecognize_window(start: float, end: float, duration: float,
                               min_span: float = 1.2,
                               pad_before: float = 1.5,
                               pad_after: float = 1.0):
    """Give short speech fragments enough context for another ASR attempt."""
    if not all(math.isfinite(float(v)) for v in (start, end, duration)):
        raise ValueError("Mốc thời gian phải là số hữu hạn.")
    if start < 0 or end <= start or duration <= 0 or start >= duration:
        raise ValueError("Khoảng nhận dạng nằm ngoài audio.")
    start = max(0.0, float(start))
    end = float(end)
    if duration and duration > 0:
        end = min(float(duration), end)
    if end - start >= min_span:
        return start, end, False
    padded_start = max(0.0, start - pad_before)
    padded_end = end + pad_after
    if duration and duration > 0:
        padded_end = min(float(duration), padded_end)
    if padded_end - padded_start < min_span and duration and duration > 0:
        extra = min_span - (padded_end - padded_start)
        padded_start = max(0.0, padded_start - extra)
        padded_end = min(float(duration), padded_end + extra)
    return padded_start, padded_end, True


def _range_has_cues(cues, start: float, end: float, tolerance: float = 0.05) -> bool:
    """Require continuous coverage, not merely one intersecting subtitle."""
    cursor = start
    for cue in sorted(cues, key=lambda s: (s.start, s.end)):
        if not (cue.text or "").strip() or cue.end <= cursor:
            continue
        if cue.start > cursor + tolerance:
            return False
        cursor = max(cursor, cue.end)
        if cursor >= end - tolerance:
            return True
    return False


def _restore_retry_source(originals, candidates):
    """Keep old source text if a retry recovered only part of its interval."""
    restore = []
    kept = list(candidates)
    while True:
        missing = [s for s in originals if s not in restore
                   and not _range_has_cues(kept, s.start, s.end)]
        if not missing:
            return sorted(kept + restore, key=lambda s: (s.start, s.end))
        restore.extend(missing)
        kept = [s for s in kept
                if not any(s.start < old.end and s.end > old.start for old in missing)]


def _isolate_retry_result(source, result, start, end):
    """ASR may clone/stitch the whole movie. Only commit the requested window."""
    outside = [s for s in source if s.end <= start or s.start >= end]
    inside = [s for s in source if s.start < end and s.end > start]
    # Cross-boundary candidates cannot safely replace a whole source sentence.
    candidates = [s for s in result if s.start >= start and s.end <= end]
    return sorted(outside + _restore_retry_source(inside, candidates),
                  key=lambda s: (s.start, s.end))


def _union_prior_review(prior, focused_start: float, focused_end: float,
                        recovered, review: List[Dict]) -> List[Dict]:
    """Keep withheld gaps outside the focused rerecognize span.

    Resolving one hole must not pop REVIEW_REQUIRED for the rest of the film.
    """
    out = [dict(row) for row in (review or [])]

    def _covered(start: float, end: float) -> bool:
        for row in out:
            try:
                rs = float(row.get("start") or 0.0)
                re = float(row.get("end") or 0.0)
            except (TypeError, ValueError):
                continue
            if abs(rs - start) < 0.05 and abs(re - end) < 0.05:
                return True
        return False

    for row in prior or []:
        try:
            start = float(row.get("start") or 0.0)
            end = float(row.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        focused = start < focused_end and end > focused_start
        if (focused and start >= focused_start and end <= focused_end
                and _range_has_cues(recovered, start, end)):
            continue
        if _covered(start, end):
            continue
        item = dict(row)
        item["start"] = start
        item["end"] = end
        item["withheld"] = True
        item["needs_review"] = True
        item.setdefault("reason", "unresolved_speech_gap")
        out.append(item)
    return out


def rerecognize_caption_gap(job_id: int, start: float, end: float):
    """Re-ASR one withheld span in ~12s slices. Never invent Chinese text."""
    from pathlib import Path
    from .. import asr as asr_mod, speechmap, video as vid_mod
    from ..asr.attempts import Attempts
    from ..asr.detect import confirm_speech_holes
    from ..asr.merge import find_uncovered_speech_ranges, normalize_segments
    from ..asr.pipeline import _rescue_gaps
    from ..asr.long_audio import save_working, atomic_srt
    from ..asr.screen_pack import CaptionReviewRequired, last_review
    from ..asr.funasr import cached_funasr_speech_ranges
    from ..utils import ffprobe_duration
    from .helpers import _path_under

    job = None
    pr = None
    try:
        job = _find(job_id)
        pr = get_project(job_id)
        if not job or not pr:
            _log("Không nhận dạng lại được: thiếu job hoặc video.", "err")
            return
        from .review import review_for_job
        current_review = review_for_job(job, pr, base_dir=HERE)
        if any(r['reason']=='source_changed' for r in current_review['effective_blockers']):
            raise ValueError('Nguồn video đã đổi; không dùng bản kiểm tra của nguồn cũ.')
        review_dir = str(job.get("review_dir") or "")
        output_root = os.path.abspath(os.path.join(HERE, "output"))
        root = os.path.abspath(review_dir) if review_dir else ""
        safe = bool(
            root
            and os.path.isdir(root)
            and os.path.basename(root).startswith("caption-review-")
            and _path_under(output_root, root)
        )
        if not safe:
            _log("Không nhận dạng lại được: thư mục kiểm tra không hợp lệ.", "err")
            return
        try:
            start = float(start)
            end = float(end)
        except (TypeError, ValueError):
            _log("Không nhận dạng lại được: mốc thời gian không hợp lệ.", "err")
            return
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            _log("Không nhận dạng lại được: khoảng thời gian trống.", "err")
            return
        from ..asr.screen_pack import load_review_gaps
        listed = load_review_gaps(root) or job.get("review_gaps") or []
        if not any(float(g.get("end") or 0) > start and float(g.get("start") or 0) < end
                   for g in listed):
            _log("Đoạn này không nằm trong danh sách cần kiểm tra.", "err")
            return
        cfg = _load_cfg()
        a = cfg.get("asr", {}) or {}
        asr_mod.set_caption_options(a)
        span = _active_media_span(pr)
        stem, _raw = _run_stem_for_project(pr, span)
        out_dir = os.path.join(HERE, "output", stem)
        tmp_dir = os.path.join(out_dir, "_tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        audio_wav = os.path.join(tmp_dir, "audio16k.wav")
        for name in ("audio16k.flac", "audio16k.wav"):
            candidate = os.path.join(tmp_dir, name)
            if os.path.isfile(candidate):
                audio_wav = candidate
                break
        else:
            audio_wav = _call_filtered(
                vid_mod.ensure_audio, pr["video"], audio_wav,
                _kw={
                    "loudnorm": a.get("loudnorm", True),
                    "trim_start": span["start"] if span.get("enabled") else 0.0,
                    "trim_duration": float(span["duration"]) if span.get("enabled") else None,
                },
            ) or audio_wav
        src_srt = os.path.join(out_dir, f"{stem}.src.srt")
        asr_srt = os.path.join(out_dir, f"{stem}.asr.srt")
        listed_start, listed_end = start, end
        with _LOCK:
            job["status"] = "dang chay"
            job["note"] = (
                f"Nhận dạng lại {_fmt_span_range(listed_start, listed_end)} "
                "(cắt nhỏ ~12s, không bịa chữ)."
            )
        _progress(pct=8, step="Nhận dạng lại đoạn", sub=1)
        _log(job["note"], "step")
        if current_cancel_event().is_set():
            raise InterruptedError
        duration = float(ffprobe_duration(audio_wav) or 0.0)
        start, end, padded = _expand_rerecognize_window(listed_start, listed_end, duration)
        if duration <= 0 or end - start < 0.4:
            _log("Không nhận dạng lại được: audio hoặc khoảng thời gian không dùng được.", "err")
            with _LOCK:
                job["status"] = "cần kiểm tra"
            return
        if padded:
            _log(
                f"Khoảng {_fmt_span_range(listed_start, listed_end)} ngắn "
                f"({listed_end - listed_start:.2f}s); nhận dạng lại kèm ngữ cảnh "
                f"{_fmt_span_range(start, end)}.",
                "info",
            )
        source_path = Path(root) / "source.srt"
        working = Path(out_dir) / f"{stem}.asr.working.srt"
        if not source_path.is_file() and working.is_file():
            source_path = working
        source_segs = srt_utils.load_srt_file(str(source_path)) if source_path.is_file() else []
        # A withheld cue can already have text but lack reliable word clocks.
        # Remove that cue from the retry input so gap rescue actually calls ASR.
        # Retry its entire interval to avoid silently dropping either end.
        retry_source = [s for s in source_segs
                        if s.start < end and s.end > start]
        if retry_source:
            start = max(0.0, min(start, min(s.start for s in retry_source)))
            end = min(duration, max(end, max(s.end for s in retry_source)))
        retry_input = [s for s in source_segs if s not in retry_source]
        forgotten = Attempts(audio_wav).forget_range(start, end)
        if forgotten:
            _log(f"Quên {forgotten} lần nhận dạng cũ trên đoạn này để thử lại.", "info")
        sm_path = Path(root) / "speechmap.json"
        if not sm_path.is_file():
            sm_path = Path(speechmap.default_path(out_dir, stem))
        existing_map = speechmap.SpeechMap.load(str(sm_path)) if sm_path.is_file() else None
        if existing_map is not None:
            speechmap.set_active(existing_map, str(sm_path))
        else:
            speechmap.clear_active()
        kw = _asr_transcribe_kwargs(a)
        report: Dict = {}
        marks: List = []
        segs = _rescue_gaps(
            audio_wav, retry_input, duration,
            kw["backend"], kw["language"] or "zh", kw["model_size"],
            kw["device"], kw["compute_type"], int(kw["batch_size"] or 8), 5,
            float(kw["min_gap_seconds"] or 25), 3, float(kw["silence_db"] or -45),
            audio_gap_rescue=True,
            speech_gap_seconds=float(kw["speech_gap_seconds"] or 1.2),
            speech_silence_db=float(kw["speech_silence_db"] or -42),
            speech_min_silence=float(kw["speech_min_silence"] or 0.35),
            marks_out=marks,
            fallback_backend=kw.get("fallback_backend") or "faster-whisper",
            report_out=report,
            force_retry=True,
            focus=(start, end),
            max_slice=12.0,
        )
        if current_cancel_event().is_set():
            raise InterruptedError
        retry_cues = [s for s in segs if s.start < listed_end and s.end > listed_start]
        retry_result = list(segs)
        segs = _isolate_retry_result(source_segs, segs, start, end)
        kept_ids = {id(s) for s in segs}
        retry_cues = [s for s in retry_cues if id(s) in kept_ids]
        input_ids = {id(s) for s in retry_input}
        replaced = [s for s in retry_result
                    if s.start >= start and s.end <= end
                    and id(s) in kept_ids and id(s) not in input_ids]
        # Word clocks belong to the accepted text. Appending new clocks to old
        # clocks doubles word counts and can withhold the very same cue again.
        marks = [(a, b) for a, b in marks
                 if any(a >= s.start - .001 and b <= s.end + .001 for s in replaced)]
        if existing_map is not None:
            existing_map = speechmap.SpeechMap([
                (a, b) for a, b in existing_map.marks
                if not any(a < s.end and b > s.start for s in replaced)
            ], time_scale=existing_map.time_scale)
        extra = speechmap.SpeechMap(marks) if marks else None
        sm_save = speechmap.default_path(out_dir, stem)
        if existing_map and extra:
            speechmap.set_active(existing_map.merge(extra), sm_save)
        elif extra:
            speechmap.set_active(extra, sm_save)
        elif existing_map:
            speechmap.set_active(existing_map, sm_save)
        else:
            speechmap.clear_active()
        added = [
            s for s in segs
            if (s.text or "").strip()
            and s.end > listed_start and s.start < listed_end
            and not any(
                abs(s.start - o.start) < 0.05 and abs(s.end - o.end) < 0.05
                and (s.text or "") == (o.text or "")
                for o in source_segs
            )
        ]
        _log(
            f"Nhận dạng lại {_fmt_span_range(listed_start, listed_end)}: "
            f"thêm {len(added)} câu Trung (không bịa).",
            "ok" if added else "warn",
        )
        max_chars = asr_mod._max_chars_for(kw["language"] or "zh", 80)
        packed = normalize_segments(list(segs), max_chars)
        review = list(last_review() or [])
        try:
            vad = cached_funasr_speech_ranges(audio_wav, duration)
        except InterruptedError:
            raise
        except Exception:
            vad = None
        gap_s = float(kw["speech_gap_seconds"] or 1.2)
        if vad is not None:
            holes = find_uncovered_speech_ranges(
                segs, vad, duration, min_gap=gap_s, edge_pad=0, subtitle_pad=0)
            holes, _silent = confirm_speech_holes(
                audio_wav, holes, float(kw["speech_silence_db"] or -42))
            review += [
                dict(reason="unresolved_speech_gap", start=a, end=b,
                     withheld=True, needs_review=True, threshold_s=gap_s,
                     detector="fsmn-vad")
                for a, b in holes
            ]
        recovered = [s for s in packed if _range_has_cues(retry_cues, s.start, s.end)]
        # VAD-confirmed pauses need no subtitles. Require every speech island
        # in a reviewed interval, while never auto-accepting an all-silent row.
        if vad is not None:
            for row in listed:
                ra, rb = float(row.get("start") or 0), float(row.get("end") or 0)
                if ra < listed_start or rb > listed_end:
                    continue
                islands = [(max(ra, a), min(rb, b)) for a, b in vad
                           if a < rb and b > ra]
                if islands and all(_range_has_cues(retry_cues, a, b)
                                   and _range_has_cues(packed, a, b) for a, b in islands):
                    recovered.append(Segment(0, ra, rb, "covered speech"))
        has_text = _range_has_cues(recovered, listed_start, listed_end)
        if not has_text:
            review.append(dict(
                reason="suspicious_chunk", start=listed_start, end=listed_end,
                withheld=True, needs_review=True,
                diagnostics={"source": "rerecognize", "empty_after_sliced_asr": True},
            ))
        review = _union_prior_review(
            listed, listed_start, listed_end, recovered, review)
        if current_cancel_event().is_set():
            raise InterruptedError
        save_working(audio_wav, segs)
        from ..asr.screen_pack import ensure_complete
        ensure_complete(packed, segs, tmp_dir, review=review, repair_report=report)
        atomic_srt(asr_srt, packed)
        atomic_srt(src_srt, packed)
        sm = speechmap.get_active()
        if sm is not None:
            path = speechmap.default_path(out_dir, stem)
            sm.save(path)
            speechmap.set_active(sm, path)
        _load_existing_segments_into_project(pr, src_srt)
        _save_project_state(pr)
        bump_rev(job_id)
        with _LOCK:
            job["status"] = "chờ"
            job["note"] = (
                f"Đã nhận dạng lại đoạn {_fmt_span_range(listed_start, listed_end)}: "
                f"{len(added)} câu Trung."
            )
            job.pop("result_status", None)
            job.pop("review_dir", None)
            job.pop("working_source", None)
            job.pop("review_gaps", None)
        _log("Cổng kiểm tra đã qua. Có thể dịch; không chạy lại cả phim.", "ok")
    except InterruptedError:
        with _LOCK:
            if job:
                job["status"] = "đã huỷ"
                job["note"] = "Đã huỷ nhận dạng lại đoạn."
        _log("Đã huỷ nhận dạng lại đoạn.", "warn")
    except CaptionReviewRequired as e:
        with _LOCK:
            if job:
                job["status"] = "cần kiểm tra"
                job["result_status"] = "REVIEW_REQUIRED"
                job["review_dir"] = getattr(e, "review_dir", "")
                job["working_source"] = getattr(e, "source_srt", "")
                job["review_gaps"] = list(getattr(e, "gaps", None) or [])
                job["note"] = str(e)[:200]
        try:
            new_source = getattr(e, "source_srt", "") or ""
            if new_source and os.path.isfile(new_source) and pr:
                _load_existing_segments_into_project(pr, new_source)
                _save_project_state(pr)
                bump_rev(job_id)
        except Exception:
            pass
        _log(f"CẦN KIỂM TRA sau nhận dạng lại: {e}", "warn")
    except Exception as e:
        with _LOCK:
            if job:
                job["status"] = "lỗi"
                job["note"] = str(e)[:200]
        _log(f"LỖI nhận dạng lại đoạn: {e}", "err")
        _log(traceback.format_exc()[-800:], "err")
    finally:
        with _LOCK:
            STATE["running"] = False


def run_pipeline(job_id: int, steps: List[str]):
    """steps ⊂ {asr, translate, tts, sync_check, render}. Chạy trong luồng riêng."""
    blocked_review = None
    job = _find(job_id)
    if job and 'asr' not in steps:
        try:
            from .review import review_for_job
            from ..asr.review import log_review_state
            state = review_for_job(job, get_project(job_id))
            log_review_state(state)
            if state['effective_blockers']:
                blocked_review = dict(status='REVIEW_REQUIRED',review_dir=state['review_dir'],
                                      effective_blockers=state['effective_blockers'])
            elif state['review_dir'] and not state.get('prepared'):
                # Materialize reviewed source artifacts without re-running ASR.
                prepared = ack_caption_review(job_id, [], continue_pipeline=True)
                if not prepared.get('continue_pipeline'):
                    blocked_review = dict(status='REVIEW_REQUIRED',error=prepared.get('error'))
        except Exception:
            with _LOCK:
                STATE['running'] = False
            raise
    if blocked_review is not None:
        with _LOCK:
            STATE['running'] = False
        return blocked_review
    if job:
        for key in ('result_status','review_dir','working_source','review_gaps'):
            job.pop(key,None)
    try:
        _run_pipeline(job_id, steps)
        job = _find(job_id)
        if job and job.get('result_status') == 'REVIEW_REQUIRED':
            return {'status': 'REVIEW_REQUIRED', 'review_dir': job.get('review_dir'),
                    'working_source': job.get('working_source')}
        if job and job.get("result_status") == "SYNC_CHECK_FAILED":
            return {
                "status": "SYNC_CHECK_FAILED",
                "report": job.get("last_sync_check") or {},
            }
        if job and job.get("status") == "lỗi":
            raise RuntimeError(job.get("note") or "Pipeline thất bại.")
    except InterruptedError:
        raise
    except Exception as exc:
        with _LOCK:
            job = _find(job_id)
            if job:
                job.update(status="lỗi", note=str(exc)[:200])
        _log(f"Pipeline không hoàn tất: {exc}", "err")
        raise
    finally:
        # Config loading, path creation and project initialization can fail
        # before the processing loop's own try/finally is entered.
        with _LOCK:
            STATE["running"] = False
            project = PROJECTS.get(job_id)
            if project:
                try:
                    _save_project_state(project)
                except Exception:
                    pass  # _save_project_state logs the persistence failure


def _run_pipeline(job_id: int, steps: List[str]):
    from .. import asr as asr_mod, translate as tr_mod, tts as tts_mod, video as vid_mod

    job = _find(job_id)
    pr = get_project(job_id)
    if not job or not pr:
        with _LOCK:
            STATE["running"] = False
        if job:
            _log("Chưa có file video — hãy đợi tải xong trước khi lồng tiếng.", "err")
        return

    cfg = _load_cfg()
    a, tr, tc, vo = cfg.get("asr", {}), cfg.get("translation", {}), \
        cfg.get("tts", {}), cfg.get("video", {})
    asr_mod.set_caption_options(a)

    span = None
    refresh_project_clocks(pr, log_fn=_log)
    span = _active_media_span(pr)
    stem, raw_stem = _run_stem_for_project(pr, span)
    speechmap.clear_active()
    effective_duration = float(span["duration"])
    out_dir = os.path.join(HERE, "output", stem)
    tmp_dir = os.path.join(out_dir, "_tmp")
    os.makedirs(os.path.join(tmp_dir, "clips"), exist_ok=True)
    from ..asr.nonspeech import bind_source
    review_context = bind_source(tmp_dir, pr['video'],
                                {'start':round(span['start'],3),'end':round(span['end'],3)} if span['enabled'] else {})
    audio_wav = os.path.join(tmp_dir, "audio16k.wav")
    src_srt = os.path.join(out_dir, f"{stem}.src.srt")
    asr_srt = os.path.join(out_dir, f"{stem}.asr.srt")
    vi_srt = os.path.join(out_dir, f"{stem}.vi.srt")
    cache_path = os.path.join(out_dir, f"{stem}.translate_cache.json")
    process_log = os.path.join(out_dir, f"{stem}.quy_trinh.log")
    if start_process_log(process_log):
        _log(f"Nhật ký quy trình được lưu vào: {process_log}", "info")
    if stem != raw_stem:
        _log(f"Tên output đã được chuẩn hóa để Windows tạo thư mục được: {stem}", "info")
    if span.get("enabled"):
        _log(
            f"Cắt video: giữ từ {_fmt_span_time(span['start'])} "
            f"đến {_fmt_span_time(span['end'])} "
            f"({effective_duration / 60:.1f} phút).",
            "info",
        )
    if effective_duration >= 2 * 3600:
        _log("Video dài: ghi cứng phụ đề/làm mờ/xoá logo sẽ render lại toàn bộ hình. "
             "Nhanh nhất là tắt hardsub/blur/delogo và xuất SRT rời; khi chỉ thay "
             "audio, backend MP4Box sẽ copy video.", "info")

    total = len(steps)
    done = 0
    src_lang = a.get("source_language") or "auto"

    def bump(step_name, pct_in_step=0.0):
        _progress(pct=(done + pct_in_step) / max(1, total) * 100,
                  step=step_name, sub=done + 1)

    def _log_stage(name: str, t0: float) -> None:
        dt = time.monotonic() - t0
        _log(f"STAGE {name}: {dt:.2f}s ({dt / 60:.2f} phút)", "info")

    auto_retranslate = bool(tr.get("auto_retranslate", True))

    def _dich_nhom_segments(target_segs: List[Segment],
                            cache_path: str = None) -> None:
        """Dịch (tại chỗ) một nhóm segment bằng đúng provider đang cấu hình.

        Dùng cho cả bước Dịch chính lẫn việc tự dịch lại các dòng mà lô
        trước trả thiếu (còn nguyên tiếng Trung).
        """
        provider = str(tr.get("provider", "browser")).lower()
        name_hint = tr_mod.build_name_hint(
            tr.get("male_lead_name", ""),
            tr.get("female_lead_name", ""))
        film_hint = tr_mod.build_film_hint(raw_stem or stem)
        if provider == "browser":
            _call_filtered(
                tr_mod.translate_via_browser, target_segs,
                os.path.join(HERE, tr.get("browser_profile",
                                          "browser_profile")),
                _kw={
                    "channel": tr.get("browser_channel", "msedge"),
                    "chunk_size": tr.get("chunk_size", 25),
                    "wait_reply": tr.get("wait_reply", 120),
                    "cache_path": cache_path,
                    "source_lang": src_lang if src_lang != "auto" else None,
                    "reset_every": tr.get("reset_every"),
                    "chars_per_sec": tr.get("chars_per_sec"),
                    "name_hint": name_hint,
                    "film_hint": film_hint,
                    "shorten_long_lines_enabled": tr.get("shorten_long_lines"),
                    "translation_cfg": tr,
                    # các tham số của bản cũ - tự bỏ nếu không còn
                    "mode": tr.get("browser_mode"),
                    "debug_port": tr.get("debug_port"),
                    "use_real_profile": tr.get("use_real_profile"),
                    "out_dir": out_dir,
                })
        else:
            api_key, model, api_base_url, api_timeout = \
                _translation_api_params(tr, provider)
            _call_filtered(
                tr_mod.translate_segments, target_segs,
                _kw={
                    "api_key": api_key,
                    "model": model,
                    "provider": provider,
                    "api_base_url": api_base_url,
                    "api_timeout": api_timeout,
                    "chunk_size": tr.get("chunk_size", 40),
                    "cache_path": cache_path,
                    "chars_per_sec": tr.get("chars_per_sec"),
                    "name_hint": name_hint,
                    "film_hint": film_hint,
                    "shorten_long_lines_enabled": tr.get("shorten_long_lines"),
                    "translation_cfg": tr,
                })

    def _chan_tieng_trung(stage_name: str, segs: List[Segment],
                          local_rows: List[Dict],
                          raise_on_fail: bool = True) -> bool:
        """Chốt chặn tiếng Trung, có tự dịch lại (translation.auto_retranslate)."""
        return _tu_dich_lai_dong_tieng_trung(
            stage_name, segs, local_rows,
            lambda target: _dich_nhom_segments(target, cache_path),
            enabled=auto_retranslate, raise_on_fail=raise_on_fail, log=_log)

    leftover_after_translate = False
    try:
        with _LOCK:
            STATE["running"] = True
            STATE["cancel"] = False
            job["status"] = "dang chay"

        # ---------------- 1. ASR ----------------
        if "asr" in steps:
            bump("Nhận dạng phụ đề gốc")
            t_asr = time.monotonic()
            keep_zh = srt_utils.keep_source_timing(tr)
            from ..asr.common import source_reuse_path
            reuse_path = source_reuse_path(a, keep_zh, asr_srt, src_srt)
            if review_context.get('require_asr'):
                reuse_path = ''
            reused_src = bool(reuse_path)
            if reused_src:
                _log(f"Dùng lại phụ đề gốc: {reuse_path}", "ok")
                _bao_dam_ban_do_thoai(asr_mod, out_dir, stem, pr, span,
                                      effective_duration)
                segs = srt_utils.load_srt_file(reuse_path)
                lang = (a.get("source_language")
                        or asr_mod.guess_language(segs) or "auto")
                raw_segments = list(segs)
                if (not keep_zh) or asr_mod.caption_style_is_screen():
                    before_norm = len(segs)
                    max_chars = asr_mod._max_chars_for(lang, 80)
                    segs = asr_mod.normalize_segments(segs, max_chars)
                    if len(segs) != before_norm:
                        if asr_mod.caption_style_is_screen():
                            _log(f"Cắt phụ đề kiểu CapCut: {before_norm} -> "
                                 f"{len(segs)} dòng (~14 chữ / ~2.7s). "
                                 "Bước Dịch sẽ khớp bản Việt cũ theo đồng hồ, "
                                 "không dịch lại cả phim vì lệch ± vài dòng.", "ok")
                        else:
                            _log(f"Đã gom/sửa lại phụ đề gốc cũ: {before_norm} -> "
                                 f"{len(segs)} dòng.", "ok")
                else:
                    _log("Giữ nhịp SRT Trung: không gộp/chia lại mốc ASR.", "ok")
                if a.get("filter_hallucinations", True):
                    segs, removed = asr_mod.drop_hallucinations(segs)
                    if removed:
                        _log(f"Đã bỏ {len(removed)} dòng nghi là câu BỊA trong "
                             "file cũ.", "warn")
                from ..asr.pipeline import withheld_vad_gaps
                from ..asr.screen_pack import ensure_complete, last_review
                review = last_review() if asr_mod.caption_style_is_screen() else []
                audio_for_vad = audio_wav
                if not os.path.isfile(audio_for_vad):
                    flac = os.path.join(tmp_dir, "audio16k.flac")
                    if os.path.isfile(flac):
                        audio_for_vad = flac
                vad_dur = 0.0
                if os.path.isfile(audio_for_vad):
                    try:
                        vad_dur = float(ffprobe_duration(audio_for_vad) or 0.0)
                    except Exception:
                        vad_dur = 0.0
                if vad_dur <= 0:
                    vad_dur = float(effective_duration or 0.0)
                _, vad_review = withheld_vad_gaps(
                    audio_for_vad if os.path.isfile(audio_for_vad) else "",
                    segs, vad_dur,
                    float(a.get("speech_gap_seconds") or 1.2),
                    float(a.get("speech_silence_db") or -42.0),
                    stitch=False)
                review += vad_review
                ensure_complete(segs, raw_segments, tmp_dir, review=review)
                if a.get("corrections"):
                    nfix = asr_mod.apply_corrections(segs, a["corrections"])
                    if nfix:
                        _log(f"Đã sửa {nfix} dòng theo bảng asr.corrections.", "ok")
                srt_utils.save_srt_file(src_srt, segs)
            else:
                _log("Tách audio...", "step")
                audio_wav = _call_filtered(vid_mod.ensure_audio, pr["video"], audio_wav,
                                           _kw={
                                               "loudnorm": a.get("loudnorm", True),
                                               "reuse_existing": not review_context.get('require_asr', False),
                                               "trim_start": span["start"] if span.get("enabled") else 0.0,
                                               "trim_duration": effective_duration if span.get("enabled") else None,
                                           })
                if current_cancel_event().is_set():
                    raise InterruptedError
                _log(f"Nhận dạng (backend={a.get('backend','paraformer')})...", "step")
                segs, lang = _call_filtered(asr_mod.transcribe, audio_wav,
                                           _kw=_asr_transcribe_kwargs(a))
                srt_utils.save_srt_file(asr_srt, segs)
                srt_utils.save_srt_file(src_srt, segs)
                sm = speechmap.get_active()
                if sm is not None:
                    path = speechmap.default_path(out_dir, stem)
                    sm.save(path)
                    speechmap.set_active(sm, path)
            local_rows = _rows_from_segments(segs, "src")
            tmp_pr = {"segments": local_rows}
            if keep_zh:
                _log("Giữ nhịp SRT Trung: 1 câu gốc = 1 câu Việt, cùng start/end.", "ok")
            else:
                delta = _prepare_project_src_for_translation(tmp_pr, tr)
                if delta:
                    local_rows = tmp_pr["segments"]
                    segs = _segments_from_rows(local_rows, use_vi=False)
                    srt_utils.save_srt_file(src_srt, segs)
                    _log(f"Đã chuẩn bị câu gốc để dịch theo ý nghĩa: "
                         f"{len(segs) - delta} -> {len(segs)} dòng.", "ok")
            clock = _keo_dong_ho_hinh(
                pr, segs, span, out_dir, stem, audio_wav, reset=not reused_src)
            if clock.get("applied"):
                _ghi_moc_sau_keo(local_rows, segs)
                srt_utils.save_srt_file(src_srt, segs)
            _sync_project_rows_for_span(pr, span, local_rows)
            bump_rev(job_id)
            src_lang = lang or src_lang
            done += 1
            _log_stage("asr", t_asr)

        # ---------------- 2. Dịch ----------------
        if "translate" in steps:
            bump("Dịch sang tiếng Việt")
            t_tr = time.monotonic()
            _bao_dam_ban_do_thoai(asr_mod, out_dir, stem, pr, span,
                                  effective_duration)
            local_rows = _project_rows_for_span(pr, span)
            segs = _segments_from_rows(local_rows, use_vi=False)
            if not segs and os.path.exists(src_srt):
                _log(f"Nạp lại phụ đề gốc từ file có sẵn: {src_srt}", "ok")
                local_rows = _load_local_rows_from_srt(src_srt)
                _sync_project_rows_for_span(pr, span, local_rows)
                segs = _segments_from_rows(local_rows, use_vi=False)
                src_lang = a.get("source_language") or asr_mod.guess_language(segs) or src_lang
            if not segs:
                raise RuntimeError("Chưa có phụ đề gốc - hãy chạy Nhận dạng trước.")
            tmp_pr = {"segments": local_rows}
            if srt_utils.keep_source_timing(tr):
                _log("Giữ nhịp SRT Trung: không gộp câu trước khi dịch.", "ok")
            else:
                delta = _prepare_project_src_for_translation(tmp_pr, tr)
                if delta:
                    local_rows = tmp_pr["segments"]
                    segs = _segments_from_rows(local_rows, use_vi=False)
                    srt_utils.save_srt_file(src_srt, segs)
                    _log(f"Đã chuẩn bị câu gốc để dịch theo ý nghĩa trước khi dịch: "
                         f"{len(segs) - delta} -> {len(segs)} dòng.", "ok")
            clock = _keo_dong_ho_hinh(
                pr, segs, span, out_dir, stem, audio_wav, reset=False)
            if clock.get("applied"):
                _ghi_moc_sau_keo(local_rows, segs)
                srt_utils.save_srt_file(src_srt, segs)
                _sync_project_rows_for_span(pr, span, local_rows)
            reused_translation = False
            split_vi = bool(tr.get("split_translated_on_punctuation", False))
            if tr.get("reuse_existing") and os.path.exists(vi_srt):
                from ..translate.reuse import reuse_translated_cues
                vi_segments = srt_utils.load_srt_file(vi_srt)
                restore_metadata(vi_segments, cache_path)
                old_n, new_n = len(vi_segments), len(segs)
                ok, dirty_idx, copied, repaired = reuse_translated_cues(
                    segs, vi_segments, local_rows)
                if ok:
                    if old_n == new_n:
                        sync_cue_text(segs, cache_path)
                    else:
                        _log(f"Khớp bản dịch cũ theo đồng hồ: giữ {copied}/{new_n} dòng "
                             f"(file cũ {old_n} dòng, gốc mới {new_n}). "
                             "Không dịch lại cả phim vì lệch số dòng.", "ok")
                    if dirty_idx:
                        sample = ", ".join(str(segs[i].index) for i in dirty_idx[:8])
                        more = "" if len(dirty_idx) <= 8 else f", ... +{len(dirty_idx) - 8}"
                        _log(f"Bản dịch cũ còn tiếng Trung ở dòng {sample}{more} "
                             "-> chỉ dịch lại các dòng đó, không dịch cả phim.", "warn")
                        _chan_tieng_trung("lưu bản dịch", segs, local_rows,
                                          raise_on_fail=False)
                    elif repaired:
                        _log(f"Đã vá {repaired} từ Trung còn sót trong bản dịch cũ; "
                             f"dùng lại {vi_srt}.", "ok")
                    elif old_n == new_n:
                        _log(f"Dùng lại bản dịch: {vi_srt}", "ok")
                    reused_translation = True
                else:
                    _log(f"Bản dịch cũ có {old_n} dòng, phụ đề gốc có {new_n} dòng "
                         "và đồng hồ không khớp -> dịch lại để tránh dùng timestamp "
                         "đã bị lệch.", "warn")
            if reused_translation:
                tmp_pr = {"segments": local_rows}
                polished = _polish_project_vi(tmp_pr, tr)
                if polished is not None:
                    _log(f"Da chia lai sub Viet theo y cau: {len(_segments_from_rows(tmp_pr['segments'], use_vi=True))} dong.", "ok")
                if split_vi:
                    added = _split_project_vi_on_punctuation(tmp_pr)
                    if added:
                        _log(f"Da tach sub Viet theo dau cau: +{added} dong.", "ok")
                n_clean = _finalize_project_vi(tmp_pr, tr)
                if n_clean:
                    _log(f"Đã làm sạch {n_clean} dòng SRT Việt lần cuối "
                         "(đủ nhịp đọc, giữ mốc).", "ok")
                local_rows = tmp_pr["segments"]
                _sync_project_rows_for_span(pr, span, local_rows)
                srt_utils.save_srt_file(vi_srt, _segments_from_rows(local_rows, use_vi=True))
                done += 1
                bump_rev(job_id)
                _log_stage("translate", t_tr)
            else:
                _translate_with_source_retry(
                    segs, lambda rows: _dich_nhom_segments(rows, cache_path),
                    lambda exc: _log(f"Còn lô dịch thiếu ({exc}); nối tiếp cache từ phụ đề gốc, "
                                     "chỉ yêu cầu các câu chưa đạt.", "warn"))
                for row, s in zip(local_rows, segs):
                    row["vi"] = s.text
                    copy_metadata(s, row)
                # Lô nào trả thiếu thì dòng đó còn nguyên tiếng Trung - tự dịch
                # lại NGAY tại đây, lúc các dòng còn khớp 1-1 với câu gốc (chưa
                # bị polish chia lại). Vẫn hỏng thì chưa chặn: bước TTS sẽ thử
                # thêm lần nữa rồi mới dừng.
                _chan_tieng_trung("lưu bản dịch", segs, local_rows,
                                  raise_on_fail=False)
                tmp_pr = {"segments": local_rows}
                polished = _polish_project_vi(tmp_pr, tr)
                if polished is not None:
                    _log(f"Da chia lai sub Viet theo y cau: {len(_segments_from_rows(tmp_pr['segments'], use_vi=True))} dong.", "ok")
                if split_vi:
                    added = _split_project_vi_on_punctuation(tmp_pr)
                    if added:
                        _log(f"Da tach sub Viet theo dau cau: +{added} dong.", "ok")
                n_clean = _finalize_project_vi(tmp_pr, tr)
                if n_clean:
                    _log(f"Đã làm sạch {n_clean} dòng SRT Việt lần cuối "
                         "(đủ nhịp đọc, giữ mốc).", "ok")
                local_rows = tmp_pr["segments"]
                _sync_project_rows_for_span(pr, span, local_rows)
                bump_rev(job_id)
                srt_utils.save_srt_file(vi_srt, _segments_from_rows(local_rows, use_vi=True))
                done += 1
                _log_stage("translate", t_tr)
            from ..translate.cjk_residue import leftover_cjk_indices as _leftover_vi
            leftover_after_translate = bool(
                _leftover_vi(_segments_from_rows(local_rows, use_vi=True)))

        # ---------------- 3. Lồng tiếng ----------------
        dub_wav = os.path.join(tmp_dir, "dub.wav")
        sync_report = None
        if "tts" in steps:
            bump("Dựng track giọng đọc")
            t_tts = time.monotonic()
            _bao_dam_ban_do_thoai(asr_mod, out_dir, stem, pr, span,
                                  effective_duration)
            local_rows = _project_rows_for_span(pr, span)
            segs = _segments_from_rows(local_rows, use_vi=True)
            if not segs and os.path.exists(src_srt):
                _log("Nạp lại phụ đề/bản dịch từ output cũ để dựng giọng.", "ok")
                local_rows = _load_local_rows_from_srt(src_srt, vi_srt)
                _sync_project_rows_for_span(pr, span, local_rows)
                segs = _segments_from_rows(local_rows, use_vi=True)
            if not segs:
                raise RuntimeError("Chưa có phụ đề - hãy chạy Nhận dạng/Dịch trước.")
            _gan_metadata_dich(segs, local_rows, cache_path)
            opt = pr["options"]
            tmp_pr = {"segments": local_rows}
            polished = _polish_project_vi(tmp_pr, tr)
            if polished is not None:
                local_rows = tmp_pr["segments"]
                _log(f"Da chia lai sub Viet theo y cau truoc TTS: {len(_segments_from_rows(local_rows, use_vi=True))} dong.", "ok")
                _sync_project_rows_for_span(pr, span, local_rows)
                srt_utils.save_srt_file(vi_srt, _segments_from_rows(local_rows, use_vi=True))
                bump_rev(job_id)
                segs = _segments_from_rows(local_rows, use_vi=True)
            if bool(tr.get("split_translated_on_punctuation", False)):
                tmp_pr = {"segments": local_rows}
                added = _split_project_vi_on_punctuation(tmp_pr)
                if added:
                    local_rows = tmp_pr["segments"]
                    _log(f"Da tach sub Viet theo dau cau truoc TTS: +{added} dong.", "ok")
                    _sync_project_rows_for_span(pr, span, local_rows)
                    srt_utils.save_srt_file(vi_srt, _segments_from_rows(local_rows, use_vi=True))
                    bump_rev(job_id)
                    segs = _segments_from_rows(local_rows, use_vi=True)
            tmp_pr = {"segments": local_rows}
            n_clean = _finalize_project_vi(tmp_pr, tr)
            if n_clean:
                local_rows = tmp_pr["segments"]
                _log(f"Đã làm sạch {n_clean} dòng SRT Việt lần cuối "
                     "(đủ nhịp đọc, giữ mốc).", "ok")
                _sync_project_rows_for_span(pr, span, local_rows)
                srt_utils.save_srt_file(vi_srt, _segments_from_rows(local_rows, use_vi=True))
                bump_rev(job_id)
                segs = _segments_from_rows(local_rows, use_vi=True)
            if _chan_tieng_trung("dựng giọng đọc", segs, local_rows):
                _sync_project_rows_for_span(pr, span, local_rows)
                srt_utils.save_srt_file(vi_srt, _segments_from_rows(local_rows, use_vi=True))
                bump_rev(job_id)
                segs = _segments_from_rows(local_rows, use_vi=True)
            clock = _keo_dong_ho_hinh(
                pr, segs, span, out_dir, stem, audio_wav, reset=False)
            if clock.get("applied"):
                _ghi_moc_sau_keo(local_rows, segs)
                _sync_project_rows_for_span(pr, span, local_rows)
                srt_utils.save_srt_file(vi_srt, segs)
                bump_rev(job_id)
            # Cảnh báo sớm nếu bản dịch dài hơn khung thời gian: mọi câu sẽ bị
            # nén/cắt và thoại kết thúc trước hình.
            tr_mod.log_reading_pressure(
                segs, tr.get("chars_per_sec", 15.0) or 15.0)
            # Xóa clips cũ để chắc chắn tạo lại với giọng/pitch mới
            clips_dir = os.path.join(tmp_dir, "clips")
            os.makedirs(clips_dir, exist_ok=True)
            # Đọc engine từ GUI trước, fallback về config.yaml
            tts_engine = opt.get("engine") or tc.get("engine") or "edge"
            tts_voice = opt.get("narrator_voice", tc.get("narrator_voice",
                                                         "vi-VN-NamMinhNeural"))
            tts_pitch = opt.get("narrator_pitch", tc.get("narrator_pitch", "+0Hz"))
            tts_rate = opt.get("base_rate", tc.get("base_rate", "+0%"))
            tts_max_speed = float(opt.get("max_speed", tc.get("max_speed", 1.6)))
            tts_min_gap = float(opt.get("min_gap", tc.get("min_gap", 0.08)))
            tts_max_overhang = float(opt.get(
                "max_overhang_seconds",
                tc.get("max_overhang_seconds", 0.75)) or 0.0)
            tts_sync_offset = float(opt.get("sync_offset_seconds",
                                            tc.get("sync_offset_seconds", 0.0)) or 0.0)
            opt.setdefault("lock_av", True)
            tts_sync_mode = resolve_sync_mode(opt, tc)
            try:
                tts_max_start_drift = max(0.0, float(opt.get(
                    "max_start_drift_seconds",
                    tc.get("max_start_drift_seconds", MAX_START_DRIFT_SECONDS))
                    or MAX_START_DRIFT_SECONDS))
            except (TypeError, ValueError):
                tts_max_start_drift = MAX_START_DRIFT_SECONDS
            tts_trim_overflow = opt.get("trim_overflow", tc.get("trim_overflow", True))
            tts_voice_mode = opt.get("voice_mode", tc.get("voice_mode", "narrator"))
            if str(tts_engine).lower() == "edge":
                norm = tts_mod.normalize_edge_narrator(
                    {"voice": tts_voice, "pitch": tts_pitch})
                tts_voice, tts_pitch = norm["voice"], norm["pitch"]
            _log(f"TTS: engine={tts_engine}, voice={tts_voice}, "
                 f"pitch={tts_pitch}, rate={tts_rate}, "
                 f"mode={tts_voice_mode}, speed={tts_max_speed}, "
                 f"gap={tts_min_gap}, overhang={tts_max_overhang}, "
                 f"sync={tts_sync_mode}, lock_av={opt.get('lock_av', True)}",
                 "info")
            clips, starts, places = _call_filtered(
                tts_mod.build_voice_track, segs, clips_dir,
                _kw={
                    "total_duration": effective_duration,
                    "semantic_cfg": tr if tc.get("semantic_groups", tr.get("semantic_translation", True)) else None,
                    "engine": tts_engine,
                    "voice_mode": tts_voice_mode,
                    "narrator": {"voice": tts_voice, "pitch": tts_pitch},
                    "base_rate": tts_rate,
                    "max_speed": tts_max_speed,
                    "min_gap": tts_min_gap,
                    "max_overhang": tts_max_overhang,
                    "sync_offset_seconds": tts_sync_offset,
                    "sync_mode": tts_sync_mode,
                    "lock_av": opt.get("lock_av", True),
                    "max_start_drift": tts_max_start_drift,
                    "trim_overflow": tts_trim_overflow,
                    "concurrency": tc.get("concurrency", 14),
                    "fail_report_path": os.path.join(out_dir,
                                                     f"{stem}.tts_loi.txt"),
                    "recover_drift": tc.get("recover_drift"),
                    "trim": tc.get("trim_silence", True),
                    "vieneu_voice": opt.get("narrator_voice") if tts_engine == "vieneu" else tc.get("vieneu_voice"),
                    "vieneu_voices": tc.get("vieneu_voices"),
                    "vieneu_options": tc.get("vieneu_options"),
                    "capcut_options": tc.get("capcut_options"),
                    **_tts_retry_kwargs(tc),
                })
            _log(f"Chống đè thoại: {summarize(places)}", "ok")
            for row, s in zip(local_rows, segs):
                row["placed"] = s.placed_start
                row["speed"] = s.speed
                row["voice_dur"] = s.voice_duration
                row["start"] = round(float(s.start), 3)
                row["end"] = round(float(s.end), 3)
                if row["end"] <= row["start"]:
                    row["end"] = round(row["start"] + 0.04, 3)
            _sync_project_rows_for_span(pr, span, local_rows)
            srt_utils.save_srt_file(vi_srt, segs, use_placed=True)
            bump_rev(job_id)
            vc = [c for c in clips if c]
            vs = [s for c, s in zip(clips, starts) if c]
            if not vc:
                raise RuntimeError(
                    "TTS không tạo được clip giọng Việt nào. Đã dừng trước khi xuất "
                    "video để tránh tạo file chỉ còn tiếng gốc + phụ đề. Xem file "
                    f"{stem}.tts_loi.txt trong thư mục output."
                )
            dub_wav = vid_mod.assemble_timeline_audio(
                vc, vs, effective_duration, dub_wav,
                mode=vo.get("audio_mix_mode", "auto"),
                chunk_seconds=vo.get("audio_mix_chunk_seconds", 120))
            native_dur = None
            try:
                native_dur = float(ffprobe_duration(dub_wav) or 0.0)
            except Exception:
                native_dur = None
            dub_wav = vid_mod.lock_audio_to_picture_duration(
                dub_wav, effective_duration)
            opt["last_tts_signature"] = _tts_input_signature(
                local_rows, opt, tc, tr, effective_duration)
            _save_project_state(pr)
            sync_report = _kiem_tra_khop_hinh(
                vid_mod, pr, span, segs, dub_wav, out_dir, stem,
                effective_duration, job_id, places,
                native_dub_duration=native_dur)
            done += 1
            _log_stage("tts", t_tts)
            overflow_path = os.path.join(out_dir, f"{stem}.tts_overflow.json")
            if os.path.exists(overflow_path):
                try:
                    with open(overflow_path, encoding="utf-8") as fh:
                        overflow = json.load(fh)
                    with _LOCK:
                        job["tts_overflow"] = overflow
                except (OSError, ValueError):
                    pass

        if "sync_check" in steps and sync_report is None:
            bump("Kiểm tra khớp hình")
            t_sync = time.monotonic()
            local_rows = _project_rows_for_span(pr, span)
            segs = _segments_from_rows(local_rows, use_vi=True)
            if not segs and os.path.exists(vi_srt):
                local_rows = _load_local_rows_from_srt(src_srt, vi_srt)
                segs = _segments_from_rows(local_rows, use_vi=True)
            dub_wav = _find_existing_dub_audio(tmp_dir) or dub_wav
            native_dur = None
            if dub_wav and os.path.exists(dub_wav):
                from ..video.process import _audio_beside_picture
                orig = _audio_beside_picture(dub_wav)
                probe = orig or dub_wav
                try:
                    native_dur = float(ffprobe_duration(probe) or 0.0)
                except Exception:
                    native_dur = None
                dub_wav = vid_mod.lock_audio_to_picture_duration(
                    dub_wav, effective_duration)
            sync_report = _kiem_tra_khop_hinh(
                vid_mod, pr, span, segs, dub_wav, out_dir, stem,
                effective_duration, job_id, None,
                native_dub_duration=native_dur)
            done += 1
            _log_stage("sync_check", t_sync)

        # ---------------- 4. Xuất video ----------------
        if "render" in steps:
            opt = pr["options"]
            report = sync_report or opt.get("last_sync_check") or {}
            bump("Xuất video")
            t_render = time.monotonic()
            _bao_dam_ban_do_thoai(asr_mod, out_dir, stem, pr, span,
                                  effective_duration)
            opt = pr["options"]
            local_rows = _project_rows_for_span(pr, span)
            segs = _segments_from_rows(local_rows, use_vi=True)
            if not segs and os.path.exists(src_srt):
                _log("Nạp lại phụ đề/bản dịch từ output cũ để xuất video.", "ok")
                local_rows = _load_local_rows_from_srt(src_srt, vi_srt)
                _sync_project_rows_for_span(pr, span, local_rows)
                segs = _segments_from_rows(local_rows, use_vi=True)
            _gan_metadata_dich(segs, local_rows, cache_path)
            # Render must be a read-only consumer of the exact text that made
            # dub.wav. Repairing/reflowing/retranslating here would silently
            # mux old speech with new subtitles. All text mutation belongs
            # before or during TTS.
            from ..translate.cjk_residue import leftover_cjk_indices
            dirty = leftover_cjk_indices(segs)
            if dirty:
                sample = ", ".join(str(segs[i].index) for i in dirty[:8])
                raise RuntimeError(
                    f"Bản dịch còn tiếng Trung ở dòng {sample}. "
                    "Hãy chạy lại Dịch và Dựng giọng đọc trước khi xuất video."
                )
            _assert_render_ready(
                local_rows, opt, tc, tr, effective_duration, report)
            clock = _keo_dong_ho_hinh(
                pr, segs, span, out_dir, stem, audio_wav, reset=False)
            if clock.get("applied"):
                raise RuntimeError(
                    "Đồng hồ phụ đề vừa thay đổi sau lần dựng giọng gần nhất. "
                    "Hãy dựng lại giọng đọc để audio và hình dùng cùng timestamp."
                )
            ass_path = None
            if opt.get("hardsub") and segs:
                ass_path = os.path.join(tmp_dir, f"{stem}.ass")
                overlays.save_ass(ass_path, segs, pr["w"], pr["h"],
                                  pr.get("sub_style"), use_placed=True)
            if opt.get("export_srt") and segs:
                srt_utils.save_srt_file(vi_srt, segs, use_placed=True)

            dub_wav = _find_existing_dub_audio(tmp_dir) or dub_wav
            if dub_wav and os.path.exists(dub_wav):
                dub_wav = vid_mod.lock_audio_to_picture_duration(
                    dub_wav, effective_duration)
            else:
                dub_wav = None
            if dub_wav is None and segs:
                raise RuntimeError(
                    "Chưa có track giọng Việt. Hãy chạy 'Dựng giọng đọc' "
                    "thành công trước khi xuất video."
                )
            final = os.path.join(out_dir, f"{stem}.vietsub_dub.mp4")
            work_pr = _render_project_for_span(pr, span)
            if opt.get("render_chunked"):
                render_with_layers_chunked(work_pr, dub_wav, final, ass_path, segs, tmp_dir)
            else:
                render_with_layers(
                    work_pr, dub_wav, final, ass_path,
                    clip_duration=effective_duration if span.get("enabled") else None,
                    validate_full_source=not span.get("enabled"),
                )
            with _LOCK:
                job["output"] = final
            _log(f"Đã xuất: {final}", "ok")
            from ..youtube_pack.dub_scenes import attach_dub_thumbnails, want_dub_thumbnail
            if want_dub_thumbnail(opt, cfg):
                bump("Cắt cảnh làm thumbnail")
                pack = attach_dub_thumbnails(
                    pr.get("video") or job.get("path") or "",
                    segments=segs,
                    out_dir=out_dir,
                    duration=effective_duration,
                    title=os.path.splitext(os.path.basename(job.get("path") or stem))[0],
                    opt=opt,
                    cfg=cfg,
                    logger=_log,
                    fallback_video=final,
                    cancel_event=current_cancel_event(),
                )
                if pack.get("thumbnail_path"):
                    with _LOCK:
                        job["thumbnail"] = pack["thumbnail_path"]
                        job["thumbnail_caption"] = pack.get("caption_path") or ""
            done += 1
            _log_stage("render", t_render)

        if leftover_after_translate and "tts" not in steps:
            with _LOCK:
                job["status"] = "chờ"
                job["note"] = (
                    "Bản dịch còn tiếng Trung — hãy dựng giọng đọc để tự dịch lại."
                )
            _log(job["note"], "warn")
            return

        _progress(pct=100, step="Hoàn tất", detail="")
        with _LOCK:
            job["status"] = "xong"
        _log("HOÀN TẤT.", "ok")

    except InterruptedError:
        with _LOCK:
            job["status"] = "đã huỷ"
        _log("Đã huỷ theo yêu cầu.", "warn")
    except Exception as e:
        with _LOCK:
            from ..asr.screen_pack import CaptionReviewRequired
            review_like = isinstance(e, (CaptionReviewRequired, SyncCheckFailed))
            job["status"] = "cần kiểm tra" if review_like else "lỗi"
            if isinstance(e, CaptionReviewRequired):
                job["result_status"] = "REVIEW_REQUIRED"
                job["review_dir"] = getattr(e, 'review_dir', '')
                job["working_source"] = getattr(e, 'source_srt', '')
                job["review_gaps"] = list(getattr(e, 'gaps', None) or [])
            elif isinstance(e, SyncCheckFailed):
                job["result_status"] = "SYNC_CHECK_FAILED"
                job["last_sync_check"] = getattr(e, "report", None) or {}
            job["note"] = str(e)[:200]
        if isinstance(e, CaptionReviewRequired):
            _log(f"CẦN KIỂM TRA: {e}", "warn")
        elif isinstance(e, SyncCheckFailed):
            _log(f"CẦN KIỂM TRA KHỚP HÌNH — không xuất video: {e}", "warn")
        else:
            _log(f"LỖI: {e}", "err")
            _log(traceback.format_exc()[-800:], "err")
    finally:
        with _LOCK:
            STATE["running"] = False
