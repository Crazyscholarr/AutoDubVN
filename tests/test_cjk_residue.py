"""Leftover Chinese function-words glued into Vietnamese must be rewritten locally.

These are not names. The map must not touch pure-Chinese lines (those still go
to the translator) and must not strip an uncertain person name such as 七月.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub.srt_utils import Segment
from autodub.translate.cjk_residue import (
    apply_residual_repairs, cjk_run_is_glued, leftover_cjk_indices,
    repair_residual_cjk, strip_standalone_names,
)
from autodub.translate.parse import _contains_cjk


# Mixed lines taken from BV17ytg6cEKg after a full semantic pass.
_FILM_LEFTOVERS = (
    "Nghĩ đến đây,思绪",
    "toàn thân hắn run lên, trong lòng顿时 dấy lên một dự cảm chẳng lành:",
    "Vô vàn suy nghĩ萦绕 trong lòng,",
    "Chỉ恨不得 muốn nuốt sống tươi nó. Đồ súc sinh nhỏ kia,",
    "Anh chỉ đùa tôi chơi thôi mà. Hồ ca, ngài đừng kích động, chiếc túi này可不 phải tầm thường đâu.",
    "Lứa người bọn họ đã hoàn toàn沦为 nô lệ;",
    "trong xe, bầu không khí trầm mặc,一片死寂,",
    "Cậu ấy vừa mới làm xong việc,好不容易 mới được nghỉ một lát, anh đừng có quá đáng như thế.",
    "Ngô Lương biết rằng sau sự cố gây thương tích lần trước, Vương Đại Lực hành sự dường như đã trở nên do dự,瞻前顾后 hơn trước.",
    "tao早就 nhìn",
    "Tuy nhiên,倘若 thực sự xảy ra đại sự, các ngươi cũng phải随时待命.",
    "mình lại多出 ra một tờ",
    "Nhị tiểu thư, trong phủ có thích khách闯入,",
    "Cũng có thể coi là thiên tư trác tuyệt, nhưng con切记 không được kiêu ngạo tự mãn.",
    "Trước đây nếu không phải ta luôn lau đít cho cái đồ hỗn láo nhà ngươi, thì ngươi早就 ngồi trong ngục rồi; thật là, nhà ngươi cứ không thể dung tha cho Vô Lương đến thế sao?",
    "ngươi千万别",
    "Trong lòng难免 dấy lên một chút áy náy, có lẽ mình đã tự đề cao bản thân quá mức.",
    "Tên Vô Lương này rõ ràng có võ công trong người nhưng lại迟迟 không chịu lộ diện,",
    "Lối ra càng bị phong tỏa层层,",
    "Có những chuyện tự mình trong lòng rõ là được, lão gia trong lòng自有 tính toán,",
)


class CjkResidue(unittest.TestCase):
    def test_film_mixed_lines_lose_function_words(self):
        doubled = ("phải phải", "muốn muốn", "mới mới", "ra ra", "chỉ chỉ")
        for blob in _FILM_LEFTOVERS:
            cleaned = repair_residual_cjk(blob)
            self.assertFalse(_contains_cjk(cleaned), blob + " => " + cleaned)
            self.assertTrue(any(c.isalpha() for c in cleaned), blob)
            low = cleaned.casefold()
            for pair in doubled:
                self.assertNotIn(pair, low, cleaned)

    def test_pure_chinese_is_untouched(self):
        src = "他们快不行了"
        self.assertEqual(repair_residual_cjk(src), src)

    def test_residue_name_in_glossary_still_rewritten(self):
        blob = "Tên Vô Lương này rõ ràng có võ công trong người nhưng lại迟迟 không chịu lộ diện,"
        cleaned = repair_residual_cjk(blob, ("迟迟", "七月"))
        self.assertNotIn("迟迟", cleaned)
        self.assertIn("mãi", cleaned)
        blob = "七月 đã về nhà rồi."
        self.assertEqual(repair_residual_cjk(blob, ("七月",)), blob)

    def test_apply_repairs_counts_only_changed_cues(self):
        segs = [
            Segment(1, 0, 1, "Xin chào"),
            Segment(2, 1, 2, "Nó 迟迟 không tới."),
            Segment(3, 2, 3, "他们快不行了"),
        ]
        self.assertEqual(apply_residual_repairs(segs), 1)
        self.assertEqual(segs[1].text, "Nó mãi không tới.")
        self.assertEqual(leftover_cjk_indices(segs), [2])

    def test_particle_leftover_in_mixed_line_is_dropped(self):
        cleaned = repair_residual_cjk("Nó 了 không tới.")
        self.assertFalse(_contains_cjk(cleaned), cleaned)
        self.assertIn("không tới", cleaned)

    def test_source_gate_does_not_clean_mismatched_cue(self):
        blob = "xin各位 hãy bình tĩnh, trở về chỗ ngồi."
        self.assertEqual(
            repair_residual_cjk(blob, source="飞机到底怎么了？"),
            blob,
        )
        cleaned = repair_residual_cjk(
            "không bị coi là trượt考核.",
            source="即便后续落败也不算考核失败。",
        )
        self.assertEqual(cleaned, "không bị coi là trượt sát hạch.")

    def test_bv1za_mixed_leftovers_rewrite_from_source(self):
        cleaned = repair_residual_cjk(
            "Cũng không dám nhiều lời啰嗦 nữa.",
            source="也不敢再多批啰嗦。",
        )
        self.assertFalse(_contains_cjk(cleaned), cleaned)
        self.assertIn("lảm nhảm", cleaned)
        cleaned = repair_residual_cjk(
            "Người giải quyết tình huống尴尬này là người chăm sóc.",
            source="解决这个尴尬场面的是照管。",
        )
        self.assertFalse(_contains_cjk(cleaned), cleaned)
        self.assertIn("khó xử", cleaned)
        self.assertNotIn("照管", cleaned)
        named = repair_residual_cjk(
            "Người giải quyết là 照管.",
            source="解决这个尴尬场面的是照管。",
        )
        self.assertIn("照管", named)

    def test_glued_han_is_leftover_even_when_listed_as_name(self):
        self.assertTrue(cjk_run_is_glued("tình huống尴尬này", "尴尬"))
        self.assertTrue(cjk_run_is_glued("tình huống照管này", "照管"))
        self.assertFalse(cjk_run_is_glued("Vì 七月 tôi đã về nhà.", "七月"))
        self.assertFalse(cjk_run_is_glued("Người giải quyết là 照管.", "照管"))
        self.assertEqual(
            strip_standalone_names("tình huống照管này", ("照管",)),
            "tình huống照管này",
        )
        self.assertEqual(strip_standalone_names("là 照管.", ("照管",)), "là .")
        glued = Segment(1, 0, 1, "tình huống照管này")
        glued.allowed_source_names = ("照管",)
        isolated = Segment(2, 1, 2, "là 照管.")
        isolated.allowed_source_names = ("照管",)
        self.assertEqual(leftover_cjk_indices([glued, isolated]), [0])

    def test_isolated_verb_is_leftover_without_name_allowance(self):
        cue = Segment(1, 0, 1, "Vì 决定 tôi đã về nhà.")
        self.assertEqual(leftover_cjk_indices([cue]), [0])
        cue.allowed_source_names = ("决定",)
        self.assertEqual(leftover_cjk_indices([cue]), [])


if __name__ == "__main__":
    unittest.main()
