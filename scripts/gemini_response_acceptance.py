"""Ten real translation batches, same Gemini profile and production validator.

Run explicitly; never included in offline test discovery. Does not run ASR/TTS.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autodub.translate import browser as B
from autodub.translate import semantic as S
from autodub.srt_utils import load_srt_file
from playwright.sync_api import sync_playwright


def main():
    source = next((ROOT / 'output').glob('*/*.src.srt'))
    cues = load_srt_file(str(source))[:30]
    assert len(cues) == 30
    out = ROOT / '_tmp' / 'gemini_acceptance'
    out.mkdir(parents=True, exist_ok=True)
    results = []
    summary = ''
    with sync_playwright() as p:
        ctx = B._launch(p, str(ROOT / 'browser_profile'), 'msedge')
        try:
            page = ctx.pages[0]
            page.goto('https://gemini.google.com/app', wait_until='domcontentloaded')
            B._visible_locator(page, B._INPUT_CANDIDATES)
            for batch in range(10):
                target = cues[batch * 3:batch * 3 + 3]
                ids = {s.index: batch for s in target}
                payload = dict(project_style=dict(name_policy='han_viet', film_hint='', name_hint=''),
                               glossary={}, previous_summary=summary, context_before=[],
                               target_cues=S.rows(target, ids), context_after=[])
                trace = {'batch': batch + 1}
                try:
                    raw = B._ask_once(page, '[AUTODUB_SEMANTIC_V1]\n' + S.TRANSLATE +
                                      '\nINPUT_JSON:\n' + json.dumps(payload, ensure_ascii=False),
                                      240, trace=trace)
                    (out / f'batch-{batch + 1}.txt').write_text(raw, encoding='utf-8')
                    obj = S.read_json(raw)
                    trace['parse'] = 'PASS'
                    B._state(trace, 'PARSED')
                    S.translated_validator(target, ids, {}, 'han_viet')(obj)
                    trace['gate'] = 'PASS'
                    B._state(trace, 'VALIDATED')
                    summary = obj['updated_summary']
                    trace['result'] = 'PASS'
                except Exception as exc:
                    trace['result'] = 'FAIL'
                    trace['error'] = str(exc)
                results.append(trace)
                (out / 'results.json').write_text(json.dumps({'source': str(source), 'results': results},
                                                            ensure_ascii=False, indent=2), encoding='utf-8')
                page.screenshot(path=str(out / f'batch-{batch + 1}.png'))
                print(json.dumps(trace, ensure_ascii=False), flush=True)
                if trace['result'] != 'PASS':
                    break
        finally:
            ctx.close()
    return 0 if len(results) == 10 and all(r['result'] == 'PASS' for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
