"""Xử lý audio/video bằng ffmpeg: tách audio, đổi tốc độ, ghép timeline,
che sub bằng blur, và render cuối (ưu tiên NVENC cho nhanh).
"""
from __future__ import annotations

import sys as _sys

from ..utils import (ffprobe_duration, ffprobe_fps, ffprobe_video_codec,
                    ffprobe_video_size, has_cuda_decode, has_nvenc, log,
                    nvenc_encode_args, run)
from . import assemble as _assemble_mod
from . import extract as _extract_mod
from . import final as _final_mod
from . import process as _process_mod
from . import sync_check as _sync_check_mod
from .assemble import (
    _CONCAT_BATCH, _assemble_ffmpeg_chunked, _assemble_stream_torch,
    _assemble_torch, _concat_audio_chunks, _concat_copy_chunks,
    _copy_concat_safe, _join_audio_chunks, _load_clip_for_stream,
    _mix_batch, _timeline_output_path, _timeline_pcm_gib,
    assemble_timeline_audio, concat_audio_clips,
)
from .common import (
    _audio_encode_args, _discard_partial, _pcm_mib, audio_duration_lock_chain,
    audio_muxer_args, discard_stale_media_temps, media_temp_path,
    preferred_asr_audio_path, seconds_to_samples,
)
from .extract import ensure_audio, extract_audio
from .final import render_final
from .process import (_atempo_chain, change_speed, compact_long_silences,
                      lock_audio_to_picture_duration, trim_silence,
                      _audio_beside_picture)
from .subs import _ass_color, _ffmpeg_sub_path, build_subtitle_style
from .sync_check import (SyncCheckFailed, assert_sync_allows_render,
                         check_dub_sync, verdict_from_metrics)

_pkg = _sys.modules[__name__]


def _bind_pkg_lookup(module, name: str, pkg=_pkg) -> None:
    if not hasattr(pkg, name):
        return

    def _proxy(*args, **kwargs):
        return getattr(pkg, name)(*args, **kwargs)

    orig = getattr(module, name, None)
    if callable(orig):
        _proxy.__name__ = getattr(orig, '__name__', name)
        _proxy.__qualname__ = getattr(orig, '__qualname__', name)
        _proxy.__doc__ = getattr(orig, '__doc__', None)
        setattr(module, name, _proxy)


for _mod in (_assemble_mod, _extract_mod, _final_mod, _process_mod, _sync_check_mod):
    for _name in (
        "run", "log", "ffprobe_duration", "ffprobe_fps",
        "ffprobe_video_codec", "ffprobe_video_size", "has_nvenc",
        "has_cuda_decode", "nvenc_encode_args",
    ):
        _bind_pkg_lookup(_mod, _name)

del _bind_pkg_lookup, _mod, _name
del _assemble_mod, _extract_mod, _final_mod, _process_mod, _sync_check_mod, _pkg, _sys
