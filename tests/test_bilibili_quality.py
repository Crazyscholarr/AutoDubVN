import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from autodub import bilibili_direct as B


def dash(announced, qualities, backups=()):
    return {'quality': announced, 'dash': {
        'video': [{'id': q, 'codecs': 'avc1',
                   'baseUrl': f'https://v.bilivideo.com/{q}.m4s',
                   'backupUrl': list(backups)} for q in qualities],
        'audio': [{'id': 30280, 'bandwidth': 100,
                   'baseUrl': 'https://a.bilivideo.com/audio.m4s'}]}}


def mp4(q):
    return {'quality': q, 'durl': [{'url': 'https://upos-hz-mirrorakam.akamaized.net/v.mp4'}]}


class BilibiliQuality(unittest.TestCase):
    def sample_info(self):
        return {'title': 'Nguồn HD', 'requested_formats': [
            {'url': 'https://v.bilivideo.com/video', 'protocol': 'https',
             'vcodec': 'avc1', 'acodec': 'none', 'width': 1920, 'height': 1080,
             'http_headers': {'Referer': 'https://www.bilibili.com/'}},
            {'url': 'https://a.bilivideo.com/audio', 'protocol': 'https',
             'vcodec': 'none', 'acodec': 'mp4a'}]}

    def test_selected_ytdlp_1080_formats_are_transferred_without_downscaling(self):
        info = self.sample_info()
        backend = MagicMock()
        backend.__enter__.return_value.extract_info.return_value = info
        with patch('yt_dlp.YoutubeDL', return_value=backend) as factory:
            title, stream, headers = B._ytdlp_metadata('https://www.bilibili.com/video/BV1yYbE6eEnB/',
                                                       '1080')
        self.assertEqual((stream.width, stream.height, stream.quality), (1920, 1080, 80))
        self.assertEqual(stream.video_urls, ('https://v.bilivideo.com/video',))
        self.assertEqual(stream.audio_urls, ('https://a.bilivideo.com/audio',))
        options = factory.call_args.args[0]
        self.assertIn('height<=1080', options['format'])
        self.assertEqual(options['format_sort'][0], 'res')
        backend.__enter__.return_value.extract_info.assert_called_once_with(
            'https://www.bilibili.com/video/BV1yYbE6eEnB/', download=False)

    def test_selected_stream_needs_both_audio_and_video(self):
        info = self.sample_info()
        info['requested_formats'].pop()
        with self.assertRaises(RuntimeError):
            B._stream_from_ytdlp(info)

    def test_configured_browser_cookie_is_forwarded_and_locked_cookie_retries_public(self):
        import yt_dlp
        from yt_dlp.cookies import CookieLoadError
        parsed = yt_dlp.parse_options(['--ignore-config', '--cookies-from-browser', 'edge:Default'])
        broken, public = MagicMock(), MagicMock()
        broken.__enter__.return_value.extract_info.side_effect = CookieLoadError('failed to load cookies')
        public.__enter__.return_value.extract_info.return_value = self.sample_info()
        seen, events = [], []
        def backend(options):
            seen.append(dict(options))
            return broken if len(seen) == 1 else public
        with patch('yt_dlp.YoutubeDL', side_effect=backend), \
                patch('yt_dlp.parse_options', return_value=parsed):
            _, stream, _ = B._ytdlp_metadata('https://www.bilibili.com/video/BV1yYbE6eEnB/',
                'best', cookies_from_browser='edge:Default', callback=events.append)
        self.assertEqual(seen[0]['cookiesfrombrowser'][:2], ('edge', 'Default'))
        self.assertNotIn('cookiesfrombrowser', seen[1])
        self.assertEqual(stream.height, 1080)
        self.assertTrue(any(e.get('event') == 'cookie_unavailable' for e in events))

    def test_public_1080_download_keeps_the_verified_range_transfer(self):
        info = self.sample_info()
        stream = B._stream_from_ytdlp(info)
        def save(urls, path, *args, **kwargs):
            Path(path).write_bytes(b'fake media')
        def mux(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b'muxed')
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(B, '_ytdlp_metadata', return_value=('Nguồn HD', stream, {})), \
                patch.object(B, '_fetch_playurl') as legacy, \
                patch.object(B, '_download_stream', side_effect=save) as transfer, \
                patch.object(B, 'run', side_effect=mux):
            _, quality, kind = B.download_bilibili('https://www.bilibili.com/video/BV1yYbE6eEnB/',tmp)
        self.assertEqual((quality, kind), (80, 'dash'))
        self.assertEqual(transfer.call_count, 2)
        legacy.assert_not_called()

    def test_real_dash_quality_beats_misleading_top_level_label(self):
        available_720 = mp4(64)
        labelled_720_actual_480 = dash(64, [32, 16],
            ['https://backup.bilivideo.com/480.m4s'])
        winner = B._pick_best_play([available_720, labelled_720_actual_480], 120)
        self.assertEqual(B._pick_stream(winner, 120).quality, 64)

    def test_more_mirrors_cannot_beat_higher_resolution(self):
        low = dash(32, [32], [f'https://cdn{i}.bilivideo.com/v' for i in range(5)])
        high = dash(80, [80])
        winner = B._pick_best_play([low, high], 120)
        self.assertIs(winner, high)

    def test_mp4_container_bonus_cannot_beat_higher_resolution(self):
        low = {'quality': 64, 'durl': [{'url': 'https://upos-sz-mirrorcos.bilivideo.com/v'}]}
        high = dash(80, [80])
        self.assertIs(B._pick_best_play([low, high], 120), high)

    def test_explicit_480_limit_prefers_available_480(self):
        high = mp4(64)
        low = dash(32, [32])
        self.assertIs(B._pick_best_play([high, low], 32), low)

    def test_invalid_dash_without_audio_does_not_hide_valid_mp4(self):
        valid = mp4(64)
        broken = dash(120, [120])
        broken['dash']['audio'] = []
        self.assertIs(B._pick_best_play([broken, valid], 120), valid)

    def test_all_api_results_are_compared_using_actual_streams(self):
        low = dash(64, [32, 16])
        high = mp4(64)
        def get(url, *args, **kwargs):
            return high if 'platform=html5' in url else low
        with patch.object(B, '_json_get', side_effect=get), \
                patch.object(B, '_get_wbi_mixin', return_value='test'):
            stream = B._fetch_playurl('BV1gehE6DEjb', '1', 120, None)
        self.assertEqual((stream.kind, stream.quality), ('mp4', 64))


if __name__ == '__main__':
    unittest.main()
