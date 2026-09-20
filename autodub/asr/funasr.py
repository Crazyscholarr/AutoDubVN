"""Backend FunASR Paraformer / SenseVoice."""
from __future__ import annotations

import math
import os
import tempfile
import logging
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from ..srt_utils import Segment
from ..utils import log, run, ffprobe_duration
from .common import (
    _MODEL_CACHE,
    _clean,
    _is_cjk,
    _set_last_marks,
    caption_options,
    caption_style_is_screen,
)

# Tên thật trên ModelScope của các alias FunASR hay dùng - để tự tìm bản đã tải
# trong cache mà nạp thẳng, khỏi phải hỏi server mỗi lần chạy.
_MS_REPO = {
    "paraformer-zh": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "fsmn-vad": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    "ct-punc": "iic/punc_ct-transformer_cn-en-common-vocab471067-large",
}

# 10-minute CPU fixtures passed the 7.5/10/15/20/30-minute comparison.
# Prefer the shorter checkpoint/recovery interval over the fastest single RTF;
# measurements and limitations are recorded in docs/ASR_LONG_AUDIO_REPAIR.md.
_FUNASR_DIRECT_LIMIT_SECONDS = 10 * 60
_FUNASR_CHUNK_SECONDS = 10 * 60
_FUNASR_CHUNK_OVERLAP_SECONDS = 1.5


def _local_model_dir(name: str) -> Optional[str]:
    """Trả về thư mục model đã tải trong cache ModelScope, hoặc None."""
    repo = _MS_REPO.get(name, name)
    if os.path.isdir(name):
        return name
    if "/" not in repo:
        return None
    root = os.environ.get("MODELSCOPE_CACHE") or os.path.join(
        os.path.expanduser("~"), ".cache", "modelscope")
    for base in (os.path.join(root, "models"), root):
        d = os.path.join(base, repo.replace("/", "--"), "snapshots", "master")
        if os.path.isfile(os.path.join(d, "configuration.json")) or \
           os.path.isfile(os.path.join(d, "config.yaml")) or \
           os.path.isfile(os.path.join(d, "model.pt")):
            return d
    return None


def _resolve(name: str) -> str:
    local = _local_model_dir(name)
    return local or name


def _fmt_hms(seconds: float) -> str:
    s = int(max(0.0, float(seconds or 0.0)) + 0.5)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _timestamp_pairs(value: Any) -> List[Tuple[float, float]]:
    if not isinstance(value, (list, tuple)):
        return []
    if len(value) >= 2 and not isinstance(value[0], (list, tuple, dict)):
        a, b = _to_float(value[0]), _to_float(value[1])
        return [(a, b)] if a is not None and b is not None else []

    pairs: List[Tuple[float, float]] = []
    for item in value:
        if isinstance(item, dict):
            a = _to_float(item.get("start", item.get("begin")))
            b = _to_float(item.get("end", item.get("stop")))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            a, b = _to_float(item[0]), _to_float(item[1])
        else:
            continue
        if a is not None and b is not None and b > a:
            pairs.append((a, b))
    return pairs


def _guess_time_scale(pairs: List[Tuple[float, float]],
                      duration_hint: float = 0.0,
                      default_ms: bool = True) -> float:
    # FunASR list timestamps are milliseconds, even for very short clips.
    # Magnitude cannot distinguish 20 ms from 20 s.
    return 0.001 if default_ms else 1.0


def _iter_funasr_dicts(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_funasr_dicts(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _iter_funasr_dicts(item)


def _collect_sentence_info(obj: Any) -> List[dict]:
    out: List[dict] = []
    for item in _iter_funasr_dicts(obj):
        sents = item.get("sentence_info") or item.get("sentences")
        if not isinstance(sents, list):
            continue
        for sent in sents:
            if isinstance(sent, dict):
                out.append(sent)
    return out


def _sentence_info_to_segments(sents: List[dict], offset: float = 0.0) -> List[Segment]:
    return normalize_funasr_result(sents, offset).segments


def _aligned_text_tokens(text, count):
    """Native Paraformer token units: Han characters and whole Latin words.

    Accept only an exact token count; punctuation retains its adjacent token.
    Never stretch character counts to fit mismatched timestamps.
    """
    matches = list(re.finditer(r"[A-Za-z]+(?:['-][A-Za-z]+)*|[0-9]+(?:[.,][0-9]+)*|[^\W_]", text))
    if len(matches) != count or not matches:
        return None
    return [text[(m.start() if i else 0):(matches[i+1].start() if i+1<len(matches) else len(text))]
            for i,m in enumerate(matches)]


def _timestamp_payload_to_segments(payload: dict, offset: float = 0.0,
                                   duration_hint: float = 0.0) -> List[Segment]:
    text = _clean(str(payload.get("text") or payload.get("sentence") or ""))
    pairs = _timestamp_pairs(payload.get("timestamp") or payload.get("timestamps"))
    if not text or not pairs:
        return []
    scale = _guess_time_scale(pairs, duration_hint=duration_hint, default_ms=True)
    if len(pairs) == 1:
        return [Segment(0, offset + pairs[0][0] * scale,
                        offset + pairs[0][1] * scale, text)]

    words = text.split()
    compact_chars = [c for c in text if not c.isspace()]
    aligned = _aligned_text_tokens(text, len(pairs))
    if aligned is not None:
        tokens, sep = aligned, ""
    elif len(words) == len(pairs):
        tokens = words
        sep = " "
    elif len(compact_chars) == len(pairs):
        tokens = compact_chars
        sep = ""
    elif _is_cjk(text) and sum(c.isalnum() for c in text) == len(pairs):
        positions = [i for i,c in enumerate(text) if c.isalnum()]
        tokens = [text[i:(positions[j+1] if j+1<len(positions) else len(text))]
                  for j,i in enumerate(positions)]
        sep = ""
    else:
        return [Segment(0, offset + pairs[0][0] * scale,
                        offset + pairs[-1][1] * scale, text)]

    terminal = set(".!?;,\u3002\uff01\uff1f\uff0c\u3001\uff1b\u2026")
    opts = caption_options()
    if caption_style_is_screen() and _is_cjk(text):
        max_chars = int(opts.get("max_chars") or 14)
        max_dur = float(opts.get("max_duration") or 2.7)
        gap_lim = float(opts.get("gap") or 0.32)
    else:
        max_chars = 34 if _is_cjk(text) else 84
        max_dur = 9.0
        gap_lim = 9.0
    segs: List[Segment] = []
    buf: List[str] = []
    start_s: Optional[float] = None
    end_s: Optional[float] = None

    def flush() -> None:
        nonlocal buf, start_s, end_s
        if buf and start_s is not None and end_s is not None and end_s > start_s:
            segs.append(Segment(0, offset + start_s, offset + end_s, sep.join(buf).strip()))
        buf, start_s, end_s = [], None, None

    for token, pair in zip(tokens, pairs):
        st, en = pair[0] * scale, pair[1] * scale
        if start_s is None:
            start_s = st
        elif start_s is not None and end_s is not None and (st - end_s) > gap_lim:
            flush()
            start_s = st
        buf.append(token)
        end_s = en
        joined = sep.join(buf)
        if (token[-1:] in terminal or len(joined) >= max_chars or
                (end_s - start_s) >= max_dur):
            flush()
    flush()
    return segs


def _funasr_segments_from_result(res: Any, offset: float = 0.0,
                                 duration_hint: float = 0.0) -> List[Segment]:
    return normalize_funasr_result(res, offset, duration_hint).segments


def _marks_from_funasr(res: Any, offset: float = 0.0,
                       duration_hint: float = 0.0) -> List[Tuple[float, float]]:
    """Rút mốc từng ký tự từ output FunASR (ms -> giây)."""
    return normalize_funasr_result(res, offset, duration_hint).marks


@dataclass
class NormalizedFunASR:
    segments: List[Segment] = field(default_factory=list)
    marks: List[Tuple[float, float]] = field(default_factory=list)
    status: str = "ASR_EMPTY"
    diagnostics: list = field(default_factory=list)


class FunASRResultError(RuntimeError):
    def __init__(self, result):
        self.result = result
        self.reason = result.status
        super().__init__(f"{result.status}: FunASR normalization: {result.diagnostics[:4]}")


def normalize_funasr_result(res, offset=0.0, duration_hint=0.0):
    """One boundary: native ms pairs / Nano seconds dicts -> global seconds.

    Explicit timestamp_unit ('ms'/'s') and timestamp_origin ('clip'/'media')
    are accepted for adapters. Native FunASR is always clip-relative.
    No timing is invented when recognition returns only text.
    """
    result = NormalizedFunASR()

    def visit(obj):
        if isinstance(obj, (list, tuple)):
            for child in obj:
                visit(child)
            return
        if not isinstance(obj, dict):
            return
        text = _clean(str(obj.get("text") or obj.get("sentence") or obj.get("raw_text") or ""))
        children = obj.get("sentence_info") or obj.get("sentences")
        parent_value = obj.get('timestamp')
        if hasattr(parent_value,'tolist'):
            parent_value = parent_value.tolist()
        parent_pairs = _timestamp_pairs(parent_value)
        if parent_pairs and _aligned_text_tokens(text,len(parent_pairs)) is not None:
            # sentence_info may preserve all words but associate them with the
            # wrong clocks (including punctuation-only trailing sentences).
            # Prefer the exact native token/text alignment when available.
            children = None
        # Prefer sentence records only when they preserve all recognized text.
        if isinstance(children, (list, tuple)) and children:
            saved = len(result.segments), len(result.marks), len(result.diagnostics)
            for child in children:
                inherited = {k: obj[k] for k in ('timestamp_unit','timestamp_origin') if k in obj}
                visit({**inherited, **child} if isinstance(child, dict) else child)
            recognized = ''.join(s.text for s in result.segments[saved[0]:])
            # Punctuation normalization can lowercase Latin words (Go -> go).
            # This does not mean sentence_info lost recognized speech.
            lexical = lambda s: ''.join(c for c in s if c.isalnum()).casefold()
            if len(result.segments) > saved[0] and (not text or lexical(recognized) == lexical(text)):
                return
            del result.segments[saved[0]:]
            del result.marks[saved[1]:]
            del result.diagnostics[saved[2]:]
        keys = ("timestamp", "timestamps", "sentence_timestamp", "token_timestamp", "word_timestamp", "char_timestamp")
        key = next((k for k in keys if obj.get(k) is not None and
                    (not hasattr(obj[k], "__len__") or len(obj[k]) > 0)), "timestamp")
        value = obj.get(key)
        if hasattr(value, "tolist"):
            value = value.tolist()
        pairs = _timestamp_pairs(value)
        entries = value if isinstance(value, (list, tuple)) else []
        if entries and not isinstance(entries[0], (list, tuple, dict)):
            entries = [entries]
        unit = obj.get("timestamp_unit", "s" if key == "timestamps" and entries and isinstance(entries[0], dict) else "ms")
        origin = obj.get("timestamp_origin", "clip")
        scale = {"ms": .001, "s": 1.0}.get(unit)
        reason = None
        if scale is None or origin not in ("clip", "media"):
            reason = "unsupported_unit_or_origin"
        if not pairs and (value is None or value == []) and ("start" in obj or "begin" in obj):
            entries = [[obj.get('start',obj.get('begin')), obj.get('end',obj.get('stop'))]]
            pairs = _timestamp_pairs(entries)
        if not pairs:
            reason = reason or ("missing_or_empty_timestamp" if value is None or value == [] else "malformed_timestamp")
        elif len(pairs) != len(entries):
            reason = "malformed_entry"
        local = [(a * (scale or 1) - (offset if origin == "media" else 0),
                  b * (scale or 1) - (offset if origin == "media" else 0)) for a, b in pairs]
        if any(a < 0 or b <= a or (duration_hint > 0 and b > duration_hint + .05)
               for a, b in local):
            reason = "out_of_bounds_or_reversed_timestamp"
        if any(a < prev[1] - .001 for prev, (a, b) in zip(local, local[1:])):
            reason = "non_monotonic_timestamp"
        status = "ASR_EMPTY" if not text else ("ASR_VALID" if reason is None else
                 "ASR_TEXT_NO_TIMESTAMP" if reason == "missing_or_empty_timestamp" else "ASR_INVALID_TIMESTAMP")
        diag = dict(status=status, reason=reason, timestamp_type=type(value).__name__,
                    timestamp_len=len(entries), first_pairs=pairs[:3],
                    minimum=min((a for a, b in pairs), default=None),
                    maximum=max((b for a, b in pairs), default=None), text_length=len(text),
                    region_start=offset, region_end=offset + duration_hint, unit=unit, origin=origin)
        if text or any(k in obj for k in keys):
            result.diagnostics.append(diag)
            logging.getLogger(__name__).debug("FunASR timestamp: %s", diag)
        if status == "ASR_VALID":
            clean_payload = dict(text=text, timestamp=[[a * 1000, b * 1000] for a, b in local])
            result.segments.extend(_timestamp_payload_to_segments(clean_payload, offset, duration_hint))
            # A sentence boundary is not a token/word alignment.
            if entries and value is not None and value != []:
                result.marks.extend((offset + a, offset + b) for a, b in local)
        elif not text:
            if reason is None and len(local) >= 2:
                result.marks.extend((offset + a, offset + b) for a, b in local)
            for k, child in obj.items():
                if k not in keys and k not in ("sentence_info", "sentences"):
                    if isinstance(child, (dict, list, tuple)):
                        visit(child)

    visit(res)
    result.marks = sorted(set(result.marks))
    result.segments.sort(key=lambda s: (s.start, s.end))
    statuses = {d['status'] for d in result.diagnostics}
    result.status = next((s for s in ("ASR_INVALID_TIMESTAMP", "ASR_TEXT_NO_TIMESTAMP", "ASR_VALID") if s in statuses), "ASR_EMPTY")
    return result


def cached_funasr_speech_ranges(audio_path, duration):
    """Reuse the loaded FSMN VAD; None means unavailable, [] means no speech."""
    import copy
    model = next((m for k, m in _MODEL_CACHE.items()
                  if k[0] == "funasr" and getattr(m, "vad_model", None) is not None), None)
    if model is None:
        return None
    raw = model.inference(input=audio_path, model=model.vad_model,
                          kwargs=copy.deepcopy(model.vad_kwargs), disable_pbar=True)
    if not raw:
        return None
    ranges = []
    for row in raw:
        value = row.get('value')
        if not isinstance(value, (list, tuple)):
            raise ValueError('FSMN VAD returned malformed speech ranges')
        pairs = _timestamp_pairs(value)
        if len(pairs) != len(value):
            raise ValueError('FSMN VAD returned invalid speech timestamps')
        for a, b in pairs:
            a, b = max(0, a / 1000), min(duration, b / 1000)
            if b > a:
                ranges.append((a, b))
    return sorted(ranges)


def _funasr_lang_from_result(res: Any, fallback: Optional[str]) -> str:
    for item in _iter_funasr_dicts(res):
        lang = item.get("lang") or item.get("language")
        if lang:
            return str(lang)
    return fallback or "zh"


def _funasr_shape(obj: Any, depth: int = 0) -> str:
    if depth >= 3:
        return type(obj).__name__
    if isinstance(obj, dict):
        keys = list(obj.keys())
        head = ", ".join(map(str, keys[:10]))
        return f"dict(keys=[{head}]" + (", ..." if len(keys) > 10 else "") + ")"
    if isinstance(obj, (list, tuple)):
        if not obj:
            return f"{type(obj).__name__}(len=0)"
        return f"{type(obj).__name__}(len={len(obj)}, first={_funasr_shape(obj[0], depth + 1)})"
    if isinstance(obj, str):
        return f"str(len={len(obj)})"
    return type(obj).__name__


def _funasr_generate(model, audio_path: str) -> Any:
    return model.generate(
        input=audio_path,
        batch_size_s=150,             # giảm so với 300 để đỡ ngốn RAM với file dài
        batch_size_threshold_s=60,
        sentence_timestamp=True,      # <- thứ cho ra timestamp theo CÂU
        merge_vad=True,
        merge_length_s=float(caption_options().get("funasr_merge_length_s") or 3.0),
    )


def _extract_funasr_chunk(audio_path: str, out_path: str,
                          start: float, duration: float) -> None:
    run([
        "ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
        "-i", audio_path, "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", out_path,
    ], timeout=max(600, min(3600, int(duration * 0.5 + 300))))


def _asr_funasr_chunked(model, audio_path: str, language: Optional[str],
                        duration: float) -> Tuple[List[Segment], str]:
    from .long_audio import recognize
    try:
        speech = cached_funasr_speech_ranges(audio_path, duration)
    except InterruptedError:
        raise
    except Exception as exc:
        log(f"FSMN VAD boundary evidence unavailable: {exc}", "warn")
        speech = None
    identity = next((list(key) for key, value in _MODEL_CACHE.items() if value is model),
                    [type(model).__name__])
    return recognize(model, audio_path, language, duration,
                     _FUNASR_CHUNK_SECONDS, _FUNASR_CHUNK_OVERLAP_SECONDS,
                     _funasr_generate, _extract_funasr_chunk, normalize_funasr_result,
                     speech=speech, model_identity=identity)


def _asr_funasr(audio_path: str, language: Optional[str], device: str,
                model_name: str = "paraformer-zh", direct: bool = False) -> Tuple[List[Segment], str]:
    # PHẢI vá TRƯỚC khi import funasr: funasr gọi modelscope ngay lúc nạp model,
    # mà modelscope_hub.HubConfig vỡ trên Python 3.10.0 -> tải model thất bại ->
    # báo nhầm thành "model 'paraformer-zh' is not registered".
    from .. import compat
    compat.patch_modelscope_hubconfig()

    from funasr import AutoModel

    key = ("funasr", model_name, device)
    model = _MODEL_CACHE.get(key)
    if model is None:
        cached = _local_model_dir(model_name)
        log(f"Nạp FunASR '{model_name}' + fsmn-vad + ct-punc "
            + ("(dùng bản đã tải trong máy)" if cached else "(lần đầu sẽ tải model)")
            + " ...", "info")
        model = AutoModel(
            model=_resolve(model_name),
            vad_model=_resolve("fsmn-vad"),
            vad_kwargs={"max_single_segment_time": 30000},
            punc_model=_resolve("ct-punc"),
            device="cuda:0" if str(device).startswith("cuda") else "cpu",
            disable_update=True,
        )
        _MODEL_CACHE[key] = model

    duration = ffprobe_duration(audio_path)
    if duration > _FUNASR_DIRECT_LIMIT_SECONDS:
        return _asr_funasr_chunked(model, audio_path, language, duration)

    if direct:
        import copy
        # Only callers with a short confirmed speech window may bypass VAD.
        if duration <= 0 or duration > 32:
            raise ValueError("Direct FunASR requires a speech window <=32s")
        res = model.inference(input=audio_path, model=model.model,
                              kwargs=copy.deepcopy(model.kwargs), disable_pbar=True)
    else:
        res = _funasr_generate(model, audio_path)
    normalized = normalize_funasr_result(res, duration_hint=duration)
    segs = normalized.segments
    if not segs:
        if duration > _FUNASR_CHUNK_SECONDS * 1.5:
            log("FunASR trả rỗng khi nhận nguyên file - thử chia nhỏ audio...",
                "warn")
            return _asr_funasr_chunked(model, audio_path, language, duration)
        raise FunASRResultError(normalized)

    if len(segs) == 1 and (segs[0].end - segs[0].start) > 120:
        log("ASR chỉ có một cue dài hơn 120s; cần kiểm tra raw text/timestamp "
            "và normalization trước khi quy lỗi cho model/VAD.", "warn")

    if normalized.status != "ASR_VALID":
        raise FunASRResultError(normalized)
    _set_last_marks(normalized.marks)
    return segs, _funasr_lang_from_result(res, language)


def _asr_sensevoice(audio_path: str, language: Optional[str],
                    device: str) -> Tuple[List[Segment], str]:
    return _asr_funasr(audio_path, language, device, model_name="iic/SenseVoiceSmall")
