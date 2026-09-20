"""Điều khiển ChatGPT web bằng hồ sơ Edge đã đăng nhập.

Tách hẳn với Gemini (`browser_profile`) để tránh khoá profile. Dùng cho
thumbnail YouTube và mô tả kênh sau khi dựng video kể chuyện.
"""
from __future__ import annotations

import base64
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .story_images import _payload_usable


CHATGPT_WEB_URL = "https://chatgpt.com/"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_SKIP_IMAGE_URL = (
    "favicon", "avatar", "sprite", "emoji", "/static/", "gravatar",
    "profile-pic", "user-pic", "/assets/",
)
_IMAGE_HOSTS = (
    "oaiusercontent.com",
    "oaidalle",
    "dalleapiprod",
    "chatgpt.com/backend-api",
)
_FAIL_TEXT = (
    "i can't generate",
    "i cannot generate",
    "image generation is not available",
    "không thể tạo ảnh",
    "khong the tao anh",
    "không tạo được ảnh",
)

COMPOSER_SELECTORS = (
    "#prompt-textarea",
    '[data-testid="prompt-textarea"]',
    'div[contenteditable="true"]#prompt-textarea',
    'textarea[placeholder*="Ask" i]',
    'textarea[placeholder*="Hỏi" i]',
)
SEND_SELECTORS = (
    'button[data-testid="send-button"]',
    'button[aria-label="Send prompt"]',
    'button[aria-label="Send message"]',
    'button[aria-label="Gửi lời nhắc"]',
    'button[aria-label="Gửi tin nhắn"]',
)
STOP_SELECTORS = (
    'button[data-testid="stop-button"]',
    'button[aria-label="Stop streaming"]',
    'button[aria-label="Stop generating"]',
    'button[aria-label="Dừng"]',
)
NEW_CHAT_SELECTORS = (
    'a[href="/"]',
    'a[data-testid="create-new-chat-button"]',
    'button[data-testid="create-new-chat-button"]',
    'a[aria-label*="New chat" i]',
    'a[aria-label*="Chat mới" i]',
    'button[aria-label*="New chat" i]',
)
FILE_INPUT_SELECTORS = (
    'input[type="file"]',
    'input[accept*="image"]',
)
ATTACH_BUTTON_SELECTORS = (
    'button[data-testid="composer-plus-btn"]',
    'button[aria-label*="Add files" i]',
    'button[aria-label*="Add photos" i]',
    'button[aria-label*="Attach" i]',
    'button[aria-label*="Đính kèm" i]',
    'button[aria-label*="Thêm tệp" i]',
)


class ChatGPTWebError(RuntimeError):
    """Lỗi đọc được khi điều khiển ChatGPT web."""


def looks_like_chatgpt_image_url(url: str) -> bool:
    """URL ảnh do ChatGPT vừa sinh, loại avatar/icon."""
    raw = str(url or "").strip()
    lowered = raw.lower()
    if len(lowered) < 8:
        return False
    if any(token in lowered for token in _SKIP_IMAGE_URL):
        return False
    if lowered.startswith("blob:"):
        return True
    if re.search(r"\.(?:png|jpe?g|webp)(?:\?|$)", lowered) and any(
            host in lowered for host in _IMAGE_HOSTS):
        return True
    return any(host in lowered for host in _IMAGE_HOSTS) and len(lowered) > 40


def composer_ready(info: Optional[dict]) -> bool:
    data = info if isinstance(info, dict) else {}
    return bool(data.get("ready")) and not bool(data.get("login"))


def _browser_candidates() -> List[Tuple[str, str]]:
    """Chrome trước, vì nhiều máy không cài Edge (msedge không có trong PATH)."""
    roots = [
        os.environ.get("ProgramFiles") or r"C:\Program Files",
        os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)",
        os.environ.get("LocalAppData") or "",
    ]
    names = (
        ("Google\\Chrome\\Application\\chrome.exe", "chrome"),
        ("Microsoft\\Edge\\Application\\msedge.exe", "msedge"),
    )
    out: List[Tuple[str, str]] = []
    seen = set()
    for root in roots:
        root = str(root or "").rstrip("\\/")
        if not root:
            continue
        for rel, kind in names:
            path = os.path.join(root, rel)
            key = os.path.normcase(path)
            if key not in seen:
                seen.add(key)
                out.append((path, kind))
    return out


def find_login_browser() -> Tuple[str, str]:
    """Trả về (đường dẫn exe, kênh Playwright) của Chrome hoặc Edge đang có."""
    for path, kind in _browser_candidates():
        if path and os.path.isfile(path):
            return path, kind
    return "", ""


def launch_chatgpt_login(profile_dir: str = "",
                         url: str = CHATGPT_WEB_URL) -> Dict[str, Any]:
    """Mở Chrome/Edge với hồ sơ ChatGPT riêng — không phụ thuộc file BAT."""
    exe, kind = find_login_browser()
    if not exe:
        return {"error": (
            "Không thấy Google Chrome hoặc Microsoft Edge. "
            "Hãy cài Chrome rồi chạy lại login_chatgpt.bat.")}
    raw = str(profile_dir or "").strip() or str(PROJECT_ROOT / "browser_profile_chatgpt")
    profile = Path(raw).expanduser()
    if not profile.is_absolute():
        profile = PROJECT_ROOT / profile
    profile.mkdir(parents=True, exist_ok=True)
    target = str(url or CHATGPT_WEB_URL).strip() or CHATGPT_WEB_URL
    try:
        subprocess.Popen(
            [exe, "--user-data-dir=" + str(profile),
             "--start-maximized", "--new-window", target],
            close_fds=True)
    except Exception as exc:
        return {"error": "Không mở được %s: %s" % (kind or "trình duyệt", str(exc)[:160])}
    return {"ok": True, "browser": kind, "exe": exe, "profile": str(profile)}


def chatgpt_settings(cfg: dict) -> dict:
    """Đọc mục dang_youtube; mặc định hồ sơ tách với Gemini."""
    yt = cfg.get("dang_youtube") if isinstance(cfg.get("dang_youtube"), dict) else {}
    raw_profile = str(yt.get("browser_profile") or "browser_profile_chatgpt").strip()
    profile = Path(raw_profile).expanduser()
    if not profile.is_absolute():
        profile = PROJECT_ROOT / profile
    requested = str(yt.get("browser_channel") or "").strip()
    _exe, installed = find_login_browser()
    channel = installed or requested or "chrome"
    return {
        "profile_dir": str(profile.resolve()),
        "channel": channel,
        "url": str(yt.get("browser_url") or CHATGPT_WEB_URL).strip(),
        "wait_image": max(45.0, float(yt.get("wait_image_seconds", 120) or 120)),
        "wait_reply": max(45.0, float(yt.get("wait_reply_seconds", 180) or 180)),
    }


def _decode_data_url(encoded: str) -> Optional[bytes]:
    raw = str(encoded or "")
    if "," not in raw:
        return None
    try:
        payload = base64.b64decode(raw.split(",", 1)[1], validate=True)
    except Exception:
        return None
    return payload if _payload_usable(payload) else None


class ChatGPTWebSession:
    """Một cửa sổ ChatGPT: gửi prompt chữ hoặc xin đúng một ảnh."""

    def __init__(self, profile_dir: str, channel: str = "msedge",
                 url: str = CHATGPT_WEB_URL, wait_image: float = 120,
                 wait_reply: float = 180, logger: Optional[Callable] = None,
                 cancel_event=None):
        self.profile_dir = os.path.abspath(profile_dir)
        self.channel = str(channel or "msedge").strip()
        self.url = str(url or CHATGPT_WEB_URL).strip()
        self.wait_image = max(45.0, float(wait_image or 120))
        self.wait_reply = max(45.0, float(wait_reply or 180))
        self.logger = logger or (lambda _msg, _kind="info": None)
        self.cancel_event = cancel_event
        self._pw = None
        self.context = None
        self.page = None
        self._downloads_dir = None
        self._sniffed: List[bytes] = []
        self._sniffer_attached = False

    def _cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise InterruptedError("Đã huỷ tác vụ ChatGPT.")

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < end:
            self._cancelled()
            time.sleep(max(0.0, min(0.25, end - time.monotonic())))

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def start(self) -> "ChatGPTWebSession":
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise ChatGPTWebError(
                "Chưa cài Playwright; hãy chạy install.bat trước.") from exc
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        self.logger("Mở ChatGPT bằng hồ sơ: %s" % self.profile_dir, "step")
        self._pw = sync_playwright().start()
        order = [self.channel] if self.channel else []
        order += [x for x in ("msedge", "chrome", None) if x not in order]
        last_error = None
        for browser_channel in order:
            for attempt in range(1, 3):
                self._cancelled()
                try:
                    if self._downloads_dir is None:
                        self._downloads_dir = Path(tempfile.mkdtemp(prefix="advn_cgpt_"))
                    kwargs = dict(
                        user_data_dir=self.profile_dir,
                        headless=False,
                        args=["--start-maximized",
                              "--disable-blink-features=AutomationControlled"],
                        ignore_default_args=["--enable-automation"],
                        no_viewport=True,
                        accept_downloads=True,
                        downloads_path=str(self._downloads_dir),
                        locale="vi-VN",
                    )
                    if browser_channel:
                        kwargs["channel"] = browser_channel
                    self.context = self._pw.chromium.launch_persistent_context(**kwargs)
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < 2:
                        self._sleep(3)
            if self.context is not None:
                break
        if self.context is None:
            self.close()
            brief = (str(last_error).strip().splitlines() or
                     [type(last_error).__name__ if last_error else "không rõ"])[0]
            raise ChatGPTWebError(
                "Không mở được hồ sơ ChatGPT. Hãy đóng cửa sổ đã mở bởi "
                "login_chatgpt.bat rồi chạy lại. (%s)" % brief[:180])
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(30000)
        self._attach_image_sniffer()
        try:
            self.page.goto(self.url, wait_until="domcontentloaded", timeout=90000)
        except Exception:
            pass
        self.ensure_ready(timeout=35.0)
        return self

    def close(self) -> None:
        folder = self._downloads_dir
        self._downloads_dir = None
        self._sniffer_attached = False
        self._sniffed = []
        for closer in (getattr(self.context, "close", None),
                       getattr(self._pw, "stop", None)):
            if closer is None:
                continue
            try:
                closer()
            except Exception:
                pass
        self.context = None
        self._pw = None
        self.page = None
        if folder:
            shutil.rmtree(folder, ignore_errors=True)

    def _page_state(self) -> dict:
        if self.page is None:
            return {"ready": False, "login": True}
        try:
            return self.page.evaluate("""() => {
                const href = String(location.href || '');
                const composer = document.querySelector(
                    '#prompt-textarea, [data-testid="prompt-textarea"]');
                const box = composer ? composer.getBoundingClientRect() : null;
                const ready = !!(box && box.height > 8 && box.width > 8);
                const loginHref = /\\/auth\\/login|\\/log-in/i.test(href);
                const loginBtn = [...document.querySelectorAll('button, a')].some(el =>
                    /^(log in|sign in|đăng nhập)$/i.test((el.innerText || '').trim()));
                return {ready, login: loginHref || (loginBtn && !ready), href};
            }""") or {}
        except Exception:
            return {"ready": False, "login": False}

    def ensure_ready(self, timeout: float = 35.0) -> None:
        deadline = time.monotonic() + max(5.0, float(timeout))
        last = {}
        while time.monotonic() < deadline:
            self._cancelled()
            last = self._page_state()
            if composer_ready(last):
                return
            self._sleep(0.6)
        current = str((last or {}).get("href") or getattr(self.page, "url", "") or "")
        raise ChatGPTWebError(
            "Chưa vào được ô chat ChatGPT (%s). Hãy chạy login_chatgpt.bat, "
            "đăng nhập tài khoản của bạn, rồi đóng hết cửa sổ Edge đó trước "
            "khi chạy lại." % (current[:80] or "chưa đăng nhập"))

    def new_chat(self) -> None:
        """Mở cuộc trò chuyện mới để tách việc tạo ảnh và viết chữ."""
        self._cancelled()
        clicked = False
        for sel in NEW_CHAT_SELECTORS:
            try:
                loc = self.page.locator(sel)
                if loc.count() and loc.first.is_visible(timeout=800):
                    loc.first.click(timeout=4000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            try:
                self.page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
        self.ensure_ready(timeout=25.0)
        self._sleep(0.4)

    def _fill_composer(self, prompt: str) -> None:
        text = str(prompt or "").strip()
        if not text:
            raise ChatGPTWebError("Prompt ChatGPT đang trống.")
        self.ensure_ready(timeout=15.0)
        filled = False
        try:
            filled = bool(self.page.evaluate("""text => {
                const el = document.querySelector(
                    '#prompt-textarea, [data-testid="prompt-textarea"]');
                if (!el) return false;
                el.focus();
                if (el.tagName === 'TEXTAREA') {
                    el.value = text;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    return true;
                }
                try {
                    document.execCommand('selectAll');
                    const ok = document.execCommand('insertText', false, text);
                    if (ok) return true;
                } catch (e) {}
                el.textContent = text;
                el.dispatchEvent(new InputEvent('input', {
                    bubbles: true, data: text, inputType: 'insertText'}));
                return true;
            }""", text))
        except Exception:
            filled = False
        if not filled:
            box = None
            for sel in COMPOSER_SELECTORS:
                try:
                    loc = self.page.locator(sel)
                    if loc.count() and loc.first.is_visible(timeout=800):
                        box = loc.first
                        break
                except Exception:
                    continue
            if box is None:
                raise ChatGPTWebError("Không thấy ô nhập ChatGPT.")
            try:
                box.click(timeout=4000)
                box.fill(text, timeout=15000)
            except Exception:
                box.click(timeout=4000)
                self.page.keyboard.insert_text(text)

    def _locator_first(self, selectors: Sequence[str], timeout: float = 800):
        for sel in selectors:
            try:
                loc = self.page.locator(sel)
                if loc.count():
                    first = loc.first
                    try:
                        if first.is_visible(timeout=timeout):
                            return first
                    except Exception:
                        return first
            except Exception:
                continue
        return None

    def _click_attach_button(self) -> bool:
        btn = self._locator_first(ATTACH_BUTTON_SELECTORS, timeout=600)
        if btn is None:
            return False
        try:
            btn.click(timeout=4000)
            return True
        except Exception:
            return False

    def _set_composer_files(self, files: Sequence[str]) -> bool:
        paths = [str(p) for p in files if p]
        if not paths or self.page is None:
            return False
        for sel in FILE_INPUT_SELECTORS:
            try:
                loc = self.page.locator(sel)
                n = loc.count()
            except Exception:
                continue
            if not n:
                continue
            for idx in (n - 1, 0):
                try:
                    loc.nth(idx).set_input_files(paths, timeout=8000)
                    return True
                except Exception:
                    continue
        return False

    def _attach_via_chooser(self, files: Sequence[str]) -> bool:
        if self.page is None or not hasattr(self.page, "expect_file_chooser"):
            return False
        try:
            with self.page.expect_file_chooser(timeout=7000) as pending:
                if not self._click_attach_button():
                    raise ChatGPTWebError("no attach button")
            pending.value.set_files(list(files))
            return True
        except Exception:
            return False

    def attach_images(self, paths: Optional[Sequence[str]] = None) -> List[str]:
        """Đính ảnh cảnh vào ô chat trước khi xin thumbnail."""
        files = []
        seen = set()
        for raw in list(paths or [])[:4]:
            path = os.path.abspath(str(raw or "").strip().strip('"'))
            if path and path not in seen and os.path.isfile(path):
                files.append(path)
                seen.add(path)
        if not files:
            return []
        self.ensure_ready(timeout=15.0)
        if self._set_composer_files(files):
            self._sleep(1.0)
            return files
        self._click_attach_button()
        self._sleep(0.4)
        if self._set_composer_files(files):
            self._sleep(1.0)
            return files
        if self._attach_via_chooser(files):
            self._sleep(1.0)
            return files
        raise ChatGPTWebError(
            "Không đính được ảnh cảnh vào ChatGPT. Hãy thử login_chatgpt.bat "
            "rồi đóng hết cửa sổ đó trước khi chạy lại.")

    def _click_send(self) -> None:
        for sel in SEND_SELECTORS:
            try:
                loc = self.page.locator(sel)
                if loc.count() and loc.first.is_enabled(timeout=800):
                    loc.first.click(timeout=6000)
                    return
            except Exception:
                continue
        self.page.keyboard.press("Enter")

    def _stop_visible(self) -> bool:
        for sel in STOP_SELECTORS:
            try:
                loc = self.page.locator(sel)
                if loc.count() and loc.first.is_visible(timeout=200):
                    return True
            except Exception:
                continue
        return False

    def _assistant_text(self) -> str:
        try:
            raw = self.page.evaluate("""() => {
                const nodes = document.querySelectorAll(
                    '[data-message-author-role="assistant"]');
                const last = nodes[nodes.length - 1];
                if (!last) return '';
                const md = last.querySelector(
                    '.markdown, .prose, [class*="markdown"]');
                return ((md || last).innerText || '').trim();
            }""") or ""
        except Exception:
            raw = ""
        return re.sub(r"[ \t]+\n", "\n", str(raw).strip())

    def _visible_image_keys(self) -> List[str]:
        try:
            keys = self.page.evaluate("""() => {
                const root = document.querySelector('main, [role="main"]') || document;
                const out = [];
                for (const img of root.querySelectorAll('img')) {
                    const src = img.currentSrc || img.src || '';
                    if (src) out.push(src);
                }
                return out;
            }""") or []
            return [str(item) for item in keys if item]
        except Exception:
            return []

    def _attach_image_sniffer(self) -> None:
        if self._sniffer_attached or self.page is None:
            return

        def _on_response(response) -> None:
            try:
                url = str(getattr(response, "url", "") or "")
                headers = getattr(response, "headers", None) or {}
                ct = str(headers.get("content-type") or
                         headers.get("Content-Type") or "").lower()
            except Exception:
                return
            if not looks_like_chatgpt_image_url(url) and "image/" not in ct:
                return
            if any(token in url.lower() for token in _SKIP_IMAGE_URL):
                return
            try:
                encoded = self.page.evaluate(
                    """async url => {
                        const toData = blob => new Promise((resolve, reject) => {
                            const reader = new FileReader();
                            reader.onload = () => resolve(reader.result);
                            reader.onerror = reject;
                            reader.readAsDataURL(blob);
                        });
                        const controller = new AbortController();
                        const timer = setTimeout(() => controller.abort(), 12000);
                        try {
                            const response = await fetch(url, {
                                credentials: 'include', signal: controller.signal});
                            if (!response.ok) return '';
                            const blob = await response.blob();
                            if (!blob || blob.size < 400) return '';
                            return await toData(blob);
                        } catch (_) { return ''; }
                        finally { clearTimeout(timer); }
                    }""",
                    url,
                )
            except Exception:
                return
            payload = _decode_data_url(str(encoded or ""))
            if payload:
                self._sniffed.append(payload)

        try:
            self.page.on("response", _on_response)
            self._sniffer_attached = True
        except Exception:
            pass

    def _best_sniffed(self, since: int = 0) -> Optional[bytes]:
        candidates = [item for item in self._sniffed[since:] if _payload_usable(item)]
        return max(candidates, key=len) if candidates else None

    def _pickup_download(self, since: float) -> Optional[bytes]:
        folder = self._downloads_dir
        if not folder or not Path(folder).is_dir():
            return None
        newest = None
        newest_mtime = float(since or 0) - 0.2
        for path in Path(folder).rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {
                    ".png", ".jpg", ".jpeg", ".webp"}:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < newest_mtime:
                continue
            data = path.read_bytes()
            if _payload_usable(data):
                newest, newest_mtime = data, stat.st_mtime
        return newest

    def _cache_image_element(self, image) -> Optional[bytes]:
        try:
            encoded = image.evaluate("""async el => {
                const toData = blob => new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(reader.result);
                    reader.onerror = reject;
                    reader.readAsDataURL(blob);
                });
                const src = el.currentSrc || el.src || '';
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), 15000);
                try {
                    if (src) {
                        const response = await fetch(src, {
                            credentials: 'include', signal: controller.signal});
                        if (response.ok) return await toData(await response.blob());
                    }
                } catch (_) {}
                finally { clearTimeout(timer); }
                try {
                    const canvas = document.createElement('canvas');
                    canvas.width = el.naturalWidth || el.width || 0;
                    canvas.height = el.naturalHeight || el.height || 0;
                    if (canvas.width < 8 || canvas.height < 8) return '';
                    canvas.getContext('2d').drawImage(el, 0, 0);
                    return canvas.toDataURL('image/png');
                } catch (_) { return ''; }
            }""")
            return _decode_data_url(str(encoded or ""))
        except Exception:
            return None

    def _new_image_element(self, previous):
        handle = self.page.evaluate_handle("""previous => {
            const seen = new Set(previous);
            const root = document.querySelector('main, [role="main"]') || document;
            const shown = el => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width >= 160 && rect.height >= 120
                    && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const skip = el => el.closest(
                'nav, aside, [role="navigation"], [role="complementary"]');
            const imgs = [...root.querySelectorAll('img')].reverse();
            for (const el of imgs) {
                if (skip(el) || !shown(el)) continue;
                const src = el.currentSrc || el.src || '';
                if (!src || seen.has(src) || !el.complete) continue;
                if (el.naturalWidth < 256 || el.naturalHeight < 180) continue;
                return el;
            }
            return null;
        }""", list(previous))
        element = handle.as_element()
        if element is None:
            handle.dispose()
        return element

    def _save_image_bytes(self, payload: bytes, dest: Path) -> Path:
        from PIL import Image
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp = dest.with_name("._" + dest.name + ".part")
        try:
            with Image.open(io.BytesIO(payload)) as img:
                img.load()
                img.save(temp, format="PNG")
            os.replace(temp, dest)
            return dest
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)

    def ask_text(self, prompt: str) -> str:
        """Gửi prompt và lấy câu trả lời chữ của lượt assistant cuối."""
        self._cancelled()
        self._fill_composer(prompt)
        before = self._assistant_text()
        self._click_send()
        sent_at = time.monotonic()
        deadline = sent_at + self.wait_reply
        last = ""
        next_log = sent_at + 20.0
        while time.monotonic() < deadline:
            self._cancelled()
            last = self._assistant_text()
            busy = self._stop_visible()
            if last and last != before and len(last) > 80 and not busy:
                self._sleep(1.2)
                stable = self._assistant_text()
                if stable == last or len(stable) >= len(last):
                    return stable or last
            now = time.monotonic()
            if now >= next_log:
                self.logger("ChatGPT vẫn đang viết mô tả (%d/%d giây)…"
                            % (int(now - sent_at), int(self.wait_reply)), "info")
                next_log = now + 20.0
            self._sleep(0.6)
        if last and last != before and len(last) > 80:
            return last
        raise ChatGPTWebError("ChatGPT không trả lời mô tả kịp trong %d giây."
                              % int(self.wait_reply))

    def generate_image(self, prompt: str, dest_path: str | os.PathLike,
                       image_paths: Optional[Sequence[str]] = None) -> Path:
        """Xin đúng một ảnh và lưu PNG vào dest_path. Có thể kèm ảnh cảnh mẫu."""
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        files = []
        seen = set()
        for raw in list(image_paths or [])[:4]:
            path = os.path.abspath(str(raw or "").strip().strip('"'))
            if path and path not in seen and os.path.isfile(path):
                files.append(path)
                seen.add(path)
        lead = (
            "Create exactly one photorealistic 16:9 image. "
            "Do not write any letters, numbers, captions, logos, watermarks, "
            "subtitles or UI text in the image. Papers or documents must show "
            "only unreadable marks, never real words."
        )
        if files:
            lead += (
                " I attached %d still frames from the actual movie. "
                "Use them as visual reference: keep the same faces, costumes, "
                "age and setting. Restage as a YouTube thumbnail, main person "
                "on the RIGHT, left half empty and a bit darker for a title."
                % len(files)
            )
        request = lead + "\n\n" + str(prompt or "").strip()
        sniff_from = len(self._sniffed)
        previous = set(self._visible_image_keys())
        started_at = time.time()
        self._fill_composer(request)
        if files:
            try:
                self.attach_images(files)
            except ChatGPTWebError as exc:
                self.logger("Không đính được ảnh cảnh, vẫn gửi prompt chữ: %s"
                            % str(exc)[:180], "warn")
        self._click_send()
        sent_at = time.monotonic()
        deadline = sent_at + self.wait_image
        last_error = None
        next_log = sent_at + 15.0
        while time.monotonic() < deadline:
            self._cancelled()
            now = time.monotonic()
            if now >= next_log:
                self.logger("ChatGPT vẫn đang tạo thumbnail (%d/%d giây)…"
                            % (int(now - sent_at), int(self.wait_image)), "info")
                next_log = now + 15.0
            reply = self._assistant_text().lower()
            if any(token in reply for token in _FAIL_TEXT) and not self._stop_visible():
                raise ChatGPTWebError(
                    "Tài khoản ChatGPT không tạo được ảnh. Kiểm tra gói Plus/Pro "
                    "hoặc quyền tạo ảnh rồi chạy lại.")
            image = None
            try:
                image = self._new_image_element(previous)
            except Exception as exc:
                last_error = exc
            for payload in (
                    self._cache_image_element(image) if image is not None else None,
                    self._best_sniffed(sniff_from),
                    self._pickup_download(started_at),
            ):
                if not payload:
                    continue
                try:
                    return self._save_image_bytes(payload, dest)
                except Exception as exc:
                    last_error = exc
            if image is not None:
                try:
                    shot = image.screenshot(type="png", timeout=12000)
                    if _payload_usable(shot):
                        return self._save_image_bytes(shot, dest)
                except Exception as exc:
                    last_error = exc
            self._sleep(0.8)
        raise ChatGPTWebError(
            "Hết %d giây mà chưa lưu được ảnh thumbnail. %s"
            % (int(self.wait_image), str(last_error or "")[:160]))
