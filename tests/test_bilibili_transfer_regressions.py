"""Local reproductions of the slow/stalled Bilibili transfer from user logs."""
import hashlib
import io
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from autodub import bilibili_direct as B


class TransferRegressions(unittest.TestCase):
    def test_probe_budget_does_not_wait_for_stalled_mirror(self):
        release = threading.Event()
        completed = threading.Event()
        result = []

        def probe(url, headers, sample=None):
            if url == 'slow':
                release.wait(2)
                raise TimeoutError('stalled')
            return B.Probe(url, 100, True, 'video/mp4', 2e6, 'same')

        def rank():
            result.extend(B._rank_probes(['slow', 'fast'], {}))
            completed.set()

        with patch.object(B, '_PROBE_BUDGET', .06, create=True), \
                patch.object(B, '_probe', side_effect=probe), \
                ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(rank)
            try:
                self.assertTrue(completed.wait(.4), 'good CDN waited for stalled probe')
                self.assertEqual(result[0].url, 'fast')
            finally:
                release.set()
                task.result(timeout=3)

    def test_next_chunk_starts_while_first_chunk_is_stalled(self):
        next_started = threading.Event()
        saw_overlap = []
        data = b'abcdefghijklmnop'

        def fetch(urls, headers, start, end):
            if start == 0:
                saw_overlap.append(next_started.wait(.5))
            if start == 8:
                next_started.set()
            return data[start:end + 1]

        with tempfile.TemporaryDirectory() as tmp, patch.object(B, '_CHUNK', 4), \
                patch.object(B, '_fetch_range_retry', side_effect=fetch):
            part = str(Path(tmp) / 'v.part')
            digest = B._download_ranges(['cdn'], {}, part, len(data), 'video', None,
                                        0, 100, window=2)
            self.assertEqual(Path(part).read_bytes(), data)
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
        self.assertEqual(saw_overlap, [True], 'batch barrier left a worker idle')

    def test_sparse_resume_keeps_verified_chunks_after_a_hole(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(B, '_CHUNK', 4):
            part = str(Path(tmp) / 'v.part')
            Path(part).write_bytes(b'XXXXefghijkl')
            records = [dict(start=s, length=4, sha256=hashlib.sha256(b).hexdigest())
                       for s, b in [(0, b'abcd'), (4, b'efgh'), (8, b'ijkl')]]
            B._save_integrity(part + '.json', dict(total=12, identity='id', chunks=records))
            self.assertEqual(B._resume_records(part, 12, 'id'), records[1:])

    def test_failed_chunk_retains_later_completed_chunks_for_retry(self):
        data = b'abcdefghijkl'
        saved = threading.Event()
        events = []

        def progress(event):
            events.append(event['downloaded'])
            if event['downloaded'] >= 8:
                saved.set()

        def fetch(urls, headers, start, end):
            if start == 0:
                saved.wait(.5)
                raise B.RangeDownloadError('first chunk failed')
            return data[start:end + 1]

        with tempfile.TemporaryDirectory() as tmp, patch.object(B, '_CHUNK', 4):
            part = str(Path(tmp) / 'v.part')
            args = (['cdn'], {}, part, len(data), 'video', progress, 0, 100)
            with patch.object(B, '_fetch_range_retry', side_effect=fetch):
                with self.assertRaises(B.RangeDownloadError):
                    B._download_ranges(*args, window=2)
            self.assertEqual(events[-1], 8)
            with patch.object(B, '_fetch_range_retry', return_value=b'abcd') as fetch_again:
                digest = B._download_ranges(*args, window=2)
            self.assertEqual(fetch_again.call_count, 1)
            self.assertEqual(fetch_again.call_args.args[-2:], (0, 3))
            self.assertEqual(Path(part).read_bytes(), data)
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())

    def test_valid_if_range_response_need_not_repeat_etag(self):
        response = io.BytesIO(b'abc')
        response.status = 206
        response.headers = {'Content-Range': 'bytes 0-2/3', 'Content-Length': '3'}
        with patch.object(B, '_open_url', return_value=response) as opened:
            self.assertEqual(B._fetch_range('https://cdn.test/v', {}, 0, 2,
                                            3, '"object"'), b'abc')
        self.assertEqual(opened.call_args.args[0].get_header('If-range'), '"object"')

    def test_corrupted_disk_write_is_not_accepted_as_finished_download(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(B, '_CHUNK', 4), \
                patch.object(B, '_fetch_range_retry', return_value=b'abcd'):
            part = str(Path(tmp) / 'v.part')

            def corrupt(event):
                if event['downloaded'] == 4:
                    with open(part, 'r+b') as handle:
                        handle.write(b'XXXX')

            with self.assertRaisesRegex(B.RangeIntegrityError, 'SHA256'):
                B._download_ranges(['cdn'], {}, part, 4, 'video', corrupt, 0, 100)

    def test_changed_etag_mirror_is_not_reused_after_cooldown(self):
        bad = B.Probe('https://bad.test/v', 3, True, 'video/mp4', 1e20, 'hash', '"v1"')
        good = B.Probe('https://good.test/v', 3, True, 'video/mp4', 1, 'hash', '"v1"')
        calls = []

        def opened(request, timeout):
            calls.append(request.full_url)
            response = io.BytesIO(b'abc')
            response.status = 206
            response.headers = {'Content-Range': 'bytes 0-2/3', 'Content-Length': '3',
                                'ETag': '"v2"' if request.full_url == bad.url else '"v1"'}
            return response

        with patch.object(B, '_open_url', side_effect=opened):
            pool = B._CDNPool([bad, good])
            self.assertEqual(B._fetch_range_retry(pool, {}, 0, 2), b'abc')
            pool.cooldown.clear()
            self.assertEqual(B._fetch_range_retry(pool, {}, 0, 2), b'abc')
        self.assertEqual(calls.count(bad.url), 1)


if __name__ == '__main__':
    unittest.main()
