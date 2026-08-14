"""Kiểm tra cơ chế TỰ DỊCH LẠI các dòng còn tiếng Trung (lô dịch trả thiếu).

Trước đây gặp dòng còn tiếng Trung là pipeline dừng bằng RuntimeError và người
dùng phải tự chạy lại bước Dịch. Giờ chương trình gom đúng các dòng bẩn, gọi
lại provider tối đa 2 lần, chỉ khi vẫn hỏng mới chặn như cũ.

Chạy: python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub.server.pipeline import _tu_dich_lai_dong_tieng_trung
from autodub.srt_utils import Segment


def _im_lang(*_a, **_k):
    pass


def _tao(texts):
    segs = [Segment(i + 1, float(i), float(i + 1), t)
            for i, t in enumerate(texts)]
    rows = [{"start": float(i), "end": float(i + 1), "src": "", "vi": t}
            for i, t in enumerate(texts)]
    return segs, rows


class TuDichLaiDongTiengTrung(unittest.TestCase):
    def test_sua_duoc_dong_ban_va_ghi_nguoc_vao_rows(self):
        segs, rows = _tao(["Xin chào", "他们快不行了", "Tạm biệt"])
        goi = []

        def dich(subset):
            goi.append([s.index for s in subset])
            for s in subset:
                s.text = "Bọn họ sắp không trụ nổi rồi"

        changed = _tu_dich_lai_dong_tieng_trung(
            "dựng giọng đọc", segs, rows, dich, log=_im_lang)
        self.assertTrue(changed)
        self.assertEqual(goi, [[2]], "chỉ dịch lại đúng dòng bẩn, không dịch cả lô")
        self.assertEqual(rows[1]["vi"], "Bọn họ sắp không trụ nổi rồi")
        self.assertEqual(rows[0]["vi"], "Xin chào", "dòng sạch phải giữ nguyên")

    def test_khong_co_dong_ban_thi_khong_goi_dich(self):
        segs, rows = _tao(["Một", "Hai"])
        def dich(_subset):
            raise AssertionError("không được gọi khi mọi dòng đã sạch")
        self.assertFalse(_tu_dich_lai_dong_tieng_trung(
            "dựng giọng đọc", segs, rows, dich, log=_im_lang))

    def test_van_ban_sau_2_lan_thi_chan_nhu_cu(self):
        segs, rows = _tao(["个妇女多嘴"])
        goi = []
        def dich_hong(subset):
            goi.append(len(subset))          # không sửa gì -> vẫn bẩn
        with self.assertRaises(RuntimeError) as ctx:
            _tu_dich_lai_dong_tieng_trung(
                "dựng giọng đọc", segs, rows, dich_hong, log=_im_lang)
        self.assertEqual(len(goi), 2, "phải thử lại đúng 2 lần trước khi chặn")
        self.assertIn("còn tiếng Trung", str(ctx.exception))
        self.assertIn("dựng giọng đọc", str(ctx.exception))

    def test_loi_khi_dich_khong_lam_sap_ma_van_chan_dung(self):
        segs, rows = _tao(["季秋这里只"])
        def dich_nem_loi(_subset):
            raise ConnectionError("mạng rớt")
        with self.assertRaises(RuntimeError):
            _tu_dich_lai_dong_tieng_trung(
                "xuất video", segs, rows, dich_nem_loi, log=_im_lang)

    def test_raise_on_fail_false_thi_chi_canh_bao(self):
        """Ngay sau bước Dịch chưa chặn - bước TTS sẽ thử thêm lần nữa."""
        segs, rows = _tao(["个妇女多嘴"])
        changed = _tu_dich_lai_dong_tieng_trung(
            "lưu bản dịch", segs, rows, lambda s: None,
            raise_on_fail=False, log=_im_lang)
        self.assertFalse(changed)

    def test_tat_tinh_nang_thi_chan_ngay_khong_goi_dich(self):
        segs, rows = _tao(["他们"])
        def dich(_subset):
            raise AssertionError("auto_retranslate=false thì không được tự dịch")
        with self.assertRaises(RuntimeError) as ctx:
            _tu_dich_lai_dong_tieng_trung(
                "dựng giọng đọc", segs, rows, dich,
                enabled=False, log=_im_lang)
        self.assertNotIn("đã tự dịch lại", str(ctx.exception))

    def test_sua_duoc_o_lan_thu_hai(self):
        segs, rows = _tao(["他们快不行了"])
        dem = {"n": 0}
        def dich_lan_hai_moi_duoc(subset):
            dem["n"] += 1
            if dem["n"] >= 2:
                for s in subset:
                    s.text = "Bọn họ sắp không trụ nổi rồi"
        changed = _tu_dich_lai_dong_tieng_trung(
            "dựng giọng đọc", segs, rows, dich_lan_hai_moi_duoc, log=_im_lang)
        self.assertTrue(changed)
        self.assertEqual(dem["n"], 2)
        self.assertEqual(rows[0]["vi"], "Bọn họ sắp không trụ nổi rồi")


if __name__ == "__main__":
    unittest.main(verbosity=1)
