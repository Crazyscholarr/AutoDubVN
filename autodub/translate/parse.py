from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from .const import (
    TRANSLATION_BUDGET_MARGIN, TRANSLATION_MIN_CHARS,
    SHORTEN_TRIGGER_RATIO, SHORTEN_KEEP_RATIO, SHORTEN_KEEP_RATIO_LONG,
    STYLE_LOCK, _CJK_RE,
)
from ..srt_utils import Segment, normalize_vi_subtitle_text
from ..utils import log
from ..vi_reflow import prompt_group_hint


def spoken_header(film_hint: str = "", name_hint: str = "") -> str:
    """Khóa vai lồng tiếng + tên phim/nhân vật, dán đầu mọi prompt dịch."""
    parts = [STYLE_LOCK]
    for extra in (film_hint, name_hint):
        text = str(extra or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts) + "\n\n"


def build_name_hint(male_lead_name: str = "", female_lead_name: str = "") -> str:
    male = str(male_lead_name or "").strip()
    female = str(female_lead_name or "").strip()
    lines = []
    if male:
        lines.append(f"- Nam chinh/nhan vat chinh bat buoc dung ten Viet: {male}.")
    if female:
        lines.append(f"- Nhan vat nu chinh/nu trung tam bat buoc dung ten Viet: {female}.")
    if not lines:
        return ""
    return (
        "Quy uoc ten rieng bat buoc khi dich:\n"
        + "\n".join(lines)
        + "\nNeu nguon co ten Han/Trung, biet danh hoac cach goi cua cac nhan vat nay, "
          "hay quy ve dung ten tren; khong tu y doi sang ten khac."
    )


def build_film_hint(title: str = "") -> str:
    """Gắn tên phim vào prompt để Gemini không lẫn sang chat viết văn/kho ý tưởng."""
    raw = str(title or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"\s*\[BV[^\]]+\]\s*", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_|")
    if len(cleaned) < 2:
        return ""
    if len(cleaned) > 80:
        cleaned = cleaned[:77] + "..."
    return (
        f"Phim đang lồng tiếng: {cleaned}. "
        "Giọng thuyết minh/thoại hoạt hình Trung, không phải bài báo hay truyện chữ."
    )


def _parse_json_lines(raw: str, expected: int) -> Optional[List[str]]:
    raw = raw.strip()
    # cắt bỏ ```json ... ``` nếu có
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
    try:
        arr = json.loads(raw)
        if isinstance(arr, dict):
            arr = list(arr.values())
        if (isinstance(arr, list) and len(arr) == expected and
                all(isinstance(x, str) and x.strip() for x in arr)):
            return [x.strip() for x in arr]
    except Exception:
        pass
    return None


def _clean_vi_lines(values: List[str]) -> List[str]:
    return [normalize_vi_subtitle_text("" if v is None else str(v)) for v in values]


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _cjk_line_numbers(values: List[str]) -> List[int]:
    return [idx + 1 for idx, txt in enumerate(values) if _contains_cjk(txt)]


def _bad_line_summary(lines: List[int], limit: int = 8) -> str:
    if not lines:
        return ""
    head = ", ".join(str(x) for x in lines[:limit])
    more = "" if len(lines) <= limit else f", ... +{len(lines) - limit}"
    return head + more


# Chấp nhận: [3] abc | (3) abc | 3. abc | 3) abc | 3: abc | 3、abc | 3 - abc
# KHÔNG chấp nhận "3 abc" (số trần + khoảng trắng): thoại thật hoàn toàn có thể
# mở đầu bằng số ("8 个头16个大") - nhận bừa là hỏng nguyên dòng đó.
_NUM_LINE = re.compile(
    r"^[>\-*_`\s]*"
    r"(?:\[\s*(\d{1,4})\s*\]|\(\s*(\d{1,4})\s*\)|(\d{1,4})\s*[\.\)\:：、]|(\d{1,4})\s+[-–—])"
    r"\s*(.+)$")
_MD_EDGE = re.compile(r"^[*_`\s]+|[*_`\s]+$")


def parse_numbered_reply(reply: str, n: int) -> Dict[int, str]:
    """Chỉ nhận các dòng CÓ SỐ THỨ TỰ, ánh xạ theo đúng con số đó.

    Quan trọng: bản cũ nhận cả dòng KHÔNG có số, nên chỉ cần Gemini thêm một câu
    dẫn ("Chắc chắn rồi, đây là bản dịch:") là TOÀN BỘ phụ đề bị lệch đi 1 dòng
    mà không có gì báo. Ánh xạ theo số thì thừa/thiếu dòng dẫn cũng vô hại.
    """
    out: Dict[int, str] = {}
    for raw in (reply or "").split("\n"):
        m = _NUM_LINE.match(raw.strip())
        if not m:
            continue
        num = next((g for g in m.group(1, 2, 3, 4) if g), None)
        if num is None:
            continue
        k = int(num)
        if 1 <= k <= n and k not in out:      # gặp trùng thì giữ lần ĐẦU
            txt = _MD_EDGE.sub("", m.group(5)).strip().strip('"').strip()
            cleaned = normalize_vi_subtitle_text(txt)
            if cleaned:
                out[k] = cleaned
    return out


def match_by_position(reply: str, numbers: List[int]) -> Dict[int, str]:
    """Phao cứu sinh khi câu trả lời KHÔNG còn số nào (Gemini bỏ số, hoặc giao
    diện đổi). Chỉ ghép khi SỐ DÒNG KHỚP CHÍNH XÁC - thà bỏ qua còn hơn ghép
    lệch, vì lệch 1 dòng là hỏng cả đoạn mà không ai biết."""
    lines = [l.strip(" -•\t") for l in (reply or "").split("\n")]
    lines = [l for l in lines if l]
    if not lines:
        return {}
    # CHỈ chấp nhận đúng 2 khả năng: khớp y nguyên, hoặc thừa một dòng dẫn kết
    # thúc bằng dấu ":". Cắt đầu cắt đuôi cho vừa số dòng là ĐOÁN BỪA - lệch 1
    # dòng thì cả đoạn thoại gán sai nhân vật mà không có gì báo.
    variants = [lines]
    if lines[0].endswith(":"):
        variants.append(lines[1:])
    for v in variants:
        if len(v) == len(numbers):
            cleaned = [normalize_vi_subtitle_text(txt) for txt in v]
            if all(cleaned):
                return {k: txt for k, txt in zip(numbers, cleaned)}
    return {}


def char_budget(seg: Segment, chars_per_sec: float) -> int:
    """Mốc ký tự mềm để giọng đọc còn bám được khung thời gian của câu.

    Đây là mấu chốt của lỗi "video chạy trước giọng": câu tiếng Việt dịch từ
    tiếng Trung dài gấp ~2 lần bản gốc, TTS đọc không kịp, câu sau bị đẩy lùi,
    lệch dồn lại thành hàng chục giây. Tăng tốc đọc chỉ chữa được một phần -
    gốc rễ là bản dịch phải gọn lại. Nhưng đây chỉ là MỐC MỀM: nếu ép quá cứng,
    câu ngắn dễ bị cụt nghĩa ("kẻ nói dối" thành "kẻ dối"), nghe còn tệ hơn.
    """
    cps = max(0.0, float(chars_per_sec or 0.0))
    if cps <= 0:
        return 0
    dur = max(0.6, float(seg.end) - float(seg.start))
    return max(TRANSLATION_MIN_CHARS, int(dur * cps * TRANSLATION_BUDGET_MARGIN))


def _too_long_for_tts(seg: Segment, text: str, chars_per_sec: float) -> bool:
    return len(text or "") > char_budget(seg, chars_per_sec) * SHORTEN_TRIGGER_RATIO


def reading_pressure(segments: List[Segment],
                     chars_per_sec: float = 15.0) -> Dict:
    """Đo xem bản dịch có đọc kịp khung thời gian của video không.

    Trả về tổng số phút cần để đọc tự nhiên so với số phút ô trống thực có, và
    số dòng buộc phải đọc nhanh. Đây là chỉ số dự báo sớm cho lỗi "tiếng chạy
    trước hình": khi cần nhiều thời gian hơn số có, TTS phải nén và cắt câu,
    thoại kết thúc sớm hơn hình dù mốc bắt đầu vẫn đúng.
    """
    cps = max(1.0, float(chars_per_sec or 15.0))
    total_chars = 0
    total_slot = 0.0
    over = 0
    hopeless = 0
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        slot = max(0.2, float(seg.end) - float(seg.start))
        total_chars += len(text)
        total_slot += slot
        need = len(text) / slot
        if need > cps:
            over += 1
        if need > cps * 1.6:
            hopeless += 1
    counted = sum(1 for s in segments if (s.text or "").strip())
    need_seconds = total_chars / cps
    return {
        "lines": counted,
        "chars": total_chars,
        "slot_seconds": total_slot,
        "need_seconds": need_seconds,
        "ratio": (need_seconds / total_slot) if total_slot > 0 else 0.0,
        "over_lines": over,
        "hopeless_lines": hopeless,
    }


def log_reading_pressure(segments: List[Segment],
                         chars_per_sec: float = 15.0) -> Dict:
    """In chẩn đoán áp lực đọc trước khi tổng hợp giọng."""
    st = reading_pressure(segments, chars_per_sec)
    if not st["lines"]:
        return st
    ratio = st["ratio"]
    msg = (f"Ap luc doc: ban dich can {st['need_seconds']/60:.1f} phut de doc "
           f"tu nhien, khung thoi gian co {st['slot_seconds']/60:.1f} phut "
           f"({ratio*100:.0f}%).")
    if ratio <= 1.05:
        log(msg + " Giong se bam sat hinh.", "ok")
        return st
    log(msg, "warn")
    log(f"  {st['over_lines']}/{st['lines']} dong phai doc nhanh hon binh thuong; "
        f"{st['hopeless_lines']} dong vuot ca tran 1.6x nen se bi cat cut.", "warn")
    log("  Giong doc se ket thuc som hon hinh -> nghe nhu 'tieng chay truoc "
        "hinh'. Muon het han: giam translation.chars_per_sec (vd 13) roi XOA "
        "file .vi.srt de dich lai.", "warn")
    return st


_MEANING_PHRASE_RULES = (
    ("nói dối", ("nói dối", "dối trá", "lừa dối")),
)


def _norm_vi(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _keeps_core_meaning(original: str, candidate: str) -> bool:
    old = _norm_vi(original)
    new = _norm_vi(candidate)
    for trigger, allowed in _MEANING_PHRASE_RULES:
        if trigger in old and not any(a in new for a in allowed):
            return False
    return True


# Token viết hoa KHÔNG đứng đầu câu gần như luôn là tên riêng/danh xưng. Rút gọn
# mà đánh rơi chúng là hỏng mạch truyện, nên chặn thẳng.
_PROPER_NOUN_RE = re.compile(r"(?<![.!?…]\s)(?<!^)\b([A-ZĐ][\wÀ-ỹ]{1,})\b")
_NUMBER_RE = re.compile(r"\d+")


def _entities(text: str) -> Tuple[set, set]:
    body = (text or "").strip()
    names = {m.group(1).lower() for m in _PROPER_NOUN_RE.finditer(body)}
    numbers = set(_NUMBER_RE.findall(body))
    return names, numbers


def _keeps_entities(original: str, candidate: str) -> bool:
    """Bản rút gọn phải giữ đủ tên riêng và con số của bản gốc."""
    old_names, old_numbers = _entities(original)
    new_names, new_numbers = _entities(candidate)
    if old_names - new_names:
        return False
    if old_numbers - new_numbers:
        return False
    return True


def _accept_shortened(original: str, candidate: str, seg: Segment,
                     chars_per_sec: float) -> bool:
    old = (original or "").strip()
    new = (candidate or "").strip()
    if not new or len(new) >= len(old):
        return False
    if not _keeps_core_meaning(old, new):
        return False
    if not _keeps_entities(old, new):
        return False
    # Những câu chỉ hơi dài không được phép bị bóp xuống còn nửa ý. Câu quá dài
    # thật sự được rút mạnh hơn để cứu nhịp lồng tiếng, nhưng vẫn có sàn cứng -
    # trước đây nhánh đó KHÔNG có sàn nào nên mới sinh ra "mà bọn nó nói" ->
    # "mà bọn nói".
    target = max(1, char_budget(seg, chars_per_sec))
    floor_ratio = (SHORTEN_KEEP_RATIO if len(old) <= target * 2
                   else SHORTEN_KEEP_RATIO_LONG)
    if len(new) < max(8, int(len(old) * floor_ratio)):
        return False
    return True


def _build_prompt(chunk: List[Segment], numbers: List[int],
                  context: List[str], again: bool = False,
                  proofread: bool = False,
                  chars_per_sec: float = 0.0,
                  name_hint: str = "",
                  film_hint: str = "",
                  rewrite_spoken: bool = False) -> str:
    """`numbers` là SỐ THỨ TỰ GỐC trong lô (1..len(chunk)). Khi hỏi bổ sung các
    dòng còn thiếu, ta giữ nguyên con số cũ để ghép lại không bao giờ lệch."""
    # Dùng [số] thay vì "số." : "1." bị markdown biến thành danh sách <ol>, mà
    # số của <ol> là do CSS vẽ ra nên đọc text về là MẤT SỐ. "[1]" thì giữ nguyên.
    def _line(k: int) -> str:
        seg = chunk[k - 1]
        spk = f"({seg.speaker}) " if seg.speaker else ""
        return f"[{k}] {spk}{seg.text}"

    src = "\n".join(_line(k) for k in numbers)
    limit_note = ""
    budget_block = ""
    if chars_per_sec > 0:
        limit_note = (
            " Số trong ngoặc đơn (\u226425) là GIỚI HẠN ĐỘ DÀI của từng dòng, "
            "tính bằng ký tự. Đây là ràng buộc THẬT: giọng đọc chỉ có đúng bấy "
            "nhiêu thời gian, dòng nào dài hơn sẽ bị đọc vội rồi cắt cụt giữa "
            "chừng. Hãy viết gọn, khẩu ngữ, dễ hiểu ngay khi nghe; bỏ từ đệm, "
            "bỏ trạng từ thừa, gộp mệnh đề rườm rà. Vẫn phải giữ nguyên tên "
            "riêng, con số, chủ-vị và quan hệ nhân vật/sự việc; tránh từ Hán "
            "Việt khó nếu có cách nói phổ thông hơn. Chỉ được vượt giới hạn khi "
            "không còn cách nào diễn đạt đủ nghĩa, và vượt càng ít càng tốt.")
        limit_note += (
            " Cac moc do dai chi la metadata: KHONG chep so, dau <=, dau ngoac "
            "hay chu max_chars vao ban dich.")
        budget_block = (
            "\nGioi han do dai cho tung dong (KHONG chep vao ban dich):\n"
            + "\n".join(
                f"[{k}] <= {char_budget(chunk[k - 1], chars_per_sec)} ky tu"
                for k in numbers)
            + "\n")
    ctx = ""
    if context and not again and not rewrite_spoken:
        ctx = ("Ngữ cảnh (câu tiếng Việt vừa dịch trước đó, để giữ mạch và xưng "
               "hô nhất quán):\n" + "\n".join(f"- {c}" for c in context[-6:]) + "\n\n")
    if proofread:
        head = ("Bạn còn thiếu mấy dòng. Sửa NỐT đúng những dòng sau, GIỮ NGUYÊN số."
                if again else
                "Đây là phụ đề TIẾNG VIỆT do máy nghe tự động từ video nên có nhiều "
                "chỗ NGHE NHẦM (sai danh xưng, sai từ, câu vô nghĩa). Hãy sửa lại "
                "cho đúng tiếng Việt tự nhiên và hợp ngữ cảnh câu chuyện. KHÔNG "
                "dịch, KHÔNG diễn giải thêm, giữ nguyên ý và độ dài tương đương. "
                "Dòng nào đã đúng thì chép lại y nguyên.")
    elif rewrite_spoken:
        head = (
            "Bản vừa rồi bị dịch kiểu VĂN BẢN/bài báo. Làm lại thành THOẠI LỒNG "
            "TIẾNG: khẩu ngữ, từng dòng một nhịp đọc, không nối nghị luận. "
            "GIỮ NGUYÊN số [k], đúng ý nguồn."
        )
    else:
        head = ("Bạn còn thiếu mấy dòng. Dịch NỐT đúng những dòng sau thành THOẠI "
                "lồng tiếng, GIỮ NGUYÊN số."
                if again else
                "Dịch các dòng sau thành THOẠI LỒNG TIẾNG tiếng Việt: đúng ngữ "
                "điệu nhân vật, khẩu ngữ nghe được, một dòng nguồn = một nhịp đọc. "
                "Dịch đủ chủ-vị-tân ngữ; không lược đại từ hoặc danh xưng làm câu cụt; "
                "không viết thành đoạn văn.")
    header = "" if proofread else spoken_header(film_hint, name_hint)
    if proofread and str(name_hint or "").strip():
        header = str(name_hint).strip() + "\n\n"
    group_note = "" if proofread else prompt_group_hint(
        [chunk[k - 1] for k in numbers] if numbers else chunk)
    return (
        header + ctx + head + limit_note +
        f" Trả về ĐÚNG {len(numbers)} dòng theo mẫu:  "
        + ("[số] câu đã sửa\n" if proofread else "[số] bản dịch\n") +
        "Mỗi dòng nguồn gắn với một mốc thời gian cố định trên video, nên dòng "
        "[k] của bạn phải nói ĐÚNG ý của dòng nguồn [k] (không đảo thứ tự câu "
        "thoại, không kéo ý của một người nói sang timestamp người khác). "
        "Nguồn do máy nghe tự động nên nhiều dòng bị cắt ngang giữa câu: hãy "
        "dịch theo câu tiếng Việt tự nhiên rồi chia vào từng [k]. "
        "Ưu tiên ngắt sau dấu phẩy/mệnh đề/lời gọi. CẤM ngắt '... tôi' / 'đã...', "
        "cấm lặp chữ nối giữa hai dòng, cấm dòng 1-2 chữ cụt như 'Cái' hoặc 'gì,'. "
        "Trong bắn đích, 环 = 'điểm' (không dịch 'vòng'). "
        "KHÔNG gom cả ý sang dòng khác như bài viết. "
        "Câu Việt phải liền mạch; KHÔNG dùng dấu ba chấm (... hoặc …) để nối "
        "các mảnh câu. "
        "Giữ nguyên con số trong ngoặc vuông của từng dòng. KHÔNG dùng danh "
        "sách đánh số của markdown, KHÔNG in đậm, không lời dẫn, không giải "
        "thích, không gộp dòng.\n"
        + group_note + budget_block + "\nCac dong nguon:\n" + src
    )


def _build_shorten_prompt(chunk: List[Segment], over: List[int],
                          got: Dict[int, str], chars_per_sec: float) -> str:
    lines = "\n".join(
        f"[{k}] Gốc: {chunk[k-1].text} | Việt: {got[k]} "
        f"| target <= {char_budget(chunk[k-1], chars_per_sec)}, current {len(got[k])}"
        for k in over)
    return (
        STYLE_LOCK + "\n"
        "Các dòng dưới đây hơi dài so với nhịp lồng tiếng. Hãy RÚT GỌN VỪA ĐỦ, "
        "vẫn là THOẠI nghe được, không rút thành văn bản cụt nghĩa. "
        "Mục tiêu là bám gần số ký tự trong ngoặc, nhưng bản "
        "rút gọn vẫn phải rõ nghĩa ngay khi nghe.\n"
        "- Giữ tên riêng, danh xưng, chủ-vị, quan hệ nguyên nhân/kết quả.\n"
        "- Không biến cụm đủ nghĩa thành cụm cụt nghĩa, ví dụ KHÔNG đổi "
        "\"kẻ nói dối\" thành \"kẻ dối\".\n"
        "- Không dùng dấu ba chấm (... hoặc …); câu Việt phải liền mạch.\n"
        "- Ưu tiên từ phổ thông, tránh Hán Việt khó hiểu nếu có cách nói tự nhiên hơn.\n"
        "- Nếu không thể ngắn hơn mà vẫn rõ nghĩa, được vượt mốc một chút.\n"
        "Trả về ĐÚNG mẫu:  [số] câu đã rút gọn\n"
        "Không lời dẫn, không giải thích.\n\n" + lines)


def shorten_long_lines(chunk: List[Segment], got: Dict[int, str],
                       chars_per_sec: float, ask, label: str = "") -> int:
    """Rút gọn tại chỗ các dòng dịch dài quá nhịp lồng tiếng.

    `got` ánh xạ số thứ tự trong lô (1-based) sang bản dịch, được cập nhật tại
    chỗ. `ask` nhận prompt và trả về nguyên văn câu trả lời của model, nhờ vậy
    dùng chung được cho cả luồng trình duyệt lẫn luồng API.

    Trả về số dòng VẪN còn vượt mốc sau khi rút gọn, để bước sau biết còn bao
    nhiêu câu sẽ phải nhờ TTS đọc nhanh bù.
    """
    cps = max(0.0, float(chars_per_sec or 0.0))
    if cps <= 0 or not got:
        return 0
    over = [k for k in sorted(got)
            if got[k] and _too_long_for_tts(chunk[k - 1], got[k], cps)]
    if not over:
        return 0

    log(f"{label}{len(over)}/{len(chunk)} dòng dài hơn nhịp lồng tiếng, "
        "nhờ rút gọn vừa đủ...", "warn")
    try:
        short = parse_numbered_reply(
            ask(_build_shorten_prompt(chunk, over, got, cps)), len(chunk))
    except Exception as e:
        log(f"  rút gọn không thành ({e}) - giữ bản dài.", "warn")
        return len(over)

    applied = 0
    for k, v in short.items():
        if k in over and _accept_shortened(got[k], v, chunk[k - 1], cps):
            got[k] = normalize_vi_subtitle_text(v)
            applied += 1
    still = sum(1 for k in over if _too_long_for_tts(chunk[k - 1], got[k], cps))
    log(f"  rút gọn được {applied}/{len(over)} dòng, còn {still} dòng vượt mốc.",
        "info")
    return still
