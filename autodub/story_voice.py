"""Phân tích truyện và xếp hạng giọng kể có sẵn.

Phần này chạy hoàn toàn tại máy: không gửi kịch bản lên dịch vụ khác và không
đoán một voice id không có trong catalog của engine đang chọn.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple


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


# ---------------------------------------------------------------------------
# Dàn giọng truyện: lời kể giữ một giọng, thoại trực tiếp dùng giọng nhân vật.
# ---------------------------------------------------------------------------

_HONORIFICS = (
    "ông", "bà", "anh", "chị", "cô", "chú", "bác", "dì", "cậu",
    "thằng", "con", "bé", "má", "mẹ", "cha", "ba",
)
_FEMALE_TITLES = {"bà", "chị", "cô", "dì", "má", "mẹ"}
_MALE_TITLES = {"ông", "anh", "chú", "bác", "cậu", "thằng", "cha", "ba"}
_OLD_TITLES = {"ông", "bà", "bác"}
_CHILD_TITLES = {"bé", "con"}
_SPEECH_WORDS = (
    "nói", "hỏi", "đáp", "bảo", "kêu", "gọi", "la", "hét", "thét",
    "thì thầm", "lắp bắp", "gằn giọng", "nghẹn", "cằn nhằn", "quát",
    "cười", "khóc", "tiếp lời", "lên tiếng", "phân trần", "van",
)
_HARMFUL_CHARACTER_MARKERS = (
    "robot", "demon", "vibrato", "méo", "đại đế", "tự test", "sunny idol",
)
_FEMALE_GIVEN = {
    "hạnh", "liên", "nhài", "mai", "lan", "hoa", "huệ", "hương", "thảo",
    "trâm", "ngọc", "linh", "my", "nga", "yến", "oanh", "lệ", "diễm",
}
_MALE_GIVEN = {
    "phong", "minh", "hùng", "dũng", "nam", "đức", "thành", "tâm", "sơn",
    "lộc", "tài", "vinh", "an", "khải", "long", "kiên", "quang",
}


def _fold(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _title_of(name: str) -> str:
    first = _fold(name).split(" ", 1)[0]
    return first if first in _HONORIFICS else ""


def _gender_age(name: str, description: str = "", age: Optional[int] = None) -> Tuple[str, str]:
    title = _title_of(name)
    folded = _fold(name + " " + description)
    if title in _FEMALE_TITLES or any(x in folded for x in (
            "con gái", "cháu gái", "bé gái", "đàn bà", "nữ")):
        gender = "nu"
    elif title in _MALE_TITLES or any(x in folded for x in (
            "con trai", "cháu trai", "bé trai", "đàn ông", "nam")):
        gender = "nam"
    else:
        last = _fold(name).split()[-1:] or [""]
        gender = "nu" if last[0] in _FEMALE_GIVEN else "nam" if last[0] in _MALE_GIVEN else "khac"
    if age is not None:
        age_group = "tre_em" if age <= 14 else "tre" if age <= 25 else "lon_tuoi" if age >= 60 else "truong_thanh"
    elif title in _CHILD_TITLES:
        age_group = "tre_em"
    elif title in _OLD_TITLES:
        age_group = "lon_tuoi"
    else:
        age_group = "truong_thanh"
    return gender, age_group


def _aliases_for(name: str) -> List[str]:
    folded = _fold(name).strip(" ,.:;!?\"“”")
    parts = folded.split()
    aliases = {folded}
    if parts and parts[0] in _HONORIFICS:
        bare = " ".join(parts[1:])
        if bare and bare != "ba":  # "ba" thường là cha/số ba, không đủ để nhận diện tên
            aliases.add(bare)
        if len(parts) >= 2:
            aliases.add(" ".join(parts[:2]))
            ordinal = parts[1]
            if parts[0] in _FEMALE_TITLES:
                aliases.update(title + " " + ordinal for title in ("bà", "chị", "cô", "dì"))
            elif parts[0] in _MALE_TITLES:
                aliases.update(title + " " + ordinal for title in ("ông", "anh", "chú", "bác", "cậu"))
            elif parts[0] in _CHILD_TITLES:
                aliases.update(title + " " + ordinal for title in ("con", "bé"))
        if len(parts) >= 3:
            aliases.add(" ".join(parts[-2:]))
    if len(parts) >= 2 and len(parts[-1]) >= 3:
        aliases.add(parts[-1])
    return sorted((x for x in aliases if len(x) >= 2), key=len, reverse=True)


def _character(name: str, description: str = "", age: Optional[int] = None,
               source: str = "script") -> Dict:
    name = re.sub(r"\s+", " ", str(name or "")).strip(" ,.:;!?\"“”")
    gender, age_group = _gender_age(name, description, age)
    aliases = _aliases_for(name)
    parts = _fold(name).split()
    if len(parts) >= 2:
        ordinal = parts[1]
        if gender == "nu":
            aliases = sorted(set(aliases).union(
                title + " " + ordinal for title in ("bà", "chị", "cô", "dì")),
                key=len, reverse=True)
        elif gender == "nam":
            aliases = sorted(set(aliases).union(
                title + " " + ordinal for title in ("ông", "anh", "chú", "bác", "cậu")),
                key=len, reverse=True)
    return {
        "key": _fold(name), "name": name, "aliases": aliases,
        "gender": gender, "age": age, "age_group": age_group,
        "description": re.sub(r"\s+", " ", str(description or "")).strip(),
        "source": source, "mentions": 0, "dialogue_lines": 0,
    }


def parse_character_bible(design_text: str) -> List[Dict]:
    """Đọc mục NHÂN VẬT của ``00_ban_thiet_ke.txt`` nếu công cụ viết truyện có trả nó."""
    raw = str(design_text or "")
    match = re.search(
        r"(?ims)^\s*(?:3\.\s*)?NHÂN VẬT\s*$\s*(.+?)(?=^\s*(?:4\.\s*)?CÂU MỞ ĐẦU\s*$)", raw)
    if not match:
        return []
    out: List[Dict] = []
    for line in re.split(r"\n\s*\n|\r?\n", match.group(1)):
        clean = re.sub(r"^\s*[-*•]\s*", "", line).strip()
        if not clean:
            continue
        item = re.match(r"^([^,.;:]{2,60}),\s*(?:(\d{1,3})\s*tuổi\s*,?)?\s*(.*)$", clean, re.I)
        if not item:
            continue
        name = item.group(1).strip()
        if not any(_fold(name).startswith(x + " ") for x in _HONORIFICS):
            continue
        age = int(item.group(2)) if item.group(2) else None
        out.append(_character(name, item.group(3), age, source="design"))
    return out


_NAME_RE = re.compile(
    r"(?<!\w)(?:Ông|ông|Bà|bà|Anh|anh|Chị|chị|Cô|cô|Chú|chú|Bác|bác|"
    r"Dì|dì|Cậu|cậu|Thằng|thằng|Con|con|Bé|bé)\s+"
    r"[A-ZĐÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢ"
    r"ÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ][\wÀ-ỹđĐ]*"
    r"(?:\s+[A-ZĐÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢ"
    r"ÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ][\wÀ-ỹđĐ]*){0,2}", re.UNICODE)


def discover_characters(text: str, design_text: str = "", limit: int = 12) -> List[Dict]:
    """Lấy hồ sơ thiết kế khi có; nếu không thì dò tên gọi kiểu Việt trong kịch bản."""
    chars = parse_character_bible(design_text)
    discovered: Dict[str, Dict] = {}
    for match in _NAME_RE.finditer(str(text or "")):
        name = match.group(0).strip()
        key = _fold(name)
        if key not in discovered:
            discovered[key] = _character(name)
        discovered[key]["mentions"] += 1
    if not chars:
        # Tên đầy đủ thắng biến thể ngắn ("Bà Bảy Nhọn" thắng "Bà Bảy").
        ordered = sorted(discovered.values(), key=lambda x: (-len(x["key"]), -x["mentions"]))
        kept: List[Dict] = []
        for item in ordered:
            if any(item["key"] in other["aliases"] or other["key"] in item["aliases"]
                   for other in kept):
                continue
            kept.append(item)
        chars = kept
    for char in chars:
        char["mentions"] = max(char.get("mentions", 0), sum(
            len(re.findall(r"(?<!\w)%s(?!\w)" % re.escape(alias), _fold(text)))
            for alias in char["aliases"][:3]))
    chars.sort(key=lambda x: (-x["mentions"], x["name"]))
    return chars[:max(1, int(limit))]


def _mentions(text: str, characters: Sequence[Dict]) -> List[Tuple[int, Dict]]:
    folded = _fold(text)
    found: List[Tuple[int, Dict]] = []
    for char in characters:
        best: Optional[int] = None
        for alias in char.get("aliases") or []:
            for match in re.finditer(r"(?<!\w)%s(?!\w)" % re.escape(alias), folded):
                best = match.start() if best is None else min(best, match.start())
        if best is not None:
            found.append((best, char))
    return sorted(found, key=lambda x: x[0])


def _has_speech_word(text: str) -> bool:
    folded = _fold(text)
    return any(word in folded for word in _SPEECH_WORDS)


def _dialogue_parts(paragraph: str) -> List[Tuple[str, str]]:
    """Tách phần kể/thoại trong cùng đoạn mà không làm mất chữ."""
    raw = str(paragraph or "").strip()
    if not raw:
        return []
    parts: List[Tuple[str, str]] = []
    cursor = 0
    for match in re.finditer(r"[\"“]([^\"”]{1,2000})[\"”]", raw, re.S):
        before = raw[cursor:match.start()].strip()
        if before:
            parts.append(("narration", before))
        quote = match.group(1).strip()
        if quote:
            parts.append(("dialogue", quote))
        cursor = match.end()
    tail = raw[cursor:].strip()
    if tail:
        parts.append(("narration", tail))
    if parts:
        return parts
    dash = re.match(r"^\s*[-–—]\s*(.+)$", raw, re.S)
    return [("dialogue", dash.group(1).strip())] if dash else [("narration", raw)]


def analyse_dialogue(text: str, design_text: str = "", character_limit: int = 12) -> Dict:
    """Tạo danh sách lượt đọc và gán người nói bằng bằng chứng gần câu thoại.

    Chỉ gán giọng nhân vật khi đủ chắc. Thoại mơ hồ dùng giọng kể để tránh việc
    một nhân vật bỗng đổi giới tính/giọng giữa truyện.
    """
    characters = discover_characters(text, design_text, limit=character_limit)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", str(text or "")) if p.strip()]
    units = []
    for para_index, paragraph in enumerate(paragraphs):
        for kind, value in _dialogue_parts(paragraph):
            units.append({"paragraph": para_index, "kind": kind, "text": value})

    recent_focus: Optional[Dict] = None
    recent_scene: List[Dict] = []
    last_speaker: Optional[Dict] = None
    speaker_history: List[Dict] = []
    narration_run = 0
    utterances: List[Dict] = []
    confident = dialogue_total = 0

    for pos, unit in enumerate(units):
        if unit["kind"] == "narration":
            narration_run += 1
            hits = _mentions(unit["text"], characters)
            if hits:
                # Chủ ngữ thường là tên đầu đoạn; riêng câu dẫn có động từ nói thì
                # ưu tiên tên mở đầu. Các câu "Bà đứng trước cửa buồng ông Tám"
                # vẫn nói về Bà của đoạn trước, không phải ông Tám là tân ngữ.
                explicit_subject = hits[0][0] <= 2
                folded = _fold(unit["text"])
                lead = folded.split(" ", 1)[0].strip(".,:;!?\"“”")
                generic_gender = ("nu" if lead in {"bà", "chị", "cô", "dì", "bả"}
                                  else "nam" if lead in {"ông", "anh", "chú", "bác", "ổng"}
                                  else "khac")
                carried = recent_focus or last_speaker
                if (not explicit_subject and carried is not None and
                        (generic_gender == "khac" or carried["gender"] == generic_gender)):
                    recent_focus = carried
                else:
                    recent_focus = hits[0][1] if explicit_subject else (
                        hits[-1][1] if _has_speech_word(unit["text"]) else hits[0][1])
                for _, char in hits:
                    recent_scene = [x for x in recent_scene if x["key"] != char["key"]]
                    recent_scene.append(char)
                recent_scene = recent_scene[-4:]
            else:
                folded = _fold(unit["text"])
                lead = folded.split(" ", 1)[0].strip(".,:;!?\"“”")
                carried = recent_focus or last_speaker
                if carried is not None:
                    if lead in {"bà", "chị", "cô", "dì", "bả"} and carried["gender"] == "nu":
                        recent_focus = carried
                    elif lead in {"ông", "anh", "chú", "bác", "ổng"} and carried["gender"] == "nam":
                        recent_focus = carried
            utterances.append({
                "text": unit["text"], "kind": "narration", "speaker": "narrator",
                "speaker_name": "Người kể", "confidence": 1.0,
            })
            continue

        dialogue_total += 1
        quote_hits = {char["key"] for _, char in _mentions(unit["text"], characters)}
        previous = units[pos - 1] if pos else None
        following = units[pos + 1] if pos + 1 < len(units) else None
        candidate: Optional[Dict] = None
        confidence = 0.0

        # Lời dẫn sát câu thoại, ví dụ: "Bà Sáu nghẹn giọng:".
        for neighbour, score in ((previous, .96), (following, .92)):
            if not neighbour or neighbour["kind"] != "narration" or not _has_speech_word(neighbour["text"]):
                continue
            if neighbour is following and neighbour["paragraph"] != unit["paragraph"]:
                continue
            hits = _mentions(neighbour["text"], characters)
            if hits:
                choice = hits[0][1] if hits[0][0] <= 2 else hits[-1][1]
                if choice["key"] not in quote_hits:
                    candidate, confidence = choice, score
                    break

        # Nhân vật đang là chủ thể của đoạn kể ngay trước câu thoại.
        if candidate is None and recent_focus is not None and recent_focus["key"] not in quote_hits:
            candidate, confidence = recent_focus, .82

        # Hai câu thoại đứng liền nhau thường là lượt đáp. Chỉ dùng những người
        # đã thực sự được gán trước đó, không lấy mọi cái tên thoáng qua trong cảnh.
        previous_assigned = utterances[-1] if utterances else None
        if (candidate is None and previous and previous["kind"] == "dialogue" and
                previous_assigned and previous_assigned.get("speaker") != "narrator" and
                last_speaker is not None):
            alternatives = [x for x in reversed(speaker_history[:-1])
                            if x["key"] != last_speaker["key"] and x["key"] not in quote_hits]
            if alternatives:
                candidate, confidence = alternatives[0], .76

        # Hội thoại hai người thường luân phiên; tên xuất hiện trong câu là người
        # được gọi, không phải người nói.
        if candidate is None and last_speaker is not None:
            alternatives = [x for x in reversed(recent_scene)
                            if x["key"] != last_speaker["key"] and x["key"] not in quote_hits]
            if len(alternatives) == 1:
                candidate, confidence = alternatives[0], .72
            elif quote_hits:
                candidates = [x for x in reversed(recent_scene)
                              if x["key"] not in quote_hits and x["key"] != last_speaker["key"]]
                if candidates:
                    candidate, confidence = candidates[0], .68

        if candidate is not None and confidence >= .68:
            speaker_key, speaker_name = candidate["key"], candidate["name"]
            candidate["dialogue_lines"] += 1
            if narration_run >= 2 or (narration_run and confidence >= .9 and
                                      all(x["key"] != candidate["key"] for x in speaker_history[-3:])):
                speaker_history = []
            speaker_history.append(candidate)
            speaker_history = speaker_history[-6:]
            last_speaker = candidate
            recent_focus = None  # không ép chủ thể cũ lên câu trả lời kế tiếp
            confident += 1
        else:
            speaker_key, speaker_name = "narrator", "Người kể"
            confidence = max(confidence, .35)
        scene_additions = []
        if candidate is not None:
            scene_additions.append(candidate)
        scene_additions.extend(char for _, char in _mentions(unit["text"], characters))
        for char in scene_additions:
            recent_scene = [x for x in recent_scene if x["key"] != char["key"]]
            recent_scene.append(char)
        recent_scene = recent_scene[-4:]
        utterances.append({
            "text": unit["text"], "kind": "dialogue", "speaker": speaker_key,
            "speaker_name": speaker_name, "confidence": round(confidence, 2),
        })
        narration_run = 0

    active = [c for c in characters if c["dialogue_lines"] > 0]
    active.sort(key=lambda x: (-x["dialogue_lines"], -x["mentions"], x["name"]))
    coverage = 100.0 * confident / dialogue_total if dialogue_total else 0.0
    return {
        "characters": active, "utterances": utterances,
        "dialogue_lines": dialogue_total, "assigned_dialogue_lines": confident,
        "assignment_coverage": round(coverage, 1),
    }


def _voice_character_tags(voice: Dict) -> Tuple[str, str, set]:
    voice_id = str(voice.get("id") or "")
    label = _fold(str(voice.get("name") or "") + " " + voice_id)
    gender, traits = _profile_for(voice)
    age_group = "truong_thanh"
    if any(x in label for x in ("giọng bé", "giong be", "bv074")):
        age_group = "tre_em"
        traits = set(traits) | {"tre", "sang"}
    elif any(x in label for x in ("mới lớn", "tre hon", "trẻ hơn", "+8hz", "hoạt ngôn")):
        age_group = "tre"
        traits = set(traits) | {"tre", "sang"}
    elif any(x in label for x in ("trầm", "tram", "-8hz", "felipe", "bv562")):
        age_group = "lon_tuoi"
        traits = set(traits) | {"tram"}
    return gender, age_group, set(traits)


def _character_desired_traits(character: Dict) -> set:
    folded = _fold(character.get("description") or "")
    wanted = set()
    if any(x in folded for x in ("hiền", "dịu", "lo lắng", "nhút", "tình cảm", "thương")):
        wanted.update({"am", "ngot", "cam_xuc"})
    if any(x in folded for x in ("điềm", "trầm", "ít nói", "chững chạc", "nghiêm")):
        wanted.update({"tram", "nghiem"})
    if any(x in folded for x in ("lắm chuyện", "chua ngoa", "nóng tính", "hoạt bát", "lanh", "tự tin")):
        wanted.update({"kich_tinh", "doi_thoai", "tu_tin"})
    if character.get("age_group") in {"tre_em", "tre"}:
        wanted.update({"tre", "sang"})
    return wanted


def _rank_character_voices(character: Dict, voices: Sequence[Dict], used: set,
                           narrator_id: str) -> List[Tuple[float, Dict, List[str]]]:
    ranked = []
    desired_traits = _character_desired_traits(character)
    for order, voice in enumerate(voices):
        if str(voice.get("status") or "unknown") == "failed":
            continue
        voice_id = str(voice.get("id") or "")
        label = _fold(str(voice.get("name") or "") + " " + voice_id)
        if any(marker in label for marker in _HARMFUL_CHARACTER_MARKERS):
            continue
        gender, age_group, traits = _voice_character_tags(voice)
        score = 10.0 - order * .01
        reasons: List[str] = []
        if gender == character["gender"]:
            score += 35
            reasons.append("đúng chất giọng nam" if gender == "nam" else "đúng chất giọng nữ")
        elif character["gender"] != "khac" and gender != "khac":
            score -= 30
        if age_group == character["age_group"]:
            score += 45 if age_group == "tre_em" else 28
            labels = {"tre_em": "hợp trẻ em", "tre": "hợp người trẻ",
                      "truong_thanh": "hợp người trưởng thành", "lon_tuoi": "hợp người lớn tuổi"}
            reasons.append(labels[age_group])
        elif character["age_group"] == "lon_tuoi" and "tram" in traits:
            score += 18
            reasons.append("âm sắc trầm, chững chạc")
        elif character["age_group"] == "tre_em" and age_group not in {"tre_em", "tre"}:
            score -= 18
        if voice_id not in used:
            score += 12
        else:
            score -= 16
        if voice_id == narrator_id:
            score -= 20
        if str(voice.get("status") or "") == "ok":
            score += 3
        if "doi_thoai" in traits:
            score += 5
            reasons.append("nhịp thoại rõ")
        matched_traits = desired_traits.intersection(traits)
        if matched_traits:
            score += min(18, 7 * len(matched_traits))
            if matched_traits.intersection({"am", "ngot", "cam_xuc"}):
                reasons.append("hợp nét hiền, giàu cảm xúc")
            elif matched_traits.intersection({"tram", "nghiem"}):
                reasons.append("hợp nét điềm, chững chạc")
            elif matched_traits.intersection({"kich_tinh", "doi_thoai", "tu_tin"}):
                reasons.append("hợp vai đối thoại có cá tính")
        ranked.append((score, voice, reasons or ["giọng trung tính, dễ phân biệt"]))
    return sorted(ranked, key=lambda x: (-x[0], str(x[1].get("name") or "")))


def _find_catalog_voice(voice_id: str, voices: Sequence[Dict]) -> Optional[Dict]:
    wanted = str(voice_id or "")
    for voice in voices:
        current = str(voice.get("id") or "")
        if current == wanted:
            return voice
    for voice in voices:
        current = str(voice.get("id") or "")
        if current.split("|", 1)[0] == wanted.split("|", 1)[0]:
            return voice
    return None


def plan_story_voices(text: str, voices: Sequence[Dict], engine: str = "capcut",
                      narrator_voice: str = "", design_text: str = "",
                      auto_narrator: bool = True, max_characters: int = 8) -> Dict:
    """Lập dàn giọng nhất quán và trả các lượt đọc đã gắn voice id."""
    catalog = [dict(v) for v in voices if str(v.get("status") or "unknown") != "failed"]
    if not catalog:
        raise ValueError("Catalog giọng đang trống.")
    recommended = recommend_voices(text, catalog, engine=engine, limit=5)
    selected = _find_catalog_voice(narrator_voice, catalog)
    if auto_narrator or selected is None:
        top = (recommended.get("recommendations") or [{}])[0]
        selected = _find_catalog_voice(str(top.get("id") or ""), catalog) or catalog[0]
    narrator_id = str(selected.get("id") or narrator_voice)
    if not auto_narrator and narrator_voice:
        # Edge cho phép pitch tự chọn không nhất thiết có sẵn như một mục catalog.
        narrator_id = str(narrator_voice)
    narrator = {
        "id": narrator_id, "name": str(selected.get("name") or narrator_id),
        "role": "narrator", "character": "Người kể",
        "reasons": ((recommended.get("recommendations") or [{}])[0].get("reasons") or
                    ["giọng kể chính"]),
    }

    dialogue = analyse_dialogue(text, design_text, character_limit=max(12, max_characters + 3))
    cast: List[Dict] = []
    used = {narrator_id}
    for char in dialogue["characters"][:max(1, int(max_characters))]:
        choices = _rank_character_voices(char, catalog, used, narrator_id)
        if not choices:
            continue
        unused = [choice for choice in choices if str(choice[1].get("id") or "") not in used]
        # Khi catalog còn giọng an toàn, mỗi vai bắt buộc lấy một voice id khác.
        # Chỉ tái sử dụng khi engine thật sự không còn lựa chọn (Edge chỉ có 6 biến thể).
        score, voice, reasons = (unused or choices)[0]
        voice_id = str(voice.get("id") or narrator_id)
        used.add(voice_id)
        cast.append({
            "character": char["name"], "key": char["key"],
            "description": char["description"], "gender": char["gender"],
            "age": char["age"], "age_group": char["age_group"],
            "dialogue_lines": char["dialogue_lines"], "voice_id": voice_id,
            "voice_name": str(voice.get("name") or voice_id),
            "score": round(score, 1), "reasons": reasons[:3],
        })
    mapping = {item["key"]: item for item in cast}
    planned = []
    for utterance in dialogue["utterances"]:
        item = dict(utterance)
        member = mapping.get(item["speaker"])
        item["voice"] = member["voice_id"] if member else narrator_id
        planned.append(item)
    return {
        "analysis": recommended["analysis"],
        "recommendations": recommended["recommendations"],
        "narrator": narrator, "cast": cast, "characters": dialogue["characters"],
        "utterances": planned, "dialogue_lines": dialogue["dialogue_lines"],
        "assigned_dialogue_lines": dialogue["assigned_dialogue_lines"],
        "assignment_coverage": dialogue["assignment_coverage"],
        "engine": engine,
    }
