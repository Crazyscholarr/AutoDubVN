"""Tiện ích dùng chung cho kho ý tưởng YouTube."""
from __future__ import annotations

import os
import time
from typing import Dict, Iterable, Tuple

from ...content_pipeline import (
    ContentStore, append_content_calendar, heuristic_analysis,
)
from ..config_api import _load_cfg
from ..state import HERE, STATE, _LOCK, _log


JsonResult = Tuple[Dict, int]


def _api():
    """Module facade mà unittest patch (submit_job, _settings, ...)."""
    from autodub.server import content_api
    return content_api


def _resolve_path(value: str, default: str) -> str:
    raw = os.path.expandvars(str(value or default).strip().strip('"'))
    return os.path.abspath(raw if os.path.isabs(raw) else os.path.join(HERE, raw))


def _settings() -> Dict:
    cfg = _load_cfg()
    cp = cfg.get("content_pipeline") if isinstance(cfg.get("content_pipeline"), dict) else {}
    return {
        "config": cfg,
        "database": _resolve_path(cp.get("database", ""), "data/content_ideas.sqlite"),
        "output_dir": _resolve_path(cp.get("output_dir", ""), "output/content_plans"),
        "provider": str(cp.get("provider") or "auto").lower(),
        "concurrency": max(1, min(8, int(cp.get("max_concurrency") or 3))),
    }


def _store() -> ContentStore:
    return ContentStore(_api()._settings()["database"])


def _sync_content_calendar(records: Iterable[Dict], settings: Dict = None,
                           final_titles: Dict[str, str] = None,
                           status: str = "") -> str:
    """Ghi lịch một lần cho mỗi ý tưởng; file khóa sẽ chuyển sang bản dự phòng."""
    rows = [dict(item) for item in records if item]
    if not rows:
        return ""
    current = settings or _api()._settings()
    cfg = current.get("config") if isinstance(current.get("config"), dict) else {}
    cp = cfg.get("content_pipeline") if isinstance(
        cfg.get("content_pipeline"), dict) else {}
    target = _resolve_path(
        cp.get("calendar_file", ""),
        os.path.join(str(current.get("output_dir") or "output/content_plans"),
                     "lich_noi_dung_goc_mit.xlsx"))
    duration = str(cp.get("target_duration") or "1h00 - 1h30")
    chosen = final_titles or {}
    for item in rows:
        target = append_content_calendar(
            item, target,
            final_title=str(chosen.get(str(item.get("id") or "")) or ""),
            target_duration=duration,
            status=str(status or item.get("status") or "Chưa làm"))
    return target


def _state(**values) -> None:
    with _LOCK:
        state = STATE["content_pipeline"]
        state.update(values)
        state["rev"] = int(state.get("rev", 0)) + 1


def _activity(message: str, kind: str = "info", stage: str = "",
              item: str = "") -> None:
    """Ghi nhật ký ngắn cho riêng Kho ý tưởng để giao diện đọc realtime."""
    with _LOCK:
        state = STATE["content_pipeline"]
        rows = list(state.get("activity") or [])
        rows.append({"t": time.time(), "kind": str(kind or "info"),
                     "stage": str(stage or ""), "item": str(item or "")[:90],
                     "message": str(message or "")[:700]})
        state["activity"] = rows[-80:]
        state["rev"] = int(state.get("rev", 0)) + 1
    # Cùng dòng này xuất hiện ở cửa sổ CMD để người dùng vẫn theo dõi được khi
    # thu nhỏ giao diện hoặc khi WebView chưa kịp vẽ trạng thái mới.
    console_kind = "warn" if kind == "warning" else kind
    _log(f"Kho ý tưởng [{stage or 'tiến trình'}]: {message}", console_kind)


def _begin(active: str, status: str) -> bool:
    with _LOCK:
        state = STATE["content_pipeline"]
        if state.get("working"):
            return False
        state.update({
            "working": True, "active": active, "status": status,
            "progress": 0.0, "done": 0, "total": 0, "error": "",
            "warning": "", "activity": [], "current_stage": "prepare",
            "current_item": "", "started_at": time.time(),
            "ai_success": 0, "ai_failed": 0, "provider": "",
            "provider_model": "", "provider_configured": False,
            "provider_offline": False,
            "rev": int(state.get("rev", 0)) + 1,
        })
    return True


def _finish(status: str, error: str = "", **values) -> None:
    values.update({"working": False, "active": "", "status": status,
                   "error": str(error or "")[:500]})
    _state(**values)


def _configured_provider(cfg: Dict, provider: str) -> bool:
    try:
        from ...content_pipeline import _provider_params
        name, key, _model, _base, _timeout = _provider_params(cfg, provider)
        return name in {"browser", "perplexity_browser"} or bool(key)
    except Exception:
        return False


def _provider_status(cfg: Dict, provider: str) -> Dict:
    """Thông tin provider an toàn để hiện UI; tuyệt đối không trả API key."""
    try:
        from ...content_pipeline import _provider_params
        name, key, model, _base, _timeout = _provider_params(cfg, provider)
        browser = name in {"browser", "perplexity_browser"}
        offline = name in {"", "none", "heuristic", "offline"}
        return {"provider": name or "heuristic", "provider_model": model or "",
                "provider_configured": browser or (bool(key) and not offline),
                "provider_offline": offline}
    except Exception:
        return {"provider": str(provider or "auto"), "provider_model": "",
                "provider_configured": False, "provider_offline": False}


def _selected_records(store: ContentStore, ids: Iterable[str], all_items: bool) -> list:
    clean = [str(x) for x in (ids or []) if str(x).strip()]
    if clean:
        return [item for item in (store.get(x) for x in clean) if item]
    return store.list(selected_only=not all_items, limit=10000)


def _body_ids(body: Dict) -> list:
    raw = body.get("ids") or []
    if isinstance(raw, str):
        raw = [raw]
    return list(dict.fromkeys(
        str(value).strip() for value in raw if str(value).strip()))
