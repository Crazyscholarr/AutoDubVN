# Translation Browser Regression

> Browser response detection and retry notes below are historical. See
> [Gemini response P0 verification](GEMINI_RESPONSE_P0.md) for the verified
> 2026-09-15 state machine, DOM selectors, taxonomy, and live acceptance results.

Investigation and repair of the Gemini-web translation pipeline in AutoDubVN.
Git history cannot checkout a prior semantic package: `autodub/translate/` is **untracked**. Last committed translator is numbered-line `autodub/translate.py` at `4e04b21`.

## Root Cause

Four stacked regressions, not one “AI gate” bug.

1. **Send ACK was wrong for current Gemini composer.** Enter inserts a newline. The send control is an arrow button (Ctrl+Enter). Automation sometimes clicked Flash/Tools, or treated an empty composer as “sent”. The pipeline then waited the full `wait_reply` (often 240s) for a reply that never started.

2. **Responses were parsed before generation finished.** Truncated JSON (`{"translated_sentences":[{...`) was returned from wait logic and fed to `json.loads`, producing `Expecting ',' / ':' / value`.

3. **Semantic path made 2–3 Gemini round-trips per batch** (translate + ALIGN + optional QUALITY) on ~12–20 cues. Last-good numbered path used **one** request per chunk. Throughput collapsed even when send/parse worked (~8–11 validated cues/min after errors).

4. **Quality gates were true-positives mixed with false positives.** Exact digit `Counter` equality rejected extra/localized numbers (`1,5` vs `1.5`). Uncertain/common tokens (`伟人`, `七月`) in `new_entities` failed the whole batch (`entity ngoài target/lặp`, `keep_source`).

## Last Known Good vs Current

| | Last committed good (`4e04b21`) | First-bad (untracked semantic + Gemini UI) | After this fix |
|---|---|---|---|
| Output | Numbered lines, 1 request/lô | JSON schema, 2–3 Gemini calls/lô | JSON translate + **local** ALIGN; QUALITY only on bad breaks |
| Send | Click send, **Enter fallback** | Enter = newline on Quill; empty composer treated as ACK | JS send (skip Flash/Tools), Ctrl+Enter; ACK = generating **or** user-query fingerprint |
| Wait | New model block + 3s stable | Could return truncated JSON (`hung_incomplete`) | Complete JSON / numbered list; truncated + Stop gone → empty (retry), not parse |
| Gate | Line-level CJK/number shorteners | Strict entity+digit equality on full batch | Gates kept; skip unexpected/duplicate/uncertain entities; missing Arabic digits only |

There is **no git commit** that is “first bad”: the semantic translator never landed on `main`. The live app was already running this untracked tree. Do not reset or checkout.

## Send Failures

Gemini composer is `contenteditable` / Quill (`rich-textarea`, `div.ql-editor`, `role=textbox`), not a simple `<textarea>`.

- Enter: newline.
- Send: `.send-button` / `aria-label` Send/Gửi / `arrow_upward`, **not** Flash/Tools/mic.
- ACK (`WAIT_FOR_SEND_ACK`, ~3s, 4 submit tries): `_is_generating()` **or** last user bubble contains prompt fingerprint. Composer empty is **not** sufficient; empty + send button disabled can corroborate.
- If composer still holds the prompt and there is no user bubble, `_wait_reply` returns `""` after `unsent_after` (~6s) instead of waiting 4 minutes.
- `_ask_once` skips re-type when the user bubble already has this prompt (idempotent). Send-class errors stop after 3 outer attempts (one new-chat), not 4×240s.
- Circuit: 5 consecutive batch `SEND_FAILURE` → stop; validated cache is kept.

## Response Completion Detection

Signals: Stop generating visible (`aria-label` Stop generating / Ngừng tạo, `data-test-id=stop-icon-button`) → still streaming.

Accept only when:

- analysis JSON closed with real `production_ease`/`recommendation`, or
- translation/align JSON object fully parseable (`translated_sentences` or `cues`), or
- numbered translation lines, Stop gone, or
- text stable ≥2.5s, Stop gone, braces closed.

If Stop is gone and text is still unclosed JSON → return empty (classify `TRUNCATED_JSON`), do not `json.loads`. Stale last-assistant node is ignored via pre-send `baseline` counts + `last_before` fingerprint.

## JSON Parser Findings

`autodub/translate/jsonutil.py` taxonomy:

`EMPTY_RESPONSE`, `TRUNCATED_JSON`, `MARKDOWN_WRAPPED_JSON`, `EXTRA_TEXT_JSON`, `VALID_JSON`, `INVALID_JSON`, `MODEL_NON_JSON`, `UI_ERROR`, `RATE_LIMIT`, `SEND_FAILURE`, `RESPONSE_TIMEOUT`, `SCHEMA_FAILURE`, `SEMANTIC_GATE_FAILURE`.

- Code fence / surrounding prose: string-aware balanced `{...}` extract. No greedy `{.*}`. No comma/quote “repairs”.
- Parse fail writes short metadata + raw (cap 100k) under `<cache-dir>/_tmp/translation-debug/`.
- Repair prompt is format-only (`Return ONLY one valid JSON…`) for parse kinds; validation wrap only after a parsed object fails the gate.

## Semantic Gate Findings

Gate is **on**. Changes are false-positive reductions:

- Arabic digits: fail only if a **source** token is missing in dest. Extra dest digits OK. `1,5` ≡ `1.5`. Chinese numerals (`三个人`) are not treated as Arabic.
- `new_entities` whose `source` is not in the target blob: skip + `entity="…" reason=unexpected` (the `伟人` case).
- Duplicate entity: skip + `reason=duplicate`.
- Uncertain / `keep_source` with `vi != source`: skip + `reason=uncertain_skipped` (do not fail the batch). Locked glossary names still required.

## Batch / Retry Findings

- Default `semantic_batch_cues` 10 / `semantic_batch_seconds` 45 (config.example). Compact JSON payload for Gemini web paste size.
- Truncated / invalid / empty / schema / semantic-gate on a batch **>8 cues** → split in half, preserve order, no duplicate IDs.
- Send failure does **not** split (transport).
- Request budget: 2 attempts per phase. ALIGN Gemini is skipped when local `fallback_alignment` passes (main throughput win: 1 model call per batch in the common case).
- Successful batches still atomic-write `*.translate_cache.json.semantic.json`. Resume does not retranslate those keys.
- Glossary in the prompt is **only entries that appear in the current target blob**.

Retry matrix:

| Class | Action |
|---|---|
| SEND_FAILURE | Resubmit / wake composer / one new chat; fail fast; circuit 5 |
| RESPONSE_TIMEOUT | Empty/truncated wait returns empty; retry request |
| MARKDOWN / EXTRA_TEXT | Normalize locally |
| TRUNCATED / INVALID / EMPTY | Format repair once, then split if large |
| SEMANTIC_GATE | Validation feedback retry once; split if large |
| RATE_LIMIT / UI_ERROR | Stop that request; do not parse as translation |

## Files Changed

- `autodub/translate/browser.py` — send JS, ACK, unsent early-exit, no truncated return, idempotent `_ask_once`
- `autodub/translate/jsonutil.py` — extract / classify / debug dump
- `autodub/translate/semantic.py` — local ALIGN first, entity skip, digit missing-only, relevant glossary, split, circuit
- `tests/test_jsonutil.py` — parser fixtures
- `tests/test_semantic_translation.py` — gates, split, send-vs-validation
- `tests/test_translate_budget.py` — ACK, streaming JSON, truncated timeout
- `tests/test_dub_e2e.py` — no longer requires a Gemini ALIGN call
- `docs/TRANSLATION_BROWSER_REGRESSION.md` — this file

## Tests Added

Parser: valid object, fence, prefix/suffix, empty, truncated, two objects, dump file, `1,5` normalization.

Browser: empty composer is not ACK; unsent wait returns `""` quickly; submit JS does not send Enter; truncated JSON is not returned; streaming JSON accepted only when closed.

Semantic: uncertain/unexpected entity skip; extra digits + decimal comma; truncated 20-cue batch splits 10+10; send failure retries **same** prompt (no “Sửa lỗi validation”).

Full suite: **624 tests OK, 1 skipped** (`python -m unittest discover -s tests`).

## Before / After

Live Gemini rates after the fix are **not** measured in this session (no production account run). Numbers below: **before** = user log 15 Sep 2026 (BV1nmbp66EmA); **after** = expected from code + unit tests.

| Metric | Before | After |
|---|---|---|
| send failure rate | High (composer still full, 4/4) | Detected in seconds; empty composer ≠ ACK |
| response timeout rate | High (wait_reply 240s on unsent) | Unsent → empty ~6s; truncated + Stop gone → empty after stability |
| JSON parse failure rate | High (`Expecting ','/':'/value`) | Fence/prose extract; truncated not parsed |
| semantic repair rate | High (`伟人`, digits, keep_source) | Skip FP entities; missing-digit only |
| median batch latency | Dominated by 2–3 Gemini calls + 4 min waits | 1 Gemini call typical (local ALIGN) |
| validated cues/minute | ~8–11 then collapse (503/1083 ≈ 49 min) | Not remeasured live; theoretically ~2–3× if send stays healthy |

## Remaining Issues

- No live Gemini E2E against the logged-in profile in this session. Restart GUI and continue **Dịch**; semantic cache around cue 503 should resume.
- Per-cue semantic repair inside a sentence JSON (19 pass / 1 fail) is **not** implemented: validator still requires full `source_ids` coverage. Split-batch is the isolation mechanism.
- Phase timers (`send_ack_ms`, `generation_ms`, P50/P95) are not a separate metrics file; batch log has `batch=N Nms`.
- Chat rotation remains `reset_every` (default 10). Long-session DOM bloat not auto-profiled.
- Browser E2E against real Gemini UI is not in CI (account/risk).

## Final Status

**PARTIAL**

Reliability layers (send ACK, completion, JSON taxonomy, gate FPs, local ALIGN, split, cache) are in place and unit-tested. Live throughput on the 1083-cue job is not yet proven in this session.

### Definition of done

- [x] Gửi prompt: ACK không dựa vào ô rỗng
- [x] Send failure phát hiện nhanh (không chờ `wait_reply` nếu chưa ACK)
- [x] Không parse streaming / JSON cắt
- [x] Không lấy response cũ (`baseline` + `last_before`)
- [x] JSON code fence normalize
- [x] Empty/truncated classified
- [x] Parser diagnostics + raw debug file
- [x] Batch split on truncated/invalid
- [x] Retry theo error class + budget 2
- [x] Semantic gate still on
- [x] Number / entity tests
- [x] Successful batches checkpointed
- [x] Full unit suite PASS (624, 1 skipped)
- [ ] Live rerun to a large cue count / cues-per-minute on BV1nmbp66EmA
