# BV1r Gemini extraction — 10-run

Date: 2026-09-17. Follow-up to JSON salvage (`BV1R_GEMINI_JSON_10_RUN.md`).

## Verdict

**ACCEPTED 10/10** on detector replay (`tests/test_bv1r_extraction_10_run.py`).

Live GUI 16:05: first two Gemini turns `VALIDATED` (JSON salvage holds). Turn 3: `SEND_ACK` 2→3 then 12s `RESPONSE_EXTRACTION_FAILURE` with `user_count=2`, `model_count=2`, `last_candidate_chars=0`, `generating=False`. Five of those trips hit send_streak and aborted `thiếu 5/113 lô`.

## Cause

The 12s empty-host fail-fast treated a **placeholder / virtualized** `model-response` as a finished empty reply. Gemini often has no `pending-response` / Stop button for several seconds after send. `infinite-scroller` also keeps ~2 turns in the window: ACK sees user 3, the next snapshot still counts 2 visible nodes with empty newest text.

JSON salvage is not the failure here.

## Fix

- Empty new host **without** generating/pending is not a started turn. Wait for first token up to `min(wait_reply, 90s)` after ACK.
- 12s `RESPONSE_EXTRACTION_FAILURE` only after the turn **did** generate, then went idle with no text.
- `_reveal_latest()` scrolls the scroller to the last `user-query` / `model-response` / `pending-response` before each snapshot.

## 10-run cases (each × 10)

1. First token at 15s, never `generating` → extract JSON (the live abort).
2. `generating` then idle empty → `RESPONSE_EXTRACTION_FAILURE` under 20s.
3. Empty placeholder, timeout 2s → `RESPONSE_START_TIMEOUT`, not extraction.

Command: `venv\Scripts\python.exe -m unittest tests.test_bv1r_extraction_10_run tests.test_gemini_response_detection tests.test_translate_budget`

Does not claim a live 113-lô finish. Resume the same job; cache still has the first validated batch.
