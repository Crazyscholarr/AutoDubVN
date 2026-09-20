"""Tổng hợp văn bản thành một file audio và nối clip TTS."""
from __future__ import annotations

import asyncio
import os
import re
from typing import List, Optional

from ..asr import is_speakable
from ..srt_utils import Segment, split_vi_text_naturally
from ..utils import ffprobe_duration, log
from ..video import change_speed, compact_long_silences, concat_audio_clips
from . import vieneu as _vieneu
from .capcut import _synth_all_capcut
from .common import (
    CAPCUT_DEFAULT_VOICE,
    DEFAULT_NARRATOR,
    _channel_cta_speed,
    _edge_voice_and_pitch,
    _is_channel_cta_text,
    _raise_if_cancelled,
)
from .edge import _synth_all
from .timeline import assign_voices, build_narration_timeline
from .vieneu import _synth_all_vieneu

def synthesize_text_audio(
    text: str,
    workdir: str,
    out_path: str,
    engine: str = "edge",
    narrator: Optional[dict] = None,
    base_rate: str = "+0%",
    concurrency: int = 8,
    max_retries: int = 3,
    retry_base_delay: float = 1.2,
    vieneu_options: Optional[dict] = None,
    capcut_options: Optional[dict] = None,
    max_chunk_chars: int = 700,
    utterances: Optional[List[dict]] = None,
    channel_cta_speed: float = 1.0,
    channel_cta_text: str = "",
    cancel_event=None,
) -> dict:
    """Tạo một file audio độc lập từ văn bản dài.

    Văn bản được tách tự nhiên thành các đoạn vừa với dịch vụ TTS rồi nối lại
    theo thứ tự. Nếu ``utterances`` được truyền vào, mỗi lượt đã có ``voice`` và
    ``speaker`` riêng (lời kể/nhân vật); khi tách nhỏ vẫn giữ nguyên giọng đó.
    Luồng này không dùng thuật toán ép timing của phụ đề, vì vậy tốc độ đọc được
    giữ đúng theo ``base_rate`` và không bị tăng tốc để lấp slot.
    """
    _raise_if_cancelled(cancel_event)
    clean = re.sub(r"[ \t]+", " ", str(text or ""))
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    if not clean:
        raise ValueError("Văn bản đang trống.")
    if len(clean) > 200_000:
        raise ValueError("Văn bản quá dài (tối đa 200.000 ký tự mỗi lần).")

    max_chunk_chars = max(120, min(1500, int(max_chunk_chars or 700)))
    os.makedirs(workdir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    engine = (engine or "edge").strip().lower()
    if engine not in {"edge", "vieneu", "capcut"}:
        raise ValueError(f"Engine TTS không hỗ trợ: {engine}")

    narrator = narrator or DEFAULT_NARRATOR
    segment_meta: List[dict] = []
    chunks: List[str] = []
    if utterances:
        for utterance in utterances:
            value = re.sub(r"\s+", " ", str(utterance.get("text") or "")).strip()
            if not value:
                continue
            pieces = split_vi_text_naturally(
                value, max_chars=max_chunk_chars,
                min_chars=min(80, max(24, max_chunk_chars // 8))) or [value]
            for piece in pieces:
                chunks.append(piece)
                segment_meta.append({
                    "speaker": str(utterance.get("speaker") or "narrator"),
                    "speaker_name": str(utterance.get("speaker_name") or "Người kể"),
                    "kind": str(utterance.get("kind") or "narration"),
                    "confidence": utterance.get("confidence"),
                    "voice": str(utterance.get("voice") or narrator.get("voice") or ""),
                    "channel_cta": _is_channel_cta_text(piece, channel_cta_text),
                })
    else:
        chunks = split_vi_text_naturally(
            clean, max_chars=max_chunk_chars,
            min_chars=min(80, max(24, max_chunk_chars // 8)))
        segment_meta = [{"speaker": "narrator", "speaker_name": "Người kể",
                         "kind": "narration", "confidence": 1.0,
                         "voice": str(narrator.get("voice") or ""),
                         "channel_cta": _is_channel_cta_text(
                             chunk, channel_cta_text)}
                        for chunk in chunks]
    if not chunks:
        raise ValueError("Không tìm thấy nội dung có thể đọc.")

    segments = [Segment(i, float(i - 1), float(i), chunk)
                for i, chunk in enumerate(chunks, 1)]
    if utterances:
        for seg, meta in zip(segments, segment_meta):
            voice = meta["voice"]
            if engine == "edge":
                edge_voice, edge_pitch = _edge_voice_and_pitch(voice)
                seg.voice = f"{edge_voice}|{edge_pitch}"
            elif engine == "capcut":
                seg.voice = voice or CAPCUT_DEFAULT_VOICE
            else:
                seg.voice = voice
            meta["voice"] = seg.voice
    else:
        assign_voices(
            segments, "narrator", narrator, engine=engine,
            vieneu_voice=narrator.get("voice") if engine == "vieneu" else None)
        for seg, meta in zip(segments, segment_meta):
            meta["voice"] = seg.voice

    if engine == "capcut":
        capcut_opts = dict(capcut_options or {})
        capcut_opts.setdefault(
            "fallback_voice", narrator.get("voice") or CAPCUT_DEFAULT_VOICE)
        raw_clips = _synth_all_capcut(
            segments, workdir, base_rate, concurrency,
            max_retries=max_retries, capcut_options=capcut_opts,
            cancel_event=cancel_event)
    elif engine == "vieneu":
        _vieneu._VIENEU_KWARGS = dict(vieneu_options or {})
        raw_clips = _synth_all_vieneu(
            segments, workdir, max_retries=max_retries,
            cancel_event=cancel_event)
    else:
        raw_clips = asyncio.run(_synth_all(
            segments, workdir, base_rate, concurrency,
            max_retries=max_retries, retry_base_delay=retry_base_delay,
            cancel_event=cancel_event))

    _raise_if_cancelled(cancel_event)
    failed = [s.index for s, path in zip(segments, raw_clips)
              if is_speakable(s.text) and not path]
    prepared_clips: List[str] = []
    clip_durations: List[float] = []
    clip_texts: List[str] = []
    clip_meta: List[dict] = []
    compacted_count = 0
    cta_fast_count = 0
    cta_speed = _channel_cta_speed(channel_cta_speed)
    for i, (seg, path) in enumerate(zip(segments, raw_clips)):
        _raise_if_cancelled(cancel_event)
        if not path or not os.path.exists(path):
            continue
        if cta_speed > 1.001 and segment_meta[i].get("channel_cta"):
            ext = os.path.splitext(path)[1] or ".mp3"
            faster = os.path.join(workdir, f"manual_cta_fast_{i:05d}{ext}")
            path = change_speed(path, faster, cta_speed)
            cta_fast_count += 1
        duration = ffprobe_duration(path)
        expected = max(1.0, len(seg.text or "") / 9.0)
        # Chỉ can thiệp khi clip rõ ràng chậm bất thường, tránh làm mất nhịp kể
        # tự nhiên của giọng bình thường.
        if duration > max(expected * 1.8, expected + 5.0):
            ext = os.path.splitext(path)[1] or ".mp3"
            compacted = os.path.join(workdir, f"manual_compact_{i:05d}{ext}")
            candidate = compact_long_silences(path, compacted)
            if candidate != path:
                cand_dur = ffprobe_duration(candidate)
                if cand_dur < duration * 0.92:
                    path = candidate
                    duration = cand_dur
                    compacted_count += 1
        prepared_clips.append(path)
        clip_durations.append(max(0.05, duration))
        clip_texts.append(seg.text or "")
        clip_meta.append(segment_meta[i])
    clips = prepared_clips
    if failed or not clips:
        sample = ", ".join(str(i) for i in failed[:10]) or "tất cả"
        raise RuntimeError(f"TTS không tạo được đoạn: {sample}.")

    if compacted_count:
        log(f"Đã rút khoảng lặng bất thường trong {compacted_count} đoạn TTS.", "info")
    if cta_fast_count:
        log(f"Đã tăng tốc {cta_fast_count} đoạn nhắc kênh lên {cta_speed:.2g}x.", "info")

    # Các voice/engine TTS trả mức âm lượng rất khác nhau. Cân từng câu trước
    # khi nối để lúc đổi nhân vật không bị câu quá to, câu quá nhỏ.
    _raise_if_cancelled(cancel_event)
    concat_audio_clips(
        clips, out_path, sr=48000, normalize_loudness=True)
    log(f"Đã cân âm lượng {len(clips)} đoạn giọng về -18 LUFS.", "info")
    duration = ffprobe_duration(out_path)
    if duration <= 0 or not os.path.exists(out_path):
        raise RuntimeError("File âm thanh tạo ra bị rỗng hoặc không đọc được.")

    # Timeline từng đoạn theo thứ tự nối - để lớp trên sinh PHỤ ĐỀ khớp giọng
    # đọc. Concat nối sát các clip nên mốc = cộng dồn độ dài từng clip.
    timeline = build_narration_timeline(clip_texts, clip_durations, clip_meta)
    log(f"Đã tạo audio từ văn bản: {len(chunks)} đoạn, {duration:.1f} giây.", "ok")
    return {"path": out_path, "duration": duration, "chunks": len(chunks),
            "segments": timeline}
