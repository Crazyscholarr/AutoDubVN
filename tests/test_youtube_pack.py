"""Kiểm tra thumbnail overlay, prompt mô tả và gói YouTube (không gọi ChatGPT)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub import chatgpt_web, youtube_pack
from autodub.server import config_api, manual_api


class TachDongThumbnail(unittest.TestCase):
    def test_tach_duoi_oan_con_dau_10_nam(self):
        line1, line2 = youtube_pack.split_thumbnail_lines("ĐUỔI OAN CON DÂU 10 NĂM")
        self.assertEqual(line1, "ĐUỔI OAN")
        self.assertEqual(line2, "CON DÂU 10 NĂM")

    def test_tach_hai_dong_san_co(self):
        line1, line2 = youtube_pack.split_thumbnail_lines("SỰ THẬT LỘ RA\n10 NĂM")
        self.assertEqual(line1, "SỰ THẬT LỘ RA")
        self.assertEqual(line2, "10 NĂM")

    def test_lay_chu_tu_y_tuong(self):
        line1, line2 = youtube_pack.overlay_lines_from_idea(
            "Mẹ chồng khóc xin lỗi con dâu sau 10 năm",
            {"thumbnails": [{"text": "ĐUỔI OAN CON DÂU 10 NĂM"}]})
        self.assertEqual((line1, line2), ("ĐUỔI OAN", "CON DÂU 10 NĂM"))


class TomTatVaMoTa(unittest.TestCase):
    def test_tom_tat_dung_truoc_cu_lat(self):
        summary = youtube_pack.make_story_summary(
            "Đuổi oan con dâu",
            {"main_hook": "Bà mẹ chồng vừa nhận tờ giấy ngân hàng.",
             "high_tension_scenes": [
                 "Con dâu bị đuổi khỏi nhà giữa đêm.",
                 "Cả họ hàng tin lời bà.",
                 "CHẤT LIỆU CÚ LẬT: hóa ra bà đã oan cho con dâu.",
             ],
             "rewrite_brief": "Mở đầu bà đuổi con dâu. Cú lật: sổ đỏ mang tên con dâu."})
        self.assertIn("tờ giấy ngân hàng", summary)
        self.assertNotIn("sổ đỏ", summary.lower())
        self.assertNotIn("hóa ra", summary.lower())

    def test_thoi_luong_tieng_viet(self):
        self.assertEqual(youtube_pack.format_duration_vi(6300), "1 giờ 45 phút")
        self.assertEqual(youtube_pack.format_duration_vi(3600), "1 giờ")
        self.assertEqual(youtube_pack.format_duration_vi(90), "1 phút")

    def test_prompt_mo_ta_du_6_khoi(self):
        prompt = youtube_pack.build_description_prompt(
            "Đuổi oan con dâu 10 năm",
            "Bà mẹ chồng vừa thấy tờ giấy nợ.",
            "1 giờ 45 phút")
        self.assertIn("Đuổi oan con dâu 10 năm", prompt)
        self.assertIn("1 giờ 45 phút", prompt)
        self.assertIn("Khối 6", prompt)
        self.assertIn("#kechuyendemkhuya", prompt)
        self.assertIn("Chỉ trả về phần mô tả", prompt)

    def test_prompt_anh_cam_chu(self):
        prompt = youtube_pack.build_thumbnail_visual_prompt(
            "Đuổi oan con dâu",
            {"thumbnails": [{"description": "Bà mẹ chồng cầm giấy nợ, mắt đỏ hoe"}],
             "main_characters": ["Bà mẹ chồng"]})
        self.assertIn("KHÔNG sinh bất kỳ chữ nào", prompt)
        self.assertIn("bên PHẢI", prompt)
        self.assertIn("Bà mẹ chồng", prompt)

    def test_ep_chan_trang_va_hashtag(self):
        text = youtube_pack.ensure_description_finish(
            "Bà vừa thấy tờ giấy. Hãy nghe hết.\n",
            "Mẹ chồng đuổi oan con dâu",
            {"primary_genre": "Mẹ chồng nàng dâu"})
        self.assertIn("08:00 sáng", text)
        self.assertIn("#kechuyendemkhuya", text)
        self.assertIn("#meChongNangDau", text)

    def test_boc_markdown(self):
        raw = "```\nĐây là mô tả: một câu hỏi?\n```"
        self.assertIn("một câu hỏi?", youtube_pack.extract_description_only(raw))

    def test_boc_dong_youtube_va_ghi_chu_cuoi(self):
        raw = (
            "YouTube\n\nBà vừa thấy tờ giấy nợ.\n"
            "#kechuyendemkhuya\n"
            "giảm tiết lộ ở tên chương cuối\n"
            "đổi câu mở đầu sát tình huống gay nhất\n"
        )
        clean = youtube_pack.extract_description_only(raw)
        self.assertTrue(clean.startswith("Bà vừa thấy"))
        self.assertNotIn("giảm tiết lộ", clean)
        self.assertNotIn("YouTube", clean)


class VeThumbnail(unittest.TestCase):
    def test_ve_chu_len_anh_16x9(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "raw.png"
            dest = Path(tmp) / "thumbnail.jpg"
            Image.new("RGB", (1600, 900), (0, 140, 0)).save(src)
            youtube_pack.draw_thumbnail_overlay(
                src, dest, "ĐUỔI OAN", "CON DÂU 10 NĂM")
            with Image.open(dest) as img:
                self.assertEqual(img.size, (1280, 720))
                # Nửa trái đã tối/chữ, không còn xanh đều như nền.
                self.assertNotEqual(img.getpixel((80, 360)), (0, 140, 0))


class ChatGPTHelpers(unittest.TestCase):
    def test_url_anh_chatgpt(self):
        self.assertTrue(chatgpt_web.looks_like_chatgpt_image_url(
            "https://files.oaiusercontent.com/file-abc123xyz/image.png"))
        self.assertTrue(chatgpt_web.looks_like_chatgpt_image_url("blob:https://chatgpt.com/1"))
        self.assertFalse(chatgpt_web.looks_like_chatgpt_image_url(
            "https://chatgpt.com/favicon.ico"))

    def test_composer_ready(self):
        self.assertTrue(chatgpt_web.composer_ready({"ready": True, "login": False}))
        self.assertFalse(chatgpt_web.composer_ready({"ready": False, "login": True}))

    def test_profile_mac_dinh_tach_gemini(self):
        settings = chatgpt_web.chatgpt_settings({})
        self.assertTrue(settings["profile_dir"].endswith("browser_profile_chatgpt"))
        self.assertNotEqual(os.path.basename(settings["profile_dir"]), "browser_profile")

    def test_tim_chrome_khi_khong_co_edge(self):
        exe, kind = chatgpt_web.find_login_browser()
        self.assertTrue(exe)
        self.assertIn(kind, {"chrome", "msedge"})
        self.assertTrue(os.path.isfile(exe))

    def test_launch_bao_loi_khi_khong_co_trinh_duyet(self):
        with mock.patch.object(chatgpt_web, "find_login_browser", return_value=("", "")):
            result = chatgpt_web.launch_chatgpt_login()
        self.assertFalse(result.get("ok"))
        self.assertIn("Chrome", result.get("error", ""))


class ConfigVaGoi(unittest.TestCase):
    def test_cau_hinh_gui_mac_dinh(self):
        with mock.patch.object(config_api, "_load_cfg", return_value={}):
            data = config_api._dang_youtube_cfg_for_gui()
        self.assertTrue(data["auto_thumbnail"])
        self.assertTrue(data["auto_description"])
        self.assertFalse(data["auto_dub_thumbnail"])
        self.assertEqual(data["dub_scene_count"], 4)
        self.assertEqual(data["browser_profile"], "browser_profile_chatgpt")

    def test_tat_ca_hai_co_thi_bo_qua(self):
        flags = youtube_pack.youtube_flags(
            {"auto_youtube_thumbnail": False, "auto_youtube_description": False},
            {"dang_youtube": {"auto_thumbnail": True, "auto_description": True}})
        self.assertEqual(flags, (False, False))

    def test_make_pack_khong_mo_chatgpt_khi_tat(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "phim.mp4"
            video.write_bytes(b"x")
            with mock.patch.object(youtube_pack, "ChatGPTWebSession", create=True):
                out = youtube_pack.make_youtube_pack(
                    str(video),
                    payload={"auto_youtube_thumbnail": False,
                             "auto_youtube_description": False,
                             "name": "Test"},
                    cfg={"dang_youtube": {"auto_thumbnail": False,
                                          "auto_description": False}})
            self.assertEqual(out, {})

    def test_make_pack_ve_chu_sau_khi_chatgpt_tra_anh(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "phim.mp4"
            video.write_bytes(b"x")
            raw = Path(tmp) / "thumbnail_raw.png"

            class FakeSession:
                def __init__(self, *a, **k):
                    pass
                def start(self):
                    return self
                def generate_image(self, prompt, dest):
                    Image.new("RGB", (1280, 720), (30, 30, 30)).save(dest)
                    return Path(dest)
                def new_chat(self):
                    return None
                def ask_text(self, prompt):
                    return (
                        "Bà vừa thấy tờ giấy nợ?\n"
                        "Đây là kể chuyện đêm khuya về mẹ chồng nàng dâu.\n"
                        "Mời quý vị nghe 1 giờ 45 phút.\n\n"
                        "Bà đuổi con dâu khỏi nhà. Cả họ hàng tin lời bà.\n"
                        "00:00 Tờ giấy trắng\n"
                        "Nếu là quý vị, quý vị sẽ làm gì?\n"
                        "Kể chuyện đêm khuya, đọc truyện đêm khuya, tâm sự tuổi già, "
                        "chuyện làng quê, truyện audio, chuyện thầm kín.\n\n"
                        + youtube_pack.FIXED_FOOTER + " #meChongNangDau #nhanQuaBaoUng"
                    )
                def close(self):
                    return None

            with mock.patch.object(chatgpt_web, "ChatGPTWebSession", FakeSession):
                out = youtube_pack.make_youtube_pack(
                    str(video),
                    payload={"name": "Đuổi oan con dâu 10 năm",
                             "auto_youtube_thumbnail": True,
                             "auto_youtube_description": True},
                    record={"thumbnails": [{"text": "ĐUỔI OAN CON DÂU 10 NĂM",
                                            "description": "Bà cầm giấy nợ"}]},
                    cfg={"dang_youtube": {"auto_thumbnail": True,
                                          "auto_description": True,
                                          "browser_profile": "browser_profile_chatgpt"}},
                    duration_seconds=6300)
            self.assertTrue(Path(out["thumbnail_path"]).is_file())
            self.assertTrue(Path(out["description_path"]).is_file())
            self.assertIn("kể chuyện đêm khuya", out["description"].lower())
            self.assertTrue(raw.is_file())

    def test_attach_bo_qua_khi_tat(self):
        with mock.patch.object(manual_api, "_load_cfg",
                               return_value={"dang_youtube": {
                                   "auto_thumbnail": False,
                                   "auto_description": False}}):
            out = manual_api._attach_youtube_pack(
                "x.mp4", {"auto_youtube_thumbnail": False,
                          "auto_youtube_description": False})
        self.assertEqual(out, {})

    def test_attach_khong_mo_chatgpt_trong_unittest(self):
        with mock.patch.object(manual_api, "_load_cfg",
                               return_value={"dang_youtube": {
                                   "auto_thumbnail": True,
                                   "auto_description": True}}), \
             mock.patch("autodub.youtube_pack.make_youtube_pack") as maker:
            out = manual_api._attach_youtube_pack("x.mp4", {"name": "Test"})
        maker.assert_not_called()
        self.assertEqual(out, {})

    def test_metadata_co_thumbnail(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "phim.mp4")
            Path(video).write_bytes(b"x")
            path = manual_api._save_youtube_metadata(video, {
                "name": "Tập test",
                "youtube_description": "Mô tả",
                "thumbnail_path": os.path.join(tmp, "thumbnail.jpg"),
            })
            self.assertTrue(path.endswith(".youtube.json"))
            text = Path(path).read_text(encoding="utf-8")
            self.assertIn("thumbnail.jpg", text)


if __name__ == "__main__":
    unittest.main()
