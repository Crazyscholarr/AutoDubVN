"""Gói ảnh kể chuyện: prompt, Gemini, tiếp tục cảnh thiếu."""
from __future__ import annotations

import os
import re
from typing import Dict, Tuple

from ..state import STATE, _LOCK, current_cancel_event, _log, _progress
from ..helpers import _doc_file_van_ban
from .common import (
    JsonResult, _api, _raise_if_cancelled, _mark_manual_cancelled,
    _story_title_key, _story_design_text,
)


def _load_cfg():
    return _api()._load_cfg()


def submit_job(*args, **kwargs):
    return _api().submit_job(*args, **kwargs)


def _story_image_inputs(payload: Dict) -> list:
    """Lấy ảnh trực tiếp hoặc khôi phục đúng thứ tự từ manifest đã lưu."""
    anh = payload.get("anh")
    if isinstance(anh, str):
        anh = [x for x in re.split(r"[\r\n]+", anh) if x.strip()]
    images = list(anh) if isinstance(anh, list) else []
    if images:
        return images
    pack = str(payload.get("image_pack") or "").strip().strip('"')
    if not pack:
        return []
    try:
        from ... import story_images
        return story_images.resolve_images(pack)
    except Exception as exc:
        _log("Không khôi phục được gói ảnh: %s" % exc, "warn")
        return []


def _generated_story_image_inputs(payload: Dict, title: str) -> Tuple[Dict, list, str]:
    """Không cho quy trình tự tạo ảnh mượn nhầm gói của truyện trước.

    Cùng tiêu đề thì giữ gói để tiếp tục các cảnh còn thiếu. Tiêu đề khác và
    đang bật tự tạo ảnh thì bỏ cả ``anh`` lẫn ``image_pack`` cũ, buộc Gemini
    lập prompt và tạo một bộ ảnh độc lập.
    """
    clean_payload = dict(payload)
    images = _story_image_inputs(clean_payload)
    if not bool(clean_payload.get("auto_images", True)):
        return clean_payload, images, ""
    pack_path = str(clean_payload.get("image_pack") or "").strip().strip('"')
    if not pack_path:
        return clean_payload, images, ""
    try:
        from ... import story_images
        manifest = story_images.load_pack(pack_path)
        pack_title = str(manifest.get("title") or "").strip()
    except Exception:
        manifest = {}
        pack_title = ""
    if pack_title and _story_title_key(pack_title) == _story_title_key(title):
        scene_count = len(manifest.get("scenes") or [])
        ready_count = int(manifest.get("ready_count", 0) or 0)
        if scene_count and ready_count < scene_count:
            # Giữ manifest để lượt sau sinh tiếp đúng những cảnh còn thiếu,
            # nhưng không đưa bộ ảnh dở dang sang bước dựng video.
            clean_payload["anh"] = []
            return clean_payload, [], ""
        return clean_payload, images, ""
    clean_payload["anh"] = []
    clean_payload["image_pack"] = ""
    return clean_payload, [], pack_title or "gói ảnh không xác định"


def _story_slideshow_images(payload: Dict, fallback: list,
                            total_duration: float) -> Tuple[list, bool]:
    """Lập lịch ảnh theo chương từ manifest; trả (ảnh, có_lặp_chủ_ý)."""
    pack = str(payload.get("image_pack") or "").strip().strip('"')
    script_path = str(payload.get("txt_path") or "").strip().strip('"')
    if not pack or not script_path:
        return list(fallback), False
    try:
        from ... import story_images
        weights = story_images.chapter_weights_from_script(script_path)
        planned = story_images.expand_for_chapters(
            pack, weights, total_duration=total_duration)
        if planned:
            _log("Gói ảnh: xếp %d cảnh theo %d chương, giữ đúng thứ tự manifest."
                 % (len(planned), len(weights) or 1), "ok")
            return planned, True
    except Exception as exc:
        _log("Không lập được lịch ảnh theo chương; dùng thứ tự ảnh thường: %s" % exc,
             "warn")
    return list(fallback), False


def api_story_image_pack(b: Dict) -> JsonResult:
    """Tạo/cập nhật gói prompt Gemini và ảnh đã tải về theo thứ tự cảnh."""
    try:
        from ... import kich_ban, story_images
        manifest_path = str(b.get("manifest_path") or b.get("image_pack") or "").strip()
        images = b.get("images", b.get("anh", []))
        if isinstance(images, str):
            images = [x for x in re.split(r"[\r\n]+", images) if x.strip()]
        if manifest_path:
            manifest = story_images.load_pack(manifest_path)
            expected_title = str(b.get("expected_title") or "").strip()
            pack_title = str(manifest.get("title") or "").strip()
            if (expected_title and
                    _story_title_key(expected_title) != _story_title_key(pack_title)):
                owner = pack_title or "gói ảnh không xác định"
                return {"error": ("Gói prompt này thuộc truyện '%s', không phải '%s'. "
                                  "Hãy tạo kịch bản cho tiêu đề mới trước."
                                  % (owner, expected_title))}, 409
            if isinstance(images, list) and (images or b.get("replace_images")):
                manifest = story_images.attach_images(manifest_path, images)
            return story_images.public_summary(
                manifest, include_prompt=bool(b.get("include_prompt", True))), 200

        design = str(b.get("design_text") or "").strip() or _story_design_text(b)
        design_path = str(b.get("character_context_path") or "").strip().strip('"')
        txt_path = str(b.get("txt_path") or "").strip().strip('"')
        if not design_path and txt_path:
            candidate = os.path.join(os.path.dirname(os.path.abspath(txt_path)),
                                     "00_ban_thiet_ke.txt")
            if os.path.isfile(candidate):
                design_path = candidate
        try:
            count = max(1, min(60, int(b.get("scene_count", 14) or 14)))
        except (TypeError, ValueError):
            count = 14
        aspect = "9:16" if str(b.get("aspect") or "") == "9:16" else "16:9"
        prompts = b.get("scene_prompts")
        if not isinstance(prompts, list):
            prompts = story_images.parse_scene_prompts(str(b.get("prompts_text") or ""))
        master = str(b.get("master_prompt") or "").strip()
        if not master:
            if design:
                master = kich_ban.prompt_anh(design, so_canh=count, kho=aspect)
            else:
                master = ("Hãy lập %d prompt ảnh %s theo đúng thứ tự cho truyện: %s. "
                          "Giữ nhân vật và phong cách nhất quán; không chữ, logo hay watermark."
                          % (count, aspect, str(b.get("title") or b.get("name") or "").strip()))
        manifest = story_images.create_pack(
            title=str(b.get("title") or b.get("name") or "Truyện").strip(),
            design_text=design, master_prompt=master, scene_prompts=prompts,
            image_paths=images if isinstance(images, list) else [],
            aspect=aspect, scene_count=count, script_path=txt_path,
            design_path=design_path)
        return story_images.public_summary(manifest, include_prompt=True), 200
    except Exception as exc:
        return {"error": "Không tạo/cập nhật được gói ảnh: %s" % exc}, 500


def api_story_image_pack_latest() -> JsonResult:
    try:
        from ... import story_images
        manifest = story_images.latest_pack()
        if not manifest:
            return {"ok": True, "manifest_path": "", "images": []}, 200
        return story_images.public_summary(manifest, include_prompt=False), 200
    except Exception as exc:
        return {"error": "Không đọc được gói ảnh gần nhất: %s" % exc}, 500


def _story_image_generation_config(cfg: Dict) -> Tuple[Dict, str, str, str]:
    """Tách cấu hình ảnh; browser tuyệt đối không mượn nhầm key dịch cũ."""
    from ... import story_images

    image_cfg = cfg.get("tao_anh") if isinstance(cfg.get("tao_anh"), dict) else {}
    provider = str(image_cfg.get("provider") or "browser").strip().lower()
    if provider not in {"api", "gemini"}:
        return image_cfg, "browser", "", str(
            image_cfg.get("gemini_image_model") or story_images.DEFAULT_IMAGE_MODEL)
    tr_cfg = cfg.get("translation") if isinstance(cfg.get("translation"), dict) else {}
    api_key = str(image_cfg.get("gemini_api_key") or
                  tr_cfg.get("gemini_api_key") or "").strip()
    model = str(image_cfg.get("gemini_image_model") or
                story_images.DEFAULT_IMAGE_MODEL)
    return image_cfg, "api", api_key, model


def _prepare_generated_story_images(result: Dict, payload: Dict,
                                    cfg: Dict) -> Tuple[list, str]:
    """Từ kịch bản vừa viết: rút prompt rồi tự sinh ảnh bằng API hoặc Gemini web."""
    from ... import kich_ban, story_images

    image_cfg, image_provider, api_key, model = _story_image_generation_config(cfg)
    manifest = None
    existing_pack = str(payload.get("image_pack") or "").strip().strip('"')
    if not existing_pack and result.get("script_path"):
        # STATE mất khi tắt app; manifest trên đĩa vẫn liên kết với kịch bản.
        script_key = os.path.normcase(os.path.abspath(result["script_path"]))
        matches = []
        for pack_file in story_images.DEFAULT_PACK_ROOT.glob("*/manifest.json"):
            try:
                saved_pack = story_images.load_pack(str(pack_file))
                saved_script = str(saved_pack.get("script_path") or "")
                if (saved_script and os.path.normcase(os.path.abspath(saved_script)) == script_key
                        and _story_title_key(saved_pack.get("title")) == _story_title_key(result.get("title"))
                        and any(str(x.get("prompt") or "").strip() for x in saved_pack.get("scenes", []))):
                    matches.append((int(saved_pack.get("ready_count") or 0), pack_file.stat().st_mtime, str(pack_file)))
            except (OSError, ValueError, TypeError):
                continue
        if matches:
            existing_pack = max(matches)[2]
    if existing_pack:
        try:
            candidate = story_images.load_pack(existing_pack)
            wanted_title = str(result.get("title") or payload.get("story_title") or "")
            same_title = (_story_title_key(candidate.get("title")) ==
                          _story_title_key(wanted_title))
            scenes = list(candidate.get("scenes") or [])
            if same_title and scenes and any(str(x.get("prompt") or "").strip()
                                             for x in scenes):
                manifest = candidate
                _log("Tiếp tục gói ảnh đang dở: %d/%d cảnh đã có; chỉ tạo phần còn thiếu."
                     % (int(candidate.get("ready_count", 0) or 0), len(scenes)), "ok")
        except Exception as exc:
            _log("Không tiếp tục được gói ảnh cũ; sẽ lập gói mới: %s" %
                 str(exc)[:160], "warn")

    if manifest is None:
        try:
            count = max(1, min(30, int(payload.get(
                "scene_count", image_cfg.get("scene_count", 14)) or 14)))
        except (TypeError, ValueError):
            count = 14
        aspect = "9:16" if str(payload.get("aspect") or "") == "9:16" else "16:9"
        design_path = str(result.get("design_path") or "")
        design = (_doc_file_van_ban(design_path)
                  if design_path and os.path.isfile(design_path) else "")
        master = kich_ban.prompt_anh(design, so_canh=count, kho=aspect)
        _progress(pct=36, step="Chuẩn bị hình ảnh",
                  detail="Rút prompt từng cảnh từ kịch bản")
        prompts = story_images.generate_scene_prompts(
            master, cfg, expected_count=count, logger=_log)
        manifest = story_images.create_pack(
            title=str(result.get("title") or payload.get("story_title") or "Truyện"),
            design_text=design, master_prompt=master, scene_prompts=prompts,
            aspect=aspect, scene_count=count,
            script_path=str(result.get("script_path") or ""),
            design_path=design_path)
    else:
        scenes = list(manifest.get("scenes") or [])
        count = len(scenes)
        aspect = str(manifest.get("aspect") or "16:9")
        prompts = [str(scene.get("prompt") or "") for scene in scenes]
    pack_path = str(manifest.get("manifest_path") or "")
    prompt_path = str(manifest.get("prompt_file") or "")
    ready_at_start = int(manifest.get("ready_count", 0) or 0)
    with _LOCK:
        manual = STATE["manual"]
        manual.update({
            "image_pack_path": pack_path,
            "image_prompt_path": prompt_path,
            "image_provider_url": story_images.GEMINI_WEB_URL,
            "image_scene_count": count,
            "image_ready_count": ready_at_start,
            "image_prompt_ready": False,
            "image_generation_status": (
                "Tiếp tục từ %d/%d ảnh…" % (ready_at_start, count)
                if ready_at_start else
                "Đã tạo prompt; chuẩn bị sinh ảnh…" if image_provider == "api"
                else "Đã tạo prompt; chuẩn bị tự tạo ảnh trên Gemini…"),
            "status": "Đã viết truyện; đang chuẩn bị hình ảnh…",
            "rev": int(manual.get("rev", 0)) + 1,
        })

    auto_images = bool(payload.get(
        "auto_images", image_cfg.get("auto_generate", True)))
    automation_error = ""

    def _image_progress(done, total, message=""):
        pct = 38 + 10 * float(done) / max(1, int(total))
        _progress(pct=pct, step="Tự tạo ảnh Gemini", detail=message)
        with _LOCK:
            manual = STATE["manual"]
            manual.update({
                "image_ready_count": int(done),
                "image_generation_status": message,
                "status": "Đang tự tạo ảnh %d/%d…" % (done, total),
                "rev": int(manual.get("rev", 0)) + 1,
            })

    if auto_images and image_provider == "api" and prompts and api_key:
        try:
            manifest = story_images.generate_images_gemini(
                pack_path, api_key, model=model,
                timeout=float(image_cfg.get("timeout_seconds", 180) or 180),
                max_retries=int(image_cfg.get("max_retries", 3) or 3),
                request_gap=float(image_cfg.get("request_gap_seconds", 1.5) or 0),
                logger=_log, progress=_image_progress,
                cancel_event=current_cancel_event())
        except Exception as exc:
            _log("Tự tạo ảnh Gemini chưa hoàn tất: %s" % str(exc)[:220], "warn")
            automation_error = str(exc)
            manifest = story_images.load_pack(pack_path)
    elif auto_images and image_provider == "browser" and prompts:
        settings = story_images.gemini_browser_settings(cfg)
        _log("Dùng Gemini Pro đã đăng nhập; app sẽ tự gửi từng prompt và tải "
             "ảnh, không dùng API key.", "info")
        try:
            manifest = story_images.generate_images_gemini_browser(
                pack_path, profile_dir=settings["profile_dir"],
                channel=settings["channel"], url=settings["url"],
                timeout=settings["timeout"], max_retries=settings["retries"],
                request_gap=settings["request_gap"],
                max_session_restarts=settings["session_restarts"],
                fresh_chat_every=settings["fresh_chat_every"],
                restart_cooldown=settings["restart_cooldown"], logger=_log,
                progress=_image_progress, cancel_event=current_cancel_event())
        except Exception as exc:
            automation_error = str(exc)
            _log("Tự tạo ảnh qua Gemini web chưa hoàn tất: %s" %
                 automation_error[:220], "warn")
            manifest = story_images.load_pack(pack_path)
    elif auto_images and not prompts:
        automation_error = "Chưa rút được danh sách prompt từng cảnh."
        _log("Chưa có danh sách prompt từng cảnh; giữ prompt tổng để xử lý lại.",
             "warn")
    elif auto_images and not api_key:
        automation_error = "Chưa có Gemini API key cho ảnh."
        _log("Chưa có Gemini API key cho ảnh; giữ gói prompt để xử lý lại.",
             "warn")

    images = story_images.resolve_images(pack_path)
    complete = bool(images) and len(images) >= count
    with _LOCK:
        manual = STATE["manual"]
        manual.update({
            "image_ready_count": len(images),
            "image_prompt_ready": not complete,
            "image_generation_status": (
                "Đã tạo đủ %d ảnh" % len(images) if complete else
                "Tự động Gemini lỗi: %s" % automation_error[:180]
                if automation_error else
                "Đang chờ ảnh: đã có %d/%d" % (len(images), count)),
            "rev": int(manual.get("rev", 0)) + 1,
        })
    return images if complete else [], pack_path


def api_story_resume_images(b: Dict) -> JsonResult:
    """Tiếp tục đúng các cảnh thiếu trong manifest rồi bàn giao thẳng sang video."""
    pack_path = str(b.get("image_pack") or "").strip().strip('"')
    if not pack_path:
        return {"error": "Chưa có gói ảnh để tiếp tục."}, 400
    try:
        from ... import story_images
        manifest = story_images.load_pack(pack_path)
    except Exception as exc:
        return {"error": "Không đọc được gói ảnh: %s" % exc}, 400
    scenes = list(manifest.get("scenes") or [])
    if not scenes:
        return {"error": "Gói ảnh không có danh sách cảnh."}, 400
    requested_title = str(b.get("story_title") or "").strip()
    pack_title = str(manifest.get("title") or "").strip()
    if (requested_title and pack_title and
            _story_title_key(requested_title) != _story_title_key(pack_title)):
        return {"error": ("Không thể tiếp tục ảnh của truyện '%s' cho tiêu đề mới '%s'."
                          % (pack_title, requested_title))}, 409
    script_path = str(b.get("txt_path") or manifest.get("script_path") or "")
    if not script_path or not os.path.isfile(script_path):
        return {"error": "Không thấy KICH_BAN_DOC.txt của gói ảnh."}, 400

    with _LOCK:
        if STATE["running"] or STATE["busy"]:
            return {"error": "Đang bận: " +
                    (STATE["busy"] or "đang xử lý")}, 409
        STATE["cancel"] = False
        STATE["running"] = True
        STATE["busy"] = "Đang tiếp tục tạo các ảnh còn thiếu…"
        manual = STATE["manual"]
        manual.update({
            "working": True,
            "status": "Tiếp tục tạo ảnh từ cảnh còn thiếu…",
            "error": "", "output_path": "",
            "script_path": os.path.abspath(script_path),
            "script_title": str(manifest.get("title") or b.get("name") or ""),
            "image_pack_path": str(manifest.get("manifest_path") or pack_path),
            "image_scene_count": len(scenes),
            "image_ready_count": int(manifest.get("ready_count", 0) or 0),
            "image_prompt_ready": False,
            "rev": int(manual.get("rev", 0)) + 1,
        })
    _progress(pct=36, step="Tiếp tục tạo ảnh",
              detail="Giữ ảnh đã có, bắt đầu từ cảnh còn thiếu")

    def _work(payload=dict(b), current=manifest,
              source_script=os.path.abspath(script_path)):
        handed_off = False
        try:
            cfg = _load_cfg()
            payload["auto_images"] = True
            payload["image_pack"] = str(current.get("manifest_path") or pack_path)
            payload["anh"] = []
            result = {
                "title": str(current.get("title") or payload.get("name") or "Truyện"),
                "script_path": source_script,
                "design_path": str(current.get("design_path") or ""),
            }
            images, resumed_pack = _api()._prepare_generated_story_images(
                result, payload, cfg)
            _raise_if_cancelled()
            if not images:
                with _LOCK:
                    manual = STATE["manual"]
                    ready = int(manual.get("image_ready_count", 0) or 0)
                    total = int(manual.get("image_scene_count", 0) or 0)
                    manual.update({
                        "working": False,
                        "status": "Tạo ảnh tạm dừng ở %d/%d; bấm Tiếp tục để thử lại"
                                  % (ready, total),
                        "error": "",
                        "rev": int(manual.get("rev", 0)) + 1,
                    })
                _progress(pct=100, step="Tạo ảnh tạm dừng",
                          detail="Ảnh đã tạo được giữ nguyên")
                return

            with _LOCK:
                STATE["running"] = False
                STATE["busy"] = ""
            video_payload = dict(payload)
            video_payload.pop("text", None)
            video_payload["txt_path"] = source_script
            video_payload["name"] = str(payload.get("name") or result["title"])
            video_payload["anh"] = images
            video_payload["image_pack"] = resumed_pack
            video_payload["character_context_path"] = result.get("design_path") or ""
            video_payload["_progress_start"] = 48
            video_payload["_handoff"] = True
            response, code = _api().api_manual_run_all(video_payload)
            if code != 200:
                raise RuntimeError(response.get("error") or
                                   "Không khởi động được bước dựng video.")
            handed_off = True
            _log("Đã tạo đủ ảnh còn thiếu; tiếp tục giọng đọc và dựng video.", "ok")
        except InterruptedError:
            _mark_manual_cancelled("Ảnh đã tạo xong vẫn được giữ để tiếp tục sau.")
        except Exception as exc:
            _log("Tiếp tục tạo ảnh lỗi: %s" % exc, "err")
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"working": False, "status": "Tiếp tục tạo ảnh lỗi",
                               "error": str(exc)[:300],
                               "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Tiếp tục tạo ảnh lỗi",
                      detail=str(exc)[:160])
        finally:
            if not handed_off:
                with _LOCK:
                    STATE["running"] = False
                    STATE["busy"] = ""

    submit_job(_work, name="Tiếp tục tạo ảnh", resource="ai",
               metadata={"kind": "story_resume_images"})
    return {"ok": True, "async": True,
            "ready_count": int(manifest.get("ready_count", 0) or 0),
            "scene_count": len(scenes)}, 200
