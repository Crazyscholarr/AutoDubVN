# -*- coding: utf-8 -*-
"""Một bảng provider API cho dịch, kho ý tưởng và GUI.

Trước đây URL/model mặc định nằm rải ở translate.py, config_api.py và app.js.
Đổi một chỗ ở đây là đủ; các module khác chỉ import lại.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

TOKENROUTER_DEFAULT_BASE_URL = "https://api.tokenrouter.com/v1"
TOKENROUTER_DEFAULT_MODEL = "moonshotai/kimi-k3-free"
TOKENROUTER_GEMINI_DEFAULT_BASE_URL = "https://api.tokenrouter.com/v1beta/models"
TOKENROUTER_GEMINI_DEFAULT_MODEL = "google/gemini-3.6-flash"
INFERX_DEFAULT_BASE_URL = "https://model.inferx.net/endpoints/v1"
INFERX_DEFAULT_MODEL = "deepseek-v4-flash"
NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_DEFAULT_MODEL = "google/gemma-4-31b-it"
NVIDIA_FAST_MODEL = "nvidia/nemotron-3-super-120b-a12b"
NVIDIA_FLASH_MODEL = "deepseek-ai/deepseek-v4-flash-0731"
NVIDIA_PRO_MODEL = "deepseek-ai/deepseek-v4-pro-0813"
NVIDIA_LIGHTNING_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
ZENMUX_DEFAULT_BASE_URL = "https://zenmux.ai/api/v1"
ZENMUX_DEFAULT_MODEL = "z-ai/glm-4.7-flash-free"
TOKENHARBOR_DEFAULT_BASE_URL = "https://tokenharbor.ai/v1"
TOKENHARBOR_DEFAULT_MODEL = "deepseek-v4-flash:free"
XKIRO_DEFAULT_BASE_URL = "https://api.xkiro.com/v1"
XKIRO_DEFAULT_MODEL = "qwen/qwen3.5-flash:free"
ZAI_DEFAULT_BASE_URL = "https://api.z.ai/api/paas/v4"
ZAI_DEFAULT_MODEL = "glm-4.7-flash"
ZAI_CATALOG = (
    {"id": "glm-4.7-flash", "label": "GLM-4.7-Flash · miễn phí (đã kiểm thử)"},
    {"id": "glm-4.5-flash", "label": "GLM-4.5-Flash · miễn phí, chậm hơn"},
    {"id": "glm-4.6v-flash", "label": "GLM-4.6V-Flash · vision, hay quá tải"},
)

# Catalog hiện trên Cài đặt: id gửi API + nhãn tiếng Việt.
NVIDIA_CATALOG = (
    {"id": NVIDIA_DEFAULT_MODEL, "label": "Gemma 4 31B · dịch (ổn, ít treo)"},
    {"id": NVIDIA_FAST_MODEL, "label": "Nemotron Super 120B · kho / dự phòng"},
    {"id": NVIDIA_FLASH_MODEL, "label": "DeepSeek V4 Flash · hay nhưng hay kẹt cổng"},
    {"id": NVIDIA_PRO_MODEL, "label": "DeepSeek V4 Pro · chậm, dễ treo"},
    {"id": NVIDIA_LIGHTNING_MODEL, "label": "Nemotron Lightning 30B · JSON rất nhanh"},
)

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def strip_think(text: str) -> str:
    """Bỏ khối <think> mà model suy luận đôi khi nhét vào JSON/dịch."""
    return _THINK_RE.sub("", str(text or "")).strip()


def nvidia_chat_extras(model: str) -> dict:
    """Tắt thinking trên NIM để JSON dịch/kho ý tưởng không lẫn chuỗi suy nghĩ."""
    name = str(model or "").strip().lower()
    if "nemotron" in name:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if "kimi" in name:
        return {"reasoning_effort": "low"}
    return {"chat_template_kwargs": {"thinking": False}}


def nvidia_model_for_ideas(tr: Dict[str, Any], cp: Dict[str, Any]) -> str:
    """Model NVIDIA cho kho ý tưởng: ép riêng, rồi bản nhanh, không thì để trống."""
    tr = tr if isinstance(tr, dict) else {}
    cp = cp if isinstance(cp, dict) else {}
    return str(
        cp.get("nvidia_model") or tr.get("nvidia_fast_model") or ""
    ).strip()


def api_params_for_provider(tr: dict, provider: str
                            ) -> Tuple[str, str, Optional[str], int]:
    """Lấy (api_key, model, base_url, timeout) đúng theo provider trong config."""
    tr = tr or {}
    p = str(provider or "gemini").strip().lower()
    if p == "xkiro":
        return (tr.get("xkiro_api_key", ""),
                tr.get("xkiro_model") or XKIRO_DEFAULT_MODEL,
                tr.get("xkiro_base_url") or XKIRO_DEFAULT_BASE_URL,
                int(tr.get("xkiro_timeout", 120) or 120))
    if p == "tokenrouter_gemini":
        return (tr.get("tokenrouter_gemini_api_key", ""),
                tr.get("tokenrouter_gemini_model", TOKENROUTER_GEMINI_DEFAULT_MODEL),
                tr.get("tokenrouter_gemini_base_url"),
                int(tr.get("tokenrouter_gemini_timeout", 420) or 420))
    if p == "tokenrouter":
        return (tr.get("tokenrouter_api_key", ""),
                tr.get("tokenrouter_model", TOKENROUTER_DEFAULT_MODEL),
                tr.get("tokenrouter_base_url"),
                int(tr.get("tokenrouter_timeout", 420) or 420))
    if p == "inferx":
        return (tr.get("inferx_api_key", ""),
                tr.get("inferx_model", INFERX_DEFAULT_MODEL),
                tr.get("inferx_base_url"),
                int(tr.get("inferx_timeout", 420) or 420))
    if p == "nvidia":
        return (tr.get("nvidia_api_key", ""),
                tr.get("nvidia_model", NVIDIA_DEFAULT_MODEL),
                tr.get("nvidia_base_url"),
                int(tr.get("nvidia_timeout", 420) or 420))
    if p == "zenmux":
        return (tr.get("zenmux_api_key", ""),
                tr.get("zenmux_model", ZENMUX_DEFAULT_MODEL),
                tr.get("zenmux_base_url"),
                int(tr.get("zenmux_timeout", 420) or 420))
    if p == "tokenharbor":
        return (tr.get("tokenharbor_api_key", ""),
                tr.get("tokenharbor_model", TOKENHARBOR_DEFAULT_MODEL),
                tr.get("tokenharbor_base_url"),
                int(tr.get("tokenharbor_timeout", 120) or 120))
    if p == "zai":
        return (tr.get("zai_api_key", ""),
                tr.get("zai_model") or ZAI_DEFAULT_MODEL,
                tr.get("zai_base_url") or ZAI_DEFAULT_BASE_URL,
                int(tr.get("zai_timeout", 120) or 120))
    return (tr.get("gemini_api_key", ""),
            tr.get("gemini_model", "gemini-3.6-flash"), None, 420)
