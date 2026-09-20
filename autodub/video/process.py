"""Đổi tốc độ và cắt/rút khoảng lặng clip TTS."""
from __future__ import annotations

import json
import math
import os
from typing import Optional

from ..media_clock import stretch_factor
from ..utils import ffprobe_duration, log, run
from .common import _audio_encode_args, audio_duration_lock_chain, media_temp_path, _discard_partial


def _atempo_chain(speed: float) -> str:
    """atempo mỗi bộ lọc chỉ nhận 0.5–2.0 -> nối chuỗi nếu vượt."""
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        speed = 1.0
    if not math.isfinite(speed) or speed <= 0:
        speed = 1.0
    parts = []
    while speed > 2.0 + 1e-9:
        parts.append("atempo=2.0")
        speed /= 2.0
    while speed < 0.5 - 1e-9:
        parts.append("atempo=0.5")
        speed *= 2.0
    parts.append(f"atempo={speed:.6f}")
    return ",".join(parts)


def change_speed(in_wav: str, out_wav: str, speed: float,
                 max_duration: Optional[float] = None) -> str:
    """Tăng/giảm tốc độ audio mà KHÔNG đổi cao độ, có thể cắt theo slot."""
    try:
        speed = float(speed)
    except (TypeError, ValueError) as exc:
        raise ValueError("Tốc độ audio phải là số hữu hạn > 0") from exc
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("Tốc độ audio phải là số hữu hạn > 0")
    duration_limit = None
    try:
        if max_duration is not None:
            limit = float(max_duration)
            if math.isfinite(limit):
                duration_limit = max(0.01, limit)
    except (TypeError, ValueError):
        duration_limit = None

    temp_path = media_temp_path(out_wav)
    try:
        if abs(speed - 1.0) < 1e-3 and duration_limit is None:
            try:
                run(["ffmpeg", "-y", "-i", in_wav, "-c", "copy", temp_path])
            except InterruptedError:
                raise
            except Exception:
                _discard_partial(temp_path)
                run(["ffmpeg", "-y", "-i", in_wav, temp_path])
        else:
            filters = _atempo_chain(speed) if abs(speed - 1.0) >= 1e-3 else "anull"
            run(["ffmpeg", "-y", "-i", in_wav, "-filter:a", filters, temp_path])
        if duration_limit is not None:
            _fit_clip_to_slot(temp_path, duration_limit)
        if not os.path.isfile(temp_path) or os.path.getsize(temp_path) == 0:
            raise RuntimeError("Đổi tốc độ không tạo được audio")
        os.replace(temp_path, out_wav)
        return out_wav
    finally:
        _discard_partial(temp_path)


def _fit_clip_to_slot(path: str, duration_limit: float) -> str:
    """Bỏ im lặng trước; chỉ atrim lời khi clip vẫn dài hơn slot."""
    got = ffprobe_duration(path)
    if got <= duration_limit + 0.04:
        return path
    root, ext = os.path.splitext(path)
    ext = ext or ".wav"
    sil = root + ".slot_sil" + ext
    cmp = root + ".slot_cmp" + ext
    cut = root + ".slot_cut" + ext
    temps = [sil, cmp, cut]
    try:
        src = trim_silence(path, sil)
        src_dur = ffprobe_duration(src) if src and os.path.exists(src) else got
        if src_dur <= duration_limit + 0.04:
            if src != path and os.path.exists(src):
                os.replace(src, path)
            return path
        packed = compact_long_silences(src, cmp)
        packed_dur = ffprobe_duration(packed) if packed and os.path.exists(packed) else src_dur
        if packed_dur <= duration_limit + 0.04:
            if packed != path and os.path.exists(packed):
                os.replace(packed, path)
            return path
        # Slot siêu ngắn: giữ tối thiểu ~0.32s lời, tránh cắt "Á!" thành tiếng lách.
        cut_to = duration_limit
        if duration_limit < 0.32:
            cut_to = min(packed_dur, 0.32)
            if packed_dur <= cut_to + 0.04:
                if packed != path and os.path.exists(packed):
                    os.replace(packed, path)
                return path
        run([
            "ffmpeg", "-y", "-i", packed if packed and os.path.exists(packed) else src,
            "-filter:a",
            f"atrim=start=0:duration={cut_to:.3f},asetpts=PTS-STARTPTS",
            cut,
        ])
        if os.path.exists(cut) and os.path.getsize(cut) > 512:
            log(
                f"Cắt đuôi clip {os.path.basename(path)}: {packed_dur:.2f}s -> "
                f"{cut_to:.2f}s sau khi đã bỏ khoảng lặng.",
                "warn",
            )
            os.replace(cut, path)
        return path
    finally:
        for extra in temps:
            if extra != path and os.path.exists(extra):
                try:
                    os.remove(extra)
                except OSError:
                    pass


def trim_silence(in_path: str, out_path: str, threshold_db: int = -45,
                 keep_pad: float = 0.06) -> str:
    """Cắt khoảng lặng ĐẦU và CUỐI của một clip TTS.

    edge-tts luôn chèn một quãng lặng nhỏ ở hai đầu mỗi câu. Với 170 câu/tập,
    chỗ lặng thừa đó cộng lại thành hàng chục giây - chính là một phần khiến
    giọng đọc tụt lại phía sau hình. Cắt đi thì không mất chữ nào mà lấy lại
    được rất nhiều thời gian.
    """
    f = (f"silenceremove=start_periods=1:start_silence={keep_pad}:"
         f"start_threshold={threshold_db}dB:detection=peak,"
         "areverse,"
         f"silenceremove=start_periods=1:start_silence={keep_pad}:"
         f"start_threshold={threshold_db}dB:detection=peak,"
         "areverse")
    try:
        run(["ffmpeg", "-y", "-i", in_path, "-af", f, out_path], quiet=True)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 512:
            return out_path
    except InterruptedError:
        raise
    except Exception:
        pass
    return in_path          # cắt hỏng thì dùng bản gốc, không được làm mất câu


def _audio_beside_picture(path: str) -> str:
    """dub.picture.wav → dub.wav nếu file gốc còn trên đĩa."""
    root, ext = os.path.splitext(path or "")
    if not root.endswith(".picture"):
        return ""
    orig = root[: -len(".picture")] + (ext or ".wav")
    if orig == path or not os.path.exists(orig) or os.path.getsize(orig) <= 512:
        return ""
    return orig


def _lock_sidecar_path(out_path: str) -> str:
    return (out_path or "") + ".lock.json"


def _lock_fingerprint(src_path: str, target: float, sr: int) -> dict:
    st = os.stat(src_path)
    return {
        "algo": "atempo-pad-v1",
        "src": os.path.abspath(src_path),
        "src_size": int(st.st_size),
        "src_mtime_ns": int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
        "target_ms": int(round(float(target) * 1000)),
        "sr": int(sr or 48000),
    }


def _read_lock_fingerprint(out_path: str) -> Optional[dict]:
    side = _lock_sidecar_path(out_path)
    if not side or not os.path.exists(side):
        return None
    try:
        with open(side, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_lock_fingerprint(out_path: str, fingerprint: dict) -> None:
    side = _lock_sidecar_path(out_path)
    tmp = side + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(fingerprint, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, side)
    except OSError:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def lock_audio_to_picture_duration(
    in_path: str,
    target: float,
    sr: int = 48000,
    out_path: Optional[str] = None,
) -> str:
    """Khóa track giọng đúng độ dài hình: kéo atempo nếu lệch tốc độ, rồi pad/cắt mẫu.

    Tiếng ngắn hơn hình (thoại chạy trước, lệch dần theo phút) -> chậm lại, giữ cao độ.
    Đuôi đệm mix (+0.2s/+1s) chỉ cắt, không đổi tốc độ nói.
    Bản .picture đã kéo nhầm khi chỉ thiếu đuôi thì pad lại từ dub gốc.
    """
    if not in_path or not os.path.exists(in_path):
        return in_path
    try:
        target = float(target or 0.0)
    except (TypeError, ValueError):
        return in_path
    if target <= 0.05:
        return in_path
    orig = _audio_beside_picture(in_path)
    if orig:
        orig_dur = ffprobe_duration(orig)
        if orig_dur > 0.05 and stretch_factor(orig_dur, target) is None:
            log("Bỏ bản đã kéo tốc độ (chỉ thiếu đuôi) — đệm im lặng từ track gốc.",
                "info")
            in_path = orig
            out_path = None
    current = ffprobe_duration(in_path)
    if current <= 0.05:
        return in_path
    factor = stretch_factor(current, target)
    if factor is None and abs(current - target) < 0.25:
        return in_path
    if not out_path:
        root, ext = os.path.splitext(in_path)
        if root.endswith(".picture"):
            out_path = in_path
        else:
            out_path = root + ".picture" + (ext or ".wav")
    fingerprint = _lock_fingerprint(in_path, target, sr)
    if (os.path.exists(out_path) and os.path.getsize(out_path) > 512
            and _read_lock_fingerprint(out_path) == fingerprint):
        log("Dùng lại track đã khóa theo hình (fingerprint khớp).", "ok")
        return out_path
    filters = []
    if factor is not None:
        filters.append(_atempo_chain(factor))
        log(
            f"Kéo đồng hồ tiếng x{factor:.6f} (giữ cao độ) "
            f"{current:.3f}s -> {target:.3f}s để khớp hình xuyên suốt.",
            "ok",
        )
    elif abs(current - target) >= 0.05:
        log(
            f"Cắt/đệm track giọng {current:.3f}s cho đúng {target:.3f}s "
            "(đuôi đệm, không đổi tốc độ nói).",
            "info",
        )
    filters.append(audio_duration_lock_chain(target, sr=sr, async_resample=True))
    try:
        run([
            "ffmpeg", "-y", "-i", in_path,
            "-af", ",".join(filters),
            "-ar", str(int(sr or 48000)), "-ac", "2",
            *_audio_encode_args(out_path), out_path,
        ])
    except Exception as exc:
        log(f"Không khóa được track giọng theo hình ({exc}) — giữ bản cũ.", "warn")
        return in_path
    if os.path.exists(out_path) and os.path.getsize(out_path) > 512:
        _write_lock_fingerprint(out_path, fingerprint)
        return out_path
    return in_path


def compact_long_silences(in_path: str, out_path: str,
                          threshold_db: int = -35,
                          trigger_duration: float = 0.10,
                          keep_silence: float = 0.12) -> str:
    """Rút các khoảng im bất thường bên trong clip TTS nhưng không cắt lời.

    Một số lần edge-tts trả audio có gần một giây im sau từng từ. Chỉ gọi hàm
    này khi lớp trên đã phát hiện clip dài bất thường so với số ký tự; vì thế
    nhịp kể bình thường của các engine khác không bị can thiệp.
    """
    f = ("silenceremove=stop_periods=-1:"
         f"stop_duration={max(0.05, float(trigger_duration)):.3f}:"
         f"stop_threshold={int(threshold_db)}dB:"
         f"stop_silence={max(0.05, float(keep_silence)):.3f}:detection=rms")
    try:
        run(["ffmpeg", "-y", "-i", in_path, "-af", f,
             *_audio_encode_args(out_path), out_path], quiet=True)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 512:
            return out_path
    except Exception:
        pass
    return in_path
