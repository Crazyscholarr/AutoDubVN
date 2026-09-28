"""Adversarial review/gate regressions, entirely on temporary files."""
import json
import tempfile
import unittest
from pathlib import Path

from autodub.asr.nonspeech import save_non_speech, effective_review, bind_source, load_latest_review
from autodub.asr.screen_pack import ensure_complete, CaptionReviewRequired
from autodub.srt_utils import Segment


class ReviewIntegrity(unittest.TestCase):
    def test_revoke_one_of_two_adjacent_confirmations(self):
        with tempfile.TemporaryDirectory() as td:
            save_non_speech(td,[dict(start=10,end=11),dict(start=11,end=12)])
            save_non_speech(td,[dict(start=10,end=11,resolution='UNREVIEWED')])
            rows=[dict(start=a,end=a+1,reason='missing_speech_marks',withheld=True) for a in (10,11)]
            state=effective_review(td,rows)
            self.assertEqual([r['start'] for r in state['effective_blockers']],[10])

    def test_partial_speech_confirmation_cannot_be_masked_by_broad_noise(self):
        with tempfile.TemporaryDirectory() as td:
            save_non_speech(td,[dict(start=10,end=20)])
            save_non_speech(td,[dict(start=14,end=15,resolution='CONFIRMED_SPEECH')])
            rows=[dict(start=10,end=20,reason='suspicious_chunk',withheld=True)]
            self.assertEqual(effective_review(td,rows)['unresolved'],1)

    def test_invalid_packed_clock_is_blocked_even_when_source_is_valid(self):
        for start,end in [(float('nan'),1),(0,float('inf')),(-1,1),(2,1)]:
            with self.subTest(start=start,end=end),tempfile.TemporaryDirectory() as td:
                with self.assertRaises(CaptionReviewRequired):
                    ensure_complete([Segment(1,start,end,'活下去')],
                                    [Segment(1,0,1,'活下去')],td,review=[])

    def test_corrupt_snapshot_cannot_silently_restore_older_clear_result(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);media=root/'film.mp4';media.write_bytes(b'fake')
            bind_source(root,media)
            old=root/'caption-review-old';old.mkdir()
            (old/'source.srt').write_text('source')
            (old/'review.json').write_text('[]')
            (root/'review_latest.json').write_text('{broken')
            state=load_latest_review(root)
            self.assertTrue(any(r.get('withheld') for r in state.get('rows',[])))
