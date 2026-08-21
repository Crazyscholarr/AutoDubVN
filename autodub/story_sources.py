"""Tìm và tải nguồn video cho chế độ Kể chuyện AI.

Nguồn tìm kiếm dùng extractor của yt-dlp thay vì tự gọi API Bilibili, nhờ vậy
vẫn dùng được cookies đăng nhập và không làm hỏng downloader hiện có.
"""
from __future__ import annotations

import json
import os
import re
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from . import downloader
from .utils import log, run


# Các nguồn chỉ dùng để lấy tình huống, tiêu đề và từ khóa tham khảo. Luồng
# viết truyện luôn yêu cầu đổi nhân vật/bối cảnh/lời văn, không đọc lại nguyên
# văn bài nguồn.
REFERENCE_SOURCES = [
    {"key": "vnexpress_tamsu", "name": "VnExpress Tâm sự",
     "address": "https://vnexpress.net/tam-su", "type": "Việt - chuyện thật",
     "why": "Bạn đọc tự kể chuyện hôn nhân, gia đình, con cái.", "priority": "Cao",
     "notes": "Chỉ giữ tình huống; viết lại hoàn toàn.", "lang": "vi", "domain": "vnexpress.net"},
    {"key": "dantri_tinhyeu", "name": "Dân trí - Tình yêu Giới tính",
     "address": "https://dantri.com.vn/tinh-yeu-gioi-tinh", "type": "Việt - chuyện thật",
     "why": "Nhiều chất liệu tâm sự gia đình và người lớn tuổi.", "priority": "Cao",
     "notes": "Đổi tên, địa danh và toàn bộ cách kể.", "lang": "vi", "domain": "dantri.com.vn"},
    {"key": "webtretho_honnhan", "name": "Webtretho - Hôn nhân gia đình",
     "address": "https://www.webtretho.vn/f/chuyen-hon-nhan-gia-dinh", "type": "Việt - diễn đàn",
     "why": "Nhiều tình huống mẹ chồng nàng dâu, ngoại tình, ly hôn.", "priority": "Rất cao",
     "notes": "Nội dung diễn đàn cần biên tập nặng.", "lang": "vi", "domain": "webtretho.vn"},
    {"key": "phunuonline", "name": "Phụ nữ Online - Tuổi xế chiều",
     "address": "https://www.phunuonline.com.vn", "type": "Việt - chuyện thật",
     "why": "Tái hôn, cô đơn và tình cảm tuổi 60-70.", "priority": "Trung bình",
     "notes": "Chỉ dùng làm chất liệu tình huống.", "lang": "vi", "domain": "phunuonline.com.vn"},
    {"key": "youtube_comments", "name": "Bình luận khán giả kênh chuyện đời",
     "address": "Kênh Hương Quê Kể Chuyện; Tuổi Già An Nhiên", "type": "Việt - bình luận khán giả",
     "why": "Khán giả tự kể tình huống, đúng tệp tuổi và nhu cầu nghe.", "priority": "Rất cao",
     "notes": "Chỉ đọc để lấy mô-típ; không chép bình luận hay nhận diện người dùng.",
     "lang": "vi", "domain": "youtube.com"},
    {"key": "zhihu_yanxuan", "name": "知乎盐选故事 (Zhihu)",
     "address": "https://www.zhihu.com", "type": "Trung - truyện ngắn",
     "why": "Kho lớn về 婆媳, 中老年 và tranh chấp gia đình.", "priority": "Rất cao",
     "notes": "Bắt buộc Việt hóa mạnh; không sao chép bản dịch.", "lang": "zh", "domain": "zhihu.com"},
    {"key": "660i_story", "name": "660i 故事大全",
     "address": "https://660i.com/story", "type": "Trung - truyện ngắn",
     "why": "Chủ yếu truyện thiếu nhi; chỉ tham khảo 民间故事 và 鬼故事 khi mở nhánh dân gian.",
     "priority": "Thấp", "notes": "Không ưu tiên cho ngách U70.", "lang": "zh", "domain": "660i.com"},
    {"key": "fanqie", "name": "番茄小说 (Fanqie)",
     "address": "https://fanqienovel.com", "type": "Trung - truyện dài",
     "why": "Truyện đô thị gia đình dài tập, phù hợp làm series.", "priority": "Thấp",
     "notes": "Chỉ dùng ý tưởng/mô-típ; cần cắt gọn và viết mới.", "lang": "zh", "domain": "fanqienovel.com"},
    {"key": "reddit_aita", "name": "Reddit r/AmItheAsshole",
     "address": "https://www.reddit.com/r/AmItheAsshole", "type": "Anh - chuyện thật",
     "why": "Xung đột gia đình có cấu trúc rõ và nhiều cú lật.", "priority": "Trung bình",
     "notes": "Phải Việt hóa mạnh bối cảnh và chuẩn mực ứng xử.", "lang": "en", "domain": "reddit.com"},
]

CHINESE_KEYWORDS = [
    {"keyword": "婆媳矛盾 故事", "meaning": "Mâu thuẫn mẹ chồng nàng dâu", "topic": "Mẹ chồng nàng dâu", "note": "Từ khóa gốc, dùng nhiều nhất"},
    {"keyword": "婆媳 真实经历", "meaning": "Trải nghiệm thật mẹ chồng nàng dâu", "topic": "Mẹ chồng nàng dâu", "note": "Tình huống người thật kể"},
    {"keyword": "赡养纠纷 故事", "meaning": "Tranh chấp phụng dưỡng cha mẹ", "topic": "Con cái bất hiếu", "note": "Tranh luận trách nhiệm chăm cha mẹ"},
    {"keyword": "儿女不孝 老人", "meaning": "Con cái bất hiếu, người già", "topic": "Con cái bất hiếu", "note": "Chủ đề xung đột mạnh"},
    {"keyword": "中老年情感故事", "meaning": "Chuyện tình cảm trung niên và cao tuổi", "topic": "Tình yêu xế chiều", "note": "Đúng tệp U60-U70"},
    {"keyword": "老年夫妻 感情", "meaning": "Tình cảm vợ chồng già", "topic": "Vợ chồng tuổi già", "note": "Có thể ghép nhân quả"},
    {"keyword": "老伴去世 再婚", "meaning": "Bạn đời mất, tái hôn", "topic": "Cha mẹ tái hôn", "note": "Con cái phản đối tái hôn"},
    {"keyword": "空巢老人 故事", "meaning": "Người già sống một mình", "topic": "Cô đơn tuổi già", "note": "Con đi xa, viện dưỡng lão"},
    {"keyword": "遗产 分家 纠纷", "meaning": "Tranh chấp thừa kế chia gia sản", "topic": "Tranh chấp thừa kế", "note": "Tình huống gia đình nhiều nút thắt"},
    {"keyword": "出轨 报应 中年", "meaning": "Ngoại tình và quả báo tuổi trung niên", "topic": "Nhân quả ngoại tình", "note": "Kết hợp với tuyến gia đình"},
    {"keyword": "农村 婆媳 故事", "meaning": "Mẹ chồng nàng dâu nông thôn", "topic": "Mẹ chồng nàng dâu", "note": "Dễ chuyển sang làng quê Việt"},
    {"keyword": "保姆 雇主 老人 故事", "meaning": "Người giúp việc và ông chủ già", "topic": "Giúp việc và người già", "note": "Tuyến đời thường giàu tình tiết"},
    {"keyword": "养老院 真实故事", "meaning": "Chuyện thật ở viện dưỡng lão", "topic": "Cô đơn tuổi già", "note": "Chất liệu cảm động"},
    {"keyword": "重男轻女 遗产", "meaning": "Trọng nam khinh nữ trong chia thừa kế", "topic": "Tranh chấp thừa kế", "note": "Hợp khán giả phụ nữ 45+"},
    {"keyword": "女婿 岳母 矛盾", "meaning": "Mâu thuẫn con rể và mẹ vợ", "topic": "Gia đình thông gia", "note": "Nhánh ít người làm, nên thử"},
]


def reference_catalog() -> List[Dict]:
    """Trả về bản sao catalog để API/UI không sửa dữ liệu gốc."""
    return [dict(row) for row in REFERENCE_SOURCES]


def chinese_keyword_catalog() -> List[Dict]:
    return [dict(row) for row in CHINESE_KEYWORDS]


def _bing_reference_search(keyword: str, domain: str, limit: int) -> List[Dict]:
    query = "site:%s %s" % (domain, keyword)
    url = "https://www.bing.com/search?format=rss&q=" + quote_plus(query)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 AutoDubVN/2.5",
                                    "Accept": "application/rss+xml,application/xml"})
    with urlopen(request, timeout=20) as response:
        root = ET.fromstring(response.read())
    rows = []
    for item in root.findall("./channel/item")[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        excerpt = re.sub(r"\s+", " ", (item.findtext("description") or "")).strip()
        if title and link:
            rows.append({"kind": "reference", "provider": "web", "title": title,
                         "url": link, "excerpt": excerpt[:500], "channel": domain,
                         "duration": 0})
    return rows


def search_web_references(keyword: str, source_keys=None, limit: int = 20) -> List[Dict]:
    """Tìm metadata/snippet bài tham khảo qua Bing RSS, không tải lại bài gốc."""
    keyword = str(keyword or "").strip()
    if not keyword:
        raise ValueError("Hãy nhập từ khóa tham khảo.")
    selected = set(str(x) for x in (source_keys or []) if str(x).strip())
    sources = [x for x in REFERENCE_SOURCES if not selected or x["key"] in selected]
    rows, seen = [], set()
    each = max(1, min(10, int(limit or 20) // max(1, len(sources))))
    for source in sources:
        try:
            found = _bing_reference_search(keyword, source["domain"], each)
        except Exception as exc:
            log("Nguồn tham khảo %s tạm không truy cập được: %s" %
                (source["name"], exc), "warn")
            continue
        for row in found:
            if row["url"] in seen:
                continue
            seen.add(row["url"])
            row["source_key"] = source["key"]
            row["source_name"] = source["name"]
            row["language"] = source["lang"]
            rows.append(row)
    return rows[:max(1, min(50, int(limit or 20)))]


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
        return "https://www.bilibili.com/video/" + ident
    return ""


def _normalise_entry(entry: Dict, index: int = 0) -> Dict:
    url = _entry_url(entry)
    extractor = str(entry.get("extractor_key") or entry.get("extractor") or "").lower()
    is_youtube = ("youtube" in extractor or "youtube.com/" in url.lower() or
                  "youtu.be/" in url.lower())
    return {
        "index": int(index or entry.get("playlist_index") or 0),
        "id": str(entry.get("id") or ""),
        "title": str(entry.get("title") or entry.get("fulltitle") or "").strip(),
        "url": url,
        "duration": float(entry.get("duration") or 0),
        "thumbnail": str(entry.get("thumbnail") or ""),
        "channel": str(entry.get("channel") or entry.get("uploader") or ""),
        "provider": ("youtube" if is_youtube else "bilibili"),
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


def search(keyword: str, limit: int = 10, provider: str = "bilibili",
           cookies_from_browser: Optional[str] = None,
           cookies_file: Optional[str] = None) -> List[Dict]:
    """Tìm một hoặc hai nguồn và gộp kết quả theo đúng giới hạn."""
    provider = str(provider or "bilibili").strip().lower()
    if provider == "youtube":
        return search_youtube(keyword, limit, cookies_from_browser, cookies_file)
    if provider in {"all", "both", "tat_ca"}:
        each = max(1, (int(limit) + 1) // 2)
        rows = []
        try:
            rows += search_bilibili(keyword, each, cookies_from_browser, cookies_file)
        except Exception as exc:
            log("Bilibili tạm chặn tìm kiếm; vẫn tiếp tục với YouTube: %s" % exc,
                "warn")
        try:
            # Nguồn còn hoạt động được phép bù toàn bộ giới hạn khi nguồn kia lỗi.
            youtube_limit = max(1, int(limit) - len(rows))
            rows += search_youtube(
                keyword, youtube_limit, cookies_from_browser, cookies_file)
        except Exception as exc:
            if not rows:
                raise
            log("YouTube tạm lỗi; dùng kết quả Bilibili đã tìm được: %s" % exc,
                "warn")
        return rows[:max(1, int(limit))]
    return search_bilibili(keyword, limit, cookies_from_browser, cookies_file)


def download_many(urls: Iterable[str], out_dir: str, quality: str = "best",
                  cookies_from_browser: Optional[str] = None,
                  cookies_file: Optional[str] = None,
                  concurrent_fragments: int = 8,
                  external_downloader: Optional[str] = "auto",
                  progress: Optional[Callable[[int, int, str], None]] = None,
                  live_progress: Optional[Callable[[float, str], None]] = None,
                  max_workers: int = 3) -> List[Dict]:
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
            progress_callback=_item_progress)
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
