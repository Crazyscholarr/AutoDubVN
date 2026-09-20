"""Nghe thử giọng, TTS, nhạc nền và dàn giọng kể chuyện."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Dict, Optional

from ...utils import ffprobe_duration
from ..state import HERE, STATE, _LOCK, current_cancel_event, _log, _progress
from ..helpers import _safe_path_stem
from .common import (
    JsonResult, DangBan, _api, _lay_van_ban_tu_body, _ensure_story_ctas,
    _cta_tts_options, _mark_manual_cancelled, _story_design_text,
)

# --------------------------------------------------------------------------- #
#  NGHE THỬ GIỌNG
#
#  Một truyện 12.000 từ đọc mất vài phút mới ra file, nên trước đây muốn biết
#  giọng nào hợp thì phải tạo cả file rồi nghe, không hợp lại tạo lại. Hàm dưới
#  đọc đúng một câu bằng chính giọng/tốc độ đang chọn để so giọng trong vài giây.
# --------------------------------------------------------------------------- #
# Câu mẫu chọn theo ngách kể chuyện: có đủ dấu thanh, tên gọi Nam Bộ và vật thể
# đời thường, nên nghe là biết ngay giọng có mộc mạc hay bị đọc như đọc báo.
CAU_NGHE_THU = (
    "Đêm đó xóm Cồn Gió có gió thật. Bà Bảy ngồi ở bậc cửa, tay còn cầm chai "
    "dầu gió xanh, nghe tiếng ghe ngoài sông mà hổng nói câu nào."
)
NGHE_THU_MAX_CHARS = 320
_NGHE_THU_LOCK = threading.Lock()


def _load_cfg():
    return _api()._load_cfg()


def submit_job(*args, **kwargs):
    return _api().submit_job(*args, **kwargs)


def _story_voice_plan(source_text: str, payload: Dict, cfg: Dict, engine: str,
                      voice: str, pitch: str) -> Optional[Dict]:
    """Lập dàn giọng khi bật tự chọn giọng hoặc đa giọng nhân vật."""
    auto_narrator = bool(payload.get("voice_auto", False))
    multi_voice = bool(payload.get("multi_voice", False))
    if not auto_narrator and not multi_voice:
        return None
    from ... import story_voice, tts as tts_mod
    catalog = tts_mod.list_voices(engine)
    if not catalog:
        _log("Không có catalog giọng để lập dàn nhân vật; dùng giọng đã chọn.", "warn")
        return None
    narrator_voice = voice
    if engine == "edge":
        normalized = tts_mod.normalize_edge_narrator({"voice": voice, "pitch": pitch})
        narrator_voice = "%s|%s" % (normalized["voice"], normalized["pitch"])
    plan = story_voice.plan_story_voices(
        source_text, catalog, engine=engine, narrator_voice=narrator_voice,
        design_text=_story_design_text(payload), auto_narrator=auto_narrator,
        max_characters=max(1, min(12, int(payload.get("max_character_voices", 8) or 8))))
    if not multi_voice:
        plan["cast"] = []
        plan["utterances"] = None
    return plan


def _save_voice_cast(path: str, plan: Optional[Dict]) -> str:
    if not plan:
        return ""
    payload = {k: v for k, v in plan.items() if k not in {"utterances", "characters"}}
    payload["characters"] = [
        {k: v for k, v in item.items() if k != "aliases"}
        for item in (plan.get("characters") or [])
    ]
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return os.path.abspath(path)
    except OSError as exc:
        _log("Không lưu được sơ đồ dàn giọng: %s" % exc, "warn")
        return ""


def _cat_cau_nghe_thu(text: str, max_chars: int = NGHE_THU_MAX_CHARS) -> str:
    """Lấy một đoạn ngắn, cắt ở dấu kết câu để nghe không bị cụt giữa câu."""
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return CAU_NGHE_THU
    if len(raw) <= max_chars:
        return raw
    cat = raw[:max_chars]
    for dau in (". ", "! ", "? ", "; ", ", "):
        vi_tri = cat.rfind(dau)
        if vi_tri >= max_chars // 3:
            return cat[:vi_tri + 1].strip()
    return cat.rsplit(" ", 1)[0].strip() or cat


def tao_ban_nghe_thu(engine: str = "", voice: str = "", pitch: str = "",
                     rate: str = "", text: str = "") -> Dict:
    """Đọc thử một câu ngắn bằng giọng đang chọn. Trả về {path, text, ...}.

    Kết quả được nhớ theo (engine, giọng, cao độ, tốc độ, câu) nên bấm lại đúng
    cấu hình cũ là phát ngay, không gọi TTS lần nữa.
    """
    import hashlib

    from ... import tts as tts_mod

    with _LOCK:
        dang_chay = bool(STATE.get("running")) or bool(STATE.get("busy"))
        ghi_chu = str(STATE.get("busy") or "đang xử lý")
    if dang_chay:
        raise DangBan(f"Đang bận: {ghi_chu}. Nghe thử được ngay sau khi xong.")

    cfg = _load_cfg()
    tc = cfg.get("tts", {}) or {}
    eng = str(engine or tc.get("engine") or "edge").strip().lower()
    if eng not in {"edge", "vieneu", "capcut"}:
        raise ValueError(f"Engine TTS không hỗ trợ: {eng}")
    mac_dinh = (tc.get("vieneu_voice") if eng == "vieneu"
                else tc.get("capcut_voice") if eng == "capcut"
                else tc.get("narrator_voice"))
    giong = str(voice or mac_dinh or "vi-VN-NamMinhNeural")
    cao_do = str(pitch or tc.get("narrator_pitch") or "+0Hz")
    toc_do = str(rate or tc.get("base_rate") or "+0%")
    narrator = {"voice": giong, "pitch": cao_do}
    if eng == "edge":
        # Danh sách giọng edge trả id kiểu "vi-VN-NamMinhNeural|+8Hz" (giọng +
        # cao độ), phải tách ra trước khi dựng SSML.
        narrator = tts_mod.normalize_edge_narrator(narrator)
    cau = _cat_cau_nghe_thu(text)

    khoa = hashlib.sha1(
        "|".join([eng, narrator["voice"], str(narrator.get("pitch") or ""),
                  toc_do, cau]).encode("utf-8")).hexdigest()[:16]
    out_dir = os.path.join(HERE, "output", "_nghe_thu")
    out_path = os.path.join(out_dir, f"{eng}_{khoa}.mp3")
    ket_qua = {"path": out_path, "text": cau, "engine": eng,
               "voice": narrator["voice"], "rate": toc_do, "cached": True}
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 1024:
        if eng == "capcut":
            tts_mod._record_capcut_voice_status(narrator["voice"], True)
        return ket_qua

    with _NGHE_THU_LOCK:          # hai lần bấm liên tiếp không chồng lên nhau
        if os.path.isfile(out_path) and os.path.getsize(out_path) > 1024:
            if eng == "capcut":
                tts_mod._record_capcut_voice_status(narrator["voice"], True)
            return ket_qua
        _log(f"Nghe thử giọng: engine={eng}, voice={narrator['voice']}, "
             f"rate={toc_do}", "step")
        tts_mod.synthesize_text_audio(
            cau, os.path.join(out_dir, "_tmp", khoa), out_path,
            engine=eng, narrator=narrator, base_rate=toc_do,
            concurrency=2, max_retries=int(tc.get("max_retries", 3) or 3),
            retry_base_delay=float(tc.get("retry_delay", 1.2) or 1.2),
            vieneu_options=tc.get("vieneu_options"),
            capcut_options=tc.get("capcut_options"),
            max_chunk_chars=NGHE_THU_MAX_CHARS)
    ket_qua["cached"] = False
    return ket_qua


def api_manual_use_audio(b: Dict) -> JsonResult:
    path = str(b.get("path") or "").strip().strip('"')
    if not path or not os.path.isfile(path):
        return {"error": f"Không thấy file âm thanh: {path}"}, 400
    if os.path.splitext(path)[1].lower() not in {
            ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}:
        return {"error": "Định dạng âm thanh chưa hỗ trợ."}, 400
    duration = ffprobe_duration(path)
    if duration <= 0:
        return {"error": "Không đọc được file âm thanh."}, 400
    with _LOCK:
        manual = STATE["manual"]
        manual.update({"audio_path": os.path.abspath(path),
                       "audio_duration": duration,
                       "output_path": "", "error": "",
                       "working": False,
                       "status": "Đã chọn audio có sẵn",
                       "rev": int(manual.get("rev", 0)) + 1})
    return {"ok": True, "path": os.path.abspath(path),
            "duration": duration}, 200


def api_manual_tts(b: Dict) -> JsonResult:
    text, err = _lay_van_ban_tu_body(b)
    if err:
        return err
    with _LOCK:
        if STATE["running"] or STATE["busy"]:
            return {"error": "Đang bận: " +
                    (STATE["busy"] or "đang xử lý")}, 409
        STATE["cancel"] = False
        STATE["running"] = True
        STATE["busy"] = "Đang tạo audio từ văn bản…"
        manual = STATE["manual"]
        manual.update({"working": True, "status": "Đang tổng hợp giọng…",
                       "error": "", "output_path": "",
                       "voice_cast": [], "voice_assignment_coverage": 0.0,
                       "voice_cast_path": "",
                       "rev": int(manual.get("rev", 0)) + 1})
    _progress(pct=5, step="Tạo audio", detail="Đang chuẩn bị văn bản")

    def _manual_tts_work(payload=dict(b), source_text=text):
        try:
            from ... import tts as tts_mod
            from ... import srt_utils
            from .render import _segments_tu_timeline
            source_text = _ensure_story_ctas(source_text, payload)
            cfg = _load_cfg()
            tc = cfg.get("tts", {}) or {}
            engine = str(payload.get("engine") or tc.get("engine") or "edge").lower()
            default_voice = (tc.get("vieneu_voice") if engine == "vieneu"
                             else tc.get("capcut_voice") if engine == "capcut"
                             else tc.get("narrator_voice"))
            voice = str(payload.get("voice") or default_voice or
                        "vi-VN-NamMinhNeural")
            pitch = str(payload.get("pitch") or tc.get("narrator_pitch") or "+0Hz")
            rate = str(payload.get("rate") or tc.get("base_rate") or "+0%")
            title = _safe_path_stem(
                payload.get("name") or source_text[:50],
                fallback="audio_ke_chuyen", limit=70)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            out_dir = os.path.join(HERE, "output", "manual_audio")
            workdir = os.path.join(out_dir, "_tmp", f"{title}_{stamp}")
            out_path = os.path.join(out_dir, f"{title}_{stamp}.mp3")
            os.makedirs(workdir, exist_ok=True)
            voice_plan = _story_voice_plan(
                source_text, payload, cfg, engine, voice, pitch)
            if voice_plan:
                voice = str((voice_plan.get("narrator") or {}).get("id") or voice)
                if voice_plan.get("cast"):
                    _log("Dàn giọng: người kể + %d nhân vật; nhận diện chắc %.1f%% lượt thoại."
                         % (len(voice_plan["cast"]),
                            float(voice_plan.get("assignment_coverage") or 0)), "ok")
            _log(f"Tạo audio thủ công: engine={engine}, voice={voice}, rate={rate}", "step")
            _progress(pct=15, step="Tạo audio", detail=f"Đang đọc bằng {engine}")
            result = tts_mod.synthesize_text_audio(
                source_text, workdir, out_path,
                engine=engine,
                narrator={"voice": voice, "pitch": pitch},
                base_rate=rate,
                concurrency=int(tc.get("concurrency", 8) or 8),
                max_retries=int(tc.get("max_retries", 3) or 3),
                retry_base_delay=float(tc.get("retry_delay", 1.2) or 1.2),
                vieneu_options=tc.get("vieneu_options"),
                capcut_options=tc.get("capcut_options"),
                max_chunk_chars=240,   # đoạn ngắn -> mốc phụ đề mịn hơn
                utterances=(voice_plan or {}).get("utterances"),
                **_cta_tts_options(payload),
                cancel_event=current_cancel_event(),
            )
            cast_path = _save_voice_cast(os.path.splitext(out_path)[0] + ".giong.json",
                                         voice_plan)
            # Lưu phụ đề khớp giọng đọc để bước dựng video ghi cứng lên hình.
            srt_path = os.path.splitext(out_path)[0] + ".srt"
            try:
                srt_utils.save_srt_file(
                    srt_path, _segments_tu_timeline(result.get("segments")))
            except Exception as e:
                srt_path = ""
                _log(f"Không lưu được phụ đề giọng đọc: {e}", "warn")
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"working": False, "status": "Tạo audio hoàn tất",
                               "audio_path": result["path"],
                               "audio_duration": result["duration"],
                               "srt_path": srt_path,
                               "voice_cast": (voice_plan or {}).get("cast") or [],
                               "voice_assignment_coverage": float(
                                   (voice_plan or {}).get("assignment_coverage") or 0),
                               "voice_cast_path": cast_path,
                               "error": "",
                               "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Tạo audio",
                      detail=os.path.basename(result["path"]))
        except InterruptedError:
            _mark_manual_cancelled("Đã giữ lại các đoạn giọng tạo xong.")
        except Exception as e:
            _log(f"Tạo audio từ văn bản lỗi: {e}", "err")
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"working": False, "status": "Tạo audio lỗi",
                               "error": str(e)[:300],
                               "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Tạo audio lỗi", detail=str(e)[:160])
        finally:
            with _LOCK:
                STATE["running"] = False
                STATE["busy"] = ""

    submit_job(_manual_tts_work, name="Tạo audio từ văn bản", resource="ai",
               metadata={"kind": "manual_tts"})
    return {"ok": True, "async": True}, 200


def api_nhac_nen_tai(b: Dict) -> JsonResult:
    so_bai = max(1, min(20, int(b.get("so_bai") or 3)))
    cats = b.get("danh_muc") if isinstance(b.get("danh_muc"), list) else None
    with _LOCK:
        if STATE["busy"]:
            return {"error": "Đang bận: " + STATE["busy"]}, 409
        STATE["busy"] = "Đang tải nhạc nền…"

    def _tai_nhac_work():
        try:
            from ... import nhac_nen as nn
            _progress(pct=10, step="Tải nhạc nền", detail="Đang tìm bài phù hợp")
            có = nn.dam_bao_co_nhac(so_bai, categories=cats)
            _log(f"Kho nhạc nền hiện có {len(có)} bài.", "ok")
            _progress(pct=100, step="Tải nhạc nền xong",
                      detail=f"{len(có)} bài trong máy")
        except Exception as e:
            _log(f"Tải nhạc nền lỗi: {e}", "err")
            _progress(pct=100, step="Tải nhạc nền lỗi", detail=str(e)[:160])
        finally:
            with _LOCK:
                STATE["busy"] = ""

    submit_job(_tai_nhac_work, name="Tải nhạc nền", resource="network",
               metadata={"kind": "music_download"})
    return {"ok": True, "async": True}, 200


def api_manual_nhac_nen(b: Dict) -> JsonResult:
    with _LOCK:
        current = str((STATE.get("manual") or {}).get("audio_path") or "")
    audio_path = str(b.get("audio_path") or current).strip().strip('"')
    if not audio_path or not os.path.isfile(audio_path):
        return {"error": "Hãy tạo hoặc chọn giọng đọc trước."}, 400
    with _LOCK:
        if STATE["running"] or STATE["busy"]:
            return {"error": "Đang bận: " +
                    (STATE["busy"] or "đang xử lý")}, 409
        STATE["cancel"] = False
        STATE["running"] = True
        STATE["busy"] = "Đang trộn nhạc nền…"
        manual = STATE["manual"]
        manual.update({"working": True, "status": "Đang trộn nhạc nền…",
                       "error": "",
                       "rev": int(manual.get("rev", 0)) + 1})

    def _nhac_work(payload=dict(b), voice=os.path.abspath(audio_path)):
        try:
            from ... import nhac_nen as nn
            cfg = _load_cfg()
            nc = cfg.get("nhac_nen", {}) or {}
            muc_db = float(payload.get("muc_db", nc.get("muc_db", -38)))
            duck = bool(payload.get("duck", nc.get("duck", True)))
            ratio = float(payload.get("duck_ratio", nc.get("duck_ratio", 8)))
            fade = float(payload.get("fade", nc.get("fade", 2.0)))
            bai = str(payload.get("bai") or "")
            if not bai and nc.get("tu_dong_tai", True):
                cats = nc.get("danh_muc")
                nn.dam_bao_co_nhac(
                    int(nc.get("so_bai_tai", 3) or 3),
                    categories=cats if isinstance(cats, list) else None)
            stem = os.path.splitext(os.path.basename(voice))[0]
            out_path = os.path.join(os.path.dirname(voice),
                                    f"{stem}_co_nhac.m4a")
            _progress(pct=20, step="Trộn nhạc nền", detail="Đang cân mức âm")
            result = nn.tron_nhac_nen(voice, out_path, music_path=bai,
                                      muc_db=muc_db, duck=duck,
                                      duck_ratio=ratio, fade=fade)
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"working": False,
                               "status": "Đã trộn nhạc nền",
                               "audio_path": result["path"],
                               "audio_duration": result["duration"],
                               "nhac_nen": os.path.basename(result.get("music") or ""),
                               "error": "",
                               "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Trộn nhạc nền xong",
                      detail=os.path.basename(result["path"]))
        except InterruptedError:
            _mark_manual_cancelled()
        except Exception as e:
            _log(f"Trộn nhạc nền lỗi: {e}", "err")
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"working": False, "status": "Trộn nhạc nền lỗi",
                               "error": str(e)[:300],
                               "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Trộn nhạc nền lỗi", detail=str(e)[:160])
        finally:
            with _LOCK:
                STATE["running"] = False
                STATE["busy"] = ""

    submit_job(_nhac_work, name="Trộn nhạc nền", resource="ffmpeg",
               metadata={"kind": "music_mix"})
    return {"ok": True, "async": True}, 200


def api_story_voice_recommendations(b: Dict) -> JsonResult:
    """Phân tích nội dung, nhân vật và đề xuất cả dàn giọng từ catalog đang có."""
    text, err = _lay_van_ban_tu_body(b)
    if err:
        return err
    engine = str(b.get("engine") or "capcut").strip().lower()
    if engine not in {"edge", "vieneu", "capcut"}:
        return {"error": "Bộ giọng không hỗ trợ: %s" % engine}, 400
    try:
        from ... import story_voice, tts as tts_mod
        voices = tts_mod.list_voices(engine)
        if not voices:
            return {"error": "Không lấy được danh sách giọng %s." % engine}, 503
        result = story_voice.plan_story_voices(
            text, voices, engine=engine,
            narrator_voice=str(b.get("voice") or ""),
            design_text=_story_design_text(b), auto_narrator=True,
            max_characters=max(1, min(12, int(b.get("max_character_voices", 8) or 8))))
        result.pop("utterances", None)  # không trả hàng nghìn lượt thoại qua HTTP
        for character in result.get("characters") or []:
            character.pop("aliases", None)
        result["engine"] = engine
        result["catalog_count"] = len(voices)
        return result, 200
    except Exception as exc:
        return {"error": "Không phân tích được giọng phù hợp: %s" % exc}, 500
