"""Regression tests with real Chromium DOM/blob handling; no Gemini requests."""
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
from playwright.sync_api import sync_playwright
from autodub import story_images


HTML = """<main>
<input aria-label="Ask Gemini"><button aria-label="Send message" onclick="makeImage()">Send</button>
<div id="icons"></div><section id="answers"></section></main>
<script>
window.sent = 0;
function icons(n) { document.querySelector('#icons').innerHTML = '<img width="16" height="16">'.repeat(n); }
icons(27);
async function makeImage() {
  ++sent;
  const color = sent === 1 ? 'red' : 'blue';
  icons(0);
  const c = document.createElement('canvas'); c.width=512; c.height=288;
  c.getContext('2d').fillStyle=color; c.getContext('2d').fillRect(0,0,512,288);
  const blob = await new Promise(resolve => c.toBlob(resolve));
  setTimeout(() => {
    const img = document.createElement('img'); img.src=URL.createObjectURL(blob);
    document.querySelector('#answers').append(img); icons(26);
  }, 150);
}
</script>"""


class RealBrowserImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="msedge", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        # All requests stay in this local fixture, including the Gemini-shaped URL.
        self.page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=HTML))
        self.page.goto("https://gemini.google.com/app/local-test")
        self.tmp = tempfile.TemporaryDirectory()
        self.session = story_images._GeminiWebImageSession(
            self.tmp.name, "msedge", story_images.GEMINI_WEB_URL, 45, mock.Mock())
        self.session.page = self.page
        self.session.context = self.context
        self.session._enable_image_mode = mock.Mock()

    def tearDown(self):
        self.context.close()
        self.tmp.cleanup()

    def test_old_nth_race_reproduced_new_reader_survives(self):
        images = self.page.locator("img")
        last_index = images.count() - 1
        self.page.evaluate("icons(0)")
        with self.assertRaisesRegex(Exception, "Timeout"):
            images.nth(last_index).evaluate("img => img.src", timeout=200)
        start = time.monotonic()
        self.assertIsNone(self.session._new_scene_image(set()))
        self.assertLess(time.monotonic() - start, 1)

    def test_two_scenes_blob_dom_churn_no_stop_or_download_button(self):
        for index, color in ((1, (255, 0, 0)), (2, (0, 0, 255))):
            path = self.session.generate_scene(index, "A scene", Path(self.tmp.name), "16:9")
            with Image.open(path) as img:
                self.assertEqual(img.size, (512, 288))
                self.assertEqual(img.convert("RGB").getpixel((0, 0)), color)
        self.assertEqual(self.page.evaluate("sent"), 2)

    def test_loaded_handle_survives_index_change_and_detachment(self):
        self.page.evaluate("makeImage()")
        self.page.wait_for_function("document.querySelector('#answers img')?.complete")
        handle = self.session._new_scene_image(set())
        self.page.evaluate("document.querySelector('#answers img').remove(); icons(0)")
        payload = self.session._cache_scene_image(handle)
        with Image.open(io.BytesIO(payload)) as img:
            self.assertEqual(img.convert("RGB").getpixel((0, 0)), (255, 0, 0))
        handle.dispose()

    def test_saved_pending_image_is_recovered_without_resending(self):
        path = self.session.generate_scene(1, "A scene", Path(self.tmp.name), "16:9")
        # A crash after file write but before manifest update must reuse that file.
        self.session._enable_image_mode.reset_mock()
        again = self.session.generate_scene(1, "A scene", Path(self.tmp.name), "16:9")
        self.assertEqual(path, again)
        self.assertEqual(self.page.evaluate("sent"), 1)
        self.session._enable_image_mode.assert_not_called()

    def test_resume_pending_chat_reads_existing_image_without_submit(self):
        path = self.session.generate_scene(1, "A scene", Path(self.tmp.name), "16:9")
        path.unlink()
        self.page.unroute("**/*")
        self.page.route("**/*", lambda route: route.fulfill(
            content_type="text/html", body=HTML + "<script>makeImage()</script>"))
        self.session._enable_image_mode.reset_mock()
        result = self.session.generate_scene(1, "A scene", Path(self.tmp.name), "16:9")
        self.assertTrue(result.is_file())
        self.session._enable_image_mode.assert_not_called()
        self.assertEqual(self.page.evaluate("sent"), 1)  # only fixture restore, no submit

    def test_real_element_screenshot_rescues_image(self):
        self.page.evaluate("makeImage()")
        self.page.wait_for_function("document.querySelector('#answers img')?.naturalWidth === 512")
        handle = self.session._new_scene_image(set())
        with mock.patch.object(self.session, "_cache_scene_image", return_value=None):
            result = self.session._download_scene(None, 1, Path(self.tmp.name), handle)
        with Image.open(result) as img:
            self.assertEqual(img.convert("RGB").getpixel((20, 20)), (255, 0, 0))
        handle.dispose()


if __name__ == "__main__":
    unittest.main()
