"""Hằng số và helper dùng chung cho các engine TTS."""
from __future__ import annotations

import os
import re
from typing import Optional, Tuple

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


def _raise_if_cancelled(cancel_event=None) -> None:
    """Dừng ở ranh giới an toàn giữa hai lượt TTS.

    File đã tạo xong vẫn được giữ trong cache để lần chạy sau tiếp tục; chỉ
    phần đang dở mới bị bỏ.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Đã dừng tạo giọng theo yêu cầu.")


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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

def _clean_partial(path: str) -> None:
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _format_ts(sec: float) -> str:
    m, s = divmod(max(0.0, sec), 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{int(m):02d}:{s:05.2f}"
