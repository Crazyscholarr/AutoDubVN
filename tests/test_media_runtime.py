"""Small real FFmpeg renders exercise the installed audio/video toolchain."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
from autodub import nhac_nen, overlays, slideshow, story_sources, video
from autodub.srt_utils import Segment
from autodub.server import projects, render
from autodub.utils import ffprobe_duration, ffprobe_video_size, has_nvenc, run, which


@unittest.skipUnless(which("ffmpeg") and which("ffprobe"), "FFmpeg is required")
class MediaRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="autodub-media-")
        cls.root = Path(cls.tmp.name)
        cls.source = str(cls.root / "nguồn mẫu.mp4")
        cls.audio = str(cls.root / "voice.wav")
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
             "-t", "4", "-c:v", "libx264", "-g", "24", "-pix_fmt", "yuv420p", "-c:a", "aac", cls.source])
        video.extract_audio(cls.source, cls.audio, sr=48000, loudnorm=False)
        cls.images = []
        for index, color in enumerate(("red", "blue")):
            path = cls.root / f"ảnh {index}.png"
            Image.new("RGB", (320, 180), color).save(path)
            cls.images.append(str(path))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assert_video(self, path, seconds=4):
        self.assertTrue(Path(path).is_file())
        self.assertEqual(ffprobe_video_size(path), (320, 180))
        self.assertAlmostEqual(ffprobe_duration(path), seconds, delta=.25)
        # Decode the entire result: a plausible header is not enough.
        run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"])

    def test_audio_speed_timeline_concat_and_music(self):
        fast = str(self.root / "fast.wav")
        video.change_speed(self.audio, fast, 2)
        self.assertAlmostEqual(ffprobe_duration(fast), 2, delta=.15)
        timeline = video.assemble_timeline_audio(
            [fast, fast], [0, 2.5], 5, str(self.root / "timeline.wav"), mode="ffmpeg")
        self.assertAlmostEqual(ffprobe_duration(timeline), 5.2, delta=.05)  # intentional end padding
        joined = video.concat_audio_clips([fast, fast], str(self.root / "joined.wav"))
        self.assertAlmostEqual(ffprobe_duration(joined), 4, delta=.25)
        mixed = str(self.root / "mixed.wav")
        nhac_nen.tron_nhac_nen(self.audio, mixed, music_path=fast, duck=True)
        self.assertAlmostEqual(ffprobe_duration(mixed), 4, delta=.15)

    def test_trim_and_audio_cache(self):
        target = str(self.root / "trim.wav")
        video.ensure_audio(self.source, target, trim_start=1, trim_duration=2)
        self.assertAlmostEqual(ffprobe_duration(target), 2, delta=.05)
        before = Path(target).stat().st_mtime_ns
        video.ensure_audio(self.source, target, trim_start=1, trim_duration=2)
        self.assertEqual(Path(target).stat().st_mtime_ns, before)

    def test_dub_layers_cpu_gpu_and_chunked(self):
        with mock.patch.object(projects, "_load_cfg", return_value={}):
            project = projects.default_project(self.source)
        project["regions"] = [{"x": 30, "y": 130, "w": 260, "h": 35,
                               "type": "blur", "strength": 10}]
        project["logo"] = {"path": self.images[0], "x": 5, "y": 5, "w": 32, "h": 18, "opacity": .5}
        ass = str(self.root / "sub.ass")
        overlays.save_ass(ass, [Segment(1, .2, 3.5, "Kiểm tra tiếng Việt")],
                          320, 180, project["sub_style"])
        for gpu in ([False, True] if has_nvenc() else [False]):
            project["options"]["use_gpu"] = gpu
            output = str(self.root / f"dub-{gpu}.mp4")
            render.render_with_layers(project, self.audio, output, ass_path=ass)
            self.assert_video(output)
        # Production chunks have a minimum of 60 s, so this fixture must
        # exceed that to exercise concatenation rather than the one-part path.
        long_video = str(self.root / "long.mp4")
        long_audio = str(self.root / "long.wav")
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", self.source,
             "-t", "61", "-c", "copy", long_video])
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", self.audio,
             "-t", "61", long_audio])
        project.update(video=long_video, duration=61)
        project["options"].update(use_gpu=False, render_chunk_minutes=1)
        output = str(self.root / "chunked.mp4")
        render.render_with_layers_chunked(project, long_audio, output, ass_path=ass,
            segments=[Segment(1, .2, 3.5, "Kiểm tra tiếng Việt")], tmp_dir=str(self.root / "parts"))
        self.assert_video(output, seconds=61)

    def test_images_static_and_moving(self):
        for style in ("tinh", "chuyen_dong"):
            output = str(self.root / f"images-{style}.mp4")
            slideshow.tao_video_tu_anh(self.images, self.audio, output,
                workdir=str(self.root / style), w=320, h=180, fps=24, kieu=style)
            self.assert_video(output)

    def test_cut_sources_and_story_from_video(self):
        clips = story_sources.cut_video_segments([self.source], str(self.root / "cuts"),
                                                 min_seconds=1, max_seconds=2)
        self.assertGreaterEqual(len(clips), 2)
        output = str(self.root / "story-video.mp4")
        slideshow.tao_video_tu_video([self.source], self.audio, output,
            workdir=str(self.root / "video-scenes"), w=320, h=180, fps=24,
            min_seconds=1, max_seconds=2, random_pick=False)
        self.assert_video(output)


if __name__ == "__main__":
    unittest.main()
