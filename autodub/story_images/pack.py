"""Gói prompt/ảnh bền vững: slug, manifest và API văn bản."""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import unicodedata
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence


GEMINI_WEB_URL = "https://gemini.google.com/app"
DEFAULT_BROWSER_IMAGE_MODEL = "Nano Banana 2 (Gemini web)"
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"
}
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PACK_ROOT = PROJECT_ROOT / "output" / "story_image_packs"


def _slug(text: str, fallback: str = "truyen") -> str:
    plain = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    plain = plain.replace("đ", "d").replace("Đ", "D")
    plain = re.sub(r"[^A-Za-z0-9]+", "_", plain).strip("_").lower()
    return (plain or fallback)[:64]


def _manifest_path(path: str | os.PathLike) -> Path:
    p = Path(path).expanduser().resolve()
    return p / "manifest.json" if p.is_dir() else p


def _write_json(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _expand_images(paths: Iterable[str]) -> List[str]:
    out: List[str] = []
    for raw in paths or []:
        p = Path(str(raw or "").strip().strip('"')).expanduser()
        if p.is_dir():
            files = sorted(
                (x for x in p.iterdir()
                 if x.is_file() and x.suffix.lower() in IMAGE_EXTENSIONS),
                key=lambda x: [int(t) if t.isdigit() else t.lower()
                               for t in re.split(r"(\d+)", x.name)],
            )
            out.extend(str(x.resolve()) for x in files)
        elif p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
            out.append(str(p.resolve()))
    # Bỏ trùng nhưng giữ nguyên thứ tự người dùng đã chọn.
    seen = set()
    unique = []
    for p in out:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            unique.append(os.path.abspath(p))
    return unique


def parse_scene_prompts(text: str) -> List[str]:
    """Đọc prompt đánh số, kể cả Markdown ``**1.** ...`` của Gemini."""
    matches = list(re.finditer(
        r"(?ms)^\s*(?:\*\*)?(\d{1,3})[.)](?:\*\*)?\s+(.+?)"
        r"(?=^\s*(?:\*\*)?\d{1,3}[.)](?:\*\*)?\s+|^\s*NEGATIVE\s*:|\Z)",
        text or "",
    ))
    return [re.sub(r"\s+", " ", m.group(2)).strip() for m in matches
            if len(re.sub(r"\s+", " ", m.group(2)).strip()) > 20]


def generate_scene_prompts(master_prompt: str, cfg: Dict,
                           expected_count: int = 14,
                           logger: Optional[Callable] = None) -> List[str]:
    """Dùng provider văn bản đang cấu hình để rút prompt từng cảnh thật.

    Chỉ gọi Gemini API khi phần tạo ảnh được cấu hình rõ ``provider: api``.
    Ở chế độ browser, key cũ còn sót trong cấu hình dịch không được dùng nhầm;
    hàm dùng Gemini web với profile đã đăng nhập khi provider văn bản cũng là browser.
    """
    logger = logger or (lambda _msg, _kind="info": None)
    from autodub.story_images import (
        gemini_browser_settings, generate_scene_prompts_gemini_browser)
    tr = dict(cfg.get("translation")
              if isinstance(cfg.get("translation"), dict) else {})
    provider = str(tr.get("provider") or "browser").strip().lower()
    image_cfg = cfg.get("tao_anh") if isinstance(cfg.get("tao_anh"), dict) else {}
    image_provider = str(image_cfg.get("provider") or "browser").strip().lower()
    # Browser ảnh vẫn có thể dùng provider văn bản khác (NVIDIA/TokenRouter)
    # để rút prompt cảnh. Nếu phần dịch cũng là browser/Gemini thì hỏi trực
    # tiếp Gemini web bằng đúng profile đăng nhập, tuyệt đối không mượn key cũ.
    browser_images = image_provider not in {"api", "gemini"}
    if browser_images and provider in {"browser", "gemini"}:
        settings = gemini_browser_settings(cfg)
        try:
            return generate_scene_prompts_gemini_browser(
                master_prompt, expected_count=expected_count,
                profile_dir=settings["profile_dir"], channel=settings["channel"],
                url=settings["url"], wait_reply=settings["wait_reply"],
                logger=logger)
        except Exception as exc:
            logger("Chưa tự rút được prompt bằng Gemini web: %s" %
                   str(exc)[:180], "warn")
            return []
    if provider == "browser" and image_provider in {"api", "gemini"}:
        image_key = str(image_cfg.get("gemini_api_key") or
                        tr.get("gemini_api_key") or "").strip()
        if image_key:
            tr["gemini_api_key"] = image_key
            provider = "gemini"
    try:
        from .. import translate
        api_key, model, base_url, timeout = translate.api_params_for_provider(
            tr, provider)
        if provider == "browser" or not str(api_key or "").strip():
            return []
        logger("Đang rút prompt hình ảnh bám theo 6 chương…", "step")
        raw = translate._api_call(
            master_prompt, api_key, model, 0.35, provider=provider,
            api_base_url=base_url, api_timeout=timeout)
        prompts = parse_scene_prompts(raw)
        if len(prompts) < expected_count:
            logger("AI chỉ trả %d/%d prompt cảnh; chuyển sang Gemini web."
                   % (len(prompts), expected_count), "warn")
            if browser_images:
                settings = gemini_browser_settings(cfg)
                try:
                    return generate_scene_prompts_gemini_browser(
                        master_prompt, expected_count=expected_count,
                        profile_dir=settings["profile_dir"],
                        channel=settings["channel"], url=settings["url"],
                        wait_reply=settings["wait_reply"], logger=logger)
                except Exception as browser_exc:
                    logger("Gemini web cũng chưa rút được prompt: %s" %
                           str(browser_exc)[:180], "warn")
            return []
        return prompts[:expected_count]
    except Exception as exc:
        logger("Chưa tự rút được prompt từng cảnh: %s" % str(exc)[:180], "warn")
        if browser_images:
            settings = gemini_browser_settings(cfg)
            try:
                return generate_scene_prompts_gemini_browser(
                    master_prompt, expected_count=expected_count,
                    profile_dir=settings["profile_dir"], channel=settings["channel"],
                    url=settings["url"], wait_reply=settings["wait_reply"],
                    logger=logger)
            except Exception as browser_exc:
                logger("Gemini web cũng chưa rút được prompt: %s" %
                       str(browser_exc)[:180], "warn")
        return []


def _prompt_document(manifest: Dict, master_prompt: str) -> str:
    lines = [
        "GÓI PROMPT ẢNH AUTODUBVN",
        "",
        f"Gemini: {manifest['provider']['url']}",
        f"Model: {manifest['provider']['model']}",
        f"Khổ ảnh: {manifest['aspect']}",
        "",
        "QUY ƯỚC LƯU ẢNH",
        "- Tạo lần lượt từ cảnh 001 đến hết.",
        "- Tải ảnh về đúng tên gợi ý scene_001.png, scene_002.png, ...",
        "- Không đổi thứ tự. AutoDubVN sẽ ghi nhớ thứ tự bằng manifest.json.",
        "- Giữ cùng ngoại hình nhân vật, trang phục, bảng màu và phong cách giữa các cảnh.",
        "",
    ]
    if master_prompt.strip():
        lines.extend(["PROMPT TỔNG ĐỂ GEMINI LẬP DANH SÁCH CẢNH", "", master_prompt.strip(), ""])
    prompts = [s for s in manifest.get("scenes", []) if s.get("prompt")]
    if prompts:
        lines.extend(["PROMPT TỪNG CẢNH", ""])
        for scene in prompts:
            lines.extend([
                f"CẢNH {scene['index']:03d} -> {scene['expected_file']}",
                scene["prompt"].strip(),
                "",
            ])
    return "\n".join(lines).rstrip() + "\n"


def load_pack(path: str | os.PathLike) -> Dict:
    manifest_path = _manifest_path(path)
    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or int(payload.get("schema_version", 0)) != 1:
        raise ValueError("Manifest gói ảnh không hợp lệ.")
    payload["manifest_path"] = str(manifest_path)
    payload["pack_dir"] = str(manifest_path.parent)
    return payload


def attach_images(path: str | os.PathLike, image_paths: Sequence[str]) -> Dict:
    """Chép ảnh vào gói và gắn lần lượt với các cảnh; không phụ thuộc file gốc."""
    manifest_path = _manifest_path(path)
    manifest = load_pack(manifest_path)
    images = _expand_images(image_paths)
    image_dir = manifest_path.parent / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    # Chụp một bản trung gian trước khi ghi đích. Nếu người dùng đảo thứ tự các
    # ảnh vốn đã nằm trong pack, copy thẳng scene_002 -> scene_001 sẽ phá mất
    # nguồn của lượt kế tiếp.
    staged = []
    for idx, source in enumerate(images, 1):
        src = Path(source)
        ext = src.suffix.lower() if src.suffix.lower() in IMAGE_EXTENSIONS else ".png"
        temp = image_dir / f"._incoming_{idx:03d}{ext}"
        shutil.copy2(src, temp)
        staged.append(temp)

    scenes = list(manifest.get("scenes") or [])
    while len(scenes) < len(images):
        i = len(scenes) + 1
        scenes.append({
            "index": i, "chapter": None, "prompt": "",
            "expected_file": f"images/scene_{i:03d}.png",
            "image_path": "", "status": "missing",
        })

    for idx, src in enumerate(staged, 1):
        ext = src.suffix.lower()
        dest = image_dir / f"scene_{idx:03d}{ext}"
        os.replace(src, dest)
        scene = scenes[idx - 1]
        scene.update({
            "index": idx,
            "expected_file": str(dest.relative_to(manifest_path.parent)).replace("\\", "/"),
            "image_path": str(dest.resolve()),
            "status": "ready",
        })

    # Ảnh bị bỏ khỏi danh sách không được lén dùng lại khi dựng.
    for scene in scenes[len(images):]:
        scene["image_path"] = ""
        scene["status"] = "missing"

    manifest["scenes"] = scenes
    manifest["scene_count"] = len(scenes)
    manifest["ready_count"] = len(images)
    manifest["status"] = "ready" if images and len(images) == len(scenes) else "waiting_images"
    manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest.pop("manifest_path", None)
    manifest.pop("pack_dir", None)
    _write_json(manifest_path, manifest)
    return load_pack(manifest_path)


def create_pack(title: str, design_text: str = "", master_prompt: str = "",
                scene_prompts: Optional[Sequence[str]] = None,
                image_paths: Optional[Sequence[str]] = None,
                aspect: str = "16:9", scene_count: int = 14,
                script_path: str = "", design_path: str = "",
                root: str | os.PathLike | None = None,
                pack_dir: str | os.PathLike | None = None) -> Dict:
    """Tạo manifest, tài liệu prompt và thư mục ảnh độc lập có thể phục hồi."""
    prompts = [str(x or "").strip() for x in (scene_prompts or [])]
    count = max(1, int(scene_count or 14), len(prompts), len(image_paths or []))
    if pack_dir:
        folder = Path(pack_dir).expanduser().resolve()
    else:
        base = Path(root).expanduser().resolve() if root else DEFAULT_PACK_ROOT
        stamp = time.strftime("%Y%m%d_%H%M%S")
        folder = base / f"{_slug(title)}_{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "images").mkdir(exist_ok=True)

    scenes = []
    for i in range(1, count + 1):
        scenes.append({
            "index": i,
            # Chia đều toàn bộ danh sách qua 6 chương, kể cả khi số cảnh
            # không chia hết (14 cảnh -> 3/2/3/2/2/2, không bỏ chương 6).
            "chapter": min(6, ((i - 1) * 6) // count + 1),
            "prompt": prompts[i - 1] if i <= len(prompts) else "",
            "expected_file": f"images/scene_{i:03d}.png",
            "image_path": "",
            "status": "missing",
        })
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest = {
        "schema_version": 1,
        "id": folder.name,
        "title": str(title or "Truyện").strip(),
        "created_at": now,
        "updated_at": now,
        "provider": {
            "name": "Gemini web",
            "model": DEFAULT_BROWSER_IMAGE_MODEL,
            "url": GEMINI_WEB_URL,
        },
        "aspect": "9:16" if str(aspect) == "9:16" else "16:9",
        "scene_count": count,
        "ready_count": 0,
        "status": "waiting_images",
        "script_path": os.path.abspath(script_path) if script_path else "",
        "design_path": os.path.abspath(design_path) if design_path else "",
        "prompt_file": str((folder / "PROMPTS_AI_STUDIO.txt").resolve()),
        "images_dir": str((folder / "images").resolve()),
        "scenes": scenes,
    }
    manifest_path = folder / "manifest.json"
    _write_json(manifest_path, manifest)
    (folder / "PROMPTS_AI_STUDIO.txt").write_text(
        _prompt_document(manifest, master_prompt), encoding="utf-8")
    if image_paths:
        return attach_images(manifest_path, list(image_paths))
    return load_pack(manifest_path)


def resolve_images(path: str | os.PathLike) -> List[str]:
    """Trả ảnh tồn tại theo index trong manifest; cảnh thiếu được bỏ qua rõ ràng."""
    manifest = load_pack(path)
    out = []
    for scene in sorted(manifest.get("scenes") or [], key=lambda x: int(x.get("index", 0))):
        # ``attach_images(..., [])`` dùng trạng thái missing để biểu thị người
        # dùng đã bấm Xoá hết. Không được thấy file scene_*.png còn trên đĩa
        # rồi tự ý nạp lại vào giao diện.
        if str(scene.get("status") or "missing") != "ready":
            continue
        raw = str(scene.get("image_path") or "")
        if not raw and scene.get("expected_file"):
            raw = str(Path(manifest["pack_dir"]) / str(scene["expected_file"]))
        if raw and Path(raw).is_file() and Path(raw).suffix.lower() in IMAGE_EXTENSIONS:
            out.append(str(Path(raw).resolve()))
    return out


def chapter_weights_from_script(script_path: str | os.PathLike) -> List[int]:
    """Lấy độ dài sáu chương cạnh KICH_BAN_DOC để rải ảnh đúng mạch truyện."""
    path = Path(script_path).expanduser().resolve()
    folder = path.parent
    chapter_files = sorted(folder.glob("chuong_*.txt"))
    weights = []
    for chapter in chapter_files[:6]:
        try:
            text = chapter.read_text(encoding="utf-8")
        except OSError:
            continue
        weights.append(max(1, len(re.findall(r"\w+", text, flags=re.UNICODE))))
    return weights if len(weights) >= 2 else []


def expand_for_chapters(path: str | os.PathLike, chapter_weights: Sequence[int],
                        total_duration: float, max_seconds: float = 25.0) -> List[str]:
    """Lặp ảnh theo nhóm chương để cảnh không bị quay vòng sai nội dung.

    Slideshow chia đều thời lượng mỗi phần. Ta phân bổ số phần theo số từ của
    từng chương, rồi chỉ quay vòng những ảnh thuộc chính chương đó. Sai số mốc
    chương tối đa xấp xỉ ``max_seconds`` thay vì ảnh chương 1 lặp tới cuối phim.
    """
    manifest = load_pack(path)
    ready = []
    for scene in sorted(manifest.get("scenes") or [], key=lambda x: int(x.get("index", 0))):
        if str(scene.get("status") or "missing") != "ready":
            continue
        raw = str(scene.get("image_path") or "")
        if not raw and scene.get("expected_file"):
            raw = str(Path(manifest["pack_dir"]) / str(scene["expected_file"]))
        if raw and Path(raw).is_file():
            ready.append((max(1, int(scene.get("chapter") or 1)), str(Path(raw).resolve())))
    if not ready:
        return []
    weights = [max(0, int(x or 0)) for x in chapter_weights]
    if not weights or sum(weights) <= 0:
        return [p for _c, p in ready]
    groups: Dict[int, List[str]] = {}
    for chapter, image in ready:
        groups.setdefault(chapter, []).append(image)
    total_parts = max(len(ready), int((max(0.1, float(total_duration)) + max_seconds - 1)
                                      // max_seconds))
    raw_counts = [total_parts * w / sum(weights) for w in weights]
    counts = [max(1 if groups.get(i + 1) else 0, int(x))
              for i, x in enumerate(raw_counts)]
    while sum(counts) < total_parts:
        candidates = sorted(range(len(weights)),
                            key=lambda i: (raw_counts[i] - int(raw_counts[i]), weights[i]),
                            reverse=True)
        counts[candidates[(sum(counts) - sum(int(x) for x in raw_counts)) % len(candidates)]] += 1
    while sum(counts) > total_parts:
        candidates = sorted(range(len(weights)), key=lambda i: counts[i], reverse=True)
        changed = False
        for i in candidates:
            minimum = 1 if groups.get(i + 1) else 0
            if counts[i] > minimum:
                counts[i] -= 1
                changed = True
                break
        if not changed:
            break

    all_images = [p for _c, p in ready]
    sequence = []
    for i, count in enumerate(counts, 1):
        choices = groups.get(i) or all_images
        sequence.extend(choices[j % len(choices)] for j in range(count))
    return sequence


def latest_pack(root: str | os.PathLike | None = None) -> Optional[Dict]:
    base = Path(root).expanduser().resolve() if root else DEFAULT_PACK_ROOT
    if not base.is_dir():
        return None
    files = list(base.glob("*/manifest.json"))
    if not files:
        return None
    return load_pack(max(files, key=lambda p: p.stat().st_mtime))


def public_summary(manifest: Dict, include_prompt: bool = False) -> Dict:
    """Dữ liệu gọn cho UI; tùy chọn kèm prompt dự phòng để sao chép sang Gemini."""
    result = {
        "ok": True,
        "title": str(manifest.get("title") or ""),
        "manifest_path": manifest.get("manifest_path", ""),
        "pack_dir": manifest.get("pack_dir", ""),
        "prompt_file": manifest.get("prompt_file", ""),
        "provider_url": (manifest.get("provider") or {}).get("url", GEMINI_WEB_URL),
        "status": manifest.get("status", "waiting_images"),
        "scene_count": int(manifest.get("scene_count", 0) or 0),
        "ready_count": int(manifest.get("ready_count", 0) or 0),
        "script_path": str(manifest.get("script_path") or ""),
        "images": resolve_images(manifest.get("manifest_path") or manifest.get("pack_dir")),
    }
    if include_prompt:
        prompts = [s for s in manifest.get("scenes") or []
                   if str(s.get("prompt") or "").strip()]
        if prompts:
            lines = [
                "Bạn đang ở chế độ tạo ảnh. Hãy tạo một bộ ảnh minh họa độc lập "
                "theo đúng thứ tự dưới đây.",
                "Không trả lại danh sách prompt, không ghép collage, không chèn "
                "chữ vào ảnh. Giữ nguyên ngoại hình nhân vật, trang phục, bối "
                "cảnh và bảng màu giữa tất cả các cảnh.",
                "Mỗi ảnh khổ %s. Hãy bắt đầu tạo từ CẢNH 001 và tiếp tục lần "
                "lượt; nếu hệ thống giới hạn số ảnh mỗi lượt thì tạo tối đa có "
                "thể rồi chờ tôi nhắn 'tiếp tục'." %
                str(manifest.get("aspect") or "16:9"),
                "",
            ]
            for scene in prompts:
                lines.extend([
                    "CẢNH %03d" % int(scene.get("index") or 0),
                    str(scene.get("prompt") or "").strip(),
                    "",
                ])
            result["prompt_text"] = "\n".join(lines).strip()
        else:
            prompt_file = str(manifest.get("prompt_file") or "")
            result["prompt_text"] = (
                Path(prompt_file).read_text(encoding="utf-8")
                if prompt_file and Path(prompt_file).is_file() else "")
    return result
