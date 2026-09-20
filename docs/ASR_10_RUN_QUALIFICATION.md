# ASR 10-run qualification — Strategy A

Date: 2026-09-17. Film: `一亩灵田修长生 [BV1GAY56VEwU]`.

This is **not** an acceptance of Strategy A. Golden evaluator numbers
were recomputed from frozen artifacts with one function:
`autodub.asr.evaluate.evaluate_captions`.

Runs 1–9 are slices of the 11:07 working SRT against the same FSMN VAD
as the full film. They are not ten new FunASR jobs. Run 10 is the
already-finished 120-minute GUI job. Code changes after 11:07 are not
re-inferred on the full film in this round.

## Golden recalculation: 92.57% vs 86.68%

- OLD recovered SRT: recorded naive 92.57% on 2118 cues with 2 mega cues (file asr.recovered.srt not present; same VAD duration 6404.19s)
- CURRENT working SRT: naive 86.68% (mega 0) → honest 86.68%
- Same-VAD compare: n/a
- Mega-cue-only replay on current VAD: 48.99% of all speech painted by the two 30-minute clocks
- OLD forensics unresolved: 475.65485448854906s (subthreshold 357.23600000001056, significant 118.41999999999888)

Same evaluator, same VAD (`asr-repair-9pp2j99f.json` speech_ranges).
OLD 92.57% used two source cues 0.05–1801s and 5398–7201s. CURRENT has
zero mega cues, so naive = honest = 86.68%. The 5.89 pp gap equals
~377s, which matches extra subthreshold holes (848.95 − 357.24) minus
significant holes CURRENT already filled (118.42 − 4.36).
That is metric inflation + visible inter-sentence gaps, not a 6 pp
loss of spoken sentences.

## 853s unresolved vs 5.46s blockers

- Honest unresolved speech: 853.31s
- Subthreshold gaps (<1.2s): 3926 / 848.95s
- Significant VAD gaps (≥1.2s): 2 / 4.36s
- Repair remaining after energy confirm: []
- Energy-dropped significant holes: 0
- Review blockers: 3 / 5.459999999999582s reasons={'missing_speech_marks': 3}

The ~848s are inter-cue holes below the 1.2s gate. The 11:07 abort was
`missing_speech_marks` on three crop inserts (`哇`, `江送 天`, `手有`),
not 853s of missing dialogue. Coverage was not relaxed to hide this.

## Strategy A defects addressed in code (not yet re-run on 120m)

1. Crop insert without neighbor match / speech marks is removed.
2. Neighbor cover waits until pad 0.35 and pad 1.0 have both run.
3. Chunk plan is frozen so VAD jitter does not redo healthy chunks.
4. Truncated `audio16k` (size ≤1024) is unlinked; extract writes `.partial`.
5. Caption clock rescue stops after 4 attempts with 0 fixes.

## 10-run matrix

| Run | Input | Strategy | Runtime | Speech Cov | Missing s | Blockers | Checkpoint | Result |
|-----|-------|----------|---------|------------|-----------|----------|------------|--------|
| 1 | 5m speech | A-adaptive-10min | artifact | 82.63% | 41.5 | 2 | present | PASS |
| 2 | 10m dense | A-adaptive-10min | artifact | 83.10% | 75.2 | 2 | present | PASS |
| 3 | 10m mid mixed | A-adaptive-10min | artifact | 86.32% | 72.5 | 0 | present | PASS |
| 4 | 10m chunk boundary | A-adaptive-10min | artifact | 85.83% | 14.0 | 0 | present | PASS |
| 5 | early problematic | A-adaptive-10min | artifact | 83.10% | 75.2 | 2 | present | PASS |
| 6 | mid film | A-adaptive-10min | artifact | 87.93% | 66.8 | 0 | present | PASS |
| 7 | late film | A-adaptive-10min | artifact | 86.20% | 72.9 | 0 | present | PASS |
| 8 | 30m continuous | A-adaptive-10min | artifact | 87.56% | 198.9 | 0 | present | PASS |
| 9 | 60m representative | A-adaptive-10min | artifact | 86.10% | 440.2 | 2 | present | PASS |
| 10 | FULL 120m | A-adaptive-10min | artifact | 86.68% | 853.3 | 3 | present | FAIL (missing_speech_marks_inserts) |

PASS COUNT: 9/10

HARD FAILURES:
- Run 10 GUI job: `missing_speech_marks` inserts; all 13 chunks
  `reused_raw=False` after a 0-byte extract even though SHA matched.
- Runtime of runs 1–9 is not a new wall-clock measurement.

FINAL DECISION:

REJECT

WHY A IS NOT DEFAULT:
Full 120-minute job still fails a hard gate. Coverage 86.7% honest is
not a 6 pp recall regression versus 92.57% naive mega-cue coverage, but
Strategy A is not accepted until a new full-film run after the crop/
checkpoint fixes scores ≥8/10 on this same matrix, including run 10.

No threshold was loosened. Translation/TTS was not started.
