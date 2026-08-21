"""Kiểm tra phần backend mới của chế độ VIDEO KỂ CHUYỆN.

Gồm: timeline phụ đề khớp giọng đọc, dựng ASS từ SRT, và validation của
endpoint chạy-tất-cả. Không gọi ffmpeg/TTS/mạng.

Chạy: python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub.tts import build_narration_timeline
from autodub import srt_utils, tts as tts_mod
from autodub.server import manual_api
from autodub.server.state import STATE, _LOCK


class TimelinePhuDe(unittest.TestCase):
    def test_nhan_dien_cau_nhac_kenh_de_tang_toc_rieng(self):
        self.assertTrue(tts_mod._is_channel_cta_text(
            "Bạn đang nghe chuyện tại Gốc Mít Kể Chuyện."))
        self.assertFalse(tts_mod._is_channel_cta_text(
            "Bà Mít đang nghe tiếng radio trong buồng."))

    def test_truyen_dan_tay_duoc_chen_it_nhat_hai_cta_va_khong_bi_lap(self):
        source = " ".join("Nội dung câu chuyện số %d." % i for i in range(500))
        payload = {"cta": {"enabled": True, "positions": [10, 60],
                           "text": "Bạn đang nghe chuyện tại {channel}."}}
        once = manual_api._ensure_story_ctas(source, payload)
        twice = manual_api._ensure_story_ctas(once, payload)
        self.assertEqual(once.count("Bạn đang nghe chuyện tại"), 2)
        self.assertEqual(twice.count("Bạn đang nghe chuyện tại"), 2)

    def test_tts_chi_goi_tang_toc_cho_clip_cta(self):
        with tempfile.TemporaryDirectory() as td:
            raw = os.path.join(td, "raw.mp3")
            out = os.path.join(td, "out.mp3")
            with open(raw, "wb") as f:
                f.write(b"raw")

            def fake_speed(_src, dst, speed, **_kwargs):
                with open(dst, "wb") as f:
                    f.write(b"fast")
                return dst

            def fake_concat(_clips, dst, **_kwargs):
                with open(dst, "wb") as f:
                    f.write(b"joined")
                return dst

            with mock.patch.object(
                    tts_mod, "_synth_all",
                    new=mock.AsyncMock(return_value=[raw])), \
                 mock.patch.object(tts_mod, "change_speed",
                                   side_effect=fake_speed) as speed, \
                 mock.patch.object(tts_mod, "concat_audio_clips",
                                   side_effect=fake_concat), \
                 mock.patch.object(tts_mod, "ffprobe_duration", return_value=2.0):
                result = tts_mod.synthesize_text_audio(
                    "Bạn đang nghe chuyện tại Gốc Mít Kể Chuyện.",
                    td, out, engine="edge", channel_cta_speed=2.0)
            speed.assert_called_once()
            self.assertEqual(speed.call_args.args[2], 2.0)
            self.assertTrue(result["segments"][0]["channel_cta"])

    def test_cong_don_dung_moc_tuyet_doi(self):
        tl = build_narration_timeline(["Câu một.", "Câu hai.", "Câu ba."],
                                      [2.0, 3.5, 1.25])
        self.assertEqual(len(tl), 3)
        self.assertEqual(tl[0], {"start": 0.0, "end": 2.0, "text": "Câu một."})
        self.assertEqual(tl[1], {"start": 2.0, "end": 5.5, "text": "Câu hai."})
        self.assertEqual(tl[2]["start"], 5.5)
        self.assertAlmostEqual(tl[2]["end"], 6.75, places=3)

    def test_doan_rong_khong_thanh_phu_de_nhung_van_giu_moc(self):
        """Đoạn trống vẫn chiếm thời gian trên track nên mốc câu sau phải trôi theo."""
        tl = build_narration_timeline(["A", "   ", "B"], [1.0, 2.0, 1.0])
        self.assertEqual([t["text"] for t in tl], ["A", "B"])
        self.assertEqual(tl[1]["start"], 3.0)

    def test_duration_hong_khong_lam_am_timeline(self):
        tl = build_narration_timeline(["A", "B"], [0.0, -5])
        self.assertGreater(tl[0]["end"], tl[0]["start"])
        self.assertGreaterEqual(tl[1]["start"], tl[0]["end"])


class DungAssTuSrt(unittest.TestCase):
    def test_sinh_file_ass_voi_style_ke_chuyen(self):
        with tempfile.TemporaryDirectory() as td:
            srt = os.path.join(td, "a.srt")
            srt_utils.save_srt_file(srt, [
                srt_utils.Segment(1, 0.0, 2.0, "Ngày xửa ngày xưa"),
                srt_utils.Segment(2, 2.0, 5.0, "có một chàng trai nghèo."),
            ])
            ass = manual_api._ass_tu_srt(srt, td, 1920, 1080,
                                         {"size": 52, "color": "#FFD700"})
            self.assertTrue(ass and os.path.isfile(ass))
            body = open(ass, encoding="utf-8").read()
            self.assertIn("PlayResX: 1920", body)
            self.assertIn(",52,", body)          # cỡ chữ người dùng đặt
            self.assertIn("Ngày xửa ngày xưa", body)

    def test_kho_doc_9_16_co_chu_theo_canh_ngan(self):
        with tempfile.TemporaryDirectory() as td:
            srt = os.path.join(td, "a.srt")
            srt_utils.save_srt_file(srt, [srt_utils.Segment(1, 0.0, 2.0, "Chào")])
            ass = manual_api._ass_tu_srt(srt, td, 1080, 1920, None)
            body = open(ass, encoding="utf-8").read()
            self.assertIn("PlayResX: 1080", body)
            self.assertIn("PlayResY: 1920", body)
            # cỡ mặc định = 4.5% cạnh ngắn (1080) = 48, không ăn theo cạnh dài
            self.assertIn(",48,", body)

    def test_khong_co_srt_thi_tra_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(manual_api._ass_tu_srt("", td, 1920, 1080, None))
            self.assertIsNone(manual_api._ass_tu_srt(
                os.path.join(td, "khong_ton_tai.srt"), td, 1920, 1080, None))


class NoiAudioPhanTang(unittest.TestCase):
    def test_hon_48_clip_khong_nhet_mot_lenh_ffmpeg(self):
        """Truyện 200k ký tự sinh ~800 clip TTS; một lệnh ffmpeg chứa cả 800
        đường dẫn sẽ vỡ giới hạn ~32k ký tự dòng lệnh của Windows."""
        from unittest import mock
        from autodub import video as vid

        calls = []

        def fake_run(cmd, **_kw):
            with open(cmd[-1], "wb") as f:    # "tạo" file đầu ra của lệnh
                f.write(b"x")
            calls.append(cmd)

        with tempfile.TemporaryDirectory() as td:
            files = []
            for i in range(120):
                p = os.path.join(td, f"clip_{i:03d}.wav")
                with open(p, "wb") as f:
                    f.write(b"x")
                files.append(p)
            out = os.path.join(td, "out.wav")
            with mock.patch.object(vid, "run", side_effect=fake_run):
                vid._concat_audio_chunks(files, out, 48000, td)
            for cmd in calls:
                so_input = sum(1 for a in cmd if a == "-i")
                self.assertLessEqual(so_input, vid._CONCAT_BATCH)
            # 120 clip -> 3 nhóm trung gian + 1 lệnh nối cuối
            self.assertEqual(len(calls), 4)
            self.assertTrue(os.path.exists(out))

    def test_it_clip_van_mot_lenh_nhu_cu(self):
        from unittest import mock
        from autodub import video as vid
        calls = []

        def fake_run(cmd, **_kw):
            with open(cmd[-1], "wb") as f:
                f.write(b"x")
            calls.append(cmd)

        with tempfile.TemporaryDirectory() as td:
            files = []
            for i in range(5):
                p = os.path.join(td, f"c{i}.wav")
                with open(p, "wb") as f:
                    f.write(b"x")
                files.append(p)
            out = os.path.join(td, "out.wav")
            with mock.patch.object(vid, "run", side_effect=fake_run):
                vid._concat_audio_chunks(files, out, 48000, td)
            self.assertEqual(len(calls), 1)

    def test_can_am_luong_tung_clip_truoc_khi_noi(self):
        from autodub import video as vid

        calls = []

        def fake_run(cmd, **_kw):
            with open(cmd[-1], "wb") as f:
                f.write(b"x")
            calls.append(cmd)

        with tempfile.TemporaryDirectory() as td:
            files = []
            for i in range(3):
                path = os.path.join(td, f"voice_{i}.mp3")
                with open(path, "wb") as f:
                    f.write(b"x")
                files.append(path)
            out = os.path.join(td, "out.wav")
            with mock.patch.object(vid, "run", side_effect=fake_run):
                vid.concat_audio_clips(
                    files, out, normalize_loudness=True)

        graph = calls[0][calls[0].index("-filter_complex") + 1]
        self.assertEqual(graph.count("loudnorm=I=-18:TP=-2:LRA=7"), 3)

    def test_chia_frame_truoc_flac_de_khong_vuot_block_65535(self):
        """loudnorm có thể trả frame 69k mẫu; FLAC chỉ nhận tối đa 65.535."""
        from autodub import video as vid

        calls = []

        def fake_run(cmd, **_kw):
            with open(cmd[-1], "wb") as f:
                f.write(b"x")
            calls.append(cmd)

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "voice.mp3")
            with open(path, "wb") as f:
                f.write(b"x")
            out = os.path.join(td, "joined.flac")
            with mock.patch.object(vid, "run", side_effect=fake_run):
                vid.concat_audio_clips(
                    [path] * 48, out, normalize_loudness=True)

        graph = calls[0][calls[0].index("-filter_complex") + 1]
        self.assertIn("concat=n=48:v=0:a=1[joined]", graph)
        self.assertTrue(graph.endswith(
            "[joined]asetnsamples=n=4096:p=0[out]"), graph[-120:])


class KhoiPhucTTSKeChuyen(unittest.TestCase):
    def test_capcut_dung_lai_thu_muc_loi_gan_nhat(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_root = os.path.join(td, "_tmp")
            old = os.path.join(tmp_root, "truyen_20260815_080000")
            latest = os.path.join(tmp_root, "truyen_20260815_090000")
            os.makedirs(old)
            os.makedirs(latest)
            for folder, count in ((old, 2), (latest, 3)):
                for i in range(count):
                    with open(os.path.join(
                            folder, "capcut_%05d_hash.mp3" % i), "wb") as f:
                        f.write(b"audio" * 200)
            os.utime(old, (1, 1))
            path, count = manual_api._story_tts_workdir(
                td, "truyen", "20260815_100000", "capcut")
            self.assertEqual(os.path.normcase(path), os.path.normcase(latest))
            self.assertEqual(count, 3)

    def test_engine_khac_khong_dung_lai_clip_capcut(self):
        with tempfile.TemporaryDirectory() as td:
            path, count = manual_api._story_tts_workdir(
                td, "truyen", "20260815_100000", "edge")
            self.assertTrue(path.endswith("truyen_20260815_100000"))
            self.assertEqual(count, 0)


class TrangThaiDungTacVu(unittest.TestCase):
    def test_dung_la_trang_thai_binh_thuong_khong_phai_loi(self):
        with _LOCK:
            manual = STATE["manual"]
            old = dict(manual)
            manual.update({"working": True, "status": "Đang chạy", "error": "lỗi cũ"})
        try:
            manual_api._mark_manual_cancelled("Đã giữ phần xong.")
            with _LOCK:
                self.assertFalse(STATE["manual"]["working"])
                self.assertEqual(STATE["manual"]["status"], "Đã dừng tác vụ")
                self.assertEqual(STATE["manual"]["error"], "")
        finally:
            with _LOCK:
                manual.clear()
                manual.update(old)


class NgheThuGiong(unittest.TestCase):
    """Nghe thử một câu bằng giọng đang chọn, khỏi phải tạo cả file MP3 dài."""

    def setUp(self):
        with _LOCK:
            STATE["running"] = False
            STATE["busy"] = ""

    tearDown = setUp

    def test_khong_co_van_ban_thi_doc_cau_mau(self):
        self.assertEqual(manual_api._cat_cau_nghe_thu(""),
                         manual_api.CAU_NGHE_THU)
        self.assertEqual(manual_api._cat_cau_nghe_thu("   \n  "),
                         manual_api.CAU_NGHE_THU)

    def test_van_ban_dai_bi_cat_o_dau_ket_cau(self):
        cau = manual_api._cat_cau_nghe_thu("Bà Bảy ngồi đó. " * 40)
        self.assertLessEqual(len(cau), manual_api.NGHE_THU_MAX_CHARS)
        self.assertTrue(cau.endswith("."), cau[-20:])

    def test_van_ban_ngan_thi_doc_nguyen_van(self):
        self.assertEqual(manual_api._cat_cau_nghe_thu("Ông Tám về rồi."),
                         "Ông Tám về rồi.")

    def test_dang_chay_viec_dai_thi_tu_choi(self):
        with _LOCK:
            STATE["running"] = True
        try:
            with self.assertRaises(manual_api.DangBan):
                manual_api.tao_ban_nghe_thu(engine="edge")
        finally:
            with _LOCK:
                STATE["running"] = False

    def test_engine_khong_ho_tro_thi_bao_loi(self):
        with self.assertRaises(ValueError):
            manual_api.tao_ban_nghe_thu(engine="festival")

    def test_nho_ket_qua_theo_giong_va_toc_do(self):
        from unittest import mock
        from autodub import tts as tts_mod

        goi = []

        def fake(text, workdir, out_path, **kw):
            narrator = kw.get("narrator") or {}
            goi.append({"voice": narrator.get("voice"),
                        "pitch": narrator.get("pitch"),
                        "rate": kw.get("base_rate")})
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(b"x" * 2048)
            return {"path": out_path, "duration": 7.0}

        with mock.patch.object(tts_mod, "synthesize_text_audio", side_effect=fake):
            a = manual_api.tao_ban_nghe_thu(
                engine="edge", voice="vi-VN-HoaiMyNeural|+8Hz", rate="+0%")
            self.assertFalse(a["cached"])
            # id giọng edge là "giọng|cao độ" -> phải tách trước khi dựng SSML
            self.assertEqual(goi[0]["voice"], "vi-VN-HoaiMyNeural")
            self.assertEqual(goi[0]["pitch"], "+8Hz")

            b = manual_api.tao_ban_nghe_thu(
                engine="edge", voice="vi-VN-HoaiMyNeural|+8Hz", rate="+0%")
            self.assertTrue(b["cached"])        # lần hai lấy lại file cũ
            self.assertEqual(len(goi), 1)       # không gọi TTS thêm lần nào
            self.assertEqual(a["path"], b["path"])

            c = manual_api.tao_ban_nghe_thu(
                engine="edge", voice="vi-VN-HoaiMyNeural|+8Hz", rate="+20%")
            self.assertNotEqual(a["path"], c["path"])   # đổi tốc độ = file khác
            self.assertEqual(len(goi), 2)
        for p in {a["path"], c["path"]}:
            if os.path.exists(p):
                os.remove(p)


class RunAllValidation(unittest.TestCase):
    def _reset(self):
        with _LOCK:
            STATE["running"] = False
            STATE["busy"] = ""

    def test_thu_muc_san_pham_lay_ten_tieu_de_video(self):
        folder = manual_api._story_output_dir("  Con dâu: ngày trở về?  ")
        self.assertEqual(os.path.dirname(folder),
                         os.path.join(manual_api.HERE, "output"))
        self.assertEqual(os.path.basename(folder), "Con dâu ngày trở về")

    def test_thieu_van_ban_bao_400(self):
        self._reset()
        obj, code = manual_api.api_manual_run_all({"anh": ["x.jpg"]})
        self.assertEqual(code, 400)
        self.assertIn("văn bản", obj["error"])

    def test_chua_co_anh_video_van_cho_tao_audio_truoc(self):
        self._reset()
        # Luồng mới cho phép tạo audio trước, sau đó người dùng chọn/random
        # video nguồn cho đủ thời lượng mà không phải tổng hợp TTS lại.
        with mock.patch.object(manual_api.threading, "Thread") as thread:
            obj, code = manual_api.api_manual_run_all({"text": "Ngày xưa..."})
        self.assertEqual(code, 200)
        self.assertTrue(obj["async"])
        thread.return_value.start.assert_called_once()
        self._reset()

    def test_dang_ban_bao_409(self):
        self._reset()
        with _LOCK:
            STATE["busy"] = "Đang dò vùng sub cứng…"
        try:
            obj, code = manual_api.api_manual_run_all(
                {"text": "Ngày xưa...", "anh": ["x.jpg"]})
            self.assertEqual(code, 409)
        finally:
            self._reset()

    def test_dung_lai_audio_random_video_khong_chay_lai_tts(self):
        """Nút Xuất MP4 sau khi có audio phải đi thẳng vào video nguồn."""
        from autodub import slideshow

        self._reset()

        class InlineThread:
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()

        with tempfile.TemporaryDirectory() as tmp:
            audio = os.path.join(tmp, "giong_co_nhac.m4a")
            with open(audio, "wb") as handle:
                handle.write(b"audio")
            output = os.path.join(tmp, "ket_qua.mp4")
            fake_result = {"path": output, "duration": 57.0,
                           "so_video": 2, "so_doan": 8}
            try:
                with mock.patch.object(manual_api.threading, "Thread", InlineThread), \
                     mock.patch.object(manual_api, "_story_output_dir",
                                       return_value=tmp), \
                     mock.patch.object(manual_api, "_load_cfg",
                                       return_value={"slideshow": {}}), \
                     mock.patch.object(slideshow, "tao_video_tu_video",
                                       return_value=fake_result) as render:
                    obj, code = manual_api.api_manual_slideshow({
                        "audio_path": audio,
                        "video_sources": ["a.mp4", "b.mp4"],
                        "source_random": True,
                        "source_random_seed": 123,
                        "source_clip_min_seconds": 300,
                        "source_clip_max_seconds": 600,
                        "source_transform": {"zoom": 120},
                        "sub": {"enabled": False},
                    })
                self.assertEqual(code, 200)
                self.assertTrue(obj["async"])
                render.assert_called_once()
                args, kwargs = render.call_args
                self.assertEqual(args[0], ["a.mp4", "b.mp4"])
                self.assertEqual(args[1], os.path.abspath(audio))
                self.assertTrue(kwargs["random_pick"])
                self.assertEqual(kwargs["random_seed"], 123)
                self.assertEqual(kwargs["min_seconds"], 300)
                self.assertEqual(kwargs["max_seconds"], 600)
                self.assertEqual(kwargs["transform"], {"zoom": 120})
                self.assertEqual(STATE["manual"]["output_path"], output)
            finally:
                self._reset()

    def test_tao_tu_tieu_de_thieu_tieu_de_bao_400(self):
        self._reset()
        obj, code = manual_api.api_story_generate_and_run({"anh": ["x.jpg"]})
        self.assertEqual(code, 400)
        self.assertIn("tiêu đề", obj["error"])

    def test_tiep_tuc_anh_thieu_goi_anh_bao_400(self):
        self._reset()
        obj, code = manual_api.api_story_resume_images({})
        self.assertEqual(code, 400)
        self.assertIn("gói ảnh", obj["error"])

    def test_tiep_tuc_anh_ban_giao_thang_sang_dung_video(self):
        from autodub import story_images

        self._reset()
        captured = {}

        class InlineThread:
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()

        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "KICH_BAN_DOC.txt")
            image = os.path.join(tmp, "scene.png")
            with open(script, "w", encoding="utf-8") as f:
                f.write("Ngày xưa có một câu chuyện.")
            with open(image, "wb") as f:
                f.write(b"image" * 300)
            pack = story_images.create_pack(
                "Chuyện tiếp tục", scene_prompts=["Prompt cảnh đủ dài."],
                scene_count=1, script_path=script, root=tmp)

            def fake_run_all(payload):
                captured.update(payload)
                return {"ok": True, "async": True}, 200

            try:
                with mock.patch.object(
                        manual_api, "_prepare_generated_story_images",
                        return_value=([image], pack["manifest_path"])), \
                     mock.patch.object(manual_api, "api_manual_run_all",
                                       side_effect=fake_run_all), \
                     mock.patch.object(manual_api.threading, "Thread", InlineThread):
                    obj, code = manual_api.api_story_resume_images({
                        "image_pack": pack["manifest_path"], "engine": "edge"})
                self.assertEqual(code, 200)
                self.assertEqual(captured["txt_path"], script)
                self.assertEqual(captured["image_pack"], pack["manifest_path"])
                self.assertEqual(captured["anh"], [image])
                self.assertTrue(captured["_handoff"])
            finally:
                with _LOCK:
                    STATE["manual"]["working"] = False
                self._reset()

    def test_tao_tu_tieu_de_khong_anh_van_bat_dau_de_tu_tao_anh(self):
        self._reset()
        with mock.patch.object(manual_api.threading, "Thread") as thread:
            obj, code = manual_api.api_story_generate_and_run(
                {"story_title": "Chuyện thử", "auto_images": True})
        self.assertEqual(code, 200)
        self.assertTrue(obj["async"])
        thread.return_value.start.assert_called_once()
        self._reset()

    def test_resume_anh_tu_choi_pack_cua_tieu_de_khac(self):
        from autodub import story_images

        self._reset()
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "KICH_BAN_DOC.txt")
            with open(script, "w", encoding="utf-8") as f:
                f.write("Nội dung truyện cũ.")
            pack = story_images.create_pack(
                "Truyện cũ", scene_count=1, script_path=script, root=tmp)
            obj, code = manual_api.api_story_resume_images({
                "image_pack": pack["manifest_path"],
                "story_title": "Truyện mới",
            })
        self.assertEqual(code, 409)
        self.assertIn("Truyện cũ", obj["error"])
        self._reset()

    def test_phan_tich_truyen_tra_giong_dang_co_trong_catalog(self):
        obj, code = manual_api.api_story_voice_recommendations({
            "text": "Bà mẹ ôm con dâu. Gia đình tha thứ cho nhau sau nhiều năm.",
            "engine": "capcut",
        })
        self.assertEqual(code, 200)
        self.assertGreaterEqual(obj["catalog_count"], 20)
        ids = {v["id"] for v in tts_mod.list_voices("capcut")}
        self.assertTrue(obj["recommendations"])
        self.assertIn(obj["recommendations"][0]["id"], ids)

    def test_tao_xong_tu_ban_giao_dung_file_doc_sang_video(self):
        self._reset()
        captured = {}

        class InlineThread:
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()

        def fake_generate(title, cfg, **kwargs):
            return {"title": title, "folder": "X:/story", "words": 1234,
                    "script_path": "X:/story/KICH_BAN_DOC.txt", "meta": {}}

        def fake_run_all(payload):
            captured.update(payload)
            return {"ok": True, "async": True}, 200

        try:
            with mock.patch("autodub.story_writer.generate", side_effect=fake_generate), \
                 mock.patch.object(manual_api, "api_manual_run_all", side_effect=fake_run_all), \
                 mock.patch.object(manual_api.threading, "Thread", InlineThread):
                obj, code = manual_api.api_story_generate_and_run({
                    "story_title": "Chuyện thử", "anh": ["x.jpg"], "engine": "edge"})
                self.assertEqual(code, 200)
            self.assertEqual(captured["txt_path"], "X:/story/KICH_BAN_DOC.txt")
            self.assertNotIn("text", captured)
            self.assertEqual(captured["_progress_start"], 48)
        finally:
            with _LOCK:
                STATE["manual"]["working"] = False
            self._reset()

    def test_khong_co_anh_thi_tao_prompt_va_dung_cho_anh_khong_chay_tts(self):
        self._reset()

        class InlineThread:
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()

        generated = {"title": "Chuyện chờ ảnh", "folder": "X:/story",
                     "words": 12000, "script_path": "X:/story/KICH_BAN_DOC.txt",
                     "design_path": "X:/story/00_ban_thiet_ke.txt", "meta": {}}
        try:
            with mock.patch("autodub.story_writer.generate", return_value=generated), \
                 mock.patch.object(manual_api, "_prepare_generated_story_images",
                                   return_value=([], "X:/pack/manifest.json")), \
                 mock.patch.object(manual_api, "api_manual_run_all") as run_all, \
                 mock.patch.object(manual_api.threading, "Thread", InlineThread):
                obj, code = manual_api.api_story_generate_and_run({
                    "story_title": "Chuyện chờ ảnh", "auto_images": True})
            self.assertEqual(code, 200)
            run_all.assert_not_called()
            self.assertFalse(STATE["manual"]["working"])
            self.assertIn("chờ bổ sung ảnh", STATE["manual"]["status"])
        finally:
            self._reset()

    def test_kich_ban_rot_chat_luong_thi_khong_ton_tts(self):
        self._reset()

        class InlineThread:
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()

        bad = {"title": "Chuyện lỗi", "folder": "X:/story", "words": 5000,
               "script_path": "X:/story/KICH_BAN_DOC.txt",
               "meta": {"kiem_tra_tu_dong": {
                   "within_target": False, "dialogue_target": True,
                   "banned_terms": {}, "repeated_paragraphs": 0}}}
        try:
            with mock.patch("autodub.story_writer.generate", return_value=bad), \
                 mock.patch.object(manual_api, "api_manual_run_all") as run_all, \
                 mock.patch.object(manual_api.threading, "Thread", InlineThread):
                obj, code = manual_api.api_story_generate_and_run({
                    "story_title": "Chuyện lỗi", "anh": ["x.jpg"]})
            self.assertEqual(code, 200)
            run_all.assert_not_called()
            self.assertIn("chưa dựng video", STATE["manual"]["error"])
        finally:
            with _LOCK:
                STATE["manual"]["working"] = False
            self._reset()

    def test_luong_mot_nut_tu_chon_giong_de_xuat_so_mot(self):
        self._reset()

        class InlineThread:
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()

        captured = {}
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "KICH_BAN_DOC.txt")
            with open(script, "w", encoding="utf-8") as f:
                f.write(" ".join(["Bà mẹ ôm con dâu và đứa cháu trong gia đình."] * 20))
            generated = {"title": "Chuyện nhà", "folder": tmp, "words": 200,
                         "script_path": script, "meta": {}}
            voices = [
                {"id": "multi_male_felipe_uranus_bigtts", "name": "Nam Trầm"},
                {"id": "vi_female_huong", "name": "Nữ Phổ Thông"},
            ]

            def fake_run_all(payload):
                captured.update(payload)
                return {"ok": True, "async": True}, 200

            try:
                with mock.patch("autodub.story_writer.generate", return_value=generated), \
                     mock.patch.object(tts_mod, "list_voices", return_value=voices), \
                     mock.patch.object(manual_api, "api_manual_run_all", side_effect=fake_run_all), \
                     mock.patch.object(manual_api.threading, "Thread", InlineThread):
                    obj, code = manual_api.api_story_generate_and_run({
                        "story_title": "Chuyện nhà", "anh": ["x.jpg"],
                        "engine": "capcut", "voice_auto": True})
                self.assertEqual(code, 200)
                self.assertEqual(captured["voice"], "vi_female_huong")
                self.assertEqual(STATE["manual"]["recommended_voice"], "vi_female_huong")
            finally:
                with _LOCK:
                    STATE["manual"]["working"] = False
                    STATE["manual"]["recommended_voice"] = ""
                    STATE["manual"]["voice_analysis"] = {}
                    STATE["manual"]["voice_recommendations"] = []
                self._reset()


if __name__ == "__main__":
    unittest.main(verbosity=1)
