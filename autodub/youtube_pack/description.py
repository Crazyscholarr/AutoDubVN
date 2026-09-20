"""Tóm tắt truyện và dựng/chỉnh mô tả YouTube 6 khối."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .const import (
    DESCRIPTION_PROMPT, FIXED_FOOTER, HASHTAG_RE,
    SENTENCE_RE, THUMBNAIL_STYLE_LOCK, TWIST_CUT_RE,
)

def format_duration_vi(seconds: float) -> str:
    """Đổi giây thành '1 giờ 45 phút'."""
    total = max(0, int(round(float(seconds or 0))))
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours and minutes:
        return "%d giờ %d phút" % (hours, minutes)
    if hours:
        return "%d giờ" % hours
    return "%d phút" % max(1, minutes)

def _first_sentences(text: str, count: int = 4) -> List[str]:
    blob = re.sub(r"\s+", " ", str(text or "")).strip()
    cut = TWIST_CUT_RE.split(blob, maxsplit=1)[0].strip(" :,-")
    bits = [x.strip() for x in SENTENCE_RE.split(cut) if x.strip()]
    return bits[:max(1, count)]


def make_story_summary(title: str, idea: Optional[Dict] = None,
                       script_text: str = "") -> str:
    """3–5 câu mở đầu + mâu thuẫn; dừng trước cú lật."""
    idea = idea if isinstance(idea, dict) else {}
    parts: List[str] = []
    hook = str(idea.get("main_hook") or "").strip()
    if hook:
        parts.append(hook.rstrip(".") + ".")
    for scene in list(idea.get("high_tension_scenes") or [])[:3]:
        line = str(scene or "").strip()
        if not line or line == hook:
            continue
        if TWIST_CUT_RE.search(line):
            continue
        parts.append(line.rstrip(".") + ".")
        if len(parts) >= 4:
            break
    if len(parts) < 3:
        brief = str(idea.get("rewrite_brief") or "").strip()
        for sent in _first_sentences(brief, 4):
            if sent not in parts:
                parts.append(sent if sent.endswith((".", "?", "!")) else sent + ".")
            if len(parts) >= 4:
                break
    if len(parts) < 3:
        outline = str(idea.get("outline") or idea.get("content_outline") or "").strip()
        for sent in _first_sentences(outline, 3):
            if "Phần 3" in sent or "cao trào" in sent.lower():
                continue
            parts.append(sent if sent.endswith((".", "?", "!")) else sent + ".")
            if len(parts) >= 4:
                break
    if len(parts) < 3 and script_text:
        for sent in _first_sentences(script_text, 5):
            parts.append(sent if sent.endswith((".", "?", "!")) else sent + ".")
            if len(parts) >= 5:
                break
    if not parts:
        parts.append("Gia đình vừa xảy ra một biến cố lớn quanh %s." % (title or "người thân"))
    return " ".join(parts[:5])


def build_thumbnail_visual_prompt(title: str, idea: Optional[Dict] = None) -> str:
    """Khóa phong cách + điền nhân vật/cảnh của tập này. Không chữ trong ảnh."""
    idea = idea if isinstance(idea, dict) else {}
    thumbs = idea.get("thumbnails") or []
    visual = ""
    if thumbs and isinstance(thumbs[0], dict):
        visual = str(thumbs[0].get("description") or "").strip()
    characters = [str(x).strip() for x in (
        idea.get("main_characters") or idea.get("archetypes") or []) if str(x).strip()]
    brief_bits = _first_sentences(str(idea.get("rewrite_brief") or ""), 2)
    lines = [
        THUMBNAIL_STYLE_LOCK,
        "",
        "Áp dụng cho ĐÚNG tập này, không lặp khuôn mặt mẫu:",
        "Tiêu đề: %s" % (title or "chuyện gia đình Việt Nam"),
    ]
    if characters:
        lines.append("Nhân vật chính gợi ý: %s." % ", ".join(characters[:3]))
    if visual:
        lines.append("Bối cảnh và cảm xúc: %s." % visual)
    elif brief_bits:
        lines.append("Bối cảnh và cảm xúc: %s" % " ".join(brief_bits))
    else:
        lines.append(
            "Nhân vật chính là người trong gia đình Việt Nam tuổi trung niên hoặc già, "
            "khuôn mặt đời thường, áo bà ba hoặc đồ quê, nhà gỗ miền Tây.")
    lines.append(
        "Nhắc lại: nhân vật lệch phải, nửa trái trống, không chữ, không watermark.")
    return "\n".join(lines)


def build_description_prompt(title: str, summary: str, duration: str) -> str:
    return DESCRIPTION_PROMPT.format(
        title=str(title or "").strip() or "[chưa có tiêu đề]",
        summary=str(summary or "").strip() or "Một biến cố gia đình vừa xảy ra ở làng quê.",
        duration=str(duration or "").strip() or "hơn 1 giờ",
        footer=FIXED_FOOTER,
    )


_JUNK_HEAD_RE = re.compile(
    r"^(youtube|mô tả|mo ta|description|phần mô tả)\s*$", re.I)


def extract_description_only(raw: str) -> str:
    """Bỏ lời dẫn/markdown và ghi chú biên tập, giữ phần mô tả."""
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:\w+)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(
        r"^(đây là|mô tả video|phần mô tả)[:.\s-]*",
        "", text, flags=re.I)
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and (not lines[0].strip() or _JUNK_HEAD_RE.match(lines[0].strip())):
        lines.pop(0)
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        if last.startswith(("#", "🕗", "👉")):
            break
        if last[:1].islower() and last[-1:] not in ".?!":
            lines.pop()
            continue
        break
    return "\n".join(lines).strip()


def suggest_extra_hashtags(title: str, idea: Optional[Dict] = None) -> List[str]:
    blob = " ".join([
        str(title or ""),
        str((idea or {}).get("primary_genre") or ""),
        " ".join(str(x) for x in ((idea or {}).get("tags") or [])),
        " ".join(str(x) for x in ((idea or {}).get("themes") or [])),
    ]).lower()
    picks: List[str] = []
    pairs = (
        (r"mẹ chồng|nàng dâu|con dâu", "#meChongNangDau"),
        (r"nhân quả|báo ứng", "#nhanQuaBaoUng"),
        (r"tâm linh|ông bà|ma|giỗ", "#chuyenTamLinh"),
        (r"tuổi già|vợ chồng", "#tuoiGia"),
        (r"làng quê|quê", "#chuyenQue"),
        (r"con cái|hiếu", "#hieuNghia"),
    )
    for pattern, tag in pairs:
        if re.search(pattern, blob) and tag not in picks:
            picks.append(tag)
        if len(picks) >= 2:
            return picks
    while len(picks) < 2:
        for fallback in ("#truyenAudio", "#chuyenGiaDinh"):
            if fallback not in picks:
                picks.append(fallback)
                break
    return picks[:2]


def ensure_description_finish(text: str, title: str = "",
                              idea: Optional[Dict] = None) -> str:
    """Ép khối cố định và đủ 2 hashtag riêng nếu ChatGPT thiếu."""
    body = extract_description_only(text)
    if "08:00 sáng" not in body or "#kechuyendemkhuya" not in body:
        body = body.rstrip() + "\n\n" + FIXED_FOOTER
    extras = suggest_extra_hashtags(title, idea)
    existing = {tag.lower() for tag in HASHTAG_RE.findall(body)}
    missing = [tag for tag in extras if tag.lower() not in existing]
    extra_count = len([
        tag for tag in HASHTAG_RE.findall(body)
        if tag.lower() not in {
            "#kechuyendemkhuya", "#tamsutuoigia", "#chuyenlangque"}
    ])
    if extra_count < 2 and missing:
        body = body.rstrip() + " " + " ".join(missing[: 2 - extra_count])
    return body.strip() + "\n"
