"""Recognition must never silently translate a display pack missing source text."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autodub import speechmap
from autodub.asr import pipeline
from autodub.asr.common import _set_last_marks, reset_caption_options, source_reuse_path
from autodub.asr.screen_pack import CaptionReviewRequired
from autodub.srt_utils import Segment, load_srt_file


class CaptionReviewGate(unittest.TestCase):
    def tearDown(self):
        speechmap.clear_active()
        reset_caption_options()
        _set_last_marks([])

    def dispatch(self, *args, **kwargs):
        _set_last_marks([(10, 10.2)])
        return [Segment(1, 0, 2.4, "活下去")], "zh"

    def test_transcribe_stops_with_source_evidence(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(pipeline, "_dispatch", side_effect=self.dispatch),
            patch.object(pipeline, "ffprobe_duration", return_value=2.4),
        ):
            with self.assertRaises(CaptionReviewRequired):
                pipeline.transcribe(
                    str(Path(td) / "audio.wav"), device="cpu", rescue_gaps=False
                )
            source = next(Path(td).glob("caption-review-*/source.srt"))
            self.assertEqual(load_srt_file(str(source))[0].text, "活下去")

    def test_rescue_receives_original_words_and_can_resolve_clocks(self):
        def rescue(audio, source, *args, marks_out=None, **kwargs):
            self.assertEqual([s.text for s in source], ["活下去"])
            marks_out[:] = [(0, 0.2), (0.2, 0.4), (0.4, 0.6)]
            return [Segment(1, 0, 0.6, "活下去")]

        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(pipeline, "_dispatch", side_effect=self.dispatch),
            patch.object(pipeline, "ffprobe_duration", return_value=2.4),
            patch.object(pipeline, "_rescue_gaps", side_effect=rescue),
        ):
            out, _ = pipeline.transcribe(
                str(Path(td) / "audio.wav"), device="cpu", rescue_gaps=True
            )
            self.assertEqual([s.text for s in out], ["活下去"])
            self.assertFalse(list(Path(td).glob("caption-review-*")))

    def test_disabling_reuse_actually_allows_new_asr(self):
        with tempfile.TemporaryDirectory() as td:
            raw, src = Path(td) / "old.asr.srt", Path(td) / "old.src.srt"
            raw.write_text("raw", encoding="utf-8")
            src.write_text("prepared", encoding="utf-8")
            for keep in (True, False):
                self.assertEqual(
                    source_reuse_path(
                        {"reuse_existing": False}, keep, str(raw), str(src)
                    ),
                    "",
                )
                self.assertEqual(
                    source_reuse_path(
                        {"reuse_existing": True}, keep, str(raw), str(src)
                    ),
                    str(raw if keep else src),
                )
            self.assertEqual(raw.read_text(encoding="utf-8"), "raw")


class RerecognizeCaptionGap(unittest.TestCase):
    def tearDown(self):
        speechmap.clear_active()
        reset_caption_options()
        _set_last_marks([])

    def test_rerecognize_merges_sliced_text_without_inventing(self):
        import json
        from autodub.server import pipeline as sp, state
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "film.mp4"
            video.write_bytes(b"fake")
            review = root / "output" / "film" / "_tmp" / "caption-review-x"
            review.mkdir(parents=True)
            (review / "unresolved.json").write_text(json.dumps([
                {"start": 4193.08, "end": 4268.44, "reason": "suspicious_chunk",
                 "withheld": True},
            ]), encoding="utf-8")
            (review / "source.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n活下去\n", encoding="utf-8")
            (root / "output" / "film" / "_tmp" / "audio16k.wav").write_bytes(b"x" * 100)
            prev_q = list(state.STATE.get("queue") or [])
            prev_p = dict(state.PROJECTS)
            prev_run = state.STATE.get("running")
            state.STATE["queue"] = [{
                "id": 9, "status": "cần kiểm tra", "result_status": "REVIEW_REQUIRED",
                "review_dir": str(review), "path": str(video), "name": "film",
            }]
            state.PROJECTS[9] = {
                "video": str(video), "w": 1280, "h": 720, "duration": 4300.0,
                "picture_duration": 4300.0, "clocks": {}, "regions": [], "logo": None,
                "sub_style": {}, "segments": [], "options": {"trim_enabled": False},
            }

            def rescue(audio_path, segs, duration, *args, **kwargs):
                self.assertEqual(kwargs.get("max_slice"), 12.0)
                self.assertTrue(kwargs.get("force_retry"))
                self.assertEqual(kwargs.get("focus"), (4193.08, 4268.44))
                out = list(segs)
                out.append(Segment(2, 4194.0, 4196.0, "住手"))
                return out

            try:
                with patch.object(sp, "HERE", td), \
                     patch.object(sp, "_load_cfg", return_value={"asr": {}}), \
                     patch("autodub.utils.ffprobe_duration", return_value=4300.0), \
                     patch("autodub.asr.pipeline._rescue_gaps", side_effect=rescue), \
                     patch("autodub.asr.funasr.cached_funasr_speech_ranges", return_value=None), \
                     patch("autodub.asr.screen_pack.last_review", return_value=[]), \
                     patch("autodub.asr.screen_pack.ensure_complete", return_value=None), \
                     patch.object(sp, "_save_project_state"), \
                     patch.object(sp, "_load_existing_segments_into_project"):
                    sp.rerecognize_caption_gap(9, 4193.08, 4268.44)
                job = state.STATE["queue"][0]
                self.assertEqual(job["status"], "chờ")
                src = (root / "output" / "film" / "film.src.srt").read_text(encoding="utf-8")
                self.assertIn("住手", src)
                self.assertIn("活下去", src)
                self.assertNotIn("蟋蟀", src)
            finally:
                state.STATE["queue"] = prev_q
                state.PROJECTS.clear()
                state.PROJECTS.update(prev_p)
                state.STATE["running"] = prev_run

    def test_rerecognize_keeps_gate_when_still_empty(self):
        import json
        from autodub.server import pipeline as sp, state
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "film.mp4"
            video.write_bytes(b"fake")
            review = root / "output" / "film" / "_tmp" / "caption-review-x"
            review.mkdir(parents=True)
            (review / "unresolved.json").write_text(json.dumps([
                {"start": 4193.08, "end": 4268.44, "reason": "suspicious_chunk",
                 "withheld": True},
            ]), encoding="utf-8")
            (review / "source.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n活下去\n", encoding="utf-8")
            (root / "output" / "film" / "_tmp" / "audio16k.wav").write_bytes(b"x" * 100)
            prev_q = list(state.STATE.get("queue") or [])
            prev_p = dict(state.PROJECTS)
            prev_run = state.STATE.get("running")
            state.STATE["queue"] = [{
                "id": 9, "status": "cần kiểm tra", "result_status": "REVIEW_REQUIRED",
                "review_dir": str(review), "path": str(video), "name": "film",
            }]
            state.PROJECTS[9] = {
                "video": str(video), "w": 1280, "h": 720, "duration": 4300.0,
                "picture_duration": 4300.0, "clocks": {}, "regions": [], "logo": None,
                "sub_style": {}, "segments": [], "options": {"trim_enabled": False},
            }
            try:
                with patch.object(sp, "HERE", td), \
                     patch.object(sp, "_load_cfg", return_value={"asr": {}}), \
                     patch("autodub.utils.ffprobe_duration", return_value=4300.0), \
                     patch("autodub.asr.pipeline._rescue_gaps",
                           side_effect=lambda *a, **k: list(a[1])), \
                     patch("autodub.asr.funasr.cached_funasr_speech_ranges", return_value=None), \
                     patch("autodub.asr.screen_pack.last_review", return_value=[]), \
                     patch.object(sp, "_save_project_state"), \
                     patch.object(sp, "_load_existing_segments_into_project"):
                    sp.rerecognize_caption_gap(9, 4193.08, 4268.44)
                job = state.STATE["queue"][0]
                self.assertEqual(job["status"], "cần kiểm tra")
                self.assertEqual(job["result_status"], "REVIEW_REQUIRED")
                self.assertFalse((root / "output" / "film" / "film.asr.srt").exists())
            finally:
                state.STATE["queue"] = prev_q
                state.PROJECTS.clear()
                state.PROJECTS.update(prev_p)
                state.STATE["running"] = prev_run

    def test_expand_window_pads_only_short_spans(self):
        from autodub.server.pipeline import _expand_rerecognize_window
        start, end, padded = _expand_rerecognize_window(1798.59, 1798.89, 3974.44)
        self.assertTrue(padded)
        self.assertAlmostEqual(start, 1797.09, places=2)
        self.assertAlmostEqual(end, 1799.89, places=2)
        start, end, padded = _expand_rerecognize_window(3297.38, 3301.295, 3974.44)
        self.assertFalse(padded)
        self.assertAlmostEqual(start, 3297.38, places=2)
        self.assertAlmostEqual(end, 3301.295, places=3)

    def test_fmt_span_range_keeps_subsecond(self):
        from autodub.server.helpers import _fmt_span_range
        shown = _fmt_span_range(1798.59, 1798.89)
        left, right = shown.split("–")
        self.assertNotEqual(left, right)
        self.assertIn("29m58.", shown)

    def _rerecognize_env(self, td, gap, audio_name="audio16k.wav"):
        import json
        from autodub.server import state
        root = Path(td)
        video = root / "film.mp4"
        video.write_bytes(b"fake")
        review = root / "output" / "film" / "_tmp" / "caption-review-x"
        review.mkdir(parents=True)
        (review / "unresolved.json").write_text(json.dumps([
            {"start": gap[0], "end": gap[1], "reason": "weak_fragment_alignment",
             "withheld": True},
        ]), encoding="utf-8")
        (review / "source.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n活下去\n", encoding="utf-8")
        (root / "output" / "film" / "_tmp" / audio_name).write_bytes(b"x" * 100)
        prev = (list(state.STATE.get("queue") or []), dict(state.PROJECTS),
                state.STATE.get("running"))
        state.STATE["queue"] = [{
            "id": 9, "status": "cần kiểm tra", "result_status": "REVIEW_REQUIRED",
            "review_dir": str(review), "path": str(video), "name": "film",
        }]
        state.PROJECTS[9] = {
            "video": str(video), "w": 1280, "h": 720, "duration": 4300.0,
            "picture_duration": 4300.0, "clocks": {}, "regions": [], "logo": None,
            "sub_style": {}, "segments": [], "options": {"trim_enabled": False},
        }
        return prev

    def test_rerecognize_uses_flac_when_wav_missing(self):
        from autodub.server import pipeline as sp, state
        with tempfile.TemporaryDirectory() as td:
            prev_q, prev_p, prev_run = self._rerecognize_env(
                td, (3297.38, 3301.295), "audio16k.flac")
            seen = {}

            def rescue(audio_path, segs, duration, *args, **kwargs):
                seen["audio"] = audio_path
                return list(segs)

            def boom(*_a, **_k):
                raise AssertionError("ensure_audio should not run when FLAC exists")

            try:
                with patch.object(sp, "HERE", td), \
                     patch.object(sp, "_load_cfg", return_value={"asr": {}}), \
                     patch("autodub.utils.ffprobe_duration", return_value=3974.44), \
                     patch("autodub.asr.pipeline._rescue_gaps", side_effect=rescue), \
                     patch("autodub.asr.funasr.cached_funasr_speech_ranges", return_value=None), \
                     patch("autodub.asr.screen_pack.last_review", return_value=[]), \
                     patch("autodub.asr.screen_pack.ensure_complete", return_value=None), \
                     patch("autodub.video.ensure_audio", side_effect=boom), \
                     patch.object(sp, "_save_project_state"), \
                     patch.object(sp, "_load_existing_segments_into_project"):
                    sp.rerecognize_caption_gap(9, 3297.38, 3301.295)
                self.assertTrue(str(seen.get("audio", "")).endswith("audio16k.flac"))
                self.assertEqual(state.STATE["queue"][0]["status"], "chờ")
            finally:
                state.STATE["queue"] = prev_q
                state.PROJECTS.clear()
                state.PROJECTS.update(prev_p)
                state.STATE["running"] = prev_run

    def test_rerecognize_pads_subsecond_gap(self):
        from autodub.server import pipeline as sp, state
        with tempfile.TemporaryDirectory() as td:
            prev_q, prev_p, prev_run = self._rerecognize_env(
                td, (1798.59, 1798.89), "audio16k.wav")
            seen = {}

            def rescue(audio_path, segs, duration, *args, **kwargs):
                seen["focus"] = kwargs.get("focus")
                self.assertEqual(kwargs.get("max_slice"), 12.0)
                return list(segs)

            try:
                with patch.object(sp, "HERE", td), \
                     patch.object(sp, "_load_cfg", return_value={"asr": {}}), \
                     patch("autodub.utils.ffprobe_duration", return_value=3974.44), \
                     patch("autodub.asr.pipeline._rescue_gaps", side_effect=rescue), \
                     patch("autodub.asr.funasr.cached_funasr_speech_ranges", return_value=None), \
                     patch("autodub.asr.screen_pack.last_review", return_value=[]), \
                     patch("autodub.asr.screen_pack.ensure_complete", return_value=None), \
                     patch.object(sp, "_save_project_state"), \
                     patch.object(sp, "_load_existing_segments_into_project"):
                    sp.rerecognize_caption_gap(9, 1798.59, 1798.89)
                start, end = seen["focus"]
                self.assertAlmostEqual(start, 1797.09, places=2)
                self.assertAlmostEqual(end, 1799.89, places=2)
            finally:
                state.STATE["queue"] = prev_q
                state.PROJECTS.clear()
                state.PROJECTS.update(prev_p)
                state.STATE["running"] = prev_run

    def test_rerecognize_keeps_other_unresolved_gaps(self):
        import json
        from autodub.server import pipeline as sp, state
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "film.mp4"
            video.write_bytes(b"fake")
            review = root / "output" / "film" / "_tmp" / "caption-review-x"
            review.mkdir(parents=True)
            (review / "unresolved.json").write_text(json.dumps([
                {"start": 10.0, "end": 20.0, "reason": "unresolved_speech_gap",
                 "withheld": True},
                {"start": 80.0, "end": 90.0, "reason": "unresolved_speech_gap",
                 "withheld": True},
            ]), encoding="utf-8")
            (review / "source.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n活下去\n", encoding="utf-8")
            (root / "output" / "film" / "_tmp" / "audio16k.wav").write_bytes(b"x" * 100)
            prev_q = list(state.STATE.get("queue") or [])
            prev_p = dict(state.PROJECTS)
            prev_run = state.STATE.get("running")
            state.STATE["queue"] = [{
                "id": 9, "status": "cần kiểm tra", "result_status": "REVIEW_REQUIRED",
                "review_dir": str(review), "path": str(video), "name": "film",
            }]
            state.PROJECTS[9] = {
                "video": str(video), "w": 1280, "h": 720, "duration": 100.0,
                "picture_duration": 100.0, "clocks": {}, "regions": [], "logo": None,
                "sub_style": {}, "segments": [], "options": {"trim_enabled": False},
            }

            def rescue(audio_path, segs, duration, *args, **kwargs):
                out = list(segs)
                out.append(Segment(2, 12.0, 14.0, "住手"))
                return out

            try:
                with patch.object(sp, "HERE", td), \
                     patch.object(sp, "_load_cfg", return_value={"asr": {}}), \
                     patch("autodub.utils.ffprobe_duration", return_value=100.0), \
                     patch("autodub.asr.pipeline._rescue_gaps", side_effect=rescue), \
                     patch("autodub.asr.funasr.cached_funasr_speech_ranges", return_value=None), \
                     patch("autodub.asr.screen_pack.last_review", return_value=[]), \
                     patch.object(sp, "_save_project_state"), \
                     patch.object(sp, "_load_existing_segments_into_project"):
                    sp.rerecognize_caption_gap(9, 10.0, 20.0)
                job = state.STATE["queue"][0]
                self.assertEqual(job["status"], "cần kiểm tra")
                self.assertEqual(job["result_status"], "REVIEW_REQUIRED")
            finally:
                state.STATE["queue"] = prev_q
                state.PROJECTS.clear()
                state.PROJECTS.update(prev_p)
                state.STATE["running"] = prev_run


class ReusedAsrVadGate(unittest.TestCase):
    def test_hole_over_threshold_is_withheld(self):
        from autodub.asr.pipeline import withheld_vad_gaps
        segs = [Segment(1, 0.0, 1.0, "活下去")]
        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / "audio16k.wav"
            audio.write_bytes(b"x" * 100)
            with patch("autodub.asr.funasr.cached_funasr_speech_ranges",
                       return_value=[(0.0, 1.0), (3.0, 5.0)]), \
                 patch("autodub.asr.pipeline.confirm_speech_holes",
                       side_effect=lambda path, holes, db: (holes, [])):
                _, review = withheld_vad_gaps(
                    str(audio), segs, 10.0, 1.2, -42.0, stitch=False)
        self.assertTrue(any(
            row.get("withheld") and abs(row["start"] - 3.0) < 0.05
            and abs(row["end"] - 5.0) < 0.05
            for row in review))

    def test_reuse_path_drops_then_gates(self):
        import inspect
        from autodub.server import pipeline as sp
        src = inspect.getsource(sp._run_pipeline)
        drop = src.find("drop_hallucinations")
        vad = src.find("withheld_vad_gaps")
        gate = src.find("ensure_complete")
        self.assertTrue(0 <= drop < vad < gate)
        self.assertIn("lambda target: _dich_nhom_segments(target, cache_path)", src)


if __name__ == "__main__":
    unittest.main()
