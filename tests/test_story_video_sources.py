import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from autodub import slideshow, story_sources


class LichVideoTheoAudio(unittest.TestCase):
    def test_random_pick_du_thoi_luong_va_khong_lap_lien_nhau(self):
        sources = ["a.mp4", "b.mp4", "c.mp4"]
        durations = {source: 3600.0 for source in sources}
        plans = slideshow.lap_lich_video(
            sources, durations, 5400.0, min_seconds=300,
            max_seconds=600, random_pick=True, random_seed=42)
        self.assertAlmostEqual(sum(item[2] for item in plans), 5400.0, places=3)
        self.assertTrue(all(300 <= item[2] <= 600 for item in plans[:-1]))
        self.assertTrue(all(a[0] != b[0] for a, b in zip(plans, plans[1:])))

    def test_nguon_ngan_van_duoc_lap_cho_du_audio(self):
        plans = slideshow.lap_lich_video(
            ["short.mp4"], {"short.mp4": 120.0}, 500.0,
            min_seconds=300, max_seconds=600, random_pick=True, random_seed=1)
        self.assertAlmostEqual(sum(item[2] for item in plans), 500.0, places=3)
        self.assertGreater(len(plans), 1)


class CropZoomFilter(unittest.TestCase):
    def test_crop_zoom_va_tam_hinh_duoc_dua_vao_ffmpeg(self):
        graph = slideshow._video_cover_filter(1920, 1080, transform={
            "zoom": 135, "x": 20, "y": 80,
            "crop_left": 10, "crop_right": 5,
            "crop_top": 3, "crop_bottom": 7,
        })
        self.assertIn("crop=iw*0.850000:ih*0.900000", graph)
        self.assertIn("crop=1920:1080", graph)
        self.assertIn("*0.200000", graph)
        self.assertIn("*0.800000", graph)

    def test_thu_nho_dung_pad_trong_khung(self):
        graph = slideshow._video_cover_filter(
            1080, 1920, transform={"zoom": 60, "x": 50, "y": 50})
        self.assertIn("force_original_aspect_ratio=decrease", graph)
        self.assertIn("pad=1080:1920", graph)


class SmokeFFmpegThuc(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Cần FFmpeg để chạy smoke test")
    def test_cat_tron_crop_zoom_ra_file_khop_audio(self):
        with tempfile.TemporaryDirectory(prefix="autodubvn-story-") as tmp:
            first = os.path.join(tmp, "a.mp4")
            second = os.path.join(tmp, "b.mp4")
            audio = os.path.join(tmp, "voice.wav")
            output = os.path.join(tmp, "result.mp4")
            commands = [
                ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                 "color=c=red:s=320x180:d=3:r=24", "-c:v", "libx264",
                 "-pix_fmt", "yuv420p", first],
                ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                 "color=c=blue:s=180x320:d=3:r=24", "-c:v", "libx264",
                 "-pix_fmt", "yuv420p", second],
                ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                 "sine=frequency=440:duration=5", audio],
            ]
            for command in commands:
                subprocess.run(command, check=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
            with mock.patch.object(slideshow, "has_nvenc", return_value=False):
                result = slideshow.tao_video_tu_video(
                    [first, second], audio, output, w=320, h=180, fps=24,
                    min_seconds=2, max_seconds=2.5, random_pick=True,
                    random_seed=7, transform={"zoom": 125, "x": 25, "y": 70,
                                              "crop_left": 5, "crop_top": 3})
            self.assertTrue(os.path.isfile(result["path"]))
            self.assertAlmostEqual(slideshow.ffprobe_duration(output), 5.0, delta=.2)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Cần FFmpeg để chạy smoke test")
    def test_cat_video_nguon_thanh_doan_nho(self):
        with tempfile.TemporaryDirectory(prefix="autodubvn-cut-") as tmp:
            source = os.path.join(tmp, "source.mp4")
            out_dir = os.path.join(tmp, "clips")
            subprocess.run([
                "ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                "testsrc=size=320x180:rate=24", "-f", "lavfi", "-i",
                "sine=frequency=440", "-t", "12", "-c:v", "libx264",
                "-g", "24", "-pix_fmt", "yuv420p", "-c:a", "aac", source,
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            clips = story_sources.cut_video_segments(
                [source], out_dir, min_seconds=4, max_seconds=6)
            self.assertGreaterEqual(len(clips), 2)
            self.assertTrue(all(os.path.isfile(path) for path in clips))
            self.assertTrue(all(slideshow.ffprobe_duration(path) > 0 for path in clips))


if __name__ == "__main__":
    unittest.main()
