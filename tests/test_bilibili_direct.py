import os
import hashlib
import tempfile
import time
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

    def test_giu_url_goc_va_do_them_mirror_upos(self):
        akamai = "https://upos-hz-mirrorakam.akamaized.net/a/video.mp4?x=1"
        self.assertEqual(bilibili_direct._collect_urls(akamai), (akamai,))
        bili = "https://upos-sz-mirrorcos.bilivideo.com/a/video.m4s?x=1"
        expanded = bilibili_direct._candidate_urls([bili])
        self.assertEqual(expanded[0], bili)
        self.assertGreater(len(expanded), 1)
        self.assertTrue(any("mirrorali.bilivideo.com" in url for url in expanded))
        akamai_expanded = bilibili_direct._candidate_urls([akamai])
        self.assertEqual(akamai_expanded[0], akamai)
        self.assertTrue(any("mirrorcosov.bilivideo.com" in url for url in akamai_expanded))
        fake = ["https://cdn0.bilivideo.com/a", "https://cdn1.bilivideo.com/a"]
        self.assertEqual(bilibili_direct._candidate_urls(fake), fake)

    def test_race_probe_chon_cdn_nhanh_nhat(self):
        slow = bilibili_direct.Probe(
            "https://a.bilivideo.com/s", 10, True, "video/mp4", 100.0)
        fast = bilibili_direct.Probe(
            "https://a.bilivideo.com/f", 10, True, "video/mp4", 900.0)

        def fake_probe(url, _headers):
            return fast if url.endswith("/f") else slow

        with mock.patch.object(bilibili_direct, "_probe", side_effect=fake_probe):
            winner = bilibili_direct._race_probe(
                ["https://a.bilivideo.com/s", "https://a.bilivideo.com/f"], {})
        self.assertEqual(winner.url, fast.url)

    def test_probe_2mib_het_han_thi_thu_mau_nho(self):
        events = []

        def fake_probe(url, headers, sample=None):
            if sample is None:
                raise TimeoutError("timed out")
            self.assertEqual(sample, bilibili_direct._PROBE_LITE_SAMPLE)
            return bilibili_direct.Probe(url, 10, True, "video/mp4", 50.0)

        with mock.patch.object(bilibili_direct, "_probe", side_effect=fake_probe):
            ranked = bilibili_direct._rank_probes(
                ["https://a.bilivideo.com/v"], {}, events.append)
        self.assertEqual(ranked[0].url, "https://a.bilivideo.com/v")
        self.assertTrue(any(item.get("event") == "probe_lite" for item in events))

    def test_cdn_cung_toc_do_uu_tien_noi_dia(self):
        inland = bilibili_direct.Probe(
            "https://upos-sz-mirrorcos.bilivideo.com/a", 10, True, "video/mp4", 100.0)
        akamai = bilibili_direct.Probe(
            "https://upos-hz-mirrorakam.akamaized.net/a", 10, True, "video/mp4", 100.0)
        ranked = sorted([inland, akamai], key=bilibili_direct._cdn_sort_key)
        self.assertEqual(ranked[0].url, inland.url)
        self.assertGreater(
            bilibili_direct._probe_read_deadline(bilibili_direct._PROBE_SAMPLE), 40)
        self.assertEqual(bilibili_direct._PROBE_CAP, 2)
        self.assertEqual(bilibili_direct._range_windows(829 * 1024)[0], 4)
        self.assertEqual(bilibili_direct._range_windows(5 * 1024 * 1024)[0], 12)
        self.assertFalse(bilibili_direct._should_stop_playurl({
            "quality": 64,
            "durl": [{"url": "https://upos-hz-mirrorakam.akamaized.net/v.mp4"}],
        }, 64))
        self.assertTrue(bilibili_direct._should_stop_playurl({
            "quality": 64,
            "durl": [{"url": "https://upos-sz-mirrorcos.bilivideo.com/v.mp4"}],
        }, 64))

    def test_mp4_akamai_thua_dash_nhieu_cdn(self):
        mp4 = {
            "quality": 64,
            "durl": [{"url": "https://upos-hz-mirrorakam.akamaized.net/v.mp4"}],
        }
        dash = {
            "quality": 64,
            "dash": {
                "video": [{"id": 64, "codecs": "avc1",
                           "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v.m4s",
                           "backupUrl": ["https://upos-sz-mirrorali.bilivideo.com/v.m4s"]}],
                "audio": [{"id": 30280, "bandwidth": 1,
                           "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/a.m4s"}],
            },
        }
        best = bilibili_direct._pick_best_play([mp4, dash], 64)
        self.assertIn("dash", best)

    def test_open_url_dung_proxy_http(self):
        opened = []

        class FakeOpener:
            def open(self, request, timeout=None):
                opened.append(timeout)
                return mock.Mock()

        previous = bilibili_direct.set_download_proxy("http://127.0.0.1:7890")
        try:
            with mock.patch.object(
                    bilibili_direct, "build_opener", return_value=FakeOpener()) as builder:
                bilibili_direct._open_url(
                    bilibili_direct.Request("https://a.bilivideo.com/v"), 5)
            self.assertTrue(builder.called)
            self.assertEqual(opened, [5])
        finally:
            bilibili_direct.set_download_proxy(previous)

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
                bilibili_direct._save_integrity(part + '.json', {
                    'total': 10,
                    'identity': hashlib.sha256(str(["https://a.bilivideo.com/v"]).encode()).hexdigest(),
                    'chunks': [{'start': 0, 'length': 4, 'sha256': hashlib.sha256(b'abcd').hexdigest()}]})

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


class BilibiliDirectResilienceTests(unittest.TestCase):
    def setUp(self):
        bilibili_direct.reset_http_session()

    def tearDown(self):
        bilibili_direct.reset_http_session()

    def test_extract_js_assign_bo_qua_ngoac_trong_chuoi(self):
        source = r'window.__playinfo__={"msg":"a}b","data":{"quality":80}};window.x'
        payload = bilibili_direct._extract_js_assign(source, "__playinfo__")
        self.assertEqual(payload["data"]["quality"], 80)

    def test_view_info_412_thi_doc_initial_state(self):
        html = (
            'window.__INITIAL_STATE__={"videoData":{"title":"Phim test",'
            '"cid":123456,"pages":[{"cid":123456,"page":1,"part":"P1"}]}};(function(){})'
        )
        with mock.patch.object(
                bilibili_direct, "_json_get",
                side_effect=RuntimeError("HTTP Error 412: Precondition Failed")), \
                mock.patch.object(
                    bilibili_direct, "_load_watch_page", return_value=html):
            title, cid, page, pages, part = bilibili_direct._view_info(
                "BV1nRsjeqEE8",
                "https://www.bilibili.com/video/BV1nRsjeqEE8", None)
        self.assertEqual(cid, "123456")
        self.assertEqual(title, "Phim test")
        self.assertEqual(part, "P1")
        self.assertEqual(page, 1)
        self.assertEqual(pages, 1)

    def test_playurl_api_chet_thi_lay_playinfo(self):
        html = (
            'window.__playinfo__={"code":0,"data":{"quality":64,"durl":['
            '{"url":"https://upos-sz-mirrorcos.bilivideo.com/v.mp4","size":9}'
            ']}};window.x=1'
        )
        with mock.patch.object(
                bilibili_direct, "_json_get",
                side_effect=RuntimeError("HTTP Error 412: Precondition Failed")), \
                mock.patch.object(
                    bilibili_direct, "_get_wbi_mixin",
                    side_effect=RuntimeError("no wbi")), \
                mock.patch.object(
                    bilibili_direct, "_load_watch_page", return_value=html):
            stream = bilibili_direct._fetch_playurl("BV1nRsjeqEE8", "1", 64, None)
        self.assertEqual(stream.kind, "mp4")
        self.assertIn("mirrorcos.bilivideo.com", stream.video_urls[0])

    def test_json_get_412_thu_lai_sau_khi_warm(self):
        calls = []

        def fake_request(url, _headers, _timeout):
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError("HTTP Error 412: Precondition Failed")
            return '{"code":0,"data":{"title":"ok"}}'

        with mock.patch.object(bilibili_direct, "_warm_session"), \
                mock.patch.object(bilibili_direct, "_request_text",
                                  side_effect=fake_request), \
                mock.patch.object(bilibili_direct.time, "sleep"):
            data = bilibili_direct._json_get("https://api.bilibili.com/x", {}, 2.0)
        self.assertEqual(data["title"], "ok")
        self.assertEqual(len(calls), 2)

    def test_merge_set_cookie_vao_phien(self):
        headers = mock.Mock()
        headers.get_all.return_value = [
            "buvid4=xyz; Path=/",
            "SESSDATA=s; HttpOnly",
        ]
        bilibili_direct._merge_set_cookie(mock.Mock(headers=headers))
        self.assertEqual(bilibili_direct._SESSION_COOKIES["buvid4"], "xyz")
        self.assertEqual(bilibili_direct._SESSION_COOKIES["SESSDATA"], "s")
        merged = bilibili_direct._overlay_session({"Cookie": "buvid3=old"})
        self.assertIn("buvid4=xyz", merged["Cookie"])
        self.assertIn("buvid3=old", merged["Cookie"])

    def test_headers_co_buvid4_de_giam_412(self):
        values = bilibili_direct._headers("BV1nRsjeqEE8")
        self.assertIn("buvid4=", values["Cookie"])
        self.assertIn("b_lsid=", values["Cookie"])
        self.assertEqual(values["Accept-Language"].split(",")[0], "zh-CN")

    def test_fetch_range_bo_cdn_cham(self):
        class SlowResp:
            status = 206
            headers = {'Content-Range': 'bytes 0-1000/1001', 'Content-Length': '1001'}

            def getcode(self):
                return 206

            def read(self, _n):
                time.sleep(0.2)
                return b"\0" * 8

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        old_deadline = bilibili_direct._RANGE_DEADLINE
        bilibili_direct._RANGE_DEADLINE = 0.05
        try:
            with mock.patch.object(bilibili_direct, "urlopen",
                                   return_value=SlowResp()):
                with self.assertRaisesRegex(RuntimeError, "quá chậm"):
                    bilibili_direct._fetch_range(
                        "https://a.bilivideo.com/v", {}, 0, 1000)
        finally:
            bilibili_direct._RANGE_DEADLINE = old_deadline


if __name__ == "__main__":
    unittest.main()
