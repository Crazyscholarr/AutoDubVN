"""Kiểm tra ràng buộc độ dài bản dịch - phần chống lỗi 'tiếng chạy trước hình'.

Chạy: python -m unittest discover -s tests
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub import translate as T
from autodub.srt_utils import Segment


def _wait_reply(page, *args, **kwargs):
    from autodub.translate import browser as B
    with mock.patch.object(B.time, "monotonic", side_effect=lambda: page.ticks * .25):
        return B._wait_reply(page, *args, **kwargs)


def seg(start: float, end: float, text: str = "nguon") -> Segment:
    return Segment(1, start, end, text)


class _FakeReply:
    def inner_text(self):
        return 'Đây là kết quả:\n{"title_localized":"A","titles":["Một","Hai"]}'

    def evaluate(self, _script):
        return "1. Một\n2. Hai"


class DocPhanHoiTrinhDuyet(unittest.TestCase):
    def test_json_khong_bi_bo_doc_chi_con_danh_sach_html(self):
        reply = T._reply_text(_FakeReply())
        self.assertIn('{"title_localized"', reply)
        self.assertNotEqual(reply, "1. Một\n2. Hai")

    def test_json_con_do_dang_khi_ngoac_chua_khop(self):
        self.assertTrue(T._json_con_do_dang('{"titles":["A"'))
        self.assertFalse(T._json_con_do_dang('{"titles":["A"]}'))
        self.assertFalse(T._json_con_do_dang("Bản dịch 1. Xin chào"))
        self.assertFalse(T._json_con_do_dang('```json\n{"titles":["A"]}\n'))
        self.assertTrue(T._co_nhieu_dong_danh_so(
            "1. Xin chào\n2. Cảm ơn\n3. Tạm biệt\n4. Hẹn gặp lại"))
        self.assertFalse(T._co_nhieu_dong_danh_so("1. Một dòng"))

    def test_chon_json_phan_tich_that_thay_vi_schema_de_bai(self):
        schema = (
            '{\n  "title_localized":"...",\n'
            '  "production_ease":"Cao|Trung|Thấp",\n'
            '  "recommendation":"Nên làm|Chờ đợi|Không nên làm"\n}'
        )
        real = (
            '{"title_localized":"Ngôi mộ bốc khói",'
            '"production_ease":"Cao","recommendation":"Nên làm",'
            '"titles":["A"]}'
        )
        self.assertFalse(T._json_phan_tich_du(schema))
        self.assertTrue(T._json_phan_tich_du(real))
        self.assertEqual(T._chon_tra_loi([schema, real]), real)

    def test_wait_reply_nhan_ban_dich_danh_so_som(self):
        text = "1. Xin chào\n2. Cảm ơn\n3. Tạm biệt\n4. Hẹn gặp lại"
        page = _FakeGeminiPage(text)
        got = _wait_reply(page, 0, 3.0, baseline={"model-response": 0})
        self.assertIn("1. Xin chào", got)
        self.assertLessEqual(page.ticks, 12)

    def test_wait_reply_khong_nhan_json_khi_generation_con_active(self):
        body = (
            '{"title_localized":"Ngôi mộ bốc khói","titles":["A"],'
            '"production_ease":"Cao","recommendation":"Nên làm"}'
        )
        page = _FakeGeminiPage(body)
        page.force_generating = True
        from autodub.translate.browser import GeminiResponseError
        with self.assertRaises(GeminiResponseError) as cm:
            _wait_reply(page, 0, 2.0, baseline={"model-response": 0})
        self.assertEqual(cm.exception.kind, "RESPONSE_COMPLETION_TIMEOUT")

    def test_wait_reply_khong_nhan_json_cat_som(self):
        from autodub.translate.jsonutil import json_translation_complete, inspect_raw, TRUNCATED_JSON
        raw = '{"translated_sentences":[{"sentence_id":"s1"'
        self.assertEqual(inspect_raw(raw)[0], TRUNCATED_JSON)
        self.assertFalse(json_translation_complete(raw))
        page = _FakeGeminiPage(raw)
        got = _wait_reply(page, 0, 3.0, baseline={"model-response": 0})
        self.assertEqual(got, raw)  # Final truncated output belongs to the parser.

    def test_wait_reply_doi_json_streaming_xong(self):
        page = _FakeGeminiPage('{"translated_sentences":[')
        page.final_text = (
            '{"translated_sentences":[{"sentence_id":"s1","source_ids":[1],'
            '"text_vi":"A","speaker":null}],"new_entities":[],'
            '"updated_summary":"x","warnings":[]}'
        )
        page.inplace_after = 2
        got = _wait_reply(page, 0, 8.0, baseline={"model-response": 0})
        self.assertIn('"text_vi":"A"', got.replace(" ", ""))
        self.assertGreaterEqual(page.ticks, 2)

    def test_wait_reply_nhan_json_du_du_giao_dien_con_gan_goi_y(self):
        body = (
            '{"title_localized":"Ngôi mộ bốc khói","titles":["A","B"],'
            '"production_ease":"Cao","recommendation":"Nên làm"}'
        )
        page = _FakeGeminiPage(body, grow_suffix=True)
        got = _wait_reply(page, 0, 3.0, baseline={"model-response": 0})
        self.assertIn('"production_ease":"Cao"', got.replace(" ", ""))
        self.assertLessEqual(page.ticks, 12)

    def test_wait_reply_doc_khoi_ghi_de_tai_cho(self):
        page = _FakeGeminiPage("cũ", inplace_after=1)
        page.final_text = (
            '{"titles":["A"],"production_ease":"Cao",'
            '"recommendation":"Nên làm"}'
        )
        got = _wait_reply(
            page, 1, 4.0, baseline={"model-response": 1}, last_before="cũ")
        self.assertTrue(T._json_phan_tich_du(got))

    def test_wait_reply_khong_chet_vi_mot_loi_wait_thoang_qua(self):
        page = _FakeGeminiPage("")
        page.final_text = "1. Xin chào\n2. Cảm ơn\n3. Tạm biệt\n4. Hẹn gặp lại"
        page.inplace_after = 1
        page.fail_waits = 1
        got = _wait_reply(page, 0, 4.0, baseline={"model-response": 0})
        self.assertIn("1. Xin chào", got)

    def test_chua_gui_khi_o_nhap_con_nguyen_tin(self):
        from autodub.translate import browser as B
        msg = "[9] 原来是这样,\n[10] 那我现在属于异化者还是。"
        page = _FakeGeminiPage("")
        page.composer = msg
        self.assertTrue(B._van_con_trong_o_nhap(page, msg))
        self.assertFalse(B._da_gui(page, msg))
        page.composer = ""
        self.assertFalse(B._da_gui(page, msg))
        page.user_query = msg
        self.assertTrue(B._da_gui(page, msg))

    def test_wait_reply_thoat_som_khi_tin_chua_gui(self):
        from autodub.translate import browser as B
        msg = "[9] 原来是这样,\n[10] 那我现在属于异化者还是。"
        page = _FakeGeminiPage("")
        page.composer = msg
        got = _wait_reply(
            page, 0, 8.0, baseline={"model-response": 0},
            prompt_probe=msg, unsent_after=0.2)
        self.assertEqual(got, "")

    def test_gui_lai_khi_o_nhap_chua_trong(self):
        from autodub.translate import browser as B
        msg = "[9] 原来是这样,\n[10] 那我现在属于异化者还是。"
        page = _FakeGeminiPage("")
        page.composer = msg
        submits = []

        def fake_submit(_page):
            submits.append(1)
            if len(submits) >= 2:
                page.composer = ""
                page.user_query = msg

        with mock.patch.object(B, "_submit", side_effect=fake_submit):
            self.assertTrue(B._gui_va_xac_nhan(page, msg))
        self.assertGreaterEqual(len(submits), 2)

    def test_submit_js_send_khong_bam_nut_nham(self):
        from autodub.translate import browser as B
        page = _FakeGeminiPage("")
        page.send_js = "button"
        B._submit(page)
        self.assertEqual(page.keys, [])

    def test_submit_js_disabled_roi_ctrl_enter(self):
        from autodub.translate import browser as B
        page = _FakeGeminiPage("")
        page.send_js = "disabled"
        with mock.patch.object(B, "_visible_locator", return_value=None):
            B._submit(page)
        self.assertIn("Control+Enter", page.keys)


class _TextReply:
    def __init__(self, text: str):
        self._text = text

    def inner_text(self):
        return self._text

    def evaluate(self, _script):
        return self._text


class _Loc:
    def __init__(self, items, visible=False):
        self._items = list(items)
        self._visible = visible

    def count(self):
        return len(self._items)

    def nth(self, i):
        return _TextReply(self._items[i])

    @property
    def first(self):
        return self

    def is_visible(self, timeout=0):
        return bool(self._visible and self._items)


class _FakeGeminiPage:
    def __init__(self, text: str, grow_suffix: bool = False, inplace_after: int = 0):
        self.text = text
        self.final_text = text
        self.grow_suffix = grow_suffix
        self.inplace_after = inplace_after
        self.ticks = 0
        self.force_generating = False
        self.fail_waits = 0
        self.composer = ""
        self.keys = []

    def locator(self, sel: str):
        low = (sel or "").lower()
        if any(token in low for token in ("button", "stop", "ngừng", "dừng")):
            return _Loc([], visible=False)
        extra = f"\nGợi ý {self.ticks}" if self.grow_suffix else ""
        return _Loc([self.text + extra], visible=True)

    def wait_for_timeout(self, _ms):
        self.ticks += 1
        if self.fail_waits:
            self.fail_waits -= 1
            raise RuntimeError("Timeout 30000ms exceeded")
        if self.inplace_after and self.ticks >= self.inplace_after:
            self.text = self.final_text

    def evaluate(self, script):
        src = str(script or "")
        if "__GEMINI_CONV_SNAPSHOT__" in src:
            from autodub.translate import browser as B
            snap = B._empty_snap()
            snap["root"] = "infinite-scroller:chat-history-container"
            snap["users"] = [{"text": self.user_query}] if getattr(self, "user_query", "") else []
            snap["user_count"] = len(snap["users"])
            mid = "new-response" if self.inplace_after and self.ticks >= self.inplace_after else ""
            # Suggestions belong outside message-content, so never enter extracted text.
            snap["models"] = [{"id": mid, "fp": mid or "model-response@0", "text": self.text}]
            if not self.text:
                snap["models"] = []
            snap["model_count"] = len(snap["models"])
            snap["generating"] = self.force_generating
            return snap
        if "ql-blank" in src:
            return True
        if "data-message-author-role" in src or "user-query" in src:
            return [getattr(self, "user_query", "")] if getattr(self, "user_query", "") else []
        if "rich-textarea div[contenteditable" in src:
            return self.composer
        if "node.click()" in src and "send-button" in src:
            return getattr(self, "send_js", "")
        return bool(self.force_generating)

    @property
    def keyboard(self):
        page = self

        class _Keys:
            def press(self, key):
                page.keys.append(key)

        return _Keys()


class CharBudget(unittest.TestCase):
    def test_budget_bam_sat_moc_chars_per_sec(self):
        """Ngân sách không được nới rộng quá mốc người dùng đặt.

        Từng để margin 1.20 nên mốc 15 c/s thành 18 c/s ngay từ đầu.
        """
        s = seg(0.0, 10.0)
        budget = T.char_budget(s, 15.0)
        self.assertLessEqual(budget / 10.0, 15.0 * 1.1)

    def test_cau_ngan_van_co_san_toi_thieu(self):
        """Câu rất ngắn không bị ép xuống mức không thể diễn đạt."""
        self.assertGreaterEqual(T.char_budget(seg(0.0, 0.3), 15.0),
                                T.TRANSLATION_MIN_CHARS)

    def test_tat_rang_buoc_khi_cps_bang_khong(self):
        self.assertEqual(T.char_budget(seg(0.0, 5.0), 0.0), 0)


class PhatHienCauQuaDai(unittest.TestCase):
    """Vùng 18-22 ký tự/giây là nơi bản dịch thực tế rơi vào và trước đây lọt lưới."""

    def test_bat_duoc_cau_22_ky_tu_moi_giay(self):
        s = seg(0.0, 10.0)
        self.assertTrue(T._too_long_for_tts(s, "x" * 220, 15.0))

    def test_khong_bat_cau_dung_nhip(self):
        s = seg(0.0, 10.0)
        self.assertFalse(T._too_long_for_tts(s, "x" * 150, 15.0))


class BoChanKhiRutGon(unittest.TestCase):
    """Rút gọn từng làm hỏng nghĩa, nên các bộ chặn này phải chắc."""

    def setUp(self):
        self.seg = seg(0.0, 4.0)

    def test_tu_choi_khi_danh_roi_ten_rieng(self):
        goc = "Cậu ta bảo Diệp Vân mau chạy khỏi đây ngay lập tức"
        rut = "Cậu ta bảo mau chạy đi"
        self.assertFalse(T._accept_shortened(goc, rut, self.seg, 15.0))

    def test_tu_choi_khi_danh_roi_con_so(self):
        goc = "Chúng ta chỉ còn đúng 7 ngày trước khi cổng đóng lại"
        rut = "Chúng ta chỉ còn vài ngày thôi"
        self.assertFalse(T._accept_shortened(goc, rut, self.seg, 15.0))

    def test_tu_choi_khi_rut_qua_tay(self):
        goc = "Hắn nói rằng bọn nó đã bỏ đi từ sáng sớm rồi"
        rut = "Bọn nó đi"
        self.assertFalse(T._accept_shortened(goc, rut, self.seg, 15.0))

    def test_chap_nhan_ban_rut_gon_hop_le(self):
        goc = "Thật ra thì tôi cũng không biết chuyện đó xảy ra như thế nào cả"
        rut = "Tôi cũng không biết chuyện đó xảy ra sao"
        self.assertTrue(T._accept_shortened(goc, rut, self.seg, 15.0))

    def test_tu_choi_khi_khong_ngan_hon(self):
        self.assertFalse(T._accept_shortened("abc", "abcd", self.seg, 15.0))


class ApLucDoc(unittest.TestCase):
    def test_bao_dung_khi_ban_dich_dai_gap_ruoi(self):
        segs = [seg(i * 10.0, i * 10.0 + 10.0, "x" * 225) for i in range(10)]
        st = T.reading_pressure(segs, 15.0)
        self.assertAlmostEqual(st["ratio"], 1.5, places=2)
        self.assertEqual(st["over_lines"], 10)

    def test_khong_bao_dong_khi_vua_nhip(self):
        segs = [seg(i * 10.0, i * 10.0 + 10.0, "x" * 150) for i in range(10)]
        st = T.reading_pressure(segs, 15.0)
        self.assertAlmostEqual(st["ratio"], 1.0, places=2)
        self.assertEqual(st["over_lines"], 0)

    def test_dem_dung_dong_vuot_tran_toc_do(self):
        segs = [seg(0.0, 10.0, "x" * 300), seg(10.0, 20.0, "x" * 150)]
        st = T.reading_pressure(segs, 15.0)
        self.assertEqual(st["hopeless_lines"], 1)

    def test_bo_qua_dong_rong(self):
        st = T.reading_pressure([seg(0.0, 5.0, "")], 15.0)
        self.assertEqual(st["lines"], 0)


if __name__ == "__main__":
    unittest.main()
