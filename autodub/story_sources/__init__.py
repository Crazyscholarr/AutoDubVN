"""Tìm và tải nguồn video cho chế độ Kể chuyện AI.

Nguồn tìm kiếm dùng extractor của yt-dlp thay vì tự gọi API Bilibili, nhờ vậy
vẫn dùng được cookies đăng nhập và không làm hỏng downloader hiện có.
"""
from __future__ import annotations

import sys as _sys
import time
from urllib.request import Request, urlopen

import requests

from .. import downloader
from ..utils import log, run

from . import fetch as _fetch_mod
from . import parse as _parse_mod
from . import search as _search_mod
from .parse import (
    CHINESE_KEYWORDS,
    REFERENCE_SOURCES,
    _ORAL_RURAL_TERMS,
    _ORAL_TELLER_TERMS,
    _SKIP_LISTING_PATH,
    _SPIRITUAL_TERMS,
    _TOPIC_TERMS,
    _clean_html_fragment,
    _compact_chinese_query,
    _decode_bing_link,
    _domain_matches,
    _focused_chinese_query,
    _html_article,
    _html_search_links,
    _json_ld_article,
    _looks_like_article,
    _metric_number,
    _parse_bing_algo_block,
    _pick_domain_url,
    _rank_direct_rows,
    _reference_relevance,
    _search_text,
    _text_metrics,
    _unwrap_search_href,
    _url_from_cite,
    chinese_keyword_catalog,
    reference_catalog,
)
from .search import (
    _SEARCH_HEADERS,
    _ddg_reference_search,
    _direct_reference_search,
    _duration_seconds,
    _entry_url,
    _fetch_search_page,
    _bing_reference_search,
    _bing_rss_search,
    _normalise_entry,
    _run_metadata,
    _search_bilibili_api,
    _site_listing_search,
    _web_reference_search,
    search,
    search_bilibili,
    search_douyin,
    search_ixigua,
    search_kuaishou,
    search_tiktok,
    search_web_references,
    search_youtube,
)
from .fetch import (
    _REFERENCE_CACHE,
    _REFERENCE_CACHE_LOCK,
    _REFERENCE_CACHE_MAX,
    _REFERENCE_CACHE_TTL,
    _jina_article,
    _reddit_article,
    _video_files,
    cut_video_segments,
    download_many,
    enrich_reference_rows,
    fetch_reference_article,
)

_pkg = _sys.modules[__name__]


def _bind_pkg_lookup(module, name: str, pkg=_pkg) -> None:
    if not hasattr(pkg, name) or not hasattr(module, name):
        return
    orig = getattr(pkg, name)
    if not callable(orig):
        return

    def _proxy(*args, **kwargs):
        return getattr(pkg, name)(*args, **kwargs)

    _proxy.__name__ = getattr(orig, "__name__", name)
    _proxy.__qualname__ = getattr(orig, "__qualname__", name)
    _proxy.__doc__ = getattr(orig, "__doc__", None)
    setattr(module, name, _proxy)


for _mod, _names in (
    (_parse_mod, (
        "_clean_html_fragment", "_decode_bing_link", "_url_from_cite",
        "_unwrap_search_href", "_domain_matches", "_pick_domain_url",
        "_parse_bing_algo_block", "_looks_like_article", "_html_search_links",
        "_json_ld_article", "_html_article", "_metric_number", "_text_metrics",
        "_search_text", "_reference_relevance", "_rank_direct_rows",
        "_compact_chinese_query", "_focused_chinese_query",
        "reference_catalog", "chinese_keyword_catalog",
    )),
    (_search_mod, (
        "urlopen", "log",
        "_fetch_search_page", "_bing_reference_search", "_bing_rss_search",
        "_ddg_reference_search", "_web_reference_search",
        "_site_listing_search", "_direct_reference_search",
        "_run_metadata", "_search_bilibili_api", "search_bilibili",
        "search_youtube", "search_douyin", "search_tiktok",
        "search_kuaishou", "search_ixigua", "search", "search_web_references",
        "_entry_url", "_normalise_entry", "_duration_seconds",
        "_html_search_links", "_parse_bing_algo_block", "_pick_domain_url",
        "_unwrap_search_href", "_url_from_cite", "_domain_matches",
        "_clean_html_fragment", "_looks_like_article",
        "_compact_chinese_query", "_focused_chinese_query", "_search_text",
        "_reference_relevance", "_rank_direct_rows", "_text_metrics",
    )),
    (_fetch_mod, (
        "log", "run",
        "fetch_reference_article", "enrich_reference_rows",
        "_html_article", "_jina_article", "_reddit_article",
        "_text_metrics", "_domain_matches",
        "_clean_html_fragment", "_reference_relevance",
        "_video_files", "cut_video_segments", "download_many",
    )),
):
    for _name in _names:
        _bind_pkg_lookup(_mod, _name)

del _bind_pkg_lookup, _mod, _names, _name
del _parse_mod, _search_mod, _fetch_mod, _pkg, _sys
