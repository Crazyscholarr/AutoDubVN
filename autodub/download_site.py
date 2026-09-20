"""Nhận diện nền tảng video và tuỳ chọn tải theo từng nguồn.

YouTube hay trả 429 / “Sign in to confirm you’re not a bot” khi không có cookie
hoặc thiếu JS runtime. Douyin cần cookie trình duyệt (kể cả chưa đăng nhập).
Module này không gọi yt-dlp; ``downloader`` dùng các hàm này để chọn cookie,
chuẩn hoá URL và gắn extractor-args.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Sequence
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from .utils import which

_SHORT_HOSTS = {
    "v.douyin.com", "www.iesdouyin.com", "iesdouyin.com",
    "v.kuaishou.com", "www.kuaishou.com",
    "b23.tv", "bili2233.cn",
    "vm.tiktok.com", "vt.tiktok.com",
}

_BROWSER_PATHS = {
    "chrome": (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ),
    "edge": (
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ),
    "firefox": (
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    ),
}

SITE_LABELS = {
    "youtube": "YouTube",
    "bilibili": "Bilibili",
    "douyin": "Douyin",
    "tiktok": "TikTok",
    "kuaishou": "Kuaishou",
    "ixigua": "Ixigua",
    "xiaohongshu": "Xiaohongshu",
    "weibo": "Weibo",
}

VIDEO_SEARCH_PROVIDERS = (
    "all", "bilibili", "youtube", "douyin", "tiktok", "kuaishou", "ixigua",
)

_PROVIDER_ALIASES = {
    "both": "all", "tat_ca": "all", "yt": "youtube", "bili": "bilibili",
    "dy": "douyin", "抖音": "douyin", "ks": "kuaishou", "快手": "kuaishou",
    "xigua": "ixigua", "西瓜": "ixigua", "西瓜视频": "ixigua",
}

_AUTH_TOKENS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you are not a bot",
    "use --cookies-from-browser",
    "use --cookies for the authentication",
    "please sign in",
    "login required",
    "fresh cookies",
    "cookies (not necessarily logged in) are needed",
    "http error 403",
    "this content isn't available",
)


def detect_site(url: str) -> str:
    """Trả về khoá nguồn (youtube, douyin, ...) hoặc ``other``."""
    host = (urlparse(str(url or "")).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in {"youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com",
                "youtube-nocookie.com"}:
        return "youtube"
    if host in {"bilibili.com", "b23.tv", "bili2233.cn"} or host.endswith(".bilibili.com"):
        return "bilibili"
    if host in {"douyin.com", "iesdouyin.com", "v.douyin.com"} or host.endswith(".douyin.com"):
        return "douyin"
    if host in {"tiktok.com", "vm.tiktok.com", "vt.tiktok.com"} or host.endswith(".tiktok.com"):
        return "tiktok"
    if "kuaishou.com" in host or host.endswith(".kuaishou.com"):
        return "kuaishou"
    if "ixigua.com" in host or "toutiao.com" in host:
        return "ixigua"
    if "xiaohongshu.com" in host or host == "xhslink.com":
        return "xiaohongshu"
    if "weibo.com" in host or host == "weibo.cn":
        return "weibo"
    return "other"


def site_label(url: str) -> str:
    return SITE_LABELS.get(detect_site(url), "nguồn video")


def normalise_search_provider(value: str, default: str = "all") -> str:
    raw = str(value or "").strip().lower()
    raw = _PROVIDER_ALIASES.get(raw, raw)
    if raw in VIDEO_SEARCH_PROVIDERS:
        return raw
    return default


def canonical_video_url(url: str, follow_redirects: bool = True) -> str:
    """Đưa link rút gọn / share Douyin về URL video chuẩn."""
    raw = str(url or "").strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query or "")
    modal = (query.get("modal_id") or [""])[0].strip()
    if modal.isdigit() and "douyin" in host:
        return "https://www.douyin.com/video/" + modal
    share = re.search(r"/share/video/(\d+)", parsed.path or "")
    if share and "douyin" in host:
        return "https://www.douyin.com/video/" + share.group(1)
    if follow_redirects and host in _SHORT_HOSTS:
        resolved = _follow_redirect(raw)
        if resolved and resolved != raw:
            return canonical_video_url(resolved, follow_redirects=False)
    return raw


def _follow_redirect(url: str, timeout: int = 12) -> str:
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 Chrome/127 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        request = Request(url, headers=headers, method="GET")
        with urlopen(request, timeout=timeout) as response:
            return str(response.geturl() or url)
    except Exception:
        return url


def installed_cookie_browsers() -> List[str]:
    """Browser spec yt-dlp có thể đọc cookie, theo thứ tự ưu tiên."""
    found: List[str] = []
    for name, spec in (("chrome", "chrome"), ("edge", "edge:Default"),
                       ("firefox", "firefox")):
        if _browser_installed(name):
            found.append(spec)
    return found


def _browser_installed(name: str) -> bool:
    exe = {"chrome": "chrome", "edge": "msedge", "firefox": "firefox"}[name]
    if which(exe):
        return True
    return any(os.path.isfile(path) for path in _BROWSER_PATHS[name])


def cookie_browser_candidates(preferred: Optional[str],
                              installed: Optional[Sequence[str]] = None) -> List[str]:
    """Danh sách cookie browser để lần lượt thử (không gồm lần không cookie)."""
    ordered: List[str] = []
    seen = set()

    def _add(spec: Optional[str]) -> None:
        value = str(spec or "").strip()
        if not value:
            return
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        ordered.append(value)

    _add(preferred)
    for spec in (installed if installed is not None else installed_cookie_browsers()):
        _add(spec)
    return ordered


def js_runtime_args() -> List[str]:
    """Bật deno/node/bun nếu có trong máy; YouTube cần JS runtime từ 2025."""
    found = []
    for name in ("deno", "node", "bun"):
        path = which(name)
        if path:
            found.append((name, path))
    if not found:
        return []
    args: List[str] = []
    if not any(name == "deno" for name, _ in found):
        args.append("--no-js-runtimes")
    for name, path in found:
        args += ["--js-runtimes", f"{name}:{path}"]
    return args


def site_ytdlp_args(url: str) -> List[str]:
    """Extractor-args / JS runtime gắn thêm vào lệnh yt-dlp."""
    args = js_runtime_args()
    site = detect_site(url)
    if site == "bilibili":
        # html5 hay trả 1 file MP4 công khai (tải thẳng); web giữ DASH nét hơn.
        args += ["--extractor-args", "bilibili:player_client=html5,web"]
    if site == "youtube":
        args += [
            "--extractor-args",
            "youtube:player_client=default,android,ios,tv,web",
            "--sleep-requests", "1",
            "--sleep-interval", "1",
            "--max-sleep-interval", "5",
            "--remote-components", "ejs:github",
        ]
    return args


def auth_challenge_failed(exc) -> bool:
    message = str(exc or "").lower()
    return any(token in message for token in _AUTH_TOKENS)


def rate_limited(exc) -> bool:
    message = str(exc or "").lower()
    return "http error 429" in message or "too many requests" in message


def needs_cookie_fallback(url: str, exc) -> bool:
    """YouTube bot/429 và Douyin thiếu cookie không phải lỗi CDN Range."""
    if auth_challenge_failed(exc):
        return True
    site = detect_site(url)
    if site in {"youtube", "douyin", "tiktok"} and rate_limited(exc):
        return True
    return False


def cookie_hint(url: str) -> str:
    site = site_label(url)
    return (
        f"{site} có thể cần cookie trình duyệt đã mở trang đó. Đặt "
        "download.cookies_from_browser: chrome (hoặc edge:Default / firefox) "
        "trong config.yaml, rồi thoát hẳn trình duyệt nếu báo cookie đang khóa."
    )
