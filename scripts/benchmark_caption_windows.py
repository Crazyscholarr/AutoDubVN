"""Offline comparison of indexed queries with the previous tail-copy algorithm."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autodub.speechmap import SpeechMap


def legacy_window(sm, start, end):
    out=[]
    for a,b in sm.marks[max(0,sm._bisect(start)-2):]:
        if a>end:
            break
        if start<=(a+b)/2<=end:
            out.append((a,b))
    return out


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output', default='_tmp/caption_windows_benchmark.json')
    args=parser.parse_args()
    start=time.perf_counter()
    sm=SpeechMap([(i*.08,i*.08+.07) for i in range(180000)])
    construction=time.perf_counter()-start
    queries=[(i*1.4,i*1.4+1) for i in range(10000)]
    timings={}
    reference=None
    for label,query in [('previous',lambda a,b:legacy_window(sm,a,b)),('indexed',sm.window)]:
        start=time.perf_counter()
        results=[query(a,b) for a,b in queries]
        timings[label]=time.perf_counter()-start
        if reference is None:
            reference=results
        elif results!=reference:
            raise AssertionError('Ordinary non-overlapping clocks changed')
    report=dict(marks=len(sm),queries=len(queries),construction_seconds=construction,
                timings_seconds=timings,speedup=timings['previous']/timings['indexed'],
                exact_output_match=True,
                scope='Synthetic timestamp lookup only, not total ASR/render runtime')
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
