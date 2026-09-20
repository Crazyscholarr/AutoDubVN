"""Đồng hồ hình vs đồng hồ tiếng — khóa khớp xuyên suốt cả phim.

Lệch bắt đầu thấy sau ~1 phút rồi càng về sau càng nặng KHÔNG phải do từng câu
trượt, mà do hai đồng hồ chạy khác tốc độ:

  - ASR/TTS đặt mốc theo audio đã decode (thường = format=duration).
  - Xuất `-c:v copy` phát hình theo PTS luồng video (23.976 vs 24, 25 vs 24,
    VFR, MKV tag DURATION, start_time...).

1% lệch = 0.6s sau phút đầu, ~36s cuối phim 1 giờ. Pad/cắt đuôi hay khóa từng
câu (strict) không sửa được lỗi tuyến tính này.

Cách xử lý: đo tỉ lệ picture/audio một lần, nhân mọi mốc thoại, mix đúng độ
dài hình, và nếu track giọng cũ vẫn theo đồng hồ tiếng thì kéo atempo (giữ
cao độ) cho khớp độ dài hình.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Optional, Sequence

from .utils import ffprobe_duration, log, run

# Tỉ lệ ngoài khoảng này gần như không phải lệch đồng hồ (file hỏng / đo sai).
_SCALE_HARD_MIN = 0.80
_SCALE_HARD_MAX = 1.25
# Dưới mức này không đáng kéo (hơn ~0.18s trên phim 1 giờ).
_SCALE_NOOP = 0.00005
# Kéo atempo khi |Δt| đủ lớn — đuôi đệm mix 0.2s/1s không phải lệch tốc độ.
_STRETCH_PAD_MAX = 1.55
_STRETCH_SHORT_MAX = 0.25
_STRETCH_REL_NOOP = 0.00015
# Thiếu/thừa vài giây trên phim dài = đuôi mix/TTS, không phải lệch PTS.
# 0.4% ≈ 0.24s/phút; lệch đồng hồ thật ~1% (0.6s/phút) vẫn kéo atempo.
_STRETCH_TAIL_ABS = 8.0
_STRETCH_TAIL_REL = 0.004


def _as_float(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "N/A", "n/a"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_rate(raw: Any) -> float:
    text = str(raw or "").strip()
    if not text or text in {"0/0", "N/A", "n/a"}:
        return 0.0
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            den_f = float(den)
            if den_f == 0:
                return 0.0
            val = float(num) / den_f
        else:
            val = float(text)
    except (TypeError, ValueError):
        return 0.0
    return val if val > 0 else 0.0


def parse_duration_tag(raw: Any) -> float:
    """DURATION kiểu MKV `HH:MM:SS.nnn` hoặc số giây."""
    text = str(raw or "").strip()
    if not text or text.upper() == "N/A":
        return 0.0
    if ":" in text:
        parts = text.split(":")
        try:
            sec = float(parts[-1])
            minutes = float(parts[-2]) if len(parts) > 1 else 0.0
            hours = float(parts[-3]) if len(parts) > 2 else 0.0
            return hours * 3600.0 + minutes * 60.0 + sec
        except (TypeError, ValueError):
            return 0.0
    return _as_float(text)


def _duration_from_timebase(stream: Dict) -> float:
    ts = stream.get("duration_ts")
    tb = stream.get("time_base")
    if ts in (None, "", "N/A") or not tb:
        return 0.0
    rate = _parse_rate(tb)
    if rate <= 0:
        return 0.0
    # time_base "1/90000" -> rate 1/90000; duration = duration_ts * time_base
    try:
        ts_f = float(ts)
    except (TypeError, ValueError):
        return 0.0
    return ts_f * rate


def _empty_clocks() -> Dict[str, Any]:
    return {
        "format_duration": 0.0,
        "format_start": 0.0,
        "video_duration": 0.0,
        "video_start": 0.0,
        "audio_duration": 0.0,
        "audio_start": 0.0,
        "fps": 0.0,
        "fps_nominal": 0.0,
        "nb_frames": 0,
        "vfr": False,
        "picture_duration": 0.0,
        "time_scale": 1.0,
        "audio_ref": 0.0,
    }


def clocks_from_probe_json(data: Dict) -> Dict[str, Any]:
    """Thuần: JSON ffprobe -> đồng hồ hình/tiếng. Dùng cho unittest."""
    out = _empty_clocks()
    fmt = data.get("format") or {}
    out["format_duration"] = _as_float(fmt.get("duration"))
    out["format_start"] = _as_float(fmt.get("start_time"))
    fmt_tag = parse_duration_tag((fmt.get("tags") or {}).get("DURATION"))

    video = None
    audio = None
    for stream in data.get("streams") or []:
        kind = str(stream.get("codec_type") or "")
        if kind == "video" and video is None:
            video = stream
        elif kind == "audio" and audio is None:
            audio = stream

    if video:
        fps_avg = _parse_rate(video.get("avg_frame_rate"))
        fps_nom = _parse_rate(video.get("r_frame_rate"))
        out["fps"] = fps_avg if fps_avg > 1 else fps_nom
        out["fps_nominal"] = fps_nom if fps_nom > 1 else fps_avg
        try:
            out["nb_frames"] = int(_as_float(video.get("nb_frames")))
        except (TypeError, ValueError):
            out["nb_frames"] = 0
        tag_dur = parse_duration_tag((video.get("tags") or {}).get("DURATION"))
        from_frames = 0.0
        if out["nb_frames"] > 0 and out["fps"] > 1:
            from_frames = out["nb_frames"] / out["fps"]
        explicit = max(
            _as_float(video.get("duration")),
            _duration_from_timebase(video),
            tag_dur,
        )
        # nb_frames/fps chỉ khi luồng không khai duration — index khung dễ lệch VFR.
        out["video_duration"] = explicit if explicit > 0.5 else from_frames
        out["video_start"] = _as_float(video.get("start_time"))
        if out["fps"] > 1 and out["fps_nominal"] > 1:
            denom = max(out["fps_nominal"], out["fps"])
            out["vfr"] = abs(out["fps"] - out["fps_nominal"]) / denom >= 0.008

    if audio:
        a_tag = parse_duration_tag((audio.get("tags") or {}).get("DURATION"))
        out["audio_duration"] = max(
            _as_float(audio.get("duration")),
            _duration_from_timebase(audio),
            a_tag,
        )
        out["audio_start"] = _as_float(audio.get("start_time"))

    picture = out["video_duration"]
    if picture <= 0.5:
        picture = out["format_duration"] or fmt_tag
    out["picture_duration"] = picture

    audio_ref = out["audio_duration"] or out["format_duration"] or fmt_tag
    out["audio_ref"] = audio_ref
    out["time_scale"] = compute_time_scale(picture, audio_ref)
    return out


def probe_media_clocks(path: str) -> Dict[str, Any]:
    """Đọc PTS/duration luồng hình và tiếng. File hỏng -> clocks rỗng, không ném."""
    if not path or not os.path.exists(path):
        return _empty_clocks()
    try:
        res = run([
            "ffprobe", "-v", "error",
            "-show_entries",
            "format=duration,start_time:"
            "stream=index,codec_type,codec_name,duration,duration_ts,"
            "start_time,nb_frames,avg_frame_rate,r_frame_rate,time_base:"
            "stream_tags=DURATION:format_tags=DURATION",
            "-of", "json", path,
        ], check=False, timeout=60)
        data = json.loads(res.stdout or "{}")
        clocks = clocks_from_probe_json(data)
    except Exception:
        return _empty_clocks()
    if clocks["picture_duration"] <= 0.5:
        fallback = ffprobe_duration(path)
        if fallback > 0.5:
            clocks["picture_duration"] = fallback
            clocks["format_duration"] = clocks["format_duration"] or fallback
            clocks["audio_ref"] = clocks["audio_ref"] or fallback
            clocks["time_scale"] = compute_time_scale(
                clocks["picture_duration"], clocks["audio_ref"])
    return clocks


def compute_time_scale(picture: float, audio_ref: float) -> float:
    """t_hình = t_tiếng * scale. 1.0 nếu không đo được hoặc tỉ lệ vô lý."""
    try:
        picture = float(picture or 0.0)
        audio_ref = float(audio_ref or 0.0)
    except (TypeError, ValueError):
        return 1.0
    if picture < 1.0 or audio_ref < 1.0:
        return 1.0
    scale = picture / audio_ref
    if scale < _SCALE_HARD_MIN or scale > _SCALE_HARD_MAX:
        return 1.0
    if abs(scale - 1.0) < _SCALE_NOOP:
        return 1.0
    return scale


def should_apply_scale(scale: float, picture_duration: float = 0.0) -> bool:
    """Áp khi lệch cả phim > 40ms hoặc sau 1 phút đã lệch > 12ms."""
    try:
        scale = float(scale or 1.0)
        picture = float(picture_duration or 0.0)
    except (TypeError, ValueError):
        return False
    delta = abs(scale - 1.0)
    if delta < _SCALE_NOOP:
        return False
    if scale < _SCALE_HARD_MIN or scale > _SCALE_HARD_MAX:
        return False
    if picture > 1.0 and delta * picture < 0.04 and delta * 60.0 < 0.012:
        return False
    return True


def _seg_end(seg: Any) -> float:
    if seg is None:
        return 0.0
    if isinstance(seg, dict):
        return _as_float(seg.get("end"))
    return _as_float(getattr(seg, "end", 0.0))


def looks_already_scaled(segments: Sequence[Any], clocks: Dict,
                         scale: float) -> bool:
    """SRT đã dài theo hình (có sidecar bị mất) thì đừng nhân lần nữa."""
    if not segments or not should_apply_scale(scale, _as_float(
            (clocks or {}).get("picture_duration"))):
        return False
    last = 0.0
    for seg in segments:
        last = max(last, _seg_end(seg))
    picture = _as_float((clocks or {}).get("picture_duration"))
    audio = _as_float((clocks or {}).get("audio_ref")) or _as_float(
        (clocks or {}).get("format_duration"))
    if last < 1.0 or picture < 1.0 or audio < 1.0:
        return False
    near_pic = abs(last - picture) <= max(2.5, picture * 0.012)
    closer_to_picture = abs(last - picture) + 0.75 < abs(last - audio)
    return near_pic and closer_to_picture


def scale_for_clocks(clocks: Dict, audio_wav: Optional[str] = None,
                     trust_wav: bool = True) -> float:
    """Tỉ lệ từ clocks; ưu tiên WAV ASR (đồng hồ thật lúc nhận dạng) khi đủ tin."""
    picture = _as_float((clocks or {}).get("picture_duration"))
    audio_ref = 0.0
    if trust_wav and audio_wav and os.path.exists(audio_wav):
        wav_dur = ffprobe_duration(audio_wav)
        # WAV cắt theo span không đại diện đồng hồ cả file — caller tắt trust_wav.
        if wav_dur >= 1.0:
            audio_ref = wav_dur
    if audio_ref < 1.0:
        audio_ref = _as_float((clocks or {}).get("audio_ref")) or _as_float(
            (clocks or {}).get("audio_duration")) or _as_float(
            (clocks or {}).get("format_duration"))
    return compute_time_scale(picture, audio_ref)


def picture_duration_for(path: str, pr: Optional[Dict] = None) -> float:
    """Độ dài hình để mix/mux. Đo file đang xuất, không tin clocks của file khác."""
    if path:
        clocks = probe_media_clocks(path)
        pic = _as_float(clocks.get("picture_duration"))
        if pic > 0.5:
            return pic
    if pr:
        clocks = pr.get("clocks") or {}
        pic = _as_float(clocks.get("picture_duration"))
        if pic > 0.5 and (not path or (pr.get("video") or "") == path):
            return pic
        pic = _as_float(pr.get("picture_duration"))
        if pic > 0.5 and (not path or (pr.get("video") or "") == path):
            return pic
        dur = _as_float(pr.get("duration"))
        if dur > 0.5:
            return dur
    return ffprobe_duration(path) if path else 0.0


def refresh_project_clocks(pr: Dict, log_fn: Optional[Callable] = None) -> Dict:
    """Gắn clocks + picture_duration vào project (cả RAM)."""
    logger = log_fn or log
    path = (pr or {}).get("video") or ""
    clocks = probe_media_clocks(path)
    pr["clocks"] = clocks
    picture = _as_float(clocks.get("picture_duration"))
    fmt = _as_float(clocks.get("format_duration")) or ffprobe_duration(path)
    if picture > 0.5:
        pr["picture_duration"] = picture
        pr["duration"] = picture
    elif fmt > 0.5:
        pr["picture_duration"] = fmt
        pr["duration"] = fmt
        picture = fmt
    opt = pr.setdefault("options", {})
    opt["picture_duration"] = pr.get("picture_duration")
    opt["av_time_scale"] = clocks.get("time_scale") or 1.0
    scale = _as_float(clocks.get("time_scale"), 1.0)
    if should_apply_scale(scale, picture):
        drift_min = abs(scale - 1.0) * 60.0
        drift_end = abs(scale - 1.0) * picture
        logger(
            f"Đồng hồ hình {picture:.3f}s, tiếng "
            f"{_as_float(clocks.get('audio_ref')):.3f}s, tỉ lệ {scale:.6f} "
            f"(sau 1 phút lệch ~{drift_min:.2f}s, cuối phim ~{drift_end:.2f}s). "
            "Sẽ kéo toàn bộ mốc thoại theo hình.",
            "warn" if drift_end >= 0.4 else "info",
        )
        if clocks.get("vfr"):
            logger("Video VFR (fps trung bình ≠ fps khai báo) — dùng duration "
                   "luồng hình, không nhân theo fps cố định.", "info")
    else:
        logger(
            f"Đồng hồ hình {picture:.3f}s khớp tiếng "
            f"{_as_float(clocks.get('audio_ref') or fmt):.3f}s.",
            "ok",
        )
    return clocks


def stretch_factor(current: float, target: float) -> Optional[float]:
    """Hệ số atempo để current trở thành target. None = chỉ pad/cắt đuôi.

    atempo = current/target: tiếng ngắn hơn hình (chạy trước) -> chậm lại.
    Đuôi đệm mix (+0.2s / +1s) và thiếu vài giây cuối phim không kéo tốc độ:
    kéo 0.17% cả tập làm tiếng chạy trước hình dù từng câu đã khóa đúng mốc.
    """
    try:
        current = float(current or 0.0)
        target = float(target or 0.0)
    except (TypeError, ValueError):
        return None
    if current <= 0.05 or target <= 0.05:
        return None
    delta = current - target
    if delta >= -_STRETCH_SHORT_MAX and delta <= _STRETCH_PAD_MAX:
        return None
    rel = abs(delta) / target
    if abs(delta) <= _STRETCH_TAIL_ABS and rel <= _STRETCH_TAIL_REL:
        return None
    factor = current / target
    if abs(factor - 1.0) < _STRETCH_REL_NOOP:
        return None
    if factor < _SCALE_HARD_MIN or factor > _SCALE_HARD_MAX:
        return None
    return factor


def scale_segment_times(segments: Sequence[Any], scale: float) -> int:
    """Nhân start/end (và xóa placed cũ). Trả số phần tử đã đổi."""
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        return 0
    if abs(scale - 1.0) < 1e-12:
        return 0
    n = 0
    for seg in segments or []:
        if seg is None:
            continue
        if isinstance(seg, dict):
            for key in ("start", "end", "placed"):
                if seg.get(key) is None:
                    continue
                try:
                    seg[key] = float(seg[key]) * scale
                except (TypeError, ValueError):
                    continue
            n += 1
            continue
        try:
            seg.start = float(seg.start) * scale
            seg.end = float(seg.end) * scale
            if getattr(seg, "end", 0) <= getattr(seg, "start", 0):
                seg.end = float(seg.start) + 0.01
            if hasattr(seg, "placed_start"):
                seg.placed_start = None
            n += 1
        except (TypeError, ValueError, AttributeError):
            continue
    return n


def sidecar_path(out_dir: str, stem: str) -> str:
    return os.path.join(out_dir, f"{stem}.av_clock.json")


def load_sidecar(path: str) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_sidecar(path: str, data: Dict[str, Any]) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _align_speech_map(m, target_scale: float):
    applied = float(getattr(m, "time_scale", 1.0) or 1.0)
    if abs(applied - target_scale) < 5e-5:
        return m, False
    need = target_scale / applied if applied else target_scale
    return m.scale(need), True


def _scale_speech_map(scale: float, map_path: Optional[str]) -> None:
    from . import speechmap
    active = speechmap.get_active()
    if active is not None and not active.empty:
        scaled, changed = _align_speech_map(active, scale)
        speechmap.set_active(scaled, map_path or speechmap.active_path())
        if changed and map_path:
            scaled.save(map_path)
        return
    if map_path and os.path.exists(map_path):
        loaded = speechmap.SpeechMap.load(map_path)
        if loaded is None or loaded.empty:
            return
        scaled, changed = _align_speech_map(loaded, scale)
        if changed:
            scaled.save(map_path)
        speechmap.set_active(scaled, map_path)


def apply_picture_clock(
    segments: Sequence[Any],
    *,
    video_path: str = "",
    pr: Optional[Dict] = None,
    audio_wav: Optional[str] = None,
    trust_wav: bool = True,
    sidecar: Optional[str] = None,
    speechmap_path: Optional[str] = None,
    reset: bool = False,
    log_fn: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Kéo timestamp ASR/SRT sang đồng hồ hình, một lần, chống nhân đôi.

    `reset=True` khi ASR mới (mốc theo WAV). SRT cũ không reset: đọc sidecar
    / options.av_clock_applied_scale để khỏi nhân tỉ lệ lần 2.
    """
    logger = log_fn or log
    clocks = (pr or {}).get("clocks") if pr else None
    if not clocks or _as_float((clocks or {}).get("picture_duration")) <= 0.5:
        path = video_path or ((pr or {}).get("video") if pr else "") or ""
        clocks = probe_media_clocks(path)
        if pr is not None:
            pr["clocks"] = clocks
            if _as_float(clocks.get("picture_duration")) > 0.5:
                pr["picture_duration"] = clocks["picture_duration"]
                pr["duration"] = clocks["picture_duration"]
    scale = scale_for_clocks(clocks, audio_wav=audio_wav, trust_wav=trust_wav)
    picture = _as_float(clocks.get("picture_duration"))
    state = load_sidecar(sidecar) if sidecar else {}
    opt = (pr or {}).setdefault("options", {}) if pr is not None else {}
    applied = None
    if not reset:
        raw = state.get("applied_scale")
        if raw is None and opt:
            raw = opt.get("av_clock_applied_scale")
        try:
            applied = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            applied = None
    else:
        applied = None

    if (not reset and applied is None
            and looks_already_scaled(segments, clocks, scale)):
        applied = scale
        logger("SRT đã theo đồng hồ hình — ghi nhận, không nhân tỉ lệ lần nữa.",
               "ok")
        payload = {
            "applied_scale": scale,
            "time_scale": scale,
            "picture_duration": picture,
            "audio_ref": clocks.get("audio_ref"),
            "vfr": bool(clocks.get("vfr")),
            "inferred": True,
        }
        if sidecar:
            save_sidecar(sidecar, payload)
        if opt is not None and pr is not None:
            opt["av_clock_applied_scale"] = scale

    info = {
        "scale": scale,
        "applied": False,
        "changed": 0,
        "clocks": clocks,
        "picture_duration": picture,
        "already": applied or 0.0,
    }
    if opt is not None and pr is not None:
        opt["av_time_scale"] = scale
        opt["picture_duration"] = picture

    if not should_apply_scale(scale, picture):
        # A later probe can look like 1.0 (tail pad, VFR jitter) while this
        # SRT was already stretched. Overwriting the sidecar with 1.0 would
        # hide the real scale and let a later reset=False skip unscale.
        if applied is not None and abs(applied - 1.0) > 5e-5:
            info["scale"] = applied
            info["already"] = applied
            logger(
                f"Đồng hồ hình đo lại ≈1.0 nhưng sidecar đang x{applied:.6f} "
                "— giữ tỉ lệ đã kéo, không ghi đè 1.0.",
                "ok",
            )
            return info
        payload = {
            "applied_scale": 1.0,
            "time_scale": 1.0,
            "picture_duration": picture,
            "audio_ref": clocks.get("audio_ref"),
            "vfr": bool(clocks.get("vfr")),
        }
        if sidecar:
            save_sidecar(sidecar, payload)
        if opt is not None and pr is not None:
            opt["av_clock_applied_scale"] = 1.0
        info["scale"] = 1.0
        return info

    if applied is not None and abs(applied - scale) < 5e-5:
        _scale_speech_map(scale, speechmap_path)
        logger(
            f"Mốc thoại đã theo đồng hồ hình (x{scale:.6f}) — không nhân lại.",
            "ok",
        )
        return info

    if applied is not None and abs(applied - 1.0) > 5e-5:
        # SRT đã kéo theo tỉ lệ cũ: về audio-clock rồi kéo lại.
        scale_segment_times(segments, 1.0 / applied)
        logger(
            f"Đổi tỉ lệ đồng hồ {applied:.6f} -> {scale:.6f}.",
            "info",
        )
    n = scale_segment_times(segments, scale)
    _scale_speech_map(scale, speechmap_path)
    payload = {
        "applied_scale": scale,
        "time_scale": scale,
        "picture_duration": picture,
        "audio_ref": clocks.get("audio_ref"),
        "vfr": bool(clocks.get("vfr")),
        "changed": n,
    }
    if sidecar:
        save_sidecar(sidecar, payload)
    if opt is not None and pr is not None:
        opt["av_clock_applied_scale"] = scale
    info["applied"] = True
    info["changed"] = n
    logger(
        f"Đã kéo {n} mốc thoại x{scale:.6f} theo đồng hồ hình "
        f"({picture:.3f}s) — khớp xuyên suốt, không chỉ đoạn đầu.",
        "ok",
    )
    return info


def describe_drift(scale: float, picture: float) -> str:
    scale = float(scale or 1.0)
    picture = float(picture or 0.0)
    if abs(scale - 1.0) < _SCALE_NOOP:
        return "Đồng hồ hình và tiếng khớp."
    per_min = abs(scale - 1.0) * 60.0
    end = abs(scale - 1.0) * picture if picture > 0 else 0.0
    side = "trước hình" if scale > 1.0 else "sau hình"
    return (f"Tỉ lệ {scale:.6f}: thoại chạy {side} ~{per_min:.2f}s/phút"
            + (f", cuối phim ~{end:.1f}s" if end else "") + ".")
