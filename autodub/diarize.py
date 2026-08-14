"""Tách người nói (speaker diarization) bằng pyannote - TÙY CHỌN.

Bật cái này để mỗi nhân vật được gán MỘT giọng riêng (phối hợp với voice_mode
'per-speaker'). Cần token Hugging Face (miễn phí) và đồng ý điều khoản model
'pyannote/speaker-diarization-3.1' trên huggingface.co.
Nếu không bật, chương trình vẫn chạy bình thường với 1 giọng kể chuyện.
"""
from __future__ import annotations

from typing import List, Optional

from .srt_utils import Segment
from .utils import log


def _best_overlap(seg: Segment, turns) -> Optional[str]:
    best, best_ov = None, 0.0
    for (ts, te, spk) in turns:
        ov = min(seg.end, te) - max(seg.start, ts)
        if ov > best_ov:
            best_ov, best = ov, spk
    return best


def diarize(
    audio_path: str,
    segments: List[Segment],
    hf_token: str,
    device: str = "cuda",
    num_speakers: Optional[int] = None,
) -> List[Segment]:
    """Gán seg.speaker cho từng dòng. Lỗi/thiếu token -> giữ nguyên, không crash."""
    if not hf_token:
        log("Bỏ qua diarization (chưa có HF token).", "warn")
        return segments
    try:
        import torch
        from pyannote.audio import Pipeline
        log("Nạp pyannote speaker-diarization-3.1 ...", "info")
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1", use_auth_token=hf_token)
        try:
            pipeline.to(torch.device(device))
        except Exception:
            pass
        kw = {"num_speakers": num_speakers} if num_speakers else {}
        diar = pipeline(audio_path, **kw)
        turns = [(t.start, t.end, spk) for t, _, spk in diar.itertracks(yield_label=True)]
        n_spk = len(set(s for *_, s in turns))
        for seg in segments:
            seg.speaker = _best_overlap(seg, turns)
        log(f"Diarization: phát hiện {n_spk} người nói.", "ok")
    except Exception as e:
        log(f"Diarization lỗi ({e}); tiếp tục với 1 giọng.", "warn")
    return segments
