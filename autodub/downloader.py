"""Tải video từ link (Bilibili, YouTube...) bằng yt-dlp.

- Lấy đúng LUỒNG GỐC chất lượng cao rồi ghép video+audio -> mp4. Bilibili KHÔNG
  chèn watermark vào luồng tải, nên bản tải về sạch (không logo do trang thêm).
- Bản nét (1080p+) một số video cần đăng nhập -> dùng cookies (xem config).
- Nếu video có LOGO CHÁY CỨNG ở góc (do người đăng chèn) thì dùng tùy chọn
  video.delogo trong config để xoá mờ đi khi render.
"""
from __future__ import annotations

import os
import re
import sys
from typing import List, Optional, Tuple

from .utils import log, run, which, ffprobe_video_size, ffprobe_has_stream, ffprobe_is_blank_video


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


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
        "--concurrent-fragments", str(n_frag),
    ]
    notes = [f"{n_frag} fragment song song"]

    choice = str(external_downloader or "").strip()
    choice_l = choice.lower()
    if choice_l in ("", "none", "false", "off", "no"):
        return cmd, ", ".join(notes)

    downloader = None
    if choice_l == "auto":
        downloader = which("aria2c")
    else:
        downloader = which(choice) or choice

    if downloader:
        cmd += ["--downloader", downloader]
        base = os.path.basename(str(downloader)).lower()
        if "aria2c" in base:
            conn = _positive_int(n_frag, 8, 1, 16)
            cmd += [
                "--downloader-args",
                f"aria2c:-x {conn} -s {conn} -k 1M --file-allocation=none",
            ]
            notes.append(f"aria2c {conn} kết nối")
        else:
            notes.append(f"downloader ngoài: {choice}")
    return cmd, ", ".join(notes)


def download_video(
    url: str,
    out_dir: str,
    quality: str = "best",
    cookies_from_browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
    concurrent_fragments=8,
    external_downloader: Optional[str] = "auto",
) -> str:
    """Tải 1 video, trả về đường dẫn file mp4 đã lưu."""
    url = extract_url(url)
    if not url:
        raise RuntimeError("Không thấy URL hợp lệ. Hãy dán riêng link bắt đầu bằng http:// hoặc https://.")
    os.makedirs(out_dir, exist_ok=True)
    fmt = _QUALITY.get(str(quality), _QUALITY["best"])
    out_tmpl = os.path.join(out_dir, "%(title).80s [%(id)s].%(ext)s")
    speed_opts, speed_note = _download_speed_options(concurrent_fragments, external_downloader)

    cmd = [
        *_ytdlp_cmd(),
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--windows-filenames",           # tên file an toàn trên Windows
        "-o", out_tmpl,
        *speed_opts,
        "--no-simulate",
        "--print", "after_move:filepath",  # in đường dẫn cuối sau khi ghép xong
    ]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    if cookies_file:
        cmd += ["--cookies", cookies_file]
    cmd.append(url)

    log("Đang tải video (yt-dlp)...", "step")
    log(f"Tăng tốc tải: {speed_note}.", "info")
    _cookie_hint = (
        "Kiểm tra link, hoặc bản nét cần đăng nhập: đặt "
        "download.cookies_from_browser: chrome (hoặc edge/firefox - trình duyệt bạn "
        "ĐÃ đăng nhập Bilibili) trong config.yaml."
    )
    try:
        res = run(cmd, quiet=True)
    except RuntimeError as e:
        raise RuntimeError(f"Tải thất bại. {_cookie_hint}\nChi tiết: {e}")

    lines = [l.strip() for l in (res.stdout or "").splitlines() if l.strip()]
    path = lines[-1] if lines else ""
    if not path or not os.path.exists(path):
        # phòng khi --print không ra đường dẫn: tìm file mp4 mới nhất
        cands = [os.path.join(out_dir, f) for f in os.listdir(out_dir)
                 if f.lower().endswith(".mp4")]
        if not cands:
            raise RuntimeError("Không tìm thấy file sau khi tải.")
        path = max(cands, key=os.path.getmtime)

    # Kiểm tra lại file THỰC SỰ có hình, không chỉ tin lời yt-dlp báo thành công.
    # Bilibili hay chỉ mở luồng hình cho tài khoản đã đăng nhập (>360p); nếu
    # thiếu cookies, yt-dlp có thể "thành công" nhưng chỉ tải được luồng tiếng.
    w, h = ffprobe_video_size(path)
    if w == 0 or h == 0:
        raise RuntimeError(
            f"File tải về CHỈ CÓ TIẾNG, không có hình ({os.path.basename(path)}). "
            "Nguyên nhân thường gặp: Bilibili chỉ cho xem/tải hình ở độ phân giải "
            "yêu cầu khi đã ĐĂNG NHẬP. Cách sửa: {hint} Sau đó thử lại (xoá file "
            "lỗi này trong thư mục downloads trước khi chạy lại). Nếu vẫn lỗi, "
            "thử hạ download.quality xuống 480 hoặc cập nhật yt-dlp: "
            "python -m pip install -U yt-dlp".format(hint=_cookie_hint)
        )
    # Chiều ngược lại: có hình nhưng thiếu tiếng (ít gặp hơn nhưng vẫn có thể
    # xảy ra nếu luồng audio DASH bị chặn/giới hạn riêng).
    if not ffprobe_has_stream(path, "a"):
        raise RuntimeError(
            f"File tải về CHỈ CÓ HÌNH, không có tiếng ({os.path.basename(path)}). "
            f"{_cookie_hint} Sau đó xoá file lỗi trong thư mục downloads rồi tải lại."
        )

    # Có luồng hình đúng kích thước NHƯNG nội dung toàn ĐEN: Bilibili đôi khi
    # trả về "hình giả" (placeholder đen) kèm tiếng thật khi độ phân giải yêu
    # cầu bị khoá sau đăng nhập/VIP - ffprobe_video_size ở trên không bắt được
    # kiểu lỗi này vì stream vẫn khai đúng width/height.
    log("Kiểm tra hình có bị đen (placeholder) không...", "info")
    if ffprobe_is_blank_video(path):
        raise RuntimeError(
            f"File tải về CÓ TIẾNG nhưng HÌNH TOÀN MÀU ĐEN ({os.path.basename(path)}). "
            "Đây là kiểu Bilibili trả 'hình giả' khi độ phân giải yêu cầu bị khoá sau "
            f"đăng nhập/VIP, dù luồng vẫn khai đúng kích thước. {_cookie_hint} Sau đó "
            "xoá file lỗi trong thư mục downloads rồi tải lại. Nếu vẫn đen dù đã có "
            "cookies, thử hạ download.quality xuống 480."
        )

    log(f"Đã tải: {os.path.basename(path)}", "ok")
    return path
