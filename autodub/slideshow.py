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
from typing import Dict, List, Optional, Sequence

from .utils import log, run, ffprobe_duration, has_nvenc, nvenc_encode_args

ANH_HOP_LE = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

MIN_GIAY_MOI_ANH = 2.0
MAX_GIAY_MOI_ANH = 25.0


def liet_ke_anh(paths: Sequence[str]) -> List[str]:
    """Gom danh sách ảnh từ các đường dẫn file hoặc thư mục, giữ thứ tự tự nhiên."""
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


def tao_video_tu_anh(anh: Sequence[str],
                     audio_path: str,
                     out_path: str,
                     workdir: Optional[str] = None,
                     w: int = 1920, h: int = 1080, fps: int = 30,
                     kieu: str = "chuyen_dong",
                     seed: Optional[int] = None,
                     ass_path: Optional[str] = None,
                     progress=None) -> Dict:
    """Dựng video từ ảnh, dài đúng bằng file âm thanh.

    `anh` nhận cả file lẫn thư mục. Ảnh ít hơn nhu cầu thì được dùng lặp lại
    theo vòng, nhiều hơn thì lấy bớt cho mỗi ảnh đủ thời gian nhìn.
    `ass_path`: có thì GHI CỨNG phụ đề lên hình (thêm một lượt encode cuối).
    Khổ dọc 9:16 chỉ là w/h khác (vd 1080x1920) - mọi filter đều theo w/h.
    """
    danh_sach = liet_ke_anh(anh)
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

    if ass_path and os.path.exists(ass_path):
        # Có phụ đề cứng: nối cảnh (copy, nhanh) rồi một lượt encode cuối để
        # burn chữ. Burn ngay lúc dựng từng cảnh thì phải dịch mốc thời gian
        # phụ đề theo từng cảnh - rắc rối và dễ lệch.
        noi_tam = os.path.join(workdir, "noi_video.mp4")
        run(["ffmpeg", "-y", "-hide_banner", "-nostdin",
             "-f", "concat", "-safe", "0", "-i", danh_sach_file,
             "-c:v", "copy", "-an", noi_tam], check=True, quiet=True)
        if progress:
            progress(88, "Đang ghi phụ đề lên hình")
        run(["ffmpeg", "-y", "-hide_banner", "-nostdin",
             "-i", noi_tam, "-i", audio_path,
             "-filter_complex",
             f"[0:v]ass='{_duong_dan_ass_cho_ffmpeg(ass_path)}'[v]",
             "-map", "[v]", "-map", "1:a:0",
             *_lenh_encode(fps, w=w, h=h),
             "-c:a", "aac", "-b:a", "192k",
             "-shortest", "-movflags", "+faststart", out_path],
            check=True, quiet=True)
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
            "w": w, "h": h, "fps": fps}
