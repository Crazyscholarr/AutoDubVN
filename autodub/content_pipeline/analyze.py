"""Phân tích một bản ghi bằng heuristic và (khi có) AI / trình duyệt."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .heuristic import heuristic_analysis
from .json_ai import _extract_json
from .records import DEFAULT_OUTPUT_DIR, ROOT, _list_value


TITLE_PROMPT = """Bạn đặt tiêu đề video cho kênh audio Việt Nam \"Gốc Mít Kể Chuyện\",
chủ đề chuyện gia đình, tuổi già và chuyện tâm linh ông bà kể, khán giả từ 45 tuổi trở lên.

NỘI DUNG TRUYỆN: [DÁN TÓM TẮT]

Hãy tạo đúng 8 tiêu đề theo công thức 3 phần:
[MÓC CẢM XÚC] : [MỆNH ĐỀ VIẾT HOA TOÀN BỘ] | [ĐUÔI TỪ KHÓA]

Móc cảm xúc chỉ chọn trong: Nghe Mà Thấm / Nghe THẤM Tận Xương / Nghe Là Khóc /
Nghe Mà Nghẹn Lòng / Nghe Mà Rơi Nước Mắt / Nghe Sướng Lỗ Tai / Nghe Mà Sốc /
Truyện Ngắn Tuổi Xế Chiều Cực Hay. Riêng chuyện tâm linh có thể chọn: Nghe Mà
Lạnh Gáy / Chuyện Ông Bà Kể / Nghe Mà Rùng Mình / Chuyện Làng Quê Kỳ Bí.

Mệnh đề viết hoa phải nêu đúng tình huống sốc nhất, dài 8 đến 14 từ, viết hoa
toàn bộ, là một sự việc cụ thể chứ không phải cảm xúc chung. Có con số càng tốt.

Đuôi từ khóa chỉ chọn trong: Kể Chuyện Đêm Khuya / Đọc Truyện Đêm Khuya /
Kể Chuyện Tuổi Già / Kể Chuyện Làng Quê / Kể Chuyện Tâm Linh.

Tổng mỗi tiêu đề không quá 100 ký tự; không đưa tên kênh hoặc số tập vào tiêu đề;
không tiết lộ kết thúc; không dịch hoặc sao chép tiêu đề nguồn."""

DESCRIPTION_PROMPT = """Tạo 3 mô tả YouTube tiếng Việt cho truyện audio gia đình,
mỗi mô tả 70-120 từ. Mở bằng tình huống gợi tò mò, nêu giá trị cảm xúc, mời
khán giả bình luận/đăng ký tự nhiên và kết thúc bằng 3-5 hashtag phù hợp. Không
bịa đây là chuyện thật và không tiết lộ kết thúc."""

ANALYSIS_PROMPT = """Bạn là biên tập viên chiến lược nội dung YouTube Việt Nam,
chuyên audio chuyện gia đình, tuổi già và chuyện tâm linh ông bà kể cho khán giả
45+. Chỉ lấy mô-típ và
tình huống để sáng tạo lại; không dịch hay sao chép nguyên văn nguồn. Với nguồn
Trung Quốc, hãy đọc để RÚT CHẤT LIỆU KỊCH TÍNH, sau đó diễn đạt toàn bộ kết quả
bằng tiếng Việt. Tuyệt đối không kể lại tuần tự hoặc đưa nguyên văn truyện nguồn
vào hồ sơ sáng tác. Với chuyện tâm linh, thể hiện như ký ức/truyền miệng của ông
bà, không khẳng định là sự thật khoa học, không cổ súy mê tín hoặc hành động nguy
hiểm; trọng tâm vẫn là tình thân, nhân quả và nếp sống làng quê Việt.

Hãy trả về MỘT JSON thuần, không markdown, theo đúng schema:
{
  "title_localized":"Tên làm việc tiếng Việt, cụ thể, không phải bản dịch từng chữ",
  "primary_genre":"...", "emotion":"...", "hook_score":1,
  "plot_twist_score":1, "themes":["..."], "archetypes":["..."],
  "main_hook":"Một câu mô tả móc mở đầu đáng giữ lại",
  "high_tension_scenes":["5-7 cảnh/xung đột kịch tính đáng khai thác"],
  "plot_twists":["2-4 cú lật hoặc bí mật đáng khai thác"],
  "must_change":["tên", "địa danh", "quan hệ", "số liệu", "diễn biến", "lời văn"],
  "rewrite_brief":"Hồ sơ sáng tác tiếng Việt 500-900 từ: tiền đề mới, xung đột, các nhịp leo thang, hook, cú lật và hướng nhân quả; chỉ giữ chất liệu, yêu cầu viết truyện Việt hoàn toàn mới",
  "keywords":["..."], "tags":["..."], "series":"...",
  "titles":[đúng 8 chuỗi], "descriptions":[3 chuỗi],
  "thumbnails":[{"description":"...","colors":"...","text":"..."}, ...3],
  "outline":"Phần 1: ...\nPhần 2: ...\nPhần 3: ...",
  "main_characters":["..."], "production_ease":"Cao|Trung|Thấp",
  "recommendation":"Nên làm|Chờ đợi|Không nên làm",
  "best_publish_time":"..."
}

Điểm Hook/Plot Twist là số nguyên 1-10. Tiêu đề tuân thủ prompt sau; trong lượt
này hãy thay [DÁN TÓM TẮT] bằng chính rewrite_brief vừa rút ra:
""" + TITLE_PROMPT + "\n\nMô tả tuân thủ:\n" + DESCRIPTION_PROMPT


def _browser_json_correction_prompt(response: str) -> str:
    """Yêu cầu model tự định dạng lại câu vừa trả, không phân tích nguồn lần hai."""
    previous = str(response or "").strip()
    return (
        "Câu trả lời vừa rồi chưa đọc được bằng JSON. Hãy XUẤT LẠI TOÀN BỘ "
        "kết quả phân tích thành đúng MỘT JSON object theo schema tôi đã yêu cầu. "
        "Ký tự đầu tiên phải là { và ký tự cuối cùng phải là }. Không dùng khối "
        "```json, không lời dẫn, không chú thích, không dấu phẩy thừa. Mọi xuống "
        "dòng bên trong chuỗi phải viết thành \\n. Không phân tích lại và không bỏ trường.\n\n"
        "CÂU TRẢ LỜI CẦN ĐỊNH DẠNG LẠI (có thể đang cụt — xuất lại ĐỦ trường):\n"
        + previous[:6000]
    )


def _save_invalid_ai_response(config: Dict[str, Any], provider: str,
                              model: str, response: str) -> str:
    """Giữ câu trả lời lỗi gần nhất để lần sau không phải đoán từ log."""
    cp = config.get("content_pipeline") if isinstance(
        config.get("content_pipeline"), dict) else {}
    configured = str(cp.get("output_dir") or DEFAULT_OUTPUT_DIR).strip()
    folder = os.path.abspath(
        configured if os.path.isabs(configured) else os.path.join(ROOT, configured))
    path = os.path.join(folder, "_phan_hoi_ai_json_loi.txt")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                f"Thời gian: {datetime.now().isoformat(timespec='seconds')}\n"
                f"Provider: {provider}\nModel: {model}\n\n{str(response or '')}")
        return path
    except OSError:
        return ""


def _score(value: Any, fallback: int) -> int:
    try:
        return max(1, min(10, int(round(float(value)))))
    except (TypeError, ValueError):
        return fallback


def _normalize_ai_result(ai: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(fallback)
    scalar = (
        "title_localized", "primary_genre", "emotion", "series", "outline",
        "main_hook", "rewrite_brief", "production_ease", "recommendation",
        "best_publish_time")
    for key in scalar:
        value = str(ai.get(key) or "").strip()
        if value:
            out[key] = value
    out["hook_score"] = _score(ai.get("hook_score"), fallback["hook_score"])
    out["plot_twist_score"] = _score(
        ai.get("plot_twist_score"), fallback["plot_twist_score"])
    for key, wanted in (("themes", 4), ("archetypes", 6), ("keywords", 10),
                        ("tags", 10), ("main_characters", 8),
                        ("high_tension_scenes", 7), ("plot_twists", 4),
                        ("must_change", 12)):
        values = _list_value(ai.get(key))
        if values:
            out[key] = values[:wanted]
    titles = _list_value(ai.get("titles"))
    if titles:
        out["titles"] = list(dict.fromkeys(titles + fallback["titles"]))[0:8]
    descriptions = _list_value(ai.get("descriptions"))
    if descriptions:
        out["descriptions"] = (descriptions + fallback["descriptions"])[0:3]
    thumbs = ai.get("thumbnails")
    if isinstance(thumbs, list):
        clean_thumbs = []
        for item in thumbs[:3]:
            if isinstance(item, dict):
                clean_thumbs.append({
                    "description": str(item.get("description") or "").strip(),
                    "colors": str(item.get("colors") or "").strip(),
                    "text": str(item.get("text") or "").strip(),
                })
            elif str(item).strip():
                clean_thumbs.append({"description": str(item).strip(),
                                     "colors": "", "text": ""})
        if clean_thumbs:
            out["thumbnails"] = (clean_thumbs + fallback["thumbnails"])[:3]
    hook, twist = out["hook_score"], out["plot_twist_score"]
    ease_value = {"Cao": 10, "Trung": 6, "Thấp": 3}.get(out["production_ease"], 6)
    out["priority_score"] = round(hook * .45 + twist * .40 + ease_value * .15, 1)
    out["analysis_provider"] = "ai"
    out["analysis_error"] = ""
    out["status"] = "analyzed"
    return out


_BROWSER_ANALYSIS_PROVIDERS = {"browser", "perplexity_browser"}


def _browser_profile_path(value: Any, fallback: str) -> str:
    raw = os.path.expandvars(os.path.expanduser(str(value or fallback).strip().strip('"')))
    return os.path.abspath(raw if os.path.isabs(raw) else os.path.join(ROOT, raw))


def _perplexity_tool_settings(config: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    section = config.get("tao_kich_ban") if isinstance(
        config.get("tao_kich_ban"), dict) else {}
    tool_dir = _browser_profile_path(
        section.get("tool_dir"), os.path.join(os.pardir, "Tạo kịch bản"))
    config_path = os.path.join(tool_dir, "config.json")
    settings: Dict[str, Any] = {}
    if os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            settings.update(loaded)
    cp = config.get("content_pipeline") if isinstance(
        config.get("content_pipeline"), dict) else {}
    settings.setdefault("site_url", "https://www.perplexity.ai/")
    settings.setdefault("browser_channel", "chrome")
    settings.setdefault("preferred_model", str(
        cp.get("perplexity_model") or "Claude Sonnet 5"))
    settings.setdefault("model_reasoning", True)
    settings.setdefault("answer_timeout_sec", 420)
    settings.setdefault("first_token_timeout_sec", 180)
    settings.setdefault("empty_fail_seconds", 90)
    settings.setdefault("stable_seconds", 6.0)
    settings.setdefault("poll_interval_sec", 1.5)
    profile = str(settings.get("user_data_dir") or "browser_profile_perplexity")
    settings["user_data_dir"] = os.path.abspath(
        profile if os.path.isabs(profile) else os.path.join(tool_dir, profile))
    return tool_dir, settings


@contextmanager
def _browser_analysis_session(config: Dict[str, Any], provider: str):
    """Mở một phiên web đã đăng nhập và trả hàm hỏi; không dùng API/key."""
    from .. import translate

    provider_name = str(provider or "browser").strip().lower()
    if provider_name == "browser":
        tr = config.get("translation") if isinstance(
            config.get("translation"), dict) else {}
        profile = _browser_profile_path(
            tr.get("browser_profile"), "browser_profile")
        channel = str(tr.get("browser_channel") or "msedge")
        url = str(tr.get("browser_url") or "https://gemini.google.com/app")
        wait_reply = max(60, int(tr.get("wait_reply") or 240))
        with translate.phien_gemini_trinh_duyet(
                profile, channel=channel, url=url, wait_reply=wait_reply) as ask:
            yield ask
        return

    tool_dir, settings = _perplexity_tool_settings(config)
    driver_path = os.path.join(tool_dir, "app", "perplexity.py")
    if not os.path.isfile(driver_path):
        raise RuntimeError(
            "Không thấy bộ điều khiển Perplexity tại %s" % driver_path)
    module_name = "_autodub_perplexity_browser"
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, driver_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Không nạp được bộ điều khiển Perplexity.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    driver = module.PerplexityDriver(settings, translate.log)
    try:
        driver.start()
        driver.goto_home()
        if not driver.is_logged_in():
            translate.log(
                "Perplexity chưa nhận phiên Pro; hãy đăng nhập trong cửa sổ Chrome vừa mở. "
                "Chương trình sẽ tự tiếp tục sau khi đăng nhập.",
                "warn")
            deadline = time.time() + 300
            while time.time() < deadline and not driver.is_logged_in():
                driver._sleep(3)
            if not driver.is_logged_in():
                raise RuntimeError(
                    "Sau 5 phút vẫn chưa thấy phiên đăng nhập Perplexity Pro; "
                    "không dùng tài khoản khách thay cho Claude đã chọn.")
        driver.wait_for_input(timeout=180)
        driver.ensure_model()

        def ask(prompt: str) -> str:
            return driver.ask(prompt, new_thread=True,
                              label="phân tích ý tưởng bằng Claude/Perplexity")

        yield ask
    finally:
        driver.close()


def _active_browser_session():
    """Dùng tên trên package để ``patch.object(content_pipeline, ...)`` vẫn có hiệu lực."""
    pkg = sys.modules.get("autodub.content_pipeline")
    factory = getattr(pkg, "_browser_analysis_session", None) if pkg is not None else None
    return factory if factory is not None else _browser_analysis_session


def _provider_params(config: Dict[str, Any], requested: str = "auto"
                     ) -> Tuple[str, str, str, Optional[str], int]:
    from ..providers import api_params_for_provider, nvidia_model_for_ideas
    tr = config.get("translation") if isinstance(config.get("translation"), dict) else {}
    cp = config.get("content_pipeline") if isinstance(config.get("content_pipeline"), dict) else {}
    provider = str(requested or cp.get("provider") or "auto").strip().lower()
    if provider == "auto":
        provider = str(tr.get("provider") or "browser").strip().lower()
    if provider == "browser":
        return "browser", "", "Gemini Web", None, max(
            60, int(tr.get("wait_reply") or 240))
    if provider == "perplexity_browser":
        _tool_dir, settings = _perplexity_tool_settings(config)
        model = str(settings.get("preferred_model") or "Claude trên Perplexity")
        return "perplexity_browser", "", model, None, int(
            settings.get("answer_timeout_sec") or 420)
    if provider in {"", "none", "heuristic", "offline"}:
        return provider or "heuristic", "", "", None, 420
    key, model, base_url, timeout = api_params_for_provider(tr, provider)
    if provider == "nvidia":
        fast = nvidia_model_for_ideas(tr, cp)
        if fast:
            model = fast
    return provider, str(key or ""), str(model or ""), base_url, int(timeout or 420)


def _analysis_error_message(provider: str, model: str, exc: Exception) -> str:
    """Bien loi API thanh thong bao co huong sua, khong lam lo credential."""
    raw = str(exc or "Lỗi không xác định").strip()
    lowered = raw.casefold()
    label = str(provider or "AI").upper()
    model_note = f" / {model}" if model else ""
    if any(token in lowered for token in ("401", "unauthorized", "invalid api key",
                                           "incorrect api key")):
        advice = "API key sai hoặc hết hiệu lực; mở Cài đặt → API, nhập lại key rồi Kiểm tra kết nối."
    elif any(token in lowered for token in ("403", "forbidden", "permission")):
        advice = "Tài khoản/key chưa được cấp quyền dùng model này; đổi model hoặc key."
    elif any(token in lowered for token in ("429", "rate limit", "quota", "too many")):
        advice = "API hết hạn mức hoặc bị giới hạn tốc độ; chờ rồi thử lại, hoặc đổi dịch vụ AI."
    elif any(token in lowered for token in ("404", "model_not_found", "model not found")):
        advice = "Không tìm thấy model; kiểm tra lại tên model trong Cài đặt → API."
    elif any(token in lowered for token in ("timeout", "timed out", "time out")):
        advice = "API chờ quá lâu; kiểm tra mạng, giảm số tác vụ đồng thời hoặc đổi dịch vụ AI."
    elif any(token in lowered for token in ("connection", "dns", "ssl", "network")):
        advice = "Không kết nối được dịch vụ AI; kiểm tra mạng, proxy/tường lửa rồi thử lại."
    elif "json" in lowered:
        advice = "AI trả sai định dạng; thử lại hoặc đổi model ổn định hơn."
    else:
        advice = "Mở Cài đặt → API, Kiểm tra kết nối; nếu vẫn lỗi hãy đổi model/dịch vụ AI."
    return f"{label}{model_note} lỗi: {raw[:220]}. Cách sửa: {advice}"


def analyze_record(record: Dict[str, Any], config: Optional[Dict[str, Any]] = None,
                   use_ai: bool = True, provider: str = "auto",
                   progress: Optional[Callable[[str, str, float], None]] = None,
                   browser_ask: Optional[Callable[[str], str]] = None
                   ) -> Dict[str, Any]:
    def notify(stage: str, message: str, pct: float) -> None:
        if progress:
            progress(stage, message, max(0.0, min(100.0, float(pct))))

    base = heuristic_analysis(record)
    merged = dict(record)
    if not use_ai:
        notify("offline", "Đang phân tích offline (không gọi API AI).", 40)
        merged.update(base)
        notify("done", "Đã phân tích offline.", 100)
        return merged
    cfg = config or {}
    provider_name, api_key, model, base_url, timeout = _provider_params(cfg, provider)
    is_browser = provider_name in _BROWSER_ANALYSIS_PROVIDERS
    if not api_key and not is_browser:
        if provider_name == "nvidia":
            base["analysis_error"] = (
                "NVIDIA chưa có API key trong Cài đặt > API; "
                "đã dùng phân tích offline.")
        else:
            base["analysis_error"] = (
                "Không có API key cho provider %s; đã dùng phân tích offline."
                % provider_name)
        merged.update(base)
        notify("warning", base["analysis_error"], 100)
        return merged
    content = str(record.get("content") or record.get("raw_content") or "")
    if len(content) <= 24000:
        excerpt = content
    else:
        middle = max(8000, len(content) // 2 - 4000)
        excerpt = (content[:8000] + "\n\n[ĐOẠN GIỮA NGUỒN]\n" +
                   content[middle:middle + 8000] +
                   "\n\n[ĐOẠN CUỐI NGUỒN]\n" + content[-8000:])
    prompt = (
        ANALYSIS_PROMPT + "\n\nDỮ LIỆU NGUỒN:\n"
        + json.dumps({
            "title": record.get("title_localized") or record.get("title_original"),
            "source": record.get("source"),
            "language": record.get("language"),
            "word_count": record.get("word_count"),
            "content_excerpt_begin_middle_end": excerpt,
        }, ensure_ascii=False)
    )
    try:
        from .. import translate
        ai_label = str(provider_name or "AI").upper()
        notify("sending", f"Đang gửi nội dung tới {ai_label} · model {model or 'mặc định'}…", 20)
        notify("waiting", f"Đang chờ {ai_label} trả hook, plot twist và 8 tiêu đề…", 45)
        heartbeat_stop = threading.Event()
        wait_started = time.monotonic()

        def heartbeat() -> None:
            while not heartbeat_stop.wait(20):
                waited = int(time.monotonic() - wait_started)
                notify("waiting", (f"Vẫn đang chờ {ai_label} · {model or 'model mặc định'} "
                                   f"phản hồi ({waited} giây)…"), 45)

        if progress:
            threading.Thread(target=heartbeat, daemon=True).start()
        response = ""

        def parse_or_retry(ask_fn, first_response: str) -> Dict[str, Any]:
            nonlocal response
            response = first_response
            notify("parsing", f"{ai_label} đã trả lời; đang kiểm tra JSON và lưu kết quả…", 85)
            try:
                return _extract_json(response)
            except ValueError as first_parse_error:
                cp = cfg.get("content_pipeline") if isinstance(
                    cfg.get("content_pipeline"), dict) else {}
                try:
                    configured_retries = int(cp.get("browser_json_retries", 1) or 0)
                except (TypeError, ValueError):
                    configured_retries = 1
                json_retries = max(0, min(2, configured_retries))
                if not is_browser or json_retries <= 0 or ask_fn is None:
                    raise
                last_parse_error: Exception = first_parse_error
                for retry_index in range(json_retries):
                    notify(
                        "parsing",
                        f"{ai_label} trả chưa đúng JSON; đang yêu cầu xuất lại "
                        f"({retry_index + 1}/{json_retries})…",
                        88,
                    )
                    response = ask_fn(_browser_json_correction_prompt(response))
                    try:
                        return _extract_json(response)
                    except ValueError as retry_error:
                        last_parse_error = retry_error
                debug_path = _save_invalid_ai_response(
                    cfg, provider_name, model, response)
                suffix = f" Phản hồi lỗi đã lưu tại: {debug_path}" if debug_path else ""
                message = str(last_parse_error).rstrip(".") + "."
                raise ValueError(f"{message}{suffix}") from last_parse_error

        try:
            if is_browser:
                if browser_ask is not None:
                    parsed_response = parse_or_retry(browser_ask, browser_ask(prompt))
                else:
                    with _active_browser_session()(cfg, provider_name) as ask:
                        parsed_response = parse_or_retry(ask, ask(prompt))
            else:
                response = translate._api_call(
                    prompt, api_key, model, 0.25, provider=provider_name,
                    api_base_url=base_url,
                    # Phân tích một ý tưởng có heuristic dự phòng. NVIDIA NIM hay
                    # kẹt TCP; hạn tường Flash 45s nên 3 lần gọi lại ~2 phút,
                    # không còn 5×504 thành 25 phút như trước.
                    api_timeout=min(int(timeout or 420), 180),
                    api_retries=3 if provider_name == "nvidia" else None)
                parsed_response = parse_or_retry(None, response)
        finally:
            heartbeat_stop.set()
        analysis = _normalize_ai_result(parsed_response, base)
        analysis["analysis_provider"] = f"{provider_name}:{model}"
        notify("done", f"{ai_label} · {model or 'model mặc định'} phân tích thành công.", 100)
    except Exception as exc:
        analysis = base
        analysis["analysis_error"] = _analysis_error_message(
            provider_name, model, exc) + " Đã dùng kết quả offline thay thế."
        analysis["analysis_provider"] = "heuristic-fallback"
        notify("error", analysis["analysis_error"], 100)
    merged.update(analysis)
    return merged


async def analyze_many(records: Sequence[Dict[str, Any]], config: Dict[str, Any],
                       use_ai: bool = True, provider: str = "auto",
                       concurrency: int = 3,
                       progress: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
                       activity: Optional[Callable[[int, int, str, str, float], None]] = None
                       ) -> List[Dict[str, Any]]:
    resolved_provider = _provider_params(config, provider)[0]
    if use_ai and resolved_provider in _BROWSER_ANALYSIS_PROVIDERS:
        def browser_batch() -> List[Dict[str, Any]]:
            results: List[Dict[str, Any]] = []
            try:
                with _active_browser_session()(config, resolved_provider) as ask:
                    for index, source in enumerate(records):
                        def report(stage: str, message: str, pct: float,
                                   item_index: int = index) -> None:
                            if activity:
                                activity(item_index + 1, len(records), stage, message, pct)

                        result = analyze_record(
                            dict(source), config, True, resolved_provider, report,
                            browser_ask=ask)
                        results.append(result)
                        if progress:
                            progress(len(results), len(records), result)
                return results
            except Exception as exc:
                # Không mở được profile/Playwright vẫn phải giữ đủ kết quả local.
                failed: List[Dict[str, Any]] = []
                for index, source in enumerate(records):
                    result = analyze_record(
                        dict(source), config, use_ai=False, provider="heuristic")
                    result["analysis_provider"] = "heuristic-fallback"
                    result["analysis_error"] = _analysis_error_message(
                        resolved_provider, _provider_params(config, resolved_provider)[2],
                        exc) + " Đã dùng kết quả offline thay thế."
                    failed.append(result)
                    if activity:
                        activity(index + 1, len(records), "error",
                                 result["analysis_error"], 100)
                    if progress:
                        progress(index + 1, len(records), result)
                return failed

        return await asyncio.to_thread(browser_batch)

    sem = asyncio.Semaphore(max(1, min(8, int(concurrency or 3))))
    done = 0
    lock = asyncio.Lock()

    async def one(index: int, record: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal done
        def report(stage: str, message: str, pct: float) -> None:
            if activity:
                activity(index + 1, len(records), stage, message, pct)

        async with sem:
            result = await asyncio.to_thread(
                analyze_record, record, config, use_ai, provider, report)
        async with lock:
            done += 1
            if progress:
                progress(done, len(records), result)
        return result

    return list(await asyncio.gather(*(
        one(index, dict(record)) for index, record in enumerate(records))))
