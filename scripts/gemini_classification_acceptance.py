"""20 live batches through production classification, retry and validation.

Explicit invocation only. Uses existing source subtitle failure regions and the
same Edge profile; does not change or mock the browser response detector.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autodub.translate import browser as B
from autodub.translate import semantic as S
from autodub.translate import jsonutil as J
from autodub.srt_utils import load_srt_file, parse_srt
from playwright.sync_api import sync_playwright


def main():
    source = next((ROOT / 'output').glob('*/*.src.srt'), None)
    if source:
        cues = load_srt_file(str(source))
        windows = [(lo, lo + 3) for lo in list(range(41, 71, 3)) + list(range(141, 171, 3))]
    else:
        # Read-only recovery from the earlier live conversation, preserving exact
        # source text and timestamps; never manufacture missing source subtitles.
        source = ROOT / '_tmp' / 'recovered_gemini_source.json'
        recovered = json.loads(source.read_text(encoding='utf-8'))
        cues = parse_srt('\n\n'.join(f"{x['id']}\n{x['start']} --> {x['end']}\n{x['source_text']}" for x in recovered))
        windows = [(lo, lo + 1) for lo in range(10)] + [(lo, lo + 2) for lo in range(10, 30, 2)]
    assert len(windows) == 20 and len(cues) >= windows[-1][1]
    out = ROOT / '_tmp' / ('gemini_classification_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    out.mkdir(parents=True)
    print('ARTIFACT_DIR=' + str(out), flush=True)
    results, metrics, summary, glossary = [], S.response_metrics(), '', {}
    with sync_playwright() as p:
        ctx = B._launch(p, str(ROOT / 'browser_profile'), 'msedge')
        try:
            page = ctx.pages[0]
            page.goto('https://gemini.google.com/app', wait_until='domcontentloaded')
            B._visible_locator(page, B._INPUT_CANDIDATES)
            for batch, (lo, hi) in enumerate(windows, 1):
                target = cues[lo:hi]
                ids = {cue.index: batch for cue in target}
                payload = dict(project_style=dict(name_policy='han_viet', film_hint='', name_hint=''),
                               glossary=glossary, previous_summary=summary,
                               context_before=S.rows(cues[max(0, lo - 2):lo]),
                               target_cues=S.rows(target, ids), context_after=S.rows(cues[hi:hi + 2]))
                rec = dict(batch=batch, source_ids=[cue.index for cue in target], attempts=[])
                def ask(prompt):
                    trace = {}
                    raw = B._ask_once(page, prompt, 240, trace=trace)
                    attempt = len(rec['attempts']) + 1
                    (out / f'batch-{batch}-attempt-{attempt}.txt').write_text(raw, encoding='utf-8')
                    shape = J.classify_response_shape(raw)
                    trace.update(shape=shape, chars=len(raw), preview=raw[:180])
                    rec['attempts'].append(trace)
                    return raw
                warnings = []
                obj, kind = S.request(ask, S.TRANSLATE, payload,
                                     S.translated_validator(target, ids, glossary, 'han_viet'),
                                     warnings, metrics=metrics, cache_path=str(out / 'debug.json'))
                rec.update(kind=kind, result='PASS' if obj is not None else 'FAIL', warnings=warnings)
                if obj:
                    summary = obj['updated_summary']
                    for entity in obj['new_entities']:
                        if not entity['needs_review'] and entity['confidence'] >= .9:
                            glossary[entity['source']] = dict(vi=entity['vi'], locked=True)
                results.append(rec)
                page.screenshot(path=str(out / f'batch-{batch}.png'))
                (out / 'results.json').write_text(json.dumps(dict(source=str(source), metrics=metrics,
                                                                results=results), ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(dict(batch=batch, result=rec['result'], kind=kind,
                                      shapes=[a['shape'] for a in rec['attempts']], metrics=metrics), ensure_ascii=False), flush=True)
                if kind.startswith('RESPONSE_') or kind == 'SEND_ACK_TIMEOUT':
                    break
        finally:
            ctx.close()
    return 0 if len(results) == 20 and all(r['result'] == 'PASS' for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
