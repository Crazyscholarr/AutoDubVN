"""Pipeline biến kho truyện thô thành kế hoạch sản xuất YouTube.

Đầu vào tương thích trực tiếp JSON do ``pixel-acre-brook-comet`` xuất ra và
nhận cả SQLite/JSON có tên cột phổ biến.  Phân tích luôn có lớp heuristic chạy
offline; khi provider API trong ``config.yaml`` sẵn sàng, một lượt AI sẽ bổ
sung tiêu đề, mô tả, thumbnail, outline và chấm điểm sâu hơn.
"""
from __future__ import annotations

from .analyze import (
    ANALYSIS_PROMPT,
    DESCRIPTION_PROMPT,
    TITLE_PROMPT,
    _BROWSER_ANALYSIS_PROVIDERS,
    _analysis_error_message,
    _browser_analysis_session,
    _browser_json_correction_prompt,
    _browser_profile_path,
    _normalize_ai_result,
    _perplexity_tool_settings,
    _provider_params,
    _save_invalid_ai_response,
    _score,
    analyze_many,
    analyze_record,
)
from .calendar import (
    CONTENT_CALENDAR_HEADERS,
    _calendar_status,
    _calendar_title,
    _calendar_topic,
    _style_content_calendar,
    _thumb_text,
    append_content_calendar,
    export_excel,
    export_plan,
    sample_records,
)
from .heuristic import (
    ARCHETYPE_TERMS,
    EMOTION_TERMS,
    HOOK_TERMS,
    THEME_TERMS,
    TWIST_TERMS,
    _fallback_rewrite_material,
    _headline_situation,
    _make_descriptions,
    _make_titles,
    _rank_labels,
    _term_score,
    heuristic_analysis,
)
from .json_ai import (
    _extract_json,
    _extract_named_json_array,
    _json_object_candidates,
    _remove_json_trailing_commas,
    _repair_one_object,
    _repair_truncated_json,
    _slice_balanced,
)
from .records import (
    DEFAULT_DB,
    DEFAULT_OUTPUT_DIR,
    FIELD_ALIASES,
    ROOT,
    _json_rows,
    _list_value,
    _lookup,
    _sqlite_table,
    _stable_id,
    canonical_source_url,
    clean_text,
    count_words,
    detect_language,
    load_json,
    load_source,
    load_sqlite,
    normalize_record,
)
from .store import ContentStore, public_record, record_was_used

__all__ = [
    "ANALYSIS_PROMPT",
    "CONTENT_CALENDAR_HEADERS",
    "ContentStore",
    "DEFAULT_DB",
    "DEFAULT_OUTPUT_DIR",
    "DESCRIPTION_PROMPT",
    "FIELD_ALIASES",
    "ROOT",
    "TITLE_PROMPT",
    "analyze_many",
    "analyze_record",
    "append_content_calendar",
    "canonical_source_url",
    "clean_text",
    "count_words",
    "detect_language",
    "export_excel",
    "export_plan",
    "heuristic_analysis",
    "load_json",
    "load_source",
    "load_sqlite",
    "normalize_record",
    "public_record",
    "record_was_used",
    "sample_records",
]
