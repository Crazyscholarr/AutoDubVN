"""Golden evaluator is independent of chunking/repair policy."""
import unittest

from autodub.asr.evaluate import (
    compare_transcripts,
    evaluate_captions,
    gate_slice,
    interval_diff,
    covered_ranges,
)
from autodub.srt_utils import Segment


class GoldenEvaluator(unittest.TestCase):
    def test_mega_cue_inflates_naive_coverage_only(self):
        speech = [(0, 100), (200, 300)]
        mega = [Segment(1, 0, 180, "x" * 100), Segment(2, 220, 230, "ok")]
        honest = evaluate_captions(
            [Segment(1, 220, 230, "ok")], speech, 300)
        inflated = evaluate_captions(mega, speech, 300)
        self.assertGreater(inflated["inflation_pp"], 30)
        self.assertEqual(inflated["mega_cue_count"], 1)
        self.assertAlmostEqual(
            honest["honest_speech_coverage_percent"],
            inflated["honest_speech_coverage_percent"],
        )

    def test_subthreshold_gaps_explain_unresolved_without_blockers(self):
        segs = [Segment(i, i * 2, i * 2 + 1, "字") for i in range(10)]
        speech = [(0, 20)]
        result = evaluate_captions(segs, speech, 20, min_gap=1.2)
        self.assertGreater(result["honest_unresolved_speech_s"], 8)
        self.assertEqual(result["significant_gap_count"], 0)
        self.assertGreater(result["subthreshold_gap_s"], 8)
        self.assertAlmostEqual(
            result["contradiction"]["explained_s"],
            result["honest_unresolved_speech_s"],
            places=4,
        )

    def test_interval_diff_old_covered_new_missing(self):
        old = [Segment(1, 0, 10, "old")]
        new = [Segment(1, 0, 3, "new")]
        lost = interval_diff(covered_ranges(old, 10), covered_ranges(new, 10),
                             [(0, 10)], 10)
        self.assertAlmostEqual(sum(row["duration"] for row in lost), 7)

    def test_gate_rejects_mega_and_invalid(self):
        mega = evaluate_captions([Segment(1, 0, 40, "x")], [(0, 40)], 40)
        self.assertEqual(gate_slice(mega)[0], "FAIL")
        bad = evaluate_captions([Segment(1, 2, 1, "x")], [(0, 2)], 2)
        self.assertEqual(gate_slice(bad)[0], "FAIL")

    def test_compare_reports_honest_delta_not_naive(self):
        speech = [(0, 100)]
        old = [Segment(1, 0, 90, "mega-ish")]
        new = [s for s in (Segment(i, i, i + 0.8, "字") for i in range(80))]
        compared = compare_transcripts(old, new, speech, 100)
        self.assertGreater(compared["old"]["inflation_pp"], 0)
        self.assertEqual(compared["new"]["mega_cue_count"], 0)


if __name__ == "__main__":
    unittest.main()
