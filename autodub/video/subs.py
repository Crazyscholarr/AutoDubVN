"""Kiểu phụ đề ASS và đường dẫn an toàn cho ffmpeg."""
from __future__ import annotations

import os
from typing import Optional

def _ass_color(value: str, default: str = "&H00FFFFFF") -> str:
    """Đổi "#RRGGBB" (cách viết quen thuộc) sang mã màu của ASS: &HAABBGGRR.

    ASS đảo thứ tự byte (BGR) nên viết thẳng mã hex web vào là ra SAI MÀU -
    đỏ hoá xanh dương. Hàm này lo phần đó.
    """
    v = str(value or "").strip().lstrip("#")
    if not v:
        return default
    if v.upper().startswith("&H"):
        return v
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        return default
    try:
        r, g, b = v[0:2], v[2:4], v[4:6]
        return f"&H00{b}{g}{r}".upper()
    except Exception:
        return default


def build_subtitle_style(style: Optional[dict] = None) -> str:
    """Dựng chuỗi force_style cho bộ lọc subtitles của ffmpeg."""
    st = dict(style or {})
    align_map = {
        "top-left": 7, "top-center": 8, "top-right": 9,
        "mid-left": 4, "mid-center": 5, "mid-right": 6,
        "bottom-left": 1, "bottom-center": 2, "bottom-right": 3,
    }
    align = align_map.get(str(st.get("align", "mid-center")), 5)
    parts = [
        f"FontName={st.get('font', 'Arial')}",
        f"FontSize={int(st.get('size', 22))}",
        f"PrimaryColour={_ass_color(st.get('color', '#FFFF00'))}",
        f"OutlineColour={_ass_color(st.get('outline_color', '#000000'), '&H00000000')}",
        f"BackColour={_ass_color(st.get('shadow_color', '#000000'), '&H00000000')}",
        f"BorderStyle={3 if st.get('box') else 1}",
        f"Outline={float(st.get('outline', 2))}",
        f"Shadow={float(st.get('shadow', 0))}",
        f"Bold={1 if st.get('bold', True) else 0}",
        f"Italic={1 if st.get('italic') else 0}",
        f"Alignment={align}",
        f"MarginV={int(st.get('margin_bottom', 30))}",
    ]
    return ",".join(parts)


def _ffmpeg_sub_path(srt_path: str) -> str:
    """Đưa file subtitle về một đường dẫn AN TOÀN cho bộ lọc subtitles.

    Bộ lọc này phân tích chuỗi nên dấu ':' của ổ đĩa, dấu '\\' và ký tự tiếng
    Trung trong tên file rất dễ làm vỡ lệnh. Chép ra thư mục tạm với tên thuần
    ASCII là hết chuyện.
    """
    import shutil
    import tempfile
    import uuid
    ext = os.path.splitext(srt_path)[1].lower() or ".srt"
    safe = os.path.join(tempfile.gettempdir(), f"autodub_hardsub_{os.getpid()}_{uuid.uuid4().hex[:8]}{ext}")
    try:
        shutil.copyfile(srt_path, safe)
    except Exception:
        safe = srt_path
    return safe.replace("\\", "/").replace(":", "\\:")
