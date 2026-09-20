"""10-run: empty Gemini host is a slow/virtualized turn, not a 12s abort."""
import unittest
from unittest.mock import patch

from autodub.translate import browser as B
from autodub.translate.jsonutil import classify_exception


TRIALS = 10


class Bv1rExtractionTenRun(unittest.TestCase):
    def setUp(self):
        self.clock = 0
        self.page = type("Page", (), {})()
        self.page.wait_for_timeout = self.advance

    def advance(self, ms):
        self.clock += ms / 1000

    def snap(self, text="", mid="c", generating=False, n=1):
        models = [{"id": mid, "fp": mid, "text": text}]
        if n > 1:
            models = [{"id": "a", "fp": "a", "text": '{"old":1}'},
                      {"id": mid, "fp": mid, "text": text}]
        return {"root": "test", "user_count": n, "model_count": n,
                "generating": generating, "pending": False, "models": models}

    def _wait(self, snapshot, timeout, before=None):
        with patch.object(B, "_snapshot", side_effect=snapshot), \
             patch.object(B, "_reveal_latest", return_value=None), \
             patch.object(B.time, "monotonic", side_effect=lambda: self.clock):
            return B._wait_reply(self.page, 0, timeout,
                                 before_snap=before or {},
                                 trace={"send_ack": True})

    def _slow_first_token(self):
        self.clock = 0

        def snapshot(_):
            if self.clock < 15:
                return self.snap("", generating=False)
            return self.snap('{"ok":true}', generating=False)

        return self._wait(snapshot, 30)

    def _generating_then_empty(self):
        self.clock = 0

        def snapshot(_):
            return self.snap("", generating=self.clock < 1)

        with self.assertRaises(B.GeminiResponseError) as cm:
            self._wait(snapshot, 240)
        self.assertEqual(classify_exception(cm.exception), "RESPONSE_EXTRACTION_FAILURE")
        self.assertLess(self.clock, 20)

    def _placeholder_start_timeout(self):
        self.clock = 0

        def snapshot(_):
            return self.snap("", generating=False)

        with self.assertRaises(B.GeminiResponseError) as cm:
            self._wait(snapshot, 2)
        self.assertEqual(classify_exception(cm.exception), "RESPONSE_START_TIMEOUT")

    def test_ten_extraction_replay_trials(self):
        failures = []
        for trial in range(1, TRIALS + 1):
            trial_fail = []
            try:
                result = self._slow_first_token()
                if result != '{"ok":true}':
                    trial_fail.append("slow_token=%r" % result)
            except Exception as exc:
                trial_fail.append("slow_token: %s" % exc)
            try:
                self._generating_then_empty()
            except Exception as exc:
                trial_fail.append("gen_empty: %s" % exc)
            try:
                self._placeholder_start_timeout()
            except Exception as exc:
                trial_fail.append("placeholder: %s" % exc)
            if trial_fail:
                failures.append("trial %d: %s" % (trial, "; ".join(trial_fail)))
        self.assertLessEqual(len(failures), 1, "need ≥9/10; failed:\n" + "\n".join(failures))
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
