import unittest
from unittest.mock import Mock,patch
from tests import test_gemini_response_detection as fixtures
from autodub.translate import browser as B
from autodub.translate.jsonutil import classify_exception

class UIErrors(unittest.TestCase):
    setUpClass = classmethod(fixtures.GeminiDOM.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.GeminiDOM.tearDownClass.__func__)
    setUp = fixtures.GeminiDOM.setUp
    tearDown = fixtures.GeminiDOM.tearDown
    set_turn = fixtures.GeminiDOM.set_turn
    def test_toast_1095_revokes_ack(self):
        self.page.evaluate('''() => {const el=document.createElement('mat-snack-bar-container');el.textContent='Đã xảy ra lỗi (1095)';document.body.append(el)}''')
        with self.assertRaises(B.GeminiResponseError) as caught:
            B._wait_reply(self.page,0,2,msg='prompt',trace={'send_ack':True})
        self.assertEqual(caught.exception.kind,'UI_ERROR_RESPONSE')
        self.assertEqual(caught.exception.diagnostic['code'],'1095')

    def test_dialogue_is_not_ui_error(self):
        self.set_turn('<div role="alert">Đã xảy ra lỗi (1095)</div>')
        B._check_ui_error(self.page,{})

    def test_hidden_toast_is_not_an_active_failure(self):
        self.page.evaluate('''() => {const el=document.createElement('mat-snack-bar-container');el.style.display='none';el.textContent='Đã xảy ra lỗi (1095)';document.body.append(el)}''')
        B._check_ui_error(self.page,{})

    def test_vietnamese_new_chat_label(self):
        self.page.evaluate('''() => {const el=document.createElement('button');el.textContent='Cuộc trò chuyện mới';el.onclick=()=>document.body.dataset.clicked='yes';document.body.append(el)}''')
        self.assertTrue(self.page.evaluate(B._NEW_CHAT_CLICK_JS))
        self.assertEqual(self.page.locator('body').get_attribute('data-clicked'),'yes')

class Recovery(unittest.TestCase):
    def test_same_batch_recovered(self):
        page=Mock()
        error=B.GeminiResponseError('UI_ERROR_RESPONSE','WAIT',{'code':'1095'})
        with patch.object(B,'_ask_once',side_effect=[error,'ok']) as ask, patch.object(B,'_mo_chat_moi',return_value=page), patch.object(B,'_snapshot',return_value={}), patch.object(B,'_visible_locator',return_value=Mock()):
            self.assertEqual(B._ask_with_recovery(page,'original',20,'url'),(page,'ok'))
        self.assertEqual([c.args[1] for c in ask.call_args_list],['original','original'])
        page.reload.assert_called_once()

    def test_persistent_error_is_bounded_and_terminal(self):
        page=Mock()
        error=B.GeminiResponseError('UI_ERROR_RESPONSE','WAIT',{'code':'1095'})
        with patch.object(B,'_ask_once',side_effect=error) as ask, patch.object(B,'_mo_chat_moi',return_value=page), patch.object(B,'_snapshot',return_value={}), patch.object(B,'_visible_locator',return_value=Mock()):
            with self.assertRaises(B.GeminiResponseError) as caught:
                B._ask_with_recovery(page,'original',20,'url')
        self.assertEqual(ask.call_count,3)
        self.assertEqual(classify_exception(caught.exception),'BROWSER_SESSION_UNHEALTHY')

    def test_reset_must_be_empty(self):
        page=Mock()
        error=B.GeminiResponseError('UI_ERROR_RESPONSE','WAIT',{})
        with patch.object(B,'_ask_once',side_effect=error) as ask, patch.object(B,'_mo_chat_moi',return_value=page), patch.object(B,'_snapshot',return_value={'user_count':1}), patch.object(B,'_visible_locator',return_value=Mock()):
            with self.assertRaises(B.GeminiResponseError) as caught:
                B._ask_with_recovery(page,'original',20,'url')
        self.assertEqual(ask.call_count,1)
        self.assertEqual(caught.exception.state,'RESET_FAILED')

if __name__=='__main__':unittest.main()
