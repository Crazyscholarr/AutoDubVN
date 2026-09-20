# BV1rF4Q6EEUT 10-run qualification

Date: 2026-09-17. Film: 32-minute 快漫 `BV1rF4Q6EEUT`.

Stop condition: **≥9/10 PASS**, full film honest coverage **≥90%**,
mega cues = 0, no invented text. One lucky slice is not completion.

Direction: paint subthreshold uncovered FSMN VAD onto neighboring
cue clocks (`paint_short_speech_holes`), plus Gemini inflight abandon
after `SEND_ACK_TIMEOUT` and stop after 5 detector failures.
Repair ≥1.2s and REVIEW_REQUIRED are unchanged.

Artifact: `_tmp/bv1r_qual/asr.working.srt`
Repair: `_tmp/bv1r_qual/asr-repair-odvb8i7e.json`
Cues: 1389
FULL honest coverage: 99.98%
mega_cue_count: 0
significant_gap_count: 0
subthreshold after paint: 0.44s

| Run | Slice | Honest % | Mega | Sig gaps | Result |
|-----|-------|----------|------|----------|--------|
| 1 | 0-3m | 99.99% | 0 | 0 | PASS  |
| 2 | 3-8m | 99.99% | 0 | 0 | PASS  |
| 3 | 8-13m | 99.99% | 0 | 0 | PASS  |
| 4 | chunk-boundary | 99.97% | 0 | 0 | PASS  |
| 5 | 13-20m | 99.97% | 0 | 0 | PASS  |
| 6 | 20-26m | 99.97% | 0 | 0 | PASS  |
| 7 | 26-end | 99.96% | 0 | 0 | PASS  |
| 8 | first-10m | 99.99% | 0 | 0 | PASS  |
| 9 | mid-10m | 99.97% | 0 | 0 | PASS  |
| 10 | FULL | 99.98% | 0 | 0 | PASS  |

PASS COUNT: **10/10**

HARD GATES:
- full ≥90%: PASS
- mega cues 0: PASS
- 9/10 slices: PASS

FINAL: **ACCEPTED**

Translation (mocked, same direction): SEND_ACK_TIMEOUT releases
`_INFLIGHT`; the next different batch can send; a 10-batch storm
does not freeze later lô; send_streak stops after 5 detector
failures instead of marking the remaining 111/113 failed.
Live 113-lô Gemini is not part of this gate.
