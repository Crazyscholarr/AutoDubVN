"""Tách chữ overlay và vẽ thumbnail 16:9."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .const import (
    LINE1_COLOR, LINE2_COLOR, STROKE_COLOR,
    THUMB_H, THUMB_W, YEAR_RE,
)

def _upcase_vi(text: str) -> str:
    return str(text or "").strip().upper()


def split_thumbnail_lines(text: str, title: str = "") -> Tuple[str, str]:
    """Tách chữ overlay thành 2 dòng cực lớn, ưu tiên cụm năm."""
    raw = str(text or "").strip()
    extra = str(title or "").strip()
    if not raw:
        raw = extra
    parts = [p.strip() for p in re.split(r"[\n|/]+", raw) if p.strip()]
    if len(parts) >= 2:
        return _upcase_vi(parts[0]), _upcase_vi(parts[1])

    blob = "%s %s" % (raw, extra)
    year = YEAR_RE.search(blob)
    year_line = ""
    if year:
        year_line = "%s %s" % (year.group(1), year.group(2).upper().replace("NAM", "NĂM")
                               .replace("THANG", "THÁNG").replace("NGAY", "NGÀY"))
    body = YEAR_RE.sub(" ", raw).strip(" -–—,.")
    words = [w for w in re.split(r"\s+", body) if w]
    if year_line and words:
        if len(words) <= 2:
            return _upcase_vi(" ".join(words)), year_line
        return _upcase_vi(" ".join(words[:2])), _upcase_vi(" ".join(words[2:]) + " " + year_line)
    if len(words) >= 4:
        return _upcase_vi(" ".join(words[:2])), _upcase_vi(" ".join(words[2:]))
    if len(words) == 3:
        return _upcase_vi(words[0]), _upcase_vi(" ".join(words[1:]))
    if year_line:
        return _upcase_vi(" ".join(words) or extra), year_line
    return _upcase_vi(" ".join(words) or raw), ""


def overlay_lines_from_idea(title: str, idea: Optional[Dict] = None) -> Tuple[str, str]:
    idea = idea if isinstance(idea, dict) else {}
    thumbs = idea.get("thumbnails") or []
    text = ""
    if thumbs and isinstance(thumbs[0], dict):
        text = str(thumbs[0].get("text") or "").strip()
    if not text and thumbs and isinstance(thumbs[0], str):
        text = thumbs[0].strip()
    return split_thumbnail_lines(text or title, title)


def _windows_fonts() -> List[str]:
    """Ưu tiên font có dấu tiếng Việt; Arial Black hay thiếu Ổ/Ă/Ê."""
    windir = os.environ.get("WINDIR", r"C:\Windows")
    fonts = Path(windir) / "Fonts"
    names = (
        "tahomabd.ttf", "arialbd.ttf", "seguibl.ttf", "calibrib.ttf",
        "verdanab.ttf", "arial.ttf", "tahoma.ttf", "ariblk.ttf", "impact.ttf",
    )
    return [str(fonts / name) for name in names if (fonts / name).is_file()]


def _font_renders_viet(path: str, size: int = 48) -> bool:
    from PIL import ImageFont
    try:
        font = ImageFont.truetype(path, size)
    except Exception:
        return False
    if not hasattr(font, "getlength"):
        return True
    try:
        return font.getlength("ĐỔ") > font.getlength("Đ") + 2 and font.getlength("ĂÂ") > 8
    except Exception:
        return False


def _load_font(size: int):
    from PIL import ImageFont
    for path in _windows_fonts():
        if not _font_renders_viet(path, size):
            continue
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_size(draw, text: str, font) -> Tuple[float, float]:
    if hasattr(draw, "textbbox"):
        box = draw.textbbox((0, 0), text, font=font, stroke_width=0)
        return float(box[2] - box[0]), float(box[3] - box[1])
    if hasattr(font, "getbbox"):
        box = font.getbbox(text)
        return float(box[2] - box[0]), float(box[3] - box[1])
    return float(len(text) * 18), 28.0


def _cover_16x9(img, width: int, height: int):
    """Phóng vừa khung, cắt thừa lệch trái để giữ nhân vật bên phải."""
    src_w, src_h = img.size
    if src_w <= 0 or src_h <= 0:
        return img.resize((width, height))
    scale = max(width / src_w, height / src_h)
    new_w, new_h = max(width, int(src_w * scale)), max(height, int(src_h * scale))
    resized = img.resize((new_w, new_h))
    left = max(0, new_w - width)  # cắt bên trái
    top = max(0, (new_h - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _draw_outlined(draw, xy, text, font, fill, stroke_w: int) -> None:
    x, y = xy
    shadow = (6, 6)
    draw.text((x + shadow[0], y + shadow[1]), text, font=font, fill=(0, 0, 0, 170))
    try:
        draw.text((x, y), text, font=font, fill=fill,
                  stroke_width=stroke_w, stroke_fill=STROKE_COLOR)
        return
    except TypeError:
        pass
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1),
                   (-1, -1), (1, -1), (-1, 1), (1, 1)):
        draw.text((x + dx * stroke_w, y + dy * stroke_w), text,
                  font=font, fill=STROKE_COLOR)
    draw.text((x, y), text, font=font, fill=fill)


def draw_thumbnail_overlay(src_path: str | os.PathLike,
                           dest_path: str | os.PathLike,
                           line1: str, line2: str = "",
                           width: int = THUMB_W, height: int = THUMB_H) -> Path:
    """Vẽ 2 dòng chữ cực lớn vào nửa trái, xuất JPEG 16:9."""
    from PIL import Image, ImageDraw

    src = Path(src_path)
    dest = Path(dest_path)
    with Image.open(src) as raw:
        img = _cover_16x9(raw.convert("RGB"), width, height).convert("RGBA")
    dark = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(dark).rectangle(
        [0, 0, int(width * 0.52), height], fill=(0, 0, 0, 72))
    img = Image.alpha_composite(img, dark)
    canvas = img.convert("RGB")
    draw = ImageDraw.Draw(canvas)

    texts = [t for t in (_upcase_vi(line1), _upcase_vi(line2)) if t]
    if not texts:
        dest.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(dest, format="JPEG", quality=92)
        return dest

    box_w = int(width * 0.48)
    left = int(width * 0.03)
    chosen = []
    for size in range(132, 39, -4):
        font = _load_font(size)
        sizes = [_text_size(draw, line, font) for line in texts]
        if all(w <= box_w for w, _h in sizes):
            chosen = [(font, wh) for wh in sizes]
            break
    if not chosen:
        font = _load_font(42)
        chosen = [(font, _text_size(draw, line, font)) for line in texts]
    font0 = chosen[0][0]
    line_gap = max(32, int(getattr(font0, "size", 64) * 0.28))
    total_h = sum(h for _f, (_w, h) in chosen) + line_gap * (len(chosen) - 1)
    y = max(24, int((height - total_h) / 2))
    for idx, ((font, (_w, h)), line) in enumerate(zip(chosen, texts)):
        stroke = max(8, min(14, int(getattr(font, "size", 64) / 12)))
        color = LINE1_COLOR if idx == 0 else LINE2_COLOR
        x = left
        words = line.split() or [line]
        extra = max(16, int(getattr(font, "size", 64) * 0.18))
        for word in words:
            _draw_outlined(draw, (x, y), word, font, color, stroke)
            ww, _hh = _text_size(draw, word, font)
            x += int(ww + extra)
        y += int(h + line_gap)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, format="JPEG", quality=92)
    return dest
