"""Replace leftover Chinese function-words glued into Vietnamese cues.

The model sometimes keeps adverbs/idioms (迟迟, 顿时) by registering them as
uncertain names. That is not a name. This map only rewrites those source
tokens; it does not invent dialogue and does not touch pure-Chinese lines.
"""
from __future__ import annotations

import re

from .const import _CJK_RE

_LATIN_RE = re.compile(r"[A-Za-zÀ-ỹ]")
_SPACE = re.compile(r" {2,}")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")

# Unique keys. Vietnamese is the spoken equivalent of the leftover token.
RESIDUE_VI = {
    "一片死寂": "một vùng chết lặng",
    "好不容易": "mãi mới",
    "瞻前顾后": "do dự trước sau",
    "随时待命": "luôn sẵn sàng",
    "久仰大名": "lâu nay ngưỡng mộ",
    "见识见识": "cho nếm thử",
    "待命": "chờ lệnh",
    "恨不得": "chỉ muốn",
    "各位": "quý vị",
    "诸位": "các vị",
    "辛苦": "vất vả",
    "潜伏": "ẩn nấp",
    "见识": "thấy rõ",
    "生源": "nguồn học sinh",
    "考核": "sát hạch",
    "尴尬": "khó xử",
    "啰嗦": "lảm nhảm",
    "切磋": "tỉ thí",
    "放任": "buông lỏng",
    "窥视": "dòm ngó",
    "千万别": "tuyệt đối đừng",
    "不得不": "đành phải",
    "来不及": "không kịp",
    "不好意思": "xin lỗi",
    "没关系": "không sao",
    "没想到": "nào ngờ",
    "为什么": "tại sao",
    "怎么样": "thế nào",
    "无论如何": "dù sao",
    "总而言之": "nói tóm lại",
    "也就是说": "nói cách khác",
    "换句话说": "nói cách khác",
    "原来如此": "hóa ra vậy",
    "不出所料": "đúng như dự liệu",
    "出乎意料": "ngoài dự liệu",
    "措手不及": "không kịp trở tay",
    "不由自主": "không tự chủ được",
    "情不自禁": "không kìm được",
    "无可奈何": "đành chịu",
    "无能为力": "bất lực",
    "还没有": "vẫn chưa",
    "不仅仅": "không chỉ",
    "思绪": "dòng nghĩ",
    "顿时": "lập tức",
    "萦绕": "vương vấn",
    "沦为": "biến thành",
    "早就": "đã sớm",
    "倘若": "nếu như",
    "随时": "luôn",
    "多出": "thừa ra",
    "闯入": "xông vào",
    "切记": "nhớ kỹ",
    "迟迟": "mãi",
    "层层": "lớp lớp",
    "自有": "tự có",
    "难免": "khó tránh",
    "可不": "đâu phải",
    "纷纷": "dồn dập",
    "瞬间": "nháy mắt",
    "立刻": "lập tức",
    "忽然": "bỗng",
    "突然": "bỗng nhiên",
    "终于": "cuối cùng",
    "竟然": "thế mà",
    "居然": "thế mà",
    "几乎": "gần như",
    "似乎": "dường như",
    "仿佛": "như thể",
    "当然": "đương nhiên",
    "其实": "thực ra",
    "原来": "hóa ra",
    "于是": "thế là",
    "因此": "vì vậy",
    "所以": "nên",
    "但是": "nhưng",
    "不过": "nhưng",
    "然而": "thế nhưng",
    "而且": "hơn nữa",
    "并且": "và",
    "或者": "hoặc",
    "还是": "vẫn",
    "如果": "nếu",
    "因为": "vì",
    "虽然": "tuy",
    "即使": "dù",
    "无论": "dù",
    "只要": "chỉ cần",
    "只有": "chỉ có",
    "不是": "không phải",
    "不会": "sẽ không",
    "不能": "không thể",
    "不要": "đừng",
    "不用": "không cần",
    "没有": "không có",
    "还有": "còn",
    "已经": "đã",
    "正在": "đang",
    "将要": "sắp",
    "一直": "luôn",
    "仍然": "vẫn",
    "依然": "vẫn",
    "更加": "càng",
    "非常": "rất",
    "十分": "rất",
    "有点": "hơi",
    "有些": "hơi",
    "一起": "cùng",
    "马上": "ngay",
    "赶紧": "mau",
    "连忙": "vội",
    "悄悄": "lén",
    "慢慢": "từ từ",
    "渐渐": "dần",
    "默默": "lặng lẽ",
    "明明": "rõ ràng",
    "偏偏": "lại",
    "刚刚": "vừa",
    "刚才": "lúc nãy",
    "现在": "bây giờ",
    "当时": "lúc ấy",
    "后来": "sau đó",
    "然后": "rồi",
    "接着": "tiếp đó",
    "随后": "sau đó",
    "同时": "đồng thời",
    "此时": "lúc này",
    "从此": "từ đó",
    "甚至": "thậm chí",
    "反而": "trái lại",
    "否则": "nếu không",
    "除非": "trừ khi",
    "不管": "bất kể",
    "尽管": "dù",
    "尽量": "cố",
    "尽快": "sớm",
    "显然": "rõ ràng",
    "明显": "rõ",
    "确实": "quả đúng",
    "的确": "quả thật",
    "果然": "quả nhiên",
    "简直": "thật sự",
    "或许": "có lẽ",
    "也许": "có lẽ",
    "大概": "chừng",
    "恐怕": "e rằng",
    "只是": "chỉ là",
    "仅仅": "chỉ",
    "就是": "chính là",
    "还要": "còn phải",
    "还没": "chưa",
    "不得已": "bất đắc dĩ",
    "不由得": "không khỏi",
    "不禁": "không khỏi",
    "忍不住": "không nhịn được",
    "不敢": "không dám",
    "不想": "không muốn",
    "不愿": "không muốn",
    "不肯": "không chịu",
    "不必": "không cần",
    "不如": "không bằng",
    "不仅": "không chỉ",
    "不但": "không chỉ",
    "总之": "tóm lại",
    "另外": "ngoài ra",
    "此外": "ngoài ra",
    "其中": "trong đó",
    "什么": "gì",
    "怎么": "sao",
    "那么": "vậy thì",
    "这么": "như vậy",
    "如此": "như vậy",
    "为何": "vì sao",
    "如何": "thế nào",
    "难道": "chẳng lẽ",
    "何必": "cần gì",
    "何况": "huống hồ",
    "原来是": "hóa ra là",
    "这才": "lúc ấy mới",
    "正是": "đúng là",
    "才是": "mới là",
    "才知道": "mới biết",
    "才明白": "mới hiểu",
    "无所谓": "không sao",
    "没问题": "không vấn đề",
    "没什么": "không có gì",
    "而已": "mà thôi",
    "幸好": "may mà",
    "幸亏": "may mà",
    "还好": "may",
    "可惜": "tiếc",
    "不料": "nào ngờ",
    "莫非": "chẳng lẽ",
    "的": "",
    "了": "",
    "着": "",
    "过": "",
    "吗": "",
    "呢": "",
    "吧": "",
    "啊": "",
    "呀": "",
    "嘛": "",
    "哦": "",
    "嗯": "",
}

RESIDUE_KEYS = frozenset(RESIDUE_VI)
_RESIDUE_ORDERED = tuple(sorted(RESIDUE_VI, key=len, reverse=True))


def is_mixed_vi_cjk(text: str) -> bool:
    blob = text or ""
    return bool(_CJK_RE.search(blob)) and bool(_LATIN_RE.search(blob))


def cjk_runs(text: str):
    """Contiguous leftover Han runs, longest-first for name masking."""
    found = _CJK_RUN.findall(text or "")
    return sorted(found, key=len, reverse=True)


def _latin_neighbor(ch: str) -> bool:
    return bool(ch) and bool(_LATIN_RE.search(ch))


def cjk_run_is_glued(text: str, run: str) -> bool:
    """True when a Han run sits against a Latin letter — leftover glue, not a name.

    Isolated ``七月 đã về`` is a keep-source name. ``huống尴尬này`` is an
    untranslated function word jammed into Vietnamese and must not be
    registered as ``uncertain_name``.
    """
    blob, token = text or "", run or ""
    if not blob or not token:
        return False
    start = 0
    while True:
        i = blob.find(token, start)
        if i < 0:
            return False
        left = blob[i - 1] if i else ""
        right_i = i + len(token)
        right = blob[right_i] if right_i < len(blob) else ""
        if _latin_neighbor(left) or _latin_neighbor(right):
            return True
        start = i + 1


def strip_standalone_names(text: str, names=()) -> str:
    """Drop keep-source names only where they are not glued into Latin.

    Naive ``replace`` would hide ``huống尴尬này`` once ``尴尬`` is listed as
    a name, and TTS would speak Chinese. Glued occurrences stay in the
    string so leftover detection still sees them.
    """
    out = text or ""
    for name in sorted((n for n in (names or ()) if n), key=len, reverse=True):
        i, parts = 0, []
        while True:
            j = out.find(name, i)
            if j < 0:
                parts.append(out[i:])
                break
            left = out[j - 1] if j else ""
            right_i = j + len(name)
            right = out[right_i] if right_i < len(out) else ""
            glued = _latin_neighbor(left) or _latin_neighbor(right)
            parts.append(out[i:j])
            parts.append(name if glued else "")
            i = right_i
        out = "".join(parts)
    return out


_PUNCT_LEFT = " \t\n([{（【“\"'，,、;；"
_PUNCT_RIGHT = " \t\n.,!?;:)]}）】”\"'，,、"


def _words(blob: str):
    return [w for w in blob.strip().split() if w]


def _trim_overlap(left: str, piece: str, right: str) -> str:
    """Drop Vietnamese words already sitting next to the leftover token."""
    words = _words(piece)
    left_w = _words(left)
    right_w = _words(right)
    while words and left_w and words[0].casefold() == left_w[-1].casefold():
        words.pop(0)
        left_w.pop()
    while words and right_w and words[-1].casefold() == right_w[0].casefold():
        words.pop()
        right_w = right_w[1:]
    return " ".join(words)


def _smart_replace(text: str, src: str, vi: str) -> str:
    if src not in text:
        return text
    i, parts = 0, []
    while True:
        j = text.find(src, i)
        if j < 0:
            parts.append(text[i:])
            break
        left_chunk = text[i:j]
        right_chunk = text[j + len(src):]
        parts.append(left_chunk)
        piece = _trim_overlap(left_chunk, vi, right_chunk)
        left = text[j - 1] if j else ""
        right_i = j + len(src)
        right = text[right_i] if right_i < len(text) else ""
        if piece:
            if left and (left in "，,、;；:：" or (
                    left not in _PUNCT_LEFT and not _CJK_RE.search(left))):
                piece = " " + piece
            if right and right not in _PUNCT_RIGHT and not _CJK_RE.search(right):
                piece = piece + " "
        elif left and right and left not in _PUNCT_LEFT and right not in _PUNCT_RIGHT:
            if not _CJK_RE.search(left) and not _CJK_RE.search(right):
                piece = " "
        parts.append(piece)
        i = right_i
    return "".join(parts)


def repair_residual_cjk(text: str, allowed_names=(), source: str = "") -> str:
    """Rewrite leftover function-word CJK inside an already-Vietnamese cue.

    When ``source`` is the Chinese cue, only rewrite tokens that actually appear
    there so a clock-mismatched Vietnamese line is not silently 'cleaned'.
    Keep-source names are masked only as standalone tokens so a glued leftover
    that happens to equal a name is still visible to residue rewrite.
    """
    blob = text or ""
    if not is_mixed_vi_cjk(blob):
        return blob
    masks = []
    out = blob
    for i, name in enumerate(sorted((n for n in (allowed_names or ())
                                    if n and n not in RESIDUE_KEYS),
                                    key=len, reverse=True)):
        token = f"\x00N{i}\x00"
        pos, parts, found = 0, [], False
        while True:
            j = out.find(name, pos)
            if j < 0:
                parts.append(out[pos:])
                break
            left = out[j - 1] if j else ""
            right_i = j + len(name)
            right = out[right_i] if right_i < len(out) else ""
            glued = _latin_neighbor(left) or _latin_neighbor(right)
            parts.append(out[pos:j])
            if glued:
                parts.append(name)
            else:
                parts.append(token)
                found = True
            pos = right_i
        out = "".join(parts)
        if found:
            masks.append((token, name))
    for src in _RESIDUE_ORDERED:
        if src not in out:
            continue
        if source and src not in source:
            continue
        out = _smart_replace(out, src, RESIDUE_VI[src])
    for token, name in masks:
        out = out.replace(token, name)
    return _SPACE.sub(" ", out).strip()


def leftover_cjk_indices(segments):
    """0-based indices whose spoken text still has source glyphs outside names.

    Glued Han is always leftover, even if the same glyphs are listed as a
    keep-source name — names are tokens, not infixes inside a Vietnamese word.
    """
    from .parse import _contains_cjk
    out = []
    for i, s in enumerate(segments or ()):
        names = tuple(n for n in (getattr(s, "allowed_source_names", ()) or ())
                      if n not in RESIDUE_KEYS)
        if _contains_cjk(strip_standalone_names(s.text or "", names)):
            out.append(i)
    return out


def apply_residual_repairs(segments, sources=None) -> int:
    """Rewrite mixed leftover function-words in place. Returns how many cues changed."""
    n = 0
    for i, s in enumerate(segments or ()):
        names = getattr(s, "allowed_source_names", ()) or ()
        src = ""
        if sources is not None and i < len(sources):
            src = sources[i] or ""
        new = repair_residual_cjk(s.text or "", names, source=src)
        if new != (s.text or ""):
            s.text = new
            n += 1
    return n
