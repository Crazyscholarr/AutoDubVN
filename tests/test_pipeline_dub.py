"""Kiểm tra pipeline lồng tiếng truyền đúng cấu hình ASR/TTS."""
import unittest

from autodub.server.pipeline import (
    _asr_transcribe_kwargs, _assert_render_ready, _tts_input_signature,
    _tts_retry_kwargs,
)
from autodub.video.sync_check import SyncCheckFailed


class PipelineDubConfig(unittest.TestCase):
    def test_asr_nhan_audio_gap_rescue_tu_config(self):
        kw = _asr_transcribe_kwargs({
            "backend": "faster-whisper",
            "audio_gap_rescue": False,
            "speech_gap_seconds": 2.5,
            "speech_silence_db": -38,
            "speech_min_silence": 0.5,
            "rescue_gaps": False,
        })
        self.assertEqual(kw["backend"], "faster-whisper")
        self.assertIs(kw["audio_gap_rescue"], False)
        self.assertEqual(kw["speech_gap_seconds"], 2.5)
        self.assertEqual(kw["speech_silence_db"], -38)
        self.assertEqual(kw["speech_min_silence"], 0.5)
        self.assertIs(kw["rescue_gaps"], False)

    def test_asr_mac_dinh_bat_cuu_vung_co_tieng(self):
        kw = _asr_transcribe_kwargs({})
        self.assertIs(kw["audio_gap_rescue"], True)
        self.assertEqual(kw["speech_gap_seconds"], 1.2)
        self.assertTrue(kw["rescue_gaps"])

    def test_tts_lay_max_retries_tu_config(self):
        kw = _tts_retry_kwargs({"max_retries": 2, "retry_delay": 1.2})
        self.assertEqual(kw["max_retries"], 2)
        self.assertEqual(kw["retry_base_delay"], 1.2)

    def test_tts_gia_tri_loi_thi_ve_mac_dinh(self):
        kw = _tts_retry_kwargs({"max_retries": "x", "retry_delay": None})
        self.assertEqual(kw["max_retries"], 4)
        self.assertEqual(kw["retry_base_delay"], 1.2)

    def test_render_chi_dung_dung_ban_da_tao_audio(self):
        rows = [{
            "start": 0.0, "end": 1.5, "src": "你好",
            "vi": "Xin chào", "placed": 0.0, "voice_dur": 1.1, "speed": 1.0,
        }]
        opt = {
            "engine": "edge", "narrator_voice": "vi-VN-NamMinhNeural",
            "narrator_pitch": "+0Hz", "base_rate": "+0%",
            "lock_av": True,
        }
        tc, tr = {}, {"semantic_translation": True}
        signature = _tts_input_signature(rows, opt, tc, tr, 2.0)
        opt["last_tts_signature"] = signature
        report = {"verdict": "ok", "block_render": False,
                  "tts_signature": signature}
        _assert_render_ready(rows, opt, tc, tr, 2.0, report)

        changed = [dict(rows[0], vi="Xin chào bạn")]
        with self.assertRaisesRegex(RuntimeError, "thay đổi"):
            _assert_render_ready(changed, opt, tc, tr, 2.0, report)

    def test_render_chan_bao_cao_sync_cu(self):
        rows = [{"start": 0.0, "end": 1.0, "src": "你好", "vi": "Xin chào"}]
        opt, tc, tr = {"engine": "edge"}, {}, {}
        signature = _tts_input_signature(rows, opt, tc, tr, 1.0)
        opt["last_tts_signature"] = signature
        with self.assertRaises(SyncCheckFailed):
            _assert_render_ready(
                rows, opt, tc, tr, 1.0,
                {"verdict": "ok", "block_render": False},
            )


if __name__ == "__main__":
    unittest.main()
