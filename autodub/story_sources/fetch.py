"""Tải bài tham khảo, cache RAM và tải/cắt video nguồn."""
from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests

from .. import downloader
from ..utils import log, run
from .parse import (
    REFERENCE_SOURCES,
    _clean_html_fragment,
    _domain_matches,
    _html_article,
    _reference_relevance,
    _text_metrics,
)

# Kết quả đọc trước khi xếp hạng được giữ ngắn hạn trong RAM. Khi người dùng
# chọn một bài, bước lưu SQLite dùng lại record này thay vì tải website lần hai.
_REFERENCE_CACHE: Dict[str, tuple] = {}
_REFERENCE_CACHE_LOCK = threading.Lock()
_REFERENCE_CACHE_TTL = 30 * 60
_REFERENCE_CACHE_MAX = 80


def _jina_article(url: str, timeout: int) -> Dict:
    parsed = urlparse(url)
    target = "http://" + parsed.netloc + (parsed.path or "/")
    if parsed.query:
        target += "?" + parsed.query
    response = requests.get("https://r.jina.ai/" + target, headers={
        "User-Agent": "AutoDubVN/2.5 content-research",
        "Accept": "text/plain",
    }, timeout=timeout)
    response.raise_for_status()
    raw = response.text
    metrics = _text_metrics(raw)
    title_match = re.search(r"^Title:\s*(.+)$", raw, flags=re.M)
    content = raw.split("Markdown Content:", 1)[-1] if "Markdown Content:" in raw else raw
    content = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", content)
    content = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", content)
    content = re.sub(r"^#{1,6}\s*", "", content, flags=re.M)
    content = re.sub(r"^[-*]{3,}\s*$", "", content, flags=re.M)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    return {
        "title": (title_match.group(1).strip() if title_match else ""),
        "content": content,
        "author": "", "published_at": "", "fetch_mode": "reader_fallback",
        **metrics,
    }


def _reddit_article(url: str, timeout: int) -> Dict:
    clean = re.sub(r"[?#].*$", "", url.rstrip("/"))
    response = requests.get(clean + ".json", headers={
        "User-Agent": "windows:AutoDubVN:2.5 (content research)",
        "Accept": "application/json",
    }, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    post = (((payload[0] or {}).get("data") or {}).get("children") or [{}])[0]
    data = post.get("data") or {}
    return {
        "title": str(data.get("title") or "").strip(),
        "content": str(data.get("selftext") or "").strip(),
        "author": str(data.get("author") or "").strip(),
        "published_at": str(data.get("created_utc") or ""),
        "fetch_mode": "reddit_json",
        "read_count": 0,
        "engagement_count": int(data.get("score") or 0) +
                            int(data.get("num_comments") or 0),
    }


def fetch_reference_article(item, cookie: str = "", timeout: int = 30,
                            force_refresh: bool = False) -> Dict:
    """Tải một bài công khai thành record tương thích ``ContentStore``.

    Cookie Zhihu (nếu người dùng chủ động cung cấp) chỉ nằm trong header của
    lượt gọi này, không được ghi vào record, log hay cấu hình. Khi website
    chặn trình đọc trực tiếp, hệ thống thử reader công khai và báo rõ cách lấy.
    """
    row = dict(item) if isinstance(item, dict) else {"url": str(item or "")}
    url = str(row.get("url") or row.get("source_url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("Link bài viết không hợp lệ: " + url[:120])
    host = (urlparse(url).hostname or "").lower()
    timeout = max(8, min(90, int(timeout or 30)))
    if not force_refresh:
        with _REFERENCE_CACHE_LOCK:
            cached = _REFERENCE_CACHE.get(url)
            if cached and time.time() - float(cached[0]) <= _REFERENCE_CACHE_TTL:
                record = dict(cached[1])
                if len(str(record.get("rawContent") or "").strip()) >= 280:
                    return record
    if host.endswith("reddit.com"):
        article = _reddit_article(url, timeout)
    else:
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/127 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,vi;q=0.8,en;q=0.7",
        }
        if str(cookie or "").strip():
            headers["Cookie"] = str(cookie).strip()
        article = {}
        direct_error = ""
        try:
            response = requests.get(url, headers=headers, timeout=timeout,
                                    allow_redirects=True)
            response.raise_for_status()
            article = _html_article(response.text)
            article["fetch_mode"] = "direct"
        except Exception as exc:
            direct_error = str(exc)
        if len(str(article.get("content") or "").strip()) < 280:
            try:
                article = _jina_article(url, timeout)
            except Exception as exc:
                message = str(exc) or direct_error or "website từ chối truy cập"
                raise RuntimeError("Không lấy được nội dung công khai: " + message) from exc
    content = str(article.get("content") or "").strip()
    # Trang chặn thường chỉ trả một câu mời đăng nhập; không lưu loại kết quả này.
    if len(content) < 280:
        raise RuntimeError(
            "Trang chỉ trả tiêu đề/đoạn giới thiệu, chưa đủ nội dung để lưu. "
            "Với Zhihu, hãy dán Cookie phiên đăng nhập hoặc thử link câu hỏi khác.")
    source_name = str(row.get("source_name") or row.get("source") or host).strip()
    catalog_source = next((source for source in REFERENCE_SOURCES
                           if _domain_matches(url, source.get("domain", ""))), {})
    language = str(row.get("language") or catalog_source.get("lang") or "").strip()
    keyword = str(row.get("keyword") or "").strip()
    topic = str(row.get("topic") or "").strip()
    narrative_style = str(row.get("narrative_style") or
                          catalog_source.get("narrative_style") or "").strip()
    mode = str(article.get("fetch_mode") or "direct")
    note = ("Chỉ dùng làm chất liệu; phải viết lại hoàn toàn. "
            "Cách lấy nội dung: %s." % mode)
    source_note = str(row.get("source_notes") or catalog_source.get("notes") or "").strip()
    if source_note:
        note += " Quy tắc nguồn: " + source_note
    record = {
        "title": str(article.get("title") or row.get("title") or url).strip(),
        "rawContent": content,
        "source": source_name,
        "sourceUrl": url,
        "author": str(article.get("author") or "").strip(),
        "publishedAt": str(article.get("published_at") or "").strip(),
        "language": language,
        "tags": [value for value in (keyword, topic, narrative_style) if value],
        "notes": note,
        "narrative_style": narrative_style,
        "content_form": str(row.get("content_form") or
                            catalog_source.get("content_form") or "").strip(),
        "fetch_mode": mode,
        "read_count": int(article.get("read_count") or 0),
        "engagement_count": int(article.get("engagement_count") or 0),
    }
    if not str(cookie or "").strip():
        with _REFERENCE_CACHE_LOCK:
            _REFERENCE_CACHE[url] = (time.time(), dict(record))
            if len(_REFERENCE_CACHE) > _REFERENCE_CACHE_MAX:
                oldest = sorted(_REFERENCE_CACHE.items(), key=lambda pair: pair[1][0])
                for key, _value in oldest[:len(_REFERENCE_CACHE) - _REFERENCE_CACHE_MAX]:
                    _REFERENCE_CACHE.pop(key, None)
    return record


def enrich_reference_rows(rows: Iterable[Dict], max_workers: int = 4,
                          progress: Optional[Callable[[int, int, Dict], None]] = None,
                          query: str = ""
                          ) -> List[Dict]:
    """Đọc trước nội dung rồi xếp đúng chủ đề trước, lượt đọc sau."""
    items = [dict(row) for row in rows or []]
    if not items:
        return []
    total, done = len(items), 0

    def one(pair):
        index, row = pair
        out = dict(row)
        out["search_rank"] = index + 1
        try:
            article = fetch_reference_article(row, timeout=35)
            out.update({
                "read_count": int(article.get("read_count") or 0),
                "engagement_count": int(article.get("engagement_count") or 0),
                "content_ready": True,
                "content_chars": len(str(article.get("rawContent") or "")),
                "fetch_error": "",
            })
            out.update(_reference_relevance(out, query, article))
        except Exception as exc:
            out.update({"read_count": 0, "engagement_count": 0,
                        "content_ready": False, "content_chars": 0,
                        "fetch_error": str(exc)[:240]})
            out.update(_reference_relevance(out, query))
        return out

    results = []
    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers or 4), len(items)))) as pool:
        futures = {pool.submit(one, pair): pair[1]
                   for pair in enumerate(items)}
        for future in as_completed(futures):
            item = future.result()
            results.append(item)
            done += 1
            if progress:
                progress(done, total, item)
    if query and any(item.get("strict_relevance") for item in results):
        results = [item for item in results if item.get("relevance_accepted")]
    results.sort(key=lambda x: (
        -int(x.get("relevance_score") or 0),
        0 if int(x.get("read_count") or 0) > 0 else 1,
        -int(x.get("read_count") or 0),
        -int(x.get("engagement_count") or 0),
        int(x.get("search_rank") or 9999),
    ))
    for index, item in enumerate(results, 1):
        item["popularity_rank"] = index
    return results

def _video_files(paths: Iterable[str]) -> List[str]:
    out, seen = [], set()
    for raw in paths or []:
        path = os.path.abspath(str(raw or "").strip().strip('"'))
        candidates = []
        if os.path.isdir(path):
            candidates = [os.path.join(path, name)
                          for name in sorted(os.listdir(path))]
        elif os.path.isfile(path):
            candidates = [path]
        for item in candidates:
            if not os.path.isfile(item) or os.path.splitext(item)[1].lower() not in {
                    ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}:
                continue
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


def cut_video_segments(paths: Iterable[str], out_dir: str,
                       min_seconds: float = 300, max_seconds: float = 600,
                       progress: Optional[Callable[[int, int, str], None]] = None,
                       filename_prefix: str = "") -> List[str]:
    """Cắt video nguồn thành các đoạn 5–10 phút bằng stream-copy nhanh.

    Chọn điểm giữa khoảng min/max để mỗi nguồn có các đoạn ổn định ~7,5 phút.
    FFmpeg cắt tại keyframe gần nhất, vì vậy thời lượng thực tế có thể lệch
    một ít; các clip vẫn được planner random-pick dùng trực tiếp.
    """
    sources = _video_files(paths)
    if not sources:
        raise ValueError("Chưa có file video nguồn để cắt.")
    try:
        lo = max(2.0, float(min_seconds))
        hi = max(lo, float(max_seconds))
    except (TypeError, ValueError):
        lo, hi = 300.0, 600.0
    segment_time = (lo + hi) / 2.0
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    results: List[str] = []
    total = len(sources)
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(filename_prefix or ""))
    for index, source in enumerate(sources, 1):
        stem = re.sub(r"[^\w.-]+", "_", Path(source).stem, flags=re.UNICODE).strip("._") or "video"
        pattern = os.path.join(out_dir, f"{safe_prefix}{index:03d}_{stem}_%03d.mp4")
        run([
            "ffmpeg", "-y", "-hide_banner", "-nostdin", "-i", source,
            "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
            "-f", "segment", "-segment_time", f"{segment_time:.3f}",
            "-reset_timestamps", "1", "-segment_format", "mp4", pattern,
        ], check=True, quiet=True)
        prefix = os.path.basename(pattern).split("%03d", 1)[0]
        made = [os.path.join(out_dir, name) for name in sorted(os.listdir(out_dir))
                if name.startswith(prefix) and name.lower().endswith(".mp4")]
        results.extend(path for path in made if path not in results)
        if progress:
            progress(index, total, os.path.basename(source))
    if not results:
        raise RuntimeError("FFmpeg không tạo được clip nào.")
    return results

def download_many(urls: Iterable[str], out_dir: str, quality: str = "best",
                  cookies_from_browser: Optional[str] = None,
                  cookies_file: Optional[str] = None,
                  concurrent_fragments: int = 8,
                  external_downloader: Optional[str] = "auto",
                  progress: Optional[Callable[[int, int, str], None]] = None,
                  live_progress: Optional[Callable[[float, str], None]] = None,
                  max_workers: int = 3,
                  proxy: Optional[str] = None) -> List[Dict]:
    """Tải song song một nhóm link, trả về các file hợp lệ theo thứ tự hoàn tất."""
    links = []
    seen = set()
    for raw in urls or []:
        url = downloader.extract_url(str(raw))
        if url and url not in seen:
            links.append(url)
            seen.add(url)
    if not links:
        raise ValueError("Chưa có link video nền hợp lệ để tải.")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    total, done, out = len(links), 0, []
    live_lock = threading.Lock()
    item_percent = {url: 0.0 for url in links}

    def one(url: str) -> Dict:
        def _item_progress(info: Dict) -> None:
            if not live_progress:
                return
            pct = info.get("percent")
            if pct is None:
                return
            with live_lock:
                # Một video DASH có thể báo 100% cho hình rồi bắt đầu luồng
                # tiếng từ 0%. Giữ mức cao nhất để thanh tổng không chạy lùi;
                # dòng chi tiết từ downloader vẫn cho biết đúng luồng hiện tại.
                item_percent[url] = max(item_percent[url],
                                        max(0.0, min(100.0, float(pct))))
                overall = sum(item_percent.values()) / max(1, total)
            detail = "%5.1f%% tổng · %s" % (
                overall, str(info.get("text") or "Đang tải video…"))
            live_progress(overall, detail)

        path = downloader.download_video(
            url, out_dir, quality=quality,
            cookies_from_browser=cookies_from_browser,
            cookies_file=cookies_file,
            concurrent_fragments=concurrent_fragments,
            external_downloader=external_downloader,
            progress_callback=_item_progress,
            proxy=proxy)
        with live_lock:
            item_percent[url] = 100.0
        return {"url": url, "path": os.path.abspath(path),
                "title": os.path.splitext(os.path.basename(path))[0]}

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(links)))) as pool:
        futures = {pool.submit(one, url): url for url in links}
        for future in as_completed(futures):
            url = futures[future]
            done += 1
            try:
                item = future.result()
                out.append(item)
                log("Đã tải nguồn video %d/%d: %s" % (done, total, os.path.basename(item["path"])), "ok")
                message = os.path.basename(item["path"])
            except Exception as exc:
                log("Tải nguồn video lỗi (%s): %s" % (url, exc), "err")
                message = "Lỗi: %s" % str(exc)[:140]
            if progress:
                progress(done, total, message)
    if not out:
        raise RuntimeError("Không tải được video nào trong danh sách.")
    return out
