"""Engine VieNeu-TTS (local)."""
from __future__ import annotations

import os
import threading
from typing import List, Optional

from ..asr import is_speakable
from ..srt_utils import Segment
from ..utils import log
from .common import _clean_partial, _raise_if_cancelled

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

def _vieneu_preset_voices() -> List[str]:
    """Danh sách tên giọng dựng sẵn của VieNeu-TTS (rỗng nếu lấy lỗi)."""
    try:
        model = _load_vieneu_model()
        return [_vid for _label, _vid in model.list_preset_voices()]
    except Exception as e:
        log(f"Không lấy được danh sách giọng VieNeu-TTS: {e}", "warn")
        return []


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
