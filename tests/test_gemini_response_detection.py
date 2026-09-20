"""Offline P0 regression tests execute the production JS in a real DOM."""
import json
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from autodub.translate import browser as B
from autodub.translate.jsonutil import classify_exception


class GeminiDOM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel='msedge', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.set_content('<aside><message-content>{"wrong":true}</message-content></aside>'
                              '<infinite-scroller data-test-id="chat-history-container"></infinite-scroller>')

    def tearDown(self):
        self.page.close()

    def set_turn(self, text='', mid='a', extra=''):
        self.page.locator('infinite-scroller').evaluate('''(el, x) => {
          el.innerHTML = '<user-query>{"user":true}</user-query><model-response>' +
            '<model-response-content><message-content id="message-content-id-r_' + x.mid + '">' +
            x.text + '</message-content></model-response-content>' +
            '<message-actions>Copy wrong label</message-actions></model-response>' + x.extra;
        }''', dict(text=text, mid=mid, extra=extra))

    def test_sidebar_and_actions_excluded(self):
        before = B._snapshot(self.page)
        self.set_turn('{"ok":true}')
        snap = B._snapshot(self.page)
        self.assertEqual(snap['user_count'], 1)
        self.assertEqual(snap['model_count'], 1)
        self.assertTrue(B.has_new_response(snap, before))
        self.assertEqual(B.extract_response_text(snap, before), '{"ok":true}')

    def test_empty_scroller_does_not_mask_populated_conversation(self):
        self.page.evaluate('''() => {
          const chat=document.createElement('chat-window-content');
          chat.innerHTML='<user-query>unique prompt</user-query><model-response><p>Tôi không được lập trình để làm điều đó.</p><message-actions>Copy</message-actions></model-response>';
          document.body.append(chat);
        }''')
        snap = B._snapshot(self.page)
        self.assertEqual(snap['user_count'], 1)
        self.assertEqual(snap['model_count'], 1)
        self.assertEqual(B._turn_text(B._current_turn(snap, 'unique prompt')),
                         'Tôi không được lập trình để làm điều đó.')

    def test_watcher_follows_replaced_root_and_reads_plain_refusal(self):
        B._watch_install(self.page, 'unique prompt')
        self.page.evaluate('''() => {
          const old=document.querySelector('infinite-scroller');
          const next=old.cloneNode(false);
          next.innerHTML='<user-query>unique prompt</user-query><model-response><p>Tôi không được lập trình để làm điều đó.</p><message-actions>Copy</message-actions></model-response>';
          old.replaceWith(next);
        }''')
        self.page.wait_for_timeout(20)
        watch = B._watch_read(self.page)
        self.assertTrue(watch['user_found'])
        self.assertEqual(watch['text'], 'Tôi không được lập trình để làm điều đó.')

    def test_shared_context_tail_cannot_reuse_another_batch(self):
        tail = 'same context after ' * 20
        old = '[AUTODUB_SEMANTIC_V1]\nINPUT_JSON: target cue 1 ' + tail
        new = '[AUTODUB_SEMANTIC_V1]\nINPUT_JSON: target cue 2 ' + tail
        self.assertFalse(B._prompt_matches_user(new, old))
        self.assertNotEqual(B._prompt_identity_needle(new), B._prompt_identity_needle(old))

    def test_watcher_keeps_completed_answer_when_virtualizer_unmounts(self):
        self.page.locator('infinite-scroller').evaluate('''el => {
          el.innerHTML='<user-query>unique prompt</user-query><model-response><message-content>{"ok":true}</message-content><message-actions>Copy</message-actions></model-response>';
        }''')
        B._watch_install(self.page, 'unique prompt')
        self.page.locator('infinite-scroller').evaluate("el=>el.replaceChildren()")
        self.page.wait_for_timeout(20)
        self.assertEqual(B._watch_read(self.page)['text'], '{"ok":true}')

    def test_watcher_does_not_keep_partial_answer_when_unmounted(self):
        self.page.locator('infinite-scroller').evaluate('''el => {
          el.innerHTML='<user-query>unique prompt</user-query><model-response><message-content>{"ok":</message-content></model-response>';
        }''')
        B._watch_install(self.page, 'unique prompt')
        self.page.locator('infinite-scroller').evaluate("el=>el.replaceChildren()")
        self.page.wait_for_timeout(20)
        self.assertEqual(B._watch_read(self.page)['text'], '')

    def test_hidden_old_root_cannot_win_over_visible_conversation(self):
        self.set_turn('{"old":true}')
        self.page.locator('infinite-scroller').evaluate("el=>el.style.display='none'")
        self.page.evaluate('''() => {
          const chat=document.createElement('chat-window-content');
          chat.innerHTML='<user-query>new prompt</user-query><model-response><message-content>{"new":true}</message-content></model-response>';
          document.body.append(chat);
        }''')
        snap = B._snapshot(self.page)
        self.assertEqual(B._turn_text(B._current_turn(snap, 'new prompt')), '{"new":true}')

    def test_empty_node_detected_before_extraction(self):
        before = B._snapshot(self.page)
        self.set_turn()
        self.assertTrue(B.has_new_response(B._snapshot(self.page), before))
        self.assertEqual(B.extract_response_text(B._snapshot(self.page), before), '')

    def test_pending_response_counts_as_started_then_model_replaces_it(self):
        before = B._snapshot(self.page)
        self.page.locator('infinite-scroller').evaluate(
            '(el) => el.innerHTML = "<user-query>test</user-query><pending-response><response-container></response-container></pending-response>"')
        pending = B._snapshot(self.page)
        self.assertTrue(B.has_new_response(pending, before))
        self.assertTrue(pending['generating'])
        self.assertEqual(pending['model_count'], 0)
        self.assertEqual(B.extract_response_text(pending, before), '')
        self.set_turn('{"ok":true}')
        final = B._snapshot(self.page)
        self.assertFalse(final['generating'])
        self.assertEqual(B.extract_response_text(final, before), '{"ok":true}')

    def test_pending_then_replacement_and_identical_second_response(self):
        self.set_turn('{"ok":true}', 'a')
        before = B._snapshot(self.page)
        self.set_turn('{"ok":true}', 'b')
        after = B._snapshot(self.page)
        self.assertTrue(B.has_new_response(after, before))
        self.assertEqual(B.extract_response_text(after, before), '{"ok":true}')
        self.assertFalse(B.has_new_response(after, after))

    def test_old_node_rerender_is_not_a_new_turn(self):
        self.set_turn('old', 'a')
        before = B._snapshot(self.page)
        self.set_turn('changed chrome text', 'a')
        self.assertFalse(B.has_new_response(B._snapshot(self.page), before))

    def test_missing_root_cannot_read_sidebar(self):
        self.page.locator('infinite-scroller').evaluate('(el) => el.remove()')
        with self.assertRaisesRegex(RuntimeError, 'root not found'):
            B._snapshot(self.page)

    def test_offscreen_and_innertext_empty(self):
        before = B._snapshot(self.page)
        self.set_turn('{"ok":true}')
        self.page.locator('model-response').evaluate('(el) => el.style.marginTop = "10000px"')
        self.page.locator('model-response message-content').evaluate('(el) => Object.defineProperty(el,"innerText",{value:""})')
        self.assertEqual(B.extract_response_text(B._snapshot(self.page), before), '{"ok":true}')

    def test_host_fallback_requires_json_brace(self):
        before = B._snapshot(self.page)
        self.page.locator('infinite-scroller').evaluate('''(el) => {
          el.innerHTML = '<user-query>{"user":true}</user-query><model-response>' +
            '{"ok":true}' +
            '<message-actions>Copy wrong label</message-actions></model-response>';
        }''')
        snap = B._snapshot(self.page)
        self.assertTrue(B.has_new_response(snap, before))
        self.assertIn('{"ok":true}', B.extract_response_text(snap, before))

    def test_virtualized_latest_appears_after_scroll(self):
        before = B._snapshot(self.page)
        self.page.locator('infinite-scroller').evaluate('''(el) => {
          el.innerHTML =
            '<user-query>u1</user-query><model-response>' +
            '<model-response-content><message-content id="message-content-id-r_a">{"a":1}</message-content></model-response-content></model-response>' +
            '<user-query>u2</user-query><model-response>' +
            '<model-response-content><message-content id="message-content-id-r_b">{"b":2}</message-content></model-response-content></model-response>';
          const hidden = document.createElement('model-response');
          hidden.innerHTML = '<model-response-content><message-content id="message-content-id-r_c">{"ok":true}</message-content></model-response-content>';
          Object.defineProperty(el, 'scrollTop', {
            configurable: true,
            get() { return 0; },
            set() {
              if (!hidden.parentNode) el.appendChild(hidden);
            }
          });
        }''')
        before = B._snapshot(self.page)
        self.assertEqual(before['model_count'], 2)
        B._reveal_latest(self.page)
        after = B._snapshot(self.page)
        self.assertEqual(after['model_count'], 3)
        self.assertIn('{"ok":true}', B.extract_response_text(after, before))

    def _mount(self, turns):
        html = ''.join(
            '<user-query>PROMPT %s</user-query><model-response>'
            '<model-response-content><message-content id="message-content-id-r_%s">'
            '%s</message-content></model-response-content></model-response>'
            % (u, mid, m) for u, mid, m in turns)
        self.page.locator('infinite-scroller').evaluate(
            '(el, h) => { el.innerHTML = h; }', html)

    def test_associated_response_not_last_model(self):
        self._mount([
            ('A-unique', 'a', '{"batch":"A"}'),
            ('B-unique', 'b', '{"batch":"B"}'),
        ])
        snap = B._snapshot(self.page)
        self.assertEqual(B.extract_response_text(snap, msg='PROMPT A-unique'), '{"batch":"A"}')
        self.assertEqual(B.extract_response_text(snap, msg='PROMPT B-unique'), '{"batch":"B"}')
        self.assertEqual(B.extract_response_text(snap, B._empty_snap()), '{"batch":"B"}')

    def test_virtualized_count_drop_still_maps_current_prompt(self):
        self._mount([
            ('one', 'a', '{"n":1}'),
            ('two', 'b', '{"n":2}'),
            ('three', 'c', '{"n":3}'),
        ])
        full = B._snapshot(self.page)
        self.assertEqual(full['user_count'], 3)
        self._mount([
            ('two', 'b', '{"n":2}'),
            ('three', 'c', '{"n":3}'),
        ])
        window = B._snapshot(self.page)
        self.assertEqual(window['user_count'], 2)
        self.assertEqual(B.extract_response_text(window, msg='PROMPT three'), '{"n":3}')
        self.assertEqual(B.extract_response_text(window, msg='PROMPT two'), '{"n":2}')
        self.assertEqual(B.extract_response_text(window, msg='PROMPT one'), '')

    def test_grouped_user_then_model_lists_pair_in_order(self):
        html = (
            '<user-query>PROMPT A-unique</user-query>'
            '<user-query>PROMPT B-unique</user-query>'
            '<model-response><model-response-content>'
            '<message-content id="message-content-id-r_a">{"batch":"A"}</message-content>'
            '</model-response-content></model-response>'
            '<model-response><model-response-content>'
            '<message-content id="message-content-id-r_b">{"batch":"B"}</message-content>'
            '</model-response-content></model-response>')
        self.page.locator('infinite-scroller').evaluate('(el, h) => { el.innerHTML = h; }', html)
        snap = B._snapshot(self.page)
        self.assertEqual(B.extract_response_text(snap, msg='PROMPT A-unique'), '{"batch":"A"}')
        self.assertEqual(B.extract_response_text(snap, msg='PROMPT B-unique'), '{"batch":"B"}')

    def test_shared_prefix_does_not_map_wrong_turn(self):
        self._mount([
            ('Return exactly this JSON object and nothing else: {"probe":"ten-01","n":1}', 'a', '{"probe":"ten-01"}'),
            ('Return exactly this JSON object and nothing else: {"probe":"ten-02","n":2}', 'b', '{"probe":"ten-02"}'),
        ])
        snap = B._snapshot(self.page)
        self.assertEqual(
            B.extract_response_text(snap, msg='Return exactly this JSON object and nothing else: {"probe":"ten-02","n":2}'),
            '{"probe":"ten-02"}')
        self.assertEqual(
            B.extract_response_text(snap, msg='Return exactly this JSON object and nothing else: {"probe":"ten-01","n":1}'),
            '{"probe":"ten-01"}')


class Flow(unittest.TestCase):
    def setUp(self):
        self.clock = 0
        self.page = type('Page', (), {})()
        self.page.wait_for_timeout = self.advance
        self.page.evaluate = lambda *args: None

    def advance(self, ms):
        self.clock += ms / 1000

    def snap(self, text='', mid='b', generating=False, user='test'):
        urec = {'id': 'u1', 'fp': 'u1', 'text': user, 'seq_index': 0, 'kind': 'user'}
        mrec = {'id': mid, 'fp': mid, 'text': text, 'pending': False,
                'seq_index': 1, 'kind': 'model'}
        return {'root': 'test', 'user_count': 1, 'model_count': 1,
                'generating': generating, 'pending': False,
                'users': [urec], 'models': [mrec],
                'turns': [{'user': urec, 'model': mrec}]}

    def test_streaming_closed_json_must_wait_for_final_and_rerender(self):
        trace = {'send_ack': True}
        def snapshot(_):
            return self.snap('x' * (100 if self.clock < .5 else 200 if self.clock < 1 else 400 if self.clock < 1.5 else 450),
                             generating=self.clock < 2)
        with patch.object(B, '_snapshot', side_effect=snapshot), patch.object(B.time, 'monotonic', side_effect=lambda: self.clock):
            result = B._wait_reply(self.page, 0, 8, before_snap={}, trace=trace)
        self.assertEqual(len(result), 450)
        self.assertEqual(trace['response_start_ms'], 0)
        self.assertGreaterEqual(self.clock, 4)
        self.assertTrue(trace['response_complete'])

    def test_ack_detector_failure_taxonomy_and_no_duplicate_on_retry(self):
        before = {'models': [], 'user_count': 0, 'model_count': 0}
        trace = {'send_ack': True, 'state': 'SEND_ACKNOWLEDGED'}
        B._INFLIGHT[self.page] = {'msg': 'test', 'before': before, 'trace': trace}
        with patch.object(B, '_trang_co_o_nhap', side_effect=lambda p: p), patch.object(B, '_submit') as submit:
            with patch.object(B, '_snapshot', side_effect=RuntimeError('detector failed')):
                with self.assertRaises(B.GeminiResponseError) as cm:
                    B._ask_once(self.page, 'test', 8)
                self.assertEqual(classify_exception(cm.exception), 'RESPONSE_DETECTION_FAILURE')
                self.assertTrue(cm.exception.send_ack)
            with patch.object(B, '_snapshot', return_value=self.snap('{"ok":true}')), patch.object(B.time, 'monotonic', side_effect=lambda: self.clock):
                self.assertEqual(B._ask_once(self.page, 'test', 8), '{"ok":true}')
            submit.assert_not_called()

    def test_started_timeout_is_completion_timeout(self):
        with patch.object(B, '_snapshot', return_value=self.snap('partial', generating=True)), patch.object(B.time, 'monotonic', side_effect=lambda: self.clock):
            with self.assertRaises(B.GeminiResponseError) as cm:
                B._wait_reply(self.page, 0, 1, before_snap={}, trace={'send_ack': True})
        self.assertEqual(classify_exception(cm.exception), 'RESPONSE_COMPLETION_TIMEOUT')

    def test_absent_response_after_ack_is_start_timeout(self):
        with patch.object(B, '_snapshot', return_value={'user_count': 1, 'models': []}), patch.object(B.time, 'monotonic', side_effect=lambda: self.clock):
            with self.assertRaises(B.GeminiResponseError) as cm:
                B._wait_reply(self.page, 0, 1, before_snap={}, trace={'send_ack': True})
        self.assertEqual(classify_exception(cm.exception), 'RESPONSE_START_TIMEOUT')

    def test_empty_placeholder_is_start_timeout_not_extraction(self):
        with patch.object(B, '_snapshot', return_value=self.snap('', generating=False)), \
             patch.object(B.time, 'monotonic', side_effect=lambda: self.clock):
            with self.assertRaises(B.GeminiResponseError) as cm:
                B._wait_reply(self.page, 0, 2, before_snap={}, trace={'send_ack': True})
        self.assertEqual(classify_exception(cm.exception), 'RESPONSE_START_TIMEOUT')
        self.assertGreaterEqual(self.clock, 2)
        self.assertLess(self.clock, 5)

    def test_slow_first_token_without_generating_is_kept(self):
        trace = {'send_ack': True}
        def snapshot(_):
            if self.clock < 15:
                return self.snap('', generating=False)
            return self.snap('{"ok":true}', generating=False)
        with patch.object(B, '_snapshot', side_effect=snapshot), \
             patch.object(B.time, 'monotonic', side_effect=lambda: self.clock):
            result = B._wait_reply(self.page, 0, 30, before_snap={}, trace=trace)
        self.assertEqual(result, '{"ok":true}')
        self.assertGreaterEqual(self.clock, 17)
        self.assertTrue(trace.get('response_complete'))

    def test_generating_then_empty_fails_extraction(self):
        def snapshot(_):
            return self.snap('', generating=self.clock < 1)
        with patch.object(B, '_snapshot', side_effect=snapshot), \
             patch.object(B.time, 'monotonic', side_effect=lambda: self.clock):
            with self.assertRaises(B.GeminiResponseError) as cm:
                B._wait_reply(self.page, 0, 240, before_snap={}, trace={'send_ack': True})
        self.assertEqual(classify_exception(cm.exception), 'RESPONSE_EXTRACTION_FAILURE')
        self.assertLess(self.clock, 20)

    def test_semantic_retry_layer_stops_on_acknowledged_response_failure(self):
        from autodub.translate.semantic import request
        from unittest.mock import Mock
        ask = Mock(side_effect=B.GeminiResponseError('RESPONSE_DETECTION_FAILURE',
                   'WAITING_MODEL_RESPONSE', {'send_ack': True}))
        result, kind = request(ask, 'test', {}, lambda obj: None, [])
        self.assertIsNone(result)
        self.assertEqual(kind, 'RESPONSE_DETECTION_FAILURE')
        ask.assert_called_once()

    def test_ack_via_prompt_when_count_does_not_increase(self):
        prompt = 'Return exactly {"probe":"ack-count"}'
        user = {'fp': 'u2', 'text': 'Bạn đã nói ' + prompt, 'seq_index': 0, 'id': 'u2'}
        model = {'fp': 'm2', 'text': '{"probe":"ack-count"}', 'pending': False, 'seq_index': 1}
        before = {'user_count': 2, 'model_count': 2, 'users': [], 'models': [],
                  'generating': False, 'turns': []}
        after = {'root': 'test', 'user_count': 2, 'model_count': 2, 'generating': False,
                 'pending': False, 'users': [user], 'models': [model],
                 'turns': [{'user': user, 'model': model}]}
        submit = patch.object(B, '_submit')
        with patch.object(B, '_trang_co_o_nhap', side_effect=lambda p: p), \
             patch.object(B, '_visible_locator', return_value=object()), \
             patch.object(B, '_put_text', return_value=True), \
             patch.object(B, '_reveal_latest'), \
             patch.object(B, '_van_con_trong_o_nhap', return_value=False), \
             patch.object(B, '_nut_gui_con_bam_duoc', return_value=False), \
             submit as submit_mock, \
             patch.object(B, '_snapshot', side_effect=lambda _=None: after if self.clock else before), \
             patch.object(B.time, 'monotonic', side_effect=lambda: self.clock):
            result = B._ask_once(self.page, prompt, 8)
        self.assertEqual(result, '{"probe":"ack-count"}')
        submit_mock.assert_called_once()

    def test_send_ack_timeout_recovers_without_resend(self):
        prompt = 'Return exactly {"probe":"recovered"}'
        user = {'fp': 'u3', 'text': prompt, 'seq_index': 0}
        model = {'fp': 'm3', 'text': '{"probe":"recovered"}', 'pending': False, 'seq_index': 1}
        empty = {'root': 'test', 'user_count': 2, 'model_count': 2, 'generating': False,
                 'users': [], 'models': [], 'turns': []}
        after = {'root': 'test', 'user_count': 2, 'model_count': 2, 'generating': False,
                 'users': [user], 'models': [model],
                 'turns': [{'user': user, 'model': model}]}
        n = {'i': 0}

        def snapshot(_=None):
            n['i'] += 1
            if n['i'] == 1:
                return empty
            if self.clock < 12:
                return empty
            return after

        with patch.object(B, '_trang_co_o_nhap', side_effect=lambda p: p), \
             patch.object(B, '_visible_locator', return_value=object()), \
             patch.object(B, '_put_text', return_value=True), \
             patch.object(B, '_reveal_latest'), \
             patch.object(B, '_van_con_trong_o_nhap', return_value=True), \
             patch.object(B, '_nut_gui_con_bam_duoc', return_value=True), \
             patch.object(B, '_submit') as submit, \
             patch.object(B, '_snapshot', side_effect=snapshot), \
             patch.object(B.time, 'monotonic', side_effect=lambda: self.clock):
            result = B._ask_once(self.page, prompt, 20)
        self.assertEqual(result, '{"probe":"recovered"}')
        submit.assert_called_once()

    def test_duplicate_guard_consumes_existing_response(self):
        prompt = 'Return exactly {"probe":"dup"}'
        user = {'fp': 'u4', 'text': prompt, 'seq_index': 0}
        model = {'fp': 'm4', 'text': '{"probe":"dup"}', 'pending': False, 'seq_index': 1}
        snap = {'root': 'test', 'user_count': 1, 'model_count': 1, 'generating': False,
                'users': [user], 'models': [model],
                'turns': [{'user': user, 'model': model}]}
        with patch.object(B, '_trang_co_o_nhap', side_effect=lambda p: p), \
             patch.object(B, '_visible_locator', return_value=object()), \
             patch.object(B, '_snapshot', return_value=snap), \
             patch.object(B, '_submit') as submit:
            result = B._ask_once(self.page, prompt, 8)
        self.assertEqual(result, '{"probe":"dup"}')
        submit.assert_not_called()

    def test_generation_signal_without_node_is_not_extraction_zero(self):
        prompt = 'PROMPT live'
        user = {'fp': 'u5', 'text': prompt, 'seq_index': 0}

        def snapshot(_=None):
            if self.clock < 1:
                return {'root': 'test', 'user_count': 1, 'model_count': 0, 'generating': True,
                        'pending': True, 'users': [user], 'models': [],
                        'turns': [{'user': user, 'model': {'pending': True, 'text': '', 'seq_index': 1}}]}
            return {'root': 'test', 'user_count': 1, 'model_count': 0, 'generating': False,
                    'pending': False, 'users': [user], 'models': [],
                    'turns': [{'user': user, 'model': None}]}

        with patch.object(B, '_snapshot', side_effect=snapshot), \
             patch.object(B, '_reveal_latest'), \
             patch.object(B.time, 'monotonic', side_effect=lambda: self.clock):
            with self.assertRaises(B.GeminiResponseError) as cm:
                B._wait_reply(self.page, 0, 20, before_snap={}, trace={'send_ack': True},
                              msg=prompt)
        self.assertEqual(classify_exception(cm.exception), 'RESPONSE_EXTRACTION_FAILURE')
        self.assertTrue(cm.exception.diagnostic.get('saw_generation_signal'))
        self.assertFalse(cm.exception.diagnostic.get('saw_response_node'))
        self.assertLess(self.clock, 20)


class TurnIdentity(unittest.TestCase):
    CLARIFY = (
        "\nTASK_CLARIFICATION: Phản hồi trước từ chối thực hiện tác vụ. "
        "Không yêu cầu bỏ qua quy tắc an toàn.\n"
    )

    def batch_prompt(self, cue_id, clarify=False):
        payload = json.dumps({
            "target_cues": [{"id": cue_id, "source_text": "枪响%d" % cue_id}],
            "context_after": [{"id": cue_id + 1, "gap_after_ms": None}],
        }, ensure_ascii=False)
        base = (
            "[AUTODUB_SEMANTIC_V1]\nĐây là tác vụ dịch/localization văn bản phụ đề sang tiếng Việt.\n"
            "INPUT_JSON:\n" + payload
        )
        return base + (self.CLARIFY if clarify else "")

    def test_clarification_tail_does_not_map_other_batch(self):
        original = self.batch_prompt(273)
        retry = self.batch_prompt(273, True)
        other_retry = self.batch_prompt(285, True)
        wrapped = "Bạn đã nói " + original
        self.assertTrue(B._prompt_matches_user(original, original))
        self.assertTrue(B._prompt_matches_user(original, wrapped))
        self.assertTrue(B._prompt_matches_user(retry, retry))
        self.assertFalse(B._prompt_matches_user(retry, original))
        self.assertFalse(B._prompt_matches_user(original, retry))
        self.assertFalse(B._prompt_matches_user(other_retry, retry))
        self.assertFalse(B._prompt_matches_user(retry, other_retry))
        self.assertEqual(B._existing_turn_action({
            "users": [{"text": retry, "seq_index": 0}],
            "models": [{"text": '{"ok":true}', "pending": False, "seq_index": 1}],
            "turns": [{"user": {"text": retry, "seq_index": 0},
                       "model": {"text": '{"ok":true}', "pending": False, "seq_index": 1}}],
            "generating": False, "pending": False,
        }, other_retry), "send")
        self.assertEqual(B._existing_turn_action({
            "users": [{"text": retry, "seq_index": 0}],
            "models": [{"text": '{"ok":true}', "pending": False, "seq_index": 1}],
            "turns": [{"user": {"text": retry, "seq_index": 0},
                       "model": {"text": '{"ok":true}', "pending": False, "seq_index": 1}}],
            "generating": False, "pending": False,
        }, retry), "consume")


if __name__ == '__main__':
    unittest.main()
