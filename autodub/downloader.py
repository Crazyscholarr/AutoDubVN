"""Tải video từ link bằng bộ tải Bilibili trực tiếp hoặc yt-dlp.

- Lấy đúng LUỒNG GỐC chất lượng cao rồi ghép video+audio -> mp4. Bilibili KHÔNG
  chèn watermark vào luồng tải, nên bản tải về sạch (không logo do trang thêm).
- Bilibili công khai ưu tiên API/CDN trực tiếp; YouTube, nguồn khác và trường
  hợp API lỗi tự dùng yt-dlp. Bản nét bị khóa có thể cần cookies (xem config).
- Nếu video có LOGO CHÁY CỨNG ở góc (do người đăng chèn) thì dùng tùy chọn
  video.delogo trong config để xoá mờ đi khi render.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Callable, Dict, List, Optional, Tuple

from .utils import log, run, which, ffprobe_video_size, ffprobe_has_stream, ffprobe_is_blank_video


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_PROGRESS_PREFIX = "__AUTODUB_PROGRESS__|"
_PATH_PREFIX = "__AUTODUB_FILE__|"


def _number(value) -> Optional[float]:
    raw = _ANSI_RE.sub("", str(value or "")).strip()
    if not raw or raw.lower() in {"na", "none", "unknown", "n/a"}:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _human_bytes(value: Optional[float]) -> str:
    if value is None or value < 0:
        return "?"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            digits = 0 if unit == "B" else 1
            return f"{amount:.{digits}f} {unit}"
        amount /= 1024.0
    return f"{amount:.1f} TiB"


def _human_eta(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "?"
    total = int(round(seconds))
    hours, remain = divmod(total, 3600)
    minutes, secs = divmod(remain, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _parse_progress_line(line: str) -> Optional[Dict]:
    """Đọc một dòng ``--progress-template`` của yt-dlp thành trạng thái UI."""
    plain = _ANSI_RE.sub("", str(line or "")).strip()
    if not plain.startswith(_PROGRESS_PREFIX):
        return None
    fields = plain[len(_PROGRESS_PREFIX):].split("|", 8)
    if len(fields) < 9:
        return None
    ident, format_id, status, percent_raw, downloaded_raw, total_raw, estimate_raw, speed_raw, eta_raw = fields
    downloaded = _number(downloaded_raw)
    total = _number(total_raw) or _number(estimate_raw)
    speed = _number(speed_raw)
    eta = _number(eta_raw)
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", percent_raw)
    percent = float(match.group(1)) if match else None
    if percent is None and downloaded is not None and total:
        percent = min(100.0, downloaded * 100.0 / total)

    stream = f" · luồng {format_id}" if format_id and format_id != "NA" else ""
    if str(status).lower() == "finished":
        text = f"Tải {ident}{stream}: 100% · đã nhận xong, đang ghép/kiểm tra"
    else:
        pieces = [f"Tải {ident}{stream}"]
        if percent is not None:
            pieces.append(f"{percent:.1f}%")
        if downloaded is not None:
            amount = _human_bytes(downloaded)
            if total is not None:
                amount += "/" + _human_bytes(total)
            pieces.append(amount)
        if speed is not None and speed > 0:
            pieces.append(_human_bytes(speed) + "/s")
        if eta is not None:
            pieces.append("còn " + _human_eta(eta))
        text = ": ".join(pieces[:2])
        if len(pieces) > 2:
            text += " · " + " · ".join(pieces[2:])
    return {
        "id": ident, "format_id": format_id, "status": status,
        "percent": percent, "downloaded": downloaded, "total": total,
        "speed": speed, "eta": eta, "text": text,
    }


def extract_url(s: str) -> str:
    """Return the first http(s) URL from pasted text/log output."""
    urls = [m.group(0).rstrip(".,;)]}") for m in _URL_RE.finditer(str(s or ""))]
    if not urls:
        return ""
    for url in urls:
        if not re.match(r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?(?:/|$)", url, re.I):
            return url
    return urls[0]


def is_url(s: str) -> bool:
    return bool(extract_url(s))


def _ytdlp_cmd() -> list:
    # Ưu tiên gọi dạng module (python -m yt_dlp) để KHÔNG đụng file yt-dlp.exe
    # - tránh bị Device Guard/WDAC của tổ chức chặn exe.
    import importlib.util
    if importlib.util.find_spec("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    if which("yt-dlp"):
        return ["yt-dlp"]
    raise RuntimeError("Chưa cài yt-dlp. Chạy: python -m pip install yt-dlp")


# [vcodec!=none] ở nhánh cuối CỐ Ý để chặn trường hợp trang (đặc biệt Bilibili)
# chỉ trả về luồng ÂM THANH cho độ phân giải yêu cầu (thường vì video đó cần
# ĐĂNG NHẬP mới xem được >360p) -> nếu không còn ứng viên nào có hình, yt-dlp sẽ
# BÁO LỖI NGAY thay vì âm thầm tải một file chỉ có tiếng.
_QUALITY = {
    "best": "bv*+ba/b[vcodec!=none]",
    "1080": "bv*[height<=1080]+ba/b[height<=1080][vcodec!=none]",
    "720": "bv*[height<=720]+ba/b[height<=720][vcodec!=none]",
    "480": "bv*[height<=480]+ba/b[height<=480][vcodec!=none]",
    "360": "bv*[height<=360]+ba/b[height<=360][vcodec!=none]",
}


def _positive_int(value, default: int, lo: int = 1, hi: int = 32) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _download_speed_options(concurrent_fragments=8,
                            external_downloader: Optional[str] = "auto") -> Tuple[List[str], str]:
    """Tuỳ chọn tăng tốc cho yt-dlp, tách riêng để test không phải tải thật."""
    n_frag = _positive_int(concurrent_fragments, 8, 1, 32)
    cmd = [
        "--continue",
        "--part",
        "--no-mtime",
        "--retries", "10",
        "--fragment-retries", "10",
        # Bilibili/CDN thường trả 503 trong vài giây khi có nhiều request
        # cùng lúc.  Chờ tăng dần giúp yt-dlp tự hồi phục thay vì dồn 10 lần
        # retry ngay lập tức rồi bị CDN chặn tiếp.
        "--retry-sleep", "http:linear=1::3",
        "--retry-sleep", "fragment:linear=1::2",
        "--concurrent-fragments", str(n_frag),
    ]
    notes = [f"{n_frag} fragment song song"]

    choice = str(external_downloader or "").strip()
    choice_l = choice.lower()
    if choice_l in ("", "none", "false", "off", "no"):
        return cmd, ", ".join(notes)

    # "auto" ưu tiên downloader HTTP/DASH nội bộ của yt-dlp. Đo thực tế với
    # cùng video cho thấy aria2c chậm hơn nhiều trên máy chủ video phân mảnh,
    # và ghép -N8 với -x8 còn tạo quá nhiều kết nối. Có thể ép aria2c bằng
    # `external_downloader: aria2c` hoặc đường dẫn đầy đủ.
    if choice_l == "auto":
        return cmd, ", ".join(notes + ["yt-dlp downloader nội bộ"])
    downloader = which(choice) or choice

    if downloader:
        cmd += ["--downloader", downloader]
        base = os.path.basename(str(downloader)).lower()
        if "aria2c" in base:
            conn = _positive_int(n_frag, 8, 1, 16)
            # Keep aria2c's retry/timeout/cache defaults in one project-local
            # config file so reinstalling the binary does not reset them.
            conf_path = os.path.abspath(os.path.join(
                os.path.dirname(__file__), "..", "tools", "aria2", "aria2.conf"))
            conf_arg = f"--conf-path={conf_path}" if os.path.isfile(conf_path) else ""
            cmd += [
                "--downloader-args",
                "aria2c:" + " ".join(filter(None, [
                    f"-x {conn}", f"-s {conn}", "-k 1M",
                    "--file-allocation=none", conf_arg,
                ])),
            ]
            notes.append(f"aria2c {conn} kết nối")
        else:
            notes.append(f"downloader ngoài: {choice}")
    return cmd, ", ".join(notes)


def _external_downloader_failed(exc: Exception) -> bool:
    """Nhận diện lỗi do downloader ngoài để có thể lui về yt-dlp nội bộ.

    aria2c đôi khi từ chối một URL DASH tạm thời (đặc biệt Bilibili) trong khi
    chính downloader HTTP của yt-dlp vẫn tải được.  Không thử lại với các lỗi
    khác như đăng nhập/format để tránh chạy cùng một lệnh vô ích hai lần.
    """
    message = str(exc or "").lower()
    return any(token in message for token in (
        "aria2c exited with code",
        "external downloader returned an error",
        "external downloader command failed",
    ))


def _browser_cookie_failed(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return ("could not copy" in message and "cookie database" in message) or (
        "failed to decrypt with dpapi" in message)


def _normalise_cookie_browser(value) -> Optional[str]:
    """Chuẩn hoá tên browser/profile cho ``--cookies-from-browser``.

    yt-dlp nhận ``edge:Default``. Người dùng đôi khi dán nguyên shortcut
    Edge (``msedge.exe --profile-directory=Default``), nên tách lại thành
    browser spec hợp lệ thay vì truyền cả chuỗi lệnh vào yt-dlp.
    """
    raw = str(value or "").strip().strip('"').strip("'")
    if not raw:
        return None
    low = raw.lower()
    browser = None
    if "msedge" in low or low.startswith("edge") or "microsoft edge" in low:
        browser = "edge"
    elif "chrome" in low:
        browser = "chrome"
    elif "firefox" in low:
        browser = "firefox"
    if not browser:
        return raw
    profile_match = re.search(
        r"--profile-directory(?:=|\s+)[\"']?([^\s\"']+)", raw, re.I)
    profile = profile_match.group(1) if profile_match else None
    # Đã là dạng yt-dlp browser[:profile] thì giữ nguyên.
    if profile:
        return f"{browser}:{profile}"
    if re.fullmatch(r"(?:edge|chrome|firefox)(?::[^\s:]+)?", raw, re.I):
        return raw
    return browser


def _cookie_browser_label(value) -> str:
    spec = _normalise_cookie_browser(value) or ""
    browser = spec.split(":", 1)[0].lower()
    return {"edge": "Edge", "chrome": "Chrome", "firefox": "Firefox"}.get(
        browser, "trình duyệt")


def _transient_download_failed(exc: Exception) -> bool:
    """Nhận diện lỗi CDN có thể tự hết (503/416/429/đứt kết nối...).

    416 thường là mảnh ``.part`` cũ có Range không còn hợp lệ; 503/429 là
    CDN tạm thời từ chối. Bilibili cũng hay đóng kết nối giữa một file nhiều
    trăm MB/GB (``RemoteDisconnected``, ``IncompleteRead``); đó là lỗi mạng
    tạm thời, cần chạy lại để yt-dlp nối tiếp file ``.part``. Các lỗi
    format/đăng nhập không nằm trong nhóm này để tránh retry vô ích và làm
    người dùng phải chờ lâu.
    """
    message = str(exc or "").lower()
    return _range_download_failed(message) or _connection_download_failed(message) or any(token in message for token in (
        "http error 416",
        "requested range not satisfiable",
        "http error 503",
        "service unavailable",
        "http error 502",
        "bad gateway",
        "http error 500",
        "internal server error",
        "http error 429",
        "too many requests",
        "temporarily unavailable",
    ))


def _range_download_failed(exc) -> bool:
    """Lỗi Range cũ: cần bỏ ``.part`` để tránh lặp lại HTTP 416."""
    message = str(exc or "").lower()
    return "http error 416" in message or "requested range not satisfiable" in message


def _connection_download_failed(exc) -> bool:
    """Lỗi kết nối giữa chừng: giữ ``.part`` và tải tiếp, không xoá dữ liệu."""
    message = str(exc or "").lower()
    if re.search(r"\b\d+\s+bytes?\s+read,\s*\d+\s+more\s+expected\b", message):
        return True
    return any(token in message for token in (
        "remotedisconnected",
        "remote end closed connection",
        "connection aborted",
        "connection reset by peer",
        "connection reset",
        "incomplete read",
        "incompleteread",
        "read timed out",
        "timed out",
        "winerror 10054",
        "connection forcibly closed",
    ))


def _without_option_value(cmd: List[str], option: str) -> List[str]:
    """Bỏ một option dạng ``--name value`` mà không sửa list gốc."""
    out = list(cmd)
    while option in out:
        index = out.index(option)
        del out[index:index + 2]
    return out


def _without_flags(cmd: List[str], *flags: str) -> List[str]:
    """Bỏ các option dạng cờ (không có giá trị) mà không sửa list gốc."""
    remove = set(flags)
    return [item for item in cmd if item not in remove]


def _fresh_retry_options(speed_opts: List[str]) -> List[str]:
    """Tạo tuỳ chọn cho lần tải sạch sau lỗi Range/503.

    Không nối tiếp ``.part`` cũ và chỉ dùng một fragment song song để tránh
    CDN Bilibili tiếp tục trả Range 416. ``--force-overwrites`` cũng buộc
    yt-dlp bỏ cơ chế resume của file đích hiện có.
    """
    opts = _without_flags(speed_opts, "--continue", "--part")
    opts = _without_option_value(opts, "--concurrent-fragments")
    opts += [
        "--no-continue",
        "--no-part",
        "--force-overwrites",
        "--concurrent-fragments", "1",
    ]
    return opts


def _resume_retry_options(speed_opts: List[str]) -> List[str]:
    """Tải nối tiếp ``.part`` sau khi CDN đóng kết nối giữa chừng.

    Một lần tải lại nguyên file rất lãng phí với video nền dài. Giảm về một
    fragment và chia HTTP thành các đoạn 10 MiB để mỗi lần CDN ngắt chỉ phải
    lấy lại một đoạn nhỏ; ``--continue --part`` giữ toàn bộ dữ liệu đã nhận.
    """
    opts = _without_flags(speed_opts, "--no-continue", "--no-part", "--force-overwrites")
    opts = _without_option_value(opts, "--concurrent-fragments")
    opts = _without_option_value(opts, "--http-chunk-size")
    if "--continue" not in opts:
        opts.append("--continue")
    if "--part" not in opts:
        opts.append("--part")
    opts += [
        "--concurrent-fragments", "1",
        "--http-chunk-size", "10M",
    ]
    return opts


def _build_download_command(url: str, out_tmpl: str, fmt: str,
                            speed_opts: List[str],
                            cookies_from_browser: Optional[str] = None,
                            cookies_file: Optional[str] = None) -> List[str]:
    """Tạo lệnh yt-dlp; tách riêng để retry và unit test không tải mạng."""
    cmd = [
        *_ytdlp_cmd(),
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--windows-filenames",
        "-o", out_tmpl,
        *speed_opts,
        # Ép mỗi lần cập nhật nằm trên một dòng để backend đọc được ngay thay
        # vì giữ progress bar bằng ký tự \r. Template dùng số thô để Python tự
        # định dạng ổn định, không phụ thuộc ngôn ngữ/ANSI của yt-dlp.
        "--newline",
        "--progress",
        "--progress-delta", "1",
        "--progress-template",
        ("download:" + _PROGRESS_PREFIX +
         "%(info.id)s|%(info.format_id)s|%(progress.status)s|"
         "%(progress._percent_str)s|%(progress.downloaded_bytes)s|"
         "%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|"
         "%(progress.speed)s|%(progress.eta)s"),
        "--no-simulate",
        "--print", "after_move:" + _PATH_PREFIX + "%(filepath)s",
    ]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    if cookies_file:
        cmd += ["--cookies", cookies_file]
    cmd.append(url)
    return cmd


def _validate_download_file(path: str, cookie_hint: str,
                            progress_callback: Optional[Callable[[Dict], None]] = None) -> str:
    """Kiểm tra đầu ra chung cho cả bộ tải trực tiếp và yt-dlp."""
    path = os.path.abspath(path)
    if not path or not os.path.isfile(path):
        raise RuntimeError("Không tìm thấy file sau khi tải.")

    # Kiểm tra lại file THỰC SỰ có hình, không chỉ tin bộ tải báo thành công.
    w, h = ffprobe_video_size(path)
    if w == 0 or h == 0:
        raise RuntimeError(
            f"File tải về CHỈ CÓ TIẾNG, không có hình ({os.path.basename(path)}). "
            "Nguyên nhân thường gặp: Bilibili chỉ cho xem/tải hình ở độ phân giải "
            "yêu cầu khi đã ĐĂNG NHẬP. Cách sửa: {hint} Sau đó thử lại (xoá file "
            "lỗi này trong thư mục downloads trước khi chạy lại). Nếu vẫn lỗi, "
            "thử hạ download.quality xuống 480 hoặc cập nhật yt-dlp: "
            "python -m pip install -U yt-dlp".format(hint=cookie_hint)
        )
    if not ffprobe_has_stream(path, "a"):
        raise RuntimeError(
            f"File tải về CHỈ CÓ HÌNH, không có tiếng ({os.path.basename(path)}). "
            f"{cookie_hint} Sau đó xoá file lỗi trong thư mục downloads rồi tải lại."
        )

    log("Kiểm tra hình có bị đen (placeholder) không...", "info")
    if ffprobe_is_blank_video(path):
        raise RuntimeError(
            f"File tải về CÓ TIẾNG nhưng HÌNH TOÀN MÀU ĐEN ({os.path.basename(path)}). "
            "Đây là kiểu Bilibili trả 'hình giả' khi độ phân giải yêu cầu bị khoá sau "
            f"đăng nhập/VIP, dù luồng vẫn khai đúng kích thước. {cookie_hint} Sau đó "
            "xoá file lỗi trong thư mục downloads rồi tải lại. Nếu vẫn đen dù đã có "
            "cookies, thử hạ download.quality xuống 480."
        )

    log(f"Đã tải: {os.path.basename(path)}", "ok")
    if progress_callback:
        progress_callback({"status": "complete", "percent": 100.0,
                           "text": "Tải xong: " + os.path.basename(path),
                           "path": path})
    return path


def download_video(
    url: str,
    out_dir: str,
    quality: str = "best",
    cookies_from_browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
    concurrent_fragments=8,
    external_downloader: Optional[str] = "auto",
    progress_callback: Optional[Callable[[Dict], None]] = None,
) -> str:
    """Tải 1 video, trả về đường dẫn file mp4 đã lưu."""
    url = extract_url(url)
    if not url:
        raise RuntimeError("Không thấy URL hợp lệ. Hãy dán riêng link bắt đầu bằng http:// hoặc https://.")
    os.makedirs(out_dir, exist_ok=True)
    cookies_from_browser = _normalise_cookie_browser(cookies_from_browser)
    _cookie_hint = (
        "Kiểm tra link, hoặc bản nét cần đăng nhập: đặt "
        "download.cookies_from_browser: edge:Default (hoặc chrome/firefox - trình duyệt bạn "
        "ĐÃ đăng nhập Bilibili) trong config.yaml."
    )

    # Bilibili công khai đi qua API/CDN trực tiếp trước. Cách này không chạm DB
    # cookie đang bị khóa của Edge, thử nhiều mirror cho từng khối và nối tiếp
    # .part. Nếu Bilibili đổi API/chặn vùng, yt-dlp bên dưới vẫn là đường lui.
    from . import bilibili_direct
    if bilibili_direct.is_bilibili_url(url):
        log("Đang phân tích video bằng bộ tải Bilibili trực tiếp…", "step")
        direct_last = {"at": 0.0, "status": ""}

        def _on_direct_progress(info: Dict) -> None:
            import time
            now = time.monotonic()
            status = str(info.get("status") or "")
            percent = info.get("percent")
            important = (status != direct_last["status"] or
                         (percent is not None and float(percent) >= 97.0))
            if not important and now - direct_last["at"] < 1.0:
                return
            direct_last.update({"at": now, "status": status})
            text = str(info.get("text") or "Đang tải Bilibili…")
            log(text, "info")
            if progress_callback:
                progress_callback(info)

        try:
            direct_path, direct_qn, direct_kind = bilibili_direct.download_bilibili(
                url, out_dir, quality=str(quality), cookies_file=cookies_file,
                progress_callback=_on_direct_progress)
            log("Bilibili trực tiếp: %s · luồng %s · đã tự chọn CDN nhanh nhất."
                % (bilibili_direct.quality_label(direct_qn), direct_kind.upper()),
                "info")
            return _validate_download_file(
                direct_path, _cookie_hint, progress_callback)
        except Exception as direct_error:
            log("Bộ tải Bilibili trực tiếp chưa lấy được video (%s); "
                "tự chuyển sang yt-dlp…" % str(direct_error)[:220], "warn")

    fmt = _QUALITY.get(str(quality), _QUALITY["best"])
    out_tmpl = os.path.join(out_dir, "%(title).80s [%(id)s].%(ext)s")
    speed_opts, speed_note = _download_speed_options(concurrent_fragments, external_downloader)

    cmd = _build_download_command(
        url, out_tmpl, fmt, speed_opts,
        cookies_from_browser=cookies_from_browser,
        cookies_file=cookies_file)

    log("Đang tải video (yt-dlp)...", "step")
    log(f"Tăng tốc tải: {speed_note}.", "info")
    _cookie_error_seen = set()

    def _on_ytdlp_line(line: str) -> None:
        info = _parse_progress_line(line)
        if info is not None:
            log(info["text"], "info")
            if progress_callback:
                progress_callback(info)
            return
        plain = _ANSI_RE.sub("", str(line or "")).strip()
        if plain.startswith("[Merger]"):
            log("Đang ghép luồng hình và âm thanh…", "info")
            if progress_callback:
                progress_callback({"status": "merging", "percent": 98.0,
                                   "text": "Đang ghép hình và âm thanh…"})
        elif plain.startswith("[download] Destination:"):
            log("Đã kết nối CDN; bắt đầu nhận dữ liệu…", "info")
        elif (plain.startswith("ERROR:") or
              plain.startswith("[download] Got error:") or
              plain.startswith("[download] Unable to download")):
            # Giữ lại lỗi mạng theo thời điểm thật; trước đây chỉ có thông báo
            # tổng kết ở cuối nên nhìn như yt-dlp bị treo.
            # yt-dlp hiện dùng chuỗi "Chrome cookie database" cố định cho cả
            # Edge/Chrome/Brave; thay lại nhãn theo browser người dùng chọn.
            if "Could not copy Chrome cookie database" in plain:
                plain = plain.replace(
                    "Could not copy Chrome cookie database",
                    f"Could not copy {_cookie_browser_label(cookies_from_browser)} cookie database")
            if "cookie database" in plain.lower():
                # yt-dlp thường in cùng một lỗi hai lần (stderr + tổng kết).
                # Giữ một dòng có timestamp là đủ, tránh làm người dùng tưởng
                # hai lần tải đã cùng lúc thất bại.
                key = plain.lower()
                if key in _cookie_error_seen:
                    return
                _cookie_error_seen.add(key)
            log("yt-dlp: " + plain, "warn")

    def _on_download_heartbeat(elapsed: float) -> None:
        text = ("yt-dlp vẫn đang chạy · chưa có dòng dữ liệu mới trong 15 giây "
                f"· tổng thời gian {_human_eta(elapsed)}")
        log(text, "dim")
        if progress_callback:
            progress_callback({"status": "waiting", "percent": None,
                               "elapsed": elapsed, "text": text})

    def _run_download(download_cmd: List[str]):
        return run(download_cmd, quiet=True, line_callback=_on_ytdlp_line,
                   heartbeat_callback=_on_download_heartbeat,
                   heartbeat_interval=15.0)

    res = None
    first_error = None
    cookie_disabled = False
    try:
        res = _run_download(cmd)
    except RuntimeError as e:
        first_error = e
        # Đóng cửa sổ chưa chắc đã đóng tiến trình nền Chromium, nên DB cookie
        # vẫn có thể bị khóa. Video công khai không cần cookie; bỏ cookie và
        # thử lại ngay thay vì coi đây là lỗi tải vĩnh viễn.
        if _browser_cookie_failed(e) and "--cookies-from-browser" in cmd:
            cookie_disabled = True
            log(f"Cookie {_cookie_browser_label(cookies_from_browser)} đang bị khóa "
                "(hãy thoát hẳn msedge.exe nếu cần bản đăng nhập); "
                "thử lại video công khai không dùng cookie…",
                "warn")
            cmd = _without_option_value(cmd, "--cookies-from-browser")
            try:
                res = _run_download(cmd)
                first_error = None
            except RuntimeError as cookie_retry_error:
                first_error = cookie_retry_error

    if res is None:
        e = first_error or RuntimeError("yt-dlp không trả kết quả")
        # aria2 nhanh nhưng không tương thích ổn định với mọi URL DASH của
        # Bilibili. Thử tiếp bằng HTTP downloader nội bộ.
        if _external_downloader_failed(e) and "--downloader" in cmd:
            log("aria2c không tải được URL này; tự chuyển sang yt-dlp và tải tiếp…",
                "warn")
            fallback_opts, _ = _download_speed_options(
                concurrent_fragments, external_downloader="none")
            fallback_cmd = _build_download_command(
                url, out_tmpl, fmt, fallback_opts,
                cookies_from_browser=None if cookie_disabled else cookies_from_browser,
                cookies_file=cookies_file)
            try:
                res = _run_download(fallback_cmd)
                cmd = fallback_cmd
            except RuntimeError as retry_error:
                first_error = retry_error
                cmd = fallback_cmd

        # CDN có hai kiểu lỗi cần xử lý khác nhau:
        #   - 416/503/429: Range hoặc phiên CDN cũ -> tải sạch một lần.
        #   - RemoteDisconnected/IncompleteRead: kết nối đứt giữa file lớn
        #     -> giữ .part, chia chunk 10 MiB và nối tiếp bằng 1 fragment.
        # Nếu bản best/4K vẫn không được, thử thêm 480p công khai.
        if res is None and _transient_download_failed(first_error or e):
            initial_error = first_error or e
            connection_retry = _connection_download_failed(initial_error)
            retry_cookie = None if cookie_disabled else cookies_from_browser
            retry_qualities = [(fmt, "bản nét hiện tại")]
            if str(quality).strip().lower() == "best":
                retry_qualities.append((_QUALITY["480"], "480p dự phòng"))

            base_internal_opts = _download_speed_options(
                concurrent_fragments, "none")[0]
            # Khi mất kết nối, cho cùng chất lượng thêm một lượt nối tiếp.
            # Lượt thứ hai cũng giúp CDN cấp URL ký mới nếu URL cũ đã hết hạn.
            attempts_per_quality = 2 if connection_retry else 1
            last_retry_error = initial_error
            abort_retries = False
            for retry_fmt, quality_note in retry_qualities:
                for attempt in range(attempts_per_quality):
                    # Một lỗi Range sau lượt nối tiếp phải chuyển sang tải
                    # sạch; lỗi kết nối tiếp tục giữ .part.
                    use_resume = _connection_download_failed(last_retry_error)
                    retry_opts = (_resume_retry_options(base_internal_opts)
                                  if use_resume else
                                  _fresh_retry_options(base_internal_opts))
                    retry_cmd = _build_download_command(
                        url, out_tmpl, retry_fmt, retry_opts,
                        cookies_from_browser=retry_cookie,
                        cookies_file=cookies_file)
                    if use_resume:
                        log(f"CDN ngắt giữa chừng; tải tiếp file .part "
                            f"({quality_note}, lượt {attempt + 1}/{attempts_per_quality}, "
                            "1 fragment, chunk 10 MiB)…", "warn")
                    else:
                        log(f"Lỗi CDN/Range; tải lại sạch ({quality_note}, "
                            "1 fragment, bỏ file .part cũ)…", "warn")
                    try:
                        res = _run_download(retry_cmd)
                        cmd = retry_cmd
                        break
                    except RuntimeError as retry_error:
                        last_retry_error = retry_error
                        # Cookie DB có thể chỉ bị khóa ở lần retry; chuyển sang
                        # link công khai ngay trong cùng lượt, không bắt người dùng
                        # phải khởi động lại ứng dụng.
                        if (retry_cookie and _browser_cookie_failed(retry_error)
                                and "--cookies-from-browser" in retry_cmd):
                            cookie_disabled = True
                            retry_cookie = None
                            retry_cmd = _without_option_value(
                                retry_cmd, "--cookies-from-browser")
                            try:
                                res = _run_download(retry_cmd)
                                cmd = retry_cmd
                                break
                            except RuntimeError as public_retry_error:
                                last_retry_error = public_retry_error
                        if not _transient_download_failed(last_retry_error):
                            abort_retries = True
                            break
                if res is not None or abort_retries:
                    break
            if res is None:
                first_error = last_retry_error

        if res is None:
            final_error = first_error or e
            raise RuntimeError(
                f"Tải thất bại. {_cookie_hint}\nChi tiết: {final_error}") from final_error

    lines = [l.strip() for l in (res.stdout or "").splitlines() if l.strip()]
    marked_paths = [line[len(_PATH_PREFIX):] for line in lines
                    if line.startswith(_PATH_PREFIX)]
    # Unit test/các bản yt-dlp cũ có thể trả trực tiếp path không có marker;
    # vẫn giữ đường lui tương thích nhưng bỏ các dòng progress khỏi ứng viên.
    plain_lines = [line for line in lines
                   if not line.startswith(_PROGRESS_PREFIX)]
    path = marked_paths[-1] if marked_paths else (plain_lines[-1] if plain_lines else "")
    if not path or not os.path.exists(path):
        # phòng khi --print không ra đường dẫn: tìm file mp4 mới nhất
        cands = [os.path.join(out_dir, f) for f in os.listdir(out_dir)
                 if f.lower().endswith(".mp4")]
        if not cands:
            raise RuntimeError("Không tìm thấy file sau khi tải.")
        path = max(cands, key=os.path.getmtime)

    return _validate_download_file(path, _cookie_hint, progress_callback)
