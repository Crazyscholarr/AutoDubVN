"""Lần cuối làm sạch SRT Việt: glossary + chia lại theo ngữ pháp."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub.srt_utils import Segment
from autodub.vi_cues import apply_vi_dub_glossary, finalize_spoken_vi_cues
from autodub.vi_reflow import content_equivalent


def cues(*texts):
    out = []
    t = 0.0
    for i, text in enumerate(texts, 1):
        out.append(Segment(i, t, t + 1.2, text))
        t += 1.3
    return out


class GlossarBanDich(unittest.TestCase):
    def test_vong_ban_dich_thanh_diem(self):
        self.assertEqual(
            apply_vi_dub_glossary("Nhắm mắt bắn cũng được 9,8 vòng"),
            "Nhắm mắt bắn cũng được 9,8 điểm",
        )
        self.assertEqual(
            apply_vi_dub_glossary("Mười vòng, mười vòng"),
            "Mười điểm, mười điểm",
        )
        self.assertEqual(
            apply_vi_dub_glossary("vẫn thiếu 0,1 là trúng chín vòng, tiếp"),
            "vẫn thiếu 0,1 là trúng chín điểm, tiếp",
        )

    def test_khong_doi_vong_lap(self):
        self.assertEqual(apply_vi_dub_glossary("vòng lặp thời gian"),
                         "vòng lặp thời gian")
        self.assertEqual(apply_vi_dub_glossary("vòng hoa cưới"),
                         "vòng hoa cưới")


class GiaoNhipNguPhap(unittest.TestCase):
    def test_biet_chua_ha_chuyen_chu_khong_lap(self):
        before = [
            "Phải noi gương Lý Bác Khởi, biết",
            "chưa hả?",
        ]
        segs = cues(*before)
        n = finalize_spoken_vi_cues(segs)
        self.assertGreater(n, 0)
        self.assertFalse(segs[0].text.endswith(" biết"))
        self.assertTrue(segs[1].text.lower().startswith("biết"))
        self.assertNotIn("biết biết", (segs[0].text + " " + segs[1].text).lower())
        self.assertTrue(content_equivalent(before, [s.text for s in segs]))

    def test_glossary_va_khong_lap_chu(self):
        before = [
            "Cái",
            "gì, toàn",
            "bộ mười vòng sao, ừm, có thể liên",
            "tiếp mười lần bắn trúng mười vòng, kỹ năng",
            "bắn đã bước đầu thành thạo rồi, rất giỏi, hôm nay em",
            "cho tôi bất ngờ lớn, xem ra hôm",
            "nay thưởng hạng nhất thuộc về em rồi, ở trung học",
            "A, mỗi đợt thi",
        ]
        segs = cues(*before)
        starts = [(s.start, s.end) for s in segs]
        finalize_spoken_vi_cues(segs)
        joined = " ".join(s.text for s in segs)
        self.assertIn("điểm", joined)
        self.assertNotIn("vòng", joined)
        self.assertTrue(content_equivalent(
            [apply_vi_dub_glossary(t) for t in before],
            [s.text for s in segs],
        ))
        self.assertEqual([(s.start, s.end) for s in segs], starts)
        self.assertTrue(all(s.text.strip() for s in segs))

    def test_tiep_theo_khong_cat_giua_cum(self):
        segs = cues("vẫn thiếu 0,1 là trúng chín vòng, tiếp",
                    "theo là Lý Bác Khởi, đạt chín vòng, ừm.")
        finalize_spoken_vi_cues(segs)
        joined = " ".join(s.text for s in segs)
        self.assertIn("tiếp theo", joined)
        self.assertIn("điểm", joined)
        self.assertFalse(segs[0].text.endswith(" tiếp"))

    def test_giu_moc_thoi_gian(self):
        segs = cues("Cái", "gì")
        start, end = segs[0].start, segs[0].end
        finalize_spoken_vi_cues(segs)
        self.assertEqual(segs[0].start, start)
        self.assertEqual(segs[0].end, end)

    def test_idempotent(self):
        segs = cues(
            "Phải noi gương Lý Bác Khởi, biết",
            "chưa hả?",
        )
        finalize_spoken_vi_cues(segs)
        once = [s.text for s in segs]
        finalize_spoken_vi_cues(segs)
        self.assertEqual([s.text for s in segs], once)


if __name__ == "__main__":
    unittest.main()
