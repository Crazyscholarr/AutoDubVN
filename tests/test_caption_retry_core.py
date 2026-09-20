"""Focused retry regressions: real review gate, offline recognizer and temporary media."""
import json
import tempfile
import threading
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

from autodub import speechmap
from autodub.server import pipeline as sp, state
from autodub.srt_utils import Segment, save_srt_file


class CaptionRetryCoreTests(unittest.TestCase):
    def test_cloned_remote_cues_keep_their_original_text_clock_and_identity(self):
        from dataclasses import replace
        source = [Segment(1,203.05,206.29,'远处'), Segment(2,14054.35,14054.9,'原文')]
        result = [replace(source[0],text='不应改变'), Segment(2,14054.35,14054.9,'新文')]
        merged = sp._isolate_retry_result(source,result,14052.85,14055.9)
        self.assertIs(merged[0],source[0])
        self.assertEqual(merged[0].text,'远处')
        self.assertEqual(merged[1].text,'新文')

    def test_cross_boundary_retry_cannot_overwrite_neighbor(self):
        source=[Segment(1,8,10,'前'),Segment(2,10,12,'原文'),Segment(3,12,14,'后')]
        result=[Segment(1,9,13,'越界')]
        self.assertEqual(sp._isolate_retry_result(source,result,10,12),source)

    def test_retry_cloning_all_source_cues_preserves_remote_speech_marks(self):
        from dataclasses import replace
        source=[Segment(1,1,2,'远处'),Segment(2,10,12,'原文')]
        with self.environment(source) as (root,job,_,saved):
            speechmap.SpeechMap([(1,1.5),(1.5,2),(10,11),(11,12)]).save(
                str(root/'output/film/_tmp/caption-review-test/speechmap.json'))
            def recognize(audio,cues,*args,**kwargs):
                kwargs['marks_out'].extend([(10,10.5),(10.5,12)])
                return [replace(s) for s in cues]+[Segment(2,10,12,'新文')]
            with mock.patch('autodub.asr.pipeline._rescue_gaps',side_effect=recognize):
                sp.rerecognize_caption_gap(9,10,12)
            self.assertEqual(speechmap.get_active().marks,[(1,1.5),(1.5,2),(10,10.5),(10.5,12)])

    @contextmanager
    def environment(self, original=(), gap=(10., 12.), vad=None):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            root = Path(td)
            folder = root / 'output/film/_tmp/caption-review-test'
            folder.mkdir(parents=True)
            (root / 'film.mp4').write_bytes(b'fake')
            (folder.parent / 'audio16k.wav').write_bytes(b'fake')
            (folder / 'unresolved.json').write_text(json.dumps([
                dict(start=gap[0], end=gap[1], reason='missing_speech_marks', withheld=True)
            ]), encoding='utf-8')
            save_srt_file(str(folder / 'source.srt'), original)
            job = dict(id=9, path=str(root/'film.mp4'), review_dir=str(folder),
                       status='cần kiểm tra', result_status='REVIEW_REQUIRED')
            project = dict(video=str(root/'film.mp4'), duration=100., picture_duration=100.,
                           w=1280, h=720, options={}, segments=[], clocks={})
            token = threading.Event()
            for target, values in [(state.STATE, dict(queue=[job], running=True)),
                                   (state.PROJECTS, {9: project})]:
                stack.enter_context(mock.patch.dict(target, values))
            for target, name, kwargs in [
                (sp, 'HERE', dict(new=td)),
                (sp, '_load_cfg', dict(return_value={'asr': {}})),
                (sp, 'current_cancel_event', dict(return_value=token)),
                (sp, '_save_project_state', {}),
                (sp, '_load_existing_segments_into_project', {}),
            ]:
                stack.enter_context(mock.patch.object(target, name, **kwargs))
            stack.enter_context(mock.patch('autodub.utils.ffprobe_duration', return_value=100.))
            stack.enter_context(mock.patch('autodub.asr.merge.normalize_segments', side_effect=lambda s, n:s))
            stack.enter_context(mock.patch('autodub.asr.screen_pack.last_review', return_value=[]))
            stack.enter_context(mock.patch('autodub.asr.funasr.cached_funasr_speech_ranges', return_value=vad))
            stack.enter_context(mock.patch('autodub.asr.detect.confirm_speech_holes', side_effect=lambda p,h,d:(h,[])))
            saved = stack.enter_context(mock.patch('autodub.asr.long_audio.save_working'))
            try:
                yield root, job, token, saved
            finally:
                speechmap.clear_active()

    def test_existing_text_is_removed_from_retry_input_and_replaced(self):
        old = Segment(1, 10, 12, '旧文字')
        with self.environment([old]) as (root, job, token, saved):
            def recognize(audio, cues, *args, **kwargs):
                self.assertEqual(cues, [])
                self.assertEqual(kwargs['focus'], (10., 12.))
                return [Segment(1, 10, 12, '新文字')]
            with mock.patch('autodub.asr.pipeline._rescue_gaps', side_effect=recognize):
                sp.rerecognize_caption_gap(9, 10, 12)
            self.assertEqual(job['status'], 'chờ')
            text = (root/'output/film/film.src.srt').read_text(encoding='utf-8')
            self.assertIn('新文字', text)
            self.assertNotIn('旧文字', text)
            self.assertFalse(state.STATE['running'])

    def test_partial_recovery_cannot_clear_review(self):
        with self.environment() as (_, job, _, saved):
            with mock.patch('autodub.asr.pipeline._rescue_gaps', return_value=[Segment(1,10,10.5,'词')]):
                sp.rerecognize_caption_gap(9, 10, 12)
            self.assertEqual(job['result_status'], 'REVIEW_REQUIRED')
            self.assertEqual(job['status'], 'cần kiểm tra')

    def test_new_word_clocks_replace_old_clocks_for_recovered_text(self):
        with self.environment([Segment(1,10,12,'旧')]) as (root, job, _, saved):
            speechmap.SpeechMap([(0,.5),(10,10.8),(10.8,12)]).save(
                str(root/'output/film/_tmp/caption-review-test/speechmap.json'))
            def recognize(*args, **kwargs):
                kwargs['marks_out'].extend([(10,11),(11,12)])
                return [Segment(1,10,12,'新词')]
            with mock.patch('autodub.asr.pipeline._rescue_gaps', side_effect=recognize):
                sp.rerecognize_caption_gap(9, 10, 12)
            self.assertEqual(speechmap.get_active().marks, [(0,.5),(10,11),(11,12)])

    def test_short_retry_reopens_neighboring_context_instead_of_skipping(self):
        originals = [Segment(1, 9, 10, '前'), Segment(2, 10, 10.3, '字'),
                     Segment(3, 10.3, 11, '后')]
        with self.environment(originals, gap=(10., 10.3)) as (_, job, _, saved):
            def recognize(audio, cues, *args, **kwargs):
                self.assertEqual(cues, [])
                self.assertEqual(kwargs['focus'], (8.5, 11.3))
                return originals
            with mock.patch('autodub.asr.pipeline._rescue_gaps', side_effect=recognize):
                sp.rerecognize_caption_gap(9, 10, 10.3)
            self.assertEqual(job['status'], 'chờ')

    def test_empty_retry_keeps_original_text_and_review(self):
        original = Segment(1, 10, 12, '原文')
        with self.environment([original]) as (_, job, _, saved):
            with mock.patch('autodub.asr.pipeline._rescue_gaps', return_value=[]):
                sp.rerecognize_caption_gap(9, 10, 12)
            self.assertEqual(job['result_status'], 'REVIEW_REQUIRED')
            self.assertEqual(saved.call_args.args[1], [original])

    def test_discarded_partial_retry_cannot_validate_restored_old_text(self):
        original = Segment(1,10,12,'原文')
        with self.environment([original], vad=[(10,10.5)]) as (_, job, _, saved):
            with mock.patch('autodub.asr.pipeline._rescue_gaps', return_value=[Segment(1,10,10.5,'新')]):
                sp.rerecognize_caption_gap(9, 10, 12)
            self.assertEqual(job['result_status'], 'REVIEW_REQUIRED')
            self.assertEqual(saved.call_args.args[1], [original])

    def test_vad_confirmed_pause_does_not_require_invented_subtitle(self):
        cues = [Segment(1,10,10.5,'一'), Segment(2,11.5,12,'二')]
        with self.environment(vad=[(10,10.5),(11.5,12)]) as (_, job, _, saved):
            with mock.patch('autodub.asr.pipeline._rescue_gaps', return_value=cues):
                sp.rerecognize_caption_gap(9, 10, 12)
            self.assertEqual(job['status'], 'chờ')

    def test_cancel_after_recognition_does_not_save_checkpoint(self):
        with self.environment() as (_, job, token, saved):
            def recognize(*a, **kw):
                token.set()
                return [Segment(1,10,12,'词')]
            with mock.patch('autodub.asr.pipeline._rescue_gaps', side_effect=recognize):
                sp.rerecognize_caption_gap(9, 10, 12)
            saved.assert_not_called()
            self.assertEqual(job['status'], 'đã huỷ')
            self.assertFalse(state.STATE['running'])

    def test_cancel_in_final_vad_does_not_save_checkpoint(self):
        with self.environment() as (_, job, _, saved):
            with mock.patch('autodub.asr.pipeline._rescue_gaps', return_value=[Segment(1,10,12,'词')]), \
                 mock.patch('autodub.asr.funasr.cached_funasr_speech_ranges', side_effect=InterruptedError):
                sp.rerecognize_caption_gap(9, 10, 12)
            saved.assert_not_called()
            self.assertEqual(job['status'], 'đã huỷ')

    def test_project_load_failure_releases_running(self):
        with self.environment() as (_, job, _, saved):
            with mock.patch.object(sp, 'get_project', side_effect=RuntimeError('bad project')):
                sp.rerecognize_caption_gap(9, 10, 12)
            self.assertFalse(state.STATE['running'])
            self.assertEqual(job['status'], 'lỗi')

    def test_queued_cancel_releases_running_preserves_review(self):
        job = dict(id=9, result_status='REVIEW_REQUIRED')
        with mock.patch.dict(state.STATE, queue=[job], running=True), \
             mock.patch.object(state, 'JOB_MANAGER') as manager:
            state.submit_job(lambda:None, name='retry', metadata=dict(kind='asr_rerecognize',queue_id=9))
            manager.submit.call_args.kwargs['on_cancel']()
            self.assertFalse(state.STATE['running'])
            self.assertEqual(job['result_status'], 'REVIEW_REQUIRED')

    def test_restoring_partial_source_does_not_drop_neighbor(self):
        originals = [Segment(1,0,1,'一'), Segment(2,1,2,'二')]
        candidates = [Segment(3,0,1.5,'一二')]
        self.assertEqual(sp._restore_retry_source(originals,candidates), originals)

    def test_partial_focus_does_not_clear_larger_review_interval(self):
        prior = [dict(start=10,end=20,withheld=True)]
        result = sp._union_prior_review(prior,10,12,[Segment(1,10,12,'词')],[])
        self.assertEqual(len(result),1)

    def test_half_second_gap_gets_context(self):
        self.assertEqual(sp._expand_rerecognize_window(10,10.5,100),(8.5,11.5,True))

    def test_invalid_clock_rejected(self):
        for start,end,duration in [(float('nan'),12,100),(10,float('inf'),100),(-1,1,100),(101,102,100)]:
            with self.subTest(start=start,end=end), self.assertRaises(ValueError):
                sp._expand_rerecognize_window(start,end,duration)
