"""Kiểm tra khâu gộp câu nguồn - phần chống lỗi 'nội dung trôi khỏi mốc thời gian'.

Bộ chấm câu của ASR đặt dấu sai chỗ, cắt ngang giữa từ ghép và giữa mệnh đề.
Nếu để nguyên, model dịch phải tự ghép lại theo nghĩa rồi rải nội dung sang các
dòng khác, làm lời thoại phát lệch khỏi hình hàng chục giây.

Chạy: python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub.srt_utils import (Segment, prepare_source_segments_for_translation,
                               repair_asr_punctuation, _source_unit_complete,
                               _starts_like_continuation)


def seg(index: int, start: float, end: float, text: str) -> Segment:
    return Segment(index, start, end, text)


class SuaDauCauSai(unittest.TestCase):
    def test_bo_dau_phay_chen_giua_tu_ghep(self):
        """"他，们" là dấu phẩy đặt sai chứ không phải chỗ ngắt hơi."""
        self.assertEqual(repair_asr_punctuation("他，们快不行了。"),
                         "他们快不行了。")

    def test_bo_dau_phay_dung_sau_hu_tu(self):
        self.assertEqual(repair_asr_punctuation("我说了你拦不，住我"),
                         "我说了你拦不住我")

    def test_bo_dau_ket_cau_chen_giua_tu_ghep(self):
        """Dấu chấm giữa từ ghép tai hại hơn dấu phẩy vì bước tách câu cắt đúng đó."""
        self.assertEqual(repair_asr_punctuation("他。们来了"), "他们来了")

    def test_giu_nguyen_dau_cau_dung_cho(self):
        for text in ("你走吧。", "那你回来干什么？", "我买你这条消息，情报上道五十白抄。"):
            self.assertEqual(repair_asr_punctuation(text), text)

    def test_khong_dung_cham_toi_van_ban_khong_phai_chu_han(self):
        text = "Xin chào, các bạn."
        self.assertEqual(repair_asr_punctuation(text), text)


class PhatHienDongVun(unittest.TestCase):
    def test_dong_ket_thuc_bang_hu_tu_la_chua_tron_ven(self):
        """"季秋这里只？" có dấu hỏi nhưng câu chưa xong."""
        self.assertFalse(_source_unit_complete("操。季秋这里只？"))

    def test_dong_mo_dau_bang_hau_to_la_doan_noi_tiep(self):
        for text in ("个妇女多嘴", "们怎么", "员不能索取小费", "右会开放点餐"):
            self.assertTrue(_starts_like_continuation(text), text)

    def test_cau_tron_ven_van_duoc_nhan_dung(self):
        self.assertTrue(_source_unit_complete("我穿越妖魔，世界成了这方世界的神命。"))
        self.assertFalse(_starts_like_continuation("我穿越妖魔。"))


class GopLaiTruocKhiDich(unittest.TestCase):
    def test_gop_manh_vun_thanh_cau_tron_ven(self):
        segs = [
            seg(1, 0.0, 2.0, "基英支。援还有多久他，们快不行了。"),
            seg(2, 2.0, 4.0, "个妇女多嘴我买你这条消息。"),
        ]
        out = prepare_source_segments_for_translation(segs)
        self.assertEqual(len(out), 1)
        self.assertNotIn("，们", out[0].text)

    def test_khung_thoi_gian_bao_tron_cac_manh_da_gop(self):
        segs = [
            seg(1, 10.0, 12.0, "我说了你拦不，住我是神"),
            seg(2, 12.0, 15.0, "们怎么会这样。"),
        ]
        out = prepare_source_segments_for_translation(segs)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0].start, 10.0)
        self.assertAlmostEqual(out[0].end, 15.0)

    def test_khong_gop_hai_cau_doc_lap_tron_ven(self):
        segs = [
            seg(1, 0.0, 2.0, "你走吧。"),
            seg(2, 2.0, 4.0, "我穿越妖魔，成了这方世界的神明。"),
        ]
        out = prepare_source_segments_for_translation(segs)
        self.assertEqual(len(out), 2)

    def test_danh_so_lai_lien_tuc_sau_khi_gop(self):
        segs = [
            seg(1, 0.0, 2.0, "你走吧。"),
            seg(2, 2.0, 4.0, "他，们来了。"),
            seg(3, 4.0, 6.0, "我知道了。"),
        ]
        out = prepare_source_segments_for_translation(segs)
        self.assertEqual([s.index for s in out], list(range(1, len(out) + 1)))


class GiuNguyenNoiDung(unittest.TestCase):
    def test_khong_lam_mat_chu_khi_gop(self):
        """Gộp chỉ được nối chuỗi, tuyệt đối không đánh rơi chữ nào."""
        segs = [
            seg(1, 0.0, 2.0, "基英支。援还有多久他，们快不行了。"),
            seg(2, 2.0, 4.0, "个妇女多嘴我买你这条消息。"),
        ]
        out = prepare_source_segments_for_translation(segs)
        goc = "".join(c for s in segs for c in s.text if "\u4e00" <= c <= "\u9fff")
        sau = "".join(c for s in out for c in s.text if "\u4e00" <= c <= "\u9fff")
        self.assertEqual(goc, sau)


if __name__ == "__main__":
    unittest.main(verbosity=1)
