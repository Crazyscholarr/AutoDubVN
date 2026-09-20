"""Đọc/ghi phần cấu hình mà giao diện được phép chỉnh (mục translation, tts).

Ghi config.yaml theo kiểu SỬA TỪNG DÒNG (giữ nguyên comment và thứ tự) chứ
không dump lại cả file - dump sẽ xoá sạch chú thích hướng dẫn của người dùng.
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Dict, Optional, Tuple

from .state import CONFIG_PATH
from ..providers import (
    XKIRO_DEFAULT_BASE_URL, XKIRO_DEFAULT_MODEL,
    INFERX_DEFAULT_BASE_URL, INFERX_DEFAULT_MODEL,
    NVIDIA_CATALOG, NVIDIA_DEFAULT_BASE_URL, NVIDIA_DEFAULT_MODEL,
    NVIDIA_FAST_MODEL, TOKENHARBOR_DEFAULT_BASE_URL, TOKENHARBOR_DEFAULT_MODEL,
    TOKENROUTER_DEFAULT_BASE_URL, TOKENROUTER_DEFAULT_MODEL,
    TOKENROUTER_GEMINI_DEFAULT_BASE_URL, TOKENROUTER_GEMINI_DEFAULT_MODEL,
    ZENMUX_DEFAULT_BASE_URL, ZENMUX_DEFAULT_MODEL,
    ZAI_DEFAULT_BASE_URL, ZAI_DEFAULT_MODEL,
    api_params_for_provider,
)


_CFG_CACHE = {"path": None, "mtime": None, "data": None}
_CFG_LOCK = threading.RLock()


def _invalidate_cfg_cache() -> None:
    with _CFG_LOCK:
        _CFG_CACHE["path"] = None
        _CFG_CACHE["mtime"] = None
        _CFG_CACHE["data"] = None


def _load_cfg() -> Dict:
    """Đọc config.yaml; cache theo (đường dẫn, mtime) để poll GUI không parse YAML mỗi nhịp."""
    import yaml
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        return {}
    with _CFG_LOCK:
        if (_CFG_CACHE["data"] is not None
                and _CFG_CACHE["mtime"] == mtime
                and _CFG_CACHE["path"] == CONFIG_PATH):
            return _CFG_CACHE["data"]
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError("config.yaml phải chứa các mục cấu hình dạng key: value.")
        _CFG_CACHE["path"] = CONFIG_PATH
        _CFG_CACHE["mtime"] = mtime
        _CFG_CACHE["data"] = data
        return data


_TRANSLATION_GUI_KEYS = (
    "provider",
    "gemini_api_key",
    "gemini_model",
    "tokenrouter_api_key",
    "tokenrouter_base_url",
    "tokenrouter_model",
    "tokenrouter_timeout",
    "tokenrouter_gemini_api_key",
    "tokenrouter_gemini_base_url",
    "tokenrouter_gemini_model",
    "tokenrouter_gemini_timeout",
    "inferx_api_key",
    "inferx_base_url",
    "inferx_model",
    "inferx_timeout",
    "nvidia_api_key",
    "nvidia_base_url",
    "nvidia_model",
    "nvidia_fast_model",
    "nvidia_timeout",
    "zenmux_api_key",
    "xkiro_api_key", "xkiro_model", "xkiro_base_url", "xkiro_timeout",
    "zai_api_key", "zai_model", "zai_base_url", "zai_timeout",
    "zenmux_base_url",
    "zenmux_model",
    "zenmux_timeout",
    "tokenharbor_api_key",
    "tokenharbor_base_url",
    "tokenharbor_model",
    "tokenharbor_timeout",
    "male_lead_name",
    "female_lead_name",
    "chunk_size",
    "chars_per_sec",
)


def _translation_cfg_for_gui() -> Dict:
    tr = (_load_cfg().get("translation") or {})
    return {
        "provider": tr.get("provider", "browser"),
        "gemini_api_key": tr.get("gemini_api_key", ""),
        "gemini_model": tr.get("gemini_model", "gemini-3.6-flash"),
        "tokenrouter_api_key": tr.get("tokenrouter_api_key", ""),
        "tokenrouter_base_url": tr.get("tokenrouter_base_url",
                                       TOKENROUTER_DEFAULT_BASE_URL),
        "tokenrouter_model": tr.get("tokenrouter_model",
                                    TOKENROUTER_DEFAULT_MODEL),
        "tokenrouter_timeout": tr.get("tokenrouter_timeout", 420),
        "tokenrouter_gemini_api_key": tr.get("tokenrouter_gemini_api_key", ""),
        "tokenrouter_gemini_base_url": tr.get(
            "tokenrouter_gemini_base_url",
            TOKENROUTER_GEMINI_DEFAULT_BASE_URL),
        "tokenrouter_gemini_model": tr.get(
            "tokenrouter_gemini_model", TOKENROUTER_GEMINI_DEFAULT_MODEL),
        "tokenrouter_gemini_timeout": tr.get("tokenrouter_gemini_timeout", 420),
        "inferx_api_key": tr.get("inferx_api_key", ""),
        "inferx_base_url": tr.get(
            "inferx_base_url", INFERX_DEFAULT_BASE_URL),
        "inferx_model": tr.get("inferx_model", INFERX_DEFAULT_MODEL),
        "inferx_timeout": tr.get("inferx_timeout", 420),
        "nvidia_api_key": tr.get("nvidia_api_key", ""),
        "nvidia_base_url": tr.get("nvidia_base_url", NVIDIA_DEFAULT_BASE_URL),
        "nvidia_model": tr.get("nvidia_model", NVIDIA_DEFAULT_MODEL),
        "nvidia_fast_model": tr.get("nvidia_fast_model", NVIDIA_FAST_MODEL),
        "nvidia_timeout": tr.get("nvidia_timeout", 420),
        "zenmux_api_key": tr.get("zenmux_api_key", ""),
        "xkiro_api_key": tr.get("xkiro_api_key", ""),
        "xkiro_model": tr.get("xkiro_model", XKIRO_DEFAULT_MODEL),
        "xkiro_base_url": tr.get("xkiro_base_url", XKIRO_DEFAULT_BASE_URL),
        "xkiro_timeout": tr.get("xkiro_timeout", 120),
        "zai_api_key": tr.get("zai_api_key", ""),
        "zai_model": tr.get("zai_model", ZAI_DEFAULT_MODEL),
        "zai_base_url": tr.get("zai_base_url", ZAI_DEFAULT_BASE_URL),
        "zai_timeout": tr.get("zai_timeout", 120),
        "zenmux_base_url": tr.get("zenmux_base_url", ZENMUX_DEFAULT_BASE_URL),
        "zenmux_model": tr.get("zenmux_model", ZENMUX_DEFAULT_MODEL),
        "zenmux_timeout": tr.get("zenmux_timeout", 420),
        "tokenharbor_api_key": tr.get("tokenharbor_api_key", ""),
        "tokenharbor_base_url": tr.get("tokenharbor_base_url",
                                       TOKENHARBOR_DEFAULT_BASE_URL),
        "tokenharbor_model": tr.get("tokenharbor_model",
                                    TOKENHARBOR_DEFAULT_MODEL),
        "tokenharbor_timeout": tr.get("tokenharbor_timeout", 120),
        "male_lead_name": tr.get("male_lead_name", ""),
        "female_lead_name": tr.get("female_lead_name", ""),
        "chunk_size": tr.get("chunk_size", 80),
        "chars_per_sec": tr.get("chars_per_sec", 14),
    }


def _tts_cfg_for_gui() -> Dict:
    tc = (_load_cfg().get("tts") or {})
    engine = str(tc.get("engine", "edge") or "edge").lower()
    voice = (tc.get("vieneu_voice") if engine == "vieneu"
             else tc.get("capcut_voice") if engine == "capcut"
             else tc.get("narrator_voice"))
    return {
        "engine": engine,
        "voice": voice or "vi-VN-NamMinhNeural",
        "pitch": tc.get("narrator_pitch", "+0Hz"),
        "rate": tc.get("base_rate", "+0%"),
    }


def _yaml_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None or value == "":
        return '""'
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _patch_yaml_section(section: str, clean: Dict, known_keys: Tuple[str, ...]
                        ) -> None:
    """Sửa từng khoá trong một mục YAML, giữ comment và thứ tự cũ."""
    with _CFG_LOCK:
        _patch_yaml_section_locked(section, clean, known_keys)


def _patch_yaml_section_locked(section: str, clean: Dict, known_keys: Tuple[str, ...]) -> None:
    with open(CONFIG_PATH, "r", encoding="utf-8", newline="") as f:
        lines = f.readlines()

    start = next((i for i, line in enumerate(lines)
                  if re.match(r"^%s\s*:" % re.escape(section), line)), None)
    if start is None:
        lines.append("\n%s:\n" % section)
        start = len(lines) - 1
        end = len(lines)
    else:
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if re.match(r"^\S", lines[i]) and not lines[i].lstrip().startswith("#"):
                end = i
                break

    seen = set()
    for i in range(start + 1, end):
        m = re.match(r"^(\s*)([A-Za-z0-9_]+)(\s*:\s*)(.*?)(\s+#.*)?(\r?\n)?$",
                     lines[i])
        if not m:
            continue
        key = m.group(2)
        if key not in clean:
            continue
        newline = m.group(6) or "\n"
        comment = m.group(5) or ""
        lines[i] = f"{m.group(1)}{key}{m.group(3)}{_yaml_scalar(clean[key])}{comment}{newline}"
        seen.add(key)

    missing = [k for k in known_keys if k in clean and k not in seen]
    if missing:
        insert = [f"  {k}: {_yaml_scalar(clean[k])}\n" for k in missing]
        lines[end:end] = insert

    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)
    os.replace(tmp, CONFIG_PATH)
    _invalidate_cfg_cache()


def _save_translation_cfg(updates: Dict) -> Dict:
    clean = {k: updates[k] for k in _TRANSLATION_GUI_KEYS if k in updates}
    provider = str(clean.get("provider", "") or "").lower()
    if provider and provider not in {"browser", "gemini", "tokenrouter",
                                     "tokenrouter_gemini", "inferx", "nvidia",
                                     "zenmux", "tokenharbor", "xkiro", "zai"}:
        raise ValueError("provider must be browser, gemini, tokenrouter, "
                         "tokenrouter_gemini, inferx, nvidia, zenmux, "
                         "tokenharbor, xkiro, or zai")
    if "xkiro_timeout" in clean:
        clean["xkiro_timeout"] = max(30, int(float(clean["xkiro_timeout"] or 120)))
    if "zai_timeout" in clean:
        clean["zai_timeout"] = max(30, int(float(clean["zai_timeout"] or 120)))
    if "chunk_size" in clean:
        clean["chunk_size"] = max(1, int(float(clean["chunk_size"] or 80)))
    if "chars_per_sec" in clean:
        clean["chars_per_sec"] = max(0, float(clean["chars_per_sec"] or 0))
    if "tokenrouter_timeout" in clean:
        clean["tokenrouter_timeout"] = max(60, int(float(clean["tokenrouter_timeout"] or 420)))
    if "tokenrouter_gemini_timeout" in clean:
        clean["tokenrouter_gemini_timeout"] = max(
            60, int(float(clean["tokenrouter_gemini_timeout"] or 420)))
    if "inferx_timeout" in clean:
        clean["inferx_timeout"] = max(60, int(float(clean["inferx_timeout"] or 420)))
    if "nvidia_timeout" in clean:
        clean["nvidia_timeout"] = max(60, int(float(clean["nvidia_timeout"] or 420)))
    if "zenmux_timeout" in clean:
        clean["zenmux_timeout"] = max(60, int(float(clean["zenmux_timeout"] or 420)))
    if "tokenharbor_timeout" in clean:
        clean["tokenharbor_timeout"] = max(30, int(float(clean["tokenharbor_timeout"] or 120)))

    _patch_yaml_section("translation", clean, _TRANSLATION_GUI_KEYS)
    return _translation_cfg_for_gui()


_CONTENT_PIPELINE_GUI_KEYS = ("provider",)
_CONTENT_PIPELINE_PROVIDERS = (
    "auto", "browser", "perplexity_browser", "tokenharbor", "nvidia",
    "heuristic", "offline",
)


def _content_pipeline_cfg_for_gui() -> Dict:
    cp = (_load_cfg().get("content_pipeline") or {})
    return {"provider": str(cp.get("provider") or "browser")}


_DANG_YOUTUBE_GUI_KEYS = (
    "auto_thumbnail",
    "auto_description",
    "auto_dub_thumbnail",
    "dub_scene_count",
    "dub_scene_seconds",
    "browser_url",
    "browser_channel",
    "browser_profile",
    "wait_image_seconds",
    "wait_reply_seconds",
)


def _dang_youtube_cfg_for_gui() -> Dict:
    yt = (_load_cfg().get("dang_youtube") or {})
    return {
        "auto_thumbnail": bool(yt.get("auto_thumbnail", True)),
        "auto_description": bool(yt.get("auto_description", True)),
        "auto_dub_thumbnail": bool(yt.get("auto_dub_thumbnail", False)),
        "dub_scene_count": max(1, min(8, int(float(yt.get("dub_scene_count", 4) or 4)))),
        "dub_scene_seconds": max(1.5, min(8.0, float(yt.get("dub_scene_seconds", 3) or 3))),
        "browser_url": str(yt.get("browser_url") or "https://chatgpt.com/"),
        "browser_channel": str(yt.get("browser_channel") or "msedge"),
        "browser_profile": str(yt.get("browser_profile") or "browser_profile_chatgpt"),
        "wait_image_seconds": max(45, int(float(yt.get("wait_image_seconds", 120) or 120))),
        "wait_reply_seconds": max(45, int(float(yt.get("wait_reply_seconds", 180) or 180))),
    }


def _save_dang_youtube_cfg(updates: Dict) -> Dict:
    clean = {k: updates[k] for k in _DANG_YOUTUBE_GUI_KEYS if k in updates}
    if "auto_thumbnail" in clean:
        clean["auto_thumbnail"] = bool(clean["auto_thumbnail"])
    if "auto_description" in clean:
        clean["auto_description"] = bool(clean["auto_description"])
    if "auto_dub_thumbnail" in clean:
        clean["auto_dub_thumbnail"] = bool(clean["auto_dub_thumbnail"])
    if "dub_scene_count" in clean:
        clean["dub_scene_count"] = max(1, min(8, int(float(clean["dub_scene_count"] or 4))))
    if "dub_scene_seconds" in clean:
        clean["dub_scene_seconds"] = max(1.5, min(8.0, float(clean["dub_scene_seconds"] or 3)))
    if "wait_image_seconds" in clean:
        clean["wait_image_seconds"] = max(
            45, int(float(clean["wait_image_seconds"] or 120)))
    if "wait_reply_seconds" in clean:
        clean["wait_reply_seconds"] = max(
            45, int(float(clean["wait_reply_seconds"] or 180)))
    if "browser_profile" in clean:
        profile = str(clean.get("browser_profile") or "browser_profile_chatgpt").strip()
        if profile in {"browser_profile", "browser_profile_gemini"}:
            profile = "browser_profile_chatgpt"
        clean["browser_profile"] = profile or "browser_profile_chatgpt"
    if clean:
        _patch_yaml_section("dang_youtube", clean, _DANG_YOUTUBE_GUI_KEYS)
    return _dang_youtube_cfg_for_gui()


def _save_content_pipeline_cfg(updates: Dict) -> Dict:
    clean = {k: updates[k] for k in _CONTENT_PIPELINE_GUI_KEYS if k in updates}
    if "provider" in clean:
        provider = str(clean.get("provider") or "browser").strip().lower()
        if provider not in _CONTENT_PIPELINE_PROVIDERS:
            raise ValueError(
                "content_pipeline.provider must be browser, nvidia, "
                "tokenharbor, perplexity_browser, heuristic, or auto")
        clean["provider"] = provider
    if clean:
        _patch_yaml_section("content_pipeline", clean, _CONTENT_PIPELINE_GUI_KEYS)
    return _content_pipeline_cfg_for_gui()


def _nvidia_catalog_for_gui() -> list:
    return [dict(item) for item in NVIDIA_CATALOG]


def _translation_api_params(tr: Dict, provider: str) -> Tuple[str, str, Optional[str], int]:
    """Cùng một bảng với CLI: autodub/providers.py."""
    return api_params_for_provider(tr, provider)


def _test_translation_api(overrides: Optional[Dict] = None) -> Dict:
    from .. import translate as tr_mod

    cfg_tr = _translation_cfg_for_gui()
    if isinstance(overrides, dict):
        cfg_tr.update({k: v for k, v in overrides.items()
                       if k in _TRANSLATION_GUI_KEYS})
    provider = str(cfg_tr.get("provider", "browser") or "browser").lower()
    if provider == "browser":
        return {"ok": True, "provider": provider,
                "message": "browser mode khong dung API key"}
    if provider not in {"gemini", "tokenrouter", "tokenrouter_gemini",
                        "inferx", "nvidia", "zenmux", "tokenharbor", "xkiro",
                        "zai"}:
        raise ValueError("provider khong hop le")

    api_key, model, base_url, timeout = _translation_api_params(cfg_tr, provider)
    if not api_key:
        if provider == "nvidia":
            raise ValueError(
                "Chưa lưu NVIDIA API key. Mở Cài đặt > API, dán key nvapi-... "
                "mới rồi bấm Kiểm tra kết nối API.")
        raise ValueError("Chưa điền API key cho provider đang chọn")
    raw = tr_mod._api_call(
        'Dịch đúng một dòng sau sang tiếng Việt. Chỉ trả về mảng JSON gồm '
        'một chuỗi không rỗng, không giải thích: ["你好"]',
        api_key, model, 0.1, provider, base_url, min(timeout, 45), api_retries=1)
    parsed = tr_mod._parse_json_lines(raw, 1)
    if not parsed or not parsed[0].strip() or tr_mod._contains_cjk(parsed[0]):
        return {"ok": False, "provider": provider, "model": model,
                "error": "API có phản hồi nhưng sai định dạng kiểm tra JSON; chưa xác nhận dùng được để dịch.",
                "raw": str(raw)[:200]}
    return {"ok": True, "provider": provider, "model": model,
            "message": "API key/model hoat dong"}
