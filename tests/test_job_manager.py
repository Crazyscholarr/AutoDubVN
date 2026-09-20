import json
import os
import tempfile
import threading
import time
import unittest

from autodub.server.job_manager import JobManager, current_cancel_event


class JobManagerTests(unittest.TestCase):
    def test_review_required_is_not_marked_completed_or_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=JobManager(os.path.join(tmp,'jobs.json'))
            ident=manager.submit(lambda: {'status':'REVIEW_REQUIRED'},name='ASR review')
            manager._jobs[ident].future.result(timeout=2)
            manager.shutdown(timeout=2)
            row=next(row for row in manager.snapshot() if row['id']==ident)
            self.assertEqual(row['status'],'review_required')

    def test_gioi_han_worker_va_huy_dung_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(
                os.path.join(tmp, "jobs.json"),
                resource_limits={"ffmpeg": 1, "network": 1, "ai": 1,
                                 "sqlite": 1, "default": 1})
            first_started = threading.Event()
            release_first = threading.Event()
            second_started = threading.Event()
            tokens = {}

            def first():
                tokens["first"] = current_cancel_event()
                first_started.set()
                release_first.wait(2)

            def second():
                tokens["second"] = current_cancel_event()
                second_started.set()

            first_id = manager.submit(first, name="first", resource="ffmpeg")
            second_id = manager.submit(second, name="second", resource="ffmpeg")
            self.assertTrue(first_started.wait(1))
            self.assertFalse(second_started.wait(.05),
                             "FFmpeg pool phải chỉ chạy một job cùng lúc")
            self.assertTrue(manager.cancel(first_id))
            self.assertTrue(tokens["first"].is_set())
            release_first.set()
            self.assertTrue(second_started.wait(1))
            self.assertFalse(tokens["second"].is_set(),
                             "Hủy job thứ nhất không được lan sang job thứ hai")
            manager.shutdown(timeout=2)
            rows = {row["id"]: row for row in manager.snapshot()}
            self.assertEqual(rows[first_id]["status"], "cancelled")
            self.assertEqual(rows[second_id]["status"], "completed")

    def test_khoi_phuc_job_dang_do_thanh_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "jobs.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "jobs": [{
                    "id": "old-job", "name": "Render cũ",
                    "resource": "ffmpeg", "foreground": True,
                    "metadata": {"queue_id": 7}, "status": "running",
                    "submitted_at": time.time() - 20,
                    "started_at": time.time() - 10,
                    "finished_at": 0, "error": "",
                }]}, handle)
            manager = JobManager(path)
            row = manager.snapshot()[-1]
            self.assertEqual(row["id"], "old-job")
            self.assertEqual(row["status"], "interrupted")
            self.assertIn("đóng", row["error"])
            manager.shutdown(timeout=1)


if __name__ == "__main__":
    unittest.main()
