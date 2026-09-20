"""Reuse an existing .vi.srt when CapCut recut changes cue count by a few lines."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub.srt_utils import Segment
from autodub.translate.reuse import (
    align_vi_to_source, apply_aligned_vi, reuse_translated_cues,
)
from autodub.translate.cjk_residue import leftover_cjk_indices


def _src(times_text):
    return [Segment(i + 1, a, b, zh) for i, (a, b, zh) in enumerate(times_text)]


def _vi(times_text):
    segs = _src(times_text)
    for s in segs:
        s.semantic_group = "g"
    return segs


class ReuseDongHo(unittest.TestCase):
    def test_lech_mot_dong_chi_dich_dong_moi(self):
        src = _src([
            (0, 1, "你好"),
            (1, 2, "他来了"),
            (2, 3, "再见"),
            (3, 4, "新增一句"),
        ])
        vi = _vi([
            (0, 1, "Xin chào"),
            (1, 2, "Nó tới rồi"),
            (2, 3, "Tạm biệt"),
        ])
        rows = [{"vi": ""} for _ in src]
        ok, dirty, copied, repaired = reuse_translated_cues(src, vi, rows)
        self.assertTrue(ok)
        self.assertEqual(copied, 3)
        self.assertEqual(src[0].text, "Xin chào")
        self.assertEqual(src[1].text, "Nó tới rồi")
        self.assertEqual(src[2].text, "Tạm biệt")
        self.assertEqual(src[3].text, "新增一句")
        self.assertEqual(dirty, [3])
        self.assertEqual(rows[0]["vi"], "Xin chào")
        self.assertEqual(repaired, 0)

    def test_cat_mot_cau_thanh_hai_giu_ban_viet(self):
        src = _src([
            (0.0, 1.2, "你好啊"),
            (1.2, 2.5, "你好啊"),
            (3.0, 4.0, "再见"),
        ])
        vi = _vi([
            (0.0, 2.5, "Xin chào nhé"),
            (3.0, 4.0, "Tạm biệt"),
        ])
        ok, dirty, copied, _ = reuse_translated_cues(src, vi)
        self.assertTrue(ok)
        self.assertEqual(copied, 2)
        self.assertEqual(src[0].text, "Xin chào nhé")
        self.assertEqual(src[2].text, "Tạm biệt")
        self.assertEqual(dirty, [1], "nửa sau của câu bị cắt phải dịch riêng, không ném cả phim")

    def test_gop_nhieu_vi_cue_giu_ten_tu_tat_ca_cue(self):
        src = [Segment(1, 0.0, 2.0, "七月回来了")]
        vi = _vi([
            (0.0, 1.0, "Xin chào"),
            (1.0, 2.0, "七月 đã về."),
        ])
        vi[1].allowed_source_names = ("七月",)
        rows = [{"src": src[0].text, "vi": ""}]
        self.assertEqual(apply_aligned_vi(src, vi, [[0, 1]], rows), 1)
        self.assertEqual(src[0].allowed_source_names, ("七月",))
        self.assertEqual(rows[0]["allowed_source_names"], ("七月",))
        self.assertEqual(leftover_cjk_indices(src), [])

    def test_dong_ho_lech_xa_thi_bo_reuse(self):
        src = _src([
            (0, 1, "你好"),
            (1, 2, "再见"),
        ])
        vi = _vi([
            (60, 61, "Xin chào"),
            (61, 62, "Tạm biệt"),
        ])
        ok, dirty, copied, _ = reuse_translated_cues(src, vi)
        self.assertFalse(ok)
        self.assertEqual(copied, 0)
        self.assertEqual(dirty, [0, 1])
        self.assertEqual(src[0].text, "你好")

    def test_bang_so_dong_van_va_residue(self):
        src = _src([
            (0, 1, "他迟迟不来"),
            (1, 2, "再见"),
        ])
        vi = _vi([
            (0, 1, "Nó 迟迟 không tới."),
            (1, 2, "Tạm biệt"),
        ])
        ok, dirty, copied, repaired = reuse_translated_cues(src, vi)
        self.assertTrue(ok)
        self.assertEqual(copied, 2)
        self.assertEqual(repaired, 1)
        self.assertEqual(src[0].text, "Nó mãi không tới.")
        self.assertEqual(dirty, [])

    def test_cung_so_dong_dong_ho_tts_van_giu_theo_chi_so(self):
        src = _src([(float(i) * 2.0, float(i) * 2.0 + 1.4, f"源{i}")
                    for i in range(12)])
        vi = _vi([(float(i) * 2.0 + 5.0, float(i) * 2.0 + 6.2, f"Việt {i}")
                  for i in range(12)])
        ok, dirty, copied, _ = reuse_translated_cues(src, vi)
        self.assertTrue(ok)
        self.assertEqual(copied, 12)
        self.assertEqual(src[0].text, "Việt 0")
        self.assertEqual(src[11].text, "Việt 11")
        self.assertEqual(dirty, [])

    def test_cung_so_dong_lech_mot_cue_van_giu_theo_chi_so(self):
        """Picture-clock remap shifts times by ~1 cue; texts stay 1:1 by index.

        Clock-align would copy Việt 1 onto source 0 because src[0] overlaps
        vi[1]. keep_source_timing equal count must not do that.
        """
        src = _src([(float(i) * 2.0 + 1.6, float(i) * 2.0 + 3.0, f"源{i}")
                    for i in range(12)])
        vi = _vi([(float(i) * 2.0, float(i) * 2.0 + 1.4, f"Việt {i}")
                  for i in range(12)])
        ok, dirty, copied, _ = reuse_translated_cues(src, vi)
        self.assertTrue(ok)
        self.assertEqual(copied, 12)
        self.assertEqual(src[0].text, "Việt 0")
        self.assertEqual(src[1].text, "Việt 1")
        self.assertEqual(src[11].text, "Việt 11")
        self.assertEqual(dirty, [])

    def test_cung_so_dong_lech_60s_van_bo_reuse(self):
        src = _src([(float(i), float(i) + 0.8, "源") for i in range(12)])
        vi = _vi([(float(i) + 60.0, float(i) + 60.8, f"Việt {i}")
                  for i in range(12)])
        ok, dirty, copied, _ = reuse_translated_cues(src, vi)
        self.assertFalse(ok)
        self.assertEqual(copied, 0)
        self.assertEqual(src[0].text, "源")
        self.assertEqual(dirty, list(range(12)))

    def test_cung_so_dong_nho_lech_deu_van_giu_theo_chi_so(self):
        src = _src([(float(i) * 2.0 + 1.6, float(i) * 2.0 + 3.0, f"源{i}")
                    for i in range(3)])
        vi = _vi([(float(i) * 2.0, float(i) * 2.0 + 1.4, f"Việt {i}")
                  for i in range(3)])
        ok, dirty, copied, _ = reuse_translated_cues(src, vi)
        self.assertTrue(ok)
        self.assertEqual(copied, 3)
        self.assertEqual(src[0].text, "Việt 0")
        self.assertEqual(src[2].text, "Việt 2")
        self.assertEqual(dirty, [])

    def test_cung_so_dong_cat_lai_ben_trong_thi_khop_dong_ho(self):
        src = _src([(float(i) * 2.0, float(i) * 2.0 + 1.4, f"源{i}")
                    for i in range(10)])
        vi = _vi([(float(i) * 2.0, float(i) * 2.0 + 1.4, f"Việt {i}")
                  for i in range(10)])
        src[2] = Segment(3, 5.8, 7.2, "源2")
        ok, dirty, copied, _ = reuse_translated_cues(src, vi)
        self.assertTrue(ok)
        self.assertEqual(src[0].text, "Việt 0")
        self.assertEqual(src[2].text, "Việt 3")
        self.assertNotEqual(src[2].text, "Việt 2")
        self.assertEqual(src[1].text, "Việt 1")

    def test_align_giu_90_phan_tram(self):
        src = [Segment(i + 1, float(i), float(i) + 0.8, "源") for i in range(10)]
        vi = [Segment(i + 1, float(i), float(i) + 0.8, f"Việt {i}") for i in range(9)]
        assigned = align_vi_to_source(src, vi)
        self.assertIsNotNone(assigned)
        self.assertEqual(sum(1 for row in assigned if row), 9)
        self.assertEqual(assigned[9], [])


class NapSrtLechSoDong(unittest.TestCase):
    def test_giu_cau_goc_khi_lech_so_cue(self):
        from autodub.srt_utils import save_srt_file
        from autodub.server.projects import _load_local_rows_from_srt
        with tempfile.TemporaryDirectory() as td:
            src_p = os.path.join(td, "a.src.srt")
            vi_p = os.path.join(td, "a.vi.srt")
            save_srt_file(src_p, _src([
                (0, 1, "你好"), (1, 2, "再见"), (2, 3, "新增"),
            ]))
            save_srt_file(vi_p, _vi([
                (0, 1, "Xin chào"), (1, 2, "Tạm biệt"),
            ]))
            rows = _load_local_rows_from_srt(src_p, vi_p)
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["src"], "你好")
            self.assertEqual(rows[0]["vi"], "Xin chào")
            self.assertEqual(rows[2]["src"], "新增")
            self.assertTrue(rows[2]["src"])


if __name__ == "__main__":
    unittest.main()
