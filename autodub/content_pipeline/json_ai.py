"""Đọc và vá JSON do model AI trả về (markdown, dấu phẩy thừa, bị cắt)."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


def _json_object_candidates(text: str) -> List[str]:
    """Tách các object JSON cân bằng, bỏ qua ngoặc nằm trong chuỗi.

    Gemini Web đôi khi thêm lời dẫn hoặc bọc kết quả trong Markdown. Cắt từ dấu
    ``{`` đầu tới dấu ``}`` cuối rất dễ nuốt hai khối khác nhau vào cùng một
    chuỗi và làm ``json.loads`` thất bại, nên ta quét từng object độc lập.
    """
    raw = str(text or "").lstrip("\ufeff").strip()
    candidates: List[str] = []
    start = -1
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                candidates.append(raw[start:index + 1])
                start = -1
    return candidates


def _remove_json_trailing_commas(text: str) -> str:
    """Bỏ dấu phẩy cuối object/array mà không sửa nội dung trong chuỗi."""
    out: List[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                index += 1
                continue
        out.append(char)
        index += 1
    return "".join(out)


def _slice_balanced(text: str, start: int) -> str:
    """Lấy substring cân bằng ngoặc từ vị trí ``start`` (phải là [ hoặc {)."""
    if start < 0 or start >= len(text) or text[start] not in "[{":
        return ""
    open_ch = text[start]
    close_ch = "]" if open_ch == "[" else "}"
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == open_ch:
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""


def _extract_named_json_array(text: str, key: str):
    token = '"%s"' % key
    pos = 0
    raw = str(text or "")
    while True:
        idx = raw.find(token, pos)
        if idx < 0:
            return None
        j = idx + len(token)
        while j < len(raw) and raw[j].isspace():
            j += 1
        if j < len(raw) and raw[j] == ":":
            j += 1
            while j < len(raw) and raw[j].isspace():
                j += 1
            if j < len(raw) and raw[j] == "[":
                blob = _slice_balanced(raw, j)
                if blob:
                    try:
                        parsed = json.loads(blob)
                    except ValueError:
                        parsed = None
                    if isinstance(parsed, list) and parsed:
                        return parsed
        pos = idx + 1


def _repair_truncated_json(text: str) -> Optional[Dict[str, Any]]:
    """Ghép nốt object JSON bị cắt giữa chừng (Gemini lấy khi còn đang viết).

    File lỗi thật dán hai object: cái đầu đứt giữa chuỗi nên ``{`` của object
    sau bị nuốt như nội dung chuỗi. Thử sửa từ MỖI dấu ``{`` rồi lấy object
    có nhiều trường nhất (ưu tiên có titles).
    """
    raw = str(text or "").lstrip("\ufeff").strip()
    starts = [i for i, ch in enumerate(raw) if ch == "{"][:40]
    best: Optional[Dict[str, Any]] = None
    best_score = -1
    for start in starts:
        parsed = _repair_one_object(raw[start:])
        if not isinstance(parsed, dict) or not parsed:
            continue
        score = len(parsed) + (20 if parsed.get("titles") else 0)
        if score > best_score:
            best, best_score = parsed, score
    if best is not None and not best.get("titles"):
        titles = _extract_named_json_array(raw, "titles")
        if titles:
            best = dict(best)
            best["titles"] = titles
    return best


def _repair_one_object(chunk: str) -> Optional[Dict[str, Any]]:
    try:
        parsed, _end = json.JSONDecoder().raw_decode(chunk)
        if isinstance(parsed, dict) and parsed:
            return parsed
    except ValueError:
        pass

    in_string = False
    escaped = False
    stack: List[str] = []
    for char in chunk:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]" and stack:
            stack.pop()

    repaired = chunk.rstrip()
    if in_string:
        repaired += '"'
    while stack:
        repaired += stack.pop()
    for variant in (repaired, _remove_json_trailing_commas(repaired)):
        try:
            parsed = json.loads(variant)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed:
            return parsed
    return None


def _extract_json(text: str) -> Dict[str, Any]:
    raw = str(text or "").lstrip("\ufeff").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
    last_error: Optional[Exception] = None
    scored: List[tuple] = []
    for candidate in _json_object_candidates(raw):
        for variant in (candidate, _remove_json_trailing_commas(candidate)):
            for strict in (True, False):
                try:
                    parsed = json.loads(variant, strict=strict)
                except (TypeError, ValueError) as exc:
                    last_error = exc
                    continue
                if isinstance(parsed, dict) and parsed:
                    scored.append((
                        len(parsed) + (20 if parsed.get("titles") else 0),
                        parsed,
                    ))
                    break
            else:
                continue
            break
    salvaged = _repair_truncated_json(raw)
    if salvaged:
        scored.append((
            len(salvaged) + (20 if salvaged.get("titles") else 0),
            salvaged,
        ))
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        winner = scored[0][1]
        if not winner.get("titles"):
            titles = _extract_named_json_array(raw, "titles")
            if titles:
                winner = dict(winner)
                winner["titles"] = titles
        return winner
    if last_error is None:
        raise ValueError("AI không trả về JSON.")
    detail = str(last_error).splitlines()[0][:160]
    raise ValueError(f"AI trả JSON chưa hợp lệ: {detail}")
