"""Chia lại SRT Việt theo ngữ pháp, validator AI, fallback."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub.srt_utils import Segment, timestamp_to_seconds, seconds_to_timestamp
from autodub.vi_beautify import beautify_windows, _parse_rows
from autodub.vi_cues import finalize_spoken_vi_cues
from autodub.vi_reflow import (
    bad_break_score, content_equivalent, detect_bad_windows, group_cues,
    reflow_spoken_vi, tidy_cue, validate_window,
)


def cue(index, start, end, text, speaker=None):
    if isinstance(start, str):
        start = timestamp_to_seconds(start)
    if isinstance(end, str):
        end = timestamp_to_seconds(end)
    return Segment(index, start, end, text, speaker=speaker)


def user_example():
    return [
        cue(1, "00:00:00,010", "00:00:00,770", "Tôi là Thất Nguyệt"),
        cue(2, "00:00:01,380", "00:00:04,252",
            "Vì không chịu nổi cú sốc khi bố mẹ cùng qua đời, tôi"),
        cue(3, "00:00:04,500", "00:00:05,746", "đã bị bệnh tâm thần."),
        cue(4, "00:00:06,040", "00:00:07,010", "Kể từ ngày đó, tôi"),
        cue(5, "00:00:07,020", "00:00:08,448", "đều mơ cùng một giấc mơ"),
        cue(6, "00:00:09,260", "00:00:10,928", "Chủ nhân thân mến ơi, anh"),
        cue(7, "00:00:11,300", "00:00:13,548",
            "có phải cũng đã sớm chán ngấy đám người đạo đức giả"),
        cue(8, "00:00:14,140", "00:00:15,550", "đến tột cùng này? Hay"),
        cue(9, "00:00:15,780", "00:00:17,572",
            "hãy cùng tôi giải phóng ma tính và"),
        cue(10, "00:00:18,680", "00:00:20,428",
            "nuốt chửng toàn bộ thế gian này đi."),
    ]


class NguPhapVi(unittest.TestCase):
    def test_case1_chu_ngu_vi_ngu(self):
        segs = user_example()
        before = [s.text for s in segs]
        stamps = [(s.start, s.end, s.index) for s in segs]
        n = reflow_spoken_vi(segs)
        self.assertGreater(n, 0)
        self.assertEqual([(s.start, s.end, s.index) for s in segs], stamps)
        self.assertFalse(segs[1].text.endswith(" tôi"))
        self.assertTrue(segs[1].text.endswith(",") or segs[1].text.endswith("đời"))
        self.assertTrue(segs[2].text.lower().startswith("tôi"))
        self.assertIn("tâm thần", segs[2].text)
        self.assertTrue(content_equivalent(before, [s.text for s in segs]))

    def test_case2_lien_tu_cuoi_cue(self):
        segs = user_example()
        reflow_spoken_vi(segs)
        self.assertFalse(segs[7].text.rstrip().endswith("Hay"))
        self.assertTrue(segs[7].text.rstrip().endswith("?"))
        self.assertTrue(segs[8].text.startswith("Hay"))

    def test_case3_cau_hoi_nhieu_cue(self):
        segs = user_example()
        reflow_spoken_vi(segs)
        joined = " ".join(s.text for s in segs[5:9])
        self.assertIn("?", joined)
        self.assertIn("Hay", joined)
        self.assertNotIn("anh có phải", segs[5].text)

    def test_case4_cau_thoai_ngan(self):
        segs = [
            cue(1, 0.0, 0.4, "Anh!"),
            cue(2, 0.6, 1.4, "Đừng."),
        ]
        reflow_spoken_vi(segs)
        self.assertEqual(segs[0].text, "Anh!")
        self.assertEqual(segs[1].text, "Đừng.")

    def test_case5_nhieu_cau_trong_mot_cue(self):
        segs = [
            cue(1, 0.0, 2.5, "Tôi đến đây. Anh đang chờ à?"),
            cue(2, 2.7, 3.4, "Ừ."),
        ]
        reflow_spoken_vi(segs)
        self.assertIn("Tôi đến đây", segs[0].text)
        self.assertEqual(segs[1].text, "Ừ.")

    def test_case6_ten_rieng(self):
        segs = [
            cue(1, 0.0, 1.2, "Lý Bác"),
            cue(2, 1.25, 2.4, "Khởi đã đến."),
        ]
        reflow_spoken_vi(segs)
        joined = " ".join(s.text for s in segs)
        self.assertIn("Lý", joined)
        self.assertIn("Khởi", joined)
        self.assertTrue(content_equivalent(
            ["Lý Bác", "Khởi đã đến."], [s.text for s in segs]))

    def test_case7_con_so(self):
        segs = [
            cue(1, 0.0, 1.1, "Còn thiếu 0,1"),
            cue(2, 1.15, 2.2, "điểm nữa."),
        ]
        reflow_spoken_vi(segs)
        joined = " ".join(s.text for s in segs)
        self.assertIn("0,1", joined)
        self.assertFalse(
            segs[0].text.endswith("0,1") and segs[1].text.startswith("điểm"))

    def test_case8_khoang_nghi_dai(self):
        segs = [
            cue(1, 0.0, 1.0, "Vì anh"),
            cue(2, 4.5, 6.0, "đã đi."),
        ]
        groups = group_cues(segs)
        self.assertEqual(groups, [(0, 1), (1, 2)])
        reflow_spoken_vi(segs)
        self.assertEqual(segs[0].text, "Vì anh")
        self.assertEqual(segs[1].text, "đã đi.")

    def test_case9_hai_nguoi_noi(self):
        segs = [
            cue(1, 0.0, 1.0, "Vì tôi", speaker="A"),
            cue(2, 1.05, 2.2, "đã nghe.", speaker="B"),
        ]
        groups = group_cues(segs)
        self.assertEqual(groups, [(0, 1), (1, 2)])
        reflow_spoken_vi(segs)
        self.assertEqual(segs[0].text, "Vì tôi")
        self.assertEqual(segs[1].text, "đã nghe.")

    def test_case10_file_hang_nghin(self):
        segs = []
        t = 0.0
        for i in range(1, 1201):
            segs.append(Segment(i, t, t + 0.8, f"Câu số {i} đây."))
            t += 1.2
        n = reflow_spoken_vi(segs)
        self.assertEqual(len(segs), 1200)
        self.assertEqual(segs[0].index, 1)
        self.assertEqual(segs[-1].index, 1200)
        self.assertGreaterEqual(n, 0)

    def test_case15_unicode_tieng_viet(self):
        segs = user_example()
        reflow_spoken_vi(segs)
        joined = " ".join(s.text for s in segs)
        self.assertIn("Thất Nguyệt", joined)
        self.assertIn("tâm thần", joined)
        self.assertIn("đạo đức", joined)

    def test_vocative_va_ke_tu_ngay_do(self):
        segs = user_example()
        reflow_spoken_vi(segs)
        self.assertTrue("ơi" in segs[5].text.lower())
        self.assertTrue(segs[6].text.lower().startswith("anh"))
        self.assertFalse(segs[3].text.endswith(" tôi"))
        self.assertTrue(segs[4].text.lower().startswith("tôi"))

    def test_bad_break_detector(self):
        self.assertGreater(
            bad_break_score("qua đời, tôi", "đã bị bệnh tâm thần."), 8)
        self.assertEqual(
            bad_break_score("đến tột cùng này?", "Hay hãy đi."), 0)
        self.assertLess(
            bad_break_score("qua đời,", "tôi đã bị bệnh tâm thần."), 8)


class BeautifyFallback(unittest.TestCase):
    def _window(self):
        return [
            cue(2, "00:00:01,380", "00:00:04,252",
                "Vì không chịu nổi cú sốc khi bố mẹ cùng qua đời,"),
            cue(3, "00:00:04,500", "00:00:05,746",
                "tôi đã bị bệnh tâm thần."),
        ]

    def test_case11_json_loi(self):
        segs = self._window()
        before = [s.text for s in segs]
        n = beautify_windows(segs, lambda _p: "```not json", {"vi_beautify": "true"})
        self.assertEqual(n, 0)
        self.assertEqual([s.text for s in segs], before)

    def test_case12_api_timeout(self):
        segs = self._window()
        before = [s.text for s in segs]

        def boom(_p):
            raise TimeoutError("wait_reply")

        n = beautify_windows(segs, boom, {"vi_beautify": "true"})
        self.assertEqual(n, 0)
        self.assertEqual([s.text for s in segs], before)

    def test_case13_bo_cau(self):
        segs = self._window()
        before = [s.text for s in segs]

        def drop(_p):
            return json.dumps([
                {"index": 2, "start": seconds_to_timestamp(segs[0].start),
                 "end": seconds_to_timestamp(segs[0].end), "text": "Vì cú sốc."},
                {"index": 3, "start": seconds_to_timestamp(segs[1].start),
                 "end": seconds_to_timestamp(segs[1].end), "text": "Tôi bị bệnh."},
            ], ensure_ascii=False)

        n = beautify_windows(segs, drop, {"vi_beautify": "true"})
        self.assertEqual(n, 0)
        self.assertEqual([s.text for s in segs], before)

    def test_case14_duplicate(self):
        segs = self._window()
        before = [s.text for s in segs]

        def dup(_p):
            return json.dumps([
                {"index": 2, "start": seconds_to_timestamp(segs[0].start),
                 "end": seconds_to_timestamp(segs[0].end),
                 "text": segs[0].text},
                {"index": 3, "start": seconds_to_timestamp(segs[1].start),
                 "end": seconds_to_timestamp(segs[1].end),
                 "text": segs[0].text + " " + segs[1].text},
            ], ensure_ascii=False)

        n = beautify_windows(segs, dup, {"vi_beautify": "true"})
        self.assertEqual(n, 0)
        self.assertEqual([s.text for s in segs], before)

    def test_ai_chinh_ngat_hop_le(self):
        segs = [
            cue(2, "00:00:01,380", "00:00:04,252",
                "Vì không chịu nổi cú sốc khi bố mẹ cùng qua đời, tôi"),
            cue(3, "00:00:04,500", "00:00:05,746", "đã bị bệnh tâm thần."),
        ]

        def ok(_p):
            return json.dumps([
                {"index": 2, "start": "00:00:01,380", "end": "00:00:04,252",
                 "text": "Vì không chịu nổi cú sốc khi bố mẹ cùng qua đời,"},
                {"index": 3, "start": "00:00:04,500", "end": "00:00:05,746",
                 "text": "tôi đã bị bệnh tâm thần."},
            ], ensure_ascii=False)

        n = beautify_windows(segs, ok, {"vi_beautify": "true"})
        self.assertGreater(n, 0)
        self.assertTrue(segs[1].text.startswith("tôi"))

    def test_parse_markdown_fence(self):
        raw = "```json\n" + json.dumps([
            {"index": 1, "start": "00:00:00,000", "end": "00:00:01,000",
             "text": "Xin chào"},
        ], ensure_ascii=False) + "\n```"
        rows = _parse_rows(raw, 1)
        self.assertIsNotNone(rows)
        self.assertEqual(rows[0]["text"], "Xin chào")

    def test_validate_doi_timestamp(self):
        segs = self._window()
        rows = [
            {"index": 2, "start": "00:00:99,000", "end": "00:00:04,252",
             "text": segs[0].text},
            {"index": 3, "start": "00:00:04,500", "end": "00:00:05,746",
             "text": segs[1].text},
        ]
        self.assertEqual(validate_window(segs, rows), "đổi start")

    def test_auto_chi_gui_cua_so_xau(self):
        segs = [
            cue(1, 0.0, 1.0, "Tôi là Thất Nguyệt."),
            cue(2, 1.2, 2.0, "Vì bố mẹ qua đời, tôi"),
            cue(3, 2.1, 3.0, "đã bị bệnh tâm thần."),
        ]
        spans = detect_bad_windows(segs, window=10, threshold=8)
        self.assertTrue(any(a <= 1 and b >= 3 for a, b in spans))


class FinalizeTatBeautify(unittest.TestCase):
    def test_khong_can_ask_khi_tat(self):
        segs = user_example()
        n = finalize_spoken_vi_cues(segs, {"vi_beautify": "false"})
        self.assertGreater(n, 0)
        self.assertTrue(segs[2].text.lower().startswith("tôi"))


class TidyCue(unittest.TestCase):
    def test_giu_dau_phay_cuoi(self):
        self.assertEqual(tidy_cue("  qua đời,  "), "qua đời,")
        self.assertEqual(tidy_cue("này?"), "này?")


if __name__ == "__main__":
    unittest.main()
