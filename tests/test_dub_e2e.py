"""Production CLI E2E: real media; ASR and network responses are MOCKED.

Windows SAPI supplies a short spoken fixture without network/model downloads.
All files are confined to a unique QA directory, never an existing project.
"""
import copy
from contextlib import ExitStack
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main as cli
from autodub import speechmap, translate, utils, video
from autodub.srt_utils import Segment, load_srt_file
from autodub.vi_reflow import content_equivalent


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe")
                     and shutil.which("powershell.exe"), "FFmpeg/FFprobe and Windows SAPI required")
class DubE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # AUTODUB_QA_KEEP is test-only; an explicitly requested artifact run keeps
        # a new unique directory. Ordinary suite runs clean their own fixtures.
        cls.tmp = tempfile.TemporaryDirectory(prefix="autodub-e2e-")
        cls.root = Path(cls.tmp.name)
        if os.environ.get("AUTODUB_QA_KEEP"):
            cls.root = Path(tempfile.mkdtemp(prefix="autodub-e2e-artifacts-"))
        cls.root = cls.root / "QA tiếng Việt [中文]"
        cls.root.mkdir()
        cls.spoken = cls.root / "speech.wav"
        script = cls.root / "speech.ps1"
        script.write_text("param([string]$Destination)\n"
                          "Add-Type -AssemblyName System.Speech\n"
                          "$qaSpeaker = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
                          "$qaSpeaker.SetOutputToWaveFile($Destination)\n"
                          "$qaSpeaker.Speak('Hello. Welcome home.')\n"
                          "$qaSpeaker.Dispose()\n", encoding="utf-8")
        subprocess.run(["powershell.exe", "-NoProfile", "-File", str(script), str(cls.spoken)],
                       check=True, capture_output=True, timeout=30)
        cls.mp3 = cls.root / "response.mp3"
        utils.run(["ffmpeg", "-y", "-i", str(cls.spoken), "-c:a", "libmp3lame", str(cls.mp3)])
        cls.source = cls.root / "nguồn mẫu [中文].mp4"
        utils.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
                   "-i", str(cls.spoken), "-af", "apad", "-t", "8", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", str(cls.source)])

    @classmethod
    def tearDownClass(cls):
        utils.stop_file_log()
        if os.environ.get("AUTODUB_QA_KEEP"):
            print(f"QA artifacts: {cls.root}")
        cls.tmp.cleanup()

    def setUp(self):
        self.cfg = {
            "asr": {"backend": "faster-whisper", "device": "cpu", "compute_type": "int8",
                    "reuse_existing": False, "loudnorm": False, "filter_hallucinations": False},
            "translation": {"provider": "gemini", "gemini_api_key": "QA_DUMMY_KEY",
                            "semantic_translation": False,
                            "reuse_existing": False, "keep_source_timing": True,
                            "vi_reflow": False, "vi_beautify": True},
            "tts": {"engine": "edge", "concurrency": 1, "max_retries": 1,
                    "trim_silence": False, "lock_av": True},
            "video": {"use_gpu": False, "hardsub_vietnamese": True,
                      "keep_original_muted": True, "audio_mix_mode": "ffmpeg"},
            "output": {"dir": str(self.root / self._testMethodName), "keep_temp": True},
            "content_pipeline": {"auto_thumbnail": False, "auto_description": False},
        }
        self.source_segs = [Segment(1, .2, 3.2, "今天我回来了"),
                            Segment(2, 3.3, 5.5, "欢迎你回来"),
                            Segment(3, 5.6, 7.8, "我们在这里等你")]
        self.translated = ["Hôm nay, tôi", "đã về nhà.", "Mọi người đang chờ."]
        self.final = ["Hôm nay,", "tôi đã về nhà.", "Mọi người đang chờ."]
        self.events = []
        self.spoken_texts = []
        self.before_tts = []
        self.addCleanup(utils.stop_file_log)

    def api_response(self, prompt, *args, **kwargs):
        if prompt.startswith("[AUTODUB_SEMANTIC_V1]"):
            payload = json.loads(prompt.split("INPUT_JSON:\n", 1)[1])
            if "target_cues" in payload:
                self.events.append("semantic_translate")
                return json.dumps(dict(translated_sentences=[dict(
                    sentence_id="s1", source_ids=[s.index for s in self.source_segs],
                    text_vi="Hôm nay tôi đã về nhà và mọi người đang chờ.", speaker=None)],
                    new_entities=[], updated_summary="Tôi trở về nhà.", warnings=[]))
            self.events.append("semantic_align")
            return json.dumps(dict(cues=[dict(id=s.index,
                start=cli.srt_utils.seconds_to_timestamp(s.start),
                end=cli.srt_utils.seconds_to_timestamp(s.end), text=t)
                for s, t in zip(self.source_segs,
                               ["Hôm nay tôi đã về nhà", "và mọi người", "đang chờ."])], warnings=[]))
        if "INPUT:\n" in prompt:
            self.events.append("beautify")
            rows = json.loads(prompt.split("INPUT:\n", 1)[1].split("\n\nTrả về", 1)[0])
            for row, text in zip(rows, self.final):
                row["text"] = text
            return "```json\n" + json.dumps(rows, ensure_ascii=False) + "\n```"
        self.events.append("translate")
        return json.dumps(self.translated, ensure_ascii=False)

    def invoke(self, fail_tts=False, gui=False):
        case = self
        build_voice_track = cli.tts.build_voice_track
        def build_voice(segs, *args, **kwargs):
            self.before_tts = copy.deepcopy(segs)
            return build_voice_track(segs, *args, **kwargs)
        class Communicate:
            def __init__(self, text, **kwargs):
                case.events.append("tts")
                case.spoken_texts.append(text)
            async def save(self, destination):
                if fail_tts:
                    raise ValueError("invalid voice fixture")
                shutil.copyfile(case.mp3, destination)
        def asr_response(audio, **kwargs):
            self.assertGreater(utils.ffprobe_duration(audio), 7.5)
            self.events.append("asr")
            return copy.deepcopy(self.source_segs), "zh"
        with mock.patch.object(cli, "load_config", return_value=self.cfg), \
             mock.patch.object(sys, "argv", ["main.py", str(self.source)]), \
             mock.patch.object(cli.asr, "transcribe", side_effect=asr_response), \
             mock.patch.object(translate, "_api_call", side_effect=self.api_response), \
             mock.patch("edge_tts.Communicate", Communicate), \
             mock.patch.object(cli.tts, "build_voice_track", side_effect=build_voice), \
             mock.patch.object(speechmap, "get_active", return_value=None):
            if gui:
                from autodub.server import helpers, pipeline, projects, state
                with ExitStack() as stack:
                    for module in (pipeline, helpers):
                        stack.enter_context(mock.patch.object(module, "HERE", str(self.root / self._testMethodName)))
                    for module in (pipeline, projects):
                        stack.enter_context(mock.patch.object(module, "_load_cfg", return_value=self.cfg))
                    stack.enter_context(mock.patch.object(cli.asr, "ensure_speech_map", return_value=None))
                    pr = projects.default_project(str(self.source))
                    pr["segments"] = [dict(start=s.start, end=s.end, src=s.text, vi="") for s in self.source_segs]
                    job = {"id": 991, "path": str(self.source), "name": self.source.stem}
                    stack.enter_context(mock.patch.dict(state.STATE, {"queue": [job], "log": [], "running": False}))
                    stack.enter_context(mock.patch.dict(state.PROJECTS, {991: pr}, clear=True))
                    pipeline._run_pipeline(991, ["translate", "tts", "render"])
                    self.assertEqual(job["status"], "xong", job.get("note"))
                    self.assertFalse(state.STATE["running"])
                    self.gui_result = Path(job["output"])
                    return 0
            return cli.main()

    def test_gui_pipeline_uses_final_beautified_text(self):
        self.assertEqual(self.invoke(gui=True), 0)
        self.assertLess(self.events.index("beautify"), self.events.index("tts"))
        self.assertEqual(self.spoken_texts, self.final)
        self.assertTrue(self.gui_result.resolve().is_relative_to(self.root.resolve()))
        self.assertAlmostEqual(utils.ffprobe_duration(str(self.gui_result)), 8, delta=.25)
        utils.run(["ffmpeg", "-v", "error", "-i", str(self.gui_result), "-f", "null", "-"])

    def test_production_pipeline_beautify_to_tts_to_render(self):
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.events[:2], ["asr", "translate"])
        self.assertLess(self.events.index("beautify"), self.events.index("tts"))
        self.assertEqual(self.spoken_texts, self.final)
        folder = Path(self.cfg["output"]["dir"]) / self.source.stem
        result = folder / (self.source.stem + ".vietsub_dub.mp4")
        subs = load_srt_file(str(folder / (self.source.stem + ".vi.srt")))
        self.assertEqual([s.text for s in subs], self.final)
        self.assertEqual(len(subs), 3)
        self.assertTrue(all(0 <= s.start < s.end <= 8 for s in subs))
        self.assertEqual([(s.index, s.start, s.end) for s in self.before_tts],
                         [(s.index, s.start, s.end) for s in self.source_segs])
        # The final SRT intentionally uses placed TTS durations; beautification
        # itself preserves all original clocks, while strict placement keeps starts.
        self.assertEqual([s.start for s in subs], [s.start for s in self.source_segs])
        self.assertTrue(content_equivalent(self.translated, [s.text for s in subs]))
        self.assertTrue(result.resolve().is_relative_to(self.root.resolve()))
        self.assertAlmostEqual(utils.ffprobe_duration(str(result)), 8, delta=.25)
        self.assertTrue(utils.ffprobe_has_stream(str(result), "v"))
        self.assertTrue(utils.ffprobe_has_stream(str(result), "a"))
        utils.run(["ffmpeg", "-v", "error", "-i", str(result), "-f", "null", "-"])
        import soundfile as sf
        samples, sr = sf.read(folder / "_tmp" / "dub.wav")
        self.assertGreater(len(samples), sr * 7)
        self.assertGreater(float(abs(samples).max()), .001)

    def test_failed_tts_does_not_render_or_replace_existing_output(self):
        folder = Path(self.cfg["output"]["dir"]) / self.source.stem
        folder.mkdir(parents=True)
        result = folder / (self.source.stem + ".vietsub_dub.mp4")
        result.write_bytes(b"previous completed output sentinel")
        old_temp = folder / "_tmp" / "previous-user-note.txt"
        old_temp.parent.mkdir()
        old_temp.write_text("keep previous temporary data", encoding="utf-8")
        self.cfg["output"]["keep_temp"] = False
        self.assertEqual(self.invoke(fail_tts=True), 1)
        self.assertEqual(result.read_bytes(), b"previous completed output sentinel")
        self.assertEqual(old_temp.read_text(encoding="utf-8"), "keep previous temporary data")
        self.assertTrue((folder / (self.source.stem + ".tts_loi.txt")).is_file())

    def _semantic_render(self, gui):
        self.cfg["translation"].update(semantic_translation=True, vi_beautify=False)
        self.assertEqual(self.invoke(gui=gui), 0)
        self.assertEqual(self.spoken_texts, ["Hôm nay tôi đã về nhà và mọi người đang chờ."])
        self.assertIn("semantic_translate", self.events)
        self.assertLess(self.events.index("semantic_translate"), self.events.index("tts"))
        folder = Path(self.cfg["output"]["dir"]) / self.source.stem
        if gui:
            folder = Path(self.cfg["output"]["dir"]) / "gui-isolated" / "output" / self.source.stem
        candidates = list(Path(self.cfg["output"]["dir"]).rglob("*.vietsub_dub.mp4"))
        self.assertEqual(len(candidates), 1)
        result = candidates[0]
        subs = load_srt_file(str(result.parent / (self.source.stem + ".vi.srt")))
        self.assertEqual([s.index for s in subs], [s.index for s in self.source_segs])
        self.assertEqual(len(subs), 3)
        self.assertAlmostEqual(subs[0].start, self.source_segs[0].start, delta=0.05)
        for left, right in zip(subs, subs[1:]):
            self.assertAlmostEqual(left.end, right.start, delta=0.06)
        chinese = [(round(s.start, 3), round(s.end, 3)) for s in self.source_segs]
        got = [(round(s.start, 3), round(s.end, 3)) for s in subs]
        self.assertNotEqual(got, chinese)
        self.assertAlmostEqual(utils.ffprobe_duration(str(result)), 8, delta=.25)
        utils.run(["ffmpeg", "-v", "error", "-i", str(result), "-f", "null", "-"])
        import soundfile as sf
        audio, sr = sf.read(result.parent / "_tmp" / "dub.wav")
        self.assertGreater(float(abs(audio).max()), .001)

    def test_semantic_cli_reads_group_and_subs_follow_speech(self):
        self._semantic_render(False)

    def test_semantic_gui_reads_group_and_subs_follow_speech(self):
        self._semantic_render(True)


if __name__ == "__main__":
    unittest.main()
