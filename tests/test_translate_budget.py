"""Kiểm tra ràng buộc độ dài bản dịch - phần chống lỗi 'tiếng chạy trước hình'.

Chạy: python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub import translate as T
from autodub.srt_utils import Segment


def seg(start: float, end: float, text: str = "nguon") -> Segment:
    return Segment(1, start, end, text)


class CharBudget(unittest.TestCase):
    def test_budget_bam_sat_moc_chars_per_sec(self):
        """Ngân sách không được nới rộng quá mốc người dùng đặt.

        Từng để margin 1.20 nên mốc 15 c/s thành 18 c/s ngay từ đầu.
        """
        s = seg(0.0, 10.0)
        budget = T.char_budget(s, 15.0)
        self.assertLessEqual(budget / 10.0, 15.0 * 1.1)

    def test_cau_ngan_van_co_san_toi_thieu(self):
        """Câu rất ngắn không bị ép xuống mức không thể diễn đạt."""
        self.assertGreaterEqual(T.char_budget(seg(0.0, 0.3), 15.0),
                                T.TRANSLATION_MIN_CHARS)

    def test_tat_rang_buoc_khi_cps_bang_khong(self):
        self.assertEqual(T.char_budget(seg(0.0, 5.0), 0.0), 0)


class PhatHienCauQuaDai(unittest.TestCase):
    """Vùng 18-22 ký tự/giây là nơi bản dịch thực tế rơi vào và trước đây lọt lưới."""

    def test_bat_duoc_cau_22_ky_tu_moi_giay(self):
        s = seg(0.0, 10.0)
        self.assertTrue(T._too_long_for_tts(s, "x" * 220, 15.0))

    def test_khong_bat_cau_dung_nhip(self):
        s = seg(0.0, 10.0)
        self.assertFalse(T._too_long_for_tts(s, "x" * 150, 15.0))


class BoChanKhiRutGon(unittest.TestCase):
    """Rút gọn từng làm hỏng nghĩa, nên các bộ chặn này phải chắc."""

    def setUp(self):
        self.seg = seg(0.0, 4.0)

    def test_tu_choi_khi_danh_roi_ten_rieng(self):
        goc = "Cậu ta bảo Diệp Vân mau chạy khỏi đây ngay lập tức"
        rut = "Cậu ta bảo mau chạy đi"
        self.assertFalse(T._accept_shortened(goc, rut, self.seg, 15.0))

    def test_tu_choi_khi_danh_roi_con_so(self):
        goc = "Chúng ta chỉ còn đúng 7 ngày trước khi cổng đóng lại"
        rut = "Chúng ta chỉ còn vài ngày thôi"
        self.assertFalse(T._accept_shortened(goc, rut, self.seg, 15.0))

    def test_tu_choi_khi_rut_qua_tay(self):
        goc = "Hắn nói rằng bọn nó đã bỏ đi từ sáng sớm rồi"
        rut = "Bọn nó đi"
        self.assertFalse(T._accept_shortened(goc, rut, self.seg, 15.0))

    def test_chap_nhan_ban_rut_gon_hop_le(self):
        goc = "Thật ra thì tôi cũng không biết chuyện đó xảy ra như thế nào cả"
        rut = "Tôi cũng không biết chuyện đó xảy ra sao"
        self.assertTrue(T._accept_shortened(goc, rut, self.seg, 15.0))

    def test_tu_choi_khi_khong_ngan_hon(self):
        self.assertFalse(T._accept_shortened("abc", "abcd", self.seg, 15.0))


class ApLucDoc(unittest.TestCase):
    def test_bao_dung_khi_ban_dich_dai_gap_ruoi(self):
        segs = [seg(i * 10.0, i * 10.0 + 10.0, "x" * 225) for i in range(10)]
        st = T.reading_pressure(segs, 15.0)
        self.assertAlmostEqual(st["ratio"], 1.5, places=2)
        self.assertEqual(st["over_lines"], 10)

    def test_khong_bao_dong_khi_vua_nhip(self):
        segs = [seg(i * 10.0, i * 10.0 + 10.0, "x" * 150) for i in range(10)]
        st = T.reading_pressure(segs, 15.0)
        self.assertAlmostEqual(st["ratio"], 1.0, places=2)
        self.assertEqual(st["over_lines"], 0)

    def test_dem_dung_dong_vuot_tran_toc_do(self):
        segs = [seg(0.0, 10.0, "x" * 300), seg(10.0, 20.0, "x" * 150)]
        st = T.reading_pressure(segs, 15.0)
        self.assertEqual(st["hopeless_lines"], 1)

    def test_bo_qua_dong_rong(self):
        st = T.reading_pressure([seg(0.0, 5.0, "")], 15.0)
        self.assertEqual(st["lines"], 0)


if __name__ == "__main__":
    unittest.main()
