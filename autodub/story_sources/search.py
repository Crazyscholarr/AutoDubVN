"""Tìm Bing/DuckDuckGo, listing website và video Bilibili/YouTube/Douyin."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

import requests

from .. import downloader
from ..download_site import (
    detect_site, normalise_search_provider, site_ytdlp_args,
)
from ..utils import log
from .parse import (
    REFERENCE_SOURCES,
    _SPIRITUAL_TERMS,
    _TOPIC_TERMS,
    _clean_html_fragment,
    _compact_chinese_query,
    _domain_matches,
    _focused_chinese_query,
    _html_search_links,
    _looks_like_article,
    _parse_bing_algo_block,
    _pick_domain_url,
    _rank_direct_rows,
    _reference_relevance,
    _search_text,
    _unwrap_search_href,
    _url_from_cite,
)

_SEARCH_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/127 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/rss+xml",
    "Accept-Language": "zh-CN,zh;q=0.9,vi;q=0.8,en;q=0.7",
}


def _fetch_search_page(url: str, timeout: int = 20) -> str:
    request = Request(url, headers=_SEARCH_HEADERS)
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _bing_reference_search(keyword: str, domain: str, limit: int) -> List[Dict]:
    """Tìm bài bằng trang HTML Bing; chấp nhận href mới (tilk) và URL trong cite."""
    query = "site:%s %s" % (domain, keyword)
    url = ("https://www.bing.com/search?setlang=zh-hans&count=%d&q=%s" %
           (max(10, min(50, int(limit or 10) * 2)), quote_plus(query)))
    page = _fetch_search_page(url)
    blocks = re.findall(
        r'<li[^>]+class=["\'][^"\']*\bb_algo\b[^"\']*["\'][^>]*>'
        r'([\s\S]*?)</li>', page, flags=re.I)
    if not blocks:
        blocks = [page]
    rows, seen = [], set()
    for block in blocks:
        row = _parse_bing_algo_block(block, domain)
        if not row or row["url"] in seen:
            continue
        seen.add(row["url"])
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _bing_rss_search(keyword: str, domain: str, limit: int) -> List[Dict]:
    query = "site:%s %s" % (domain, keyword)
    url = "https://www.bing.com/search?format=rss&q=" + quote_plus(query)
    page = _fetch_search_page(url)
    rows, seen = [], set()
    for item in re.findall(r"<item>([\s\S]*?)</item>", page, flags=re.I):
        title_m = re.search(r"<title>([\s\S]*?)</title>", item, flags=re.I)
        title = _clean_html_fragment(title_m.group(1) if title_m else "")
        link_m = re.search(r"<link>([\s\S]*?)</link>", item, flags=re.I)
        desc_m = re.search(r"<description>([\s\S]*?)</description>", item, flags=re.I)
        raw_link = _clean_html_fragment(link_m.group(1) if link_m else "")
        link = _pick_domain_url(
            [raw_link, _url_from_cite(desc_m.group(1) if desc_m else "", domain)],
            domain)
        if not title or not link or link in seen:
            continue
        seen.add(link)
        rows.append({"kind": "reference", "provider": "bing_rss", "title": title,
                     "url": link,
                     "excerpt": _clean_html_fragment(desc_m.group(1) if desc_m else "")[:700],
                     "channel": domain, "duration": 0})
        if len(rows) >= limit:
            break
    return rows


def _ddg_reference_search(keyword: str, domain: str, limit: int) -> List[Dict]:
    """DuckDuckGo HTML: Bing hay bỏ ``site:`` hoặc đổi class kết quả."""
    query = "site:%s %s" % (domain, keyword)
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    page = _fetch_search_page(url)
    rows, seen = [], set()
    pattern = re.compile(
        r'<a[^>]+class=["\'][^"\']*result__a[^"\']*["\'][^>]*href=["\']([^"\']+)["\']'
        r'[^>]*>([\s\S]*?)</a>', re.I)
    for match in pattern.finditer(page or ""):
        link = _unwrap_search_href(match.group(1))
        if not _domain_matches(link, domain) or link in seen:
            continue
        title = _clean_html_fragment(match.group(2))
        if len(title) < 4:
            continue
        nearby = page[max(0, match.start() - 80):min(len(page), match.end() + 400)]
        excerpt = _clean_html_fragment(nearby)
        seen.add(link)
        rows.append({"kind": "reference", "provider": "duckduckgo", "title": title,
                     "url": link, "excerpt": excerpt[:700], "channel": domain,
                     "duration": 0})
        if len(rows) >= limit:
            break
    return rows


def _web_reference_search(keyword: str, domain: str, limit: int) -> List[Dict]:
    """Bing HTML -> Bing RSS -> DuckDuckGo. Trả list rỗng nếu cả ba không ra."""
    last_error = ""
    for fn in (_bing_reference_search, _bing_rss_search, _ddg_reference_search):
        try:
            rows = fn(keyword, domain, limit)
        except Exception as exc:
            last_error = "%s: %s" % (fn.__name__, exc)
            rows = []
        if rows:
            return rows
    if last_error:
        log("Tìm web %s tạm lỗi: %s" % (domain, last_error[:180]), "warn")
    return []


def _site_listing_search(keyword: str, source: Dict, limit: int) -> List[Dict]:
    """Lấy bài từ trang mục lục/ô tìm của chính website dân gian."""
    domain = str(source.get("domain") or "")
    query = _focused_chinese_query(keyword) or keyword
    encoded = quote_plus(query)
    urls = [str(template).replace("{query}", encoded)
            for template in (source.get("listing_urls") or []) if template]
    if not urls:
        return []
    headers = {
        "User-Agent": _SEARCH_HEADERS["User-Agent"],
        "Accept-Language": "zh-CN,zh;q=0.9,vi;q=0.8",
    }
    rows, seen = [], set()
    compact = _search_text(keyword)
    terms = [term for term in _TOPIC_TERMS if term in compact] or [compact]
    for page_url in urls:
        try:
            response = requests.get(page_url, headers=headers, timeout=20)
            response.raise_for_status()
            page = response.text
        except Exception:
            continue
        found = _html_search_links(
            page, page_url, domain,
            r".+", max(20, int(limit or 8) * 4))
        scored = []
        for row in found:
            if row["url"] in seen or not _looks_like_article(row["url"], row["title"]):
                continue
            hay = _search_text(row["title"] + " " + row.get("excerpt", ""))
            score = sum(4 if term in hay else 0 for term in terms)
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        # Ưu tiên bài khớp từ khóa; nếu trang đã là mục dân gian thì lấy cả bài
        # có tiêu đề dài dù không trùng 100% chữ tìm.
        picked = [row for score, row in scored if score > 0]
        if len(picked) < max(3, limit // 2):
            picked.extend(row for score, row in scored if score == 0)
        for row in picked:
            if row["url"] in seen:
                continue
            seen.add(row["url"])
            row["provider"] = "site_listing"
            rows.append(row)
            if len(rows) >= limit:
                return rows
    return rows

def _direct_reference_search(keyword: str, source: Dict, limit: int) -> List[Dict]:
    """Dùng ô tìm kiếm/API chính chủ trước, Bing chỉ là phương án dự phòng."""
    strategy = str(source.get("direct_search") or "").strip()
    if not strategy:
        return []
    compact = _compact_chinese_query(keyword)
    spiritual = any(term in compact for term in _SPIRITUAL_TERMS)
    query = (_compact_chinese_query(keyword) if strategy in {
        "douban_groups", "douban_read", "hongxiu"
    } and not spiritual else _focused_chinese_query(keyword))
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 Chrome/127 Safari/537.36"),
        "Accept-Language": "zh-CN,zh;q=0.9,vi;q=0.8",
    }
    if strategy == "site_listing":
        return _site_listing_search(keyword, source, limit)
    if strategy == "qimao":
        response = requests.get("https://www.qimao.com/api/search/result",
                                params={"keyword": query, "page": 1},
                                headers=headers, timeout=20)
        response.raise_for_status()
        items = ((response.json().get("data") or {}).get("search_list") or [])
        rows = [{
            "kind": "reference", "provider": "qimao_search",
            "title": _clean_html_fragment(str(item.get("title") or "")),
            "url": str(item.get("read_url") or
                       "https://www.qimao.com/shuku/%s/" % item.get("book_id")),
            "excerpt": _clean_html_fragment(str(item.get("intro") or ""))[:700],
            "channel": source["domain"], "duration": 0,
            "author": str(item.get("author") or ""),
            "word_count_label": str(item.get("words_num") or ""),
        } for item in items if item.get("title") and item.get("book_id")]
        return _rank_direct_rows(rows, query, limit)
    if strategy == "zongheng":
        response = requests.get("https://search.zongheng.com/search/book",
                                params={"keyword": query, "pageNo": 1,
                                        "pageNum": max(10, limit * 3),
                                        "sort": "totalClick"},
                                headers={**headers, "Referer": "https://search.zongheng.com/"},
                                timeout=20)
        response.raise_for_status()
        payload = response.json()
        items = ((((payload.get("data") or {}).get("datas") or {}).get("list")) or [])
        rows = [{
            "kind": "reference", "provider": "zongheng_search",
            "title": _clean_html_fragment(str(item.get("name") or "")),
            "url": "https://book.zongheng.com/book/%s.html" % item.get("bookId"),
            "excerpt": _clean_html_fragment(str(item.get("description") or ""))[:700],
            "channel": source["domain"], "duration": 0,
            "author": str(item.get("authorName") or ""),
            "word_count": int(item.get("totalWord") or 0),
            "read_count": int(item.get("totalClick") or 0),
            "engagement_count": int(item.get("totalRecommend") or 0),
        } for item in items if item.get("name") and item.get("bookId")]
        return _rank_direct_rows(rows, query, limit)

    if strategy == "douban_groups":
        url = ("https://www.douban.com/group/search?cat=1013&sort=relevance&q=" +
               quote_plus(query))
        path_pattern = r"/group/topic/\d+"
    elif strategy == "douban_read":
        url = "https://read.douban.com/search?q=" + quote_plus(query)
        path_pattern = r"/(?:ebook|column)/\d+"
    elif strategy == "hongxiu":
        url = "https://www.hongxiu.com/search?kw=" + quote_plus(query)
        path_pattern = r"/book/\d+"
    elif strategy == "qidian_mobile":
        url = "https://m.qidian.com/search?kw=" + quote_plus(query)
        path_pattern = r"/chapter/\d+/0"
    else:
        return []
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    rows = _html_search_links(response.text, url, source["domain"],
                              path_pattern, max(10, limit * 3))
    return _rank_direct_rows(rows, query, limit)


def search_web_references(keyword: str, source_keys=None, limit: int = 20) -> List[Dict]:
    """Tìm metadata/snippet bài tham khảo, chưa tải toàn văn bài gốc."""
    keyword = str(keyword or "").strip()
    if not keyword:
        raise ValueError("Hãy nhập từ khóa tham khảo.")
    selected = set(str(x) for x in (source_keys or []) if str(x).strip())
    sources = [x for x in REFERENCE_SOURCES if not selected or x["key"] in selected]
    rows, seen = [], set()
    wanted = max(1, min(50, int(limit or 20)))
    spiritual_query = any(term in _search_text(keyword)
                          for term in _SPIRITUAL_TERMS)
    # Tâm linh hay bị lọc mất bài: lấy nhiều hơn mỗi nguồn rồi cắt theo hạn.
    each_cap = 16 if spiritual_query else 10
    each = max(1, min(each_cap,
                      (wanted + max(1, len(sources)) - 1) // max(1, len(sources))))
    if spiritual_query:
        each = max(each, min(each_cap, 8))

    def search_one(source):
        suffix = str(source.get("search_suffix") or "").strip()
        query = " ".join(value for value in (keyword, suffix) if value)
        direct_error = ""
        try:
            direct = _direct_reference_search(keyword, source, each)
        except Exception as exc:
            direct, direct_error = [], str(exc)
        direct_fallback = list(direct)
        # Ô tìm nội bộ của vài website khớp kiểu OR. Với truy vấn tâm linh,
        # bỏ các dòng chỉ khớp "nông thôn/người già" trước khi quyết định rằng
        # tìm trực tiếp đã thành công, để còn được thử Bing chính xác hơn.
        if direct and any(term in _search_text(keyword)
                          for term in _SPIRITUAL_TERMS):
            direct = [row for row in direct
                      if _reference_relevance(row, keyword)["relevance_accepted"]]
        if direct:
            return source, direct, ""
        try:
            bing = _web_reference_search(query, source["domain"], each)
            return source, (bing or direct_fallback), ""
        except Exception as exc:
            if direct_fallback:
                return source, direct_fallback, ""
            error = str(exc)
            if direct_error:
                error = "tìm trực tiếp: %s; Bing: %s" % (direct_error, error)
            return source, [], error

    # Số nền tảng Trung Quốc đã tăng đáng kể. Tìm song song giúp thời gian chờ
    # gần với một lượt Bing thay vì cộng dồn timeout của từng website.
    results = []
    workers = max(1, min(6, len(sources)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(search_one, source): index
                   for index, source in enumerate(sources)}
        for future in as_completed(futures):
            source, found, error = future.result()
            results.append((futures[future], source, found, error))

    # Trả về theo thứ tự catalog ổn định; bước enrich sau đó mới xếp theo lượt đọc.
    for _index, source, found, error in sorted(results, key=lambda value: value[0]):
        if error:
            log("Nguồn tham khảo %s tạm không truy cập được: %s" %
                (source["name"], error), "warn")
            continue
        for row in found:
            if row["url"] in seen:
                continue
            seen.add(row["url"])
            row["source_key"] = source["key"]
            row["source_name"] = source["name"]
            row["language"] = source["lang"]
            row["source_priority"] = source.get("priority", "")
            row["narrative_style"] = source.get("narrative_style", "")
            row["content_form"] = source.get("content_form", "")
            row["source_notes"] = source.get("notes", "")
            rows.append(row)
    return rows[:wanted]

def _run_metadata(cmd: List[str]):
    """Chạy truy vấn yt-dlp; cookie browser bị khóa thì lui về nguồn công khai."""
    try:
        return downloader.run(cmd, quiet=True)
    except RuntimeError as exc:
        if (downloader._browser_cookie_failed(exc) and
                "--cookies-from-browser" in cmd):
            log("Không đọc được cookie trình duyệt; tìm lại trong nguồn video công khai.",
                "warn")
            return downloader.run(
                downloader._without_option_value(cmd, "--cookies-from-browser"),
                quiet=True)
        raise


def _entry_url(entry: Dict) -> str:
    url = str(entry.get("webpage_url") or entry.get("original_url") or
              entry.get("url") or "").strip()
    if url.startswith("http"):
        return url
    ident = str(entry.get("id") or "").strip()
    if ident:
        extractor = str(entry.get("extractor_key") or entry.get("extractor") or "").lower()
        if "youtube" in extractor:
            return "https://www.youtube.com/watch?v=" + ident
        if "douyin" in extractor:
            return "https://www.douyin.com/video/" + ident
        if "tiktok" in extractor:
            return "https://www.tiktok.com/video/" + ident
        if "ixigua" in extractor:
            return "https://www.ixigua.com/" + ident
        return "https://www.bilibili.com/video/" + ident
    return ""


def _normalise_entry(entry: Dict, index: int = 0) -> Dict:
    url = _entry_url(entry)
    extractor = str(entry.get("extractor_key") or entry.get("extractor") or "").lower()
    provider = detect_site(url)
    if provider == "other":
        if "youtube" in extractor:
            provider = "youtube"
        elif "douyin" in extractor:
            provider = "douyin"
        elif "tiktok" in extractor:
            provider = "tiktok"
        else:
            provider = "bilibili"
    return {
        "index": int(index or entry.get("playlist_index") or 0),
        "id": str(entry.get("id") or ""),
        "title": str(entry.get("title") or entry.get("fulltitle") or "").strip(),
        "url": url,
        "duration": float(entry.get("duration") or 0),
        "thumbnail": str(entry.get("thumbnail") or ""),
        "channel": str(entry.get("channel") or entry.get("uploader") or ""),
        "provider": provider,
    }


def _duration_seconds(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value or "").strip().split(":")
    try:
        total = 0.0
        for part in parts:
            total = total * 60 + float(part)
        return total
    except (TypeError, ValueError):
        return 0.0


def _search_bilibili_api(keyword: str, limit: int) -> List[Dict]:
    """Fallback chính chủ khi yt-dlp không nhận trang search.bilibili.com."""
    url = ("https://api.bilibili.com/x/web-interface/search/type?"
           "search_type=video&page=1&page_size=%d&keyword=%s" %
           (limit, quote_plus(keyword)))
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 AutoDubVN/2.5",
        "Referer": "https://search.bilibili.com/",
        "Accept": "application/json",
    })
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    items = ((payload.get("data") or {}).get("result") or []) if isinstance(payload, dict) else []
    rows = []
    for index, item in enumerate(items[:limit], 1):
        bvid = str(item.get("bvid") or item.get("id") or "").strip()
        if not bvid:
            continue
        title = re.sub(r"<[^>]+>", "", str(item.get("title") or "")).strip()
        thumb = str(item.get("pic") or "")
        if thumb.startswith("//"):
            thumb = "https:" + thumb
        rows.append({
            "index": index, "id": bvid, "title": title,
            "url": "https://www.bilibili.com/video/" + bvid,
            "duration": _duration_seconds(item.get("duration")),
            "thumbnail": thumb,
            "channel": str(item.get("author") or "Bilibili"),
            "provider": "bilibili",
        })
    return rows


def search_bilibili(keyword: str, limit: int = 10,
                    cookies_from_browser: Optional[str] = None,
                    cookies_file: Optional[str] = None) -> List[Dict]:
    """Tìm tối đa ``limit`` video trên trang tìm kiếm Bilibili."""
    keyword = str(keyword or "").strip()
    if not keyword:
        raise ValueError("Hãy nhập từ khóa tìm video Bilibili.")
    cookies_from_browser = downloader._normalise_cookie_browser(cookies_from_browser)
    limit = max(1, min(50, int(limit or 10)))
    cmd = [
        *downloader._ytdlp_cmd(), "--flat-playlist", "--dump-single-json",
        "--skip-download", "--playlist-end", str(limit), "--no-warnings",
        "--ignore-errors", "https://search.bilibili.com/all?keyword=" +
        quote_plus(keyword),
    ]
    if cookies_from_browser:
        cmd[cmd.index("--no-warnings"):cmd.index("--no-warnings")] = [
            "--cookies-from-browser", str(cookies_from_browser)]
    if cookies_file:
        cmd[cmd.index("--no-warnings"):cmd.index("--no-warnings")] = [
            "--cookies", str(cookies_file)]
    try:
        result = _run_metadata(cmd)
    except RuntimeError as exc:
        if "unsupported url" in str(exc).lower():
            return _search_bilibili_api(keyword, limit)
        raise
    raw = str(result.stdout or "").strip()
    if not raw:
        return []
    payloads: List[Dict] = []
    try:
        payload = json.loads(raw)
        payloads = list(payload.get("entries") or []) if isinstance(payload, dict) else []
    except json.JSONDecodeError:
        for line in raw.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                payloads.append(item)
    out = []
    seen = set()
    for i, item in enumerate(payloads[:limit], 1):
        row = _normalise_entry(item, i)
        if not row["url"] or row["url"] in seen:
            continue
        seen.add(row["url"])
        out.append(row)
    return out


def search_youtube(keyword: str, limit: int = 10,
                   cookies_from_browser: Optional[str] = None,
                   cookies_file: Optional[str] = None) -> List[Dict]:
    """Tìm video YouTube bằng ytsearch của yt-dlp, không cần API key."""
    keyword = str(keyword or "").strip()
    if not keyword:
        raise ValueError("Hãy nhập từ khóa tìm video YouTube.")
    cookies_from_browser = downloader._normalise_cookie_browser(cookies_from_browser)
    limit = max(1, min(50, int(limit or 10)))
    cmd = [*downloader._ytdlp_cmd(), "--flat-playlist", "--dump-single-json",
           "--skip-download", "--no-warnings", "--ignore-errors",
           *site_ytdlp_args("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
           "ytsearch%d:%s" % (limit, keyword)]
    if cookies_from_browser:
        cmd[cmd.index("--no-warnings"):cmd.index("--no-warnings")] = [
            "--cookies-from-browser", str(cookies_from_browser)]
    if cookies_file:
        cmd[cmd.index("--no-warnings"):cmd.index("--no-warnings")] = [
            "--cookies", str(cookies_file)]
    result = _run_metadata(cmd)
    try:
        payload = json.loads(str(result.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        return []
    entries = list(payload.get("entries") or []) if isinstance(payload, dict) else []
    return [_normalise_entry(item, i) for i, item in enumerate(entries[:limit], 1)
            if isinstance(item, dict) and _entry_url(item)]


_WEB_VIDEO_SITES = {
    "douyin": {
        "site": "www.douyin.com/video",
        "pattern": re.compile(
            r"https?://(?:www\.)?douyin\.com/video/\d+", re.I),
        "label": "Douyin",
    },
    "tiktok": {
        "site": "www.tiktok.com",
        "pattern": re.compile(
            r"https?://(?:www\.)?tiktok\.com/@[^/\s]+/video/\d+", re.I),
        "label": "TikTok",
    },
    "kuaishou": {
        "site": "www.kuaishou.com",
        "pattern": re.compile(
            r"https?://(?:www\.)?kuaishou\.com/(?:short-video|f)/[\w.-]+", re.I),
        "label": "Kuaishou",
    },
    "ixigua": {
        "site": "www.ixigua.com",
        "pattern": re.compile(
            r"https?://(?:www\.)?ixigua\.com/(?:video/)?\d+", re.I),
        "label": "Ixigua",
    },
}


def _html_video_links(page: str, pattern: re.Pattern, provider: str,
                      limit: int) -> List[Dict]:
    rows, seen = [], set()
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', page or "", flags=re.I)
    hrefs += re.findall(r'https?://[^\s<>"\']+', page or "", flags=re.I)
    for raw in hrefs:
        link = _unwrap_search_href(raw)
        match = pattern.search(link or "")
        if not match:
            continue
        url = match.group(0).rstrip(".,;)]}")
        if url in seen:
            continue
        seen.add(url)
        title = url.rsplit("/", 1)[-1]
        nearby = (page or "")
        title_m = re.search(
            r'href=["\'][^"\']*' + re.escape(url.split("://", 1)[-1][:40]) +
            r'[^"\']*["\'][^>]*>([\s\S]*?)</a>', nearby, flags=re.I)
        if title_m:
            cleaned = _clean_html_fragment(title_m.group(1)).strip()
            if len(cleaned) >= 4:
                title = cleaned
        rows.append({
            "index": len(rows) + 1, "id": url.rsplit("/", 1)[-1],
            "title": title, "url": url, "duration": 0.0, "thumbnail": "",
            "channel": "", "provider": provider,
        })
        if len(rows) >= limit:
            break
    return rows


def _search_web_videos(keyword: str, provider: str, limit: int) -> List[Dict]:
    meta = _WEB_VIDEO_SITES[provider]
    query = "site:%s %s" % (meta["site"], keyword)
    pages = []
    bing = ("https://www.bing.com/search?setlang=zh-hans&count=%d&q=%s" %
            (max(10, min(50, int(limit or 10) * 3)), quote_plus(query)))
    ddg = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    last_error = ""
    for url in (bing, ddg):
        try:
            pages.append(_fetch_search_page(url))
        except Exception as exc:
            last_error = str(exc)
            continue
        rows = _html_video_links(pages[-1], meta["pattern"], provider, limit)
        if rows:
            return rows
    if last_error:
        log("Tìm %s tạm lỗi: %s" % (meta["label"], last_error[:180]), "warn")
    return []


def search_douyin(keyword: str, limit: int = 10,
                  cookies_from_browser: Optional[str] = None,
                  cookies_file: Optional[str] = None) -> List[Dict]:
    keyword = str(keyword or "").strip()
    if not keyword:
        raise ValueError("Hãy nhập từ khóa tìm video Douyin.")
    return _search_web_videos(keyword, "douyin", max(1, min(50, int(limit or 10))))


def search_tiktok(keyword: str, limit: int = 10,
                  cookies_from_browser: Optional[str] = None,
                  cookies_file: Optional[str] = None) -> List[Dict]:
    keyword = str(keyword or "").strip()
    if not keyword:
        raise ValueError("Hãy nhập từ khóa tìm video TikTok.")
    return _search_web_videos(keyword, "tiktok", max(1, min(50, int(limit or 10))))


def search_kuaishou(keyword: str, limit: int = 10,
                    cookies_from_browser: Optional[str] = None,
                    cookies_file: Optional[str] = None) -> List[Dict]:
    keyword = str(keyword or "").strip()
    if not keyword:
        raise ValueError("Hãy nhập từ khóa tìm video Kuaishou.")
    return _search_web_videos(keyword, "kuaishou", max(1, min(50, int(limit or 10))))


def search_ixigua(keyword: str, limit: int = 10,
                  cookies_from_browser: Optional[str] = None,
                  cookies_file: Optional[str] = None) -> List[Dict]:
    keyword = str(keyword or "").strip()
    if not keyword:
        raise ValueError("Hãy nhập từ khóa tìm video Ixigua.")
    return _search_web_videos(keyword, "ixigua", max(1, min(50, int(limit or 10))))


def search(keyword: str, limit: int = 10, provider: str = "bilibili",
           cookies_from_browser: Optional[str] = None,
           cookies_file: Optional[str] = None) -> List[Dict]:
    """Tìm một hoặc nhiều nguồn và gộp kết quả theo đúng giới hạn."""
    provider = normalise_search_provider(provider, default="bilibili")
    fns = {
        "youtube": search_youtube,
        "bilibili": search_bilibili,
        "douyin": search_douyin,
        "tiktok": search_tiktok,
        "kuaishou": search_kuaishou,
        "ixigua": search_ixigua,
    }
    fn = fns.get(provider)
    if fn is not None:
        return fn(keyword, limit, cookies_from_browser, cookies_file)
    rows = []
    wanted = max(1, int(limit or 10))
    for name in ("bilibili", "youtube", "douyin"):
        if len(rows) >= wanted:
            break
        try:
            rows += fns[name](
                keyword, wanted - len(rows), cookies_from_browser, cookies_file)
        except Exception as exc:
            log("%s tạm chặn tìm kiếm; thử nguồn khác: %s" % (name, exc), "warn")
            if not rows and name == "douyin":
                raise
    return rows[:wanted]
