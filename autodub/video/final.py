"""Render video cuối: ghép tiếng, blur, phụ đề, NVENC/x264."""
from __future__ import annotations

import os
import time
from typing import Dict, Optional, Sequence

from ..utils import (ffprobe_duration, ffprobe_fps, ffprobe_video_codec,
                    ffprobe_video_size, has_cuda_decode, has_nvenc, log,
                    nvenc_encode_args, run)
from ..media_clock import picture_duration_for
from .common import audio_duration_lock_chain
from .process import lock_audio_to_picture_duration
from .subs import _ffmpeg_sub_path, build_subtitle_style

def render_final(
    video: str,
    dub_wav: str,
    out_path: str,
    blur_bottom_ratio: float = 0.0,
    blur_strength: int = 20,
    keep_original_db: Optional[float] = None,
    subtitle_srt: Optional[str] = None,
    delogo: Optional[str] = None,
    regions: Optional[Sequence[Dict]] = None,
    use_gpu: bool = True,
    crf: int = 20,
    subtitle_style: Optional[dict] = None,
    force_h264: bool = True,
    x264_preset: str = "superfast",
    cpu_threads: int = 4,
) -> str:
    """Render video cuối.

    blur_bottom_ratio > 0 : làm mờ một dải ở ĐÁY màn hình (che sub gốc), ví dụ 0.18
                            = 18% chiều cao dưới cùng. =0 thì không re-encode video (copy, siêu nhanh).
    keep_original_db      : None = thay hẳn audio gốc bằng lồng tiếng.
                            số âm (vd -18) = trộn audio gốc ở mức nhỏ làm nền nhạc.
    subtitle_srt          : nếu có -> hardsub phụ đề tiếng Việt vào hình.
    use_gpu               : dùng h264_nvenc (RTX 3060) nếu máy hỗ trợ.
    """
    gpu = use_gpu and has_nvenc()
    cpu_threads = max(1, int(cpu_threads or 4))
    need_reencode = blur_bottom_ratio > 0 or bool(subtitle_srt) or bool(delogo) or bool(regions)
    src_dur = picture_duration_for(video) or ffprobe_duration(video)
    if src_dur <= 0:
        raise RuntimeError("Không đọc được thời lượng video gốc, dừng để tránh xuất file lỗi.")
    if dub_wav:
        dub_wav = lock_audio_to_picture_duration(dub_wav, src_dur)

    # Bilibili hay phát HEVC (H.265), có khi còn 10-bit. Chép nguyên luồng đó
    # sang MP4 thì file vẫn ĐÚNG CHUẨN nhưng Windows Photos / Movies & TV KHÔNG
    # mở được (báo "format is currently unsupported or the file is corrupted")
    # vì Windows không kèm sẵn bộ giải mã HEVC. Chuyển sang H.264 8-bit thì máy
    # nào, điện thoại nào, web nào cũng phát được.
    src_codec, src_pix = ffprobe_video_codec(video)
    ten_bit = "10" in src_pix or "12" in src_pix
    if force_h264 and not need_reencode and (src_codec not in ("h264", "") or ten_bit):
        need_reencode = True
        log(f"Video gốc là {src_codec.upper() or '?'}"
            + (f" {src_pix}" if ten_bit else "")
            + " - Windows Photos không mở được loại này. Đang chuyển sang H.264 "
              "cho mọi máy đều xem được (tắt bằng video.force_h264: false).", "info")

    inputs = ["-i", video, "-i", dub_wav]
    filters = []
    vlabel = "0:v"

    def _src():
        return vlabel if filters else "0:v"

    if blur_bottom_ratio > 0:
        r = min(0.6, max(0.02, blur_bottom_ratio))
        # crop dải đáy (crop hiểu iw/ih), làm mờ, rồi overlay lại ĐÁY.
        # overlay chỉ hiểu H (cao video chính) và h (cao lớp phủ) -> đặt y = H-h.
        crop_h = f"ih*{r:.4f}"
        crop_y = f"ih*{1.0 - r:.4f}"
        # avgblur thay cho gblur: cùng độ mờ nhìn bằng mắt (SSIM 0.99 so với
        # gblur=sigma=20) nhưng rẻ hơn ~2.6 lần vì nó cộng dồn theo hàng/cột,
        # chi phí không tăng theo bán kính. Trên chuỗi render đầy đủ (mờ + phụ
        # đề + x264) đo được nhanh hơn ~1.4 lần.
        filters.append(
            f"[0:v]crop=iw:{crop_h}:0:{crop_y},avgblur={max(1, int(blur_strength or 20))}[blur];"
            f"[0:v][blur]overlay=0:H-h[vb]"
        )
        vlabel = "vb"

    if delogo or regions:
        from .. import overlays
        vw, vh = ffprobe_video_size(video)
        vw, vh = vw or 1280, vh or 720

    if delogo:
        # Xoá mờ logo cháy cứng ở góc. Định dạng "x:y:w:h" (pixel).
        try:
            x, y, w, h = [int(float(v)) for v in str(delogo).split(":")]
        except Exception:
            log(f"Bỏ qua delogo (định dạng phải là 'x:y:w:h'): {delogo!r}", "warn")
        else:
            d = overlays.clamp_delogo({"x": x, "y": y, "w": w, "h": h}, vw, vh)
            if not d:
                log(f"Bỏ qua delogo {delogo!r}: không nằm trong khung {vw}x{vh} "
                    "hoặc vùng quá nhỏ.", "warn")
            else:
                if (d["x"], d["y"], d["w"], d["h"]) != (x, y, w, h):
                    log(f"delogo {x}:{y}:{w}:{h} chạm/vượt mép khung {vw}x{vh} - "
                        f"đã co về {d['x']}:{d['y']}:{d['w']}:{d['h']} (ffmpeg cần "
                        "chừa viền quanh vùng).", "warn")
                filters.append(f"[{_src()}]delogo=x={d['x']}:y={d['y']}"
                               f":w={d['w']}:h={d['h']}[vd]")
                vlabel = "vd"

    if regions:
        rg_filters, rg_label = overlays.build_overlay_filters(
            regions, vw, vh, src_label=_src())
        if rg_filters:
            filters.extend(rg_filters)
            vlabel = rg_label

    if subtitle_srt:
        sub = _ffmpeg_sub_path(subtitle_srt)
        style = build_subtitle_style(subtitle_style)
        filters.append(f"[{_src()}]subtitles='{sub}':charenc=UTF-8:"
                       f"force_style='{style}'[vs]")
        vlabel = "vs"
        log(f"Ghi phụ đề Việt lên hình ({style.split(',')[2]})", "info")

    # Audio: khóa đúng số mẫu video + aresample async để không trôi trên phim dài
    alabel = "1:a"
    lock = audio_duration_lock_chain(src_dur)
    if keep_original_db is not None:
        filters.append(
            f"[0:a]volume={keep_original_db}dB[bg];"
            f"[1:a]{lock}[dubpad];"
            f"[bg][dubpad]amix=inputs=2:duration=first:dropout_transition=0,"
            f"{lock}[aout]"
        )
        alabel = "aout"
    else:
        filters.append(f"[1:a]{lock}[aout]")
        alabel = "aout"

    src_fps = ffprobe_fps(video)
    src_w, src_h = ffprobe_video_size(video)

    def _video_args(use_nvenc: bool, hw: str):
        if not need_reencode:
            return ["-c:v", "copy"]
        if use_nvenc:
            # hw=full: khung chưa rời GPU nên -pix_fmt (đổi màu CPU) không áp được.
            return nvenc_encode_args(
                crf, pix_fmt=None if hw == "full" else "yuv420p",
                fps=src_fps, width=src_w, height=src_h)
        v = ["-c:v", "libx264", "-preset", str(x264_preset or "superfast"),
             "-crf", str(crf), "-threads", str(cpu_threads)]
        if hw == "full":
            return v + ["-profile:v", "high"]
        # yuv420p + profile high: mẫu số chung mà MỌI trình phát đều đọc được
        # (nguồn 10-bit sẽ được hạ về 8-bit ở đây).
        return v + ["-pix_fmt", "yuv420p", "-profile:v", "high"]

    tail = ["-c:a", "aac", "-b:a", "192k", "-ac", "2",
            "-movflags", "+faststart",      # cho phép phát/tua ngay khi chưa tải xong
            out_path]

    def _build_cmd(use_nvenc: bool, hw: str):
        """Dựng lệnh ffmpeg. hw = 'none' hoặc 'full'.

        'full' = khung hình nằm nguyên trên GPU từ lúc giải mã tới lúc mã
        hoá, không copy qua lại RAM lần nào. Đo trên RTX 3060 với clip 1080p
        3 phút: đổi HEVC sang H.264 mất 15.0s theo đường thường, còn 10.0s
        theo đường này.

        Chỉ dùng được khi KHÔNG có filter hình nào. Đã thử cả cách chỉ bật
        '-hwaccel cuda' rồi tải khung hình về RAM cho filter chạy: cùng clip
        đó, mờ đáy + phụ đề mất 26.7s không hwaccel nhưng 40.8s khi bật, vì
        tiền copy GPU->RAM->GPU đắt hơn tiền giải mã tiết kiệm được. Nên
        đường đó đã bị bỏ.
        """
        c = ["ffmpeg", "-y"]
        if hw == "full":
            c += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        c += inputs
        fl = list(filters)
        vlab = vlabel
        if hw == "full":
            fl.append("[0:v]scale_cuda=format=nv12[vhw]")
            vlab = "vhw"
        if fl:
            c += ["-filter_complex", ";".join(fl)]
        # vlabel chỉ là NHÃN filtergraph khi đã có filter VIDEO (blur/delogo/sub).
        # Nếu chỉ có filter AUDIO (vd bật keep_original_db mà tắt hardsub/blur/delogo)
        # thì vlabel vẫn là "0:v" - phải map THẲNG luồng gốc, KHÔNG bọc ngoặc vuông:
        # "[0:v]" là nhãn filtergraph không tồn tại -> ffmpeg báo lỗi và render chết.
        c += ["-map", f"[{vlab}]" if vlab != "0:v" else "0:v"]
        c += ["-map", f"[{alabel}]" if alabel == "aout" else alabel]
        return c + _video_args(use_nvenc, hw) + tail

    render_timeout = int(max(7200, src_dur * 1.5 + 1800)) if need_reencode \
        else int(max(3600, min(21600, src_dur * 0.20 + 1800)))

    # Chỉ hỏi GPU khi thật sự sắp mã hoá lại: lúc chỉ copy luồng thì không có
    # gì để giải mã, mà lúc có filter hình thì đường GPU lại chậm hơn.
    co_filter_hinh = vlabel != "0:v"
    if gpu and need_reencode and not co_filter_hinh and has_cuda_decode():
        bac_hw = ["full", "none"]
    else:
        bac_hw = ["none"]

    log("Render " + ("(GPU NVENC)" if gpu and need_reencode
                     else "(copy video)" if not need_reencode
                     else f"(CPU x264, threads={cpu_threads})")
        + (", giải mã luôn trên GPU" if bac_hw[0] == "full" else "") + " ...",
        "step")

    def _run_render(use_nvenc: bool, hw: str = "none"):
        """Chạy FFmpeg render với hiển thị tiến độ % (dựa trên thời gian đã xử lý)."""
        rcmd = _build_cmd(use_nvenc, hw)
        # Không có thời lượng hoặc video ngắn -> chạy bình thường không cần progress
        if src_dur < 1.0:
            run(rcmd, quiet=True, timeout=render_timeout)
            return

        # Chạy FFmpeg với -progress pipe:1 để đọc tiến độ realtime
        rcmd_prog = rcmd[:1] + ["-progress", "pipe:1"] + rcmd[1:]
        t_render = time.monotonic()
        last_pct = -1
        loi_ffmpeg = None
        err_tail = []

        def _render_line(raw: str) -> None:
            nonlocal last_pct
            line = str(raw or "").strip()
            if line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1])
                    pct = min(100, int(us / (src_dur * 1_000_000) * 100))
                    if pct >= last_pct + 5:
                        elapsed = time.monotonic() - t_render
                        if pct > 0:
                            eta = elapsed / pct * (100 - pct)
                            log(f"  Render: {pct}% | đã {elapsed:.0f}s | còn ~{eta:.0f}s", "info")
                        else:
                            log(f"  Render: {pct}% | đã {elapsed:.0f}s", "info")
                        last_pct = pct
                except (ValueError, ZeroDivisionError):
                    pass
            elif line:
                err_tail.append(line)
                del err_tail[:-80]

        try:
            result = run(rcmd_prog, check=False, quiet=True,
                         timeout=render_timeout, line_callback=_render_line)
            if result.returncode != 0:
                err = "\n".join(err_tail)[-2000:]
                # Ghi lại rồi ném ở NGOÀI khối try: ném ngay tại đây sẽ rơi
                # vào nhánh except bên dưới và render lại lần nữa cho một lệnh
                # đã biết chắc là hỏng.
                loi_ffmpeg = RuntimeError(
                    f"FFmpeg render lỗi ({result.returncode}):\n{err}")
        except InterruptedError:
            raise
        except FileNotFoundError:
            raise
        except Exception:
            # Fallback: lỗi pipe hoặc timeout -> chạy lại bình thường
            run(rcmd, quiet=True, timeout=render_timeout)

        if loi_ffmpeg is not None:
            raise loi_ffmpeg
        log(f"  Render xong trong {time.monotonic() - t_render:.1f}s.", "ok")

    def _loi_chuoi_filter(e: Exception) -> bool:
        """Sai sót trong chuỗi filter thì đổi encoder cũng hỏng y hệt."""
        return any(s in str(e) for s in ("Logo area is outside of the frame",
                                         "Error reinitializing filters",
                                         "Failed to configure",
                                         "Error initializing filter"))

    loi_cuoi = None
    for hw in bac_hw:
        try:
            _run_render(gpu, hw)
            loi_cuoi = None
            break
        except FileNotFoundError:
            raise
        except Exception as e:
            loi_cuoi = e
            if hw == "none":
                break
            # Máy nào không nuốt được đường GPU thì lùi một bậc. Những lỗi này
            # lộ ra ngay lúc dựng chuỗi filter nên không tốn mấy giây.
            log(f"Đường GPU '{hw}' không chạy được "
                f"({str(e).splitlines()[-1][:100]}) - thử lại bậc thấp hơn...",
                "warn")
    if loi_cuoi is not None:
        if not (gpu and need_reencode) or _loi_chuoi_filter(loi_cuoi):
            raise loi_cuoi
        # NVENC hay từ chối vài loại nguồn (10-bit, độ phân giải lạ, driver cũ).
        # Thà render bằng CPU chậm hơn còn hơn không ra file.
        log(f"NVENC không render được ({str(loi_cuoi).splitlines()[0][:100]}) - "
            "chuyển sang CPU x264...", "warn")
        _run_render(False)

    # Kiểm tra thành phẩm: có hình, có tiếng, đúng độ dài. Thà báo ngay còn hơn
    # để người dùng mở file rồi mới phát hiện hỏng.
    out_codec, out_pix = ffprobe_video_codec(out_path)
    out_dur = ffprobe_duration(out_path)
    src_dur = picture_duration_for(video) or ffprobe_duration(video)
    max_drift = max(2.0, src_dur * 0.05)
    if not out_codec:
        raise RuntimeError("File xuất ra KHÔNG có luồng hình - render hỏng.")
    elif src_dur > 0 and out_dur + max_drift < src_dur:
        raise RuntimeError(
            f"File xuất bị cắt cụt: {out_dur:.1f}s trong khi video gốc {src_dur:.1f}s.")
    elif src_dur > 0 and abs(out_dur - src_dur) > max_drift:
        log(f"File xuất ra dài {out_dur:.1f}s trong khi video gốc {src_dur:.1f}s "
            "- có thể bị cắt cụt.", "warn")
    elif out_codec == "h264" and out_pix == "yuv420p":
        log(f"Video cuối: H.264 8-bit, {out_dur:.1f}s - phát được trên Windows "
            "Photos / điện thoại / web.", "ok")
    else:
        log(f"Video cuối: {out_codec.upper()} {out_pix}, {out_dur:.1f}s. Lưu ý "
            "Windows Photos có thể KHÔNG mở được - dùng VLC, hoặc bật "
            "video.force_h264: true.", "warn")
    return out_path
