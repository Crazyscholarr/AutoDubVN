"""Cắt phụ đề kiểu màn hình (CapCut): ~14 chữ / ~2.7s, ngắt khi im lặng."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub import speechmap
from autodub.asr.common import (
    caption_style_is_screen,
    reset_caption_options,
    set_caption_options,
)
from autodub.asr.merge import merge_cjk_sentence_fragments, normalize_segments, pack_screen_cues
from autodub.srt_utils import Segment


class ScreenCaptions(unittest.TestCase):
    def setUp(self):
        reset_caption_options()
        set_caption_options({"caption_style": "screen"})
        speechmap.clear_active()

    def tearDown(self):
        speechmap.clear_active()
        reset_caption_options()

    def test_mac_dinh_la_screen(self):
        reset_caption_options()
        self.assertTrue(caption_style_is_screen())
        set_caption_options({"caption_style": "sentence"})
        self.assertFalse(caption_style_is_screen())
        set_caption_options({"caption_style": "capcut"})
        self.assertTrue(caption_style_is_screen())

    def test_khong_cat_o_khoang_trang_latin(self):
        text = "暂定为 a 级的远程天赋八角"
        out = pack_screen_cues([Segment(1, 0.80, 3.07, text)])
        self.assertEqual("".join(s.text for s in out).replace(" ", ""),
                         text.replace(" ", ""))
        self.assertTrue(all(" a" not in s.text or _visible(s.text) >= 8
                            for s in out))

    def test_khong_dan_manh_le_qua_dau_het_cau(self):
        segs = [
            Segment(1, 0.0, 1.5, "抗人天赋。"),
            Segment(2, 1.55, 1.90, "我妈"),
        ]
        out = pack_screen_cues(segs)
        joined = "".join(s.text for s in out)
        self.assertIn("天赋。", joined)
        self.assertFalse(any("天赋。" in s.text and "我妈" in s.text for s in out))

    def test_tach_sau_dau_phay_va_het_y(self):
        text = "我觉醒了，暂定为a级的远程天赋。"
        segs = [Segment(1, 0.05, 2.39, text)]
        out = pack_screen_cues(segs)
        self.assertGreaterEqual(len(out), 2)
        self.assertTrue(out[0].text.startswith("我觉醒了"))
        self.assertLessEqual(_visible(out[0].text), 6)

    def test_khong_chong_moc_va_gop_manh_ngan(self):
        segs = [
            Segment(1, 0.0, 2.0, "这一发二象国将重置新空秩序不错"),
            Segment(2, 1.8, 2.1, "还"),
        ]
        out = pack_screen_cues(segs)
        for a, b in zip(out, out[1:]):
            self.assertLessEqual(a.end, b.start + 1e-6)
        self.assertTrue(all(_visible(s.text) >= 2 or len(out) == 1 for s in out))

    def test_cat_dong_dai_theo_so_chu_va_thoi_luong(self):
        text = "然而却遭同学和老师的讥讽和嘲笑只因班里其他人觉醒的全是远程天赋"
        segs = [Segment(1, 2.39, 9.55, text)]
        out = pack_screen_cues(segs)
        glued = "".join(s.text for s in out)
        self.assertEqual(glued, text)
        self.assertGreaterEqual(len(out), 3)
        self.assertTrue(all(_visible(s.text) <= 22 for s in out))
        self.assertTrue(all(s.duration <= 4.35 for s in out))
        self.assertLess(max(s.duration for s in out), 7.0)

    def test_ngat_khi_im_lang_du_cau_ngan(self):
        # 我觉醒了 (0.07-0.80) rồi nghỉ, sau đó cụm dài hơn.
        text = "我觉醒了淡定为a级的远程天赋"
        marks = [
            (0.07, 0.22), (0.22, 0.38), (0.38, 0.55), (0.55, 0.80),
            (1.20, 1.40), (1.40, 1.55), (1.55, 1.70), (1.70, 1.85),
            (1.85, 2.00), (2.00, 2.20), (2.20, 2.40), (2.40, 2.60),
            (2.60, 2.80), (2.80, 3.00),
        ]
        speechmap.set_active(speechmap.SpeechMap(marks))
        out = pack_screen_cues([Segment(1, 0.07, 3.00, text)])
        self.assertGreaterEqual(len(out), 2)
        self.assertEqual(out[0].text, "我觉醒了")
        self.assertLess(out[0].end, 1.05)
        self.assertGreaterEqual(out[1].start, 1.10)

    def test_normalize_khong_gop_het_cau_khi_screen(self):
        segs = [
            Segment(1, 0.0, 0.8, "我觉醒了"),
            Segment(2, 0.8, 2.5, "暂定为a级的远程天赋"),
            Segment(3, 2.6, 4.5, "然而却遭同学嘲笑"),
        ]
        out = normalize_segments(segs, max_chars=14)
        glued = "".join(s.text for s in out)
        self.assertIn("我觉醒了", glued)
        self.assertGreaterEqual(len(out), 2)
        self.assertTrue(all(s.duration <= 4.35 for s in out))

    def test_sentence_van_gop_toi_dau_het_cau(self):
        set_caption_options({"caption_style": "sentence"})
        segs = [
            Segment(1, 0.0, 0.8, "我觉醒了，"),
            Segment(2, 0.9, 2.5, "暂定为a级。"),
        ]
        out = merge_cjk_sentence_fragments(segs, max_chars=34)
        self.assertEqual(len(out), 1)
        self.assertIn("暂定为", out[0].text)


def _visible(text: str) -> int:
    return sum(1 for c in (text or "") if not c.isspace())


class ScreenCaptionsVsCapCut(unittest.TestCase):
    """So với SRT CapCut user đưa, nếu file còn trong output/."""

    def test_pack_asr_gan_nhip_capcut(self):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        asr_srt = os.path.join(
            root, "output",
            "合集篇：方源觉醒了“暂定”为A级的远程天【巴雷特】！ [BV1rX8c6DEgv]",
            "合集篇：方源觉醒了“暂定”为A级的远程天【巴雷特】！ [BV1rX8c6DEgv].asr.srt",
        )
        sm_path = asr_srt.replace(".asr.srt", ".ban_do_thoai.json")
        capcut = os.path.join(root, "output", "0913(1).srt")
        if not (os.path.isfile(asr_srt) and os.path.isfile(sm_path)):
            self.skipTest("không có asr.srt / bản đồ thoại của phim mẫu")
        from autodub.srt_utils import load_srt_file
        reset_caption_options()
        set_caption_options({"caption_style": "screen"})
        speechmap.load_active(sm_path)
        src = load_srt_file(asr_srt)
        packed = pack_screen_cues(src)
        speechmap.clear_active()
        reset_caption_options()
        durs = [s.duration for s in packed]
        chars = [_visible(s.text) for s in packed]
        avg_dur = sum(durs) / len(durs)
        avg_chars = sum(chars) / len(chars)
        avg_src_dur = sum(max(0.01, s.duration) for s in src) / max(1, len(src))
        # .asr.srt có lúc còn câu dài (pack phải cắt ra nhiều dòng hơn), có lúc
        # đã pack kiểu màn hình (chạy lại phải gần như giữ nguyên số dòng).
        if avg_src_dur >= 3.2 or len(src) < 400:
            self.assertGreater(len(packed), len(src))
        else:
            self.assertGreaterEqual(len(packed), int(len(src) * 0.92))
            self.assertLessEqual(abs(len(packed) - len(src)), max(25, int(len(src) * 0.08)))
        self.assertGreater(avg_dur, 1.2)
        self.assertLess(avg_dur, 3.2)
        self.assertGreater(avg_chars, 7.0)
        self.assertLess(avg_chars, 16)
        self.assertLess(max(durs), 5.0)
        for a, b in zip(packed, packed[1:]):
            self.assertLessEqual(a.end, b.start + 1e-6)
        if os.path.isfile(capcut):
            cc = load_srt_file(capcut)
            # Cùng phim: số dòng cùng bậc với CapCut (681), không còn ~267 câu dài.
            self.assertGreater(len(packed), len(cc) * 0.7)
            self.assertLess(len(packed), len(cc) * 1.4)


class ScreenWordBoundaries(unittest.TestCase):
    """Regression cases use explicit clocks, never gold text as ASR input."""

    setUp = ScreenCaptions.setUp
    tearDown = ScreenCaptions.tearDown

    def test_protected_words_survive_every_possible_old_cut(self):
        for word in ("活下去", "哥们", "来一根", "同学们", "设墙", "戒严令"):
            for cut in range(1, len(word)):
                with self.subTest(word=word, cut=cut):
                    text = "大家现在请你" + word + "然后一起往前走"
                    edge = 6 + cut
                    out = pack_screen_cues([
                        Segment(1, 0, edge * .14, text[:edge]),
                        Segment(2, edge * .14, len(text) * .14, text[edge:]),
                    ], max_chars=edge, hard_max_chars=22)
                    self.assertTrue(any(word in s.text for s in out), [s.text for s in out])
                    self.assertEqual("".join(s.text for s in out), text)

    def test_repair_punctuation_across_old_cues(self):
        out = pack_screen_cues([Segment(1, 0, .2, "哥，"),
                                Segment(2, .2, 1.4, "们有火吗？")])
        self.assertTrue(any("哥们" in s.text for s in out))

    def test_missing_punctuation_repeated_subject_is_boundary(self):
        out = pack_screen_cues([Segment(1, 0, 2.6, "他们不再只是伪装他们在成长，")])
        self.assertEqual([s.text for s in out], ["他们不再只是伪装", "他们在成长，"])

    def test_punctuation_has_no_speech_mark(self):
        text = "请找出他们，或者活下去，祝你们好运。松姜"
        spoken = [c for c in text if c.isalnum()]
        marks = [(i*.2, (i+1)*.2) for i in range(len(spoken))]
        # A real scene pause before 松姜 must survive punctuation mapping.
        marks[-2:] = [(8, 8.2), (8.2, 8.4)]
        speechmap.set_active(speechmap.SpeechMap(marks))
        out = pack_screen_cues([Segment(1, 0, 8.4, text)])
        self.assertTrue(any("活下去" in s.text for s in out))
        self.assertFalse(any("好运" in s.text and "松姜" in s.text for s in out))
        self.assertFalse(any(s.start < 6 < s.end for s in out))

    def test_short_coalesce_cannot_cross_hard_pause(self):
        out = pack_screen_cues([Segment(1, 0, 1, "祝你们好运"),
                                Segment(2, 1.45, 1.75, "松江")])
        self.assertEqual([s.text for s in out], ["祝你们好运", "松江"])
        self.assertGreater(out[1].start - out[0].end, .32)

    def test_conflicting_word_clocks_are_reviewed_without_bridging_silence(self):
        review = []
        sm = speechmap.SpeechMap([(0,.2), (2,2.2), (2.2,2.4)])
        out = pack_screen_cues([Segment(1, 0, 2.4, "活下去")], speech_map=sm, review=review)
        self.assertFalse(any(s.start < 1 < s.end for s in out))
        self.assertTrue(any(r['reason'] == 'word_crosses_pause' for r in review))
        self.assertFalse(any(r.get('withheld') for r in review if r['reason']=='word_crosses_pause'))
        self.assertEqual("".join(s.text for s in out), "活下去")

    def test_no_marks_for_isolated_garbage_requires_review(self):
        review = []
        out = pack_screen_cues([Segment(1, 0,1,"大家快走"), Segment(2, 3,3.1,"盐。")],
                               speech_map=speechmap.SpeechMap([(0,.2),(.2,.4),(.4,.6),(.6,1)]),
                               review=review)
        self.assertEqual([s.text for s in out], ["大家快走"])
        self.assertTrue(any(r.get('withheld') and '盐' in r['text'] for r in review))

    def test_short_cue_with_fewer_marks_is_kept(self):
        review = []
        out = pack_screen_cues(
            [Segment(1, 0, 1, "大家快走"), Segment(2, 1.5, 1.74, "搜寻")],
            speech_map=speechmap.SpeechMap(
                [(0, .2), (.2, .4), (.4, .6), (.6, 1), (1.5, 1.74)]
            ),
            review=review,
        )
        self.assertTrue(any("搜寻" in s.text for s in out))
        self.assertFalse(any(
            r.get("withheld") and r.get("reason") == "weak_fragment_alignment"
            for r in review
        ))

    def test_short_cue_with_extra_marks_is_still_withheld(self):
        review = []
        out = pack_screen_cues(
            [Segment(1, 0, 1, "大家快走"), Segment(2, 1.5, 1.9, "搜寻")],
            speech_map=speechmap.SpeechMap(
                [(0, .2), (.2, .4), (.4, .6), (.6, 1),
                 (1.5, 1.6), (1.6, 1.7), (1.7, 1.8), (1.8, 1.9)]
            ),
            review=review,
        )
        self.assertFalse(any("搜寻" in s.text for s in out))
        self.assertTrue(any(
            r.get("withheld") and r.get("reason") == "weak_fragment_alignment"
            and "搜寻" in r.get("text", "")
            for r in review
        ))

    def test_overlapping_prefix_fragment_is_absorbed_not_withheld(self):
        review = []
        out = pack_screen_cues(
            [
                Segment(1, 0, 1, "大家快走"),
                Segment(2, 1.79, 1.89, "请"),
                Segment(3, 1.76, 3.0, "请问你们还缺人吗"),
            ],
            speech_map=speechmap.SpeechMap(
                [
                    (0, .2), (.2, .4), (.4, .6), (.6, 1),
                    (1.79, 1.84), (1.84, 1.89),
                    (1.76, 1.95), (1.95, 2.1), (2.1, 2.25), (2.25, 2.4),
                    (2.4, 2.55), (2.55, 2.7), (2.7, 2.85), (2.85, 3.0),
                ]
            ),
            review=review,
        )
        packed = "".join(s.text for s in out)
        self.assertIn("请问", packed)
        self.assertFalse(any(
            r.get("withheld") and r.get("reason") == "weak_fragment_alignment"
            for r in review
        ))
        self.assertTrue(any(
            r.get("reason") == "absorbed_overlapping_fragment"
            and "请" in r.get("text", "")
            for r in review
        ))

    def test_aligned_short_vocatives_stay_in_the_pack(self):
        review = []
        marks = [
            (0, .2), (.2, .4), (.4, .6), (.6, 1.0),
            (1.4, 1.6), (1.6, 1.85),
            (2.4, 2.6), (2.6, 2.85),
        ]
        out = pack_screen_cues(
            [Segment(1, 0, 1, "大家快走"),
             Segment(2, 1.4, 1.85, "谁呀？"),
             Segment(3, 2.4, 2.85, "虎哥")],
            speech_map=speechmap.SpeechMap(marks),
            review=review,
        )
        packed = "".join(s.text for s in out)
        self.assertIn("大家快走", packed)
        self.assertIn("谁呀", packed)
        self.assertIn("虎哥", packed)
        self.assertFalse(any(
            r.get("withheld") and r.get("reason") == "isolated_unknown_fragment"
            for r in review
        ))

    def test_unknown_glyphs_without_lexicon_support_are_still_withheld(self):
        review = []
        out = pack_screen_cues(
            [Segment(1, 0, 1, "大家快走"), Segment(2, 1.5, 1.9, "丄丅")],
            speech_map=speechmap.SpeechMap(
                [(0, .2), (.2, .4), (.4, .6), (.6, 1), (1.5, 1.7), (1.7, 1.9)]
            ),
            review=review,
        )
        self.assertEqual("".join(s.text for s in out), "大家快走")
        self.assertTrue(any(
            r.get("withheld") and r.get("reason") == "isolated_unknown_fragment"
            and "丄丅" in r.get("text", "")
            for r in review
        ))

    def test_grade_number_unit_and_name_are_atomic(self):
        out = pack_screen_cues([Segment(1, 0,3,"他的能力暂定为 a 级射程300米方源已经觉醒")],
                               max_chars=8, protected_words=("方源",))
        for word in ("暂定为 a 级", "300米", "方源"):
            self.assertTrue(any(word in s.text for s in out), [s.text for s in out])

    def test_numeric_punctuation_preserves_values(self):
        text='这里温度是-10度移动距离1.5米'
        out=pack_screen_cues([Segment(1,0,3,text)],max_chars=8)
        self.assertEqual(''.join(s.text for s in out),text)
        for word in ('-10度','1.5米'):
            self.assertTrue(any(word in s.text for s in out))

    def test_fallback_tokenizer_protects_gold_phrases(self):
        from autodub.asr.screen_pack import word_spans
        text = "活下去哥们来一根同学们设墙戒严令暂定为 a 级300米"
        words = [text[a:b] for a,b in word_spans(text, use_jieba=False)]
        self.assertEqual(words, ["活下去", "哥们", "来一根", "同学们", "设墙", "戒严令",
                                 "暂定为 a 级", "300米"])

    def test_dictionary_words_repair_ct_punc_without_changing_letters(self):
        out = pack_screen_cues([Segment(1,0,1.2,'请大家寻，'),
                                Segment(2,1.2,3,'找人。类的避难所')])
        joined = ''.join(s.text for s in out)
        self.assertIn('寻找',joined)
        self.assertIn('人类',joined)
        self.assertEqual(''.join(c for c in joined if c.isalnum()),'请大家寻找人类的避难所')

    def test_raw_funasr_keeps_text_until_speechmap_is_available(self):
        from autodub.asr.funasr import _sentence_info_to_segments, _timestamp_payload_to_segments
        text='他们不再只是伪装他们在成长，'
        marks=[(i*200,(i+1)*200) for i,c in enumerate(text) if c.isalnum()]
        payload=dict(text=text,start=0,end=2600,timestamp=marks)
        for raw in (_sentence_info_to_segments([payload]),_timestamp_payload_to_segments(payload)):
            self.assertEqual(len(raw),1)
            self.assertEqual(raw[0].text,text)
            out=pack_screen_cues(raw,speech_map=speechmap.SpeechMap([(a/1000,b/1000) for a,b in marks]))
            self.assertEqual([s.text for s in out],['他们不再只是伪装','他们在成长，'])

    def test_incomplete_pack_keeps_source_evidence_and_blocks_success(self):
        import json
        from pathlib import Path
        import tempfile
        from autodub.asr.screen_pack import CaptionReviewRequired, ensure_complete
        from autodub.srt_utils import load_srt_file
        src=[Segment(1,0,2.4,'活下去')]
        review=[]
        out=pack_screen_cues(src,speech_map=speechmap.SpeechMap([(10,10.2)]),review=review)
        self.assertTrue(any(r.get('withheld') and r['reason']=='missing_speech_marks' for r in review))
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(CaptionReviewRequired):
                ensure_complete(out,src,td,review)
            folder=next(Path(td).iterdir())
            self.assertEqual(load_srt_file(str(folder/'source.srt'))[0].text,'活下去')
            self.assertTrue(json.loads((folder/'review.json').read_text(encoding='utf-8')))
            self.assertFalse((folder/'packed.srt').exists())

    def test_pause_split_keeps_words_and_does_not_block_export(self):
        import tempfile
        from pathlib import Path
        from autodub.asr.screen_pack import ensure_complete
        src=[Segment(1,0,2.4,'活下去')]
        review=[]
        out=pack_screen_cues(src,speech_map=speechmap.SpeechMap([(0,.2),(2,2.2),(2.2,2.4)]),
                             review=review)
        self.assertEqual("".join(s.text for s in out),"活下去")
        self.assertFalse(any(s.start < 1 < s.end for s in out))
        with tempfile.TemporaryDirectory() as td:
            ensure_complete(out,src,td,review)
            self.assertFalse(list(Path(td).glob("caption-review-*")))

    def test_packer_does_not_invent_homophone_corrections(self):
        out=pack_screen_cues([Segment(1,0,2.5,'据说这个世界掺杂着伟人')],protected_words=('伪人',))
        self.assertIn('伟人',''.join(s.text for s in out))
        self.assertNotIn('伪人',''.join(s.text for s in out))

    def test_damaged_replay_reports_unrecoverable_words(self):
        import json
        from pathlib import Path
        data=json.loads((Path(__file__).parent/'fixtures'/'caption_replay.json').read_text(encoding='utf-8'))
        review=[]
        source=[Segment(**row) for row in data['source']]
        out=pack_screen_cues(source,speech_map=speechmap.SpeechMap(data['marks']),review=review)
        packed="".join(s.text for s in out)
        for word in ('活下去','哥们','来一根'):
            self.assertIn(word, packed)
        self.assertTrue(any(s.text=='他们不再只是伪装' for s in out))
        self.assertTrue(any(s.text.rstrip('，,')=='他们在成长' for s in out))
        self.assertFalse(any('好运' in s.text and '松姜' in s.text for s in out))
        self.assertTrue(all(a.end<=b.start+1e-6 for a,b in zip(out,out[1:])))
        self.assertTrue(all(s.duration<5 for s in out))
        # 盐 has no marks; 蔓 has a clock/count conflict. 延嘿 has aligned
        # clocks, so packing must not erase that recognized source.
        self.assertFalse(any(s.text.strip('，。') in ('蔓','盐') for s in out))
        self.assertTrue(any('延嘿' in s.text for s in out))

    def test_hard_boundary_metadata_applies_only_to_first_child(self):
        out=normalize_segments([Segment(1,0,3,'他们不再只是伪装他们在成长，',
                                         hard_boundary=True,scene='one',speaker='A')],16)
        self.assertEqual(len(out),2)
        self.assertTrue(out[0].hard_boundary)
        self.assertFalse(out[1].hard_boundary)
        self.assertTrue(all(s.scene=='one' and s.speaker=='A' for s in out))


if __name__ == "__main__":
    unittest.main()
