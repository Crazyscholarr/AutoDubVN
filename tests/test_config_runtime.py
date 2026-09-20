import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from autodub import translate
from autodub.server import config_api


class ConfigRuntimeTests(unittest.TestCase):
    def test_xkiro_roundtrip_and_dispatch(self):
        from autodub.translate import api as api_mod
        from autodub.providers import api_params_for_provider
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("translation:\n  provider: browser\n", encoding="utf-8")
            with mock.patch.object(config_api, "CONFIG_PATH", str(path)):
                cfg = config_api._save_translation_cfg({"provider":"xkiro", "xkiro_api_key":"test-key",
                    "xkiro_model":"qwen/qwen3.5-plus:free", "xkiro_timeout":60})
                config_api._invalidate_cfg_cache()
                self.assertEqual(config_api._translation_cfg_for_gui()["provider"], "xkiro")
            key, model, base, timeout = api_params_for_provider(cfg, "xkiro")
            with mock.patch.object(api_mod, "_openai_compatible_call", return_value='["Xin chào"]') as call:
                api_mod._api_call("prompt",key,model,.2,"xkiro",base,timeout,api_retries=1)
            self.assertEqual(call.call_args.args[4], "https://api.xkiro.com/v1")
            self.assertEqual(call.call_args.args[2], "qwen/qwen3.5-plus:free")
            self.assertEqual(call.call_args.kwargs["retries"], 1)

    def test_zai_roundtrip_and_dispatch(self):
        from autodub.translate import api as api_mod
        from autodub.providers import api_params_for_provider
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("translation:\n  provider: browser\n", encoding="utf-8")
            with mock.patch.object(config_api, "CONFIG_PATH", str(path)):
                cfg = config_api._save_translation_cfg({
                    "provider": "zai", "zai_api_key": "test-key",
                    "zai_model": "glm-4.5-flash", "zai_timeout": 60})
                config_api._invalidate_cfg_cache()
                self.assertEqual(config_api._translation_cfg_for_gui()["provider"], "zai")
            key, model, base, timeout = api_params_for_provider(cfg, "zai")
            with mock.patch.object(api_mod, "_openai_compatible_call",
                                   return_value='["Xin chào"]') as call:
                api_mod._api_call("prompt", key, model, .2, "zai", base, timeout,
                                  api_retries=1)
            self.assertEqual(call.call_args.args[4], "https://api.z.ai/api/paas/v4")
            self.assertEqual(call.call_args.args[2], "glm-4.5-flash")
            self.assertEqual(call.call_args.kwargs["retries"], 1)
            self.assertEqual(call.call_args.kwargs["provider_label"], "Z.AI")

    def test_translation_check_rejects_unusable_response(self):
        for response in ('not json', '[null]', '[""]', '["你好"]', '[["xin chào"]]'):
            with self.subTest(response=response), mock.patch.object(
                    translate, "_api_call", return_value=response):
                result = config_api._test_translation_api(
                    {"provider": "nvidia", "nvidia_api_key": "test-key"})
                self.assertFalse(result["ok"])
                self.assertIn("error", result)

    def test_translation_check_accepts_vietnamese_and_limits_retries(self):
        with mock.patch.object(translate, "_api_call", return_value='["Xin chào"]') as call:
            result = config_api._test_translation_api(
                {"provider": "nvidia", "nvidia_api_key": "test-key", "nvidia_timeout": 420})
        self.assertTrue(result["ok"])
        self.assertEqual(call.call_args.kwargs["api_retries"], 1)
        self.assertLessEqual(call.call_args.args[6], 45)

    def test_parallel_config_saves_preserve_all_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("# Keep this comment\ntranslation:\n  provider: browser\n", encoding="utf-8")
            keys = tuple(f"test_{i}" for i in range(20))
            with mock.patch.object(config_api, "CONFIG_PATH", str(path)):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    futures = [pool.submit(config_api._patch_yaml_section,
                        "translation", {key: index}, keys) for index, key in enumerate(keys)]
                    for future in futures:
                        future.result(timeout=3)
                data = config_api._load_cfg()["translation"]
            self.assertTrue(path.read_text(encoding="utf-8").startswith("# Keep this comment"))
            self.assertEqual({key: data[key] for key in keys}, dict(zip(keys, range(20))))


if __name__ == "__main__":
    unittest.main()
