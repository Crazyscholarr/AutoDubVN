"""Tiện ích dùng chung cho luồng kể chuyện."""
from __future__ import annotations

import os
import re
from typing import Dict, Optional, Tuple

from ..state import HERE, STATE, _LOCK, current_cancel_event, _log, _progress
from ..helpers import _safe_path_stem, _doc_file_van_ban

JsonResult = Tuple[Dict, int]


def _api():
    """Module facade mà unittest patch (submit_job, _load_cfg, ...)."""
    from autodub.server import manual_api
    return manual_api


class DangBan(RuntimeError):
    """Máy đang chạy việc dài - không chen bản nghe thử vào giữa."""


def _raise_if_cancelled() -> None:
    if current_cancel_event().is_set():
        raise InterruptedError("Đã dừng tác vụ theo yêu cầu.")


def _mark_manual_cancelled(detail: str = "Các phần đã làm xong vẫn được giữ lại.") -> None:
    """Đưa tác vụ kể chuyện về trạng thái dừng, không báo nhầm là lỗi."""
    with _LOCK:
        manual = STATE["manual"]
        manual.update({
            "working": False,
            "status": "Đã dừng tác vụ",
            "error": "",
            "rev": int(manual.get("rev", 0)) + 1,
        })
    _progress(step="Đã dừng", detail=detail)
    _log("Đã dừng tác vụ theo yêu cầu; dữ liệu hoàn tất vẫn được giữ lại.", "warn")


def _story_output_dir(title: str) -> str:
    """Thư mục sản phẩm video kể chuyện, đặt theo đúng tiêu đề dễ nhận biết."""
    safe_title = _safe_path_stem(title, fallback="video_ke_chuyen", limit=70)
    return os.path.join(HERE, "output", safe_title)


def _story_tts_workdir(out_dir: str, title: str, stamp: str,
                       engine: str) -> Tuple[str, int]:
    """Tiếp tục các clip CapCut của lượt hỏng gần nhất nếu còn trên đĩa.

    Tên mỗi clip CapCut chứa hash của nội dung + voice + tốc độ nên dùng lại
    thư mục cũ vẫn an toàn khi người dùng sửa truyện hoặc đổi giọng: chỉ file có
    đúng hash mới được tái sử dụng, phần khác sẽ được tạo mới.
    """
    tmp_root = os.path.join(out_dir, "_tmp")
    fresh = os.path.join(tmp_root, f"{title}_{stamp}")
    if str(engine or "").lower() != "capcut" or not os.path.isdir(tmp_root):
        return fresh, 0
    candidates = []
    try:
        for entry in os.scandir(tmp_root):
            if not entry.is_dir() or not entry.name.startswith(title + "_"):
                continue
            try:
                clips = sum(1 for item in os.scandir(entry.path)
                            if item.is_file() and item.name.startswith("capcut_")
                            and item.name.lower().endswith(".mp3")
                            and item.stat().st_size >= 512)
                if clips:
                    candidates.append((entry.stat().st_mtime, clips, entry.path))
            except OSError:
                continue
    except OSError:
        return fresh, 0
    if not candidates:
        return fresh, 0
    _mtime, clips, path = max(candidates, key=lambda item: item[0])
    return path, clips


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


def _cta_tts_options(payload: Dict) -> Dict:
    """Lấy tốc độ/nội dung CTA; tắt CTA thì tuyệt đối không tăng tốc câu nào."""
    cta = payload.get("cta") if isinstance(payload.get("cta"), dict) else {}
    if not bool(cta.get("enabled", True)):
        return {"channel_cta_speed": 1.0, "channel_cta_text": ""}
    try:
        speed = max(1.0, min(2.0, float(cta.get("speed", 2.0) or 2.0)))
    except (TypeError, ValueError):
        speed = 2.0
    return {"channel_cta_speed": speed,
            "channel_cta_text": str(cta.get("text") or "").strip()}


def _ensure_story_ctas(text: str, payload: Dict) -> str:
    """Bổ sung CTA cho cả truyện dán tay; truyện do writer tạo sẵn không bị lặp."""
    cta_cfg = payload.get("cta") if isinstance(payload.get("cta"), dict) else None
    body = str(text or "").strip()
    if not cta_cfg or not bool(cta_cfg.get("enabled", True)) or not body:
        return body
    template = str(cta_cfg.get("text") or "").strip() or (
        "Bạn đang nghe chuyện tại gốc mít kể chuyện . Nếu thấy câu chuyện này ý nghĩa, "
        "cô chú, anh chị nhớ đăng ký kênh, bật chuông và để lại một lời bình luận để "
        "tiếp tục đồng hành cùng Gốc Mít nghen. Mọi nội dung đều hư cấu xin mọi người "
        "không làm theo bất cứ dưới hình thức nào hoặc tung tin đồn , chúng tôi không "
        "chịu trách nhiệm .")
    cta = template.replace("{channel}", "Gốc Mít Kể Chuyện").strip()
    raw_positions = cta_cfg.get("positions")
    if not isinstance(raw_positions, (list, tuple)):
        raw_positions = [12, 55]
    positions = []
    for value in raw_positions:
        try:
            pct = max(5, min(90, int(float(value))))
        except (TypeError, ValueError):
            continue
        if pct not in positions:
            positions.append(pct)
    for fallback in (12, 55):
        if len(positions) >= 2:
            break
        if fallback not in positions:
            positions.append(fallback)
    already = body.count(cta)
    needed = max(0, len(positions) - already)
    if not needed:
        return body
    words = list(re.finditer(r"\S+", body))
    if len(words) < 2:
        return (body + "\n\n" + "\n\n".join([cta] * needed)).strip()
    cuts = []
    for pct in sorted(positions[-needed:]):
        target = max(1, min(len(words) - 1, int(len(words) * pct / 100.0)))
        start = words[target - 1].end()
        window = body[start:start + 1200]
        paragraph = re.search(r"\n\s*\n", window)
        sentence = re.search(r"[.!?][\"'”’)]*\s+", window)
        candidates = [start + m.end() for m in (paragraph, sentence) if m]
        cut = min(candidates) if candidates else start
        if cut in cuts:
            cut = start
        if cut not in cuts:
            cuts.append(cut)
    for cut in sorted(cuts, reverse=True):
        body = body[:cut].rstrip() + "\n\n" + cta + "\n\n" + body[cut:].lstrip()
    return body


def _story_design_text(payload: Dict) -> str:
    """Tìm hồ sơ nhân vật cạnh KICH_BAN_DOC.txt; truyện dán tay vẫn dùng dò tên."""
    explicit = str(payload.get("character_context_path") or "").strip().strip('"')
    txt_path = str(payload.get("txt_path") or "").strip().strip('"')
    candidates = [explicit]
    if txt_path:
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(txt_path)),
                                       "00_ban_thiet_ke.txt"))
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                return _doc_file_van_ban(path)
            except Exception:
                continue
    return ""


def _story_title_key(value: str) -> str:
    """Khoá so sánh tiêu đề, bỏ khác biệt hoa/thường và dấu câu."""
    return re.sub(r"[^\w]+", " ", str(value or "").casefold(),
                  flags=re.UNICODE).strip()
