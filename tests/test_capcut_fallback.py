import os
import tempfile
import threading
import unittest
from unittest import mock

from autodub import tts
from autodub.srt_utils import Segment


class CapCutFallback(unittest.TestCase):
    def test_dong_loi_duoc_doc_lai_bang_giong_ke(self):
        segment = Segment(1, 0.0, 1.0, "Xin chào cô chú.")
        segment.voice = "voice_khong_hop_le"
        calls = []

        def fake_synth(text, voice, rate, out_path, **_kwargs):
            calls.append(voice)
            if voice == "voice_khong_hop_le":
                return False
            with open(out_path, "wb") as f:
                f.write(b"x" * 1024)
            return True

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(tts, "_synth_one_capcut", side_effect=fake_synth):
            paths = tts._synth_all_capcut(
                [segment], tmp, "+0%", 1, max_retries=2,
                capcut_options={
                    "concurrency": 1,
                    "fallback_concurrency": 1,
                    "fallback_voice": "voice_ke_an_toan",
                    "reuse_existing": False,
                })

            self.assertEqual(calls, ["voice_khong_hop_le", "voice_ke_an_toan"])
            self.assertTrue(paths[0] and os.path.isfile(paths[0]))

    def test_nut_dung_chan_capcut_truoc_luot_tiep_theo(self):
        segment = Segment(1, 0.0, 1.0, "Xin chào cô chú.")
        segment.voice = "voice_ke"
        cancel = threading.Event()
        cancel.set()

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(tts, "_synth_one_capcut") as synth:
            with self.assertRaisesRegex(InterruptedError, "dừng"):
                tts._synth_all_capcut(
                    [segment], tmp, "+0%", 1,
                    capcut_options={"concurrency": 1},
                    cancel_event=cancel)
        synth.assert_not_called()

    def test_huy_som_khong_xoa_file_capcut_da_luu(self):
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as tmp:
            cached = os.path.join(tmp, "capcut_00000_cache.mp3")
            with open(cached, "wb") as f:
                f.write(b"x" * 1024)
            with self.assertRaises(InterruptedError):
                tts.synthesize_text_audio(
                    "Một câu chuyện ngắn để kiểm tra nút dừng.", tmp,
                    os.path.join(tmp, "out.mp3"), engine="capcut",
                    cancel_event=cancel)
            self.assertTrue(os.path.isfile(cached))


if __name__ == "__main__":
    unittest.main()
