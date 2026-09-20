"""Cắt cảnh phim lồng tiếng thành thumbnail + câu hay (không gọi ffmpeg thật)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autodub import youtube_pack
from autodub.srt_utils import Segment
from autodub.youtube_pack import dub_scenes


class CauThumbnail(unittest.TestCase):
    def test_rut_cau_hay_tu_thoai(self):
        line1, line2 = dub_scenes.compact_hook(
            "Sao mày dám lừa tao mười năm trời như vậy?")
        blob = ("%s %s" % (line1, line2)).upper()
        self.assertIn("DÁM", blob)
        self.assertIn("LỪA", blob)
        self.assertIn("10 NĂM", blob)

    def test_bo_ten_nguoi_noi(self):
        line1, line2 = dub_scenes.compact_hook("Lan: Đừng hòng bước vào nhà này nữa!")
        blob = ("%s %s" % (line1, line2)).upper()
        self.assertIn("ĐỪNG", blob)
        self.assertNotIn("LAN:", blob)

    def test_diem_uu_tien_cau_gay(self):
        hot = dub_scenes.score_scene("Sự thật là mày đã lừa tao 10 năm?", 400, 1000)
        dull = dub_scenes.score_scene("Ừ.", 5, 1000)
        self.assertGreater(hot, dull)


class ChonCanh(unittest.TestCase):
    def test_chon_canh_tach_xa_va_co_chu(self):
        segs = [
            Segment(1, 10, 12, "Xin chào."),
            Segment(2, 120, 124, "Sao mày dám lừa tao mười năm?"),
            Segment(3, 125, 128, "Đừng hòng bước vào nhà này nữa!"),
            Segment(4, 400, 405, "Sự thật lộ ra rồi."),
            Segment(5, 800, 804, "Im đi, ra khỏi nhà tao."),
        ]
        picked = dub_scenes.pick_scene_moments(segs, video_dur=900, count=3)
        self.assertEqual(len(picked), 3)
        times = [row["at"] for row in picked]
        self.assertEqual(times, sorted(times))
        for row in picked:
            self.assertTrue(row["line1"])
        texts = " ".join(row["text"] for row in picked).lower()
        self.assertTrue("lừa" in texts or "sự thật" in texts or "im đi" in texts)

    def test_doc_ca_dict_vi(self):
        picked = dub_scenes.pick_scene_moments(
            [{"start": 80, "end": 84, "vi": "Đừng hòng lừa tao!"}],
            video_dur=200, count=1)
        self.assertEqual(len(picked), 1)
        self.assertIn("ĐỪNG", picked[0]["line1"])


class CoCauHinh(unittest.TestCase):
    def test_mac_dinh_tat(self):
        self.assertFalse(dub_scenes.want_dub_thumbnail({}, {}))
        self.assertFalse(youtube_pack.want_dub_thumbnail(None, None))

    def test_tat_o_du_an(self):
        self.assertFalse(dub_scenes.want_dub_thumbnail(
            {"auto_dub_thumbnail": False},
            {"dang_youtube": {"auto_dub_thumbnail": True}}))

    def test_tat_o_config(self):
        self.assertFalse(dub_scenes.want_dub_thumbnail(
            {}, {"dang_youtube": {"auto_dub_thumbnail": False}}))

    def test_attach_bo_qua_khi_tat(self):
        with mock.patch.object(dub_scenes, "make_dub_thumbnail_pack") as maker:
            out = dub_scenes.attach_dub_thumbnails(
                "x.mp4", opt={"auto_dub_thumbnail": False})
        maker.assert_not_called()
        self.assertEqual(out, {})


class LamPack(unittest.TestCase):
    def test_gui_chatgpt_canh_roi_ve_tieu_de(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "phim.mp4")
            Path(video).write_bytes(b"fake")
            segs = [
                Segment(1, 12, 16, "Sao mày dám lừa tao mười năm?"),
                Segment(2, 80, 84, "Đừng hòng bước vào nhà này nữa!"),
            ]

            def fake_frame(_video, _t, dest):
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (1280, 720), (90, 40, 28)).save(dest, quality=90)
                return dest

            def fake_clip(_video, _start, _seconds, dest):
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                Path(dest).write_bytes(b"mp4")
                return dest

            class FakeSession:
                def __init__(self):
                    self.image_paths = None
                    self.prompt = ""

                def generate_image(self, prompt, dest, image_paths=None):
                    self.prompt = prompt
                    self.image_paths = list(image_paths or [])
                    Image.new("RGB", (1280, 720), (18, 90, 160)).save(dest)
                    return Path(dest)

            session = FakeSession()
            with mock.patch.object(dub_scenes, "extract_frame", side_effect=fake_frame), \
                 mock.patch.object(dub_scenes, "extract_clip", side_effect=fake_clip), \
                 mock.patch.object(dub_scenes, "ffprobe_duration", return_value=120.0):
                out = dub_scenes.make_dub_thumbnail_pack(
                    video, segments=segs, out_dir=tmp, duration=120,
                    count=2, title="Phim test", chatgpt_session=session)
            self.assertTrue(Path(out["thumbnail_path"]).is_file())
            self.assertTrue(out["chatgpt"])
            self.assertTrue(session.image_paths)
            self.assertTrue(all(os.path.isfile(p) for p in session.image_paths))
            self.assertIn("KHÔNG sinh chữ", session.prompt)
            text = Path(out["caption_path"]).read_text(encoding="utf-8")
            self.assertIn("ChatGPT", text)
            with Image.open(out["thumbnail_path"]) as img:
                self.assertEqual(img.size, (1280, 720))
                self.assertNotEqual(img.getpixel((80, 360)), (18, 90, 160))

    def test_chatgpt_loi_thi_giu_anh_cat_tu_phim(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "phim.mp4")
            Path(video).write_bytes(b"fake")
            segs = [Segment(1, 12, 16, "Đừng hòng lừa tao mười năm!")]

            def fake_frame(_video, _t, dest):
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (1280, 720), (90, 40, 28)).save(dest, quality=90)
                return dest

            class Boom:
                def generate_image(self, *a, **k):
                    raise RuntimeError("ChatGPT bận")

            with mock.patch.object(dub_scenes, "extract_frame", side_effect=fake_frame), \
                 mock.patch.object(dub_scenes, "extract_clip", return_value="x.mp4"), \
                 mock.patch.object(dub_scenes, "ffprobe_duration", return_value=80.0):
                out = dub_scenes.make_dub_thumbnail_pack(
                    video, segments=segs, out_dir=tmp, duration=80,
                    count=1, title="Phim", chatgpt_session=Boom())
            self.assertTrue(Path(out["thumbnail_path"]).is_file())
            self.assertFalse(out["chatgpt"])
            self.assertIn("thumbnail.jpg", Path(out["caption_path"]).read_text(encoding="utf-8"))

    def test_prompt_dub_cam_chu_va_lech_phai(self):
        prompt = dub_scenes.build_dub_thumbnail_visual_prompt(
            "Đuổi oan con dâu",
            [{"line1": "ĐUỔI OAN", "line2": "10 NĂM", "text": "Đừng hòng vào nhà."}])
        self.assertIn("KHÔNG sinh chữ", prompt)
        self.assertIn("PHẢI", prompt)
        self.assertIn("ĐUỔI OAN", prompt)
        self.assertNotIn("kể chuyện đêm khuya", prompt.lower())


class DinhAnhChatGPT(unittest.TestCase):
    def test_attach_images_goi_set_input_files(self):
        from autodub import chatgpt_web
        from PIL import Image

        class FakeInput:
            def __init__(self):
                self.files = None

            def count(self):
                return 1

            def nth(self, _idx):
                return self

            def set_input_files(self, files, timeout=0):
                self.files = list(files)

        class FakePage:
            def __init__(self):
                self.inp = FakeInput()

            def locator(self, sel):
                if "file" in sel:
                    return self.inp
                return FakeInput()

        with tempfile.TemporaryDirectory() as tmp:
            shot = os.path.join(tmp, "canh.jpg")
            Image.new("RGB", (64, 64), (10, 10, 10)).save(shot)
            session = chatgpt_web.ChatGPTWebSession(tmp)
            session.page = FakePage()
            session.ensure_ready = lambda timeout=0: None
            session._sleep = lambda _s: None
            got = session.attach_images([shot])
            self.assertEqual(got, [os.path.abspath(shot)])
            self.assertEqual(session.page.inp.files, [os.path.abspath(shot)])


class GiaoDien(unittest.TestCase):
    def test_co_o_cat_canh_trong_gui(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        core = Path(root, "ui", "js", "core.js").read_text(encoding="utf-8")
        panel = Path(root, "ui", "js", "dub-panel.js").read_text(encoding="utf-8")
        html = Path(root, "ui", "index.html").read_text(encoding="utf-8")
        self.assertIn("auto_dub_thumbnail", core)
        self.assertIn("ChatGPT làm thumbnail", core)
        self.assertIn("tiêu đề trên ảnh", core)
        self.assertIn("ChatGPT thumbnail + tiêu đề", panel)
        self.assertIn('data-settings-tab="youtube"', html)


if __name__ == "__main__":
    unittest.main()
