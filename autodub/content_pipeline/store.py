"""Kho SQLite ý tưởng nội dung và bộ lọc bản ghi công khai."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from .records import DEFAULT_DB, canonical_source_url


def record_was_used(record: Dict[str, Any]) -> bool:
    if not isinstance(record, dict):
        return False
    try:
        usage_count = int(record.get("usage_count") or 0)
    except (TypeError, ValueError):
        usage_count = 0
    if str(record.get("used_at") or "").strip() or usage_count > 0:
        return True
    status = str(record.get("status") or "").strip().casefold()
    # "Đang viết" có thể là trạng thái nhập sẵn từ kho cũ và không chứng minh
    # người dùng đã thực sự sản xuất. Chỉ khóa khi có dấu thời gian/lượt dùng,
    # hoặc trạng thái hoàn tất rõ ràng.
    return status in {"đã dùng", "used", "rendered", "published"}


class ContentStore:
    def __init__(self, path: str = DEFAULT_DB):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        conn = self.connect()
        try:
            with conn:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS content_ideas (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    selected INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_content_selected "
                             "ON content_ideas(selected, updated_at)")
        finally:
            conn.close()

    def upsert(self, records: Sequence[Dict[str, Any]], overwrite: bool = False) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        count = 0
        conn = self.connect()
        try:
            with conn:
                for record in records:
                    item = dict(record)
                    record_id = str(item.get("id") or "").strip()
                    if not record_id:
                        continue
                    old = conn.execute(
                        "SELECT payload, selected, created_at FROM content_ideas WHERE id=?",
                        (record_id,)).fetchone()
                    if old:
                        previous = json.loads(old["payload"])
                        if overwrite:
                            # Người dùng chủ động Phân tích lại: thay kết quả AI cũ,
                            # nhưng trạng thái tick vẫn do cột selected quản lý.
                            previous.update(item)
                        else:
                            # Nhập lại cùng kho nguồn không được xoá phần đã phân
                            # tích/chỉnh tay; chỉ bổ sung trường còn thiếu.
                            for key, value in item.items():
                                if key not in previous or previous.get(key) in (None, "", []):
                                    previous[key] = value
                        item = previous
                        selected = int(old["selected"])
                        created = str(old["created_at"])
                    else:
                        selected = int(bool(item.get("selected")))
                        created = now
                    item["selected"] = bool(selected)
                    conn.execute("""
                        INSERT INTO content_ideas(id,payload,selected,created_at,updated_at)
                        VALUES(?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,
                          selected=excluded.selected, updated_at=excluded.updated_at
                    """, (record_id, json.dumps(item, ensure_ascii=False), selected,
                          created, now))
                    count += 1
        finally:
            conn.close()
        return count

    def list(self, search: str = "", selected_only: bool = False,
             limit: int = 1000) -> List[Dict[str, Any]]:
        clauses, params = [], []
        if selected_only:
            clauses.append("selected=1")
        sql = "SELECT payload, selected FROM content_ideas"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(10000, int(limit or 1000))))
        conn = self.connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        items = []
        needle = str(search or "").casefold().strip()
        for row in rows:
            item = json.loads(row["payload"])
            item["selected"] = bool(row["selected"])
            if needle:
                haystack = " ".join(str(item.get(k) or "") for k in (
                    "title_original", "title_localized", "source", "primary_genre",
                    "emotion", "series")).casefold()
                if needle not in haystack:
                    continue
            items.append(item)
        return items

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        conn = self.connect()
        try:
            row = conn.execute("SELECT payload,selected FROM content_ideas WHERE id=?",
                               (str(record_id),)).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        item = json.loads(row["payload"])
        item["selected"] = bool(row["selected"])
        return item

    def source_history(self, urls: Sequence[str] = ()) -> Dict[str, Dict[str, Any]]:
        """Lập chỉ mục URL đã tải/đã dùng để chặn sản xuất trùng chuyện."""
        wanted = {canonical_source_url(x) for x in urls if canonical_source_url(x)}
        if not wanted:
            return {}
        conn = self.connect()
        try:
            keys = list(wanted)
            placeholders = ",".join("?" * len(keys))
            try:
                rows = conn.execute(
                    "SELECT payload, selected FROM content_ideas "
                    "WHERE json_extract(payload, '$.source_fingerprint') "
                    f"IN ({placeholders})",
                    keys,
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    "SELECT payload, selected FROM content_ideas LIMIT 10000"
                ).fetchall()
        finally:
            conn.close()
        history: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            item = json.loads(row["payload"])
            item["selected"] = bool(row["selected"])
            key = str(item.get("source_fingerprint") or
                      canonical_source_url(item.get("source_url") or ""))
            if not key or (wanted and key not in wanted):
                continue
            previous = history.get(key)
            used = record_was_used(item)
            value = {
                "id": item.get("id"),
                "title": item.get("title_localized") or item.get("title_original") or "",
                "source_url": item.get("source_url") or "",
                "used": used,
                "used_at": item.get("used_at") or "",
                "usage_count": (int(item.get("usage_count") or 0)
                                if str(item.get("usage_count") or "").isdigit()
                                else (1 if used else 0)),
                "status": item.get("status") or "",
            }
            if previous is None or (used and not previous.get("used")):
                history[key] = value
        return history

    def patch(self, record_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        item = self.get(record_id)
        if not item:
            raise KeyError("Không tìm thấy ý tưởng.")
        allowed = {
            "title_localized", "primary_genre", "emotion", "hook_score",
            "plot_twist_score", "themes", "archetypes", "keywords", "tags",
            "series", "titles", "descriptions", "thumbnails", "outline",
            "main_hook", "high_tension_scenes", "plot_twists", "must_change",
            "rewrite_brief",
            "main_characters", "production_ease", "recommendation",
            "best_publish_time", "notes", "status", "content", "selected",
            "used_at", "usage_count", "last_used_title",
        }
        for key, value in values.items():
            if key in allowed:
                item[key] = value
        self.upsert([item], overwrite=True)
        if "selected" in values:
            self.set_selected([record_id], bool(values["selected"]))
        return self.get(record_id) or item

    def set_selected(self, ids: Sequence[str], selected: bool) -> int:
        clean_ids = [str(x) for x in ids if str(x).strip()]
        if not clean_ids:
            return 0
        marks = ",".join("?" for _ in clean_ids)
        conn = self.connect()
        try:
            with conn:
                rows = conn.execute(
                    f"SELECT id,payload FROM content_ideas WHERE id IN ({marks})",
                    clean_ids).fetchall()
                for row in rows:
                    item = json.loads(row["payload"])
                    item["selected"] = bool(selected)
                    conn.execute(
                        "UPDATE content_ideas SET selected=?,payload=?,updated_at=? WHERE id=?",
                        (int(selected), json.dumps(item, ensure_ascii=False),
                         datetime.now().isoformat(timespec="seconds"), row["id"]))
        finally:
            conn.close()
        return len(rows)

    def delete_many(self, ids: Sequence[str], protect_used: bool = True
                    ) -> Dict[str, List[str]]:
        """Xóa nhiều mục; mặc định không cho xóa dấu vết chuyện đã dùng."""
        clean_ids = list(dict.fromkeys(
            str(value).strip() for value in ids if str(value).strip()))
        result = {"deleted": [], "protected": [], "not_found": []}
        if not clean_ids:
            return result
        marks = ",".join("?" for _ in clean_ids)
        conn = self.connect()
        try:
            with conn:
                rows = conn.execute(
                    f"SELECT id,payload FROM content_ideas WHERE id IN ({marks})",
                    clean_ids).fetchall()
                found = {str(row["id"]): row for row in rows}
                for record_id in clean_ids:
                    row = found.get(record_id)
                    if row is None:
                        result["not_found"].append(record_id)
                        continue
                    item = json.loads(row["payload"])
                    if protect_used and record_was_used(item):
                        result["protected"].append(record_id)
                        continue
                    conn.execute("DELETE FROM content_ideas WHERE id=?", (record_id,))
                    result["deleted"].append(record_id)
        finally:
            conn.close()
        return result


def public_record(record: Dict[str, Any], include_content: bool = False) -> Dict[str, Any]:
    item = dict(record)
    if not include_content:
        item["content_preview"] = str(item.get("content") or item.get("raw_content") or "")[:600]
        item.pop("content", None)
        item.pop("raw_content", None)
    return item
