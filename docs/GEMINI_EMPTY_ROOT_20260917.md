# Gemini missing batches — 2026-09-17

## Evidence from the supplied BV1rF4Q6EEUT job

- The screenshot is a model refusal, not subtitle JSON. Existing classification
  recognizes this exact Vietnamese sentence as MODEL_REFUSAL.
- At 20:05:55 and 20:06:44 the detector successfully extracted refusals. At
  20:06:09 it reused existing responses without sending. Suffix-only identity
  can collide across requests with the same context-after; the log alone does
  not prove which reuse was wrong.
- Starting 20:06:54, ACK sees one user node; subsequent snapshots see zero user
  and model nodes in the selected infinite-scroller. Four extraction failures
  and a start timeout trip the five-failure circuit breaker.
- The review file contains 486 validated cues out of 1,375, reaching cue 510.
  Failed recorded ranges: 183–188, 243–248, 433–438, 493–498, 511–522,
  523–534, 535–546, 547–558. The final failed batch and unattempted suffix
  were not included in the review. “9/113 batches missing” therefore did NOT
  imply 104 successful batches.

## Changes

- `autodub/translate/browser.py`: polling, reveal and observer share a root
  selector that prefers populated visible conversation containers over empty
  or hidden scrollers. No whole-page response extraction fallback.
- Observer re-resolves the root after DOM replacement instead of watching only
  the detached original root. A completed answer with action controls survives
  virtualizer unmount; incomplete output does not.
- Full normalized prompt identity replaces the shared 80-character payload
  suffix in polling and observer. Retry clarification remains a different turn.
- Plain text in a model host is extracted with action controls removed, so a
  refusal can be classified instead of waiting for nonexistent JSON. Polling
  output takes precedence over a longer, potentially stale observer string.
- Timeout diagnostics include candidate conversation containers and counts.
- `autodub/translate/semantic.py`: missing-batch accounting includes unattempted
  original batches and partially failed splits. Cache records split decisions,
  so resume can reach validated child entries without re-requesting the parent.
- Source text, existing cache files, prompts, model choice, timeout budgets and
  JSON/schema/content validation are unchanged.

## Verification and limits

Regression tests use production JS in real headless Edge with controlled DOM:
empty/hidden roots, root replacement, completed/partial unmount, shared context
tails, prompt/response ownership. Semantic tests exercise circuit-breaker counts
and split-cache resume with zero extra model calls. Classification tests include
the exact screenshot refusal.

This is not a live Gemini qualification or a completed translation of this job.
The supplied log cannot distinguish all DOM-removal causes. The fixes cover
reproduced defects; the historical zero-node incident still needs a live DOM
capture if it recurs. A genuine model refusal cannot be guaranteed to disappear.

Local commands:

```powershell
venv\Scripts\python -m unittest discover -s tests -p test_gemini_response_detection.py -q
venv\Scripts\python -m unittest discover -s tests -p test_semantic_translation.py -q
venv\Scripts\python -m unittest discover -s tests -p test_response_classification.py -q
```

Logs: `_tmp/gemini-fix-tests.log`, `_tmp/semantic-fix-tests.log`,
`_tmp/classification-fix-tests.log`, `_tmp/gemini-fix-full-suite.log`.

Results: targeted suites 32 + 30 + 13 = 75 passed. Full-suite run: 780 tests,
777 passed, 1 failed, 2 skipped. The failing test is
`test_bv1r_gemini_json.Bv1rLiveDumpCorpus.test_job_debug_dumps_if_present`:
three existing dump files (`1789647890`, `1789647900`, `1789647910`)
contain malformed JSON with unescaped quotes around `chuồn`. This patch does
not modify `jsonutil.py` or reinterpret malformed output as a successful
translation. Full-suite all-green and live end-to-end completion are not claimed.
