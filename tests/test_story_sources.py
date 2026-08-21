import json
import unittest
from unittest import mock

from autodub import story_sources


class TimNguonVideo(unittest.TestCase):
    def test_youtube_search_chuyen_id_thanh_link_day_du(self):
        payload = {"entries": [{
            "id": "abc123", "title": "A new family story",
            "duration": 321, "channel": "Story Channel",
            "extractor_key": "Youtube",
        }]}
        result = mock.Mock(stdout=json.dumps(payload))
        with mock.patch.object(story_sources.downloader, "_ytdlp_cmd",
                               return_value=["python", "-m", "yt_dlp"]), \
                mock.patch.object(story_sources.downloader, "run",
                                  return_value=result):
            rows = story_sources.search_youtube("family story", 5)
        self.assertEqual(rows[0]["url"],
                         "https://www.youtube.com/watch?v=abc123")
        self.assertEqual(rows[0]["provider"], "youtube")

    def test_tim_ca_hai_nguon_gop_dung_gioi_han(self):
        bili = [{"url": "https://bilibili/1", "provider": "bilibili"}] * 2
        youtube = [{"url": "https://youtube/1", "provider": "youtube"}] * 2
        with mock.patch.object(story_sources, "search_bilibili", return_value=bili), \
                mock.patch.object(story_sources, "search_youtube", return_value=youtube):
            rows = story_sources.search("story", limit=3, provider="all")
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["provider"] for row in rows}, {"bilibili", "youtube"})

    def test_bilibili_fallback_api_khi_ytdlp_khong_ho_tro_search(self):
        api_rows = [{"url": "https://www.bilibili.com/video/BV1",
                     "provider": "bilibili"}]
        with mock.patch.object(story_sources.downloader, "_ytdlp_cmd",
                               return_value=["yt-dlp"]), \
                mock.patch.object(story_sources, "_run_metadata",
                                  side_effect=RuntimeError("Unsupported URL")), \
                mock.patch.object(story_sources, "_search_bilibili_api",
                                  return_value=api_rows) as fallback:
            rows = story_sources.search_bilibili("rain", 3)
        self.assertEqual(rows, api_rows)
        fallback.assert_called_once_with("rain", 3)

    def test_doc_thoi_luong_bilibili_dang_phut_giay(self):
        self.assertEqual(story_sources._duration_seconds("1:02:03"), 3723.0)

    def test_tim_tat_ca_bilibili_loi_van_tra_youtube(self):
        youtube = [{"url": "https://youtube/1", "provider": "youtube"}]
        with mock.patch.object(story_sources, "search_bilibili",
                               side_effect=RuntimeError("HTTP 412")), \
                mock.patch.object(story_sources, "search_youtube",
                                  return_value=youtube) as search_youtube:
            rows = story_sources.search("story", limit=4, provider="all")
        self.assertEqual(rows, youtube)
        search_youtube.assert_called_once_with("story", 4, None, None)

    def test_catalog_co_nguon_trung_va_tu_khoa_tieng_trung(self):
        sources = story_sources.reference_catalog()
        keys = {row["key"] for row in sources}
        self.assertIn("zhihu_yanxuan", keys)
        self.assertIn("660i_story", keys)
        self.assertGreaterEqual(len(story_sources.chinese_keyword_catalog()), 15)
        self.assertIn("婆媳矛盾 故事",
                      {row["keyword"] for row in story_sources.chinese_keyword_catalog()})

    def test_tim_bai_tham_khao_tu_rss_va_gan_nhan_nguon(self):
        rss = '''<?xml version="1.0"?><rss><channel><item>
          <title>婆媳矛盾故事 - sample</title><link>https://zhihu.com/question/1</link>
          <description>snippet ve mau thuan gia dinh</description>
        </item></channel></rss>'''.encode("utf-8")

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return rss

        with mock.patch.object(story_sources, "urlopen", return_value=Response()):
            rows = story_sources.search_web_references(
                "婆媳矛盾 故事", source_keys=["zhihu_yanxuan"], limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "reference")
        self.assertEqual(rows[0]["source_key"], "zhihu_yanxuan")


if __name__ == "__main__":
    unittest.main()
