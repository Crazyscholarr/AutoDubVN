"""Gọi Gemini Image API."""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional

from .media import _save_generated_scene
from .pack import load_pack


GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-lite-image"


class GeminiImageError(RuntimeError):
    def __init__(self, message: str, status: int = 0, retry_after: float = 0.0):
        super().__init__(message)
        self.status = int(status or 0)
        self.retry_after = max(0.0, float(retry_after or 0.0))


def _gemini_image_request(prompt: str, api_key: str, model: str,
                          aspect: str, timeout: float = 180.0) -> tuple[bytes, str]:
    """Gọi Interactions API chính thức và lấy ảnh inline đầu tiên."""
    payload = {
        "model": str(model or DEFAULT_IMAGE_MODEL),
        "input": str(prompt or "").strip(),
        "response_format": {
            "type": "image", "mime_type": "image/jpeg",
            "aspect_ratio": "9:16" if str(aspect) == "9:16" else "16:9",
            "image_size": "1K",
        },
    }
    req = urllib.request.Request(
        GEMINI_INTERACTIONS_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": str(api_key or "").strip()},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=max(30.0, float(timeout))) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            detail = str(exc)
        try:
            retry_after = float(exc.headers.get("Retry-After") or 0)
        except (TypeError, ValueError, AttributeError):
            retry_after = 0.0
        raise GeminiImageError(
            "Gemini Image HTTP %d: %s" % (exc.code, detail),
            status=exc.code, retry_after=retry_after) from exc
    except Exception as exc:
        raise GeminiImageError("Không gọi được Gemini Image: %s" % exc) from exc

    for step in data.get("steps") or []:
        if str(step.get("type") or "") != "model_output":
            continue
        for block in step.get("content") or []:
            if str(block.get("type") or "") != "image" or not block.get("data"):
                continue
            try:
                raw = base64.b64decode(str(block["data"]), validate=True)
            except Exception as exc:
                raise GeminiImageError("Gemini trả dữ liệu ảnh base64 bị hỏng.") from exc
            if len(raw) < 1024:
                raise GeminiImageError("Gemini trả ảnh rỗng hoặc quá nhỏ.")
            return raw, str(block.get("mime_type") or "image/jpeg")
    raise GeminiImageError("Gemini hoàn tất nhưng không trả ảnh.")


def generate_images_gemini(path: str | os.PathLike, api_key: str,
                           model: str = DEFAULT_IMAGE_MODEL,
                           timeout: float = 180.0, max_retries: int = 3,
                           request_gap: float = 1.5,
                           logger: Optional[Callable] = None,
                           progress: Optional[Callable] = None,
                           cancel_event=None) -> Dict:
    """Sinh các cảnh còn thiếu bằng Gemini, tuần tự để tránh rate-limit."""
    if not str(api_key or "").strip():
        raise GeminiImageError("Chưa cấu hình Gemini API key để tự tạo ảnh.")
    logger = logger or (lambda _msg, _kind="info": None)
    progress = progress or (lambda _done, _total, _message="": None)
    manifest = load_pack(path)
    scenes = list(manifest.get("scenes") or [])
    missing = [scene for scene in scenes
               if scene.get("status") != "ready" and str(scene.get("prompt") or "").strip()]
    if not missing:
        if int(manifest.get("ready_count", 0) or 0) == len(scenes) and scenes:
            return manifest
        raise GeminiImageError("Gói ảnh chưa có prompt từng cảnh để tự sinh.")
    retries = max(1, int(max_retries or 1))
    for done, scene in enumerate(missing, 1):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Đã huỷ tạo ảnh.")
        index = int(scene.get("index") or done)
        last_error: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                raw, mime = _gemini_image_request(
                    str(scene.get("prompt") or ""), api_key, model,
                    str(manifest.get("aspect") or "16:9"), timeout=timeout)
                manifest = _save_generated_scene(path, index, raw, mime)
                progress(done, len(missing), "Đã tạo cảnh %03d/%03d" % (done, len(missing)))
                logger("Đã tạo ảnh cảnh %03d bằng Gemini." % index, "ok")
                last_error = None
                break
            except GeminiImageError as exc:
                last_error = exc
                if attempt >= retries or exc.status not in {0, 429, 500, 502, 503, 504}:
                    break
                wait = exc.retry_after or min(30.0, 2.0 ** attempt)
                logger("Cảnh %03d bị giới hạn; thử lại sau %.0f giây."
                       % (index, wait), "warn")
                time.sleep(wait)
        if last_error is not None:
            raise last_error
        if done < len(missing) and request_gap > 0:
            time.sleep(float(request_gap))
    return load_pack(path)
