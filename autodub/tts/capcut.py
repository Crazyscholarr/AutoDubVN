"""Engine CapCut TTS."""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from ..asr import is_speakable
from ..srt_utils import Segment
from ..utils import log
from .common import (
    CAPCUT_DEFAULT_VOICE,
    _clean_partial,
    _project_root,
    _raise_if_cancelled,
)

_CAPCUT_CLIENT = None
_CAPCUT_CLIENT_KEY: Optional[str] = None
_CAPCUT_ERROR: Optional[Exception] = None
_CAPCUT_STATUS_LOCK = threading.Lock()
_CAPCUT_LOCK = threading.Lock()

def _capcut_sdk_path() -> str:
    return os.path.join(_project_root(), "tools", "capcut-tts-api")


def _capcut_catalog_path() -> Optional[str]:
    p = os.path.join(_capcut_sdk_path(), "Voice.json")
    return p if os.path.exists(p) else None


def _capcut_status_path() -> str:
    return os.path.join(_project_root(), "output", "_nghe_thu",
                        "capcut_voice_status.json")


def _load_capcut_voice_status() -> dict:
    try:
        with open(_capcut_status_path(), "r", encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _record_capcut_voice_status(voice: str, ok: bool, error: str = "") -> None:
    voice = str(voice or CAPCUT_DEFAULT_VOICE)
    with _CAPCUT_STATUS_LOCK:
        data = _load_capcut_voice_status()
        data[voice] = {"status": "ok" if ok else "failed",
                       "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "error": str(error or "")[:240]}
        path = _capcut_status_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp, path)


def _load_capcut_client(device_json: Optional[str] = None):
    """Load K07VN/capcut-tts-api as an optional local SDK."""
    global _CAPCUT_CLIENT, _CAPCUT_CLIENT_KEY, _CAPCUT_ERROR
    key = os.path.abspath(device_json) if device_json else ""
    if _CAPCUT_CLIENT is not None and _CAPCUT_CLIENT_KEY == key:
        return _CAPCUT_CLIENT

    with _CAPCUT_LOCK:
        if _CAPCUT_CLIENT is not None and _CAPCUT_CLIENT_KEY == key:
            return _CAPCUT_CLIENT

        sdk = _capcut_sdk_path()
        if os.path.isdir(sdk) and sdk not in sys.path:
            sys.path.insert(0, sdk)

        try:
            from capcut_tts_api import CapCutClient
        except Exception as e:
            _CAPCUT_ERROR = RuntimeError(
                "Chua cai capcut-tts-api. Chay: git clone "
                "https://github.com/K07VN/capcut-tts-api tools/capcut-tts-api")
            raise _CAPCUT_ERROR from e

        try:
            if device_json and os.path.exists(device_json):
                _CAPCUT_CLIENT = CapCutClient(device=device_json)
            else:
                _CAPCUT_CLIENT = CapCutClient()
            _CAPCUT_CLIENT_KEY = key
            _CAPCUT_ERROR = None
            return _CAPCUT_CLIENT
        except Exception as e:
            _CAPCUT_ERROR = e
            raise


def _capcut_rate(base_rate: str) -> str:
    raw = str(base_rate or "1.0").strip()
    if raw.endswith("%"):
        try:
            pct = float(raw[:-1].replace("+", "") or "0")
            return f"{max(0.5, min(2.0, 1.0 + pct / 100.0)):.2f}"
        except ValueError:
            return "1.0"
    try:
        return f"{max(0.5, min(2.0, float(raw))):.2f}"
    except ValueError:
        return "1.0"


def _capcut_speech_urls(query_res: dict) -> List[str]:
    urls: List[str] = []
    for task in ((query_res.get("data") or {}).get("tasks") or []):
        payload = task.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        for item in payload.get("audio_subtitles") or []:
            if item.get("invalid_input") or item.get("code") not in (None, 0):
                continue
            url = item.get("speech_url")
            if url:
                urls.append(str(url))
    return urls


def _capcut_synth_via_http_api(text: str, voice: Optional[str], out_path: str,
                               api_url: str, speed: int = 10,
                               timeout: float = 90.0) -> bool:
    """Use kuwacom/CapCut-TTS compatible HTTP server when configured."""
    try:
        import requests
        base = str(api_url or "").rstrip("/")
        if not base:
            return False
        resp = requests.post(
            base + "/v2/synthesize",
            json={"text": text, "speaker": voice or "", "speed": speed,
                  "method": "buffer"},
            timeout=timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:240]}")
        if len(resp.content) < 512:
            raise RuntimeError("Audio CapCut HTTP qua nho hoac rong.")
        with open(out_path, "wb") as f:
            f.write(resp.content)
        return True
    except Exception as e:
        raise RuntimeError(f"CapCut HTTP API loi: {e}") from e


def _synth_one_capcut(text: str, voice: Optional[str], rate: str, out_path: str,
                      device_json: Optional[str] = None, max_retries: int = 2,
                      poll_interval: float = 1.0, timeout: float = 90.0,
                      label: str = "1 dong", api_url: Optional[str] = None,
                      speed: int = 10, reuse_existing: bool = True,
                      cancel_event=None) -> bool:
    last_err: Optional[Exception] = None
    done = {"succeed", "success", "completed", "complete"}
    bad = {"failed", "fail", "error", "canceled", "cancelled"}
    voice = voice or CAPCUT_DEFAULT_VOICE
    _raise_if_cancelled(cancel_event)
    if reuse_existing and os.path.exists(out_path) and os.path.getsize(out_path) >= 512:
        _record_capcut_voice_status(voice, True)
        return True

    for attempt in range(1, max_retries + 1):
        try:
            _raise_if_cancelled(cancel_event)
            if api_url:
                ok = _capcut_synth_via_http_api(text, voice, out_path, api_url,
                                                speed=speed, timeout=timeout)
                _raise_if_cancelled(cancel_event)
                if ok:
                    _record_capcut_voice_status(voice, True)
                return ok

            client = _load_capcut_client(device_json)
            created = client.create_tts_task(texts=text, voice=voice, rate=rate)
            tasks = (created.get("data") or {}).get("tasks") or []
            if not tasks:
                raise RuntimeError(f"CapCut khong tra task: {str(created)[:240]}")
            task = tasks[0]
            task_id, token = task["id"], task["token"]

            start = time.time()
            while time.time() - start < timeout:
                _raise_if_cancelled(cancel_event)
                query = client.query_tts_task(task_id, token,
                                              bind_id=task.get("bind_id", ""))
                qtasks = (query.get("data") or {}).get("tasks") or []
                status = str((qtasks[0] if qtasks else {}).get("status") or "").lower()
                if status in done:
                    urls = _capcut_speech_urls(query)
                    if not urls:
                        raise RuntimeError(f"CapCut xong nhung khong co speech_url: {str(query)[:300]}")
                    resp = client.session.get(urls[0], timeout=60)
                    if resp.status_code >= 400:
                        raise RuntimeError(f"Tai audio CapCut HTTP {resp.status_code}")
                    if len(resp.content) < 512:
                        raise RuntimeError("Audio CapCut qua nho hoac rong.")
                    with open(out_path, "wb") as f:
                        f.write(resp.content)
                    _record_capcut_voice_status(voice, True)
                    return True
                if status in bad:
                    raise RuntimeError(f"CapCut task loi: {str(query)[:300]}")
                # Chia nhỏ thời gian chờ để nút Dừng phản hồi nhanh.
                wait_until = time.time() + poll_interval
                while time.time() < wait_until:
                    _raise_if_cancelled(cancel_event)
                    time.sleep(min(0.1, max(0.0, wait_until - time.time())))
            raise RuntimeError(f"CapCut timeout sau {timeout:.0f}s")
        except InterruptedError:
            _clean_partial(out_path)
            raise
        except Exception as e:
            last_err = e
            _clean_partial(out_path)
            if attempt < max_retries:
                wait_until = time.time() + 1.0 + random.uniform(0, 0.5)
                while time.time() < wait_until:
                    _raise_if_cancelled(cancel_event)
                    time.sleep(min(0.1, max(0.0, wait_until - time.time())))

    preview = text if len(text) <= 50 else text[:50] + "..."
    _record_capcut_voice_status(voice, False, str(last_err or "không rõ lỗi"))
    log(f"TTS (CapCut) loi {label} sau {max_retries} lan thu: {last_err} - \"{preview}\"", "warn")
    return False


def _synth_all_capcut(segments: List[Segment], workdir: str, base_rate: str,
                      concurrency: int, max_retries: int = 2,
                      capcut_options: Optional[dict] = None,
                      cancel_event=None) -> List[Optional[str]]:
    opts = dict(capcut_options or {})
    rate = str(opts.get("rate") or _capcut_rate(base_rate))
    api_url = opts.get("kuwacom_api_url") or opts.get("api_url")
    device_json = opts.get("device_json")
    timeout = float(opts.get("timeout", 90.0))
    poll_interval = float(opts.get("poll_interval", 1.0))
    speed = int(opts.get("speed", round(float(rate) * 10)))
    max_concurrency = max(1, int(opts.get("max_concurrency", 12) or 12))
    cap_conc = max(1, min(int(opts.get("concurrency", 4) or 4),
                          int(concurrency or 4), max_concurrency))
    reuse_existing = bool(opts.get("reuse_existing", True))
    paths: List[Optional[str]] = [None] * len(segments)
    todo = [(i, s) for i, s in enumerate(segments) if is_speakable(s.text)]
    log(f"Tong hop {len(todo)} dong bang CapCut TTS ({cap_conc} luong, rate={rate})...", "step")

    def one(i: int, s: Segment, override_voice: Optional[str] = None,
            label_suffix: str = "") -> tuple[int, Optional[str]]:
        _raise_if_cancelled(cancel_event)
        voice = override_voice or s.voice or CAPCUT_DEFAULT_VOICE
        cache_src = "\n".join([s.text or "", voice, str(rate), str(speed),
                               str(api_url or ""), str(device_json or "")])
        cache_key = hashlib.md5(cache_src.encode("utf-8")).hexdigest()[:12]
        out = os.path.join(workdir, f"capcut_{i:05d}_{cache_key}.mp3")
        ok = _synth_one_capcut(
            s.text, voice, rate, out,
            device_json=device_json, max_retries=max_retries,
            poll_interval=poll_interval, timeout=timeout,
            label=f"dong {s.index}{label_suffix}", api_url=api_url, speed=speed,
            reuse_existing=reuse_existing, cancel_event=cancel_event)
        return i, out if ok else None

    done_count = 0
    with ThreadPoolExecutor(max_workers=cap_conc) as ex:
        futs = [ex.submit(one, i, s) for i, s in todo]
        for fut in as_completed(futs):
            i, path = fut.result()
            paths[i] = path
            done_count += 1
            if done_count % 20 == 0 or done_count == len(todo):
                log(f"  ...da xong {done_count}/{len(todo)} dong", "info")

    # Catalog CapCut ngoài thực tế có thể chứa voice id đã cũ hoặc voice Edge
    # không được endpoint hiện tại chấp nhận. Không để một vai phụ làm hỏng cả
    # truyện: chỉ những dòng lỗi được đọc lại bằng giọng kể an toàn.
    _raise_if_cancelled(cancel_event)
    failed = [(i, s) for i, s in todo if not paths[i]]
    if failed:
        fallback_voice = str(opts.get("fallback_voice") or CAPCUT_DEFAULT_VOICE)
        fallback_retries = max(3, int(opts.get("fallback_max_retries", max_retries) or max_retries))
        fallback_concurrency = max(1, min(
            len(failed), cap_conc,
            int(opts.get("fallback_concurrency", 2) or 2)))
        log("CapCut loi %d dong; thu lai bang giong ke du phong %s (%d luong)..."
            % (len(failed), fallback_voice, fallback_concurrency), "warn")

        def fallback_one(i: int, s: Segment) -> tuple[int, Optional[str]]:
            _raise_if_cancelled(cancel_event)
            voice = fallback_voice
            if voice == (s.voice or CAPCUT_DEFAULT_VOICE):
                voice = CAPCUT_DEFAULT_VOICE
            cache_src = "\n".join([s.text or "", voice, str(rate), str(speed),
                                   str(api_url or ""), str(device_json or "")])
            cache_key = hashlib.md5(cache_src.encode("utf-8")).hexdigest()[:12]
            out = os.path.join(workdir, f"capcut_{i:05d}_{cache_key}.mp3")
            ok = _synth_one_capcut(
                s.text, voice, rate, out,
                device_json=device_json, max_retries=fallback_retries,
                poll_interval=poll_interval, timeout=timeout,
                label=f"dong {s.index} (giong du phong)", api_url=api_url,
                speed=speed, reuse_existing=reuse_existing,
                cancel_event=cancel_event)
            return i, out if ok else None

        with ThreadPoolExecutor(max_workers=fallback_concurrency) as ex:
            futs = [ex.submit(fallback_one, i, s) for i, s in failed]
            for fut in as_completed(futs):
                i, path = fut.result()
                paths[i] = path
        recovered = sum(1 for i, _s in failed if paths[i])
        level = "ok" if recovered == len(failed) else "warn"
        log("Giong ke du phong da cuu %d/%d dong CapCut bi loi."
            % (recovered, len(failed)), level)
    return paths
