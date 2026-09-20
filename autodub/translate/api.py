from __future__ import annotations

import json
import threading
import time
import urllib.request
import urllib.error
from typing import List, Optional

from .const import GEMINI_URL, SYSTEM_INSTRUCTION, system_instruction_for_prompt
from ..utils import active_cancel_event, log, raise_if_cancelled, wait_or_cancel
from ..providers import (
    XKIRO_DEFAULT_BASE_URL, XKIRO_DEFAULT_MODEL,
    TOKENROUTER_DEFAULT_BASE_URL, TOKENROUTER_DEFAULT_MODEL,
    TOKENROUTER_GEMINI_DEFAULT_BASE_URL, TOKENROUTER_GEMINI_DEFAULT_MODEL,
    INFERX_DEFAULT_BASE_URL, INFERX_DEFAULT_MODEL,
    NVIDIA_DEFAULT_BASE_URL, NVIDIA_DEFAULT_MODEL,
    NVIDIA_FAST_MODEL,
    ZENMUX_DEFAULT_BASE_URL, ZENMUX_DEFAULT_MODEL,
    TOKENHARBOR_DEFAULT_BASE_URL, TOKENHARBOR_DEFAULT_MODEL,
    ZAI_DEFAULT_BASE_URL, ZAI_DEFAULT_MODEL,
    nvidia_chat_extras as _nvidia_chat_extras,
    strip_think as _strip_think,
)

# DeepSeek V4 Pro/Flash hay giữ TCP im lặng. Gemma 4 trả lời ổn ~20s;
# treo thì chuyển Nemotron Super (NIM NVIDIA, ~1s).
_NVIDIA_PRO_WALL = 90
_NVIDIA_PRO_IDLE = 45
_NVIDIA_FLASH_WALL = 45
_NVIDIA_FLASH_IDLE = 30
_NVIDIA_GEMMA_WALL = 45
_NVIDIA_GEMMA_IDLE = 30
_NVIDIA_SUPER_WALL = 40
_NVIDIA_SUPER_IDLE = 25
_NVIDIA_FAST_WALL = 90


class _DeadlineHangError(TimeoutError):
    """Kết nối còn mở nhưng không trả xong trong hạn tường."""


def _nvidia_slow_model(model: str) -> bool:
    return "deepseek-v4-pro" in str(model or "").strip().lower()


def _nvidia_flash_model(model: str) -> bool:
    return "deepseek-v4-flash" in str(model or "").strip().lower()


def _nvidia_wall_idle(model: str, configured: int) -> tuple:
    """Hạn tường / idle theo model. NIM free hay giữ TCP im lặng."""
    configured = max(30, int(configured or 420))
    name = str(model or "").strip().lower()
    if "deepseek-v4-pro" in name:
        wall = min(configured, _NVIDIA_PRO_WALL)
        return wall, min(_NVIDIA_PRO_IDLE, wall)
    if "deepseek-v4-flash" in name:
        wall = min(configured, _NVIDIA_FLASH_WALL)
        return wall, min(_NVIDIA_FLASH_IDLE, wall)
    if "gemma-4" in name:
        wall = min(configured, _NVIDIA_GEMMA_WALL)
        return wall, min(_NVIDIA_GEMMA_IDLE, wall)
    if "nemotron-3-super" in name:
        wall = min(configured, _NVIDIA_SUPER_WALL)
        return wall, min(_NVIDIA_SUPER_IDLE, wall)
    wall = min(configured, _NVIDIA_FAST_WALL)
    return wall, wall


def _is_hang_or_stall(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, _DeadlineHangError)):
        return True
    msg = str(exc).lower()
    return any(key in msg for key in (
        "treo qua", "het han tuong", "timed out", "timeout",
        "response content is empty",
    ))


def _urlopen_deadline(req, *, wall: int, idle: int, stream: bool) -> dict:
    """urlopen + đọc body; đóng socket khi quá hạn tường dù TCP còn keepalive."""
    raise_if_cancelled()
    wall = max(1, int(wall))
    idle = max(1, int(idle))
    holder = {"resp": None, "data": None, "lines": None, "err": None}
    done = threading.Event()

    def worker() -> None:
        try:
            with urllib.request.urlopen(req, timeout=idle) as resp:
                holder["resp"] = resp
                if stream:
                    holder["lines"] = list(resp)
                else:
                    holder["data"] = resp.read()
        except Exception as exc:
            holder["err"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=worker, daemon=True, name="llm-http-deadline")
    thread.start()
    t0 = time.monotonic()

    def _close_resp() -> None:
        resp = holder.get("resp")
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    while not done.is_set():
        event = active_cancel_event()
        if event is not None and event.is_set():
            _close_resp()
            raise InterruptedError("Đã hủy tác vụ")
        remain = wall - (time.monotonic() - t0)
        if remain <= 0:
            _close_resp()
            raise _DeadlineHangError(
                "treo qua %ss (ket noi con mo, khong nhan xong phan hoi)" % wall)
        done.wait(min(0.2, max(0.01, remain)))
    thread.join(0.5)
    raise_if_cancelled()
    if holder["err"] is not None:
        raise holder["err"]
    return holder


# --------------------------------------------------------------------------- #
#  Chế độ 1: Gemini REST API
# --------------------------------------------------------------------------- #
_GEMINI_FALLBACK_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]


def _gemini_model_candidates(model: str) -> List[str]:
    seen, out = set(), []
    for m in [str(model or "").strip(), *_GEMINI_FALLBACK_MODELS]:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _gemini_call(prompt: str, api_key: str, model: str, temperature: float,
                 retries: int = 3) -> str:
    body = {
        "system_instruction": {"parts": [{"text": system_instruction_for_prompt(prompt)}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "topP": 0.9,
                             "responseMimeType": "application/json"},
    }
    data = json.dumps(body).encode("utf-8")
    last_err = None
    candidates = [model] if prompt.startswith("[AUTODUB_SEMANTIC_V1]\n") else _gemini_model_candidates(model)
    for cand_model in candidates:
        url = GEMINI_URL.format(model=cand_model, key=api_key)
        for attempt in range(retries):
            raise_if_cancelled()
            try:
                req = urllib.request.Request(
                    url, data=data, headers={"Content-Type": "application/json"})
                holder = _urlopen_deadline(req, wall=120, idle=120, stream=False)
                out = json.loads((holder.get("data") or b"").decode("utf-8"))
                if cand_model != model:
                    log(f"Gemini model '{model}' không dùng được, đã chuyển sang '{cand_model}'.", "warn")
                return out["candidates"][0]["content"]["parts"][0]["text"]
            except InterruptedError:
                raise
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "ignore")[:500]
                last_err = f"{cand_model}: HTTP {e.code}: {msg}"
                if e.code in (404, 410):
                    break
                if e.code in (429, 500, 503):
                    wait_or_cancel(2 * (attempt + 1), sleeper=time.sleep)
                    continue
                break
            except Exception as e:
                last_err = f"{cand_model}: {e}"
                wait_or_cancel(1.5 * (attempt + 1), sleeper=time.sleep)
                break
    raise RuntimeError(f"Gemini lỗi: {last_err}")


def _openai_compatible_chat_url(base_url: Optional[str]) -> str:
    base = (base_url or TOKENROUTER_DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1") or base.endswith("/v4"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _tokenrouter_gemini_url(base_url: Optional[str], model: str) -> str:
    base = (base_url or TOKENROUTER_GEMINI_DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith(":generateContent"):
        return base
    if not base.endswith("/models"):
        base += "/models"
    return f"{base}/{model or TOKENROUTER_GEMINI_DEFAULT_MODEL}:generateContent"


def _message_content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return "" if content is None else str(content)


def _openai_message_text(msg: dict) -> str:
    """Lấy nội dung trả lời; fallback reasoning_content rồi gỡ <think>."""
    msg = msg or {}
    text = _strip_think(_message_content_to_text(msg.get("content")))
    if text:
        return text
    return _strip_think(_message_content_to_text(msg.get("reasoning_content")))


def _http_error_summary(provider_label: str, status: int, body: str) -> str:
    msg = body
    try:
        obj = json.loads(body)
        err = obj.get("error") if isinstance(obj, dict) else None
        if isinstance(err, dict):
            msg = err.get("message") or body
    except Exception:
        pass
    msg = str(msg or "").strip()
    if status == 401:
        return f"{provider_label}: key khong hop le hoac da bi tat (HTTP 401: {msg})"
    if status == 403:
        return f"{provider_label}: truy cap bi tu choi; kiem tra quyen key/model hoac cong bao ve API (HTTP 403: {msg})"
    if status == 429:
        return f"{provider_label}: het quota/rate limit hoac model dang qua tai (HTTP 429: {msg})"
    return f"{provider_label}: HTTP {status}: {msg}"


def _text_from_openai_json(out: dict) -> str:
    choices = out.get("choices") or []
    if not choices:
        raise RuntimeError("response has no choices")
    msg = choices[0].get("message") or {}
    return _openai_message_text(msg) or _strip_think(
        _message_content_to_text(choices[0].get("text", "")))


def _text_from_openai_sse(lines) -> str:
    parts = []
    for raw_line in lines or []:
        line = raw_line.decode("utf-8", "ignore").strip() if isinstance(
            raw_line, (bytes, bytearray)) else str(raw_line).strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        elif line.startswith("event:"):
            continue
        if line == "[DONE]":
            break
        try:
            chunk = json.loads(line)
        except Exception:
            continue
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            text = _message_content_to_text(delta.get("content"))
            if not text:
                text = _message_content_to_text(delta.get("reasoning_content"))
            if text:
                parts.append(text)
            elif choice.get("message"):
                text = _openai_message_text(choice["message"])
                if text:
                    parts.append(text)
    return _strip_think("".join(parts)) if parts else ""


def _openai_compatible_call(prompt: str, api_key: str, model: str,
                            temperature: float, base_url: Optional[str] = None,
                            retries: int = 3, timeout: int = 420,
                            stream: bool = True,
                            provider_label: str = "TokenRouter",
                            rate_limit_wait: float = 2.0,
                            idle_timeout: Optional[int] = None,
                            enforce_wall: bool = False) -> str:
    """Gọi endpoint chuẩn OpenAI /chat/completions (TokenRouter/InferX/NVIDIA...).

    rate_limit_wait: số giây chờ NỀN khi dính HTTP 429, nhân dần theo lần thử.
    Tier free của NVIDIA chặn burst khá gắt (nhánh dịch-lại-từng-câu từng chết
    vì retry 2-4s quá ngắn) nên provider đó truyền mức chờ dài hơn.

    enforce_wall: cắt kết nối theo đồng hồ tường dù socket còn keepalive
    (DeepSeek V4 Pro từng treo 17 phút vì timeout urllib chỉ tính idle).
    """
    body = {
        "model": model or TOKENROUTER_DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": system_instruction_for_prompt(prompt)},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }
    if str(model or "").strip().lower() == "minimaxai/minimax-m3":
        # Tham số theo API Reference chính thức của NVIDIA cho MiniMax M3.
        # Giữ temperature do từng tác vụ truyền vào để bản dịch/JSON ổn định.
        body["top_p"] = 0.95
        body["max_tokens"] = 8192
    host = str(base_url or "").lower()
    if "nvidia.com" in host or provider_label == "NVIDIA":
        body.update(_nvidia_chat_extras(model))
        if _nvidia_slow_model(model) or _nvidia_flash_model(model):
            body.setdefault("max_tokens", 4096)
    if "api.z.ai" in host or provider_label == "Z.AI":
        body["thinking"] = {"type": "disabled"}
        body.setdefault("max_tokens", 4096)
    if "tokenharbor" in host or str(model or "").endswith(":free"):
        body["max_tokens"] = int(body.get("max_tokens") or 4096)
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    data = json.dumps(body).encode("utf-8")
    url = _openai_compatible_chat_url(base_url)
    wall = max(1, int(timeout or 420))
    idle = max(1, int(idle_timeout if idle_timeout is not None else wall))
    last_err = None
    for attempt in range(retries):
        raise_if_cancelled()
        try:
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": "AutoDubVN/1.0",
                    "Accept-Language": "en-US,en",
                    "Accept": "text/event-stream" if stream else "application/json",
                },
            )
            if enforce_wall:
                holder = _urlopen_deadline(
                    req, wall=wall, idle=idle, stream=stream)
                if not stream:
                    out = json.loads((holder.get("data") or b"").decode("utf-8"))
                    text = _text_from_openai_json(out)
                    if text:
                        return text
                else:
                    text = _text_from_openai_sse(holder.get("lines"))
                    if text:
                        return text
            else:
                with urllib.request.urlopen(
                        req, timeout=max(60, int(timeout or 420))) as resp:
                    if not stream:
                        out = json.loads(resp.read().decode("utf-8"))
                        text = _text_from_openai_json(out)
                        if text:
                            return text
                    else:
                        text = _text_from_openai_sse(resp)
                        if text:
                            return text
            raise RuntimeError("response content is empty")
        except InterruptedError:
            raise
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")[:500]
            last_err = _http_error_summary(provider_label, e.code, msg)
            if e.code in (429, 500, 502, 503, 504):
                # Đừng ngủ sau LẦN THỬ CUỐI: đằng nào cũng raise ngay sau đó,
                # ngủ thêm chỉ bắt người dùng chờ không. Mỗi nhịp chờ trần 60s
                # để không bao giờ treo quá lâu một chỗ.
                if attempt + 1 < retries:
                    base_wait = rate_limit_wait if e.code == 429 else 2.0
                    wait_or_cancel(min(60.0, base_wait * (attempt + 1)),
                                   sleeper=time.sleep)
                continue
            break
        except (TimeoutError, _DeadlineHangError) as e:
            last_err = str(e)
            # NIM hay kẹt một nhịp rồi lần sau ~1–2s xong. Gọi lại ngay,
            # không chờ 15s như 429; hết retries thì thôi.
            if attempt + 1 < retries:
                log("%s treo %ss; goi lai lan %s/%s."
                    % (provider_label, wall, attempt + 2, retries), "warn")
                wait_or_cancel(2.0, sleeper=time.sleep)
                continue
            break
        except Exception as e:
            last_err = str(e)
            if attempt + 1 < retries:
                wait_or_cancel(1.5 * (attempt + 1), sleeper=time.sleep)
    raise RuntimeError(f"{provider_label} loi: {last_err}")


def _anthropic_messages_call(prompt: str, api_key: str, model: str,
                             temperature: float, base_url: Optional[str] = None,
                             retries: int = 2, timeout: int = 120) -> str:
    """Token Harbor cũng nói giao thức Anthropic /v1/messages (Harbor Bench).

    Dùng khi /chat/completions lỗi hoặc trả rỗng. Không in key ra log.
    """
    root = str(base_url or TOKENHARBOR_DEFAULT_BASE_URL).rstrip("/")
    url = root + "/messages" if root.endswith("/v1") else root + "/v1/messages"
    body = {
        "model": model or TOKENHARBOR_DEFAULT_MODEL,
        "max_tokens": 4096,
        "temperature": temperature,
        "system": system_instruction_for_prompt(prompt),
        "messages": [{"role": "user", "content": prompt}],
    }
    data = json.dumps(body).encode("utf-8")
    last_err = None
    for attempt in range(retries):
        raise_if_cancelled()
        try:
            req = urllib.request.Request(
                url, data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + api_key,
                    "anthropic-version": "2023-06-01",
                    "Accept": "application/json",
                },
            )
            holder = _urlopen_deadline(
                req, wall=max(30, int(timeout or 120)),
                idle=max(30, int(timeout or 120)), stream=False)
            out = json.loads((holder.get("data") or b"").decode("utf-8"))
            chunks = out.get("content") or []
            parts = []
            for item in chunks:
                if isinstance(item, dict) and item.get("type") in (None, "text"):
                    parts.append(str(item.get("text") or ""))
                elif isinstance(item, str):
                    parts.append(item)
            text = "".join(parts).strip()
            if text:
                return text
            raise RuntimeError("response content is empty")
        except InterruptedError:
            raise
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")[:500]
            last_err = _http_error_summary("TokenHarbor", e.code, msg)
            if e.code in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                wait_or_cancel(min(30.0, 5.0 * (attempt + 1)), sleeper=time.sleep)
                continue
            break
        except Exception as e:
            last_err = str(e)
            if attempt + 1 < retries:
                wait_or_cancel(1.5 * (attempt + 1), sleeper=time.sleep)
    raise RuntimeError(f"TokenHarbor loi: {last_err}")


def _tokenharbor_call(prompt: str, api_key: str, model: str, temperature: float,
                      api_base_url: Optional[str] = None, api_timeout: int = 120,
                      api_retries: Optional[int] = None) -> str:
    """OpenAI /chat/completions trước; Harbor Bench trên web dùng /messages."""
    retries = 2 if api_retries is None else max(1, int(api_retries))
    timeout = max(30, int(api_timeout or 120))
    base = api_base_url or TOKENHARBOR_DEFAULT_BASE_URL
    try:
        return _openai_compatible_call(
            prompt, api_key, model or TOKENHARBOR_DEFAULT_MODEL,
            temperature, base, retries=retries, timeout=timeout,
            stream=False, provider_label="TokenHarbor", rate_limit_wait=5.0)
    except RuntimeError as openai_err:
        try:
            return _anthropic_messages_call(
                prompt, api_key, model or TOKENHARBOR_DEFAULT_MODEL,
                temperature, base, retries=retries, timeout=timeout)
        except RuntimeError as anthropic_err:
            raise RuntimeError(
                "TokenHarbor loi: %s | du phong Anthropic: %s"
                % (openai_err, anthropic_err))


def _tokenrouter_gemini_call(prompt: str, api_key: str, model: str,
                             temperature: float, base_url: Optional[str] = None,
                             retries: int = 3, timeout: int = 420) -> str:
    full_prompt = system_instruction_for_prompt(prompt) + "\n\n" + prompt
    body = {
        "contents": [
            {"role": "user", "parts": [{"text": full_prompt}]}
        ],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    url = _tokenrouter_gemini_url(base_url, model or TOKENROUTER_GEMINI_DEFAULT_MODEL)
    last_err = None
    for attempt in range(retries):
        raise_if_cancelled()
        try:
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
            )
            holder = _urlopen_deadline(
                req, wall=max(60, int(timeout or 420)),
                idle=max(60, int(timeout or 420)), stream=False)
            out = json.loads((holder.get("data") or b"").decode("utf-8"))
            candidates = out.get("candidates") or []
            if not candidates:
                raise RuntimeError("response has no candidates")
            parts = (((candidates[0].get("content") or {}).get("parts")) or [])
            text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
            if text:
                return text
            raise RuntimeError("response content is empty")
        except InterruptedError:
            raise
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")[:500]
            last_err = _http_error_summary("TokenRouter Gemini", e.code, msg)
            if e.code in (429, 500, 502, 503, 504):
                wait_or_cancel(2 * (attempt + 1), sleeper=time.sleep)
                continue
            break
        except Exception as e:
            last_err = str(e)
            wait_or_cancel(1.5 * (attempt + 1), sleeper=time.sleep)
    raise RuntimeError(f"TokenRouter Gemini loi: {last_err}")


def _api_call(prompt: str, api_key: str, model: str, temperature: float,
              provider: str = "gemini", api_base_url: Optional[str] = None,
              api_timeout: int = 420,
              api_retries: Optional[int] = None, allow_model_fallback: bool = True) -> str:
    provider_key = str(provider or "gemini").lower()
    if provider_key == "xkiro":
        return _openai_compatible_call(
            prompt, api_key, model or XKIRO_DEFAULT_MODEL, temperature,
            api_base_url or XKIRO_DEFAULT_BASE_URL,
            timeout=api_timeout, retries=api_retries if api_retries is not None else 2,
            provider_label="Xkiro", enforce_wall=True)
    if provider_key == "tokenrouter":
        return _openai_compatible_call(
            prompt, api_key, model or TOKENROUTER_DEFAULT_MODEL,
            temperature, api_base_url or TOKENROUTER_DEFAULT_BASE_URL,
            timeout=api_timeout)
    if provider_key == "inferx":
        return _openai_compatible_call(
            prompt, api_key, model or INFERX_DEFAULT_MODEL,
            temperature, api_base_url or INFERX_DEFAULT_BASE_URL,
            timeout=api_timeout, stream=False,
            provider_label="InferX")
    if provider_key == "nvidia":
        # Tier free chặn burst từng đợt 1-2 phút và KHÔNG trả header
        # Retry-After (đã soi thật) -> chỉ còn cách kiên nhẫn: 5 lần thử,
        # chờ 15/30/45/60s giữa các lần (tổng ~2.5 phút đủ vượt một đợt chặn).
        primary = model or NVIDIA_DEFAULT_MODEL
        retries = 5 if api_retries is None else max(1, int(api_retries))
        configured = max(30, int(api_timeout or 420))
        wall, idle = _nvidia_wall_idle(primary, configured)
        try:
            return _openai_compatible_call(
                prompt, api_key, primary,
                temperature, api_base_url or NVIDIA_DEFAULT_BASE_URL,
                retries=retries, timeout=wall, stream=False,
                provider_label="NVIDIA", rate_limit_wait=15.0,
                idle_timeout=idle, enforce_wall=True)
        except InterruptedError:
            raise
        except RuntimeError as exc:
            backup = NVIDIA_FAST_MODEL
            if (allow_model_fallback and _is_hang_or_stall(exc) and backup
                    and backup.lower() != str(primary).lower()):
                log("NVIDIA '%s' treo/het han; chuyen sang '%s'."
                    % (primary, backup), "warn")
                fwall, fidle = _nvidia_wall_idle(backup, configured)
                return _openai_compatible_call(
                    prompt, api_key, backup,
                    temperature, api_base_url or NVIDIA_DEFAULT_BASE_URL,
                    retries=2, timeout=fwall, stream=False,
                    provider_label="NVIDIA", rate_limit_wait=15.0,
                    idle_timeout=fidle, enforce_wall=True)
            raise
    if provider_key == "zenmux":
        return _openai_compatible_call(
            prompt, api_key, model or ZENMUX_DEFAULT_MODEL,
            temperature, api_base_url or ZENMUX_DEFAULT_BASE_URL,
            retries=4, timeout=api_timeout, stream=False,
            provider_label="ZenMux", rate_limit_wait=5.0)
    if provider_key == "zai":
        return _openai_compatible_call(
            prompt, api_key, model or ZAI_DEFAULT_MODEL, temperature,
            api_base_url or ZAI_DEFAULT_BASE_URL,
            timeout=api_timeout, retries=api_retries if api_retries is not None else 3,
            stream=False, provider_label="Z.AI", rate_limit_wait=5.0,
            enforce_wall=True)
    if provider_key == "tokenharbor":
        return _tokenharbor_call(
            prompt, api_key, model or TOKENHARBOR_DEFAULT_MODEL,
            temperature, api_base_url or TOKENHARBOR_DEFAULT_BASE_URL,
            api_timeout=api_timeout, api_retries=api_retries)
    if provider_key == "tokenrouter_gemini":
        return _tokenrouter_gemini_call(
            prompt, api_key, model or TOKENROUTER_GEMINI_DEFAULT_MODEL,
            temperature, api_base_url or TOKENROUTER_GEMINI_DEFAULT_BASE_URL,
            timeout=api_timeout)
    return _gemini_call(prompt, api_key, model, temperature)
