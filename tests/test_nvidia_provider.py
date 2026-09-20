"""Kiểm tra provider NVIDIA NIM (build.nvidia.com) cho khâu dịch.

Toàn bộ test chạy offline: mock urllib để kiểm tra nhãn lỗi, backoff 429 và
việc đọc cấu hình - không gọi mạng thật.

Chạy: python -m unittest discover -s tests
"""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub import translate as tr
from autodub.server.config_api import (_TRANSLATION_GUI_KEYS,
                                       _translation_api_params,
                                       _save_content_pipeline_cfg)


def _http_429(url="https://x"):
    return urllib.error.HTTPError(
        url, 429, "Too Many Requests", {},
        io.BytesIO(b'{"status":429,"title":"Too Many Requests"}'))


class DocCauHinhNvidia(unittest.TestCase):
    def test_gui_keys_co_du_bo_nvidia(self):
        for k in ("nvidia_api_key", "nvidia_base_url",
                  "nvidia_model", "nvidia_fast_model", "nvidia_timeout"):
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
        self.assertEqual(model, "google/gemma-4-31b-it")
        self.assertIsNone(base)          # None -> _api_call tự dùng default
        self.assertEqual(timeout, 420)

    def test_luu_provider_kho_y_tuong_khong_dong_translation(self):
        import tempfile
        from autodub.server import config_api as capi
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "config.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("translation:\n  provider: browser\n"
                        "content_pipeline:\n  provider: browser\n")
            with mock.patch.object(capi, "CONFIG_PATH", path):
                try:
                    out = _save_content_pipeline_cfg({"provider": "nvidia"})
                finally:
                    capi._invalidate_cfg_cache()
            self.assertEqual(out["provider"], "nvidia")
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("content_pipeline:", text)
            self.assertIn('provider: "nvidia"', text)
            self.assertIn("translation:\n  provider: browser\n", text)


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
        self.assertIn("InferX", str(ctx.exception))
        self.assertNotIn("TokenRouter", str(ctx.exception))

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

    def test_ca_lo_con_han_thi_dich_lai_ca_lo_truoc(self):
        """NIM đôi khi echo nguyên chữ Hán cả lô; gọi lại cả lô 1 lần
        trước khi xé từng câu (tránh 4+ request khi đang kẹt cổng)."""
        from autodub.srt_utils import Segment
        segs = [Segment(1, 0.0, 2.0, "月落乌啼霜满天"),
                Segment(2, 2.0, 4.0, "江枫渔火对愁眠"),
                Segment(3, 4.0, 6.0, "姑苏城外寒山寺"),
                Segment(4, 6.0, 8.0, "夜半钟声到客船")]
        calls = []

        def fake_api(prompt, *a, **k):
            calls.append(prompt)
            if len(calls) == 1:
                return json.dumps(["月落乌啼霜满天", "江枫渔火对愁眠",
                                   "姑苏城外寒山寺", "夜半钟声到客船"],
                                  ensure_ascii=False)
            return json.dumps(["Trăng lặn quạ kêu sương đầy trời",
                               "Lửa thuyền đối diện người buồn ngủ",
                               "Ngoài thành Cô Tô chùa Hàn Sơn",
                               "Nửa đêm tiếng chuông vọng tới thuyền khách"],
                              ensure_ascii=False)

        with mock.patch.object(tr, "_api_call", side_effect=fake_api):
            tr.translate_segments(segs, api_key="nvapi-x", provider="nvidia",
                                  model="deepseek-ai/deepseek-v4-flash-0731",
                                  chunk_size=4, cache_path=None,
                                  shorten_long_lines_enabled=False)
        self.assertEqual(len(calls), 2, "1 lô hỏng + 1 lô lại, không xé 4 câu")
        self.assertEqual(len(segs), 4)
        self.assertTrue(all(s.text.strip() for s in segs))
        self.assertFalse(any(tr._contains_cjk(s.text) for s in segs))


class MacDinhNvidia(unittest.TestCase):
    def test_hang_so_endpoint_va_model(self):
        self.assertEqual(tr.NVIDIA_DEFAULT_BASE_URL,
                         "https://integrate.api.nvidia.com/v1")
        self.assertEqual(tr.NVIDIA_DEFAULT_MODEL, "google/gemma-4-31b-it")
        self.assertEqual(tr.NVIDIA_FLASH_MODEL, "deepseek-ai/deepseek-v4-flash-0731")
        self.assertEqual(tr.NVIDIA_PRO_MODEL, "deepseek-ai/deepseek-v4-pro-0813")
        self.assertEqual(tr.NVIDIA_FAST_MODEL,
                         "nvidia/nemotron-3-super-120b-a12b")

    def test_nvidia_tat_thinking_theo_ho_model(self):
        self.assertEqual(
            tr._nvidia_chat_extras("deepseek-ai/deepseek-v4-pro-0813"),
            {"chat_template_kwargs": {"thinking": False}})
        self.assertEqual(
            tr._nvidia_chat_extras("nvidia/nemotron-3-super-120b-a12b"),
            {"chat_template_kwargs": {"enable_thinking": False}})
        self.assertEqual(
            tr._nvidia_chat_extras("google/gemma-4-31b-it"),
            {"chat_template_kwargs": {"thinking": False}})
        self.assertEqual(
            tr._nvidia_chat_extras("moonshotai/kimi-k3"),
            {"reasoning_effort": "low"})

    def test_nvidia_bo_think_va_doc_reasoning_content(self):
        self.assertEqual(
            tr._strip_think("<think>abc</think>\n[\"ok\"]"), '["ok"]')
        self.assertEqual(
            tr._openai_message_text({
                "content": "",
                "reasoning_content": "<think>x</think>xin chao",
            }),
            "xin chao")

    def test_catalog_va_model_kho_y_tuong(self):
        from autodub.providers import (NVIDIA_CATALOG, NVIDIA_DEFAULT_MODEL,
                                       nvidia_model_for_ideas)
        from autodub.server.config_api import _nvidia_catalog_for_gui
        cat = _nvidia_catalog_for_gui()
        self.assertEqual([x["id"] for x in cat], [x["id"] for x in NVIDIA_CATALOG])
        self.assertEqual(cat[0]["id"], NVIDIA_DEFAULT_MODEL)
        cat[0]["id"] = "mutated"
        self.assertEqual(NVIDIA_CATALOG[0]["id"], NVIDIA_DEFAULT_MODEL)
        self.assertEqual(
            nvidia_model_for_ideas({"nvidia_fast_model": "fast"}, {}), "fast")
        self.assertEqual(
            nvidia_model_for_ideas({}, {"nvidia_model": "cp-fast"}), "cp-fast")

    def test_load_cfg_cache_theo_duong_dan_va_mtime(self):
        import tempfile
        from autodub.server import config_api as capi
        with tempfile.TemporaryDirectory() as td:
            path_a = os.path.join(td, "a.yaml")
            path_b = os.path.join(td, "b.yaml")
            with open(path_a, "w", encoding="utf-8") as f:
                f.write("translation:\n  provider: browser\n")
            with open(path_b, "w", encoding="utf-8") as f:
                f.write("translation:\n  provider: nvidia\n")
            try:
                with mock.patch.object(capi, "CONFIG_PATH", path_a):
                    first = capi._load_cfg()
                    second = capi._load_cfg()
                    self.assertIs(first, second)
                    self.assertEqual(first["translation"]["provider"], "browser")
                with mock.patch.object(capi, "CONFIG_PATH", path_b):
                    other = capi._load_cfg()
                    self.assertEqual(other["translation"]["provider"], "nvidia")
                    self.assertIsNot(first, other)
            finally:
                capi._invalidate_cfg_cache()

    def test_minimax_m3_gui_dung_tham_so_api_reference(self):
        with mock.patch.object(tr.urllib.request, "urlopen") as urlopen:
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            response.read.return_value = b'{"choices":[{"message":{"content":"ok"}}]}'
            urlopen.return_value = response
            out = tr._api_call(
                "prompt", "nvapi-test", "minimaxai/minimax-m3", .25,
                provider="nvidia")
        self.assertEqual(out, "ok")
        request = urlopen.call_args.args[0]
        import json
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "minimaxai/minimax-m3")
        self.assertEqual(payload["top_p"], .95)
        self.assertEqual(payload["max_tokens"], 8192)
        self.assertEqual(payload["chat_template_kwargs"], {"thinking": False})


class TreoDeepSeekV4Pro(unittest.TestCase):
    """urlopen timeout chỉ tính idle; DeepSeek V4 Pro từng giữ TCP 17 phút."""

    def test_deepseek_v4_pro_cap_tuong_90s(self):
        with mock.patch.object(tr, "_openai_compatible_call",
                               return_value="ok") as call:
            out = tr._api_call(
                "prompt", "nvapi-x", "deepseek-ai/deepseek-v4-pro-0813", 0.2,
                provider="nvidia", api_timeout=420)
        self.assertEqual(out, "ok")
        self.assertEqual(call.call_count, 1)
        kwargs = call.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 90)
        self.assertEqual(kwargs["idle_timeout"], 45)
        self.assertTrue(kwargs["enforce_wall"])
        self.assertEqual(kwargs["retries"], 5)

    def test_flash_cap_tuong_45s(self):
        with mock.patch.object(tr, "_openai_compatible_call",
                               return_value="ok") as call:
            tr._api_call(
                "prompt", "nvapi-x", "deepseek-ai/deepseek-v4-flash-0731", 0.2,
                provider="nvidia", api_timeout=420)
        kwargs = call.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 45)
        self.assertEqual(kwargs["idle_timeout"], 30)
        self.assertTrue(kwargs["enforce_wall"])

    def test_model_khac_cap_tuong_90s(self):
        with mock.patch.object(tr, "_openai_compatible_call",
                               return_value="ok") as call:
            tr._api_call(
                "prompt", "nvapi-x", "z-ai/glm-5.2", 0.2,
                provider="nvidia", api_timeout=420)
        kwargs = call.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 90)
        self.assertEqual(kwargs["idle_timeout"], 90)
        self.assertTrue(kwargs["enforce_wall"])

    def test_gemma4_cap_tuong_45s(self):
        with mock.patch.object(tr, "_openai_compatible_call",
                               return_value="ok") as call:
            tr._api_call(
                "prompt", "nvapi-x", "google/gemma-4-31b-it", 0.2,
                provider="nvidia", api_timeout=420)
        kwargs = call.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 45)
        self.assertEqual(kwargs["idle_timeout"], 30)
        self.assertTrue(kwargs["enforce_wall"])

    def test_super_cap_tuong_40s(self):
        with mock.patch.object(tr, "_openai_compatible_call",
                               return_value="ok") as call:
            tr._api_call(
                "prompt", "nvapi-x", "nvidia/nemotron-3-super-120b-a12b", 0.2,
                provider="nvidia", api_timeout=420)
        kwargs = call.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 40)
        self.assertEqual(kwargs["idle_timeout"], 25)

    def test_treo_pro_thi_chuyen_super(self):
        calls = []

        def fake(prompt, api_key, model, *a, **k):
            calls.append(model)
            if "deepseek-v4-pro" in str(model):
                raise RuntimeError(
                    "NVIDIA loi: treo qua 90s "
                    "(ket noi con mo, khong nhan xong phan hoi)")
            return '["Xin chao"]'

        with mock.patch.object(tr, "_openai_compatible_call", side_effect=fake):
            out = tr._api_call(
                "prompt", "nvapi-x", "deepseek-ai/deepseek-v4-pro-0813", 0.2,
                provider="nvidia")
        self.assertEqual(out, '["Xin chao"]')
        self.assertEqual(calls[0], "deepseek-ai/deepseek-v4-pro-0813")
        self.assertEqual(calls[1], "nvidia/nemotron-3-super-120b-a12b")

    def test_429_khong_chuyen_flash(self):
        models = []

        def fake_urlopen(req, timeout=None):
            import json
            models.append(json.loads(req.data.decode("utf-8"))["model"])
            raise _http_429()

        waits = []
        with mock.patch.object(tr.urllib.request, "urlopen", fake_urlopen), \
             mock.patch.object(tr.time, "sleep", waits.append):
            with self.assertRaises(RuntimeError) as ctx:
                tr._api_call(
                    "prompt", "nvapi-x", "deepseek-ai/deepseek-v4-pro-0813",
                    0.3, provider="nvidia")
        self.assertIn("NVIDIA", str(ctx.exception))
        self.assertEqual(models, ["deepseek-ai/deepseek-v4-pro-0813"] * 5)
        self.assertEqual(waits, [15.0, 30.0, 45.0, 60.0])

    def test_han_tuong_cat_ket_noi_treo(self):
        import threading
        import time

        class HangResp:
            def __init__(self):
                self._closed = threading.Event()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.close()
                return False

            def read(self):
                self._closed.wait(60)
                raise OSError("closed")

            def close(self):
                self._closed.set()

        start = time.monotonic()
        with mock.patch.object(tr.urllib.request, "urlopen",
                               return_value=HangResp()), \
             mock.patch.object(tr.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                tr._openai_compatible_call(
                    "prompt", "nvapi-x", "deepseek-ai/deepseek-v4-pro-0813",
                    0.2, retries=1, timeout=1, stream=False,
                    provider_label="NVIDIA", enforce_wall=True,
                    idle_timeout=1)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 5.0, "phai cat trong ~1s, khong cho 60s")
        self.assertIn("treo qua", str(ctx.exception))

    def test_treo_roi_goi_lai_thi_xong(self):
        import threading

        class HangResp:
            def __init__(self):
                self._closed = threading.Event()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.close()
                return False

            def read(self):
                self._closed.wait(60)
                raise OSError("closed")

            def close(self):
                self._closed.set()

        ok = mock.MagicMock()
        ok.__enter__.return_value = ok
        ok.__exit__.return_value = False
        ok.read.return_value = b'{"choices":[{"message":{"content":"ok"}}]}'
        with mock.patch.object(tr.urllib.request, "urlopen",
                               side_effect=[HangResp(), ok]) as urlopen, \
             mock.patch.object(tr.time, "sleep"):
            out = tr._openai_compatible_call(
                "prompt", "nvapi-x", "deepseek-ai/deepseek-v4-flash-0731",
                0.2, retries=2, timeout=1, stream=False,
                provider_label="NVIDIA", enforce_wall=True, idle_timeout=1)
        self.assertEqual(out, "ok")
        self.assertEqual(urlopen.call_count, 2)

    def test_deepseek_v4_pro_them_max_tokens(self):
        with mock.patch.object(tr.urllib.request, "urlopen") as urlopen:
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            response.read.return_value = (
                b'{"choices":[{"message":{"content":"ok"}}]}')
            urlopen.return_value = response
            out = tr._api_call(
                "prompt", "nvapi-test", "deepseek-ai/deepseek-v4-flash-0731",
                .25, provider="nvidia")
        self.assertEqual(out, "ok")
        import json
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["model"], "deepseek-ai/deepseek-v4-flash-0731")
        self.assertEqual(payload["max_tokens"], 4096)
        self.assertEqual(payload["chat_template_kwargs"], {"thinking": False})
        self.assertFalse(payload.get("stream"))


class ProviderZenMux(unittest.TestCase):
    def test_gui_keys_va_tham_so_zenmux(self):
        for key in ("zenmux_api_key", "zenmux_base_url",
                    "zenmux_model", "zenmux_timeout"):
            self.assertIn(key, _TRANSLATION_GUI_KEYS)
        self.assertEqual(
            _translation_api_params({
                "zenmux_api_key": "secret-test",
                "zenmux_base_url": "https://zenmux.ai/api/v1",
                "zenmux_model": "z-ai/glm-5.3-free",
                "zenmux_timeout": 240,
            }, "zenmux"),
            ("secret-test", "z-ai/glm-5.3-free",
             "https://zenmux.ai/api/v1", 240))

    def test_zenmux_goi_endpoint_openai_compatible(self):
        with mock.patch.object(tr, "_openai_compatible_call",
                               return_value='["ok"]') as call:
            result = tr._api_call(
                "prompt", "secret-test", "z-ai/glm-5.3-free", .2,
                provider="zenmux", api_base_url="https://zenmux.ai/api/v1")
        self.assertEqual(result, '["ok"]')
        self.assertEqual(call.call_args.args[4], "https://zenmux.ai/api/v1")
        self.assertEqual(call.call_args.kwargs["provider_label"], "ZenMux")


class ProviderZai(unittest.TestCase):
    def test_gui_keys_va_tham_so_zai(self):
        for key in ("zai_api_key", "zai_base_url", "zai_model", "zai_timeout"):
            self.assertIn(key, _TRANSLATION_GUI_KEYS)
        self.assertEqual(
            _translation_api_params({
                "zai_api_key": "secret-test",
                "zai_base_url": "https://api.z.ai/api/paas/v4",
                "zai_model": "glm-4.7-flash",
                "zai_timeout": 90,
            }, "zai"),
            ("secret-test", "glm-4.7-flash",
             "https://api.z.ai/api/paas/v4", 90))

    def test_zai_goi_endpoint_v4_va_tat_thinking(self):
        from autodub.translate.api import _openai_compatible_chat_url
        self.assertEqual(
            _openai_compatible_chat_url("https://api.z.ai/api/paas/v4"),
            "https://api.z.ai/api/paas/v4/chat/completions")
        with mock.patch.object(tr, "_openai_compatible_call",
                               return_value='["ok"]') as call:
            result = tr._api_call(
                "prompt", "secret-test", "glm-4.7-flash", .2,
                provider="zai", api_base_url="https://api.z.ai/api/paas/v4")
        self.assertEqual(result, '["ok"]')
        self.assertEqual(call.call_args.args[4], "https://api.z.ai/api/paas/v4")
        self.assertEqual(call.call_args.kwargs["provider_label"], "Z.AI")
        self.assertFalse(call.call_args.kwargs.get("stream", True))

    def test_zai_payload_tat_thinking(self):
        captured = {}

        class _Resp:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": '["Xin chào"]'}}]
                }).encode("utf-8")

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _Resp()

        with mock.patch.object(tr.urllib.request, "urlopen", side_effect=fake_urlopen):
            out = tr._api_call(
                "prompt", "secret-test", "glm-4.7-flash", .2,
                provider="zai", api_base_url="https://api.z.ai/api/paas/v4",
                api_retries=1)
        self.assertEqual(out, '["Xin chào"]')
        self.assertEqual(captured["url"],
                         "https://api.z.ai/api/paas/v4/chat/completions")
        self.assertEqual(captured["body"]["thinking"], {"type": "disabled"})
        self.assertFalse(captured["body"].get("stream"))


class ProviderTokenHarbor(unittest.TestCase):
    def test_gui_keys_va_tham_so_tokenharbor(self):
        for key in ("tokenharbor_api_key", "tokenharbor_base_url",
                    "tokenharbor_model", "tokenharbor_timeout"):
            self.assertIn(key, _TRANSLATION_GUI_KEYS)
        self.assertEqual(
            _translation_api_params({
                "tokenharbor_api_key": "thk_live_test",
                "tokenharbor_base_url": "https://tokenharbor.ai/v1",
                "tokenharbor_model": "deepseek-v4-flash:free",
                "tokenharbor_timeout": 90,
            }, "tokenharbor"),
            ("thk_live_test", "deepseek-v4-flash:free",
             "https://tokenharbor.ai/v1", 90))

    def test_tokenharbor_goi_openai_truoc(self):
        with mock.patch.object(tr, "_openai_compatible_call",
                               return_value='["ok"]') as call:
            result = tr._api_call(
                "prompt", "thk_live_test", "deepseek-v4-flash:free", .2,
                provider="tokenharbor",
                api_base_url="https://tokenharbor.ai/v1")
        self.assertEqual(result, '["ok"]')
        self.assertEqual(call.call_args.args[4], "https://tokenharbor.ai/v1")
        self.assertEqual(call.call_args.kwargs["provider_label"], "TokenHarbor")

    def test_tokenharbor_chuyen_anthropic_khi_openai_loi(self):
        with mock.patch.object(tr, "_openai_compatible_call",
                               side_effect=RuntimeError("HTTP 404")), \
             mock.patch.object(tr, "_anthropic_messages_call",
                               return_value="ok anthropic") as anth:
            result = tr._api_call(
                "prompt", "thk_live_test", "deepseek-v4-flash:free", .2,
                provider="tokenharbor")
        self.assertEqual(result, "ok anthropic")
        self.assertTrue(anth.called)


if __name__ == "__main__":
    unittest.main(verbosity=1)
