"""Bộ tải Bilibili trực tiếp, tối ưu cho các video nền công khai.

Luồng xử lý được chuyển thể từ dự án ``zephyr-breeze-sage-crystal``:

* hỏi song song nhiều API playurl (MP4, DASH và WBI), nếu 412 thì lấy
  ``__playinfo__`` / ``__INITIAL_STATE__`` từ trang xem;
* đo 2 MiB trên từng baseUrl/backupUrl và xếp CDN theo throughput;
  nếu đường quốc tế quá chậm thì đo lại mẫu 256 KiB thay vì bỏ cuộc;
* tải file lớn theo các khối Range 2 MiB, tối đa 12 khối song song
  (4 khối khi probe < 512 KiB/s để tránh nghẽn peering VN-TQ);
* từng khối tự đổi CDN khi một máy chủ ngắt hoặc chảy nhỏ giọt;
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
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from .utils import run


CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_BVID_RE = re.compile(r"\b(BV[0-9A-Za-z]{10})\b", re.I)
_MIRRORS = (
    "upos-sz-mirrorcosov.bilivideo.com",
    "upos-sz-mirroraliov.bilivideo.com",
    "upos-sz-mirrorhwov.bilivideo.com",
    "upos-sz-mirrorali.bilivideo.com",
    "upos-sz-mirrorhw.bilivideo.com",
    "upos-sz-mirrorcos.bilivideo.com",
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
_WINDOW = 12
_CHUNK = 2 * 1024 * 1024
_MULTI_MIN = int(1.2 * 1024 * 1024)
# Đường VNPT/nhà mạng VN sang CDN Trung Quốc thường chỉ ~100-400 KiB/s.
# 6 probe 2 MiB cùng lúc sẽ đói băng thông rồi hết hạn, sau đó rơi xuống yt-dlp.
_PROBE_CAP = 2
_READ_SIZE = 256 * 1024
_PROBE_TIMEOUT = 20.0
_PROBE_SAMPLE = 2 * 1024 * 1024
_PROBE_LITE_SAMPLE = 256 * 1024
_MIN_SPEED = 128 * 1024
_SPEED_WINDOW = 2.0
_RANGE_TIMEOUT = 20.0
_RANGE_DEADLINE = 12.0
_WATCH_PAGE_TTL = 60.0
_OVERSEAS_CDN_HINTS = ("akamaized", "akam", "cosov", "aliov", "hwov")
_DOWNLOAD_PROXY = ""


def _lsid() -> str:
    return f"{int(time.time() * 1000):X}{uuid.uuid4().hex[:8].upper()}"


def _uuid_cookie() -> str:
    return f"{uuid.uuid4()}{int(time.time() * 1000)}infoc"


def _buvid4_from(buvid3: str) -> str:
    digest = hashlib.md5(str(buvid3).encode("utf-8")).hexdigest()
    return (f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-"
            f"{digest[16:20]}-{digest[20:32]}infoc")


_BUVID3 = str(uuid.uuid4()).upper() + "infoc"
_BUVID4 = _buvid4_from(_BUVID3)
_UUID = _uuid_cookie()
_B_LSID = _lsid()
_SESSION_COOKIES: Dict[str, str] = {}
_SESSION_WARMED = False
_WATCH_PAGE_CACHE: Dict[str, Tuple[str, float]] = {}
_WBI_CACHE: Tuple[str, float] = ("", 0.0)


def reset_http_session() -> None:
    """Xoá cookie phiên / cache trang — dùng cho unit test."""
    global _SESSION_COOKIES, _SESSION_WARMED, _WATCH_PAGE_CACHE, _WBI_CACHE
    _SESSION_COOKIES = {}
    _SESSION_WARMED = False
    _WATCH_PAGE_CACHE = {}
    _WBI_CACHE = ("", 0.0)


def set_download_proxy(proxy: Optional[str]) -> str:
    """Gắn HTTP/SOCKS proxy cho mọi request Bilibili; trả proxy trước đó."""
    global _DOWNLOAD_PROXY
    previous = _DOWNLOAD_PROXY
    _DOWNLOAD_PROXY = str(proxy or "").strip()
    return previous


def _open_url(request, timeout: float):
    """urlopen có proxy (Clash/V2Ray HTTP) để né peering VNPT-Trung Quốc."""
    proxy = _DOWNLOAD_PROXY
    if not proxy:
        return urlopen(request, timeout=timeout)
    parsed = urlparse(proxy)
    scheme = (parsed.scheme or "http").lower()
    if scheme in {"http", "https"}:
        opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
        return opener.open(request, timeout=timeout)
    if scheme.startswith("socks"):
        host = parsed.hostname
        port = parsed.port or (1080 if "5" in scheme else 1080)
        if not host:
            raise RuntimeError("Proxy SOCKS thiếu hostname")
        try:
            import socks
            from sockshandler import SocksiPyHandler
        except ImportError as exc:
            raise RuntimeError(
                "Proxy SOCKS cần PySocks. Chạy: python -m pip install PySocks "
                "hoặc dùng proxy HTTP (Clash cổng mixed, ví dụ http://127.0.0.1:7890)"
            ) from exc
        socks_type = socks.SOCKS5 if "5" in scheme else socks.SOCKS4
        rdns = scheme.endswith("h") or scheme.endswith("5h")
        opener = build_opener(SocksiPyHandler(
            socks_type, host, port, rdns,
            parsed.username, parsed.password))
        return opener.open(request, timeout=timeout)
    raise RuntimeError("Chỉ hỗ trợ proxy http://, https:// hoặc socks5://")


ProgressCallback = Optional[Callable[[Dict], None]]


@dataclass(frozen=True)
class StreamChoice:
    kind: str
    quality: int
    video_urls: Tuple[str, ...]
    audio_urls: Tuple[str, ...] = ()
    declared_size: int = 0
    video_checksums: Tuple[Tuple[str, str], ...] = ()
    audio_checksums: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Probe:
    url: str
    length: int
    accepts_ranges: bool
    content_type: str
    speed: float = 0.0
    sample_hash: str = ""
    etag: str = ""


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
        "buvid4": _BUVID4,
        "b_nut": str(int(time.time())),
        "_uuid": _UUID,
        "b_lsid": _B_LSID,
    }
    cookie_values.update(_SESSION_COOKIES)
    cookie_values.update(_read_cookie_file(cookies_file))
    referer = (f"https://www.bilibili.com/video/{bvid}" if bvid
               else "https://www.bilibili.com")
    return {
        "User-Agent": CHROME_UA,
        "Referer": referer,
        "Origin": "https://www.bilibili.com",
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,vi;q=0.7",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "Cookie": "; ".join(f"{key}={value}" for key, value in cookie_values.items()
                            if key and value),
    }


def _merge_set_cookie(response) -> None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return
    getter = getattr(headers, "get_all", None)
    raw_items = getter("Set-Cookie") if callable(getter) else [headers.get("Set-Cookie") or ""]
    for item in raw_items or []:
        name_val = str(item or "").split(";", 1)[0]
        if "=" not in name_val:
            continue
        name, value = name_val.split("=", 1)
        name = name.strip()
        if name:
            _SESSION_COOKIES[name] = value.strip()


def _overlay_session(headers: Dict[str, str]) -> Dict[str, str]:
    out = dict(headers)
    cookies: Dict[str, str] = {}
    for part in str(out.get("Cookie") or "").split(";"):
        if "=" in part:
            name, value = part.split("=", 1)
            cookies[name.strip()] = value.strip()
    cookies.update(_SESSION_COOKIES)
    out["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items()
                              if key and value)
    return out


def _request_text(url: str, headers: Dict[str, str], timeout: float) -> str:
    request = Request(url, headers=_overlay_session(headers))
    try:
        with _open_url(request, timeout) as response:
            _merge_set_cookie(response)
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        _merge_set_cookie(exc)
        body = b""
        try:
            body = exc.read() or b""
        except Exception:
            body = b""
        text = body.decode("utf-8", errors="replace")
        if exc.code in (412, 403, 429) and text.lstrip().startswith(("{", "[")):
            return text
        raise RuntimeError(str(exc)) from exc


def _warm_session(headers: Dict[str, str], force: bool = False) -> None:
    """Lấy buvid/spi từ trang chủ trước khi gọi API — giảm HTTP 412."""
    global _SESSION_WARMED
    if _SESSION_WARMED and not force:
        return
    _SESSION_WARMED = True
    html_headers = dict(headers)
    html_headers["Accept"] = (
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    html_headers["Sec-Fetch-Dest"] = "document"
    html_headers["Sec-Fetch-Mode"] = "navigate"
    html_headers["Sec-Fetch-Site"] = "none"
    try:
        _request_text("https://www.bilibili.com/", html_headers, 4.0)
    except Exception:
        pass
    try:
        raw = _request_text(
            "https://api.bilibili.com/x/frontend/finger/spi", headers, 4.0)
        payload = json.loads(raw)
        data = payload.get("data") or payload
        if isinstance(data, dict):
            if data.get("b_3"):
                _SESSION_COOKIES["buvid3"] = str(data["b_3"])
            if data.get("b_4"):
                _SESSION_COOKIES["buvid4"] = str(data["b_4"])
    except Exception:
        pass


def _decode_api_json(raw: str, allow_codes: Sequence[int] = ()) -> Dict:
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


def _json_get(url: str, headers: Dict[str, str], timeout: float = 20.0,
              allow_codes: Sequence[int] = ()) -> Dict:
    last_error: Optional[BaseException] = None
    for attempt in range(2):
        try:
            _warm_session(headers, force=attempt > 0)
            return _decode_api_json(_request_text(url, headers, timeout),
                                    allow_codes)
        except Exception as exc:
            last_error = exc
            message = str(exc).lower()
            retryable = ("http error 412" in message or
                         "precondition failed" in message or
                         "http error 403" in message)
            if not retryable or attempt:
                raise
            time.sleep(0.35)
    raise RuntimeError(str(last_error or "API Bilibili không phản hồi"))


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


def _cdn_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _is_overseas_cdn(url: str) -> bool:
    host = _cdn_host(url)
    return any(hint in host for hint in _OVERSEAS_CDN_HINTS)


def _play_media_urls(play: Dict) -> Tuple[str, ...]:
    durl = play.get("durl") or []
    if durl and isinstance(durl[0], dict) and durl[0].get("url"):
        return _stream_urls(durl[0])
    dash = play.get("dash") or {}
    if not isinstance(dash, dict):
        return ()
    urls: List[str] = []
    for group in (dash.get("video") or [])[:1], (dash.get("audio") or [])[:1]:
        for item in group:
            if isinstance(item, dict):
                urls.extend(_stream_urls(item))
    return tuple(urls)


def _play_is_overseas_only(play: Dict) -> bool:
    urls = _play_media_urls(play)
    return bool(urls) and all(_is_overseas_cdn(url) for url in urls)


def _should_stop_playurl(play: Dict, want: int) -> bool:
    """MP4 đủ chất lượng chỉ dừng sớm khi đã có CDN nội địa/nhiều mirror."""
    durl = play.get("durl") or []
    if not (durl and isinstance(durl[0], dict) and durl[0].get("url")):
        return False
    if _play_quality(play) < want:
        return False
    return not _play_is_overseas_only(play)


def _pick_best_play(plays: Sequence[Dict], want: int) -> Dict:
    if not plays:
        raise RuntimeError("Không lấy được địa chỉ phát từ Bilibili")
    def score(play: Dict) -> int:
        quality = _play_quality(play)
        durl = play.get("durl") or []
        has_mp4 = bool(durl and isinstance(durl[0], dict) and durl[0].get("url"))
        urls = _play_media_urls(play)
        hosts = {_cdn_host(url) for url in urls if _cdn_host(url)}
        hit = 2000 if quality >= want else 0
        # HTML5 MP4 overseas thường chỉ một host Akamai; đừng khóa DASH/backup.
        mp4_bonus = (250 if has_mp4 and quality >= min(want, 64)
                     and not _play_is_overseas_only(play) else 0)
        cdn_bonus = min(120, 30 * max(0, len(hosts) - 1))
        return hit + mp4_bonus + cdn_bonus + quality
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


def _stream_urls(item: Dict) -> Tuple[str, ...]:
    values = [item.get(key) for key in ("url", "baseUrl", "base_url")]
    for key in ("backupUrl", "backup_url"):
        value = item.get(key)
        values.extend(value if isinstance(value, (list, tuple)) else [value])
    return _collect_urls(None, values)


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


def _source_checksums(item: Dict) -> Tuple[Tuple[str, str], ...]:
    """Only explicit per-representation digests; never infer one from an ETag/URL."""
    checksums = []
    for algorithm, width in (("sha256", 64), ("sha1", 40), ("md5", 32)):
        value = item.get(algorithm)
        if value is None or value == "":
            continue
        if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-fA-F]{{{width}}}", value):
            raise ValueError(f"Bilibili trả checksum {algorithm} không hợp lệ")
        checksums.append((algorithm, value.lower()))
    return tuple(checksums)


def _pick_stream(play: Dict, want: int) -> StreamChoice:
    durl = play.get("durl") or []
    if durl and isinstance(durl[0], dict) and durl[0].get("url"):
        item = durl[0]
        urls = _stream_urls(item)
        if not urls:
            raise RuntimeError("Bilibili trả URL MP4 không hợp lệ")
        return StreamChoice("mp4", _play_quality(play) or want, urls,
                            declared_size=int(item.get("size") or 0),
                            video_checksums=_source_checksums(item))

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
    video_urls = _stream_urls(video)
    audio_urls = _stream_urls(audio)
    if not video_urls or not audio_urls:
        raise RuntimeError("URL DASH Bilibili không hợp lệ")
    return StreamChoice("dash", int(video.get("id") or _play_quality(play) or want),
                        video_urls, audio_urls,
                        video_checksums=_source_checksums(video),
                        audio_checksums=_source_checksums(audio))


def _extract_js_assign(source: str, name: str) -> Optional[Dict]:
    """Lấy object JSON gán vào ``window.NAME = {...}`` trên trang xem."""
    text = str(source or "")
    idx = text.find(f"window.{name}")
    if idx < 0:
        idx = text.find(f"{name}=")
    if idx < 0:
        return None
    start = text.find("{", idx)
    if start < 0 or start - idx > 80:
        return None
    depth = 0
    in_str = None
    escape = False
    limit = min(len(text), start + 2_000_000)
    for pos in range(start, limit):
        ch = text[pos]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(text[start:pos + 1])
                except json.JSONDecodeError:
                    return None
                return payload if isinstance(payload, dict) else None
    return None


def _load_watch_page(bvid: str, headers: Dict[str, str]) -> str:
    cached = _WATCH_PAGE_CACHE.get(bvid)
    if cached and cached[1] > time.time():
        return cached[0]
    html_headers = dict(headers)
    html_headers["Accept"] = (
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    html_headers["Sec-Fetch-Dest"] = "document"
    html_headers["Sec-Fetch-Mode"] = "navigate"
    html_headers["Sec-Fetch-Site"] = "none"
    raw = _request_text(
        f"https://www.bilibili.com/video/{bvid}/", html_headers, 15.0)
    if len(raw) < 200:
        raise RuntimeError("Trang Bilibili trả về rỗng")
    _WATCH_PAGE_CACHE[bvid] = (raw, time.time() + _WATCH_PAGE_TTL)
    return raw


def _play_from_watch_page(bvid: str, want: int,
                          headers: Dict[str, str]) -> Optional[Dict]:
    page = _load_watch_page(bvid, headers)
    playinfo = _extract_js_assign(page, "__playinfo__") or {}
    play = playinfo.get("data") if isinstance(playinfo.get("data"), dict) else playinfo
    if isinstance(play, dict) and _valid_play(play):
        return play
    return None


def _page_no_from_url(source_url: str) -> int:
    try:
        return max(1, int((parse_qs(urlparse(source_url).query).get("p") or [1])[0]))
    except (TypeError, ValueError):
        return 1


def _pick_page(pages: Sequence, source_url: str, title: str,
               fallback_cid) -> Tuple[str, str, int, int, str]:
    page_no = _page_no_from_url(source_url)
    page_list = [item for item in pages if isinstance(item, dict)] if pages else []
    page = (page_list[min(page_no - 1, len(page_list) - 1)]
            if page_list else {"cid": fallback_cid, "part": title, "page": 1})
    cid = str(page.get("cid") or fallback_cid or "")
    if not cid.isdigit():
        raise RuntimeError("Không lấy được cid của video Bilibili")
    part = html.unescape(str(page.get("part") or title)).strip()
    actual_page = int(page.get("page") or page_no)
    return title, cid, actual_page, max(1, len(page_list) or 1), part


def _fields_from_view(data: Dict, source_url: str, fallback_title: str
                      ) -> Tuple[str, str, int, int, str]:
    video: Dict = data
    for key in ("videoData", "videoInfo", "View"):
        nested = data.get(key)
        if isinstance(nested, dict):
            video = nested
            break
    title = html.unescape(re.sub(
        r"<[^>]+>", "", str(video.get("title") or data.get("title") or fallback_title)
    )).strip() or fallback_title
    pages = video.get("pages") or data.get("pages") or []
    if not isinstance(pages, list):
        pages = []
    return _pick_page(pages, source_url, title, video.get("cid") or data.get("cid"))


def _fetch_playurl(bvid: str, cid: str, want: int,
                   cookies_file: Optional[str]) -> StreamChoice:
    headers = _headers(bvid, cookies_file)
    common = {"bvid": bvid, "cid": cid, "qn": want, "fourk": 1}
    html5 = dict(common, fnval=1, platform="html5", high_quality=1)
    dash = dict(common, fnval=16)
    dash_full = dict(common, fnval=4048)
    urls = [
        "https://api.bilibili.com/x/player/playurl?" + urlencode(html5),
        "https://api.bilibili.com/x/player/playurl?" + urlencode(dash),
        "https://api.bilibili.com/x/player/playurl?" + urlencode(dash_full),
    ]

    def fetch_wbi() -> Dict:
        mixin = _get_wbi_mixin(headers)
        signed = _wbi_query(dict(common, fnval=4048, from_client="BROWSER"), mixin)
        return _json_get(
            "https://api.bilibili.com/x/player/wbi/playurl?" + signed,
            headers, 8.0)

    plays: List[Dict] = []
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bili-api")
    futures = {pool.submit(_json_get, endpoint, headers, 8.0)
               for endpoint in urls}
    futures.add(pool.submit(fetch_wbi))
    try:
        # Không để một endpoint chậm giữ toàn bộ lượt tải. 412/retry nằm trong
        # ``_json_get``; nếu API vẫn trống thì lấy __playinfo__ từ trang xem.
        pending = set(futures)
        deadline = time.monotonic() + 2.5
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
                        if _should_stop_playurl(play, want):
                            stop_early = True
                        elif _play_is_overseas_only(play):
                            deadline = max(deadline, time.monotonic() + 2.0)
                except Exception:
                    continue
            if stop_early:
                break
        for future in pending:
            future.cancel()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if not plays:
        embedded = _play_from_watch_page(bvid, want, headers)
        if embedded:
            plays.append(embedded)
    return _pick_stream(_pick_best_play(plays, want), want)


def _view_info(bvid: str, source_url: str,
               cookies_file: Optional[str]) -> Tuple[str, str, int, int, str]:
    headers = _headers(bvid, cookies_file)
    errors: List[BaseException] = []

    def _try(load) -> Optional[Tuple[str, str, int, int, str]]:
        try:
            return load()
        except Exception as exc:
            errors.append(exc)
            return None

    result = _try(lambda: _fields_from_view(
        _json_get("https://api.bilibili.com/x/web-interface/view?bvid=" + quote(bvid),
                  headers, timeout=12.0),
        source_url, bvid))
    if result:
        return result

    def _wbi_view() -> Tuple[str, str, int, int, str]:
        mixin = _get_wbi_mixin(headers)
        signed = _wbi_query({"bvid": bvid}, mixin)
        return _fields_from_view(
            _json_get("https://api.bilibili.com/x/web-interface/wbi/view?" + signed,
                      headers, timeout=12.0),
            source_url, bvid)

    result = _try(_wbi_view)
    if result:
        return result

    def _html_view() -> Tuple[str, str, int, int, str]:
        page = _load_watch_page(bvid, headers)
        state = _extract_js_assign(page, "__INITIAL_STATE__") or {}
        return _fields_from_view(state, source_url, bvid)

    result = _try(_html_view)
    if result:
        return result

    def _pagelist() -> Tuple[str, str, int, int, str]:
        payload = _json_get(
            "https://api.bilibili.com/x/player/pagelist?bvid=" + quote(bvid),
            headers, timeout=12.0)
        pages = payload.get("result") if isinstance(payload.get("result"), list) else []
        return _pick_page(pages, source_url, bvid,
                          pages[0].get("cid") if pages else None)

    result = _try(_pagelist)
    if result:
        return result
    raise RuntimeError(str(errors[0]) if errors else
                       "Không lấy được thông tin video Bilibili")


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


def _rewritable_cdn_host(host: str) -> bool:
    host = str(host or "").lower()
    return "akamaized" in host or host.startswith("upos-")


def _expand_mirrors(url: str) -> List[str]:
    out = [url]
    try:
        parsed = urlparse(url)
    except ValueError:
        return out
    host = (parsed.hostname or "").lower()
    # Token/path Bilibili thường dùng được trên nhiều upos. URL Akamai đổi
    # hostname sang COS/Ali/HW rồi đo tốc độ; probe 403 thì bỏ, không trộn hash lệch.
    if not _allowed_cdn_host(host) or not _rewritable_cdn_host(host):
        return out
    for mirror in _MIRRORS:
        if mirror == host:
            continue
        netloc = mirror + ((":" + str(parsed.port)) if parsed.port else "")
        candidate = urlunparse(parsed._replace(netloc=netloc))
        if candidate not in out:
            out.append(candidate)
    return out


def _candidate_urls(urls: Iterable[str]) -> List[str]:
    # Keep every API/backup URL, then add same-path upos mirrors for real CDNs.
    collected = list(_collect_urls(None, list(urls)))
    out: List[str] = []
    seen = set()
    for url in collected:
        for candidate in _expand_mirrors(url):
            if candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
    return out


def _range_windows(speed: float) -> Tuple[int, ...]:
    """Match parallel Range count to measured CDN speed.

    ~0.8 MiB/s Akamai with 12 connections starves each socket; the CDN then
    returns short bodies and we fall back to 256 KiB pieces.
    """
    bps = float(speed or 0)
    if bps < 256 * 1024:
        return (2, 1)
    if bps < 1024 * 1024:
        return (4, 1)
    if bps < 3 * 1024 * 1024:
        return (6, 2, 1)
    return (_WINDOW, 4, 1)


def _range_headers(headers: Dict[str, str], start: int, end: int) -> Dict[str, str]:
    out = dict(headers)
    out["Range"] = f"bytes={start}-{end}"
    out["Accept-Encoding"] = "identity"
    return out


def _content_range(response, start, end=None, total=None):
    value = response.headers.get("Content-Range", "")
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", value)
    if not match:
        raise RuntimeError("CDN trả Content-Range không hợp lệ")
    lo, hi, size = map(int, match.groups())
    if lo != start or hi < lo or hi >= size or (end is not None and hi != end) or (total is not None and size != total):
        raise RuntimeError("CDN trả sai offset/kích thước Content-Range")
    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
        raise RuntimeError("CDN nén dữ liệu Range")
    length = response.headers.get("Content-Length")
    if length is not None and int(length) != hi - lo + 1:
        raise RuntimeError("CDN trả Content-Length không khớp Range")
    return hi, size


def _read_measured(response, expected, deadline, min_speed=0):
    chunks, got = [], 0
    window_start, window_bytes = time.monotonic(), 0
    read = getattr(response, "read1", response.read)
    while got < expected:
        block = read(min(_READ_SIZE, expected - got))
        now = time.monotonic()
        if now > deadline:
            raise RuntimeError("CDN quá chậm, đổi máy chủ")
        if not block:
            break
        chunks.append(block)
        got += len(block)
        window_bytes += len(block)
        if now - window_start >= _SPEED_WINDOW:
            if min_speed and window_bytes / (now - window_start) < min_speed:
                raise RuntimeError("CDN quá chậm, đổi máy chủ")
            window_start, window_bytes = now, 0
    return b"".join(chunks)


def _probe_read_deadline(sample_size: int) -> float:
    """Cho đường quốc tế chậm hoàn thành mẫu probe thay vì bỏ cuộc sau 12 giây."""
    return max(_RANGE_DEADLINE, min(60.0, float(sample_size) / (40 * 1024) + 8.0))


def _cdn_sort_key(probe: Probe):
    # Faster first. On a tie prefer inland: Akamai from VN often bursts on
    # the 2 MiB probe then drips during the full file.
    overseas = 1 if _is_overseas_cdn(probe.url) else 0
    return (-float(probe.speed or 0), overseas)


def _probe(url: str, headers: Dict[str, str], sample: Optional[int] = None) -> Probe:
    sample_size = int(sample or _PROBE_SAMPLE)
    started = time.monotonic()
    request = Request(url, headers=_range_headers(headers, 0, sample_size - 1))
    with _open_url(request, _PROBE_TIMEOUT) as response:
        status = int(response.status)
        content_type = response.headers.get("Content-Type", "video/mp4")
        if 'text/' in content_type or 'json' in content_type:
            raise RuntimeError("CDN trả trang lỗi thay vì media")
        if status == 206:
            end, total = _content_range(response, 0)
            if end != min(sample_size, total) - 1:
                raise RuntimeError("CDN trả sai độ dài mẫu probe")
            expected = end + 1
        elif status == 200:
            total = int(response.headers.get("Content-Length") or 0)
            expected = min(sample_size, total) if total else sample_size
        else:
            raise RuntimeError(f"CDN HTTP {status}")
        data = _read_measured(response, expected, started + _probe_read_deadline(sample_size))
        if not data or (len(data) != expected and (total or status == 206)):
            raise RuntimeError("CDN trả thiếu mẫu probe")
        etag = response.headers.get("ETag", "")
        return Probe(url, total, status == 206, content_type,
                     len(data) / max(.001, time.monotonic() - started),
                     hashlib.sha256(data).hexdigest(),
                     etag if etag.startswith('"') else "")


def _collect_probes(urls, headers, callback, sample: Optional[int] = None):
    ok, errors = [], []
    workers = min(_PROBE_CAP, len(urls))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bili-probe") as pool:
        if sample is None:
            futures = {pool.submit(_probe, url, headers): url for url in urls}
        else:
            futures = {pool.submit(_probe, url, headers, sample): url for url in urls}
        for future in as_completed(futures):
            try:
                probe = future.result()
                ok.append(probe)
                if callback:
                    callback({"status": "downloading", "cdn": urlparse(probe.url).hostname,
                              "probe_speed": probe.speed,
                              "text": f"CDN {urlparse(probe.url).hostname}: {_human_bytes(probe.speed)}/s"})
            except Exception as exc:
                errors.append(exc)
    return ok, errors


def _rank_probes(urls, headers, callback=None):
    if not urls:
        raise RuntimeError("Không có địa chỉ CDN Bilibili hợp lệ")
    # Limit concurrent benchmarks but schedule ALL supplied candidates.
    ok, errors = _collect_probes(urls, headers, callback)
    if not ok:
        if callback:
            callback({"status": "downloading", "event": "probe_lite",
                      "text": "CDN quốc tế chậm khi đo 2 MiB; thử mẫu 256 KiB rồi tải thật…"})
        ok, errors = _collect_probes(urls, headers, callback, _PROBE_LITE_SAMPLE)
    if not ok:
        detail = _range_error_detail(errors[-1]) if errors else ""
        message = "Không kết nối được CDN Bilibili"
        if detail:
            message += f" ({detail})"
        raise RuntimeError(message) from (errors[-1] if errors else None)
    return sorted(ok, key=_cdn_sort_key)


def _race_probe(urls: Sequence[str], headers: Dict[str, str]) -> Probe:
    return _rank_probes(urls, headers)[0]


class RangeDownloadError(RuntimeError):
    """All bounded attempts for one range failed; verified prefixes can resume."""


class ShortRangeError(RuntimeError):
    """A valid Range response ended before the requested bytes arrived."""


def _range_error_detail(error):
    message = re.sub(r"https?://\S+", "[CDN URL]", str(error or ""))
    return f"{type(error).__name__}: {message[:180]}"


class _CDNPool:
    def __init__(self, probes, callback=None):
        self.probes = probes
        self.scores = {p.url: p.speed for p in probes}
        self.cooldown = {}
        self.lock = threading.Lock()
        self.callback = callback
        self.identity = f"{probes[0].length}:{probes[0].sample_hash}"

    def ranked(self):
        with self.lock:
            now = time.monotonic()
            return sorted(self.probes, key=lambda p: (
                self.cooldown.get(p.url, 0) > now, -self.scores[p.url]))

    def failed(self, probe, error=None):
        with self.lock:
            self.cooldown[probe.url] = time.monotonic() + 30
        if self.callback:
            action = ("thử CDN khác cho khối đang tải" if len(self.probes) > 1 else
                      "chỉ có một CDN; thử lại khối đang tải")
            self.callback({"status": "downloading", "cdn": urlparse(probe.url).hostname,
                           "event": "cdn_retry", "error_type": type(error).__name__,
                           "text": f"CDN {urlparse(probe.url).hostname} chậm/lỗi ({_range_error_detail(error)}); {action}"})

    def succeeded(self, probe, speed):
        with self.lock:
            self.scores[probe.url] = .5 * self.scores[probe.url] + .5 * speed


def _fetch_range(url: str, headers: Dict[str, str], start: int, end: int,
                 total=None, etag="", min_speed=None) -> bytes:
    expected = end - start + 1
    request_headers = _range_headers(headers, start, end)
    if etag:
        request_headers["If-Range"] = etag
    request = Request(url, headers=request_headers)
    min_speed = _MIN_SPEED if min_speed is None else min_speed
    deadline = time.monotonic() + max(_RANGE_DEADLINE,
        min(120.0, expected / max(1, min_speed) * 1.5))
    with _open_url(request, _RANGE_TIMEOUT) as response:
        if int(response.status) != 206:
            raise RuntimeError(f"CDN không nhận Range (HTTP {response.status})")
        _content_range(response, start, end, total)
        if etag and response.headers.get("ETag") != etag:
            raise RuntimeError("CDN thay đổi nội dung giữa lúc tải")
        data = _read_measured(response, expected, deadline, min_speed)
    if len(data) != expected:
        raise ShortRangeError(f"CDN trả thiếu khối: {len(data)}/{expected} byte")
    return data


def _fetch_range_retry(urls, headers: Dict[str, str], start: int, end: int) -> bytes:
    last = None
    for _round in range(2):
        candidates = urls.ranked() if isinstance(urls, _CDNPool) else urls
        for item in candidates:
            started = time.monotonic()
            try:
                if isinstance(item, Probe):
                    # Probe uses one connection; divide its threshold across the
                    # parallel window rather than falsely condemning all 12 flows.
                    floor = max(_MIN_SPEED / _WINDOW, item.speed / _WINDOW * .2)
                    # With no alternative CDN, aborting a slow but progressing
                    # transfer cannot improve throughput. Keep socket/deadline
                    # limits, but do not apply the CDN-switch speed threshold.
                    if len(urls.probes) == 1:
                        floor = 0
                    try:
                        data = _fetch_range(item.url, headers, start, end,
                                            item.length, item.etag, min_speed=floor)
                    except ShortRangeError:
                        # A CDN may repeatedly truncate a cached large Range.
                        # Request distinct smaller ranges, each validated fully.
                        # Never append the unverified short response itself.
                        step = 256 * 1024
                        if end - start + 1 <= step:
                            raise
                        if urls.callback:
                            urls.callback({"status": "downloading", "event": "range_split",
                                           "text": f"CDN trả thiếu khối {start}-{end}; thử các đoạn 256 KiB"})
                        data = b"".join(_fetch_range(item.url, headers, offset,
                            min(end, offset + step - 1), item.length, item.etag,
                            min_speed=floor) for offset in range(start, end + 1, step))
                    urls.succeeded(item, len(data) / max(.001, time.monotonic() - started))
                else:
                    data = _fetch_range(item, headers, start, end)
                return data
            except Exception as exc:
                last = exc
                if isinstance(urls, _CDNPool):
                    urls.failed(item, exc)
    raise RangeDownloadError(f"Không tải được khối {start}-{end} từ các CDN ({_range_error_detail(last)})") from last


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


def _file_hash(path, algorithm="sha256"):
    digest = hashlib.new(algorithm)
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_source_checksums(path, checksums):
    for algorithm, expected in checksums:
        if _file_hash(path, algorithm) != expected:
            raise RuntimeError(f"Checksum nguồn Bilibili {algorithm} không khớp; không công nhận tải xong")
    return {"status": "verified" if checksums else "unavailable",
            "source": "bilibili_playurl" if checksums else None,
            "checksums": dict(checksums)}


def _save_integrity(path, data):
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(temp, path)


def _resume_records(path, total, identity):
    try:
        with open(path + ".json", encoding="utf-8") as handle:
            meta = json.load(handle)
        if meta.get("total") != total or meta.get("identity") != identity:
            return []
        records, offset = [], 0
        with open(path, "rb") as handle:
            for rec in meta["chunks"]:
                length = min(_CHUNK, total - offset)
                if not length or rec["start"] != offset or rec["length"] != length:
                    break
                block = handle.read(length)
                if len(block) != length or hashlib.sha256(block).hexdigest() != rec["sha256"]:
                    break
                records.append(rec)
                offset += length
        return records
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _download_single(url: str, headers: Dict[str, str], part_path: str,
                     total: int, label: str, callback: ProgressCallback,
                     phase_start: float, phase_span: float) -> str:
    request_headers = dict(headers)
    request_headers["Accept-Encoding"] = "identity"
    request = Request(url, headers=request_headers)
    started, downloaded, digest = time.monotonic(), 0, hashlib.sha256()
    with _open_url(request, 45.0) as response, open(part_path, "wb") as handle:
        if int(response.status) != 200:
            raise RuntimeError("CDN không trả file đầy đủ")
        actual_total = int(response.headers.get("Content-Length") or 0)
        if total and actual_total and total != actual_total:
            raise RuntimeError("CDN thay đổi kích thước file")
        actual_total = total or actual_total
        while True:
            block = response.read(_READ_SIZE)
            if not block:
                break
            handle.write(block)
            digest.update(block)
            downloaded += len(block)
            _emit_progress(callback, label, downloaded, actual_total, started,
                           phase_start, phase_span)
    if not downloaded or (actual_total and downloaded != actual_total):
        raise RuntimeError(f"CDN ngắt giữa chừng: {downloaded}/{actual_total} byte")
    return digest.hexdigest()


def _download_ranges(urls, headers: Dict[str, str], part_path: str,
                     total: int, label: str, callback: ProgressCallback,
                     phase_start: float, phase_span: float, window=None) -> str:
    window = _WINDOW if window is None else window
    identity = (urls.identity if isinstance(urls, _CDNPool) else
                hashlib.sha256(str(list(urls)).encode()).hexdigest())
    records = _resume_records(part_path, total, identity)
    aligned = sum(rec["length"] for rec in records)
    meta = {"identity": identity, "total": total, "chunks": records}
    mode = "r+b" if os.path.exists(part_path) else "wb"
    started = time.monotonic()
    digest = hashlib.sha256()
    with open(part_path, mode) as handle:
        handle.truncate(aligned)
        handle.seek(0)
        for rec in records:
            digest.update(handle.read(rec["length"]))
        handle.seek(aligned)
        _save_integrity(part_path + ".json", meta)
        _emit_progress(callback, label, aligned, total, started, phase_start, phase_span)
        starts = range(aligned, total, _CHUNK)
        with ThreadPoolExecutor(max_workers=window, thread_name_prefix="bili-range") as pool:
            for base in range(0, len(starts), window):
                batch = starts[base:base + window]
                futures = [(start, pool.submit(_fetch_range_retry, urls, headers,
                            start, min(total, start + _CHUNK) - 1)) for start in batch]
                for start, future in futures:
                    block = future.result()
                    expected = min(_CHUNK, total - start)
                    if len(block) != expected:
                        raise RuntimeError("Khối tải về không đủ kích thước")
                    handle.seek(start)
                    handle.write(block)
                    digest.update(block)
                    records.append({"start": start, "length": len(block),
                                    "sha256": hashlib.sha256(block).hexdigest()})
                    handle.flush()
                    _save_integrity(part_path + ".json", meta)
                    _emit_progress(callback, label, start + len(block), total,
                                   started, phase_start, phase_span)
    return digest.hexdigest()


def _download_stream(urls: Sequence[str], destination: str, headers: Dict[str, str],
                     label: str, callback: ProgressCallback,
                     phase_start: float, phase_span: float,
                     source_checksums=()) -> str:
    probes = _rank_probes(_candidate_urls(urls), headers, callback)
    winner = probes[0]
    identity = f"{winner.length}:{winner.sample_hash}"
    if callback:
        host = urlparse(winner.url).hostname or ""
        hint = ""
        if (winner.speed and winner.speed < 1024 * 1024 and _is_overseas_cdn(winner.url)
                and not _DOWNLOAD_PROXY):
            hint = (" · CDN quốc tế chậm; nếu Clash/V2Ray đang mở hãy điền "
                    "download.proxy rồi tải lại")
        callback({"status": "downloading", "percent": phase_start,
                  "text": f"{label} · chọn CDN {host} ({_human_bytes(winner.speed)}/s){hint}"})
    try:
        with open(destination + ".integrity.json", encoding="utf-8") as handle:
            cached = json.load(handle)
        if (cached["identity"] == identity and os.path.getsize(destination) == cached["size"]
                and _file_hash(destination) == cached["sha256"]):
            cached["source_verification"] = _verify_source_checksums(destination, source_checksums)
            _save_integrity(destination + ".integrity.json", cached)
            return destination
    except (OSError, ValueError, KeyError, TypeError):
        pass
    part_path = destination + ".part"
    if winner.accepts_ranges and winner.length >= _MULTI_MIN:
        # Only mix range segments with the same size and measured prefix digest.
        compatible = [p for p in probes if p.accepts_ranges and p.length == winner.length
                      and p.sample_hash == winner.sample_hash]
        pool = _CDNPool(compatible, callback)
        windows = _range_windows(winner.speed)
        for index, window in enumerate(windows):
            try:
                expected_hash = _download_ranges(pool, headers, part_path, winner.length,
                                                 label, callback, phase_start, phase_span,
                                                 window=window)
                break
            except RangeDownloadError:
                if index + 1 >= len(windows):
                    raise
                if callback:
                    nxt = windows[index + 1]
                    callback({"status": "downloading", "event": "range_recovery",
                              "text": f"CDN lỗi với {window} kết nối; giảm còn {nxt}, nối tiếp phần đã xác minh"})
    else:
        expected_hash = _download_single(winner.url, headers, part_path, winner.length,
                                         label, callback, phase_start, phase_span)
    size = os.path.getsize(part_path)
    if (winner.length and size != winner.length) or _file_hash(part_path) != expected_hash:
        raise RuntimeError("File ghép sai kích thước/SHA256; không công nhận tải xong")
    source_verification = _verify_source_checksums(part_path, source_checksums)
    if callback:
        callback({"status": "downloading", "source_verification": source_verification["status"],
                  "text": ("Đã đối chiếu checksum nguồn Bilibili" if source_checksums else
                           "Bilibili không cung cấp hash nguồn; đã kiểm tra SHA-256 nội bộ")})
    os.replace(part_path, destination)
    _save_integrity(destination + ".integrity.json",
                    {"identity": identity, "size": size, "sha256": expected_hash,
                     "source_verification": source_verification})
    if os.path.isfile(part_path + ".json"):
        os.unlink(part_path + ".json")
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
                         progress_callback, 0.0, 97.0, source_checksums=stream.video_checksums)
    else:
        video_temp = destination + ".video.m4s"
        audio_temp = destination + ".audio.m4s"
        _download_stream(stream.video_urls, video_temp, headers,
                         f"Hình {bvid} {quality_label(stream.quality)}",
                         progress_callback, 0.0, 82.0, source_checksums=stream.video_checksums)
        _download_stream(stream.audio_urls, audio_temp, headers,
                         f"Tiếng {bvid}", progress_callback, 82.0, 15.0,
                         source_checksums=stream.audio_checksums)
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
