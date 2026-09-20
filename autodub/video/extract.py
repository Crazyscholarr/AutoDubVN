"""Tách audio từ video cho ASR."""
from __future__ import annotations

import os
import json
import time
from typing import Optional

from ..media_clock import probe_media_clocks
from ..utils import ffprobe_duration, log, run
from .common import (_audio_encode_args, _discard_partial, _pcm_mib,
                     audio_muxer_args, discard_stale_media_temps,
                     media_temp_path, preferred_asr_audio_path)

_EXTRACT_MIN_BYTES = 1024


def _extract_temp_is_valid(path: str, sr: int) -> bool:
    if not os.path.exists(path) or os.path.getsize(path) <= _EXTRACT_MIN_BYTES:
        return False
    try:
        duration = ffprobe_duration(path)
        if not duration or duration <= 0:
            return False
        probe = run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                     "-show_entries", "stream=sample_rate,channels",
                     "-of", "json", path])
        streams = json.loads(probe.stdout or "{}").get("streams") or []
        if not streams:
            return False
        if int(streams[0].get("sample_rate") or 0) != int(sr):
            return False
        return int(streams[0].get("channels") or 0) == 1
    except InterruptedError:
        raise
    except Exception:
        return False


def _commit_extracted_audio(temp_path: str, final_path: str, sr: int) -> None:
    if not _extract_temp_is_valid(temp_path, sr):
        _discard_partial(temp_path)
        raise RuntimeError(f"ASR extract temp invalid: {temp_path}")
    os.replace(temp_path, final_path)


def _run_extract_command(cmd_prefix, out_wav: str, sr: int) -> None:
    discard_stale_media_temps(out_wav)
    temp_path = media_temp_path(out_wav)
    if os.path.splitext(temp_path)[1].lower() != os.path.splitext(out_wav)[1].lower():
        raise RuntimeError(
            f"ASR extract temp lost media suffix: {temp_path} vs {out_wav}")
    _discard_partial(temp_path)
    # Muxer comes from the requested final type, not the temp name, so a
    # leftover .flac.partial-style bug cannot hide behind FFmpeg's guesser.
    cmd = list(cmd_prefix) + [*_audio_encode_args(out_wav), *audio_muxer_args(out_wav), temp_path]
    try:
        run(cmd)
        _commit_extracted_audio(temp_path, out_wav, sr)
    except InterruptedError:
        _discard_partial(temp_path)
        raise
    except Exception:
        _discard_partial(temp_path)
        if os.path.exists(out_wav) and os.path.getsize(out_wav) <= _EXTRACT_MIN_BYTES:
            _discard_partial(out_wav)
        raise

def extract_audio(video: str, out_wav: str, sr: int = 16000,
                  loudnorm: bool = True, trim_start: float = 0.0,
                  trim_duration: Optional[float] = None) -> str:
    """Tách audio về mono 16kHz - chuẩn đầu vào cho ASR.

    loudnorm=True: chuẩn hoá âm lượng. Rất quan trọng vì audio quá nhỏ khiến VAD
    nằm sát ngưỡng quyết định và bỏ sót cả đoạn có tiếng nói.
    Nếu bản mono bị triệt tiêu (2 kênh ngược pha), tự lấy riêng kênh trái.
    """
    af = "loudnorm=I=-16:TP=-1.5:LRA=11" if loudnorm else None
    started = time.perf_counter()
    trim_start = max(0.0, float(trim_start or 0.0))
    trim_duration = (None if trim_duration is None
                     else max(0.01, float(trim_duration)))
    cmd = ["ffmpeg", "-y"]
    if trim_start > 0:
        cmd += ["-ss", f"{trim_start:.3f}"]
    if trim_duration is not None:
        cmd += ["-t", f"{trim_duration:.3f}"]
    cmd += ["-i", video, "-vn", "-ac", "1", "-ar", str(sr)]
    if af:
        cmd += ["-af", af]
    parent = os.path.dirname(os.path.abspath(out_wav)) or "."
    os.makedirs(parent, exist_ok=True)
    _run_extract_command(cmd, out_wav, sr)

    # Kiểm tra bản mono có bị "câm" bất thường không
    try:
        res = run(["ffmpeg", "-hide_banner", "-i", out_wav, "-af", "volumedetect",
                   "-f", "null", "-"], check=False)
        import re as _re
        m = _re.search(r"mean_volume:\s*(-?[\d.]+) dB", res.stderr or "")
        if m and float(m.group(1)) < -60:
            log("Bản mono gần như im lặng (2 kênh có thể ngược pha) - "
                "thử lấy riêng kênh trái...", "warn")
            cmd2 = ["ffmpeg", "-y"]
            if trim_start > 0:
                cmd2 += ["-ss", f"{trim_start:.3f}"]
            if trim_duration is not None:
                cmd2 += ["-t", f"{trim_duration:.3f}"]
            cmd2 += ["-i", video, "-vn", "-ar", str(sr),
                     "-af", "pan=mono|c0=c0" + (f",{af}" if af else "")]
            _run_extract_command(cmd2, out_wav, sr)
    except InterruptedError:
        raise
    except Exception:
        if not os.path.exists(out_wav) or os.path.getsize(out_wav) <= _EXTRACT_MIN_BYTES:
            raise
        pass
    log(f"Audio extraction including mono validation: {time.perf_counter()-started:.2f}s "
        f"({os.path.splitext(out_wav)[1]}, {sr} Hz)", "info")
    return out_wav


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    text = str(value).strip().lower()
    if text in ('true', '1', 'yes', 'on'):
        return True
    if text in ('false', '0', 'no', 'off', ''):
        return False
    return bool(value)


def _identity_value(key, value):
    if key == 'source':
        return os.path.normcase(os.path.abspath(str(value)))
    if key in ('size', 'sr', 'mtime_ns'):
        return int(value)
    if key == 'loudnorm':
        return _as_bool(value)
    if key == 'trim_start':
        return float(value or 0.0)
    if key == 'trim_duration':
        return None if value in (None, '') else float(value)
    return value


def _source_identity_matches(stored, identity, ignore=('mtime_ns', 'extracted_duration')):
    """Sidecar may grow extra keys. mtime is ignored: copy/AV scanners bump it."""
    if not isinstance(stored, dict):
        return False
    skipped = set(ignore or ())
    for key, value in identity.items():
        if key in skipped:
            continue
        try:
            left = _identity_value(key, stored.get(key))
            right = _identity_value(key, value)
        except (TypeError, ValueError, OSError):
            return False
        if left != right:
            return False
    return True


def _duration_reusable(audio_dur, video_dur, loudnorm, recorded=None):
    if not audio_dur or audio_dur <= 0:
        return False
    if recorded not in (None, ''):
        try:
            return abs(float(audio_dur) - float(recorded)) <= 0.5
        except (TypeError, ValueError):
            return False
    if video_dur is None or video_dur <= 0:
        return True
    lo = float(video_dur) - 0.5
    hi = float(video_dur) + (5.0 if loudnorm else 0.5)
    return lo <= float(audio_dur) <= hi


def _write_audio_sidecar(path, identity, extracted_duration=None):
    payload = dict(identity)
    if extracted_duration is not None:
        try:
            payload['extracted_duration'] = float(extracted_duration)
        except (TypeError, ValueError):
            pass
    with open(path, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream)


def _reuse_skip_detail(skip_reason, out_wav, metadata):
    if skip_reason != 'missing_file':
        return skip_reason
    exists = os.path.exists(out_wav)
    size = os.path.getsize(out_wav) if exists else 0
    sidecar = 'có sidecar' if os.path.exists(metadata) else 'không sidecar'
    return f"{skip_reason}: {out_wav} size={size} {sidecar}"


def ensure_audio(video: str, out_wav: str, sr: int = 16000,
                 loudnorm: bool = True, reuse_existing: bool = True,
                 trim_start: float = 0.0,
                 trim_duration: Optional[float] = None) -> str:
    """Tách audio nếu cần; dùng lại file đã tách khi cùng nguồn và cùng bộ lọc."""
    target_duration = None
    if trim_duration is not None:
        target_duration = max(0.01, float(trim_duration))
    else:
        try:
            clocks = probe_media_clocks(video)
            # So WAV với đồng hồ TIẾNG (ASR), không phải PTS hình — lệch 1%
            # hình/tiếng là bình thường và không phải lý do tách lại.
            target_duration = (
                float(clocks.get("audio_duration") or 0.0)
                or float(clocks.get("format_duration") or 0.0)
                or ffprobe_duration(video)
            )
        except Exception:
            target_duration = None
            try:
                target_duration = ffprobe_duration(video)
            except Exception:
                target_duration = None
    if target_duration:
        if trim_duration is None:
            target_duration = max(0.0, target_duration - max(0.0, float(trim_start or 0)))
        preferred = preferred_asr_audio_path(out_wav, target_duration, sr)
        if preferred != out_wav:
            log(
                f"Audio ASR dai (~{_pcm_mib(target_duration, sr):.0f} MiB neu WAV) "
                f"- luu {os.path.basename(preferred)} de tiet kiem o dia.",
                "info",
            )
            out_wav = preferred
    # Jobs created by a previous WAV/FLAC policy can already have valid audio.
    if reuse_existing and not os.path.exists(out_wav):
        stem, ext = os.path.splitext(out_wav)
        alternate = stem + ('.flac' if ext.lower() == '.wav' else '.wav')
        if ext.lower() in ('.wav', '.flac') and os.path.exists(alternate):
            out_wav = alternate
    stat = os.stat(video)
    identity = dict(source=os.path.normcase(os.path.abspath(video)), size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns, sr=sr, loudnorm=loudnorm,
                    trim_start=trim_start, trim_duration=trim_duration)
    metadata = out_wav + '.source.json'
    stored = None
    if os.path.exists(metadata):
        try:
            with open(metadata, encoding='utf-8') as stream:
                stored = json.load(stream)
        except (OSError, ValueError):
            stored = None
    skip_reason = None
    if not reuse_existing:
        skip_reason = 'reuse_disabled'
    elif not os.path.exists(out_wav) or os.path.getsize(out_wav) <= _EXTRACT_MIN_BYTES:
        skip_reason = 'missing_file'
    elif stored is not None and not _source_identity_matches(stored, identity):
        skip_reason = 'source_identity'
    if skip_reason is None and reuse_existing:
        try:
            audio_dur = ffprobe_duration(out_wav)
            probe = run(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                         '-show_entries', 'stream=sample_rate,channels', '-of', 'json', out_wav])
            streams = json.loads(probe.stdout).get('streams', [])
            valid_format = bool(streams and int(streams[0].get('sample_rate', 0)) == sr
                                and streams[0].get('channels') == 1)
        except InterruptedError:
            raise
        except Exception:
            audio_dur, valid_format = 0, False
        if not valid_format:
            skip_reason = 'format'
        else:
            # loudnorm + 16 kHz can finish a couple of seconds longer than the
            # container clock. Comparing that extract to the video then
            # re-extracts the same file and busts the ASR chunk ledger.
            recorded = stored.get('extracted_duration') if isinstance(stored, dict) else None
            video_dur = target_duration
            if not _duration_reusable(audio_dur, video_dur, loudnorm, recorded):
                skip_reason = 'duration'
            else:
                if stored is None or stored.get('extracted_duration') in (None, ''):
                    _write_audio_sidecar(metadata, identity, audio_dur)
                elif stored.get('mtime_ns') != identity.get('mtime_ns'):
                    _write_audio_sidecar(metadata, identity,
                                         stored.get('extracted_duration', audio_dur))
                log(f"Dùng lại audio đã tách: {out_wav}", "ok")
                return out_wav
    if skip_reason:
        kind = 'warn' if skip_reason in ('duration', 'source_identity', 'format') else 'info'
        log(f"Không dùng lại audio ({_reuse_skip_detail(skip_reason, out_wav, metadata)}) "
            f"- tách lại từ đầu.", kind)
        if (skip_reason == 'missing_file' and os.path.exists(out_wav)
                and os.path.getsize(out_wav) <= _EXTRACT_MIN_BYTES):
            _discard_partial(out_wav)
    result = extract_audio(video, out_wav, sr=sr, loudnorm=loudnorm,
                           trim_start=trim_start, trim_duration=trim_duration)
    extracted = None
    try:
        extracted = ffprobe_duration(result)
    except Exception:
        extracted = None
    _write_audio_sidecar(metadata, identity, extracted)
    return result
