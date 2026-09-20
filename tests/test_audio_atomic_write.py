"""P0: FFmpeg audio extraction must keep the media suffix on atomic temps."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from autodub.utils import which
from autodub.video.common import (
    audio_muxer_args, discard_stale_media_temps, media_temp_path,
)
from autodub.video import extract

FILM_A = Path(r"E:\Video\AutoDubVN\downloads\一亩灵田修长生 [BV1GAY56VEwU].mp4")
FILM_B = Path(r"E:\Video\AutoDubVN\downloads\1一8万集超长合集！！！时长高达32万小时！！！爽到爆炸！ [BV1rF4Q6EEUT].mp4")


def _fake_probe(cmd, **kwargs):
    if cmd and os.path.basename(str(cmd[0])).lower().startswith("ffprobe"):
        return SimpleNamespace(
            stdout=json.dumps({"streams": [{"sample_rate": "16000", "channels": 1}]}),
            stderr="",
        )
    if cmd[:3] == ["ffmpeg", "-hide_banner", "-i"]:
        return SimpleNamespace(stdout="", stderr="mean_volume: -20.0 dB")
    Path(cmd[-1]).write_bytes(b"a" * 2000)
    return SimpleNamespace(stdout="", stderr="")


class TempNaming(unittest.TestCase):
    def test_preserves_media_suffix_and_rejects_old_partial(self):
        cases = [
            ("audio16k.flac", ".flac"),
            ("audio.wav", ".wav"),
            ("audio.m4a", ".m4a"),
            ("video.mp4", ".mp4"),
        ]
        for name, suffix in cases:
            temp = media_temp_path(str(Path("out") / name), token="jobid")
            self.assertTrue(temp.endswith(suffix), msg=temp)
            self.assertIn(".partial" + suffix, temp)
            self.assertNotEqual(temp, os.path.abspath(str(Path("out") / name)))
            self.assertFalse(temp.endswith(suffix + ".partial"))
            self.assertFalse(temp.endswith(suffix + ".tmp"))
            self.assertFalse(temp.endswith(suffix + ".part"))
            self.assertNotEqual(os.path.basename(temp), name)

    def test_muxer_matches_requested_format(self):
        self.assertEqual(audio_muxer_args("a.flac"), ["-f", "flac"])
        self.assertEqual(audio_muxer_args("a.wav"), ["-f", "wav"])
        self.assertEqual(audio_muxer_args("a.m4a"), ["-f", "ipod"])
        self.assertEqual(audio_muxer_args("a.mp3"), ["-f", "mp3"])

    def test_legacy_flac_partial_is_cleaned_without_touching_final(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "audio16k.flac"
            dest.write_bytes(b"final" * 400)
            legacy = Path(td) / "audio16k.flac.partial"
            stale = Path(td) / "audio16k.abc.partial.flac"
            legacy.write_bytes(b"old")
            stale.write_bytes(b"temp" * 400)
            discard_stale_media_temps(str(dest))
            self.assertTrue(dest.exists())
            self.assertFalse(legacy.exists())
            self.assertFalse(stale.exists())
            self.assertEqual(dest.read_bytes()[:5], b"final")


class AtomicExtract(unittest.TestCase):
    def test_ffmpeg_command_uses_partial_flac_not_flac_partial(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.mp4"
            source.write_bytes(b"source")
            dest = Path(td) / "audio16k.flac"
            seen = []

            def fake_run(cmd, **kwargs):
                if cmd and str(cmd[0]).startswith("ffprobe"):
                    return _fake_probe(cmd, **kwargs)
                if cmd[:3] == ["ffmpeg", "-hide_banner", "-i"]:
                    return _fake_probe(cmd, **kwargs)
                seen.append(cmd)
                Path(cmd[-1]).write_bytes(b"a" * 2000)
                return SimpleNamespace(stdout="", stderr="")

            with patch.object(extract, "run", side_effect=fake_run), \
                 patch.object(extract, "ffprobe_duration", return_value=2):
                extract.extract_audio(str(source), str(dest), loudnorm=False)
            self.assertTrue(seen)
            out = seen[0][-1]
            self.assertTrue(out.endswith(".partial.flac"))
            self.assertFalse(out.endswith(".flac.partial"))
            self.assertIn("-f", seen[0])
            self.assertEqual(seen[0][seen[0].index("-f") + 1], "flac")
            self.assertTrue(dest.exists())
            self.assertFalse(Path(str(dest) + ".partial").exists())

    def test_zero_byte_target_is_regenerated(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.mp4"
            source.write_bytes(b"source")
            dest = Path(td) / "audio16k.flac"
            dest.write_bytes(b"")
            with patch.object(extract, "run", side_effect=_fake_probe), \
                 patch.object(extract, "ffprobe_duration", return_value=2), \
                 patch.object(extract, "probe_media_clocks",
                              return_value=dict(audio_duration=2.0, format_duration=2.0)):
                extract.ensure_audio(str(source), str(dest), trim_duration=2)
            self.assertGreater(dest.stat().st_size, 1024)
            self.assertTrue(Path(str(dest) + ".source.json").exists())

    def test_valid_cache_is_reused(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.mp4"
            source.write_bytes(b"source")
            dest = Path(td) / "audio16k.flac"
            dest.write_bytes(b"a" * 2000)
            with patch.object(extract, "run", side_effect=_fake_probe), \
                 patch.object(extract, "ffprobe_duration", return_value=2), \
                 patch.object(extract, "probe_media_clocks",
                              return_value=dict(audio_duration=2.0, format_duration=2.0)), \
                 patch.object(extract, "extract_audio") as make:
                extract.ensure_audio(str(source), str(dest), trim_duration=2)
                extract.ensure_audio(str(source), str(dest), trim_duration=2)
            make.assert_not_called()

    def test_ffmpeg_failure_keeps_valid_final(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.mp4"
            source.write_bytes(b"source")
            dest = Path(td) / "audio16k.flac"
            dest.write_bytes(b"good" * 500)
            before = dest.read_bytes()

            def boom(cmd, **kwargs):
                raise RuntimeError("injected ffmpeg failure")

            with patch.object(extract, "run", side_effect=boom):
                with self.assertRaises(RuntimeError):
                    extract.extract_audio(str(source), str(dest), loudnorm=False)
            self.assertEqual(dest.read_bytes(), before)
            self.assertFalse(list(Path(td).glob("*.partial.flac")))

    def test_cancel_removes_temp_and_keeps_valid_final(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.mp4"
            source.write_bytes(b"source")
            dest = Path(td) / "audio16k.flac"
            dest.write_bytes(b"good" * 500)

            def cancel(cmd, **kwargs):
                raise InterruptedError("cancel")

            with patch.object(extract, "run", side_effect=cancel):
                with self.assertRaises(InterruptedError):
                    extract.extract_audio(str(source), str(dest), loudnorm=False)
            self.assertGreater(dest.stat().st_size, 1024)
            self.assertEqual(dest.read_bytes()[:4], b"good")
            self.assertFalse(list(Path(td).glob("*.partial.flac")))

    def test_crash_between_temp_and_replace_does_not_promote_temp(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "audio16k.flac"
            dest.write_bytes(b"final" * 400)
            stale = Path(td) / "audio16k.abc.partial.flac"
            stale.write_bytes(b"temp" * 400)
            discard_stale_media_temps(str(dest))
            self.assertTrue(dest.exists())
            self.assertFalse(stale.exists())
            self.assertEqual(dest.read_bytes()[:5], b"final")


@unittest.skipUnless(which("ffmpeg") and which("ffprobe"), "FFmpeg is required")
class RealFfmpegExtract(unittest.TestCase):
    def _make_source(self, path: Path, seconds: float = 2.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        from autodub.utils import run
        run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", f"{seconds:.2f}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(path),
        ])

    def _assert_extract(self, source: Path, dest: Path, sr: int = 16000):
        extract.extract_audio(str(source), str(dest), sr=sr, loudnorm=False)
        self.assertTrue(dest.exists())
        self.assertGreater(dest.stat().st_size, 1024)
        self.assertFalse(Path(str(dest) + ".partial").exists())
        self.assertFalse(list(dest.parent.glob(dest.stem + ".*.partial" + dest.suffix)))
        from autodub.utils import ffprobe_duration, run
        self.assertGreater(ffprobe_duration(str(dest)), 0.5)
        probe = run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                     "-show_entries", "stream=sample_rate,channels",
                     "-of", "json", str(dest)])
        stream = json.loads(probe.stdout)["streams"][0]
        self.assertEqual(int(stream["sample_rate"]), sr)
        self.assertEqual(int(stream["channels"]), 1)

    def test_old_suffix_cannot_infer_muxer_new_suffix_can(self):
        from autodub.utils import run
        with tempfile.TemporaryDirectory() as td:
            cmd = [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "sine=frequency=440:sample_rate=16000",
                "-t", "0.4", "-ac", "1", "-ar", "16000", "-c:a", "flac",
            ]
            bad = str(Path(td) / "audio16k.flac.partial")
            good = str(Path(td) / "audio16k.jobid.partial.flac")
            with self.assertRaises(RuntimeError) as raised:
                run(cmd + [bad])
            self.assertIn("Unable to choose an output format", str(raised.exception))
            self.assertFalse(os.path.exists(bad) and os.path.getsize(bad) > 1024)
            run(cmd + [good])
            self.assertGreater(os.path.getsize(good), 1024)

    def test_run1_ascii_filename(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "clip.mp4"
            dest = Path(td) / "audio16k.flac"
            self._make_source(source)
            self._assert_extract(source, dest)

    def test_run2_chinese_filename(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "一亩灵田修长生 [BV1GAY56VEwU]"
            source = root / "一亩灵田修长生 [BV1GAY56VEwU].mp4"
            dest = root / "_tmp" / "audio16k.flac"
            self._make_source(source)
            self._assert_extract(source, dest)

    def test_run3_vietnamese_unicode_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "nguồn mẫu video"
            source = root / "phim tiếng Việt.mp4"
            dest = root / "audio16k.wav"
            self._make_source(source)
            self._assert_extract(source, dest)

    def test_run4_spaces_and_brackets(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "output dir [test]"
            source = root / "file [BV].mp4"
            dest = root / "audio16k.flac"
            self._make_source(source)
            self._assert_extract(source, dest)

    def test_run5_target_absent(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "clip.mp4"
            dest = Path(td) / "missing" / "audio16k.flac"
            self._make_source(source)
            self.assertFalse(dest.exists())
            extract.ensure_audio(str(source), str(dest), loudnorm=False, trim_duration=2)
            self.assertTrue(dest.exists())
            self.assertTrue(Path(str(dest) + ".source.json").exists())

    def test_run6_valid_cache_reused(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "clip.mp4"
            dest = Path(td) / "audio16k.flac"
            self._make_source(source)
            extract.ensure_audio(str(source), str(dest), loudnorm=False, trim_duration=2)
            before = dest.stat().st_mtime_ns
            extract.ensure_audio(str(source), str(dest), loudnorm=False, trim_duration=2)
            self.assertEqual(dest.stat().st_mtime_ns, before)

    def test_run7_zero_byte_regenerated(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "clip.mp4"
            dest = Path(td) / "audio16k.flac"
            self._make_source(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"")
            extract.ensure_audio(str(source), str(dest), loudnorm=False, trim_duration=2)
            self.assertGreater(dest.stat().st_size, 1024)

    def test_run8_real_ffmpeg_failure_keeps_valid_final(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "clip.mp4"
            dest = Path(td) / "audio16k.flac"
            self._make_source(source)
            extract.extract_audio(str(source), str(dest), loudnorm=False)
            before = dest.read_bytes()
            with patch.object(extract, "_audio_encode_args",
                              return_value=["-c:a", "not_a_real_codec"]):
                with self.assertRaises(RuntimeError):
                    extract.extract_audio(str(source), str(dest), loudnorm=False)
            self.assertEqual(dest.read_bytes(), before)
            self.assertFalse(list(Path(td).glob("*.partial.flac")))

    def test_run9_cancel_keeps_valid_final(self):
        from autodub import utils
        from autodub.utils import ffprobe_duration
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "clip.mp4"
            dest = Path(td) / "audio16k.flac"
            self._make_source(source, seconds=20.0)
            extract.extract_audio(str(source), str(dest), loudnorm=False)
            before = dest.read_bytes()
            event = threading.Event()
            error = []

            def worker():
                try:
                    extract.extract_audio(str(source), str(dest), loudnorm=True)
                except BaseException as exc:
                    error.append(exc)

            with patch.object(utils, "_CANCEL_EVENT_PROVIDER", None), \
                 patch.object(utils, "_CANCEL_EVENT", event):
                thread = threading.Thread(target=worker)
                thread.start()
                time.sleep(0.05)
                event.set()
                thread.join(timeout=30)
            self.assertFalse(thread.is_alive())
            self.assertTrue(error)
            self.assertGreater(dest.stat().st_size, 1024)
            self.assertFalse(list(Path(td).glob("*.partial.flac")))
            if dest.read_bytes() != before:
                self.assertGreater(ffprobe_duration(str(dest)), 0.5)
            extract.extract_audio(str(source), str(dest), loudnorm=False)
            self.assertGreater(dest.stat().st_size, 1024)
            self.assertGreater(ffprobe_duration(str(dest)), 15.0)


@unittest.skipUnless(which("ffmpeg") and which("ffprobe"), "FFmpeg is required")
class DownloadedFilmExtract(unittest.TestCase):
    def test_run10_real_bilibili_extract(self):
        present = [p for p in (FILM_A, FILM_B) if p.exists()]
        if not present:
            self.skipTest("downloaded Bilibili files missing")
        from autodub.utils import ffprobe_duration
        with tempfile.TemporaryDirectory() as td:
            for index, source in enumerate(present):
                dest = Path(td) / f"film-{index}" / "audio16k.flac"
                dest.parent.mkdir(parents=True)
                extract.extract_audio(str(source), str(dest), sr=16000,
                                      loudnorm=False, trim_duration=3.0)
                self.assertGreater(dest.stat().st_size, 1024)
                self.assertGreater(ffprobe_duration(str(dest)), 1.0)
                self.assertFalse(Path(str(dest) + ".partial").exists())
                self.assertFalse(list(dest.parent.glob("*.partial.flac")))


@unittest.skipUnless(which("ffmpeg") and which("ffprobe"), "FFmpeg is required")
class ShortAsrSmoke(unittest.TestCase):
    def test_run10_short_asr_reaches_paraformer(self):
        source = FILM_B if FILM_B.exists() else FILM_A
        if not source.exists():
            self.skipTest("downloaded Bilibili file missing")
        try:
            import funasr  # noqa: F401
        except ImportError:
            self.skipTest("FunASR is not installed")
        from autodub.asr.funasr import _asr_funasr
        from autodub.utils import ffprobe_duration
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "smoke" / "audio16k.flac"
            dest.parent.mkdir(parents=True)
            extract.extract_audio(str(source), str(dest), sr=16000,
                                  loudnorm=False, trim_start=20.0,
                                  trim_duration=45.0)
            self.assertGreater(ffprobe_duration(str(dest)), 20.0)
            try:
                segs, _lang = _asr_funasr(str(dest), "zh", "cuda")
            except Exception:
                segs, _lang = _asr_funasr(str(dest), "zh", "cpu")
            self.assertTrue(segs)
            joined = "".join(getattr(seg, "text", "") or "" for seg in segs)
            self.assertRegex(joined, r"[\u4e00-\u9fff]")


if __name__ == "__main__":
    unittest.main()
