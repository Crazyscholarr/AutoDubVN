"""Tiện ích nhỏ dùng chung: tên file an toàn, đọc file văn bản, dọn file tạm.

Các hàm ở đây thuần tuý (không giữ trạng thái), chỉ phụ thuộc `state` để biết
thư mục gốc dự án.
"""
from __future__ import annotations

import os
import re
import shutil
from typing import Dict, List, Optional

from .state import HERE


def _safe_path_stem(name: str, fallback: str = "video", limit: int = 120) -> str:
    """Return a Windows-safe folder/file stem while keeping readable Unicode text."""
    text = str(name or "").strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = fallback

    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if text.upper() in reserved:
        text = f"{text}_video"

    if len(text) > limit:
        text = text[:limit].rstrip(" .")
    return text or fallback


def _output_stem_for_video(path: str) -> str:
    raw = os.path.splitext(os.path.basename(path or ""))[0]
    return _safe_path_stem(raw)


def _output_dir_for_video(path: str) -> str:
    return os.path.join(HERE, "output", _output_stem_for_video(path))


def _doc_file_van_ban(path: str) -> str:
    """Đọc file truyện. Thử vài bảng mã vì file .txt tiếng Việt hay là UTF-16
    (Notepad lưu "Unicode") hoặc CP1258 chứ không phải lúc nào cũng UTF-8."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", "replace")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    for enc in ("utf-8", "cp1258", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _call_filtered(fn, *args, **_ignored):
    """Gọi fn với CHỈ những tham số nó thật sự nhận.

    Các module (nhất là asr.py) có thể được nâng cấp và đổi chữ ký. Gọi cứng
    một tham số không còn tồn tại sẽ ném TypeError và làm hỏng cả pipeline,
    nên ở đây lọc theo chữ ký thật của hàm.
    """
    import inspect
    kwargs = _ignored.get("_kw") or {}
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args)
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        clean = {k: v for k, v in kwargs.items() if v is not None}
    else:
        clean = {k: v for k, v in kwargs.items()
                 if k in params and v is not None}
    return fn(*args, **clean)


def _float_or_none(value) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_span_time(seconds: float) -> str:
    seconds = max(0, int(round(float(seconds or 0.0))))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}h{m:02d}m{s:02d}s"


def _find_existing_dub_audio(tmp_dir: str) -> Optional[str]:
    names = ["dub.wav", "dub.flac", "dub.mka", "dub.m4a", "dub.aac"]
    candidates = []
    for name in names:
        path = os.path.join(tmp_dir, name)
        if os.path.exists(path) and os.path.getsize(path) > 512:
            candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda p: os.path.getmtime(p))


def _path_under(parent: str, path: str) -> bool:
    parent = os.path.abspath(parent)
    path = os.path.abspath(path)
    try:
        return os.path.commonpath([parent, path]) == parent
    except ValueError:
        return False


def _cleanup_temp_files() -> Dict:
    """Delete regeneratable files in output/**/_tmp without touching final outputs."""
    root = os.path.abspath(os.path.join(HERE, "output"))
    removed = 0
    removed_bytes = 0
    failed: List[str] = []
    if not os.path.isdir(root):
        return {"files": 0, "bytes": 0, "failed": [], "free": 0}

    targets: List[str] = []
    audio_names = {"audio16k.wav", "audio16k.flac", "dub.wav", "dub.flac"}
    render_exts = {".mp4", ".m4v", ".ts"}
    for dirpath, _dirnames, filenames in os.walk(root):
        if not _path_under(root, dirpath):
            continue
        rel_dir = os.path.relpath(dirpath, root)
        parts = [] if rel_dir == "." else rel_dir.split(os.sep)
        if "_tmp" not in parts:
            continue
        in_render_parts = "render_parts" in parts
        for name in filenames:
            full = os.path.abspath(os.path.join(dirpath, name))
            if not _path_under(root, full):
                continue
            ext = os.path.splitext(name)[1].lower()
            if name in audio_names or (in_render_parts and ext in render_exts):
                targets.append(full)

    for full in targets:
        try:
            size = os.path.getsize(full)
            os.remove(full)
            removed += 1
            removed_bytes += size
        except Exception:
            failed.append(full)

    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        try:
            rel_dir = os.path.relpath(dirpath, root)
            parts = [] if rel_dir == "." else rel_dir.split(os.sep)
            if "_tmp" in parts and os.path.basename(dirpath) in {"render_parts"}:
                os.rmdir(dirpath)
        except Exception:
            pass

    try:
        free = shutil.disk_usage(root).free
    except Exception:
        free = 0
    return {"files": removed, "bytes": removed_bytes, "failed": failed[:20], "free": free}
