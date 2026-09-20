"""Cancellation and shutdown races that affect the GUI's progress state."""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from autodub.server.job_manager import JobManager, _BoundedExecutor


class JobLifecycleTests(unittest.TestCase):
    def test_pipeline_gate_is_not_recorded_as_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(str(Path(tmp) / "jobs.json"))
            try:
                jid = manager.submit(
                    lambda: {"status": "SYNC_CHECK_FAILED"},
                    name="sync-gate",
                )
                manager._jobs[jid].future.result(timeout=1)
                rows = {row["id"]: row for row in manager.snapshot()}
                self.assertEqual(rows[jid]["status"], "review_required")
            finally:
                manager.shutdown(timeout=2)

    def test_long_running_job_survives_history_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(str(Path(tmp) / "jobs.json"), history_limit=20)
            started, release = threading.Event(), threading.Event()
            try:
                active = manager.submit(lambda: (started.set(), release.wait(5)),
                                        name="long-running", resource="ffmpeg")
                self.assertTrue(started.wait(1))
                for index in range(25):
                    jid = manager.submit(lambda: None, name=f"short-{index}")
                    manager._jobs[jid].future.result(timeout=1)
                self.assertIn(active, [row["id"] for row in manager.snapshot(active_only=True)])
                self.assertLessEqual(len(manager._jobs), 21)
            finally:
                release.set()
                manager.shutdown(timeout=2)

    def test_shutdown_timeout_is_respected_with_tiny_full_queue(self):
        executor = _BoundedExecutor(3, "test", max_queue=1)
        release = threading.Event()
        started = [threading.Event() for _ in range(3)]
        try:
            for signal in started:
                executor.submit(lambda s=signal: (s.set(), release.wait(3)))
                self.assertTrue(signal.wait(1))
            result = []
            thread = threading.Thread(target=lambda: result.append(
                executor.shutdown(timeout=.05)), daemon=True)
            thread.start()
            thread.join(.5)
            self.assertFalse(thread.is_alive(), "shutdown blocked adding stop sentinels")
            self.assertEqual(result, [False])
        finally:
            release.set()
            executor.shutdown(timeout=2)

    def test_second_shutdown_reports_still_running_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(str(Path(tmp) / "jobs.json"))
            started, release = threading.Event(), threading.Event()
            try:
                manager.submit(lambda: (started.set(), release.wait(3)), name="blocked")
                self.assertTrue(started.wait(1))
                self.assertFalse(manager.shutdown(timeout=.01))
                self.assertFalse(manager.shutdown(timeout=.01))
            finally:
                release.set()
                manager.shutdown(timeout=2)


if __name__ == "__main__":
    unittest.main()
