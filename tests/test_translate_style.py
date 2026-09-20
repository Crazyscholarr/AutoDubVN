"""Khóa vai lồng tiếng: chat Gemini mới không được rơi về dịch văn bản."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub import translate as T
from autodub.srt_utils import Segment


def _chunk(*texts):
    return [Segment(i, 0.0, 1.2, t) for i, t in enumerate(texts, 1)]


class KhoaLongTieng(unittest.TestCase):
    def test_cache_phien_ban_moi(self):
        self.assertEqual(T.TRANSLATION_CACHE_VERSION, "vi-dub-spoken-v8")
        self.assertIn("lồng tiếng", T.SYSTEM_INSTRUCTION.lower())
        self.assertIn("KHÓA NHIỆM VỤ", T.STYLE_LOCK)

    def test_brief_yeu_cau_xac_nhan(self):
        brief = T.session_brief(
            "Phim đang lồng tiếng: Fang Yuan.",
            "Quy uoc ten: Phương Nguyên.")
        self.assertIn("SẴN SÀNG LỒNG TIẾNG", brief)
        self.assertIn("Fang Yuan", brief)
        self.assertIn("Phương Nguyên", brief)
        self.assertIn("biên kịch lồng tiếng", brief.lower())

    def test_brief_confirmed_khong_dau(self):
        self.assertTrue(T.brief_confirmed("SẴN SÀNG LỒNG TIẾNG"))
        self.assertTrue(T.brief_confirmed("Ok, San sang long tieng."))
        self.assertFalse(T.brief_confirmed("Đã hiểu, cứ gửi văn bản cần dịch."))

    def test_film_hint_bo_ma_bilibili(self):
        hint = T.build_film_hint(
            "合集篇：方源觉醒了 [BV1rX8c6DEgv]")
        self.assertIn("Phim đang lồng tiếng", hint)
        self.assertNotIn("BV1rX8c6DEgv", hint)
        self.assertIn("không phải bài báo", hint)
        self.assertEqual(T.build_film_hint("  "), "")

    def test_prompt_luon_gan_khoa_ke_ca_hoi_bo_sung(self):
        chunk = _chunk("我觉醒了，", "暂定为A级的天赋。")
        film = T.build_film_hint("方源觉醒")
        for again in (False, True):
            prompt = T._build_prompt(
                chunk, [1, 2], ["Tôi tỉnh rồi"], again=again,
                film_hint=film)
            self.assertIn("KHÓA NHIỆM VỤ", prompt)
            self.assertIn("LỒNG TIẾNG", prompt)
            self.assertIn("Phim đang lồng tiếng", prompt)
            self.assertIn("[1]", prompt)
            self.assertIn("[2]", prompt)
            self.assertIn("CẤM ngắt", prompt)

    def test_prompt_rewrite_spoken(self):
        chunk = _chunk("但是", "同时")
        prompt = T._build_prompt(
            chunk, [1, 2], [], rewrite_spoken=True,
            film_hint="Phim đang lồng tiếng: test.")
        self.assertIn("VĂN BẢN", prompt)
        self.assertIn("KHÓA NHIỆM VỤ", prompt)

    def test_proofread_khong_gieo_adr(self):
        chunk = _chunk("sao chân ta không bằng")
        prompt = T._build_prompt(chunk, [1], [], proofread=True)
        self.assertNotIn("KHÓA NHIỆM VỤ", prompt)
        self.assertIn("NGHE NHẦM", prompt)

    def test_spoken_header_thu_tu(self):
        head = T.spoken_header("Phim X", "Tên Y")
        self.assertTrue(head.startswith("KHÓA NHIỆM VỤ"))
        self.assertLess(head.find("Phim X"), head.find("Tên Y"))

    def test_looks_like_document(self):
        essay = [
            "Tuy nhiên hắn vẫn chưa tỉnh.",
            "Đồng thời linh hồn bắt đầu dao động.",
            "Hơn nữa đây là dấu hiệu nguy hiểm.",
            "Do đó hắn phải rời đi ngay.",
        ]
        spoken = [
            "Tôi tỉnh rồi.",
            "Tạm định cấp A.",
            "Thiên phú Barrett.",
            "Ngươi im đi.",
        ]
        self.assertTrue(T.looks_like_document_vi(essay))
        self.assertFalse(T.looks_like_document_vi(spoken))
        self.assertFalse(T.looks_like_document_vi(essay[:2]))

    def test_shorten_prompt_van_khoa_thoai(self):
        chunk = _chunk("我醒了")
        prompt = T._build_shorten_prompt(chunk, [1], {1: "Tôi đã tỉnh dậy rồi đó"}, 15.0)
        self.assertIn("KHÓA NHIỆM VỤ", prompt)
        self.assertIn("THOẠI", prompt)

    def test_nhan_dong_danh_so_ngoac_vuong(self):
        text = "\n".join(f"[{i}] Câu thoại {i}" for i in range(1, 6))
        self.assertTrue(T._co_nhieu_dong_danh_so(text))

    def test_chu_ky_browser_nhan_film_hint(self):
        import inspect
        params = inspect.signature(T.translate_via_browser).parameters
        self.assertIn("film_hint", params)
        self.assertIn("film_hint", inspect.signature(T.translate_segments).parameters)
        self.assertIn("translation_cfg", params)
        self.assertIn("translation_cfg", inspect.signature(T.translate_segments).parameters)


if __name__ == "__main__":
    unittest.main()
