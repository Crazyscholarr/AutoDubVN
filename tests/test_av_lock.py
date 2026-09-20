"""Khóa cứng lời thoại với hình: không dồn lệch trên phim dài."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autodub.timeline import (
    MAX_START_DRIFT_SECONDS,
    fit_segments,
    fit_segments_strict,
    resolve_sync_mode,
)
from autodub.video.common import audio_duration_lock_chain, seconds_to_samples


class ResolveSyncMode(unittest.TestCase):
    def test_mac_dinh_strict(self):
        self.assertEqual(resolve_sync_mode(), "strict")
        self.assertEqual(resolve_sync_mode({}), "strict")

    def test_lock_av_ep_strict_ke_ca_cascade_cu(self):
        self.assertEqual(
            resolve_sync_mode({"sync_mode": "cascade", "lock_av": True}),
            "strict")

    def test_tat_khoa_thi_cascade(self):
        self.assertEqual(
            resolve_sync_mode({"sync_mode": "cascade", "lock_av": False}),
            "cascade")

    def test_alias_strict(self):
        self.assertEqual(resolve_sync_mode({"sync_mode": "frame-lock"}), "strict")


class FitSegmentsLock(unittest.TestCase):
    def test_strict_khong_lech_start(self):
        starts = [0.0, 3.0, 10.0, 3600.0]
        nats = [5.0, 8.0, 2.0, 4.0]
        ends = [2.5, 6.0, 12.0, 3603.0]
        pl = fit_segments_strict(starts, nats, ends=ends, max_overhang=0.75)
        self.assertTrue(all(abs(p.drift) < 1e-9 for p in pl))
        self.assertEqual([p.placed_start for p in pl], starts)

    def test_cascade_tran_5s_khong_cong_don(self):
        starts = [i * 2.0 for i in range(80)]
        nats = [10.0] * 80
        uncapped = fit_segments(
            starts, nats, max_speed=1.0, min_gap=0.0, recover_drift=False,
            max_start_drift=10_000)
        self.assertGreater(max(p.drift for p in uncapped), 100)

        capped = fit_segments(
            starts, nats, max_speed=1.0, min_gap=0.0, recover_drift=False,
            max_start_drift=MAX_START_DRIFT_SECONDS)
        self.assertLessEqual(max(p.drift for p in capped),
                             MAX_START_DRIFT_SECONDS + 1e-6)
        self.assertGreaterEqual(min(p.drift for p in capped), -1e-9)


class MixVaMuxKhoaMau(unittest.TestCase):
    def test_khoa_dung_so_mau(self):
        self.assertEqual(seconds_to_samples(120.0, 48000), 5_760_000)
        chain = audio_duration_lock_chain(3600.0, sr=48000)
        self.assertIn("aresample=48000:async=1:first_pts=0", chain)
        self.assertIn("atrim=end_sample=172800000", chain)
        self.assertNotIn("atrim=0:", chain)

    def test_mix_batch_ep_end_sample(self):
        from autodub import video as vid

        calls = []

        def fake_run(cmd, **_kw):
            with open(cmd[-1], "wb") as f:
                f.write(b"x")
            calls.append(cmd)

        with tempfile.TemporaryDirectory() as td:
            clip = os.path.join(td, "c.wav")
            with open(clip, "wb") as f:
                f.write(b"x")
            out = os.path.join(td, "mix.wav")
            with mock.patch.object(vid, "run", side_effect=fake_run):
                vid._mix_batch([(clip, 0.0)], 120_000, out, 48000)
        graph = calls[0][calls[0].index("-filter_complex") + 1]
        self.assertIn("atrim=end_sample=5760000", graph)
        self.assertNotIn("asetpts=N/SR/TB", graph)

    def test_mix_rong_cung_khoa_so_mau(self):
        from autodub import video as vid

        calls = []

        def fake_run(cmd, **_kw):
            with open(cmd[-1], "wb") as f:
                f.write(b"x")
            calls.append(cmd)

        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "silence.wav")
            with mock.patch.object(vid, "run", side_effect=fake_run):
                vid._mix_batch([], 1000, out, 48000)
        af = calls[0][calls[0].index("-af") + 1]
        self.assertIn("atrim=end_sample=48000", af)

    def test_noi_manh_uu_tien_copy(self):
        from autodub import video as vid

        calls = []

        def fake_run(cmd, **_kw):
            with open(cmd[-1], "wb") as f:
                f.write(b"x")
            calls.append(cmd)

        with tempfile.TemporaryDirectory() as td:
            files = []
            for i in range(3):
                path = os.path.join(td, f"c{i}.wav")
                with open(path, "wb") as f:
                    f.write(b"x")
                files.append(path)
            out = os.path.join(td, "joined.wav")
            with mock.patch.object(vid, "run", side_effect=fake_run):
                vid._concat_copy_chunks(files, out)
        self.assertIn("-f", calls[0])
        self.assertEqual(calls[0][calls[0].index("-f") + 1], "concat")
        self.assertIn("-c", calls[0])
        self.assertEqual(calls[0][calls[0].index("-c") + 1], "copy")


class GiaoDienVaConfig(unittest.TestCase):
    def test_gui_co_o_khoa_cung(self):
        root = Path(__file__).resolve().parents[1]
        panel = (root / "ui" / "js" / "dub-panel.js").read_text(encoding="utf-8")
        core = (root / "ui" / "js" / "core.js").read_text(encoding="utf-8")
        cfg = (root / "config.example.yaml").read_text(encoding="utf-8")
        self.assertIn("Khóa cứng lời thoại với hình", panel)
        self.assertIn("đồng hồ hình", panel.lower())
        self.assertIn("lock_av", panel)
        self.assertIn("Dựng lại giọng đọc", panel)
        self.assertIn("Khóa cứng lời thoại với hình", core)
        self.assertIn("sync_mode: strict", cfg)
        self.assertIn("lock_av: true", cfg)

    def test_render_dung_khoa_async(self):
        import inspect
        from autodub.video import final as final_mod
        from autodub.server import render as render_mod

        self.assertIn("audio_duration_lock_chain", inspect.getsource(final_mod.render_final))
        self.assertIn("audio_duration_lock_chain",
                      inspect.getsource(render_mod.render_with_layers))
        self.assertIn("lock_audio_to_picture_duration",
                      inspect.getsource(final_mod.render_final))
        self.assertIn("lock_audio_to_picture_duration",
                      inspect.getsource(render_mod.render_with_layers))

    def test_project_mac_dinh_khoa(self):
        from autodub.server import projects

        with mock.patch.object(projects, "ffprobe_video_size", return_value=(1280, 720)), \
             mock.patch.object(projects, "ffprobe_duration", return_value=10.0), \
             mock.patch.object(projects, "_load_cfg", return_value={}):
            pr = projects.default_project("x.mp4")
        self.assertTrue(pr["options"]["lock_av"])
        self.assertEqual(pr["options"]["sync_mode"], "strict")
        self.assertEqual(pr["options"]["max_start_drift_seconds"], 5.0)


class PictureLockNguonGoc(unittest.TestCase):
    def test_tim_dub_goc_ben_canh_picture(self):
        from autodub.video.process import _audio_beside_picture

        with tempfile.TemporaryDirectory() as td:
            orig = os.path.join(td, "dub.wav")
            pic = os.path.join(td, "dub.picture.wav")
            with open(orig, "wb") as fh:
                fh.write(b"0" * 600)
            self.assertEqual(_audio_beside_picture(pic), orig)
            self.assertEqual(_audio_beside_picture(orig), "")


if __name__ == "__main__":
    unittest.main()
