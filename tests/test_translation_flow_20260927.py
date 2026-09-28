"""Translation quality and request-volume regressions (no provider required)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autodub.srt_utils import Segment
from autodub.translate import semantic as S


def payload(prompt):
    return json.JSONDecoder().raw_decode(prompt.split('INPUT_JSON:\n', 1)[1])[0]


def response(ids, bad=()):
    return dict(translated_sentences=[dict(sentence_id=f's{i}', source_ids=[i],
        text_vi='Đi vào 回山门.' if i in bad else 'Anh đã về rồi.', speaker=None) for i in ids],
        new_entities=[], updated_summary='Anh đã về.', warnings=[])


def source(n=3):
    return [Segment(i+1, i*2., i*2.+1.9, '你回来了') for i in range(n)]


class TranslationFlow(unittest.TestCase):
    def test_repaired_earlier_hole_does_not_invalidate_later_completed_batches(self):
        from autodub.translate.cache import TranslationIncomplete
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'cache')
            seen=[]
            repair=False
            def ask(prompt):
                cue_ids=[r['id'] for r in payload(prompt)['target_cues']]
                seen.append(cue_ids)
                return json.dumps(response(cue_ids, bad=[] if repair else [2]))
            with patch.object(S,'batches',return_value=[(0,1),(1,2),(2,3)]):
                with self.assertRaises(TranslationIncomplete):
                    S.translate_semantic(source(),ask,{},cache_path=path)
                repair=True
                seen.clear()
                S.translate_semantic(source(),ask,{},cache_path=path)
            self.assertEqual(seen,[[2]])

    def test_absolute_clock_change_invalidates_cached_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'cache')
            S.translate_semantic(source(1),Mock(return_value=json.dumps(response([1]))),{},cache_path=path)
            moved=source(1); moved[0].start+=30; moved[0].end+=30
            ask=Mock(return_value=json.dumps(response([1])))
            S.translate_semantic(moved,ask,{},cache_path=path)
            self.assertEqual(ask.call_count,1)
            self.assertEqual(moved[0].start,30)

    def test_residue_error_identifies_exact_cue_and_characters(self):
        obj=response([1193]);obj['translated_sentences'][0]['text_vi']='Nó 抓住 anh ấy.'
        with self.assertRaisesRegex(ValueError,r'1193.*抓住'):
            S.translated_validator([Segment(1193,0,2,'抓住他')],{1193:0},{},'han_viet')(obj)

    def test_cjk_retry_starts_from_source_without_quoting_bad_translation(self):
        first={'cues':[{'id':1,'text_vi':'Anh về rồi.'},
                       {'id':2,'text_vi':'Bàn tay 死死抓住 anh.'}]}
        ask=Mock(side_effect=[json.dumps(first),json.dumps({'cues':[{'id':2,'text_vi':'Tay giữ chặt anh.'}]})])
        segs=source(2)
        S.translate_semantic(segs,ask,{})
        retry=ask.call_args.args[0]
        self.assertNotIn('Bàn tay',retry)
        self.assertNotIn('PREVIOUS_RESPONSE',retry)
        self.assertIn('Translate ALL',retry)
        self.assertEqual([r['id'] for r in payload(retry)['target_cues']],[2])
        self.assertEqual(segs[0].text,'Anh về rồi.')

    def test_changed_source_context_invalidates_completed_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'cache')
            def ask(prompt):
                return json.dumps(response([r['id'] for r in payload(prompt)['target_cues']]))
            with patch.object(S,'batches',return_value=[(0,1),(1,2)]):
                S.translate_semantic(source(2),ask,{},cache_path=path)
                edited=source(2);edited[0].text='你还没回来'
                seen=Mock(side_effect=ask)
                S.translate_semantic(edited,seen,{},cache_path=path)
            self.assertEqual(seen.call_count,2)

    def test_progress_counts_completed_cues_instead_of_last_attempted_index(self):
        from autodub.translate.cache import TranslationIncomplete
        def ask(prompt):
            return json.dumps(response([r['id'] for r in payload(prompt)['target_cues']],bad=[2]))
        with patch.object(S,'batches',return_value=[(0,1),(1,2),(2,3)]), patch.object(S,'log') as log:
            with self.assertRaises(TranslationIncomplete):
                S.translate_semantic(source(),ask,{})
        progress=[c.args[0] for c in log.call_args_list if 'Dịch semantic' in c.args[0]]
        self.assertIn('2/3 cue',progress[-1])
        self.assertNotIn('3/3 cue',progress[-1])

    def test_valid_sentences_are_not_sent_again_to_repair_one_bad_cue(self):
        seen=[]
        def ask(prompt):
            ids=[r['id'] for r in payload(prompt)['target_cues']]
            seen.append(ids)
            return json.dumps(response(ids, bad=[2] if len(seen)==1 else []))
        segs=source()
        clocks=[(s.index,s.start,s.end) for s in segs]
        S.translate_semantic(segs,ask,{})
        self.assertEqual(seen,[[1,2,3],[2]])
        self.assertEqual([s.text for s in segs],['Anh đã về rồi.']*3)
        self.assertEqual([(s.index,s.start,s.end) for s in segs],clocks)

    def test_partial_checkpoint_survives_cancel_without_retranslating_good_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'translation')
            ask=Mock(side_effect=[json.dumps(response([1,2,3],bad=[2])),InterruptedError()])
            with self.assertRaises(InterruptedError):
                S.translate_semantic(source(),ask,{},cache_path=path)
            seen=[]
            def resume(prompt):
                ids=[r['id'] for r in payload(prompt)['target_cues']]
                seen.append(ids)
                return json.dumps(response(ids))
            segs=source()
            S.translate_semantic(segs,resume,{},cache_path=path)
            self.assertEqual(seen,[[2]])
            S.translate_semantic(source(),Mock(side_effect=AssertionError('cache miss')),{},cache_path=path)

    def test_context_contains_verified_vietnamese_and_never_changes_source(self):
        seen=[]
        def ask(prompt):
            p=payload(prompt)
            seen.append(p)
            return json.dumps(response([r['id'] for r in p['target_cues']]))
        with patch.object(S,'batches',return_value=[(0,12),(12,24)]):
            S.translate_semantic(source(24),ask,{'semantic_context_cues':10})
        previous=seen[1]['context_before']
        self.assertEqual(len(previous),10)
        self.assertTrue(all(r['text_vi']=='Anh đã về rồi.' for r in previous))
        self.assertTrue(all(r['source_text']=='你回来了' for r in previous))
        self.assertTrue(all('text_vi' not in r for r in seen[0]['context_after']))

    def test_prompt_carries_per_cue_length_budget(self):
        seen=[]
        def ask(prompt):
            p=payload(prompt); seen.append(p)
            return json.dumps(response([r['id'] for r in p['target_cues']]))
        S.translate_semantic(source(),ask,{'chars_per_sec':18,'max_chars_per_line':42,'max_lines_per_cue':2})
        row=seen[0]['target_cues'][0]
        self.assertGreater(row['target_chars'],0)
        self.assertLessEqual(row['target_chars'],35)
        self.assertGreater(row['target_syllables'],0)

    def test_missing_object_name_cannot_be_prepended_as_a_subject(self):
        segs=[Segment(1,0,2,'我不是七月')]
        obj=response([1]); obj['translated_sentences'][0]['text_vi']='Tôi không phải.'
        before=copy.deepcopy(obj)
        glossary={'七月':{'vi':'Thất Nguyệt','locked':True,'type':'person'}}
        with self.assertRaisesRegex(ValueError,'glossary'):
            S.translated_validator(segs,{1:0},glossary,'han_viet')(obj)
        self.assertEqual(obj,before)

    def test_group_limits_are_part_of_translation_cache_identity(self):
        self.assertNotEqual(S.cache_affecting_cfg({'semantic_group_max_cues':2}),
                            S.cache_affecting_cfg({'semantic_group_max_cues':6}))

    def test_model_cue_alignment_wins_over_duration_only_split(self):
        segs=[Segment(1,0,1,'因为我'),Segment(2,1,5,'回来了')]
        obj=response([1]); sentence=obj['translated_sentences'][0]
        sentence.update(source_ids=[1,2],text_vi='Vì tôi đã về nhà.',cue_texts=['Vì tôi','đã về nhà.'])
        S.translate_semantic(segs,Mock(return_value=json.dumps(obj)),{})
        self.assertEqual([s.text for s in segs],['Vì tôi','đã về nhà.'])
        sentence['cue_texts']=['Vì cô ấy','đã về nhà.']
        fallback=S.fallback_alignment([Segment(1,0,1,'因为我'),Segment(2,1,5,'回来了')],[sentence],{})
        self.assertEqual(' '.join(r['text'] for r in fallback['cues']),'Vì tôi đã về nhà.')

    def test_duplicate_ids_are_never_checkpointed_as_valid(self):
        bad=response([1,1,2,3])
        ask=Mock(side_effect=[json.dumps(bad),json.dumps(response([1]))])
        segs=source()
        S.translate_semantic(segs,ask,{})
        self.assertEqual([r['id'] for r in payload(ask.call_args.args[0])['target_cues']],[1])
        self.assertEqual([s.text for s in segs],['Anh đã về rồi.']*3)

    def test_shortening_only_requests_long_cue_and_keeps_other_text_and_clocks(self):
        segs=source(2)
        first=response([1,2])
        first['translated_sentences'][1]['text_vi']='Tôi nghĩ rằng vào lúc này anh ấy chắc chắn đã trở về nhà của mình rồi.'
        seen=[]
        def ask(prompt):
            p=payload(prompt); seen.append(p)
            return json.dumps(first if len(seen)==1 else response([2]))
        cfg={'chars_per_sec':18,'shorten_long_lines':True}
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'cache')
            S.translate_semantic(segs,ask,cfg,cache_path=path)
            S.translate_semantic(source(2),Mock(side_effect=AssertionError('cache miss')),cfg,cache_path=path)
        self.assertEqual([r['id'] for r in seen[1]['target_cues']],[2])
        self.assertEqual([s.text for s in segs],['Anh đã về rồi.']*2)
        self.assertEqual([(s.start,s.end) for s in segs],[(0.,1.9),(2.,3.9)])

    def test_shortening_failure_cannot_erase_number_or_loop(self):
        segs=[Segment(1,0,1,'他欠我12元')]
        original='Anh ấy hiện tại đang còn thiếu tôi tất cả là 12 đồng tiền.'
        obj=response([1]); obj['translated_sentences'][0]['text_vi']=original
        ask=Mock(side_effect=[json.dumps(obj),json.dumps(response([1]))])
        S.translate_semantic(segs,ask,{'chars_per_sec':18,'shorten_long_lines':True})
        self.assertEqual(ask.call_count,2)
        self.assertEqual(segs[0].text,original)

    def test_pipeline_retry_cannot_feed_translated_text_back_as_source(self):
        from autodub.server.pipeline import _translate_with_source_retry
        from autodub.translate.cache import TranslationIncomplete
        segs=source(); seen=[]
        def translate(rows):
            seen.append([(s.text,s.start,s.end) for s in rows])
            rows[0].text='Anh đã về.'
            rows[0].semantic_group='translated'
            if len(seen)==1:
                raise TranslationIncomplete([2],2)
        _translate_with_source_retry(segs,translate,lambda exc:None)
        self.assertEqual(seen[0],seen[1])
        self.assertEqual(segs[0].text,'Anh đã về.')

    def test_compact_response_repairs_only_untranslated_id(self):
        first={'cues':[{'id':1,'text_vi':'Anh về rồi.'},{'id':2,'text_vi':'Anh 回山门.'}]}
        second={'cues':[{'id':2,'text_vi':'Anh về sơn môn.'}]}
        ask=Mock(side_effect=[json.dumps(first),json.dumps(second)])
        segs=source(2)
        S.translate_semantic(segs,ask,{})
        self.assertEqual([s.text for s in segs],['Anh về rồi.','Anh về sơn môn.'])
        self.assertEqual([r['id'] for r in payload(ask.call_args.args[0])['target_cues']],[2])

    def test_shortening_keeps_good_rewrite_when_other_cue_did_not_shrink(self):
        segs=source(2)
        first=response([1,2])
        for s in first['translated_sentences']:
            s['text_vi']='Vào thời điểm hiện tại, anh ấy chắc chắn đã trở về nhà rồi.'
        second=copy.deepcopy(first)
        second['translated_sentences'][1]['text_vi']='Anh đã về nhà.'
        ask=Mock(side_effect=[json.dumps(first),json.dumps(second)])
        S.translate_semantic(segs,ask,{'shorten_long_lines':True})
        self.assertEqual(ask.call_count,2)
        self.assertEqual(segs[0].text,first['translated_sentences'][0]['text_vi'])
        self.assertEqual(segs[1].text,'Anh đã về nhà.')

    def test_corrupt_partial_cache_does_not_block_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'cache')
            S.translate_semantic(source(),Mock(return_value=json.dumps(response([1,2,3]))),{},cache_path=path)
            file=Path(path+'.semantic.json')
            data=json.loads(file.read_text(encoding='utf-8'))
            file.write_text(json.dumps({key:[] for key in data}),encoding='utf-8')
            segs=source()
            S.translate_semantic(segs,Mock(return_value=json.dumps(response([1,2,3]))),{},cache_path=path)
            self.assertEqual([s.text for s in segs],['Anh đã về rồi.']*3)

    def test_shortening_cannot_drop_negation_or_newly_translated_number(self):
        for original, rewritten in [('Anh ấy vẫn chưa hề quay trở về nhà vào lúc này.','Anh ấy đã về.'),
                                     ('Hiện tại anh còn nợ tôi tất cả là 12 đồng.','Anh còn nợ tôi.')]:
            with self.subTest(original=original):
                segs=[Segment(1,0,1,'他还没回来')]
                first=response([1]);first['translated_sentences'][0]['text_vi']=original
                second=response([1]);second['translated_sentences'][0]['text_vi']=rewritten
                S.translate_semantic(segs,Mock(side_effect=[json.dumps(first),json.dumps(second)]),
                                     {'shorten_long_lines':True})
                self.assertEqual(segs[0].text,original)


if __name__=='__main__':
    unittest.main()
