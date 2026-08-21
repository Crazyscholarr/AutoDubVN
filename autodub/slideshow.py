"""Dựng video từ một loạt ảnh, khớp đúng độ dài giọng đọc.

Dùng cho luồng kể chuyện: có file truyện TXT, đọc thành giọng, rồi cần một
khung hình để dán giọng lên. Ảnh được chia đều theo độ dài giọng đọc nên video
ra lúc nào cũng vừa khít, không thừa không thiếu.

Hai kiểu hình:
  tinh        - ảnh đứng yên. Nhanh nhất, hợp video dài.
  chuyen_dong - ảnh trôi và phóng chậm (Ken Burns), đỡ cảm giác tĩnh, đổi lại
                phải encode nặng hơn.
"""
from __future__ import annotations

import math
import os
import random
import re
import shutil
from typing import Dict, List, Optional, Sequence, Tuple

from .utils import log, run, ffprobe_duration, has_nvenc, nvenc_encode_args

ANH_HOP_LE = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_HOP_LE = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v"}

MIN_GIAY_MOI_ANH = 2.0
MAX_GIAY_MOI_ANH = 25.0


def liet_ke_anh(paths: Sequence[str], bo_trung: bool = True) -> List[str]:
    """Gom ảnh theo thứ tự; mặc định bỏ trùng, trừ khi caller đã lập lịch cảnh."""
    out: List[str] = []
    for raw in paths or []:
        p = str(raw or "").strip().strip('"')
        if not p:
            continue
        if os.path.isdir(p):
            names = [n for n in os.listdir(p)
                     if os.path.splitext(n)[1].lower() in ANH_HOP_LE]
            out.extend(os.path.join(p, n) for n in sorted(names, key=_khoa_sap_xep))
        elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in ANH_HOP_LE:
            out.append(os.path.abspath(p))
    if not bo_trung:
        return [os.path.abspath(p) for p in out]
    # Giữ nguyên thứ tự nhưng bỏ trùng.
    seen = set()
    uniq = []
    for p in out:
        key = os.path.abspath(p).lower()
        if key not in seen:
            seen.add(key)
            uniq.append(os.path.abspath(p))
    return uniq


def _khoa_sap_xep(name: str):
    """Sắp "anh2.jpg" trước "anh10.jpg" thay vì ngược lại."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", name)]


def chia_thoi_luong(so_anh: int, tong_giay: float) -> List[float]:
    """Chia đều thời lượng cho từng ảnh, phần dư dồn vào ảnh cuối.

    Chia đều tới từng mili giây rồi bù phần lẻ vào ảnh cuối, để tổng luôn khớp
    tuyệt đối với giọng đọc - lệch một chút thôi là khung hình cuối bị đen.
    """
    so_anh = max(1, int(so_anh))
    tong = max(0.1, float(tong_giay))
    moi_anh = round(tong / so_anh, 3)
    ra = [moi_anh] * so_anh
    ra[-1] = round(tong - moi_anh * (so_anh - 1), 3)
    return ra


def so_canh_nen_dung(tong_giay: float, so_anh_co: int) -> int:
    """Cắt video thành bao nhiêu cảnh.

    Mặc định mỗi tấm ảnh một cảnh, vì đã chọn ảnh thì hẳn muốn thấy đủ. Hai
    trường hợp phải nắn lại:
      - Ảnh nhiều tới mức mỗi tấm chưa kịp nhìn đã trôi -> lấy bớt, rải đều.
      - Ảnh ít mà truyện dài -> quay vòng lại từ đầu, để không tấm nào phải
        đứng im quá MAX_GIAY_MOI_ANH giây.
    Riêng khi vỏn vẹn một tấm thì giữ đúng một cảnh chạy suốt video: chẻ một
    tấm ra nhiều cảnh chỉ tạo thêm những cú giật vô cớ ở chỗ nối.
    """
    if so_anh_co <= 0:
        return 0
    if so_anh_co == 1:
        return 1
    tong = max(0.1, float(tong_giay))
    toi_da = max(1, int(tong / MIN_GIAY_MOI_ANH))
    so_canh = min(so_anh_co, toi_da)
    if tong / so_canh > MAX_GIAY_MOI_ANH:
        so_canh = min(toi_da, int(math.ceil(tong / MAX_GIAY_MOI_ANH)))
    return max(1, so_canh)


def _bo_loc_khung_hinh(w: int, h: int) -> str:
    """Đưa ảnh về đúng khung, giữ tỉ lệ, phần thiếu lấp bằng chính ảnh làm nền mờ.

    Nền mờ nhìn dễ chịu hơn hai dải đen, nhất là khi ảnh dọc ghép vào khung ngang.
    """
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},boxblur=luma_radius=28:luma_power=2[bg];"
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


def _lenh_encode(fps: int, crf: int = 22, w: int = 1920, h: int = 1080) -> List[str]:
    if has_nvenc():
        return nvenc_encode_args(crf, fps=fps, width=w, height=h,
                                 extra=["-r", str(fps)])
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-r", str(fps)]


def _dung_mot_canh(anh: str, giay: float, w: int, h: int, fps: int,
                   out_path: str, chuyen_dong: bool,
                   huong: str = "vao") -> str:
    """Dựng một đoạn video từ một tấm ảnh.

    `huong` được rút sẵn ở ngoài (thay vì truyền rng vào) để các cảnh dựng
    SONG SONG vẫn cho kết quả y hệt bản chạy tuần tự với cùng seed.
    """
    khung = _bo_loc_khung_hinh(w, h)
    if chuyen_dong:
        tong_frame = max(2, int(round(giay * fps)))
        # zoompan làm việc trên ảnh đã phóng to sẵn, nếu không đường nét sẽ giật
        # từng nấc vì mỗi khung chỉ nhích được một pixel.
        phong = 2
        if huong == "vao":
            z, x, y = "min(zoom+0.0006,1.25)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
        elif huong == "ra":
            z, x, y = "if(eq(on,0),1.25,max(zoom-0.0006,1.0))", \
                      "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
        elif huong == "trai":
            z, x, y = "1.15", f"(iw-iw/zoom)*(1-on/{tong_frame})", "ih/2-(ih/zoom/2)"
        else:
            z, x, y = "1.15", f"(iw-iw/zoom)*(on/{tong_frame})", "ih/2-(ih/zoom/2)"
        vf = (f"[0:v]{khung},scale={w * phong}:{h * phong},"
              f"zoompan=z='{z}':x='{x}':y='{y}':d={tong_frame}:"
              f"s={w}x{h}:fps={fps}")
    else:
        vf = f"[0:v]{khung}"

    cmd = ["ffmpeg", "-y", "-hide_banner", "-nostdin",
           "-loop", "1", "-t", f"{giay:.3f}", "-i", anh,
           "-filter_complex", vf,
           *_lenh_encode(fps, w=w, h=h), "-t", f"{giay:.3f}", "-an", out_path]
    run(cmd, check=True, quiet=True)
    return out_path


def _duong_dan_ass_cho_ffmpeg(ass_path: str) -> str:
    """Thoát đường dẫn cho filter `ass` (dấu ':' của ổ đĩa hay làm vỡ lệnh)."""
    return ass_path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def build_story_overlay_filter(w: int, h: int, logo: Optional[Dict] = None,
                               ass_path: Optional[str] = None,
                               logo_input_index: int = 2,
                               source_cover: str = "none",
                               character: Optional[Dict] = None,
                               char_input_index: int = -1) -> str:
    """Tạo filter cuối cho logo + phụ đề; logo đặt trước để chữ luôn nổi trên."""
    logo_cfg = logo if isinstance(logo, dict) and logo.get("enabled") else None
    has_sub = bool(ass_path)
    cover = str(source_cover or "none").strip().lower()
    has_cover = cover in {"blur_bottom", "blur", "che_bottom", "cover_bottom", "black_bottom"}
    char_cfg = character if isinstance(character, dict) and character.get("enabled") else None
    has_char = bool(char_cfg) and char_input_index >= 0
    if not logo_cfg and not has_sub and not has_cover and not has_char:
        return ""

    parts: List[str] = []
    current = "[0:v]"
    if has_cover:
        # Làm mờ hoặc che dải phụ đề cũ ở 22% phía dưới; áp dụng ở lượt encode
        # cuối để không làm thay đổi lịch cắt/nối nguồn.
        if cover in {"blur_bottom", "blur"}:
            parts.append(
                f"{current}split=2[story_base][story_blur];"
                f"[story_blur]crop=iw:ih*0.22:0:ih*0.78,boxblur=18:8[story_blur_crop];"
                f"[story_base][story_blur_crop]overlay=0:H-h:shortest=1[story_cover]")
        else:
            parts.append(
                f"{current}drawbox=x=0:y=ih*0.78:w=iw:h=ih*0.22:"
                "color=black@0.62:t=fill[story_cover]")
        current = "[story_cover]"
    if has_char:
        try:
            char_scale = max(0.55, min(1.8, float(char_cfg.get("scale", 1.0) or 1.0)))
        except (TypeError, ValueError):
            char_scale = 1.0
        try:
            char_opacity = max(0.25, min(1.0, float(char_cfg.get("opacity", 0.92) or 0.92)))
        except (TypeError, ValueError):
            char_opacity = 0.92
        # Kích thước nhân vật dựa trên scale, giữ tỉ lệ gốc của ảnh PNG.
        char_h = max(120, int(220 * char_scale)) // 2 * 2
        # Nhân vật quả mít cute: scale ảnh PNG, nhấp nhô nhẹ bằng sin(t).
        parts.append(
            f"[{int(char_input_index)}:v]scale=-2:{char_h}:"
            "force_original_aspect_ratio=decrease,format=rgba,"
            f"colorchannelmixer=aa={char_opacity:.3f}[story_char];"
            f"{current}[story_char]overlay="
            f"x='W-w-18':y='H-h-18+6*sin(t*3)':"
            "format=auto:shortest=1[story_character]")
        current = "[story_character]"
    if logo_cfg:
        try:
            width_pct = max(4.0, min(40.0, float(logo_cfg.get("width_pct", 12) or 12)))
        except (TypeError, ValueError):
            width_pct = 12.0
        try:
            opacity = max(0.05, min(1.0, float(logo_cfg.get("opacity", 0.82) or 0.82)))
        except (TypeError, ValueError):
            opacity = 0.82
        logo_w = max(32, int(round(int(w) * width_pct / 100.0)) // 2 * 2)
        margin = max(8, int(round(min(int(w), int(h)) * 0.018)))
        position = str(logo_cfg.get("position") or "top-right").strip().lower()
        coords = {
            "top-left": (str(margin), str(margin)),
            "top-right": (f"W-w-{margin}", str(margin)),
            "bottom-left": (str(margin), f"H-h-{margin}"),
            "bottom-right": (f"W-w-{margin}", f"H-h-{margin}"),
        }
        x, y = coords.get(position, coords["top-right"])
        parts.append(
            f"[{int(logo_input_index)}:v]scale={logo_w}:-2:"
            "force_original_aspect_ratio=decrease,format=rgba,"
            f"colorchannelmixer=aa={opacity:.3f}[story_logo]")
        overlay_out = "vlogo" if has_sub else "v"
        parts.append(
            f"{current}[story_logo]overlay=x='{x}':y='{y}':format=auto"
            f"[{overlay_out}]")
        current = f"[{overlay_out}]"
    if has_sub:
        parts.append(
            f"{current}ass='{_duong_dan_ass_cho_ffmpeg(str(ass_path))}'[v]")
    elif current != "[v]":
        parts.append(f"{current}null[v]")
    return ";".join(parts)


def tao_video_tu_anh(anh: Sequence[str],
                     audio_path: str,
                     out_path: str,
                     workdir: Optional[str] = None,
                     w: int = 1920, h: int = 1080, fps: int = 30,
                     kieu: str = "chuyen_dong",
                     seed: Optional[int] = None,
                     ass_path: Optional[str] = None,
                     logo: Optional[Dict] = None,
                     character: Optional[Dict] = None,
                     giu_canh_lap: bool = False,
                     progress=None) -> Dict:
    """Dựng video từ ảnh, dài đúng bằng file âm thanh.

    `anh` nhận cả file lẫn thư mục. Ảnh ít hơn nhu cầu thì được dùng lặp lại
    theo vòng, nhiều hơn thì lấy bớt cho mỗi ảnh đủ thời gian nhìn.
    `ass_path`: có thì GHI CỨNG phụ đề lên hình (thêm một lượt encode cuối).
    `logo`: ảnh nhận diện kênh, vị trí/kích thước/độ mờ được tùy chỉnh.
    Khổ dọc 9:16 chỉ là w/h khác (vd 1080x1920) - mọi filter đều theo w/h.
    """
    # Gói ảnh theo chương có thể chủ ý lặp một tấm nhiều lần để giữ đúng nội
    # dung chương. Không khử trùng lịch đó.
    danh_sach = liet_ke_anh(anh, bo_trung=not giu_canh_lap)
    if not danh_sach:
        raise ValueError("Chưa chọn được tấm ảnh nào.")
    if not audio_path or not os.path.isfile(audio_path):
        raise ValueError("Chưa có file âm thanh để căn độ dài video.")

    tong = ffprobe_duration(audio_path)
    if tong <= 0:
        raise ValueError("Không đọc được độ dài file âm thanh.")

    w = max(160, int(w) // 2 * 2)
    h = max(160, int(h) // 2 * 2)
    fps = max(1, min(60, int(fps or 30)))
    chuyen_dong = str(kieu or "").strip().lower() in {"chuyen_dong", "ken_burns", "kenburns"}

    can = so_canh_nen_dung(tong, len(danh_sach))
    if can < len(danh_sach):
        # Nhiều ảnh hơn mức cần: lấy rải đều cả bộ chứ không cắt cụt phần đuôi.
        buoc = len(danh_sach) / can
        chon = [danh_sach[min(len(danh_sach) - 1, int(i * buoc))] for i in range(can)]
    else:
        chon = [danh_sach[i % len(danh_sach)] for i in range(can)]

    thoi_luong = chia_thoi_luong(len(chon), tong)
    workdir = workdir or os.path.join(
        os.path.dirname(os.path.abspath(out_path)), "_tmp", "slideshow")
    os.makedirs(workdir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

    try:
        vong = len(chon) / len(danh_sach)
        log(f"Dựng {len(chon)} cảnh từ {len(danh_sach)} ảnh, mỗi cảnh "
            f"{thoi_luong[0]:.1f} giây, tổng {tong:.1f} giây."
            + (f" Ảnh được dùng lại {vong:.1f} vòng." if vong > 1.05 else ""), "step")
    
        # Dựng cảnh SONG SONG 3 luồng: mỗi cảnh là một lần encode độc lập, chạy
        # tuần tự từng là nút cổ chai của video dài (Ken Burns encode nặng).
        # 3 luồng nằm trong hạn mức phiên NVENC của card phổ thông.
        rng = random.Random(seed)
        huongs = [rng.choice(["vao", "ra", "trai", "phai"]) for _ in chon]
        canh: List[str] = [os.path.join(workdir, f"canh_{i:04d}.mp4")
                           for i in range(len(chon))]
    
        from concurrent.futures import ThreadPoolExecutor, as_completed
        xong = 0
        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = [pool.submit(_dung_mot_canh, img, giay, w, h, fps,
                                canh[i], chuyen_dong, huongs[i])
                    for i, (img, giay) in enumerate(zip(chon, thoi_luong))]
            for fut in as_completed(futs):
                fut.result()                 # ném lỗi ngay nếu một cảnh hỏng
                xong += 1
                if progress:
                    progress(int(5 + 80 * xong / len(chon)),
                             f"Đã dựng {xong}/{len(chon)} cảnh")
    
        danh_sach_file = os.path.join(workdir, "canh.txt")
        with open(danh_sach_file, "w", encoding="utf-8") as f:
            for part in canh:
                f.write("file '" + part.replace("'", "'\\''") + "'\n")
    
        sub_path = ass_path if ass_path and os.path.exists(ass_path) else None
        logo_cfg = dict(logo) if isinstance(logo, dict) else {}
        logo_path = str(logo_cfg.get("path") or "").strip().strip('"')
        logo_ok = (bool(logo_cfg.get("enabled")) and os.path.isfile(logo_path)
                   and os.path.splitext(logo_path)[1].lower() in ANH_HOP_LE)
        if logo_cfg.get("enabled") and not logo_ok:
            log("Đã bật logo nhưng chưa chọn được file ảnh hợp lệ; bỏ qua logo.", "warn")
        if logo_ok:
            logo_cfg["path"] = os.path.abspath(logo_path)
    
        char_cfg = dict(character) if isinstance(character, dict) else {}
        char_ok = bool(char_cfg.get("enabled"))
        # Tìm file ảnh nhân vật PNG
        char_png = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "assets", "nhan_vat_mit.png")
        char_ok = char_ok and os.path.isfile(char_png)
        if sub_path or logo_ok or char_ok:
            # Có logo/phụ đề cứng: nối cảnh (copy, nhanh) rồi một lượt encode cuối.
            # Burn ngay lúc dựng từng cảnh sẽ lặp encode và dễ lệch mốc phụ đề.
            noi_tam = os.path.join(workdir, "noi_video.mp4")
            run(["ffmpeg", "-y", "-hide_banner", "-nostdin",
                 "-f", "concat", "-safe", "0", "-i", danh_sach_file,
                 "-c:v", "copy", "-an", noi_tam], check=True, quiet=True)
            if progress:
                label = "logo và phụ đề" if logo_ok and sub_path else (
                    "logo" if logo_ok else "phụ đề")
                progress(88, f"Đang ghi {label} lên hình")
            cmd = ["ffmpeg", "-y", "-hide_banner", "-nostdin",
                   "-i", noi_tam, "-i", audio_path]
            next_input = 2
            if logo_ok:
                cmd.extend(["-loop", "1", "-framerate", "1", "-i", logo_cfg["path"]])
                next_input += 1
            char_idx = -1
            if char_ok:
                cmd.extend(["-loop", "1", "-framerate", "1", "-i", char_png])
                char_idx = next_input
            graph = build_story_overlay_filter(
                w, h, logo_cfg if logo_ok else None, sub_path,
                logo_input_index=2, character=char_cfg if char_ok else None,
                char_input_index=char_idx)
            cmd.extend(["-filter_complex", graph,
                 "-map", "[v]", "-map", "1:a:0",
                 *_lenh_encode(fps, w=w, h=h),
                 "-c:a", "aac", "-b:a", "192k",
                  "-shortest", "-movflags", "+faststart", out_path])
            run(cmd, check=True, quiet=True)
        else:
            run(["ffmpeg", "-y", "-hide_banner", "-nostdin",
                 "-f", "concat", "-safe", "0", "-i", danh_sach_file,
                 "-i", audio_path,
                 "-map", "0:v:0", "-map", "1:a:0",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                 "-shortest", "-movflags", "+faststart", out_path],
                check=True, quiet=True)
    
        ra = ffprobe_duration(out_path)
        if ra <= 0:
            raise RuntimeError("Dựng xong nhưng video rỗng.")
        log(f"Đã dựng video từ ảnh: {os.path.basename(out_path)} ({ra:.1f} giây).", "ok")
        return {"path": out_path, "duration": ra, "so_anh": len(chon),
                "kieu": "chuyen_dong" if chuyen_dong else "tinh",
                "logo": logo_cfg.get("path", "") if logo_ok else "",
                "w": w, "h": h, "fps": fps}
    finally:
        if os.path.isdir(workdir):
            shutil.rmtree(workdir, ignore_errors=True)


def liet_ke_video(paths: Sequence[str], bo_trung: bool = True) -> List[str]:
    """Gom file video từ danh sách đường dẫn hoặc thư mục."""
    out: List[str] = []
    for raw in paths or []:
        p = str(raw or "").strip().strip('"')
        if not p:
            continue
        if os.path.isdir(p):
            names = [n for n in os.listdir(p)
                     if os.path.splitext(n)[1].lower() in VIDEO_HOP_LE]
            out.extend(os.path.join(p, n) for n in sorted(names, key=_khoa_sap_xep))
        elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in VIDEO_HOP_LE:
            out.append(os.path.abspath(p))
    if not bo_trung:
        return [os.path.abspath(p) for p in out]
    seen, uniq = set(), []
    for p in out:
        key = os.path.abspath(p).lower()
        if key not in seen:
            seen.add(key)
            uniq.append(os.path.abspath(p))
    return uniq


def _video_cover_filter(w: int, h: int, effect: str = "tinh",
                        transform: Optional[Dict] = None) -> str:
    """Đưa clip vào khung theo crop/zoom/vị trí người dùng đã chọn."""
    mode = str(effect or "tinh").strip().lower()
    cfg = dict(transform) if isinstance(transform, dict) else {}

    def number(key, default, lo, hi):
        try:
            value = float(cfg.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    zoom = number("zoom", 100.0, 40.0, 220.0) / 100.0
    if "zoom" not in cfg and mode in {"zoom_in", "zoom-in", "zoomvao", "zoom_vao"}:
        zoom = 1.16
    elif "zoom" not in cfg and mode in {"zoom_out", "zoom-out", "zoomra", "zoom_ra"}:
        zoom = .86
    pos_x = number("x", 50.0, 0.0, 100.0) / 100.0
    pos_y = number("y", 50.0, 0.0, 100.0) / 100.0
    left = number("crop_left", 0.0, 0.0, 45.0) / 100.0
    right = number("crop_right", 0.0, 0.0, 45.0) / 100.0
    top = number("crop_top", 0.0, 0.0, 45.0) / 100.0
    bottom = number("crop_bottom", 0.0, 0.0, 45.0) / 100.0
    # Không cho crop mất sạch hình nếu người dùng kéo hai mép quá gần nhau.
    if left + right > .9:
        right = max(0.0, .9 - left)
    if top + bottom > .9:
        bottom = max(0.0, .9 - top)

    filters: List[str] = []
    if left or right or top or bottom:
        filters.append(
            "crop=iw*%.6f:ih*%.6f:iw*%.6f:ih*%.6f" %
            (1.0 - left - right, 1.0 - top - bottom, left, top))
    if zoom < .999:
        # Thu nhỏ thật sự trong khung (có viền nền đen), vị trí vẫn kéo được.
        sw = max(2, int(round(w * zoom)) // 2 * 2)
        sh = max(2, int(round(h * zoom)) // 2 * 2)
        filters.extend([
            f"scale={sw}:{sh}:force_original_aspect_ratio=decrease",
            "pad=%d:%d:(ow-iw)*%.6f:(oh-ih)*%.6f:black" %
            (w, h, pos_x, pos_y),
        ])
    else:
        sw = max(w, int(round(w * zoom)))
        sh = max(h, int(round(h * zoom)))
        filters.extend([
            f"scale={sw}:{sh}:force_original_aspect_ratio=increase",
            "crop=%d:%d:(in_w-out_w)*%.6f:(in_h-out_h)*%.6f" %
            (w, h, pos_x, pos_y),
        ])
    filters.append("setsar=1")
    return ",".join(filters)


def lap_lich_video(danh_sach: Sequence[str], durations: Dict[str, float],
                   audio_duration: float, min_seconds: float = 300.0,
                   max_seconds: float = 600.0, random_pick: bool = True,
                   random_seed: Optional[int] = None) -> List[Tuple[str, float, float]]:
    """Chọn/cắt video thành lịch đủ đúng thời lượng audio.

    Mỗi đoạn nằm trong khoảng min/max khi nguồn và phần thời gian còn lại cho
    phép. Random không lặp cùng một nguồn hai lần liên tiếp nếu có lựa chọn.
    """
    sources = [s for s in danh_sach if float(durations.get(s, 0) or 0) > .05]
    if not sources or audio_duration <= .05:
        return []
    lo = max(2.0, float(min_seconds or 300.0))
    hi = max(lo, float(max_seconds or lo))
    rng = random.Random(random_seed)
    offsets = {src: 0.0 for src in sources}
    plans: List[Tuple[str, float, float]] = []
    remaining = float(audio_duration)
    cursor = 0
    last = None
    while remaining > .05 and len(plans) < 2000:
        choices = [s for s in sources if s != last] if len(sources) > 1 else sources
        if random_pick:
            src = rng.choice(choices)
            wanted = rng.uniform(lo, hi) if hi > lo else lo
        else:
            src = choices[cursor % len(choices)]
            cursor += 1
            wanted = hi
        src_dur = float(durations[src])
        part = min(remaining, wanted, src_dur)
        if part <= .05:
            break
        if random_pick and src_dur > part + .05:
            offset = rng.uniform(0.0, src_dur - part)
        else:
            offset = offsets[src]
            if offset + part > src_dur:
                offset = 0.0
            offsets[src] = (offset + part) % src_dur
        plans.append((src, offset, part))
        remaining -= part
        last = src
    return plans


def tao_video_tu_video(videos: Sequence[str], audio_path: str, out_path: str,
                       workdir: Optional[str] = None,
                       w: int = 1920, h: int = 1080, fps: int = 30,
                       hieu_ung: str = "tinh", ass_path: Optional[str] = None,
                       logo: Optional[Dict] = None, max_seconds: float = 600.0,
                       min_seconds: float = 300.0, random_pick: bool = True,
                       random_seed: Optional[int] = None,
                       transform: Optional[Dict] = None,
                       source_cover: str = "none",
                       character: Optional[Dict] = None,
                       blur_regions: Optional[list] = None, blur_bottom_ratio: float = 0,
                       progress=None) -> Dict:
    """Cắt/lặp một nhóm video Bilibili để khớp đúng độ dài audio.

    Mỗi nguồn được chuẩn hoá cùng khung hình rồi nối tuần tự. ``hieu_ung`` hỗ
    trợ ``tinh``, ``zoom_in`` và ``zoom_out``; sub/logo được ghi ở lượt encode
    cuối giống pipeline ảnh.
    """
    danh_sach = liet_ke_video(videos)
    if not danh_sach:
        raise ValueError("Chưa chọn được video nguồn nào.")
    if not audio_path or not os.path.isfile(audio_path):
        raise ValueError("Chưa có file âm thanh để căn độ dài video.")
    tong = ffprobe_duration(audio_path)
    if tong <= 0:
        raise ValueError("Không đọc được độ dài file âm thanh.")
    w = max(160, int(w) // 2 * 2)
    h = max(160, int(h) // 2 * 2)
    fps = max(1, min(60, int(fps or 30)))
    workdir = workdir or os.path.join(
        os.path.dirname(os.path.abspath(out_path)), "_tmp", "video_sources")
    os.makedirs(workdir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

    try:
        durs = {src: ffprobe_duration(src) for src in danh_sach}
        plans = lap_lich_video(
            danh_sach, durs, tong, min_seconds=min_seconds,
            max_seconds=max_seconds, random_pick=random_pick,
            random_seed=random_seed)
    
        if not plans:
            raise ValueError("Không lập được lịch video nguồn.")

        if progress:
            mode = "random" if random_pick else "tuần tự"
            progress(
                3,
                "Đã lập lịch %s %d đoạn từ %d video, đủ %.1f phút audio"
                % (mode, len(plans), len(danh_sach), tong / 60.0),
            )
    
        parts: List[str] = []
        for i, (src, start_offset, part) in enumerate(plans):
            part_path = os.path.join(workdir, "nguon_%04d.mp4" % i)
            vf = _video_cover_filter(w, h, hieu_ung, transform=transform)
            
            filter_args = ["-vf", vf]
            if blur_regions or blur_bottom_ratio > 0:
                graph_parts = [f"[0:v]{vf}[base]"]
                curr = "[base]"
                if blur_regions:
                    for idx, r in enumerate(blur_regions):
                        bx = max(0, min(w - 2, int(r.get("x", 0))))
                        by = max(0, min(h - 2, int(r.get("y", 0))))
                        bw = max(2, min(w - bx, int(r.get("w", 0))))
                        bh = max(2, min(h - by, int(r.get("h", 0))))
                        if bw > 2 and bh > 2:
                            blur_size = max(2, min(24, min(bw, bh) // 4))
                            graph_parts.append(
                                f"{curr}split=2[in{idx}][crop{idx}];"
                                f"[crop{idx}]crop={bw}:{bh}:{bx}:{by},boxblur={blur_size}:4[blur{idx}];"
                                f"[in{idx}][blur{idx}]overlay={bx}:{by}[out{idx}]"
                            )
                            curr = f"[out{idx}]"
                if blur_bottom_ratio > 0:
                    bh = max(2, min(h - 2, int(h * blur_bottom_ratio)))
                    if bh > 0:
                        by = h - bh
                        idx = "bottom"
                        blur_size = max(4, min(24, bh // 4))
                        graph_parts.append(
                            f"{curr}split=2[in{idx}][crop{idx}];"
                            f"[crop{idx}]crop={w}:{bh}:0:{by},boxblur={blur_size}:6[blur{idx}];"
                            f"[in{idx}][blur{idx}]overlay=0:{by}[out{idx}]"
                        )
                        curr = f"[out{idx}]"
                if curr != "[base]":
                    filter_args = ["-filter_complex", ";".join(graph_parts), "-map", curr]
    
            cmd = ["ffmpeg", "-y", "-hide_banner", "-nostdin"]
            if start_offset > 0.05:
                cmd.extend(["-ss", f"{start_offset:.3f}"])
            cmd.extend([
                "-i", src, "-t", f"{part:.3f}",
                *filter_args, *_lenh_encode(fps, w=w, h=h),
                "-an", part_path
            ])
            run(cmd, check=True, quiet=True)
            parts.append(part_path)
            if progress:
                progress(int(5 + 78 * (i + 1) / len(plans)),
                         "Đã xử lý %d/%d đoạn video (%.1fs)" % (i + 1, len(plans), sum(p[2] for p in plans[:i+1])))
    
        list_path = os.path.join(workdir, "nguon.txt")
        with open(list_path, "w", encoding="utf-8") as handle:
            for part in parts:
                handle.write("file '" + part.replace("'", "'\\''") + "'\n")
        joined = os.path.join(workdir, "video_noi.mp4")
        if progress:
            progress(85, "Đã cắt đủ thời lượng; đang nối các đoạn video")
        run(["ffmpeg", "-y", "-hide_banner", "-nostdin", "-f", "concat",
             "-safe", "0", "-i", list_path, "-c:v", "copy", "-an", joined],
            check=True, quiet=True)
    
        logo_cfg = dict(logo) if isinstance(logo, dict) else {}
        logo_path = str(logo_cfg.get("path") or "").strip().strip('"')
        logo_ok = bool(logo_cfg.get("enabled")) and os.path.isfile(logo_path)
        sub_path = ass_path if ass_path and os.path.exists(ass_path) else None
        cover_mode = str(source_cover or "none").strip().lower()
        has_cover = cover_mode in {"blur_bottom", "blur", "che_bottom", "cover_bottom", "black_bottom"}
        char_cfg = dict(character) if isinstance(character, dict) else {}
        char_ok = bool(char_cfg.get("enabled"))
        # Tìm file ảnh nhân vật PNG
        char_png = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "assets", "nhan_vat_mit.png")
        char_ok = char_ok and os.path.isfile(char_png)
        if progress:
            layers = []
            if sub_path:
                layers.append("phụ đề")
            if logo_ok:
                layers.append("logo")
            if char_ok:
                layers.append("nhân vật")
            if has_cover:
                layers.append("che/blur nguồn")
            suffix = ", ".join(layers) if layers else "hình và audio"
            progress(90, "Đang ghép lượt cuối: %s; FFmpeg vẫn đang chạy" % suffix)

        def _final_heartbeat(elapsed: float) -> None:
            if progress:
                progress(90, "FFmpeg vẫn đang ghép lượt cuối · đã chạy %02d:%02d"
                         % (int(elapsed) // 60, int(elapsed) % 60))

        if sub_path or logo_ok or has_cover or char_ok:
            cmd = ["ffmpeg", "-y", "-hide_banner", "-nostdin",
                   "-i", joined, "-i", audio_path]
            next_input = 2
            if logo_ok:
                cmd.extend(["-loop", "1", "-framerate", "1", "-i", logo_path])
                next_input += 1
            char_idx = -1
            if char_ok:
                cmd.extend(["-loop", "1", "-framerate", "1", "-i", char_png])
                char_idx = next_input
            graph = build_story_overlay_filter(
                w, h, logo_cfg if logo_ok else None, sub_path,
                logo_input_index=2, source_cover=cover_mode,
                character=char_cfg if char_ok else None,
                char_input_index=char_idx)
            cmd.extend(["-filter_complex", graph, "-map", "[v]", "-map", "1:a:0",
                        *_lenh_encode(fps, w=w, h=h), "-c:a", "aac", "-b:a", "192k",
                        "-shortest", "-movflags", "+faststart", out_path])
            run(cmd, check=True, quiet=True,
                heartbeat_callback=_final_heartbeat, heartbeat_interval=15)
        else:
            run(["ffmpeg", "-y", "-hide_banner", "-nostdin", "-i", joined,
                 "-i", audio_path, "-map", "0:v:0", "-map", "1:a:0",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                 "-movflags", "+faststart", out_path], check=True, quiet=True,
                heartbeat_callback=_final_heartbeat, heartbeat_interval=15)
        ra = ffprobe_duration(out_path)
        if ra <= 0:
            raise RuntimeError("Dựng video nguồn xong nhưng file rỗng.")
        if progress:
            progress(99, "Đã ghép xong MP4; đang kiểm tra file kết quả")
        return {"path": out_path, "duration": ra, "so_video": len(danh_sach),
                "so_doan": len(plans), "w": w, "h": h, "fps": fps,
                "hieu_ung": str(hieu_ung or "tinh")}
    finally:
        if os.path.isdir(workdir):
            shutil.rmtree(workdir, ignore_errors=True)
