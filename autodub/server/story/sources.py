"""Tìm, tải, cắt video nguồn cho kể chuyện."""
from __future__ import annotations

import os
import re
from typing import Dict

from ..state import HERE, STATE, _LOCK, _log, _progress
from ..helpers import _safe_path_stem
from .common import JsonResult, _api, _mark_manual_cancelled


def _load_cfg():
    return _api()._load_cfg()


def submit_job(*args, **kwargs):
    return _api().submit_job(*args, **kwargs)


def _story_video_inputs(payload: Dict) -> list:
    raw = payload.get("video_sources", payload.get("source_videos", []))
    if isinstance(raw, str):
        raw = re.split(r"[\r\n]+", raw)
    return [str(x.get("path") if isinstance(x, dict) else x).strip()
            for x in (raw if isinstance(raw, list) else [])
            if str(x.get("path") if isinstance(x, dict) else x).strip()]


def api_story_search_sources(b: Dict) -> JsonResult:
    """Tìm video Bilibili/YouTube/Douyin theo từ khóa cho tab Kể chuyện AI."""
    keyword = str(b.get("keyword") or "").strip()
    try:
        limit = max(1, min(50, int(b.get("limit", 10) or 10)))
    except (TypeError, ValueError):
        limit = 10
    if not keyword:
        return {"error": "Hãy nhập từ khóa tìm video."}, 400
    try:
        from ... import story_sources
        cfg = _load_cfg().get("download", {}) or {}
        provider = str(b.get("provider") or "bilibili").strip().lower()
        rows = story_sources.search(
            keyword, limit=limit, provider=provider,
            cookies_from_browser=cfg.get("cookies_from_browser"),
            cookies_file=cfg.get("cookies_file"))
        with _LOCK:
            manual = STATE["manual"]
            manual.update({"source_keyword": keyword, "source_results": rows,
                           "source_status": "Tìm thấy %d video" % len(rows),
                           "rev": int(manual.get("rev", 0)) + 1})
        return {"ok": True, "keyword": keyword, "provider": provider,
                "results": rows}, 200
    except Exception as exc:
        return {"error": "Tìm nguồn video thất bại: %s" % exc}, 500


def api_story_reference_catalog(b: Dict = None) -> JsonResult:
    """Danh mục nguồn tham khảo và bộ từ khóa tiếng Trung cho truyện mới."""
    try:
        from ... import story_sources
        return {"ok": True, "sources": story_sources.reference_catalog(),
                "chinese_keywords": story_sources.chinese_keyword_catalog()}, 200
    except Exception as exc:
        return {"error": "Không đọc được danh mục nguồn tham khảo: %s" % exc}, 500


def api_story_search_references(b: Dict) -> JsonResult:
    """Tìm tiêu đề/snippet tham khảo từ các website đã chọn, không sao chép bài."""
    keyword = str(b.get("keyword") or "").strip()
    if not keyword:
        return {"error": "Hãy nhập từ khóa tham khảo tiếng Trung hoặc tiếng Việt."}, 400
    try:
        limit = max(1, min(50, int(b.get("limit", 20) or 20)))
    except (TypeError, ValueError):
        limit = 20
    source_keys = b.get("source_keys") or []
    if isinstance(source_keys, str):
        source_keys = re.split(r"[,\s]+", source_keys)
    try:
        from ... import story_sources
        rows = story_sources.search_web_references(keyword, source_keys, limit)
        with _LOCK:
            manual = STATE["manual"]
            manual.update({"reference_keyword": keyword, "reference_results": rows,
                           "reference_status": "Tìm thấy %d bài tham khảo" % len(rows),
                           "rev": int(manual.get("rev", 0)) + 1})
        return {"ok": True, "keyword": keyword, "results": rows}, 200
    except Exception as exc:
        return {"error": "Tìm bài tham khảo thất bại: %s" % exc}, 500


def api_story_cut_sources(b: Dict) -> JsonResult:
    """Cắt video nền thành các clip ngắn để random-pick theo audio."""
    raw = b.get("video_sources", b.get("paths", []))
    if isinstance(raw, str):
        raw = re.split(r"[\r\n]+", raw)
    paths = [str(x.get("path") if isinstance(x, dict) else x).strip()
             for x in (raw if isinstance(raw, list) else []) if str(x).strip()]
    if not paths:
        return {"error": "Hãy chọn video nền trước khi cắt."}, 400
    try:
        min_seconds = max(2.0, float(b.get("min_seconds", 300) or 300))
        max_seconds = max(min_seconds, float(b.get("max_seconds", 600) or 600))
    except (TypeError, ValueError):
        min_seconds, max_seconds = 300.0, 600.0
    with _LOCK:
        if STATE["running"] or STATE["busy"]:
            return {"error": "Đang bận: " + (STATE["busy"] or "đang xử lý")}, 409
        STATE["cancel"] = False
        STATE["running"] = True
        STATE["busy"] = "Đang cắt video nền thành đoạn nhỏ"
        manual = STATE["manual"]
        manual.update({"cut_status": "Đang chuẩn bị cắt video…", "cut_done": 0,
                       "cut_total": len(paths), "error": "",
                       "rev": int(manual.get("rev", 0)) + 1})

    def _work(source_paths=list(paths), lo=min_seconds, hi=max_seconds,
              payload=dict(b)):
        try:
            from ... import story_sources
            title = _safe_path_stem(str(payload.get("name") or "story_clips"),
                                    fallback="story_clips", limit=60)
            out_dir = os.path.join(HERE, "downloads", "story_clips", title)

            def progress(done, total, message):
                with _LOCK:
                    manual = STATE["manual"]
                    manual.update({"cut_status": "%d/%d: %s" % (done, total, message),
                                   "cut_done": done, "cut_total": total,
                                   "rev": int(manual.get("rev", 0)) + 1})
                _progress(pct=done * 100.0 / max(1, total),
                          step="Cắt video nền", detail=message)

            clips = story_sources.cut_video_segments(
                source_paths, out_dir, min_seconds=lo, max_seconds=hi,
                progress=progress)
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"source_videos": clips, "source_clips": clips,
                               "cut_status": "Đã cắt %d clip" % len(clips),
                               "cut_done": len(source_paths), "cut_total": len(source_paths),
                               "error": "", "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Cắt video xong", detail="%d clip sẵn sàng" % len(clips))
        except InterruptedError:
            _mark_manual_cancelled("Đã dừng cắt video; các clip đã tạo vẫn được giữ lại.")
        except Exception as exc:
            _log("Cắt video nền lỗi: %s" % exc, "err")
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"cut_status": "Cắt lỗi", "error": str(exc)[:300],
                               "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Cắt video lỗi", detail=str(exc)[:160])
        finally:
            with _LOCK:
                STATE["running"] = False
                STATE["busy"] = ""

    submit_job(_work, name="Cắt video nguồn", resource="ffmpeg",
               metadata={"kind": "story_cut_sources"})
    return {"ok": True, "async": True, "total": len(paths)}, 200


def api_story_download_sources(b: Dict) -> JsonResult:
    """Tải hàng loạt link đã chọn, cập nhật tiến độ qua /api/state."""
    raw = b.get("links", b.get("urls", []))
    if isinstance(raw, str):
        raw = re.split(r"[\r\n]+", raw)
    links = [str(x.get("url") if isinstance(x, dict) else x).strip()
             for x in (raw if isinstance(raw, list) else [])]
    links = [x for x in links if x]
    if not links:
        return {"error": "Hãy chọn video tìm được hoặc dán danh sách link."}, 400
    with _LOCK:
        if STATE["running"] or STATE["busy"]:
            return {"error": "Đang bận: " + (STATE["busy"] or "đang xử lý")}, 409
        STATE["cancel"] = False
        STATE["running"] = True
        STATE["busy"] = "Đang tải video nền…"
        manual = STATE["manual"]
        manual.update({"working": True, "source_links": links,
                       "source_videos": [], "source_done": 0,
                       "source_total": len(links),
                       "source_status": "Đang chuẩn bị tải %d video…" % len(links),
                       "error": "", "rev": int(manual.get("rev", 0)) + 1})

    def _work(payload=dict(b), urls=list(links)):
        try:
            from ... import story_sources
            cfg = _load_cfg().get("download", {}) or {}
            title = _safe_path_stem(str(payload.get("name") or "story_sources"),
                                    fallback="story_sources", limit=60)
            out_dir = os.path.join(HERE, "downloads", "story_sources", title)

            def progress(done, total, message):
                with _LOCK:
                    manual = STATE["manual"]
                    manual.update({"source_done": done, "source_total": total,
                                   "source_status": "%d/%d: %s" %
                                   (done, total, message),
                                   "rev": int(manual.get("rev", 0)) + 1})
                _progress(pct=done * 100.0 / max(1, total),
                          step="Tải video nền", detail=message)

            def live_progress(pct, detail):
                with _LOCK:
                    manual = STATE["manual"]
                    manual.update({"source_status": str(detail)[:260],
                                   "rev": int(manual.get("rev", 0)) + 1})
                _progress(pct=float(pct), step="Tải video nền",
                          detail=str(detail)[:260])

            result = story_sources.download_many(
                urls, out_dir, quality=str(cfg.get("quality") or "best"),
                cookies_from_browser=cfg.get("cookies_from_browser"),
                cookies_file=cfg.get("cookies_file"),
                concurrent_fragments=int(cfg.get("concurrent_fragments", 8) or 8),
                external_downloader=cfg.get("external_downloader", "auto"),
                progress=progress, live_progress=live_progress,
                proxy=cfg.get("proxy"))
            paths = [x["path"] for x in result]
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"working": False, "source_videos": paths,
                               "source_done": len(paths), "source_total": len(urls),
                               "source_status": "Đã tải %d/%d video" %
                               (len(paths), len(urls)), "error": "",
                               "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Tải video nền xong",
                      detail="%d file sẵn sàng" % len(paths))
        except InterruptedError:
            _mark_manual_cancelled("Đã dừng tải video; các file đã tải vẫn được giữ lại.")
        except Exception as exc:
            _log("Tải video nền lỗi: %s" % exc, "err")
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"working": False, "source_status": "Tải lỗi",
                               "error": str(exc)[:300],
                               "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Tải video nền lỗi", detail=str(exc)[:160])
        finally:
            with _LOCK:
                STATE["running"] = False
                STATE["busy"] = ""

    submit_job(_work, name="Tải video nguồn", resource="network",
               metadata={"kind": "story_download_sources"})
    return {"ok": True, "async": True, "total": len(links)}, 200
