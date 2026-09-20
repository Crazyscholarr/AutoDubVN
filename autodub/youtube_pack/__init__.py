"""Thumbnail + mô tả YouTube cho video kể chuyện Gốc Mít.

Ảnh nền do ChatGPT sinh (không chữ). Chữ lớn được vẽ sau bằng PIL.
Mô tả theo đúng 6 khối, chỉ trả phần mô tả.
"""
from __future__ import annotations

from .const import (
    DESCRIPTION_PROMPT, FIXED_FOOTER, HASHTAG_RE, LINE1_COLOR,
    LINE2_COLOR, SENTENCE_RE, STROKE_COLOR, THUMBNAIL_STYLE_LOCK,
    THUMB_H, THUMB_W, TWIST_CUT_RE, YEAR_RE,
)
from .description import (
    build_description_prompt, build_thumbnail_visual_prompt,
    ensure_description_finish, extract_description_only,
    format_duration_vi, make_story_summary, suggest_extra_hashtags,
)
from .overlay import (
    draw_thumbnail_overlay, overlay_lines_from_idea, split_thumbnail_lines,
)
from .pack import (
    idea_from_sources, make_youtube_pack, read_script_excerpt, youtube_flags,
)
from .dub_scenes import (
    attach_dub_thumbnails, build_dub_thumbnail_visual_prompt, compact_hook,
    chatgpt_thumbnail_from_scenes, make_dub_thumbnail_pack,
    pick_scene_moments, score_scene, want_dub_thumbnail,
)

__all__ = [
    "DESCRIPTION_PROMPT", "FIXED_FOOTER", "HASHTAG_RE", "LINE1_COLOR",
    "LINE2_COLOR", "SENTENCE_RE", "STROKE_COLOR", "THUMBNAIL_STYLE_LOCK",
    "THUMB_H", "THUMB_W", "TWIST_CUT_RE", "YEAR_RE",
    "build_description_prompt", "build_thumbnail_visual_prompt",
    "draw_thumbnail_overlay", "ensure_description_finish",
    "extract_description_only", "format_duration_vi", "idea_from_sources",
    "make_story_summary", "make_youtube_pack", "overlay_lines_from_idea",
    "read_script_excerpt", "split_thumbnail_lines", "suggest_extra_hashtags",
    "youtube_flags",
    "attach_dub_thumbnails", "build_dub_thumbnail_visual_prompt",
    "chatgpt_thumbnail_from_scenes", "compact_hook", "make_dub_thumbnail_pack",
    "pick_scene_moments", "score_scene", "want_dub_thumbnail",
]
