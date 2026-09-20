"""Regression for review 18/09/2026 packages A, B1+D, C."""
from __future__ import annotations

import inspect
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from autodub.asr.screen_pack import compact_review_gaps
from autodub.semantic import batches
from autodub.srt_utils import Segment
from autodub.translate.semantic import api_log_tag, cache_affecting_cfg
from autodub.utils import start_process_log, stop_file_log, which
from autodub.video.assemble import _copy_concat_safe
from autodub.video.sync_check import (
    SyncCheckFailed,
    assert_sync_allows_render,
    check_dub_sync,
)


def _have_ffmpeg() -> bool:
    return bool(which("ffmpeg") and which("ffprobe"))


class FlacConcat(unittest.TestCase):
    def test_flac_khong_copy_concat(self):
        self.assertFalse(_copy_concat_safe(["a.flac", "b.flac"]))
        self.assertTrue(_copy_concat_safe(["a.wav", "b.wav"]))

    @unittest.skipUnless(_have_ffmpeg(), "FFmpeg is required")
    def test_noi_3x2s_flac_giu_tieng_duoi(self):
        from autodub.utils import ffprobe_duration, run
        from autodub.video.assemble import _join_audio_chunks
        from autodub.video.sync_check import mean_volume_db

        with tempfile.TemporaryDirectory() as td:
            pieces = []
            for i, freq in enumerate((220, 440, 880)):
                path = os.path.join(td, f"p{i}.flac")
                run([
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i", f"sine=frequency={freq}:sample_rate=48000:duration=2",
                    "-c:a", "flac", path,
                ], timeout=30)
                pieces.append(path)
            joined = os.path.join(td, "joined.flac")
            _join_audio_chunks(pieces, joined, 48000, td)
            got = ffprobe_duration(joined)
            self.assertAlmostEqual(got, 6.0, delta=0.35)
            head = mean_volume_db(joined, 0.8, 0.4)
            tail = mean_volume_db(joined, 5.0, 0.4)
            self.assertIsNotNone(head)
            self.assertIsNotNone(tail)
            self.assertGreater(head, -38.0)
            self.assertGreater(tail, -38.0)


class SyncBlockRender(unittest.TestCase):
    def test_fail_chan_render_tru_force_export(self):
        report = {"verdict": "fail", "block_render": True, "message": "Thiếu thoại tại 7/8 mốc."}
        with self.assertRaises(SyncCheckFailed) as ctx:
            assert_sync_allows_render(report)
        self.assertIn("7/8", str(ctx.exception))
        assert_sync_allows_render(report, force_export=True)

    def test_pipeline_chan_truoc_render(self):
        from autodub.server import pipeline

        src = inspect.getsource(pipeline)
        self.assertIn("assert_sync_allows_render", src)
        self.assertLess(
            src.index("assert_sync_allows_render"),
            src.index("render_with_layers("),
        )

    def test_native_duration_truoc_pad(self):
        segs = [Segment(1, 10, 12, "xin chào") for _ in range(8)]
        for i, seg in enumerate(segs):
            seg.start = 10 + i * 20
            seg.end = seg.start + 2
            seg.index = i + 1
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("autodub.video.sync_check.ffprobe_duration", return_value=5850.0), \
                 mock.patch("autodub.video.sync_check.mean_volume_db",
                            side_effect=lambda *_a, **_k: -16.0 if _a[1] < 30 else -60.0), \
                 mock.patch("autodub.video.sync_check.export_sync_previews", return_value=[]):
                report = check_dub_sync(
                    "video.mp4", os.path.join(td, "dub.picture.flac"), segs,
                    out_dir=td, duration=5850.0, stem="phim", make_previews=False,
                    placements_max_drift=0.0, native_dub_duration=120.0)
        self.assertEqual(report["verdict"], "fail")
        self.assertTrue(report["block_render"])
        self.assertGreater(report["duration_short_s"], 2.0)
        self.assertIn("Thiếu thoại tại", report["message"])


class CancelApi(unittest.TestCase):
    def test_huy_khong_gui_request_tiep(self):
        from autodub import utils
        from autodub.translate import api as tr_api

        calls = []
        ev = threading.Event()

        class SlowResp:
            def __enter__(self):
                time.sleep(0.35)
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}]}'

            def close(self):
                pass

        def fake_urlopen(req, timeout=None):
            calls.append(time.monotonic())
            return SlowResp()

        old = utils._CANCEL_EVENT_PROVIDER
        utils.set_cancel_event_provider(lambda resource="": ev)
        try:
            def cancel_soon():
                time.sleep(0.05)
                ev.set()

            threading.Thread(target=cancel_soon, daemon=True).start()
            with mock.patch.object(tr_api.urllib.request, "urlopen", fake_urlopen):
                with self.assertRaises(InterruptedError):
                    tr_api._openai_compatible_call(
                        "prompt", "k", "m", 0.2, "http://example.invalid/v1",
                        retries=3, timeout=8, stream=False,
                        provider_label="Xkiro", enforce_wall=True, idle_timeout=8)
            self.assertLessEqual(len(calls), 1)
        finally:
            utils.set_cancel_event_provider(old)


class SemanticCancel(unittest.TestCase):
    def test_huy_khong_goi_batch_tiep(self):
        import json
        from autodub import utils
        from autodub.translate.cache import TranslationIncomplete
        from autodub.translate.semantic import translate_semantic

        segs = [
            Segment(101, 0, 2, "因为我"),
            Segment(102, 2.1, 4, "回来了"),
        ]
        calls = []
        ev = threading.Event()

        def ask(prompt):
            calls.append(1)
            ev.set()
            cue = 101 if len(calls) == 1 else 102
            return json.dumps(dict(
                translated_sentences=[dict(
                    sentence_id="s1", source_ids=[cue],
                    text_vi="Tôi về nhà.", speaker=None)],
                new_entities=[], updated_summary="Đã về.", warnings=[]))

        old = utils._CANCEL_EVENT_PROVIDER
        utils.set_cancel_event_provider(lambda resource="": ev)
        try:
            with mock.patch("autodub.translate.semantic.batches", return_value=[(0, 1), (1, 2)]):
                with self.assertRaises((InterruptedError, TranslationIncomplete)):
                    translate_semantic(segs, ask, {"vi_beautify": False}, cache_path=None)
            self.assertEqual(len(calls), 1)
        finally:
            utils.set_cancel_event_provider(old)


class BatchAndCache(unittest.TestCase):
    def test_batch_cat_theo_so_ky_tu(self):
        segs = [Segment(i + 1, i * 2, i * 2 + 1.5, "x" * 1000) for i in range(3)]
        spans = batches(segs, {
            "semantic_batch_cues": 20,
            "semantic_batch_seconds": 60,
            "semantic_batch_chars": 2400,
            "semantic_group_max_cues": 1,
        })
        self.assertEqual(spans, [(0, 2), (2, 3)])

    def test_cache_bo_timeout_khoi_key(self):
        a = cache_affecting_cfg({"name_policy": "han_viet", "wait_reply": 30, "timeout": 9})
        b = cache_affecting_cfg({"name_policy": "han_viet", "wait_reply": 120, "timeout": 90})
        self.assertEqual(a, b)
        self.assertNotIn("wait_reply", a)
        self.assertEqual(api_log_tag(["xkiro", "grok-code"]), "XKIRO/grok-code")
        self.assertEqual(api_log_tag(["browser", "gemini"]), "GEMINI-WEB/gemini")


class ProcessLogAndLock(unittest.TestCase):
    def test_start_process_log_giu_file_cu(self):
        with tempfile.TemporaryDirectory() as td:
            latest = os.path.join(td, "phim.quy_trinh.log")
            Path(latest).write_text("lan 1\n", encoding="utf-8")
            old_mtime = time.time() - 120
            os.utime(latest, (old_mtime, old_mtime))
            self.assertTrue(start_process_log(latest))
            try:
                archived = list(Path(td).glob("phim.quy_trinh.*.log"))
                self.assertTrue(archived)
                self.assertIn("lan 1", archived[0].read_text(encoding="utf-8"))
                self.assertTrue(Path(latest).is_file())
            finally:
                stop_file_log()

    def test_khoa_audio_dung_lai_fingerprint(self):
        from autodub.video import process as proc

        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "dub.wav")
            out = os.path.join(td, "dub.picture.wav")
            Path(src).write_bytes(b"x" * 1024)
            Path(out).write_bytes(b"y" * 1024)
            fp = proc._lock_fingerprint(src, 10.0, 48000)
            proc._write_lock_fingerprint(out, fp)
            runs = []

            def fake_run(cmd, **_kw):
                runs.append(cmd)

            with mock.patch.object(proc, "ffprobe_duration", return_value=9.5), \
                 mock.patch.object(proc, "run", side_effect=fake_run):
                got = proc.lock_audio_to_picture_duration(src, 10.0, sr=48000, out_path=out)
            self.assertEqual(got, out)
            self.assertEqual(runs, [])


class ReviewGapsAndOverflowUi(unittest.TestCase):
    def test_gom_vung_asr_gan_nhau(self):
        rows = [
            {"start": 1.0, "end": 2.0, "reason": "unresolved_speech_gap"},
            {"start": 2.5, "end": 3.4, "reason": "unresolved_speech_gap"},
            {"start": 20.0, "end": 21.5, "reason": "suspicious_chunk"},
        ]
        gaps = compact_review_gaps(rows)
        self.assertEqual(len(gaps), 2)
        self.assertAlmostEqual(gaps[0]["start"], 1.0)
        self.assertAlmostEqual(gaps[0]["end"], 3.4)
        self.assertLess(gaps[0]["play_start"], gaps[0]["start"])

    def test_gui_co_force_export_va_overflow(self):
        panel = (Path(__file__).resolve().parents[1] / "ui" / "js" / "dub-panel.js").read_text(
            encoding="utf-8")
        self.assertIn("force_export", panel)
        self.assertIn("ttsOverflowHtml", panel)
        self.assertIn("play_start", panel)
        self.assertIn("Xuất bất chấp", panel)


if __name__ == "__main__":
    unittest.main()
