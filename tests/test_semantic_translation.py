"""Offline adversarial tests for sentence ownership, retries, cache and TTS."""
import copy
from contextlib import contextmanager
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autodub import translate, tts
from autodub.semantic import batches, groups, restore_metadata, speech_segments, stamp_display_cues_to_speech
from autodub.srt_utils import Segment, format_srt, seconds_to_timestamp
from autodub.translate.const import system_instruction_for_prompt
from autodub.translate.semantic import (
    ALIGN, TRANSLATE, _MAX_HOLE_TRIES, alignment_validator, read_json,
    translate_semantic, translated_validator,
)


def source():
    return [Segment(101, 0, 2, "因为我"), Segment(102, 2.1, 4, "回来了")]


def translation():
    return dict(translated_sentences=[dict(sentence_id="s1", source_ids=[101, 102],
                                          text_vi="Vì tôi đã về nhà.", speaker=None)],
                new_entities=[], updated_summary="Tôi đã trở về.", warnings=[])


def aligned(segs=None):
    return dict(cues=[dict(id=s.index, start=seconds_to_timestamp(s.start),
                          end=seconds_to_timestamp(s.end), text=t)
                      for s, t in zip(segs or source(), ["Vì tôi", "đã về nhà."])], warnings=[])


class SemanticTests(unittest.TestCase):
    def test_session_failure_preserves_completed_cache_for_resume(self):
        from autodub.translate.browser import GeminiResponseError
        segs = source()
        def good(cue):
            return json.dumps(dict(translated_sentences=[dict(sentence_id='s1',source_ids=[cue],
                text_vi='Tôi về nhà.',speaker=None)],new_entities=[],updated_summary='Đã về.',warnings=[]))
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'cache.json')
            first = Mock(side_effect=[good(101), GeminiResponseError('BROWSER_SESSION_UNHEALTHY','RECOVERY_EXHAUSTED',{'code':'1095'})])
            with patch('autodub.translate.semantic.batches',return_value=[(0,1),(1,2)]):
                with self.assertRaises(translate.TranslationIncomplete) as caught:
                    translate_semantic(segs,first,{'vi_beautify':False},cache_path=path)
                self.assertEqual(caught.exception.failed,[2])
                resumed = Mock(return_value=good(102))
                translate_semantic(source(),resumed,{'vi_beautify':False},cache_path=path)
                self.assertEqual(resumed.call_count,1)

    def test_exhausted_browser_recovery_stops_before_next_batch(self):
        from autodub.translate.browser import GeminiResponseError
        segs = [Segment(i+1, i*2, i*2+1, '你好') for i in range(12)]
        ask = Mock(side_effect=GeminiResponseError('BROWSER_SESSION_UNHEALTHY', 'RECOVERY_EXHAUSTED', {'code':'1095'}))
        with patch('autodub.translate.semantic.batches', return_value=[(i,i+1) for i in range(12)]):
            with self.assertRaises(translate.TranslationIncomplete) as cm:
                translate_semantic(segs, ask, {})
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(cm.exception.failed, list(range(1,13)))

    def test_circuit_breaker_counts_unattempted_batches(self):
        from autodub.translate.browser import GeminiResponseError
        segs = [Segment(i+1, i*2, i*2+1, '你好') for i in range(12)]
        ask = Mock(side_effect=GeminiResponseError('RESPONSE_START_TIMEOUT', 'WAITING', {}))
        with patch('autodub.translate.semantic.batches', return_value=[(i,i+1) for i in range(12)]):
            with self.assertRaises(translate.TranslationIncomplete) as cm:
                translate_semantic(segs, ask, {})
        self.assertEqual(ask.call_count, 5)
        self.assertEqual(cm.exception.failed, list(range(1,13)))
        self.assertEqual(cm.exception.total, 12)

    def test_end_to_end_cache_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "cache.json")
            ask = Mock(side_effect=[json.dumps(translation()), json.dumps(aligned())])
            segs = source()
            translate_semantic(segs, ask, {"vi_beautify": False}, cache_path=path, identity="model-a")
            self.assertEqual(ask.call_count, 1)
            self.assertEqual([(s.index, s.start, s.end) for s in segs], [(101, 0, 2), (102, 2.1, 4)])
            self.assertEqual(segs[0].semantic_group, segs[1].semantic_group)
            again = source()
            translate_semantic(again, Mock(side_effect=AssertionError("cache miss")),
                               {"vi_beautify": False}, cache_path=path, identity="model-a")
            self.assertEqual([s.text for s in segs], [s.text for s in again])
            for s in again:
                s.semantic_group = None
            self.assertTrue(restore_metadata(again, path))
            shifted = copy.deepcopy(segs)
            for s in shifted:
                s.start += 5.0
                s.end += 5.0
                s.semantic_group = None
            self.assertTrue(
                restore_metadata(shifted, path),
                "TTS đổi clock nhưng giữ nguyên text phải phục hồi được metadata",
            )
            again[0].text += " sửa"
            self.assertTrue(restore_metadata(again, path),
                            "vá chữ Việt tại chỗ không được làm mất semantic_group")
            again[0].end += 0.5
            self.assertFalse(restore_metadata(again, path))

    def test_cache_invalidates_model_and_source_and_glossary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "cache")
            cfg = {"vi_beautify": False}
            for model, text, extra in [("a", "因为我", {}), ("b", "因为我", {}),
                                       ("b", "现在我", {}), ("b", "现在我", {"七月": {"vi": "Thất Nguyệt", "locked": True}})]:
                segs = source()
                segs[0].text = text
                ask = Mock(side_effect=[json.dumps(translation()), json.dumps(aligned())])
                translate_semantic(segs, ask, {**cfg, "glossary": extra}, cache_path=path, identity=model)
                self.assertEqual(ask.call_count, 1)

    def test_bad_translation_retries_once_and_keeps_source(self):
        segs, ask = [source()[0]], Mock(return_value="[]")
        with self.assertRaises(translate.TranslationIncomplete):
            translate_semantic(segs, ask, {})
        self.assertEqual(ask.call_count, 2 * _MAX_HOLE_TRIES)
        self.assertEqual([s.text for s in segs], [source()[0].text])
        self.assertIn("SCHEMA_FAILURE", ask.call_args.args[0])
        self.assertIn("Sửa lỗi validation", ask.call_args.args[0])

    def test_send_failure_retries_same_prompt_not_validation_wrap(self):
        n = {"i": 0}

        def ask(prompt):
            n["i"] += 1
            self.assertNotIn("Sửa lỗi validation", prompt)
            self.assertNotIn("ô nhập vẫn còn nội dung", prompt)
            if n["i"] == 1:
                raise RuntimeError(
                    "không gửi được sau 4 lần: đã gõ xong nhưng chưa gửi được "
                    "(ô nhập vẫn còn nội dung)")
            if n["i"] == 2:
                return json.dumps(translation())
            return json.dumps(aligned())

        segs = source()
        translate_semantic(segs, ask, {"vi_beautify": False})
        self.assertEqual(n["i"], 2)
        self.assertEqual(" ".join(s.text for s in segs), "Vì tôi đã về nhà.")

    def test_bad_alignment_falls_back_without_loss(self):
        ask = Mock(side_effect=[json.dumps(translation()), "[]", "[]"])
        segs = source()
        translate_semantic(segs, ask, {"vi_beautify": False})
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(" ".join(s.text for s in segs), "Vì tôi đã về nhà.")

    def test_cancel_propagates(self):
        ask = Mock(side_effect=InterruptedError)
        with self.assertRaises(InterruptedError):
            translate_semantic(source(), ask, {})
        self.assertEqual(ask.call_count, 1)

    def test_translation_ownership_adversaries(self):
        check = translated_validator(source(), {101: 0, 102: 0}, {}, "han_viet")
        for refs in ([101], [102, 101], [101, 101, 102], [100, 101, 102], [True, 102]):
            with self.subTest(refs=refs):
                obj = translation()
                obj["translated_sentences"][0]["source_ids"] = refs
                with self.assertRaises(ValueError):
                    check(obj)

    def test_translation_cannot_cross_speaker_group(self):
        with self.assertRaisesRegex(ValueError, "boundary"):
            translated_validator(source(), {101: 0, 102: 1}, {}, "han_viet")(translation())

    def test_translation_cannot_drop_digits(self):
        segs = source()
        segs[0].text += " 12"
        with self.assertRaisesRegex(ValueError, "số"):
            translated_validator(segs, {101: 0, 102: 0}, {}, "han_viet")(translation())

    def test_locked_name_is_required(self):
        segs = source()
        segs[0].text += "七月"
        notes = []
        obj = translation()
        with self.assertRaisesRegex(ValueError, 'glossary'):
            translated_validator(segs, {101: 0, 102: 0},
                                 {"七月": {"vi": "Thất Nguyệt", "locked": True}},
                                 "han_viet", notes)(obj)
        self.assertEqual(obj['translated_sentences'][0]['text_vi'], 'Vì tôi đã về nhà.')

    def test_uncertain_name_is_skipped_not_batch_failed(self):
        segs, obj = source(), translation()
        segs[0].text += "七月"
        notes = []
        obj["new_entities"] = [dict(source="七月", vi="Thất Nguyệt", type="person",
                                    confidence=.4, needs_review=True)]
        translated_validator(segs, {101: 0, 102: 0}, {}, "han_viet", notes)(obj)
        self.assertTrue(any("uncertain_skipped" in n for n in notes))
        obj["new_entities"][0]["vi"] = "七月"
        obj["translated_sentences"][0]["text_vi"] = "七月 đã về nhà rồi."
        translated_validator(segs, {101: 0, 102: 0}, {}, "han_viet")(obj)

    def test_residue_function_word_is_not_a_retained_name(self):
        segs, obj = source(), translation()
        segs[0].text += "迟迟"
        notes = []
        obj["new_entities"] = [dict(source="迟迟", vi="迟迟", type="person",
                                    confidence=.4, needs_review=True)]
        obj["translated_sentences"][0]["text_vi"] = "Hắn 迟迟 không về nhà."
        translated_validator(segs, {101: 0, 102: 0}, {}, "han_viet", notes)(obj)
        self.assertTrue(any("not_a_name" in n for n in notes))
        self.assertEqual(obj["translated_sentences"][0]["text_vi"],
                         "Hắn mãi không về nhà.")
        self.assertNotIn("迟迟", obj["translated_sentences"][0]["text_vi"])

    def test_unexpected_entity_is_skipped(self):
        notes = []
        obj = translation()
        obj["new_entities"] = [dict(source="伟人", vi="vĩ nhân", type="person",
                                    confidence=.4, needs_review=True)]
        translated_validator(source(), {101: 0, 102: 0}, {}, "han_viet", notes)(obj)
        self.assertTrue(any('entity="伟人" reason=unexpected' in n for n in notes))

    def test_unused_new_entity_does_not_fail_batch(self):
        segs, obj = source(), translation()
        segs[0].text += "纷纷"
        notes = []
        obj["new_entities"] = [dict(source="纷纷", vi="Phân Phân", type="person",
                                    confidence=.95, needs_review=False)]
        translated_validator(segs, {101: 0, 102: 0}, {}, "han_viet", notes)(obj)
        self.assertTrue(any("new_name_not_enforced" in n for n in notes))
        notes = []
        obj = translation()
        segs = source()
        segs[0].text += "瞬间"
        obj["new_entities"] = [dict(source="瞬间", vi="nháy mắt", type="adverb",
                                    confidence=.99, needs_review=False)]
        translated_validator(segs, {101: 0, 102: 0}, {}, "han_viet", notes)(obj)
        self.assertTrue(any("not_a_name" in n for n in notes))

    def test_leftover_locked_name_is_replaced(self):
        segs, obj = source(), translation()
        segs[0].text += "清风"
        obj["translated_sentences"][0]["text_vi"] = "Vì 清风 đã về nhà."
        glossary = {"清风": {"vi": "Thanh Phong", "locked": True, "type": "person"}}
        translated_validator(segs, {101: 0, 102: 0}, glossary, "han_viet")(obj)
        self.assertEqual(obj["translated_sentences"][0]["text_vi"], "Vì Thanh Phong đã về nhà.")

    def test_locked_name_accepts_case_and_spacing(self):
        segs, obj = source(), translation()
        segs[0].text += "七月"
        obj["translated_sentences"][0]["text_vi"] = "Vì thất nguyệt đã về nhà."
        glossary = {"七月": {"vi": "Thất Nguyệt", "locked": True, "type": "person"}}
        translated_validator(segs, {101: 0, 102: 0}, glossary, "han_viet")(obj)
        self.assertEqual(obj["translated_sentences"][0]["text_vi"], "Vì Thất Nguyệt đã về nhà.")

    def test_one_cue_missing_locked_name_needs_semantic_repair(self):
        segs = [Segment(1, 0, 2, "去五道中学报到")]
        obj = dict(translated_sentences=[dict(sentence_id="s1", source_ids=[1],
                                              text_vi="Đi báo danh đi.", speaker=None)],
                   new_entities=[], updated_summary="ok", warnings=[])
        notes = []
        glossary = {"五道中学": {"vi": "Trường Trung học Ngũ Đạo", "locked": True, "type": "location"}}
        with self.assertRaisesRegex(ValueError, 'glossary'):
            translated_validator(segs, {1: 0}, glossary, "han_viet", notes)(obj)
        self.assertEqual(obj['translated_sentences'][0]['text_vi'], 'Đi báo danh đi.')

    def test_one_cue_retries_hole_in_place(self):
        segs = [Segment(1, 0, 2, "因为我回来了")]
        n = {"i": 0}

        def ask(prompt):
            n["i"] += 1
            body = prompt.split("INPUT_JSON:\n", 1)[1]
            raw = body.split("\nTASK_CLARIFICATION:", 1)[0]
            raw = raw.split("\nFORMAT_REPAIR:", 1)[0]
            raw = raw.split("\nSửa lỗi validation:", 1)[0]
            payload = json.loads(raw)
            if "source_cues" in payload:
                return json.dumps(dict(cues=[dict(id=x["id"], start=x["start"], end=x["end"],
                    text="Vì tôi đã về nhà.") for x in payload["source_cues"]], warnings=[]))
            vi = "Vì 回山门 tôi đã về." if n["i"] <= 2 else "Vì tôi đã về nhà."
            return json.dumps(dict(translated_sentences=[dict(sentence_id="s1", source_ids=[1],
                text_vi=vi, speaker=None)], new_entities=[], updated_summary="ok", warnings=[]))

        translate_semantic(segs, ask, {"vi_beautify": False})
        self.assertEqual(segs[0].text, "Vì tôi đã về nhà.")
        self.assertGreaterEqual(n["i"], 3)

    def test_model_repairs_missing_name_in_its_correct_grammatical_role(self):
        segs = [Segment(i + 1, float(i), float(i) + 0.9, "去五道中学报到") for i in range(3)]
        glossary = {"五道中学": {"vi": "Trường Trung học Ngũ Đạo", "locked": True, "type": "location"}}

        def ask(prompt):
            body = prompt.split("INPUT_JSON:\n", 1)[1]
            raw = body.split("\nTASK_CLARIFICATION:", 1)[0]
            raw = raw.split("\nFORMAT_REPAIR:", 1)[0]
            raw = raw.split("\nSửa lỗi validation:", 1)[0]
            payload = json.loads(raw)
            if "source_cues" in payload:
                return json.dumps(dict(cues=[dict(id=x["id"], start=x["start"], end=x["end"],
                    text=x.get("text") or "Đi báo danh đi.") for x in payload["source_cues"]], warnings=[]))
            refs = [x["id"] for x in payload["target_cues"]]
            return json.dumps(dict(
                translated_sentences=[dict(sentence_id="s%d" % x, source_ids=[x],
                                           text_vi=("Đến Trường Trung học Ngũ Đạo báo danh."
                                                    if 'Sửa lỗi validation' in prompt else "Đi báo danh đi."),
                                           speaker=None) for x in refs],
                new_entities=[], updated_summary="ok", warnings=[]))

        translate_semantic(segs, ask, {
            "vi_beautify": False, "semantic_batch_cues": 20,
            "semantic_batch_seconds": 60, "glossary": glossary,
        })
        self.assertEqual([s.index for s in segs], [1, 2, 3])
        self.assertTrue(all(s.text == "Đến Trường Trung học Ngũ Đạo báo danh." for s in segs))

    def test_common_or_short_locked_token_is_not_required(self):
        segs, obj = source(), translation()
        segs[0].text += "红光圣"
        glossary = {
            "红光": {"vi": "ánh sáng đỏ", "locked": True, "type": "common"},
            "圣": {"vi": "Thánh", "locked": True, "type": "symbol"},
        }
        translated_validator(segs, {101: 0, 102: 0}, glossary, "han_viet")(obj)

    def test_locked_name_conflict_is_skipped_not_failed(self):
        segs, obj = source(), translation()
        segs[0].text += "七月"
        obj["translated_sentences"][0]["text_vi"] = "Vì Thất Nguyệt đã về nhà."
        obj["new_entities"] = [dict(source="七月", vi="Thất Nguyệt khác", type="person",
                                    confidence=.99, needs_review=False)]
        notes = []
        translated_validator(segs, {101: 0, 102: 0},
                             {"七月": {"vi": "Thất Nguyệt", "locked": True}}, "han_viet", notes)(obj)
        self.assertTrue(any("locked_unchanged" in n for n in notes))

    def test_leftover_cjk_without_glossary_still_fails(self):
        obj = translation()
        obj["translated_sentences"][0]["text_vi"] = "Vì 回山门 tôi đã về nhà."
        with self.assertRaisesRegex(ValueError, "chữ nguồn"):
            translated_validator(source(), {101: 0, 102: 0}, {}, "han_viet")(obj)

    def test_leftover_name_in_source_is_kept_locally(self):
        segs, obj = source(), translation()
        segs[0].text += "七月"
        obj["translated_sentences"][0]["text_vi"] = "Vì 七月 tôi đã về nhà."
        obj["new_entities"] = [dict(source="七月", vi="七月", type="uncertain_name",
                                    confidence=0.0, needs_review=True)]
        notes = []
        translated_validator(segs, {101: 0, 102: 0}, {}, "han_viet", notes)(obj)
        self.assertIn("七月", obj["translated_sentences"][0]["text_vi"])

    def test_listed_name_persists_to_tts_metadata(self):
        segs = [Segment(1, 0.0, 2.0, "七月回来了")]

        def ask(_prompt):
            return json.dumps({
                "translated_sentences": [{
                    "sentence_id": "s1",
                    "source_ids": [1],
                    "text_vi": "七月 đã về rồi.",
                    "speaker": None,
                }],
                "new_entities": [{
                    "source": "七月", "vi": "七月", "type": "uncertain_name",
                    "confidence": 0.0, "needs_review": True,
                }],
                "updated_summary": "Thất Nguyệt đã trở về.",
                "warnings": [],
            }, ensure_ascii=False)

        translate_semantic(
            segs, ask,
            {"vi_beautify": False, "semantic_batch_cues": 1},
        )
        self.assertEqual(segs[0].allowed_source_names, ("七月",))
        from autodub.translate.cjk_residue import leftover_cjk_indices
        self.assertEqual(leftover_cjk_indices(segs), [])

    def test_isolated_common_verb_in_source_is_not_a_name(self):
        segs, obj = source(), translation()
        segs[0].text += "决定"
        obj["translated_sentences"][0]["text_vi"] = "Vì 决定 tôi đã về nhà."
        with self.assertRaisesRegex(ValueError, "chữ nguồn"):
            translated_validator(segs, {101: 0, 102: 0}, {}, "han_viet")(obj)

    def test_keep_source_name_cannot_leak_to_another_sentence(self):
        segs = [
            Segment(1, 0.0, 1.0, "七月来了"),
            Segment(2, 1.0, 2.0, "他回家了"),
        ]
        obj = {
            "translated_sentences": [
                {"sentence_id": "s1", "source_ids": [1],
                 "text_vi": "七月 đã đến.", "speaker": None},
                {"sentence_id": "s2", "source_ids": [2],
                 "text_vi": "七月 đã về nhà.", "speaker": None},
            ],
            "new_entities": [{
                "source": "七月", "vi": "七月", "type": "person",
                "confidence": 0.4, "needs_review": True,
            }],
            "updated_summary": "Hai người trở về.",
            "warnings": [],
        }
        with self.assertRaisesRegex(ValueError, "chữ nguồn"):
            translated_validator(
                segs, {1: 0, 2: 1}, {}, "han_viet"
            )(obj)

    def test_glued_leftover_in_source_is_not_a_name(self):
        """Han jammed into a Vietnamese word is leftover, never keep_source."""
        segs, obj = source(), translation()
        segs[0].text += "照管"
        obj["translated_sentences"][0]["text_vi"] = (
            "Vì tình huống照管này tôi đã về nhà.")
        with self.assertRaisesRegex(ValueError, "chữ nguồn"):
            translated_validator(segs, {101: 0, 102: 0}, {}, "han_viet")(obj)

    def test_isolated_non_july_name_in_source_is_still_kept(self):
        segs, obj = source(), translation()
        segs[0].text += "照管"
        obj["translated_sentences"][0]["text_vi"] = "Vì 照管 tôi đã về nhà."
        obj["new_entities"] = [dict(source="照管", vi="照管", type="uncertain_name",
                                    confidence=0.0, needs_review=True)]
        notes = []
        translated_validator(segs, {101: 0, 102: 0}, {}, "han_viet", notes)(obj)
        self.assertIn("照管", obj["translated_sentences"][0]["text_vi"])

    def test_missing_locked_name_rejected_on_multi_cue_batch(self):
        segs = source()
        segs[0].text += "五道中学"
        obj = translation()
        notes = []
        glossary = {"五道中学": {"vi": "Trường Trung học Ngũ Đạo", "locked": True, "type": "location"}}
        with self.assertRaisesRegex(ValueError, 'glossary'):
            translated_validator(segs, {101: 0, 102: 0}, glossary, "han_viet", notes)(obj)
        self.assertEqual(obj['translated_sentences'][0]['text_vi'], 'Vì tôi đã về nhà.')

    def test_unused_new_entity_is_not_written_to_glossary(self):
        segs = [Segment(i + 1, i * 2, i * 2 + 1.9, "纷纷来了") for i in range(25)]
        seen = []

        def ask(prompt):
            payload = json.loads(prompt.split("INPUT_JSON:\n", 1)[1])
            if "target_cues" in payload:
                seen.append(payload)
                refs = [x["id"] for x in payload["target_cues"]]
                return json.dumps(dict(translated_sentences=[dict(sentence_id=str(x), source_ids=[x],
                                  text_vi="Đã đến rồi.", speaker=None) for x in refs],
                                  new_entities=[dict(source="纷纷", vi="Phân Phân", type="person",
                                      confidence=.98, needs_review=False)],
                                  updated_summary="ok", warnings=[]))
            return json.dumps(dict(cues=[dict(id=x["id"], start=x["start"], end=x["end"],
                                              text="Đã đến rồi.")
                                      for x in payload["source_cues"]], warnings=[]))

        translate_semantic(segs, ask, {"vi_beautify": False, "semantic_batch_cues": 2,
                                       "semantic_batch_seconds": 60})
        self.assertGreater(len(seen), 1)
        self.assertNotIn("纷纷", seen[1].get("glossary", {}))

    def test_arabic_numbers_allow_extra_and_decimal_comma(self):
        segs = source()
        segs[0].text += " 1.5"
        obj = translation()
        obj["translated_sentences"][0]["text_vi"] = "Vì tôi đã về nhà 1,5 rồi."
        translated_validator(segs, {101: 0, 102: 0}, {}, "han_viet")(obj)
        segs[0].text += " 12"
        with self.assertRaisesRegex(ValueError, "12"):
            translated_validator(segs, {101: 0, 102: 0}, {}, "han_viet")(obj)

    def test_json_extracts_fences_and_rejects_bad_payloads(self):
        self.assertEqual(read_json('```json\n{"ok": true}\n```')["ok"], True)
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '[]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                read_json(raw)

    def test_alignment_rejects_clock_token_and_whitespace_changes(self):
        check = alignment_validator(source(), translation()["translated_sentences"])
        for field, value in [("id", True), ("start", "00:00:00,001"), ("text", "Vì  tôi"),
                             ("text", "Vì tôi không"), ("text", " Vì tôi"), ("text", "**Vì tôi**")]:
            with self.subTest(field=field, value=value):
                obj = aligned()
                obj["cues"][0][field] = value
                with self.assertRaises(ValueError):
                    check(obj)

    def test_quality_retry_and_fallback(self):
        ask = Mock(side_effect=[json.dumps(translation()), "[]"])
        segs = source()
        translate_semantic(segs, ask, {"vi_beautify": "auto"})
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(" ".join(s.text for s in segs), "Vì tôi đã về nhà.")

    def test_quality_can_move_subject(self):
        q = aligned()
        q["cues"][0]["text"], q["cues"][1]["text"] = "Vì", "tôi đã về nhà."
        q.update(changed=True, issues=[])
        ask = Mock(side_effect=[json.dumps(translation()), json.dumps(q)])
        segs = source()
        translate_semantic(segs, ask, {"vi_beautify": "auto"})
        self.assertEqual([s.text for s in segs], ["Vì", "tôi đã về nhà."])

    def test_group_limits_and_context_ownership(self):
        segs = [Segment(i, i * 2, i * 2 + 1.9, "你好") for i in range(120)]
        spans = batches(segs)
        self.assertEqual([i for a, b in spans for i in range(a, b)], list(range(120)))
        self.assertTrue(all(b-a <= 30 for a, b in spans))
        self.assertTrue(all(segs[b-1].end-segs[a].start <= 60 for a, b in spans))

    def test_boundary_types(self):
        for attr, value in [("speaker", "other"), ("scene", "scene2"), ("chapter", "chapter2"),
                            ("hard_boundary", True)]:
            segs = source()
            if attr != "hard_boundary":
                setattr(segs[0], attr, "first")
            setattr(segs[1], attr, value)
            self.assertEqual(groups(segs), [(0, 1), (1, 2)])
        segs = source()
        self.assertEqual(groups(segs, {"max_group_gap_ms": 50}), [(0, 1), (1, 2)])

    def test_tts_group_does_not_modify_display_or_cross_voice(self):
        segs = source()
        for s, text in zip(segs, ["Vì tôi", "đã về nhà."]):
            s.text = text
            s.semantic_group = "s1"
        before = format_srt(segs)
        units = speech_segments(segs)
        self.assertEqual(len(units), 1)
        self.assertEqual((units[0].start, units[0].end, units[0].text), (0, 4, "Vì tôi đã về nhà."))
        units[0].placed_start, units[0].voice_duration = .5, 6
        self.assertEqual(format_srt(segs, use_placed=True), before)
        stamp_display_cues_to_speech(segs, units)
        self.assertAlmostEqual(segs[0].start, .5, places=3)
        self.assertAlmostEqual(segs[-1].end, 6.5, places=3)
        self.assertAlmostEqual(segs[0].end, segs[1].start, places=3)
        segs[0].voice, segs[1].voice = "male", "female"
        self.assertEqual(len(speech_segments(segs)), 2)

    def test_api_dispatch_uses_semantic_system(self):
        with patch.object(translate, "_api_call", side_effect=[json.dumps(translation()), json.dumps(aligned())]) as ask:
            translate.translate_segments(source(), "dummy", translation_cfg={"vi_beautify": False})
        self.assertEqual(ask.call_count, 1)
        self.assertIn("target_cues", ask.call_args_list[0].args[0])

    def test_summary_glossary_and_read_only_context_flow_across_batches(self):
        segs = [Segment(i+1, i*2, i*2+1.9, "七月来了") for i in range(25)]
        seen = []
        def ask(prompt):
            payload = json.loads(prompt.split("INPUT_JSON:\n")[1])
            if "target_cues" in payload:
                seen.append(payload)
                refs = [x["id"] for x in payload["target_cues"]]
                return json.dumps(dict(translated_sentences=[dict(sentence_id=str(x), source_ids=[x],
                                  text_vi="Thất Nguyệt đã đến.", speaker=None) for x in refs],
                                  new_entities=[] if payload["glossary"] else [dict(source="七月", vi="Thất Nguyệt",
                                      type="person", confidence=.98, needs_review=False)],
                                  updated_summary="Thất Nguyệt đã đến.", warnings=[]))
            return json.dumps(dict(cues=[dict(id=x["id"],start=x["start"],end=x["end"],text="Thất Nguyệt đã đến.")
                                      for x in payload["source_cues"]],warnings=[]))
        translate_semantic(segs, ask, {"vi_beautify": False, 'semantic_batch_cues':12,
                                       'semantic_context_cues':2})
        self.assertGreater(len(seen), 1)
        owned = [r["id"] for p in seen for r in p["target_cues"]]
        self.assertEqual(owned, list(range(1,26)))
        self.assertTrue(seen[1]["glossary"]["七月"]["locked"])
        self.assertEqual(seen[1]["previous_summary"], "Thất Nguyệt đã đến.")
        self.assertEqual(len(seen[0]["context_after"]), 2)
        self.assertEqual(len(seen[1]["context_before"]), 2)
        self.assertTrue(all(r["source_text"]=="七月来了" for p in seen for r in p["context_before"]))

    def test_translate_payload_omits_context_clocks(self):
        seen = []

        def ask(prompt):
            body = prompt.split("INPUT_JSON:\n", 1)[1]
            raw = body.split("\nTASK_CLARIFICATION:", 1)[0]
            raw = raw.split("\nFORMAT_REPAIR:", 1)[0]
            raw = raw.split("\nSửa lỗi validation:", 1)[0]
            payload = json.loads(raw)
            seen.append((payload, raw))
            refs = [x["id"] for x in payload["target_cues"]]
            return json.dumps(dict(translated_sentences=[dict(sentence_id="s%d" % x, source_ids=[x],
                text_vi="Đã đến.", speaker=None) for x in refs],
                new_entities=[], updated_summary="ok", warnings=[]))

        segs = [Segment(i + 1, float(i), float(i) + 0.8, "来了") for i in range(8)]
        translate_semantic(segs, ask, {"vi_beautify": False, "semantic_context_cues": 2})
        payload, raw = seen[0]
        self.assertNotIn("start", payload["target_cues"][0])
        self.assertNotIn("end", payload["target_cues"][0])
        self.assertIn("semantic_group", payload["target_cues"][0])
        self.assertIn("duration", payload["target_cues"][0])
        if payload["context_after"]:
            self.assertEqual(set(payload["context_after"][0]), {"id", "source_text"})
        self.assertNotIn("film_hint", payload["project_style"])
        self.assertNotIn("\n  ", raw)

    def test_owned_speech_group_ignores_max_cues(self):
        segs = [Segment(i + 1, float(i), float(i) + 0.8, "chữ %d" % i) for i in range(8)]
        for s in segs:
            s.semantic_group = "s1"
        cfg = {"semantic_group_max_cues": 6}
        self.assertEqual(len(speech_segments(segs, cfg)), 1)
        self.assertGreater(len(groups(segs, cfg)), 1)

    def test_locked_name_cannot_be_split_during_alignment(self):
        obj = translation()
        obj["translated_sentences"][0]["text_vi"] = "Thất Nguyệt đã về nhà."
        out = aligned()
        out["cues"][0]["text"], out["cues"][1]["text"] = "Thất", "Nguyệt đã về nhà."
        with self.assertRaisesRegex(ValueError,"glossary"):
            alignment_validator(source(),obj["translated_sentences"],
                                {"七月":{"vi":"Thất Nguyệt","locked":True}})(out)

    def test_asr_punctuation_inside_word_is_soft(self):
        segs=[Segment(1,0,1,"他整。"),Segment(2,1.1,2,"晚没睡")]
        self.assertEqual(groups(segs),[(0,2)])

    def test_real_bad_break_examples_and_vocative(self):
        from autodub.vi_reflow import bad_break_score
        for a,b in [("Chúng còn lớn","lên."),("Hãy sống","sót."),("Chúc mọi người may","mắn.")]:
            self.assertGreaterEqual(bad_break_score(a,b),8)
        self.assertEqual(bad_break_score("Anh!","Tôi về rồi."),0)

    def test_browser_dispatch_uses_same_semantic_contract(self):
        ask = Mock(side_effect=[json.dumps(translation()), json.dumps(aligned())])
        @contextmanager
        def session(*args, **kwargs):
            self.assertEqual(kwargs["reset_every"], 10)
            yield ask
        with patch.dict("sys.modules", {"playwright": Mock(), "playwright.sync_api": Mock()}):
            with patch("autodub.translate.browser.phien_gemini_trinh_duyet", session):
                segs = source()
                translate.translate_via_browser(segs, "unused-test-profile", translation_cfg={"vi_beautify": False})
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(" ".join(s.text for s in segs), "Vì tôi đã về nhà.")

    def test_truncated_batch_splits_and_keeps_order(self):
        segs = [Segment(i + 1, i * 2, i * 2 + 1.9, "你好朋友们") for i in range(20)]
        n = {"i": 0}

        def ask(prompt):
            n["i"] += 1
            body = prompt.split("INPUT_JSON:\n", 1)[1]
            payload = json.loads(body.split("\nReturn", 1)[0])
            refs = [x["id"] for x in payload["target_cues"]]
            if len(refs) > 10 and n["i"] <= 2:
                return '{"translated_sentences":['
            return json.dumps(dict(
                translated_sentences=[
                    dict(sentence_id="s%d" % x, source_ids=[x],
                         text_vi="Xin chào các bạn.", speaker=None)
                    for x in refs],
                new_entities=[], updated_summary="ok", warnings=[]))

        cfg = {
            "vi_beautify": False, "semantic_batch_cues": 20,
            "semantic_batch_seconds": 60,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "split-cache.json")
            original = copy.deepcopy(segs)
            translate_semantic(segs, ask, cfg, cache_path=path)
            translate_semantic(original, Mock(side_effect=AssertionError("split cache miss")),
                               cfg, cache_path=path)
            self.assertEqual([s.text for s in original], [s.text for s in segs])
        self.assertEqual([s.index for s in segs], list(range(1, 21)))
        self.assertTrue(all("Xin chào" in s.text for s in segs))
        self.assertGreaterEqual(n["i"], 4)

    def test_refusal_batch_splits_instead_of_dropping_cues(self):
        segs = [Segment(i + 1, i * 1.0, i * 1.0 + 0.9, "因为我") for i in range(12)]
        refusal = "Tôi chỉ là một mô hình ngôn ngữ, nên không thể trợ giúp về điều đó."
        n = {"i": 0}

        def ask(prompt):
            n["i"] += 1
            body = prompt.split("INPUT_JSON:\n", 1)[1]
            raw = body.split("\nTASK_CLARIFICATION:", 1)[0]
            raw = raw.split("\nFORMAT_REPAIR:", 1)[0]
            payload = json.loads(raw)
            refs = [x["id"] for x in payload["target_cues"]]
            if len(refs) > 8:
                return refusal
            return json.dumps(dict(
                translated_sentences=[
                    dict(sentence_id="s%d" % x, source_ids=[x],
                         text_vi="Vì tôi đây rồi.", speaker=None)
                    for x in refs],
                new_entities=[], updated_summary="ok", warnings=[]))

        translate_semantic(segs, ask, {
            "vi_beautify": False, "semantic_batch_cues": 20,
            "semantic_batch_seconds": 60,
        })
        self.assertEqual([s.index for s in segs], list(range(1, 13)))
        self.assertTrue(all("Vì tôi" in s.text for s in segs))
        self.assertGreaterEqual(n["i"], 4)

    def test_semantic_gate_splits_small_batches(self):
        segs = [Segment(i + 1, i * 1.0, i * 1.0 + 0.9, "因为我") for i in range(6)]
        n = {"i": 0}

        def ask(prompt):
            n["i"] += 1
            body = prompt.split("INPUT_JSON:\n", 1)[1]
            raw = body.split("\nTASK_CLARIFICATION:", 1)[0]
            raw = raw.split("\nFORMAT_REPAIR:", 1)[0]
            raw = raw.split("\nSửa lỗi validation:", 1)[0]
            payload = json.loads(raw)
            refs = [x["id"] for x in payload.get("target_cues", [])]
            if "source_cues" in payload:
                return json.dumps(dict(cues=[dict(id=x["id"], start=x["start"], end=x["end"],
                    text="Vì tôi đây rồi.") for x in payload["source_cues"]], warnings=[]))
            vi = "Vì 回山门 tôi." if len(refs) > 3 else "Vì tôi đây rồi."
            return json.dumps(dict(
                translated_sentences=[
                    dict(sentence_id="s%d" % x, source_ids=[x],
                         text_vi=vi, speaker=None)
                    for x in refs],
                new_entities=[], updated_summary="ok", warnings=[]))

        translate_semantic(segs, ask, {
            "vi_beautify": False, "semantic_batch_cues": 20,
            "semantic_batch_seconds": 60,
        })
        self.assertEqual([s.index for s in segs], list(range(1, 7)))
        self.assertTrue(all("Vì tôi" in s.text for s in segs))
        self.assertGreaterEqual(n["i"], 3)

    def test_semantic_transport_does_not_switch_model_silently(self):
        from autodub.translate import api
        with patch.object(translate, "_openai_compatible_call", side_effect=RuntimeError("timed out")) as call:
            with self.assertRaises(RuntimeError):
                api._api_call("prompt", "dummy", "primary", .2, "nvidia", allow_model_fallback=False)
        self.assertEqual(call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
