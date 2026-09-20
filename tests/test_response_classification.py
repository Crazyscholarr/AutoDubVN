import json
import unittest
from unittest.mock import Mock, patch

from autodub.translate import jsonutil as J
from autodub.translate import semantic as S

REFUSAL = 'Tôi là một mô hình ngôn ngữ nên điều đó nằm ngoài mục đích mà tôi được tạo ra.'


class ResponseShape(unittest.TestCase):
    def test_classify_before_json_loads(self):
        cases = {
            REFUSAL: J.MODEL_REFUSAL,
            'Tôi chỉ là một mô hình ngôn ngữ, nên không thể trợ giúp về điều đó.': J.MODEL_REFUSAL,
            'Tôi là một công nghệ trí tuệ nhân tạo dựa trên văn bản, nên điều đó nằm ngoài khả năng của tôi.': J.MODEL_REFUSAL,
            'Tôi không được lập trình để làm điều đó.': J.MODEL_REFUSAL,
            'Tôi không được thiết kế để thực hiện yêu cầu này.': J.MODEL_REFUSAL,
            'Yêu cầu của bạn nằm ngoài khả năng tôi được lập trình. Tôi chỉ có thể tạo văn bản.': J.MODEL_REFUSAL,
            "I'm sorry, I cannot assist with that request.": J.MODEL_REFUSAL,
            '我无法帮助完成这个请求。': J.MODEL_REFUSAL,
            'Xin chào bạn': J.NON_JSON_RESPONSE,
            'Text has a { brace but is prose': J.NON_JSON_RESPONSE,
            ' \n\t': J.EMPTY_RESPONSE,
            'Something went wrong. Try again later.': J.UI_ERROR_RESPONSE,
            'Usage limit exceeded': J.UI_ERROR_RESPONSE,
            '{"ok":true}': J.VALID_JSON_CANDIDATE,
        }
        with patch.object(J.json, 'loads', side_effect=AssertionError('shape must run before JSON parsing')):
            for raw, kind in cases.items():
                with self.subTest(raw=raw):
                    self.assertEqual(J.classify_response_shape(raw), kind)
                    if kind != J.VALID_JSON_CANDIDATE:
                        with self.assertRaises(J.ResponseShapeError) as cm:
                            J.extract_object(raw)
                        self.assertEqual(cm.exception.kind, kind)

    def test_refusal_with_schema_example_is_still_refusal(self):
        self.assertEqual(J.classify_response_shape(REFUSAL + '\nSchema: {"ok":'), J.MODEL_REFUSAL)

    def test_refusal_words_inside_valid_json_are_data(self):
        raw = json.dumps({'text_vi': REFUSAL})
        self.assertEqual(J.classify_response_shape(raw), J.VALID_JSON_CANDIDATE)
        self.assertEqual(J.extract_object(raw)['text_vi'], REFUSAL)

    def test_actual_incomplete_prefixes(self):
        for raw in ('{', '{"a":', '{"a":[1,', '{"a":"hello', '{"a":tru',
                    '[1, {"a":2}', '{"a":"escaped\\', '{"a":"\\u12', '{"a":1e+'):
            with self.subTest(raw=raw):
                self.assertEqual(J.classify_response_shape(raw), J.TRUNCATED_JSON)

    def test_syntax_fault_is_malformed_not_truncated(self):
        for raw in ('{"a": 1, "b": }', '{"a": [1,]}', '{"a":wrong',
                    '{"a":01}', '{"a":NaN}', '{"a":"bad\\q"}', '{"a":[1}}'):
            with self.subTest(raw=raw):
                self.assertEqual(J.classify_response_shape(raw), J.MALFORMED_JSON)

    def test_wrapping_quotes_are_unwrapped_locally(self):
        raw = '{"a": ""Xin chào""}'
        self.assertEqual(J.classify_response_shape(raw), J.VALID_JSON_CANDIDATE)
        self.assertEqual(J.extract_object(raw)['a'], '"Xin chào"')
        raw2 = '{"a": ""Xin chào"","warnings":[]}'
        self.assertEqual(J.extract_object(raw2)['a'], '"Xin chào"')
        self.assertEqual(J.extract_object(raw2)['warnings'], [])

    def test_strict_decode_and_schema_are_distinct(self):
        for raw in ('{"a":1,"a":2}', '{"a":NaN}'):
            with self.assertRaises(J.ResponseShapeError) as cm:
                J.extract_object(raw)
            self.assertEqual(cm.exception.kind, J.MALFORMED_JSON)
        with self.assertRaises(J.ResponseShapeError) as cm:
            J.extract_object('[1,2]')
        self.assertEqual(cm.exception.kind, J.SCHEMA_FAILURE)

    def test_exception_taxonomy_keeps_shape(self):
        exc = json.JSONDecodeError('Expecting value', REFUSAL, 0)
        self.assertEqual(J.classify_exception(exc, REFUSAL), J.MODEL_REFUSAL)
        self.assertEqual(J.classify_exception(ValueError('schema'), '{"ok":true}'), J.SCHEMA_FAILURE)


class ResponseRetry(unittest.TestCase):
    def run_request(self, responses):
        ask = Mock(side_effect=responses)
        metrics = S.response_metrics()
        warnings = []
        result = S.request(ask, S.TRANSLATE, {'target_cues': []}, lambda obj: None,
                           warnings, metrics=metrics)
        return ask, metrics, result

    def test_refusal_retry_has_specific_clarification(self):
        ask, metrics, result = self.run_request([REFUSAL, '{"ok":true}'])
        self.assertEqual(ask.call_count, 2)
        first, second = [call.args[0] for call in ask.call_args_list]
        self.assertNotEqual(first, second)
        self.assertIn('TASK_CLARIFICATION', second)
        self.assertNotIn('FORMAT_REPAIR', second)
        self.assertEqual(metrics[J.MODEL_REFUSAL], 1)
        self.assertEqual(metrics[J.TRUNCATED_JSON], 0)
        self.assertEqual(metrics['retry_success_count'], 1)
        self.assertEqual(result[0], {'ok': True})

    def test_repeat_refusal_is_bounded(self):
        ask, metrics, result = self.run_request([REFUSAL, REFUSAL])
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(result, (None, J.MODEL_REFUSAL))
        self.assertEqual(metrics[J.MODEL_REFUSAL], 2)
        self.assertEqual(metrics['retry_success_count'], 0)

    def test_format_repair_is_targeted_and_counted(self):
        for raw, kind in [('hello', J.NON_JSON_RESPONSE), ('{"a":', J.TRUNCATED_JSON),
                          ('{"a": 1, "b": }', J.MALFORMED_JSON), ('{"a":1,"a":2}', J.MALFORMED_JSON)]:
            with self.subTest(raw=raw):
                ask, metrics, result = self.run_request([raw, '{"ok":true}'])
                self.assertIn('FORMAT_REPAIR', ask.call_args.args[0])
                self.assertIn(json.dumps(raw, ensure_ascii=False), ask.call_args.args[0])
                self.assertEqual(metrics[kind], 1)
                self.assertEqual(metrics['retry_success_count'], 1)
                self.assertEqual(result[0], {'ok': True})

    def test_local_unwrap_does_not_resend(self):
        for raw in ('```json\n{"ok":true}\n```', 'Kết quả:\n{"ok":true}',
                    '{"a": ""hello""}'):
            with self.subTest(raw=raw):
                ask, metrics, result = self.run_request([raw])
                ask.assert_called_once()
                self.assertEqual(metrics['retry_success_count'], 0)
                self.assertIsNotNone(result[0])

    def test_ui_error_does_not_trigger_refusal_or_format_retry(self):
        ask, metrics, result = self.run_request(['Something went wrong'])
        ask.assert_called_once()
        self.assertEqual(result[1], J.UI_ERROR_RESPONSE)
        self.assertEqual(metrics[J.MODEL_REFUSAL], 0)


if __name__ == '__main__':
    unittest.main()
