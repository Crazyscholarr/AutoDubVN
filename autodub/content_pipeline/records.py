"""Chuẩn hóa bản ghi kho truyện (JSON/SQLite) thành schema nội bộ."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB = os.path.join(ROOT, "data", "content_ideas.sqlite")
DEFAULT_OUTPUT_DIR = os.path.join(ROOT, "output", "content_plans")


FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "id": ("id", "story_id", "uuid"),
    "title_original": (
        "titleOriginal", "title_original", "original_title", "title", "name",
        "tieu_de_goc", "tiêu đề gốc"),
    "title_localized": (
        "titleLocalized", "title_localized", "localized_title", "title_vi",
        "tieu_de_viet", "tiêu đề việt"),
    "content": (
        "localizedContent", "localized_content", "cleanedContent",
        "cleaned_content", "rawContent", "raw_content", "content", "body",
        "text", "story", "noi_dung", "nội dung"),
    "raw_content": (
        "rawContent", "raw_content", "content", "body", "text", "story",
        "noi_dung", "nội dung"),
    "source": ("source", "source_name", "platform", "nguon", "nguồn"),
    "source_url": ("sourceUrl", "source_url", "url", "link", "permalink"),
    "author": ("author", "username", "user", "tac_gia", "tác giả"),
    "published_at": (
        "publishedAt", "published_at", "createdAt", "created_at", "date",
        "ngay_dang", "ngày đăng"),
    "language": ("language", "lang", "ngon_ngu", "ngôn ngữ"),
    "tags": ("tags", "tag", "labels", "keywords"),
    "notes": ("notes", "note", "crawlNote", "crawl_note", "ghi_chu"),
}


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<(script|style|noscript|iframe)[^>]*>[\s\S]*?</\1>", " ", text,
                  flags=re.I)
    text = re.sub(r"<br\s*/?>|</(?:p|div|li|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _lookup(row: Dict[str, Any], field: str, default: Any = "") -> Any:
    folded = {str(k).casefold(): v for k, v in row.items()}
    for alias in FIELD_ALIASES[field]:
        if alias in row and row[alias] not in (None, ""):
            return row[alias]
        value = folded.get(alias.casefold())
        if value not in (None, ""):
            return value
    return default


def _list_value(value: Any) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, dict):
        return [str(x).strip() for x in value.values() if str(x).strip()]
    return [x.strip() for x in re.split(r"[,;|\n]+", str(value or "")) if x.strip()]


def detect_language(text: str) -> str:
    sample = str(text or "")[:2000]
    cjk = len(re.findall(r"[\u4e00-\u9fff]", sample))
    vi = len(re.findall(
        r"[ăâêôơưđáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]",
        sample, flags=re.I))
    latin = len(re.findall(r"[A-Za-z]", sample))
    if cjk > 12 and cjk > vi:
        return "zh"
    if vi > 6:
        return "vi"
    return "en" if latin > 20 else "vi"


def count_words(text: str, language: str = "") -> int:
    lang = language or detect_language(text)
    if lang == "zh":
        # 1 chữ Hán thường nở thành khoảng 1.3-1.6 từ khi Việt hóa.
        cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
        latin = len(re.findall(r"\b\w+\b", text, flags=re.UNICODE))
        return max(latin, int(cjk * 1.45))
    return len(re.findall(r"\b\w+\b", text, flags=re.UNICODE))


def _stable_id(source: str, url: str, title: str, content: str) -> str:
    digest = hashlib.sha1(
        (source + "|" + url + "|" + title + "|" + content[:500]).encode("utf-8", "ignore")
    ).hexdigest()[:16]
    return "idea_" + digest


def canonical_source_url(value: str) -> str:
    """Chuẩn hóa URL nguồn để nhận ra cùng một chuyện qua link có tracking."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        if not parts.hostname:
            return raw.rstrip("/")
        blocked = {"spm_id_from", "vd_source", "from", "source", "share_code"}
        query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=False)
                 if not key.lower().startswith("utm_") and key.lower() not in blocked]
        host = (parts.hostname or "").lower()
        if parts.port:
            host += ":" + str(parts.port)
        path = re.sub(r"/{2,}", "/", parts.path or "/").rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower() or "https", host, path,
                           urlencode(query, doseq=True), ""))
    except Exception:
        return raw.split("#", 1)[0].rstrip("/")


def normalize_record(row: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
    title_original = clean_text(_lookup(row, "title_original") or f"Truyện {index + 1}")
    title_localized = clean_text(_lookup(row, "title_localized"))
    raw_content = clean_text(_lookup(row, "raw_content"))
    content = clean_text(_lookup(row, "content") or raw_content)
    if not raw_content:
        raw_content = content
    source = clean_text(_lookup(row, "source") or "manual")
    source_url = str(_lookup(row, "source_url") or "").strip()
    language = str(_lookup(row, "language") or detect_language(raw_content)).lower()
    if language.startswith("zh"):
        language = "zh"
    elif language.startswith("en"):
        language = "en"
    else:
        language = "vi"
    record_id = str(_lookup(row, "id") or "").strip()
    if not record_id:
        record_id = _stable_id(source, source_url, title_original, raw_content)
    created = str(_lookup(row, "published_at") or "").strip()
    return {
        "id": record_id,
        "title_original": title_original,
        "title_localized": title_localized,
        "source": source,
        "source_url": source_url,
        "source_fingerprint": canonical_source_url(source_url),
        "author": clean_text(_lookup(row, "author")),
        "published_at": created,
        "language": language,
        "raw_content": raw_content,
        "content": content,
        "word_count": int(row.get("wordCount") or row.get("word_count") or
                          count_words(content, "vi" if title_localized else language)),
        "source_tags": _list_value(_lookup(row, "tags")),
        "notes": clean_text(_lookup(row, "notes")),
        "selected": bool(row.get("selected", False)),
        "status": str(row.get("status") or "raw"),
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }


def _json_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("stories", "items", "data", "results", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return [payload]
    return []


def load_json(path: str, limit: int = 0) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig") as handle:
        rows = _json_rows(json.load(handle))
    if limit > 0:
        rows = rows[:limit]
    return [normalize_record(row, i) for i, row in enumerate(rows)]


def _sqlite_table(conn: sqlite3.Connection) -> str:
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()]
    aliases = {a.casefold() for key in ("content", "raw_content")
               for a in FIELD_ALIASES[key]}
    scored: List[Tuple[int, str]] = []
    for table in tables:
        quoted = table.replace('"', '""')
        cols = [str(row[1]) for row in conn.execute(
            f'PRAGMA table_info("{quoted}")').fetchall()]
        score = sum(1 for col in cols if col.casefold() in aliases)
        if score:
            score += 2 if table.casefold() in {"stories", "story", "articles"} else 0
            scored.append((score, table))
    if not scored:
        raise ValueError("SQLite không có bảng chứa cột nội dung truyện phù hợp.")
    return max(scored)[1]


def load_sqlite(path: str, limit: int = 0, table: str = "") -> List[Dict[str, Any]]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        chosen = table or _sqlite_table(conn)
        quoted = chosen.replace('"', '""')
        sql = f'SELECT * FROM "{quoted}"'
        params: Tuple[Any, ...] = ()
        if limit > 0:
            sql += " LIMIT ?"
            params = (limit,)
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    return [normalize_record(row, i) for i, row in enumerate(rows)]


def load_source(path: str, limit: int = 0, table: str = "") -> List[Dict[str, Any]]:
    source = os.path.abspath(os.path.expandvars(str(path or "").strip().strip('"')))
    if not os.path.isfile(source):
        raise FileNotFoundError("Không tìm thấy file dữ liệu: " + source)
    ext = os.path.splitext(source)[1].lower()
    if ext in {".sqlite", ".sqlite3", ".db"}:
        return load_sqlite(source, limit=limit, table=table)
    if ext == ".json":
        return load_json(source, limit=limit)
    raise ValueError("Chỉ hỗ trợ file JSON, SQLite, SQLite3 hoặc DB.")
