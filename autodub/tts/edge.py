"""Engine edge-tts (Microsoft)."""
from __future__ import annotations

import asyncio
import os
import random
import time
from typing import List, Optional

from ..asr import is_speakable
from ..srt_utils import Segment
from ..utils import active_cancel_event, log
from ..video.common import media_temp_path
from .common import (
    _RETRYABLE_ERRORS,
    _clean_partial,
    _edge_voice_and_pitch,
    _raise_if_cancelled,
)

async def _synth_one(text: str, voice: str, pitch: str, rate: str, out_path: str,
                     max_retries: int = 4, base_delay: float = 1.2,
                     label: str = "1 dòng", cancel_event=None) -> bool:
    """Tổng hợp 1 dòng, tự thử lại khi gặp lỗi mạng/rate-limit tạm thời từ edge-tts.

    Lỗi "No audio was received" hầu hết là do server tạm thời không trả dữ liệu
    (thường xảy ra khi có nhiều kết nối song song) chứ không phải lỗi nội dung câu,
    nên thử lại với độ trễ tăng dần gần như luôn khắc phục được.
    """
    import edge_tts

    cancel_event = cancel_event if cancel_event is not None else active_cancel_event()
    max_retries = max(1, int(max_retries))
    temp_path = media_temp_path(out_path)
    last_err: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            _raise_if_cancelled(cancel_event)
            comm = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
            save_task = asyncio.create_task(comm.save(temp_path))
            try:
                while not save_task.done():
                    _raise_if_cancelled(cancel_event)
                    await asyncio.wait({save_task}, timeout=0.1)
                await save_task
            finally:
                if not save_task.done():
                    save_task.cancel()
                await asyncio.gather(save_task, return_exceptions=True)
            _raise_if_cancelled(cancel_event)
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                os.replace(temp_path, out_path)
                return True
            raise RuntimeError("File âm thanh rỗng.")
        except (InterruptedError, asyncio.CancelledError):
            _clean_partial(temp_path)
            raise
        except _RETRYABLE_ERRORS as e:
            last_err = e
            _clean_partial(temp_path)
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.6)
                end = time.monotonic() + delay
                while time.monotonic() < end:
                    _raise_if_cancelled(cancel_event)
                    await asyncio.sleep(min(0.1, max(0.0, end - time.monotonic())))
        except Exception as e:  # lỗi không thuộc nhóm biết trước -> vẫn ghi nhận, không retry vô ích
            last_err = e
            _clean_partial(temp_path)
            break

    preview = text if len(text) <= 50 else text[:50] + "…"
    log(f"TTS lỗi {label} sau {max_retries} lần thử ({voice}): {last_err} — \"{preview}\"", "warn")
    return False


async def _synth_all(segments: List[Segment], workdir: str, base_rate: str,
                     concurrency: int, max_retries: int = 4,
                     retry_base_delay: float = 1.2,
                     cancel_event=None) -> List[Optional[str]]:
    concurrency = int(concurrency)
    if concurrency < 1:
        raise ValueError("TTS concurrency phải >= 1")
    cancel_event = cancel_event if cancel_event is not None else active_cancel_event()
    _raise_if_cancelled(cancel_event)
    sem = asyncio.Semaphore(concurrency)
    paths: List[Optional[str]] = [None] * len(segments)

    async def attempt(i: int, seg: Segment, conc_sem: asyncio.Semaphore, retries: int) -> bool:
        _raise_if_cancelled(cancel_event)
        voice, pitch = _edge_voice_and_pitch(seg.voice)
        out = os.path.join(workdir, f"line_{i:05d}.mp3")
        async with conc_sem:
            _raise_if_cancelled(cancel_event)
            ok = await _synth_one(seg.text, voice, pitch, base_rate, out,
                                  max_retries=retries, base_delay=retry_base_delay,
                                  label=f"dòng {seg.index}",
                                  cancel_event=cancel_event)
        if ok:
            paths[i] = out
        return ok

    todo = [(i, s) for i, s in enumerate(segments) if is_speakable(s.text)]
    # Keep only concurrency workers alive, even for tens of thousands of cues.
    pending = iter(todo)

    async def worker():
        for i, seg in pending:
            _raise_if_cancelled(cancel_event)
            await attempt(i, seg, sem, max_retries)

    workers = [asyncio.create_task(worker()) for _ in range(min(concurrency, len(todo)))]
    try:
        await asyncio.gather(*workers)
    finally:
        for task in workers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
    _raise_if_cancelled(cancel_event)

    # Vòng 2: các dòng vẫn lỗi sau vòng 1 -> thử lại RIÊNG LẺ (concurrency thấp) để
    # né rate-limit do quá nhiều kết nối song song, thường vớt lại được gần hết.
    still_failed = [(i, s) for i, s in todo if paths[i] is None]
    if still_failed:
        log(f"Thử lại {len(still_failed)} dòng TTS lỗi (giảm luồng để né giới hạn tốc độ)...", "step")
        low_sem = asyncio.Semaphore(min(2, concurrency))
        for i, s in still_failed:  # chạy tuần tự từng dòng, tránh dồn dập lại gây lỗi tiếp
            if await attempt(i, s, low_sem, retries=max_retries + 1):
                continue
            # Vẫn hỏng -> thử GIỌNG KHÁC. Đôi khi một giọng cụ thể bị server từ
            # chối với một câu cụ thể; đổi giọng thường đọc được ngay, thà lệch
            # giọng một dòng còn hơn câm tiếng.
            cur = (s.voice or "").split("|")[0]
            alt = ("vi-VN-HoaiMyNeural" if "NamMinh" in cur
                   else "vi-VN-NamMinhNeural")
            out = os.path.join(workdir, f"line_{i:05d}.mp3")
            async with low_sem:
                if await _synth_one(s.text, alt, "+0Hz", base_rate, out,
                                    max_retries=2, base_delay=retry_base_delay,
                                    label=f"dòng {s.index} (đổi giọng {alt})",
                                    cancel_event=cancel_event):
                    paths[i] = out
                    log(f"Dòng {s.index}: đọc được bằng giọng dự phòng {alt}.", "ok")

    return paths
