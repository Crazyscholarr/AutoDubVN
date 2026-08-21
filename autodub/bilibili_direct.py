"""Bộ tải Bilibili trực tiếp, tối ưu cho các video nền công khai.

Luồng xử lý được chuyển thể từ dự án ``zephyr-breeze-sage-crystal``:

* hỏi song song nhiều API playurl (MP4, DASH và WBI);
* mở rộng URL sang các CDN mirror của Bilibili và chọn máy chủ phản hồi trước;
* tải file lớn theo các khối Range 1 MiB, bốn khối song song;
* từng khối tự đổi CDN khi một máy chủ ngắt giữa chừng;
* giữ file ``.part`` để lần sau nối tiếp thay vì tải lại từ đầu.

Module này không phụ thuộc yt-dlp. ``autodub.downloader`` vẫn giữ yt-dlp làm
đường lui cho URL không phải Bilibili và các trường hợp API trực tiếp bị chặn.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from .utils import run


CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_BVID_RE = re.compile(r"\b(BV[0-9A-Za-z]{10})\b", re.I)
_MIRRORS = (
    "upos-sz-mirrorcos.bilivideo.com",
    "upos-sz-mirrorali.bilivideo.com",
    "upos-sz-mirrorhw.bilivideo.com",
    "upos-sz-estgcos.bilivideo.com",
)
_ALLOWED_CDN_SUFFIXES = (
    "bilivideo.com", "akamaized.net", "biliapi.net", "hdslb.com",
    "bilibili.com", "b23.tv", "bili2233.cn",
)
_QUALITY_TO_QN = {"360": 16, "480": 32, "720": 64, "1080": 80, "best": 120}
_QUALITY_LABEL = {
    16: "360P", 32: "480P", 64: "720P", 80: "1080P",
    112: "1080P+", 116: "1080P60", 120: "4K",
}
_MIXIN_KEY_ENC_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)
_WINDOW = 4
_CHUNK = 1024 * 1024
_MULTI_MIN = int(1.2 * 1024 * 1024)
_PROBE_CAP = 6
_READ_SIZE = 256 * 1024
_BUVID3 = str(uuid.uuid4()).upper() + "infoc"
_WBI_CACHE: Tuple[str, float] = ("", 0.0)


ProgressCallback = Optional[Callable[[Dict], None]]


@dataclass(frozen=True)
class StreamChoice:
    kind: str
    quality: int
    video_urls: Tuple[str, ...]
    audio_urls: Tuple[str, ...] = ()
    declared_size: int = 0


@dataclass(frozen=True)
class Probe:
    url: str
    length: int
    accepts_ranges: bool
    content_type: str


def extract_bvid(value: str) -> str:
    match = _BVID_RE.search(str(value or ""))
    return match.group(1) if match else ""


def is_bilibili_url(value: str) -> bool:
    """Chỉ nhận link Bilibili có BV id thật; URL test giả sẽ qua yt-dlp."""
    try:
        host = (urlparse(str(value or "")).hostname or "").lower()
    except ValueError:
        return False
    return bool(extract_bvid(value) and
                (host == "bilibili.com" or host.endswith(".bilibili.com")))


def quality_qn(value: str) -> int:
    return _QUALITY_TO_QN.get(str(value or "best").strip().lower(), 120)


def quality_label(qn: int) -> str:
    return _QUALITY_LABEL.get(int(qn or 0), f"QN{int(qn or 0)}")


def _read_cookie_file(path: Optional[str]) -> Dict[str, str]:
    """Đọc cookies.txt dạng Netscape mà không đụng DB đang khóa của Edge."""
    cookies: Dict[str, str] = {}
    if not path or not os.path.isfile(path):
        return cookies
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                raw = line.rstrip("\r\n")
                if raw.startswith("#HttpOnly_"):
                    raw = raw[len("#HttpOnly_"):]
                elif not raw or raw.startswith("#"):
                    continue
                fields = raw.split("\t")
                if len(fields) >= 7:
                    domain, name, value = fields[0], fields[5], fields[6]
                    if "bilibili.com" in domain.lower() and name:
                        cookies[name] = value
    except OSError:
        return {}
    return cookies


def _headers(bvid: str = "", cookies_file: Optional[str] = None,
             accept: str = "application/json, text/plain, */*") -> Dict[str, str]:
    cookie_values = {
        "buvid3": _BUVID3,
        "b_nut": str(int(time.time())),
    }
    cookie_values.update(_read_cookie_file(cookies_file))
    referer = (f"https://www.bilibili.com/video/{bvid}" if bvid
               else "https://www.bilibili.com")
    return {
        "User-Agent": CHROME_UA,
        "Referer": referer,
        "Origin": "https://www.bilibili.com",
        "Accept": accept,
        "Cookie": "; ".join(f"{key}={value}" for key, value in cookie_values.items()),
    }


def _json_get(url: str, headers: Dict[str, str], timeout: float = 20.0,
              allow_codes: Sequence[int] = ()) -> Dict:
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    stripped = raw.lstrip()
    if not stripped.startswith(("{", "[")):
        raise RuntimeError("API Bilibili không trả JSON; có thể đang giới hạn truy cập")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError("Dữ liệu API Bilibili không hợp lệ")
    code = int(payload.get("code") or 0)
    if code != 0 and code not in allow_codes:
        raise RuntimeError(str(payload.get("message") or payload.get("msg") or
                               f"Bilibili API lỗi {code}"))
    data = payload.get("data", payload.get("result", payload))
    return data if isinstance(data, dict) else {"result": data}


def _filename_key(url: str) -> str:
    return os.path.basename(urlparse(url).path).split(".", 1)[0]


def _mixin_key(raw: str) -> str:
    return "".join(raw[index] if index < len(raw) else ""
                   for index in _MIXIN_KEY_ENC_TAB)[:32]


def _get_wbi_mixin(headers: Dict[str, str]) -> str:
    global _WBI_CACHE
    cached, expires = _WBI_CACHE
    if cached and expires > time.time():
        return cached
    nav = _json_get("https://api.bilibili.com/x/web-interface/nav", headers,
                    allow_codes=(-101,))
    wbi = nav.get("wbi_img") or {}
    img_url, sub_url = str(wbi.get("img_url") or ""), str(wbi.get("sub_url") or "")
    if not img_url or not sub_url:
        raise RuntimeError("Không lấy được khóa WBI")
    cached = _mixin_key(_filename_key(img_url) + _filename_key(sub_url))
    _WBI_CACHE = (cached, time.time() + 30 * 60)
    return cached


def _wbi_query(params: Dict[str, object], mixin: str) -> str:
    cleaned = {
        str(key): re.sub(r"[!'()*]", "", str(value))
        for key, value in params.items()
    }
    cleaned["wts"] = str(int(time.time()))
    query = urlencode(sorted(cleaned.items()), quote_via=quote)
    signature = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
    return query + "&w_rid=" + signature


def _play_quality(play: Dict) -> int:
    try:
        quality = int(play.get("quality") or 0)
    except (TypeError, ValueError):
        quality = 0
    if quality:
        return quality
    videos = ((play.get("dash") or {}).get("video") or [])
    return max([int(item.get("id") or 0) for item in videos
                if isinstance(item, dict)] or [0])


def _valid_play(play: Dict) -> bool:
    durl = play.get("durl") or []
    dash = play.get("dash") or {}
    return bool((durl and isinstance(durl[0], dict) and durl[0].get("url")) or
                (dash.get("video") if isinstance(dash, dict) else None))


def _pick_best_play(plays: Sequence[Dict], want: int) -> Dict:
    if not plays:
        raise RuntimeError("Không lấy được địa chỉ phát từ Bilibili")
    def score(play: Dict) -> int:
        quality = _play_quality(play)
        durl = play.get("durl") or []
        has_mp4 = bool(durl and isinstance(durl[0], dict) and durl[0].get("url"))
        hit = 2000 if quality >= want else 0
        mp4_bonus = 250 if has_mp4 and quality >= min(want, 64) else 0
        return hit + mp4_bonus + quality
    return max(plays, key=score)


def _https_url(value: object) -> str:
    raw = str(value or "").strip()
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("http://"):
        return "https://" + raw[len("http://"):]
    return raw


def _allowed_cdn_host(host: str) -> bool:
    host = str(host or "").lower().rstrip(".")
    return any(host == suffix or host.endswith("." + suffix)
               for suffix in _ALLOWED_CDN_SUFFIXES)


def _collect_urls(primary: object, backups: object = None) -> Tuple[str, ...]:
    values: List[object] = [primary]
    if isinstance(backups, (list, tuple)):
        values.extend(backups)
    elif backups:
        values.append(backups)
    out: List[str] = []
    for value in values:
        url = _https_url(value)
        try:
            parsed = urlparse(url)
        except ValueError:
            continue
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not _allowed_cdn_host(host):
            continue
        if url not in out:
            out.append(url)
    return tuple(out)


def _choose_dash_video(videos: Sequence[Dict], want: int) -> Optional[Dict]:
    items = [item for item in videos if isinstance(item, dict) and
             (item.get("baseUrl") or item.get("base_url"))]
    if not items:
        return None
    exact = [item for item in items if int(item.get("id") or 0) == want]
    pool = exact or [item for item in items if int(item.get("id") or 0) <= want] or items
    best_id = max(int(item.get("id") or 0) for item in pool)
    same_quality = [item for item in pool if int(item.get("id") or 0) == best_id]
    # AVC có độ tương thích tốt nhất với Windows/FFmpeg; nếu không có thì dùng
    # đúng thứ tự Bilibili trả về như dự án nguồn.
    return next((item for item in same_quality
                 if str(item.get("codecs") or "").lower().startswith("avc")),
                same_quality[0])


def _pick_stream(play: Dict, want: int) -> StreamChoice:
    durl = play.get("durl") or []
    if durl and isinstance(durl[0], dict) and durl[0].get("url"):
        item = durl[0]
        urls = _collect_urls(item.get("url"), item.get("backup_url"))
        if not urls:
            raise RuntimeError("Bilibili trả URL MP4 không hợp lệ")
        return StreamChoice("mp4", _play_quality(play) or want, urls,
                            declared_size=int(item.get("size") or 0))

    dash = play.get("dash") or {}
    videos = dash.get("video") or [] if isinstance(dash, dict) else []
    audios = dash.get("audio") or [] if isinstance(dash, dict) else []
    video = _choose_dash_video(videos, want)
    audio_items = [item for item in audios if isinstance(item, dict) and
                   (item.get("baseUrl") or item.get("base_url"))]
    audio = max(audio_items, key=lambda item: int(item.get("bandwidth") or 0),
                default=None)
    if not video:
        raise RuntimeError("Không có luồng hình Bilibili phù hợp")
    if not audio:
        raise RuntimeError("Không có luồng tiếng Bilibili phù hợp")
    video_url = video.get("baseUrl") or video.get("base_url")
    audio_url = audio.get("baseUrl") or audio.get("base_url")
    video_urls = _collect_urls(
        video_url, video.get("backupUrl", video.get("backup_url")))
    audio_urls = _collect_urls(
        audio_url, audio.get("backupUrl", audio.get("backup_url")))
    if not video_urls or not audio_urls:
        raise RuntimeError("URL DASH Bilibili không hợp lệ")
    return StreamChoice("dash", int(video.get("id") or _play_quality(play) or want),
                        video_urls, audio_urls)


def _fetch_playurl(bvid: str, cid: str, want: int,
                   cookies_file: Optional[str]) -> StreamChoice:
    headers = _headers(bvid, cookies_file)
    common = {"bvid": bvid, "cid": cid, "qn": want, "fourk": 1}
    html5 = dict(common, fnval=1, platform="html5", high_quality=1)
    dash = dict(common, fnval=16)
    urls = [
        "https://api.bilibili.com/x/player/playurl?" + urlencode(html5),
        "https://api.bilibili.com/x/player/playurl?" + urlencode(dash),
    ]

    def fetch_wbi() -> Dict:
        mixin = _get_wbi_mixin(headers)
        signed = _wbi_query(dict(common, fnval=16, from_client="BROWSER"), mixin)
        return _json_get(
            "https://api.bilibili.com/x/player/wbi/playurl?" + signed,
            headers, 8.0)

    plays: List[Dict] = []
    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="bili-api")
    futures = {pool.submit(_json_get, endpoint, headers, 8.0)
               for endpoint in urls}
    futures.add(pool.submit(fetch_wbi))
    try:
        # Giống mã nguồn mới: không để một endpoint chậm giữ toàn bộ lượt tải.
        # Dừng sớm khi đã có MP4 đúng mức; nếu không, gom kết quả trong 1,8 giây.
        pending = set(futures)
        deadline = time.monotonic() + 1.8
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, pending = wait(
                pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                break
            stop_early = False
            for future in done:
                try:
                    play = future.result()
                    if _valid_play(play):
                        plays.append(play)
                        durl = play.get("durl") or []
                        if (durl and isinstance(durl[0], dict) and
                                durl[0].get("url") and _play_quality(play) >= want):
                            stop_early = True
                except Exception:
                    continue
            if stop_early:
                break
        for future in pending:
            future.cancel()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return _pick_stream(_pick_best_play(plays, want), want)


def _view_info(bvid: str, source_url: str,
               cookies_file: Optional[str]) -> Tuple[str, str, int, int, str]:
    data = _json_get(
        "https://api.bilibili.com/x/web-interface/view?bvid=" + quote(bvid),
        _headers(bvid, cookies_file), timeout=12.0)
    title = html.unescape(re.sub(r"<[^>]+>", "", str(data.get("title") or bvid))).strip()
    pages = data.get("pages") or []
    page_no = 1
    try:
        page_no = max(1, int((parse_qs(urlparse(source_url).query).get("p") or [1])[0]))
    except (TypeError, ValueError):
        page_no = 1
    page = (pages[min(page_no - 1, len(pages) - 1)]
            if pages else {"cid": data.get("cid"), "part": title, "page": 1})
    cid = str(page.get("cid") or data.get("cid") or "")
    if not cid.isdigit():
        raise RuntimeError("Không lấy được cid của video Bilibili")
    part = html.unescape(str(page.get("part") or title)).strip()
    actual_page = int(page.get("page") or page_no)
    return title, cid, actual_page, max(1, len(pages)), part


def _safe_filename(title: str, bvid: str, page: int, pages: int, part: str) -> str:
    page_bit = f" P{page} {part}" if pages > 1 else ""
    raw = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", f"{title}{page_bit} [{bvid}]")
    raw = re.sub(r"\s+", " ", raw).strip(" .")[:140].rstrip(" .")
    return (raw or bvid) + ".mp4"


def _unique_path(out_dir: str, filename: str) -> str:
    candidate = os.path.join(out_dir, filename)
    # Nếu chỉ có .part thì giữ đúng tên để nối tiếp phiên tải trước. Chỉ tạo
    # hậu tố mới khi file hoàn chỉnh đã tồn tại, tránh ghi đè dữ liệu người dùng.
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(filename)
    for index in range(2, 10000):
        candidate = os.path.join(out_dir, f"{stem} ({index}){ext}")
        if not os.path.exists(candidate) and not os.path.exists(candidate + ".part"):
            return candidate
    raise RuntimeError("Thư mục đích có quá nhiều file trùng tên")


def _expand_mirrors(url: str) -> List[str]:
    out = [url]
    try:
        parsed = urlparse(url)
    except ValueError:
        return out
    host = (parsed.hostname or "").lower()
    if not host.endswith("bilivideo.com"):
        return out
    for mirror in _MIRRORS:
        if mirror == host:
            continue
        netloc = mirror + ((":" + str(parsed.port)) if parsed.port else "")
        out.append(urlunparse(parsed._replace(netloc=netloc)))
    return out


def _candidate_urls(urls: Iterable[str]) -> List[str]:
    out: List[str] = []
    for raw in urls:
        for url in _expand_mirrors(raw):
            try:
                parsed = urlparse(url)
            except ValueError:
                continue
            if (parsed.scheme in {"http", "https"} and
                    _allowed_cdn_host(parsed.hostname or "") and
                    url not in out):
                out.append(url)
    return out[:_PROBE_CAP]


def _range_headers(headers: Dict[str, str], start: int, end: int) -> Dict[str, str]:
    out = dict(headers)
    out["Range"] = f"bytes={start}-{end}"
    out["Accept-Encoding"] = "identity"
    return out


def _probe(url: str, headers: Dict[str, str]) -> Probe:
    request = Request(url, headers=_range_headers(headers, 0, 1023))
    with urlopen(request, timeout=8.0) as response:
        status = int(getattr(response, "status", response.getcode()))
        content_type = response.headers.get("Content-Type") or "video/mp4"
        if status == 206:
            content_range = response.headers.get("Content-Range") or ""
            total_text = content_range.rsplit("/", 1)[-1] if "/" in content_range else ""
            total = int(total_text) if total_text.isdigit() else int(
                response.headers.get("Content-Length") or 0)
            response.read(1024)
            return Probe(url, total, True, content_type)
        if status == 200:
            return Probe(url, int(response.headers.get("Content-Length") or 0),
                         False, content_type)
        raise RuntimeError(f"CDN HTTP {status}")


def _race_probe(urls: Sequence[str], headers: Dict[str, str]) -> Probe:
    if not urls:
        raise RuntimeError("Không có địa chỉ CDN Bilibili hợp lệ")
    pool = ThreadPoolExecutor(max_workers=len(urls), thread_name_prefix="bili-probe")
    futures = {pool.submit(_probe, url, headers): url for url in urls}
    errors: List[Exception] = []
    try:
        for future in as_completed(futures):
            try:
                winner = future.result()
                for pending in futures:
                    if pending is not future:
                        pending.cancel()
                return winner
            except Exception as exc:
                errors.append(exc)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError("Không kết nối được CDN Bilibili") from (errors[-1] if errors else None)


def _fetch_range(url: str, headers: Dict[str, str], start: int, end: int) -> bytes:
    request = Request(url, headers=_range_headers(headers, start, end))
    with urlopen(request, timeout=45.0) as response:
        status = int(getattr(response, "status", response.getcode()))
        if status != 206:
            raise RuntimeError(f"CDN không nhận Range (HTTP {status})")
        data = response.read()
    expected = end - start + 1
    if len(data) != expected:
        raise RuntimeError(f"CDN trả thiếu khối: {len(data)}/{expected} byte")
    return data


def _fetch_range_retry(urls: Sequence[str], headers: Dict[str, str],
                       start: int, end: int) -> bytes:
    last: Optional[Exception] = None
    # Thử hai vòng vì CDN đôi khi ngắt đúng một request rồi hoạt động lại.
    for _round in range(2):
        for url in urls:
            try:
                return _fetch_range(url, headers, start, end)
            except Exception as exc:
                last = exc
    raise RuntimeError(f"Không tải được khối {start}-{end} từ các CDN") from last


def _emit_progress(callback: ProgressCallback, label: str, downloaded: int,
                   total: int, started: float, phase_start: float = 0.0,
                   phase_span: float = 100.0) -> None:
    if not callback:
        return
    elapsed = max(0.001, time.monotonic() - started)
    speed = downloaded / elapsed
    fraction = min(1.0, downloaded / total) if total > 0 else 0.0
    percent = phase_start + phase_span * fraction
    eta = ((total - downloaded) / speed) if total > downloaded and speed > 0 else 0.0
    callback({
        "status": "downloading", "percent": percent,
        "downloaded": downloaded, "total": total or None,
        "speed": speed, "eta": eta,
        "text": (f"{label}: {percent:.1f}% · "
                 f"{_human_bytes(downloaded)}/{_human_bytes(total)} · "
                 f"{_human_bytes(speed)}/s · còn {_human_eta(eta)}"),
    })


def _human_bytes(value: float) -> str:
    amount = max(0.0, float(value or 0))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024.0
    return f"{amount:.1f} TiB"


def _human_eta(seconds: float) -> str:
    value = max(0, int(round(seconds or 0)))
    hours, remain = divmod(value, 3600)
    minutes, secs = divmod(remain, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _download_single(url: str, headers: Dict[str, str], part_path: str,
                     total: int, label: str, callback: ProgressCallback,
                     phase_start: float, phase_span: float) -> None:
    request_headers = dict(headers)
    request_headers["Accept-Encoding"] = "identity"
    request = Request(url, headers=request_headers)
    started, downloaded = time.monotonic(), 0
    with urlopen(request, timeout=45.0) as response, open(part_path, "wb") as handle:
        actual_total = total or int(response.headers.get("Content-Length") or 0)
        while True:
            block = response.read(_READ_SIZE)
            if not block:
                break
            handle.write(block)
            downloaded += len(block)
            _emit_progress(callback, label, downloaded, actual_total, started,
                           phase_start, phase_span)
    if total and downloaded != total:
        raise RuntimeError(f"CDN ngắt giữa chừng: {downloaded}/{total} byte")


def _download_ranges(urls: Sequence[str], headers: Dict[str, str], part_path: str,
                     total: int, label: str, callback: ProgressCallback,
                     phase_start: float, phase_span: float) -> None:
    existing = os.path.getsize(part_path) if os.path.isfile(part_path) else 0
    if existing > total:
        existing = 0
    # Chỉ nối từ ranh giới khối hoàn chỉnh; loại phần khối cuối bị đứt.
    aligned = min(total, (existing // _CHUNK) * _CHUNK)
    mode = "r+b" if os.path.exists(part_path) else "wb"
    started = time.monotonic()
    with open(part_path, mode) as handle:
        handle.truncate(aligned)
        handle.seek(aligned)
        downloaded = aligned
        _emit_progress(callback, label, downloaded, total, started,
                       phase_start, phase_span)
        starts = list(range(aligned, total, _CHUNK))
        pool = ThreadPoolExecutor(max_workers=_WINDOW, thread_name_prefix="bili-range")
        try:
            for base in range(0, len(starts), _WINDOW):
                batch = starts[base:base + _WINDOW]
                futures = []
                for start in batch:
                    end = min(total, start + _CHUNK) - 1
                    futures.append((start, pool.submit(
                        _fetch_range_retry, urls, headers, start, end)))
                # Ghi đúng thứ tự file dù các request mạng chạy song song.
                for start, future in futures:
                    block = future.result()
                    handle.seek(start)
                    handle.write(block)
                    downloaded = start + len(block)
                    _emit_progress(callback, label, downloaded, total, started,
                                   phase_start, phase_span)
                handle.flush()
        finally:
            pool.shutdown(wait=True, cancel_futures=True)


def _download_stream(urls: Sequence[str], destination: str, headers: Dict[str, str],
                     label: str, callback: ProgressCallback,
                     phase_start: float, phase_span: float) -> str:
    candidates = _candidate_urls(urls)
    winner = _race_probe(candidates, headers)
    if (os.path.isfile(destination) and os.path.getsize(destination) > 0 and
            (winner.length <= 0 or os.path.getsize(destination) == winner.length)):
        return destination
    ordered = [winner.url] + [url for url in candidates if url != winner.url]
    part_path = destination + ".part"
    if winner.accepts_ranges and winner.length >= _MULTI_MIN:
        _download_ranges(ordered, headers, part_path, winner.length, label,
                         callback, phase_start, phase_span)
    else:
        _download_single(winner.url, headers, part_path, winner.length, label,
                         callback, phase_start, phase_span)
    os.replace(part_path, destination)
    return destination


def download_bilibili(url: str, out_dir: str, quality: str = "best",
                       cookies_file: Optional[str] = None,
                       progress_callback: ProgressCallback = None) -> Tuple[str, int, str]:
    """Tải link Bilibili thành MP4; trả ``(path, qn_thực, kiểu_luồng)``."""
    bvid = extract_bvid(url)
    if not is_bilibili_url(url) or not bvid:
        raise ValueError("Không phải link video Bilibili có BV id hợp lệ")
    os.makedirs(out_dir, exist_ok=True)
    title, cid, page, pages, part = _view_info(bvid, url, cookies_file)
    want = quality_qn(quality)
    stream = _fetch_playurl(bvid, cid, want, cookies_file)
    filename = _safe_filename(title, bvid, page, pages, part)
    destination = _unique_path(out_dir, filename)
    headers = _headers(bvid, cookies_file, accept="*/*")

    if stream.kind == "mp4":
        _download_stream(stream.video_urls, destination, headers,
                         f"Bilibili {bvid} {quality_label(stream.quality)}",
                         progress_callback, 0.0, 97.0)
    else:
        video_temp = destination + ".video.m4s"
        audio_temp = destination + ".audio.m4s"
        _download_stream(stream.video_urls, video_temp, headers,
                         f"Hình {bvid} {quality_label(stream.quality)}",
                         progress_callback, 0.0, 82.0)
        _download_stream(stream.audio_urls, audio_temp, headers,
                         f"Tiếng {bvid}", progress_callback, 82.0, 15.0)
        if progress_callback:
            progress_callback({"status": "merging", "percent": 98.0,
                               "text": "Đang ghép hình và âm thanh Bilibili…"})
        run([
            "ffmpeg", "-y", "-hide_banner", "-nostdin",
            "-i", video_temp, "-i", audio_temp,
            "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
            "-movflags", "+faststart", destination,
        ], check=True, quiet=True)
        for temp_path in (video_temp, audio_temp):
            try:
                os.remove(temp_path)
            except OSError:
                pass

    if not os.path.isfile(destination) or os.path.getsize(destination) <= 0:
        raise RuntimeError("Bộ tải trực tiếp không tạo được file MP4")
    return os.path.abspath(destination), stream.quality, stream.kind
