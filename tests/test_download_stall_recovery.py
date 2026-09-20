import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autodub import bilibili_direct as B, downloader as D, utils


class StallRecovery(unittest.TestCase):
    def test_byte_watchdog_ignores_repeated_and_regressing_counters(self):
        with patch.object(D.time, 'monotonic', return_value=0) as clock:
            guard = D._TransferWatchdog(timeout=60, minimum_bytes=100)
            def update(n, stream='audio', status='downloading'):
                guard.update(dict(id='v', format_id=stream, downloaded=n, status=status))
            update(1000)
            clock.return_value = 59
            update(1050)
            guard.check()
            update(50)
            clock.return_value = 61
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                guard.check()
            update(1100)
            guard.check()
            clock.return_value = 200
            update(0, 'video')
            guard.check()
            update(200, status='finished')
            clock.return_value = 999
            with self.assertRaisesRegex(RuntimeError, 'between_streams'):
                guard.check()
            guard.observe_line('[Merger] Merging formats')
            guard.check()
            guard.reset()
            guard.check()

    def test_cached_video_finished_does_not_disable_audio_wait_timeout(self):
        with patch.object(D.time, 'monotonic', return_value=0) as clock:
            guard = D._TransferWatchdog()
            info = D._parse_progress_line(D._PROGRESS_PREFIX +
                'BV1GmYT6FE2S|100026|finished|100%|123|123|NA|NA|NA')
            guard.update(info)
            clock.return_value = 59
            guard.update(info)
            guard.check()
            clock.return_value = 61
            with self.assertRaisesRegex(RuntimeError, 'between_streams') as ctx:
                guard.check()
            self.assertTrue(D._connection_download_failed(ctx.exception))
            guard.update(dict(id='BV1GmYT6FE2S', format_id='30280',
                              status='downloading', downloaded=1024))
            guard.check()
            guard.observe_line(D._PATH_PREFIX + 'out.mp4')
            clock.return_value = 999
            guard.check()
            guard.reset()
            clock.return_value = 1120
            with self.assertRaisesRegex(RuntimeError, 'connecting'):
                guard.check()

    def test_single_cdn_does_not_abort_only_for_speed_and_reports_reason(self):
        probe = B.Probe('https://cdn.bilivideo.com/v', 3, True, 'video/mp4', 1e7)
        events = []
        pool = B._CDNPool([probe], events.append)
        with patch.object(B, '_fetch_range', return_value=b'abc') as fetch:
            self.assertEqual(B._fetch_range_retry(pool, {}, 0, 2), b'abc')
            self.assertEqual(fetch.call_args.kwargs['min_speed'], 0)
        pool.failed(probe, RuntimeError('CDN trả thiếu khối: 1/3 byte'))
        self.assertIn('1/3 byte', events[-1]['text'])
        self.assertNotIn('token=secret', B._range_error_detail(
            RuntimeError('failed https://cdn.bilivideo.com/v?token=secret')))

    def test_health_failure_stops_actual_subprocess_despite_output(self):
        seen, processes = [], []
        original = subprocess.Popen
        def spawn(*args, **kwargs):
            proc = original(*args, **kwargs)
            processes.append(proc)
            return proc
        def health():
            if seen:
                raise RuntimeError('stalled')
        with patch.object(utils.subprocess, 'Popen', side_effect=spawn):
            with self.assertRaisesRegex(RuntimeError, 'stalled'):
                utils.run([sys.executable, '-u', '-c',
                           'import time; print("progress"); time.sleep(30)'],
                          line_callback=seen.append, health_check=health)
        self.assertIsNotNone(processes[0].poll())

    def test_short_large_range_is_split_and_every_piece_is_validated(self):
        data = bytes(range(256)) * 4096
        probe = B.Probe('https://cdn.bilivideo.com/v', len(data), True,
                        'video/mp4', 1000, 'hash', '"etag"')
        calls = []
        def fetch(url, headers, start, end, total, etag, min_speed):
            calls.append((start, end))
            self.assertEqual((total, etag), (len(data), '"etag"'))
            if end - start + 1 > 256 * 1024:
                raise B.ShortRangeError('short body')
            return data[start:end + 1]
        pool = B._CDNPool([probe])
        with patch.object(B, '_fetch_range', side_effect=fetch):
            self.assertEqual(B._fetch_range_retry(pool, {}, 0, len(data) - 1), data)
        self.assertEqual(len(calls), 5)
        with patch.object(B, '_fetch_range', side_effect=B.ShortRangeError('still short')) as f:
            with self.assertRaises(B.RangeDownloadError):
                B._fetch_range_retry(pool, {}, 0, len(data) - 1)
            self.assertEqual(f.call_count, 4)

    def test_direct_recovery_reduces_connections_and_preserves_partial(self):
        data = b'correct content'
        probe = B.Probe('https://cdn.bilivideo.com/v', len(data), True,
                        'video/mp4', 5e6, 'prefix')
        windows, events = [], []
        def ranges(pool, headers, path, *args, window=None):
            windows.append(window)
            if len(windows) == 1:
                Path(path).write_bytes(data[:3])
                raise B.RangeDownloadError('timeout')
            self.assertEqual(Path(path).read_bytes(), data[:3])
            Path(path).write_bytes(data)
            return hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(B, '_rank_probes', return_value=[probe]), \
                patch.object(B, '_MULTI_MIN', 1), \
                patch.object(B, '_download_ranges', side_effect=ranges):
            B._download_stream([probe.url], str(Path(tmp) / 'v'), {}, 'v', events.append, 0, 100)
        self.assertEqual(windows, [12, 4])
        self.assertTrue(any(e.get('event') == 'range_recovery' for e in events))
        events.clear()
        B._CDNPool([probe], events.append).failed(probe, TimeoutError())
        self.assertIn('chỉ có một CDN', events[0]['text'])

        slow = B.Probe('https://cdn.bilivideo.com/v', len(data), True,
                       'video/mp4', 1000, 'prefix')
        slow_windows = []
        def slow_ranges(*_args, window=None, **_kwargs):
            slow_windows.append(window)
            raise B.RangeDownloadError('timeout')
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(B, '_rank_probes', return_value=[slow]), \
                patch.object(B, '_MULTI_MIN', 1), \
                patch.object(B, '_download_ranges', side_effect=slow_ranges):
            with self.assertRaises(B.RangeDownloadError):
                B._download_stream([slow.url], str(Path(tmp) / 's'), {}, 's', None, 0, 100)
        self.assertEqual(slow_windows, [2, 1])


if __name__ == '__main__':
    unittest.main()
