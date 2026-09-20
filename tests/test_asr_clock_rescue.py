import unittest
from unittest.mock import patch

from autodub import speechmap
from autodub.asr import repair_clocks, pipeline
from autodub.asr.common import _set_last_marks, reset_caption_options
from autodub.srt_utils import Segment


class ClockRescue(unittest.TestCase):
    def tearDown(self):
        speechmap.clear_active()
        _set_last_marks([])
        reset_caption_options()

    def run_rescue(self,text):
        source=[Segment(1,0,2.4,'活下去')]
        marks=[(0,.2),(2,2.2),(2.2,2.4)]
        report={}
        def dispatch(*args):
            _set_last_marks([(0,.2),(.2,.4),(.4,.6)])
            return [Segment(1,0,.6,text)],'zh'
        with patch.object(repair_clocks,'_slice_audio'):
            result=repair_clocks.rescue_caption_clocks('audio',source,marks,
                [dict(start=0,end=2.4,reason='word_crosses_pause',withheld=True)],3,
                'paraformer','zh','tiny','cpu','int8',8,5,None,None,16,report,
                dispatch,pipeline._target_segments)
        return result,marks,report

    def test_rescue_replaces_clocks_only_when_words_agree(self):
        result,marks,report=self.run_rescue('活下去')
        self.assertEqual(result[0].text,'活下去')
        self.assertEqual(result[0].end,.6)
        self.assertEqual(marks,[(0,.2),(.2,.4),(.4,.6)])
        self.assertEqual([r['reason'] for r in report['clock_attempts']],['fixed'])

    def test_disagreement_does_not_rewrite_or_drop_source(self):
        result,marks,report=self.run_rescue('不存在')
        self.assertEqual(result[0].text,'活下去')
        self.assertEqual(result[0].end,2.4)
        self.assertEqual(marks,[(0,.2),(2,2.2),(2.2,2.4)])
        self.assertEqual(len(report['clock_attempts']),2)

    def test_clock_rescue_stops_after_zero_fix_roi(self):
        source=[Segment(i,i*2,i*2+1,'原文') for i in range(70)]
        rows=[dict(start=i*10,end=i*10+1,withheld=True,reason='missing_speech_marks') for i in range(14)]
        report={}
        with patch.object(repair_clocks,'_slice_audio'):
            result=repair_clocks.rescue_caption_clocks('audio',source,[],rows,140,
                'paraformer','zh','tiny','cpu','int8',8,5,None,None,16,report,
                lambda *args:([Segment(0,0,.2,'different')],'zh'),pipeline._target_segments)
        self.assertEqual(len(result),len(source))
        self.assertEqual(len(report['clock_attempts']),4)
        self.assertEqual(report['clock_stop_reason'],'zero_fixes_roi')

    def test_previous_zero_roi_pass_never_runs_equivalent_attempts(self):
        source=[Segment(1,0,1,'原文')]
        report={'clock_attempts':[dict(reason='text_disagreement') for _ in range(30)]}
        with patch.object(repair_clocks,'_slice_audio') as crop:
            repair_clocks.rescue_caption_clocks('audio',source,[],
                [dict(start=0,end=1,withheld=True,reason='missing_speech_marks')],2,
                'paraformer','zh','large-v3','cpu','int8',8,5,None,'faster-whisper',16,
                report,lambda *args:self.fail('zero ROI strategy repeated'),pipeline._target_segments)
            crop.assert_not_called()
        self.assertEqual(len(report['clock_attempts']),30)
