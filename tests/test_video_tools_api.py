import copy
import os
import tempfile
import unittest
from unittest import mock

from autodub.server import video_tools_api
from autodub.server.state import STATE, _CANCEL_EVENT, _LOCK


class InlineThread:
    def __init__(self, target, args=(), kwargs=None, daemon=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


class CongCuVideoDocLap(unittest.TestCase):
    def setUp(self):
        with _LOCK:
            self.old_running = STATE["running"]
            self.old_busy = STATE["busy"]
            self.old_cancel = STATE.get("cancel", False)
            self.old_tools = copy.deepcopy(STATE["video_tools"])
            self.old_manual = copy.deepcopy(STATE["manual"])
            STATE["running"] = False
            STATE["busy"] = ""
            STATE["cancel"] = False
            STATE["video_tools"].update({"working": False, "active": "", "error": ""})
        _CANCEL_EVENT.clear()

    def tearDown(self):
        with _LOCK:
            STATE["running"] = self.old_running
            STATE["busy"] = self.old_busy
            STATE["cancel"] = self.old_cancel
            STATE["video_tools"].clear()
            STATE["video_tools"].update(self.old_tools)
            STATE["manual"].clear()
            STATE["manual"].update(self.old_manual)
        _CANCEL_EVENT.clear()

    def test_tim_video_khong_ghi_sang_trang_thai_story(self):
        rows = [{"title": "Phong cảnh", "url": "https://bilibili.com/video/BV1"}]
        with mock.patch("autodub.story_sources.search", return_value=rows):
            obj, code = video_tools_api.api_search_videos({
                "keyword": "农村 风景", "provider": "bilibili", "limit": 5})
        self.assertEqual(code, 200)
        self.assertEqual(obj["results"], rows)
        self.assertEqual(STATE["video_tools"]["search_results"], rows)
        self.assertEqual(STATE["manual"], self.old_manual)

    def test_tai_nhieu_link_vao_kho_rieng(self):
        with tempfile.TemporaryDirectory(prefix="autodubvn-vt-download-") as tmp:
            def fake_download(urls, target, **kwargs):
                made = []
                for index, url in enumerate(urls, 1):
                    path = os.path.join(target, f"video_{index}.mp4")
                    with open(path, "wb") as stream:
                        stream.write(b"video")
                    made.append({"url": url, "path": path})
                kwargs["progress"](len(made), len(made), "đã xong")
                return made

            with mock.patch("autodub.story_sources.download_many",
                            side_effect=fake_download), \
                 mock.patch.object(video_tools_api, "_load_cfg",
                                   return_value={"download": {}}), \
                 mock.patch.object(video_tools_api.threading, "Thread", InlineThread):
                obj, code = video_tools_api.api_download_videos({
                    "links": ["https://example.com/a", "https://example.com/b"],
                    "output_dir": tmp, "quality": "480"})

            self.assertEqual(code, 200)
            self.assertEqual(obj["total"], 2)
            self.assertEqual(len(STATE["video_tools"]["download_files"]), 2)
            self.assertFalse(STATE["video_tools"]["working"])
            self.assertEqual(STATE["manual"], self.old_manual)

    def test_cat_dong_thoi_danh_sach_nhieu_video(self):
        with tempfile.TemporaryDirectory(prefix="autodubvn-vt-cut-") as tmp:
            sources = []
            for name in ("a.mp4", "b.mp4"):
                path = os.path.join(tmp, name)
                with open(path, "wb") as stream:
                    stream.write(b"source")
                sources.append(path)
            output = os.path.join(tmp, "clips")
            captured = {}

            def fake_cut(paths, target, **kwargs):
                captured["paths"] = list(paths)
                captured["min"] = kwargs["min_seconds"]
                captured["max"] = kwargs["max_seconds"]
                os.makedirs(target, exist_ok=True)
                clips = []
                for index, source in enumerate(paths, 1):
                    clip = os.path.join(target, f"clip_{index}.mp4")
                    with open(clip, "wb") as stream:
                        stream.write(b"clip")
                    clips.append(clip)
                    kwargs["progress"](index, len(paths), os.path.basename(source))
                return clips

            with mock.patch("autodub.story_sources.cut_video_segments",
                            side_effect=fake_cut), \
                 mock.patch.object(video_tools_api.threading, "Thread", InlineThread):
                obj, code = video_tools_api.api_cut_videos({
                    "paths": sources, "output_dir": output,
                    "min_seconds": 300, "max_seconds": 600})

            self.assertEqual(code, 200)
            self.assertEqual(obj["total"], 2)
            self.assertEqual(captured["paths"], sources)
            self.assertEqual((captured["min"], captured["max"]), (300, 600))
            self.assertEqual(len(STATE["video_tools"]["cut_files"]), 2)
            self.assertEqual(STATE["manual"], self.old_manual)


class GiaoDienCongCuVideo(unittest.TestCase):
    def test_co_che_do_rieng_va_hai_tab_phu_tro(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "ui", "index.html"), encoding="utf-8") as stream:
            html = stream.read()
        with open(os.path.join(root, "ui", "app.js"), encoding="utf-8") as stream:
            script = stream.read()
        with open(os.path.join(root, "ui", "style.css"), encoding="utf-8") as stream:
            style = stream.read()
        self.assertIn('id="videoToolsMain"', html)
        self.assertIn("Tải video nền cho Audio", html)
        self.assertIn("Cắt video hàng loạt", html)
        self.assertIn('pywebview.api.pick_video()', script)
        self.assertIn('"/api/tools/download_videos"', script)
        self.assertIn('"/api/tools/cut_videos"', script)
        self.assertNotIn("function storyDownloadSources", script)
        self.assertNotIn("function storyCutSources", script)

        # Ba thao tác trong panel chỉ rộng ~330 px: hai thẻ trên + một nút
        # toàn hàng dưới, tuyệt đối không quay lại flex một dòng bị tràn ngang.
        self.assertIn('class="story-video-tool-grid"', script)
        self.assertIn('class="story-video-pick"', script)
        self.assertIn(".story-video-tool-grid{display:grid", style)
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", style)
        self.assertIn(".story-video-pick{grid-column:1/-1", style)


if __name__ == "__main__":
    unittest.main()
