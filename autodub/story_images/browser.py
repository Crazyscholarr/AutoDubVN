"""Điều khiển Gemini web để tạo ảnh và rút prompt cảnh."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .media import (
    _MIN_SCENE_BYTES, _maybe_image_response, _payload_usable,
)
from .pack import (
    GEMINI_WEB_URL, IMAGE_EXTENSIONS, PROJECT_ROOT,
    _manifest_path, _write_json, load_pack, parse_scene_prompts,
)


def gemini_browser_settings(cfg: Dict) -> Dict:
    """Lấy cấu hình Gemini web, mặc định dùng chung profile với phần dịch."""
    tr = cfg.get("translation") if isinstance(cfg.get("translation"), dict) else {}
    image_cfg = cfg.get("tao_anh") if isinstance(cfg.get("tao_anh"), dict) else {}
    raw_profile = str(image_cfg.get("browser_profile") or
                      tr.get("browser_profile") or "browser_profile").strip()
    profile = Path(raw_profile).expanduser()
    if not profile.is_absolute():
        profile = PROJECT_ROOT / profile
    return {
        "profile_dir": str(profile.resolve()),
        "channel": str(image_cfg.get("browser_channel") or
                       tr.get("browser_channel") or "msedge").strip(),
        "url": str(image_cfg.get("browser_url") or GEMINI_WEB_URL).strip(),
        "timeout": max(45.0, float(image_cfg.get(
            "wait_image_seconds", 90) or 90)),
        "wait_reply": max(30, int(image_cfg.get(
            "wait_prompt_seconds", tr.get("wait_reply", 180)) or 180)),
        "retries": max(1, int(image_cfg.get(
            "browser_retries", 2) or 2)),
        "request_gap": max(0.0, float(image_cfg.get("request_gap_seconds", 1.5) or 0)),
        "session_restarts": max(0, int(image_cfg.get(
            "browser_session_restarts", 5) or 0)),
        "fresh_chat_every": max(0, int(image_cfg.get(
            "browser_fresh_chat_every", 2) or 0)),
        "restart_cooldown": max(0.0, float(image_cfg.get(
            "browser_restart_cooldown_seconds", 10) or 0)),
    }


class GeminiBrowserError(RuntimeError):
    """Lỗi có thể đọc được của luồng điều khiển Gemini web."""


class GeminiSceneSaveError(GeminiBrowserError):
    """Ảnh đã tạo: không được retry bằng cách gửi lại prompt."""


def generate_scene_prompts_gemini_browser(
        master_prompt: str, expected_count: int, profile_dir: str,
        channel: str = "msedge", url: str = GEMINI_WEB_URL,
        wait_reply: int = 180, logger: Optional[Callable] = None) -> List[str]:
    """Nhờ Gemini web lập prompt cảnh bằng profile đã đăng nhập."""
    logger = logger or (lambda _msg, _kind="info": None)
    try:
        from .. import translate
        logger("Đang rút prompt cảnh bằng Gemini đã đăng nhập…", "step")
        with translate.phien_gemini_trinh_duyet(
                profile_dir, channel=channel, url=url,
                wait_reply=wait_reply) as ask:
            raw = ask(master_prompt)
        prompts = parse_scene_prompts(raw)
        if len(prompts) < expected_count:
            raise GeminiBrowserError(
                "Gemini chỉ trả %d/%d prompt cảnh." %
                (len(prompts), expected_count))
        logger("Đã nhận đủ %d prompt cảnh từ Gemini web." % expected_count, "ok")
        return prompts[:expected_count]
    except GeminiBrowserError:
        raise
    except Exception as exc:
        raise GeminiBrowserError(
            "Không rút được prompt bằng Gemini web: %s" % str(exc)[:220]) from exc


def _visible_role(page, role: str, name_pattern, timeout: float = 30.0):
    """Tìm phần tử role đang hiện; Gemini thường giữ vài bản DOM đã ẩn."""
    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        try:
            loc = page.get_by_role(role, name=name_pattern)
            for idx in range(min(loc.count(), 8)):
                item = loc.nth(idx)
                if item.is_visible(timeout=500):
                    return item
        except Exception:
            pass
        page.wait_for_timeout(350)
    return None


class _GeminiWebImageSession:
    """Một cửa sổ Gemini web tạo nhiều cảnh liên tiếp trong cùng cuộc chat."""

    _INPUT_NAME = re.compile(
        r"Nhập câu lệnh cho Gemini|Mô tả hình ảnh|Enter a prompt|Ask Gemini|Describe your image",
        re.IGNORECASE)
    _TOOLS_NAME = re.compile(
        r"Nội dung tải lên và công cụ|Upload files|Add files|files and tools",
        re.IGNORECASE)
    _IMAGE_MODE_NAME = re.compile(
        r"Tạo hình ảnh|Create images?|Generate images?", re.IGNORECASE)
    _IMAGE_MODE_ACTIVE = re.compile(
        r"Bỏ chọn Hình ảnh|Remove Image|Deselect Image", re.IGNORECASE)
    _SEND_NAME = re.compile(r"Gửi tin nhắn|Send message|Submit", re.IGNORECASE)
    _STOP_NAME = re.compile(
        r"Ngừng tạo câu trả lời|Stop response|Stop generating", re.IGNORECASE)
    _DOWNLOAD_SELECTOR = ", ".join((
        "button[aria-label*='Tải hình ảnh có kích thước đầy đủ' i]",
        "button[aria-label*='Tải xuống hình ảnh' i]",
        "button[aria-label*='Tải hình ảnh' i]",
        "button[aria-label*='Tải xuống' i]",
        "button[aria-label*='Download full size image' i]",
        "button[aria-label*='Download image' i]",
    ))
    _DOWNLOAD_NAME = re.compile(
        r"Tải hình ảnh có kích thước đầy đủ|Tải xuống hình ảnh|Tải hình ảnh|"
        r"Download full size image|Download image",
        re.IGNORECASE)
    _FAILURE_TEXT = (
        "i don't seem to have access to that content",
        "failed to generate",
        "something went wrong",
        "đã xảy ra lỗi",
        "không thể tạo",
        "yêu cầu tạo ảnh bị từ chối",
    )

    def __init__(self, profile_dir: str, channel: str, url: str,
                 timeout: float, logger: Callable, cancel_event=None):
        self.profile_dir = os.path.abspath(profile_dir)
        self.channel = str(channel or "msedge").strip()
        self.url = str(url or GEMINI_WEB_URL).strip()
        self.timeout = max(45.0, float(timeout or 240))
        self.logger = logger
        self.cancel_event = cancel_event
        self._pw = None
        self.context = None
        self.page = None
        self._downloads_dir = None
        self._image_urls: List[str] = []
        self._sniffed: List[bytes] = []
        self._sniffer_attached = False

    def _cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise InterruptedError("Đã huỷ tạo ảnh.")

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < end:
            self._cancelled()
            time.sleep(max(0.0, min(0.25, end - time.monotonic())))

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise GeminiBrowserError(
                "Chưa cài Playwright; hãy chạy install.bat trước.") from exc
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        self.logger("Mở Gemini bằng hồ sơ: %s" % self.profile_dir, "step")
        self._pw = sync_playwright().start()
        order = [self.channel] if self.channel else []
        order += [x for x in ("msedge", "chrome", None) if x not in order]
        last_error = None
        for browser_channel in order:
            for attempt in range(1, 3):
                self._cancelled()
                try:
                    if self._downloads_dir is None:
                        self._downloads_dir = Path(tempfile.mkdtemp(prefix="advn_gimg_"))
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
            raise GeminiBrowserError(
                "Không mở được hồ sơ Gemini. Hãy đóng cửa sổ đã mở bởi "
                "login_gemini.bat rồi chạy lại. (%s)" % brief[:180])

        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(30000)
        self._attach_image_sniffer()
        try:
            self.page.goto(self.url, wait_until="domcontentloaded", timeout=90000)
        except Exception:
            # Gemini là SPA; dù wait_until lỗi, ô nhập thường vẫn dựng được.
            pass
        box = _visible_role(self.page, "textbox", self._INPUT_NAME, timeout=45)
        if box is None:
            current = str(self.page.url or "")
            self.close()
            if "accounts.google.com" in current:
                detail = "hồ sơ chưa đăng nhập Google"
            else:
                detail = "không thấy ô nhập Gemini"
            raise GeminiBrowserError(
                "%s. Hãy chạy login_gemini.bat, đăng nhập xong rồi đóng "
                "toàn bộ cửa sổ đó trước khi chạy AutoDubVN." % detail.capitalize())
        self._enable_image_mode()
        return self

    def close(self) -> None:
        folder = self._downloads_dir
        self._downloads_dir = None
        self._sniffer_attached = False
        self._image_urls = []
        for closer in (getattr(self.context, "close", None),
                       getattr(self._pw, "stop", None)):
            try:
                if closer:
                    closer()
            except Exception:
                pass
        self.context = None
        self.page = None
        self._pw = None
        if folder:
            shutil.rmtree(folder, ignore_errors=True)

    def __exit__(self, _exc_type, _exc, _tb):
        self.close()
        return False

    def _enable_image_mode(self) -> None:
        active = _visible_role(
            self.page, "button", self._IMAGE_MODE_ACTIVE, timeout=2)
        if active is not None:
            return
        tools = _visible_role(self.page, "button", self._TOOLS_NAME, timeout=15)
        if tools is None:
            raise GeminiBrowserError("Không tìm thấy nút Công cụ của Gemini.")
        tools.click(timeout=10000)
        image_mode = _visible_role(
            self.page, "menuitemcheckbox", self._IMAGE_MODE_NAME, timeout=10)
        if image_mode is None:
            raise GeminiBrowserError(
                "Tài khoản Gemini này chưa hiện mục 'Tạo hình ảnh'.")
        image_mode.click(timeout=10000)
        if _visible_role(self.page, "button", self._IMAGE_MODE_ACTIVE,
                         timeout=10) is None:
            raise GeminiBrowserError("Gemini không bật được chế độ Tạo hình ảnh.")

    def _is_generating(self) -> bool:
        return _visible_role(self.page, "button", self._STOP_NAME, timeout=0.2) is not None

    def _failure_detail(self) -> str:
        try:
            text = (self.page.locator("main").inner_text(timeout=2000) or "").lower()
        except Exception:
            return ""
        for marker in self._FAILURE_TEXT:
            if marker in text:
                return marker
        return ""

    def open_clean_chat(self) -> None:
        """Dừng lượt cũ và mở chat sạch nhưng giữ nguyên phiên đăng nhập."""
        self._cancelled()
        try:
            stop = _visible_role(self.page, "button", self._STOP_NAME, timeout=2)
            if stop is not None:
                stop.click(timeout=5000)
        except Exception:
            pass
        try:
            self.page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        box = _visible_role(self.page, "textbox", self._INPUT_NAME, timeout=35)
        if box is None:
            raise GeminiBrowserError(
                "Gemini không phục hồi được ô nhập sau khi mở chat sạch.")
        self._enable_image_mode()

    def recover_after_failure(self, scene_index: int, reason: Exception) -> None:
        """Dừng lượt Gemini bị kẹt và mở chat sạch trước khi thử lại."""
        self.logger(
            "Khôi phục Gemini sau lỗi cảnh %03d; mở cuộc trò chuyện mới trước khi thử lại."
            % int(scene_index), "info")
        self.open_clean_chat()

    _SCENE_IMAGES = "img"

    def _attach_image_sniffer(self) -> None:
        """Chỉ ghi URL ảnh. Không gọi response.body() — Playwright sync dễ treo."""
        if self._sniffer_attached or self.page is None:
            return

        def on_response(response):
            try:
                if not _maybe_image_response(response):
                    return
                url = str(getattr(response, "url", "") or "")
                if url and url not in self._image_urls:
                    self._image_urls.append(url)
            except Exception:
                pass

        self.page.on("response", on_response)
        self._sniffer_attached = True

    def _drain_image_responses(self) -> None:
        if self.page is None or not self._image_urls:
            return
        urls = [url for url in self._image_urls if url and not url.startswith("blob:")]
        self._image_urls = []
        for url in urls:
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
                continue
            if not encoded or "," not in str(encoded):
                continue
            try:
                payload = base64.b64decode(str(encoded).split(",", 1)[1], validate=True)
            except Exception:
                continue
            if _payload_usable(payload):
                self._sniffed.append(payload)

    def _best_sniffed(self, since: int = 0) -> Optional[bytes]:
        self._drain_image_responses()
        candidates = [item for item in self._sniffed[since:] if _payload_usable(item)]
        return max(candidates, key=len) if candidates else None

    def _ghi_url_checkpoint(self, pending: Dict, pending_path: Path,
                            tries: int = 3) -> None:
        """Ghi URL chat ngay sau khi gửi; vòng chờ ảnh vẫn cập nhật nếu chưa có."""
        for index in range(max(1, int(tries))):
            current_url = str(self.page.url or "")
            if re.match(r"https://gemini\.google\.com/app/[^/?#]+", current_url):
                if current_url != pending.get("url"):
                    pending["url"] = current_url
                    _write_json(pending_path, pending)
                return
            if index + 1 < tries:
                self._sleep(0.25)

    def _pickup_download(self, since: float) -> Optional[bytes]:
        """Nhặt file Chrome vừa thả vào downloads_path khi expect_download trượt."""
        folder = self._downloads_dir
        if not folder:
            return None
        folder = Path(folder)
        if not folder.is_dir():
            return None
        newest = None
        newest_mtime = float(since or 0) - 0.2
        for path in folder.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < newest_mtime or stat.st_size < _MIN_SCENE_BYTES:
                continue
            data = path.read_bytes()
            if _payload_usable(data):
                newest, newest_mtime = data, stat.st_mtime
        return newest

    def _visible_media_keys(self) -> List[str]:
        try:
            keys = self.page.evaluate("""() => {
                const root = document.querySelector('main, [role="main"]') || document;
                const out = [];
                const walk = (node) => {
                    if (!node || !node.querySelectorAll) return;
                    for (const img of node.querySelectorAll('img')) {
                        const src = img.currentSrc || img.src;
                        if (src) out.push(src);
                    }
                    for (const canvas of node.querySelectorAll('canvas')) {
                        const box = canvas.getBoundingClientRect();
                        out.push('canvas:' + canvas.width + 'x' + canvas.height + ':'
                                 + Math.round(box.left) + ':' + Math.round(box.top));
                    }
                    for (const el of node.querySelectorAll('*')) {
                        if (el.shadowRoot) walk(el.shadowRoot);
                    }
                };
                walk(root);
                return out;
            }""")
            return [str(item) for item in (keys or []) if item]
        except Exception:
            return []

    def _new_scene_image(self, previous):
        # Một lần chạy JS đồng bộ: không count rồi nth trên DOM đang thay đổi.
        # ElementHandle giữ đúng node ảnh; vị trí của icon/thumbnail không liên quan.
        handle = self.page.evaluate_handle("""previous => {
            const seen = new Set(previous);
            const root = document.querySelector('main, [role="main"]') || document;
            const shown = el => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width >= 128 && rect.height >= 128
                    && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const skipChrome = el => el.closest(
                'nav, aside, [role="navigation"], [role="complementary"]');
            const collect = (node, acc) => {
                if (!node || !node.querySelectorAll) return;
                acc.push(...node.querySelectorAll('img, canvas'));
                for (const el of node.querySelectorAll('*')) {
                    if (el.shadowRoot) collect(el.shadowRoot, acc);
                }
            };
            const media = [];
            collect(root, media);
            for (const el of media.reverse()) {
                if (skipChrome(el) || !shown(el)) continue;
                if (el.tagName === 'IMG') {
                    const src = el.currentSrc || el.src;
                    if (!src || seen.has(src) || !el.complete) continue;
                    if (el.naturalWidth < 256 || el.naturalHeight < 256) continue;
                    return el;
                }
                const key = 'canvas:' + el.width + 'x' + el.height + ':'
                    + Math.round(el.getBoundingClientRect().left) + ':'
                    + Math.round(el.getBoundingClientRect().top);
                if (seen.has(key) || el.width < 256 || el.height < 256) continue;
                return el;
            }
            return null;
        }""", list(previous))
        element = handle.as_element()
        if element is None:
            handle.dispose()
        return element

    def _image_download_button(self, image):
        handle = image.evaluate_handle("""(img, selector) => {
            const labelRe = /tải hình|tải xuống|download (full|image)/i;
            const visibleBtn = (root) => {
                if (!root || !root.querySelectorAll) return null;
                for (const btn of root.querySelectorAll('button, [role="button"]')) {
                    const text = ((btn.getAttribute('aria-label') || '') + ' '
                                  + (btn.innerText || '')).trim();
                    if (!labelRe.test(text)) continue;
                    const box = btn.getBoundingClientRect();
                    if (box.width > 4 && box.height > 4) return btn;
                }
                return null;
            };
            for (let node = img.parentElement; node && node !== document.body; node = node.parentElement) {
                const hit = visibleBtn(node);
                if (hit) return hit;
                const buttons = [...node.querySelectorAll(selector)];
                if (buttons.length === 1) return buttons[0];
                if (buttons.length > 1 || node.tagName === 'MODEL-RESPONSE') break;
            }
            return visibleBtn(document.querySelector('main') || document);
        }""", self._DOWNLOAD_SELECTOR)
        element = handle.as_element()
        if element is None:
            handle.dispose()
        return element

    def _cache_scene_image(self, image) -> Optional[bytes]:
        """Đọc blob/URL/canvas ngay trong trang; không lệ thuộc nút Tải."""
        try:
            encoded = image.evaluate("""async el => {
                const toData = blob => new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(reader.result);
                    reader.onerror = reject;
                    reader.readAsDataURL(blob);
                });
                if (el.tagName === 'CANVAS') {
                    try { return el.toDataURL('image/png'); } catch (e) { return ''; }
                }
                const urls = [];
                const src = el.currentSrc || el.src || '';
                if (src) urls.push(src);
                for (const part of (el.getAttribute('srcset') || '').split(',')) {
                    const url = part.trim().split(/\\s+/)[0];
                    if (url) urls.push(url);
                }
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), 15000);
                try {
                    for (const url of urls) {
                        try {
                            const response = await fetch(url, {
                                credentials: 'include', signal: controller.signal});
                            if (response.ok) return await toData(await response.blob());
                        } catch (_) {}
                    }
                } finally { clearTimeout(timer); }
                try {
                    const canvas = document.createElement('canvas');
                    canvas.width = el.naturalWidth || el.width || 0;
                    canvas.height = el.naturalHeight || el.height || 0;
                    if (canvas.width < 8 || canvas.height < 8) return '';
                    canvas.getContext('2d').drawImage(el, 0, 0);
                    return canvas.toDataURL('image/png');
                } catch (_) { return ''; }
            }""")
            if not encoded or "," not in str(encoded):
                return None
            payload = base64.b64decode(str(encoded).split(",", 1)[1], validate=True)
            return payload if _payload_usable(payload) else None
        except InterruptedError:
            raise
        except Exception:
            return None

    def _save_scene_bytes(self, payload, scene_index, images_dir):
        from PIL import Image
        self._cancelled()
        images_dir.mkdir(parents=True, exist_ok=True)
        dest = images_dir / ("scene_%03d.png" % scene_index)
        temp = images_dir / ("._scene_%03d.part" % scene_index)
        try:
            with Image.open(io.BytesIO(payload)) as img:
                img.load()
                img.save(temp, format="PNG")
            self._cancelled()
            os.replace(temp, dest)
            return dest
        finally:
            temp.unlink(missing_ok=True)

    def _try_save_payload(self, payload, scene_index, images_dir):
        if not _payload_usable(payload):
            return None
        return self._save_scene_bytes(payload, scene_index, images_dir)

    def _download_scene(self, button, scene_index: int, images_dir: Path, image=None,
                        sniff_from: int = 0, started_at: float = 0.0) -> Path:
        # Ưu tiên byte đã có (mạng / blob / thư mục tải). Nút Tải chỉ là dự phòng.
        last_error = None
        cached = self._cache_scene_image(image) if image is not None else None
        for payload, note in (
                (cached, ""),
                (self._best_sniffed(sniff_from),
                 "Đã lấy ảnh cảnh %03d từ phản hồi mạng, không cần nút Tải."),
                (self._pickup_download(started_at),
                 "Đã nhặt ảnh cảnh %03d từ thư mục tải của trình duyệt."),
        ):
            self._cancelled()
            try:
                dest = self._try_save_payload(payload, scene_index, images_dir)
                if dest is not None:
                    if note:
                        self.logger(note % scene_index, "ok")
                    return dest
            except InterruptedError:
                raise
            except Exception as exc:
                last_error = exc

        for attempt in range(2):
            self._cancelled()
            if image is not None:
                try:
                    shot = image.screenshot(type="png", timeout=15000)
                    dest = self._try_save_payload(shot, scene_index, images_dir)
                    if dest is not None:
                        self.logger("Đã vớt ảnh đang hiện của cảnh %03d." % scene_index, "warn")
                        return dest
                except InterruptedError:
                    raise
                except Exception as exc:
                    last_error = exc
                try:
                    box = image.bounding_box()
                    if box and min(box.get("width") or 0, box.get("height") or 0) >= 128:
                        clip = {key: box[key] for key in ("x", "y", "width", "height")}
                        shot = self.page.screenshot(type="png", clip=clip, timeout=15000)
                        dest = self._try_save_payload(shot, scene_index, images_dir)
                        if dest is not None:
                            self.logger("Đã chụp vùng ảnh cảnh %03d trên trang." % scene_index, "warn")
                            return dest
                except InterruptedError:
                    raise
                except Exception as exc:
                    last_error = exc
            if attempt == 0:
                self._sleep(.5)

        if button is not None:
            temp = images_dir / ("._scene_%03d.download.part" % scene_index)
            try:
                images_dir.mkdir(parents=True, exist_ok=True)
                with self.page.expect_download(timeout=45000) as info:
                    button.click(timeout=15000)
                info.value.save_as(str(temp))
                dest = self._try_save_payload(temp.read_bytes(), scene_index, images_dir)
                if dest is not None:
                    return dest
            except InterruptedError:
                raise
            except Exception as exc:
                last_error = exc
            finally:
                temp.unlink(missing_ok=True)
            pickup = self._pickup_download(started_at)
            try:
                dest = self._try_save_payload(pickup, scene_index, images_dir)
                if dest is not None:
                    self.logger("Đã nhặt ảnh cảnh %03d sau khi bấm Tải." % scene_index, "ok")
                    return dest
            except InterruptedError:
                raise
            except Exception as exc:
                last_error = exc
        raise GeminiSceneSaveError(
            "Ảnh cảnh %03d đã hiện nhưng chưa lưu được; dừng, không tạo lại cảnh: %s"
            % (scene_index, str(last_error)[:180])) from last_error

    def generate_scene(self, scene_index: int, prompt: str,
                       images_dir: Path, aspect: str,
                       heartbeat: Optional[Callable[[str], None]] = None) -> Path:
        """Gửi một lần; đọc ảnh cùng lượt, kể cả khi mở lại app."""
        self._cancelled()
        request = (
            "Create exactly one image for this story scene. Preserve the same "
            "characters, clothing, locations, cinematic color palette and visual "
            "style. Treat all character details in the scene prompt as canonical, "
            "including in a fresh chat. Use aspect ratio %s. No text, "
            "caption, logo, watermark or collage.\n\nSCENE %03d:\n%s"
            % ("9:16" if str(aspect) == "9:16" else "16:9",
               int(scene_index), str(prompt or "").strip()))
        pending_path = images_dir / (".pending_scene_%03d.json" % scene_index)
        fingerprint = hashlib.sha256((str(aspect) + "\n" + prompt).encode("utf-8")).hexdigest()
        self._attach_image_sniffer()
        sniff_from = len(self._sniffed)
        started_at = time.time()
        pending = None
        if pending_path.is_file():
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
            if pending.get("fingerprint") != fingerprint:
                raise GeminiSceneSaveError("Cảnh đang chờ thuộc prompt khác; không gửi đè.")
            saved_image = images_dir / ("scene_%03d.png" % scene_index)
            if saved_image.is_file():
                from PIL import Image
                with Image.open(saved_image) as img:
                    img.verify()
                return saved_image
            conversation = str(pending.get("url") or "")
            current_url = str(self.page.url or "")
            if not re.match(r"https://gemini\.google\.com/app/[^/?#]+", conversation):
                if re.match(r"https://gemini\.google\.com/app/[^/?#]+", current_url):
                    conversation = current_url
                    pending["url"] = current_url
                    _write_json(pending_path, pending)
                    self.logger(
                        "Checkpoint cảnh %03d thiếu URL; dùng cuộc trò chuyện đang mở."
                        % scene_index, "warn")
                else:
                    self.logger(
                        "Checkpoint cảnh %03d thiếu URL; đọc ảnh trên trang hiện tại, "
                        "không gửi lại prompt." % scene_index, "warn")
                    conversation = ""
            if conversation:
                self.logger("Mở lại cuộc trò chuyện để lấy ảnh cảnh %03d; không gửi prompt mới." % scene_index, "info")
                self.page.goto(conversation, wait_until="domcontentloaded", timeout=60000)
            previous_images = set(pending.get("previous_images") or [])
        else:
            self._enable_image_mode()
            box = _visible_role(self.page, "textbox", self._INPUT_NAME, timeout=20)
            if box is None:
                raise GeminiBrowserError("Không còn thấy ô nhập Gemini.")
            previous_images = set(self._visible_media_keys())
            box.fill(request, timeout=20000)
            pending = {"fingerprint": fingerprint, "url": "", "previous_images": list(previous_images)}
            _write_json(pending_path, pending)
            send = _visible_role(self.page, "button", self._SEND_NAME, timeout=10)
            if send is not None:
                send.click(timeout=10000)
            else:
                box.press("Enter", timeout=10000)
            self._ghi_url_checkpoint(pending, pending_path)

        sent_at = time.monotonic()
        deadline = sent_at + self.timeout
        last_image_error = None
        next_heartbeat = sent_at
        next_log = sent_at + 30.0
        while time.monotonic() < deadline:
            self._cancelled()
            current_url = str(self.page.url or "")
            if current_url != pending.get("url") and re.match(r"https://gemini\.google\.com/app/[^/?#]+", current_url):
                pending["url"] = current_url
                _write_json(pending_path, pending)
            now = time.monotonic()
            elapsed = int(now - sent_at)
            if now >= next_heartbeat:
                message = ("Đang chờ Gemini tạo cảnh %03d · %d/%d giây"
                           % (scene_index, elapsed, int(self.timeout)))
                if heartbeat:
                    heartbeat(message)
                next_heartbeat = now + 8.0
            if now >= next_log:
                self.logger("Cảnh %03d vẫn đang xử lý (%d/%d giây)…" %
                            (scene_index, elapsed, int(self.timeout)), "info")
                next_log = now + 30.0
            try:
                current_image = self._new_scene_image(previous_images)
            except Exception as exc:
                # DOM thay đổi khi dựng ảnh; tiếp tục đọc cùng lượt đã gửi.
                last_image_error = exc
                self._sleep(.5)
                continue
            sniffed = self._best_sniffed(sniff_from)
            pickup = self._pickup_download(started_at)
            if current_image is None:
                for payload, note in (
                        (sniffed, "Đã lấy ảnh cảnh %03d từ phản hồi mạng, không cần nút Tải."),
                        (pickup, "Đã nhặt ảnh cảnh %03d từ thư mục tải của trình duyệt."),
                ):
                    try:
                        dest = self._try_save_payload(payload, scene_index, images_dir)
                        if dest is not None:
                            self.logger(note % scene_index, "ok")
                            return dest
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        last_image_error = exc
            else:
                button = None
                try:
                    try:
                        current_image.scroll_into_view_if_needed(timeout=2000)
                        current_image.hover(timeout=2000)
                    except Exception:
                        pass
                    try:
                        button = self._image_download_button(current_image)
                    except Exception:
                        pass
                    if button is None:
                        button = _visible_role(
                            self.page, "button", self._DOWNLOAD_NAME, timeout=1.2)
                    result = self._download_scene(
                        button, scene_index, images_dir, current_image,
                        sniff_from=sniff_from, started_at=started_at)
                    return result
                except InterruptedError:
                    raise
                except Exception as exc:
                    last_image_error = exc
                    # Node bị thay thế: đọc lại ảnh cùng lượt, không mở chat mới.
                finally:
                    for handle in (current_image, button):
                        if handle is not None:
                            try:
                                handle.dispose()
                            except Exception:
                                pass
            # Nút Stop có thể đổi nhãn/biến mất trước khi ảnh decode xong.
            # Chờ hết ngân sách ảnh, không kết luận thất bại sau 3/45 giây.
            self._sleep(0.8)
        for payload, note in (
                (self._best_sniffed(sniff_from),
                 "Hết giờ chờ nút Tải; đã lưu ảnh cảnh %03d từ mạng."),
                (self._pickup_download(started_at),
                 "Hết giờ chờ nút Tải; đã nhặt ảnh cảnh %03d trong thư mục tải."),
        ):
            try:
                dest = self._try_save_payload(payload, scene_index, images_dir)
                if dest is not None:
                    self.logger(note % scene_index, "warn")
                    return dest
            except InterruptedError:
                raise
            except Exception as exc:
                last_image_error = exc
        raise GeminiSceneSaveError(
            "Đã gửi cảnh %03d; hết %d giây đọc/lưu ảnh. Không tự tạo lại cảnh. %s"
            % (scene_index, int(self.timeout), str(last_image_error or "" )[:180]))


def _register_generated_scene_file(path: str | os.PathLike, scene_index: int,
                                   image_path: str | os.PathLike) -> Dict:
    """Gắn file Gemini web vừa tải vào đúng cảnh và cập nhật manifest."""
    manifest_path = _manifest_path(path)
    manifest = load_pack(manifest_path)
    scenes = list(manifest.get("scenes") or [])
    if scene_index < 1 or scene_index > len(scenes):
        raise IndexError("Chỉ số cảnh nằm ngoài manifest.")
    source = Path(image_path).resolve()
    if not source.is_file() or source.suffix.lower() not in IMAGE_EXTENSIONS:
        raise GeminiBrowserError("File ảnh tải về không hợp lệ: %s" % source)
    scenes[scene_index - 1].update({
        "expected_file": str(source.relative_to(manifest_path.parent)).replace("\\", "/"),
        "image_path": str(source),
        "status": "ready",
    })
    manifest["scenes"] = scenes
    manifest["ready_count"] = sum(1 for item in scenes
                                  if item.get("status") == "ready")
    manifest["status"] = ("ready" if manifest["ready_count"] == len(scenes)
                          else "generating_images")
    manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest.pop("manifest_path", None)
    manifest.pop("pack_dir", None)
    _write_json(manifest_path, manifest)
    return load_pack(manifest_path)


def generate_images_gemini_browser(
        path: str | os.PathLike, profile_dir: str,
        channel: str = "msedge", url: str = GEMINI_WEB_URL,
        timeout: float = 240.0, max_retries: int = 3,
        request_gap: float = 1.5, max_session_restarts: int = 5,
        fresh_chat_every: int = 2, restart_cooldown: float = 10.0,
        logger: Optional[Callable] = None,
        progress: Optional[Callable] = None, cancel_event=None) -> Dict:
    """Tạo ảnh bằng Gemini web và tự phục hồi trình duyệt khi phiên bị chai.

    Mỗi ảnh được ghi manifest ngay. Lỗi mở phiên trước khi gửi được thử lại;
    sau khi gửi, checkpoint giữ URL cuộc trò chuyện để đọc lại cùng lượt,
    không dùng việc mở chat mới/gửi lại prompt để khắc phục lỗi đọc ảnh.
    """
    logger = logger or (lambda _msg, _kind="info": None)
    progress = progress or (lambda _done, _total, _message="": None)
    retries = max(1, int(max_retries or 1))
    allowed_restarts = max(0, int(max_session_restarts or 0))
    fresh_every = max(0, int(fresh_chat_every or 0))
    restart_count = 0

    def cancelable_wait(seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Đã huỷ tạo ảnh.")
            time.sleep(max(0.0, min(.25, deadline - time.monotonic())))

    while True:
        manifest = load_pack(path)
        scenes = list(manifest.get("scenes") or [])
        missing = [scene for scene in scenes
                   if scene.get("status") != "ready" and
                   str(scene.get("prompt") or "").strip()]
        if not missing:
            if scenes and int(manifest.get("ready_count", 0) or 0) == len(scenes):
                return manifest
            raise GeminiBrowserError("Gói ảnh chưa có prompt từng cảnh để tự sinh.")
        total_scenes = len(scenes)
        try:
            with _GeminiWebImageSession(
                    profile_dir, channel, url, timeout, logger,
                    cancel_event=cancel_event) as session:
                made_in_chat = 0
                for scene in missing:
                    index = int(scene.get("index") or 1)
                    if fresh_every and made_in_chat >= fresh_every:
                        refresh = getattr(session, "open_clean_chat", None)
                        if callable(refresh):
                            logger("Đã tạo %d ảnh; chủ động mở chat Gemini sạch để tránh phiên bị chai."
                                   % made_in_chat, "info")
                            refresh()
                        made_in_chat = 0
                    last_error = None
                    for attempt in range(1, retries + 1):
                        try:
                            ready_now = int(manifest.get("ready_count", 0) or 0)
                            downloaded = session.generate_scene(
                                index, str(scene.get("prompt") or ""),
                                Path(manifest["pack_dir"]) / "images",
                                str(manifest.get("aspect") or "16:9"),
                                heartbeat=lambda message, completed=ready_now: progress(
                                    completed, total_scenes, message))
                            manifest = _register_generated_scene_file(
                                path, index, downloaded)
                            (Path(manifest["pack_dir"]) / "images" /
                             (".pending_scene_%03d.json" % index)).unlink(missing_ok=True)
                            ready_now = int(manifest.get("ready_count", 0) or 0)
                            message = ("Đã tải cảnh %03d · tổng %d/%d"
                                       % (index, ready_now, total_scenes))
                            progress(ready_now, total_scenes, message)
                            logger("Đã tạo và tải ảnh cảnh %03d bằng Gemini web." % index,
                                   "ok")
                            last_error = None
                            made_in_chat += 1
                            break
                        except (InterruptedError, GeminiSceneSaveError):
                            raise
                        except Exception as exc:
                            pending_file = (Path(manifest["pack_dir"]) / "images" /
                                            (".pending_scene_%03d.json" % index))
                            if pending_file.is_file():
                                raise GeminiSceneSaveError(
                                    "Cảnh %03d đã gửi; giữ lượt để lấy ảnh, không tạo lại: %s"
                                    % (index, str(exc)[:180])) from exc
                            last_error = exc
                            logger("Cảnh %03d lỗi lượt %d/%d: %s" %
                                   (index, attempt, retries, str(exc)[:180]), "warn")
                            if attempt < retries:
                                recover = getattr(session, "recover_after_failure", None)
                                if callable(recover):
                                    recover(index, exc)
                                session._sleep(min(12.0, 2.5 * attempt))
                    if last_error is not None:
                        raise GeminiBrowserError(str(last_error)) from last_error
                    if request_gap > 0:
                        session._sleep(request_gap)
            return load_pack(path)
        except (InterruptedError, GeminiSceneSaveError):
            raise
        except Exception as exc:
            if restart_count >= allowed_restarts:
                raise GeminiBrowserError(
                    "Gemini vẫn lỗi sau %d lần tự khởi động lại: %s" %
                    (restart_count, str(exc))) from exc
            restart_count += 1
            delay = min(45.0, max(0.0, float(restart_cooldown)) * restart_count)
            current = load_pack(path)
            ready_now = int(current.get("ready_count", 0) or 0)
            message = ("Gemini bị kẹt; app tự mở lại phiên %d/%d sau %.0f giây "
                       "và tiếp tục từ %d/%d ảnh…" %
                       (restart_count, allowed_restarts, delay,
                        ready_now, total_scenes))
            logger(message, "warn")
            progress(ready_now, total_scenes, message)
            cancelable_wait(delay)
