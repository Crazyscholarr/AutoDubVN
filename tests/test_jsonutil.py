"""Parser taxonomy for Gemini JSON replies."""
import json
import tempfile
import unittest
from pathlib import Path

from autodub.translate.jsonutil import (
    EMPTY_RESPONSE, EXTRA_TEXT_JSON, INVALID_JSON, MARKDOWN_WRAPPED_JSON,
    MODEL_NON_JSON, TRUNCATED_JSON, VALID_JSON,
    dump_parse_failure, extract_object, inspect_raw, json_translation_complete,
)
from autodub.translate.semantic import arabic_numbers, read_json


class JsonExtract(unittest.TestCase):
    def test_valid_object(self):
        raw = '{"translated_sentences":[{"sentence_id":"s1","source_ids":[1],"text_vi":"A","speaker":null}],"new_entities":[],"updated_summary":"x","warnings":[]}'
        self.assertEqual(inspect_raw(raw)[0], VALID_JSON)
        self.assertTrue(json_translation_complete(raw))
        self.assertEqual(extract_object(raw)["updated_summary"], "x")

    def test_markdown_fence(self):
        raw = '```json\n{"ok":true,"cues":[{"id":1}]}\n```'
        self.assertEqual(inspect_raw(raw)[0], MARKDOWN_WRAPPED_JSON)
        self.assertTrue(extract_object(raw)["ok"])

    def test_prefix_suffix(self):
        raw = 'Sure.\n{"cues":[{"id":1,"text":"A"}]}\nThanks'
        self.assertEqual(inspect_raw(raw)[0], EXTRA_TEXT_JSON)
        self.assertEqual(extract_object(raw)["cues"][0]["id"], 1)

    def test_empty_and_non_json(self):
        self.assertEqual(inspect_raw("")[0], EMPTY_RESPONSE)
        self.assertEqual(inspect_raw("Something went wrong")[0], "UI_ERROR_RESPONSE")
        self.assertEqual(inspect_raw("hello world")[0], MODEL_NON_JSON)

    def test_truncated_and_invalid(self):
        self.assertEqual(inspect_raw('{"translations":[{"id":1,"text":"')[0], TRUNCATED_JSON)
        self.assertEqual(inspect_raw('{"a": 1, "b": }')[0], INVALID_JSON)
        self.assertFalse(json_translation_complete('{"translated_sentences":['))

    def test_wrapping_quotes_and_last_translated_object(self):
        raw = '{"translated_sentences":[{"sentence_id":"s1","source_ids":[1],"text_vi":""A"","speaker":null}],"new_entities":[],"updated_summary":"x","warnings":[]}'
        self.assertEqual(inspect_raw(raw)[0], VALID_JSON)
        self.assertEqual(extract_object(raw)["translated_sentences"][0]["text_vi"], '"A"')
        spliced = (
            '{"translated_sentences":[{"sentence_id":"s1","source_ids":[1],'
            '"text_vi":"cũ","speaker":null}],"new_entities":[],'
            '"updated_summary":"x","warnings":[]}'
            '{"translated_sentences":[{"sentence_id":"s1","source_ids":[1],'
            '"text_vi":"mới","speaker":null}],"new_entities":[],'
            '"updated_summary":"y","warnings":[]}'
        )
        self.assertEqual(extract_object(spliced)["translated_sentences"][0]["text_vi"], "mới")
        self.assertEqual(inspect_raw(spliced)[0], EXTRA_TEXT_JSON)

    def test_two_objects_takes_first_balanced(self):
        raw = '{"a":1}{"b":2}'
        kind, snippet, _ = inspect_raw(raw)
        self.assertEqual(kind, EXTRA_TEXT_JSON)
        self.assertEqual(json.loads(snippet), {"a": 1})

    def test_read_json_rejects_array(self):
        with self.assertRaises(ValueError):
            read_json("[]")

    def test_dump_parse_failure_writes_small_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = str(Path(tmp) / "cache.json")
            dump_parse_failure(cache, TRUNCATED_JSON, '{"a":', "Expecting")
            files = list((Path(tmp) / "_tmp" / "translation-debug").glob("*.json"))
            self.assertEqual(len(files), 1)
            rec = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(rec["kind"], TRUNCATED_JSON)
            self.assertLessEqual(len(rec["first"]), 300)

    def test_arabic_numbers_normalize_comma(self):
        self.assertEqual(arabic_numbers("1.5 và 1,5"), arabic_numbers("1.5 1.5"))
        self.assertEqual(arabic_numbers("三个人"), arabic_numbers(""))


if __name__ == "__main__":
    unittest.main()
