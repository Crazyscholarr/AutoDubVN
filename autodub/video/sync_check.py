"""Kiểm tra giọng Việt đã lồng có khớp hình trước khi xuất cả phim dài."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional, Sequence

from ..utils import ffprobe_duration, log, run
from .common import audio_duration_lock_chain

_VOLUME_RE = re.compile(r"mean_volume:\s*(-?[\d.]+)\s*dB")
SPEECH_DB = -38.0
EARLY_DB = 6.0


class SyncCheckFailed(RuntimeError):
    """Không xuất video khi kiểm tra khớp hình không đạt."""

    def __init__(self, report=None):
        self.report = dict(report or {})
        msg = str(self.report.get("message") or "Kiểm tra khớp hình không đạt.")
        super().__init__(msg)


def assert_sync_allows_render(report, *, force_export: bool = False) -> None:
    """Chặn render khi báo cáo fail, trừ khi người dùng chọn xuất bất chấp."""
    if force_export:
        log("Xuất bất chấp vì force_export=true — track có thể thiếu thoại.", "warn")
        return
    report = report or {}
    if report.get("block_render") or report.get("verdict") == "fail":
        raise SyncCheckFailed(report)


def _seg_start(seg) -> float:
    if isinstance(seg, dict):
        return float(seg.get("start") or 0.0)
    return float(getattr(seg, "start", 0.0) or 0.0)


def _seg_end(seg) -> float:
    if isinstance(seg, dict):
        return float(seg.get("end") or 0.0)
    return float(getattr(seg, "end", 0.0) or 0.0)


def _seg_text(seg) -> str:
    if isinstance(seg, dict):
        return str(seg.get("vi") or seg.get("text") or seg.get("src") or "")
    return str(getattr(seg, "text", "") or "")


def _seg_placed(seg) -> Optional[float]:
    if isinstance(seg, dict):
        raw = seg.get("placed")
        if raw is None:
            raw = seg.get("placed_start")
        try:
            return None if raw is None else float(raw)
        except (TypeError, ValueError):
            return None
    raw = getattr(seg, "placed_start", None)
    try:
        return None if raw is None else float(raw)
    except (TypeError, ValueError):
        return None


def max_start_drift(segments: Sequence) -> float:
    drifts = []
    for seg in segments or []:
        placed = _seg_placed(seg)
        if placed is None:
            continue
        drifts.append(abs(placed - _seg_start(seg)))
    return max(drifts) if drifts else 0.0


def pick_cue_segments(segments: Sequence, count: int = 8) -> List:
    usable = []
    for seg in segments or []:
        if not _seg_text(seg).strip():
            continue
        if _seg_end(seg) - _seg_start(seg) < 0.35:
            continue
        usable.append(seg)
    if not usable:
        return []
    count = max(1, int(count or 8))
    if len(usable) <= count:
        return list(usable)
    last = len(usable) - 1
    idxs = sorted({round(i * last / (count - 1)) for i in range(count)})
    return [usable[i] for i in idxs]


def mean_volume_db(path: str, start: float, duration: float = 0.4) -> Optional[float]:
    if not path or not os.path.exists(path):
        return None
    start = max(0.0, float(start or 0.0))
    duration = max(0.12, float(duration or 0.4))
    try:
        res = run(
            ["ffmpeg", "-hide_banner", "-nostdin",
             "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
             "-i", path, "-af", "volumedetect", "-f", "null", "-"],
            check=False, timeout=40)
    except Exception:
        return None
    text = f"{getattr(res, 'stderr', '') or ''}\n{getattr(res, 'stdout', '') or ''}"
    match = _VOLUME_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def verdict_from_metrics(max_drift_s: float, duration_short_s: float,
                         speech_hit_ratio: Optional[float],
                         early_votes: int, speech_checked: int,
                         picture_s: float = 0.0) -> str:
    """ok | warn | fail — fail = đừng xuất cả phim.

    Track ngắn vài giây ở đuôi phim dài (credit/pad) không được gộp với
    track cụt thật (native << picture, kiểu BV1Za còn 120s).
    """
    if max_drift_s > 5.01:
        return "fail"
    picture = float(picture_s or 0.0)
    tail_only = (
        picture >= 120.0
        and duration_short_s > 2.0
        and duration_short_s <= 15.0
        and duration_short_s / picture <= 0.02
    )
    if duration_short_s > 2.0 and not tail_only:
        return "fail"
    miss = (speech_checked >= 4 and speech_hit_ratio is not None
            and speech_hit_ratio < 0.35)
    # Câu sát nhau / nhạc nền làm RMS trước mốc to — chỉ fail khi đúng mốc cũng thiếu lời.
    early = speech_checked >= 4 and early_votes >= 3
    if miss or (early and (speech_hit_ratio is None or speech_hit_ratio < 0.50)):
        return "fail"
    if max_drift_s > 0.35 or duration_short_s > 0.6:
        return "warn"
    if speech_checked >= 4 and speech_hit_ratio is not None and speech_hit_ratio < 0.65:
        return "warn"
    if early_votes >= 1:
        return "warn"
    return "ok"


def _preview_clip(video: str, dub: str, start: float, duration: float,
                  out_path: str, source_offset: float = 0.0,
                  preroll: float = 12.0) -> str:
    """Cắt đoạn mẫu khớp timestamp (tua gần rồi trim đúng mốc, không copy keyframe lệch)."""
    start = max(0.0, float(start or 0.0))
    duration = max(1.5, float(duration or 10.0))
    video_at = max(0.0, float(source_offset or 0.0) + start)
    fast = max(0.0, video_at - max(2.0, float(preroll)))
    remain = video_at - fast
    vf = (f"[0:v]trim=start={remain:.3f}:duration={duration:.3f},"
          "setpts=PTS-STARTPTS[v]")
    lock = audio_duration_lock_chain(duration, async_resample=False)
    af = f"[1:a]{lock}[a]"
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    run(
        ["ffmpeg", "-y", "-hide_banner", "-nostdin",
         "-ss", f"{fast:.3f}", "-i", video,
         "-ss", f"{start:.3f}", "-i", dub,
         "-filter_complex", f"{vf};{af}",
         "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
         "-c:a", "aac", "-b:a", "192k", "-ac", "2",
         "-movflags", "+faststart", out_path],
        timeout=180)
    return out_path


def export_sync_previews(video: str, dub: str, out_dir: str, stem: str,
                         duration: float, clip_seconds: float = 10.0,
                         source_offset: float = 0.0) -> List[Dict]:
    duration = max(1.0, float(duration or 0.0))
    clip = min(12.0, max(6.0, float(clip_seconds or 10.0)))
    marks = [
        ("Đầu phim", "dau", min(8.0, duration * 0.04)),
        ("Giữa phim", "giua", max(0.0, duration * 0.50 - clip * 0.5)),
        ("Cuối phim", "cuoi", max(0.0, duration - clip - max(4.0, duration * 0.04))),
    ]
    out = []
    for label, slug, at in marks:
        at = min(max(0.0, at), max(0.0, duration - clip))
        path = os.path.join(out_dir, f"{stem}.kiem_tra_{slug}.mp4")
        try:
            _preview_clip(video, dub, at, clip, path, source_offset=source_offset)
            if os.path.exists(path) and os.path.getsize(path) > 1024:
                out.append({"label": label, "slug": slug, "path": path,
                            "at": round(at, 2), "seconds": round(clip, 1)})
                log(f"Đoạn mẫu {label}: {os.path.basename(path)} (từ {at/60:.1f} phút).", "ok")
            else:
                log(f"Không tạo được đoạn mẫu {label}.", "warn")
        except Exception as exc:
            log(f"Không cắt đoạn mẫu {label}: {exc}", "warn")
    return out


def check_dub_sync(
    video: str,
    dub_path: str,
    segments: Sequence,
    *,
    out_dir: str,
    duration: float,
    source_offset: float = 0.0,
    clip_seconds: float = 10.0,
    stem: str = "dub",
    make_previews: bool = True,
    placements_max_drift: Optional[float] = None,
    native_dub_duration: Optional[float] = None,
) -> Dict:
    """Đo lệch lịch + năng lượng tiếng tại mốc hình, cắt 3 đoạn mẫu để nghe."""
    duration = max(0.01, float(duration or 0.0))
    max_drift = float(placements_max_drift) if placements_max_drift is not None \
        else max_start_drift(segments)
    dub_dur = 0.0
    if dub_path and os.path.exists(dub_path):
        try:
            dub_dur = float(ffprobe_duration(dub_path) or 0.0)
        except Exception:
            dub_dur = 0.0
    native_dur = dub_dur
    if native_dub_duration is not None:
        try:
            native_dur = float(native_dub_duration)
        except (TypeError, ValueError):
            native_dur = dub_dur
    duration_short = max(0.0, duration - native_dur) if native_dur > 0.05 else 0.0

    cues = pick_cue_segments(segments, 8)
    hits = 0
    measured = 0
    early_votes = 0
    for seg in cues:
        at = _seg_start(seg)
        vol = mean_volume_db(dub_path, at)
        if vol is None:
            continue
        measured += 1
        if vol > SPEECH_DB:
            hits += 1
        elif at >= 3.0:
            before = mean_volume_db(dub_path, at - 2.5)
            if before is not None and before > SPEECH_DB and before > vol + EARLY_DB:
                early_votes += 1
    hit_ratio = (hits / measured) if measured else None

    verdict = verdict_from_metrics(
        max_drift, duration_short, hit_ratio, early_votes, measured,
        picture_s=duration)
    previews: List[Dict] = []
    if make_previews and video and dub_path and os.path.exists(video) \
            and os.path.exists(dub_path):
        previews = export_sync_previews(
            video, dub_path, out_dir, stem, duration,
            clip_seconds=clip_seconds, source_offset=source_offset)

    speech_note = ""
    if measured:
        missed = max(0, measured - hits)
        if missed:
            speech_note = f" Thiếu thoại tại {missed}/{measured} mốc."
        else:
            speech_note = f" Có tiếng đúng mốc {hits}/{measured}."

    if verdict == "ok":
        message = (f"Giọng Việt khớp mốc hình (lệch start tối đa {max_drift:.2f}s)."
                   f"{speech_note} "
                   "Hãy nghe 3 đoạn mẫu đầu/giữa/cuối rồi xuất cả phim.")
    elif verdict == "warn":
        message = (f"Gần khớp — lệch start {max_drift:.2f}s, track ngắn hơn hình "
                   f"{duration_short:.2f}s.{speech_note} Nghe 3 đoạn mẫu; nếu cuối phim vẫn trượt "
                   "thì dựng lại giọng đọc (không cần nhận dạng/dịch lại).")
    else:
        message = (f"Chưa khớp — lệch start {max_drift:.2f}s, track ngắn {duration_short:.2f}s"
                   f"{', tiếng chạy trước hình' if early_votes >= 3 else ''}."
                   f"{speech_note} "
                   "Nghe 3 đoạn mẫu trong thư mục output. "
                   "Khớp thì bấm Xuất video; lệch thì Dựng lại giọng đọc. "
                   "Không xuất tự động khi thiếu thoại.")

    report = {
        "verdict": verdict,
        "ok": verdict != "fail",
        "block_render": verdict == "fail",
        "max_drift_s": round(max_drift, 3),
        "duration_s": round(duration, 3),
        "dub_duration_s": round(dub_dur, 3),
        "native_dub_duration_s": round(native_dur, 3),
        "duration_short_s": round(duration_short, 3),
        "speech_hit_ratio": None if hit_ratio is None else round(hit_ratio, 3),
        "speech_checked": measured,
        "speech_hits": hits,
        "early_votes": early_votes,
        "previews": previews,
        "message": message,
        "checked_at": int(time.time()),
    }
    report_path = os.path.join(out_dir, f"{stem}.kiem_tra_khop.json")
    try:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        report["report_path"] = report_path
    except OSError:
        pass
    return report
