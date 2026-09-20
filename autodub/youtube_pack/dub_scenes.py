"""Cắt cảnh từ phim lồng tiếng, gửi ChatGPT tạo thumbnail, rồi vẽ tiêu đề lên ảnh."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ..utils import ffprobe_duration, run
from .const import THUMBNAIL_STYLE_LOCK, YEAR_RE
from .overlay import draw_thumbnail_overlay, split_thumbnail_lines

DEFAULT_SCENE_COUNT = 4
DEFAULT_CLIP_SECONDS = 3.0
_SPEAKER_RE = re.compile(r"^[\w .'\-]{1,24}[:：]\s*")
_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[^\s]+")
_STOP = {
    "thì", "mà", "ạ", "ư", "nhỉ", "nhé", "cái", "của", "này", "đó", "với",
    "cho", "được", "rồi", "đã", "sẽ", "đang", "như", "vậy", "thế", "nào",
    "hay", "và", "hoặc", "một", "những", "các", "rất", "lắm", "thôi", "à",
    "ơi", "hả", "cơ", "đi", "là", "ở", "trong", "ra", "lại", "còn", "nữa",
    "cũng", "nhưng", "nếu", "vì", "do", "khi", "lúc", "từ", "đến", "về",
}
_HOOK_NEEDLES = (
    "sự thật", "hóa ra", "hoá ra", "đừng", "chết", "lừa", "phản bội",
    "oan", "bí mật", "không thể", "sao ", "ai dám", "dám ", "mười năm",
    "10 năm", "con dâu", "mẹ chồng", "ly hôn", "giết", "cứu", "sổ đỏ",
    "giấy nợ", "đuổi", "xin lỗi", "không phải", "im đi", "ra khỏi",
    "đừng hòng", "trả thù", "nhân quả",
)


def want_dub_thumbnail(opt: Optional[Dict], cfg: Optional[Dict]) -> bool:
    """Ô lồng tiếng: ưu tiên option dự án, không thì config, mặc định bật."""
    opt = opt if isinstance(opt, dict) else {}
    yt = (cfg or {}).get("dang_youtube") if isinstance((cfg or {}).get("dang_youtube"), dict) else {}
    if "auto_dub_thumbnail" in opt and opt.get("auto_dub_thumbnail") is not None:
        return bool(opt.get("auto_dub_thumbnail"))
    return bool(yt.get("auto_dub_thumbnail", False))


def dub_scene_settings(cfg: Optional[Dict]) -> Tuple[int, float]:
    yt = (cfg or {}).get("dang_youtube") if isinstance((cfg or {}).get("dang_youtube"), dict) else {}
    try:
        count = max(1, min(8, int(yt.get("dub_scene_count") or DEFAULT_SCENE_COUNT)))
    except (TypeError, ValueError):
        count = DEFAULT_SCENE_COUNT
    try:
        seconds = max(1.5, min(8.0, float(yt.get("dub_scene_seconds") or DEFAULT_CLIP_SECONDS)))
    except (TypeError, ValueError):
        seconds = DEFAULT_CLIP_SECONDS
    return count, seconds


def _seg_fields(item: Any) -> Tuple[float, float, str]:
    if item is None:
        return 0.0, 0.0, ""
    if isinstance(item, dict):
        start = float(item.get("start") or 0.0)
        end = float(item.get("end") or start)
        text = str(item.get("vi") or item.get("text") or item.get("src") or "").strip()
        return start, end, text
    start = float(getattr(item, "start", 0.0) or 0.0)
    end = float(getattr(item, "end", start) or start)
    text = str(getattr(item, "text", "") or "").strip()
    return start, end, text


def compact_hook(text: str, title: str = "") -> Tuple[str, str]:
    """Rút câu thoại thành 2 dòng chữ thumbnail cực lớn."""
    raw = _SPEAKER_RE.sub("", str(text or "")).strip().strip("\"'`“”‘’")
    raw = re.sub(r"mười\s*năm", "10 NĂM", raw, flags=re.I)
    raw = _SPACE_RE.sub(" ", raw)
    if not raw:
        return split_thumbnail_lines(title)
    year = YEAR_RE.search(raw)
    words = [w for w in _WORD_RE.findall(re.sub(r"[.,;:!?…]+", " ", raw)) if w]
    keep = []
    for word in words:
        token = re.sub(r"[^\wÀ-ỹ]+", "", word, flags=re.UNICODE)
        if token.lower() in _STOP and len(keep) >= 2:
            continue
        keep.append(word.strip(".,;:!?…").upper())
        if len(keep) >= 8:
            break
    blob = " ".join(keep) if keep else raw
    if year and YEAR_RE.search(blob) is None:
        blob = "%s %s %s" % (blob, year.group(1), year.group(2))
    line1, line2 = split_thumbnail_lines(blob, title)
    if not line1:
        return split_thumbnail_lines(title or raw)
    return line1, line2


def score_scene(text: str, start: float, video_dur: float = 0.0) -> float:
    blob = str(text or "").strip()
    if not blob:
        return -10.0
    low = blob.lower()
    score = 0.0
    n = len(blob)
    if 10 <= n <= 52:
        score += 3.5
    elif 6 <= n <= 80:
        score += 1.5
    else:
        score -= 1.0
    if "?" in blob or "？" in blob:
        score += 2.5
    if "!" in blob or "！" in blob:
        score += 1.5
    for needle in _HOOK_NEEDLES:
        if needle in low:
            score += 2.0
    if video_dur > 1:
        pos = max(0.0, min(1.0, start / video_dur))
        if 0.18 <= pos <= 0.88:
            score += 2.0
        elif pos < 0.06 or pos > 0.96:
            score -= 4.0
        else:
            score += 0.5
    return score


def pick_scene_moments(segments: Iterable[Any], video_dur: float = 0.0,
                       count: int = DEFAULT_SCENE_COUNT) -> List[Dict[str, Any]]:
    """Chọn vài câu thoại nổi (tách xa nhau) để cắt cảnh."""
    want = max(1, min(8, int(count or DEFAULT_SCENE_COUNT)))
    scored: List[Dict[str, Any]] = []
    for item in segments or []:
        start, end, text = _seg_fields(item)
        if not text:
            continue
        mid = start + max(0.0, (end - start) * 0.42)
        scored.append({
            "start": start,
            "end": max(end, start + 0.4),
            "at": mid,
            "text": text,
            "score": score_scene(text, start, video_dur),
        })
    scored.sort(key=lambda row: (-float(row["score"]), float(row["start"])))
    gap = 20.0
    if video_dur > 1:
        if video_dur < 90:
            gap = max(2.4, video_dur / max(3.0, want + 1))
        else:
            gap = max(12.0, min(90.0, video_dur / max(4.0, want * 2.5)))
    picked: List[Dict[str, Any]] = []
    for row in scored:
        if any(abs(row["at"] - other["at"]) < gap for other in picked):
            continue
        line1, line2 = compact_hook(row["text"])
        if not line1:
            continue
        row = dict(row)
        row["line1"] = line1
        row["line2"] = line2
        picked.append(row)
        if len(picked) >= want:
            break
    if len(picked) < want and video_dur > 2:
        for frac in (0.22, 0.41, 0.58, 0.74, 0.86):
            at = video_dur * frac
            if any(abs(at - other["at"]) < gap for other in picked):
                continue
            line1, line2 = compact_hook("", title="CẢNH NÓNG")
            picked.append({
                "start": max(0.0, at - 0.4),
                "end": min(video_dur, at + 2.0),
                "at": at,
                "text": "",
                "score": 0.0,
                "line1": line1,
                "line2": line2,
            })
            if len(picked) >= want:
                break
    picked.sort(key=lambda row: float(row["at"]))
    return picked[:want]


def _format_tc(seconds: float) -> str:
    total = max(0, int(seconds))
    hh, rem = divmod(total, 3600)
    mm, ss = divmod(rem, 60)
    return "%02d:%02d:%02d" % (hh, mm, ss)


def build_dub_thumbnail_visual_prompt(title: str,
                                      scenes: Optional[Sequence[Dict[str, Any]]] = None
                                      ) -> str:
    """Prompt ảnh phim lồng tiếng: bám cảnh mẫu, không chữ, chừa trái cho tiêu đề."""
    lines = [
        THUMBNAIL_STYLE_LOCK,
        "",
        "Đây là thumbnail YouTube cho video LỒNG TIẾNG / DỊCH PHIM, không phải kể chuyện audio.",
        "Các ảnh đính kèm là khung hình THẬT cắt từ phim. Hãy vẽ lại một ảnh 16:9 photorealistic, giữ đúng người, trang phục, tuổi tác và bối cảnh trong ảnh mẫu.",
        "Nhân vật chính lệch hẳn về bên PHẢI. Nửa TRÁI trống, tối nhẹ, để ghép tiêu đề lớn sau này.",
        "KHÔNG sinh chữ, số, phụ đề, logo, watermark.",
        "Tiêu đề video (chỉ để hiểu mood, tuyệt đối KHÔNG vẽ chữ lên ảnh): %s"
        % (str(title or "").strip() or "phim lồng tiếng Việt"),
    ]
    for idx, scene in enumerate(list(scenes or [])[:4], 1):
        hook = " / ".join(
            x for x in (scene.get("line1"), scene.get("line2")) if x)
        spoken = str(scene.get("text") or "").strip()
        bit = hook or spoken
        if bit:
            lines.append("Cảnh %d: %s" % (idx, bit[:120]))
    lines.append(
        "Nhắc lại: nhân vật lệch phải, nửa trái trống, không chữ, không watermark.")
    return "\n".join(lines)


def _prepare_reference_jpeg(src: str, dest: Path, max_side: int = 1280) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
        with Image.open(src) as im:
            img = im.convert("RGB")
            width, height = img.size
            scale = min(1.0, float(max_side) / float(max(width, height) or 1))
            if scale < 1:
                img = img.resize((max(1, int(width * scale)),
                                  max(1, int(height * scale))))
            img.save(dest, format="JPEG", quality=86)
        return str(dest)
    except Exception:
        return src


def chatgpt_thumbnail_from_scenes(
        scenes: Sequence[Dict[str, Any]],
        dest_dir: str | os.PathLike,
        title: str,
        line1: str,
        line2: str,
        cfg: Optional[Dict] = None,
        logger: Optional[Callable[[str, str], None]] = None,
        cancel_event=None,
        session=None) -> Optional[str]:
    """Gửi ảnh cảnh cho ChatGPT, vẽ tiêu đề lên ảnh trả về. Lỗi thì trả None."""
    log = logger or (lambda _msg, _kind="info": None)
    frames = [str(row.get("raw_path") or "") for row in (scenes or [])
              if row.get("raw_path") and os.path.isfile(str(row.get("raw_path")))]
    if not frames:
        return None
    out_dir = Path(dest_dir)
    refs_dir = out_dir / "chatgpt_refs"
    prepared = [
        _prepare_reference_jpeg(path, refs_dir / ("%02d.jpg" % idx))
        for idx, path in enumerate(frames[:4], 1)
    ]
    prompt = build_dub_thumbnail_visual_prompt(title, scenes)
    own = session is None
    if own:
        from .. import chatgpt_web
        settings = chatgpt_web.chatgpt_settings(cfg or {})
        session = chatgpt_web.ChatGPTWebSession(
            settings["profile_dir"], channel=settings["channel"],
            url=settings["url"], wait_image=settings["wait_image"],
            wait_reply=settings["wait_reply"], logger=log,
            cancel_event=cancel_event)
        session.start()
    try:
        log("Đang gửi %d cảnh sang ChatGPT để tạo thumbnail…" % len(prepared), "step")
        raw_path = out_dir / "thumbnail_raw.png"
        session.generate_image(prompt, raw_path, image_paths=prepared)
        dest = draw_thumbnail_overlay(raw_path, out_dir / "thumbnail.jpg",
                                      line1, line2)
        log("ChatGPT đã tạo thumbnail, đã ghép tiêu đề: %s" % dest, "ok")
        return str(dest)
    finally:
        if own and session is not None:
            try:
                session.close()
            except Exception:
                pass


def extract_frame(video: str, t: float, dest: str) -> str:
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, float(t)):.3f}", "-i", video,
        "-frames:v", "1", "-q:v", "2", str(dest_path),
    ], timeout=40)
    if not dest_path.is_file() or dest_path.stat().st_size < 200:
        raise RuntimeError("Không lấy được khung hình.")
    return str(dest_path)


def extract_clip(video: str, start: float, seconds: float, dest: str) -> str:
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, float(start)):.3f}",
        "-t", f"{max(1.0, float(seconds)):.3f}",
        "-i", video,
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-ac", "2", "-b:a", "128k",
        "-movflags", "+faststart",
        str(dest_path),
    ], timeout=90)
    if not dest_path.is_file() or dest_path.stat().st_size < 400:
        raise RuntimeError("Không cắt được cảnh.")
    return str(dest_path)


def _frame_is_dark(path: str, threshold: float = 16.0) -> bool:
    try:
        from PIL import Image
        with Image.open(path) as im:
            small = im.convert("L").resize((64, 36))
            buf = small.tobytes()
        if not buf:
            return True
        return (sum(buf) / len(buf)) < threshold
    except Exception:
        return False


def _write_caption_file(path: Path, scenes: Sequence[Dict[str, Any]],
                        title: str = "", chatgpt: bool = False) -> Path:
    lines = [
        "Câu thumbnail cho video lồng tiếng",
        ("Tiêu đề: %s" % title) if title else "",
        "Ảnh chính: thumbnail.jpg"
        + (" (ChatGPT vẽ từ cảnh phim, đã ghép tiêu đề)" if chatgpt
           else " (cắt từ phim, đã ghép tiêu đề)"),
        "",
    ]
    for idx, scene in enumerate(scenes, 1):
        hook = " / ".join(x for x in (scene.get("line1"), scene.get("line2")) if x)
        lines.append("%d. [%s] %s" % (idx, _format_tc(float(scene.get("at") or 0)), hook))
        if scene.get("text"):
            lines.append("   Thoại: %s" % scene["text"])
        if scene.get("thumb_path"):
            lines.append("   Ảnh: %s" % os.path.basename(str(scene["thumb_path"])))
        if scene.get("clip_path"):
            lines.append("   Cảnh: %s" % os.path.basename(str(scene["clip_path"])))
        lines.append("")
    path.write_text("\n".join(line for line in lines if line is not None).strip() + "\n",
                    encoding="utf-8")
    return path


def make_dub_thumbnail_pack(
        video_path: str,
        segments: Optional[Iterable[Any]] = None,
        out_dir: str = "",
        duration: float = 0.0,
        count: int = DEFAULT_SCENE_COUNT,
        clip_seconds: float = DEFAULT_CLIP_SECONDS,
        title: str = "",
        logger: Optional[Callable[[str, str], None]] = None,
        fallback_video: str = "",
        cfg: Optional[Dict] = None,
        cancel_event=None,
        chatgpt_session=None) -> Dict[str, Any]:
    """Cắt vài cảnh, vẽ thumbnail, ghi cau_thumbnail.txt. Lỗi cảnh lẻ thì bỏ qua."""
    log = logger or (lambda _msg, _kind="info": None)
    video = str(video_path or "").strip()
    alt = str(fallback_video or "").strip()
    if video and not os.path.isfile(video) and alt and os.path.isfile(alt):
        video = alt
    if not video or not os.path.isfile(video):
        raise FileNotFoundError("Chưa có file video để cắt cảnh thumbnail.")
    dest_dir = Path(out_dir or Path(video).parent)
    dest_dir.mkdir(parents=True, exist_ok=True)
    seconds = float(duration or 0.0)
    if seconds <= 1:
        try:
            seconds = float(ffprobe_duration(video) or 0.0)
        except Exception:
            seconds = 0.0
    moments = pick_scene_moments(segments or [], seconds, count)
    thumbs_dir = dest_dir / "thumbnails"
    scenes_dir = dest_dir / "scenes"
    made: List[Dict[str, Any]] = []
    sources = [p for p in (video, alt) if p and os.path.isfile(p)]

    for idx, moment in enumerate(moments, 1):
        stamp = moment["at"]
        raw_path = thumbs_dir / ("%02d_raw.jpg" % idx)
        thumb_path = thumbs_dir / ("%02d.jpg" % idx)
        clip_path = scenes_dir / ("%02d.mp4" % idx)
        grabbed = ""
        src_used = ""
        for src in sources:
            try:
                grabbed = extract_frame(src, stamp, str(raw_path))
                if _frame_is_dark(grabbed):
                    grabbed = extract_frame(src, min(stamp + 0.9, stamp + 2.0),
                                            str(raw_path))
                src_used = src
                break
            except InterruptedError:
                raise
            except Exception as exc:
                log("Bỏ khung %02d (%.1fs): %s" % (idx, stamp, str(exc)[:160]), "warn")
                grabbed = ""
        if not grabbed:
            continue
        try:
            extract_clip(
                src_used,
                max(0.0, float(moment["start"]) - 0.25),
                clip_seconds,
                str(clip_path),
            )
            moment["clip_path"] = str(clip_path)
        except InterruptedError:
            raise
        except Exception as exc:
            log("Cắt clip %02d lỗi (vẫn giữ ảnh): %s" % (idx, str(exc)[:160]), "warn")
        try:
            draw_thumbnail_overlay(
                grabbed, thumb_path, moment.get("line1") or "",
                moment.get("line2") or "")
            moment["thumb_path"] = str(thumb_path)
            moment["raw_path"] = grabbed
            made.append(dict(moment))
        except Exception as exc:
            log("Vẽ chữ thumbnail %02d lỗi: %s" % (idx, str(exc)[:160]), "warn")
            try:
                Path(thumb_path).write_bytes(Path(grabbed).read_bytes())
                moment["thumb_path"] = str(thumb_path)
                made.append(dict(moment))
            except Exception:
                continue

    if not made:
        raise RuntimeError("Không cắt được cảnh nào để làm thumbnail.")

    best = max(made, key=lambda row: float(row.get("score") or 0.0))
    main = dest_dir / "thumbnail.jpg"
    try:
        from shutil import copyfile
        copyfile(best["thumb_path"], main)
    except Exception:
        main = Path(best["thumb_path"])
    line1 = str(best.get("line1") or "")
    line2 = str(best.get("line2") or "")
    chatgpt_path = ""
    try:
        chatgpt_path = chatgpt_thumbnail_from_scenes(
            made, dest_dir, title, line1, line2, cfg=cfg, logger=log,
            cancel_event=cancel_event, session=chatgpt_session) or ""
    except InterruptedError:
        raise
    except Exception as exc:
        log("ChatGPT chưa tạo được thumbnail, giữ ảnh cắt từ phim: %s"
            % str(exc)[:220], "warn")
    if chatgpt_path and os.path.isfile(chatgpt_path):
        main = Path(chatgpt_path)
    caption_path = _write_caption_file(
        dest_dir / "cau_thumbnail.txt", made, title=title,
        chatgpt=bool(chatgpt_path))
    log("Đã cắt %d cảnh, thumbnail: %s" % (len(made), main), "ok")
    return {
        "thumbnail_path": str(main),
        "caption_path": str(caption_path),
        "thumbnail_raw_path": str(dest_dir / "thumbnail_raw.png")
        if (dest_dir / "thumbnail_raw.png").is_file() else "",
        "scene_count": len(made),
        "scenes": made,
        "line1": line1,
        "line2": line2,
        "chatgpt": bool(chatgpt_path),
    }


def attach_dub_thumbnails(
        video_path: str,
        segments: Optional[Iterable[Any]] = None,
        out_dir: str = "",
        duration: float = 0.0,
        title: str = "",
        opt: Optional[Dict] = None,
        cfg: Optional[Dict] = None,
        logger: Optional[Callable[[str, str], None]] = None,
        fallback_video: str = "",
        cancel_event=None) -> Dict[str, Any]:
    """Gắn sau bước xuất video; tắt ô hoặc lỗi thì trả {} chứ không làm hỏng job."""
    log = logger or (lambda _msg, _kind="info": None)
    if not want_dub_thumbnail(opt, cfg):
        return {}
    count, clip_seconds = dub_scene_settings(cfg)
    try:
        return make_dub_thumbnail_pack(
            video_path, segments=segments, out_dir=out_dir, duration=duration,
            count=count, clip_seconds=clip_seconds, title=title, logger=log,
            fallback_video=fallback_video, cfg=cfg, cancel_event=cancel_event)
    except InterruptedError:
        raise
    except Exception as exc:
        log("Chưa cắt được cảnh thumbnail: %s" % str(exc)[:240], "warn")
        return {}
