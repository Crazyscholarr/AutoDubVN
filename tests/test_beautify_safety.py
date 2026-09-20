"""Offline regressions for lossless cue editing at the AI boundary."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

from autodub.srt_utils import Segment, seconds_to_timestamp
from autodub.vi_beautify import beautify_windows
from autodub.vi_reflow import content_equivalent, reflow_options, reflow_spoken_vi, validate_window
from autodub.vi_cues import finalize_spoken_vi_cues
from autodub import translate


def rows_for(segs):
    return [dict(index=s.index, start=seconds_to_timestamp(s.start),
                 end=seconds_to_timestamp(s.end), text=s.text) for s in segs]


class BeautifySafetyTests(unittest.TestCase):
    def test_yaml_false_really_disables_ai(self):
        segs = [Segment(1, 0, 1, "Vì tôi"), Segment(2, 1.1, 2, "đã đi.")]
        ask = Mock(return_value="[]")
        self.assertEqual(beautify_windows(segs, ask, {"vi_beautify": False}), 0)
        ask.assert_not_called()

    def test_overlap_is_bounded_for_forward_progress(self):
        for overlap in (4, 100, -5):
            opt = reflow_options({"vi_beautify_window": 4, "vi_beautify_overlap": overlap})
            self.assertLess(opt["overlap"], opt["window"])
            self.assertGreaterEqual(opt["overlap"], 0)

    def test_content_changes_are_never_fuzzy_accepted(self):
        for before, after in (
            (["tôi không muốn cùng anh đi đến nơi xa ấy."], ["tôi muốn cùng anh đi đến nơi xa ấy."]),
            (["tôi đã đi đến nơi ấy cùng anh."], ["tôi đã đã đi đến nơi ấy cùng anh."]),
            (["他是我的哥哥"], ["他是我的弟弟"]),
            (["xin chào " * 1000 + "không đi"], ["xin chào " * 1000 + "đi"]),
            (["tôi không đi"], ["tôi kh ông đi"]),
        ):
            with self.subTest(before=before[0][:30]):
                self.assertFalse(content_equivalent(before, after))

    def test_case_and_cue_break_changes_are_allowed(self):
        self.assertTrue(content_equivalent(["Hôm nay, tôi", "đã về."],
                                           ["Hôm nay,", "Tôi đã về."]))

    def test_schema_and_markdown_rejected(self):
        segs = [Segment(1, 0, 1, "tôi đã đi."), Segment(2, 1.1, 2, "anh đã về.")]
        for field, value in (("start", None), ("end", ""), ("text", None),
                             ("text", ["tôi đã đi."]), ("text", "**tôi đã đi.**"),
                             ("text", "`tôi đã đi.`"), ("index", 1.5)):
            rows = rows_for(segs)
            rows[0][field] = value
            with self.subTest(field=field, value=value):
                self.assertIsNotNone(validate_window(segs, rows))

    def test_ai_does_not_receive_windows_across_hard_boundaries(self):
        for mode in ("true", "auto"):
            for boundary in ("gap", "speaker"):
                segs = [Segment(1, 0, 1, "Hôm nay tôi", speaker="A"),
                        Segment(2, 1.1, 2, "đã đến vì anh", speaker="A"),
                        Segment(3, 5 if boundary == "gap" else 2.1, 6,
                                "đang chờ ở đó.", speaker="B" if boundary == "speaker" else "A")]
                received = []
                def ask(prompt):
                    rows = json.loads(prompt.split("INPUT:\n", 1)[1].split("\n\nTrả về", 1)[0])
                    received.append([int(r["index"]) for r in rows])
                    return json.dumps(rows, ensure_ascii=False)
                beautify_windows(segs, ask, {"vi_beautify": mode})
                with self.subTest(mode=mode, boundary=boundary):
                    self.assertTrue(received)
                    self.assertFalse(any(2 in ids and 3 in ids for ids in received))

    def test_validator_defends_boundary_even_for_external_rows(self):
        segs = [Segment(1, 0, 1, "Vì tôi"), Segment(2, 5, 6, "đã đi.")]
        rows = rows_for(segs)
        rows[0]["text"], rows[1]["text"] = "Vì", "tôi đã đi."
        self.assertIsNotNone(validate_window(segs, rows))

    def test_rule_reflow_preserves_intentional_repetition(self):
        segs = [Segment(1, 0, 1, "Anh đi cùng tôi"),
                Segment(2, 1.1, 2, "tôi sẽ dẫn đường.")]
        before = copy.deepcopy(segs)
        reflow_spoken_vi(segs)
        self.assertTrue(content_equivalent([s.text for s in before], [s.text for s in segs]))

    def test_overlap_merge_is_deterministic_and_lossless(self):
        original = [Segment(i+1, i, i+.9, f"vì tôi đã chờ anh ở đây {i}") for i in range(30)]
        outcomes = []
        for _ in range(2):
            segs = copy.deepcopy(original)
            calls = []
            def ask(prompt):
                rows = json.loads(prompt.split("INPUT:\n", 1)[1].split("\n\nTrả về", 1)[0])
                calls.append(rows)
                # Move one word at the left boundary. Overlap conflicts must roll back.
                first = rows[0]["text"].split()
                rows[0]["text"] = " ".join(first[:-1])
                rows[1]["text"] = first[-1] + " " + rows[1]["text"]
                return json.dumps(rows)
            beautify_windows(segs, ask, {"vi_beautify": True, "vi_beautify_window": 6})
            self.assertLessEqual(len(calls), 8)
            self.assertTrue(content_equivalent([s.text for s in original], [s.text for s in segs]))
            outcomes.append([s.text for s in segs])
        self.assertEqual(*outcomes)

    def test_provider_errors_fall_back_without_text_mutation(self):
        for error in (TimeoutError("timeout"), ConnectionError("offline"), RuntimeError("HTTP 429")):
            segs = [Segment(1, 0, 1, "Vì tôi"), Segment(2, 1.1, 2, "đã đi.")]
            before = copy.deepcopy(segs)
            self.assertEqual(beautify_windows(segs, Mock(side_effect=error), {"vi_beautify": True}), 0)
            self.assertEqual(segs, before)

    def test_glossary_does_not_change_running_laps(self):
        segs = [Segment(1, 0, 3, "Hôm nay tôi chạy mười vòng quanh sân.")]
        finalize_spoken_vi_cues(segs, {"vi_beautify": False})
        self.assertEqual(segs[0].text, "Hôm nay tôi chạy mười vòng quanh sân.")

    def test_thousands_of_cues_have_bounded_ai_calls(self):
        segs = [Segment(i+1, i, i+.9, f"Tôi vẫn đang chờ ở đây {i}.") for i in range(2500)]
        calls = []
        def ask(prompt):
            rows = json.loads(prompt.split("INPUT:\n", 1)[1].split("\n\nTrả về", 1)[0])
            calls.append(len(rows))
            return json.dumps(rows)
        self.assertEqual(beautify_windows(segs, ask, {"vi_beautify": True}), 0)
        self.assertLessEqual(len(calls), 313)
        self.assertLessEqual(max(calls), 10)

    def test_job_cancellation_is_not_an_ai_fallback(self):
        segs = [Segment(1, 0, 1, "Vì tôi"), Segment(2, 1.1, 2, "đã đi.")]
        with self.assertRaises(InterruptedError):
            beautify_windows(segs, Mock(side_effect=InterruptedError("cancelled")),
                             {"vi_beautify": True})

    def test_translation_propagates_beautify_cancellation(self):
        segs = [Segment(1, 0, 1, "你好"), Segment(2, 1.1, 2, "再见")]
        with patch.object(translate, "_api_call", side_effect=[
                '["Xin chào.", "Tạm biệt."]', InterruptedError("cancelled")]):
            with self.assertRaises(InterruptedError):
                translate.translate_segments(segs, "QA_DUMMY_KEY", chars_per_sec=0,
                    translation_cfg={"vi_reflow": False, "vi_beautify": True})


if __name__ == "__main__":
    unittest.main()
