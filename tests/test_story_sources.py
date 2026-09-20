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
                mock.patch.object(story_sources, "search_youtube", return_value=youtube), \
                mock.patch.object(story_sources, "search_douyin", return_value=[]):
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
                                  return_value=youtube) as search_youtube, \
                mock.patch.object(story_sources, "search_douyin",
                                  return_value=[]):
            rows = story_sources.search("story", limit=4, provider="all")
        self.assertEqual(rows, youtube)
        search_youtube.assert_called_once_with("story", 4, None, None)

    def test_tim_douyin_lay_link_video_tu_bing(self):
        page = (
            '<a href="https://www.douyin.com/video/7123456789012345678">'
            "古诗朗诵</a>"
        ).encode("utf-8")

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return page

        with mock.patch.object(story_sources, "urlopen", return_value=Response()):
            rows = story_sources.search_douyin("古诗 朗诵", 5)
        self.assertEqual(rows[0]["provider"], "douyin")
        self.assertEqual(rows[0]["url"],
                         "https://www.douyin.com/video/7123456789012345678")

    def test_catalog_co_nguon_trung_va_tu_khoa_tieng_trung(self):
        sources = story_sources.reference_catalog()
        keys = {row["key"] for row in sources}
        self.assertIn("zhihu_yanxuan", keys)
        self.assertIn("660i_story", keys)
        self.assertTrue({"douban_groups", "douban_read", "qidian", "hongxiu",
                         "qimao", "zongheng"}.issubset(keys))
        chinese = [row for row in sources if row.get("lang") == "zh"]
        self.assertGreaterEqual(len(chinese), 9)
        self.assertTrue(all(row.get("narrative_style") for row in chinese))
        self.assertGreaterEqual(len(story_sources.chinese_keyword_catalog()), 25)
        self.assertIn("婆媳矛盾 故事",
                      {row["keyword"] for row in story_sources.chinese_keyword_catalog()})

    def test_catalog_tam_linh_nhieu_tu_khoa_va_nguon(self):
        keys = {row["key"] for row in story_sources.reference_catalog()}
        self.assertTrue({"gushi365", "minjian6mj",
                         "dantri_tamlinh", "vnexpress_tamlinh"}.issubset(keys))
        catalog = story_sources.chinese_keyword_catalog()
        spiritual = [row for row in catalog
                     if str(row.get("source_group") or "").startswith("spiritual")]
        self.assertGreaterEqual(len(spiritual), 28)
        self.assertTrue(any(row.get("source_group") == "spiritual_vi"
                            for row in spiritual))

    def test_tam_linh_khong_loai_bai_thieu_chu_nguoi_gia(self):
        ghost = {"title": "古井闹鬼传说", "excerpt": "村里一口古井夜里有人声"}
        family = {"title": "医路芳华", "excerpt": "一位医生回乡照顾父母"}
        self.assertTrue(story_sources._reference_relevance(
            ghost, "古井 闹鬼 传说")["relevance_accepted"])
        self.assertFalse(story_sources._reference_relevance(
            family, "古井 闹鬼 传说")["relevance_accepted"])

    def test_tam_linh_giu_chuyen_ma_do_thi_loai_doi_song(self):
        urban = {"title": "都市灵异小说", "excerpt": "高楼里的鬼故事"}
        family = {"title": "医路芳华", "excerpt": "一位医生回乡照顾父母"}
        self.assertTrue(story_sources._reference_relevance(
            urban, "奶奶讲的 鬼故事")["relevance_accepted"])
        self.assertFalse(story_sources._reference_relevance(
            family, "奶奶讲的 鬼故事")["relevance_accepted"])

    def test_tim_nguon_ap_dung_hau_to_va_tra_loi_ke(self):
        found = [{"url": "https://www.douban.com/group/topic/123/",
                  "title": "Một trải nghiệm gia đình", "kind": "reference"}]
        with mock.patch.object(story_sources, "_direct_reference_search",
                               return_value=[]), \
                mock.patch.object(story_sources, "_bing_reference_search",
                               return_value=found) as search:
            rows = story_sources.search_web_references(
                "婆媳矛盾", source_keys=["douban_groups"], limit=5)
        query, domain, _limit = search.call_args.args
        self.assertIn("小组 真实经历", query)
        self.assertEqual(domain, "www.douban.com")
        self.assertEqual(rows[0]["narrative_style"], "Ngôi thứ nhất · trải nghiệm thật")
        self.assertEqual(rows[0]["content_form"], "experience")

    def test_qimao_dung_api_chinh_chu_va_xep_ket_qua_lien_quan(self):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": {"search_list": [
            {"book_id": "2", "title": "Một sách khác", "intro": "矛盾理论",
             "read_url": "https://www.qimao.com/shuku/2/"},
            {"book_id": "1", "title": "婆媳俩", "intro": "婆媳矛盾与家庭养老",
             "read_url": "https://www.qimao.com/shuku/1/", "author": "甲"},
        ]}}
        source = next(row for row in story_sources.reference_catalog()
                      if row["key"] == "qimao")
        with mock.patch.object(story_sources.requests, "get", return_value=response):
            rows = story_sources._direct_reference_search("婆媳矛盾 故事", source, 2)
        self.assertEqual(rows[0]["title"], "婆媳俩")
        self.assertEqual(rows[0]["provider"], "qimao_search")
        self.assertEqual(rows[0]["author"], "甲")

    def test_tim_bai_tham_khao_tu_html_va_gan_nhan_nguon(self):
        page = '''<html><body>
          <li class="b_algo"><h2><a href="https://www.zhihu.com/question/1">
          婆媳矛盾故事 - sample</a></h2><p>snippet ve mau thuan gia dinh</p></li>
          <li class="b_algo"><h2><a href="https://answers.microsoft.com/1">
          ket qua rac</a></h2></li></body></html>'''.encode("utf-8")

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return page

        with mock.patch.object(story_sources, "urlopen", return_value=Response()):
            rows = story_sources.search_web_references(
                "婆媳矛盾 故事", source_keys=["zhihu_yanxuan"], limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "reference")
        self.assertEqual(rows[0]["source_key"], "zhihu_yanxuan")
        self.assertTrue(rows[0]["url"].startswith("https://www.zhihu.com/"))

    def test_tai_bai_json_ld_thanh_schema_kho_noi_dung(self):
        body = "婆媳矛盾与老人赡养。" * 80
        page = ('<script type="application/ld+json">' + json.dumps({
            "@type": "Article", "headline": "一个真实故事",
            "articleBody": body, "author": {"name": "Tác giả"},
        }, ensure_ascii=False) + '</script>')
        response = mock.Mock(text=page)
        response.raise_for_status.return_value = None
        with mock.patch.object(story_sources.requests, "get", return_value=response):
            item = story_sources.fetch_reference_article({
                "url": "https://www.qimao.com/shuku/1/", "source_name": "Nguồn thử",
                "keyword": "婆媳矛盾 故事", "topic": "Mẹ chồng nàng dâu",
            })
        self.assertEqual(item["title"], "一个真实故事")
        self.assertGreater(len(item["rawContent"]), 300)
        self.assertEqual(item["fetch_mode"], "direct")
        self.assertIn("婆媳矛盾 故事", item["tags"])
        self.assertEqual(item["language"], "zh")
        self.assertEqual(item["content_form"], "serial")
        self.assertIn("hook nhanh", item["narrative_style"])

    def test_cookie_khong_tai_lai_khi_cache_da_co_toan_van(self):
        url = "https://www.zhihu.com/question/999001"
        story_sources._REFERENCE_CACHE[url] = (story_sources.time.time(), {
            "title": "Đã enrich", "rawContent": "nội dung đủ dài. " * 40,
            "sourceUrl": url, "fetch_mode": "jina",
        })
        try:
            with mock.patch.object(story_sources.requests, "get") as get:
                item = story_sources.fetch_reference_article(
                    {"url": url}, cookie="z_c0=session")
            get.assert_not_called()
            self.assertIn("nội dung đủ dài", item["rawContent"])
        finally:
            story_sources._REFERENCE_CACHE.pop(url, None)

    def test_xep_luot_doc_cao_nhat_len_dau(self):
        rows = [{"url": "https://zhihu.com/question/1", "title": "thấp"},
                {"url": "https://zhihu.com/question/2", "title": "cao"},
                {"url": "https://zhihu.com/question/3", "title": "không công khai"}]
        def fetched(row, **_kwargs):
            count = {"1": 1200, "2": 99000, "3": 0}[row["url"].rsplit("/", 1)[-1]]
            return {"rawContent": "内容" * 300, "read_count": count,
                    "engagement_count": 10, "title": row["title"]}
        with mock.patch.object(story_sources, "fetch_reference_article",
                               side_effect=fetched):
            ranked = story_sources.enrich_reference_rows(rows, max_workers=2)
        self.assertEqual([x["title"] for x in ranked], ["cao", "thấp", "không công khai"])
        self.assertEqual(ranked[0]["popularity_rank"], 1)

    def test_tim_tam_linh_loai_chuyen_doi_song_du_nhieu_luot_doc(self):
        rows = [
            {"url": "https://example.com/doi-song", "title": "医路芳华",
             "excerpt": "一位医生回乡照顾父母的现实生活", "read_count": 9_000_000},
            {"url": "https://example.com/tam-linh", "title": "农村老人讲的灵异故事",
             "excerpt": "奶奶讲述村里一件怪事", "read_count": 1200},
        ]

        def fetched(row, **_kwargs):
            if "doi-song" in row["url"]:
                return {"title": row["title"], "rawContent": "医生与家人的生活" * 100,
                        "read_count": 9_000_000, "engagement_count": 500}
            return {"title": row["title"], "rawContent": "乡村老人讲灵异故事" * 100,
                    "read_count": 1200, "engagement_count": 20}

        with mock.patch.object(story_sources, "fetch_reference_article",
                               side_effect=fetched):
            ranked = story_sources.enrich_reference_rows(
                rows, max_workers=2, query="农村老人 灵异故事")
        self.assertEqual([x["title"] for x in ranked], ["农村老人讲的灵异故事"])
        self.assertGreater(ranked[0]["relevance_score"], 0)

    def test_xep_do_lien_quan_truoc_luot_doc_cho_chu_de_thuong(self):
        rows = [
            {"url": "https://example.com/noise", "title": "热门小说",
             "excerpt": "都市生活"},
            {"url": "https://example.com/right", "title": "婆媳矛盾真实故事",
             "excerpt": "家庭冲突"},
        ]

        def fetched(row, **_kwargs):
            relevant = "right" in row["url"]
            return {"title": row["title"],
                    "rawContent": ("婆媳矛盾" if relevant else "都市生活") * 100,
                    "read_count": 100 if relevant else 8_000_000,
                    "engagement_count": 0}

        with mock.patch.object(story_sources, "fetch_reference_article",
                               side_effect=fetched):
            ranked = story_sources.enrich_reference_rows(
                rows, max_workers=2, query="婆媳矛盾 故事")
        self.assertEqual(ranked[0]["title"], "婆媳矛盾真实故事")

    def test_doc_luot_xem_zhihu_tu_reader(self):
        metrics = story_sources._text_metrics(
            "关注者\n\n**4**\n\n被浏览\n\n**2,259**\n\n12 人赞同了该回答\n3 条评论")
        self.assertEqual(metrics["read_count"], 2259)
        self.assertEqual(metrics["engagement_count"], 15)

    def test_bing_giai_ma_link_tuong_doi_va_cite(self):
        import base64
        target = "https://www.zhihu.com/question/9"
        token = "a1" + base64.urlsafe_b64encode(target.encode("utf-8")).decode("ascii").rstrip("=")
        page = (
            '<ol id="b_results"><li class="b_algo">'
            '<a class="tilk" href="/ck/l?u=%s"></a>'
            '<h2><a href="/ck/l?u=%s">乡村怪谈村里那口井</a></h2>'
            "<cite>zhihu.com › question › 9</cite>"
            "<p>奶奶讲述一件怪事</p></li></ol>" % (token, token)
        ).encode("utf-8")

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return page

        with mock.patch.object(story_sources, "urlopen", return_value=Response()):
            rows = story_sources._bing_reference_search("乡村怪谈", "zhihu.com", 5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], target)
        self.assertIn("乡村怪谈", rows[0]["title"])

    def test_cite_khi_href_bing_khong_giai_ma_duoc(self):
        page = (
            '<li class="b_algo"><h2><a href="https://www.bing.com/ck/l?q=x">'
            "乡村怪谈</a></h2><cite>zhihu.com › question › 88</cite>"
            "<p>村里怪事</p></li>"
        ).encode("utf-8")

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return page

        with mock.patch.object(story_sources, "urlopen", return_value=Response()):
            rows = story_sources._bing_reference_search("乡村怪谈", "zhihu.com", 5)
        self.assertEqual(rows[0]["url"], "https://zhihu.com/question/88")

    def test_duckduckgo_khi_bing_khong_ra_bai(self):
        bing = b"<html><body>no results</body></html>"
        ddg = (
            '<a class="result__a" href="https://duckduckgo.com/l/?uddg='
            'https%3A%2F%2Fwww.zhihu.com%2Fquestion%2F2">乡村怪谈夜路</a>'
        ).encode("utf-8")

        class Response:
            def __init__(self, data): self._data = data
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return self._data

        calls = []

        def fake_urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            calls.append(url)
            return Response(ddg if "duckduckgo" in url else bing)

        with mock.patch.object(story_sources, "urlopen", side_effect=fake_urlopen):
            rows = story_sources._web_reference_search("乡村怪谈", "zhihu.com", 5)
        self.assertTrue(any("duckduckgo" in url for url in calls))
        self.assertEqual(rows[0]["url"], "https://www.zhihu.com/question/2")
        self.assertEqual(rows[0]["provider"], "duckduckgo")

    def test_site_listing_lay_bai_dan_gian(self):
        html = '''<html><a href="https://660i.com/12345.html">乡村怪谈：村口那口古井夜里有人声</a>
        <a href="https://660i.com/about">关于我们</a></html>'''
        response = mock.Mock(text=html)
        response.raise_for_status.return_value = None
        source = next(row for row in story_sources.reference_catalog()
                      if row["key"] == "660i_story")
        with mock.patch.object(story_sources.requests, "get", return_value=response):
            rows = story_sources._direct_reference_search(
                "乡村怪谈 老人讲述", source, 5)
        self.assertEqual(rows[0]["url"], "https://660i.com/12345.html")
        self.assertEqual(rows[0]["provider"], "site_listing")


if __name__ == "__main__":
    unittest.main()
