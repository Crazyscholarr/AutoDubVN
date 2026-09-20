"""Gói prompt/ảnh bền vững cho luồng video kể chuyện.

Danh sách ảnh trước đây chỉ sống trong JavaScript nên mất khi đóng giao diện.
Module này lưu toàn bộ thứ tự cảnh, prompt và file ảnh vào một manifest JSON.
Người dùng có thể mang ``PROMPTS_AI_STUDIO.txt`` sang Gemini/AI Studio, tải
ảnh về rồi gắn lại; bước dựng video luôn đọc ảnh theo đúng thứ tự trong manifest.
"""
from __future__ import annotations

import time
import urllib
import urllib.error
import urllib.request

from .api import (
    DEFAULT_IMAGE_MODEL,
    GEMINI_INTERACTIONS_URL,
    GeminiImageError,
    _gemini_image_request,
    generate_images_gemini,
)
from .browser import (
    GeminiBrowserError,
    GeminiSceneSaveError,
    _GeminiWebImageSession,
    _register_generated_scene_file,
    _visible_role,
    gemini_browser_settings,
    generate_images_gemini_browser,
    generate_scene_prompts_gemini_browser,
)
from .media import (
    _MIN_SCENE_BYTES,
    _MIN_SCENE_EDGE,
    _SKIP_IMAGE_URL,
    _looks_like_image_bytes,
    _maybe_image_response,
    _payload_usable,
    _save_generated_scene,
)
from .pack import (
    DEFAULT_BROWSER_IMAGE_MODEL,
    DEFAULT_PACK_ROOT,
    GEMINI_WEB_URL,
    IMAGE_EXTENSIONS,
    PROJECT_ROOT,
    _expand_images,
    _manifest_path,
    _prompt_document,
    _slug,
    _write_json,
    attach_images,
    chapter_weights_from_script,
    create_pack,
    expand_for_chapters,
    generate_scene_prompts,
    latest_pack,
    load_pack,
    parse_scene_prompts,
    public_summary,
    resolve_images,
)

from . import api as _api
from . import browser as _browser


def _lookup_package(name):
    """unittest.patch.object(story_images, name) vẫn tới chỗ gọi trong submodule."""
    def _call(*args, **kwargs):
        return globals()[name](*args, **kwargs)
    _call.__name__ = name
    return _call


_browser._visible_role = _lookup_package("_visible_role")
_browser._GeminiWebImageSession = _lookup_package("_GeminiWebImageSession")
_api._gemini_image_request = _lookup_package("_gemini_image_request")
