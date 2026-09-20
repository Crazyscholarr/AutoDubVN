"""Replay persisted raw results through the previous and corrected parsers."""
import inspect
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autodub.asr import funasr as F
from autodub.asr.long_audio import atomic_json
from autodub.srt_utils import Segment


def legacy_parser():
    namespace=dict(vars(F))
    original=F._timestamp_payload_to_segments
    def old_payload(payload,offset=0,duration_hint=0):
        text=F._clean(str(payload.get('text') or ''))
        pairs=F._timestamp_pairs(payload.get('timestamp'))
        if text and pairs and F._is_cjk(text):
            return [Segment(0,offset+pairs[0][0]*.001,offset+pairs[-1][1]*.001,text)]
        return original(payload,offset,duration_hint)
    namespace['_timestamp_payload_to_segments']=old_payload
    namespace['_aligned_text_tokens']=lambda text,count: None
    # Reproduce exactly the two changed parser choices, using trusted local
    # function source; never execute model output.
    source=inspect.getsource(F.normalize_funasr_result).replace('.casefold()', '')
    exec(compile(source,'<legacy-normalizer-replay>','exec'),namespace)
    return namespace['normalize_funasr_result']


def main():
    old=legacy_parser();rows=[]
    for directory in sys.argv[1:]:
        for path in Path(directory).glob('*.metrics.json'):
            metric=json.loads(path.read_text(encoding='utf-8'))
            raw_path=path.with_name(path.name.replace('.metrics.json','.raw.json'))
            if not raw_path.exists():continue
            raw=json.loads(raw_path.read_text(encoding='utf-8'))
            duration=metric['minutes']*60
            before,after=old(raw,duration_hint=duration),F.normalize_funasr_result(raw,duration_hint=duration)
            lexical=lambda segs: ''.join(c.casefold() for s in segs for c in s.text if c.isalnum())
            metric.update(raw_path=str(raw_path),old_parser_segments=len(before.segments),
                          new_parser_segments=len(after.segments),
                          same_recognized_lexical_text=lexical(before.segments)==lexical(after.segments))
            rows.append(metric)
    atomic_json('docs/asr_benchmark_results.json',rows)
    print(json.dumps([{k:r.get(k) for k in ('start','minutes','device','wall_s','old_parser_segments','new_parser_segments','same_recognized_lexical_text')} for r in rows],indent=2))


if __name__=='__main__':main()
