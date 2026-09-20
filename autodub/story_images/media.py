"""Kiểm tra byte ảnh và ghi cảnh vào gói."""
from __future__ import annotations

import io
import os
import re
import time
from pathlib import Path
from typing import Dict, Optional

from .pack import IMAGE_EXTENSIONS, _manifest_path, _write_json, load_pack


_MIN_SCENE_EDGE = 256
_MIN_SCENE_BYTES = 400
_SKIP_IMAGE_URL = (
    "favicon", "gstatic.com/s/", "/static/", "sprite", "logo.",
    "encrypted-tbn", "/a/default",
)


def _looks_like_image_bytes(payload: bytes) -> bool:
    raw = payload or b""
    if len(raw) < _MIN_SCENE_BYTES:
        return False
    if raw.startswith(b"\x89PNG") or raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"GIF8"):
        return True
    return raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"


def _payload_usable(payload: Optional[bytes]) -> bool:
    """Ảnh đủ lớn và PIL đọc được — loại icon/placeholder."""
    if not payload or not _looks_like_image_bytes(payload):
        return False
    try:
        from PIL import Image
        with Image.open(io.BytesIO(payload)) as img:
            if min(img.size) < _MIN_SCENE_EDGE:
                return False
            img.verify()
        return True
    except Exception:
        return False


def _maybe_image_response(response) -> bool:
    """Lọc phản hồi mạng có khả năng là ảnh Gemini vừa sinh."""
    try:
        url = str(getattr(response, "url", "") or "")
        headers = getattr(response, "headers", None) or {}
        ct = str(headers.get("content-type") or headers.get("Content-Type") or "").lower()
    except Exception:
        return False
    lowered = url.lower()
    if any(token in lowered for token in _SKIP_IMAGE_URL):
        return False
    if "image/" in ct:
        return True
    if re.search(r"\.(?:png|jpe?g|webp|gif)(?:\?|$)", url, re.I):
        return True
    return "googleusercontent.com" in lowered and len(url) > 60


def _save_generated_scene(path: str | os.PathLike, scene_index: int,
                          image_bytes: bytes, mime_type: str) -> Dict:
    """Lưu ngay từng ảnh và cập nhật manifest để lỗi giữa chừng không mất công."""
    manifest_path = _manifest_path(path)
    manifest = load_pack(manifest_path)
    scenes = list(manifest.get("scenes") or [])
    if scene_index < 1 or scene_index > len(scenes):
        raise IndexError("Chỉ số cảnh nằm ngoài manifest.")
    ext = ".png" if "png" in str(mime_type).lower() else ".jpg"
    image_dir = manifest_path.parent / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    dest = image_dir / f"scene_{scene_index:03d}{ext}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(image_bytes)
    os.replace(tmp, dest)
    scenes[scene_index - 1].update({
        "expected_file": str(dest.relative_to(manifest_path.parent)).replace("\\", "/"),
        "image_path": str(dest.resolve()), "status": "ready",
    })
    manifest["scenes"] = scenes
    manifest["ready_count"] = sum(1 for scene in scenes if scene.get("status") == "ready")
    manifest["status"] = ("ready" if manifest["ready_count"] == len(scenes)
                          else "generating_images")
    manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest.pop("manifest_path", None)
    manifest.pop("pack_dir", None)
    _write_json(manifest_path, manifest)
    return load_pack(manifest_path)
