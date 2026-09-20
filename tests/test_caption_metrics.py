"""Offline checks for text anchors, independent error scores and CLI exit status."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from autodub.srt_utils import Segment, save_srt_file
from scripts.caption_metrics import comparison, edit_distance, evaluate, metrics


class CaptionMetrics(unittest.TestCase):
    def test_exact_character_and_word_distance(self):
        self.assertEqual(edit_distance("kitten", "sitting"), 3)
        self.assertEqual(edit_distance("世界伪人", "世界伟人"), 1)
        self.assertEqual(edit_distance(["你好", "世界"], ["你好", "新的", "世界"]), 1)
        self.assertEqual(edit_distance("", "abc"), 3)
        self.assertEqual(edit_distance("abc", ""), 3)

    def test_han_anchors_do_not_align_by_index(self):
        gold = [Segment(1, 0, 2, "他们不再只是伪装"), Segment(2, 2, 3.5, "他们在成长")]
        actual = [
            Segment(1, 0, 0.1, "开场"),
            Segment(2, 0.1, 2, "他们不再只是伪装"),
            Segment(3, 2, 3.5, "他们在成长"),
        ]
        report = comparison(gold, actual)
        self.assertEqual(report["rows"][0]["actual_ids"], [2])
        self.assertEqual(report["rows"][1]["actual_ids"], [3])
        self.assertGreater(report["han_cer"], 0)

    def test_word_tear_does_not_change_content_error(self):
        gold = [Segment(1, 0, 2, "请大家一起活下去")]
        broken = [Segment(1, 0, 1, "请大家一起活"), Segment(2, 1, 2, "下去")]
        self.assertEqual(comparison(gold, broken)["han_cer"], 0)
        self.assertGreater(metrics(broken)["torn_word_pct"], 0)
        self.assertEqual(metrics(gold)["torn_word_pct"], 0)

    def test_pause_f1_counts_unmatched_pauses(self):
        gold = [Segment(1, 0, 1, "他们不再只是伪装"), Segment(2, 2, 3, "他们在成长")]
        same = comparison(gold, gold)
        no_pause = comparison(gold, [gold[0], Segment(2, 1, 3, "他们在成长")])
        self.assertEqual(same["pause_f1"], 1)
        self.assertEqual(no_pause["pause_f1"], 0)

    def test_report_preserves_inputs_and_uses_new_directories(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "source.srt"
            save_srt_file(str(p), [Segment(1, 0, 2, "请大家一起往前走")])
            before = hashlib.sha256(p.read_bytes()).hexdigest()
            first, report = evaluate(p, p, parent=td)
            second, _ = evaluate(p, p, parent=td)
            self.assertTrue(report["passed"])
            self.assertNotEqual(first, second)
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), before)
            self.assertTrue((first / "comparison.html").exists())
            with self.assertRaisesRegex(ValueError, "Unknown thresholds"):
                evaluate(p, p, parent=td, thresholds={"max_duraton": 5})

    def test_cli_threshold_exit_codes(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "source.srt"
            save_srt_file(str(p), [Segment(1, 0, 2, "请大家一起往前走")])
            cmd = [
                sys.executable,
                "scripts/compare_subtitles.py",
                str(p),
                str(p),
                "--output-parent",
                td,
            ]
            run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            limits = Path(td) / "limits.json"
            limits.write_text(json.dumps({"max_avg_duration": 1}), encoding="utf-8")
            run = subprocess.run(
                cmd + ["--thresholds", str(limits)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
