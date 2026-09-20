"""Tìm và tải nguồn cho kho ý tưởng."""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict

from ...content_pipeline import (
    analyze_many, analyze_record, canonical_source_url, heuristic_analysis,
    normalize_record, record_was_used,
)
from ..state import STATE, _LOCK, _log
from .common import (
    JsonResult, _activity, _api, _begin, _body_ids, _configured_provider,
    _finish, _provider_status, _state, _sync_content_calendar,
)


def _settings():
    return _api()._settings()


def _store():
    return _api()._store()


def submit_job(*args, **kwargs):
    return _api().submit_job(*args, **kwargs)


def api_content_catalog(_query: Dict = None) -> JsonResult:
    """Nguồn và từ khóa dùng ở màn Tìm & tải nội dung."""
    try:
        from ... import story_sources
        return {"sources": story_sources.reference_catalog(),
                "chinese_keywords": story_sources.chinese_keyword_catalog()}, 200
    except Exception as exc:
        return {"error": str(exc)}, 500


def api_content_search(body: Dict) -> JsonResult:
    keyword = str(body.get("keyword") or "").strip()
    if not keyword:
        return {"error": "Hãy nhập từ khóa tìm chuyện."}, 400
    source_keys = body.get("source_keys") or ["zhihu_yanxuan"]
    if isinstance(source_keys, str):
        source_keys = [x.strip() for x in source_keys.split(",") if x.strip()]
    try:
        limit = max(1, min(50, int(body.get("limit") or 20)))
    except (TypeError, ValueError):
        limit = 20
    if not _begin("source_search", "Đang tìm chuyện thật trên các nguồn đã chọn…"):
        return {"error": "Kho ý tưởng đang chạy một tác vụ khác."}, 409
    _state(search_keyword=keyword, search_source_keys=list(source_keys),
           search_results=[], total=len(source_keys), done=0,
           download_errors=[], download_success=0, download_failed=0)

    def work() -> None:
        try:
            from ... import story_sources
            rows = story_sources.search_web_references(keyword, source_keys, limit)
            topic = str(body.get("topic") or "").strip()
            meaning = str(body.get("meaning") or "").strip()
            for row in rows:
                row.update({"keyword": keyword, "topic": topic, "meaning": meaning})
            if rows:
                _state(total=len(rows), done=0, progress=35,
                       status=f"Đã tìm thấy {len(rows)} link; đang đọc lượt xem và kiểm tra nội dung…")

                def enrich_progress(done: int, total: int, item: Dict) -> None:
                    title = str(item.get("title") or "")[:60]
                    _state(done=done, total=total,
                           progress=round(35 + done * 60 / max(1, total), 1),
                           status=f"Đang xếp hạng {done}/{total}: {title}")

                rows = story_sources.enrich_reference_rows(
                    rows, max_workers=4, progress=enrich_progress,
                    query=keyword)
            history = _store().source_history([row.get("url") or "" for row in rows])
            for row in rows:
                previous = history.get(canonical_source_url(row.get("url") or "")) or {}
                row.update({"selected": False,
                            "already_downloaded": bool(previous),
                            "existing_id": previous.get("id") or "",
                            "used_before": bool(previous.get("used")),
                            "used_at": previous.get("used_at") or "",
                            "download_status": ("Đã dùng" if previous.get("used")
                                                else "Đã có trong kho" if previous
                                                else "Chưa tải")})
            if not rows:
                _finish("Không tìm thấy bài phù hợp. Thử từ khóa khác, đổi nguồn Zhihu, hoặc dán link cụ thể — không dùng dữ liệu mẫu.",
                        progress=100, done=0, total=0, search_results=[])
                return
            used_count = sum(1 for row in rows if row.get("used_before"))
            unreadable = sum(1 for row in rows if not row.get("content_ready"))
            _finish((f"Tìm thấy {len(rows)} bài đúng chủ đề, đã xếp độ liên quan trước lượt đọc"
                     + (f"; khóa {used_count} bài từng dùng" if used_count else "")
                     + (f"; {unreadable} bài chưa đọc được" if unreadable else "") + "."),
                    progress=100, done=len(rows), total=len(rows),
                    search_results=rows)
            _log(f"Kho ý tưởng: tìm thấy {len(rows)} nguồn cho '{keyword}'.", "ok")
        except Exception as exc:
            _finish("Tìm nguồn nội dung lỗi.", error=str(exc), search_results=[])
            _log(f"Tìm nguồn nội dung lỗi: {exc}", "err")

    submit_job(work, name="Tìm nguồn nội dung", resource="network",
               foreground=False, metadata={"kind": "content_search"})
    return {"ok": True, "async": True, "keyword": keyword}, 200


def _download_items(body: Dict) -> list:
    rows = body.get("items") or []
    if isinstance(rows, dict):
        rows = [rows]
    clean = [dict(x) for x in rows if isinstance(x, dict) and
             str(x.get("url") or x.get("source_url") or "").strip()]
    raw_urls = body.get("urls") or []
    if isinstance(raw_urls, str):
        raw_urls = raw_urls.splitlines()
    for raw in raw_urls:
        url = str(raw or "").strip()
        if url:
            clean.append({"url": url, "title": url, "source_name": "Link nhập tay",
                          "selected": True})
    out, seen = [], set()
    for item in clean:
        url = str(item.get("url") or item.get("source_url") or "").strip()
        if url not in seen:
            seen.add(url)
            item["url"] = url
            out.append(item)
    return out[:50]


def api_content_download(body: Dict) -> JsonResult:
    items = _download_items(body)
    if not items:
        return {"error": "Hãy chọn kết quả tìm kiếm hoặc dán link bài viết."}, 400
    store = _store()
    history = store.source_history([item.get("url") or "" for item in items])
    used = [history.get(canonical_source_url(item.get("url") or ""))
            for item in items]
    used = [item for item in used if item and item.get("used")]
    if used:
        names = ", ".join(str(item.get("title") or "")[:45] for item in used[:3])
        return {"error": "Đã chặn chuyện từng dùng, không được sản xuất lại: " + names}, 409
    # Link đã nằm trong kho thì không tải mạng lần nữa. Với một lô trộn, chỉ
    # xử lý các link mới; bài có sẵn vẫn giữ nguyên trong SQLite.
    items = [item for item in items
             if canonical_source_url(item.get("url") or "") not in history]
    if not items:
        return {"error": "Các chuyện đã có trong Kho đã tải; không cần tải lại."}, 409
    if not _begin("source_download", "Đang tải nội dung thật và lưu vào SQLite…"):
        return {"error": "Kho ý tưởng đang chạy một tác vụ khác."}, 409
    try:
        workers = max(1, min(4, int(body.get("concurrency") or 3), len(items)))
    except (TypeError, ValueError):
        workers = min(3, len(items))
    cookie = str(body.get("cookie") or "").strip()
    settings = _settings()
    provider = str(body.get("provider") or settings["provider"] or "auto").lower()
    auto_analyze = body.get("auto_analyze", True) is not False
    use_ai = auto_analyze and provider not in {"heuristic", "offline", "none"}
    provider_info = _provider_status(settings["config"], provider)
    browser_analysis = use_ai and provider_info["provider"] in {
        "browser", "perplexity_browser"}
    provider_label = str(provider_info["provider"] or provider).upper()
    model_label = str(provider_info["provider_model"] or "")
    provider_text = (provider_label + (f" · {model_label}" if model_label else ""))
    initial_warning = ""
    if use_ai and not provider_info["provider_configured"]:
        initial_warning = (f"{provider_text} chưa có API key hợp lệ; "
                           "bài sẽ được phân tích offline. Mở Cài đặt → API để sửa.")
    _state(total=len(items), done=0, download_success=0, download_failed=0,
           download_errors=[], provider=provider_info["provider"],
           provider_model=model_label,
           provider_configured=provider_info["provider_configured"],
           provider_offline=provider_info["provider_offline"],
           warning=initial_warning,
           status=(f"Đang tải toàn văn; sau đó phân tích bằng {provider_text}…"
                   if use_ai else "Đang tải toàn văn; sau đó phân tích offline…"
                   if auto_analyze else "Đang tải toàn văn…"))
    _activity((f"Bắt đầu {len(items)} bài · AI: {provider_text}."
               if use_ai else f"Bắt đầu tải {len(items)} bài · phân tích offline."
               if auto_analyze else f"Bắt đầu tải {len(items)} bài · không phân tích."),
              "warning" if initial_warning else "info", "prepare")
    if initial_warning:
        _activity(initial_warning, "warning", "provider")

    def work() -> None:
        from ... import story_sources
        store = _store()
        successes, errors, done = [], [], 0
        success_indices = {}
        item_progress = {index: 0.0 for index in range(len(items))}
        progress_lock = threading.Lock()

        def update_item(index: int, pct: float, status: str, stage: str,
                        label: str, log_kind: str = "", log_message: str = "") -> None:
            with progress_lock:
                item_progress[index] = max(item_progress.get(index, 0.0), float(pct))
                overall = round(sum(item_progress.values()) / max(1, len(items)), 1)
            _state(progress=overall, status=status, current_stage=stage,
                   current_item=label)
            if log_message:
                _activity(log_message, log_kind or "info", stage, label)

        def mark_result(url: str, status: str, error: str = "") -> None:
            with _LOCK:
                state = STATE["content_pipeline"]
                rows = [dict(x) for x in (state.get("search_results") or [])]
                changed = False
                for row in rows:
                    if str(row.get("url") or "") == url:
                        row["download_status"] = status
                        row["download_error"] = str(error or "")[:300]
                        changed = True
                if changed:
                    state["search_results"] = rows
                    state["rev"] = int(state.get("rev", 0)) + 1

        def fetch(index_item):
            index, source_item = index_item
            label = str(source_item.get("title") or source_item.get("url") or
                        f"Bài {index + 1}")[:90]
            update_item(index, 3, f"Bài {index + 1}/{len(items)}: đang tải toàn văn…",
                        "download", label, "info",
                        f"[{index + 1}/{len(items)}] Đang tải toàn văn nguồn.")
            fetched = story_sources.fetch_reference_article(
                source_item, cookie=cookie, timeout=35)
            record = normalize_record(fetched, index)
            record["selected"] = True
            update_item(index, 30,
                        f"Bài {index + 1}/{len(items)}: đã tải toàn văn; chuẩn bị phân tích…",
                        "downloaded", label, "ok",
                        f"Đã tải toàn văn ({int(record.get('word_count') or 0):,} từ).")
            if use_ai and not browser_analysis:
                def ai_progress(stage: str, message: str, pct: float) -> None:
                    kind = "err" if stage == "error" else (
                        "warning" if stage == "warning" else
                        "ok" if stage == "done" else "info")
                    update_item(index, 30 + float(pct) * .65,
                                f"Bài {index + 1}/{len(items)}: {message}",
                                "ai_" + stage, label, kind, message)

                record = analyze_record(
                    record, settings["config"], use_ai=True, provider=provider,
                    progress=ai_progress)
            elif auto_analyze:
                record.update(heuristic_analysis(record))
                record["analysis_provider"] = "offline-on-import"
                update_item(index, 92,
                            f"Bài {index + 1}/{len(items)}: đã phân tích offline; đang lưu…",
                            "offline", label, "ok", "Đã phân tích offline.")
            return record

        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(fetch, pair): pair
                           for pair in enumerate(items)}
                for future in as_completed(futures):
                    item_index, source_item = futures[future]
                    done += 1
                    try:
                        record = future.result()
                        store.upsert([record])
                        successes.append(record)
                        success_indices[str(record.get("id") or "")] = item_index
                        mark_result(str(source_item.get("url") or ""), "Đã tải")
                        label = str(record.get("title_original") or "")[:65]
                        if browser_analysis:
                            update_item(item_index, 35,
                                        f"Đã tải {done}/{len(items)} · chờ phân tích trên trình duyệt…",
                                        "saved", label, "ok",
                                        "Đã lưu toàn văn vào SQLite; chờ trình duyệt phân tích.")
                        else:
                            update_item(item_index, 100,
                                        f"Đã hoàn tất {done}/{len(items)} · đang xử lý bài còn lại…",
                                        "saved", label, "ok",
                                        "Đã lưu bài và kết quả phân tích vào SQLite.")
                        _log(f"Đã tải bài {done}/{len(items)}: {label}", "ok")
                    except Exception as exc:
                        url = str(source_item.get("url") or "")
                        errors.append({"url": url, "error": str(exc)[:300]})
                        mark_result(url, "Lỗi", str(exc))
                        update_item(item_index, 100,
                                    f"Bài {done}/{len(items)} lỗi; chuyển sang bài còn lại…",
                                    "error", str(source_item.get("title") or url)[:90],
                                    "err", f"Không xử lý được bài: {str(exc)[:300]}")
                    ai_success = sum(1 for item in successes
                                     if str(item.get("analysis_provider") or "").startswith(
                                         ("nvidia:", "zenmux:", "zai:", "tokenharbor:", "gemini:",
                                          "tokenrouter:", "browser:", "perplexity_browser:")))
                    ai_failures = [str(item.get("analysis_error") or "") for item in successes
                                   if str(item.get("analysis_error") or "").strip()]
                    _state(done=done, total=len(items),
                           download_success=len(successes),
                           download_failed=len(errors),
                           download_errors=list(errors),
                           ai_success=ai_success, ai_failed=len(ai_failures),
                           warning=(ai_failures[0] if ai_failures else initial_warning),
                           status=(f"Đã xử lý {done}/{len(items)} · "
                                   f"lưu {len(successes)} · lỗi {len(errors)}"))
            if browser_analysis and successes:
                _activity(
                    f"Đã tải xong {len(successes)} bài; mở một phiên trình duyệt để phân tích tuần tự.",
                    "info", "ai_sending")

                def browser_activity(index: int, total: int, stage: str,
                                     message: str, pct: float) -> None:
                    record = successes[max(0, min(len(successes) - 1, index - 1))]
                    item_index = success_indices.get(str(record.get("id") or ""), index - 1)
                    label = str(record.get("title_original") or f"Bài {index}")[:90]
                    kind = "err" if stage == "error" else (
                        "warning" if stage == "warning" else
                        "ok" if stage == "done" else "info")
                    update_item(item_index, 35 + float(pct) * .65,
                                f"Phân tích trình duyệt {index}/{total}: {message}",
                                "ai_" + stage, label, kind, message)

                def browser_progress(done_count: int, total: int, item: Dict) -> None:
                    store.upsert([item], overwrite=True)
                    _state(done=done_count, total=total,
                           status=f"Đã phân tích trên trình duyệt {done_count}/{total} bài.")

                successes = asyncio.run(analyze_many(
                    successes, settings["config"], use_ai=True,
                    provider=provider_info["provider"], concurrency=1,
                    progress=browser_progress, activity=browser_activity))
                store.upsert(successes, overwrite=True)
            if not successes:
                message = errors[0]["error"] if errors else "Không có bài đủ nội dung."
                _finish("Không tải được bài nào vào kho.", error=message,
                        progress=100, done=done, total=len(items),
                        download_success=0, download_failed=len(errors),
                        download_errors=errors)
                return
            ai_count = sum(1 for item in successes
                           if str(item.get("analysis_provider") or "").startswith(
                               ("nvidia:", "zenmux:", "zai:", "tokenharbor:", "gemini:", "tokenrouter:",
                                "browser:", "perplexity_browser:")))
            ai_errors = [str(item.get("analysis_error") or "") for item in successes
                         if str(item.get("analysis_error") or "").strip()]
            calendar_path = (_sync_content_calendar(successes, settings)
                             if auto_analyze else "")
            if use_ai and ai_errors:
                status = (f"Đã tải {len(successes)} bài; AI thành công {ai_count}, "
                          f"phân tích offline thay thế {len(ai_errors)} bài"
                          + (f"; {len(errors)} bài tải lỗi." if errors else "."))
            elif use_ai:
                status = (f"Đã tải {len(successes)} bài vào kho SQLite; "
                          f"AI đã rút chất liệu cho {ai_count} bài"
                          + (f"; {len(errors)} bài lỗi." if errors else "."))
            elif auto_analyze:
                status = (f"Đã tải và phân tích offline {len(successes)} bài"
                          + (f"; {len(errors)} bài tải lỗi." if errors else "."))
            else:
                status = (f"Đã tải {len(successes)} bài vào kho SQLite"
                          + (f"; {len(errors)} bài tải lỗi." if errors else "."))
            _finish(status, progress=100, done=done, total=len(items),
                    count=len(store.list(limit=10000)),
                    download_success=len(successes), download_failed=len(errors),
                    download_errors=errors, calendar_path=calendar_path,
                    ai_success=ai_count, ai_failed=len(ai_errors),
                    warning=(ai_errors[0] if ai_errors else initial_warning),
                    current_stage="done")
            _activity(status, "warning" if ai_errors or errors else "ok", "done")
        except Exception as exc:
            _finish("Tải nội dung lỗi; các bài đã xong vẫn được giữ.", error=str(exc),
                    download_success=len(successes), download_failed=len(errors),
                    download_errors=errors)
            _activity(str(exc), "err", "error")

    submit_job(work, name="Tải và phân tích nội dung", resource="ai",
               foreground=False, metadata={"kind": "content_download"})
    return {"ok": True, "async": True, "total": len(items), **provider_info}, 200


def api_content_reload(body: Dict) -> JsonResult:
    """Tải lại toàn văn từ URL và phân tích lại đúng các mục trong hàng đợi."""
    ids = _body_ids(body)
    if not ids:
        return {"error": "Hãy chọn ít nhất một mục cần tải lại."}, 400
    store = _store()
    requested = [item for item in (store.get(record_id) for record_id in ids) if item]
    protected = [item for item in requested if record_was_used(item)]
    rows = [item for item in requested if not record_was_used(item)]
    if not rows:
        return {"error": (
            "Các mục đã chọn đều thuộc lịch sử đã dùng hoặc không còn tồn tại; "
            "hệ thống không tải lại để tránh làm trùng chuyện.")}, 409
    if not _begin("content_reload", "Đang chuẩn bị tải lại hàng đợi…"):
        return {"error": "Kho ý tưởng đang chạy một tác vụ khác."}, 409
    settings = _settings()
    provider = str(body.get("provider") or settings["provider"] or "auto").lower()
    use_ai = bool(body.get("use_ai", True)) and provider not in {
        "heuristic", "offline", "none"}
    cookie = str(body.get("cookie") or "").strip()
    try:
        workers = max(1, min(4, int(body.get("concurrency") or 3), len(rows)))
    except (TypeError, ValueError):
        workers = min(3, len(rows))
    resolved_provider = _provider_status(settings["config"], provider)["provider"]
    if resolved_provider in {"browser", "perplexity_browser"}:
        # Cùng một persistent profile không thể bị nhiều Playwright context giữ.
        workers = 1
    _state(total=len(rows), done=0, reload_success=0, reload_failed=0,
           reload_errors=[], protected_count=len(protected), provider=resolved_provider,
           provider_configured=_configured_provider(settings["config"], provider),
           status=f"Đang tải lại và phân tích 0/{len(rows)} mục…")

    def work() -> None:
        from ... import story_sources
        successes, errors, done = [], [], 0

        def reload_one(index_item):
            index, old = index_item
            record_id = str(old.get("id") or "")
            url = str(old.get("source_url") or "").strip()
            if url.startswith(("http://", "https://")):
                fetched = story_sources.fetch_reference_article({
                    "url": url,
                    "title": old.get("title_original") or old.get("title_localized"),
                    "source_name": old.get("source") or "Nguồn đã tải",
                    "language": old.get("language") or "",
                    "topic": old.get("primary_genre") or "",
                    "narrative_style": old.get("narrative_style") or "",
                    "content_form": old.get("content_form") or "",
                }, cookie=cookie, timeout=35, force_refresh=True)
                fresh = normalize_record(fetched, index)
                fresh["id"] = record_id
            else:
                fresh = dict(old)
                content = str(fresh.get("content") or fresh.get("raw_content") or "")
                if len(content.strip()) < 80:
                    raise ValueError(
                        "Mục không có URL nguồn và nội dung hiện có quá ngắn để phân tích lại.")
            fresh["selected"] = True
            fresh["reloaded_at"] = datetime.now().isoformat(timespec="seconds")
            result = analyze_record(
                fresh, settings["config"], use_ai=use_ai, provider=provider)
            result["id"] = record_id
            result["selected"] = True
            result["reloaded_at"] = fresh["reloaded_at"]
            store.upsert([result], overwrite=True)
            return result

        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(reload_one, pair): pair[1]
                           for pair in enumerate(rows)}
                for future in as_completed(futures):
                    old = futures[future]
                    done += 1
                    try:
                        result = future.result()
                        successes.append(result)
                        _log("Đã tải lại %d/%d: %s" % (
                            done, len(rows),
                            str(result.get("title_localized") or
                                result.get("title_original") or "")[:65]), "ok")
                    except Exception as exc:
                        errors.append({
                            "id": str(old.get("id") or ""),
                            "title": str(old.get("title_localized") or
                                         old.get("title_original") or "")[:100],
                            "error": str(exc)[:300],
                        })
                    _state(done=done, total=len(rows),
                           progress=round(done * 100 / max(1, len(rows)), 1),
                           reload_success=len(successes),
                           reload_failed=len(errors), reload_errors=list(errors),
                           status=(f"Đã xử lý {done}/{len(rows)} · "
                                   f"tải lại {len(successes)} · lỗi {len(errors)}"))
            if not successes:
                message = errors[0]["error"] if errors else "Không có mục tải lại thành công."
                _finish("Tải lại hàng đợi thất bại; dữ liệu cũ vẫn được giữ.",
                        error=message, progress=100, done=done, total=len(rows),
                        reload_success=0, reload_failed=len(errors),
                        reload_errors=errors, protected_count=len(protected))
                return
            calendar_path = _sync_content_calendar(successes, settings)
            status = f"Đã tải lại và phân tích {len(successes)}/{len(rows)} mục."
            if errors:
                status += f" {len(errors)} mục lỗi vẫn giữ dữ liệu cũ."
            if protected:
                status += f" Bỏ qua {len(protected)} mục đã dùng."
            _finish(status, progress=100, done=done, total=len(rows),
                    count=len(store.list(limit=10000)),
                    reload_success=len(successes), reload_failed=len(errors),
                    reload_errors=errors, protected_count=len(protected),
                    calendar_path=calendar_path)
        except Exception as exc:
            _finish("Tải lại hàng đợi lỗi; dữ liệu cũ vẫn được giữ.", error=str(exc),
                    reload_success=len(successes), reload_failed=len(errors),
                    reload_errors=errors, protected_count=len(protected))

    submit_job(work, name="Tải lại hàng đợi nội dung", resource="ai",
               foreground=False, metadata={"kind": "content_reload"})
    return {"ok": True, "async": True, "total": len(rows),
            "protected": len(protected), "provider": provider,
            "provider_configured": _configured_provider(
                settings["config"], provider)}, 200
