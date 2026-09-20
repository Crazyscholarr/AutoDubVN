"""Compare cached Whisper models on a few confirmed unresolved gaps, not a film."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autodub.asr import whisper as W, funasr as F, pipeline as P
from autodub.asr.common import _take_last_marks, _MODEL_CACHE
from autodub.asr.long_audio import atomic_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('audio');parser.add_argument('--out',required=True)
    args=parser.parse_args();root=Path(args.out);root.mkdir(parents=True,exist_ok=True)
    data=json.loads(Path('docs/asr_gap_forensics.json').read_text(encoding='utf-8'))
    gaps=data['gaps'];gaps=[gaps[i] for i in sorted({0,len(gaps)//2,len(gaps)-1})]
    rows=[]
    for size in ('large-v3','medium','small'):
        for i,gap in enumerate(gaps):
            start,end=gap['start'],gap['end'];cs=max(0,start-1);ce=end+1
            audio=str(root/f'gap-{i}.wav')
            if not Path(audio).exists():F._extract_funasr_chunk(args.audio,audio,cs,ce-cs)
            row=dict(model=size,start=start,end=end,source_language='zh')
            t=time.monotonic()
            try:
                segs,lang=W._asr_faster_whisper(audio,'zh',size,'cpu','int8')
                marks=_take_last_marks(cs)
                accepted=P._target_segments(segs,marks,cs,start,end)
                row.update(telemetry=W.LAST_TELEMETRY.get(),text=''.join(s.text for s in segs),
                           accepted_text=''.join(s.text for s in accepted),
                           accepted_seconds=sum(s.duration for s in accepted),
                           detected_language=lang,quality_reference='no human-verified transcript')
            except Exception as exc:row['error']=repr(exc)
            row['wall_s']=time.monotonic()-t;rows.append(row)
            atomic_json(root/'results.json',rows)
            print(json.dumps(row,ensure_ascii=False),flush=True)
        for key in list(_MODEL_CACHE):
            if key[0]=='fw' and size != 'small':del _MODEL_CACHE[key]
    # A paired language-detection timing sample, separate from production.
    t=time.monotonic()
    segs,lang=W._asr_faster_whisper(str(root/'gap-0.wav'),None,'small','cpu','int8')
    atomic_json(root/'auto_language_sample.json',dict(language=lang,wall_s=time.monotonic()-t,
                telemetry=W.LAST_TELEMETRY.get(),text=''.join(s.text for s in segs)))


if __name__=='__main__':main()
