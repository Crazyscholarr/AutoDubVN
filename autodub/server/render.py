"""Xuất video cho GUI: xem trước 1 khung hình, render đủ 3 lớp, render chia phần.

Đường render chọn theo thứ tự rẻ -> đắt: MP4Box mux (chỉ thay audio) ->
copy video -> NVENC -> h264_mf -> CPU x264. Render chia phần có cache theo
nội dung để chạy lại không phải render lại phần chưa đổi.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Dict, List, Optional, Tuple

from .. import overlays
from ..srt_utils import Segment
from ..utils import (log, run, ffprobe_duration, has_nvenc,
                     ffprobe_video_codec, ffprobe_fps, nvenc_encode_args,
                     has_h264_mf, resolve_keep_original_db, which)
from .state import HERE, _log
from .projects import get_project, _segments_from_project

_RENDER_PART_CACHE_VERSION = 2


# --------------------------------------------------------------------------- #
#  Xem trước: render 1 khung hình đã áp đủ 3 lớp
# --------------------------------------------------------------------------- #
def render_preview(job_id: int, t: float, out_png: str) -> str:
    pr = get_project(job_id)
    if not pr:
        raise ValueError("Không có dự án")
    vw, vh = pr["w"], pr["h"]

    ass_path = None
    segs = _segments_from_project(pr)
    if segs:
        tmp_dir = os.path.join(HERE, "output", "_preview")
        os.makedirs(tmp_dir, exist_ok=True)
        ass_path = os.path.join(tmp_dir, f"prev_{job_id}.ass")
        overlays.save_ass(ass_path, segs, vw, vh, pr.get("sub_style"))

    logo = pr.get("logo")
    # -copyts giữ nguyên mốc thời gian gốc sau khi tua nhanh, nếu không filter
    # 'ass' sẽ tưởng đang ở giây 0 và không vẽ dòng phụ đề nào.
    inputs = ["-ss", f"{max(0.0, float(t)):.3f}", "-copyts", "-i", pr["video"]]
    logo_idx = None
    if logo and logo.get("path") and os.path.exists(logo["path"]):
        inputs += ["-i", logo["path"]]
        logo_idx = 1

    filters, vlabel = overlays.build_overlay_filters(
        pr.get("regions", []), vw, vh, ass_path=ass_path,
        logo_input_index=logo_idx, logo=logo)

    cmd = ["ffmpeg", "-y", *inputs]
    if filters:
        cmd += ["-filter_complex", ";".join(filters), "-map", f"[{vlabel}]"]
    else:
        cmd += ["-map", "0:v"]
    cmd += ["-frames:v", "1", "-update", "1", "-q:v", "3", out_png]
    run(cmd)
    return out_png


# --------------------------------------------------------------------------- #
#  Render helpers
# --------------------------------------------------------------------------- #
def _render_timeout_seconds(duration: float, reencode: bool = True) -> int:
    """Give long videos enough time to finish instead of killing FFmpeg at 1 hour."""
    duration = max(0.0, float(duration or 0.0))
    if reencode:
        return int(max(7200, duration * 1.5 + 1800))
    return int(max(3600, min(21600, duration * 0.20 + 1800)))


def _is_timeout_error(exc: BaseException) -> bool:
    return "Lenh chay qua" in str(exc) or "timed out" in str(exc).lower()


def _render_gpu_then_cpu(base, gpu_args, cpu_args, tail, timeout: int):
    """Thử NVENC trước; hỏng thì tự chạy lại bằng CPU.

    has_nvenc() chỉ kiểm tra ffmpeg CÓ LIỆT KÊ encoder hay không, chứ không biết
    máy có GPU dùng được hay không. Driver cũ, GPU đang bận, độ phân giải lạ
    hoặc chạy trong máy ảo đều làm NVENC hỏng - khi đó phải tự lùi về CPU thay
    vì báo lỗi cho người dùng.
    """
    try:
        run(base + gpu_args + tail, timeout=timeout)
        return
    except RuntimeError as e:
        if _is_timeout_error(e):
            raise RuntimeError(
                "NVENC chưa hỏng, chỉ là video quá dài nên render chưa xong trong "
                f"{timeout/3600:.1f} giờ. Không tự chuyển CPU vì sẽ chậm hơn nhiều."
            ) from e
        _log(f"NVENC không dùng được ({str(e)[:120]}…). Đang render lại bằng CPU.",
             "warn")
    run(base + cpu_args + tail, timeout=timeout)


def _render_mp4box_replace_audio(video: str, dub_wav: str, out_path: str,
                                 mp4box: str) -> None:
    audio_mp4 = out_path + ".audio.m4a"
    try:
        src_dur = ffprobe_duration(video)
        timeout = _render_timeout_seconds(src_dur, reencode=False)
        cmd = ["ffmpeg", "-y", "-i", dub_wav, "-vn"]
        if src_dur > 0:
            cmd += ["-af", f"apad,atrim=0:{src_dur:.3f},asetpts=N/SR/TB"]
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", audio_mp4]
        run(cmd, timeout=timeout)
        run([mp4box, "-quiet", "-add", f"{video}#video",
             "-add", f"{audio_mp4}#audio", "-new", out_path], timeout=timeout)
        run([mp4box, "-quiet", "-inter", "500", out_path], check=False, timeout=timeout)
    finally:
        try:
            if os.path.exists(audio_mp4):
                os.remove(audio_mp4)
        except OSError:
            pass


def _chunk_bounds(duration: float, chunk_seconds: float) -> List[Tuple[float, float]]:
    duration = max(0.0, float(duration or 0.0))
    chunk_seconds = max(60.0, float(chunk_seconds or 0.0))
    out: List[Tuple[float, float]] = []
    start = 0.0
    while start < duration - 0.01:
        end = min(duration, start + chunk_seconds)
        out.append((start, max(0.01, end - start)))
        start = end
    return out


def _local_regions_for_chunk(regions: List[Dict], chunk_start: float,
                             chunk_duration: float) -> List[Dict]:
    chunk_end = chunk_start + chunk_duration
    out: List[Dict] = []
    for raw in regions or []:
        r = dict(raw)
        start = r.get("start")
        end = r.get("end")
        try:
            start_f = None if start in (None, "") else float(start)
        except (TypeError, ValueError):
            start_f = None
        try:
            end_f = None if end in (None, "") else float(end)
        except (TypeError, ValueError):
            end_f = None

        if start_f is None and end_f is None:
            out.append(r)
            continue

        abs_start = 0.0 if start_f is None else max(0.0, start_f)
        abs_end = chunk_end if end_f is None else max(0.0, end_f)
        local_start = max(abs_start, chunk_start) - chunk_start
        local_end = min(abs_end, chunk_end) - chunk_start
        if local_end <= 0 or local_start >= chunk_duration or local_end <= local_start:
            continue
        r["start"] = round(max(0.0, local_start), 3)
        r["end"] = round(min(chunk_duration, local_end), 3)
        out.append(r)
    return out


def _local_segments_for_chunk(segments: List[Segment], chunk_start: float,
                              chunk_duration: float) -> List[Segment]:
    chunk_end = chunk_start + chunk_duration
    out: List[Segment] = []
    for seg in segments or []:
        base_start = seg.placed_start if seg.placed_start is not None else seg.start
        base_end = base_start + max(seg.duration, float(seg.voice_duration or 0.0), 0.01)
        if base_end <= chunk_start or base_start >= chunk_end:
            continue
        local_placed = max(base_start, chunk_start) - chunk_start
        local_end = min(base_end, chunk_end) - chunk_start
        local_voice_dur = max(0.01, local_end - local_placed)
        child = Segment(
            index=len(out) + 1,
            start=max(seg.start, chunk_start) - chunk_start,
            end=min(seg.end, chunk_end) - chunk_start,
            text=seg.text,
            speaker=seg.speaker,
            audio_path=seg.audio_path,
            voice=seg.voice,
            placed_start=local_placed,
            speed=seg.speed,
            voice_duration=local_voice_dur,
        )
        if child.end <= child.start:
            child.end = min(chunk_duration, child.start + 0.01)
        out.append(child)
    return out


def _file_cache_signature(path: Optional[str], *, hash_contents: bool = False) -> Optional[Dict]:
    if not path:
        return None
    abspath = os.path.abspath(str(path))
    try:
        st = os.stat(abspath)
    except OSError:
        return {"path": abspath, "missing": True}
    sig: Dict = {"path": abspath, "size": int(st.st_size)}
    if hash_contents:
        h = hashlib.sha256()
        with open(abspath, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        sig["sha256"] = h.hexdigest()
    else:
        sig["mtime_ns"] = int(st.st_mtime_ns)
    return sig


def _stable_cache_hash(payload: Dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _render_part_cache_key(pr: Dict, dub_wav: Optional[str], ass_path: Optional[str],
                           chunk_start: float, chunk_duration: float) -> str:
    logo = pr.get("logo") if isinstance(pr.get("logo"), dict) else None
    logo_path = logo.get("path") if logo else None
    payload = {
        "version": _RENDER_PART_CACHE_VERSION,
        "chunk": [round(float(chunk_start), 3), round(float(chunk_duration), 3)],
        "video": _file_cache_signature(pr.get("video")),
        "dub": _file_cache_signature(dub_wav),
        "ass": _file_cache_signature(ass_path, hash_contents=True),
        "logo_file": _file_cache_signature(logo_path),
        "w": pr.get("w"),
        "h": pr.get("h"),
        "source_offset": round(float(pr.get("source_offset") or 0.0), 3),
        "duration": round(float(pr.get("duration") or 0.0), 3),
        "regions": pr.get("regions", []),
        "logo": logo,
        "sub_style": pr.get("sub_style"),
        "options": pr.get("options", {}),
    }
    return _stable_cache_hash(payload)


def _concat_mp4_parts(parts: List[str], out_path: str) -> None:
    list_path = out_path + ".parts.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for part in parts:
            safe = os.path.abspath(part).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe}'\n")
    try:
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-c", "copy", "-movflags", "+faststart", out_path],
            timeout=max(3600, len(parts) * 900))
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass


def render_with_layers_chunked(pr: Dict, dub_wav: Optional[str], out_path: str,
                               ass_path: Optional[str], segments: List[Segment],
                               tmp_dir: str) -> str:
    opt = pr.get("options", {})
    chunk_minutes = float(opt.get("render_chunk_minutes") or 0.0)
    chunk_seconds = chunk_minutes * 60.0
    duration = float(pr.get("duration") or ffprobe_duration(pr["video"]) or 0.0)
    chunks = _chunk_bounds(duration, chunk_seconds)
    if len(chunks) <= 1:
        has_offset = float(pr.get("source_offset") or 0.0) > 0.001
        return render_with_layers(
            pr, dub_wav, out_path, ass_path,
            clip_duration=duration if has_offset else None,
            validate_full_source=not has_offset,
        )

    part_dir = os.path.join(tmp_dir, "render_parts")
    os.makedirs(part_dir, exist_ok=True)
    manifest_path = out_path + ".render_parts.json"
    parts: List[str] = []
    manifest = {
        "source": pr.get("video"),
        "output": out_path,
        "chunk_minutes": chunk_minutes,
        "duration": duration,
        "parts": [],
    }
    old_manifest = {}
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                old_manifest = json.load(f) or {}
        except Exception:
            old_manifest = {}
    old_parts = {}
    old_manifest_parts = old_manifest.get("parts", []) if isinstance(old_manifest, dict) else []
    for item in old_manifest_parts:
        try:
            old_parts[int(item.get("index"))] = item
        except Exception:
            continue
    _log(f"Chia render: {len(chunks)} phần, mỗi phần khoảng {chunk_minutes:g} phút.", "step")
    for idx, (start, dur) in enumerate(chunks, 1):
        part = os.path.join(part_dir, f"part_{idx:04d}.mp4")
        cache_key = _render_part_cache_key(pr, dub_wav, ass_path, start, dur)
        part_info = {
            "index": idx,
            "start": round(start, 3),
            "end": round(start + dur, 3),
            "duration": round(dur, 3),
            "path": part,
            "cache_key": cache_key,
        }
        if os.path.exists(part):
            cached = old_parts.get(idx, {})
            if cached.get("cache_key") == cache_key:
                part_dur = ffprobe_duration(part)
                part_codec, _ = ffprobe_video_codec(part)
                if part_codec and part_dur + max(2.0, dur * 0.05) >= dur:
                    _log(f"Dùng lại phần {idx}/{len(chunks)} đã render: {part}", "ok")
                    part_info["reused"] = True
                    manifest["parts"].append(part_info)
                    parts.append(part)
                    continue
            else:
                _log(f"Render lại phần {idx}/{len(chunks)} vì cache cũ khác audio/sub/tùy chọn.", "info")

        local_pr = dict(pr)
        local_pr["duration"] = dur
        local_pr["regions"] = _local_regions_for_chunk(pr.get("regions", []), start, dur)

        local_ass = None
        if ass_path and segments:
            local_segments = _local_segments_for_chunk(segments, start, dur)
            local_ass = os.path.join(part_dir, f"part_{idx:04d}.ass")
            overlays.save_ass(local_ass, local_segments, pr["w"], pr["h"],
                              pr.get("sub_style"), use_placed=True)

        _log(f"Render phần {idx}/{len(chunks)}: {start/60:.1f} -> {(start+dur)/60:.1f} phút", "step")
        render_with_layers(local_pr, dub_wav, part, local_ass,
                           clip_start=start, clip_duration=dur,
                           validate_full_source=False)
        manifest["parts"].append(part_info)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        parts.append(part)

    _log("Ghép các phần render lại...", "step")
    _concat_mp4_parts(parts, out_path)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return out_path


def render_with_layers(pr: Dict, dub_wav: Optional[str], out_path: str,
                       ass_path: Optional[str], clip_start: float = 0.0,
                       clip_duration: Optional[float] = None,
                       validate_full_source: bool = True) -> str:
    """Xuất video cuối với đủ 3 lớp + tiếng lồng."""
    opt = pr.get("options", {})
    vw, vh = pr["w"], pr["h"]
    logo = pr.get("logo")
    clip_start = max(0.0, float(clip_start or 0.0))
    source_offset = max(0.0, float(pr.get("source_offset") or 0.0))
    src_dur = float(clip_duration or pr.get("duration") or ffprobe_duration(pr["video"]) or 0.0)
    if src_dur <= 0:
        raise RuntimeError("Không đọc được thời lượng video gốc, dừng để tránh xuất file lỗi.")

    def _media_input(path: str, start: float = 0.0,
                     duration: Optional[float] = None) -> List[str]:
        args: List[str] = []
        start = max(0.0, float(start or 0.0))
        if start > 0:
            args += ["-ss", f"{start:.3f}"]
        if duration is not None:
            args += ["-t", f"{max(0.01, float(duration)):.3f}"]
        args += ["-i", path]
        return args

    video_limit = src_dur if (clip_duration is not None or source_offset > 0) else None
    audio_limit = src_dur if clip_duration is not None else None
    inputs = _media_input(pr["video"], source_offset + clip_start, video_limit)
    idx = 1
    dub_idx = None
    if dub_wav and os.path.exists(dub_wav):
        inputs += _media_input(dub_wav, clip_start, audio_limit)
        dub_idx = idx
        idx += 1
    logo_idx = None
    if logo and logo.get("path") and os.path.exists(logo["path"]):
        inputs += ["-i", logo["path"]]
        logo_idx = idx
        idx += 1

    filters, vlabel = overlays.build_overlay_filters(
        pr.get("regions", []), vw, vh, ass_path=ass_path,
        logo_input_index=logo_idx, logo=logo)
    video_has_filters = bool(filters)

    # Âm thanh
    alabel = None
    keep_db = resolve_keep_original_db(opt)
    if dub_idx is not None and keep_db is not None:
        filters.append(
            f"[0:a]volume={float(keep_db)}dB[bg];"
            f"[{dub_idx}:a]apad,atrim=0:{src_dur:.3f},asetpts=N/SR/TB[dubpad];"
            f"[bg][dubpad]amix=inputs=2:duration=first:"
            f"dropout_transition=0,apad,atrim=0:{src_dur:.3f},asetpts=N/SR/TB[aout]")
        alabel = "[aout]"
        _log(f"Giu am goc: {keep_db:+.1f} dB", "info")
    elif dub_idx is not None:
        filters.append(
            f"[{dub_idx}:a]apad,atrim=0:{src_dur:.3f},asetpts=N/SR/TB[aout]")
        alabel = "[aout]"
    else:
        filters.append(
            f"[0:a]apad,atrim=0:{src_dur:.3f},asetpts=N/SR/TB[aout]")
        alabel = "[aout]"

    cmd = ["ffmpeg", "-y", *inputs]
    if filters:
        cmd += ["-filter_complex", ";".join(filters)]
    if video_has_filters:
        cmd += ["-map", f"[{vlabel}]"]
    else:
        cmd += ["-map", "0:v"]
    cmd += ["-map", alabel]

    # Video gốc là HEVC/AV1 hoặc 10-bit thì Windows Photos không mở được ->
    # ép sang H.264 8-bit kể cả khi không có lớp phủ nào.
    src_codec, src_pix = ffprobe_video_codec(pr["video"])
    ten_bit = "10" in (src_pix or "") or "12" in (src_pix or "")
    force_h264 = opt.get("force_h264", True)
    if force_h264 and not video_has_filters and (src_codec not in ("h264", "") or ten_bit):
        log(f"Video gốc là {(src_codec or '?').upper()}"
            + (f" {src_pix}" if ten_bit else "")
            + " - đang chuyển sang H.264 cho mọi máy đều xem được.", "info")
        filters_needed = True
    else:
        filters_needed = video_has_filters

    crf = str(opt.get("crf", 20))
    x264_preset = str(opt.get("x264_preset", "superfast") or "superfast")
    cpu_threads = max(1, int(opt.get("cpu_threads", 4) or 4))
    gpu_args = nvenc_encode_args(
        crf, fps=ffprobe_fps(pr["video"]), width=vw, height=vh)
    mf_args   = ["-c:v", "h264_mf", "-quality", "quality",
                 "-pix_fmt", "yuv420p"]           # Windows MediaFoundation HW
    cpu_args  = ["-c:v", "libx264", "-preset", x264_preset, "-profile:v", "high",
                 "-pix_fmt", "yuv420p", "-crf", crf, "-threads", str(cpu_threads)]
    tail = ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            out_path]

    use_nvenc = filters_needed and opt.get("use_gpu", True) and has_nvenc()
    # h264_mf (Windows MediaFoundation) hay nhận encoder nhưng chết khi gặp
    # filter/subtitle/đường dẫn Unicode. Mặc định bỏ qua để đi thẳng CPU x264
    # ổn định; ai muốn tự thử có thể đặt video.try_h264_mf: true.
    use_mf = (not use_nvenc and filters_needed
              and bool(opt.get("try_h264_mf", False)) and has_h264_mf())
    render_backend = str(opt.get("render_backend", "auto") or "auto").lower()
    mp4box_path = which("MP4Box")
    use_mp4box = (not filters_needed and dub_wav and os.path.exists(dub_wav)
                  and keep_db is None and mp4box_path
                  and render_backend in ("auto", "mp4box")
                  and clip_duration is None and source_offset <= 0.001)
    render_timeout = _render_timeout_seconds(src_dur, reencode=filters_needed)

    if use_mp4box:
        mode = "MP4Box mux"
        _log("Render (MP4Box mux - copy video, thay audio)...", "step")
    elif not filters_needed:
        mode = "copy video"
        _log("Render (copy video)...", "step")
    elif use_nvenc:
        mode = "GPU NVENC"
        _log("Render (GPU NVENC)...", "step")
    elif use_mf:
        mode = "Windows MediaFoundation HW"
        _log("Render (h264_mf - Windows HW accel)...", "step")
    else:
        _log(f"Render (CPU x264, preset={x264_preset}, threads={cpu_threads})...", "step")

    t_render = time.time()
    if use_mp4box:
        try:
            _render_mp4box_replace_audio(pr["video"], dub_wav, out_path, mp4box_path)
        except Exception as e:
            _log(f"MP4Box loi ({str(e)[:120]}) - fallback FFmpeg copy video...", "warn")
            run(cmd + ["-c:v", "copy"] + tail,
                timeout=_render_timeout_seconds(src_dur, reencode=False))
    elif not filters_needed:
        run(cmd + ["-c:v", "copy"] + tail, timeout=render_timeout)
    elif use_nvenc:
        _render_gpu_then_cpu(cmd, gpu_args, cpu_args, tail, timeout=render_timeout)
    elif use_mf:
        try:
            run(cmd + mf_args + tail, timeout=render_timeout)
        except Exception as e:
            _log(f"h264_mf l\u1ed7i ({str(e)[:80]}) - chuy\u1ec3n sang CPU x264...", "warn")
            run(cmd + cpu_args + tail, timeout=render_timeout)
    else:
        run(cmd + cpu_args + tail, timeout=render_timeout)
    _log(f"Render xong trong {time.time() - t_render:.1f}s.", "ok")

    # Kiểm tra file xuất ra có thật sự dùng được không, thay vì báo "xong" mù quáng
    out_codec, _ = ffprobe_video_codec(out_path)
    out_dur = ffprobe_duration(out_path)
    expected_dur = src_dur if not validate_full_source else ffprobe_duration(pr["video"])
    max_drift = max(2.0, expected_dur * 0.05)
    if not out_codec:
        raise RuntimeError("File xuất ra KHÔNG có luồng hình - render hỏng.")
    elif expected_dur > 0 and out_dur + max_drift < expected_dur:
        raise RuntimeError(
            f"File xuất bị cắt cụt: {out_dur:.1f}s trong khi cần {expected_dur:.1f}s.")
    elif expected_dur > 0 and abs(out_dur - expected_dur) > max_drift:
        _log(f"File xuất dài {out_dur:.1f}s trong khi cần {expected_dur:.1f}s - "
             "hãy kiểm tra lại.", "warn")
    else:
        _log(f"Kiểm tra file xuất: {out_codec.upper()}, {out_dur:.1f}s - OK", "ok")
    return out_path
