"""Phân tích offline: chủ đề, archetype, tiêu đề và hồ sơ sáng tác dự phòng."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .records import count_words


THEME_TERMS: Dict[str, Tuple[str, ...]] = {
    "Tâm linh ông bà kể": (
        "tâm linh", "chuyện ma", "ông bà kể", "người âm", "báo mộng",
        "điềm báo", "miếu", "nghĩa địa", "bàn thờ", "ngày giỗ", "hương khói",
        "cải táng", "vàng mã", "oan hồn", "gõ cửa", "rằm tháng bảy",
        "灵异", "鬼故事", "托梦", "民间怪谈", "乡村怪谈", "闹鬼", "鬼魂",
        "祖坟", "土地庙", "七月半", "阴间", "显灵", "香火", "古井",
        "半夜敲门", "改坟", "冤魂", "遗照", "鬼节"),
    "Mẹ chồng nàng dâu": (
        "mẹ chồng", "nàng dâu", "婆媳", "mother-in-law", "daughter-in-law"),
    "Con cái bất hiếu": (
        "bất hiếu", "không nuôi", "đuổi bố", "đuổi mẹ", "赡养", "不孝",
        "abandoned parent"),
    "Tình yêu xế chiều": (
        "tái hôn", "tuổi già", "xế chiều", "老年", "中老年", "再婚",
        "widow", "widower"),
    "Tranh chấp thừa kế": (
        "thừa kế", "di chúc", "chia đất", "gia sản", "遗产", "分家",
        "inheritance"),
    "Nghiệp báo ngoại tình": (
        "ngoại tình", "bồ nhí", "phản bội", "出轨", "affair", "cheated"),
    "Cô đơn tuổi già": (
        "cô đơn", "viện dưỡng lão", "sống một mình", "空巢", "养老院",
        "nursing home"),
    "Trọng nam khinh nữ": (
        "trọng nam", "khinh nữ", "重男轻女", "son preference"),
    "Giúp việc và chủ nhà": (
        "giúp việc", "người ở", "bảo mẫu", "保姆", "housekeeper"),
    "Mâu thuẫn con rể mẹ vợ": (
        "con rể", "mẹ vợ", "女婿", "岳母", "son-in-law"),
    "Gia đình và lòng tham": (
        "tham lam", "tiền bạc", "chiếm nhà", "lừa tiền", "贪", "money",
        "greed"),
}

EMOTION_TERMS: Dict[str, Tuple[str, ...]] = {
    "Rùng mình": ("tâm linh", "chuyện ma", "báo mộng", "điềm báo", "灵异", "鬼",
                  "闹鬼", "托梦", "gõ cửa", "nghĩa địa"),
    "Phẫn nộ": ("bất hiếu", "phản bội", "đuổi", "lừa", "đánh", "cướp", "出轨"),
    "Xót xa": ("cô đơn", "khóc", "mất", "bệnh", "nghèo", "qua đời", "孤独"),
    "Nghẹn lòng": ("hy sinh", "nhịn", "chịu đựng", "ân hận", "hối hận"),
    "Hạnh phúc": ("đoàn tụ", "tha thứ", "bình yên", "hạnh phúc", "sum họp"),
    "Sốc": ("bí mật", "sự thật", "không ngờ", "bất ngờ", "真相", "秘密"),
}

ARCHETYPE_TERMS: Dict[str, Tuple[str, ...]] = {
    "Người ông kể chuyện xưa": ("ông kể", "ông nội", "ông ngoại", "老爷爷", "老人讲"),
    "Người bà giữ bí mật làng": ("bà kể", "bà nội", "bà ngoại", "奶奶", "婆婆讲"),
    "Người mẹ chồng khắc nghiệt": ("mẹ chồng", "婆婆"),
    "Nàng dâu chịu thương chịu khó": ("nàng dâu", "con dâu", "儿媳"),
    "Đứa con bất hiếu": ("bất hiếu", "không nuôi", "不孝"),
    "Người mẹ già hy sinh": ("mẹ già", "bà lão", "母亲", "老人"),
    "Người chồng phản bội": ("chồng ngoại tình", "bồ nhí", "丈夫出轨"),
    "Người vợ nhẫn nhịn": ("vợ", "chịu đựng", "妻子"),
    "Kẻ tham gia sản": ("thừa kế", "di chúc", "gia sản", "遗产"),
    "Người hàng xóm biết chuyện": ("hàng xóm", "邻居"),
}

TWIST_TERMS = (
    "bí mật", "sự thật", "hóa ra", "không ngờ", "bất ngờ", "di chúc",
    "xét nghiệm", "đứa con", "đổi tráo", "lật mặt", "真相", "原来", "秘密",
    "báo mộng", "điềm báo", "người âm", "托梦", "灵异",
    "turns out", "secret", "revealed")
HOOK_TERMS = (
    "đuổi", "ngoại tình", "thừa kế", "mất tích", "ly hôn", "đám tang",
    "tái hôn", "bất hiếu", "tranh chấp", "cướp", "lừa", "报应", "出轨",
    "chuyện ma", "tâm linh", "báo mộng", "nghĩa địa", "灵异", "鬼故事",
    "AITA", "inheritance", "divorce")


def _term_score(text: str, terms: Iterable[str]) -> int:
    folded = text.casefold()
    return sum(folded.count(term.casefold()) for term in terms)


def _rank_labels(text: str, groups: Dict[str, Sequence[str]], limit: int) -> List[str]:
    scores = [(name, _term_score(text, terms)) for name, terms in groups.items()]
    found = [name for name, score in sorted(scores, key=lambda item: item[1], reverse=True)
             if score > 0]
    return found[:limit]


def _headline_situation(record: Dict[str, Any], theme: str) -> str:
    localized = str(record.get("title_localized") or "").strip()
    language = str(record.get("language") or "").lower()
    # Chỉ tận dụng tiêu đề nguồn khi nó đã là tiếng Việt. Tiêu đề Trung/Anh
    # không được giả làm "Tiêu đề Việt" trong chế độ dự phòng offline.
    title = localized or (record.get("title_original") if language == "vi" else "") or ""
    title = re.sub(r"[|:—–-]+", " ", str(title)).strip()
    title = re.sub(r"\s+", " ", title)
    if 16 <= len(title) <= 62:
        return title.upper()
    situations = {
        "Tâm linh ông bà kể": "BÀ NGOẠI KỂ LẠI TIẾNG GÕ CỬA SAU ĐÊM GIỖ",
        "Mẹ chồng nàng dâu": "MẸ CHỒNG ĐUỔI CON DÂU KHỎI NHÀ GIỮA ĐÊM",
        "Con cái bất hiếu": "BA NGƯỜI CON TRANH NHÀ NHƯNG KHÔNG AI NUÔI MẸ",
        "Tình yêu xế chiều": "BÀ 68 TUỔI TÁI HÔN KHIẾN CẢ NHÀ PHẢN ĐỐI",
        "Tranh chấp thừa kế": "TỜ DI CHÚC XUẤT HIỆN NGAY TRƯỚC NGÀY CHIA ĐẤT",
        "Nghiệp báo ngoại tình": "NGƯỜI CHỒNG GIẤU BÍ MẬT SUỐT 20 NĂM",
        "Cô đơn tuổi già": "BỐ GIÀ BỊ BỎ LẠI TRONG CĂN NHÀ TRỐNG",
    }
    return situations.get(theme, "BÍ MẬT GIA ĐÌNH BẤT NGỜ BỊ PHƠI BÀY")


def _make_titles(record: Dict[str, Any], theme: str, emotion: str) -> List[str]:
    situation = _headline_situation(record, theme)
    spiritual = theme == "Tâm linh ông bà kể"
    hooks = ([
        "Nghe Mà Lạnh Gáy", "Chuyện Ông Bà Kể", "Nghe Mà Rùng Mình",
        "Chuyện Làng Quê Kỳ Bí", "Nghe Mà Thấm", "Nghe Mà Sốc",
        "Chuyện Cũ Bên Bếp Lửa", "Kể Chuyện Đêm Khuya",
    ] if spiritual else [
        "Nghe Mà Thấm", "Nghe THẤM Tận Xương", "Nghe Là Khóc",
        "Nghe Mà Nghẹn Lòng", "Nghe Mà Rơi Nước Mắt", "Nghe Sướng Lỗ Tai",
        "Nghe Mà Sốc", "Truyện Ngắn Tuổi Xế Chiều Cực Hay",
    ])
    tails = ([
        "Kể Chuyện Tâm Linh", "Kể Chuyện Đêm Khuya", "Kể Chuyện Làng Quê",
        "Kể Chuyện Tâm Linh", "Chuyện Ông Bà Kể", "Kể Chuyện Đêm Khuya",
        "Kể Chuyện Làng Quê", "Kể Chuyện Tâm Linh",
    ] if spiritual else [
        "Kể Chuyện Đêm Khuya", "Đọc Truyện Đêm Khuya", "Kể Chuyện Tuổi Già",
        "Kể Chuyện Làng Quê", "Kể Chuyện Đêm Khuya", "Kể Chuyện Tuổi Già",
        "Đọc Truyện Đêm Khuya", "Kể Chuyện Làng Quê",
    ])
    variations = [
        situation,
        situation.replace("BÍ MẬT", "SỰ THẬT"),
        situation.replace("NGƯỜI CHỒNG", "CHỒNG GIÀ"),
        situation.replace("CẢ NHÀ", "CÁC CON"),
        situation + " SAU BỮA CƠM GIỖ",
        situation.replace("GIỮA ĐÊM", "TRƯỚC MẶT HỌ HÀNG"),
        situation.replace("20 NĂM", "30 NĂM"),
        situation.replace("BẤT NGỜ", "SAU NGÀY GIỖ"),
    ]
    out: List[str] = []
    for hook, body, tail in zip(hooks, variations, tails):
        value = f"{hook} : {body} | {tail}"
        if len(value) > 100:
            room = max(18, 100 - len(hook) - len(tail) - 7)
            body = body[:room].rsplit(" ", 1)[0]
            value = f"{hook} : {body} | {tail}"
        if value not in out:
            out.append(value)
    while len(out) < 8:
        out.append(f"Nghe Mà Thấm : {situation[:48]} | Kể Chuyện Tuổi Già")
    return out[:8]


def _fallback_rewrite_material(theme: str, archetypes: Sequence[str],
                               situation: str) -> Dict[str, Any]:
    """Hồ sơ an toàn khi chưa gọi được AI: không chép một câu nào từ nguồn."""
    lead = archetypes[0] if archetypes else "người thân trong gia đình"
    scenes = [
        f"Biến cố mở màn cụ thể quanh {situation.lower()}, xảy ra trước mặt người thân.",
        f"Một dấu hiệu nhỏ cho thấy {lead.lower()} đang che giấu hoặc chịu đựng điều gì đó.",
        "Một lời nói dối để giữ hòa khí khiến một người bị tổn thương thật.",
        f"Áp lực tiền bạc hoặc lời dị nghị trong xóm làm mâu thuẫn {theme.lower()} bùng lên.",
        "Một vật chứng đời thường xuất hiện, đảo ngược cách mọi người nhìn sự việc.",
    ]
    twists = [
        "Người tưởng là có lỗi lại đang âm thầm bảo vệ một người khác.",
        "Quyền lợi tiền bạc chỉ là bề mặt; món nợ tình thân cũ mới là nguyên nhân chính.",
    ]
    brief = (
        "MỤC TIÊU: Viết một truyện gia đình Việt Nam hoàn toàn mới, không dịch và "
        "không kể lại truyện nguồn.\n"
        f"TIỀN ĐỀ MỚI: Khai thác chủ đề {theme.lower()} qua một gia đình ở làng quê "
        f"Nam Bộ. Mở truyện bằng sự việc: {situation.lower()}.\n"
        "NHỊP KỊCH TÍNH: " + " ".join(f"({i + 1}) {scene}" for i, scene in enumerate(scenes)) + "\n"
        "CHẤT LIỆU CÚ LẬT: " + " ".join(twists) + "\n"
        "RÀNG BUỘC SÁNG TÁC: Đổi toàn bộ tên, địa danh, quan hệ, số liệu, vật chứng, "
        "thứ tự sự việc và lời văn; thêm nguyên nhân-hậu quả mới; kết theo lẽ nhân quả. "
        "Không dùng tên riêng hoặc câu chữ của nguồn."
    )
    if theme == "Tâm linh ông bà kể":
        brief += (
            "\nQUY ƯỚC TÂM LINH: Kể qua lời hồi tưởng của ông/bà như chuyện truyền "
            "miệng bên bếp lửa; giữ không khí làng quê và sự mơ hồ. Không khẳng "
            "định hiện tượng siêu nhiên là thật, không hướng dẫn cúng bái/chữa bệnh "
            "mê tín; kết lại bằng tình thân hoặc bài học sống."
        )
    return {"main_hook": scenes[0], "high_tension_scenes": scenes,
            "plot_twists": twists,
            "must_change": ["tên", "địa danh", "quan hệ", "số liệu",
                            "vật chứng", "diễn biến", "lời văn"],
            "rewrite_brief": brief}


def _make_descriptions(record: Dict[str, Any], theme: str, emotion: str) -> List[str]:
    source_note = "Câu chuyện được sáng tạo lại từ một mô-típ gia đình, toàn bộ nhân vật và bối cảnh đều hư cấu."
    return [
        f"Một biến cố về {theme.lower()} đã đẩy cả gia đình đến lúc phải đối diện sự thật. "
        f"Câu chuyện mang màu sắc {emotion.lower()}, có nhiều lớp mâu thuẫn và những lựa chọn khiến người nghe phải suy ngẫm. "
        f"{source_note} Cô chú, anh chị hãy để lại cảm nhận và đăng ký để đồng hành cùng những câu chuyện mới. #kechuyen #tuoigia #giadinh",
        f"Khi tình thân bị thử thách bởi {theme.lower()}, ai mới là người giữ được nghĩa tình? "
        "Bản kể tập trung vào xung đột đời thường, cách đối nhân xử thế và bài học dành cho mỗi gia đình Việt. "
        f"{source_note} Mời mọi người nghe đến cuối và chia sẻ góc nhìn của mình. #truyendemkhuya #langque #chuyengiadinh",
        f"Từ một tình huống {emotion.lower()}, câu chuyện mở ra những bí mật, hiểu lầm và món nợ tình thân kéo dài nhiều năm. "
        "Nội dung đã được biên tập thành truyện Việt mới, không phải bản dịch nguyên văn. "
        "Nếu thấy ý nghĩa, cô chú anh chị nhớ bật chuông và để lại bình luận. #goctruyen #kechuyentuoiGia #nhanqua",
    ]


def heuristic_analysis(record: Dict[str, Any]) -> Dict[str, Any]:
    text = "\n".join([
        str(record.get("title_original") or ""),
        str(record.get("title_localized") or ""),
        str(record.get("content") or record.get("raw_content") or ""),
    ])
    themes = _rank_labels(text, THEME_TERMS, 4) or ["Gia đình và lòng tham"]
    emotions = _rank_labels(text, EMOTION_TERMS, 2) or ["Nghẹn lòng"]
    archetypes = _rank_labels(text, ARCHETYPE_TERMS, 6) or ["Người thân trong gia đình"]
    hook_hits = _term_score(text, HOOK_TERMS)
    twist_hits = _term_score(text, TWIST_TERMS)
    has_number = bool(re.search(r"\b\d{1,3}\b", text))
    has_quote = bool(re.search(r"[\"“”']", text))
    word_count = int(record.get("word_count") or count_words(text))
    hook = min(10, max(1, 4 + min(4, hook_hits) + int(has_number) + int(has_quote)))
    twist = min(10, max(1, 3 + min(5, twist_hits) + int(has_number)))
    duration = round(max(1, word_count / 150.0), 1)
    ease = "Cao" if 1200 <= word_count <= 15000 else "Trung" if word_count <= 24000 else "Thấp"
    priority = round(hook * .45 + twist * .40 + ({"Cao": 10, "Trung": 6, "Thấp": 3}[ease]) * .15, 1)
    recommendation = "Nên làm" if priority >= 7.2 else "Chờ đợi" if priority >= 5.5 else "Không nên làm"
    theme = themes[0]
    emotion = emotions[0]
    titles = _make_titles(record, theme, emotion)
    situation = _headline_situation(record, theme)
    material = _fallback_rewrite_material(theme, archetypes, situation)
    descriptions = _make_descriptions(record, theme, emotion)
    thumbnails = [
        {"description": f"Cận cảnh hai thế hệ đối đầu trong căn nhà làng quê, chủ đề {theme.lower()}",
         "colors": "đỏ sẫm, vàng ấm, đen", "text": "SỰ THẬT LỘ RA"},
        {"description": "Người mẹ già ngồi bên mâm cơm nguội, phía sau là các con đang tranh cãi",
         "colors": "xanh đêm, cam, trắng", "text": "KHÔNG AI NUÔI MẸ"},
        {"description": "Một tờ giấy quan trọng trên bàn thờ, mọi người bàng hoàng nhìn nhau",
         "colors": "nâu gỗ, đỏ, vàng", "text": "TỜ GIẤY CUỐI CÙNG"},
    ]
    keywords = list(dict.fromkeys(themes + [emotion, "chuyện gia đình", "tuổi già", "nhân quả"]))[:8]
    tags = ["kể chuyện đêm khuya", "chuyện tuổi già", "chuyện làng quê"] + themes[:3]
    if theme == "Tâm linh ông bà kể":
        tags = ["chuyện tâm linh", "chuyện ông bà kể", "chuyện làng quê",
                "kể chuyện đêm khuya"] + themes[:3]
    series = f"Series {theme} · {archetypes[0]}"
    outline = (
        f"Phần 1 — Mở nút: Giới thiệu {archetypes[0].lower()} và biến cố về {theme.lower()}.\n"
        f"Phần 2 — Leo thang: Mâu thuẫn gia đình dồn dập, các nhân vật buộc phải chọn bên.\n"
        "Phần 3 — Cao trào: Một sự thật quan trọng xuất hiện; dừng trước kết cục để biên tập phát triển truyện mới."
    )
    return {
        "title_localized": situation.title(),
        "primary_genre": theme,
        "emotion": emotion,
        "hook_score": hook,
        "plot_twist_score": twist,
        "themes": themes,
        "archetypes": archetypes,
        "keywords": keywords,
        "tags": list(dict.fromkeys(tags)),
        "estimated_duration_minutes": duration,
        "series": series,
        "titles": titles,
        **material,
        "descriptions": descriptions,
        "thumbnails": thumbnails,
        "outline": outline,
        "main_characters": archetypes[:5],
        "production_ease": ease,
        "priority_score": priority,
        "recommendation": recommendation,
        "best_publish_time": "19:30–21:00, ưu tiên Thứ Năm hoặc Chủ Nhật",
        "analysis_provider": "heuristic",
        "analysis_error": "",
        "status": "analyzed",
    }
