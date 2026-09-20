"""Dựng video kể chuyện: slideshow, chạy tất cả, mux, phụ đề."""
from __future__ import annotations

import os
import time
from typing import Dict, Optional

from ... import overlays, srt_utils
from ...srt_utils import Segment
from ...utils import ffprobe_duration
from ..state import (HERE, STATE, _LOCK, current_cancel_event, _log, _progress,
                     _find)
from ..helpers import _safe_path_stem, _doc_file_van_ban
from ..projects import (get_project, _active_media_span, _run_stem_for_project,
                        _project_rows_for_span, _segments_from_rows,
                        _render_project_for_span)
from ..render import render_with_layers, render_with_layers_chunked
from .common import (
    JsonResult, _api, _raise_if_cancelled, _mark_manual_cancelled,
    _lay_van_ban_tu_body, _ensure_story_ctas, _cta_tts_options,
    _story_tts_workdir,
)
from .images import _story_image_inputs, _generated_story_image_inputs, \
    _story_slideshow_images
from .sources import _story_video_inputs
from .audio import _story_voice_plan, _save_voice_cast
from .youtube import _save_story_deliverables


def _load_cfg():
    return _api()._load_cfg()


def submit_job(*args, **kwargs):
    return _api().submit_job(*args, **kwargs)


def _story_output_dir(title: str) -> str:
    return _api()._story_output_dir(title)


def _segments_tu_timeline(timeline) -> list:
    return [Segment(i + 1, float(t["start"]), float(t["end"]), str(t["text"]))
            for i, t in enumerate(timeline or [])]


def _ass_tu_srt(srt_path: str, workdir: str, w: int, h: int,
                style: Optional[Dict]) -> Optional[str]:
    """Dựng file .ass phụ đề kể chuyện từ SRT khớp giọng đọc.

    Mặc định hợp video kể chuyện: chữ dưới đáy, cho xuống 2 dòng (khác chế độ
    lồng tiếng vốn ép 1 dòng), cỡ chữ theo cạnh ngắn nên khổ dọc 9:16 vẫn cân.
    """
    if not srt_path or not os.path.isfile(srt_path):
        return None
    segs = srt_utils.load_srt_file(srt_path)
    if not segs:
        return None
    st = dict(overlays.DEFAULT_SUB_STYLE)
    st.update({
        "single_line": False,
        "align": "bottom-center",
        "margin_v": max(40, int(h * 0.06)),
        "size": max(28, int(min(w, h) * 0.045)),
        "box": None,
    })
    if isinstance(style, dict):
        st.update({k: v for k, v in style.items() if v is not None})
    ass_path = os.path.join(workdir, "phu_de_ke_chuyen.ass")
    overlays.save_ass(ass_path, segs, w, h, st)
    return ass_path


def api_manual_slideshow(b: Dict) -> JsonResult:
    with _LOCK:
        current = str((STATE.get("manual") or {}).get("audio_path") or "")
    audio_path = str(b.get("audio_path") or current).strip().strip('"')
    anh = _story_image_inputs(b)
    video_sources = _story_video_inputs(b)
    if not anh and not video_sources:
        return {"error": "Hãy chọn ảnh, thư mục ảnh hoặc video nguồn."}, 400
    if not audio_path or not os.path.isfile(audio_path):
        return {"error": "Hãy tạo giọng đọc trước khi dựng video."}, 400
    with _LOCK:
        if STATE["running"] or STATE["busy"]:
            return {"error": "Đang bận: " +
                    (STATE["busy"] or "đang xử lý")}, 409
        STATE["cancel"] = False
        STATE["running"] = True
        render_kind = "video nguồn" if video_sources else "ảnh"
        STATE["busy"] = f"Đang dựng video từ {render_kind}…"
        manual = STATE["manual"]
        manual.update({"working": True, "status": f"Đang dựng video từ {render_kind}…",
                       "error": "", "output_path": "",
                       "source_videos": video_sources,
                       "rev": int(manual.get("rev", 0)) + 1})

    def _slideshow_work(payload=dict(b), imgs=list(anh),
                        source_videos=list(video_sources),
                        voice=os.path.abspath(audio_path)):
        try:
            from ... import slideshow as ss
            cfg = _load_cfg()
            sc = cfg.get("slideshow", {}) or {}
            w = int(payload.get("w") or sc.get("w", 1920))
            h = int(payload.get("h") or sc.get("h", 1080))
            fps = int(payload.get("fps") or sc.get("fps", 30))
            kieu = str(payload.get("kieu") or sc.get("kieu", "chuyen_dong"))
            title = _safe_path_stem(
                payload.get("name") or os.path.splitext(
                    os.path.basename(voice))[0],
                fallback="video_ke_chuyen", limit=70)
            out_dir = _story_output_dir(title)
            workdir = os.path.join(out_dir, "_tmp", title)
            out_path = os.path.join(out_dir, f"{title}.mp4")
            os.makedirs(workdir, exist_ok=True)
            # Phụ đề cứng (tuỳ chọn): dùng SRT sinh ra lúc tạo giọng đọc.
            ass_path = None
            sub_cfg = payload.get("sub") if isinstance(payload.get("sub"), dict) else {}
            if sub_cfg.get("enabled"):
                with _LOCK:
                    srt_path = str((STATE.get("manual") or {}).get("srt_path") or "")
                ass_path = _ass_tu_srt(srt_path, workdir, w, h, sub_cfg.get("style"))
                if not ass_path:
                    _log("Chưa có phụ đề khớp giọng đọc (hãy tạo audio bằng app "
                         "trước) - dựng video không phụ đề.", "warn")
            if source_videos:
                clip_min = float(payload.get("source_clip_min_seconds", 300) or 300)
                clip_max = float(payload.get(
                    "source_clip_max_seconds",
                    payload.get("source_clip_seconds", 600)) or 600)
                random_pick = bool(payload.get("source_random", True))
                _log(
                    "[Kể chuyện] Dùng lại audio có sẵn; bắt đầu %s %d mục "
                    "video nguồn (mỗi đoạn %.1f–%.1f phút)."
                    % ("random" if random_pick else "xếp tuần tự",
                       len(source_videos), clip_min / 60.0, clip_max / 60.0),
                    "step")

                def _video_progress(pct, detail):
                    _progress(pct=pct, step="Dựng video từ video nguồn",
                              detail=detail)
                    if (float(pct) <= 3.0 or
                            str(detail).startswith("FFmpeg vẫn đang ghép")):
                        _log("[Kể chuyện] " + str(detail), "step")

                result = ss.tao_video_tu_video(
                    source_videos, voice, out_path, workdir=workdir,
                    w=w, h=h, fps=fps,
                    hieu_ung=str(payload.get("source_effect") or "tinh"),
                    ass_path=ass_path,
                    logo=(payload.get("logo") if isinstance(payload.get("logo"), dict)
                          else None),
                    character=(payload.get("character")
                               if isinstance(payload.get("character"), dict)
                               else None),
                    source_cover=str(payload.get("source_cover") or "none"),
                    min_seconds=clip_min, max_seconds=clip_max,
                    random_pick=random_pick,
                    random_seed=int(payload.get("source_random_seed", 0) or 0) or None,
                    transform=(payload.get("source_transform")
                               if isinstance(payload.get("source_transform"), dict)
                               else None),
                    blur_regions=(payload.get("regions")
                                  if isinstance(payload.get("regions"), list)
                                  else None),
                    blur_bottom_ratio=float(payload.get("blur_bottom_ratio", 0) or 0),
                    progress=_video_progress)
            else:
                render_imgs, keep_repeats = _story_slideshow_images(
                    payload, imgs, ffprobe_duration(voice))
                result = ss.tao_video_tu_anh(
                    render_imgs, voice, out_path, workdir=workdir,
                    w=w, h=h, fps=fps, kieu=kieu, ass_path=ass_path,
                    logo=(payload.get("logo") if isinstance(payload.get("logo"), dict)
                          else None),
                    character=(payload.get("character")
                               if isinstance(payload.get("character"), dict)
                               else None),
                    giu_canh_lap=keep_repeats,
                    progress=lambda pct, detail: _progress(
                        pct=pct, step="Dựng video từ ảnh", detail=detail))
            metadata_path, calendar_path = _save_story_deliverables(
                result["path"], payload)
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"working": False,
                               "status": "Dựng video hoàn tất",
                               "output_path": result["path"], "error": "",
                               "metadata_path": metadata_path,
                               "calendar_path": calendar_path,
                               "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Dựng video xong",
                      detail=os.path.basename(result["path"]))
            if source_videos:
                _log("Đã random %d đoạn từ %d video nguồn: %s"
                     % (result["so_doan"], result["so_video"], result["path"]), "ok")
            else:
                _log(f"Đã dựng video từ {result['so_anh']} ảnh: {result['path']}", "ok")
        except InterruptedError:
            _mark_manual_cancelled(
                "Đã dừng dựng video; audio và nguồn hình vẫn được giữ lại.")
        except Exception as e:
            _log(f"Dựng video từ nguồn hình lỗi: {e}", "err")
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"working": False, "status": "Dựng video lỗi",
                               "error": str(e)[:300],
                               "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Dựng video lỗi", detail=str(e)[:160])
        finally:
            with _LOCK:
                STATE["running"] = False
                STATE["busy"] = ""

    submit_job(_slideshow_work, name="Dựng video kể chuyện", resource="ffmpeg",
               metadata={"kind": "story_render"})
    return {"ok": True, "async": True}, 200


def api_story_generated_script() -> JsonResult:
    """Trả bản đọc vừa tạo cho giao diện; không nhét văn bản dài vào /api/state."""
    with _LOCK:
        manual = STATE.get("manual") or {}
        path = str(manual.get("script_path") or "")
        title = str(manual.get("script_title") or "")
        words = int(manual.get("script_words") or 0)
    if not path or not os.path.isfile(path):
        return {"error": "Chưa có kịch bản vừa tạo."}, 404
    try:
        text = _doc_file_van_ban(path)
    except Exception as exc:
        return {"error": "Không đọc được kịch bản vừa tạo: %s" % exc}, 500
    return {"text": text, "path": path, "title": title, "words": words}, 200


def api_story_generate_and_run(b: Dict) -> JsonResult:
    """Một nút: tiêu đề -> KICH_BAN_DOC.txt -> TTS/nhạc/sub/video."""
    title = str(b.get("story_title") or b.get("title") or "").strip()
    if not title:
        return {"error": "Hãy nhập tiêu đề truyện cần viết."}, 400
    payload, anh, stale_pack_title = _generated_story_image_inputs(b, title)
    if stale_pack_title:
        _log("Tiêu đề mới '%s': không dùng lại ảnh của '%s'; sẽ tạo gói ảnh mới."
             % (title, stale_pack_title), "info")
    with _LOCK:
        if STATE["running"] or STATE["busy"]:
            return {"error": "Đang bận: " + (STATE["busy"] or "đang xử lý")}, 409
        STATE["cancel"] = False
        STATE["running"] = True
        STATE["busy"] = "Đang tạo kịch bản từ tiêu đề…"
        manual = STATE["manual"]
        manual.update({
            "working": True, "status": "Đang lập và viết kịch bản…",
            "error": "", "output_path": "", "script_path": "",
            "script_title": title, "script_words": 0,
            "recommended_voice": "", "voice_analysis": {},
            "voice_recommendations": [], "voice_cast": [],
            "voice_assignment_coverage": 0.0, "voice_cast_path": "",
            "image_pack_path": "", "image_prompt_path": "",
            "image_provider_url": "", "image_scene_count": 0,
            "image_ready_count": 0, "image_prompt_ready": False,
            "image_generation_status": "",
            "content_idea_id": str(payload.get("content_idea_id") or
                                   manual.get("content_idea_id") or ""),
            "rewrite_brief": str(payload.get("rewrite_brief") or
                                 manual.get("rewrite_brief") or ""),
            "rev": int(manual.get("rev", 0)) + 1,
        })
    _progress(pct=2, step="Tạo kịch bản", detail="Mở công cụ viết truyện")

    def _work(payload=dict(payload), imgs=list(anh), source_title=title):
        handed_off = False
        try:
            from ... import story_writer
            cfg = _load_cfg()

            def _writer_progress(done, total, message=""):
                ratio = float(done) / max(1, int(total))
                _progress(pct=3 + ratio * 32, step="Tạo kịch bản",
                          detail=message[:160] or ("Đã xong %d/%d bước" % (done, total)))

            result = story_writer.generate(
                source_title, cfg, log=_log, progress=_writer_progress,
                cancel_event=current_cancel_event(),
                rewrite_brief=str(payload.get("rewrite_brief") or ""),
                cta=(payload.get("cta") if isinstance(payload.get("cta"), dict)
                     else None))
            _raise_if_cancelled()
            with _LOCK:
                manual = STATE["manual"]
                manual.update({
                    "script_path": result["script_path"],
                    "script_title": result["title"],
                    "script_words": result["words"],
                    "status": "Đã tạo kịch bản; đang kiểm tra chất lượng…",
                    "rev": int(manual.get("rev", 0)) + 1,
                })
            writer_cfg = cfg.get("tao_kich_ban") \
                if isinstance(cfg.get("tao_kich_ban"), dict) else {}
            measured = result.get("meta", {}).get("kiem_tra_tu_dong")
            quality_issues = []
            if isinstance(measured, dict):
                # Mốc 12.000 là mục tiêu biên tập, không nên làm mất cả lượt
                # chạy dài khi file TTS cuối chỉ thiếu vài phần trăm. Dùng số
                # từ của KICH_BAN_DOC (đã gồm CTA) để quyết định bàn giao;
                # vẫn chặn bản thiếu đáng kể hoặc vượt trần 16.000 từ.
                target_min = max(1, int(writer_cfg.get(
                    "quality_target_min_words", 12000) or 12000))
                target_max = max(target_min, int(writer_cfg.get(
                    "quality_target_max_words", 16000) or 16000))
                min_ratio = max(.80, min(1.0, float(writer_cfg.get(
                    "quality_min_word_ratio", .95) or .95)))
                final_words = int(result.get("words") or
                                  measured.get("total_words") or 0)
                minimum_handoff = int(round(target_min * min_ratio))
                word_handoff_ok = minimum_handoff <= final_words <= target_max
                if not word_handoff_ok:
                    quality_issues.append("tổng số từ ngoài mục tiêu")
                elif not measured.get("within_target", False):
                    _log(("Kịch bản đạt %.1f%% mục tiêu từ (%s/%s); sai số nhỏ "
                          "nên vẫn tiếp tục tạo video.") %
                         (100.0 * final_words / target_min,
                          format(final_words, ","), format(target_min, ",")), "warn")
                if not measured.get("dialogue_target", False):
                    quality_issues.append("tỉ lệ đoạn đối thoại dưới 45%")
                if measured.get("banned_terms"):
                    quality_issues.append("còn từ cấm")
                if measured.get("repeated_paragraphs"):
                    _log("Kịch bản có %s đoạn lặp nguyên văn; nên xem 98_kiem_tra_tu_dong.txt."
                         % measured["repeated_paragraphs"], "warn")
            if quality_issues and writer_cfg.get("require_quality_pass", True):
                raise RuntimeError(
                    "Kịch bản đã được lưu nhưng chưa dựng video để tránh tốn TTS: %s. "
                    "Xem 98_kiem_tra_tu_dong.txt trong %s."
                    % (", ".join(quality_issues), result["folder"]))
            source_videos = _story_video_inputs(payload)
            auto_img = bool(payload.get("auto_images", True))
            if not imgs and not source_videos:
                if auto_img:
                    imgs, image_pack = _api()._prepare_generated_story_images(
                        result, payload, cfg)
                    _raise_if_cancelled()
                    payload["image_pack"] = image_pack
                    if not imgs:
                        ready = int((STATE.get("manual") or {}).get(
                            "image_ready_count", 0) or 0)
                        total = int((STATE.get("manual") or {}).get(
                            "image_scene_count", 0) or 0)
                        with _LOCK:
                            manual = STATE["manual"]
                            manual.update({
                                "working": False,
                                "status": ("Tạo ảnh tạm dừng ở %d/%d; chờ bổ sung ảnh "
                                           "hoặc bấm Tiếp tục"
                                           % (ready, total)),
                                "error": "",
                                "rev": int(manual.get("rev", 0)) + 1,
                            })
                        _progress(pct=100, step="Đang chờ ảnh",
                                  detail="Ảnh đã lưu; bấm Tiếp tục để tạo các cảnh còn thiếu")
                        _log("Tạo ảnh đã tạm dừng ở %d/%d. Ảnh đã có vẫn được giữ; "
                             "bấm Tiếp tục để chạy từ cảnh còn thiếu." % (ready, total), "warn")
                        return
                else:
                    _log("Đã tắt tạo ảnh AI: bỏ qua bước tạo ảnh, chuyển sang tạo giọng đọc.", "info")
            auto_voice_id = ""
            voice_result = None
            if bool(payload.get("voice_auto", False)) or bool(payload.get("multi_voice", False)):
                script_text = _doc_file_van_ban(result["script_path"])
                engine = str(payload.get("engine") or
                             (cfg.get("tts") or {}).get("engine") or "capcut").lower()
                plan_payload = dict(payload)
                plan_payload["character_context_path"] = result.get("design_path") or ""
                voice_result = _story_voice_plan(
                    script_text, plan_payload, cfg, engine,
                    str(payload.get("voice") or ""), str(payload.get("pitch") or "+0Hz"))
                if voice_result:
                    auto_voice_id = str((voice_result.get("narrator") or {}).get("id") or "")
                    narrator = voice_result.get("narrator") or {}
                    _log("Giọng kể tự chọn: %s. Đã gán %d nhân vật, phủ %.1f%% lượt thoại."
                         % (narrator.get("name") or auto_voice_id,
                            len(voice_result.get("cast") or []),
                            float(voice_result.get("assignment_coverage") or 0)), "ok")
            with _LOCK:
                manual = STATE["manual"]
                manual.update({
                    "status": "Đã tạo kịch bản; đang tự nạp vào dựng video…",
                    "recommended_voice": auto_voice_id,
                    "voice_analysis": (voice_result or {}).get("analysis") or {},
                    "voice_recommendations": (voice_result or {}).get("recommendations") or [],
                    "voice_cast": (voice_result or {}).get("cast") or [],
                    "voice_assignment_coverage": float(
                        (voice_result or {}).get("assignment_coverage") or 0),
                    "rev": int(manual.get("rev", 0)) + 1,
                })
                # Bàn giao khoá cho api_manual_run_all ngay trong thread này.
                STATE["running"] = False
                STATE["busy"] = ""
            _progress(pct=48, step="Kịch bản và hình ảnh hoàn tất",
                      detail="%s từ · đang tạo giọng đọc" % result["words"])
            video_payload = dict(payload)
            video_payload.pop("text", None)
            video_payload["txt_path"] = result["script_path"]
            video_payload["name"] = str(payload.get("name") or result["title"])
            video_payload["anh"] = imgs
            video_payload["video_sources"] = source_videos
            video_payload["character_context_path"] = result.get("design_path") or ""
            if auto_voice_id:
                video_payload["voice"] = auto_voice_id
            video_payload["_progress_start"] = 48
            video_payload["_handoff"] = True
            _raise_if_cancelled()
            response, code = _api().api_manual_run_all(video_payload)
            if code != 200:
                raise RuntimeError(response.get("error") or "Không khởi động được bước dựng video.")
            handed_off = True
            _log("Đã tự nạp KICH_BAN_DOC.txt vào pipeline video kể chuyện.", "ok")
        except InterruptedError:
            _mark_manual_cancelled(
                "Đã giữ lại kịch bản, prompt, ảnh và các phần đã hoàn thành.")
        except Exception as exc:
            _log("Tạo kịch bản và video lỗi: %s" % exc, "err")
            with _LOCK:
                manual = STATE["manual"]
                manual.update({
                    "working": False, "status": "Quy trình tự động bị lỗi",
                    "error": str(exc)[:300],
                    "rev": int(manual.get("rev", 0)) + 1,
                })
            _progress(pct=100, step="Tạo kịch bản lỗi", detail=str(exc)[:160])
        finally:
            if not handed_off:
                with _LOCK:
                    STATE["running"] = False
                    STATE["busy"] = ""

    submit_job(_work, name="Tạo kịch bản kể chuyện", resource="ai",
               metadata={"kind": "story_generate"})
    return {"ok": True, "async": True}, 200


def api_manual_run_all(b: Dict) -> JsonResult:
    """CHẠY TẤT CẢ cho chế độ Kể chuyện: text -> giọng -> nhạc -> phụ đề -> video.

    Body: {text|txt_path, name, engine, voice, pitch, rate,
           anh: [file/thư mục...], w, h, fps, kieu,
           nhac: {enabled, bai, muc_db, duck, duck_ratio, fade},
           sub: {enabled, style: {size, color, align, ...}}}
    """
    text, err = _lay_van_ban_tu_body(b)
    if err:
        return err
    anh = _story_image_inputs(b)
    video_sources = _story_video_inputs(b)
    audio_only = not anh and not video_sources
    try:
        progress_start = max(0.0, min(95.0, float(b.get("_progress_start", 0) or 0)))
    except (TypeError, ValueError):
        progress_start = 0.0

    def _story_progress(pct, step, detail):
        mapped = progress_start + (100.0 - progress_start) * float(pct) / 100.0
        _progress(pct=mapped, step=step, detail=detail)

    with _LOCK:
        if STATE["running"] or STATE["busy"]:
            return {"error": "Đang bận: " +
                    (STATE["busy"] or "đang xử lý")}, 409
        if b.get("_handoff") and current_cancel_event().is_set():
            return {"error": "Đã dừng tác vụ theo yêu cầu."}, 409
        STATE["cancel"] = False
        STATE["running"] = True
        STATE["busy"] = "Đang làm video kể chuyện…"
        manual = STATE["manual"]
        manual.update({"working": True,
                       "status": "Bước 1/4: tổng hợp giọng đọc…",
                       "error": "", "output_path": "",
                       "image_pack_path": str(b.get("image_pack") or ""),
                       "image_ready_count": len(anh),
                       "image_prompt_ready": False,
                       "image_generation_status": (
                           "Đã nhận video nguồn; bắt đầu dựng video"
                           if video_sources else "Đã nhận ảnh; bắt đầu dựng video" if anh else "Đang tạo giọng đọc"),
                       "source_videos": video_sources,
                       "voice_cast": [], "voice_assignment_coverage": 0.0,
                       "voice_cast_path": "",
                       "rev": int(manual.get("rev", 0)) + 1})
    _story_progress(pct=3, step="Video kể chuyện", detail="Chuẩn bị văn bản")

    def _work(payload=dict(b), source_text=text, imgs=list(anh),
              source_videos=list(video_sources)):
        try:
            from ... import tts as tts_mod, nhac_nen as nn, slideshow as ss
            source_text = _ensure_story_ctas(source_text, payload)
            cfg = _load_cfg()
            tc = cfg.get("tts", {}) or {}
            nc = cfg.get("nhac_nen", {}) or {}
            sc = cfg.get("slideshow", {}) or {}

            engine = str(payload.get("engine") or tc.get("engine") or "edge").lower()
            default_voice = (tc.get("vieneu_voice") if engine == "vieneu"
                             else tc.get("capcut_voice") if engine == "capcut"
                             else tc.get("narrator_voice"))
            voice = str(payload.get("voice") or default_voice or
                        "vi-VN-NamMinhNeural")
            pitch = str(payload.get("pitch") or tc.get("narrator_pitch") or "+0Hz")
            rate = str(payload.get("rate") or tc.get("base_rate") or "+0%")
            w = int(payload.get("w") or sc.get("w", 1920))
            h = int(payload.get("h") or sc.get("h", 1080))
            fps = int(payload.get("fps") or sc.get("fps", 30))
            kieu = str(payload.get("kieu") or sc.get("kieu", "chuyen_dong"))
            nhac = payload.get("nhac") if isinstance(payload.get("nhac"), dict) else {}
            sub_cfg = payload.get("sub") if isinstance(payload.get("sub"), dict) else {}

            title = _safe_path_stem(payload.get("name") or source_text[:50],
                                    fallback="video_ke_chuyen", limit=70)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            out_dir = _story_output_dir(title)
            workdir, reusable_clips = _story_tts_workdir(
                out_dir, title, stamp, engine)
            os.makedirs(workdir, exist_ok=True)
            if reusable_clips:
                _log("[Kể chuyện] Tiếp tục từ %d đoạn CapCut đã tạo ở lượt trước."
                     % reusable_clips, "ok")

            voice_plan = _story_voice_plan(
                source_text, payload, cfg, engine, voice, pitch)
            if voice_plan:
                voice = str((voice_plan.get("narrator") or {}).get("id") or voice)
                cast_count = len(voice_plan.get("cast") or [])
                if cast_count:
                    _log("[Kể chuyện] Dàn giọng: 1 người kể + %d nhân vật; "
                         "nhận diện chắc %.1f%% lượt thoại."
                         % (cast_count, float(voice_plan.get("assignment_coverage") or 0)), "ok")

            # ---- 1/4: giọng đọc ----
            _log(f"[Kể chuyện] 1/4 giọng đọc: engine={engine}, voice={voice}, "
                 f"khung {w}x{h}", "step")
            _story_progress(pct=8, step="Video kể chuyện", detail=f"1/4 Đọc bằng {engine}")
            tts_res = tts_mod.synthesize_text_audio(
                source_text, workdir, os.path.join(workdir, "giong_doc.mp3"),
                engine=engine, narrator={"voice": voice, "pitch": pitch},
                base_rate=rate,
                concurrency=int(tc.get("concurrency", 8) or 8),
                max_retries=int(tc.get("max_retries", 3) or 3),
                retry_base_delay=float(tc.get("retry_delay", 1.2) or 1.2),
                vieneu_options=tc.get("vieneu_options"),
                capcut_options=tc.get("capcut_options"),
                max_chunk_chars=240,   # đoạn ngắn -> mốc phụ đề mịn
                utterances=(voice_plan or {}).get("utterances"),
                **_cta_tts_options(payload),
                cancel_event=current_cancel_event())
            _raise_if_cancelled()
            srt_path = os.path.join(out_dir, f"{title}_{stamp}.srt")
            srt_utils.save_srt_file(
                srt_path, _segments_tu_timeline(tts_res.get("segments")))
            audio = tts_res["path"]
            cast_path = _save_voice_cast(
                os.path.join(out_dir, f"{title}_{stamp}.giong.json"), voice_plan)
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"audio_path": audio,
                               "audio_duration": tts_res["duration"],
                               "srt_path": srt_path,
                               "voice_cast": (voice_plan or {}).get("cast") or [],
                               "voice_assignment_coverage": float(
                                   (voice_plan or {}).get("assignment_coverage") or 0),
                               "voice_cast_path": cast_path,
                               "status": "Bước 2/4: nhạc nền…",
                               "rev": int(manual.get("rev", 0)) + 1})

            # ---- 2/4: nhạc nền (tuỳ chọn) ----
            if nhac.get("enabled", True):
                _raise_if_cancelled()
                _story_progress(pct=35, step="Video kể chuyện", detail="2/4 Trộn nhạc nền")
                if not str(nhac.get("bai") or "") and nc.get("tu_dong_tai", True):
                    cats = nc.get("danh_muc")
                    nn.dam_bao_co_nhac(
                        int(nc.get("so_bai_tai", 3) or 3),
                        categories=cats if isinstance(cats, list) else None)
                mix = nn.tron_nhac_nen(
                    audio, os.path.join(workdir, "giong_co_nhac.m4a"),
                    music_path=str(nhac.get("bai") or ""),
                    muc_db=float(nhac.get("muc_db", nc.get("muc_db", -38))),
                    duck=bool(nhac.get("duck", nc.get("duck", True))),
                    duck_ratio=float(nhac.get("duck_ratio", nc.get("duck_ratio", 8))),
                    fade=float(nhac.get("fade", nc.get("fade", 2.0))))
                audio = mix["path"]
                # Luôn công bố track cuối (đã có nhạc) cho nút dựng lại video.
                # Nếu giữ đường dẫn giong_doc.mp3 ở STATE, lượt "random video +
                # xuất MP4" sau đó sẽ vô tình làm mất nhạc nền.
                with _LOCK:
                    manual = STATE["manual"]
                    manual.update({"audio_path": audio,
                                   "audio_duration": float(
                                       mix.get("duration") or tts_res["duration"]),
                                   "rev": int(manual.get("rev", 0)) + 1})

            # ---- 3/4: phụ đề cứng (tuỳ chọn) ----
            ass_path = None
            _raise_if_cancelled()
            if sub_cfg.get("enabled", True):
                _story_progress(pct=45, step="Video kể chuyện", detail="3/4 Dựng phụ đề")
                ass_path = _ass_tu_srt(srt_path, workdir, w, h, sub_cfg.get("style"))

            # ---- Nếu chưa có ảnh/video: kết thúc ở audio để người dùng tải lên sau ----
            if not imgs and not source_videos:
                dur_str = "%dp%02ds" % (int(tts_res["duration"] // 60), int(tts_res["duration"] % 60))
                with _LOCK:
                    manual = STATE["manual"]
                    manual.update({"working": False,
                                   "status": f"Đã tạo xong audio ({dur_str})! Hãy thêm video nguồn (cột trái) rồi bấm Xuất MP4.",
                                   "audio_path": audio,
                                   "audio_duration": tts_res["duration"],
                                   "srt_path": srt_path,
                                   "error": "",
                                   "rev": int(manual.get("rev", 0)) + 1})
                _story_progress(pct=100, step="Audio sẵn sàng",
                                detail=f"Thời lượng audio: {dur_str} — Hãy chọn video nguồn để ghép")
                _log(f"[Kể chuyện] Đã tạo xong giọng đọc ({dur_str}) và phụ đề. Thêm video nguồn và bấm Xuất MP4 để hoàn thành.", "ok")
                return

            # ---- 4/4: dựng video từ ảnh hoặc video nguồn ----
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"status": ("Bước 4/4: dựng video từ video nguồn…"
                                            if source_videos else
                                            "Bước 4/4: dựng video từ ảnh…"),
                               "rev": int(manual.get("rev", 0)) + 1})
            out_path = os.path.join(out_dir, f"{title}_{stamp}.mp4")
            if source_videos:
                clip_min = float(payload.get("source_clip_min_seconds", 300) or 300)
                clip_max = float(payload.get(
                    "source_clip_max_seconds",
                    payload.get("source_clip_seconds", 600)) or 600)
                random_pick = bool(payload.get("source_random", True))
                _log(
                    "[Kể chuyện] 4/4 %s %d mục video nguồn cho đủ %.1f phút "
                    "audio (mỗi đoạn %.1f–%.1f phút)."
                    % ("random" if random_pick else "xếp tuần tự",
                       len(source_videos), float(tts_res["duration"]) / 60.0,
                       clip_min / 60.0, clip_max / 60.0),
                    "step")

                def _video_progress(pct, detail):
                    _story_progress(pct=50 + pct * 0.5,
                                    step="Video kể chuyện",
                                    detail="4/4 " + str(detail))
                    if (float(pct) <= 3.0 or
                            str(detail).startswith("FFmpeg vẫn đang ghép")):
                        _log("[Kể chuyện] " + str(detail), "step")

                result = ss.tao_video_tu_video(
                    source_videos, audio, out_path, workdir=workdir, w=w, h=h,
                    fps=fps, hieu_ung=str(payload.get("source_effect") or "tinh"),
                    ass_path=ass_path,
                    logo=(payload.get("logo") if isinstance(payload.get("logo"), dict)
                          else None),
                    character=(payload.get("character") if isinstance(payload.get("character"), dict)
                               else None),
                    source_cover=str(payload.get("source_cover") or "none"),
                    min_seconds=clip_min,
                    max_seconds=clip_max,
                    random_pick=random_pick,
                    random_seed=int(payload.get("source_random_seed", 0) or 0) or None,
                    transform=(payload.get("source_transform")
                               if isinstance(payload.get("source_transform"), dict)
                               else None),
                    blur_regions=(payload.get("regions")
                                  if isinstance(payload.get("regions"), list)
                                  else None),
                    blur_bottom_ratio=float(payload.get("blur_bottom_ratio", 0) or 0),
                    progress=_video_progress)
            else:
                render_imgs, keep_repeats = _story_slideshow_images(
                    payload, imgs, ffprobe_duration(audio))
                result = ss.tao_video_tu_anh(
                    render_imgs, audio, out_path, workdir=workdir, w=w, h=h, fps=fps,
                    kieu=kieu, ass_path=ass_path,
                    logo=(payload.get("logo") if isinstance(payload.get("logo"), dict)
                          else None), giu_canh_lap=keep_repeats,
                    character=(payload.get("character") if isinstance(payload.get("character"), dict)
                               else None),
                    progress=lambda pct, detail: _story_progress(
                        pct=50 + pct * 0.5, step="Video kể chuyện",
                        detail="4/4 " + str(detail)))
            metadata_path, calendar_path = _save_story_deliverables(
                result["path"], payload)
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"working": False,
                               "status": "Video kể chuyện hoàn tất",
                               "output_path": result["path"], "error": "",
                               "metadata_path": metadata_path,
                               "calendar_path": calendar_path,
                               "rev": int(manual.get("rev", 0)) + 1})
            _story_progress(pct=100, step="Video kể chuyện xong",
                            detail=os.path.basename(result["path"]))
            _log(f"[Kể chuyện] Xong: {result['path']}", "ok")
        except InterruptedError:
            _mark_manual_cancelled(
                "Đã giữ lại các đoạn giọng, audio, ảnh và phụ đề đã hoàn thành.")
        except Exception as e:
            _log(f"Làm video kể chuyện lỗi: {e}", "err")
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"working": False, "status": "Làm video lỗi",
                               "error": str(e)[:300],
                               "rev": int(manual.get("rev", 0)) + 1})
            _story_progress(pct=100, step="Video kể chuyện lỗi", detail=str(e)[:160])
        finally:
            with _LOCK:
                STATE["running"] = False
                STATE["busy"] = ""

    submit_job(_work, name="Làm video kể chuyện tự động", resource="ffmpeg",
               metadata={"kind": "story_run_all"})
    return {"ok": True, "async": True}, 200


def api_story_video_info(b: Dict) -> JsonResult:
    """Trả về thông tin các video nguồn: duration, resolution."""
    try:
        from ...video import ffprobe_duration
        from ...downloader import ffprobe_video_size
        paths = b.get("paths", [])
        if isinstance(paths, str):
            paths = [paths]
        from ...slideshow import liet_ke_video
        expanded = liet_ke_video(paths)
        results = []
        total_duration = 0.0
        for p in expanded:
            p = str(p).strip().strip('"')
            if not p or not os.path.isfile(p):
                results.append({"path": p, "error": "File không tồn tại"})
                continue
            try:
                dur = ffprobe_duration(p)
                size = ffprobe_video_size(p)
                total_duration += dur
                results.append({
                    "path": p,
                    "name": os.path.basename(p),
                    "duration": dur,
                    "width": size[0] if size else 0,
                    "height": size[1] if size else 0,
                })
            except Exception as e:
                results.append({"path": p, "error": str(e)[:200]})
        audio_dur = 0.0
        with _LOCK:
            audio_dur = float((STATE.get("manual") or {}).get("audio_duration") or 0)
        return {
            "videos": results,
            "paths": expanded,
            "total_video_duration": total_duration,
            "audio_duration": audio_dur,
            "trim_needed": total_duration > audio_dur > 0,
            "trim_seconds": max(0, total_duration - audio_dur) if audio_dur > 0 else 0,
        }, 200
    except Exception as e:
        return {"error": str(e)[:300]}, 500


def api_manual_mux(b: Dict) -> JsonResult:
    jid = int(b.get("id", 0) or 0)
    pr = get_project(jid)
    job = _find(jid)
    with _LOCK:
        current_audio = str((STATE.get("manual") or {}).get("audio_path") or "")
    audio_path = str(b.get("audio_path") or current_audio).strip().strip('"')
    if not pr or not job:
        return {"error": "Hãy chọn video cần ghép."}, 404
    if not audio_path or not os.path.isfile(audio_path):
        return {"error": "Hãy tạo hoặc chọn file âm thanh trước."}, 400
    if ffprobe_duration(audio_path) <= 0:
        return {"error": "Không đọc được file âm thanh."}, 400
    with _LOCK:
        if STATE["running"] or STATE["busy"]:
            return {"error": "Đang bận: " +
                    (STATE["busy"] or "đang xử lý")}, 409
        STATE["cancel"] = False
        STATE["running"] = True
        STATE["busy"] = "Đang ghép audio vào video…"
        manual = STATE["manual"]
        manual.update({"working": True, "status": "Đang xuất video…",
                       "error": "", "output_path": "",
                       "rev": int(manual.get("rev", 0)) + 1})
    _progress(pct=5, step="Ghép audio vào video", detail=os.path.basename(job["path"]))

    def _manual_mux_work(job_id=jid, selected_audio=os.path.abspath(audio_path)):
        try:
            project = get_project(job_id)
            selected_job = _find(job_id)
            if not project or not selected_job:
                raise RuntimeError("Video không còn trong hàng đợi.")
            span = _active_media_span(project)
            effective_duration = float(span["duration"])
            audio_duration = ffprobe_duration(selected_audio)
            if audio_duration > effective_duration + 0.1:
                _log(f"Audio dài hơn video {audio_duration-effective_duration:.1f}s; "
                     "phần vượt quá cuối video sẽ được cắt.", "warn")
            elif audio_duration < effective_duration - 0.1:
                _log(f"Audio ngắn hơn video {effective_duration-audio_duration:.1f}s; "
                     "phần cuối sẽ được chèn im lặng.", "info")

            stem, _ = _run_stem_for_project(project, span)
            out_dir = os.path.join(HERE, "output", stem)
            tmp_dir = os.path.join(out_dir, "_tmp", "manual_mux")
            os.makedirs(tmp_dir, exist_ok=True)
            local_rows = _project_rows_for_span(project, span)
            segs = _segments_from_rows(local_rows, use_vi=True)
            ass_path = None
            if project.get("options", {}).get("hardsub") and segs:
                ass_path = os.path.join(tmp_dir, f"{stem}.manual.ass")
                overlays.save_ass(
                    ass_path, segs, project["w"], project["h"],
                    project.get("sub_style"), use_placed=True)
            final = os.path.join(out_dir, f"{stem}.ghep_audio.mp4")
            work_pr = _render_project_for_span(project, span)
            _progress(pct=12, step="Ghép audio vào video",
                      detail="Đang áp dụng cắt/làm mờ/logo/phụ đề")
            if project.get("options", {}).get("render_chunked"):
                render_with_layers_chunked(
                    work_pr, selected_audio, final, ass_path,
                    segs, tmp_dir)
            else:
                render_with_layers(
                    work_pr, selected_audio, final, ass_path,
                    clip_duration=effective_duration if span.get("enabled") else None,
                    validate_full_source=not span.get("enabled"))
            with _LOCK:
                selected_job = _find(job_id)
                if selected_job:
                    selected_job.update({"status": "xong", "progress": 100,
                                         "output": final,
                                         "note": "Đã ghép audio vào video"})
                manual = STATE["manual"]
                manual.update({"working": False, "status": "Ghép video hoàn tất",
                               "output_path": final, "error": "",
                               "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Ghép audio hoàn tất",
                      detail=os.path.basename(final))
            _log(f"Đã ghép audio vào video: {final}", "ok")
        except InterruptedError:
            _mark_manual_cancelled("Đã dừng ghép audio vào video.")
        except Exception as e:
            _log(f"Ghép audio vào video lỗi: {e}", "err")
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"working": False, "status": "Ghép video lỗi",
                               "error": str(e)[:300],
                               "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Ghép video lỗi", detail=str(e)[:160])
        finally:
            with _LOCK:
                STATE["running"] = False
                STATE["busy"] = ""

    submit_job(_manual_mux_work, name="Ghép audio vào video", resource="ffmpeg",
               metadata={"kind": "manual_mux"})
    return {"ok": True, "async": True}, 200
