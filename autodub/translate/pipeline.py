from __future__ import annotations

import json
from typing import Dict, List, Optional

from .api import _api_call
from .cache import ChunkCache
from .const import essay_hit_count, looks_like_document_vi
from .parse import (
    _bad_line_summary, _cjk_line_numbers, _clean_vi_lines, _contains_cjk,
    _parse_json_lines, char_budget, shorten_long_lines, spoken_header,
)
from ..srt_utils import Segment, normalize_vi_subtitle_text
from ..utils import log


def _vi_from_api_raw(raw: str, expected: int) -> tuple:
    """Parse JSON dịch. Trả (dòng sạch, keep từng index nếu còn lẫn chữ Hán)."""
    vi = _parse_json_lines(raw, expected)
    if vi is None:
        return None, {}
    vi = _clean_vi_lines(vi)
    bad = _cjk_line_numbers(vi)
    if not bad:
        return vi, {}
    keep = {k: t for k, t in enumerate(vi) if t and not _contains_cjk(t)}
    return None, keep


def translate_segments(
    segments: List[Segment],
    api_key: str,
    model: str = "gemini-3.6-flash",
    provider: str = "gemini",
    api_base_url: Optional[str] = None,
    chunk_size: int = 40,
    temperature: float = 0.35,
    context_lines: int = 3,
    cache_path: Optional[str] = None,
    chars_per_sec: float = 0.0,
    name_hint: str = "",
    film_hint: str = "",
    api_timeout: int = 420,
    shorten_long_lines_enabled: bool = True,
    translation_cfg: Optional[Dict] = None,
) -> List[Segment]:
    """Dịch tại chỗ: gán segment.text = bản tiếng Việt. Trả lại chính list đó."""
    provider_key = str(provider or "gemini").lower()
    if not api_key:
        if provider_key == "xkiro":
            name = "Xkiro API key"
        elif provider_key.startswith("tokenrouter"):
            name = "TOKENROUTER_API_KEY"
        elif provider_key == "nvidia":
            name = "NVIDIA API key (nvapi-..., tạo free tại build.nvidia.com)"
        elif provider_key == "zenmux":
            name = "ZenMux API key (tạo tại zenmux.ai)"
        elif provider_key == "zai":
            name = "Z.AI API key (tạo tại z.ai/manage-apikey)"
        elif provider_key == "tokenharbor":
            name = "TokenHarbor API key (thk_live_..., tạo tại tokenharbor.ai)"
        else:
            name = "GEMINI_API_KEY"
        raise ValueError(f"Chưa có {name}. Điền key trong GUI/config.yaml hoặc chọn chế độ browser.")
    from ..semantic import enabled
    if enabled(translation_cfg):
        from .semantic import translate_semantic
        return translate_semantic(
            segments, lambda prompt: _api_call(prompt, api_key, model, temperature,
                                               provider, api_base_url, api_timeout, allow_model_fallback=False),
            translation_cfg, cache_path=cache_path,
            identity=[provider, model, api_base_url, temperature],
            film_hint=film_hint, name_hint=name_hint)
    chunk_size = max(1, int(chunk_size or 40))
    if provider_key == "tokenrouter" and chunk_size > 20:
        log(f"TokenRouter free de timeout voi lo lon; giam chunk_size {chunk_size} -> 20 dong/luot.", "warn")
        chunk_size = 20
    log(f"Dich qua API provider={provider_key}, model='{model}', chunk_size={chunk_size}.", "info")
    lock = spoken_header(film_hint, name_hint)
    escape_note = (
        "QUAN TRONG voi TokenRouter: Tra ve JSON hop le va moi ky tu khong thuoc ASCII "
        "trong ban dich tieng Viet phai viet bang escape JSON \\\\uXXXX "
        "(vi du: \"Xin ch\\\\u00e0o\"). Khong de ky tu co dau truc tiep trong output.\n\n"
        if provider_key == "tokenrouter" else ""
    )

    cache = ChunkCache(cache_path)
    n = len(segments)
    total_chunks = max(1, (n + chunk_size - 1) // chunk_size)
    done = 0
    long_lines = 0                 # số dòng vẫn dài hơn nhịp lồng tiếng sau rút gọn
    prev_context: List[str] = []   # vài dòng dịch trước đó để giữ mạch

    for i in range(0, n, chunk_size):
        chunk = segments[i:i + chunk_size]
        src_lines = []
        for s in chunk:
            spk = f"[{s.speaker}] " if s.speaker else ""
            src_lines.append(f"{spk}{s.text}")

        ckey = ChunkCache.key(i, src_lines)
        vi = cache.get(ckey)
        if vi is not None:
            cleaned_vi = _clean_vi_lines(vi)
            if cleaned_vi != vi:
                cache.put(ckey, cleaned_vi)
            vi = cleaned_vi
            bad = _cjk_line_numbers(vi)
            if bad:
                log(f"Cache lô {i//chunk_size+1}/{total_chunks} còn tiếng Trung "
                    f"ở dòng {_bad_line_summary(bad)} -> bỏ cache và dịch lại.", "warn")
                cache.discard(ckey)
                vi = None

        if vi is None or len(vi) != len(chunk):
            log(f"(api:{provider_key}) lo {i//chunk_size+1}/{total_chunks} - dang goi {model}...", "info")
            ctx = ""
            if prev_context:
                ctx = ("Ngữ cảnh (các câu tiếng Việt VỪA dịch trước đó, để giữ mạch & "
                       "xưng hô nhất quán):\n- " + "\n- ".join(prev_context[-context_lines:]) + "\n\n")

            cps = max(0.0, float(chars_per_sec or 0.0))
            budget_note = ""
            payload = src_lines
            if cps > 0:
                budget_note = (
                    " Moi dong co truong max_chars: day la GIOI HAN THAT ve so "
                    "ky tu, vi giong doc chi co dung bay nhieu thoi gian. Dong "
                    "nao dai hon se bi doc voi roi cat cut giua chung. Hay viet "
                    "gon, khau ngu, bo tu dem va trang tu thua; van giu nguyen "
                    "ten rieng, con so, chu-vi va quan he nhan vat. Chi vuot "
                    "gioi han khi khong con cach nao dien dat du nghia.\n"
                )
                payload = [
                    {"text": line, "max_chars": char_budget(seg, cps)}
                    for line, seg in zip(src_lines, chunk)
                ]

            from ..vi_reflow import prompt_group_hint
            group_note = prompt_group_hint(chunk)
            prompt = (
                lock +
                ctx +
                escape_note +
                f"Dịch thành THOẠI LỒNG TIẾNG tiếng Việt {len(src_lines)} dòng "
                f"phụ đề dưới đây. Mỗi phần tử JSON = một nhịp đọc trên màn hình, "
                f"không viết thành đoạn văn hay bài báo. "
                f"Trả về JSON array gồm ĐÚNG {len(src_lines)} chuỗi, cùng thứ tự. "
                "Không dùng dấu ba chấm (... hoặc …); nếu câu bị cắt mảnh thì "
                "dịch cả câu Việt tự nhiên rồi chia vào từng phần tử, ngắt ở "
                "dấu phẩy/mệnh đề, không ngắt '... tôi' / 'đã...', không lặp chữ.\n"
                + group_note + "\n"
                + budget_note + "\n"
                + json.dumps(payload, ensure_ascii=False)
            )

            raw = _api_call(prompt, api_key, model, temperature, provider,
                            api_base_url, api_timeout)
            vi, keep = _vi_from_api_raw(raw, len(chunk))
            if vi is None and keep:
                bad = [n + 1 for n in range(len(chunk)) if n not in keep]
                log(f"Lô {i//chunk_size+1}: API trả còn tiếng Trung ở dòng "
                    f"{_bad_line_summary(bad)} -> giữ dòng sạch, dịch lại "
                    "đúng các dòng đó.", "warn")
            elif vi is None:
                # Cả lô còn Hán/không parse: gọi lại CẢ LÔ, prompt gọn (mảng
                # chuỗi, không object max_chars) vì NIM hay echo đúng field text.
                log(f"Lô {i//chunk_size+1}: lô đầu chưa sạch, dịch lại cả lô một lần...", "warn")
                retry_prompt = (
                    lock + ctx + escape_note +
                    f"Dịch thành THOẠI LỒNG TIẾNG ĐÚNG {len(src_lines)} dòng phụ đề. "
                    "Trả về JSON array gồm đúng số chuỗi tiếng Việt, cùng thứ tự. "
                    "CẤM trả lại chữ Hán/Trung; cấm chép nguyên văn nguồn; "
                    "cấm viết thành đoạn văn. Tên riêng phiên âm chữ Latin.\n"
                    + json.dumps(src_lines, ensure_ascii=False)
                )
                raw = _api_call(retry_prompt, api_key, model, temperature, provider,
                                api_base_url, api_timeout)
                vi, keep = _vi_from_api_raw(raw, len(chunk))
                if vi is None and keep:
                    bad = [n + 1 for n in range(len(chunk)) if n not in keep]
                    log(f"Lô {i//chunk_size+1}: API trả còn tiếng Trung ở dòng "
                        f"{_bad_line_summary(bad)} -> giữ dòng sạch, dịch lại "
                        "đúng các dòng đó.", "warn")

            if vi is None:
                # Thử lại từng dòng để không vỡ số lượng
                if not keep:
                    log(f"Lô {i//chunk_size+1}: số dòng không khớp, dịch lại từng câu...", "warn")
                vi = []
                for line_no, s in enumerate(chunk, 1):
                    if line_no - 1 in keep:
                        vi.append(keep[line_no - 1])
                        continue
                    translated = ""
                    last_one = ""
                    for retry in range(3):
                        if retry == 0:
                            ask = (
                                lock + escape_note +
                                "Dịch 1 dòng phụ đề sau thành THOẠI LỒNG TIẾNG tiếng Việt, "
                                "khẩu ngữ nghe được, câu liền mạch, không dùng dấu ba chấm "
                                "(... hoặc …), không viết như bài báo. "
                                "BẮT BUỘC trả về JSON array có đúng 1 chuỗi tiếng Việt. "
                                "Kết quả KHÔNG được chứa chữ Hán/Trung; tên riêng phải "
                                "phiên âm hoặc Việt hoá bằng chữ Latin. Nếu nguồn chỉ là "
                                "tiếng đệm/tiếng cười như 嗯, 嘿嘿 thì chuyển thành cách "
                                "nói/ngắt tiếng Việt tự nhiên.\nNguồn: "
                                + json.dumps([s.text], ensure_ascii=False)
                            )
                        else:
                            ask = (
                                lock + escape_note +
                                "Lần trước bạn trả chữ Hán hoặc không đúng JSON. "
                                "Chỉ dịch thành thoại tiếng Việt, JSON array đúng 1 chuỗi, "
                                "không chữ Hán, không chép nguồn, không viết bài báo.\nNguồn: "
                                + json.dumps([s.text], ensure_ascii=False)
                            )
                        r = _api_call(
                            ask, api_key, model, temperature, provider,
                            api_base_url, api_timeout)
                        one = _parse_json_lines(r, 1)
                        last_one = normalize_vi_subtitle_text(one[0] if one else "")
                        if last_one and not _contains_cjk(last_one):
                            translated = last_one
                            break
                        log(f"Lô {i//chunk_size+1}, dòng {line_no}: bản dịch vẫn còn "
                            "tiếng Trung, thử lại...", "warn")
                    if not translated:
                        sample = (last_one or s.text or "")[:80]
                        raise RuntimeError(
                            f"Lô {i//chunk_size+1}, dòng {line_no} vẫn chưa dịch sang "
                            f"tiếng Việt sạch sau khi thử lại: {sample}")
                    vi.append(translated)

            if vi and looks_like_document_vi(vi):
                log(f"Lô {i//chunk_size+1}: nghi dịch kiểu văn bản/bài báo "
                    "-> hỏi lại 1 lần theo thoại lồng tiếng.", "warn")
                retry_spoken = (
                    lock + ctx + escape_note +
                    "Bản vừa rồi bị dịch kiểu VĂN BẢN/bài báo. Làm lại thành "
                    "THOẠI LỒNG TIẾNG: khẩu ngữ, mỗi phần tử JSON một nhịp đọc, "
                    "không nối nghị luận. "
                    f"Trả về JSON array ĐÚNG {len(src_lines)} chuỗi, cùng thứ tự.\n"
                    + json.dumps(payload, ensure_ascii=False)
                )
                try:
                    raw2 = _api_call(retry_spoken, api_key, model, temperature,
                                     provider, api_base_url, api_timeout)
                    vi2, _keep2 = _vi_from_api_raw(raw2, len(chunk))
                    if vi2 and (not looks_like_document_vi(vi2)
                                or essay_hit_count(vi2) < essay_hit_count(vi)):
                        vi = vi2
                except Exception as e:
                    log(f"Không hỏi lại được lô văn bản ({e}) — giữ bản vừa có.",
                        "warn")

            # Mốc độ dài trong prompt chỉ là gợi ý và model thường vượt. Đo trên
            # 4 video thật: đặt 15 ký tự/giây nhưng bản dịch ra 21-23. Ép lại ở
            # đây, trước khi ghi cache, để lần chạy sau không dùng lại bản dài.
            if shorten_long_lines_enabled and cps > 0 and vi:
                got = {k + 1: t for k, t in enumerate(vi)}
                still = shorten_long_lines(
                    chunk, got, cps,
                    lambda prompt: _api_call(prompt, api_key, model, temperature,
                                             provider, api_base_url, api_timeout),
                    label=f"Lô {i//chunk_size+1}/{total_chunks}: ")
                vi = [got.get(k + 1, vi[k]) for k in range(len(vi))]
                long_lines += still

            cache.put(ckey, vi)

        for s, t in zip(chunk, vi):
            if t:
                s.text = normalize_vi_subtitle_text(t)
        prev_context.extend([t for t in vi if t])
        del prev_context[:-8]        # chỉ dùng vài dòng cuối làm ngữ cảnh
        done += len(chunk)
        log(f"Đã dịch {done}/{n} dòng", "info")

    log("Dịch xong.", "ok")
    if long_lines:
        log(f"Còn {long_lines}/{n} dòng dài hơn thời lượng cho phép - bước lồng "
            "tiếng sẽ tự tăng tốc đọc để bù.", "warn")
    if translation_cfg is not None:
        from ..vi_cues import finalize_spoken_vi_cues

        def _ask(prompt: str) -> str:
            return _api_call(prompt, api_key, model, temperature, provider,
                             api_base_url, api_timeout)

        try:
            n_clean = finalize_spoken_vi_cues(segments, translation_cfg, _ask)
            if n_clean:
                log(f"Đã chia lại {n_clean} dòng SRT Việt theo ngữ pháp "
                    "(giữ mốc).", "ok")
        except InterruptedError:
            raise
        except Exception as exc:
            log(f"Chia lại SRT Việt lỗi ({exc}); giữ bản dịch 1-1.", "warn")
    return segments
