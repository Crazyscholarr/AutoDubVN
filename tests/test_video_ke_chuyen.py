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

    def test_thieu_van_ban_bao_400(self):
        self._reset()
        obj, code = manual_api.api_manual_run_all({"anh": ["x.jpg"]})
        self.assertEqual(code, 400)
        self.assertIn("văn bản", obj["error"])

    def test_thieu_anh_bao_400(self):
        self._reset()
        obj, code = manual_api.api_manual_run_all({"text": "Ngày xưa..."})
        self.assertEqual(code, 400)
        self.assertIn("ảnh", obj["error"])

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

    def test_tao_tu_tieu_de_thieu_tieu_de_bao_400(self):
        self._reset()
        obj, code = manual_api.api_story_generate_and_run({"anh": ["x.jpg"]})
        self.assertEqual(code, 400)
        self.assertIn("tiêu đề", obj["error"])

    def test_tao_tu_tieu_de_thieu_anh_bao_400(self):
        self._reset()
        obj, code = manual_api.api_story_generate_and_run({"story_title": "Chuyện thử"})
        self.assertEqual(code, 400)
        self.assertIn("ảnh", obj["error"])

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
            self.assertEqual(captured["_progress_start"], 35)
        finally:
            with _LOCK:
                STATE["manual"]["working"] = False
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
