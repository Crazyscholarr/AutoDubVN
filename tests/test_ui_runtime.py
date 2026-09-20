"""Headless Edge tests against an isolated real AutoDubVN HTTP server."""
import json
import unittest
from unittest import mock

from playwright.sync_api import sync_playwright
import test_http_runtime as fixture
from autodub.server import http_api, projects


class UiRuntimeTests(unittest.TestCase):
    def test_xkiro_can_be_selected_saved_and_reloaded(self):
        import yaml
        config_path = self.http.root / "config.yaml"
        config_path.write_text(yaml.safe_dump(json.loads(config_path.read_text(encoding="utf-8"))), encoding="utf-8")
        self.page.evaluate("PR={options:{},segments:[],regions:[],sub_style:{}};setMode('dub');setTab('tr')")
        provider = self.page.locator('select[onchange*="setTrCfg(\'provider\'"]')
        provider.select_option("xkiro")
        self.page.wait_for_function("CFG.translation.provider === 'xkiro'")
        self.assertTrue(self.page.get_by_role("button", name="Thử kết nối Xkiro", exact=True).is_visible())
        model = self.page.locator('select[onchange*="xkiro_model"]')
        self.assertEqual(model.input_value(), "qwen/qwen3.5-flash:free")
        model.select_option("qwen/qwen3.5-plus:free")
        self.assertTrue(self.page.evaluate("clearTimeout(configTimer);saveTrCfg(false)"))
        self.page.reload()
        self.page.wait_for_function("CFG.translation && CFG.translation.provider === 'xkiro'")
        self.page.evaluate("PR={options:{},segments:[],regions:[],sub_style:{}};setTab('tr')")
        self.assertEqual(self.page.locator('select[onchange*="xkiro_model"]').input_value(), "qwen/qwen3.5-plus:free")
        self.assertEqual(self.errors, [])

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="msedge", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.http = fixture.HttpRuntimeTests()
        self.http.setUp()
        self.addCleanup(self.http.doCleanups)
        cfg = {"content_pipeline": {"database": str(self.http.root / "ideas.sqlite"),
                                    "output_dir": str(self.http.root / "exports")}}
        # JSON is valid YAML and avoids touching the real config or idea library.
        (self.http.root / "config.yaml").write_text(json.dumps(cfg), encoding="utf-8")
        self.context = self.browser.new_context(viewport={"width": 1500, "height": 940})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda exc: self.errors.append(str(exc)))
        self.page.goto("http://%s:%s/" % self.http.server.server_address)
        self.page.wait_for_function("typeof STORY !== 'undefined' && typeof setMode === 'function'")

    def test_all_modes_and_settings_render_without_js_errors(self):
        for button in ("#mStory", "#mIdeas", "#mVideoTools", "#mDub"):
            self.page.locator(button).click()
            self.page.wait_for_timeout(150)
        self.page.evaluate("openSettings()")
        for name in ("api", "voice", "gpu", "youtube"):
            self.page.locator(f'[data-settings-tab="{name}"]').click()
            self.assertTrue(self.page.locator("#settingsBody").inner_text().strip())
        self.page.evaluate("closeSettings()")
        self.assertEqual(self.errors, [])

    def test_http_error_is_not_reported_as_success(self):
        self.page.route("**/api/test-error", lambda route: route.fulfill(status=503, body="unavailable"))
        message = self.page.evaluate("api('/api/test-error').then(()=>null, e=>e.message)")
        self.assertIn("503", message)

    def test_overlapping_refreshes_share_one_request(self):
        self.page.evaluate("refresh()")
        requests = []
        self.page.on("request", lambda req: requests.append(req.url) if req.url.endswith("/api/state") else None)
        self.page.evaluate("Promise.all([refresh(),refresh(),refresh()])")
        self.assertEqual(len(requests), 1)

    def test_debounced_save_keeps_original_project_identity(self):
        saved = []
        self.page.route("**/api/project", lambda route: (
            saved.append(route.request.post_data_json),
            route.fulfill(content_type="application/json", body='{"ok":true}')
        ))
        self.page.evaluate("""() => {
            JID=1; PR={regions:[],logo:null,sub_style:{},options:{},segments:[{vi:'bản sửa'}]};
            save(); JID=2; PR={regions:[],logo:null,sub_style:{},options:{},segments:[]};
        }""")
        self.page.wait_for_timeout(550)
        self.assertEqual(saved[0]["id"], 1)
        self.assertEqual(saved[0]["segments"][0]["vi"], "bản sửa")


if __name__ == "__main__":
    unittest.main()
