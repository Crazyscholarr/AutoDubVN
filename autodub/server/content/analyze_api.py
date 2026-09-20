"""Phân tích đa tầng kho ý tưởng."""
from __future__ import annotations

import asyncio
import threading
from typing import Dict

from ...content_pipeline import analyze_many
from ..state import _log
from .common import (
    JsonResult, _activity, _api, _begin, _finish, _provider_status,
    _selected_records, _state, _sync_content_calendar,
)


def _settings():
    return _api()._settings()


def _store():
    return _api()._store()


def submit_job(*args, **kwargs):
    return _api().submit_job(*args, **kwargs)


def api_content_analyze(body: Dict) -> JsonResult:
    if not _begin("analyze", "Đang chuẩn bị phân tích đa tầng…"):
        return {"error": "Kho ý tưởng đang chạy một tác vụ khác."}, 409
    store = _store()
    rows = _selected_records(store, body.get("ids") or [], bool(body.get("all")))
    if not rows:
        _finish("Không có ý tưởng được chọn.", error="Hãy tick ý tưởng hoặc chọn Phân tích tất cả.")
        return {"error": "Không có ý tưởng được chọn."}, 400
    settings = _settings()
    provider = str(body.get("provider") or settings["provider"] or "auto").lower()
    use_ai = bool(body.get("use_ai", True)) and provider not in {"heuristic", "offline", "none"}
    concurrency = max(1, min(8, int(body.get("concurrency") or settings["concurrency"])))
    provider_info = _provider_status(settings["config"], provider)
    provider_label = str(provider_info["provider"] or provider).upper()
    model_label = str(provider_info["provider_model"] or "")
    provider_text = provider_label + (f" · {model_label}" if model_label else "")
    initial_warning = ""
    if use_ai and not provider_info["provider_configured"]:
        initial_warning = (f"{provider_text} chưa có API key hợp lệ; sẽ dùng phân tích "
                           "offline. Mở Cài đặt → API để sửa.")
    _state(total=len(rows), provider=provider_info["provider"],
           provider_model=model_label,
           provider_configured=provider_info["provider_configured"],
           provider_offline=provider_info["provider_offline"],
           warning=initial_warning,
           status=(f"Đang phân tích {len(rows)} ý tưởng bằng {provider_text}…"
                   if use_ai else f"Đang phân tích offline {len(rows)} ý tưởng…"))
    _activity((f"Bắt đầu phân tích {len(rows)} bài · AI: {provider_text}."
               if use_ai else f"Bắt đầu phân tích offline {len(rows)} bài."),
              "warning" if initial_warning else "info", "prepare")
    if initial_warning:
        _activity(initial_warning, "warning", "provider")

    def work() -> None:
        try:
            item_progress = {index: 0.0 for index in range(len(rows))}
            progress_lock = threading.Lock()
            completed_results = []

            def activity(index: int, total: int, stage: str, message: str,
                         pct: float) -> None:
                item_index = max(0, index - 1)
                with progress_lock:
                    item_progress[item_index] = max(
                        item_progress.get(item_index, 0.0), float(pct))
                    overall = round(sum(item_progress.values()) / max(1, total), 1)
                title = str(rows[item_index].get("title_original") or
                            rows[item_index].get("title_localized") or f"Bài {index}")[:90]
                kind = "err" if stage == "error" else (
                    "warning" if stage == "warning" else
                    "ok" if stage == "done" else "info")
                _state(progress=overall, current_stage="ai_" + stage,
                       current_item=title,
                       status=f"Bài {index}/{total}: {message}")
                _activity(message, kind, "ai_" + stage, title)

            def progress(done: int, total: int, item: Dict) -> None:
                store.upsert([item], overwrite=True)
                completed_results.append(item)
                ai_ok = sum(1 for result in completed_results
                            if str(result.get("analysis_provider") or "").startswith(
                                ("nvidia:", "zenmux:", "zai:", "tokenharbor:", "gemini:", "tokenrouter:",
                                 "browser:", "perplexity_browser:")))
                failures = [str(result.get("analysis_error") or "")
                            for result in completed_results
                            if str(result.get("analysis_error") or "").strip()]
                _state(done=done, total=total,
                       ai_success=ai_ok, ai_failed=len(failures),
                       warning=(failures[0] if failures else initial_warning),
                       status=f"Đã phân tích {done}/{total}: "
                              f"{str(item.get('title_original') or '')[:70]}")

            results = asyncio.run(analyze_many(
                rows, settings["config"], use_ai=use_ai, provider=provider,
                concurrency=concurrency, progress=progress, activity=activity))
            ai_success = sum(1 for item in results
                             if str(item.get("analysis_provider") or "").startswith(
                                 ("nvidia:", "zenmux:", "zai:", "tokenharbor:", "gemini:", "tokenrouter:",
                                  "browser:", "perplexity_browser:")))
            ai_errors = [str(item.get("analysis_error") or "") for item in results
                         if str(item.get("analysis_error") or "").strip()]
            calendar_path = _sync_content_calendar(results, settings)
            if use_ai:
                status = (f"Đã phân tích xong {len(results)} ý tưởng · AI thành công "
                          f"{ai_success} · offline thay thế {len(ai_errors)}.")
            else:
                status = f"Đã phân tích offline xong {len(results)} ý tưởng."
            _finish(status, progress=100,
                    done=len(results), total=len(results), count=len(results),
                    calendar_path=calendar_path, ai_success=ai_success,
                    ai_failed=len(ai_errors),
                    warning=(ai_errors[0] if ai_errors else initial_warning),
                    current_stage="done")
            _activity(status, "warning" if ai_errors else "ok", "done")
            _log(f"Kho ý tưởng: phân tích xong {len(results)} truyện.", "ok")
        except Exception as exc:
            _finish("Phân tích ý tưởng lỗi; phần đã xong vẫn được giữ.", error=str(exc))
            _activity(str(exc), "err", "error")
            _log(f"Phân tích ý tưởng lỗi: {exc}", "err")

    submit_job(work, name="Phân tích kho ý tưởng", resource="ai",
               foreground=False, metadata={"kind": "content_analyze"})
    return {"ok": True, "async": True, "total": len(rows),
            **provider_info}, 200
