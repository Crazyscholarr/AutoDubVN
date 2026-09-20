"""Human non-speech ack does not invent ASR text or relax the 1.2s gate."""
import tempfile
import unittest
from pathlib import Path

from autodub.asr.nonspeech import covered_by, filter_withheld_rows, save_non_speech
from autodub.asr.screen_pack import CaptionReviewRequired, ensure_complete
from autodub.srt_utils import Segment


class NonSpeechAck(unittest.TestCase):
    def test_legacy_saved_ack_clears_missing_word_clock(self):
        from autodub.asr.nonspeech import load_non_speech, remaining_gaps
        with tempfile.TemporaryDirectory() as td:
            old_review=Path(td)/'caption-review-old'
            new_review=Path(td)/'caption-review-new'
            old_review.mkdir(); new_review.mkdir()
            save_non_speech(old_review,[dict(start=14054.35,end=14054.9,
                                            reason='unresolved_speech_gap')])
            rows=[dict(reason='missing_speech_marks',text='滑。',
                       start=14054.35,end=14054.9,withheld=True)]
            self.assertEqual(remaining_gaps(rows,load_non_speech(new_review)),[])
            ensure_complete([Segment(1,0,1,'活下去')],
                            [Segment(1,0,1,'活下去')],td,review=rows)

    def test_non_speech_does_not_ack_structural_clock_errors(self):
        accepted=[dict(start=10,end=12,reason='missing_speech_marks')]
        for reason in ['invalid_source_clock','invalid_clock','overlapping_clock']:
            with self.subTest(reason=reason):
                row=dict(reason=reason,start=10,end=12,withheld=True)
                self.assertTrue(filter_withheld_rows([row],accepted)[0]['withheld'])

    def test_partial_cover_does_not_clear_long_chunk(self):
        accepted = [{"start": 23.7, "end": 25.8, "reason": "unresolved_speech_gap"}]
        self.assertFalse(covered_by(4193.08, 4268.44, accepted))
        rows = [dict(reason="suspicious_chunk", start=4193.08, end=4268.44, withheld=True)]
        kept = filter_withheld_rows(rows, accepted)
        self.assertTrue(kept[0]["withheld"])

    def test_ack_matches_listed_cricket_span(self):
        accepted = [{"start": 4193.08, "end": 4268.44, "reason": "suspicious_chunk"}]
        self.assertTrue(covered_by(4193.08, 4268.44, accepted))

    def test_gate_still_blocks_unacked_gap(self):
        with tempfile.TemporaryDirectory() as td:
            save_non_speech(td, [{"start": 23.7, "end": 25.8}])
            rows = [
                dict(reason="unresolved_speech_gap", start=23.7, end=25.8, withheld=True, threshold_s=1.2),
                dict(reason="unresolved_speech_gap", start=599.66, end=601.6, withheld=True, threshold_s=1.2),
            ]
            with self.assertRaises(CaptionReviewRequired) as ctx:
                ensure_complete([Segment(1, 0, 1, "活下去")],
                                [Segment(1, 0, 1, "活下去")], td, review=rows)
            self.assertEqual(ctx.exception.gaps[0]["start"], 599.66)
            self.assertTrue(list(Path(td).glob("caption-review-*")))


    def test_ack_clears_weak_fragment_without_inventing_text(self):
        from autodub.asr.nonspeech import remaining_gaps
        accepted = [
            {"start": 1798.59, "end": 1798.89, "reason": "unresolved_speech_gap"},
            {"start": 3297.38, "end": 3301.295, "reason": "unresolved_speech_gap"},
        ]
        rows = [
            dict(reason="weak_fragment_alignment", text="请",
                 start=1798.59, end=1798.89, withheld=True),
            dict(reason="weak_fragment_alignment", text="啊，",
                 start=3297.38, end=3301.295, withheld=True),
        ]
        kept = filter_withheld_rows(rows, accepted)
        self.assertFalse(any(row.get("withheld") for row in kept))
        self.assertEqual(remaining_gaps(rows, accepted), [])

    def test_acked_weak_fragments_do_not_block_gate(self):
        with tempfile.TemporaryDirectory() as td:
            save_non_speech(td, [
                {"start": 1798.59, "end": 1798.89},
                {"start": 3297.38, "end": 3301.295},
            ])
            rows = [
                dict(reason="weak_fragment_alignment", text="请",
                     start=1798.59, end=1798.89, withheld=True),
                dict(reason="weak_fragment_alignment", text="啊，",
                     start=3297.38, end=3301.295, withheld=True),
            ]
            ensure_complete(
                [Segment(1, 0, 1, "活下去")],
                [Segment(1, 0, 1, "活下去")],
                td, review=rows,
            )
            self.assertFalse(list(Path(td).glob("caption-review-*")))
