"""Bounded fixtures only: preserve raw evidence and report real model timings."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autodub.asr import funasr as F
from autodub.asr.long_audio import atomic_json, diagnostics
from autodub.asr.merge import speech_coverage_report
from autodub import compat


def main():
    p=argparse.ArgumentParser()
    p.add_argument('audio')
    p.add_argument('--out',required=True)
    p.add_argument('--device',default='cpu',choices=['cpu','cuda'])
    p.add_argument('--start',type=float,default=0)
    p.add_argument('--minutes',type=float,nargs='+',default=[5])
    args=p.parse_args()
    if any(x<=0 or x>30 for x in args.minutes):
        p.error('Each fixture must be >0 and <=30 minutes; full-video inference is forbidden')
    root=Path(args.out);root.mkdir(parents=True,exist_ok=True)
    compat.patch_modelscope_hubconfig()
    from funasr import AutoModel
    import psutil
    proc=psutil.Process()
    t=time.monotonic()
    model=AutoModel(model=F._resolve('paraformer-zh'),vad_model=F._resolve('fsmn-vad'),
        vad_kwargs={'max_single_segment_time':30000},punc_model=F._resolve('ct-punc'),
        device='cuda:0' if args.device=='cuda' else 'cpu',disable_update=True)
    F._MODEL_CACHE[('funasr','paraformer-zh',args.device)]=model
    load=time.monotonic()-t
    for minutes in args.minutes:
        tag=f'{args.device}-{args.start:g}-{minutes:g}m'
        if (root/(tag+'.metrics.json')).exists():continue
        audio=str(root/(tag+'.wav'))
        t=time.monotonic();F._extract_funasr_chunk(args.audio,audio,args.start,minutes*60)
        extraction=time.monotonic()-t
        peak=[proc.memory_info().rss];stop=threading.Event()
        def sample():
            while not stop.wait(.2):peak[0]=max(peak[0],proc.memory_info().rss)
        worker=threading.Thread(target=sample,daemon=True);worker.start()
        t=time.monotonic()
        try:
            raw=F._funasr_generate(model,audio)
            wall=time.monotonic()-t
            atomic_json(root/(tag+'.raw.json'),raw)
            result=F.normalize_funasr_result(raw,duration_hint=minutes*60)
            vad=F.cached_funasr_speech_ranges(audio,minutes*60)
            row=diagnostics(raw,result,0,minutes*60,vad)
            row.update(wall_s=wall,rtf=wall/(minutes*60),load_s=load,
                extraction_s=extraction,peak_rss_bytes=peak[0],device=args.device,
                effective_device=str(next(model.model.parameters()).device),
                start=args.start,minutes=minutes,
                normalization_status=result.status,
                speech_coverage=speech_coverage_report(result.segments,vad,minutes*60) if vad is not None else None)
            from autodub.srt_utils import save_srt_file
            save_srt_file(str(root/(tag+'.srt')),result.segments)
        except Exception as exc:
            row=dict(error=repr(exc),wall_s=time.monotonic()-t,device=args.device,minutes=minutes)
        finally:
            stop.set();worker.join()
        atomic_json(root/(tag+'.metrics.json'),row)
        print('BENCHMARK_RESULT '+json.dumps(row,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
