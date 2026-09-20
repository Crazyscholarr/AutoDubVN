"""Replay a focused retry using saved real source/clock artifacts, without ASR or writes to input files."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autodub import speechmap, srt_utils
from autodub.asr.common import set_caption_options
from autodub.asr.merge import normalize_segments
from autodub.asr.screen_pack import last_review, compact_review_gaps
from autodub.server.pipeline import _isolate_retry_result
import yaml


def artifact(directory, name):
    direct = directory / name
    if direct.exists():
        return direct
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    return Path(next(row['backup'] for row in manifest if Path(row['path']).name == name))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--regression', type=Path, required=True,
                        help='Review directory or recovery backup with manifest.json')
    parser.add_argument('--start', type=float, required=True)
    parser.add_argument('--end', type=float, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    old = srt_utils.load_srt_file(str(artifact(args.baseline, 'source.srt')))
    bad = srt_utils.load_srt_file(str(artifact(args.regression, 'source.srt')))
    old_map = speechmap.SpeechMap.load(str(artifact(args.baseline, 'speechmap.json')))
    bad_map = speechmap.SpeechMap.load(str(artifact(args.regression, 'speechmap.json')))
    start, end = args.start, args.end
    affected = [s for s in old if s.start < end and s.end > start]
    start = min([start] + [s.start for s in affected])
    end = max([end] + [s.end for s in affected])
    fixed = _isolate_retry_result(old, bad, start, end)
    old_remote = {(a,b) for a,b in old_map.marks if b <= start or a >= end}
    new_map = speechmap.SpeechMap(list(old_remote) + [
        (a,b) for a,b in bad_map.marks if a < end and b > start])
    source_signature = lambda rows: [(s.start,s.end,s.text) for s in rows
                                    if s.end <= start or s.start >= end]
    assert source_signature(fixed) == source_signature(old)
    assert old_remote == {(a,b) for a,b in new_map.marks if b <= start or a >= end}
    cfg = yaml.safe_load((ROOT/'config.yaml').read_text(encoding='utf-8'))
    set_caption_options(cfg.get('asr',{}))
    report = {'focus':[start,end], 'outside_source_unchanged':True,
              'outside_marks_unchanged':True,
              'lost_remote_marks_in_regression':len(old_remote - set(bad_map.marks))}
    for label, directory in [('baseline',args.baseline),('regression',args.regression)]:
        persisted = json.loads(artifact(directory,'unresolved.json').read_text(encoding='utf-8'))
        report[label+'_persisted_ui_count'] = len(compact_review_gaps(persisted))
    for label, source, sm in [('baseline',old,old_map),('regression',bad,bad_map),('fixed',fixed,new_map)]:
        speechmap.set_active(sm)
        packed = normalize_segments(source,80)
        blocked = [r for r in last_review() if r.get('withheld')]
        report[label] = {'source_count':len(source),'mark_count':len(sm),
                         'packed_count':len(packed),'blocked_rows':len(blocked),
                         'ui_gaps':compact_review_gaps(blocked)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:len(report[key]['ui_gaps']) for key in ['baseline','regression','fixed']}))
    assert len(report['fixed']['ui_gaps']) <= len(report['baseline']['ui_gaps'])
    speechmap.clear_active()


if __name__ == '__main__':
    main()
