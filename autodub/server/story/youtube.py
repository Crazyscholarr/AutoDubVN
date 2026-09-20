"""Sidecar YouTube, lịch nội dung và đăng nhập ChatGPT."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, Optional, Tuple

from ..state import HERE, STATE, _LOCK, current_cancel_event, _log, _progress
from .common import JsonResult, _api


def _load_cfg():
    return _api()._load_cfg()


def _save_youtube_metadata(video_path: str, payload: Dict) -> str:
    """Ghi sidecar để tiêu đề/mô tả/tag đi cùng file render cuối."""
    description = str(payload.get("youtube_description") or "").strip()
    outline = str(payload.get("content_outline") or "").strip()
    idea_id = str(payload.get("content_idea_id") or "").strip()
    thumbnail_path = str(payload.get("thumbnail_path") or "").strip()
    description_path = str(payload.get("description_path") or "").strip()
    raw_tags = payload.get("youtube_tags") or []
    if isinstance(raw_tags, str):
        tags = [x.strip() for x in re.split(r"[,;\n]+", raw_tags) if x.strip()]
    elif isinstance(raw_tags, (list, tuple)):
        tags = [str(x).strip() for x in raw_tags if str(x).strip()]
    else:
        tags = []
    if not any((description, outline, idea_id, tags, thumbnail_path)):
        return ""
    metadata = {
        "video_title": str(payload.get("name") or "").strip(),
        "youtube_description": description,
        "youtube_tags": tags,
        "content_outline": outline,
        "content_idea_id": idea_id,
        "content_calendar_path": str(payload.get("content_calendar_path") or ""),
        "thumbnail_path": thumbnail_path,
        "description_path": description_path,
        "video_path": os.path.abspath(video_path),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    target = os.path.splitext(os.path.abspath(video_path))[0] + ".youtube.json"
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)
    return target


def _save_story_deliverables(video_path: str, payload: Dict) -> Tuple[str, str]:
    """Lưu sidecar YouTube và cập nhật Excel lịch nội dung sau render."""
    calendar_path = ""
    record = None
    try:
        from ...content_pipeline import ContentStore, append_content_calendar

        if not os.path.isfile(video_path):
            raise FileNotFoundError("video kết quả chưa tồn tại để ghi lịch")

        cfg = _load_cfg()
        cp = cfg.get("content_pipeline") if isinstance(
            cfg.get("content_pipeline"), dict) else {}

        def resolve(value: str, default: str) -> str:
            raw = os.path.expandvars(str(value or default).strip().strip('"'))
            return os.path.abspath(raw if os.path.isabs(raw) else os.path.join(HERE, raw))

        database = resolve(cp.get("database", ""), "data/content_ideas.sqlite")
        output_dir = resolve(cp.get("output_dir", ""), "output/content_plans")
        calendar_target = resolve(
            cp.get("calendar_file", ""),
            os.path.join(output_dir, "lich_noi_dung_goc_mit.xlsx"))
        idea_id = str(payload.get("content_idea_id") or "").strip()
        store = ContentStore(database)
        record = store.get(idea_id) if idea_id else None
        record = dict(record or {
            "id": idea_id,
            "source": "Nhập trực tiếp",
            "primary_genre": "Chuyện gia đình và tuổi già",
        })
        calendar_path = append_content_calendar(
            record, calendar_target,
            final_title=str(payload.get("name") or ""), video_path=video_path,
            target_duration=str(cp.get("target_duration") or "1h00 - 1h30"),
            status="Đã xuất video")
        if idea_id and store.get(idea_id):
            store.patch(idea_id, {"status": "rendered"})
        _log(f"[Kể chuyện] Đã cập nhật Excel lịch nội dung: {calendar_path}", "ok")
    except Exception as exc:
        # Video đã render thành công không được báo lỗi chỉ vì Excel đang mở.
        _log(f"[Kể chuyện] Chưa cập nhật được Excel lịch nội dung: {exc}", "warn")
        record = None
    metadata_payload = dict(payload)
    metadata_payload["content_calendar_path"] = calendar_path
    pack = _attach_youtube_pack(video_path, payload, record)
    if pack.get("description"):
        metadata_payload["youtube_description"] = pack["description"]
    if pack.get("thumbnail_path"):
        metadata_payload["thumbnail_path"] = pack["thumbnail_path"]
    if pack.get("description_path"):
        metadata_payload["description_path"] = pack["description_path"]
    return _save_youtube_metadata(video_path, metadata_payload), calendar_path


def _attach_youtube_pack(video_path: str, payload: Dict,
                         record: Optional[Dict] = None) -> Dict:
    """Tạo thumbnail/mô tả sau render; lỗi ChatGPT không làm hỏng video."""
    import sys
    from ... import youtube_pack

    cfg = _load_cfg()
    want_thumb, want_desc = youtube_pack.youtube_flags(payload, cfg)
    if not want_thumb and not want_desc:
        return {}
    # Unittest không được mở Edge/ChatGPT; chỉ chạy khi test chủ động bật cờ.
    if "unittest" in sys.modules:
        explicit = bool(payload.get("auto_youtube_thumbnail") is True
                        or payload.get("auto_youtube_description") is True)
        if not explicit:
            return {}
    if not os.path.isfile(video_path):
        return {}
    _progress(pct=94, step="Thumbnail & mô tả YouTube",
              detail="Đang mở ChatGPT bằng hồ sơ đã đăng nhập…")
    try:
        pack = youtube_pack.make_youtube_pack(
            video_path, payload=payload, record=record, cfg=cfg,
            duration_seconds=float(payload.get("audio_duration") or 0),
            logger=_log, cancel_event=current_cancel_event())
    except InterruptedError:
        raise
    except Exception as exc:
        _log("[YouTube] Bỏ qua gói thumbnail/mô tả: %s" % str(exc)[:220], "warn")
        pack = {}
    if pack.get("description") or pack.get("thumbnail_path"):
        with _LOCK:
            manual = STATE["manual"]
            updates = {"rev": int(manual.get("rev", 0)) + 1}
            if pack.get("description"):
                updates["youtube_description"] = pack["description"]
            if pack.get("thumbnail_path"):
                updates["thumbnail_path"] = pack["thumbnail_path"]
            if pack.get("description_path"):
                updates["description_path"] = pack["description_path"]
            manual.update(updates)
    return pack


def api_login_chatgpt(_body: Optional[Dict] = None) -> JsonResult:
    """Mở Chrome/Edge với hồ sơ ChatGPT riêng để người dùng đăng nhập một lần."""
    from ...chatgpt_web import launch_chatgpt_login

    result = launch_chatgpt_login()
    if not result.get("ok"):
        return {"error": result.get("error") or "Không mở được trình duyệt ChatGPT."}, 500
    return {"ok": True, "browser": result.get("browser"),
            "profile": result.get("profile")}, 200
