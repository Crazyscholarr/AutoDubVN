"""Đoán ngôn ngữ, lọc câu bịa, và dò vùng có tiếng / bản đồ thoại."""
from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from .. import speechmap
from ..srt_utils import Segment
from ..utils import log, run, ffprobe_duration
from .common import _reindex
from .merge import merge_time_ranges, parse_silencedetect_intervals

_VI_CHARS = set("ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩị"
                "òóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ")

# Whisper hay "điền vào chỗ trống" bằng câu quảng cáo kênh học được từ dữ liệu
# huấn luyện (phụ đề YouTube). Những câu này lọt vào bản dịch rồi được ĐỌC TO
# trong video lồng tiếng, nên phải chặn ngay từ đây.
_JUNK_PATTERNS = re.compile(
    r"(hãy\s+subscribe|đăng\s*ký\s+kênh|ghiền\s+mì\s+gõ|bấm\s+chuông"
    r"|like\s+và\s+chia\s+sẻ|请不吝点赞|订阅|转发|打赏|明镜与点点栏目"
    r"|å­—å¹•ç”±|subtitles?\s+by|amara\.org|thanks?\s+for\s+watching"
    r"|è§†é¢‘ç¼–è¾‘|ä¸­æ–‡å­—å¹•)", re.I)

_NORM_KEY_RE = re.compile(r"[\s\W_]+")


def _norm_key(text: str) -> str:
    return _NORM_KEY_RE.sub("", (text or "").lower())


def guess_language(segs: List[Segment]) -> Optional[str]:
    """Trả 'zh' | 'vi' | None. Chỉ dùng ký tự, không cần thư viện ngoài."""
    text = " ".join((s.text or "") for s in segs[:400])
    if not text.strip():
        return None
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    vi = sum(1 for c in text.lower() if c in _VI_CHARS)
    letters = sum(1 for c in text if c.isalpha())
    if letters and cjk / max(1, letters) > 0.3:
        return "zh"
    if letters and vi / max(1, letters) > 0.04:
        return "vi"
    return None


def drop_hallucinations(segs: List[Segment], min_repeat: int = 3,
                        min_dur: float = 4.0) -> Tuple[List[Segment], List[str]]:
    """Bỏ các dòng gần như chắc chắn là câu bịa. Rất thận trọng - chỉ bỏ khi:

      * câu lặp lại >= `min_repeat` lần VÀ kéo dài >= `min_dur` giây
        (thoại thật lặp nhiều lần thì cũng ngắn, không dài lê thê), HOẶC
      * câu khớp mẫu quảng cáo kênh quen thuộc VÀ lặp >= 2 lần.
    """
    counts: dict = {}
    keys: List[str] = []
    for s in segs:
        k = _norm_key(s.text)
        keys.append(k)
        counts[k] = counts.get(k, 0) + 1

    kept, removed = [], []
    for s, k in zip(segs, keys):
        c = counts.get(k, 0)
        dur = float(s.end) - float(s.start)
        junk = bool(_JUNK_PATTERNS.search(s.text or ""))
        if (c >= min_repeat and dur >= min_dur) or (junk and c >= 2) or (junk and dur >= min_dur):
            removed.append(f"[{s.start:.1f}s] {s.text}")
            continue
        kept.append(s)
    return _reindex(kept), removed


def _slice_audio(audio_path: str, start: float, end: float, out_path: str) -> str:
    run(["ffmpeg", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
         "-i", audio_path, "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", out_path])
    return out_path


def _mean_volume_db(path: str) -> float:
    res = run(["ffmpeg", "-hide_banner", "-i", path, "-af", "volumedetect",
               "-f", "null", "-"], check=False)
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", res.stderr or "")
    return float(m.group(1)) if m else -99.0


def confirm_speech_holes(audio_path, holes, silence_db: float = -42.0,
                         slicer=None, measurer=None):
    """Drop VAD holes whose mean energy is at or below the silence floor.

    Measurement failure keeps the hole (fail closed). Callers must not treat
    a missing file as silence.
    """
    import tempfile
    slicer = slicer or _slice_audio
    measurer = measurer or _mean_volume_db
    kept, dropped = [], []
    for start, end in holes:
        measured = None
        try:
            if not audio_path or not os.path.isfile(audio_path):
                kept.append((start, end))
                continue
            with tempfile.TemporaryDirectory(prefix="autodub_vad_") as tmpdir:
                piece = os.path.join(tmpdir, "gap.wav")
                slicer(audio_path, start, end, piece)
                if os.path.isfile(piece) and os.path.getsize(piece) > 64:
                    measured = measurer(piece)
        except InterruptedError:
            raise
        except Exception:
            measured = None
        if measured is None or float(measured) > float(silence_db):
            kept.append((start, end))
        else:
            dropped.append((start, end, float(measured)))
    return kept, dropped


def _nonsilent_ranges(audio_path: str, duration: float,
                      noise_db: float = -42.0,
                      min_silence: float = 0.35) -> List[Tuple[float, float]]:
    res = run(["ffmpeg", "-hide_banner", "-i", audio_path,
               "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
               "-f", "null", "-"], check=False)
    return parse_silencedetect_intervals(res.stderr or "", duration)


def speech_map_from_audio(audio_path: str, duration: float = 0.0,
                          noise_db: float = -42.0,
                          min_silence: float = 0.35,
                          step: float = 0.2) -> Optional[speechmap.SpeechMap]:
    """Dựng BẢN ĐỒ THOẠI thô từ chính file audio (ffmpeg silencedetect).

    Dùng khi chạy lại trên phụ đề cũ (không còn mốc ký tự của ASR) hoặc khi
    backend không trả timestamp. Bản đồ này không biết từng ký tự nằm ở đâu,
    nhưng biết CHÍNH XÁC chỗ nào có tiếng chỗ nào im - đủ để không còn chia phụ
    đề vắt qua quãng lặng. Mỗi vùng có tiếng được băm thành các mốc `step` giây
    để làm "đồng hồ thoại".
    """
    duration = float(duration or 0.0) or ffprobe_duration(audio_path)
    if duration <= 0:
        return None
    try:
        ranges = _nonsilent_ranges(audio_path, duration, noise_db, min_silence)
    except Exception as e:
        log(f"Không dò được vùng có tiếng để dựng bản đồ thoại: {e}", "warn")
        return None
    step = max(0.05, float(step))
    marks: List[Tuple[float, float]] = []
    for rs, re_ in ranges:
        t = float(rs)
        while t < re_ - 1e-6:
            nxt = min(re_, t + step)
            marks.append((t, nxt))
            t = nxt
    m = speechmap.SpeechMap(marks)
    return None if m.empty else m


def _find_tmp_audio(out_dir: str) -> Optional[str]:
    for name in ("audio16k.flac", "audio16k.wav"):
        p = os.path.join(out_dir, "_tmp", name)
        if os.path.exists(p):
            return p
    return None


def ensure_speech_map(out_dir: str, stem: str,
                      video_path: Optional[str] = None,
                      trim_start: float = 0.0,
                      trim_duration: Optional[float] = None,
                      allow_extract: bool = True) -> Optional[speechmap.SpeechMap]:
    """Bảo đảm có BẢN ĐỒ THOẠI cho lần chạy hiện tại, kể cả khi dùng SRT cũ.

    Thứ tự thử: file bản đồ đã lưu -> audio 16k còn trong _tmp -> tách audio lại
    từ video. Không có gì thì trả None và các bước chia phụ đề lùi về chia theo
    tỉ lệ (như bản trước).
    """
    path = speechmap.default_path(out_dir, stem)
    m = speechmap.SpeechMap.load(path)
    if m is not None:
        speechmap.set_active(m, path)
        log(f"Dùng lại bản đồ thoại đã lưu ({len(m)} mốc): {os.path.basename(path)}",
            "ok")
        return m

    audio = _find_tmp_audio(out_dir)
    if audio is None and allow_extract and video_path and os.path.exists(video_path):
        from .. import video as _video
        try:
            log("Chưa có bản đồ thoại - tách nhanh audio để dò mốc có tiếng "
                "(giúp chia phụ đề không vắt qua quãng lặng)...", "step")
            # loudnorm=True để giống hệt audio mà ASR đã nghe: ngưỡng im lặng
            # -42 dB chỉ đúng trên track đã chuẩn hoá âm lượng.
            audio = _video.ensure_audio(
                video_path, os.path.join(out_dir, "_tmp", "audio16k.wav"),
                loudnorm=True, trim_start=trim_start,
                trim_duration=trim_duration)
        except Exception as e:
            log(f"Không tách được audio để dựng bản đồ thoại: {e}", "warn")
            audio = None
    if not audio:
        log("Không có bản đồ thoại: các bước chia lại phụ đề sẽ chia theo tỉ lệ "
            "ký tự. Muốn khớp hình tốt nhất, xoá file .src.srt để nhận diện lại.",
            "warn")
        return None

    m = speech_map_from_audio(audio)
    if m is None:
        return None
    speechmap.set_active(m, path)
    m.save(path)
    log(f"Đã dựng bản đồ thoại từ audio ({len(m)} mốc) và lưu lại: "
        f"{os.path.basename(path)}", "ok")
    return m
