import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autodub.asr import funasr as F, long_audio as L
from autodub.asr.common import reset_caption_options


class LongAudio(unittest.TestCase):
    def tearDown(self):
        reset_caption_options()
        L.LAST_RUN.set(None)

    def extract(self, audio, path, start, duration):
        Path(path).write_text(json.dumps([start,duration]))

    def generate(self, model, path):
        a,d = json.loads(Path(path).read_text())
        return [dict(text='一句话', timestamp=[[i*1000,(i+1)*1000]]) for i in range(int(d))]

    def test_checkpoint_crash_and_resume_only_failed_chunk(self):
        with tempfile.TemporaryDirectory() as td:
            audio=Path(td)/'input.wav'; audio.write_bytes(b'fixture')
            calls=[]
            def generate(model,path):
                a,d=json.loads(Path(path).read_text());calls.append(a)
                if a==10:raise RuntimeError('injected crash')
                return self.generate(model,path)
            args=(object(),str(audio),'zh',15,5,0)
            with self.assertRaisesRegex(RuntimeError,'injected'):
                L.recognize(*args,generate,self.extract,F.normalize_funasr_result)
            self.assertEqual(calls,[0,5,10])
            root=Path(L.LAST_RUN.get()['checkpoint_dir'])
            self.assertEqual(len(list(root.glob('chunk_*.srt'))),2)
            self.assertTrue(L.working_path(audio).read_text(encoding='utf-8').strip())
            calls.clear()
            def resume(model,path):
                calls.append(json.loads(Path(path).read_text())[0])
                return self.generate(model,path)
            out,_=L.recognize(*args,resume,self.extract,F.normalize_funasr_result)
            self.assertEqual(calls,[10])
            self.assertEqual(len(out),15)

    def test_suspicious_parent_splits_with_speech_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            audio=Path(td)/'input.wav'; audio.write_bytes(b'fixture')
            def generate(model,path):
                a,d=json.loads(Path(path).read_text())
                return ([dict(text='很少',timestamp=[[0,d*1000]])] if d>60
                        else self.generate(model,path))
            out,_=L.recognize(object(),str(audio),'zh',120,120,0,generate,
                             self.extract,F.normalize_funasr_result,speech=[(0,120)],min_seconds=15)
            self.assertEqual(len(out),120)
            report=L.LAST_RUN.get()
            self.assertEqual(report['chunks'][0]['status'],'CHUNK_SUSPICIOUS')
            self.assertEqual(report['unresolved'],[])
            self.assertTrue((Path(report['checkpoint_dir'])/'chunk_0001.raw.json').exists())

    def test_long_input_never_invokes_whole_file_inference(self):
        model=object()
        with patch.dict(sys.modules, {'funasr':Mock()}), \
             patch.dict(F._MODEL_CACHE,{('funasr','paraformer-zh','cpu'):model}), \
             patch.object(F,'ffprobe_duration',return_value=7200), \
             patch.object(F,'_asr_funasr_chunked',return_value=([], 'zh')) as chunk, \
             patch.object(F,'_funasr_generate') as whole:
            F._asr_funasr('two-hours.wav','zh','cpu')
            whole.assert_not_called()
            chunk.assert_called_once()

    def test_silence_boundary_and_alignment_not_giant_single_segment(self):
        self.assertAlmostEqual(L.safe_boundary(60,0,120,[(0,58),(61,120)]),60)
        result=F.normalize_funasr_result([dict(text='中'*200,
            timestamp=[[i*1000,(i+1)*1000] for i in range(200)])],duration_hint=200)
        self.assertGreater(len(result.segments),50)
        self.assertEqual(''.join(s.text for s in result.segments),'中'*200)

    def test_sentence_case_change_does_not_discard_all_sentence_clocks(self):
        raw=[dict(text='Go，回家。',timestamp=[[0,100],[100,200],[200,300]],
                  sentence_info=[dict(text='go，',timestamp=[[0,100]]),
                                 dict(text='回家。',timestamp=[[100,200],[200,300]])])]
        n=F.normalize_funasr_result(raw,duration_hint=1)
        self.assertEqual(n.status,'ASR_VALID')
        self.assertEqual(len(n.segments),2)
        self.assertEqual(len(n.marks),3)

    def test_attempt_ledger_survives_new_instance_and_parameters_are_distinct(self):
        from autodub.asr.attempts import Attempts
        with tempfile.TemporaryDirectory() as td:
            audio=Path(td)/'a.wav';audio.write_bytes(b'audio')
            a=Attempts(audio)
            key=a.key(0,3,'paraformer','zh','large','cpu','int8',8,5,None,False)
            a.record(key,dict(reason='ASR_EMPTY'))
            self.assertTrue(Attempts(audio).failed(key))
            other=a.key(0,3,'paraformer','zh','large','cpu','int8',8,5,None,True)
            self.assertFalse(Attempts(audio).failed(other))

    def test_forget_range_drops_overlapping_failures(self):
        from autodub.asr.attempts import Attempts
        with tempfile.TemporaryDirectory() as td:
            audio=Path(td)/'a.wav';audio.write_bytes(b'audio')
            a=Attempts(audio)
            keep=a.key(10,12,'paraformer','zh','large','cpu','int8',8,5,None,False)
            drop=a.key(4193.08,4213.08,'paraformer','zh','large','cpu','int8',8,5,None,False)
            a.record(keep,dict(start=10,end=12,reason='ASR_EMPTY'))
            a.record(drop,dict(start=4193.08,end=4213.08,reason='ASR_EMPTY'))
            self.assertEqual(a.forget_range(4193.08,4268.44),1)
            again=Attempts(audio)
            self.assertTrue(again.failed(keep))
            self.assertFalse(again.failed(drop))

    def test_empty_speech_leaf_splits_below_min_seconds(self):
        with tempfile.TemporaryDirectory() as td:
            audio=Path(td)/'input.wav'; audio.write_bytes(b'fixture')
            def generate(model,path):
                _a,d=json.loads(Path(path).read_text())
                if d >= 24:
                    return [dict(text='',timestamp=[])]
                return [dict(text='住手',timestamp=[[0,int(d*1000)]])]
            out,_=L.recognize(object(),str(audio),'zh',75,75,0,generate,
                             self.extract,F.normalize_funasr_result,speech=[(0,75)],
                             min_seconds=60, max_depth=4)
            self.assertTrue(any('住手' in (s.text or '') for s in out))
            self.assertFalse(L.LAST_RUN.get()['unresolved'])

    def test_whisper_reuses_model_and_receives_known_language(self):
        from autodub.asr import whisper as W
        from types import SimpleNamespace
        model=Mock()
        model.transcribe.side_effect=[([],SimpleNamespace(language='zh'))]*2
        api=Mock();api.WhisperModel.return_value=model
        with patch.dict(sys.modules,{'faster_whisper':api}),patch.dict(W._MODEL_CACHE,{},clear=True):
            for _ in range(2):W._asr_faster_whisper('clip.wav','zh','small','cpu','int8')
        self.assertEqual(api.WhisperModel.call_count,1)
        self.assertEqual(model.transcribe.call_args.kwargs['language'],'zh')

    def test_parent_token_clocks_win_over_shifted_sentence_info(self):
        raw=[dict(text='你好世界。',timestamp=[[0,100],[100,200],[200,300],[300,400]],
                  sentence_info=[dict(text='你好世界。',timestamp=[[0,100],[100,200]]),
                                 dict(text='，',timestamp=[[200,300],[300,400]])])]
        n=F.normalize_funasr_result(raw,duration_hint=1)
        self.assertEqual(''.join(s.text for s in n.segments),'你好世界。')
        self.assertAlmostEqual(n.segments[-1].end,.4)
        self.assertFalse(any(not any(c.isalnum() for c in s.text) for s in n.segments))

    def test_parser_upgrade_reuses_raw_without_inference(self):
        with tempfile.TemporaryDirectory() as td:
            audio=Path(td)/'input.wav';audio.write_bytes(b'fixture')
            args=(object(),str(audio),'zh',10,5,0)
            expected,_=L.recognize(*args,self.generate,self.extract,F.normalize_funasr_result)
            with patch.object(L,'VERSION',L.VERSION+1):
                actual,_=L.recognize(*args,lambda *a:self.fail('raw cache should avoid ASR'),
                                     self.extract,F.normalize_funasr_result)
            self.assertEqual([(s.start,s.text) for s in actual],[(s.start,s.text) for s in expected])
            self.assertTrue(all(c['reused_raw'] for c in L.LAST_RUN.get()['chunks']))

    def test_reextract_reuses_raw_when_audio_hash_changes(self):
        with tempfile.TemporaryDirectory() as td:
            audio=Path(td)/'input.wav';audio.write_bytes(b'fixture-1')
            args=(object(),str(audio),'zh',10,5,0)
            L.recognize(*args,self.generate,self.extract,F.normalize_funasr_result)
            first=L.LAST_RUN.get()['checkpoint_dir']
            audio.write_bytes(b'fixture-2-different-bytes')
            actual,_=L.recognize(*args,lambda *a:self.fail('re-extract should reuse FunASR raw'),
                                 self.extract,F.normalize_funasr_result)
            self.assertTrue(actual)
            self.assertNotEqual(L.LAST_RUN.get()['checkpoint_dir'], first)
            self.assertTrue(all(c['reused_raw'] for c in L.LAST_RUN.get()['chunks']))

    def test_compatible_checkpoint_rejects_other_model(self):
        base=dict(version=2,audio_sha256='aaa',duration=10,model='paraformer',
                  language='zh',options={},chunk_seconds=600,overlap=1.5,
                  min_seconds=60,max_depth=4)
        other=dict(base,audio_sha256='bbb',model='whisper')
        self.assertTrue(L.compatible_checkpoint(dict(base,audio_sha256='bbb'), base))
        self.assertFalse(L.compatible_checkpoint(other, base))
        self.assertTrue(L.compatible_checkpoint(dict(base,language=None), base))

    def test_frozen_chunk_plan_ignores_later_vad_jitter(self):
        with tempfile.TemporaryDirectory() as td:
            audio=Path(td)/'input.wav'; audio.write_bytes(b'fixture')
            args=(object(),str(audio),'zh',10,5,0)
            L.recognize(*args,self.generate,self.extract,F.normalize_funasr_result,
                        speech=[(0,10)])
            plan=Path(L.LAST_RUN.get()['checkpoint_dir'])/'chunk_plan.json'
            self.assertTrue(plan.exists())
            L.recognize(*args,lambda *a:self.fail('frozen plan must not re-infer'),
                        self.extract,F.normalize_funasr_result,
                        speech=[(0,3),(4.5,10)])
            self.assertFalse(L.LAST_RUN.get()['chunks'])

    def test_overlap_context_is_partitioned_by_observed_token(self):
        raw=[dict(text='你好世界',timestamp=[[0,1000],[1000,2000],[2000,3000],[3000,4000]])]
        n=F.normalize_funasr_result(raw,duration_hint=4)
        left,lm=L.owned_result(n,0,2);right,rm=L.owned_result(n,2,4)
        self.assertEqual(''.join(s.text for s in left+right),'你好世界')
        self.assertFalse(set(lm)&set(rm))


if __name__=='__main__':unittest.main()
