"""Backend faster-whisper và WhisperX."""
from __future__ import annotations

from typing import List, Optional, Tuple
import time
from contextvars import ContextVar

from ..srt_utils import Segment
from ..utils import log
from .common import _MODEL_CACHE, _set_last_marks

LAST_TELEMETRY = ContextVar('whisper_telemetry', default=None)
_LOAD_COUNTS = {}


def _asr_faster_whisper(audio_path: str, language: Optional[str], model_size: str,
                        device: str, compute_type: str,
                        beam_size: int = 5,
                        initial_prompt: Optional[str] = None) -> Tuple[List[Segment], str]:
    LAST_TELEMETRY.set(None)
    from faster_whisper import WhisperModel

    key = ("fw", model_size, device, compute_type)
    started = time.monotonic()
    model = _MODEL_CACHE.get(key)
    if model is None:
        log(f"Nạp faster-whisper '{model_size}' ({device}/{compute_type}) ...", "info")
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        _MODEL_CACHE[key] = model
        _LOAD_COUNTS[key] = _LOAD_COUNTS.get(key,0)+1
    loaded = time.monotonic()

    seg_iter, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=beam_size,
        # Gợi ý từ vựng: tên nhân vật / cách gọi hay gặp. Whisper nghe tiếng
        # Việt rất dễ nhầm danh xưng Hán Việt ("tiểu soái ca" -> "tiểu xoái
        # ta"); mớm sẵn đúng chính tả thì nó bám theo.
        initial_prompt=initial_prompt or None,
        # LƯỚI AN TOÀN mà WhisperX không có: thử lại với temperature cao dần
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        compression_ratio_threshold=2.4,   # bắt vòng lặp lặp chữ
        log_prob_threshold=-1.0,           # bắt đoạn giải mã kém -> thử lại
        no_speech_threshold=0.6,
        condition_on_previous_text=False,  # chống trôi/bịa/nuốt đoạn
        vad_filter=True,
        vad_parameters={
            "threshold": 0.2,              # nhạy hơn mặc định -> ít bỏ sót
            "min_silence_duration_ms": 500,
            "speech_pad_ms": 400,          # ĐỆM THẬT quanh câu nói
        },
        word_timestamps=True,
    )

    segs: List[Segment] = []
    marks: List[Tuple[float, float]] = []
    for s in seg_iter:
        words = getattr(s, "words", None) or []
        start = words[0].start if words else s.start
        end = words[-1].end if words else s.end
        segs.append(Segment(0, float(start), float(end), s.text))
        for w in words:
            try:
                ws, we = float(w.start), float(w.end)
            except (TypeError, ValueError):
                continue
            if we > ws:
                marks.append((ws, we))
    _set_last_marks(marks)
    telemetry = dict(model=model_size, requested_device=device,
                     effective_device=str(getattr(getattr(model,'model',None),'device',device)),
                     compute_type=compute_type, language=language,
                     load_s=loaded-started, inference_s=time.monotonic()-loaded,
                     load_count=_LOAD_COUNTS.get(key,0), segments=len(segs))
    LAST_TELEMETRY.set(telemetry)
    log(f"Whisper telemetry: {telemetry}", 'info')
    return segs, getattr(info, "language", language or "auto")


def _asr_whisperx(audio_path: str, language: Optional[str], model_size: str,
                  device: str, compute_type: str,
                  batch_size: int = 8) -> Tuple[List[Segment], str]:
    import whisperx

    log("WhisperX dễ MẤT ĐOẠN với video dài. Đang ép tham số an toàn "
        "(chunk_size=10, VAD silero). Khuyên dùng backend 'paraformer' "
        "(tiếng Trung) hoặc 'faster-whisper'.", "warn")

    key = ("whisperx", model_size, device, compute_type)
    model = _MODEL_CACHE.get(key)
    if model is None:
        model = whisperx.load_model(
            model_size, device, compute_type=compute_type, language=language,
            vad_method="silero",
            vad_options={"chunk_size": 10, "vad_onset": 0.15, "vad_offset": 0.1},
        )
        _MODEL_CACHE[key] = model

    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=batch_size, chunk_size=10)
    lang = result.get("language", language or "auto")
    segs = [Segment(0, float(s["start"]), float(s["end"]), s.get("text", ""))
            for s in result.get("segments", [])]
    return segs, lang
