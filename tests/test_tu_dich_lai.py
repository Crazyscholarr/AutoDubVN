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

from autodub.server.pipeline import _hydrate_vi_onto_source, _tu_dich_lai_dong_tieng_trung
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

    def test_hon_hop_tu_trung_con_sot_khong_goi_api(self):
        segs, rows = _tao(["Nó 迟迟 không tới.", "Xin chào"])
        def dich(_subset):
            raise AssertionError("từ chức năng còn sót phải vá local, không đốt API")
        changed = _tu_dich_lai_dong_tieng_trung(
            "dựng giọng đọc", segs, rows, dich, log=_im_lang)
        self.assertTrue(changed)
        self.assertEqual(segs[0].text, "Nó mãi không tới.")
        self.assertEqual(rows[0]["vi"], "Nó mãi không tới.")
        self.assertEqual(rows[1]["vi"], "Xin chào")

    def test_dich_lai_gui_cau_nguon_chu_khong_gui_cau_viet_lan_han(self):
        segs = [Segment(1, 0, 1, "xin各位 hãy bình tĩnh.")]
        rows = [{"start": 0.0, "end": 1.0, "src": "飞机到底怎么了？",
                 "vi": segs[0].text}]
        seen = []

        def dich(subset):
            seen.append([s.text for s in subset])
            for s in subset:
                s.text = "Máy bay rốt cuộc bị sao vậy?"

        changed = _tu_dich_lai_dong_tieng_trung(
            "dựng giọng đọc", segs, rows, dich, log=_im_lang)
        self.assertTrue(changed)
        self.assertEqual(seen, [["飞机到底怎么了？"]])
        self.assertEqual(segs[0].text, "Máy bay rốt cuộc bị sao vậy?")
        self.assertEqual(rows[0]["vi"], "Máy bay rốt cuộc bị sao vậy?")

    def test_tu_chuc_nang_trung_nguon_duoc_va_local(self):
        segs = [Segment(1, 0, 1, "không bị coi là trượt考核.")]
        rows = [{"start": 0.0, "end": 1.0,
                 "src": "即便后续落败也不算考核失败。", "vi": segs[0].text}]

        def dich(_subset):
            raise AssertionError("từ 考核 có trong nguồn thì vá local, không đốt API")

        changed = _tu_dich_lai_dong_tieng_trung(
            "dựng giọng đọc", segs, rows, dich, log=_im_lang)
        self.assertTrue(changed)
        self.assertEqual(segs[0].text, "không bị coi là trượt sát hạch.")

    def test_bv1za_tu_chuc_nang_khong_goi_api(self):
        segs = [
            Segment(734, 0, 1, "Cũng không dám nhiều lời啰嗦 nữa."),
            Segment(738, 1, 2, "Người giải quyết tình huống尴尬này là người chăm sóc."),
        ]
        rows = [
            {"start": 0.0, "end": 1.0, "src": "也不敢再多批啰嗦。", "vi": segs[0].text},
            {"start": 1.0, "end": 2.0, "src": "解决这个尴尬场面的是照管。",
             "vi": segs[1].text},
        ]

        def dich(_subset):
            raise AssertionError("啰嗦/尴尬 có trong nguồn thì vá local, không đốt API")

        changed = _tu_dich_lai_dong_tieng_trung(
            "dựng giọng đọc", segs, rows, dich, log=_im_lang)
        self.assertTrue(changed)
        self.assertNotIn("啰嗦", segs[0].text)
        self.assertNotIn("尴尬", segs[1].text)
        self.assertIn("lảm nhảm", segs[0].text)
        self.assertIn("khó xử", segs[1].text)

    def test_ghi_nguoc_ca_khi_con_ten_rieng_trung(self):
        segs, rows = _tao(["七月 đã về nhà rồi."])
        segs[0].allowed_source_names = ("七月",)
        def dich(_subset):
            raise AssertionError("tên giữ nguồn đã được phép thì không gọi dịch")
        self.assertFalse(_tu_dich_lai_dong_tieng_trung(
            "dựng giọng đọc", segs, rows, dich, log=_im_lang))
        self.assertEqual(rows[0]["vi"], "七月 đã về nhà rồi.")

    def test_ghi_nguoc_kem_metadata_ten_nguon(self):
        segs, rows = _tao(["他们快不行了"])

        def dich(subset):
            for s in subset:
                s.text = "Bọn họ sắp không trụ nổi rồi"
                s.allowed_source_names = ("七月",)

        self.assertTrue(_tu_dich_lai_dong_tieng_trung(
            "dựng giọng đọc", segs, rows, dich, log=_im_lang))
        self.assertEqual(rows[0]["allowed_source_names"], ("七月",))


class PipelineReuseLechDong(unittest.TestCase):
    def test_khong_dich_lai_ca_phim_khi_lech_so_dong(self):
        import inspect
        from autodub.server import pipeline
        src = inspect.getsource(pipeline)
        self.assertIn("reuse_translated_cues", src)
        self.assertNotIn("dịch lại để tránh TTS bị câm", src)
        self.assertIn("khớp bản dịch cũ theo đồng hồ", src.lower())


class HydrateBanDichCu(unittest.TestCase):
    def test_chi_va_dong_ban_khong_doi_dong_sach(self):
        src = [
            Segment(1, 0, 1, "你好"),
            Segment(2, 1, 2, "他迟迟不来"),
            Segment(3, 2, 3, "再见"),
        ]
        vi = [
            Segment(1, 0, 1, "Xin chào"),
            Segment(2, 1, 2, "Nó 迟迟 không tới."),
            Segment(3, 2, 3, "Tạm biệt"),
        ]
        for s in vi:
            s.semantic_group = "g1"
        rows = [{"start": s.start, "end": s.end, "src": a.text, "vi": ""}
                for a, s in zip(src, vi)]
        n = _hydrate_vi_onto_source(src, vi, rows)
        self.assertEqual(n, 1)
        self.assertEqual(src[0].text, "Xin chào")
        self.assertEqual(src[1].text, "Nó mãi không tới.")
        self.assertEqual(src[2].text, "Tạm biệt")
        self.assertEqual(rows[1]["vi"], "Nó mãi không tới.")
        self.assertEqual(src[0].semantic_group, "g1")


if __name__ == "__main__":
    unittest.main(verbosity=1)
