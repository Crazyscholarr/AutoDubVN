"""Tiện ích chung cho tách/ghép audio-video."""
from __future__ import annotations

import os
import uuid
from typing import List, Optional

_AUDIO_MUXERS = {
    ".flac": "flac",
    ".wav": "wav",
    ".m4a": "ipod",
    ".aac": "adts",
    ".mp3": "mp3",
}


def _discard_partial(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def media_temp_path(final_path: str, token: Optional[str] = None) -> str:
    """Keep the media suffix last so FFmpeg can infer the muxer.

    audio16k.flac -> audio16k.<token>.partial.flac
    never audio16k.flac.partial
    """
    directory, name = os.path.split(os.path.abspath(final_path))
    stem, ext = os.path.splitext(name)
    marker = (token or uuid.uuid4().hex[:12]).replace(".", "")
    return os.path.join(directory or ".", f"{stem}.{marker}.partial{ext}")


def audio_muxer_args(out_path: str) -> List[str]:
    fmt = _AUDIO_MUXERS.get(os.path.splitext(out_path)[1].lower())
    return ["-f", fmt] if fmt else []


def discard_stale_media_temps(final_path: str) -> None:
    """Remove leftover atomic temps, including the broken .flac.partial form."""
    final_path = os.path.abspath(final_path)
    _discard_partial(final_path + ".partial")
    directory, name = os.path.split(final_path)
    stem, ext = os.path.splitext(name)
    prefix = stem + "."
    suffix = ".partial" + ext
    try:
        names = os.listdir(directory or ".")
    except OSError:
        return
    for item in names:
        if item.startswith(prefix) and item.endswith(suffix) and item != name:
            _discard_partial(os.path.join(directory, item))


def _pcm_mib(total_duration: float, sr: int, channels: int = 1,
             bytes_per_sample: int = 2) -> float:
    return (max(0.0, float(total_duration or 0.0))
            * sr * channels * bytes_per_sample) / (1024 ** 2)


def preferred_asr_audio_path(out_path: str, total_duration: float,
                             sr: int = 16000) -> str:
    """Prefer FLAC for long ASR extracts to avoid huge temporary WAV files."""
    if (os.path.splitext(out_path)[1].lower() == ".wav"
            and (float(total_duration or 0.0) >= 1800.0
                 or _pcm_mib(total_duration, sr) >= 256.0)):
        return os.path.splitext(out_path)[0] + ".flac"
    return out_path

def seconds_to_samples(seconds: float, sr: int) -> int:
    sr = max(1, int(sr or 48000))
    try:
        seconds = float(seconds or 0.0)
    except (TypeError, ValueError):
        seconds = 0.0
    return max(0, int(round(seconds * sr)))


def audio_duration_lock_chain(duration: float, sr: int = 48000,
                              async_resample: bool = True) -> str:
    """Pad/cắt audio đúng số mẫu của video và khóa PTS (aresample async).

    Trên phim dài, cắt theo giây `.3f` rồi concat/mux làm track bị co dần:
    thoại chạy trước hình, càng về cuối càng lệch. Ép `end_sample` theo
    sample-rate cố định + `async=1` để đồng hồ audio bám PTS hình.
    """
    sr = max(1, int(sr or 48000))
    samples = max(1, seconds_to_samples(duration, sr))
    parts = []
    if async_resample:
        parts.append(f"aresample={sr}:async=1:first_pts=0")
    parts.extend([
        "apad",
        f"atrim=end_sample={samples}",
        "asetpts=PTS-STARTPTS",
    ])
    return ",".join(parts)


def _audio_encode_args(out_path: str) -> List[str]:
    ext = os.path.splitext(out_path)[1].lower()
    if ext == ".flac":
        return ["-c:a", "flac", "-compression_level", "5"]
    if ext in (".m4a", ".aac"):
        return ["-c:a", "aac", "-b:a", "192k"]
    if ext == ".mp3":
        return ["-c:a", "libmp3lame", "-b:a", "192k"]
    if ext == ".wav":
        return ["-rf64", "auto", "-c:a", "pcm_s16le"]
    return ["-c:a", "pcm_s16le"]
