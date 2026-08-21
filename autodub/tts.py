"""Chuyển phụ đề tiếng Việt thành giọng nói + gán NHIỀU GIỌNG cho nhân vật +
áp thuật toán CHỐNG ĐÈ THOẠI.

Hỗ trợ 2 engine TTS (tts.engine trong config.yaml):
  - "edge"   (mặc định) - edge-tts, miễn phí, 2 giọng Việt gốc (nữ HoaiMy, nam
             NamMinh). Để có "nhiều giọng nhân vật", tạo biến thể bằng cách
             đổi nhẹ cao độ (pitch) -> ra nhiều chất giọng nam/nữ, già/trẻ.
  - "vieneu" - VieNeu-TTS (github.com/pnnbao97/VieNeu-TTS), mã nguồn mở, chạy
             LOCAL (CPU/GPU), giọng tự nhiên hơn hẳn, có nhiều giọng dựng sẵn
             thật (không cần giả lập bằng pitch) + hỗ trợ nhân bản giọng.
             Cần cài: pip install vieneu
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

from .asr import is_speakable
from .srt_utils import Segment, split_vi_text_naturally
from .timeline import auto_fit, fit_segments_strict, Placement
from .utils import log, ffprobe_duration
from .video import (change_speed, trim_silence, concat_audio_clips,
                    compact_long_silences)

try:
    from edge_tts.exceptions import NoAudioReceived, UnexpectedResponse, WebSocketError
    _RETRYABLE_ERRORS = (NoAudioReceived, UnexpectedResponse, WebSocketError, OSError)
except Exception:  # edge-tts chưa cài hoặc đổi cấu trúc nội bộ -> vẫn retry mọi lỗi
    _RETRYABLE_ERRORS = (Exception,)


def _channel_cta_speed(value) -> float:
    try:
        return max(1.0, min(2.0, float(value or 1.0)))
    except (TypeError, ValueError):
        return 1.0


def _is_channel_cta_text(text: str, configured_text: str = "") -> bool:
    """Nhận diện riêng câu nhắc kênh để chỉ tăng tốc đúng đoạn này."""
    value = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
    if not value:
        return False
    if ("bạn đang nghe chuyện tại" in value or
            "bạn đang nghe truyện tại" in value):
        return True
    configured = re.sub(r"\s+", " ", str(configured_text or "")).strip().casefold()
    if not configured:
        return False
    # Bỏ biến tên kênh rồi dùng cụm dài nhất hai bên làm dấu nhận diện. Cách
    # này vẫn nhận ra một CTA tùy biến khi bộ tách câu chia nó thành vài clip.
    parts = [p.strip(" .,!?:;-—") for p in configured.split("{channel}")]
    needles = [p for p in parts if len(p) >= 10]
    return any(needle in value or value in needle for needle in needles)


# Bộ giọng nhân vật (tạo từ 2 giọng gốc + dịch cao độ nhẹ)
VOICE_PRESETS = [
    {"voice": "vi-VN-NamMinhNeural", "pitch": "+0Hz"},   # 0: nam chuẩn
    {"voice": "vi-VN-HoaiMyNeural",  "pitch": "+0Hz"},   # 1: nữ chuẩn
    {"voice": "vi-VN-NamMinhNeural", "pitch": "+8Hz"},   # 2: nam trẻ
    {"voice": "vi-VN-HoaiMyNeural",  "pitch": "-8Hz"},   # 3: nữ trầm
    {"voice": "vi-VN-NamMinhNeural", "pitch": "-8Hz"},   # 4: nam trầm/lớn tuổi
    {"voice": "vi-VN-HoaiMyNeural",  "pitch": "+8Hz"},   # 5: nữ trẻ
]
DEFAULT_NARRATOR = {"voice": "vi-VN-NamMinhNeural", "pitch": "+0Hz"}
_PITCH_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*Hz\s*$", re.I)

CAPCUT_DEFAULT_VOICE = "BV421_vivn_streaming"
_CAPCUT_CLIENT = None
_CAPCUT_CLIENT_KEY: Optional[str] = None
_CAPCUT_ERROR: Optional[Exception] = None
_CAPCUT_STATUS_LOCK = threading.Lock()
_CAPCUT_LOCK = threading.Lock()


def _raise_if_cancelled(cancel_event=None) -> None:
    """Dừng ở ranh giới an toàn giữa hai lượt TTS.

    File đã tạo xong vẫn được giữ trong cache để lần chạy sau tiếp tục; chỉ
    phần đang dở mới bị bỏ.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Đã dừng tạo giọng theo yêu cầu.")


# --------------------------------------------------------------------------- #
#  Engine "vieneu" (VieNeu-TTS - mã nguồn mở, chạy local, giọng tự nhiên hơn)
# --------------------------------------------------------------------------- #
_VIENEU_MODEL = None
_VIENEU_ERROR: Optional[Exception] = None
_VIENEU_LOCK = threading.Lock()
_VIENEU_KWARGS: dict = {}

# Các kho model VieNeu-TTS tải về (đều CÔNG KHAI, KHÔNG cần token HuggingFace).
_VIENEU_REPOS = ("pnnbao-ump/VieNeu-TTS-v3-Turbo",
                 "OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano")


def _hf_cache_root() -> str:
    return (os.environ.get("HF_HUB_CACHE")
            or os.path.join(os.environ.get("HF_HOME")
                            or os.path.join(os.path.expanduser("~"), ".cache",
                                            "huggingface"), "hub"))


def _hf_is_cached(repo_id: str) -> bool:
    """Kho model này đã tải đủ về máy chưa?"""
    d = os.path.join(_hf_cache_root(), "models--" + repo_id.replace("/", "--"),
                     "snapshots")
    if not os.path.isdir(d):
        return False
    for snap in os.listdir(d):
        p = os.path.join(d, snap)
        if os.path.isdir(p) and os.listdir(p):
            return True
    return False


def _set_hf_offline(on: bool) -> None:
    """Bật/tắt chế độ CHẠY OFFLINE của huggingface_hub.

    VieNeu-TTS chạy hoàn toàn trên máy bạn - phần "gọi mạng" duy nhất là
    huggingface_hub kiểm tra xem bản trong cache có mới nhất không, LẦN NÀO
    KHỞI ĐỘNG CŨNG HỎI (đó là mấy dòng HTTP HEAD trong log). Model đã có sẵn
    thì tắt hẳn việc hỏi đó đi: nhanh hơn và chạy được cả khi mất mạng.
    """
    val = "1" if on else "0"
    os.environ["HF_HUB_OFFLINE"] = val
    os.environ["TRANSFORMERS_OFFLINE"] = val
    try:                    # đã import rồi thì sửa cả biến trong module
        import huggingface_hub.constants as _c
        _c.HF_HUB_OFFLINE = bool(on)
    except Exception:
        pass


def unpatch_modelscope_hub() -> bool:
    """Gỡ bản vá mà `modelscope` áp lên `huggingface_hub`.

    VÌ SAO BẮT BUỘC: khi FunASR/Paraformer chạy trước, nó nạp `modelscope`, và
    modelscope gọi `patch_hub()` - THAY THẾ toàn bộ hàm của `huggingface_hub`
    trong tiến trình để mọi lượt tải đều đi qua modelscope.cn. Đến lượt
    VieNeu-TTS xin file từ HuggingFace thì bị bẻ lái sang modelscope.cn, nơi
    KHÔNG có kho `OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano` -> lỗi 404
    "record not found", dù chính kho đó trên HuggingFace vẫn trả 200 OK.
    """
    try:
        from modelscope.utils.hf_util import unpatch_hub
    except Exception:
        return False          # không có modelscope thì chẳng có gì để gỡ
    try:
        unpatch_hub()
        log("Đã trả huggingface_hub về nguyên trạng (modelscope đã vá đè).", "ok")
        return True
    except Exception as e:
        log(f"Không gỡ được bản vá modelscope: {e}", "warn")
        return False


def _load_vieneu_model(**kwargs):
    """Nạp model VieNeu-TTS 1 lần rồi dùng lại (nạp lại rất tốn thời gian).

    Nạp HỎNG cũng phải nhớ: trước đây lỗi không được ghi lại nên mỗi dòng thoại
    lại nạp lại từ đầu (~15 giây/lần). 170 dòng = hơn 40 phút nạp đi nạp lại rồi
    cùng thất bại. Giờ hỏng một lần là báo ngay cho mọi lần sau.
    """
    global _VIENEU_MODEL, _VIENEU_ERROR
    if _VIENEU_MODEL is not None:
        return _VIENEU_MODEL
    if _VIENEU_ERROR is not None:
        raise _VIENEU_ERROR          # đã hỏng rồi - hỏng ngay, đừng thử lại

    with _VIENEU_LOCK:
        if _VIENEU_MODEL is not None:
            return _VIENEU_MODEL
        if _VIENEU_ERROR is not None:
            raise _VIENEU_ERROR

        try:
            from vieneu import Vieneu
        except ImportError:
            _VIENEU_ERROR = RuntimeError(
                "Chưa cài VieNeu-TTS. Chạy trong venv: python -m pip install vieneu")
            raise _VIENEU_ERROR

    # Phải gỡ bản vá TRƯỚC khi khởi tạo, nếu không mọi lượt tải đều lạc sang
    # modelscope.cn (xem giải thích ở unpatch_modelscope_hub).
    unpatch_modelscope_hub()

    opts = dict(_VIENEU_KWARGS)
    opts.update(kwargs)
    offline = opts.pop("offline", "auto")

    cached = all(_hf_is_cached(r) for r in _VIENEU_REPOS)
    go_offline = (offline is True) or (offline == "auto" and cached)

    if go_offline:
        _set_hf_offline(True)
        log("Nạp VieNeu-TTS từ model đã tải trong máy (không gọi mạng).", "info")
    elif cached:
        log("Nạp mô hình VieNeu-TTS...", "info")
    else:
        log("Nạp mô hình VieNeu-TTS - LẦN ĐẦU phải tải model từ HuggingFace "
            "(kho công khai, KHÔNG cần token; vài GB, chỉ tải một lần). "
            "Mẹo: bấm 'Tải model về máy' trong giao diện để tải trước cho khỏi "
            "phải chờ giữa chừng.", "info")

    try:
        _VIENEU_MODEL = Vieneu(**opts)
    except Exception as e:
        if go_offline:
            # Cache thiếu file -> tắt offline, tải nốt rồi thôi.
            log(f"Bản trong máy chưa đủ ({type(e).__name__}) - tải bổ sung...", "warn")
            _set_hf_offline(False)
            try:
                _VIENEU_MODEL = Vieneu(**opts)
                return _VIENEU_MODEL
            except Exception as e2:
                e = e2
        _VIENEU_ERROR = RuntimeError(
            f"Không nạp được VieNeu-TTS: {e}\n"
            "  → Thử: bấm 'Tải model về máy' trong giao diện, hoặc chạy\n"
            "    python tools\\tai_model.py\n"
            "  → Hoặc đổi tts.engine sang 'edge' trong config.yaml để dùng "
            "edge-tts (nhanh, không cần tải model).")
        raise _VIENEU_ERROR


def reset_vieneu_error() -> None:
    """Cho phép thử nạp lại sau khi người dùng đã tải model xong."""
    global _VIENEU_ERROR
    _VIENEU_ERROR = None


def prefetch_models(progress=None) -> dict:
    """TẢI TRƯỚC toàn bộ model VieNeu-TTS về máy.

    Tải giữa chừng lúc đang lồng tiếng thì vừa chậm vừa dễ hỏng cả mẻ. Hàm này
    để gọi riêng (nút 'Tải model về máy' hoặc `python tools/tai_model.py`) rồi
    yên tâm chạy offline về sau.
    """
    def say(m, k="info"):
        log(m, k)
        if progress:
            try:
                progress(m)
            except Exception:
                pass

    unpatch_modelscope_hub()
    _set_hf_offline(False)          # đang tải thì phải cho gọi mạng
    result = {"ok": [], "loi": []}

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise RuntimeError("Thiếu huggingface_hub: python -m pip install huggingface_hub")

    for repo in _VIENEU_REPOS:
        if _hf_is_cached(repo):
            say(f"Đã có sẵn: {repo}", "ok")
            result["ok"].append(repo)
            continue
        say(f"Đang tải {repo} … (vài GB, chỉ một lần)", "step")
        try:
            snapshot_download(repo_id=repo)
            say(f"Xong: {repo}", "ok")
            result["ok"].append(repo)
        except Exception as e:
            say(f"Tải {repo} lỗi: {e}", "err")
            result["loi"].append({"repo": repo, "loi": str(e)[:300]})

    if not result["loi"]:
        reset_vieneu_error()
        say("Đã tải đủ model. Lần chạy sau sẽ không cần mạng nữa.", "ok")
    return result


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def list_voices(engine: str = "edge") -> List[dict]:
    """Danh sách giọng CÓ THẬT của engine, để giao diện hiện đúng.

    Trả [{"id": ..., "name": ...}]. Không bao giờ ném lỗi - hỏng thì trả danh
    sách rỗng kèm ghi log, giao diện tự hiểu.
    """
    eng = (engine or "edge").lower()
    if eng == "capcut":
        # Voice.json là catalog tĩnh; không cần khởi tạo thiết bị/API chỉ để
        # hiện dropdown. Nhờ vậy mọi giọng vẫn hiện và nghe thử được kể cả khi
        # phiên CapCut chưa tạo client thành công ở lần nạp giao diện đầu tiên.
        try:
            catalog = _capcut_catalog_path()
            if catalog:
                with open(catalog, "r", encoding="utf-8") as f:
                    rows = json.load(f)
                statuses = _load_capcut_voice_status()
                seen, out = set(), []
                for row in rows if isinstance(rows, list) else []:
                    lang = str(row.get("lang") or row.get("lan") or "").lower()
                    voice_id = str(row.get("voice_type") or "").strip()
                    if lang not in {"vi", "vi-vn"} or not voice_id or voice_id in seen:
                        continue
                    # Đây là id của Microsoft Edge TTS, không phải speaker hợp lệ
                    # của endpoint CapCut. Voice.json của vài bản SDK trộn cả hai
                    # loại; đưa chúng vào dàn nhân vật sẽ trả err_code 40402004.
                    folded_id = voice_id.casefold()
                    if folded_id.startswith("vi-") and folded_id.endswith("neural"):
                        continue
                    seen.add(voice_id)
                    label = str(row.get("display_name") or voice_id).strip()
                    state = statuses.get(voice_id) if isinstance(statuses.get(voice_id), dict) else {}
                    out.append({"id": voice_id, "name": f"{label} ({voice_id})",
                                "ref": str(row.get("resource_id") or ""),
                                "status": state.get("status", "unknown"),
                                "status_error": state.get("error", "")})
                if out:
                    return out
            client = _load_capcut_client()
            voices = client.list_voices(lang="vi-VN", catalog_path=catalog)
            return [{"id": v.voice_type, "name": f"{v.display_name} ({v.voice_type})",
                     "ref": v.resource_id} for v in voices]
        except Exception as e:
            log(f"Chua lay duoc danh sach giong CapCut: {str(e)[:160]}", "warn")
            return []

    if eng == "vieneu":
        out = []
        try:
            model = _load_vieneu_model()
            for item in model.list_preset_voices():
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    label, vid = item[0], item[1]
                else:
                    label = vid = str(item)
                out.append({"id": str(vid), "name": str(label),
                            "ref": str(vid)})
        except Exception as e:
            log(f"Chưa lấy được danh sách giọng VieNeu: {str(e)[:160]}", "warn")
        return out

    ten = {"vi-VN-NamMinhNeural": "Nam Minh (nam)",
           "vi-VN-HoaiMyNeural": "Hoài My (nữ)"}
    seen, out = set(), []
    for i, p in enumerate(VOICE_PRESETS):
        key = f"{p['voice']}|{p['pitch']}"
        if key in seen:
            continue
        seen.add(key)
        base = ten.get(p["voice"], p["voice"])
        pitch = p["pitch"]
        extra = ("" if pitch in ("+0Hz", "0Hz")
                 else " · trẻ hơn" if pitch.startswith("+") else " · trầm hơn")
        out.append({"id": key, "name": base + extra})
    return out


def _vieneu_preset_voices() -> List[str]:
    """Danh sách tên giọng dựng sẵn của VieNeu-TTS (rỗng nếu lấy lỗi)."""
    try:
        model = _load_vieneu_model()
        return [_vid for _label, _vid in model.list_preset_voices()]
    except Exception as e:
        log(f"Không lấy được danh sách giọng VieNeu-TTS: {e}", "warn")
        return []


def _pitch_value(pitch: Optional[str]) -> Optional[float]:
    m = _PITCH_RE.match(str(pitch or ""))
    if not m:
        return None
    return float(m.group(1))


def _format_pitch_hz(value: float) -> str:
    value = max(-500.0, min(500.0, float(value)))
    if abs(value - round(value)) < 0.001:
        n = int(round(value))
        return f"{n:+d}Hz"
    return f"{value:+.1f}Hz"


def _combine_pitch_hz(*pitches: Optional[str]) -> str:
    total = 0.0
    found = False
    for p in pitches:
        v = _pitch_value(p)
        if v is None:
            continue
        total += v
        found = True
    return _format_pitch_hz(total) if found else DEFAULT_NARRATOR["pitch"]


def _edge_voice_and_pitch(voice_tag: Optional[str],
                          extra_pitch: Optional[str] = None) -> Tuple[str, str]:
    """Chuẩn hoá giọng edge về đúng dạng (voice, pitch).

    GUI lưu các biến thể edge dưới dạng "voice|presetPitch" (vd
    "vi-VN-HoaiMyNeural|+8Hz"), đồng thời vẫn có slider pitch riêng. Nếu ghép
    thẳng hai thứ đó sẽ thành "voice|+8Hz|+60Hz" và vỡ ở lúc split. Hàm này
    gom mọi pitch hợp lệ lại thành một giá trị duy nhất.
    """
    fallback = DEFAULT_NARRATOR
    raw = str(voice_tag or "").strip()
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    voice = parts[0] if parts else fallback["voice"]
    embedded_pitches = parts[1:]
    pitch = _combine_pitch_hz(*embedded_pitches, extra_pitch)
    return voice, pitch


def normalize_edge_narrator(narrator: Optional[dict]) -> dict:
    narrator = narrator or DEFAULT_NARRATOR
    voice, pitch = _edge_voice_and_pitch(narrator.get("voice"),
                                         narrator.get("pitch"))
    return {"voice": voice, "pitch": pitch}


def assign_voices(segments: List[Segment], mode: str = "narrator",
                  narrator: Optional[dict] = None, engine: str = "edge",
                  vieneu_voice: Optional[str] = None,
                  vieneu_voices: Optional[List[str]] = None) -> None:
    """Gán preset giọng cho từng segment (ghi vào seg.voice).

    engine="edge"   -> seg.voice = 'voice|pitch' (SSML edge-tts).
    engine="vieneu" -> seg.voice = tên giọng dựng sẵn của VieNeu-TTS (chuỗi
                       rỗng "" = dùng giọng mặc định của model).
    """
    engine = (engine or "edge").strip().lower()
    if engine == "capcut":
        presets = [v["id"] for v in list_voices("capcut")] or [CAPCUT_DEFAULT_VOICE]
        default_v = ((narrator or {}).get("voice") or CAPCUT_DEFAULT_VOICE)
        if default_v not in presets:
            presets = [default_v] + presets
        speakers = [s.speaker for s in segments if s.speaker]
        if mode == "per-speaker" and speakers and presets:
            uniq = sorted(set(speakers))
            mapping = {spk: presets[i % len(presets)] for i, spk in enumerate(uniq)}
            for s in segments:
                s.voice = mapping.get(s.speaker, default_v) or CAPCUT_DEFAULT_VOICE
        elif mode == "alternate" and presets:
            for i, s in enumerate(segments):
                s.voice = presets[i % len(presets)] or CAPCUT_DEFAULT_VOICE
        else:
            for s in segments:
                s.voice = default_v or CAPCUT_DEFAULT_VOICE
        return

    if engine == "vieneu":
        presets = vieneu_voices or _vieneu_preset_voices()
        default_v = vieneu_voice or (presets[0] if presets else None)
        speakers = [s.speaker for s in segments if s.speaker]
        if mode == "per-speaker" and speakers and presets:
            uniq = sorted(set(speakers))
            mapping = {spk: presets[i % len(presets)] for i, spk in enumerate(uniq)}
            for s in segments:
                s.voice = mapping.get(s.speaker, default_v) or ""
        elif mode == "alternate" and presets:
            for i, s in enumerate(segments):
                s.voice = presets[i % len(presets)] or ""
        else:
            for s in segments:
                s.voice = default_v or ""
        return

    narrator = normalize_edge_narrator(narrator)

    def tag(p):
        n = normalize_edge_narrator(p)
        return f"{n['voice']}|{n['pitch']}"

    speakers = [s.speaker for s in segments if s.speaker]
    if mode == "per-speaker" and speakers:
        uniq = sorted(set(speakers))
        mapping = {spk: VOICE_PRESETS[i % len(VOICE_PRESETS)] for i, spk in enumerate(uniq)}
        for s in segments:
            s.voice = tag(mapping.get(s.speaker, narrator))
    elif mode == "alternate":
        # Luân phiên nam/nữ theo lượt (khi không có diarization)
        for i, s in enumerate(segments):
            s.voice = tag(VOICE_PRESETS[i % 2])
    else:  # narrator
        for s in segments:
            s.voice = tag(narrator)


def _clean_partial(path: str) -> None:
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


async def _synth_one(text: str, voice: str, pitch: str, rate: str, out_path: str,
                     max_retries: int = 4, base_delay: float = 1.2,
                     label: str = "1 dòng", cancel_event=None) -> bool:
    """Tổng hợp 1 dòng, tự thử lại khi gặp lỗi mạng/rate-limit tạm thời từ edge-tts.

    Lỗi "No audio was received" hầu hết là do server tạm thời không trả dữ liệu
    (thường xảy ra khi có nhiều kết nối song song) chứ không phải lỗi nội dung câu,
    nên thử lại với độ trễ tăng dần gần như luôn khắc phục được.
    """
    import edge_tts

    last_err: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            _raise_if_cancelled(cancel_event)
            comm = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
            await comm.save(out_path)
            _raise_if_cancelled(cancel_event)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return True
            raise RuntimeError("File âm thanh rỗng.")
        except InterruptedError:
            _clean_partial(out_path)
            raise
        except _RETRYABLE_ERRORS as e:
            last_err = e
            _clean_partial(out_path)
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.6)
                end = time.monotonic() + delay
                while time.monotonic() < end:
                    _raise_if_cancelled(cancel_event)
                    await asyncio.sleep(min(0.1, max(0.0, end - time.monotonic())))
        except Exception as e:  # lỗi không thuộc nhóm biết trước -> vẫn ghi nhận, không retry vô ích
            last_err = e
            _clean_partial(out_path)
            break

    preview = text if len(text) <= 50 else text[:50] + "…"
    log(f"TTS lỗi {label} sau {max_retries} lần thử ({voice}): {last_err} — \"{preview}\"", "warn")
    return False


async def _synth_all(segments: List[Segment], workdir: str, base_rate: str,
                     concurrency: int, max_retries: int = 4,
                     retry_base_delay: float = 1.2,
                     cancel_event=None) -> List[Optional[str]]:
    sem = asyncio.Semaphore(concurrency)
    paths: List[Optional[str]] = [None] * len(segments)

    async def attempt(i: int, seg: Segment, conc_sem: asyncio.Semaphore, retries: int) -> bool:
        _raise_if_cancelled(cancel_event)
        voice, pitch = _edge_voice_and_pitch(seg.voice)
        out = os.path.join(workdir, f"line_{i:05d}.mp3")
        async with conc_sem:
            ok = await _synth_one(seg.text, voice, pitch, base_rate, out,
                                  max_retries=retries, base_delay=retry_base_delay,
                                  label=f"dòng {seg.index}",
                                  cancel_event=cancel_event)
        if ok:
            paths[i] = out
        return ok

    todo = [(i, s) for i, s in enumerate(segments) if is_speakable(s.text)]
    await asyncio.gather(*(attempt(i, s, sem, max_retries) for i, s in todo))
    _raise_if_cancelled(cancel_event)

    # Vòng 2: các dòng vẫn lỗi sau vòng 1 -> thử lại RIÊNG LẺ (concurrency thấp) để
    # né rate-limit do quá nhiều kết nối song song, thường vớt lại được gần hết.
    still_failed = [(i, s) for i, s in todo if paths[i] is None]
    if still_failed:
        log(f"Thử lại {len(still_failed)} dòng TTS lỗi (giảm luồng để né giới hạn tốc độ)...", "step")
        low_sem = asyncio.Semaphore(min(2, concurrency))
        for i, s in still_failed:  # chạy tuần tự từng dòng, tránh dồn dập lại gây lỗi tiếp
            if await attempt(i, s, low_sem, retries=max_retries + 1):
                continue
            # Vẫn hỏng -> thử GIỌNG KHÁC. Đôi khi một giọng cụ thể bị server từ
            # chối với một câu cụ thể; đổi giọng thường đọc được ngay, thà lệch
            # giọng một dòng còn hơn câm tiếng.
            cur = (s.voice or "").split("|")[0]
            alt = ("vi-VN-HoaiMyNeural" if "NamMinh" in cur
                   else "vi-VN-NamMinhNeural")
            out = os.path.join(workdir, f"line_{i:05d}.mp3")
            async with low_sem:
                if await _synth_one(s.text, alt, "+0Hz", base_rate, out,
                                    max_retries=2, base_delay=retry_base_delay,
                                    label=f"dòng {s.index} (đổi giọng {alt})",
                                    cancel_event=cancel_event):
                    paths[i] = out
                    log(f"Dòng {s.index}: đọc được bằng giọng dự phòng {alt}.", "ok")

    return paths


def _synth_one_vieneu(text: str, voice: Optional[str], out_path: str,
                      max_retries: int = 2, label: str = "1 dòng",
                      cancel_event=None) -> bool:
    """Tổng hợp 1 dòng bằng VieNeu-TTS (model local, KHÔNG qua mạng nên hiếm
    khi lỗi tạm thời - vẫn thử lại vài lần phòng OOM/glitch)."""
    # Tên giọng từ GUI/config có thể là dạng đầy đủ ('Minh Đức — Nam · Bắc · ...')
    # nhưng VieNeu chỉ nhận tên ngắn ('Minh Đức'). Cắt phần mô tả.
    if voice and " \u2014 " in voice:
        voice = voice.split(" \u2014 ")[0].strip()
    last_err: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            _raise_if_cancelled(cancel_event)
            model = _load_vieneu_model()
            audio = model.infer(text, voice=(voice or None))
            _raise_if_cancelled(cancel_event)
            model.save(audio, out_path)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return True
            raise RuntimeError("File âm thanh rỗng.")
        except InterruptedError:
            _clean_partial(out_path)
            raise
        except Exception as e:
            last_err = e
            _clean_partial(out_path)

    preview = text if len(text) <= 50 else text[:50] + "…"
    log(f"TTS (VieNeu) lỗi {label} sau {max_retries} lần thử: {last_err} — \"{preview}\"", "warn")
    return False


def _synth_all_vieneu(segments: List[Segment], workdir: str,
                      max_retries: int = 2, cancel_event=None) -> List[Optional[str]]:
    """Tổng hợp TUẦN TỰ (không song song) vì model chạy trên 1 GPU/CPU cục bộ -
    chạy song song nhiều luồng dễ tranh chấp VRAM/không an toàn luồng."""
    paths: List[Optional[str]] = [None] * len(segments)
    todo = [(i, s) for i, s in enumerate(segments) if is_speakable(s.text)]
    log(f"Tổng hợp {len(todo)} dòng bằng VieNeu-TTS (model local, tuần tự)...", "step")
    for n, (i, s) in enumerate(todo, 1):
        _raise_if_cancelled(cancel_event)
        out = os.path.join(workdir, f"line_{i:05d}.wav")
        ok = _synth_one_vieneu(s.text, s.voice, out, max_retries=max_retries,
                               label=f"dòng {s.index}", cancel_event=cancel_event)
        if ok:
            paths[i] = out
        if n % 20 == 0 or n == len(todo):
            log(f"  ...đã xong {n}/{len(todo)} dòng", "info")
    return paths


def synthesize_text_audio(
    text: str,
    workdir: str,
    out_path: str,
    engine: str = "edge",
    narrator: Optional[dict] = None,
    base_rate: str = "+0%",
    concurrency: int = 8,
    max_retries: int = 3,
    retry_base_delay: float = 1.2,
    vieneu_options: Optional[dict] = None,
    capcut_options: Optional[dict] = None,
    max_chunk_chars: int = 700,
    utterances: Optional[List[dict]] = None,
    channel_cta_speed: float = 1.0,
    channel_cta_text: str = "",
    cancel_event=None,
) -> dict:
    """Tạo một file audio độc lập từ văn bản dài.

    Văn bản được tách tự nhiên thành các đoạn vừa với dịch vụ TTS rồi nối lại
    theo thứ tự. Nếu ``utterances`` được truyền vào, mỗi lượt đã có ``voice`` và
    ``speaker`` riêng (lời kể/nhân vật); khi tách nhỏ vẫn giữ nguyên giọng đó.
    Luồng này không dùng thuật toán ép timing của phụ đề, vì vậy tốc độ đọc được
    giữ đúng theo ``base_rate`` và không bị tăng tốc để lấp slot.
    """
    global _VIENEU_KWARGS
    _raise_if_cancelled(cancel_event)
    clean = re.sub(r"[ \t]+", " ", str(text or ""))
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    if not clean:
        raise ValueError("Văn bản đang trống.")
    if len(clean) > 200_000:
        raise ValueError("Văn bản quá dài (tối đa 200.000 ký tự mỗi lần).")

    max_chunk_chars = max(120, min(1500, int(max_chunk_chars or 700)))
    os.makedirs(workdir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    engine = (engine or "edge").strip().lower()
    if engine not in {"edge", "vieneu", "capcut"}:
        raise ValueError(f"Engine TTS không hỗ trợ: {engine}")

    narrator = narrator or DEFAULT_NARRATOR
    segment_meta: List[dict] = []
    chunks: List[str] = []
    if utterances:
        for utterance in utterances:
            value = re.sub(r"\s+", " ", str(utterance.get("text") or "")).strip()
            if not value:
                continue
            pieces = split_vi_text_naturally(
                value, max_chars=max_chunk_chars,
                min_chars=min(80, max(24, max_chunk_chars // 8))) or [value]
            for piece in pieces:
                chunks.append(piece)
                segment_meta.append({
                    "speaker": str(utterance.get("speaker") or "narrator"),
                    "speaker_name": str(utterance.get("speaker_name") or "Người kể"),
                    "kind": str(utterance.get("kind") or "narration"),
                    "confidence": utterance.get("confidence"),
                    "voice": str(utterance.get("voice") or narrator.get("voice") or ""),
                    "channel_cta": _is_channel_cta_text(piece, channel_cta_text),
                })
    else:
        chunks = split_vi_text_naturally(
            clean, max_chars=max_chunk_chars,
            min_chars=min(80, max(24, max_chunk_chars // 8)))
        segment_meta = [{"speaker": "narrator", "speaker_name": "Người kể",
                         "kind": "narration", "confidence": 1.0,
                         "voice": str(narrator.get("voice") or ""),
                         "channel_cta": _is_channel_cta_text(
                             chunk, channel_cta_text)}
                        for chunk in chunks]
    if not chunks:
        raise ValueError("Không tìm thấy nội dung có thể đọc.")

    segments = [Segment(i, float(i - 1), float(i), chunk)
                for i, chunk in enumerate(chunks, 1)]
    if utterances:
        for seg, meta in zip(segments, segment_meta):
            voice = meta["voice"]
            if engine == "edge":
                edge_voice, edge_pitch = _edge_voice_and_pitch(voice)
                seg.voice = f"{edge_voice}|{edge_pitch}"
            elif engine == "capcut":
                seg.voice = voice or CAPCUT_DEFAULT_VOICE
            else:
                seg.voice = voice
            meta["voice"] = seg.voice
    else:
        assign_voices(
            segments, "narrator", narrator, engine=engine,
            vieneu_voice=narrator.get("voice") if engine == "vieneu" else None)
        for seg, meta in zip(segments, segment_meta):
            meta["voice"] = seg.voice

    if engine == "capcut":
        capcut_opts = dict(capcut_options or {})
        capcut_opts.setdefault(
            "fallback_voice", narrator.get("voice") or CAPCUT_DEFAULT_VOICE)
        raw_clips = _synth_all_capcut(
            segments, workdir, base_rate, concurrency,
            max_retries=max_retries, capcut_options=capcut_opts,
            cancel_event=cancel_event)
    elif engine == "vieneu":
        _VIENEU_KWARGS = dict(vieneu_options or {})
        raw_clips = _synth_all_vieneu(
            segments, workdir, max_retries=max_retries,
            cancel_event=cancel_event)
    else:
        raw_clips = asyncio.run(_synth_all(
            segments, workdir, base_rate, concurrency,
            max_retries=max_retries, retry_base_delay=retry_base_delay,
            cancel_event=cancel_event))

    _raise_if_cancelled(cancel_event)
    failed = [s.index for s, path in zip(segments, raw_clips)
              if is_speakable(s.text) and not path]
    prepared_clips: List[str] = []
    clip_durations: List[float] = []
    clip_texts: List[str] = []
    clip_meta: List[dict] = []
    compacted_count = 0
    cta_fast_count = 0
    cta_speed = _channel_cta_speed(channel_cta_speed)
    for i, (seg, path) in enumerate(zip(segments, raw_clips)):
        _raise_if_cancelled(cancel_event)
        if not path or not os.path.exists(path):
            continue
        if cta_speed > 1.001 and segment_meta[i].get("channel_cta"):
            ext = os.path.splitext(path)[1] or ".mp3"
            faster = os.path.join(workdir, f"manual_cta_fast_{i:05d}{ext}")
            path = change_speed(path, faster, cta_speed)
            cta_fast_count += 1
        duration = ffprobe_duration(path)
        expected = max(1.0, len(seg.text or "") / 9.0)
        # Chỉ can thiệp khi clip rõ ràng chậm bất thường, tránh làm mất nhịp kể
        # tự nhiên của giọng bình thường.
        if duration > max(expected * 1.8, expected + 5.0):
            ext = os.path.splitext(path)[1] or ".mp3"
            compacted = os.path.join(workdir, f"manual_compact_{i:05d}{ext}")
            candidate = compact_long_silences(path, compacted)
            if candidate != path:
                cand_dur = ffprobe_duration(candidate)
                if cand_dur < duration * 0.92:
                    path = candidate
                    duration = cand_dur
                    compacted_count += 1
        prepared_clips.append(path)
        clip_durations.append(max(0.05, duration))
        clip_texts.append(seg.text or "")
        clip_meta.append(segment_meta[i])
    clips = prepared_clips
    if failed or not clips:
        sample = ", ".join(str(i) for i in failed[:10]) or "tất cả"
        raise RuntimeError(f"TTS không tạo được đoạn: {sample}.")

    if compacted_count:
        log(f"Đã rút khoảng lặng bất thường trong {compacted_count} đoạn TTS.", "info")
    if cta_fast_count:
        log(f"Đã tăng tốc {cta_fast_count} đoạn nhắc kênh lên {cta_speed:.2g}x.", "info")

    # Các voice/engine TTS trả mức âm lượng rất khác nhau. Cân từng câu trước
    # khi nối để lúc đổi nhân vật không bị câu quá to, câu quá nhỏ.
    _raise_if_cancelled(cancel_event)
    concat_audio_clips(
        clips, out_path, sr=48000, normalize_loudness=True)
    log(f"Đã cân âm lượng {len(clips)} đoạn giọng về -18 LUFS.", "info")
    duration = ffprobe_duration(out_path)
    if duration <= 0 or not os.path.exists(out_path):
        raise RuntimeError("File âm thanh tạo ra bị rỗng hoặc không đọc được.")

    # Timeline từng đoạn theo thứ tự nối - để lớp trên sinh PHỤ ĐỀ khớp giọng
    # đọc. Concat nối sát các clip nên mốc = cộng dồn độ dài từng clip.
    timeline = build_narration_timeline(clip_texts, clip_durations, clip_meta)
    log(f"Đã tạo audio từ văn bản: {len(chunks)} đoạn, {duration:.1f} giây.", "ok")
    return {"path": out_path, "duration": duration, "chunks": len(chunks),
            "segments": timeline}


def build_narration_timeline(texts: List[str], durations: List[float],
                             metadata: Optional[List[dict]] = None) -> List[dict]:
    """Cộng dồn độ dài các đoạn TTS đã nối thành mốc thời gian tuyệt đối.

    Thuần logic để test được: trả [{"start","end","text"}] theo giây.
    """
    out: List[dict] = []
    cursor = 0.0
    for i, (text, dur) in enumerate(zip(texts, durations)):
        end = cursor + max(0.05, float(dur or 0.0))
        cleaned = (text or "").strip()
        if cleaned:
            item = {"start": round(cursor, 3), "end": round(end, 3),
                    "text": cleaned}
            if metadata and i < len(metadata):
                item.update({k: v for k, v in metadata[i].items() if v is not None})
            out.append(item)
        cursor = end
    return out


def _format_ts(sec: float) -> str:
    m, s = divmod(max(0.0, sec), 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{int(m):02d}:{s:05.2f}"


def build_voice_track(
    segments: List[Segment],
    workdir: str,
    total_duration: float,
    engine: str = "edge",
    voice_mode: str = "narrator",
    narrator: Optional[dict] = None,
    base_rate: str = "+0%",
    max_speed: float = 1.6,
    min_gap: float = 0.08,
    concurrency: int = 8,
    max_retries: int = 4,
    retry_base_delay: float = 1.2,
    fail_report_path: Optional[str] = None,
    recover_drift: bool = True,
    vieneu_voice: Optional[str] = None,
    vieneu_voices: Optional[List[str]] = None,
    vieneu_options: Optional[dict] = None,
    capcut_options: Optional[dict] = None,
    trim: bool = True,
    sync_offset_seconds: float = 0.0,
    sync_mode: str = "cascade",
    trim_overflow: bool = True,
    max_overhang: float = 0.75,
) -> Tuple[List[Optional[str]], List[float], List[Placement]]:
    """Tổng hợp giọng cho toàn bộ + xếp lịch chống đè.

    engine: "edge" (mặc định, edge-tts) | "vieneu" (VieNeu-TTS local, cần
            pip install vieneu - xem docstring đầu file).
    Trả về (danh sách clip cuối, danh sách mốc đặt, danh sách Placement).
    Những dòng lỗi TTS sau khi đã thử lại hết mức sẽ bị bỏ trống (không có giọng)
    và được liệt kê ra cảnh báo + file báo cáo (fail_report_path) để người dùng
    biết chính xác dòng nào cần xử lý thủ công.
    """
    os.makedirs(workdir, exist_ok=True)
    engine = (engine or "edge").strip().lower()
    global _VIENEU_KWARGS
    _VIENEU_KWARGS = dict(vieneu_options or {})
    assign_voices(segments, voice_mode, narrator, engine=engine,
                 vieneu_voice=vieneu_voice, vieneu_voices=vieneu_voices)

    if engine == "vieneu":
        engine_label = "VieNeu-TTS"
        raw_clips = _synth_all_vieneu(segments, workdir,
                                      max_retries=max(2, max_retries // 2))
    elif engine == "capcut":
        engine_label = "CapCut TTS"
        capcut_opts = dict(capcut_options or {})
        capcut_opts.setdefault(
            "fallback_voice", narrator.get("voice") or CAPCUT_DEFAULT_VOICE)
        raw_clips = _synth_all_capcut(
            segments, workdir, base_rate, concurrency,
            max_retries=max(2, max_retries // 2),
            capcut_options=capcut_opts)
    else:
        engine_label = "edge-tts"
        log(f"Tổng hợp giọng cho {len(segments)} dòng (edge-tts, {concurrency} luồng)...", "step")
        raw_clips = asyncio.run(_synth_all(segments, workdir, base_rate, concurrency,
                                           max_retries=max_retries,
                                           retry_base_delay=retry_base_delay))

    # Báo cáo các dòng vẫn lỗi sau mọi lần thử lại (sẽ bị câm tiếng trong video cuối)
    failed = [s for s, p in zip(segments, raw_clips) if is_speakable(s.text) and not p]
    if failed:
        log(f"{len(failed)}/{len(segments)} dòng KHÔNG tổng hợp được giọng sau khi đã thử lại "
            f"-> các đoạn này sẽ câm tiếng trong video.", "warn")
        lines = [f"[{_format_ts(s.start)} --> {_format_ts(s.end)}] (dòng {s.index}) {s.text}"
                for s in failed]
        for l in lines[:10]:
            log(f"  · {l}", "warn")
        if len(lines) > 10:
            log(f"  · ... và {len(lines) - 10} dòng khác (xem đầy đủ trong báo cáo).", "warn")
        if fail_report_path:
            try:
                with open(fail_report_path, "w", encoding="utf-8") as f:
                    f.write(f"Các dòng KHÔNG tổng hợp được giọng ({engine_label}) sau khi đã thử lại:\n\n")
                    f.write("\n".join(lines) + "\n")
                log(f"Đã ghi danh sách dòng lỗi: {fail_report_path}", "info")
            except OSError as e:
                log(f"Không ghi được báo cáo lỗi TTS: {e}", "warn")

    # Cắt khoảng lặng thừa hai đầu mỗi câu + đo độ dài tự nhiên. Trước đây chỗ
    # này là 4 LƯỢT tiến trình con TUẦN TỰ cho mỗi clip (đo trước, cắt, đo sau,
    # đo nat) - với 500-1000 dòng thoại, riêng tiền spawn process đã tốn nhiều
    # phút. Giờ gom về MỘT việc/clip (cắt + đo một lần) chạy song song 8 luồng;
    # tổng "trước/sau" để log được cộng ngay trong từng việc.
    nat: List[float] = [0.0] * len(raw_clips)
    todo_do = [(i, p) for i, p in enumerate(raw_clips) if p]
    if todo_do:
        t_do = time.time()

        def _trim_va_do(i: int, p: str):
            truoc = ffprobe_duration(p) if trim else 0.0
            if trim:
                t_out = os.path.join(workdir, f"trim_{i:05d}.wav")
                p = trim_silence(p, t_out)
            sau = ffprobe_duration(p)
            return i, p, truoc, sau

        before = after = 0.0
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = [pool.submit(_trim_va_do, i, p) for i, p in todo_do]
            for fut in as_completed(futs):
                i, p, truoc, sau = fut.result()
                raw_clips[i] = p
                nat[i] = max(0.0, sau)
                before += truoc
                after += sau
        if trim and before > after > 0:
            log(f"Cắt khoảng lặng thừa: {before:.0f}s -> {after:.0f}s "
                f"(tiết kiệm {before - after:.0f}s)", "ok")
        log(f"Chuẩn hoá + đo {len(todo_do)} clip trong {time.time() - t_do:.1f}s "
            "(song song 8 luồng).", "dim")

    # Xếp lịch chống đè + TÌM TỐC ĐỘ NỀN đủ để không trôi (chia đều cho mọi câu
    # thay vì để câu 1.0x câu 1.6x nghe giật cục mà vẫn trôi).
    try:
        sync_offset_seconds = float(sync_offset_seconds or 0.0)
    except (TypeError, ValueError):
        sync_offset_seconds = 0.0
    starts = [min(max(0.0, s.start + sync_offset_seconds), max(0.0, total_duration))
              for s in segments]
    ends = [min(max(start + 0.01, s.end + sync_offset_seconds),
                max(start + 0.01, total_duration))
            for start, s in zip(starts, segments)]
    if abs(sync_offset_seconds) >= 0.001:
        direction = "trễ" if sync_offset_seconds > 0 else "sớm"
        log(f"Đồng bộ voice/sub: cho chạy {direction} {abs(sync_offset_seconds):.2f}s.", "info")
    sync_mode = str(sync_mode or "cascade").strip().lower()
    try:
        max_overhang = max(0.0, float(max_overhang))
    except (TypeError, ValueError):
        max_overhang = 0.0
    strict_sync = sync_mode in ("strict", "hard", "frame", "frame-lock",
                                "frame_locked", "source", "original")
    if strict_sync:
        placements = fit_segments_strict(
            starts, nat, max_speed=max_speed, min_gap=min_gap,
            total_duration=total_duration, trim_overflow=trim_overflow,
            ends=ends, max_overhang=max_overhang)
        trimmed_count = sum(1 for p in placements if p.trimmed)
        log("Sync strict: mỗi câu bám mốc start gốc, giữ ít nhất đến end gốc "
            f"và chỉ được tràn tối đa {max_overhang:.2f}s vào khoảng im lặng; "
            "thoại vốn chồng nhau không bị ép nhanh theo câu kế tiếp.", "info")
        over = [p for p, e in zip(placements, ends)
                if p.placed_end > e + max_overhang + 0.05]
        if over:
            log(f"Còn {len(over)}/{len(placements)} clip đọc quá phụ đề của nó "
                "(bản dịch quá dài so với slot). Giảm translate.chars_per_sec "
                "hoặc tăng tts.max_speed.", "warn")
        if trimmed_count:
            log(f"Sync strict phải cắt đuôi {trimmed_count}/{len(placements)} clip "
                "vì bản đọc vẫn dài hơn slot dù đã tăng tốc. Muốn ít cắt hơn: "
                "rút gọn bản dịch hoặc tăng tts.max_speed.", "warn")
    else:
        placements, base = auto_fit(starts, nat, max_speed=max_speed, min_gap=min_gap,
                                    total_duration=total_duration,
                                    recover_drift=recover_drift)
        if base > 1.001:
            log(f"Giọng đọc dài hơn thời lượng video -> nói nhanh đều {base:.2f}x "
                "cho khớp hình (thay vì tăng tốc giật cục từng câu).", "info")
    worst = max((p.drift for p in placements), default=0.0)
    if worst > 3.0:
        log(f"CẢNH BÁO: vẫn trễ tới {worst:.1f}s so với hình. Bản dịch còn dài "
            "quá so với thoại gốc - có thể tăng tts.max_speed nhẹ hoặc giảm "
            "tts.sync_offset_seconds nếu đang đặt trễ quá nhiều.", "warn")

    # Áp tăng tốc cho clip nào cần — SONG SONG (ThreadPoolExecutor) để nhanh hơn
    final_clips: List[Optional[str]] = list(raw_clips)  # copy, sẽ ghi đè chỗ cần
    placed_starts: List[float] = []
    speed_jobs = []  # (index_in_list, clip_path, output_path, speed, seg_index)
    for idx, (seg, clip, pl) in enumerate(zip(segments, raw_clips, placements)):
        seg.placed_start = pl.placed_start
        seg.speed = pl.speed
        seg.voice_duration = pl.final_dur if clip else None
        placed_starts.append(pl.placed_start)
        if clip is not None and (pl.speed > 1.001 or pl.trimmed):
            fout = os.path.join(workdir, f"final_{pl.index:05d}.wav")
            limit = pl.final_dur if pl.trimmed else None
            speed_jobs.append((idx, clip, fout, pl.speed, pl.index, limit))

    if speed_jobs:
        t_speed = time.time()
        log(f"Đổi tốc độ {len(speed_jobs)} clip (song song, tối đa 4 luồng)...", "step")
        done_count = 0

        def _do_speed(job):
            idx, clip, fout, speed, seg_idx, limit = job
            try:
                change_speed(clip, fout, speed, max_duration=limit)
                return idx, fout, None
            except Exception as e:
                return idx, clip, e  # giữ bản gốc nếu lỗi

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_do_speed, j): j for j in speed_jobs}
            for fut in as_completed(futures):
                idx, result_path, err = fut.result()
                final_clips[idx] = result_path
                if err:
                    j = futures[fut]
                    log(f"Không tăng tốc được dòng {j[4]}: {err}", "warn")
                done_count += 1
                if done_count % 10 == 0 or done_count == len(speed_jobs):
                    log(f"  ...đổi tốc độ {done_count}/{len(speed_jobs)} clip", "info")

        log(f"Đổi tốc độ xong trong {time.time() - t_speed:.1f}s.", "ok")

    # Đo LẠI độ dài THẬT của từng clip sau khi đổi tốc độ/cắt đuôi.
    # Trước đây seg.voice_duration giữ nguyên pl.final_dur (độ dài DỰ KIẾN) đặt
    # từ trước khi chạy ffmpeg. Nếu change_speed lỗi, nhánh except giữ lại clip
    # GỐC (dài hơn, chưa tăng tốc) nhưng SRT vẫn được ghi theo độ dài dự kiến
    # -> phụ đề ghi lại ngắn hơn giọng thật, nghe như thoại chạy trước hình.
    # (Đo song song - đây từng là một lượt ffprobe tuần tự nữa cho mỗi dòng.)
    real_dur_map: dict = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(ffprobe_duration, clip): idx
                for idx, clip in enumerate(final_clips) if clip}
        for fut in as_completed(futs):
            real_dur_map[futs[fut]] = fut.result()

    strict_guarded = 0
    for idx, (seg, clip, pl) in enumerate(zip(segments, final_clips, placements)):
        seg.audio_path = clip
        if clip:
            real_dur = real_dur_map.get(idx, 0.0)
            # Strict là một cam kết về timeline, kể cả khi lần atempo phía
            # trên lỗi và nhánh fallback giữ lại clip tự nhiên.  Nếu chỉ đo
            # lại rồi chấp nhận clip dài, giọng có thể tiếp tục hàng chục giây
            # sau hình tương ứng.  Áp trần dự kiến lần cuối trước khi mixer đọc.
            if (strict_sync and trim_overflow and pl.final_dur > 0.01
                    and real_dur > pl.final_dur + 0.03):
                guarded = os.path.join(workdir, f"guard_{pl.index:05d}.wav")
                try:
                    change_speed(clip, guarded, 1.0,
                                 max_duration=pl.final_dur)
                    guarded_dur = ffprobe_duration(guarded)
                    if guarded_dur > 0.01:
                        final_clips[idx] = guarded
                        clip = guarded
                        real_dur = guarded_dur
                        seg.audio_path = guarded
                        pl.trimmed = True
                        pl.final_dur = min(pl.final_dur, guarded_dur)
                        strict_guarded += 1
                except Exception as e:
                    log(f"Không áp được trần strict cho dòng {seg.index}: {e}",
                        "warn")
            if real_dur > 0.01:
                seg.voice_duration = real_dur

    if strict_guarded:
        log(f"Đã áp lại trần strict cho {strict_guarded} clip có thời lượng thực "
            "dài hơn kế hoạch; không cho thoại kéo dài quá hình.", "warn")

    return final_clips, placed_starts, placements
