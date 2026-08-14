"""Phân tích truyện và xếp hạng giọng kể có sẵn.

Phần này chạy hoàn toàn tại máy: không gửi kịch bản lên dịch vụ khác và không
đoán một voice id không có trong catalog của engine đang chọn.
"""
from __future__ import annotations

import re
from typing import Dict, List, Sequence


_WORD_RE = re.compile(r"\S+", re.UNICODE)
_SENTENCE_RE = re.compile(r"[.!?…]+")
_DIALOGUE_RE = re.compile(r'(^\s*[-–—]\s+)|(["“”][^"“”]{2,}["“”])', re.MULTILINE)

_KEYWORDS = {
    "gia_dinh": (
        "gia đình", "mẹ chồng", "con dâu", "cha", "mẹ", "ông nội", "bà nội",
        "vợ", "chồng", "đứa con", "cháu", "anh em", "chị em", "hiếu thảo",
    ),
    "bi_an": (
        "bí mật", "sự thật", "mất tích", "nghi ngờ", "vật chứng", "lá thư",
        "oan", "điều tra", "chết lặng", "không ai biết", "che giấu", "cú lật",
    ),
    "kinh_di": (
        "ma", "bóng trắng", "vong", "ám", "máu", "xác", "nghĩa địa",
        "đêm khuya", "tiếng khóc", "rùng mình", "kinh hoàng",
    ),
    "chieu_nghiem": (
        "tuổi già", "u70", "u60", "về già", "sống thọ", "lương tâm",
        "nhân quả", "đời người", "hối hận", "tha thứ", "bài học",
    ),
    "hai_huoc": (
        "bật cười", "cười ngặt", "hài", "tréo ngoe", "dở khóc dở cười",
        "mắc cười", "cà khịa",
    ),
}

_FEMALE_WORDS = ("bà", "chị", "cô", "dì", "mẹ", "con dâu", "người đàn bà")
_MALE_WORDS = ("ông", "anh", "chú", "cậu", "cha", "người đàn ông")


# Hồ sơ tập trung vào các giọng hợp kể truyện dài. Giọng hiệu ứng vẫn ở thư
# viện để người dùng nghe, nhưng không được tự đề xuất cho truyện nghiêm túc.
_PROFILES = {
    "vi_female_huong": ("nu", {"ro", "am", "gia_dinh", "chieu_nghiem"}),
    "vi-VN-HoaiMyNeural": ("nu", {"ro", "am", "gia_dinh", "cam_xuc"}),
    "BV421_vivn_streaming": ("nu", {"ngot", "am", "gia_dinh", "cam_xuc"}),
    "BV562_streaming": ("nu", {"am", "gia_dinh", "chieu_nghiem"}),
    "multi_female_yangguangnv_uranus_bigtts": ("nu", {"ro", "sang", "doi_thoai"}),
    "multi_female_richgirl_uranus_bigtts": ("nu", {"kich_tinh", "bi_an", "doi_thoai"}),
    "multi_female_daqi_uranus_bigtts": ("nu", {"kich_tinh", "bi_an"}),
    "multi_female_stokie_uranus_bigtts": ("nu", {"kich_tinh", "doi_thoai"}),
    "vi-VN-NamMinhNeural": ("nam", {"ro", "tram", "chieu_nghiem", "bi_an"}),
    "multi_male_felipe_uranus_bigtts": ("nam", {"tram", "am", "bi_an", "chieu_nghiem"}),
    "multi_female_xinwenjieshuo_uranus_bigtts": ("nam", {"ro", "nghiem", "bi_an"}),
    "BV075_streaming": ("nam", {"tu_tin", "kich_tinh", "doi_thoai"}),
    "BV560_streaming": ("nam", {"tram", "kich_tinh"}),
}

_NOVELTY_MARKERS = (
    "robot", "demon", "vibrato", "méo", "đại đế", "giọng bé", "gái mới lớn",
    "tự test", "sunny idol",
)


def _count_keywords(folded: str, values: Sequence[str]) -> int:
    return sum(len(re.findall(r"(?<!\w)%s(?!\w)" % re.escape(value), folded))
               for value in values)


def _first_excerpt(text: str, limit: int = 280) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= limit:
        return clean
    chunk = clean[:limit]
    cut = max(chunk.rfind(". "), chunk.rfind("! "), chunk.rfind("? "))
    return chunk[:cut + 1].strip() if cut >= limit // 3 else chunk.rsplit(" ", 1)[0]


def analyse_story(text: str, wpm: int = 135) -> Dict:
    raw = str(text or "").strip()
    folded = raw.casefold()
    words = len(_WORD_RE.findall(raw))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    dialogue = sum(1 for p in paragraphs if _DIALOGUE_RE.search(p))
    dialogue_ratio = 100.0 * dialogue / len(paragraphs) if paragraphs else 0.0
    scores = {key: _count_keywords(folded, values) for key, values in _KEYWORDS.items()}
    genre = max(scores, key=scores.get) if any(scores.values()) else "doi_thuong"
    female = _count_keywords(folded, _FEMALE_WORDS)
    male = _count_keywords(folded, _MALE_WORDS)
    focus = "nu" if female > male * 1.25 else "nam" if male > female * 1.25 else "can_bang"
    sentence_count = max(1, len(_SENTENCE_RE.findall(raw)))
    average_sentence_words = words / sentence_count
    labels = {
        "gia_dinh": "gia đình, tình cảm",
        "bi_an": "bí ẩn, giải oan",
        "kinh_di": "kinh dị, căng thẳng",
        "chieu_nghiem": "chiêm nghiệm, tuổi già",
        "hai_huoc": "hài hước, đời thường",
        "doi_thuong": "đời thường",
    }
    return {
        "genre": genre,
        "genre_label": labels[genre],
        "word_count": words,
        "estimated_minutes": round(words / max(1, int(wpm)), 1),
        "dialogue_ratio": round(dialogue_ratio, 1),
        "character_focus": focus,
        "average_sentence_words": round(average_sentence_words, 1),
        "keyword_scores": scores,
        "preview_text": _first_excerpt(raw),
    }


def _profile_for(voice: Dict):
    voice_id = str(voice.get("id") or "").split("|", 1)[0]
    if voice_id in _PROFILES:
        return _PROFILES[voice_id]
    label = (str(voice.get("name") or "") + " " + voice_id).casefold()
    gender = "nu" if any(x in label for x in ("nữ", "female", "cô gái", "mai")) else (
        "nam" if re.search(r"(?<!\w)nam(?!\w)|(?<!fe)male", label) else "khac")
    traits = {"ro"}
    if "trầm" in label:
        traits.add("tram")
    if "review" in label:
        traits.update({"kich_tinh", "doi_thoai"})
    return gender, traits


def recommend_voices(text: str, voices: Sequence[Dict], engine: str = "capcut",
                     limit: int = 5) -> Dict:
    analysis = analyse_story(text)
    ranked: List[Dict] = []
    genre = analysis["genre"]
    for order, voice in enumerate(voices):
        if str(voice.get("status") or "unknown") == "failed":
            continue
        voice_id = str(voice.get("id") or "")
        name = str(voice.get("name") or voice_id)
        gender, traits = _profile_for(voice)
        folded_name = name.casefold()
        novelty = any(marker in folded_name or marker in voice_id.casefold()
                      for marker in _NOVELTY_MARKERS)
        score = 20.0 - order * 0.02
        reasons = []
        if "ro" in traits:
            score += 8
            reasons.append("phát âm rõ cho truyện dài")
        if genre == "gia_dinh":
            if "gia_dinh" in traits or "am" in traits:
                score += 18
                reasons.append("ấm và gần với chuyện gia đình")
            if "cam_xuc" in traits or "ngot" in traits:
                score += 8
                reasons.append("truyền cảm ở đoạn tình thân")
        elif genre in {"bi_an", "kinh_di"}:
            if "bi_an" in traits or "tram" in traits:
                score += 18
                reasons.append("chất giọng trầm giữ được bí ẩn")
            if "kich_tinh" in traits:
                score += 8
                reasons.append("có lực ở các đoạn lật tình tiết")
        elif genre == "chieu_nghiem":
            if "chieu_nghiem" in traits or "tram" in traits or "am" in traits:
                score += 18
                reasons.append("điềm và hợp khán giả lớn tuổi")
        elif genre == "hai_huoc":
            if "doi_thoai" in traits or "tu_tin" in traits or "sang" in traits:
                score += 16
                reasons.append("nhịp linh hoạt cho thoại đời thường")
        if analysis["dialogue_ratio"] >= 45 and "doi_thoai" in traits:
            score += 9
            reasons.append("hợp kịch bản nhiều đối thoại")
        if analysis["character_focus"] == gender:
            score += 5
            reasons.append("hợp trọng tâm nhân vật %s" % ("nữ" if gender == "nu" else "nam"))
        if novelty:
            score -= 80
            reasons = ["giọng hiệu ứng; chỉ nên dùng cho nhân vật phụ"]
        if not reasons:
            reasons.append("giọng trung tính, nên nghe thử trước")
        ranked.append({
            "id": voice_id, "name": name, "engine": engine,
            "score": round(score, 1), "gender": gender,
            "traits": sorted(traits), "reasons": reasons[:3],
            "novelty": novelty,
        })
    ranked.sort(key=lambda x: (-x["score"], x["name"]))
    safe = [item for item in ranked if not item["novelty"]]
    return {"analysis": analysis, "recommendations": safe[:max(1, int(limit))]}
