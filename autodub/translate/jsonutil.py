"""Safe JSON extraction and error taxonomy for Gemini replies."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional, Tuple

EMPTY_RESPONSE = "EMPTY_RESPONSE"
VALID_JSON_CANDIDATE = "VALID_JSON_CANDIDATE"
MODEL_REFUSAL = "MODEL_REFUSAL"
NON_JSON_RESPONSE = "NON_JSON_RESPONSE"
UI_ERROR_RESPONSE = "UI_ERROR_RESPONSE"
MALFORMED_JSON = "MALFORMED_JSON"
TRUNCATED_JSON = "TRUNCATED_JSON"
MARKDOWN_WRAPPED_JSON = "MARKDOWN_WRAPPED_JSON"
EXTRA_TEXT_JSON = "EXTRA_TEXT_JSON"
VALID_JSON = "VALID_JSON"
# Compatibility names for existing callers; new logs use the explicit taxonomy.
INVALID_JSON = MALFORMED_JSON
MODEL_NON_JSON = NON_JSON_RESPONSE
UI_ERROR = UI_ERROR_RESPONSE
RATE_LIMIT = "RATE_LIMIT"
SEND_FAILURE = "SEND_FAILURE"
RESPONSE_TIMEOUT = "RESPONSE_TIMEOUT"
SCHEMA_FAILURE = "SCHEMA_FAILURE"
SEMANTIC_GATE_FAILURE = "SEMANTIC_GATE_FAILURE"

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
_RATE_RE = re.compile(
    r"usage limit|rate limit|too many requests|quota exceeded|hết hạn mức",
    re.IGNORECASE)
_UI_RE = re.compile(
    r"something went wrong|try again later|đã xảy ra lỗi(?!\s*\()",
    re.IGNORECASE)
_REFUSAL_RE = re.compile(
    r"\b(?:tôi|mình)\s+(?:rất\s+tiếc[,，]?\s*)?(?:không thể|không có khả năng)\s+"
    r"(?:giúp|trợ giúp|hỗ trợ|thực hiện|đáp ứng|dịch|xử lý|trả lời|hiểu)"
    r"|(?:mô hình ngôn ngữ|trí tuệ nhân tạo|công nghệ[^.!?\n]{0,40}văn bản)"
    r"[^.!?\n]{0,180}(?:ngoài (?:mục đích|khả năng)|không thể|không có khả năng)"
    r"|không được (?:lập trình|thiết kế|tạo ra)[^.!?\n]{0,80}để"
    r"|(?:tôi|mình)\s+chỉ là (?:một\s+)?mô hình ngôn ngữ"
    r"|nằm ngoài khả năng[^.!?\n]{0,80}(?:lập trình|thiết kế|tạo ra)"
    r"|chỉ có thể tạo văn bản"
    r"|\bI\s+(?:cannot|can't|can’t|am unable to|won't|will not)\s+"
    r"(?:help|assist|comply|fulfil|fulfill|perform|translate|provide|process)"
    r"|(?:language model|text.based AI)[^.!?\n]{0,160}(?:beyond|outside|unable|cannot)"
    r"|(?:我无法|我不能|无法协助|无法帮助|不能帮助)", re.IGNORECASE)
_TS_OBJECT_RE = re.compile(r'\{\s*"translated_sentences"\s*:')
_STRING_CLOSE_AFTER = frozenset(',:}]\x00')


class ResponseShapeError(ValueError):
    """Preserve the classification across parser/caller exception boundaries."""
    def __init__(self, kind: str, detail: str = ""):
        self.kind = kind
        super().__init__(kind + (": " + detail if detail else ""))


class _IncompleteJSON(Exception):
    pass


class _MalformedJSON(Exception):
    pass


class _JSONPrefix:
    """Recognize JSON grammar without decoding it or guessing missing characters.

    EOF at a legal continuation is truncated; an illegal token is malformed,
    even when a naive brace counter would report unclosed braces/quotes.
    """
    def __init__(self, text):
        self.text, self.i = text, 0

    def peek(self):
        while self.i < len(self.text) and self.text[self.i].isspace():
            self.i += 1
        if self.i == len(self.text):
            raise _IncompleteJSON
        return self.text[self.i]

    def string(self):
        self.i += 1
        while self.i < len(self.text):
            ch = self.text[self.i]
            self.i += 1
            if ch == '"':
                return
            if ord(ch) < 32:
                raise _MalformedJSON
            if ch == '\\':
                if self.i == len(self.text):
                    raise _IncompleteJSON
                escape = self.text[self.i]
                self.i += 1
                if escape == 'u':
                    for _ in range(4):
                        if self.i == len(self.text):
                            raise _IncompleteJSON
                        if self.text[self.i] not in '0123456789abcdefABCDEF':
                            raise _MalformedJSON
                        self.i += 1
                elif escape not in '"\\/bfnrt':
                    raise _MalformedJSON
        raise _IncompleteJSON

    def value(self, depth=0):
        if depth > 128:
            raise _MalformedJSON
        ch = self.peek()
        if ch == '"':
            self.string()
        elif ch in '{[':
            close = '}' if ch == '{' else ']'
            self.i += 1
            if self.peek() == close:
                self.i += 1
                return
            while True:
                if ch == '{':
                    if self.peek() != '"':
                        raise _MalformedJSON
                    self.string()
                    if self.peek() != ':':
                        raise _MalformedJSON
                    self.i += 1
                self.value(depth + 1)
                sep = self.peek()
                self.i += 1
                if sep == close:
                    return
                if sep != ',':
                    raise _MalformedJSON
        elif ch in '-0123456789':
            match = re.match(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?', self.text[self.i:])
            if not match:
                if self.text[self.i:] == '-':
                    raise _IncompleteJSON
                raise _MalformedJSON
            self.i += len(match.group())
            tail = self.text[self.i:]
            if re.fullmatch(r'(?:\.|[eE][+-]?)', tail):
                raise _IncompleteJSON
        else:
            for literal in ('true', 'false', 'null'):
                rest = self.text[self.i:]
                if rest.startswith(literal):
                    self.i += len(literal)
                    return
                if literal.startswith(rest):
                    raise _IncompleteJSON
            raise _MalformedJSON


def _next_nonspace(text, i):
    while i < len(text) and text[i] in ' \t\r\n':
        i += 1
    return text[i] if i < len(text) else '\x00'


def _escape_interior_quotes_and_controls(text: str) -> str:
    """Escape raw quotes/newlines inside JSON strings. Do not invent braces.

    A `"` closes a string only when the next non-space is `,` `:` `}` `]` or
    EOF. Any other `"` is dialogue punctuation that Gemini left unescaped.
    """
    out = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            i += 1
            continue
        if ch == '\\':
            out.append(ch)
            i += 1
            if i < n:
                out.append(text[i])
                i += 1
            continue
        if ch == '"':
            nxt = _next_nonspace(text, i + 1)
            if nxt in _STRING_CLOSE_AFTER:
                out.append('"')
                in_string = False
            else:
                out.append('\\"')
            i += 1
            continue
        if ch == '\r':
            out.append('\\n')
            i += 1
            if i < n and text[i] == '\n':
                i += 1
            continue
        if ch == '\n':
            out.append('\\n')
            i += 1
            continue
        if ch == '\t':
            out.append('\\t')
            i += 1
            continue
        if ord(ch) < 32:
            out.append('\\u%04x' % ord(ch))
            i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _try_json_prefix(text: str):
    recovered = _escape_interior_quotes_and_controls(text)
    parser = _JSONPrefix(recovered)
    try:
        parser.value()
    except _IncompleteJSON:
        return TRUNCATED_JSON, None, recovered
    except _MalformedJSON:
        return MALFORMED_JSON, None, recovered
    return VALID_JSON_CANDIDATE, recovered[:parser.i], recovered


def _response_shape(raw):
    text = str(raw or '').strip()
    diag = dict(length=len(text), first=text[:300], last=text[-300:],
                fence=text.startswith('```'), json_open='{' in text,
                json_close=text.endswith('}'))
    if not text:
        return EMPTY_RESPONSE, None, diag
    body = re.sub(r'^```(?:json)?\s*', '', text, count=1, flags=re.IGNORECASE)
    body = re.sub(r'\s*```\s*$', '', body).strip()
    # Refusal words INSIDE a JSON value are subtitle data, not a model refusal.
    if not body.startswith(('{', '[')):
        prefix = re.split(r'(?=[{\[])', body, maxsplit=1)[0]
        if _RATE_RE.search(prefix) or _UI_RE.search(prefix):
            diag['rate_limit'] = bool(_RATE_RE.search(prefix))
            return UI_ERROR_RESPONSE, None, diag
        if _REFUSAL_RE.search(prefix):
            return MODEL_REFUSAL, None, diag
    # Embedded objects need a plausible JSON key; a prose brace is not evidence
    # of truncation. Arrays/objects at the start are explicit JSON attempts.
    start = 0 if body.startswith(('{', '[')) else None
    if start is None:
        match = re.search(r'\{\s*(?:"[^"\n]*"\s*:|\})|\[\s*(?:\{|\[|\])', body)
        start = match.start() if match else None
    if start is None:
        return NON_JSON_RESPONSE, None, diag

    def _accept(kind, snippet, recovered, orig, restart=False):
        leftover = orig[len(snippet):] if recovered == orig else ''
        diag['recovered'] = recovered != orig
        diag['restart'] = restart
        diag['wrapped'] = text.startswith('```')
        diag['extra_text'] = bool(body[:start].strip() or leftover.strip() or restart)
        return kind, snippet, diag

    positions = [m.start() for m in _TS_OBJECT_RE.finditer(body)]
    for pos in reversed(positions):
        orig = body[pos:]
        kind, snippet, recovered = _try_json_prefix(orig)
        if kind == VALID_JSON_CANDIDATE:
            return _accept(kind, snippet, recovered, orig, restart=pos != start)
    orig = body[start:]
    kind, snippet, recovered = _try_json_prefix(orig)
    if kind == VALID_JSON_CANDIDATE:
        return _accept(kind, snippet, recovered, orig, restart=False)
    return kind, body[start:] if kind == MALFORMED_JSON else None, diag


def classify_response_shape(raw: str) -> str:
    """Classify before json.loads; no parsing of refusal/empty/plain UI text."""
    return _response_shape(raw)[0]
_SEND_TOKENS = (
    "chưa gửi được", "chèn nội dung", "không thấy ô nhập",
    "không gửi được sau",
)
_TIMEOUT_TOKENS = ("hết thời gian chờ",)
_GATE_TOKENS = (
    "đổi/mất/lặp chữ số", "thiếu tên glossary", "entity ngoài",
    "keep_source", "tên chưa chắc", "source_ids", "còn chữ nguồn",
)


def classify_exception(exc: BaseException, raw: str = "") -> str:
    if getattr(exc, "kind", "") in {
            "UI_ERROR_RESPONSE", "BROWSER_SESSION_UNHEALTHY",
            "SEND_ACK_TIMEOUT", "RESPONSE_DETECTION_FAILURE", "RESPONSE_START_TIMEOUT",
            "RESPONSE_COMPLETION_TIMEOUT", "RESPONSE_EXTRACTION_FAILURE"}:
        return exc.kind
    if isinstance(exc, ResponseShapeError):
        return exc.kind
    msg = str(exc or "")
    low = msg.lower()
    if any(tok in msg for tok in _SEND_TOKENS):
        return SEND_FAILURE
    if any(tok in msg for tok in _TIMEOUT_TOKENS):
        return RESPONSE_TIMEOUT
    if isinstance(exc, json.JSONDecodeError) or "expecting" in low:
        kind, _, _ = inspect_raw(raw)
        return kind if kind != VALID_JSON else INVALID_JSON
    if any(tok in msg for tok in _GATE_TOKENS):
        return SEMANTIC_GATE_FAILURE
    if "schema" in low or "thiếu translated" in msg or "sai số cue" in msg:
        return SCHEMA_FAILURE
    kind, _, _ = inspect_raw(raw)
    if kind not in {EMPTY_RESPONSE, VALID_JSON, MARKDOWN_WRAPPED_JSON, EXTRA_TEXT_JSON}:
        return kind
    return SCHEMA_FAILURE


def _decode(snippet):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("JSON duplicate key: " + key)
            obj[key] = value
        return obj

    try:
        return json.loads(snippet, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("JSON NaN")))
    except (ValueError, RecursionError) as exc:
        raise ResponseShapeError(MALFORMED_JSON, str(exc)) from exc


def inspect_raw(raw: str) -> Tuple[str, Optional[str], Optional[dict]]:
    """Compatibility inspection: shape first, then strict syntax validation."""
    kind, snippet, diag = _response_shape(raw)
    if kind != VALID_JSON_CANDIDATE:
        return kind, snippet, diag
    try:
        _decode(snippet)
    except ResponseShapeError:
        return MALFORMED_JSON, snippet, diag
    if diag.get("wrapped"):
        return MARKDOWN_WRAPPED_JSON, snippet, diag
    if diag.get("extra_text"):
        return EXTRA_TEXT_JSON, snippet, diag
    return VALID_JSON, snippet, diag


def extract_object(raw: str) -> dict:
    """Unwrap locally; never attempt json.loads on a rejected response shape."""
    kind, snippet, _ = _response_shape(raw)
    if kind != VALID_JSON_CANDIDATE:
        raise ResponseShapeError(kind)
    obj = _decode(snippet)
    if not isinstance(obj, dict):
        raise ResponseShapeError(SCHEMA_FAILURE, "expected JSON object")
    return obj


def json_translation_complete(text: str) -> bool:
    """True when a translation/align/quality JSON object is fully closed."""
    try:
        obj = extract_object(text)
    except (ValueError, TypeError):
        return False
    if obj.get("translated_sentences"):
        return True
    if isinstance(obj.get("cues"), list) and obj["cues"]:
        return True
    return False


def dump_parse_failure(cache_path: Optional[str], kind: str, raw: str, error: str) -> None:
    if not cache_path:
        return
    try:
        root = Path(cache_path).parent / "_tmp" / "translation-debug"
        root.mkdir(parents=True, exist_ok=True)
        seen, snippet, diag = inspect_raw(raw)
        rec = dict(
            ts=int(time.time()),
            kind=kind or seen,
            error=str(error or "")[:240],
            extracted_len=len(snippet or ""),
            **diag,
        )
        name = "%d-%s.json" % (rec["ts"], str(rec["kind"]).lower())
        (root / name).write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raw_text = str(raw or "")[:100000]
        if raw_text:
            (root / name.replace(".json", ".txt")).write_text(
                raw_text, encoding="utf-8"
            )
    except OSError:
        pass
