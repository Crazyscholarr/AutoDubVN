"""Các endpoint của luồng "kể chuyện" (tab Tạo audio trên giao diện).

Mỗi hàm nhận body JSON đã parse và trả về (obj, http_code) để Handler gọi
`self._json(*api_xxx(b))`. Việc nặng đều chạy trong thread nền, kết quả cập
nhật vào STATE["manual"] cho giao diện poll.

Triển khai nằm ở ``autodub.server.story``; module này chỉ re-export tên cũ.
"""
from __future__ import annotations

from .state import HERE, submit_job
from .config_api import _load_cfg
from .story.common import (
    JsonResult,
    DangBan,
    _raise_if_cancelled,
    _mark_manual_cancelled,
    _story_output_dir,
    _story_title_key,
    _lay_van_ban_tu_body,
    _cta_tts_options,
    _ensure_story_ctas,
    _story_design_text,
    _story_tts_workdir,
)
from .story.youtube import (
    _save_youtube_metadata,
    _save_story_deliverables,
    _attach_youtube_pack,
    api_login_chatgpt,
)
from .story.images import (
    _story_image_inputs,
    _generated_story_image_inputs,
    _story_slideshow_images,
    _story_image_generation_config,
    _prepare_generated_story_images,
    api_story_image_pack,
    api_story_image_pack_latest,
    api_story_resume_images,
)
from .story.audio import (
    CAU_NGHE_THU,
    NGHE_THU_MAX_CHARS,
    _NGHE_THU_LOCK,
    _cat_cau_nghe_thu,
    tao_ban_nghe_thu,
    _story_voice_plan,
    _save_voice_cast,
    api_manual_use_audio,
    api_manual_tts,
    api_nhac_nen_tai,
    api_manual_nhac_nen,
    api_story_voice_recommendations,
)
from .story.sources import (
    _story_video_inputs,
    api_story_search_sources,
    api_story_reference_catalog,
    api_story_search_references,
    api_story_cut_sources,
    api_story_download_sources,
)
from .story.render import (
    _segments_tu_timeline,
    _ass_tu_srt,
    api_manual_slideshow,
    api_story_generated_script,
    api_story_generate_and_run,
    api_manual_run_all,
    api_story_video_info,
    api_manual_mux,
)

__all__ = [
    "HERE",
    "submit_job",
    "_load_cfg",
    "JsonResult",
    "DangBan",
    "_raise_if_cancelled",
    "_mark_manual_cancelled",
    "_story_output_dir",
    "_story_title_key",
    "_lay_van_ban_tu_body",
    "_cta_tts_options",
    "_ensure_story_ctas",
    "_story_design_text",
    "_story_tts_workdir",
    "_save_youtube_metadata",
    "_save_story_deliverables",
    "_attach_youtube_pack",
    "api_login_chatgpt",
    "_story_image_inputs",
    "_generated_story_image_inputs",
    "_story_slideshow_images",
    "_story_image_generation_config",
    "_prepare_generated_story_images",
    "api_story_image_pack",
    "api_story_image_pack_latest",
    "api_story_resume_images",
    "CAU_NGHE_THU",
    "NGHE_THU_MAX_CHARS",
    "_NGHE_THU_LOCK",
    "_cat_cau_nghe_thu",
    "tao_ban_nghe_thu",
    "_story_voice_plan",
    "_save_voice_cast",
    "api_manual_use_audio",
    "api_manual_tts",
    "api_nhac_nen_tai",
    "api_manual_nhac_nen",
    "api_story_voice_recommendations",
    "_story_video_inputs",
    "api_story_search_sources",
    "api_story_reference_catalog",
    "api_story_search_references",
    "api_story_cut_sources",
    "api_story_download_sources",
    "_segments_tu_timeline",
    "_ass_tu_srt",
    "api_manual_slideshow",
    "api_story_generated_script",
    "api_story_generate_and_run",
    "api_manual_run_all",
    "api_story_video_info",
    "api_manual_mux",
]
