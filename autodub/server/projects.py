"""Dữ liệu DỰ ÁN của giao diện: vùng phủ, kiểu phụ đề, các dòng thoại.

Gồm 3 nhóm việc:
  - tạo/nạp/lưu project (project chỉ sống trong RAM, state lưu ra JSON);
  - quy đổi giữa dòng của giao diện (dict) và Segment của pipeline;
  - xử lý "span": người dùng cắt một khoảng video thì mọi mốc thời gian
    phải quy về gốc 0 của đoạn cắt rồi trả ngược lại giờ tuyệt đối cho UI.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

from .. import srt_utils, overlays
from ..srt_utils import Segment
from ..semantic import copy_metadata, METADATA_FIELDS
from ..vi_cues import finalize_spoken_vi_cues
from ..media_clock import probe_media_clocks
from ..utils import ffprobe_duration, ffprobe_video_size
from .state import _LOCK, PROJECTS, _find, _log
from .helpers import (_safe_path_stem, _output_stem_for_video,
                      _output_dir_for_video, _float_or_none, _fmt_span_time)
from .config_api import _load_cfg

PROJECT_STATE_KEYS = ("regions", "logo", "sub_style", "options", "segments")


def _project_state_path(path: str) -> str:
    stem = _output_stem_for_video(path)
    return os.path.join(_output_dir_for_video(path), f"{stem}.project.json")


def _load_project_state(pr: Dict) -> None:
    path = _project_state_path(pr.get("video", ""))
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            saved = json.load(f) or {}
    except Exception as e:
        _log(f"Bỏ qua project state lỗi: {e}", "warn")
        return
    if not isinstance(saved, dict):
        _log("Project state phải là object; giữ file gốc để kiểm tra.", "warn")
        return
    for key in PROJECT_STATE_KEYS:
        if key not in saved:
            continue
        val = saved[key]
        if key in ("sub_style", "options") and isinstance(val, dict):
            merged = dict(pr.get(key) or {})
            merged.update(val)
            pr[key] = merged
        elif key in ("regions", "segments") and isinstance(val, list):
            pr[key] = val
        elif key == "logo" and (val is None or isinstance(val, dict)):
            pr[key] = val


def _save_project_state(pr: Dict) -> None:
    path = _project_state_path(pr.get("video", ""))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {k: pr.get(k) for k in PROJECT_STATE_KEYS}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        _log(f"Không lưu được project state: {e}", "warn")
        raise


def default_project(path: str) -> Dict:
    vw, vh = ffprobe_video_size(path)
    vw, vh = (vw or 1280), (vh or 720)
    try:
        cfg = _load_cfg()
    except Exception:
        cfg = {}
    tc = cfg.get("tts", {}) or {}
    vo = cfg.get("video", {}) or {}
    clocks = {}
    try:
        clocks = probe_media_clocks(path)
    except Exception:
        clocks = {}
    picture = 0.0
    try:
        picture = float((clocks or {}).get("picture_duration") or 0.0)
    except (TypeError, ValueError):
        picture = 0.0
    duration = picture if picture > 0.5 else ffprobe_duration(path)
    return {
        "video": path,
        "w": vw, "h": vh,
        "duration": duration,
        "picture_duration": duration,
        "clocks": clocks or {},
        "regions": [],                      # lớp 2: làm mờ / xoá logo
        "logo": None,                       # chèn logo riêng
        "sub_style": dict(overlays.DEFAULT_SUB_STYLE,
                          align="mid-center",
                          box={"x": int(vw * 0.08), "y": int(vh * 0.44),
                               "w": int(vw * 0.84), "h": int(vh * 0.12)}),
        "segments": [],                     # [{start,end,src,vi}]
        "options": {
            "hardsub": vo.get("hardsub_vietnamese", True),
            "export_srt": True,
            # GUI mới chỉnh trực tiếp bằng dB. Giữ giá trị 0-10 cũ (nếu có)
            # để project trước đây vẫn được quy đổi đúng bởi resolver.
            "keep_original_volume": vo.get("keep_original_volume"),
            "keep_original_db": vo.get("keep_original_db", -30),
            "keep_original_muted": vo.get("keep_original_muted", False),
            "use_gpu": vo.get("use_gpu", True),
            "force_h264": vo.get("force_h264", True),
            "try_h264_mf": vo.get("try_h264_mf", False),
            "x264_preset": vo.get("x264_preset", "superfast"),
            "cpu_threads": vo.get("cpu_threads", 4),
            "render_backend": vo.get("render_backend", "auto"),
            "render_chunked": vo.get("render_chunked", False),
            "render_chunk_minutes": vo.get("render_chunk_minutes", 120),
            "crf": 20,
            # Lưu rõ engine hiệu lực vào project để GUI hiển thị đúng thứ backend
            # sẽ chạy. Trước đây GUI hiện edge khi value null, còn backend fallback
            # về config.yaml (vieneu), gây xuất file không có giọng Việt nếu VieNeu lỗi.
            "engine": tc.get("engine", "edge") or "edge",
            "voice_mode": tc.get("voice_mode", "narrator"),
            "narrator_voice": (tc.get("vieneu_voice") if tc.get("engine") == "vieneu"
                               else tc.get("capcut_voice") if tc.get("engine") == "capcut"
                               else tc.get("narrator_voice")) or "vi-VN-NamMinhNeural",
            "narrator_pitch": tc.get("narrator_pitch", "+0Hz"),
            "max_speed": tc.get("max_speed", 1.6),
            "min_gap": tc.get("min_gap", 0.08),
            "max_overhang_seconds": tc.get("max_overhang_seconds", 0.75),
            "base_rate": tc.get("base_rate", "+0%"),
            "sync_offset_seconds": tc.get("sync_offset_seconds", 0.0),
            "sync_mode": tc.get("sync_mode", "strict"),
            "lock_av": tc.get("lock_av", True),
            "max_start_drift_seconds": tc.get("max_start_drift_seconds", 5.0),
            "trim_overflow": tc.get("trim_overflow", True),
            "trim_enabled": False,
            "trim_start": 0.0,
            "trim_end": None,
            "auto_dub_thumbnail": (cfg.get("dang_youtube") or {}).get(
                "auto_dub_thumbnail", False),
        },
    }


def get_project(job_id: int) -> Optional[Dict]:
    with _LOCK:
        if job_id in PROJECTS:
            return PROJECTS[job_id]
        j = _find(job_id)
        if not j:
            return None
        path = j.get("path") or ""
        if not path or not os.path.isfile(path):
            return None
        PROJECTS[job_id] = default_project(path)
        _load_project_state(PROJECTS[job_id])
        from .review import review_for_job, project_review_to_job
        review = review_for_job(j, PROJECTS[job_id])
        if review['items'] or review['review_dir']:
            project_review_to_job(j, review)
        return PROJECTS[job_id]


def _segments_from_project(pr: Dict, use_vi: bool = True) -> List[Segment]:
    return _segments_from_rows(pr.get("segments") or [], use_vi=use_vi)


def _split_project_vi_on_punctuation(pr: Dict) -> int:
    """Split Vietnamese subtitle rows into smaller timed rows for TTS/render."""
    rows = pr.get("segments") or []
    if not rows:
        return 0
    new_rows = []
    changed = False
    for row in rows:
        text = (row.get("vi") or row.get("src") or "").strip()
        if not text:
            new_rows.append(row)
            continue
        seg = Segment(1, float(row.get("start", 0)), float(row.get("end", 0)),
                      text, speaker=row.get("speaker"))
        parts = srt_utils.split_segment_on_punctuation(seg)
        if len(parts) > 1:
            changed = True
        for part in parts:
            nr = dict(row)
            nr["start"] = part.start
            nr["end"] = part.end
            nr["vi"] = part.text
            nr.pop("placed", None)
            nr.pop("speed", None)
            nr.pop("voice_dur", None)
            new_rows.append(nr)
    if changed:
        pr["segments"] = new_rows
    return len(new_rows) - len(rows) if changed else 0


def _polish_project_vi(pr: Dict, tr: Dict) -> Optional[int]:
    """Reflow Vietnamese subtitles in the GUI project using the same CLI polish pass."""
    rows = pr.get("segments") or []
    if not rows or srt_utils.keep_source_timing(tr) or not tr.get("polish_subtitles", True):
        return None
    segs = _segments_from_project(pr, use_vi=True)
    polished = srt_utils.polish_translated_segments(
        segs,
        max_chars=int(tr.get("polish_max_chars", 125) or 125),
        min_chars=int(tr.get("polish_min_chars", 24) or 24),
        min_gap=float(tr.get("polish_min_gap", 0.04) or 0.04),
    )
    if len(polished) == len(segs) and all(
        abs(a.start - b.start) < 1e-6 and abs(a.end - b.end) < 1e-6
        and (a.text or "") == (b.text or "")
        for a, b in zip(segs, polished)
    ):
        return None
    # Giữ lại câu GỐC theo mốc thời gian. Trước đây chỗ này ghi src="" cho mọi
    # dòng, nên sau khi chia lại thì bảng "Sửa từng dòng" mất hết tiếng Trung và
    # bấm Dịch lần nữa là không còn gì để dịch.
    pr["segments"] = [
        _row_from_polished(s, src)
        for s, src in zip(polished, _src_theo_moc(rows, polished))
    ]
    return len(polished) - len(segs)


def _row_from_polished(seg: Segment, src: str) -> Dict:
    row = {"start": seg.start, "end": seg.end,
           "src": src, "vi": seg.text, "speaker": seg.speaker}
    copy_metadata(seg, row)
    return row


def _finalize_project_vi(pr: Dict, tr: Optional[Dict] = None) -> int:
    """Giữ nguyên mốc thời gian, làm sạch chữ Việt lần cuối (ngữ pháp + 环=điểm)."""
    rows = pr.get("segments") or []
    segs = _segments_from_project(pr, use_vi=True)
    if not segs:
        return 0
    if tr is None:
        tr = (_load_cfg().get("translation") or {})
    changed = finalize_spoken_vi_cues(segs, tr)
    if not changed:
        return 0
    for row, seg in zip(rows, segs):
        row["vi"] = seg.text
    return changed


def _src_theo_moc(rows: List[Dict], polished: List[Segment]) -> List[str]:
    """Gán câu gốc cho dòng Việt mới theo chồng lấp đồng hồ.

    Một dòng gốc bị chia thành nhiều dòng Việt thì câu gốc đặt ở dòng con ĐẦU
    TIÊN (các dòng con sau để trống), để không nhân bản cùng một câu Trung ra
    nhiều dòng rồi dịch lại thừa. Một dòng Việt bao nhiều dòng gốc thì ghép
    hết câu Trung chồng lấp — không bỏ mất nguồn khi polish gộp.
    """
    def _clock(row: Dict, key: str) -> float:
        try:
            return float(row.get(key, 0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    out: List[str] = []
    used = set()
    for seg in polished:
        pieces = []
        for i, row in enumerate(rows):
            rs, re = _clock(row, "start"), _clock(row, "end")
            overlap = max(0.0, min(float(seg.end), re) - max(float(seg.start), rs))
            if overlap <= 0.0:
                continue
            src = (row.get("src") or "").strip()
            if not src or i in used:
                continue
            pieces.append(src)
            used.add(i)
        out.append(" ".join(pieces))
    return out


def _split_project_src_on_punctuation(pr: Dict) -> int:
    """Split source subtitle rows by Chinese/Vietnamese punctuation before translation."""
    rows = pr.get("segments") or []
    if not rows:
        return 0
    new_rows = []
    changed = False
    for row in rows:
        text = (row.get("src") or "").strip()
        if not text:
            new_rows.append(row)
            continue
        seg = Segment(1, float(row.get("start", 0)), float(row.get("end", 0)),
                      text, speaker=row.get("speaker"))
        parts = srt_utils.split_segment_on_punctuation(seg)
        if len(parts) > 1:
            changed = True
        for part in parts:
            nr = dict(row)
            nr["start"] = part.start
            nr["end"] = part.end
            nr["src"] = part.text
            nr["vi"] = ""
            nr.pop("placed", None)
            nr.pop("speed", None)
            nr.pop("voice_dur", None)
            new_rows.append(nr)
    if changed:
        pr["segments"] = new_rows
    return len(new_rows) - len(rows) if changed else 0


def _prepare_project_src_for_translation(pr: Dict, tr: Dict) -> int:
    """Split overfull rows and merge ASR fragments into translation units."""
    rows = pr.get("segments") or []
    if not rows:
        return 0
    if srt_utils.keep_source_timing(tr):
        return 0
    if not (tr.get("split_on_punctuation", True)
            or tr.get("merge_source_fragments", True)):
        return 0
    before = len(rows)
    segs = _segments_from_project(pr, use_vi=False)
    prepared = srt_utils.prepare_source_segments_for_translation(
        segs,
        split_on_punctuation=bool(tr.get("split_on_punctuation", True)),
        merge_fragments=bool(tr.get("merge_source_fragments", True)),
        max_chars=int(tr.get("source_merge_max_chars", 180) or 180),
        min_chars=int(tr.get("source_merge_min_chars", 24) or 24),
        max_gap=float(tr.get("source_merge_max_gap", 1.2) or 1.2),
        max_duration=float(tr.get("source_merge_max_duration", 18.0) or 18.0),
    )
    if len(prepared) == len(segs) and all(
        abs(a.start - b.start) < 1e-6 and abs(a.end - b.end) < 1e-6
        and (a.text or "") == (b.text or "") and (a.speaker or "") == (b.speaker or "")
        for a, b in zip(segs, prepared)
    ):
        return 0
    pr["segments"] = [
        {"start": s.start, "end": s.end, "src": s.text,
         "vi": "", "speaker": s.speaker}
        for s in prepared
    ]
    return len(prepared) - before


def _load_existing_segments_into_project(pr: Dict, src_srt: str,
                                         vi_srt: Optional[str] = None) -> List[Segment]:
    """Nạp lại SRT đã có vào project GUI sau khi app restart.

    Project chỉ sống trong RAM. Nếu người dùng mở app lại rồi bấm thẳng "Dịch",
    "Dựng giọng đọc" hoặc "Xuất video", ta khôi phục các dòng từ output cũ thay
    vì bắt chạy ASR lại.
    """
    src = srt_utils.load_srt_file(src_srt) if os.path.exists(src_srt) else []
    vi = srt_utils.load_srt_file(vi_srt) if vi_srt and os.path.exists(vi_srt) else []
    with _LOCK:
        rows = []
        if vi and len(vi) != len(src):
            for s in vi:
                rows.append({
                    "start": s.start, "end": s.end, "src": "",
                    "vi": s.text, "speaker": s.speaker,
                })
        else:
            for i, s in enumerate(src, 1):
                rows.append({
                    "start": s.start, "end": s.end, "src": s.text,
                    "vi": vi[i - 1].text if i - 1 < len(vi) else "",
                    "speaker": s.speaker,
                })
        pr["segments"] = rows
    return src


# --------------------------------------------------------------------------- #
#  Span: khoảng thời gian nguồn được chọn cho lần chạy này
# --------------------------------------------------------------------------- #
def _active_media_span(pr: Dict) -> Dict:
    """Return the source time range selected for this run.

    Project rows and UI timings stay in original video time. Pipeline work uses
    the returned range as a local 0-based clip.
    """
    source_dur = float(pr.get("picture_duration") or pr.get("duration") or 0.0)
    if source_dur <= 0.5:
        clocks = pr.get("clocks") or {}
        try:
            source_dur = float(clocks.get("picture_duration") or 0.0)
        except (TypeError, ValueError):
            source_dur = 0.0
    if source_dur <= 0.5:
        source_dur = float(ffprobe_duration(pr["video"]) or 0.0)
    source_dur = max(0.01, source_dur)
    opt = pr.get("options", {}) or {}
    enabled = bool(opt.get("trim_enabled"))
    start = _float_or_none(opt.get("trim_start"))
    end = _float_or_none(opt.get("trim_end"))
    if not enabled:
        return {
            "enabled": False,
            "start": 0.0,
            "end": source_dur,
            "duration": source_dur,
            "source_duration": source_dur,
        }
    start = max(0.0, min(source_dur - 0.01, start or 0.0))
    end = source_dur if end is None else max(0.01, min(source_dur, end))
    if end <= start + 0.05:
        end = min(source_dur, start + 0.05)
    active = start > 0.01 or end < source_dur - 0.01
    return {
        "enabled": active,
        "start": start if active else 0.0,
        "end": end if active else source_dur,
        "duration": max(0.01, (end - start) if active else source_dur),
        "source_duration": source_dur,
    }


def _run_stem_for_project(pr: Dict, span: Dict) -> Tuple[str, str]:
    raw_stem = os.path.splitext(os.path.basename(pr["video"]))[0]
    base = _safe_path_stem(raw_stem, limit=92)
    if span.get("enabled"):
        suffix = f"_cut_{_fmt_span_time(span['start'])}-{_fmt_span_time(span['end'])}"
        return _safe_path_stem(base + suffix, limit=120), raw_stem
    return _safe_path_stem(raw_stem), raw_stem


def _project_rows_for_span(pr: Dict, span: Dict) -> List[Dict]:
    """Convert project rows from original-video time to local clip time."""
    start = float(span.get("start") or 0.0)
    end = float(span.get("end") or span.get("duration") or 0.0)
    out: List[Dict] = []
    for row in pr.get("segments") or []:
        rs = _float_or_none(row.get("start")) or 0.0
        re = _float_or_none(row.get("end")) or rs
        if re <= start or rs >= end:
            continue
        nr = dict(row)
        nr["start"] = round(max(rs, start) - start, 3)
        nr["end"] = round(min(re, end) - start, 3)
        if nr["end"] <= nr["start"]:
            nr["end"] = round(nr["start"] + 0.01, 3)
        if row.get("placed") is not None:
            placed = _float_or_none(row.get("placed"))
            if placed is not None:
                nr["placed"] = round(max(0.0, placed - start), 3)
        out.append(nr)
    return out


def _segments_from_rows(rows: List[Dict], use_vi: bool = True) -> List[Segment]:
    out: List[Segment] = []
    for i, row in enumerate(rows or [], 1):
        txt = (row.get("vi") or "") if use_vi else (row.get("src") or "")
        if use_vi and not str(txt).strip():
            txt = row.get("src") or ""
        seg = Segment(i, float(row.get("start", 0)), float(row.get("end", 0)),
                      txt, speaker=row.get("speaker"))
        for attr in METADATA_FIELDS:
            if attr in row:
                val = row[attr]
                if attr == "allowed_source_names" and val is not None and not isinstance(val, tuple):
                    val = tuple(val) if not isinstance(val, str) else (val,)
                setattr(seg, attr, val)
        if row.get("placed") is not None:
            placed = _float_or_none(row.get("placed"))
            if placed is not None:
                seg.placed_start = placed
        if row.get("voice_dur") is not None:
            dur = _float_or_none(row.get("voice_dur"))
            if dur is not None:
                seg.voice_duration = dur
        if row.get("speed") is not None:
            speed = _float_or_none(row.get("speed"))
            if speed is not None:
                seg.speed = speed
        out.append(seg)
    return out


def _rows_from_segments(segments: List[Segment], key: str,
                        prior_rows: Optional[List[Dict]] = None) -> List[Dict]:
    prior_rows = prior_rows or []
    rows: List[Dict] = []
    for i, seg in enumerate(segments or []):
        base = dict(prior_rows[i]) if i < len(prior_rows) else {}
        base["start"] = round(float(seg.start), 3)
        base["end"] = round(float(seg.end), 3)
        if key == "src":
            base["src"] = seg.text
            base.setdefault("vi", "")
        elif key == "vi":
            base["vi"] = seg.text
            base.setdefault("src", "")
        else:
            base["src"] = getattr(seg, "src", base.get("src", ""))
            base["vi"] = seg.text
        if seg.speaker:
            base["speaker"] = seg.speaker
        for attr in METADATA_FIELDS:
            base[attr] = getattr(seg, attr)
        for transient in ("placed", "speed", "voice_dur"):
            base.pop(transient, None)
        rows.append(base)
    return rows


def _sync_project_rows_for_span(pr: Dict, span: Dict, local_rows: List[Dict]) -> None:
    """Store local clip rows back in original-video time for the UI."""
    offset = float(span.get("start") or 0.0)
    end = float(span.get("end") or (offset + span.get("duration", 0.0)))
    abs_rows: List[Dict] = []
    for row in local_rows or []:
        nr = dict(row)
        nr["start"] = round(offset + float(row.get("start", 0)), 3)
        nr["end"] = round(offset + float(row.get("end", 0)), 3)
        if row.get("placed") is not None:
            placed = _float_or_none(row.get("placed"))
            if placed is not None:
                nr["placed"] = round(offset + placed, 3)
        abs_rows.append(nr)
    with _LOCK:
        kept = []
        for row in pr.get("segments") or []:
            rs = _float_or_none(row.get("start")) or 0.0
            re = _float_or_none(row.get("end")) or rs
            if re <= offset or rs >= end:
                kept.append(row)
        pr["segments"] = sorted(kept + abs_rows,
                                key=lambda r: (float(r.get("start", 0)),
                                               float(r.get("end", 0))))
        _save_project_state(pr)


def _load_local_rows_from_srt(src_srt: str,
                              vi_srt: Optional[str] = None) -> List[Dict]:
    src = srt_utils.load_srt_file(src_srt) if os.path.exists(src_srt) else []
    vi = srt_utils.load_srt_file(vi_srt) if vi_srt and os.path.exists(vi_srt) else []
    rows: List[Dict] = []
    if not src:
        for s in vi:
            rows.append({"start": s.start, "end": s.end,
                         "src": "", "vi": s.text, "speaker": s.speaker})
        return rows
    originals = [s.text for s in src]
    if vi:
        from ..translate.reuse import reuse_translated_cues
        ok, _dirty, _copied, _repaired = reuse_translated_cues(src, vi)
        if ok:
            for i, s in enumerate(src):
                row = {
                    "start": s.start,
                    "end": s.end,
                    "src": originals[i],
                    "vi": s.text,
                    "speaker": s.speaker,
                }
                copy_metadata(s, row)
                rows.append(row)
            return rows
    for i, s in enumerate(src):
        rows.append({
            "start": s.start,
            "end": s.end,
            "src": originals[i],
            "vi": vi[i].text if vi and len(vi) == len(src) and i < len(vi) else "",
            "speaker": s.speaker,
        })
    return rows


def _regions_for_span(regions: List[Dict], span: Dict) -> List[Dict]:
    if not span.get("enabled"):
        return [dict(r) for r in (regions or [])]
    start = float(span.get("start") or 0.0)
    end = float(span.get("end") or span.get("duration") or 0.0)
    out: List[Dict] = []
    for raw in regions or []:
        r = dict(raw)
        rs = _float_or_none(r.get("start"))
        re = _float_or_none(r.get("end"))
        if rs is None and re is None:
            out.append(r)
            continue
        abs_start = start if rs is None else max(0.0, rs)
        abs_end = end if re is None else max(0.0, re)
        local_start = max(abs_start, start) - start
        local_end = min(abs_end, end) - start
        if local_end <= 0 or local_start >= span["duration"] or local_end <= local_start:
            continue
        r["start"] = round(max(0.0, local_start), 3)
        r["end"] = round(min(float(span["duration"]), local_end), 3)
        out.append(r)
    return out


def _render_project_for_span(pr: Dict, span: Dict) -> Dict:
    work = dict(pr)
    work["duration"] = float(span["duration"])
    work["source_offset"] = float(span.get("start") or 0.0)
    work["regions"] = _regions_for_span(pr.get("regions", []), span)
    return work
