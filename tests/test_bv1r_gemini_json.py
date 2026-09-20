"""Replay BV1r Gemini dumps: local JSON salvage, not live 113-lô."""
import unittest
from pathlib import Path

from autodub.translate import jsonutil as J

ROOT = Path(__file__).resolve().parent / "fixtures" / "bv1r_gemini"
TRIALS = 10

MALFORMED = (
    "1789629544-malformed_json.txt",
    "1789629672-malformed_json.txt",
    "1789629777-malformed_json.txt",
    "1789629852-malformed_json.txt",
    "1789629888-malformed_json.txt",
    "1789630780-malformed_json.txt",
)
REFUSALS = (
    "1789629580-non_json_response.txt",
    "1789629740-model_refusal.txt",
    "1789629869-non_json_response.txt",
)
VALID_INLINE = (
    '{"translated_sentences":[{"sentence_id":"s1","source_ids":[1],'
    '"text_vi":"Xin chào","speaker":null}],"new_entities":[],'
    '"updated_summary":"ok","warnings":[]}'
)


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _malformed_ok(raw: str) -> None:
    obj = J.extract_object(raw)
    rows = obj.get("translated_sentences")
    if not isinstance(rows, list) or not rows:
        raise AssertionError("missing translated_sentences after salvage")
    if not any(str(row.get("text_vi") or "").strip() for row in rows if isinstance(row, dict)):
        raise AssertionError("salvaged object has empty text_vi")


def _refusal_ok(raw: str) -> None:
    if J.classify_response_shape(raw) != J.MODEL_REFUSAL:
        raise AssertionError("expected MODEL_REFUSAL, got %s" % J.classify_response_shape(raw))
    try:
        J.extract_object(raw)
    except J.ResponseShapeError as exc:
        if exc.kind != J.MODEL_REFUSAL:
            raise
    else:
        raise AssertionError("refusal must not extract as JSON")


class Bv1rGeminiJsonTenRun(unittest.TestCase):
    def test_ten_dump_replay_trials(self):
        self.assertTrue(ROOT.is_dir(), "missing fixtures at %s" % ROOT)
        failures = []
        for trial in range(1, TRIALS + 1):
            trial_fail = []
            for name in MALFORMED:
                try:
                    _malformed_ok(_read(name))
                except Exception as exc:
                    trial_fail.append("%s: %s" % (name, exc))
            for name in REFUSALS:
                try:
                    _refusal_ok(_read(name))
                except Exception as exc:
                    trial_fail.append("%s: %s" % (name, exc))
            try:
                _malformed_ok(VALID_INLINE)
            except Exception as exc:
                trial_fail.append("valid_inline: %s" % exc)
            if trial_fail:
                failures.append("trial %d: %s" % (trial, "; ".join(trial_fail)))
        self.assertLessEqual(len(failures), 1, "need ≥9/10 trials PASS; failed:\n" + "\n".join(failures))
        self.assertEqual(len(failures), 0)

    def test_wrapping_quotes_keep_dialogue_marks(self):
        obj = J.extract_object(_read("1789629544-malformed_json.txt"))
        texts = [str(row.get("text_vi") or "") for row in obj["translated_sentences"]]
        self.assertTrue(any(t.strip() for t in texts))
        self.assertTrue(any('"' in t for t in texts), texts[:3])

    def test_restart_uses_last_translated_object(self):
        obj = J.extract_object(_read("1789629672-malformed_json.txt"))
        self.assertTrue(obj["translated_sentences"])
        self.assertNotIn('{"translated_sentences"', obj["translated_sentences"][0]["text_vi"])


class Bv1rLiveDumpCorpus(unittest.TestCase):
    def test_job_debug_dumps_if_present(self):
        root = Path(r"E:\Video\AutoDubVN\output")
        if not root.is_dir():
            self.skipTest("no output folder")
        dumps = []
        for job in root.iterdir():
            if "BV1r" not in job.name:
                continue
            debug = job / "_tmp" / "translation-debug"
            if debug.is_dir():
                dumps.extend(sorted(debug.glob("*.txt")))
        if not dumps:
            self.skipTest("no BV1r translation-debug dumps")
        recovered = refused = still_bad = other = 0
        for path in dumps:
            raw = path.read_text(encoding="utf-8")
            kind = J.classify_response_shape(raw)
            if kind == J.MODEL_REFUSAL:
                refused += 1
                continue
            if kind == J.VALID_JSON_CANDIDATE:
                try:
                    obj = J.extract_object(raw)
                except J.ResponseShapeError:
                    still_bad += 1
                    continue
                rows = obj.get("translated_sentences")
                if isinstance(rows, list) and rows:
                    recovered += 1
                else:
                    other += 1
                continue
            if kind in {J.MALFORMED_JSON, J.TRUNCATED_JSON, J.NON_JSON_RESPONSE}:
                still_bad += 1
            else:
                other += 1
        # Live corpus is evidence, not the 10-run gate. Salvage should clear
        # the quote-malformed majority; leftover is documented in the report.
        self.assertGreaterEqual(recovered + refused, len(dumps) - 1, {
            "dumps": len(dumps), "recovered": recovered, "refused": refused,
            "still_bad": still_bad, "other": other,
        })
        # A physically truncated response has no missing tail to recover.
        # Treat at most one such live artifact as retry evidence; requiring the
        # parser to fabricate the tail would silently invent translation.
        self.assertLessEqual(still_bad, 1)


if __name__ == "__main__":
    unittest.main()
