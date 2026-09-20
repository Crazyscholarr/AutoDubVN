"""Rebuild reviewable subtitles from raw chunk evidence without model inference."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autodub import speechmap
from autodub.asr import funasr as F
from autodub.asr.long_audio import atomic_json, atomic_srt, owned_result
from autodub.asr.common import set_caption_options
from autodub.asr.merge import normalize_segments, speech_coverage_report, find_uncovered_speech_ranges
from autodub.asr.screen_pack import last_review, ensure_complete, CaptionReviewRequired


def main():
    p=argparse.ArgumentParser();p.add_argument('report');args=p.parse_args()
    started=time.monotonic()
    report=json.loads(Path(args.report).read_text(encoding='utf-8'))
    run=report['long_audio'];root=Path(run['checkpoint_dir']);identity=run['identity']
    output=Path(tempfile.mkdtemp(prefix='asr-reparsed-',dir=root.parent.parent))
    set_caption_options({'caption_style':'screen'})
    segs=[];marks=[];chunks=[]
    for path in sorted(root.glob('chunk_*.json')):
        if path.name.count('.') != 1:continue
        state=json.loads(path.read_text(encoding='utf-8'))
        if state.get('state')!='healthy':continue
        a,b=state['start'],state['end'];cs=max(0,a-identity['overlap']);ce=min(identity['duration'],b+identity['overlap'])
        raw=json.loads(path.with_suffix('.raw.json').read_text(encoding='utf-8'))
        n=F.normalize_funasr_result(raw,offset=cs,duration_hint=ce-cs)
        if n.status!='ASR_VALID':raise RuntimeError(f'{path.name}: {n.status}')
        owned,mm=owned_result(n,a,b);segs.extend(owned);marks.extend(mm)
        chunks.append(dict(chunk=path.name,segments=len(owned),marks=len(mm)))
    segs.sort(key=lambda s:(s.start,s.end))
    atomic_srt(output/'source.reparsed.srt',segs)
    speechmap.set_active(speechmap.SpeechMap(sorted(set(marks))))
    cues=normalize_segments(segs,16)
    atomic_srt(output/'packed.needs-review.srt',cues)
    speechmap.get_active().save(str(output/'speechmap.json'))
    review=last_review();vad=report['speech_ranges'];duration=identity['duration']
    gaps=find_uncovered_speech_ranges(cues,vad,duration,min_gap=1.2,edge_pad=0,subtitle_pad=0)
    review.extend(dict(start=a,end=b,reason='unresolved_speech_gap',withheld=True,threshold_s=1.2,detector='fsmn-vad') for a,b in gaps)
    metric=speech_coverage_report(cues,vad,duration)
    result=dict(chunks=chunks,source_segments=len(segs),packed_segments=len(cues),
                coverage=metric,significant_gap_s=sum(b-a for a,b in gaps),
                wall_s=time.monotonic()-started,output=str(output),inference_calls=0)
    try:
        ensure_complete(cues,segs,str(output),review=review,repair_report={'coverage':metric})
        result['status']='PASS'
    except CaptionReviewRequired as exc:
        result.update(status=exc.status,review_dir=exc.review_dir)
    atomic_json(output/'result.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
