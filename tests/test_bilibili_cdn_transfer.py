"""HTTP integration tests with real Range sockets, including hostile responses."""
import hashlib
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from autodub import bilibili_direct as B

DATA = bytes(range(256)) * 2048


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        lo, hi = 0, len(DATA) - 1
        ranged = 'Range' in self.headers and self.path != '/single'
        if ranged:
            lo, hi = map(int, self.headers['Range'][6:].split('-'))
            hi = min(hi, len(DATA) - 1)
        self.server.calls.append((self.path, lo, hi))
        self.send_response(206 if ranged else 200)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('ETag', '"same-object"')
        if ranged:
            offset = lo + 1 if self.path == '/wrong' else lo
            self.send_header('Content-Range', f'bytes {offset}-{hi}/{len(DATA)}')
        self.send_header('Content-Length', str(hi - lo + 1))
        self.end_headers()
        blob = DATA[lo:hi + 1]
        if self.path == '/short':
            blob = blob[:len(blob) // 2]
        try:
            if self.path == '/slow':
                for i in range(0, len(blob), 4096):
                    time.sleep(.06)
                    self.wfile.write(blob[i:i + 4096])
                    self.wfile.flush()
            else:
                self.wfile.write(blob)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class CDNTransfer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.daemon_threads = True
        cls.server.calls = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.calls.clear()

    def test_all_backups_are_probed_and_sorted(self):
        urls = [f'https://cdn{i}.bilivideo.com/a' for i in range(10)]
        self.assertEqual(B._candidate_urls(urls + [urls[0]]), urls)
        def probe(url, headers):
            return B.Probe(url, 123, True, 'video/mp4', urls.index(url) + 1)
        with patch.object(B, '_probe', side_effect=probe) as fn:
            ranked = B._rank_probes(urls, {})
        self.assertEqual(fn.call_count, 10)
        self.assertEqual(ranked[0].url, urls[-1])
        self.assertEqual(B._stream_urls({'baseUrl': urls[0], 'base_url': urls[1],
            'backupUrl': urls[2:7], 'backup_url': urls[7:]}), tuple(urls))

    def test_real_probe_samples_2mib_or_complete_small_file(self):
        self.assertEqual(B._PROBE_SAMPLE, 2 * 1024 * 1024)
        probe = B._probe(self.base + '/fast', {})
        self.assertTrue(probe.accepts_ranges)
        self.assertEqual(probe.length, len(DATA))
        self.assertEqual(probe.sample_hash, hashlib.sha256(DATA).hexdigest())
        self.assertGreater(probe.speed, 0)

    def test_wrong_range_and_short_body_rejected(self):
        for path in ['/wrong', '/short']:
            with self.subTest(path=path), self.assertRaises(Exception):
                B._fetch_range(self.base + path, {}, 100, 2000, len(DATA))

    def test_slow_cdn_switch_is_shared_and_keeps_exact_bytes(self):
        slow = B.Probe(self.base + '/slow', len(DATA), True, 'video/mp4', 9e6)
        fast = B.Probe(self.base + '/fast', len(DATA), True, 'video/mp4', 8e6)
        events = []
        pool = B._CDNPool([slow, fast], events.append)
        with patch.object(B, '_SPEED_WINDOW', .04), patch.object(B, '_MIN_SPEED', 100000):
            block = B._fetch_range_retry(pool, {}, 0, 65535)
        self.assertEqual(block, DATA[:65536])
        self.assertEqual(pool.ranked()[0].url, fast.url)
        self.assertTrue(events)
        before = len(self.server.calls)
        self.assertEqual(B._fetch_range_retry(pool, {}, 65536, 131071), DATA[65536:131072])
        self.assertEqual(self.server.calls[before][0], '/fast')

    def test_parallel_assembly_hash_and_cache_corruption(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(B, '_allowed_cdn_host', return_value=True), \
                patch.object(B, '_https_url', side_effect=lambda url: str(url or '')), \
                patch.object(B, '_PROBE_SAMPLE', 65536), patch.object(B, '_CHUNK', 32768), \
                patch.object(B, '_MULTI_MIN', 1):
            dest = str(Path(tmp) / 'video.mp4')
            B._download_stream([self.base + '/fast'], dest, {}, 'test', None, 0, 100)
            self.assertEqual(Path(dest).read_bytes(), DATA)
            meta = json.loads(Path(dest + '.integrity.json').read_text())
            self.assertEqual(meta['sha256'], hashlib.sha256(DATA).hexdigest())
            self.assertGreaterEqual(len(self.server.calls), 17)
            Path(dest).write_bytes(b'X' * len(DATA))
            B._download_stream([self.base + '/fast'], dest, {}, 'test', None, 0, 100)
            self.assertEqual(Path(dest).read_bytes(), DATA)

    def test_unverified_partial_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(B, '_CHUNK', 32768):
            path = str(Path(tmp) / 'v.part')
            Path(path).write_bytes(b'X' * 65536)
            B._download_ranges([self.base + '/fast'], {}, path, len(DATA), 'test', None, 0, 100)
            self.assertEqual(Path(path).read_bytes(), DATA)
            # Corrupt a previously hashed prefix; the resume manifest must detect it.
            with open(path, 'r+b') as handle:
                handle.write(b'X')
            self.assertEqual(B._resume_records(path, len(DATA),
                hashlib.sha256(str([self.base + '/fast']).encode()).hexdigest()), [])

    def test_no_range_has_measured_speed_and_single_download(self):
        probe = B._probe(self.base + '/single', {})
        self.assertFalse(probe.accepts_ranges)
        self.assertGreater(probe.speed, 0)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'single')
            digest = B._download_single(probe.url, {}, path, probe.length, 'test', None, 0, 100)
            self.assertEqual(digest, hashlib.sha256(DATA).hexdigest())
            self.assertEqual(Path(path).read_bytes(), DATA)

    def test_source_checksums_are_explicit_and_validated(self):
        self.assertEqual(B._source_checksums({'etag': 'a' * 32, 'md5': ''}), ())
        self.assertEqual(B._source_checksums({'md5': 'A' * 32}), (('md5', 'a' * 32),))
        for value in ('abc', 'a' * 32 + '-6', 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                B._source_checksums({'md5': value})
        choice = B._pick_stream({'quality': 64, 'durl': [{'url':
            'https://cdn.bilivideo.com/a', 'md5': 'a' * 32}]}, 64)
        self.assertEqual(choice.video_checksums, (('md5', 'a' * 32),))

    def test_origin_hash_checked_before_promotion_and_on_cache_hit(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(B, '_allowed_cdn_host', return_value=True), \
                patch.object(B, '_https_url', side_effect=lambda url: str(url or '')):
            dest = str(Path(tmp) / 'video.mp4')
            args = ([self.base + '/fast'], dest, {}, 'test', None, 0, 100)
            wrong = (('md5', '0' * 32),)
            correct = (('md5', hashlib.md5(DATA).hexdigest()),
                       ('sha256', hashlib.sha256(DATA).hexdigest()))
            with self.assertRaisesRegex(RuntimeError, 'Checksum'):
                B._download_stream(*args, source_checksums=wrong)
            self.assertFalse(Path(dest).exists())
            B._download_stream(*args, source_checksums=correct)
            meta = json.loads(Path(dest + '.integrity.json').read_text())
            self.assertEqual(meta['source_verification']['status'], 'verified')
            self.assertEqual(meta['source_verification']['checksums'], dict(correct))
            with self.assertRaisesRegex(RuntimeError, 'Checksum'):
                B._download_stream(*args, source_checksums=wrong)
            B._download_stream(*args)
            meta = json.loads(Path(dest + '.integrity.json').read_text())
            self.assertEqual(meta['source_verification']['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
