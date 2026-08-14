"""Nhận diện phụ đề (ASR) - thiết kế CHỐNG MẤT ĐOẠN cho video dài.

VÌ SAO VIẾT LẠI: WhisperX bị 2 lỗi gốc khiến video 3 tiếng chỉ ra 15 phút sub:
  (1) VAD pyannote ép pad_onset=pad_offset=0 -> cắt cụt/bỏ qua cả đoạn có tiếng.
  (2) Giải mã theo lô KHÔNG có temperature fallback; các tham số an toàn
      (no_speech_threshold, compression_ratio_threshold, log_prob_threshold)
      hoàn toàn không được đọc -> Whisper đọc 1 câu rồi ngắt mà không ai bắt lỗi,
      timestamp vẫn đủ dài nhưng CHỮ MẤT SẠCH phần giữa.

KIẾN TRÚC MỚI (3 lớp):
  Lớp 1 - Engine tốt cho từng ngôn ngữ:
      "paraformer" : FunASR Paraformer-large + fsmn-vad + ct-punc.
                     Tốt nhất cho TIẾNG TRUNG (CER ~1.7% vs Whisper ~10%),
                     có timestamp câu gốc, ~2GB VRAM, an toàn với file 3 tiếng.
      "faster-whisper": cấu hình ĐÚNG - temperature fallback + Silero VAD có
                     speech_pad_ms (đệm thật) -> đa ngôn ngữ, không trôi/bịa.
      "sensevoice" : nhanh, chính xác nhưng timestamp thô (chỉ theo VAD).
      "whisperx"   : giữ để tương thích, đã ép tham số an toàn + cảnh báo.
  Lớp 2 - KIỂM TRA ĐỘ PHỦ: so tổng thời lượng nói bốc được với độ dài audio.
      Thiếu là báo động ngay, không im lặng cho qua.
  Lớp 3 - TỰ VÁ LỖ HỔNG (gap rescue): dò khoảng trống dài, cắt riêng đoạn đó,
      nhận diện lại rồi ghép vào đúng mốc thời gian tuyệt đối.
"""
from __future__ import annotations

import math
import os
import re
import tempfile
from typing import Any, List, Optional, Tuple

from . import speechmap
from .srt_utils import Segment
from .utils import log, run, ffprobe_duration

_MODEL_CACHE = {}
_TAG_RE = re.compile(r"<\|[^|]*\|>")

# Ngôn ngữ không dùng khoảng trắng -> giới hạn ký tự mỗi dòng phải nhỏ hơn
_CJK = {"zh", "ja", "yue", "ko"}


def _clean(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _max_chars_for(lang: Optional[str], default: int) -> int:
    return 34 if (lang or "").lower()[:2] in {"zh", "ja", "ko"} else default


# --------------------------------------------------------------------------- #
#  Tiện ích thuần logic (test được, không cần model)
# --------------------------------------------------------------------------- #
def split_long(text: str, start: float, end: float, max_chars: int) -> List[Segment]:
    """Tách đoạn dài theo dấu câu.

    Mốc thời gian lấy từ BẢN ĐỒ THOẠI (mốc từng ký tự ASR nghe được) nếu có;
    chỉ khi không có mới chia theo tỉ lệ số ký tự như trước.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [Segment(0, start, end, text)]
    parts = [p for p in re.split(r"(?<=[.!?\u3002\uff01\uff1f,\uff0c\u3001\uff1b;])\s*", text) if p.strip()]
    if len(parts) <= 1:
        return [Segment(0, start, end, text)]
    # Gộp các mảnh quá ngắn lại cho tự nhiên
    merged: List[str] = []
    for p in parts:
        if merged and len(merged[-1]) + len(p) <= max_chars:
            merged[-1] += " " + p if not _is_cjk(p) else p
        else:
            merged.append(p)
    weights = [max(1, len(p.strip())) for p in merged]
    bounds = speechmap.slice_window(start, end, weights)
    if bounds:
        return [Segment(0, a, b, p.strip()) for (a, b), p in zip(bounds, merged)]
    total = sum(weights) or 1
    segs, t, dur = [], start, max(0.01, end - start)
    for p, w in zip(merged, weights):
        d = dur * (w / total)
        segs.append(Segment(0, t, t + d, p.strip()))
        t += d
    return segs


def _is_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff" for c in s[:10])


_CJK_TERMINAL = set("\u3002\uff01\uff1f!?\u2026")


def merge_cjk_sentence_fragments(segs: List[Segment],
                                 max_chars: int = 34,
                                 max_gap: float = 0.85) -> List[Segment]:
    """Gom các mảnh tiếng Trung/Nhật/Hàn bị ASR cắt vụn giữa cụm.

    FunASR đôi khi trả timestamp kiểu karaoke, ví dụ một câu bị tách thành
    "周" rồi "末，老手...". Nếu để nguyên, Gemini dịch từng dòng riêng và tên
    người/cụm nghĩa bị xé sang hai subtitle. Ta gom liên tiếp tới khi gặp dấu
    kết câu mạnh; dấu phẩy/đốn hiệu chỉ là chỗ nghỉ, chưa phải hết ý.
    """
    merged: List[Segment] = []
    buf: Optional[Segment] = None

    def flush():
        nonlocal buf
        if buf is not None:
            merged.append(buf)
            buf = None

    for s in sorted(segs, key=lambda x: (x.start, x.end)):
        txt = (s.text or "").strip()
        if not txt:
            continue
        if not _is_cjk(txt):
            flush()
            merged.append(s)
            continue
        if buf is None:
            buf = Segment(s.index, s.start, s.end, txt, speaker=s.speaker)
            continue

        gap = max(0.0, s.start - buf.end)
        joined = buf.text + txt
        prev_done = buf.text[-1] in _CJK_TERMINAL
        too_long = len(joined) > max(max_chars, 1) * 2
        if prev_done or gap > max_gap or too_long:
            flush()
            buf = Segment(s.index, s.start, s.end, txt, speaker=s.speaker)
        else:
            buf.text = joined
            buf.end = max(buf.end, s.end)
            if not buf.speaker:
                buf.speaker = s.speaker

    flush()
    for i, s in enumerate(merged, 1):
        s.index = i
    return merged


def is_speakable(text: str) -> bool:
    """Có gì để ĐỌC không? Dòng chỉ còn dấu câu ("." , "?" , "…") là rác:
    ASR sinh ra ở đoạn im lặng, TTS không đọc được (edge-tts trả "No audio was
    received"), mà giữ lại thì tốn thêm 5 lần thử lại rồi câm tiếng."""
    return any(c.isalnum() for c in (text or ""))


def normalize_segments(segs: List[Segment], max_chars: int) -> List[Segment]:
    """Làm sạch, tách dòng dài, sắp theo thời gian, đánh lại số thứ tự."""
    out: List[Segment] = []
    for s in segs:
        txt = _clean(s.text)
        if not is_speakable(txt):
            continue
        st, en = float(s.start), float(s.end)
        if en <= st:
            en = st + max(0.4, len(txt) * 0.06)
        for sub in split_long(txt, st, en, max_chars):
            sub.speaker = s.speaker
            out.append(sub)
    out.sort(key=lambda x: (x.start, x.end))
    out = merge_cjk_sentence_fragments(out, max_chars=max_chars)
    for i, s in enumerate(out, 1):
        s.index = i
    return out


def find_gaps(segs: List[Segment], total_duration: float,
              min_gap: float = 25.0, edge_pad: float = 0.3) -> List[Tuple[float, float]]:
    """Tìm các khoảng thời gian KHÔNG có phụ đề, dài hơn min_gap giây.

    Đây là công cụ phát hiện 'mất đoạn giữa'. Trả về list (start, end).
    """
    if total_duration <= 0:
        return []
    gaps: List[Tuple[float, float]] = []
    cursor = 0.0
    for s in sorted(segs, key=lambda x: x.start):
        if s.start - cursor >= min_gap:
            gaps.append((max(0.0, cursor - edge_pad), s.start + edge_pad))
        cursor = max(cursor, s.end)
    if total_duration - cursor >= min_gap:
        gaps.append((max(0.0, cursor - edge_pad), total_duration))
    return gaps


def merge_time_ranges(ranges: List[Tuple[float, float]],
                      merge_gap: float = 0.4) -> List[Tuple[float, float]]:
    """Merge overlapping or near-adjacent time ranges."""
    out: List[Tuple[float, float]] = []
    for start, end in sorted((float(s), float(e)) for s, e in ranges if e > s):
        if not out or start > out[-1][1] + merge_gap:
            out.append((start, end))
        else:
            out[-1] = (out[-1][0], max(out[-1][1], end))
    return out


def parse_silencedetect_intervals(stderr: str, duration: float) -> List[Tuple[float, float]]:
    """Convert ffmpeg silencedetect logs to non-silent audio intervals."""
    if duration <= 0:
        return []
    events: List[Tuple[float, float]] = []
    for m in re.finditer(r"silence_(start|end):\s*([0-9.]+)", stderr or ""):
        events.append((float(m.group(2)), 1.0 if m.group(1) == "start" else 0.0))
    events.sort(key=lambda x: (x[0], x[1]))

    intervals: List[Tuple[float, float]] = []
    cursor = 0.0
    in_silence = False
    for t, kind in events:
        t = max(0.0, min(duration, t))
        if kind == 1.0 and not in_silence:
            if t > cursor:
                intervals.append((cursor, t))
            in_silence = True
        elif kind == 0.0 and in_silence:
            cursor = t
            in_silence = False
    if not in_silence and cursor < duration:
        intervals.append((cursor, duration))
    return merge_time_ranges(intervals, merge_gap=0.15)


def find_uncovered_speech_ranges(segs: List[Segment],
                                 speech_ranges: List[Tuple[float, float]],
                                 total_duration: float,
                                 min_gap: float = 1.2,
                                 edge_pad: float = 0.35,
                                 subtitle_pad: float = 0.2) -> List[Tuple[float, float]]:
    """Find non-silent audio ranges that have no subtitle coverage."""
    if total_duration <= 0:
        return []
    covered = merge_time_ranges([
        (max(0.0, s.start - subtitle_pad), min(total_duration, s.end + subtitle_pad))
        for s in segs
    ], merge_gap=0.05)

    holes: List[Tuple[float, float]] = []
    ci = 0
    for rs, re_ in speech_ranges:
        rs, re_ = max(0.0, rs), min(total_duration, re_)
        cursor = rs
        while ci < len(covered) and covered[ci][1] <= rs:
            ci += 1
        j = ci
        while j < len(covered) and covered[j][0] < re_:
            cs, ce = covered[j]
            if cs > cursor and cs - cursor >= min_gap:
                holes.append((max(0.0, cursor - edge_pad), min(total_duration, cs + edge_pad)))
            cursor = max(cursor, ce)
            if cursor >= re_:
                break
            j += 1
        if re_ - cursor >= min_gap:
            holes.append((max(0.0, cursor - edge_pad), min(total_duration, re_ + edge_pad)))
    return merge_time_ranges(holes, merge_gap=0.35)


def coverage_report(segs: List[Segment], total_duration: float) -> dict:
    """Tính độ phủ: tổng thời lượng có lời / độ dài audio."""
    if total_duration <= 0:
        return {"ok": False, "covered_s": 0.0, "ratio": 0.0, "last_end_s": 0.0,
                "duration_s": 0.0, "lines": len(segs)}
    # Gộp các đoạn chồng nhau trước khi cộng
    covered = 0.0
    cur_s = cur_e = None
    for s in sorted(segs, key=lambda x: x.start):
        if cur_e is None:
            cur_s, cur_e = s.start, s.end
        elif s.start <= cur_e:
            cur_e = max(cur_e, s.end)
        else:
            covered += cur_e - cur_s
            cur_s, cur_e = s.start, s.end
    if cur_e is not None:
        covered += cur_e - cur_s
    last_end = max((s.end for s in segs), default=0.0)
    return {
        "lines": len(segs),
        "duration_s": round(total_duration, 1),
        "covered_s": round(covered, 1),
        "ratio": round(covered / total_duration, 4),
        "last_end_s": round(last_end, 1),
        "tail_ratio": round(last_end / total_duration, 4),
    }


def merge_new_segments(base: List[Segment], extra: List[Segment],
                       tolerance: float = 0.4) -> List[Segment]:
    """Ghép phụ đề vá thêm vào bộ gốc, bỏ trùng lặp gần nhau."""
    out = list(base)
    for e in extra:
        dup = any(abs(b.start - e.start) < tolerance and
                  _clean(b.text) == _clean(e.text) for b in out)
        if not dup:
            out.append(e)
    out.sort(key=lambda x: (x.start, x.end))
    for i, s in enumerate(out, 1):
        s.index = i
    return out


# --------------------------------------------------------------------------- #
#  Đoán ngôn ngữ từ chính nội dung phụ đề (dùng khi DÙNG LẠI file .src.srt cũ,
#  lúc đó không còn kết quả nhận diện ngôn ngữ của ASR nữa)
# --------------------------------------------------------------------------- #
_VI_CHARS = set("ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩị"
                "òóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ")


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


# --------------------------------------------------------------------------- #
#  Sửa lỗi nghe nhầm lặp đi lặp lại (bảng do người dùng khai trong config.yaml)
# --------------------------------------------------------------------------- #
def apply_corrections(segs: List[Segment], rules) -> int:
    """Thay thế theo bảng `asr.corrections` trong config.yaml.

    ASR nghe sai DANH XƯNG/TÊN RIÊNG theo kiểu lặp lại y hệt suốt cả bộ phim
    ("tiểu soái ca" -> "tiểu xoái ta"). Khai một lần, cả 44 tập đều đúng.

    Dạng khai (đều được):
        corrections:
          "tiểu xoái ta": "tiểu soái ca"        # thay chuỗi, KHÔNG phân biệt hoa/thường
          "re:\\bxoái\\b": "soái"                # bắt đầu bằng 're:' -> biểu thức chính quy
    """
    if not rules:
        return 0
    pairs = list(rules.items()) if isinstance(rules, dict) else [
        (k, v) for item in rules for k, v in
        (item.items() if isinstance(item, dict) else [(item[0], item[1])])]
    def _keep_case(right: str):
        """Giữ chữ hoa đầu câu: 'Tiểu Xoái Ta' -> 'Tiểu soái ca', không thành
        'tiểu soái ca' làm hỏng chữ hoa đầu dòng."""
        def _f(m):
            return (right[:1].upper() + right[1:]) if m.group(0)[:1].isupper() else right
        return _f

    compiled = []
    for wrong, right in pairs:
        wrong, right = str(wrong), str(right)
        try:
            if wrong.startswith("re:"):
                # luật regex: dùng thẳng chuỗi thay thế để \1, \2... vẫn chạy
                compiled.append((re.compile(wrong[3:], re.I), right))
            else:
                compiled.append((re.compile(re.escape(wrong), re.I), _keep_case(right)))
        except re.error as e:
            log(f"Bỏ qua luật sửa lỗi sai cú pháp {wrong!r}: {e}", "warn")
    changed = 0
    for s in segs:
        before = s.text
        for rx, right in compiled:
            s.text = rx.sub(right, s.text)
        if s.text != before:
            changed += 1
    return changed


def build_initial_prompt(rules, extra: Optional[str] = None) -> Optional[str]:
    """Mớm sẵn cho Whisper các từ ĐÚNG (vế phải của bảng sửa lỗi) + gợi ý riêng."""
    words = []
    if isinstance(rules, dict):
        words = [str(v) for v in rules.values() if not str(v).startswith("re:")]
    parts = [w for w in words if w.strip()]
    if extra:
        parts.append(str(extra).strip())
    if not parts:
        return None
    return ". ".join(dict.fromkeys(parts))[:900]      # Whisper giới hạn 224 token


# --------------------------------------------------------------------------- #
#  Lọc câu BỊA (hallucination) của Whisper trên đoạn nhạc/im lặng
# --------------------------------------------------------------------------- #
# Whisper hay "điền vào chỗ trống" bằng câu quảng cáo kênh học được từ dữ liệu
# huấn luyện (phụ đề YouTube). Những câu này lọt vào bản dịch rồi được ĐỌC TO
# trong video lồng tiếng, nên phải chặn ngay từ đây.
_JUNK_PATTERNS = re.compile(
    r"(hãy\s+subscribe|đăng\s*ký\s+kênh|ghiền\s+mì\s+gõ|bấm\s+chuông"
    r"|like\s+và\s+chia\s+sẻ|请不吝点赞|订阅|转发|打赏|明镜与点点栏目"
    r"|å­—å¹•ç”±|subtitles?\s+by|amara\.org|thanks?\s+for\s+watching"
    r"|è§†é¢‘ç¼–è¾‘|ä¸­æ–‡å­—å¹•)", re.I)


def _norm_key(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", (text or "").lower())


def drop_hallucinations(segs: List[Segment], min_repeat: int = 3,
                        min_dur: float = 4.0) -> Tuple[List[Segment], List[str]]:
    """Bỏ các dòng gần như chắc chắn là câu bịa. Rất thận trọng - chỉ bỏ khi:

      * câu lặp lại >= `min_repeat` lần VÀ kéo dài >= `min_dur` giây
        (thoại thật lặp nhiều lần thì cũng ngắn, không dài lê thê), HOẶC
      * câu khớp mẫu quảng cáo kênh quen thuộc VÀ lặp >= 2 lần.
    """
    counts: dict = {}
    for s in segs:
        counts[_norm_key(s.text)] = counts.get(_norm_key(s.text), 0) + 1

    kept, removed = [], []
    for s in segs:
        k = _norm_key(s.text)
        c = counts.get(k, 0)
        dur = float(s.end) - float(s.start)
        junk = bool(_JUNK_PATTERNS.search(s.text or ""))
        if (c >= min_repeat and dur >= min_dur) or (junk and c >= 2) or (junk and dur >= min_dur):
            removed.append(f"[{s.start:.1f}s] {s.text}")
            continue
        kept.append(s)
    for i, s in enumerate(kept, 1):
        s.index = i
    return kept, removed


# --------------------------------------------------------------------------- #
#  Kiểm tra một khoảng có tiếng nói hay không (tránh vá vào đoạn im lặng)
# --------------------------------------------------------------------------- #
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
        speechmap.set_active(m)
        log(f"Dùng lại bản đồ thoại đã lưu ({len(m)} mốc): {os.path.basename(path)}",
            "ok")
        return m

    audio = _find_tmp_audio(out_dir)
    if audio is None and allow_extract and video_path and os.path.exists(video_path):
        from . import video as _video
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
    speechmap.set_active(m)
    m.save(path)
    log(f"Đã dựng bản đồ thoại từ audio ({len(m)} mốc) và lưu lại: "
        f"{os.path.basename(path)}", "ok")
    return m


# --------------------------------------------------------------------------- #
#  Backend 1: FunASR Paraformer  (MẶC ĐỊNH - tốt nhất cho tiếng Trung)
# --------------------------------------------------------------------------- #
# Tên thật trên ModelScope của các alias FunASR hay dùng - để tự tìm bản đã tải
# trong cache mà nạp thẳng, khỏi phải hỏi server mỗi lần chạy.
_MS_REPO = {
    "paraformer-zh": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "fsmn-vad": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    "ct-punc": "iic/punc_ct-transformer_cn-en-common-vocab471067-large",
}


def _local_model_dir(name: str) -> Optional[str]:
    """Trả về thư mục model đã tải trong cache ModelScope, hoặc None."""
    repo = _MS_REPO.get(name, name)
    if os.path.isdir(name):
        return name
    if "/" not in repo:
        return None
    root = os.environ.get("MODELSCOPE_CACHE") or os.path.join(
        os.path.expanduser("~"), ".cache", "modelscope")
    for base in (os.path.join(root, "models"), root):
        d = os.path.join(base, repo.replace("/", "--"), "snapshots", "master")
        if os.path.isfile(os.path.join(d, "configuration.json")) or \
           os.path.isfile(os.path.join(d, "config.yaml")) or \
           os.path.isfile(os.path.join(d, "model.pt")):
            return d
    return None


def _resolve(name: str) -> str:
    local = _local_model_dir(name)
    return local or name


_FUNASR_DIRECT_LIMIT_SECONDS = 3 * 3600
_FUNASR_CHUNK_SECONDS = 30 * 60
_FUNASR_CHUNK_OVERLAP_SECONDS = 1.5


def _fmt_hms(seconds: float) -> str:
    s = int(max(0.0, float(seconds or 0.0)) + 0.5)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _timestamp_pairs(value: Any) -> List[Tuple[float, float]]:
    if not isinstance(value, (list, tuple)):
        return []
    if len(value) >= 2 and not isinstance(value[0], (list, tuple, dict)):
        a, b = _to_float(value[0]), _to_float(value[1])
        return [(a, b)] if a is not None and b is not None else []

    pairs: List[Tuple[float, float]] = []
    for item in value:
        if isinstance(item, dict):
            a = _to_float(item.get("start", item.get("begin")))
            b = _to_float(item.get("end", item.get("stop")))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            a, b = _to_float(item[0]), _to_float(item[1])
        else:
            continue
        if a is not None and b is not None and b > a:
            pairs.append((a, b))
    return pairs


def _guess_time_scale(pairs: List[Tuple[float, float]],
                      duration_hint: float = 0.0,
                      default_ms: bool = True) -> float:
    if not pairs:
        return 0.001 if default_ms else 1.0
    max_end = max(b for _, b in pairs)
    if default_ms:
        if max_end > 30.0:
            return 0.001
        if duration_hint > 0 and duration_hint <= 35.0 and max_end <= duration_hint + 5.0:
            return 1.0
        return 0.001
    if duration_hint > 0 and max_end <= duration_hint + 5.0:
        return 1.0
    if max_end > 1000.0:
        return 0.001
    return 1.0


def _iter_funasr_dicts(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_funasr_dicts(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _iter_funasr_dicts(item)


def _collect_sentence_info(obj: Any) -> List[dict]:
    out: List[dict] = []
    for item in _iter_funasr_dicts(obj):
        sents = item.get("sentence_info") or item.get("sentences")
        if not isinstance(sents, list):
            continue
        for sent in sents:
            if isinstance(sent, dict):
                out.append(sent)
    return out


def _sentence_info_to_segments(sents: List[dict], offset: float = 0.0) -> List[Segment]:
    segs: List[Segment] = []
    for sent in sents:
        text = _clean(str(sent.get("text") or sent.get("sentence") or sent.get("raw_text") or ""))
        start = _to_float(sent.get("start", sent.get("begin")))
        end = _to_float(sent.get("end", sent.get("stop")))
        scale = 0.001  # FunASR sentence_info dùng millisecond.
        if start is None or end is None or end <= start:
            pairs = _timestamp_pairs(sent.get("timestamp") or sent.get("timestamps"))
            if not pairs:
                continue
            start, end = pairs[0][0], pairs[-1][1]
            scale = _guess_time_scale(pairs, default_ms=True)
        if end <= start:
            continue
        segs.append(Segment(0, offset + start * scale, offset + end * scale, text))
    return segs


def _timestamp_payload_to_segments(payload: dict, offset: float = 0.0,
                                   duration_hint: float = 0.0) -> List[Segment]:
    text = _clean(str(payload.get("text") or payload.get("sentence") or ""))
    pairs = _timestamp_pairs(payload.get("timestamp") or payload.get("timestamps"))
    if not text or not pairs:
        return []
    scale = _guess_time_scale(pairs, duration_hint=duration_hint, default_ms=True)
    if len(pairs) == 1:
        return [Segment(0, offset + pairs[0][0] * scale,
                        offset + pairs[0][1] * scale, text)]

    words = text.split()
    compact_chars = [c for c in text if not c.isspace()]
    if len(words) == len(pairs):
        tokens = words
        sep = " "
    elif len(compact_chars) == len(pairs):
        tokens = compact_chars
        sep = ""
    else:
        return [Segment(0, offset + pairs[0][0] * scale,
                        offset + pairs[-1][1] * scale, text)]

    terminal = set(".!?;,\u3002\uff01\uff1f\uff0c\u3001\uff1b\u2026")
    max_chars = 34 if _is_cjk(text) else 84
    segs: List[Segment] = []
    buf: List[str] = []
    start_s: Optional[float] = None
    end_s: Optional[float] = None

    def flush() -> None:
        nonlocal buf, start_s, end_s
        if buf and start_s is not None and end_s is not None and end_s > start_s:
            segs.append(Segment(0, offset + start_s, offset + end_s, sep.join(buf).strip()))
        buf, start_s, end_s = [], None, None

    for token, pair in zip(tokens, pairs):
        st, en = pair[0] * scale, pair[1] * scale
        if start_s is None:
            start_s = st
        buf.append(token)
        end_s = en
        joined = sep.join(buf)
        if (token[-1:] in terminal or len(joined) >= max_chars or
                (end_s - start_s) >= 9.0):
            flush()
    flush()
    return segs


def _funasr_segments_from_result(res: Any, offset: float = 0.0,
                                 duration_hint: float = 0.0) -> List[Segment]:
    sents = _collect_sentence_info(res)
    if sents:
        return _sentence_info_to_segments(sents, offset=offset)

    segs: List[Segment] = []
    for item in _iter_funasr_dicts(res):
        segs.extend(_timestamp_payload_to_segments(
            item, offset=offset, duration_hint=duration_hint))
    return segs


# --------------------------------------------------------------------------- #
#  MỐC THỜI GIAN TỪNG KÝ TỰ (nuôi autodub/speechmap.py)
#
#  Paraformer trả timestamp theo TỪNG KÝ TỰ, faster-whisper theo TỪNG TỪ. Đây là
#  dữ liệu quý nhất để chống lệch tiếng/hình, nhưng trước đây bị bỏ đi ngay sau
#  khi lấy start/end của câu. Ba hàm dưới gom lại rồi giao cho speechmap, để mọi
#  bước chia lại phụ đề sau này đặt ranh giới đúng vào khe giữa hai ký tự thật.
# --------------------------------------------------------------------------- #
_LAST_MARKS: List[Tuple[float, float]] = []


def _set_last_marks(marks: List[Tuple[float, float]]) -> None:
    global _LAST_MARKS
    _LAST_MARKS = list(marks or [])


def _take_last_marks(offset: float = 0.0) -> List[Tuple[float, float]]:
    """Lấy (và xoá) mốc của lượt nhận diện vừa rồi, dịch về mốc tuyệt đối."""
    global _LAST_MARKS
    out, _LAST_MARKS = _LAST_MARKS, []
    if offset:
        out = [(a + offset, b + offset) for a, b in out]
    return out


def _marks_from_funasr(res: Any, offset: float = 0.0,
                       duration_hint: float = 0.0) -> List[Tuple[float, float]]:
    """Rút mốc từng ký tự từ output FunASR (ms -> giây)."""
    raw: List[Tuple[float, float]] = []
    for item in _iter_funasr_dicts(res):
        pairs = _timestamp_pairs(item.get("timestamp") or item.get("timestamps"))
        if len(pairs) < 2:
            continue
        scale = _guess_time_scale(pairs, duration_hint=duration_hint,
                                 default_ms=True)
        raw.extend((a * scale + offset, b * scale + offset) for a, b in pairs)
    return raw


def _funasr_lang_from_result(res: Any, fallback: Optional[str]) -> str:
    for item in _iter_funasr_dicts(res):
        lang = item.get("lang") or item.get("language")
        if lang:
            return str(lang)
    return fallback or "zh"


def _funasr_shape(obj: Any, depth: int = 0) -> str:
    if depth >= 3:
        return type(obj).__name__
    if isinstance(obj, dict):
        keys = list(obj.keys())
        head = ", ".join(map(str, keys[:10]))
        return f"dict(keys=[{head}]" + (", ..." if len(keys) > 10 else "") + ")"
    if isinstance(obj, (list, tuple)):
        if not obj:
            return f"{type(obj).__name__}(len=0)"
        return f"{type(obj).__name__}(len={len(obj)}, first={_funasr_shape(obj[0], depth + 1)})"
    if isinstance(obj, str):
        return f"str(len={len(obj)})"
    return type(obj).__name__


def _funasr_generate(model, audio_path: str) -> Any:
    return model.generate(
        input=audio_path,
        batch_size_s=150,             # giảm so với 300 để đỡ ngốn RAM với file dài
        batch_size_threshold_s=60,
        sentence_timestamp=True,      # <- thứ cho ra timestamp theo CÂU
        merge_vad=True,
        merge_length_s=15,
    )


def _extract_funasr_chunk(audio_path: str, out_path: str,
                          start: float, duration: float) -> None:
    run([
        "ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
        "-i", audio_path, "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", out_path,
    ], timeout=max(600, min(3600, int(duration * 0.5 + 300))))


def _asr_funasr_chunked(model, audio_path: str, language: Optional[str],
                        duration: float) -> Tuple[List[Segment], str]:
    chunk_s = float(_FUNASR_CHUNK_SECONDS)
    overlap = float(_FUNASR_CHUNK_OVERLAP_SECONDS)
    total = max(1, int(math.ceil(duration / chunk_s)))
    log(f"Audio dài {_fmt_hms(duration)} - FunASR sẽ chia {total} đoạn "
        f"{int(chunk_s // 60)} phút để tránh lỗi empty result.", "info")

    all_segs: List[Segment] = []
    all_marks: List[Tuple[float, float]] = []
    lang = language or "zh"
    last_shape = ""
    with tempfile.TemporaryDirectory(prefix="autodub_funasr_") as td:
        for idx in range(total):
            keep_start = idx * chunk_s
            keep_end = min(duration, keep_start + chunk_s)
            extract_start = max(0.0, keep_start - overlap)
            extract_end = min(duration, keep_end + overlap)
            extract_dur = max(0.1, extract_end - extract_start)
            chunk_path = os.path.join(td, f"chunk_{idx + 1:04d}.wav")

            log(f"  FunASR đoạn {idx + 1}/{total}: "
                f"{_fmt_hms(keep_start)} -> {_fmt_hms(keep_end)}", "info")
            _extract_funasr_chunk(audio_path, chunk_path, extract_start, extract_dur)
            res = _funasr_generate(model, chunk_path)
            last_shape = _funasr_shape(res)
            chunk_segs = _funasr_segments_from_result(
                res, offset=extract_start, duration_hint=extract_dur)
            lang = _funasr_lang_from_result(res, lang)

            all_marks.extend(
                (a, b) for a, b in _marks_from_funasr(
                    res, offset=extract_start, duration_hint=extract_dur)
                if keep_start - 0.05 <= (a + b) / 2.0
                and ((a + b) / 2.0 < keep_end - 0.05 or idx == total - 1))

            kept: List[Segment] = []
            for seg in chunk_segs:
                mid = (seg.start + seg.end) / 2.0
                if mid < keep_start - 0.05:
                    continue
                if idx < total - 1 and mid >= keep_end - 0.05:
                    continue
                kept.append(seg)
            all_segs.extend(kept)
            log(f"  FunASR đoạn {idx + 1}/{total}: nhận {len(kept)} dòng.", "ok")

    all_segs.sort(key=lambda s: (s.start, s.end))
    if not all_segs:
        raise RuntimeError(
            "FunASR trả kết quả rỗng cho cả các đoạn nhỏ. "
            f"Dạng output cuối: {last_shape or 'không rõ'}."
        )
    _set_last_marks(all_marks)
    return all_segs, lang


def _asr_funasr(audio_path: str, language: Optional[str], device: str,
                model_name: str = "paraformer-zh") -> Tuple[List[Segment], str]:
    # PHẢI vá TRƯỚC khi import funasr: funasr gọi modelscope ngay lúc nạp model,
    # mà modelscope_hub.HubConfig vỡ trên Python 3.10.0 -> tải model thất bại ->
    # báo nhầm thành "model 'paraformer-zh' is not registered".
    from . import compat
    compat.patch_modelscope_hubconfig()

    from funasr import AutoModel

    key = ("funasr", model_name, device)
    model = _MODEL_CACHE.get(key)
    if model is None:
        cached = _local_model_dir(model_name)
        log(f"Nạp FunASR '{model_name}' + fsmn-vad + ct-punc "
            + ("(dùng bản đã tải trong máy)" if cached else "(lần đầu sẽ tải model)")
            + " ...", "info")
        model = AutoModel(
            model=_resolve(model_name),
            vad_model=_resolve("fsmn-vad"),
            vad_kwargs={"max_single_segment_time": 30000},
            punc_model=_resolve("ct-punc"),
            device="cuda:0" if str(device).startswith("cuda") else "cpu",
            disable_update=True,
        )
        _MODEL_CACHE[key] = model

    duration = ffprobe_duration(audio_path)
    if duration > _FUNASR_DIRECT_LIMIT_SECONDS:
        return _asr_funasr_chunked(model, audio_path, language, duration)

    res = _funasr_generate(model, audio_path)
    segs = _funasr_segments_from_result(res, duration_hint=duration)
    if not segs:
        if duration > _FUNASR_CHUNK_SECONDS * 1.5:
            log("FunASR trả rỗng khi nhận nguyên file - thử chia nhỏ audio...",
                "warn")
            return _asr_funasr_chunked(model, audio_path, language, duration)
        raise RuntimeError(
            "FunASR không trả timestamp dùng được. "
            f"Dạng output: {_funasr_shape(res)}."
        )

    # Bẫy lỗi FSMN-VAD 1.3.9: trả về 1 đoạn khổng lồ ôm cả file
    if len(segs) == 1 and (segs[0].end - segs[0].start) > 120:
        log("FSMN-VAD trả về 1 đoạn khổng lồ - phiên bản funasr có thể lỗi "
            "(1.3.9). Hãy thử: python -m pip install funasr==1.3.1", "warn")

    _set_last_marks(_marks_from_funasr(res, duration_hint=duration))
    return segs, _funasr_lang_from_result(res, language)


# --------------------------------------------------------------------------- #
#  Backend 2: faster-whisper CẤU HÌNH ĐÚNG (đa ngôn ngữ, có lưới an toàn)
# --------------------------------------------------------------------------- #
def _asr_faster_whisper(audio_path: str, language: Optional[str], model_size: str,
                        device: str, compute_type: str,
                        beam_size: int = 5,
                        initial_prompt: Optional[str] = None) -> Tuple[List[Segment], str]:
    from faster_whisper import WhisperModel

    key = ("fw", model_size, device, compute_type)
    model = _MODEL_CACHE.get(key)
    if model is None:
        log(f"Nạp faster-whisper '{model_size}' ({device}/{compute_type}) ...", "info")
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        _MODEL_CACHE[key] = model

    seg_iter, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=beam_size,
        # Gợi ý từ vựng: tên nhân vật / cách gọi hay gặp. Whisper nghe tiếng
        # Việt rất dễ nhầm danh xưng Hán Việt ("tiểu soái ca" -> "tiểu xoái
        # ta"); mớm sẵn đúng chính tả thì nó bám theo.
        initial_prompt=initial_prompt or None,
        # LƯỚI AN TOÀN mà WhisperX không có: thử lại với temperature cao dần
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        compression_ratio_threshold=2.4,   # bắt vòng lặp lặp chữ
        log_prob_threshold=-1.0,           # bắt đoạn giải mã kém -> thử lại
        no_speech_threshold=0.6,
        condition_on_previous_text=False,  # chống trôi/bịa/nuốt đoạn
        vad_filter=True,
        vad_parameters={
            "threshold": 0.2,              # nhạy hơn mặc định -> ít bỏ sót
            "min_silence_duration_ms": 500,
            "speech_pad_ms": 400,          # ĐỆM THẬT quanh câu nói
        },
        word_timestamps=True,
    )

    segs: List[Segment] = []
    marks: List[Tuple[float, float]] = []
    for s in seg_iter:
        words = getattr(s, "words", None) or []
        start = words[0].start if words else s.start
        end = words[-1].end if words else s.end
        segs.append(Segment(0, float(start), float(end), s.text))
        for w in words:
            try:
                ws, we = float(w.start), float(w.end)
            except (TypeError, ValueError):
                continue
            if we > ws:
                marks.append((ws, we))
    _set_last_marks(marks)
    return segs, getattr(info, "language", language or "auto")


# --------------------------------------------------------------------------- #
#  Backend 3: SenseVoice (nhanh, nhưng timestamp thô)
# --------------------------------------------------------------------------- #
def _asr_sensevoice(audio_path: str, language: Optional[str],
                    device: str) -> Tuple[List[Segment], str]:
    return _asr_funasr(audio_path, language, device, model_name="iic/SenseVoiceSmall")


# --------------------------------------------------------------------------- #
#  Backend 4: WhisperX (giữ để tương thích - đã ép tham số an toàn)
# --------------------------------------------------------------------------- #
def _asr_whisperx(audio_path: str, language: Optional[str], model_size: str,
                  device: str, compute_type: str,
                  batch_size: int = 8) -> Tuple[List[Segment], str]:
    import whisperx

    log("WhisperX dễ MẤT ĐOẠN với video dài. Đang ép tham số an toàn "
        "(chunk_size=10, VAD silero). Khuyên dùng backend 'paraformer' "
        "(tiếng Trung) hoặc 'faster-whisper'.", "warn")

    key = ("whisperx", model_size, device, compute_type)
    model = _MODEL_CACHE.get(key)
    if model is None:
        model = whisperx.load_model(
            model_size, device, compute_type=compute_type, language=language,
            vad_method="silero",
            vad_options={"chunk_size": 10, "vad_onset": 0.15, "vad_offset": 0.1},
        )
        _MODEL_CACHE[key] = model

    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=batch_size, chunk_size=10)
    lang = result.get("language", language or "auto")
    segs = [Segment(0, float(s["start"]), float(s["end"]), s.get("text", ""))
            for s in result.get("segments", [])]
    return segs, lang


# --------------------------------------------------------------------------- #
#  Điều phối + căn chỉnh word-level (tùy chọn) + vá lỗ hổng
# --------------------------------------------------------------------------- #
def _dispatch(audio_path, backend, language, model_size, device, compute_type,
              batch_size, beam_size, initial_prompt=None) -> Tuple[List[Segment], str]:
    b = (backend or "").lower().replace("_", "-")
    if b in ("paraformer", "funasr"):
        return _asr_funasr(audio_path, language, device)
    if b == "sensevoice":
        return _asr_sensevoice(audio_path, language, device)
    if b == "faster-whisper":
        return _asr_faster_whisper(audio_path, language, model_size, device,
                                   compute_type, beam_size, initial_prompt)
    if b == "whisperx":
        return _asr_whisperx(audio_path, language, model_size, device,
                             compute_type, batch_size)
    raise ValueError(f"Backend ASR không hợp lệ: {backend!r} "
                     "(chọn: paraformer | faster-whisper | sensevoice | whisperx)")


def _rescue_gaps(audio_path: str, segs: List[Segment], duration: float,
                 backend: str, language: Optional[str], model_size: str,
                 device: str, compute_type: str, batch_size: int, beam_size: int,
                 min_gap: float, max_rounds: int, silence_db: float,
                 audio_gap_rescue: bool = True,
                  speech_gap_seconds: float = 1.2,
                  speech_silence_db: float = -42.0,
                  speech_min_silence: float = 0.35,
                  initial_prompt: Optional[str] = None,
                  marks_out: Optional[List[Tuple[float, float]]] = None,
                  ) -> List[Segment]:
    """Dò khoảng trống dài -> cắt riêng -> nhận lại -> ghép vào đúng mốc."""
    speech_ranges = None
    if audio_gap_rescue:
        try:
            speech_ranges = _nonsilent_ranges(
                audio_path, duration, speech_silence_db, speech_min_silence)
        except Exception as e:
            log(f"  Khong do duoc vung co tieng bang ffmpeg: {e}", "warn")
            speech_ranges = []

    with tempfile.TemporaryDirectory(prefix="autodub_gap_") as tmpdir:
        for rnd in range(1, max_rounds + 1):
            long_gaps = find_gaps(segs, duration, min_gap=min_gap)
            speech_holes: List[Tuple[float, float]] = []
            if audio_gap_rescue and speech_ranges:
                speech_holes = find_uncovered_speech_ranges(
                    segs, speech_ranges, duration, min_gap=speech_gap_seconds)
                if speech_holes:
                    speech_total = sum(e - s for s, e in speech_holes)
                    longest = max((e - s for s, e in speech_holes), default=0.0)
                    log(f"  Dò theo âm thanh: thấy {len(speech_holes)} vùng non-silent "
                        f"chưa có sub (tổng {speech_total/60:.1f} phút, "
                        f"dài nhất {longest:.1f}s).", "warn")

            gaps = merge_time_ranges(long_gaps + speech_holes,
                                     merge_gap=max(0.6, speech_gap_seconds * 0.5))
            if not gaps:
                msg = ("Không có lỗ hổng đáng kể - phụ đề phủ đều."
                       if rnd == 1 else "Không còn lỗ hổng đáng kể sau khi vá.")
                log(msg, "ok")
                break

            total_gap = sum(e - s for s, e in gaps)
            log(f"Vòng vá {rnd}: phát hiện {len(gaps)} vùng cần kiểm tra "
                f"(tổng {total_gap/60:.1f} phút). Đang nhận diện lại...", "step")

            added_total = 0
            for gi, (gs, ge) in enumerate(gaps, 1):
                if ge - gs < min_gap:
                    if not audio_gap_rescue or ge - gs < speech_gap_seconds:
                        continue
                piece = os.path.join(tmpdir, f"gap_{rnd}_{gi:04d}.wav")
                try:
                    _slice_audio(audio_path, gs, ge, piece)
                except Exception as e:
                    log(f"  Không cắt được đoạn {gs:.0f}-{ge:.0f}s: {e}", "warn")
                    continue

                vol = _mean_volume_db(piece)
                if vol <= silence_db:
                    log(f"  [{gs/60:.1f}-{ge/60:.1f}p] im lặng ({vol:.0f} dB) - bỏ qua.",
                        "info")
                    continue

                try:
                    sub_segs, _ = _dispatch(piece, backend, language, model_size,
                                            device, compute_type, batch_size, beam_size,
                                            initial_prompt)
                except Exception as e:
                    log(f"  Nhận lại đoạn {gs/60:.1f}p lỗi: {e}", "warn")
                    _take_last_marks()      # bỏ mốc của lượt lỗi
                    continue

                if marks_out is not None:   # mốc ký tự của đoạn vá, đã cộng offset
                    marks_out.extend(_take_last_marks(gs))
                else:
                    _take_last_marks()
                for s in sub_segs:          # đưa về mốc thời gian TUYỆT ĐỐI
                    s.start += gs
                    s.end += gs
                if sub_segs:
                    before = len(segs)
                    segs = merge_new_segments(segs, sub_segs)
                    added = max(0, len(segs) - before)
                    added_total += added
                    log(f"  [{gs/60:.1f}-{ge/60:.1f}p] vá thêm {added} dòng.", "ok")
                try:
                    os.remove(piece)
                except OSError:
                    pass

            if added_total == 0:
                log("Vòng vá không thêm được dòng nào từ các vùng ứng viên - "
                    "có thể là nhạc/nhiễu nền hoặc ASR đã phủ đủ thoại.", "warn")
                break
    return segs


def transcribe(
    audio_path: str,
    backend: str = "paraformer",
    language: Optional[str] = None,
    model_size: str = "large-v3",
    device: str = "cuda",
    compute_type: str = "float16",
    batch_size: int = 8,
    beam_size: int = 5,
    max_chars_per_line: int = 84,
    # chống mất đoạn
    rescue_gaps: bool = True,
    min_gap_seconds: float = 25.0,
    max_rescue_rounds: int = 2,
    silence_db: float = -45.0,
    audio_gap_rescue: bool = True,
    speech_gap_seconds: float = 1.2,
    speech_silence_db: float = -42.0,
    speech_min_silence: float = 0.35,
    min_coverage: float = 0.35,
    fallback_backend: Optional[str] = "faster-whisper",
    filter_hallucinations: bool = True,
    corrections=None,
    vocab_hint: Optional[str] = None,
) -> Tuple[List[Segment], str]:
    """Nhận diện phụ đề với 3 lớp chống mất đoạn. Trả về (segments, ngôn_ngữ)."""
    duration = ffprobe_duration(audio_path)
    initial_prompt = build_initial_prompt(corrections, vocab_hint)
    if initial_prompt:
        log(f"Mớm từ vựng cho ASR: {initial_prompt[:110]}"
            + ("..." if len(initial_prompt) > 110 else ""), "info")

    def _try(bk: str, dev: str, ctype: str):
        return _dispatch(audio_path, bk, language, model_size, dev, ctype,
                         batch_size, beam_size, initial_prompt)

    # --- Lớp 1: chạy engine chính, tự hạ cấp CPU nếu GPU lỗi ---
    active_backend = backend
    active_device = device
    active_compute_type = compute_type
    speechmap.clear_active()
    _take_last_marks()                       # xoá mốc còn sót của lần chạy trước
    try:
        segs, lang = _try(backend, device, compute_type)
    except Exception as e:
        log(f"Backend '{backend}' lỗi trên {device} ({e}).", "warn")
        primary_error = e
        recovered = False
        if not str(device or "").lower().startswith("cpu"):
            try:
                log("Thử lại bằng CPU...", "info")
                segs, lang = _try(backend, "cpu", "int8")
                active_device = "cpu"
                active_compute_type = "int8"
                recovered = True
            except Exception as e2:
                primary_error = e2
        if not recovered:
            if not fallback_backend or fallback_backend == backend:
                raise primary_error
            log(f"Chuyển sang backend dự phòng '{fallback_backend}' "
                f"vì backend chính vẫn lỗi ({primary_error}).", "warn")
            try:
                segs, lang = _try(fallback_backend, "cpu", "int8")
                active_backend = fallback_backend
                active_device = "cpu"
                active_compute_type = "int8"
            except Exception:
                segs, lang = _try(fallback_backend, device, compute_type)
                active_backend = fallback_backend

    # Bản đồ thoại phải dựng NGAY, trước normalize_segments: chính hàm đó đã
    # chia/gộp dòng theo tỉ lệ ký tự nên cần mốc thật để chia đúng chỗ.
    marks = _take_last_marks()
    if marks:
        speechmap.set_active(speechmap.SpeechMap(marks))

    max_chars = _max_chars_for(lang, max_chars_per_line)
    segs = normalize_segments(segs, max_chars)

    # --- Lớp 2: kiểm tra độ phủ ---
    rep = coverage_report(segs, duration)
    log(f"Độ phủ: {rep['lines']} dòng | có lời {rep['covered_s']/60:.1f}p "
        f"/ {rep['duration_s']/60:.1f}p ({rep['ratio']*100:.1f}%) | "
        f"dòng cuối ở {rep['last_end_s']/60:.1f}p", "info")

    # --- Lớp 3: vá lỗ hổng ---
    if rescue_gaps and duration > 0:
        segs = _rescue_gaps(audio_path, segs, duration, active_backend, language,
                            model_size, active_device, active_compute_type, batch_size,
                            beam_size, min_gap_seconds, max_rescue_rounds,
                            silence_db, audio_gap_rescue, speech_gap_seconds,
                            speech_silence_db, speech_min_silence, initial_prompt,
                            marks_out=marks)
        if marks:
            speechmap.set_active(speechmap.SpeechMap(marks))
        segs = normalize_segments(segs, max_chars)
        rep = coverage_report(segs, duration)
        log(f"Sau khi vá: {rep['lines']} dòng | phủ {rep['ratio']*100:.1f}% | "
            f"dòng cuối ở {rep['last_end_s']/60:.1f}p", "ok")

    # --- Lớp 4: bỏ câu bịa (Whisper "điền" quảng cáo kênh vào đoạn nhạc) ---
    if filter_hallucinations:
        segs, removed = drop_hallucinations(segs)
        if removed:
            log(f"Đã bỏ {len(removed)} dòng nghi là câu BỊA (đoạn nhạc/im lặng):",
                "warn")
            for line in removed[:5]:
                print(f"      - {line}")
            if len(removed) > 5:
                print(f"      ... và {len(removed) - 5} dòng nữa")
            rep = coverage_report(segs, duration)

    # --- Lớp 5: sửa các lỗi nghe nhầm lặp lại (bảng trong config.yaml) ---
    if corrections:
        nfix = apply_corrections(segs, corrections)
        if nfix:
            log(f"Đã sửa {nfix} dòng theo bảng asr.corrections.", "ok")

    # Cảnh báo to nếu vẫn thiếu nghiêm trọng
    if duration > 0 and rep["tail_ratio"] < 0.9:
        log(f"CẢNH BÁO: dòng cuối chỉ ở {rep['last_end_s']/60:.1f} phút trong khi "
            f"video dài {rep['duration_s']/60:.1f} phút - phụ đề CÓ THỂ BỊ CẮT CỤT. "
            "Hãy kiểm tra file .src.srt trước khi lồng tiếng.", "err")
    elif duration > 0 and rep["ratio"] < min_coverage:
        log(f"CẢNH BÁO: chỉ {rep['ratio']*100:.1f}% thời lượng có lời. Nếu video "
            "thoại liên tục thì đây là dấu hiệu MẤT ĐOẠN - thử đổi "
            "asr.backend sang 'faster-whisper' hoặc 'paraformer'.", "warn")

    sm = speechmap.get_active()
    if sm is not None and not sm.empty:
        log(f"Bản đồ thoại: {len(sm)} mốc thời gian ký tự - dùng để chia lại phụ "
            "đề đúng lúc nhân vật nói (không nội suy qua quãng lặng).", "ok")
    else:
        log("Backend này không trả mốc thời gian từng ký tự - các bước chia lại "
            "phụ đề sẽ phải chia theo tỉ lệ, dễ lệch tiếng/hình hơn.", "warn")

    log(f"Hoàn tất: {len(segs)} dòng phụ đề (backend={backend}, lang={lang}).", "ok")
    return segs, lang
