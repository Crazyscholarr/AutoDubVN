"""Nhận diện phụ đề (ASR) - thiết kế CHỐNG MẤT ĐOẠN cho video dài.

VÌ SAO VIẾT LẠI: WhisperX bị 2 lỗi gốc khiến video 3 tiếng chỉ ra 15 phút sub:
  (1) VAD pyannote ép pad_onset=pad_offset=0 -> cắt cụt/bỏ qua cả đoạn có tiếng.
  (2) Giải mã theo lô KHÔNG có temperature fallback; các tham số an toàn
      (no_speech_threshold, compression_ratio_threshold, log_prob_threshold)
      hoàn toàn không được đọc -> Whisper đọc 1 câu rồi ngắt mà không ai bắt lỗi,
      timestamp vẫn đủ dài nhưng CHỮ MẤT SẠCH phần giữa.

KIẾN TRÚC MỚI (3 lớp):
  Lớp 1 - Engine tốt cho từng ngôn ngữ:
      "paraformer" : FunASR Paraformer-large + fsmn-vad + ct-punc.
                     Tốt nhất cho TIẾNG TRUNG (CER ~1.7% vs Whisper ~10%),
                     có timestamp câu gốc, ~2GB VRAM, an toàn với file 3 tiếng.
      "faster-whisper": cấu hình ĐÚNG - temperature fallback + Silero VAD có
                     speech_pad_ms (đệm thật) -> đa ngôn ngữ, không trôi/bịa.
      "sensevoice" : nhanh, chính xác nhưng timestamp thô (chỉ theo VAD).
      "whisperx"   : giữ để tương thích, đã ép tham số an toàn + cảnh báo.
  Lớp 2 - KIỂM TRA ĐỘ PHỦ: so tổng thời lượng nói bốc được với độ dài audio.
      Thiếu là báo động ngay, không im lặng cho qua.
  Lớp 3 - TỰ VÁ LỖ HỔNG (gap rescue): dò khoảng trống dài, cắt riêng đoạn đó,
      nhận diện lại rồi ghép vào đúng mốc thời gian tuyệt đối.
"""
from __future__ import annotations

from .common import (
    _CJK,
    _CJK_TERMINAL,
    _LAST_MARKS,
    _MODEL_CACHE,
    _TAG_RE,
    _clean,
    _is_cjk,
    _max_chars_for,
    _reindex,
    _set_last_marks,
    _take_last_marks,
    caption_options,
    caption_style_is_screen,
    reset_caption_options,
    set_caption_options,
)
from .detect import (
    _JUNK_PATTERNS,
    _NORM_KEY_RE,
    _VI_CHARS,
    _find_tmp_audio,
    _mean_volume_db,
    _nonsilent_ranges,
    _norm_key,
    _slice_audio,
    confirm_speech_holes,
    drop_hallucinations,
    ensure_speech_map,
    guess_language,
    speech_map_from_audio,
)
from .funasr import (
    normalize_funasr_result,
    FunASRResultError,
    _FUNASR_CHUNK_OVERLAP_SECONDS,
    _FUNASR_CHUNK_SECONDS,
    _FUNASR_DIRECT_LIMIT_SECONDS,
    _MS_REPO,
    _asr_funasr,
    _asr_funasr_chunked,
    _asr_sensevoice,
    _collect_sentence_info,
    _extract_funasr_chunk,
    _fmt_hms,
    _funasr_generate,
    _funasr_lang_from_result,
    _funasr_segments_from_result,
    _funasr_shape,
    _guess_time_scale,
    _iter_funasr_dicts,
    _local_model_dir,
    _marks_from_funasr,
    _resolve,
    _sentence_info_to_segments,
    _timestamp_pairs,
    _timestamp_payload_to_segments,
    _to_float,
)
from .merge import (
    apply_corrections,
    build_initial_prompt,
    clip_to_uncovered,
    coverage_report,
    find_gaps,
    find_uncovered_speech_ranges,
    is_speakable,
    merge_cjk_sentence_fragments,
    merge_new_segments,
    merge_time_ranges,
    normalize_segments,
    pack_screen_cues,
    parse_silencedetect_intervals,
    split_long,
    stitch_split_utterances,
    paint_short_speech_holes,
    apply_crop_to_hole,
)
from .evaluate import evaluate_captions, compare_transcripts
from .pipeline import _dispatch, _rescue_gaps, transcribe, withheld_vad_gaps
from .whisper import _asr_faster_whisper, _asr_whisperx
