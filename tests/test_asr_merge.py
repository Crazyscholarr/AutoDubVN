"""Ghép phụ đề vá lỗ hổng — logic thuần, không cần model ASR."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub.asr import drop_hallucinations, merge_new_segments
from autodub.srt_utils import Segment


def seg(index, start, text, end=None):
    return Segment(index, start, start + 1.0 if end is None else end, text)


class MergeNewSegments(unittest.TestCase):
    def test_bo_trung_cung_chu_va_gan_moc(self):
        base = [seg(1, 1.0, "Xin chào")]
        extra = [seg(2, 1.2, "Xin chào"), seg(3, 10.0, "Tạm biệt")]
        out = merge_new_segments(base, extra, tolerance=0.4)
        self.assertEqual([s.text for s in out], ["Xin chào", "Tạm biệt"])
        self.assertEqual([s.index for s in out], [1, 2])

    def test_cung_chu_nhung_xa_moc_thi_giu(self):
        base = [seg(1, 1.0, "Xin chào")]
        extra = [seg(2, 5.0, "Xin chào")]
        out = merge_new_segments(base, extra, tolerance=0.4)
        self.assertEqual(len(out), 2)
        self.assertEqual([s.start for s in out], [1.0, 5.0])

    def test_sap_theo_thoi_gian_va_danh_lai_so(self):
        base = [seg(1, 20.0, "Sau")]
        extra = [seg(9, 1.0, "Trước")]
        out = merge_new_segments(base, extra)
        self.assertEqual([s.text for s in out], ["Trước", "Sau"])
        self.assertEqual([s.index for s in out], [1, 2])


class DropHallucinations(unittest.TestCase):
    def test_bo_cau_quang_cao_lap(self):
        segs = [
            seg(1, 0.0, "Hãy subscribe kênh", end=5.0),
            seg(2, 10.0, "Hãy subscribe kênh", end=15.0),
            seg(3, 20.0, "Xin chào mọi người", end=22.0),
        ]
        kept, removed = drop_hallucinations(segs, min_repeat=3, min_dur=4.0)
        self.assertEqual([s.text for s in kept], ["Xin chào mọi người"])
        self.assertEqual(len(removed), 2)
        self.assertEqual([s.index for s in kept], [1])
