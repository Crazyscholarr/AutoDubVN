"""Khóa đồng hồ hình/tiếng: lệch tuyến tính sau ~1 phút, kéo xuyên suốt."""
import os
import tempfile
import unittest

from autodub.media_clock import (
    apply_picture_clock,
    clocks_from_probe_json,
    compute_time_scale,
    looks_already_scaled,
    scale_segment_times,
    should_apply_scale,
    stretch_factor,
)
from autodub.srt_utils import Segment
from autodub.video.process import _atempo_chain


def _clocks(picture=3618.0, audio=3600.0):
    return {
        "format_duration": audio,
        "audio_duration": audio,
        "audio_ref": audio,
        "picture_duration": picture,
        "video_duration": picture,
        "time_scale": picture / audio,
        "vfr": False,
        "fps": 23.976,
        "fps_nominal": 24.0,
        "nb_frames": 0,
        "video_start": 0.0,
        "audio_start": 0.0,
        "format_start": 0.0,
    }


class ParseProbe(unittest.TestCase):
    def test_uu_tien_duration_luong_hinh(self):
        data = {
            "format": {"duration": "3600.040000"},
            "streams": [
                {
                    "codec_type": "video",
                    "duration": "3618.018000",
                    "avg_frame_rate": "24000/1001",
                    "r_frame_rate": "24/1",
                    "nb_frames": "86760",
                    "start_time": "0.000000",
                },
                {
                    "codec_type": "audio",
                    "duration": "3600.040000",
                    "start_time": "0.000000",
                },
            ],
        }
        clocks = clocks_from_probe_json(data)
        self.assertAlmostEqual(clocks["picture_duration"], 3618.018, places=3)
        self.assertAlmostEqual(clocks["audio_ref"], 3600.04, places=2)
        self.assertGreater(clocks["time_scale"], 1.004)

    def test_mkv_tag_duration(self):
        data = {
            "format": {"duration": "120.0"},
            "streams": [
                {
                    "codec_type": "video",
                    "tags": {"DURATION": "00:02:01.200000000"},
                    "avg_frame_rate": "25/1",
                    "r_frame_rate": "25/1",
                },
                {
                    "codec_type": "audio",
                    "tags": {"DURATION": "00:02:00.000000000"},
                },
            ],
        }
        clocks = clocks_from_probe_json(data)
        self.assertAlmostEqual(clocks["picture_duration"], 121.2, places=2)
        self.assertAlmostEqual(clocks["audio_ref"], 120.0, places=2)


class TyLeVaAtempo(unittest.TestCase):
    def test_scale_1_phan_tram(self):
        scale = compute_time_scale(3618.0, 3600.0)
        self.assertAlmostEqual(scale, 1.005, places=4)
        self.assertTrue(should_apply_scale(scale, 3618.0))
        # Sau 1 phút lệch ~0.3s; cuối phim ~18s.
        self.assertAlmostEqual((scale - 1.0) * 60.0, 0.3, places=2)
        self.assertAlmostEqual((scale - 1.0) * 3618.0, 18.09, places=1)

    def test_ty_le_vo_ly_bo_qua(self):
        self.assertEqual(compute_time_scale(3600, 100), 1.0)
        self.assertFalse(should_apply_scale(1.00001, 3600.0))

    def test_stretch_keo_cham_khi_tieng_ngan_hon_hinh(self):
        factor = stretch_factor(3600.0, 3618.0)
        self.assertIsNotNone(factor)
        self.assertAlmostEqual(factor, 3600.0 / 3618.0, places=6)

    def test_stretch_khong_keo_duoi_dem_mix(self):
        self.assertIsNone(stretch_factor(3600.2, 3600.0))
        self.assertIsNone(stretch_factor(3601.0, 3600.0))
        self.assertIsNone(stretch_factor(5.2, 5.0))
        self.assertIsNone(stretch_factor(10.04, 10.0))
        self.assertIsNone(stretch_factor(61.0, 61.112))

    def test_stretch_khong_keo_khi_chi_thieu_duoi_phim_dai(self):
        # BV1rX8c6DEgv: đồng hồ đã khớp, mix thiếu ~2.4s đuôi — pad, không atempo.
        self.assertIsNone(stretch_factor(1384.232, 1386.633))
        self.assertIsNone(stretch_factor(3600.0, 3605.0))

    def test_atempo_duoi_0_5_va_keo_cham(self):
        chain = _atempo_chain(0.995)
        self.assertIn("atempo=0.995", chain)
        self.assertNotIn("atempo=2.0", chain)
        slow = _atempo_chain(0.25)
        self.assertIn("atempo=0.5", slow)
        parts = [p for p in slow.split(",") if p.startswith("atempo=")]
        self.assertGreaterEqual(len(parts), 2)


class KeoMoc(unittest.TestCase):
    def test_nhan_start_end_xoa_placed(self):
        segs = [Segment(1, 60.0, 62.0, "a"), Segment(2, 120.0, 123.0, "b")]
        segs[0].placed_start = 60.0
        n = scale_segment_times(segs, 1.005)
        self.assertEqual(n, 2)
        self.assertAlmostEqual(segs[0].start, 60.3, places=3)
        self.assertIsNone(segs[0].placed_start)

    def test_apply_mot_lan_khong_nhan_doi(self):
        segs = [Segment(1, 60.0, 62.0, "a"), Segment(2, 3590.0, 3595.0, "b")]
        pr = {"video": "x.mp4", "clocks": _clocks(), "options": {}}
        with tempfile.TemporaryDirectory() as td:
            side = os.path.join(td, "phim.av_clock.json")
            info = apply_picture_clock(
                segs, pr=pr, sidecar=side, reset=True, trust_wav=False)
            self.assertTrue(info["applied"])
            first = segs[0].start
            self.assertAlmostEqual(first, 60.3, places=2)
            info2 = apply_picture_clock(
                segs, pr=pr, sidecar=side, reset=False, trust_wav=False)
            self.assertFalse(info2["applied"])
            self.assertAlmostEqual(segs[0].start, first, places=6)
            self.assertTrue(os.path.exists(side))

    def test_srt_da_dai_theo_hinh_thi_khong_nhan_lai(self):
        clocks = _clocks()
        segs = [Segment(1, 60.3, 62.3, "a"),
                Segment(2, 3610.0, 3617.5, "b")]
        self.assertTrue(looks_already_scaled(segs, clocks, 1.005))
        pr = {"video": "x.mp4", "clocks": clocks, "options": {}}
        with tempfile.TemporaryDirectory() as td:
            side = os.path.join(td, "phim.av_clock.json")
            before = segs[0].start
            info = apply_picture_clock(
                segs, pr=pr, sidecar=side, reset=False, trust_wav=False)
            self.assertFalse(info["applied"])
            self.assertAlmostEqual(segs[0].start, before, places=6)

    def test_khong_ghi_de_sidecar_1_0_len_ti_le_that(self):
        import json
        segs = [Segment(1, 60.0, 62.0, "a"), Segment(2, 3590.0, 3595.0, "b")]
        pr = {"video": "x.mp4", "clocks": _clocks(), "options": {}}
        with tempfile.TemporaryDirectory() as td:
            side = os.path.join(td, "phim.av_clock.json")
            info = apply_picture_clock(
                segs, pr=pr, sidecar=side, reset=True, trust_wav=False)
            self.assertTrue(info["applied"])
            pr["clocks"] = _clocks(picture=3600.0, audio=3600.0)
            info2 = apply_picture_clock(
                segs, pr=pr, sidecar=side, reset=False, trust_wav=False)
            self.assertFalse(info2["applied"])
            self.assertAlmostEqual(info2["scale"], 1.005, places=4)
            with open(side, encoding="utf-8") as fh:
                data = json.loads(fh.read())
            self.assertAlmostEqual(data["applied_scale"], 1.005, places=4)

    def test_speechmap_scale_giu_time_scale(self):
        from autodub import speechmap
        m = speechmap.SpeechMap([(1.0, 1.2), (2.0, 2.3)])
        s = m.scale(1.005)
        self.assertAlmostEqual(s.marks[0][0], 1.005, places=4)
        self.assertAlmostEqual(s.time_scale, 1.005, places=4)
        s2 = s.scale(1.0)
        self.assertAlmostEqual(s2.time_scale, 1.005, places=4)


class GanVaoMux(unittest.TestCase):
    def test_render_final_khoa_picture(self):
        import inspect
        from autodub.video import final as final_mod
        from autodub.server import render as render_mod
        from autodub.server import pipeline as pipeline_mod

        src_final = inspect.getsource(final_mod.render_final)
        self.assertIn("lock_audio_to_picture_duration", src_final)
        self.assertIn("picture_duration_for", src_final)
        self.assertIn("lock_audio_to_picture_duration",
                      inspect.getsource(render_mod.render_with_layers))
        self.assertIn("lock_audio_to_picture_duration",
                      inspect.getsource(pipeline_mod._run_pipeline))
        self.assertIn("apply_picture_clock",
                      inspect.getsource(pipeline_mod._keo_dong_ho_hinh))


if __name__ == "__main__":
    unittest.main()
