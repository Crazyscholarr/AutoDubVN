"""Gán giọng, timeline phụ đề và xếp lịch chống đè thoại."""
from __future__ import annotations

import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

from ..asr import is_speakable
from ..srt_utils import Segment
from ..timeline import Placement, auto_fit, fit_segments_strict, resolve_sync_mode
from ..utils import ffprobe_duration, log, active_cancel_event
from ..video import change_speed, trim_silence
from . import vieneu as _vieneu
from .capcut import (
    _capcut_catalog_path,
    _load_capcut_client,
    _load_capcut_voice_status,
    _synth_all_capcut,
)
from .edge import _synth_all
from .vieneu import (
    _load_vieneu_model,
    _synth_all_vieneu,
    _vieneu_preset_voices,
)
from .common import (
    CAPCUT_DEFAULT_VOICE,
    DEFAULT_NARRATOR,
    VOICE_PRESETS,
    _format_ts,
    normalize_edge_narrator,
    _raise_if_cancelled,
)

def list_voices(engine: str = "edge") -> List[dict]:
    """Danh sách giọng CÓ THẬT của engine, để giao diện hiện đúng.

    Trả [{"id": ..., "name": ...}]. Không bao giờ ném lỗi - hỏng thì trả danh
    sách rỗng kèm ghi log, giao diện tự hiểu.
    """
    eng = (engine or "edge").lower()
    if eng == "capcut":
        # Voice.json là catalog tĩnh; không cần khởi tạo thiết bị/API chỉ để
        # hiện dropdown. Nhờ vậy mọi giọng vẫn hiện và nghe thử được kể cả khi
        # phiên CapCut chưa tạo client thành công ở lần nạp giao diện đầu tiên.
        try:
            catalog = _capcut_catalog_path()
            if catalog:
                with open(catalog, "r", encoding="utf-8") as f:
                    rows = json.load(f)
                statuses = _load_capcut_voice_status()
                seen, out = set(), []
                for row in rows if isinstance(rows, list) else []:
                    lang = str(row.get("lang") or row.get("lan") or "").lower()
                    voice_id = str(row.get("voice_type") or "").strip()
                    if lang not in {"vi", "vi-vn"} or not voice_id or voice_id in seen:
                        continue
                    # Đây là id của Microsoft Edge TTS, không phải speaker hợp lệ
                    # của endpoint CapCut. Voice.json của vài bản SDK trộn cả hai
                    # loại; đưa chúng vào dàn nhân vật sẽ trả err_code 40402004.
                    folded_id = voice_id.casefold()
                    if folded_id.startswith("vi-") and folded_id.endswith("neural"):
                        continue
                    seen.add(voice_id)
                    label = str(row.get("display_name") or voice_id).strip()
                    state = statuses.get(voice_id) if isinstance(statuses.get(voice_id), dict) else {}
                    out.append({"id": voice_id, "name": f"{label} ({voice_id})",
                                "ref": str(row.get("resource_id") or ""),
                                "status": state.get("status", "unknown"),
                                "status_error": state.get("error", "")})
                if out:
                    return out
            client = _load_capcut_client()
            voices = client.list_voices(lang="vi-VN", catalog_path=catalog)
            return [{"id": v.voice_type, "name": f"{v.display_name} ({v.voice_type})",
                     "ref": v.resource_id} for v in voices]
        except Exception as e:
            log(f"Chua lay duoc danh sach giong CapCut: {str(e)[:160]}", "warn")
            return []

    if eng == "vieneu":
        out = []
        try:
            model = _load_vieneu_model()
            for item in model.list_preset_voices():
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    label, vid = item[0], item[1]
                else:
                    label = vid = str(item)
                out.append({"id": str(vid), "name": str(label),
                            "ref": str(vid)})
        except Exception as e:
            log(f"Chưa lấy được danh sách giọng VieNeu: {str(e)[:160]}", "warn")
        return out

    ten = {"vi-VN-NamMinhNeural": "Nam Minh (nam)",
           "vi-VN-HoaiMyNeural": "Hoài My (nữ)"}
    seen, out = set(), []
    for i, p in enumerate(VOICE_PRESETS):
        key = f"{p['voice']}|{p['pitch']}"
        if key in seen:
            continue
        seen.add(key)
        base = ten.get(p["voice"], p["voice"])
        pitch = p["pitch"]
        extra = ("" if pitch in ("+0Hz", "0Hz")
                 else " · trẻ hơn" if pitch.startswith("+") else " · trầm hơn")
        out.append({"id": key, "name": base + extra})
    return out


def assign_voices(segments: List[Segment], mode: str = "narrator",
                  narrator: Optional[dict] = None, engine: str = "edge",
                  vieneu_voice: Optional[str] = None,
                  vieneu_voices: Optional[List[str]] = None) -> None:
    """Gán preset giọng cho từng segment (ghi vào seg.voice).

    engine="edge"   -> seg.voice = 'voice|pitch' (SSML edge-tts).
    engine="vieneu" -> seg.voice = tên giọng dựng sẵn của VieNeu-TTS (chuỗi
                       rỗng "" = dùng giọng mặc định của model).
    """
    engine = (engine or "edge").strip().lower()
    if engine == "capcut":
        presets = [v["id"] for v in list_voices("capcut")] or [CAPCUT_DEFAULT_VOICE]
        default_v = ((narrator or {}).get("voice") or CAPCUT_DEFAULT_VOICE)
        if default_v not in presets:
            presets = [default_v] + presets
        speakers = [s.speaker for s in segments if s.speaker]
        if mode == "per-speaker" and speakers and presets:
            uniq = sorted(set(speakers))
            mapping = {spk: presets[i % len(presets)] for i, spk in enumerate(uniq)}
            for s in segments:
                s.voice = mapping.get(s.speaker, default_v) or CAPCUT_DEFAULT_VOICE
        elif mode == "alternate" and presets:
            for i, s in enumerate(segments):
                s.voice = presets[i % len(presets)] or CAPCUT_DEFAULT_VOICE
        else:
            for s in segments:
                s.voice = default_v or CAPCUT_DEFAULT_VOICE
        return

    if engine == "vieneu":
        presets = vieneu_voices or _vieneu_preset_voices()
        default_v = vieneu_voice or (presets[0] if presets else None)
        speakers = [s.speaker for s in segments if s.speaker]
        if mode == "per-speaker" and speakers and presets:
            uniq = sorted(set(speakers))
            mapping = {spk: presets[i % len(presets)] for i, spk in enumerate(uniq)}
            for s in segments:
                s.voice = mapping.get(s.speaker, default_v) or ""
        elif mode == "alternate" and presets:
            for i, s in enumerate(segments):
                s.voice = presets[i % len(presets)] or ""
        else:
            for s in segments:
                s.voice = default_v or ""
        return

    narrator = normalize_edge_narrator(narrator)

    def tag(p):
        n = normalize_edge_narrator(p)
        return f"{n['voice']}|{n['pitch']}"

    speakers = [s.speaker for s in segments if s.speaker]
    if mode == "per-speaker" and speakers:
        uniq = sorted(set(speakers))
        mapping = {spk: VOICE_PRESETS[i % len(VOICE_PRESETS)] for i, spk in enumerate(uniq)}
        for s in segments:
            s.voice = tag(mapping.get(s.speaker, narrator))
    elif mode == "alternate":
        # Luân phiên nam/nữ theo lượt (khi không có diarization)
        for i, s in enumerate(segments):
            s.voice = tag(VOICE_PRESETS[i % 2])
    else:  # narrator
        for s in segments:
            s.voice = tag(narrator)


def build_narration_timeline(texts: List[str], durations: List[float],
                             metadata: Optional[List[dict]] = None) -> List[dict]:
    """Cộng dồn độ dài các đoạn TTS đã nối thành mốc thời gian tuyệt đối.

    Thuần logic để test được: trả [{"start","end","text"}] theo giây.
    """
    out: List[dict] = []
    cursor = 0.0
    for i, (text, dur) in enumerate(zip(texts, durations)):
        end = cursor + max(0.05, float(dur or 0.0))
        cleaned = (text or "").strip()
        if cleaned:
            item = {"start": round(cursor, 3), "end": round(end, 3),
                    "text": cleaned}
            if metadata and i < len(metadata):
                item.update({k: v for k, v in metadata[i].items() if v is not None})
            out.append(item)
        cursor = end
    return out


def build_voice_track(
    segments: List[Segment],
    workdir: str,
    total_duration: float,
    engine: str = "edge",
    voice_mode: str = "narrator",
    narrator: Optional[dict] = None,
    base_rate: str = "+0%",
    max_speed: float = 1.6,
    min_gap: float = 0.08,
    concurrency: int = 8,
    max_retries: int = 4,
    retry_base_delay: float = 1.2,
    fail_report_path: Optional[str] = None,
    recover_drift: bool = True,
    vieneu_voice: Optional[str] = None,
    vieneu_voices: Optional[List[str]] = None,
    vieneu_options: Optional[dict] = None,
    capcut_options: Optional[dict] = None,
    trim: bool = True,
    sync_offset_seconds: float = 0.0,
    sync_mode: str = "strict",
    trim_overflow: bool = True,
    max_overhang: float = 0.75,
    lock_av: Optional[bool] = None,
    max_start_drift: float = 5.0,
    semantic_cfg: Optional[dict] = None,
) -> Tuple[List[Optional[str]], List[float], List[Placement]]:
    """Tổng hợp giọng cho toàn bộ + xếp lịch chống đè.

    engine: "edge" (mặc định, edge-tts) | "vieneu" (VieNeu-TTS local, cần
            pip install vieneu - xem docstring đầu file).
    Trả về (danh sách clip cuối, danh sách mốc đặt, danh sách Placement).
    Những dòng lỗi TTS sau khi đã thử lại hết mức sẽ bị bỏ trống (không có giọng)
    và được liệt kê ra cảnh báo + file báo cáo (fail_report_path) để người dùng
    biết chính xác dòng nào cần xử lý thủ công.
    """
    cancel_event = active_cancel_event()
    _raise_if_cancelled(cancel_event)
    os.makedirs(workdir, exist_ok=True)
    engine = (engine or "edge").strip().lower()
    _vieneu._VIENEU_KWARGS = dict(vieneu_options or {})
    assign_voices(segments, voice_mode, narrator, engine=engine,
                 vieneu_voice=vieneu_voice, vieneu_voices=vieneu_voices)
    if semantic_cfg is not None:
        from ..semantic import speech_segments
        display = segments
        segments = speech_segments(display, semantic_cfg)
        for cue in display:
            cue.placed_start = None
            cue.voice_duration = None
            cue.audio_path = None
            cue.speed = 1.0
        # Group audio occupies the group's source interval. Display clocks are
        # stamped from the fitted clip after synthesis so on-screen captions
        # follow spoken Vietnamese, not leftover Chinese screen-pack gaps.
        sync_mode, lock_av, max_overhang = "strict", True, 0.0
        log(f"TTS theo {len(segments)} cụm câu; giữ {len(display)} cue hiển thị.", "info")

    if engine == "vieneu":
        engine_label = "VieNeu-TTS"
        raw_clips = _synth_all_vieneu(segments, workdir,
                                      max_retries=max(2, max_retries // 2))
    elif engine == "capcut":
        engine_label = "CapCut TTS"
        capcut_opts = dict(capcut_options or {})
        capcut_opts.setdefault(
            "fallback_voice", narrator.get("voice") or CAPCUT_DEFAULT_VOICE)
        raw_clips = _synth_all_capcut(
            segments, workdir, base_rate, concurrency,
            max_retries=max(2, max_retries // 2),
            capcut_options=capcut_opts)
    else:
        engine_label = "edge-tts"
        log(f"Tổng hợp giọng cho {len(segments)} dòng (edge-tts, {concurrency} luồng)...", "step")
        raw_clips = asyncio.run(_synth_all(segments, workdir, base_rate, concurrency,
                                           max_retries=max_retries,
                                           retry_base_delay=retry_base_delay))

    # Báo cáo các dòng vẫn lỗi sau mọi lần thử lại (sẽ bị câm tiếng trong video cuối)
    failed = [s for s, p in zip(segments, raw_clips) if is_speakable(s.text) and not p]
    if failed:
        log(f"{len(failed)}/{len(segments)} dòng KHÔNG tổng hợp được giọng sau khi đã thử lại "
            f"-> các đoạn này sẽ câm tiếng trong video.", "warn")
        lines = [f"[{_format_ts(s.start)} --> {_format_ts(s.end)}] (dòng {s.index}) {s.text}"
                for s in failed]
        for l in lines[:10]:
            log(f"  · {l}", "warn")
        if len(lines) > 10:
            log(f"  · ... và {len(lines) - 10} dòng khác (xem đầy đủ trong báo cáo).", "warn")
        if fail_report_path:
            try:
                with open(fail_report_path, "w", encoding="utf-8") as f:
                    f.write(f"Các dòng KHÔNG tổng hợp được giọng ({engine_label}) sau khi đã thử lại:\n\n")
                    f.write("\n".join(lines) + "\n")
                log(f"Đã ghi danh sách dòng lỗi: {fail_report_path}", "info")
            except OSError as e:
                log(f"Không ghi được báo cáo lỗi TTS: {e}", "warn")

    # Cắt khoảng lặng thừa hai đầu mỗi câu + đo độ dài tự nhiên. Trước đây chỗ
    # này là 4 LƯỢT tiến trình con TUẦN TỰ cho mỗi clip (đo trước, cắt, đo sau,
    # đo nat) - với 500-1000 dòng thoại, riêng tiền spawn process đã tốn nhiều
    # phút. Giờ gom về MỘT việc/clip (cắt + đo một lần) chạy song song 8 luồng;
    # tổng "trước/sau" để log được cộng ngay trong từng việc.
    nat: List[float] = [0.0] * len(raw_clips)
    todo_do = [(i, p) for i, p in enumerate(raw_clips) if p]
    if todo_do:
        t_do = time.time()

        def _trim_va_do(i: int, p: str):
            _raise_if_cancelled(cancel_event)
            truoc = ffprobe_duration(p) if trim else 0.0
            if trim:
                t_out = os.path.join(workdir, f"trim_{i:05d}.wav")
                p = trim_silence(p, t_out)
            sau = ffprobe_duration(p)
            return i, p, truoc, sau

        before = after = 0.0
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = [pool.submit(_trim_va_do, i, p) for i, p in todo_do]
            for fut in as_completed(futs):
                i, p, truoc, sau = fut.result()
                raw_clips[i] = p
                nat[i] = max(0.0, sau)
                before += truoc
                after += sau
        if trim and before > after > 0:
            log(f"Cắt khoảng lặng thừa: {before:.0f}s -> {after:.0f}s "
                f"(tiết kiệm {before - after:.0f}s)", "ok")
        log(f"Chuẩn hoá + đo {len(todo_do)} clip trong {time.time() - t_do:.1f}s "
            "(song song 8 luồng).", "dim")

    # Xếp lịch chống đè + TÌM TỐC ĐỘ NỀN đủ để không trôi (chia đều cho mọi câu
    # thay vì để câu 1.0x câu 1.6x nghe giật cục mà vẫn trôi).
    try:
        sync_offset_seconds = float(sync_offset_seconds or 0.0)
    except (TypeError, ValueError):
        sync_offset_seconds = 0.0
    starts = [min(max(0.0, s.start + sync_offset_seconds), max(0.0, total_duration))
              for s in segments]
    ends = [min(max(start + 0.01, s.end + sync_offset_seconds),
                max(start + 0.01, total_duration))
            for start, s in zip(starts, segments)]
    if abs(sync_offset_seconds) >= 0.001:
        direction = "trễ" if sync_offset_seconds > 0 else "sớm"
        log(f"Đồng bộ voice/sub: cho chạy {direction} {abs(sync_offset_seconds):.2f}s.", "info")
    sync_opts = {"sync_mode": sync_mode}
    if lock_av is not None:
        sync_opts["lock_av"] = lock_av
    sync_mode = resolve_sync_mode(sync_opts)
    try:
        max_overhang = max(0.0, float(max_overhang))
    except (TypeError, ValueError):
        max_overhang = 0.0
    try:
        max_start_drift = max(0.0, float(max_start_drift))
    except (TypeError, ValueError):
        max_start_drift = 5.0
    strict_sync = sync_mode == "strict"
    if strict_sync:
        placements = fit_segments_strict(
            starts, nat, max_speed=max_speed, min_gap=min_gap,
            total_duration=total_duration, trim_overflow=trim_overflow,
            ends=ends, max_overhang=max_overhang)
        trimmed_count = sum(1 for p in placements if p.trimmed)
        log("Khóa cứng lời thoại với hình: mỗi câu bám đúng mốc start gốc, "
            f"giữ ít nhất đến end gốc và chỉ được tràn tối đa {max_overhang:.2f}s "
            "vào khoảng im lặng; không dồn lệch sang câu sau trên phim dài.",
            "info")
        over = [p for p, e in zip(placements, ends)
                if p.placed_end > e + max_overhang + 0.05]
        if over:
            log(f"Còn {len(over)}/{len(placements)} clip đọc quá phụ đề của nó "
                "(bản dịch quá dài so với slot). Giảm translate.chars_per_sec "
                "hoặc tăng tts.max_speed.", "warn")
        if trimmed_count:
            log(f"Sync strict phải cắt đuôi {trimmed_count}/{len(placements)} clip "
                "vì bản đọc vẫn dài hơn slot dù đã tăng tốc. Muốn ít cắt hơn: "
                "rút gọn bản dịch hoặc tăng tts.max_speed.", "warn")
            overflow = []
            for seg, pl, natural in zip(segments, placements, nat):
                if not pl.trimmed:
                    continue
                overflow.append({
                    "index": getattr(seg, "index", None),
                    "start": round(float(getattr(seg, "start", 0.0) or 0.0), 3),
                    "end": round(float(getattr(seg, "end", 0.0) or 0.0), 3),
                    "text": (getattr(seg, "text", "") or "")[:240],
                    "natural_s": round(float(natural or 0.0), 3),
                    "slot_s": round(float(pl.final_dur or 0.0), 3),
                    "cut_s": round(max(0.0, float(natural or 0.0) - float(pl.final_dur or 0.0)), 3),
                    "speed": round(float(pl.speed or 1.0), 3),
                })
            overflow_path = None
            if fail_report_path:
                root, _ext = os.path.splitext(fail_report_path)
                if root.endswith(".tts_loi"):
                    overflow_path = root[:-8] + ".tts_overflow.json"
                else:
                    overflow_path = root + ".tts_overflow.json"
            if overflow_path:
                try:
                    with open(overflow_path, "w", encoding="utf-8") as fh:
                        json.dump({"count": len(overflow), "clips": overflow},
                                  fh, ensure_ascii=False, indent=2)
                    log(f"Đã ghi {len(overflow)} clip cắt đuôi: {overflow_path}", "info")
                except OSError as exc:
                    log(f"Không ghi được danh sách clip cắt đuôi: {exc}", "warn")
    else:
        placements, base = auto_fit(starts, nat, max_speed=max_speed, min_gap=min_gap,
                                    total_duration=total_duration,
                                    recover_drift=recover_drift,
                                    max_start_drift=max_start_drift)
        if base > 1.001:
            log(f"Giọng đọc dài hơn thời lượng video -> nói nhanh đều {base:.2f}x "
                "cho khớp hình (thay vì tăng tốc giật cục từng câu).", "info")
        log(f"Cascade: trần lệch start so với hình {max_start_drift:.1f}s "
            "(không để trôi cộng dồn hết phim).", "info")
    worst = max((p.drift for p in placements), default=0.0)
    if worst > 3.0:
        log(f"CẢNH BÁO: vẫn trễ tới {worst:.1f}s so với hình. Bản dịch còn dài "
            "quá so với thoại gốc - có thể tăng tts.max_speed nhẹ hoặc giảm "
            "tts.sync_offset_seconds nếu đang đặt trễ quá nhiều.", "warn")

    # Áp tăng tốc cho clip nào cần — SONG SONG (ThreadPoolExecutor) để nhanh hơn
    final_clips: List[Optional[str]] = list(raw_clips)  # copy, sẽ ghi đè chỗ cần
    placed_starts: List[float] = []
    speed_jobs = []  # (index_in_list, clip_path, output_path, speed, seg_index)
    for idx, (seg, clip, pl) in enumerate(zip(segments, raw_clips, placements)):
        seg.placed_start = pl.placed_start
        seg.speed = pl.speed
        seg.voice_duration = pl.final_dur if clip else None
        placed_starts.append(pl.placed_start)
        if clip is not None and (pl.speed > 1.001 or pl.trimmed):
            fout = os.path.join(workdir, f"final_{pl.index:05d}.wav")
            limit = pl.final_dur if pl.trimmed else None
            speed_jobs.append((idx, clip, fout, pl.speed, pl.index, limit))

    if speed_jobs:
        t_speed = time.time()
        log(f"Đổi tốc độ {len(speed_jobs)} clip (song song, tối đa 4 luồng)...", "step")
        done_count = 0

        def _do_speed(job):
            idx, clip, fout, speed, seg_idx, limit = job
            try:
                _raise_if_cancelled(cancel_event)
                change_speed(clip, fout, speed, max_duration=limit)
                return idx, fout, None
            except InterruptedError:
                raise
            except Exception as e:
                return idx, clip, e  # giữ bản gốc nếu lỗi

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_do_speed, j): j for j in speed_jobs}
            for fut in as_completed(futures):
                idx, result_path, err = fut.result()
                final_clips[idx] = result_path
                if err:
                    j = futures[fut]
                    log(f"Không tăng tốc được dòng {j[4]}: {err}", "warn")
                done_count += 1
                if done_count % 10 == 0 or done_count == len(speed_jobs):
                    log(f"  ...đổi tốc độ {done_count}/{len(speed_jobs)} clip", "info")

        log(f"Đổi tốc độ xong trong {time.time() - t_speed:.1f}s.", "ok")

    # Đo LẠI độ dài THẬT của từng clip sau khi đổi tốc độ/cắt đuôi.
    # Trước đây seg.voice_duration giữ nguyên pl.final_dur (độ dài DỰ KIẾN) đặt
    # từ trước khi chạy ffmpeg. Nếu change_speed lỗi, nhánh except giữ lại clip
    # GỐC (dài hơn, chưa tăng tốc) nhưng SRT vẫn được ghi theo độ dài dự kiến
    # -> phụ đề ghi lại ngắn hơn giọng thật, nghe như thoại chạy trước hình.
    # (Đo song song - đây từng là một lượt ffprobe tuần tự nữa cho mỗi dòng.)
    _raise_if_cancelled(cancel_event)
    # Unchanged (or failed-to-transform) clips already have an exact measurement.
    real_dur_map = {idx: nat[idx] for idx, clip in enumerate(final_clips)
                    if clip and clip == raw_clips[idx]}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(ffprobe_duration, clip): idx
                for idx, clip in enumerate(final_clips)
                if clip and idx not in real_dur_map}
        for fut in as_completed(futs):
            real_dur_map[futs[fut]] = fut.result()

    strict_guarded = 0
    for idx, (seg, clip, pl) in enumerate(zip(segments, final_clips, placements)):
        seg.audio_path = clip
        if clip:
            real_dur = real_dur_map.get(idx, 0.0)
            # Strict là một cam kết về timeline, kể cả khi lần atempo phía
            # trên lỗi và nhánh fallback giữ lại clip tự nhiên.  Nếu chỉ đo
            # lại rồi chấp nhận clip dài, giọng có thể tiếp tục hàng chục giây
            # sau hình tương ứng.  Áp trần dự kiến lần cuối trước khi mixer đọc.
            if (strict_sync and trim_overflow and pl.final_dur > 0.01
                    and real_dur > pl.final_dur + 0.03):
                guarded = os.path.join(workdir, f"guard_{pl.index:05d}.wav")
                try:
                    change_speed(clip, guarded, 1.0,
                                 max_duration=pl.final_dur)
                    guarded_dur = ffprobe_duration(guarded)
                    if guarded_dur > 0.01:
                        final_clips[idx] = guarded
                        clip = guarded
                        real_dur = guarded_dur
                        seg.audio_path = guarded
                        pl.trimmed = True
                        pl.final_dur = min(pl.final_dur, guarded_dur)
                        strict_guarded += 1
                except InterruptedError:
                    raise
                except Exception as e:
                    log(f"Không áp được trần strict cho dòng {seg.index}: {e}",
                        "warn")
            if real_dur > 0.01:
                seg.voice_duration = real_dur

    if strict_guarded:
        log(f"Đã áp lại trần strict cho {strict_guarded} clip có thời lượng thực "
            "dài hơn kế hoạch; không cho thoại kéo dài quá hình.", "warn")

    if semantic_cfg is not None:
        from ..semantic import stamp_display_cues_to_speech
        n = stamp_display_cues_to_speech(display, segments, semantic_cfg)
        if n:
            log(f"Đã gắn mốc phụ đề theo giọng đọc: {n} cue / {len(segments)} cụm.", "ok")

    return final_clips, placed_starts, placements
