"""Dịch phụ đề sang tiếng Việt, giữ NGỮ ĐIỆU và NHẤT QUÁN NHÂN VẬT.

Hai chế độ:
  - provider: gemini   -> gọi REST API (ổn định nhất, nên dùng nếu có API key)
  - provider: browser  -> điều khiển Edge/Chrome vào gemini.google.com, dùng
                          phiên đăng nhập Pro sẵn có, KHÔNG cần API key.

Chiến lược giữ nhất quán:
  - Dịch theo lô (chunk) kèm ngữ cảnh vài dòng đã dịch trước đó.
  - Nếu có nhãn người nói (speaker) thì đưa vào để mỗi nhân vật giữ văn phong riêng.
  - Bắt buộc trả về ĐÚNG số dòng, không thêm chú thích/đánh số.

CHỐNG MẤT CÔNG: mọi lô dịch xong đều được ghi ngay vào file cache cạnh output.
Chạy lại sau khi lỗi/tắt máy sẽ bỏ qua các lô đã dịch, không làm lại từ đầu.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys as _sys
import time
import urllib.request
import urllib.error
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

from ..srt_utils import Segment, normalize_vi_subtitle_text
from ..utils import log
from ..providers import (
    TOKENROUTER_DEFAULT_BASE_URL, TOKENROUTER_DEFAULT_MODEL,
    TOKENROUTER_GEMINI_DEFAULT_BASE_URL, TOKENROUTER_GEMINI_DEFAULT_MODEL,
    INFERX_DEFAULT_BASE_URL, INFERX_DEFAULT_MODEL,
    NVIDIA_DEFAULT_BASE_URL, NVIDIA_DEFAULT_MODEL, NVIDIA_FAST_MODEL,
    NVIDIA_PRO_MODEL, NVIDIA_FLASH_MODEL,
    ZENMUX_DEFAULT_BASE_URL, ZENMUX_DEFAULT_MODEL,
    TOKENHARBOR_DEFAULT_BASE_URL, TOKENHARBOR_DEFAULT_MODEL,
    api_params_for_provider, nvidia_chat_extras as _nvidia_chat_extras,
    strip_think as _strip_think,
)

from .const import (
    ENABLE_BROWSER_SHORTENING,
    GEMINI_URL,
    SHORTEN_KEEP_RATIO,
    SHORTEN_KEEP_RATIO_LONG,
    SHORTEN_TRIGGER_RATIO,
    STYLE_LOCK,
    SYSTEM_INSTRUCTION,
    TRANSLATION_BUDGET_MARGIN,
    TRANSLATION_CACHE_VERSION,
    TRANSLATION_MIN_CHARS,
    brief_confirmed,
    essay_hit_count,
    looks_like_document_vi,
    session_brief,
    _CJK_RE,
)
from .cache import ChunkCache, TranslationIncomplete
from .api import (
    _GEMINI_FALLBACK_MODELS,
    _anthropic_messages_call,
    _api_call,
    _gemini_call,
    _gemini_model_candidates,
    _http_error_summary,
    _message_content_to_text,
    _openai_compatible_call,
    _openai_compatible_chat_url,
    _openai_message_text,
    _tokenharbor_call,
    _tokenrouter_gemini_call,
    _tokenrouter_gemini_url,
)
from .parse import (
    _MD_EDGE,
    _MEANING_PHRASE_RULES,
    _NUMBER_RE,
    _NUM_LINE,
    _PROPER_NOUN_RE,
    _accept_shortened,
    _bad_line_summary,
    _build_prompt,
    _build_shorten_prompt,
    _cjk_line_numbers,
    _clean_vi_lines,
    _contains_cjk,
    _entities,
    _keeps_core_meaning,
    _keeps_entities,
    _norm_vi,
    _parse_json_lines,
    _too_long_for_tts,
    build_film_hint,
    build_name_hint,
    char_budget,
    log_reading_pressure,
    match_by_position,
    parse_numbered_reply,
    reading_pressure,
    shorten_long_lines,
    spoken_header,
)
from .browser import (
    _INPUT_CANDIDATES,
    _JS_EXTRACT,
    _PHAN_TICH_EASE_RE,
    _PHAN_TICH_RECO_RE,
    _RESP_CANDIDATES,
    _RESP_FALLBACK,
    _RESP_SEL_CHOSEN,
    _SEND_CANDIDATES,
    _SO_DONG_RE,
    _STOP_CANDIDATES,
    _STOP_LABEL_JS,
    _ask_once,
    _chon_tra_loi,
    _clear_composer,
    _co_nhieu_dong_danh_so,
    _dem_khoi_tra_loi,
    _dump_debug,
    _is_generating,
    _json_con_do_dang,
    _json_ngoac_chua_khop,
    _json_phan_tich_du,
    _launch,
    _mo_chat_moi,
    _put_text,
    _reply_text,
    _resp_locator,
    _submit,
    _thu_thap_tra_loi,
    _visible_locator,
    _wait_reply,
    phien_gemini_trinh_duyet,
    translate_via_browser,
)
from .pipeline import translate_segments
from . import api as _api_mod
from . import pipeline as _pipeline_mod

# mock.patch.object(translate, name) vá tên trên package; hàm con vẫn tra
# global của module gốc. Gắn proxy để lần gọi nội bộ nhìn thấy bản đã vá.
_pkg = _sys.modules[__name__]


def _bind_pkg_lookup(module, name: str, pkg=_pkg) -> None:
    def _proxy(*args, **kwargs):
        return getattr(pkg, name)(*args, **kwargs)
    _proxy.__name__ = name
    _proxy.__qualname__ = name
    setattr(module, name, _proxy)


for _patched in (
    "_openai_compatible_call",
    "_anthropic_messages_call",
    "_tokenharbor_call",
    "_tokenrouter_gemini_call",
    "_gemini_call",
):
    _bind_pkg_lookup(_api_mod, _patched)
_bind_pkg_lookup(_pipeline_mod, "_api_call")

del _bind_pkg_lookup, _patched, _api_mod, _pipeline_mod, _pkg, _sys

__all__ = [
    "ENABLE_BROWSER_SHORTENING",
    "GEMINI_URL",
    "INFERX_DEFAULT_BASE_URL",
    "INFERX_DEFAULT_MODEL",
    "NVIDIA_DEFAULT_BASE_URL",
    "NVIDIA_DEFAULT_MODEL",
    "NVIDIA_FAST_MODEL",
    "NVIDIA_FLASH_MODEL",
    "NVIDIA_PRO_MODEL",
    "SHORTEN_KEEP_RATIO",
    "SHORTEN_KEEP_RATIO_LONG",
    "SHORTEN_TRIGGER_RATIO",
    "SYSTEM_INSTRUCTION",
    "STYLE_LOCK",
    "TOKENHARBOR_DEFAULT_BASE_URL",
    "TOKENHARBOR_DEFAULT_MODEL",
    "TOKENROUTER_DEFAULT_BASE_URL",
    "TOKENROUTER_DEFAULT_MODEL",
    "TOKENROUTER_GEMINI_DEFAULT_BASE_URL",
    "TOKENROUTER_GEMINI_DEFAULT_MODEL",
    "TRANSLATION_BUDGET_MARGIN",
    "TRANSLATION_CACHE_VERSION",
    "TRANSLATION_MIN_CHARS",
    "ZENMUX_DEFAULT_BASE_URL",
    "ZENMUX_DEFAULT_MODEL",
    "ChunkCache",
    "TranslationIncomplete",
    "api_params_for_provider",
    "build_film_hint",
    "build_name_hint",
    "brief_confirmed",
    "char_budget",
    "essay_hit_count",
    "looks_like_document_vi",
    "log",
    "log_reading_pressure",
    "match_by_position",
    "normalize_vi_subtitle_text",
    "parse_numbered_reply",
    "phien_gemini_trinh_duyet",
    "reading_pressure",
    "shorten_long_lines",
    "session_brief",
    "spoken_header",
    "translate_segments",
    "translate_via_browser",
    "_CJK_RE",
    "_GEMINI_FALLBACK_MODELS",
    "_INPUT_CANDIDATES",
    "_JS_EXTRACT",
    "_MD_EDGE",
    "_MEANING_PHRASE_RULES",
    "_NUMBER_RE",
    "_NUM_LINE",
    "_PHAN_TICH_EASE_RE",
    "_PHAN_TICH_RECO_RE",
    "_PROPER_NOUN_RE",
    "_RESP_CANDIDATES",
    "_RESP_FALLBACK",
    "_RESP_SEL_CHOSEN",
    "_SEND_CANDIDATES",
    "_SO_DONG_RE",
    "_STOP_CANDIDATES",
    "_STOP_LABEL_JS",
    "_accept_shortened",
    "_anthropic_messages_call",
    "_api_call",
    "_ask_once",
    "_bad_line_summary",
    "_build_prompt",
    "_build_shorten_prompt",
    "_chon_tra_loi",
    "_cjk_line_numbers",
    "_clean_vi_lines",
    "_clear_composer",
    "_co_nhieu_dong_danh_so",
    "_contains_cjk",
    "_dem_khoi_tra_loi",
    "_dump_debug",
    "_entities",
    "_gemini_call",
    "_gemini_model_candidates",
    "_http_error_summary",
    "_is_generating",
    "_json_con_do_dang",
    "_json_ngoac_chua_khop",
    "_json_phan_tich_du",
    "_keeps_core_meaning",
    "_keeps_entities",
    "_launch",
    "_mo_chat_moi",
    "_message_content_to_text",
    "_norm_vi",
    "_nvidia_chat_extras",
    "_openai_compatible_call",
    "_openai_compatible_chat_url",
    "_openai_message_text",
    "_parse_json_lines",
    "_put_text",
    "_reply_text",
    "_resp_locator",
    "_strip_think",
    "_submit",
    "_thu_thap_tra_loi",
    "_tokenharbor_call",
    "_tokenrouter_gemini_call",
    "_tokenrouter_gemini_url",
    "_too_long_for_tts",
    "_visible_locator",
    "_wait_reply",
]
