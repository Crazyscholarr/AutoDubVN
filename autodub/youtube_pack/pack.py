"""Gọi ChatGPT rồi lưu thumbnail + mô tả cạnh file video."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .. import chatgpt_web
from .description import (
    build_description_prompt, build_thumbnail_visual_prompt,
    ensure_description_finish, format_duration_vi, make_story_summary,
)
from .overlay import draw_thumbnail_overlay, overlay_lines_from_idea

def youtube_flags(payload: Optional[Dict], cfg: Optional[Dict]) -> Tuple[bool, bool]:
    yt = (cfg or {}).get("dang_youtube") if isinstance((cfg or {}).get("dang_youtube"), dict) else {}
    payload = payload if isinstance(payload, dict) else {}

    def _flag(key_payload: str, key_cfg: str) -> bool:
        if key_payload in payload and payload.get(key_payload) is not None:
            return bool(payload.get(key_payload))
        return bool(yt.get(key_cfg, True))

    return _flag("auto_youtube_thumbnail", "auto_thumbnail"), \
        _flag("auto_youtube_description", "auto_description")


def read_script_excerpt(payload: Optional[Dict], limit: int = 4000) -> str:
    payload = payload if isinstance(payload, dict) else {}
    text = str(payload.get("text") or "").strip()
    if text:
        return text[:limit]
    for key in ("txt_path", "script_path"):
        path = str(payload.get(key) or "").strip().strip('"')
        if path and os.path.isfile(path):
            try:
                return Path(path).read_text(encoding="utf-8")[:limit]
            except OSError:
                continue
    return ""


def idea_from_sources(payload: Optional[Dict], record: Optional[Dict] = None) -> Dict[str, Any]:
    idea = dict(record or {})
    payload = payload if isinstance(payload, dict) else {}
    if payload.get("rewrite_brief") and not idea.get("rewrite_brief"):
        idea["rewrite_brief"] = payload.get("rewrite_brief")
    if payload.get("content_outline") and not idea.get("outline"):
        idea["outline"] = payload.get("content_outline")
    if payload.get("thumbnails") and not idea.get("thumbnails"):
        idea["thumbnails"] = payload.get("thumbnails")
    return idea


def make_youtube_pack(
        video_path: str,
        payload: Optional[Dict] = None,
        record: Optional[Dict] = None,
        cfg: Optional[Dict] = None,
        duration_seconds: float = 0.0,
        logger: Optional[Callable] = None,
        cancel_event=None) -> Dict[str, str]:
    """Tạo thumbnail/mô tả; lỗi ChatGPT chỉ cảnh báo, không làm hỏng video."""
    from ..utils import ffprobe_duration

    log = logger or (lambda _msg, _kind="info": None)
    payload = payload if isinstance(payload, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else {}
    want_thumb, want_desc = youtube_flags(payload, cfg)
    out: Dict[str, str] = {}
    if not want_thumb and not want_desc:
        return out

    video = Path(video_path)
    out_dir = video.parent if video.parent.is_dir() else Path(video_path).parent
    title = str(payload.get("name") or video.stem or "").strip()
    idea = idea_from_sources(payload, record)
    seconds = float(duration_seconds or 0)
    if seconds <= 1 and video.is_file():
        try:
            seconds = float(ffprobe_duration(str(video)) or 0)
        except Exception:
            seconds = 0.0
    duration = format_duration_vi(seconds or 3600)
    summary = make_story_summary(title, idea, read_script_excerpt(payload))
    settings = chatgpt_web.chatgpt_settings(cfg)
    session = None
    try:
        session = chatgpt_web.ChatGPTWebSession(
            settings["profile_dir"], channel=settings["channel"],
            url=settings["url"], wait_image=settings["wait_image"],
            wait_reply=settings["wait_reply"], logger=log,
            cancel_event=cancel_event)
        session.start()
        if want_thumb:
            log("[YouTube] Đang nhờ ChatGPT tạo ảnh thumbnail (không chữ)…", "step")
            raw_path = out_dir / "thumbnail_raw.png"
            session.generate_image(
                build_thumbnail_visual_prompt(title, idea), raw_path)
            line1, line2 = overlay_lines_from_idea(title, idea)
            final_path = draw_thumbnail_overlay(
                raw_path, out_dir / "thumbnail.jpg", line1, line2)
            out["thumbnail_raw_path"] = str(raw_path)
            out["thumbnail_path"] = str(final_path)
            log("[YouTube] Đã lưu thumbnail: %s" % final_path, "ok")
        if want_desc:
            if want_thumb:
                session.new_chat()
            log("[YouTube] Đang nhờ ChatGPT viết mô tả 6 khối…", "step")
            raw = session.ask_text(build_description_prompt(title, summary, duration))
            description = ensure_description_finish(raw, title, idea)
            desc_path = out_dir / "mo_ta_youtube.txt"
            desc_path.write_text(description, encoding="utf-8")
            out["description"] = description
            out["description_path"] = str(desc_path)
            log("[YouTube] Đã lưu mô tả: %s" % desc_path, "ok")
    except InterruptedError:
        raise
    except Exception as exc:
        log("[YouTube] Chưa tạo xong gói YouTube: %s" % str(exc)[:240], "warn")
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
    return out
