"""Lớp phủ trên video: vùng làm mờ, xoá logo, chèn logo, và phụ đề tiếng Việt (.ass).

Mọi toạ độ dùng ĐƠN VỊ PIXEL CỦA VIDEO GỐC (ví dụ 1280x720), không phải pixel
trên màn hình xem trước. Giao diện web tự quy đổi khi hiển thị.

Một "vùng" (region) là dict:
    {"x": 100, "y": 600, "w": 1080, "h": 90, "type": "blur"|"delogo",
     "strength": 20}

Phụ đề tiếng Việt được sinh ra file .ass để kiểm soát chính xác font, cỡ chữ,
màu, viền và VỊ TRÍ (theo hộp kéo-thả trên giao diện) - hơn hẳn dùng .srt.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

from .srt_utils import Segment


# --------------------------------------------------------------------------- #
#  Màu: giao diện gửi "#RRGGBB", ASS cần "&HAABBGGRR" (đảo BGR, AA=độ trong)
# --------------------------------------------------------------------------- #
def hex_to_ass_color(hex_color: str, alpha: int = 0) -> str:
    s = (hex_color or "#FFFFFF").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        s = "FFFFFF"
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        r = g = b = 255
    a = max(0, min(255, int(alpha)))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def _ass_time(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    cs_total = int(round(sec * 100))
    h, rem = divmod(cs_total, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    """Thoát ký tự đặc biệt và đổi xuống dòng thành \\N của ASS."""
    t = (text or "").replace("\\", "\\\\")
    t = t.replace("{", "\\{").replace("}", "\\}")
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\N")
    return t


# Dấu kết câu mạnh — luôn tách tại đây (nhưng KHÔNG tách giữa chuỗi ... )
_DISPLAY_SPLIT_RE = re.compile(r"(?<=[.!?。！？…])(?![.!?。！？…])\s*")

# Dấu phẩy / chấm phẩy / hai chấm / ngoặc kép đóng / gạch ngang dài — chỉ
# tách khi cụm đang dài hơn MAX_DISPLAY_CHARS ký tự (tránh vụn quá)
_SOFT_SPLIT_RE = re.compile(
    r'(?<=[,;:，；：])\s*'        # sau dấu phẩy / chấm phẩy / hai chấm
    r'|(?<=["\u201D\u300D])\s*'   # sau ngoặc kép đóng " 」
    r'|(?<=\s)[–—]\s*'            # trước/sau gạch ngang dài
)

# Số ký tự tối đa cho 1 cụm hiển thị trước khi bị tách nhỏ thêm
MAX_DISPLAY_CHARS = 40

# Cụm ngắn hơn số này sẽ bị gom vào cụm kề để tránh sub chớp nháy
MIN_DISPLAY_CHARS = 18

# Số ký tự tối đa trên 1 DÒNG hiển thị (trước khi xuống dòng \N trong ASS)
MAX_LINE_CHARS = 38

# Dọn dấu ngoặc kép / ngoặc đơn bị lẻ ở đầu hoặc cuối sau khi tách
_STRAY_QUOTE_RE = re.compile(r'^[\s""\u201C\u201D\u300C\u300D\']+|[\s""\u201C\u201D\u300C\u300D\']+$')


def _clean_quotes(text: str) -> str:
    """Bỏ dấu ngoặc kép / ngoặc đơn lẻ ở đầu cuối do tách cụm."""
    cleaned = _STRAY_QUOTE_RE.sub("", text).strip()
    return cleaned if cleaned else text.strip()


def _has_display_text(text: str) -> bool:
    """True if the chunk contains letters, numbers, or CJK characters."""
    for ch in text or "":
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff":
            return True
    return False


def split_display_sentences(text: str) -> List[str]:
    """Tách phụ đề để HIỂN THỊ theo câu/cụm, không làm đổi số dòng SRT/TTS gốc.

    Bước 1: Tách theo dấu kết câu mạnh (. ! ? 。 ！ ？ …)
    Bước 2: Cụm nào vẫn quá dài → tách tiếp ở dấu phẩy, ngoặc kép, gạch ngang
    Bước 3: Gom cụm quá ngắn vào cụm kề, dọn dấu ngoặc lẻ
    """
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return []
    # Bước 1: tách ở dấu kết câu mạnh
    rough = [p.strip() for p in _DISPLAY_SPLIT_RE.split(raw) if p.strip()]
    if not rough:
        rough = [raw]

    # Bước 2: tách tiếp cụm dài ở điểm ngắt mềm
    expanded: List[str] = []
    for chunk in rough:
        if len(chunk) <= MAX_DISPLAY_CHARS:
            expanded.append(chunk)
            continue
        # Thử tách ở dấu phẩy, ngoặc kép, gạch ngang
        subs = [s.strip() for s in _SOFT_SPLIT_RE.split(chunk) if s.strip()]
        if len(subs) <= 1:
            expanded.append(chunk)
            continue
        expanded.extend(subs)

    # Bước 3: gom cụm quá ngắn vào cụm kề (pass lùi: gom ngắn vào trước)
    merged: List[str] = []
    buf = ""
    for s in expanded:
        if buf and len(buf) + 1 + len(s) <= MAX_DISPLAY_CHARS:
            buf = buf + " " + s
        elif buf and len(buf) < MIN_DISPLAY_CHARS:
            buf = buf + " " + s
        else:
            if buf:
                merged.append(buf)
            buf = s
    if buf:
        merged.append(buf)

    # Pass tới: gom cụm ngắn ở ĐẦU vào cụm KẾ TIẾP
    parts: List[str] = []
    i = 0
    while i < len(merged):
        chunk = merged[i]
        if (len(chunk) < MIN_DISPLAY_CHARS and i + 1 < len(merged)
                and len(chunk) + 1 + len(merged[i + 1]) <= MAX_DISPLAY_CHARS + 10):
            parts.append(_clean_quotes(chunk + " " + merged[i + 1]))
            i += 2
        else:
            parts.append(_clean_quotes(chunk))
            i += 1

    # Lọc bỏ cụm rỗng hoặc chỉ còn dấu câu sau khi dọn quotes
    parts = [p for p in parts if p and _has_display_text(p)]
    return parts or [raw]


def _wrap_long_line(text: str, max_chars: int = MAX_LINE_CHARS,
                    single_line: bool = False) -> str:
    """Xuống dòng thông minh cho 1 cụm hiển thị nếu vẫn quá dài.

    Trả về text với \\N (ký tự xuống dòng của ASS) chèn ở vị trí khoảng trắng
    gần nhất, ưu tiên ngắt SAU dấu phẩy/chấm phẩy.
    """
    if single_line:
        return text.replace("\\N", " ")
    if len(text) <= max_chars:
        return text
    words = text.split()
    lines: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for w in words:
        test_len = cur_len + (1 if cur else 0) + len(w)
        if cur and test_len > max_chars:
            lines.append(" ".join(cur))
            cur = [w]
            cur_len = len(w)
        else:
            cur.append(w)
            cur_len = test_len
    if cur:
        lines.append(" ".join(cur))
    return "\\N".join(lines)


def _sentence_events(start: float, end: float, text: str) -> List[Tuple[float, float, str]]:
    parts = split_display_sentences(text)
    if not parts:
        return []
    if len(parts) == 1:
        return [(start, end, parts[0])]

    span = max(0.01, end - start)
    weights = [max(1, len(p)) for p in parts]
    total = float(sum(weights))
    events: List[Tuple[float, float, str]] = []
    cur = start
    used = 0
    for i, part in enumerate(parts):
        used += weights[i]
        if i == len(parts) - 1:
            nxt = end
        else:
            nxt = start + span * used / total
            if nxt <= cur:
                nxt = min(end, cur + 0.01)
        ev_end = nxt
        if ev_end <= cur:
            ev_end = min(end, cur + 0.01)
        events.append((cur, ev_end, part))
        cur = nxt
    return events


def _style_float(st: Dict, key: str, default: float,
                 lo: float, hi: float) -> float:
    try:
        val = float(st.get(key, default))
    except (TypeError, ValueError):
        val = default
    return max(lo, min(hi, val))


def _style_int(st: Dict, key: str, default: int,
               lo: int, hi: int) -> int:
    try:
        val = int(float(st.get(key, default)))
    except (TypeError, ValueError):
        val = default
    return max(lo, min(hi, val))


def _display_window(seg: Segment, start: float, end: float,
                    text: str, st: Dict) -> Tuple[float, float]:
    """Giới hạn thời gian subtitle để một câu ngắn không treo suốt khoảng im lặng."""
    if end <= start:
        end = start + 0.4

    min_dur = _style_float(st, "min_duration", 0.9, 0.2, 5.0)
    max_dur = _style_float(st, "max_duration", 6.5, min_dur, 30.0)
    cps = _style_float(st, "read_cps", 14.0, 6.0, 40.0)
    pad = _style_float(st, "tail_pad", 0.35, 0.0, 3.0)

    voice_dur = getattr(seg, "voice_duration", None)
    try:
        voice_dur = float(voice_dur) if voice_dur is not None else 0.0
    except (TypeError, ValueError):
        voice_dur = 0.0

    if voice_dur > 0.01:
        # Khi đã có TTS, phụ đề nên tắt ngay sau voice. Không áp max_duration
        # vào nhánh này vì voice dài thật mà phụ đề biến mất giữa câu còn tệ hơn.
        wanted = max(min_dur, voice_dur + pad)
        return start, start + wanted

    readable = len(re.sub(r"\s+", "", text or "")) / cps + pad
    wanted = max(min_dur, min(max_dur, readable))
    return start, min(end, start + wanted)


# Vị trí neo theo lưới 3x3 của giao diện -> mã alignment của ASS
# ASS: 1=trái-dưới 2=giữa-dưới 3=phải-dưới / 4,5,6=giữa / 7,8,9=trên
GRID_TO_ALIGN = {
    "top-left": 7, "top-center": 8, "top-right": 9,
    "mid-left": 4, "mid-center": 5, "mid-right": 6,
    "bottom-left": 1, "bottom-center": 2, "bottom-right": 3,
}

DEFAULT_SUB_STYLE = {
    "font": "Be Vietnam Pro",
    "size": 30,
    "color": "#FFFFFF",
    "outline_color": "#000000",
    "outline": 2,
    "shadow": 1,
    "bold": False,
    "italic": False,
    "align": "mid-center",
    # Hộp phụ đề (kéo-thả trên giao diện), toạ độ pixel video:
    "box": None,          # {"x":..,"y":..,"w":..,"h":..} hoặc None = dùng canh mặc định
    "margin_v": 40,
    "min_duration": 0.9,
    "max_duration": 6.5,
    "read_cps": 14.0,
    "tail_pad": 0.35,
    "animation": "fade_pop",       # none | fade | fade_pop
    "single_line": True,
    "fade_in_ms": 80,
    "fade_out_ms": 60,
    "min_gap_between_lines": 0.03,
}


def _single_line_scale_x(text: str, st: Dict, video_w: int) -> int:
    if not st.get("single_line", True):
        return 100
    try:
        size = float(st.get("size", 30) or 30)
    except (TypeError, ValueError):
        size = 30.0
    box = st.get("box")
    if isinstance(box, dict) and box.get("w"):
        available = max(120.0, float(box.get("w", video_w)))
    else:
        available = max(120.0, float(video_w) * 0.96)
    plain = re.sub(r"\\[A-Za-z0-9]+", "", text or "")
    estimated = max(1.0, len(plain) * size * 0.56)
    if estimated <= available:
        return 100
    return max(55, min(100, int(available / estimated * 100)))


def _subtitle_effect_tag(st: Dict, duration: float, scale_x: int = 100) -> str:
    mode = str(st.get("animation", "fade_pop") or "none").strip().lower()
    if mode in {"", "none", "off", "false", "0"}:
        return ""

    dur_ms = max(1, int(round(max(0.0, duration) * 1000)))
    fade_in = min(_style_int(st, "fade_in_ms", 80, 0, 400), max(0, dur_ms // 3))
    fade_out = min(_style_int(st, "fade_out_ms", 60, 0, 400), max(0, dur_ms // 4))
    if fade_in + fade_out > dur_ms - 20:
        fade_out = max(0, dur_ms - 20 - fade_in)

    tags = []
    if fade_in or fade_out:
        tags.append(f"\\fad({fade_in},{fade_out})")
    if scale_x != 100:
        tags.append(f"\\fscx{scale_x}\\fscy100")
    if mode in {"pop", "fade_pop", "smooth", "zoom"} and dur_ms >= 140:
        t_end = min(140, max(60, dur_ms // 3))
        start_x = max(1, int(round(scale_x * 0.96)))
        tags.append(f"\\fscx{start_x}\\fscy96\\t(0,{t_end},\\fscx{scale_x}\\fscy100)")
    return "".join(tags)


def _clamp_event_overlaps(events: List[Tuple[float, float, str]],
                          st: Dict) -> List[Tuple[float, float, str]]:
    """Keep close subtitle events from drawing on top of each other."""
    if len(events) <= 1:
        return events
    gap = _style_float(st, "min_gap_between_lines", 0.03, 0.0, 0.25)
    ordered = sorted(events, key=lambda e: (e[0], e[1]))
    out: List[Tuple[float, float, str]] = []
    for idx, (start, end, text) in enumerate(ordered):
        if idx + 1 < len(ordered):
            next_start = ordered[idx + 1][0]
            if end > next_start - gap:
                end = max(start, next_start - gap)
        if end - start >= 0.05:
            out.append((start, end, text))
    return out


def build_ass(
    segments: Sequence[Segment],
    video_w: int,
    video_h: int,
    style: Optional[Dict] = None,
    use_placed: bool = False,
) -> str:
    """Sinh nội dung file .ass cho phụ đề tiếng Việt."""
    st = dict(DEFAULT_SUB_STYLE)
    if style:
        st.update({k: v for k, v in style.items() if v is not None})

    align = GRID_TO_ALIGN.get(str(st.get("align", "mid-center")), 5)
    primary = hex_to_ass_color(st.get("color", "#FFFFFF"))
    outline_c = hex_to_ass_color(st.get("outline_color", "#000000"))
    bold = -1 if st.get("bold") else 0
    italic = -1 if st.get("italic") else 0
    size = int(st.get("size", 30) or 30)
    outline = float(st.get("outline", 2) or 0)
    shadow = float(st.get("shadow", 1) or 0)
    margin_v = int(st.get("margin_v", st.get("margin_bottom", 40)) or 0)

    head = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {int(video_w)}\n"
        f"PlayResY: {int(video_h)}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{st.get('font','Be Vietnam Pro')},{size},{primary},"
        f"&H000000FF,{outline_c},&H80000000,{bold},{italic},0,0,100,100,0,0,1,"
        f"{outline:g},{shadow:g},{align},20,20,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )

    # Nếu người dùng đã kéo hộp phụ đề -> dùng \pos để đặt đúng chỗ đó
    pos_tag = ""
    box = st.get("box")
    if isinstance(box, dict) and box.get("w"):
        bx, by = float(box.get("x", 0)), float(box.get("y", 0))
        bw, bh = float(box.get("w", 0)), float(box.get("h", 0))
        cx = bx + bw / 2.0
        if align in (1, 2, 3):        # neo đáy
            py = by + bh
        elif align in (4, 5, 6):      # neo giữa
            py = by + bh / 2.0
        else:                          # neo trên
            py = by
        if align in (1, 4, 7):
            px = bx
        elif align in (3, 6, 9):
            px = bx + bw
        else:
            px = cx
        pos_tag = f"\\pos({px:.0f},{py:.0f})"

    events: List[Tuple[float, float, str]] = []
    for seg in segments:
        if use_placed and seg.placed_start is not None:
            start = seg.placed_start
            voice_dur = getattr(seg, "voice_duration", None)
            try:
                voice_dur = float(voice_dur) if voice_dur is not None else 0.0
            except (TypeError, ValueError):
                voice_dur = 0.0
            end = seg.placed_start + max(0.3, seg.duration, voice_dur)
        else:
            start, end = seg.start, seg.end
        start, end = _display_window(seg, start, end, seg.text or "", st)
        for ev_start, ev_end, ev_text in _sentence_events(start, end, seg.text or ""):
            events.append((ev_start, ev_end, ev_text))

    lines = []
    for ev_start, ev_end, ev_text in _clamp_event_overlaps(events, st):
            text = _ass_escape(ev_text)
            if not text.strip():
                continue
            # Xuống dòng thông minh nếu text quá dài cho 1 dòng hiển thị
            single_line = bool(st.get("single_line", True))
            text = _wrap_long_line(text, single_line=single_line)
            scale_x = _single_line_scale_x(ev_text, st, video_w) if single_line else 100
            tags = ("\\q2" if single_line else "") + pos_tag + _subtitle_effect_tag(
                st, ev_end - ev_start, scale_x=scale_x)
            prefix = f"{{{tags}}}" if tags else ""
            lines.append(
                f"Dialogue: 0,{_ass_time(ev_start)},{_ass_time(ev_end)},Default,,0,0,0,,"
                f"{prefix}{text}"
            )
    return head + "\n".join(lines) + "\n"


def save_ass(path: str, segments: Sequence[Segment], video_w: int, video_h: int,
             style: Optional[Dict] = None, use_placed: bool = False) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_ass(segments, video_w, video_h, style, use_placed))
    return path


# --------------------------------------------------------------------------- #
#  Dựng chuỗi filter ffmpeg cho các vùng phủ
# --------------------------------------------------------------------------- #
# Filter delogo của ffmpeg đoán màu vùng logo bằng cách nội suy từ VIỀN NGAY
# NGOÀI vùng đó, nên vùng phải chừa ít nhất 1px mỗi phía. Chạm mép khung hình
# (x=0, hay x+w = chiều rộng video) là nó bỏ luôn cả lệnh render:
# "Logo area is outside of the frame" -> "Error reinitializing filters".
DELOGO_INSET = 1


def _clamp_region(r: Dict, vw: int, vh: int, inset: int = 0) -> Optional[Dict]:
    """Ép vùng nằm gọn trong khung hình; bỏ vùng quá nhỏ. Kích thước chẵn.

    inset > 0 thì chừa thêm bấy nhiêu pixel viền quanh khung (delogo cần).
    """
    try:
        x = int(round(float(r.get("x", 0))))
        y = int(round(float(r.get("y", 0))))
        w = int(round(float(r.get("w", 0))))
        h = int(round(float(r.get("h", 0))))
    except (TypeError, ValueError):
        return None
    m = max(0, int(inset))
    x = max(m, min(x, max(m, vw - m - 2)))
    y = max(m, min(y, max(m, vh - m - 2)))
    w = min(w, vw - m - x)
    h = min(h, vh - m - y)
    w -= w % 2
    h -= h % 2
    if w < 8 or h < 8:
        return None
    out = dict(r)
    out.update({"x": x, "y": y, "w": w, "h": h})
    return out


def clamp_delogo(r: Dict, vw: int, vh: int) -> Optional[Dict]:
    """Ép vùng xoá logo vào trong khung, chừa viền theo yêu cầu của ffmpeg."""
    return _clamp_region(r, vw, vh, inset=DELOGO_INSET)


def _region_enable_expr(r: Dict) -> str:
    """Return an FFmpeg enable expression for an optional region time window."""
    def _num(name: str) -> Optional[float]:
        v = r.get(name)
        if v in (None, ""):
            return None
        try:
            return max(0.0, float(v))
        except (TypeError, ValueError):
            return None

    start = _num("start")
    end = _num("end")
    if start is None and end is None:
        return ""
    if end is not None and start is not None and end <= start:
        return ""
    if start is not None and end is not None:
        return f":enable='between(t,{start:.3f},{end:.3f})'"
    if start is not None:
        return f":enable='gte(t,{start:.3f})'"
    return f":enable='lte(t,{end:.3f})'"


def _logo_motion_expr(logo: Dict) -> tuple[str, str, str]:
    """Return FFmpeg overlay x/y expressions for a slow drifting logo."""
    motion = logo.get("motion", True)
    if isinstance(motion, str):
        motion = motion.strip().lower() not in {"0", "false", "off", "none", "static"}
    if not motion:
        return str(int(float(logo.get("x", 0) or 0))), str(int(float(logo.get("y", 0) or 0))), ""

    try:
        speed = float(logo.get("motion_speed", 1.0) or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    speed = max(0.2, min(4.0, speed))
    x_period = 47.0 / speed
    y_period = 61.0 / speed
    # W/H/w/h are FFmpeg overlay variables. Different periods keep the path
    # drifting instead of bouncing along a short diagonal loop.
    x_expr = f"(W-w)*(0.5+0.5*sin(t/{x_period:.4g}))"
    y_expr = f"(H-h)*(0.5+0.5*sin(t/{y_period:.4g}+1.5708))"
    return x_expr, y_expr, ":eval=frame"


def build_overlay_filters(
    regions: Sequence[Dict],
    video_w: int,
    video_h: int,
    ass_path: Optional[str] = None,
    logo_input_index: Optional[int] = None,
    logo: Optional[Dict] = None,
    src_label: str = "0:v",
) -> tuple[List[str], str]:
    """Trả về (danh sách filter, nhãn luồng video cuối).

    Thứ tự xử lý (đúng như 3 lớp trên giao diện):
      Lớp 1: video gốc (có sub tiếng Trung cháy cứng)
      Lớp 2: các vùng LÀM MỜ / XOÁ LOGO đè lên
      Lớp 3: phụ đề tiếng Việt (.ass) vẽ trên cùng, + logo nếu có
    """
    filters: List[str] = []
    cur = src_label
    n = 0

    for raw in regions or []:
        kind = str((raw or {}).get("type", "blur")).lower()
        # delogo cần chừa viền 1px, blur thì phủ sát mép được.
        r = _clamp_region(raw, video_w, video_h,
                          inset=DELOGO_INSET if kind == "delogo" else 0)
        if not r:
            continue
        x, y, w, h = r["x"], r["y"], r["w"], r["h"]

        if kind == "delogo":
            out = f"v{n}"
            filters.append(f"[{cur}]delogo=x={x}:y={y}:w={w}:h={h}{_region_enable_expr(r)}[{out}]")
            cur = out
            n += 1
        else:  # blur
            do_mo = int(round(max(1.0, min(80.0, float(r.get("strength", 20) or 20)))))
            # Phải split luồng thành 2 bản sao: một để crop/blur, một để overlay.
            # Nếu dùng [{cur}] 2 lần (vừa crop vừa overlay) FFmpeg báo lỗi và
            # chỉ vùng đầu tiên được áp — đây là nguyên nhân bug nhiều vùng chỉ hiện 1.
            # avgblur chứ không phải gblur: mắt thường không phân biệt được
            # (SSIM 0.99) nhưng rẻ hơn khoảng 3,8 lần, mà mỗi vùng che là một
            # lần lọc nên người đặt ba bốn vùng sẽ thấy khác biệt rõ.
            base, crop_in, bl, out = f"base{n}", f"cr{n}", f"b{n}", f"v{n}"
            filters.append(
                f"[{cur}]split[{base}][{crop_in}];"
                f"[{crop_in}]crop={w}:{h}:{x}:{y},avgblur={do_mo}[{bl}];"
                f"[{base}][{bl}]overlay={x}:{y}{_region_enable_expr(r)}[{out}]"
            )
            cur = out
            n += 1

    if logo and logo_input_index is not None:
        lr = _clamp_region(logo, video_w, video_h)
        if lr:
            sc, out = f"lg{n}", f"v{n}"
            op = float(logo.get("opacity", 1.0) or 1.0)
            op = max(0.05, min(1.0, op))
            ox, oy, eval_arg = _logo_motion_expr({**logo, **lr})
            filters.append(
                f"[{logo_input_index}:v]scale={lr['w']}:{lr['h']},"
                f"format=rgba,colorchannelmixer=aa={op:g}[{sc}];"
                f"[{cur}][{sc}]overlay=x='{ox}':y='{oy}'{eval_arg}[{out}]"
            )
            cur = out
            n += 1

    if ass_path:
        esc = ass_path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        out = f"v{n}"
        filters.append(f"[{cur}]ass='{esc}'[{out}]")
        cur = out
        n += 1

    return filters, cur


def suggest_subtitle_band(video_w: int, video_h: int,
                          ratio: float = 0.16) -> Dict:
    """Gợi ý vùng che mặc định: dải đáy màn hình (nơi sub gốc hay nằm)."""
    h = int(video_h * max(0.04, min(0.5, ratio)))
    h -= h % 2
    return {"x": 0, "y": max(0, video_h - h), "w": video_w, "h": h,
            "type": "blur", "strength": 20}
