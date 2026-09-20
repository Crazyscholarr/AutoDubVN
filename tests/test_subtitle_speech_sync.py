"""On-screen Vietnamese captions must follow spoken TTS, not Chinese packs."""
import unittest

from autodub.overlays import _ass_time, build_ass
from autodub.semantic import speech_segments, stamp_display_cues_to_speech
from autodub.srt_utils import Segment, format_srt


def _dialogue_spans(ass: str):
    rows = []
    for line in ass.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        rows.append((parts[1], parts[2]))
    return rows


class StampDisplayCuesTests(unittest.TestCase):
    def test_group_captions_fill_spoken_interval_without_chinese_gaps(self):
        segs = [
            Segment(1, 10.0, 11.2, "Xin chào các bạn"),
            Segment(2, 11.6, 13.0, "tôi đã về"),
            Segment(3, 13.0, 15.8, "rồi."),
        ]
        for seg in segs:
            seg.semantic_group = "g1"
        units = speech_segments(segs)
        units[0].placed_start = 10.0
        units[0].voice_duration = 4.0
        n = stamp_display_cues_to_speech(segs, units)
        self.assertEqual(n, 3)
        self.assertAlmostEqual(segs[0].start, 10.0, places=3)
        self.assertAlmostEqual(segs[-1].end, 14.0, places=3)
        self.assertAlmostEqual(segs[0].end, segs[1].start, places=3)
        self.assertAlmostEqual(segs[1].end, segs[2].start, places=3)
        self.assertGreater(segs[0].end, 11.2)
        self.assertNotEqual(round(segs[1].start, 3), 11.6)
        self.assertEqual(segs[0].placed_start, segs[0].start)
        self.assertAlmostEqual(segs[0].voice_duration, segs[0].end - segs[0].start, places=3)
        srt = format_srt(segs, use_placed=True)
        self.assertIn("00:00:10,000 -->", srt)
        self.assertIn("00:00:14,000", srt)

    def test_missing_voice_duration_keeps_chinese_clocks(self):
        segs = [Segment(1, 1.0, 2.0, "Một"), Segment(2, 2.2, 3.0, "Hai")]
        for seg in segs:
            seg.semantic_group = "g1"
        units = speech_segments(segs)
        units[0].placed_start = 1.0
        stamp_display_cues_to_speech(segs, units)
        self.assertEqual((segs[0].start, segs[0].end), (1.0, 2.0))
        self.assertEqual((segs[1].start, segs[1].end), (2.2, 3.0))

    def test_caption_share_follows_vietnamese_length(self):
        segs = [
            Segment(1, 0.0, 2.0, "aaaaaaaaaa"),
            Segment(2, 2.5, 3.0, "aa"),
        ]
        for seg in segs:
            seg.semantic_group = "g1"
        units = speech_segments(segs)
        units[0].placed_start = 0.0
        units[0].voice_duration = 12.0
        stamp_display_cues_to_speech(segs, units)
        self.assertAlmostEqual(segs[0].end, 10.0, places=2)
        self.assertAlmostEqual(segs[1].start, 10.0, places=2)
        self.assertAlmostEqual(segs[1].end, 12.0, places=3)


class AssFollowsSpeechTests(unittest.TestCase):
    def test_voice_duration_beats_read_cps_and_min_duration(self):
        seg = Segment(1, 10.0, 16.0, "Hi")
        seg.placed_start = 10.0
        seg.voice_duration = 2.4
        ass = build_ass([seg], 1280, 720, use_placed=True,
                        style={"read_cps": 14, "min_duration": 0.9, "tail_pad": 0.35,
                               "animation": "none"})
        self.assertEqual(_dialogue_spans(ass), [(_ass_time(10.0), _ass_time(12.4))])

    def test_stamped_group_ass_covers_speech_not_chinese_end(self):
        segs = [
            Segment(1, 10.0, 11.2, "Xin chào"),
            Segment(2, 11.6, 15.8, "tôi về."),
        ]
        for seg in segs:
            seg.semantic_group = "g1"
        units = speech_segments(segs)
        units[0].placed_start = 10.0
        units[0].voice_duration = 3.0
        stamp_display_cues_to_speech(segs, units)
        ass = build_ass(segs, 1280, 720, use_placed=True, style={"animation": "none"})
        spans = _dialogue_spans(ass)
        self.assertGreaterEqual(len(spans), 2)
        self.assertEqual(spans[0][0], _ass_time(10.0))
        self.assertEqual(spans[-1][1], _ass_time(13.0))


if __name__ == "__main__":
    unittest.main()
