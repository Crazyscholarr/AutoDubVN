"""Chuyển phụ đề tiếng Việt thành giọng nói + gán NHIỀU GIỌNG cho nhân vật +
áp thuật toán CHỐNG ĐÈ THOẠI.

Hỗ trợ 2 engine TTS (tts.engine trong config.yaml):
  - "edge"   (mặc định) - edge-tts, miễn phí, 2 giọng Việt gốc (nữ HoaiMy, nam
             NamMinh). Để có "nhiều giọng nhân vật", tạo biến thể bằng cách
             đổi nhẹ cao độ (pitch) -> ra nhiều chất giọng nam/nữ, già/trẻ.
  - "vieneu" - VieNeu-TTS (github.com/pnnbao97/VieNeu-TTS), mã nguồn mở, chạy
             LOCAL (CPU/GPU), giọng tự nhiên hơn hẳn, có nhiều giọng dựng sẵn
             thật (không cần giả lập bằng pitch) + hỗ trợ nhân bản giọng.
             Cần cài: pip install vieneu
"""
from __future__ import annotations

import sys as _sys

from ..asr import is_speakable
from ..srt_utils import Segment, split_vi_text_naturally
from ..timeline import Placement, auto_fit, fit_segments_strict
from ..utils import ffprobe_duration, log
from ..video import (change_speed, compact_long_silences, concat_audio_clips,
                    trim_silence)

from . import capcut as _capcut_mod
from . import edge as _edge_mod
from . import vieneu as _vieneu_mod
from . import timeline as _timeline_mod
from . import concat as _concat_mod
from .common import (
    CAPCUT_DEFAULT_VOICE,
    DEFAULT_NARRATOR,
    VOICE_PRESETS,
    _PITCH_RE,
    _RETRYABLE_ERRORS,
    _channel_cta_speed,
    _clean_partial,
    _combine_pitch_hz,
    _edge_voice_and_pitch,
    _format_pitch_hz,
    _format_ts,
    _is_channel_cta_text,
    _pitch_value,
    _project_root,
    _raise_if_cancelled,
    normalize_edge_narrator,
)
from .edge import _synth_all, _synth_one
from .capcut import (
    _CAPCUT_CLIENT,
    _CAPCUT_CLIENT_KEY,
    _CAPCUT_ERROR,
    _CAPCUT_LOCK,
    _CAPCUT_STATUS_LOCK,
    _capcut_catalog_path,
    _capcut_rate,
    _capcut_sdk_path,
    _capcut_speech_urls,
    _capcut_status_path,
    _capcut_synth_via_http_api,
    _load_capcut_client,
    _load_capcut_voice_status,
    _record_capcut_voice_status,
    _synth_all_capcut,
    _synth_one_capcut,
)
from .vieneu import (
    _VIENEU_ERROR,
    _VIENEU_LOCK,
    _VIENEU_MODEL,
    _VIENEU_REPOS,
    _hf_cache_root,
    _hf_is_cached,
    _load_vieneu_model,
    _set_hf_offline,
    _synth_all_vieneu,
    _synth_one_vieneu,
    _vieneu_preset_voices,
    prefetch_models,
    reset_vieneu_error,
    unpatch_modelscope_hub,
)
from .timeline import (
    assign_voices,
    build_narration_timeline,
    build_voice_track,
    list_voices,
)
from .concat import synthesize_text_audio

_pkg = _sys.modules[__name__]


def _bind_pkg_lookup(module, name: str, pkg=_pkg) -> None:
    if not hasattr(pkg, name) or not hasattr(module, name):
        return
    orig = getattr(pkg, name)
    if not callable(orig):
        return

    def _proxy(*args, **kwargs):
        return getattr(pkg, name)(*args, **kwargs)

    _proxy.__name__ = getattr(orig, "__name__", name)
    _proxy.__qualname__ = getattr(orig, "__qualname__", name)
    _proxy.__doc__ = getattr(orig, "__doc__", None)
    setattr(module, name, _proxy)


for _mod, _names in (
    (_edge_mod, (
        "_synth_one", "_synth_all", "_raise_if_cancelled", "_clean_partial",
        "_edge_voice_and_pitch", "log",
    )),
    (_capcut_mod, (
        "_synth_one_capcut", "_synth_all_capcut", "_raise_if_cancelled",
        "_clean_partial", "_record_capcut_voice_status",
        "_load_capcut_client", "_load_capcut_voice_status",
        "_capcut_catalog_path", "_capcut_sdk_path", "_capcut_status_path",
        "_capcut_rate", "_capcut_speech_urls", "_capcut_synth_via_http_api",
        "_project_root", "log", "is_speakable",
    )),
    (_vieneu_mod, (
        "_load_vieneu_model", "_synth_one_vieneu", "_synth_all_vieneu",
        "_vieneu_preset_voices", "prefetch_models", "reset_vieneu_error",
        "unpatch_modelscope_hub", "_hf_cache_root", "_hf_is_cached",
        "_set_hf_offline", "_raise_if_cancelled", "_clean_partial", "log",
    )),
    (_timeline_mod, (
        "list_voices", "assign_voices", "build_narration_timeline",
        "build_voice_track", "normalize_edge_narrator",
        "_vieneu_preset_voices", "_load_vieneu_model",
        "_load_capcut_client", "_load_capcut_voice_status",
        "_capcut_catalog_path", "_synth_all", "_synth_all_capcut",
        "_synth_all_vieneu", "_format_ts", "log", "ffprobe_duration",
        "change_speed", "trim_silence", "is_speakable",
    )),
    (_concat_mod, (
        "synthesize_text_audio", "assign_voices", "build_narration_timeline",
        "_synth_all", "_synth_all_capcut", "_synth_all_vieneu",
        "_edge_voice_and_pitch", "_is_channel_cta_text", "_channel_cta_speed",
        "_raise_if_cancelled", "change_speed", "concat_audio_clips",
        "compact_long_silences", "ffprobe_duration", "log", "is_speakable",
    )),
):
    for _name in _names:
        _bind_pkg_lookup(_mod, _name)

del _bind_pkg_lookup, _mod, _names, _name
del _capcut_mod, _concat_mod, _edge_mod, _timeline_mod, _vieneu_mod, _pkg, _sys
