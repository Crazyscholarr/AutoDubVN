import os
import tempfile
import unittest
from unittest import mock

from autodub import bilibili_direct, downloader


class BilibiliDirectUnitTests(unittest.TestCase):
    def test_nhan_dung_bvid_va_khong_nhan_link_test_gia(self):
        url = "https://www.bilibili.com/video/BV1nRsjeqEE8?p=1"
        self.assertEqual(bilibili_direct.extract_bvid(url), "BV1nRsjeqEE8")
        self.assertTrue(bilibili_direct.is_bilibili_url(url))
        self.assertFalse(bilibili_direct.is_bilibili_url(
            "https://www.bilibili.com/video/BVTEST"))

    def test_giu_akamai_va_mo_rong_bilivideo_sang_mirror(self):
        akamai = "https://upos-hz-mirrorakam.akamaized.net/a/video.mp4?x=1"
        self.assertEqual(bilibili_direct._collect_urls(akamai), (akamai,))
        bili = "https://upos-sz-mirrorcos.bilivideo.com/a/video.m4s?x=1"
        expanded = bilibili_direct._candidate_urls([bili])
        self.assertIn(bili, expanded)
        self.assertTrue(any("mirrorali.bilivideo.com" in url for url in expanded))

    def test_doc_duoc_sessdata_dong_httponly_cua_cookies_txt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cookies.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    "# Netscape HTTP Cookie File\n"
                    "#HttpOnly_.bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tabc123\n")
            values = bilibili_direct._read_cookie_file(path)
        self.assertEqual(values.get("SESSDATA"), "abc123")

    def test_chon_mp4_lien_va_dash_avc_dung_chat_luong(self):
        mp4 = bilibili_direct._pick_stream({
            "quality": 64,
            "durl": [{"url": "https://a.bilivideo.com/v.mp4", "size": 123}],
        }, 64)
        self.assertEqual(mp4.kind, "mp4")
        self.assertEqual(mp4.declared_size, 123)

        dash = bilibili_direct._pick_stream({
            "quality": 80,
            "dash": {
                "video": [
                    {"id": 80, "codecs": "hev1", "baseUrl":
                     "https://a.bilivideo.com/h.m4s"},
                    {"id": 80, "codecs": "avc1.640028", "baseUrl":
                     "https://a.bilivideo.com/a.m4s"},
                    {"id": 120, "codecs": "av01", "baseUrl":
                     "https://a.bilivideo.com/4k.m4s"},
                ],
                "audio": [
                    {"id": 30216, "bandwidth": 64000, "baseUrl":
                     "https://a.bilivideo.com/lo.m4s"},
                    {"id": 30280, "bandwidth": 192000, "baseUrl":
                     "https://a.bilivideo.com/hi.m4s"},
                ],
            },
        }, 80)
        self.assertEqual(dash.kind, "dash")
        self.assertIn("/a.m4s", dash.video_urls[0])
        self.assertIn("/hi.m4s", dash.audio_urls[0])

    def test_tai_range_song_song_va_noi_lai_tu_khoi_hoan_chinh(self):
        old_chunk = bilibili_direct._CHUNK
        bilibili_direct._CHUNK = 4
        try:
            with tempfile.TemporaryDirectory() as tmp:
                part = os.path.join(tmp, "video.mp4.part")
                with open(part, "wb") as handle:
                    handle.write(b"abcdXX")

                def fake_fetch(_urls, _headers, start, end):
                    return bytes(range(start, end + 1))

                with mock.patch.object(
                        bilibili_direct, "_fetch_range_retry",
                        side_effect=fake_fetch):
                    bilibili_direct._download_ranges(
                        ["https://a.bilivideo.com/v"], {}, part, 10,
                        "Video", None, 0.0, 100.0)
                with open(part, "rb") as handle:
                    data = handle.read()
            self.assertEqual(data, b"abcd" + bytes(range(4, 10)))
        finally:
            bilibili_direct._CHUNK = old_chunk


class BilibiliDirectIntegrationTests(unittest.TestCase):
    def test_download_video_uu_tien_bo_tai_truc_tiep(self):
        updates = []
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "direct.mp4")

            def fake_direct(*_args, progress_callback=None, **_kwargs):
                with open(path, "wb") as handle:
                    handle.write(b"video")
                if progress_callback:
                    progress_callback({"status": "downloading", "percent": 50.0,
                                       "text": "Bilibili: 50%"})
                return path, 64, "mp4"

            with mock.patch.object(bilibili_direct, "download_bilibili",
                                   side_effect=fake_direct), \
                    mock.patch.object(downloader, "_ytdlp_cmd") as ytdlp, \
                    mock.patch.object(downloader, "ffprobe_video_size",
                                      return_value=(1280, 720)), \
                    mock.patch.object(downloader, "ffprobe_has_stream", return_value=True), \
                    mock.patch.object(downloader, "ffprobe_is_blank_video",
                                      return_value=False):
                result = downloader.download_video(
                    "https://www.bilibili.com/video/BV1nRsjeqEE8", tmp,
                    progress_callback=updates.append)

            self.assertEqual(result, os.path.abspath(path))
            self.assertFalse(ytdlp.called)
            self.assertEqual(updates[-1]["status"], "complete")

    def test_api_truc_tiep_loi_thi_tu_lui_ve_ytdlp(self):
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            out_tmpl = cmd[cmd.index("-o") + 1]
            path = out_tmpl.replace("%(title).80s", "fallback").replace(
                "%(id)s", "BV1nRsjeqEE8").replace("%(ext)s", "mp4")
            with open(path, "wb") as handle:
                handle.write(b"video")
            return mock.Mock(stdout=path + "\n")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(bilibili_direct, "download_bilibili",
                                  side_effect=RuntimeError("API tạm chặn")), \
                mock.patch.object(downloader, "_ytdlp_cmd", return_value=["yt-dlp"]), \
                mock.patch.object(downloader, "run", side_effect=fake_run), \
                mock.patch.object(downloader, "ffprobe_video_size",
                                  return_value=(854, 480)), \
                mock.patch.object(downloader, "ffprobe_has_stream", return_value=True), \
                mock.patch.object(downloader, "ffprobe_is_blank_video",
                                  return_value=False):
            result = downloader.download_video(
                "https://www.bilibili.com/video/BV1nRsjeqEE8", tmp,
                external_downloader="none")

        self.assertTrue(result.endswith(".mp4"))
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
