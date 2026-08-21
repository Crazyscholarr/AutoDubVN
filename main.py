#!/usr/bin/env python3
"""AutoDubVN - Tự động lồng tiếng Việt cho video.

Chạy:  python main.py            (sẽ hỏi đường dẫn video)
   hoặc python main.py "E:\\Video\\phim.mp4"

Quy trình: tách audio -> nhận phụ đề (ASR) -> (tùy chọn tách nhân vật) ->
dịch sang tiếng Việt -> tổng hợp giọng + chống đè thoại -> ghép & render.
"""
from __future__ import annotations

import os
import shutil
import sys
import time

# Cho phép chạy trực tiếp không cần cài như package
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from autodub import (srt_utils, asr, translate, tts, video, diarize, downloader,
                     overlays, speechmap)
from autodub.utils import (log, require, ffprobe_duration, has_nvenc,
                           ffprobe_has_stream, ffprobe_is_blank_video,
                           ffprobe_video_size, start_file_log, C,
                           ffmpeg_dir_to_path)


def load_config(path: str) -> dict:
    try:
        import yaml
    except ModuleNotFoundError:
        print("[x] Thiếu thư viện. Kích hoạt venv rồi cài gói nhẹ:\n"
              "    venv\\Scripts\\activate\n"
              "    python -m pip install pyyaml edge-tts yt-dlp playwright\n"
              "  (ASR cài thêm: python -m pip install \"funasr!=1.3.9\" modelscope "
              "faster-whisper)")
        raise SystemExit(1)
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            
        # Đọc file .env đơn giản (không thêm thư viện)
        env_path = os.path.join(os.path.dirname(path), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as ef:
                for line in ef:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip().strip("'\"")
        
        # Hỗ trợ đọc API key từ biến môi trường nếu trong YAML rỗng
        if "translation" in cfg:
            tr = cfg["translation"]
            if not tr.get("gemini_api_key"):
                tr["gemini_api_key"] = os.environ.get("GEMINI_API_KEY", "")
            if not tr.get("tokenrouter_api_key"):
                tr["tokenrouter_api_key"] = os.environ.get("TOKENROUTER_API_KEY", "")
            if not tr.get("tokenrouter_gemini_api_key"):
                tr["tokenrouter_gemini_api_key"] = os.environ.get("TOKENROUTER_GEMINI_API_KEY", "")
            if not tr.get("inferx_api_key"):
                tr["inferx_api_key"] = os.environ.get("INFERX_API_KEY", "")
            if not tr.get("nvidia_api_key"):
                tr["nvidia_api_key"] = os.environ.get("NVIDIA_API_KEY", "")
                
        return cfg
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (dòng {mark.line + 1})" if mark else ""
        print(f"\n[x] config.yaml bị lỗi cú pháp{where}: {getattr(e, 'problem', e)}\n")
        print("  Nguyên nhân hay gặp nhất: đường dẫn Windows đặt trong NHÁY KÉP.")
        print("  Trong YAML, dấu \\ trong nháy kép là ký tự thoát nên đường dẫn bị vỡ.\n")
        print("  SAI :  ffmpeg_dir: \"D:\\ffmpeg\\bin\"")
        print("  ĐÚNG:  ffmpeg_dir: 'D:\\ffmpeg\\bin'      <- nháy ĐƠN")
        print("  hoặc:  ffmpeg_dir: \"D:\\\\ffmpeg\\\\bin\"   <- nháy kép thì phải gấp đôi \\\n")
        raise SystemExit(1)


def ask_video_path() -> str:
    print(f"\n{C.B}==> Nhập đường dẫn video HOẶC link (Bilibili/YouTube...){C.E}")
    print(f"{C.DIM}   (kéo-thả file vào cửa sổ, hoặc dán link https://...){C.E}")
    raw = input("   Video/Link: ").strip().strip('"').strip("'")
    return raw


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = load_config(os.path.join(here, "config.yaml"))

    # Ưu tiên ffmpeg do người dùng chỉ định (bản static) để né lỗi DLL/WDAC.
    fdir = (cfg.get("ffmpeg_dir") or "").strip()
    if fdir:
        ffmpeg_dir_to_path(fdir)

    require("ffmpeg", "Tải bản STATIC (ffmpeg-release-essentials.zip) ở https://www.gyan.dev/ffmpeg/builds/ rồi điền ffmpeg_dir trong config.yaml.")
    require("ffprobe", "ffprobe đi kèm ffmpeg (cùng thư mục bin).")

    # 1) Đường dẫn video HOẶC link
    video_path = sys.argv[1] if len(sys.argv) > 1 else ask_video_path()

    if downloader.is_url(video_path):
        video_path = downloader.extract_url(video_path)
        dl = cfg.get("download", {})
        video_path = downloader.download_video(
            video_path, os.path.join(here, "downloads"),
            quality=dl.get("quality", "best"),
            cookies_from_browser=dl.get("cookies_from_browser"),
            cookies_file=dl.get("cookies_file"),
            concurrent_fragments=dl.get("concurrent_fragments", 8),
            external_downloader=dl.get("external_downloader", "auto"),
        )

    if not video_path or not os.path.isfile(video_path):
        log(f"Không tìm thấy file: {video_path!r}", "err")
        return 1

    stem = os.path.splitext(os.path.basename(video_path))[0]
    out_dir = os.path.join(here, cfg["output"]["dir"], stem)
    tmp_dir = os.path.join(out_dir, "_tmp")
    clips_dir = os.path.join(tmp_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)

    try:
        # Ghi lại TOÀN BỘ nhật ký lần chạy này ra file để xem lại / gửi khi có lỗi.
        log_path = os.path.join(out_dir, f"{stem}.quy_trinh.log")
        if start_file_log(log_path):
            log(f"Nhật ký phiên chạy được lưu vào: {log_path}", "info")

        src_srt = os.path.join(out_dir, f"{stem}.src.srt")
        vi_srt = os.path.join(out_dir, f"{stem}.vi.srt")
        dub_wav = os.path.join(tmp_dir, "dub.wav")
        audio_wav = os.path.join(tmp_dir, "audio16k.wav")
        suffix = cfg["output"].get("suffix", ".vietsub_dub")
        suffix = ".vietsub_dub" if suffix is None else str(suffix)
        final_out = os.path.join(out_dir, f"{stem}{suffix}.mp4")

        t0 = time.time()
        duration = ffprobe_duration(video_path)
        if duration <= 0:
            log(f"Không đọc được thời lượng video (file hỏng?): {video_path}", "err")
            log("Nếu tiếp tục, mọi mốc thời gian sẽ bị kẹp về 0 và cả track thoại "
                "chồng lên nhau - nên dừng ngay tại đây.", "err")
            return 1
        log(f"Video: {stem}  |  dài {duration/60:.1f} phút  |  NVENC: {'CÓ' if has_nvenc() else 'KHÔNG'}", "info")
        if duration >= 2 * 3600:
            log("Video dài: nếu bật ghi cứng phụ đề/làm mờ/xoá logo thì phải mã hoá "
                "lại toàn bộ hình nên sẽ lâu. Nhanh nhất cho phim nhiều giờ là tắt "
                "hardsub/blur/delogo và xuất SRT rời; khi chỉ thay audio, backend "
                "MP4Box sẽ copy video thay vì render lại.", "info")

        # Kiểm tra sớm file có ĐỦ cả hình lẫn tiếng - tránh để ffmpeg crash khó hiểu
        # ở giữa chừng (hay gặp với video tải từ Bilibili bằng công cụ khác, chỉ lấy
        # được 1 trong 2 luồng do DASH tách riêng hình/tiếng).
        if not ffprobe_has_stream(video_path, "a"):
            log(f"File KHÔNG CÓ luồng tiếng (audio): {video_path}", "err")
            log("Thường do tải bằng công cụ khác chỉ lấy luồng hình (Bilibili tách "
                "riêng DASH hình/tiếng). Hãy tải lại file gốc đủ tiếng, hoặc dán "
                "thẳng link Bilibili vào chương trình để nó tự tải bằng yt-dlp "
                "(đã có sẵn kiểm tra đủ hình+tiếng).", "err")
            return 1
        if not ffprobe_has_stream(video_path, "v"):
            log(f"File KHÔNG CÓ luồng hình (video): {video_path}", "err")
            log("Hãy tải lại file gốc đủ hình, hoặc dán thẳng link Bilibili vào "
                "chương trình để nó tự tải bằng yt-dlp.", "err")
            return 1
        if ffprobe_is_blank_video(video_path):
            log(f"File CÓ luồng hình nhưng nội dung TOÀN MÀU ĐEN: {video_path}", "err")
            log("Thường do Bilibili trả 'hình giả' (placeholder đen) kèm tiếng thật "
                "khi độ phân giải yêu cầu bị khoá sau đăng nhập/VIP - dù luồng vẫn "
                "khai đúng kích thước nên không phát hiện được ở bước trên. Hãy dán "
                "thẳng link Bilibili + bật download.cookies_from_browser trong "
                "config.yaml (trình duyệt bạn đã đăng nhập Bilibili) rồi tải lại.", "err")
            return 1

        # 2) ASR -> phụ đề gốc
        a = cfg["asr"]
        if a.get("reuse_existing") and os.path.exists(src_srt):
            log(f"Dùng lại phụ đề gốc: {src_srt}", "ok")
            # Bản đồ thoại phải có TRƯỚC khi gom/chia lại phụ đề, nếu không mọi mốc
            # thời gian lại được nội suy theo tỉ lệ ký tự và voice trượt khỏi hình.
            asr.ensure_speech_map(out_dir, stem, video_path=video_path)
            segments = srt_utils.load_srt_file(src_srt)
            # Dùng lại file cũ thì không còn kết quả nhận diện ngôn ngữ của ASR ->
            # đoán từ chính nội dung, để bước sau biết là DỊCH hay SỬA LỖI.
            src_lang = (a.get("source_language")
                        or asr.guess_language(segments) or "auto")
            before_norm = len(segments)
            max_chars = 34 if str(src_lang).lower()[:2] in {"zh", "ja", "ko"} else 80
            segments = asr.normalize_segments(segments, max_chars)
            if len(segments) != before_norm:
                log(f"Đã gom/sửa lại phụ đề gốc cũ: {before_norm} -> {len(segments)} dòng.", "ok")
                srt_utils.save_srt_file(src_srt, segments)
            # File cũ có thể còn câu BỊA từ lần nhận diện trước - lọc luôn, nếu
            # không chúng sẽ được dịch rồi ĐỌC TO trong video lồng tiếng.
            if a.get("filter_hallucinations", True):
                segments, removed = asr.drop_hallucinations(segments)
                if removed:
                    log(f"Đã bỏ {len(removed)} dòng nghi là câu BỊA trong file cũ:", "warn")
                    for line in removed[:5]:
                        print(f"      - {line}")
                    if len(removed) > 5:
                        print(f"      ... và {len(removed) - 5} dòng nữa")
                    srt_utils.save_srt_file(src_srt, segments)
            # Bảng sửa lỗi nghe nhầm cũng áp cho file cũ - thêm luật mới vào
            # config.yaml rồi chạy lại là sửa được ngay, khỏi nhận diện lại.
            if a.get("corrections"):
                nfix = asr.apply_corrections(segments, a["corrections"])
                if nfix:
                    log(f"Đã sửa {nfix} dòng theo bảng asr.corrections.", "ok")
                    srt_utils.save_srt_file(src_srt, segments)
        else:
            log("Tách audio...", "step")
            audio_wav = video.ensure_audio(video_path, audio_wav,
                                           loudnorm=a.get("loudnorm", True))
            log(f"Nhận diện phụ đề (backend={a['backend']})...", "step")
            segments, src_lang = asr.transcribe(
                audio_wav, backend=a["backend"], language=a.get("source_language"),
                model_size=a.get("model_size", "large-v3"), device=a["device"],
                compute_type=a["compute_type"], batch_size=a.get("batch_size", 16),
                rescue_gaps=a.get("rescue_gaps", True),
                min_gap_seconds=a.get("min_gap_seconds", 25),
                max_rescue_rounds=a.get("max_rescue_rounds", 2),
                silence_db=a.get("silence_db", -45),
                audio_gap_rescue=a.get("audio_gap_rescue", True),
                speech_gap_seconds=a.get("speech_gap_seconds", 1.2),
                speech_silence_db=a.get("speech_silence_db", -42),
                speech_min_silence=a.get("speech_min_silence", 0.35),
                min_coverage=a.get("min_coverage", 0.35),
                fallback_backend=a.get("fallback_backend", "faster-whisper"),
                filter_hallucinations=a.get("filter_hallucinations", True),
                corrections=a.get("corrections"),
                vocab_hint=a.get("vocab_hint"),
            )
            srt_utils.save_srt_file(src_srt, segments)
            sm = speechmap.get_active()
            if sm is not None:
                sm.save(speechmap.default_path(out_dir, stem))
            log(f"Đã lưu phụ đề gốc: {src_srt}", "ok")
            print(f"\n{C.Y}  → Hãy MỞ FILE NÀY KIỂM TRA trước khi dịch:{C.E} {src_srt}\n")

        if not segments:
            log("Không có phụ đề nào được tạo. Dừng.", "err")
            return 1

        # 3) Tách nhân vật (tùy chọn)
        d = cfg.get("diarization", {})
        if d.get("enabled"):
            if not os.path.exists(audio_wav):
                audio_wav = video.ensure_audio(video_path, audio_wav)
            diarize.diarize(audio_wav, segments, d.get("hf_token", ""),
                            device=a["device"], num_speakers=d.get("num_speakers"))

        # 4) Dịch sang tiếng Việt
        tr = cfg["translation"]
        prep_source = bool(tr.get("split_on_punctuation", True)
                           or tr.get("merge_source_fragments", True))
        if prep_source:
            before_prep = len(segments)
            segments = srt_utils.prepare_source_segments_for_translation(
                segments,
                split_on_punctuation=bool(tr.get("split_on_punctuation", True)),
                merge_fragments=bool(tr.get("merge_source_fragments", True)),
                max_chars=int(tr.get("source_merge_max_chars", 180) or 180),
                min_chars=int(tr.get("source_merge_min_chars", 24) or 24),
                max_gap=float(tr.get("source_merge_max_gap", 1.2) or 1.2),
                max_duration=float(tr.get("source_merge_max_duration", 18.0) or 18.0),
            )
            if len(segments) != before_prep:
                log(f"Đã chuẩn bị câu gốc để dịch theo ý nghĩa: "
                    f"{before_prep} -> {len(segments)} dòng.", "ok")
                srt_utils.save_srt_file(src_srt, segments)
        reuse_vi = bool(tr.get("reuse_existing") and os.path.exists(vi_srt))
        reuse_vi_direct = False
        if reuse_vi:
            vi_probe = srt_utils.load_srt_file(vi_srt)
            dirty_lines = [
                s.index for s in vi_probe
                if translate._contains_cjk(s.text)
            ]
            if dirty_lines:
                sample = ", ".join(str(x) for x in dirty_lines[:8])
                more = "" if len(dirty_lines) <= 8 else f", ... +{len(dirty_lines) - 8}"
                log(f"Ban dich cu con tieng Trung o dong {sample}{more} "
                    "-> bo qua file .vi.srt cu va dich lai.", "warn")
                reuse_vi = False
            elif len(vi_probe) != len(segments):
                if vi_probe and len(vi_probe) > len(segments):
                    log(f"Ban dich cu co {len(vi_probe)} dong, phu de goc co {len(segments)} dong. "
                        "Co ve day la ban da chia lai/polish tu lan truoc -> dung truc tiep.", "ok")
                    # Mốc thời gian trong file cũ được sinh bằng cách chia theo tỉ lệ
                    # ký tự nên đang lệch. Chữ thì giữ nguyên (có thể đã sửa tay),
                    # chỉ neo lại mốc theo bản đồ thoại.
                    doi = srt_utils.reanchor_translated_segments(vi_probe, segments)
                    if doi:
                        log(f"Da neo lai moc thoi gian cho {doi} dong cua ban dich cu "
                            "theo ban do thoai.", "ok")
                    segments = vi_probe
                    reuse_vi_direct = True
                else:
                    log(f"Ban dich cu co {len(vi_probe)} dong, phu de goc hien tai co "
                        f"{len(segments)} dong -> bo qua file .vi.srt cu va dich lai.", "warn")
                    reuse_vi = False

        if reuse_vi:
            log(f"Dùng lại bản dịch: {vi_srt}", "ok")
            vi_segments = srt_utils.load_srt_file(vi_srt)
            # Chỉ lấy TEXT Việt, giữ timing từ phụ đề gốc hiện tại. Nếu số dòng lệch
            # thì nhánh trên đã tắt reuse để tránh dùng lại timestamp .vi.srt cũ.
            cleaned_reuse = 0
            for s, v in zip(segments, vi_segments):
                clean_text = srt_utils.normalize_vi_subtitle_text(v.text)
                if clean_text != v.text:
                    cleaned_reuse += 1
                s.text = clean_text
            if cleaned_reuse:
                log(f"Da don sach {cleaned_reuse} dong metadata bi lot trong ban dich cu.", "ok")
                srt_utils.save_srt_file(vi_srt, segments)
            # Bản dịch cũ (dịch trước khi có ràng buộc độ dài) thường dài gấp đôi
            # thời lượng -> lồng tiếng chắc chắn trễ. Báo rõ thay vì để người dùng
            # tự đoán vì sao video vẫn chạy trước giọng.
            cps_cfg = float(tr.get("chars_per_sec", 0) or 0)
            if cps_cfg > 0 and segments:
                need = sum(len(s.text) for s in segments)
                have = sum(max(0.3, s.end - s.start) for s in segments) * cps_cfg
                if have > 0 and need > have * 1.25:
                    log(f"Bản dịch cũ dài gấp {need/have:.1f} lần mức đọc kịp -> "
                        "giọng sẽ TRỄ so với hình.", "warn")
                    log(f"  Muốn hết trễ: XOÁ file {os.path.basename(vi_srt)} rồi "
                        "chạy lại để dịch lại theo đúng thời lượng.", "warn")
        if not reuse_vi:
            log("Dịch sang tiếng Việt (giữ ngữ điệu, nhất quán nhân vật)...", "step")
            # Cache theo lô: lỗi giữa chừng thì lần chạy sau dịch tiếp, không làm lại.
            tr_cache = os.path.join(out_dir, f"{stem}.dich_cache.json")
            partial_srt = os.path.join(out_dir, f"{stem}.vi.CHUA_XONG.srt")
            try:
                provider = str(tr.get("provider", "browser")).lower()
                name_hint = translate.build_name_hint(
                    tr.get("male_lead_name", ""),
                    tr.get("female_lead_name", ""))
                if provider == "browser":
                    translate.translate_via_browser(
                        segments, os.path.join(here, tr.get("browser_profile", "browser_profile")),
                        channel=tr.get("browser_channel", "msedge"),
                        chunk_size=tr.get("chunk_size", 25),
                        wait_reply=tr.get("wait_reply", 120),
                        reset_every=tr.get("reset_every", 10),
                        cache_path=tr_cache, source_lang=src_lang,
                        chars_per_sec=tr.get("chars_per_sec", 0.0),
                        name_hint=name_hint,
                        shorten_long_lines_enabled=bool(
                            tr.get("shorten_long_lines", True)))
                else:
                    api_key, model, api_base_url, api_timeout = \
                        translate.api_params_for_provider(tr, provider)
                    translate.translate_segments(
                        segments, api_key=api_key,
                        model=model,
                        provider=provider,
                        api_base_url=api_base_url,
                        api_timeout=api_timeout,
                        chunk_size=tr.get("chunk_size", 40),
                        cache_path=tr_cache,
                        chars_per_sec=tr.get("chars_per_sec", 0.0),
                        name_hint=name_hint,
                        shorten_long_lines_enabled=bool(
                            tr.get("shorten_long_lines", True)))
            except Exception as e:
                # KHÔNG ghi đè <stem>.vi.srt bằng bản dở dang: lần chạy sau
                # reuse_existing sẽ tưởng đã dịch xong rồi lồng tiếng luôn.
                srt_utils.save_srt_file(partial_srt, segments)
                log(f"Dịch chưa xong. Phần đã dịch được lưu ở: {partial_srt}", "warn")
                log("CHẠY LẠI chương trình để dịch tiếp phần còn thiếu - các lô đã "
                    "xong được nhớ trong cache, không phải làm lại từ đầu.", "info")
                if isinstance(e, translate.TranslationIncomplete):
                    log(str(e), "err")
                    return 1
                raise
            srt_utils.save_srt_file(vi_srt, segments)
            log(f"Đã lưu bản dịch: {vi_srt}", "ok")
            for stale in (partial_srt, tr_cache):
                if os.path.exists(stale):
                    try:
                        os.remove(stale)
                    except OSError:
                        pass

        # 5) Tổng hợp giọng + chống đè thoại
        if tr.get("polish_subtitles", True):
            before_polish = len(segments)
            before_polish_state = [(s.start, s.end, s.text) for s in segments]
            segments = srt_utils.polish_translated_segments(
                segments,
                max_chars=int(tr.get("polish_max_chars", 125) or 125),
                min_chars=int(tr.get("polish_min_chars", 24) or 24),
                min_gap=float(tr.get("polish_min_gap", 0.04) or 0.04),
            )
            polish_changed = (
                len(segments) != before_polish
                or len(segments) != len(before_polish_state)
                or any((s.start, s.end, s.text) != before_polish_state[i]
                       for i, s in enumerate(segments))
            )
            if len(segments) != before_polish:
                log(f"Da chia lai sub Viet theo y cau: {before_polish} -> {len(segments)} dong.", "ok")
            elif polish_changed:
                log("Da don sach/chuan hoa lai text sub Viet.", "ok")
            if polish_changed:
                srt_utils.save_srt_file(vi_srt, segments)

        if tr.get("split_translated_on_punctuation", False):
            before_split = len(segments)
            segments = srt_utils.split_segments_on_punctuation(segments)
            if len(segments) != before_split:
                log(f"Da tach sub Viet theo dau cau: {before_split} -> {len(segments)} dong.", "ok")
                srt_utils.save_srt_file(vi_srt, segments)

        # Cảnh báo TRƯỚC khi tổng hợp giọng: nếu bản dịch dài hơn khung thời gian
        # thì mọi câu sẽ bị nén/cắt và thoại kết thúc sớm hơn hình. Biết trước ở đây
        # còn kịp sửa, thay vì render xong 30 phút mới phát hiện.
        translate.log_reading_pressure(segments, tr.get("chars_per_sec", 15.0) or 15.0)

        tcfg = cfg["tts"]
        t_engine = (tcfg.get("engine", "edge") or "edge").lower()
        t_voice = (tcfg.get("vieneu_voice") if t_engine == "vieneu"
                   else tcfg.get("capcut_voice") if t_engine == "capcut"
                   else tcfg.get("narrator_voice", "vi-VN-NamMinhNeural"))
        narrator = {"voice": t_voice or "vi-VN-NamMinhNeural",
                    "pitch": tcfg.get("narrator_pitch", "+0Hz")}
        tts_fail_report = os.path.join(out_dir, f"{stem}.tts_loi.txt")
        if os.path.exists(tts_fail_report):
            os.remove(tts_fail_report)  # xoá báo cáo lỗi cũ để không nhầm với lần chạy này
        clips, placed_starts, placements = tts.build_voice_track(
            segments, clips_dir, total_duration=duration,
            engine=t_engine,
            voice_mode=tcfg.get("voice_mode", "narrator"), narrator=narrator,
            base_rate=tcfg.get("base_rate", "+0%"), max_speed=tcfg.get("max_speed", 1.6),
            min_gap=tcfg.get("min_gap", 0.08), concurrency=tcfg.get("concurrency", 8),
            max_retries=tcfg.get("max_retries", 4), retry_base_delay=tcfg.get("retry_delay", 1.2),
            fail_report_path=tts_fail_report, recover_drift=tcfg.get("recover_drift", True),
            vieneu_voice=tcfg.get("vieneu_voice"), vieneu_voices=tcfg.get("vieneu_voices"),
            vieneu_options=tcfg.get("vieneu_options"),
            capcut_options=tcfg.get("capcut_options"),
            trim=tcfg.get("trim_silence", True),
            sync_offset_seconds=tcfg.get("sync_offset_seconds", 0.0),
            sync_mode=tcfg.get("sync_mode", "cascade"),
            trim_overflow=tcfg.get("trim_overflow", True),
            max_overhang=tcfg.get("max_overhang_seconds", 0.75),
        )
        from autodub.timeline import summarize
        log(f"Chống đè thoại: {summarize(placements)}", "ok")
        srt_utils.save_srt_file(vi_srt, segments, use_placed=True)

        v = cfg["video"]

        # 6) Ghép audio theo timeline
        log("Ghép giọng vào đúng vị trí thời gian...", "step")
        valid_clips = [c for c in clips if c]
        valid_starts = [s for c, s in zip(clips, placed_starts) if c]
        if not valid_clips:
            log("TTS không tạo được clip giọng Việt nào. Dừng trước khi render để "
                "tránh xuất file chỉ còn tiếng gốc + phụ đề.", "err")
            log(f"Xem báo cáo lỗi TTS: {tts_fail_report}", "err")
            return 1
        t6 = time.time()
        dub_wav = video.assemble_timeline_audio(
            valid_clips, valid_starts, duration, dub_wav,
            mode=v.get("audio_mix_mode", "auto"),
            chunk_seconds=v.get("audio_mix_chunk_seconds", 120),
        )
        log(f"Bước 6 hoàn tất trong {time.time() - t6:.1f}s.", "ok")

        # 7) Render video cuối
        hardsub = None
        if v.get("hardsub_vietnamese"):
            vw, vh = ffprobe_video_size(video_path)
            if vw <= 0 or vh <= 0:
                vw, vh = 1280, 720
            hardsub = os.path.join(tmp_dir, f"{stem}.display.ass")
            overlays.save_ass(hardsub, segments, vw, vh,
                              v.get("subtitle_style"), use_placed=True)
        t7 = time.time()
        from autodub.utils import resolve_keep_original_db
        keep_db = resolve_keep_original_db(v)
        if keep_db is not None:
            log(f"Giu am goc: {keep_db:+.1f} dB", "info")
        video.render_final(
            video_path, dub_wav, final_out,
            blur_bottom_ratio=v.get("blur_bottom_ratio", 0.0),
            blur_strength=v.get("blur_strength", 20),
            keep_original_db=keep_db,
            subtitle_srt=hardsub, delogo=v.get("delogo"),
            regions=v.get("regions"),
            use_gpu=v.get("use_gpu", True),
            subtitle_style=v.get("subtitle_style"),
            force_h264=v.get("force_h264", True),
            x264_preset=v.get("x264_preset", "superfast"),
            cpu_threads=v.get("cpu_threads", 4),
        )
        log(f"Bước 7 hoàn tất trong {time.time() - t7:.1f}s.", "ok")

    finally:
        if not cfg["output"].get("keep_temp"):
            shutil.rmtree(tmp_dir, ignore_errors=True)

    dt = time.time() - t0
    speed_x = duration / dt if dt > 0 else 0
    log(f"XONG trong {dt/60:.1f} phút  ({speed_x:.1f}x thời lượng video)", "ok")
    print(f"\n{C.G}▶ Video tiếng Việt:{C.E} {final_out}")
    print(f"{C.DIM}  Phụ đề gốc: {src_srt}\n  Phụ đề Việt: {vi_srt}{C.E}")
    if os.path.exists(tts_fail_report):
        print(f"{C.Y}  ⚠ Có dòng bị câm tiếng (TTS lỗi) — xem chi tiết: {tts_fail_report}{C.E}")
    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nĐã hủy.")
        sys.exit(130)
