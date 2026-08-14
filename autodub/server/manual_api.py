"""Các endpoint của luồng "kể chuyện" (tab Tạo audio trên giao diện).

Mỗi hàm nhận body JSON đã parse và trả về (obj, http_code) để Handler gọi
`self._json(*api_xxx(b))`. Việc nặng đều chạy trong thread nền, kết quả cập
nhật vào STATE["manual"] cho giao diện poll.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Dict, Optional, Tuple

from .. import overlays, srt_utils
from ..srt_utils import Segment
from ..utils import ffprobe_duration
from .state import HERE, STATE, _LOCK, _CANCEL_EVENT, _log, _progress, _find
from .helpers import _safe_path_stem, _doc_file_van_ban
from .config_api import _load_cfg
from .projects import (get_project, _active_media_span, _run_stem_for_project,
                       _project_rows_for_span, _segments_from_rows,
                       _render_project_for_span)
from .render import render_with_layers, render_with_layers_chunked

JsonResult = Tuple[Dict, int]


def _lay_van_ban_tu_body(b: Dict) -> Tuple[str, Optional[JsonResult]]:
    """Lấy nội dung truyện từ body (text trực tiếp hoặc đường dẫn file txt)."""
    text = str(b.get("text") or "").strip()
    txt_path = str(b.get("txt_path") or "").strip().strip('"')
    if not text and txt_path:
        if not os.path.isfile(txt_path):
            return "", ({"error": f"Không thấy file: {txt_path}"}, 400)
        try:
            text = _doc_file_van_ban(txt_path)
        except Exception as e:
            return "", ({"error": f"Không đọc được file: {e}"}, 400)
        if not text.strip():
            return "", ({"error": "File văn bản trống."}, 400)
        if not b.get("name"):
            b["name"] = os.path.splitext(os.path.basename(txt_path))[0]
    if not text:
        return "", ({"error": "Hãy nhập văn bản cần đọc."}, 400)
    return text, None


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


class DangBan(RuntimeError):
    """Máy đang chạy việc dài - không chen bản nghe thử vào giữa."""


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

    from .. import tts as tts_mod

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
        STATE["running"] = True
        STATE["busy"] = "Đang tạo audio từ văn bản…"
        manual = STATE["manual"]
        manual.update({"working": True, "status": "Đang tổng hợp giọng…",
                       "error": "", "output_path": "",
                       "rev": int(manual.get("rev", 0)) + 1})
    _progress(pct=5, step="Tạo audio", detail="Đang chuẩn bị văn bản")

    def _manual_tts_work(payload=dict(b), source_text=text):
        try:
            from .. import tts as tts_mod
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
            )
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
                               "error": "",
                               "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Tạo audio",
                      detail=os.path.basename(result["path"]))
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

    threading.Thread(target=_manual_tts_work, daemon=True).start()
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
            from .. import nhac_nen as nn
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

    threading.Thread(target=_tai_nhac_work, daemon=True).start()
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
        STATE["running"] = True
        STATE["busy"] = "Đang trộn nhạc nền…"
        manual = STATE["manual"]
        manual.update({"working": True, "status": "Đang trộn nhạc nền…",
                       "error": "",
                       "rev": int(manual.get("rev", 0)) + 1})

    def _nhac_work(payload=dict(b), voice=os.path.abspath(audio_path)):
        try:
            from .. import nhac_nen as nn
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

    threading.Thread(target=_nhac_work, daemon=True).start()
    return {"ok": True, "async": True}, 200


def api_manual_slideshow(b: Dict) -> JsonResult:
    with _LOCK:
        current = str((STATE.get("manual") or {}).get("audio_path") or "")
    audio_path = str(b.get("audio_path") or current).strip().strip('"')
    anh = b.get("anh")
    if isinstance(anh, str):
        anh = [x for x in re.split(r"[\r\n]+", anh) if x.strip()]
    if not isinstance(anh, list) or not anh:
        return {"error": "Hãy chọn ảnh hoặc thư mục ảnh."}, 400
    if not audio_path or not os.path.isfile(audio_path):
        return {"error": "Hãy tạo giọng đọc trước khi dựng video."}, 400
    with _LOCK:
        if STATE["running"] or STATE["busy"]:
            return {"error": "Đang bận: " +
                    (STATE["busy"] or "đang xử lý")}, 409
        STATE["running"] = True
        STATE["busy"] = "Đang dựng video từ ảnh…"
        manual = STATE["manual"]
        manual.update({"working": True, "status": "Đang dựng video từ ảnh…",
                       "error": "", "output_path": "",
                       "rev": int(manual.get("rev", 0)) + 1})

    def _slideshow_work(payload=dict(b), imgs=list(anh),
                        voice=os.path.abspath(audio_path)):
        try:
            from .. import slideshow as ss
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
            out_dir = os.path.join(HERE, "output", "manual_video")
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
            result = ss.tao_video_tu_anh(
                imgs, voice, out_path, workdir=workdir,
                w=w, h=h, fps=fps, kieu=kieu, ass_path=ass_path,
                progress=lambda pct, detail: _progress(
                    pct=pct, step="Dựng video từ ảnh", detail=detail))
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"working": False,
                               "status": "Dựng video hoàn tất",
                               "output_path": result["path"], "error": "",
                               "rev": int(manual.get("rev", 0)) + 1})
            _progress(pct=100, step="Dựng video xong",
                      detail=os.path.basename(result["path"]))
            _log(f"Đã dựng video từ {result['so_anh']} ảnh: {result['path']}", "ok")
        except Exception as e:
            _log(f"Dựng video từ ảnh lỗi: {e}", "err")
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

    threading.Thread(target=_slideshow_work, daemon=True).start()
    return {"ok": True, "async": True}, 200


def api_story_voice_recommendations(b: Dict) -> JsonResult:
    """Phân tích nội dung tại máy và đề xuất giọng chỉ từ catalog đang có."""
    text, err = _lay_van_ban_tu_body(b)
    if err:
        return err
    engine = str(b.get("engine") or "capcut").strip().lower()
    if engine not in {"edge", "vieneu", "capcut"}:
        return {"error": "Bộ giọng không hỗ trợ: %s" % engine}, 400
    try:
        from .. import story_voice, tts as tts_mod
        voices = tts_mod.list_voices(engine)
        if not voices:
            return {"error": "Không lấy được danh sách giọng %s." % engine}, 503
        result = story_voice.recommend_voices(text, voices, engine=engine, limit=5)
        result["engine"] = engine
        result["catalog_count"] = len(voices)
        return result, 200
    except Exception as exc:
        return {"error": "Không phân tích được giọng phù hợp: %s" % exc}, 500


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
    anh = b.get("anh")
    if isinstance(anh, str):
        anh = [x for x in re.split(r"[\r\n]+", anh) if x.strip()]
    if not isinstance(anh, list) or not anh:
        return {"error": "Hãy thêm ảnh trước khi chạy trọn quy trình."}, 400
    with _LOCK:
        if STATE["running"] or STATE["busy"]:
            return {"error": "Đang bận: " + (STATE["busy"] or "đang xử lý")}, 409
        _CANCEL_EVENT.clear()
        STATE["running"] = True
        STATE["busy"] = "Đang tạo kịch bản từ tiêu đề…"
        manual = STATE["manual"]
        manual.update({
            "working": True, "status": "Đang lập và viết kịch bản…",
            "error": "", "output_path": "", "script_path": "",
            "script_title": title, "script_words": 0,
            "recommended_voice": "", "voice_analysis": {},
            "voice_recommendations": [],
            "rev": int(manual.get("rev", 0)) + 1,
        })
    _progress(pct=2, step="Tạo kịch bản", detail="Mở công cụ viết truyện")

    def _work(payload=dict(b), imgs=list(anh), source_title=title):
        handed_off = False
        try:
            from .. import story_writer
            cfg = _load_cfg()

            def _writer_progress(done, total, message=""):
                ratio = float(done) / max(1, int(total))
                _progress(pct=3 + ratio * 32, step="Tạo kịch bản",
                          detail=message[:160] or ("Đã xong %d/%d bước" % (done, total)))

            result = story_writer.generate(
                source_title, cfg, log=_log, progress=_writer_progress,
                cancel_event=_CANCEL_EVENT)
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
                if not measured.get("within_target", False):
                    quality_issues.append("tổng số từ ngoài mục tiêu")
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
            auto_voice_id = ""
            voice_result = None
            if bool(payload.get("voice_auto", False)):
                from .. import story_voice, tts as tts_mod
                script_text = _doc_file_van_ban(result["script_path"])
                engine = str(payload.get("engine") or
                             (cfg.get("tts") or {}).get("engine") or "capcut").lower()
                catalog = tts_mod.list_voices(engine)
                if catalog:
                    voice_result = story_voice.recommend_voices(
                        script_text, catalog, engine=engine, limit=5)
                    recs = voice_result.get("recommendations") or []
                    if recs:
                        auto_voice_id = str(recs[0]["id"])
                        _log("Phân tích truyện đề xuất giọng %s: %s"
                             % (recs[0]["name"], "; ".join(recs[0]["reasons"])), "ok")
                else:
                    _log("Không có catalog giọng để tự đề xuất; giữ giọng đã chọn.", "warn")
            with _LOCK:
                manual = STATE["manual"]
                manual.update({
                    "status": "Đã tạo kịch bản; đang tự nạp vào dựng video…",
                    "recommended_voice": auto_voice_id,
                    "voice_analysis": (voice_result or {}).get("analysis") or {},
                    "voice_recommendations": (voice_result or {}).get("recommendations") or [],
                    "rev": int(manual.get("rev", 0)) + 1,
                })
                # Bàn giao khoá cho api_manual_run_all ngay trong thread này.
                STATE["running"] = False
                STATE["busy"] = ""
            _progress(pct=35, step="Kịch bản hoàn tất",
                      detail="%s từ · đang tạo giọng đọc" % result["words"])
            video_payload = dict(payload)
            video_payload.pop("text", None)
            video_payload["txt_path"] = result["script_path"]
            video_payload["name"] = str(payload.get("name") or result["title"])
            video_payload["anh"] = imgs
            if auto_voice_id:
                video_payload["voice"] = auto_voice_id
            video_payload["_progress_start"] = 35
            response, code = api_manual_run_all(video_payload)
            if code != 200:
                raise RuntimeError(response.get("error") or "Không khởi động được bước dựng video.")
            handed_off = True
            _log("Đã tự nạp KICH_BAN_DOC.txt vào pipeline video kể chuyện.", "ok")
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

    threading.Thread(target=_work, daemon=True).start()
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
    anh = b.get("anh")
    if isinstance(anh, str):
        anh = [x for x in re.split(r"[\r\n]+", anh) if x.strip()]
    if not isinstance(anh, list) or not anh:
        return {"error": "Hãy chọn ảnh hoặc thư mục ảnh."}, 400
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
        _CANCEL_EVENT.clear()
        STATE["running"] = True
        STATE["busy"] = "Đang làm video kể chuyện…"
        manual = STATE["manual"]
        manual.update({"working": True,
                       "status": "Bước 1/4: tổng hợp giọng đọc…",
                       "error": "", "output_path": "",
                       "rev": int(manual.get("rev", 0)) + 1})
    _story_progress(pct=3, step="Video kể chuyện", detail="Chuẩn bị văn bản")

    def _work(payload=dict(b), source_text=text, imgs=list(anh)):
        try:
            from .. import tts as tts_mod, nhac_nen as nn, slideshow as ss
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
            out_dir = os.path.join(HERE, "output", "manual_video")
            workdir = os.path.join(out_dir, "_tmp", f"{title}_{stamp}")
            os.makedirs(workdir, exist_ok=True)

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
                max_chunk_chars=240)   # đoạn ngắn -> mốc phụ đề mịn
            srt_path = os.path.join(out_dir, f"{title}_{stamp}.srt")
            srt_utils.save_srt_file(
                srt_path, _segments_tu_timeline(tts_res.get("segments")))
            audio = tts_res["path"]
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"audio_path": audio,
                               "audio_duration": tts_res["duration"],
                               "srt_path": srt_path,
                               "status": "Bước 2/4: nhạc nền…",
                               "rev": int(manual.get("rev", 0)) + 1})

            # ---- 2/4: nhạc nền (tuỳ chọn) ----
            if nhac.get("enabled", True):
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

            # ---- 3/4: phụ đề cứng (tuỳ chọn) ----
            ass_path = None
            if sub_cfg.get("enabled", True):
                _story_progress(pct=45, step="Video kể chuyện", detail="3/4 Dựng phụ đề")
                ass_path = _ass_tu_srt(srt_path, workdir, w, h, sub_cfg.get("style"))

            # ---- 4/4: dựng video từ ảnh ----
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"status": "Bước 4/4: dựng video từ ảnh…",
                               "rev": int(manual.get("rev", 0)) + 1})
            result = ss.tao_video_tu_anh(
                imgs, audio, os.path.join(out_dir, f"{title}_{stamp}.mp4"),
                workdir=workdir, w=w, h=h, fps=fps, kieu=kieu,
                ass_path=ass_path,
                progress=lambda pct, detail: _story_progress(
                    pct=50 + pct * 0.5, step="Video kể chuyện",
                    detail="4/4 " + str(detail)))
            with _LOCK:
                manual = STATE["manual"]
                manual.update({"working": False,
                               "status": "Video kể chuyện hoàn tất",
                               "output_path": result["path"], "error": "",
                               "rev": int(manual.get("rev", 0)) + 1})
            _story_progress(pct=100, step="Video kể chuyện xong",
                            detail=os.path.basename(result["path"]))
            _log(f"[Kể chuyện] Xong: {result['path']}", "ok")
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

    threading.Thread(target=_work, daemon=True).start()
    return {"ok": True, "async": True}, 200


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

    threading.Thread(target=_manual_mux_work, daemon=True).start()
    return {"ok": True, "async": True}, 200
