"""List / cập nhật / chọn / xóa và xuất kho ý tưởng."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, Iterable

from ...content_pipeline import (
    ContentStore, export_plan, heuristic_analysis, load_source, public_record,
    record_was_used, sample_records,
)
from ..state import STATE, _LOCK, _log
from .common import (
    JsonResult, _api, _begin, _body_ids, _configured_provider, _finish,
    _resolve_path, _selected_records, _state, _sync_content_calendar,
)


def _settings():
    return _api()._settings()


def _store():
    return _api()._store()


def submit_job(*args, **kwargs):
    return _api().submit_job(*args, **kwargs)


def _backfill_legacy_analysis(store: ContentStore, items: Iterable[Dict]) -> list:
    """Bù schema mới cho bài đã tải trước khi có hồ sơ viết lại.

    Chỉ bù các bản heuristic/offline; kết quả AI cũ thiếu trường sẽ được phân
    tích AI lại khi người dùng bấm viết, tránh gắn nhầm hồ sơ chung vào bản AI.
    """
    out = []
    keys = ("title_localized", "main_hook", "high_tension_scenes",
            "plot_twists", "must_change", "rewrite_brief")
    for source in items:
        item = dict(source)
        provider = str(item.get("analysis_provider") or "").lower()
        is_offline = not provider or any(
            token in provider for token in ("heuristic", "offline", "fallback"))
        missing = [key for key in keys if not item.get(key)]
        titles_bad = len(list(item.get("titles") or [])) != 8
        changed = False
        if is_offline and (missing or titles_bad):
            fallback = heuristic_analysis(item)
            for key in keys:
                if not item.get(key) and fallback.get(key):
                    item[key] = fallback[key]
                    changed = True
            if len(list(item.get("titles") or [])) != 8:
                item["titles"] = fallback["titles"]
                changed = True
        if changed:
            store.upsert([item], overwrite=True)
        out.append(item)
    return out


def api_content_list(query: Dict) -> JsonResult:
    try:
        search = str((query.get("search") or [""])[0])
        selected = str((query.get("selected") or ["0"])[0]).lower() in {"1", "true", "yes"}
        limit = int((query.get("limit") or ["1000"])[0] or 1000)
        store = _store()
        items = _backfill_legacy_analysis(
            store, store.list(search=search, selected_only=selected, limit=limit))
        settings = _settings()
        return {
            "items": [public_record(x) for x in items],
            "count": len(items),
            "selected_count": sum(1 for x in items if x.get("selected")),
            "database": settings["database"],
            "output_dir": settings["output_dir"],
            "default_provider": settings["provider"],
            "provider_configured": _configured_provider(
                settings["config"], settings["provider"]),
        }, 200
    except Exception as exc:
        return {"error": str(exc)}, 500


def api_content_item(query: Dict) -> JsonResult:
    record_id = str((query.get("id") or [""])[0]).strip()
    store = _store()
    found = store.get(record_id)
    item = (_backfill_legacy_analysis(store, [found])[0] if found else None)
    return (({"item": public_record(item, include_content=True)}, 200)
            if item else ({"error": "Không tìm thấy ý tưởng."}, 404))


def api_content_import(body: Dict) -> JsonResult:
    path = str(body.get("path") or "").strip()
    if not path:
        return {"error": "Hãy chọn file JSON hoặc SQLite."}, 400
    if not _begin("import", "Đang đọc và chuẩn hoá kho truyện…"):
        return {"error": "Kho ý tưởng đang chạy một tác vụ khác."}, 409

    def work() -> None:
        try:
            rows = load_source(path, limit=max(0, int(body.get("limit") or 0)),
                               table=str(body.get("table") or ""))
            count = _store().upsert(rows)
            _finish(f"Đã nhập {count} truyện từ {os.path.basename(path)}.",
                    progress=100, done=count, total=count, count=count,
                    input_path=os.path.abspath(path))
            _log(f"Kho ý tưởng: đã nhập {count} truyện.", "ok")
        except Exception as exc:
            _finish("Nhập dữ liệu lỗi.", error=str(exc))
            _log(f"Nhập kho ý tưởng lỗi: {exc}", "err")

    submit_job(work, name="Nhập kho ý tưởng", resource="sqlite",
               foreground=False, metadata={"kind": "content_import"})
    return {"ok": True, "async": True}, 200


def api_content_sample(_body: Dict = None) -> JsonResult:
    if not _begin("sample", "Đang tạo dữ liệu mẫu…"):
        return {"error": "Kho ý tưởng đang chạy một tác vụ khác."}, 409

    def work() -> None:
        try:
            rows = []
            for record in sample_records():
                item = dict(record)
                item.update(heuristic_analysis(item))
                rows.append(item)
            count = _store().upsert(rows)
            _finish(f"Đã tạo {count} ý tưởng mẫu để kiểm thử.", progress=100,
                    done=count, total=count, count=count)
        except Exception as exc:
            _finish("Tạo dữ liệu mẫu lỗi.", error=str(exc))

    submit_job(work, name="Tạo dữ liệu nội dung mẫu", resource="sqlite",
               foreground=False, metadata={"kind": "content_sample"})
    return {"ok": True, "async": True}, 200


def api_content_update(body: Dict) -> JsonResult:
    try:
        record_id = str(body.get("id") or "").strip()
        values = body.get("values") if isinstance(body.get("values"), dict) else {}
        item = _store().patch(record_id, values)
        _state(status="Đã lưu chỉnh sửa ý tưởng.")
        return {"ok": True, "item": public_record(item, include_content=True)}, 200
    except KeyError as exc:
        return {"error": str(exc)}, 404
    except Exception as exc:
        return {"error": str(exc)}, 400


def api_content_select(body: Dict) -> JsonResult:
    ids = body.get("ids") or []
    if isinstance(ids, str):
        ids = [ids]
    count = _store().set_selected(ids, bool(body.get("selected", True)))
    _state(selected_count=len(_store().list(selected_only=True, limit=10000)))
    return {"ok": True, "count": count}, 200


def api_content_delete(body: Dict) -> JsonResult:
    """Xóa mục chưa dùng khỏi kho; lịch sử đã dùng luôn được bảo vệ."""
    ids = _body_ids(body)
    if not ids:
        return {"error": "Hãy chọn ít nhất một mục cần xóa."}, 400
    with _LOCK:
        if STATE["content_pipeline"].get("working"):
            return {"error": "Kho ý tưởng đang chạy tác vụ khác; chưa thể xóa."}, 409
    try:
        store = _store()
        result = store.delete_many(ids, protect_used=True)
        deleted = len(result["deleted"])
        protected = len(result["protected"])
        missing = len(result["not_found"])
        status = f"Đã xóa {deleted} mục khỏi kho."
        if protected:
            status += f" Giữ lại {protected} mục đã dùng để chống lặp."
        if missing:
            status += f" {missing} mục không còn tồn tại."
        remaining = len(store.list(limit=10000))
        _state(status=status, count=remaining, deleted_count=deleted,
               protected_count=protected,
               selected_count=len(store.list(selected_only=True, limit=10000)))
        _log(status, "ok" if deleted else "warn")
        return {"ok": True, **result, "count": remaining, "status": status}, 200
    except Exception as exc:
        return {"error": "Xóa mục trong kho lỗi: " + str(exc)}, 500


def api_content_export(body: Dict) -> JsonResult:
    if not _begin("export", "Đang tạo Excel và JSON kế hoạch…"):
        return {"error": "Kho ý tưởng đang chạy một tác vụ khác."}, 409
    store = _store()
    rows = _selected_records(store, body.get("ids") or [], bool(body.get("all")))
    if not rows:
        _finish("Không có ý tưởng để xuất.", error="Hãy chọn ít nhất một ý tưởng.")
        return {"error": "Không có ý tưởng để xuất."}, 400
    output_dir = _resolve_path(str(body.get("output_dir") or ""), _settings()["output_dir"])

    def work() -> None:
        try:
            result = export_plan(rows, output_dir=output_dir,
                                 stem=str(body.get("stem") or "ke_hoach_noi_dung"))
            _finish(f"Đã xuất {len(rows)} ý tưởng sang Excel và JSON.", progress=100,
                    done=len(rows), total=len(rows), export_xlsx=result["xlsx"],
                    export_json=result["json"], output_dir=output_dir)
            _log(f"Đã xuất kế hoạch: {result['xlsx']}", "ok")
        except Exception as exc:
            _finish("Xuất kế hoạch lỗi.", error=str(exc))
            _log(f"Xuất kế hoạch lỗi: {exc}", "err")

    submit_job(work, name="Xuất kế hoạch nội dung", resource="sqlite",
               foreground=False, metadata={"kind": "content_export"})
    return {"ok": True, "async": True, "total": len(rows)}, 200


def api_content_use_story(body: Dict) -> JsonResult:
    record_id = str(body.get("id") or "").strip()
    store = _store()
    item = store.get(record_id)
    if not item:
        return {"error": "Không tìm thấy ý tưởng."}, 404
    if record_was_used(item):
        return {"error": "Chuyện này đã có trong lịch sử sử dụng và bị khóa để tránh làm lại."}, 409
    titles = list(item.get("titles") or [])
    descriptions = list(item.get("descriptions") or [])
    title_index = max(0, min(len(titles) - 1, int(body.get("title_index") or 0))) if titles else 0
    description_index = max(0, min(len(descriptions) - 1,
                                   int(body.get("description_index") or 0))) if descriptions else 0
    title = (titles[title_index] if titles else
             item.get("title_localized") or item.get("title_original") or "").strip()
    description = descriptions[description_index] if descriptions else ""
    rewrite_brief = str(item.get("rewrite_brief") or "").strip()
    analysis_provider = str(item.get("analysis_provider") or "").lower()
    chinese_not_ai = (str(item.get("language") or "").lower() == "zh" and
                      (not rewrite_brief or any(token in analysis_provider
                       for token in ("heuristic", "offline", "fallback"))))
    if chinese_not_ai:
        return {"error": ("Truyện Trung chưa có hồ sơ hook/plot twist tiếng Việt. "
                          "Hãy Phân tích bằng Gemini Web hoặc Claude trên Perplexity "
                          "trước khi viết.")}, 409
    with _LOCK:
        manual = STATE["manual"]
        manual.update({
            "content_idea_id": record_id,
            "writer_title": title,
            "youtube_description": description,
            "youtube_tags": list(item.get("tags") or []),
            "content_outline": str(item.get("outline") or ""),
            "rewrite_brief": rewrite_brief,
            "status": "Đã nhận hồ sơ sáng tác; sẵn sàng viết mới bằng Perplexity.",
            "rev": int(manual.get("rev", 0)) + 1,
        })
    updated = store.patch(record_id, {
        "status": "Đang viết",
        "used_at": datetime.now().isoformat(timespec="seconds"),
        "usage_count": int(item.get("usage_count") or 0) + 1,
        "last_used_title": title,
        "selected": False,
    })
    try:
        calendar_path = _sync_content_calendar(
            [updated], final_titles={record_id: title}, status="Đang viết")
        _state(calendar_path=calendar_path)
        _log(f"Đã ghi tiêu đề được chọn vào lịch nội dung: {calendar_path}", "ok")
    except Exception as exc:
        # Không chặn AI Story chỉ vì workbook đang lỗi/không có quyền ghi.
        _log(f"Chưa ghi được lịch nội dung sau khi chọn tiêu đề: {exc}", "warn")
    return {"ok": True, "story": {
        "id": record_id, "title": title, "description": description,
        "tags": list(item.get("tags") or []), "outline": item.get("outline") or "",
        "title_localized": item.get("title_localized") or "",
        "rewrite_brief": rewrite_brief,
        "main_hook": item.get("main_hook") or "",
        "high_tension_scenes": list(item.get("high_tension_scenes") or []),
        "plot_twists": list(item.get("plot_twists") or []),
        "analysis_provider": item.get("analysis_provider") or "",
        "keywords": list(item.get("keywords") or []),
    }}, 200
