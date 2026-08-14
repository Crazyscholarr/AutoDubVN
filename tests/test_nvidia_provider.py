"""Kiểm tra provider NVIDIA NIM (build.nvidia.com) cho khâu dịch.

Toàn bộ test chạy offline: mock urllib để kiểm tra nhãn lỗi, backoff 429 và
việc đọc cấu hình - không gọi mạng thật.

Chạy: python -m unittest discover -s tests
"""
import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub import translate as tr
from autodub.server.config_api import (_TRANSLATION_GUI_KEYS,
                                       _translation_api_params)


def _http_429(url="https://x"):
    return urllib.error.HTTPError(
        url, 429, "Too Many Requests", {},
        io.BytesIO(b'{"status":429,"title":"Too Many Requests"}'))


class DocCauHinhNvidia(unittest.TestCase):
    def test_gui_keys_co_du_bo_nvidia(self):
        for k in ("nvidia_api_key", "nvidia_base_url",
                  "nvidia_model", "nvidia_timeout"):
            self.assertIn(k, _TRANSLATION_GUI_KEYS, k)

    def test_translation_api_params_nhanh_nvidia(self):
        key, model, base, timeout = _translation_api_params({
            "nvidia_api_key": "nvapi-test",
            "nvidia_model": "z-ai/glm-5.2",
            "nvidia_base_url": "https://integrate.api.nvidia.com/v1",
            "nvidia_timeout": 300,
        }, "nvidia")
        self.assertEqual(key, "nvapi-test")
        self.assertEqual(model, "z-ai/glm-5.2")
        self.assertEqual(base, "https://integrate.api.nvidia.com/v1")
        self.assertEqual(timeout, 300)

    def test_mac_dinh_khi_thieu_cau_hinh(self):
        key, model, base, timeout = _translation_api_params({}, "nvidia")
        self.assertEqual(key, "")
        self.assertEqual(model, "z-ai/glm-5.2")
        self.assertIsNone(base)          # None -> _api_call tự dùng default
        self.assertEqual(timeout, 420)


class NhanLoiVaBackoff(unittest.TestCase):
    def test_loi_429_mang_nhan_nvidia_khong_phai_tokenrouter(self):
        waits = []
        with mock.patch.object(tr.urllib.request, "urlopen",
                               side_effect=_http_429()), \
             mock.patch.object(tr.time, "sleep", waits.append):
            with self.assertRaises(RuntimeError) as ctx:
                tr._api_call("prompt", "nvapi-x", "z-ai/glm-5.2", 0.3,
                             provider="nvidia")
        msg = str(ctx.exception)
        self.assertIn("NVIDIA", msg)
        self.assertNotIn("TokenRouter", msg)

    def test_backoff_429_cua_nvidia_kien_nhan_du_vuot_dot_chan(self):
        """Tier free chặn burst 1-2 phút, không có Retry-After: 5 lần thử,
        chờ 15/30/45/60s và KHÔNG ngủ vô ích sau lần thử cuối."""
        waits = []
        with mock.patch.object(tr.urllib.request, "urlopen",
                               side_effect=_http_429()), \
             mock.patch.object(tr.time, "sleep", waits.append):
            with self.assertRaises(RuntimeError):
                tr._api_call("prompt", "nvapi-x", "z-ai/glm-5.2", 0.3,
                             provider="nvidia")
        self.assertEqual(waits, [15.0, 30.0, 45.0, 60.0])

    def test_provider_cu_giu_nguyen_backoff_ngan(self):
        waits = []
        with mock.patch.object(tr.urllib.request, "urlopen",
                               side_effect=_http_429()), \
             mock.patch.object(tr.time, "sleep", waits.append):
            with self.assertRaises(RuntimeError) as ctx:
                tr._api_call("prompt", "ix_x", "deepseek-v4-flash", 0.3,
                             provider="inferx")
        self.assertEqual(waits, [2.0, 4.0], "inferx giữ nhịp chờ cũ 2s/4s")
        self.assertIn("TokenRouter", str(ctx.exception))

    def test_thieu_key_bao_ro_cach_lay(self):
        from autodub.srt_utils import Segment
        with self.assertRaises(ValueError) as ctx:
            tr.translate_segments(
                [Segment(1, 0.0, 1.0, "你好")], api_key="",
                provider="nvidia", model="z-ai/glm-5.2")
        self.assertIn("build.nvidia.com", str(ctx.exception))


class GiuDongSachKhiLoLanTiengTrung(unittest.TestCase):
    def test_chi_dich_lai_dung_dong_ban_khong_vut_ca_lo(self):
        """Lô 3 dòng, 1 dòng lẫn tiếng Trung -> chỉ tốn 1 request dịch bù,
        không phải 3 request dịch lại từ đầu (đỡ chậm + đỡ rate-limit)."""
        from autodub.srt_utils import Segment
        segs = [Segment(1, 0.0, 2.0, "你好"),
                Segment(2, 2.0, 4.0, "谢谢"),
                Segment(3, 4.0, 6.0, "再见")]
        calls = []

        def fake_api(prompt, *a, **k):
            calls.append(prompt)
            if len(calls) == 1:      # lô đầu: dòng 2 còn nguyên tiếng Trung
                return '["Xin chào", "谢谢", "Tạm biệt"]'
            return '["Cảm ơn"]'      # chỉ được hỏi bù đúng 1 dòng

        with mock.patch.object(tr, "_api_call", side_effect=fake_api):
            tr.translate_segments(segs, api_key="nvapi-x", provider="nvidia",
                                  model="z-ai/glm-5.2", chunk_size=3,
                                  cache_path=None,
                                  shorten_long_lines_enabled=False)
        self.assertEqual(len(calls), 2, "1 lô + 1 câu bù, không hơn")
        self.assertEqual([s.text for s in segs],
                         ["Xin chào", "Cảm ơn", "Tạm biệt"])


class MacDinhNvidia(unittest.TestCase):
    def test_hang_so_endpoint_va_model(self):
        self.assertEqual(tr.NVIDIA_DEFAULT_BASE_URL,
                         "https://integrate.api.nvidia.com/v1")
        self.assertEqual(tr.NVIDIA_DEFAULT_MODEL, "z-ai/glm-5.2")


if __name__ == "__main__":
    unittest.main(verbosity=1)
