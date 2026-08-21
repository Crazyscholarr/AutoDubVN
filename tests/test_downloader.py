import os
import tempfile
import unittest
from unittest import mock

from autodub import downloader


class DownloadFallback(unittest.TestCase):
    def test_chuan_hoa_shortcut_edge_thanh_browser_profile_yt_dlp(self):
        shortcut = ('"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\'
                    'msedge.exe" --profile-directory=Default')
        self.assertEqual(
            downloader._normalise_cookie_browser(shortcut), "edge:Default")
        self.assertEqual(downloader._cookie_browser_label(shortcut), "Edge")

    def test_loi_aria2_tu_dong_thu_lai_bang_ytdlp_noi_bo(self):
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            if len(calls) == 1:
                raise RuntimeError("ERROR: aria2c exited with code 29")
            out_tmpl = cmd[cmd.index("-o") + 1]
            path = out_tmpl.replace("%(title).80s", "video").replace(
                "%(id)s", "BVTEST").replace("%(ext)s", "mp4")
            with open(path, "wb") as handle:
                handle.write(b"video")
            return mock.Mock(stdout=path + "\n")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(downloader, "_ytdlp_cmd",
                                  return_value=["python", "-m", "yt_dlp"]), \
                mock.patch.object(downloader, "which", return_value="aria2c.exe"), \
                mock.patch.object(downloader, "run", side_effect=fake_run), \
                mock.patch.object(downloader, "ffprobe_video_size", return_value=(1280, 720)), \
                mock.patch.object(downloader, "ffprobe_has_stream", return_value=True), \
                mock.patch.object(downloader, "ffprobe_is_blank_video", return_value=False):
            result = downloader.download_video(
                "https://www.bilibili.com/video/BVTEST", tmp,
                external_downloader="aria2c")

        self.assertTrue(result.endswith(".mp4"))
        self.assertEqual(len(calls), 2)
        self.assertIn("--downloader", calls[0])
        self.assertNotIn("--downloader", calls[1])

    def test_loi_khong_phai_aria2_khong_bi_goi_lai(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(downloader, "_ytdlp_cmd",
                                  return_value=["python", "-m", "yt_dlp"]), \
                mock.patch.object(downloader, "which", return_value="aria2c.exe"), \
                mock.patch.object(downloader, "run",
                                  side_effect=RuntimeError("Requested format is not available")) as run:
            with self.assertRaisesRegex(RuntimeError, "Tải thất bại"):
                downloader.download_video(
                    "https://www.bilibili.com/video/BVTEST", tmp,
                    external_downloader="auto")
        self.assertEqual(run.call_count, 1)

    def test_auto_uu_tien_downloader_noi_bo_de_tranh_nhan_doi_ket_noi(self):
        with mock.patch.object(downloader, "which", return_value="aria2c.exe"):
            opts, note = downloader._download_speed_options(8, "auto")
        self.assertNotIn("--downloader", opts)
        self.assertIn("nội bộ", note)

    def test_chrome_khoa_cookie_thi_thu_lai_khong_cookie(self):
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            if len(calls) == 1:
                raise RuntimeError("ERROR: Could not copy Chrome cookie database")
            out_tmpl = cmd[cmd.index("-o") + 1]
            path = out_tmpl.replace("%(title).80s", "public").replace(
                "%(id)s", "ID").replace("%(ext)s", "mp4")
            with open(path, "wb") as handle:
                handle.write(b"video")
            return mock.Mock(stdout=path + "\n")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(downloader, "_ytdlp_cmd", return_value=["yt-dlp"]), \
                mock.patch.object(downloader, "which", return_value=None), \
                mock.patch.object(downloader, "run", side_effect=fake_run), \
                mock.patch.object(downloader, "ffprobe_video_size", return_value=(640, 360)), \
                mock.patch.object(downloader, "ffprobe_has_stream", return_value=True), \
                mock.patch.object(downloader, "ffprobe_is_blank_video", return_value=False):
            downloader.download_video(
                "https://example.com/public", tmp, cookies_from_browser="chrome",
                external_downloader="none")
        self.assertIn("--cookies-from-browser", calls[0])
        self.assertNotIn("--cookies-from-browser", calls[1])

    def test_416_tai_lai_sach_va_ha_480p(self):
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            if len(calls) == 1:
                raise RuntimeError("ERROR: Could not copy Edge cookie database")
            if len(calls) in (2, 3):
                raise RuntimeError(
                    "ERROR: HTTP Error 416: Requested Range Not Satisfiable")
            out_tmpl = cmd[cmd.index("-o") + 1]
            path = out_tmpl.replace("%(title).80s", "public").replace(
                "%(id)s", "ID").replace("%(ext)s", "mp4")
            with open(path, "wb") as handle:
                handle.write(b"video")
            return mock.Mock(stdout=path + "\n")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(downloader, "_ytdlp_cmd", return_value=["yt-dlp"]), \
                mock.patch.object(downloader, "run", side_effect=fake_run), \
                mock.patch.object(downloader, "ffprobe_video_size", return_value=(640, 360)), \
                mock.patch.object(downloader, "ffprobe_has_stream", return_value=True), \
                mock.patch.object(downloader, "ffprobe_is_blank_video", return_value=False):
            result = downloader.download_video(
                "https://www.bilibili.com/video/BVTEST", tmp,
                cookies_from_browser="edge", external_downloader="none")

        self.assertTrue(result.endswith(".mp4"))
        self.assertEqual(len(calls), 4)
        self.assertNotIn("--cookies-from-browser", calls[2])
        self.assertIn("--no-part", calls[2])
        self.assertIn("--force-overwrites", calls[2])
        self.assertEqual(calls[2][calls[2].index("--concurrent-fragments") + 1], "1")
        self.assertIn("height<=480", calls[3][calls[3].index("-f") + 1])

    def test_503_tai_lai_sach_giam_fragment(self):
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            if len(calls) == 1:
                raise RuntimeError("ERROR: HTTP Error 503: Service Unavailable")
            out_tmpl = cmd[cmd.index("-o") + 1]
            path = out_tmpl.replace("%(title).80s", "video").replace(
                "%(id)s", "ID").replace("%(ext)s", "mp4")
            with open(path, "wb") as handle:
                handle.write(b"video")
            return mock.Mock(stdout=path + "\n")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(downloader, "_ytdlp_cmd", return_value=["yt-dlp"]), \
                mock.patch.object(downloader, "run", side_effect=fake_run), \
                mock.patch.object(downloader, "ffprobe_video_size", return_value=(640, 360)), \
                mock.patch.object(downloader, "ffprobe_has_stream", return_value=True), \
                mock.patch.object(downloader, "ffprobe_is_blank_video", return_value=False):
            downloader.download_video(
                "https://www.bilibili.com/video/BVTEST", tmp,
                external_downloader="none")

        self.assertEqual(len(calls), 2)
        self.assertIn("--force-overwrites", calls[1])
        self.assertEqual(calls[1][calls[1].index("--concurrent-fragments") + 1], "1")

    def test_remote_disconnect_noi_tiep_part_bang_chunk_nho(self):
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            if len(calls) == 1:
                raise RuntimeError(
                    "ERROR: Got error: ('Connection aborted.', "
                    "RemoteDisconnected('Remote end closed connection without response'))")
            out_tmpl = cmd[cmd.index("-o") + 1]
            path = out_tmpl.replace("%(title).80s", "video").replace(
                "%(id)s", "ID").replace("%(ext)s", "mp4")
            with open(path, "wb") as handle:
                handle.write(b"video")
            return mock.Mock(stdout=path + "\n")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(downloader, "_ytdlp_cmd", return_value=["yt-dlp"]), \
                mock.patch.object(downloader, "run", side_effect=fake_run), \
                mock.patch.object(downloader, "ffprobe_video_size", return_value=(640, 360)), \
                mock.patch.object(downloader, "ffprobe_has_stream", return_value=True), \
                mock.patch.object(downloader, "ffprobe_is_blank_video", return_value=False):
            result = downloader.download_video(
                "https://www.bilibili.com/video/BVTEST", tmp,
                external_downloader="none")

        self.assertTrue(result.endswith(".mp4"))
        self.assertEqual(len(calls), 2)
        self.assertIn("--continue", calls[1])
        self.assertIn("--part", calls[1])
        self.assertNotIn("--no-part", calls[1])
        self.assertNotIn("--force-overwrites", calls[1])
        self.assertEqual(
            calls[1][calls[1].index("--concurrent-fragments") + 1], "1")
        self.assertEqual(
            calls[1][calls[1].index("--http-chunk-size") + 1], "10M")

    def test_incomplete_read_va_bytes_thieu_la_loi_ket_noi_tam_thoi(self):
        self.assertTrue(downloader._connection_download_failed(
            "ERROR: 4880926 bytes read, 268350966 more expected"))
        self.assertTrue(downloader._transient_download_failed(
            "ERROR: IncompleteRead(230 bytes read)"))

    def test_progress_duoc_stream_vao_callback_va_path_marker(self):
        calls, updates = [], []

        def fake_run(cmd, line_callback=None, **_kwargs):
            calls.append(list(cmd))
            if line_callback:
                line_callback(
                    "__AUTODUB_PROGRESS__|BVTEST|100023|downloading|"
                    " 42.5%|335544320|790122426|NA|8388608|55")
            out_tmpl = cmd[cmd.index("-o") + 1]
            path = out_tmpl.replace("%(title).80s", "video").replace(
                "%(id)s", "BVTEST").replace("%(ext)s", "mp4")
            with open(path, "wb") as handle:
                handle.write(b"video")
            return mock.Mock(stdout="__AUTODUB_FILE__|" + path + "\n")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(downloader, "_ytdlp_cmd", return_value=["yt-dlp"]), \
                mock.patch.object(downloader, "run", side_effect=fake_run), \
                mock.patch.object(downloader, "ffprobe_video_size", return_value=(852, 480)), \
                mock.patch.object(downloader, "ffprobe_has_stream", return_value=True), \
                mock.patch.object(downloader, "ffprobe_is_blank_video", return_value=False):
            result = downloader.download_video(
                "https://www.bilibili.com/video/BVTEST", tmp,
                external_downloader="none", progress_callback=updates.append)
            existed = os.path.isfile(result)

        self.assertTrue(existed)
        self.assertIn("--newline", calls[0])
        self.assertIn("--progress-template", calls[0])
        self.assertEqual(updates[0]["percent"], 42.5)
        self.assertIn("8.0 MiB/s", updates[0]["text"])
        self.assertEqual(updates[-1]["status"], "complete")


if __name__ == "__main__":
    unittest.main()
