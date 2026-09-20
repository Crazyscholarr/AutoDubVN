"""Replay local audio regions with bounded timestamp diagnostics, no transcripts."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autodub.asr import funasr
from autodub.asr.common import _take_last_marks
from autodub.asr.detect import _slice_audio
from autodub.asr.pipeline import _target_segments
from autodub.utils import ffprobe_duration


def audit(audio, regions):
    duration = ffprobe_duration(audio)
    result = dict(regions=[], model_loads=0)
    with tempfile.TemporaryDirectory(prefix='asr-region-audit-') as td:
        for start,end in regions:
            for padding in (0,1):
                cs,ce=max(0,start-padding),min(duration,end+padding)
                piece=str(Path(td)/'clip.wav')
                begin=time.perf_counter()
                _slice_audio(audio,cs,ce,piece)
                extraction=time.perf_counter()-begin
                count=len(funasr._MODEL_CACHE)
                begin=time.perf_counter()
                row=dict(start=start,end=end,padding=padding,extraction_s=extraction)
                try:
                    segs,_=funasr._asr_funasr(piece,'zh','cpu')
                    marks=_take_last_marks(cs)
                    row.update(status='ASR_VALID',segments=len(segs),
                        target_segments=len(_target_segments(segs,marks,cs,start,end)),
                        text_length=sum(len(s.text) for s in segs),first_marks=marks[:3])
                except funasr.FunASRResultError as exc:
                    row.update(status=exc.reason,diagnostics=exc.result.diagnostics)
                finally:
                    _take_last_marks()
                row['recognition_s']=time.perf_counter()-begin
                result['model_loads'] += len(funasr._MODEL_CACHE)-count
                result['regions'].append(row)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('audio')
    parser.add_argument('regions',help='JSON array of [start_seconds,end_seconds]')
    parser.add_argument('--limit',type=int,default=8)
    parser.add_argument('--output',default='asr-region-audit.json')
    args=parser.parse_args()
    regions=json.loads(Path(args.regions).read_text())[:args.limit]
    Path(args.output).write_text(json.dumps(audit(args.audio,regions),indent=2),encoding='utf-8')
