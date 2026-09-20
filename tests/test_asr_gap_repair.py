"""P1: normalization, cropped clocks, bounded rescue and strict speech gate."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autodub import speechmap
from autodub.asr import funasr, pipeline
from autodub.asr.common import _set_last_marks, reset_caption_options
from autodub.asr.merge import find_uncovered_speech_ranges, speech_coverage_report
from autodub.asr.screen_pack import ensure_complete, CaptionReviewRequired
from autodub.srt_utils import Segment


class Normalize(unittest.TestCase):
    def tearDown(self):
        reset_caption_options()
        _set_last_marks([])

    def norm(self, timestamp, **kw):
        return funasr.normalize_funasr_result([dict(text='hello world', timestamp=timestamp, **kw)], 264, 6)

    def test_ms_clip_offset(self):
        n = self.norm([[300, 1100], [1100, 2100]])
        self.assertEqual(n.status, 'ASR_VALID')
        self.assertEqual((n.segments[0].start, n.segments[-1].end), (264.3, 266.1))
        self.assertEqual(n.marks, [(264.3, 265.1), (265.1, 266.1)])

    def test_short_ms_never_guessed_seconds(self):
        self.assertAlmostEqual(self.norm([[0, 20]]).segments[0].end, 264.02)

    def test_seconds_explicit(self):
        self.assertEqual(self.norm([(0.3, 2.1)], timestamp_unit='s').segments[0].end, 266.1)

    def test_absolute_explicit(self):
        n = self.norm([(264.3, 266.1)], timestamp_unit='s', timestamp_origin='media')
        self.assertEqual(n.segments[0].start, 264.3)

    def test_empty_states(self):
        self.assertEqual(funasr.normalize_funasr_result([]).status, 'ASR_EMPTY')
        self.assertEqual(funasr.normalize_funasr_result([dict(key='clip',text='',timestamp=[])]).status, 'ASR_EMPTY')
        for value in (None, []):
            self.assertEqual(self.norm(value).status, 'ASR_TEXT_NO_TIMESTAMP')

    def test_recorded_empty_regions_are_not_timestamp_parse_failures(self):
        fixture=json.loads((Path(__file__).parent/'fixtures'/'funasr_p1_observed.json').read_text())
        empty=[r for r in fixture['observations'] if r['status']=='ASR_EMPTY']
        self.assertEqual(len(empty),7)
        for row in empty:
            diag=row['diagnostics'][0]
            self.assertEqual(diag['text_length'],0)
            self.assertEqual(diag['timestamp_len'],0)
            self.assertEqual(funasr.normalize_funasr_result([dict(text='',timestamp=[])]).status,'ASR_EMPTY')

    def test_invalid(self):
        for value in ([[0,0]], [[20,10]], [[0,float('nan')]], [[0,100],['oops',200]], [[0,7000]], [[200,400],[100,300]], 'bad'):
            with self.subTest(value=value):
                n = self.norm(value)
                self.assertEqual(n.status, 'ASR_INVALID_TIMESTAMP')
                self.assertFalse(n.segments)

    def test_mismatched_token_count_preserves_text_without_inventing_marks(self):
        n = self.norm([[0, 100], [100,200], [200,300]])
        self.assertEqual(n.segments[0].text, 'hello world')
        self.assertEqual(len(n.marks), 3)

    def test_nano_seconds_dicts(self):
        n = funasr.normalize_funasr_result([dict(text='hello',timestamps=[dict(start=.3,end=2.1)])],264,6)
        self.assertEqual(n.segments[0].end,266.1)

    def test_bad_sentence_falls_back_to_parent(self):
        n = funasr.normalize_funasr_result([dict(text='hello',timestamp=[[0,100]],
                    sentence_info=[dict(text='hello',start=0,end=0)])])
        self.assertEqual(n.status,'ASR_VALID')

    def test_parent_and_children_no_duplicate_marks(self):
        child=dict(text='hello world',timestamp=[[0,100],[100,200]])
        n=funasr.normalize_funasr_result([dict(**child,sentence_info=[child])])
        self.assertEqual(len(n.marks),2)

    def test_single_observed_token_is_kept_but_boundary_is_not_word_mark(self):
        n=funasr.normalize_funasr_result([dict(text='yes',timestamp=[[0,200]])])
        self.assertEqual(n.marks,[(0,.2)])
        n=funasr.normalize_funasr_result([dict(text='yes',start=0,end=200)])
        self.assertFalse(n.marks)

    def test_numpy_pairs(self):
        import numpy as np
        self.assertEqual(self.norm(np.array([[300,2100]])).status,'ASR_VALID')

    def test_model_loaded_once_for_multiple_regions(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        fake = Mock()
        fake.generate.return_value = [dict(text='yes',timestamp=[[0,200]])]
        factory = Mock(return_value=fake)
        with patch.dict(sys.modules, {'funasr':SimpleNamespace(AutoModel=factory)}), \
             patch.dict(funasr._MODEL_CACHE, {}, clear=True), \
             patch.object(funasr,'ffprobe_duration',return_value=1), \
             patch.object(funasr,'_local_model_dir',return_value=None):
            funasr._asr_funasr('first.wav','zh','cpu')
            funasr._asr_funasr('second.wav','zh','cpu')
            self.assertEqual(factory.call_count,1)
            self.assertEqual(fake.generate.call_count,2)


class Rescue(unittest.TestCase):
    def tearDown(self):
        speechmap.clear_active()
        _set_last_marks([])
        reset_caption_options()

    def rescue(self, dispatch, speech, rounds=5, fallback='faster-whisper', segs=None):
        report, marks = {}, []
        with patch.object(funasr,'cached_funasr_speech_ranges',return_value=speech), \
             patch.object(pipeline,'_slice_audio'), patch.object(pipeline,'_dispatch',side_effect=dispatch):
            result=pipeline._rescue_gaps('audio.wav',list(segs or []),300,'funasr','zh','tiny','cpu','int8',8,5,
                25,rounds,-45,marks_out=marks,fallback_backend=fallback,report_out=report)
        return result,report,marks

    def test_repaired_gap_does_not_reappear_and_offset_once(self):
        def dispatch(*args):
            _set_last_marks([(.35,1.35),(1.35,2.35)])
            return [Segment(0,.35,2.35,'hello world')],'en'
        segs,report,marks=self.rescue(dispatch,[(264,266)])
        self.assertEqual(len(report['attempts']),1)
        self.assertFalse(report['remaining'])
        self.assertEqual((segs[0].start,segs[0].end),(264,266))
        self.assertEqual(marks,[(264,265),(265,266)])

    def test_failures_change_strategy_and_stop(self):
        def fail(*args):
            raise funasr.FunASRResultError(funasr.normalize_funasr_result([]))
        segs,report,marks=self.rescue(fail,[(264,266)])
        self.assertEqual(len(report['attempts']),4)
        self.assertEqual([r['padding'] for r in report['attempts']],[.35,1,1,1])
        self.assertEqual(report['attempts'][-1]['engine'],'faster-whisper')
        self.assertEqual(len(report['rounds']),1)

    def test_context_conflict_does_not_poison_marks(self):
        def dispatch(*args):
            _set_last_marks([(0,1),(1,2)])
            return [Segment(0,0,4,'context and speech')],'en'
        segs,report,marks=self.rescue(dispatch,[(264,266)])
        self.assertFalse(segs)
        self.assertFalse(marks)
        self.assertTrue(report['remaining'])

    def test_context_trim_requires_real_token_alignment(self):
        segs=[Segment(0,0,3,'before hello after')]
        out=pipeline._target_segments(segs,[(10,11),(11,12),(12,13)],10,11,12)
        self.assertEqual([(s.start,s.end,s.text) for s in out],[(11,12,'hello')])
        self.assertFalse(pipeline._target_segments(segs,[(10,13)],10,11,12))

    def test_cjk_word_marks_trim_context(self):
        segs=[Segment(0,0,3,'他们现在没有')]
        out=pipeline._target_segments(segs,[(10,11),(11,12),(12,13)],10,11,12)
        self.assertEqual([(s.start,s.end,s.text) for s in out],[(11,12,'现在')])
        self.assertFalse(pipeline._target_segments(segs,[(10,13)],10,11,12))

    def test_cjk_character_marks_still_trim(self):
        segs=[Segment(0,0,6,'他们现在没有')]
        marks=[(10+i,11+i) for i in range(6)]
        out=pipeline._target_segments(segs,marks,10,12,14)
        self.assertEqual(''.join(s.text for s in out),'现在')

    def test_whisper_cjk_word_marks_fill_speech_gap(self):
        def dispatch(*args):
            _set_last_marks([(0,.35),(.35,1.35),(1.35,2.35)])
            return [Segment(0,0,2.35,'他们现在没有')],'zh'
        segs,report,marks=self.rescue(dispatch,[(264,266)])
        self.assertEqual(''.join(s.text for s in segs),'现在没有')
        self.assertFalse(report['remaining'])
        self.assertEqual(report['attempts'][0]['reason'],'fixed')

    def test_broken_fallback_import_is_not_retried_per_region(self):
        def dispatch(piece,engine,*args):
            if engine=='faster-whisper':
                raise ImportError('DLL blocked')
            return [],'zh'
        segs,report,marks=self.rescue(dispatch,[(5,7),(10,12)])
        attempts=[r for r in report['attempts'] if r['engine']=='faster-whisper']
        self.assertEqual(len(attempts),1)
        self.assertEqual(report['unavailable_engines'],['faster-whisper'])

    def test_force_retry_focus_and_short_slices(self):
        with tempfile.TemporaryDirectory() as td:
            audio=Path(td)/'audio.wav'; audio.write_bytes(b'x'*64)
            from autodub.asr.attempts import Attempts
            ledger=Attempts(str(audio))
            failed=ledger.key(263.65,276.35,'funasr','zh','tiny','cpu','int8',8,5,None,False)
            ledger.record(failed,dict(start=264,end=276,crop_start=263.65,crop_end=276.35,
                                      reason='ASR_EMPTY'))
            slices=[]
            def dispatch(*args,**kwargs):
                _set_last_marks([(.35,1.35),(1.35,2.35)])
                return [Segment(0,.35,2.35,'住手')],'zh'
            def slice_audio(src,cs,ce,dest):
                slices.append(round(ce-cs,2))
            report={}
            with patch.object(funasr,'cached_funasr_speech_ranges',return_value=[(264,288)]), \
                 patch.object(pipeline,'confirm_speech_holes',
                              side_effect=lambda audio,holes,db:(list(holes),[])), \
                 patch.object(pipeline,'_slice_audio',side_effect=slice_audio), \
                 patch.object(pipeline,'_dispatch',side_effect=dispatch):
                segs=pipeline._rescue_gaps(
                    str(audio),[],300,'funasr','zh','tiny','cpu','int8',8,5,
                    25,2,-45,marks_out=[],fallback_backend=None,report_out=report,
                    force_retry=True,focus=(264,288),max_slice=12)
            self.assertTrue(any((s.text or '').find('住')>=0 for s in segs))
            self.assertFalse(report.get('skipped_previous_failures'))
            self.assertTrue(slices)
            self.assertTrue(all(w <= 14.1 for w in slices))

    def test_music_not_speech_no_forced_subtitle(self):
        def forbidden(*args):
            self.fail('ASR must not run without VAD speech')
        self.assertFalse(self.rescue(forbidden,[])[0])

    def test_gate_significant_speech_still_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            rows=[dict(reason='unresolved_speech_gap',start=264,end=270,withheld=True,threshold_s=1.2)]
            with self.assertRaisesRegex(CaptionReviewRequired,'total=6.00s') as ctx:
                ensure_complete([],[],td,review=rows)
            data=json.loads(next(Path(td).glob('caption-review-*/review.json')).read_text())
            self.assertEqual(data[0]['threshold_s'],1.2)
            self.assertEqual(ctx.exception.gaps, [
                {"start": 264.0, "end": 270.0, "reason": "unresolved_speech_gap",
                 "play_start": 262.5, "play_end": 271.0},
            ])

    def test_confirmed_speech_gap_blocks_even_with_existing_pack(self):
        with tempfile.TemporaryDirectory() as td:
            rows=[dict(reason='unresolved_speech_gap',start=264,end=270,withheld=True,threshold_s=1.2)]
            cues=[Segment(1,0,1,'hello')]
            with self.assertRaises(CaptionReviewRequired):
                ensure_complete(cues,[Segment(1,0,1,'hello')],td,review=rows)
            self.assertTrue(list(Path(td).glob('caption-review-*')))

    def test_human_non_speech_ack_clears_energy_false_positives(self):
        from autodub.asr.nonspeech import save_non_speech
        with tempfile.TemporaryDirectory() as td:
            save_non_speech(td, [{"start": 264, "end": 270, "reason": "unresolved_speech_gap",
                                  "note": "crickets"}])
            rows=[dict(reason='unresolved_speech_gap',start=264,end=270,withheld=True,threshold_s=1.2)]
            cues=[Segment(1,0,1,'活下去')]
            ensure_complete(cues,[Segment(1,0,1,'活下去')],td,review=rows)
            self.assertFalse(list(Path(td).glob('caption-review-*')))
            self.assertEqual(cues[0].text, '活下去')

    def test_non_speech_ack_does_not_clear_clock_corruption(self):
        from autodub.asr.nonspeech import save_non_speech
        with tempfile.TemporaryDirectory() as td:
            save_non_speech(td, [{"start": 0, "end": 2, "reason": "unresolved_speech_gap"}])
            with self.assertRaisesRegex(CaptionReviewRequired,'invalid_source_clock'):
                ensure_complete([], [Segment(1,float('nan'),1,'bad')],td,review=[])

    def test_source_clock_corruption_blocks_even_without_packer_review(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(CaptionReviewRequired,'invalid_source_clock'):
                ensure_complete([], [Segment(1,float('nan'),1,'bad')],td,review=[])

    def test_speech_coverage_excludes_music(self):
        m=speech_coverage_report([Segment(1,10,20,'hello')],[(10,20)],300)
        self.assertEqual(m['speech_coverage_percent'],100)

    def test_gap_never_merges_across_short_subtitle(self):
        holes=find_uncovered_speech_ranges([Segment(1,2,2.2,'yes')],[(0,5)],5,
            min_gap=1,edge_pad=0,subtitle_pad=0)
        self.assertEqual(holes,[(0,2),(2.2,5)])

    def test_source_covering_pack_hole_is_not_a_recognition_gap(self):
        source=[Segment(1,0,5,'嘿 那')]
        self.assertFalse(find_uncovered_speech_ranges(
            source,[(1,4)],5,min_gap=1.2,edge_pad=0,subtitle_pad=0))
        packed=[Segment(1,0,1.5,'嘿'),Segment(2,3.2,5,'那')]
        self.assertEqual(find_uncovered_speech_ranges(
            packed,[(1,4)],5,min_gap=1.2,edge_pad=0,subtitle_pad=0),[(1.5,3.2)])

    def test_clip_to_uncovered_shrinks_after_neighbor(self):
        self.assertEqual(pipeline.clip_to_uncovered(10.999, 13, 11, 13, [(0, 11.2)]), (11.2, 13))
        self.assertEqual(pipeline.clip_to_uncovered(11, 13, 11, 13, [(0, 11), (12.8, 14)]), (11, 12.8))
        self.assertIsNone(pipeline.clip_to_uncovered(11, 13, 11, 13, [(10.5, 13.5)]))

    def test_neighbor_one_ms_into_gap_still_places_fill(self):
        def dispatch(*args):
            _set_last_marks([(.35,1.35),(1.35,2.35)])
            return [Segment(0,.35,2.35,'hello world')],'en'
        segs,report,_=self.rescue(dispatch,[(264,266)], segs=[Segment(1,260,264.0005,'before')])
        fill=[s for s in segs if 'hello' in (s.text or '')]
        self.assertTrue(fill)
        self.assertGreaterEqual(fill[0].start, 264.0005 - 1e-6)
        self.assertFalse(report['remaining'])

    def test_stitch_mid_phrase_covers_vad_hole(self):
        segs=[Segment(1,0,1.0,'来人把肉带'), Segment(2,2.5,3.5,'上光虎。')]
        out=pipeline.stitch_split_utterances(segs, [(0,3.5)], 4, min_gap=1.2)
        self.assertEqual([(s.start,s.end,s.text) for s in out], [(0,3.5,'来人把肉带上光虎。')])
        self.assertFalse(find_uncovered_speech_ranges(out,[(0,3.5)],4,min_gap=1.2,edge_pad=0,subtitle_pad=0))

    def test_stitch_skips_sentence_terminal(self):
        segs=[Segment(1,0,1,'宵夜。'), Segment(2,2.5,4,'这几日全靠那便宜老婆捡来')]
        out=pipeline.stitch_split_utterances(segs, [(0,4)], 5, min_gap=1.2)
        self.assertEqual(len(out), 2)

    def test_stitch_attaches_particle_after_terminal(self):
        segs=[Segment(1,0,1,'你都不知道我花了多少年'), Segment(2,4.1,4.4,'呀。')]
        out=pipeline.stitch_split_utterances(segs, [(1.5,4.1)], 5, min_gap=1.2, max_gap=3.5)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].text.endswith('呀。'))
        self.assertFalse(find_uncovered_speech_ranges(out,[(1.5,4.1)],5,min_gap=1.2,edge_pad=0,subtitle_pad=0))

    def test_stitch_does_not_join_distant_sentences(self):
        segs=[Segment(1,0,1,'宵夜。'), Segment(2,10,12,'这几日')]
        out=pipeline.stitch_split_utterances(segs, [(4,5.4)], 13, min_gap=1.2)
        self.assertEqual(len(out), 2)

    def test_stitch_skips_english_left_over(self):
        segs=[Segment(1,0,1,'you'), Segment(2,3,4,'蛇月果夫君？')]
        out=pipeline.stitch_split_utterances(segs, [(0,4)], 5, min_gap=1.2)
        self.assertEqual(len(out), 2)

    def test_crop_extends_late_start_right_cue(self):
        segs=[Segment(1,0,1.0,'这麦粒真有那么好吃吗？'),
              Segment(2,2.29,3.1,'麦粒有灵力，')]
        out=pipeline.apply_crop_to_hole(segs, 1.0, 2.29, '麦里有灵力。')
        self.assertEqual(out[-1].start, 1.0)
        self.assertEqual(out[-1].text, '麦粒有灵力，')
        self.assertFalse(find_uncovered_speech_ranges(out,[(1.0,2.29)],4,min_gap=1.2,edge_pad=0,subtitle_pad=0))

    def test_crop_extends_left_when_only_previous_sentence(self):
        segs=[Segment(1,0,1.0,'秦云这件老爷的货送到快开门。'),
              Segment(2,3.5,4.5,'这位壮士是，')]
        out=pipeline.apply_crop_to_hole(segs, 1.0, 3.07, '送到快开门。')
        self.assertAlmostEqual(out[0].end, 3.07)
        self.assertEqual(len(out), 2)

    def test_crop_does_not_insert_short_cjk_without_neighbor_match(self):
        segs=[Segment(1,0,1.0,'就你这三脚猫的功夫成狂。'),
              Segment(2,3.0,3.3,'嗯，')]
        self.assertIsNone(pipeline.apply_crop_to_hole(segs, 1.2, 2.4, '哇。'))
        out=pipeline.stitch_split_utterances(segs, [(1.2,2.4)], 4, min_gap=1.2)
        self.assertEqual(len(out), 1)
        self.assertFalse(find_uncovered_speech_ranges(out,[(1.2,2.4)],4,min_gap=1.2,edge_pad=0,subtitle_pad=0))

    def test_crop_refuses_english_context_dump(self):
        segs=[Segment(1,0,1,'before'), Segment(2,3,4,'after')]
        self.assertIsNone(pipeline.apply_crop_to_hole(segs, 1.0, 2.5, 'context and speech'))

    def test_crop_refuses_latin_cjk_garbage(self):
        segs=[Segment(1,0,1,'好吃吗？'), Segment(2,2.3,3,'麦粒有灵力，')]
        self.assertIsNone(pipeline.apply_crop_to_hole(segs, 1.0, 2.3, 'My物。'))

    def test_film_remaining_holes_cover_from_crop_ten_times(self):
        cases=[
            (819.1,820.39,'这麦粒真有那么好吃吗？','麦粒有灵力，','麦里有灵力。'),
            (1888.58,1889.92,'你找这人干啥？','没傻，','你找这人干啥， 没傻就随你。'),
            (2426.28,2427.7,'蔬菜瞬间消失。','现在阵法的持续时间能达到十天了，','才瞬间消失。 现在阵法的持续。'),
            (2825.2,2826.56,'喊他们来赔罪握手言和如何？','呵呵实不相瞒，','言和如何？ 呵呵实不相。'),
            (2843.91,2845.31,'看不起我也笑脸相送。','钱。','也笑脸相送 钱老哥严重。'),
            (2916.82,2918.12,'日后可要好好做人，让你知道一下什么是人形险恶。','不好意思，','什么是人形？ 险恶？ 不好意思， 我。'),
            (2928.66,2930.73,'秦云这件老爷的货送到快开门。','这位壮士是，','送到快开门。'),
            (4784.99,4786.27,'你也不想死吧。','狼王十分通人性，','你也不想死吧， 狼王十分通人。'),
            (5204.795,5206.35,'下次见面能有更深的认识。','虾兄弟，','认识夏 兄弟老。'),
            (5596.985,5598.365,'老爷虾哥回来了。','老爷','虾人回来了， 钱老爷。'),
            (6000.55,6003.38,'you','蛇月果夫君？','蛇月果。'),
            (6440.25,6441.509,'千年份的火焰应该更强吧。','肯定百年黄火，','应该更强吧， 肯定 百。'),
        ]
        particle=(2235.46,2236.69,'就你这三脚猫的功夫成狂。','嗯，','哇。')
        for _ in range(10):
            uncovered=[]
            for gs,ge,left,right,crop in cases:
                segs=[Segment(1,gs-2,gs,left), Segment(2,ge,ge+2,right)]
                out=pipeline.apply_crop_to_hole(segs,gs,ge,crop)
                self.assertIsNotNone(out, msg=f'{gs} {crop}')
                holes=find_uncovered_speech_ranges(out,[(gs,ge)],ge+3,min_gap=1.2,edge_pad=0,subtitle_pad=0)
                if holes:
                    uncovered.append((gs,holes,crop))
            self.assertFalse(uncovered)
            gs,ge,left,right,crop=particle
            segs=[Segment(1,gs-2,gs,left), Segment(2,ge,ge+2,right)]
            self.assertIsNone(pipeline.apply_crop_to_hole(segs,gs,ge,crop))
            stitched=pipeline.stitch_split_utterances(segs,[(gs,ge)],ge+3,min_gap=1.2)
            holes=find_uncovered_speech_ranges(stitched,[(gs,ge)],ge+3,min_gap=1.2,edge_pad=0,subtitle_pad=0)
            self.assertFalse(holes)

    def test_rescue_waits_for_longer_pad_before_neighbor_cover(self):
        segs=[Segment(1,260,262,'you'), Segment(2,265,267,'蛇月果夫君？')]
        texts=['手有。','蛇月果。','蛇月果。','蛇月果。']
        def dispatch(*args):
            _set_last_marks([])
            return [Segment(0,0,1,texts.pop(0) if texts else '蛇月果。')],'zh'
        out,report,_=self.rescue(dispatch,[(262,265)], segs=segs)
        self.assertFalse(any('手有' in (s.text or '') for s in out))
        self.assertTrue(any(abs(s.start-262)<1e-6 and '蛇月果' in (s.text or '') for s in out))
        self.assertTrue(any(r.get('after_all_pads') for r in report['attempts']))

    def test_rescue_uses_crop_when_marks_cannot_place(self):
        segs=[Segment(1,262,264,'这麦粒真有那么好吃吗？'),
              Segment(2,266,268,'麦粒有灵力，')]
        def dispatch(*args):
            _set_last_marks([])
            return [Segment(0,0,2,'麦里有灵力。')],'zh'
        out,report,_=self.rescue(dispatch,[(264,266)], segs=segs)
        self.assertFalse(report['remaining'])
        self.assertTrue(any(abs(s.start-264)<1e-6 and '灵力' in (s.text or '') for s in out))

    def test_silent_vad_hole_is_not_rescued(self):
        def forbidden(*args):
            self.fail('ASR must not run on a silent VAD hole')
        with patch.object(pipeline,'confirm_speech_holes',
                          return_value=([], [(264.0, 266.0, -50.0)])):
            segs,report,_=self.rescue(forbidden,[(264,266)])
        self.assertFalse(segs)
        self.assertFalse(report['remaining'])

    def test_energy_keep_when_audio_missing(self):
        from autodub.asr.detect import confirm_speech_holes
        kept, dropped = confirm_speech_holes('missing-audio.wav', [(1.0, 3.0)], -42.0)
        self.assertEqual(kept, [(1.0, 3.0)])
        self.assertFalse(dropped)

    def test_energy_drops_quiet_slice(self):
        from autodub.asr.detect import confirm_speech_holes
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(b'x' * 200)
            path = handle.name
        def slicer(src, start, end, out):
            Path(out).write_bytes(b'x' * 200)
        try:
            kept, dropped = confirm_speech_holes(
                path, [(1.0, 3.0)], -42.0, slicer=slicer, measurer=lambda p: -50.0)
        finally:
            os.remove(path)
        self.assertFalse(kept)
        self.assertEqual(dropped[0][:2], (1.0, 3.0))
