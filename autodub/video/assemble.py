"""Trộn timeline lồng tiếng và nối nhiều clip audio."""
from __future__ import annotations

import math
import os
import shutil
import tempfile
import time
from typing import Dict, List, Optional, Sequence

from ..utils import ffprobe_duration, log, run, raise_if_cancelled
from .common import _audio_encode_args, seconds_to_samples, media_temp_path, _discard_partial

def _timeline_pcm_gib(total_duration: float, sr: int,
                      channels: int = 2, bytes_per_sample: int = 2) -> float:
    return (max(0.0, float(total_duration)) * sr * channels * bytes_per_sample) / (1024 ** 3)


def _timeline_output_path(out_path: str, total_duration: float, sr: int) -> str:
    """Avoid plain WAV for long dub tracks.

    WAV is uncompressed, so a few hours of stereo 48 kHz audio can eat multiple
    GiB and fail on low-free-space drives even below the 4 GiB WAV danger zone.
    FLAC keeps silence tiny and is accepted by the render step.
    """
    if (os.path.splitext(out_path)[1].lower() == ".wav"
            and (float(total_duration or 0.0) >= 3600
                 or _timeline_pcm_gib(total_duration, sr) >= 1.0)):
        return os.path.splitext(out_path)[0] + ".flac"
    return out_path


def _mix_batch(items, total_ms, out_wav, sr, label="",
               window_start_ms: float = 0.0,
               clip_durations_ms: Optional[Dict[str, float]] = None):
    """Mix clips into one audio window.

    window_start_ms lets the FFmpeg fallback render short timeline windows instead
    of producing a full-duration WAV for every batch.
    """
    if label:
        log(f"  {label}: trộn {len(items)} clip...", "info")
    t0 = time.monotonic()
    window_ms = max(1.0, float(total_ms))
    clip_durations_ms = clip_durations_ms or {}
    prepared = []
    for path, start_ms in items:
        start_ms = float(start_ms)
        rel_ms = start_ms - float(window_start_ms)
        skip_ms = max(0.0, -rel_ms)
        delay_ms = max(0.0, rel_ms)
        if delay_ms >= window_ms:
            continue
        keep_ms = window_ms - delay_ms
        dur_ms = clip_durations_ms.get(path)
        if dur_ms is not None and dur_ms > 0:
            keep_ms = min(keep_ms, max(0.0, float(dur_ms) - skip_ms))
        if keep_ms <= 1.0:
            continue
        prepared.append((path, delay_ms, skip_ms, keep_ms))

    if not prepared:
        window_samples = max(1, seconds_to_samples(window_ms / 1000.0, sr))
        pad_s = window_samples / float(sr) + 0.05
        run(["ffmpeg", "-y", "-f", "lavfi", "-t", f"{pad_s:.6f}",
             "-i", f"anullsrc=r={sr}:cl=stereo",
             "-af", f"apad,atrim=end_sample={window_samples},asetpts=PTS-STARTPTS",
             "-ac", "2", "-ar", str(sr), *_audio_encode_args(out_wav), out_wav])
        if label:
            log(f"  {label}: xong trong {time.monotonic() - t0:.1f}s.", "ok")
        return out_wav

    inputs = []
    for path, *_ in prepared:
        inputs += ["-i", path]
    parts, labels = [], []
    for i, (_, delay_ms, skip_ms, keep_ms) in enumerate(prepared):
        skip_samples = seconds_to_samples(skip_ms / 1000.0, sr)
        keep_samples = max(1, seconds_to_samples(keep_ms / 1000.0, sr))
        chain = (f"[{i}:a]aresample={sr},"
                 f"atrim=start_sample={skip_samples}:end_sample={skip_samples + keep_samples},"
                 "asetpts=PTS-STARTPTS")
        d = max(0, int(round(delay_ms)))
        if d:
            chain += f",adelay={d}:all=1"
        parts.append(f"{chain}[a{i}]")
        labels.append(f"[a{i}]")
    if len(prepared) == 1:
        graph = parts[0].replace("[a0]", "[mixed]")
    else:
        graph = ";".join(parts) + ";" + "".join(labels) + \
            f"amix=inputs={len(prepared)}:normalize=0:dropout_transition=0[mixed]"
    # Ép đúng số mẫu của cửa sổ — cắt theo giây .3f rồi concat sẽ co timeline
    # trên phim nhiều tiếng, làm thoại chạy trước hình càng về cuối càng lệch.
    window_samples = max(1, seconds_to_samples(window_ms / 1000.0, sr))
    graph += (f";[mixed]apad,atrim=end_sample={window_samples},"
              "asetpts=PTS-STARTPTS[out]")
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", graph,
           "-map", "[out]", "-ac", "2", "-ar", str(sr), *_audio_encode_args(out_wav), out_wav]
    run(cmd)
    if label:
        log(f"  {label}: xong trong {time.monotonic() - t0:.1f}s.", "ok")
    return out_wav


def _assemble_torch(
    items: List[tuple],
    total_duration: float,
    out_wav: str,
    sr: int = 48000,
) -> str:
    """Ghép timeline bằng torchaudio — KHÔNG spawn FFmpeg cho mỗi clip.

    Thuật toán:
      1. Tạo tensor zeros 2×N (stereo) kích thước đúng độ dài video.
      2. Load từng clip WAV vào tensor, cộng dồn tại offset đúng.
      3. Clamp [-1, 1] rồi save 1 lần duy nhất.

    Nhanh hơn FFmpeg filter_complex ~10-20× vì không có overhead spawn process.
    """
    import torch
    import torchaudio

    total_samples = int(math.ceil(total_duration * sr)) + sr  # thêm 1s đệm
    mixed = torch.zeros(2, total_samples, dtype=torch.float32)

    ok = 0
    for path, start_sec in items:
        raise_if_cancelled()
        try:
            wav, orig_sr = torchaudio.load(path)
        except InterruptedError:
            raise
        except Exception as e:
            raise RuntimeError(f"Không đọc được clip {os.path.basename(path)}") from e
        # Resample nếu clip có sr khác
        if orig_sr != sr:
            wav = torchaudio.functional.resample(wav, orig_sr, sr)
        # Ép stereo
        if wav.shape[0] == 1:
            wav = wav.expand(2, -1)
        elif wav.shape[0] > 2:
            wav = wav[:2]
        start_s = max(0, int(round(float(start_sec) * sr)))
        end_s = start_s + wav.shape[1]
        if start_s >= total_samples:
            continue
        if end_s > total_samples:
            wav = wav[:, :total_samples - start_s]
            end_s = total_samples
        mixed[:, start_s:end_s] += wav
        ok += 1

    mixed = mixed.clamp(-1.0, 1.0)
    torchaudio.save(out_wav, mixed, sr)
    return out_wav


def _load_clip_for_stream(path: str, sr: int):
    import torchaudio

    wav, orig_sr = torchaudio.load(path)
    if orig_sr != sr:
        wav = torchaudio.functional.resample(wav, orig_sr, sr)
    if wav.shape[0] == 1:
        wav = wav.expand(2, -1)
    elif wav.shape[0] > 2:
        wav = wav[:2]
    return wav.numpy().astype("float32", copy=False)


def _assemble_stream_torch(
    items: List[tuple],
    total_duration: float,
    out_wav: str,
    sr: int = 48000,
    chunk_seconds: float = 30.0,
) -> str:
    """Ghép audio theo từng chunk để video nhiều giờ không ngốn hàng chục GB RAM."""
    import numpy as np
    import soundfile as sf

    items = sorted(items, key=lambda x: x[1])
    total_samples = int(math.ceil(total_duration * sr)) + sr
    chunk_samples = max(sr, int(chunk_seconds * sr))
    active = []  # (start_sample, end_sample, wav_2ch)
    next_i = 0
    ok = 0

    with sf.SoundFile(out_wav, mode="w", samplerate=sr, channels=2,
                      subtype="PCM_16") as writer:
        for chunk_start in range(0, total_samples, chunk_samples):
            raise_if_cancelled()
            chunk_end = min(total_samples, chunk_start + chunk_samples)
            while next_i < len(items):
                path, start_sec = items[next_i]
                start_sample = max(0, int(round(float(start_sec) * sr)))
                if start_sample >= chunk_end:
                    break
                next_i += 1
                if start_sample >= total_samples:
                    continue
                try:
                    wav = _load_clip_for_stream(path, sr)
                except InterruptedError:
                    raise
                except Exception as e:
                    raise RuntimeError(f"Không đọc được clip {os.path.basename(path)}") from e
                end_sample = min(total_samples, start_sample + wav.shape[1])
                if end_sample <= chunk_start:
                    continue
                active.append((start_sample, end_sample, wav))
                ok += 1

            buf = np.zeros((2, chunk_end - chunk_start), dtype=np.float32)
            kept = []
            for start_sample, end_sample, wav in active:
                if end_sample <= chunk_start:
                    continue
                if start_sample < chunk_end:
                    ov_start = max(start_sample, chunk_start)
                    ov_end = min(end_sample, chunk_end)
                    src_a = ov_start - start_sample
                    src_b = ov_end - start_sample
                    dst_a = ov_start - chunk_start
                    dst_b = ov_end - chunk_start
                    buf[:, dst_a:dst_b] += wav[:, src_a:src_b]
                if end_sample > chunk_end:
                    kept.append((start_sample, end_sample, wav))
            active = kept

            np.clip(buf, -1.0, 1.0, out=buf)
            writer.write(buf.T)

    return out_wav


# Mỗi lệnh ffmpeg chỉ nối tối đa bấy nhiêu file. Windows giới hạn dòng lệnh
# ~32k ký tự; truyện 200k ký tự sinh ~800 clip TTS, nhét một lệnh là vỡ ngay.
_CONCAT_BATCH = 48


# Concat demuxer + copy resets packet timestamps on these containers, so
# ffprobe reports the last piece and lock_audio pads the rest with silence.
_COPY_UNSAFE_EXT = {
    ".flac", ".ogg", ".oga", ".opus", ".mp3", ".m4a", ".aac", ".wma", ".webm",
}
_COPY_SAFE_EXT = {".wav", ".aiff", ".aif"}


def _copy_concat_safe(chunk_files: Sequence[str]) -> bool:
    """Chỉ copy-concat khi đã kiểm chứng timestamp (WAV/AIFF cùng đuôi)."""
    exts = {os.path.splitext(p)[1].lower() for p in chunk_files if p}
    if len(exts) != 1:
        return False
    ext = next(iter(exts))
    return ext in _COPY_SAFE_EXT and ext not in _COPY_UNSAFE_EXT


def _concat_copy_chunks(chunk_files: Sequence[str], out_path: str) -> str:
    """Nối các mảnh cùng codec bằng copy — không resample, không rơi mẫu."""
    chunk_files = [p for p in chunk_files if p and os.path.exists(p)]
    if not chunk_files:
        raise ValueError("Không có mảnh audio để nối")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    if len(chunk_files) == 1:
        if os.path.abspath(chunk_files[0]) != os.path.abspath(out_path):
            shutil.copy2(chunk_files[0], out_path)
        return out_path
    list_path = out_path + ".concat.txt"
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for path in chunk_files:
                safe = os.path.abspath(path).replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{safe}'\n")
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-c", "copy", out_path])
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass
    return out_path


def _probe_total_duration(paths: Sequence[str]) -> float:
    total = 0.0
    for path in paths:
        try:
            total += float(ffprobe_duration(path) or 0.0)
        except Exception:
            pass
    return total


def _join_audio_chunks(chunk_files: Sequence[str], out_path: str, sr: int,
                       work_dir: str) -> str:
    """Nối mảnh mix: copy chỉ khi an toàn; FLAC luôn decode + filter concat."""
    chunk_files = [p for p in chunk_files if p and os.path.exists(p)]
    if not chunk_files:
        raise ValueError("Không có mảnh audio để nối")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    expected = _probe_total_duration(chunk_files)
    tmp = media_temp_path(out_path)

    def _duration_ok(path: str) -> float:
        got = 0.0
        try:
            got = float(ffprobe_duration(path) or 0.0)
        except Exception:
            got = 0.0
        if expected > 1.0 and got + 0.35 < expected * 0.90:
            raise RuntimeError(
                f"Nối audio thiếu timestamp: {got:.3f}s < {expected:.3f}s")
        return got

    try:
        if _copy_concat_safe(chunk_files):
            try:
                _concat_copy_chunks(chunk_files, tmp)
                _duration_ok(tmp)
            except InterruptedError:
                raise
            except Exception as exc:
                log(f"Nối copy mảnh audio lỗi ({exc}) — dùng filter concat.", "warn")
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                _concat_audio_chunks(chunk_files, tmp, sr, work_dir)
                _duration_ok(tmp)
        else:
            _concat_audio_chunks(chunk_files, tmp, sr, work_dir)
            _duration_ok(tmp)
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return out_path


def _concat_audio_chunks(chunk_files: Sequence[str], out_path: str, sr: int,
                         work_dir: str,
                         normalize_loudness: bool = False) -> str:
    chunk_files = [p for p in chunk_files if p and os.path.exists(p)]
    if not chunk_files:
        run(["ffmpeg", "-y", "-f", "lavfi", "-t", "0.2",
             "-i", f"anullsrc=r={sr}:cl=stereo",
             "-ac", "2", "-ar", str(sr), *_audio_encode_args(out_path), out_path])
        return out_path

    if len(chunk_files) > _CONCAT_BATCH:
        # Nối PHÂN TẦNG: gộp từng nhóm 48 file thành file trung gian FLAC
        # (không mất chất lượng) rồi nối tiếp các file trung gian.
        parent = work_dir if work_dir and os.path.isdir(work_dir) \
            else (os.path.dirname(os.path.abspath(out_path)) or ".")
        with tempfile.TemporaryDirectory(prefix="_noi_tang_", dir=parent) as td:
            mids: List[str] = []
            for gi in range(0, len(chunk_files), _CONCAT_BATCH):
                mid = os.path.join(td, f"tang_{gi // _CONCAT_BATCH:04d}.flac")
                _concat_audio_chunks(chunk_files[gi:gi + _CONCAT_BATCH],
                                     mid, sr, td,
                                     normalize_loudness=normalize_loudness)
                mids.append(mid)
            # Các clip gốc đã được cân ở tầng dưới. Không loudnorm lại từng
            # khối trung gian vì sẽ làm thay đổi mức giữa các nhóm 48 câu.
            return _concat_audio_chunks(
                mids, out_path, sr, td, normalize_loudness=False)

    inputs: List[str] = []
    chains: List[str] = []
    labels: List[str] = []
    for i, path in enumerate(chunk_files):
        inputs += ["-i", path]
        label = f"a{i}"
        loudness = "loudnorm=I=-18:TP=-2:LRA=7," \
            if normalize_loudness else ""
        chains.append(
            f"[{i}:a]{loudness}aresample={sr},"
            "aformat=sample_fmts=s16:channel_layouts=stereo,"
            "asetpts=PTS-STARTPTS"
            f"[{label}]"
        )
        labels.append(f"[{label}]")
    graph = ";".join(chains)
    if len(chunk_files) == 1:
        graph += f";{labels[0]}anull[joined]"
    else:
        graph += ";" + "".join(labels) + \
            f"concat=n={len(chunk_files)}:v=0:a=1[joined]"
    # loudnorm đôi khi trả cả clip ngắn trong một AVFrame lớn hơn 65.535 mẫu.
    # FLAC không mã hoá được block lớn như vậy (thực tế đã gặp 69.104 mẫu ở
    # nhóm 48 câu). Chia lại frame trước encoder; p=0 không chèn thêm im lặng.
    graph += ";[joined]asetnsamples=n=4096:p=0[out]"

    try:
        run(["ffmpeg", "-y", *inputs, "-filter_complex", graph,
             "-map", "[out]", "-ac", "2", "-ar", str(sr),
             *_audio_encode_args(out_path), out_path])
    except Exception:
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except OSError:
            pass
        raise
    return out_path


def concat_audio_clips(chunk_files: Sequence[str], out_path: str,
                       sr: int = 48000,
                       normalize_loudness: bool = False) -> str:
    """Nối tuần tự các clip thoại và chuẩn hoá chúng về một track âm thanh.

    Hàm public này phục vụ cả timeline lồng tiếng lẫn công cụ tạo audio từ văn
    bản. Dùng filter concat thay vì ghép byte nên MP3/WAV/M4A đầu vào có thể
    khác sample-rate hoặc số kênh. ``normalize_loudness=True`` cân từng clip
    về -18 LUFS, chặn đỉnh -2 dB trước khi nối; phù hợp khi nhiều giọng TTS có
    mức âm đầu ra khác nhau.
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    chunk_files = list(chunk_files)
    for path in chunk_files:
        if not path or not os.path.isfile(path):
            raise FileNotFoundError(f"Thiếu clip audio: {path}")
    tmp = media_temp_path(out_path)
    try:
        _concat_audio_chunks(
            chunk_files, tmp, max(8000, int(sr or 48000)),
            os.path.dirname(os.path.abspath(out_path)),
            normalize_loudness=normalize_loudness)
        if not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            raise RuntimeError("Nối audio không tạo được dữ liệu")
        os.replace(tmp, out_path)
        return out_path
    finally:
        _discard_partial(tmp)


def _assemble_ffmpeg_chunked(
    items_sec: List[tuple],
    total_duration: float,
    out_path: str,
    sr: int = 48000,
    batch: int = 60,
    chunk_seconds: float = 120.0,
) -> str:
    """FFmpeg fallback that mixes short time windows, not full-video batch WAVs."""
    chunk_ms = int(max(30.0, float(chunk_seconds or 120.0)) * 1000)
    items_ms = sorted([(p, float(s) * 1000.0) for p, s in items_sec], key=lambda x: x[1])
    durations_ms: Dict[str, float] = {}
    for path, _ in items_ms:
        if path not in durations_ms:
            try:
                durations_ms[path] = max(0.0, ffprobe_duration(path) * 1000.0)
            except Exception:
                durations_ms[path] = 0.0

    full_total_ms = int(math.ceil(total_duration * 1000)) + 200
    active_end_ms = 0.0
    for path, start_ms in items_ms:
        dur_ms = durations_ms.get(path, 0.0)
        if dur_ms <= 0:
            dur_ms = 1000.0
        active_end_ms = max(active_end_ms, start_ms + dur_ms)
    if active_end_ms > 0:
        total_ms = int(min(full_total_ms, math.ceil(active_end_ms) + 500))
        if total_ms + chunk_ms < full_total_ms:
            log(
                f"Track TTS có âm đến {total_ms / 1000 / 60:.1f} phút; "
                "đoạn im lặng cuối sẽ được đệm ở bước render để tiết kiệm ổ đĩa.",
                "info",
            )
    else:
        total_ms = min(full_total_ms, 1000)

    n_chunks = max(1, math.ceil(total_ms / chunk_ms))
    log(f"FFmpeg fallback streaming: {n_chunks} mảnh, mỗi mảnh tối đa {chunk_ms / 1000:.0f}s.", "info")
    tmp_parent = os.path.dirname(out_path) or "."
    chunk_ext = ".flac" if os.path.splitext(out_path)[1].lower() == ".flac" else ".wav"
    with tempfile.TemporaryDirectory(prefix="_mix_chunks_", dir=tmp_parent) as td:
        chunk_files: List[str] = []
        for ci in range(n_chunks):
            start_ms = ci * chunk_ms
            win_ms = min(chunk_ms, total_ms - start_ms)
            end_ms = start_ms + win_ms
            active = []
            for path, clip_start_ms in items_ms:
                dur_ms = durations_ms.get(path, 0.0)
                clip_end_ms = clip_start_ms + dur_ms if dur_ms > 0 else clip_start_ms + win_ms
                if clip_start_ms < end_ms and clip_end_ms > start_ms:
                    active.append((path, clip_start_ms))

            chunk_path = os.path.join(td, f"chunk_{ci:04d}{chunk_ext}")
            if len(active) <= max(1, batch):
                _mix_batch(active, win_ms, chunk_path, sr,
                           window_start_ms=start_ms,
                           clip_durations_ms=durations_ms)
            else:
                sub_files = []
                for si in range(0, len(active), max(1, batch)):
                    sub_path = os.path.join(td, f"chunk_{ci:04d}_sub_{si // max(1, batch):03d}{chunk_ext}")
                    _mix_batch(active[si:si + max(1, batch)], win_ms, sub_path, sr,
                               window_start_ms=start_ms,
                               clip_durations_ms=durations_ms)
                    sub_files.append(sub_path)
                _mix_batch([(p, 0.0) for p in sub_files], win_ms, chunk_path, sr)
                for sub_path in sub_files:
                    try:
                        os.remove(sub_path)
                    except OSError:
                        pass

            chunk_files.append(chunk_path)
            if n_chunks <= 20 or ci == 0 or ci + 1 == n_chunks or (ci + 1) % 10 == 0:
                log(f"  Mảnh {ci + 1}/{n_chunks}: {len(active)} clip.", "info")

        log(f"Gộp {len(chunk_files)} mảnh audio thành {os.path.basename(out_path)}...", "step")
        _join_audio_chunks(chunk_files, out_path, sr, td)
    return out_path


def assemble_timeline_audio(
    clip_paths: List[str],
    placed_starts: List[float],
    total_duration: float,
    out_wav: str,
    sr: int = 48000,
    batch: int = 60,
    mode: str = "auto",
    chunk_seconds: float = 120.0,
) -> str:
    """Validate the timeline and publish audio only after the mix succeeds."""
    if len(clip_paths) != len(placed_starts):
        raise ValueError("Số clip và số mốc đặt audio phải bằng nhau")
    total_duration = float(total_duration)
    if not math.isfinite(total_duration) or total_duration <= 0:
        raise ValueError("Thời lượng timeline phải là số hữu hạn > 0")
    if int(batch) < 1 or int(sr) < 1:
        raise ValueError("batch và sample rate phải >= 1")
    for path, start in zip(clip_paths, placed_starts):
        if not math.isfinite(float(start)):
            raise ValueError("Mốc audio phải là số hữu hạn")
        if path and not os.path.isfile(path):
            raise FileNotFoundError(f"Thiếu clip audio: {path}")
    raise_if_cancelled()
    destination = _timeline_output_path(out_wav, total_duration, sr)
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    tmp = media_temp_path(destination)
    try:
        _assemble_timeline_audio(clip_paths, placed_starts, total_duration, tmp,
                                 sr, batch, mode, chunk_seconds)
        raise_if_cancelled()
        if not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            raise RuntimeError("Ghép timeline không tạo được audio")
        os.replace(tmp, destination)
        return destination
    finally:
        _discard_partial(tmp)


def _assemble_timeline_audio(
    clip_paths: List[str], placed_starts: List[float], total_duration: float,
    out_wav: str, sr: int = 48000, batch: int = 60, mode: str = "auto",
    chunk_seconds: float = 120.0,
) -> str:
    """Đặt từng clip TTS lên đúng mốc thời gian — ưu tiên torchaudio (nhanh 10-20×),
    fallback sang FFmpeg filter_complex nếu torchaudio không có.
    """
    items_sec = [(p, s) for p, s in zip(clip_paths, placed_starts)
                 if p and os.path.exists(p)]
    total_ms = int(math.ceil(total_duration * 1000)) + 200
    timeline_out = _timeline_output_path(out_wav, total_duration, sr)
    if timeline_out != out_wav:
        log(
            f"Track giọng Việt dài (~{_timeline_pcm_gib(total_duration, sr):.1f} GiB nếu WAV) "
            f"- lưu {os.path.basename(timeline_out)} để tránh giới hạn WAV 4GB.",
            "info",
        )
        if os.path.exists(out_wav):
            try:
                os.remove(out_wav)
            except OSError:
                pass

    if not items_sec:
        run(["ffmpeg", "-y", "-f", "lavfi", "-t", f"{total_duration + 0.2:.3f}",
             "-i", f"anullsrc=r={sr}:cl=stereo",
             "-ac", "2", "-ar", str(sr), *_audio_encode_args(timeline_out), timeline_out])
        return timeline_out

    t_total = time.monotonic()
    log(f"Ghép {len(items_sec)} clip lên timeline...", "step")

    est_gib = (max(0.0, total_duration) * sr * 2 * 4) / (1024 ** 3)
    mix_mode = str(mode or "auto").strip().lower()
    skip_torch = mix_mode in ("ffmpeg", "filter", "filter_complex", "safe", "wdac")
    if mix_mode in ("ram", "memory", "fast", "torch"):
        long_mix = False
    elif mix_mode in ("stream", "streaming", "low-ram"):
        long_mix = True
    else:
        long_mix = est_gib >= 14.0 or len(items_sec) >= 20000
    if skip_torch:
        log("Ghép audio bằng FFmpeg để tránh DLL tăng tốc bị Application Control chặn.", "info")
    elif long_mix:
        try:
            log(f"Ghép audio kiểu streaming (ước tính buffer RAM {est_gib:.1f} GiB).", "info")
            _assemble_stream_torch(items_sec, total_duration, timeline_out, sr)
            log(f"Ghép timeline (streaming) xong trong {time.monotonic() - t_total:.1f}s.", "ok")
            return timeline_out
        except InterruptedError:
            raise
        except ImportError:
            log("Thiếu torchaudio/soundfile cho streaming — dùng cách dự phòng.", "warn")
        except Exception as e:
            log(f"Ghép streaming lỗi ({e}) — dùng cách dự phòng.", "warn")
        # Streaming was selected to avoid a full-film RAM allocation. A failed
        # streaming backend must fall straight through to FFmpeg, not RAM mix.
        skip_torch = True

    # ── Đường nhanh: torchaudio ─────────────────────────────────────────────
    if not skip_torch:
        try:
            _assemble_torch(items_sec, total_duration, timeline_out, sr)
            log(f"Ghép timeline (torchaudio) xong trong {time.monotonic() - t_total:.1f}s.", "ok")
            return timeline_out
        except InterruptedError:
            raise
        except ImportError as e:
            log(f"torchaudio không dùng được ({e}) — dùng FFmpeg fallback.", "warn")
        except Exception as e:
            log(f"torchaudio lỗi ({e}) — thử streaming trước khi fallback FFmpeg.", "warn")
            try:
                _assemble_stream_torch(items_sec, total_duration, timeline_out, sr)
                log(f"Ghép timeline (streaming) xong trong {time.monotonic() - t_total:.1f}s.", "ok")
                return timeline_out
            except InterruptedError:
                raise
            except Exception as e2:
                log(f"streaming cũng lỗi ({e2}) — dùng FFmpeg fallback.", "warn")

    # ── Fallback: FFmpeg filter_complex (stream theo mảnh thời gian) ─────────
    items_ms = [(p, s * 1000.0) for p, s in items_sec]
    n_batches = math.ceil(len(items_ms) / batch)
    log(f"FFmpeg fallback: {n_batches} lô logic, mỗi lô tối đa {batch} clip.", "info")

    if len(items_ms) <= batch and timeline_out == out_wav:
        _mix_batch(items_ms, total_ms, timeline_out, sr, label="Lô 1/1")
        log(f"Ghép timeline (FFmpeg) xong trong {time.monotonic() - t_total:.1f}s.", "ok")
        return timeline_out

    _assemble_ffmpeg_chunked(items_sec, total_duration, timeline_out, sr,
                             batch=batch, chunk_seconds=chunk_seconds)
    log(f"Ghép timeline (FFmpeg) xong trong {time.monotonic() - t_total:.1f}s.", "ok")
    try:
        got = ffprobe_duration(timeline_out)
        if got > 0 and got + 0.25 < float(total_duration or 0.0):
            log(f"Track giọng ngắn hơn video {float(total_duration) - got:.2f}s. "
                "Đoạn cuối sẽ được đệm im lặng khi render; nếu lệch giữa phim, "
                "hãy dựng lại giọng đọc.", "warn")
    except Exception:
        pass
    return timeline_out
