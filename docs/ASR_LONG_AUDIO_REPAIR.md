# ASR long-audio repair — BV1GAY56VEwU

Date: 2026-09-16. Work performed directly in `E:\Video\AutoDubVN`.

## Outcome and remaining quality work

The long-audio execution and normalization bugs are repaired. The film is **still REVIEW_REQUIRED**, not translation-ready. No quality threshold was relaxed and no translation/TTS was started.

After the parser replay, two packing defects were still erasing recognized source and inventing speech gaps:

1. Isolated 1–2 character cues were dropped when jieba had no compound entry, even when every character was in the lexicon (`谁呀`, `虎哥`, `给你`, `元婴`).
2. A 2-character cue with fewer marks than letters (`搜尋`, 2 chars / 1 mark) was withheld, which deleted gap-rescue text and reopened the surrounding VAD interval.

Those words are now kept. Extra marks on a 1–2 character cue still withhold (the punctuation-clock failure mode). Unknown glyphs outside the lexicon still withhold. Confirmed VAD gaps ≥ 1.2 s still block.

A production resume reused all 13 checkpoints (no chunk re-inference), then ran gap rescue. Wall time 599.3 s including FunASR load and Whisper large-v3 on remaining gaps.

| Stage | Source | Packed | Blockers | Union | Notes |
| --- | --- | --- | --- | --- | --- |
| User log 17:46 | — | — | 92 | 167.96 s | 4 weak alignment, 35 unknown fragments, 53 speech gaps |
| Raw checkpoint replay | 4,493 | 4,143 | 90 | 111.23 s | Parser-only; no gap rescue |
| Packing vocative fix | 4,493 | 4,173 | 56 | 93.93 s | Unknown-fragment blockers → 0 |
| Checkpoint resume + gap rescue | 4,532 | 4,199 | 34 | 51.21 s | 31+2 gaps filled; 1 weak `搜尋` |
| Keep under-aligned short cues | 4,532 | 4,200 | 32 | 49.61 s | `搜尋` kept; that 1.6 s gap dropped below threshold |

Current speech coverage is 86.78% of FSMN speech (5,557.7 s covered / 6,404.19 s speech). The remaining 32 blockers are all `unresolved_speech_gap` (longest 3.03 s). Review: `_tmp/caption-review-mrqotoyz`. Working source: `一亩灵田修长生 [BV1GAY56VEwU].asr.working.srt`. Repair report: `_tmp/asr-repair-s0q2umcn.json`. Human-verified reference subtitles are unavailable.


## Recovery before inference

All paths below are under `output/一亩灵田修长生 [BV1GAY56VEwU]`:

- Original review/source artifacts remain intact. They contain 3,823 packed cues, not the reported approximate 3,833. Recovered source has 2,119 cues, including two problematic giant cues.
- `一亩灵田修长生 [BV1GAY56VEwU].asr.recovered.srt`: recovered source.
- `一亩灵田修长生 [BV1GAY56VEwU].asr.recovered.packed.srt`: recovered 3,823 display cues.
- `一亩灵田修长生 [BV1GAY56VEwU].asr.working.before-token-reparse.srt`: preserved previous working transcript.
- `一亩灵田修长生 [BV1GAY56VEwU].asr.working.srt`: current working source after parser reparse and gap rescue (4,532 cues). A copy from before the token reparse remains in `.asr.working.before-token-reparse.srt`.
- `_tmp/asr-reparsed-oxulreal`: independent replay evidence, source/packed SRT, speech map, result and review files. Replay took 3.422 s and made zero inference calls.
- `_tmp/asr_chunks/d3dbdd8513529c91d9376b68`: retained 13 raw checkpoints from the user's latest run.
- `_tmp/asr_chunks/c20747ef40142e021860fa2b`: parser-version-2 normalized checkpoints. Production migration reused all 13 raw results; extraction/generation callbacks were guarded to fail if called. Evidence: project-root `_tmp/asr_production_reparse.log`.

The existing 7,217.450688 s `audio16k.flac` was reused. No complete two-hour model inference was launched during this investigation.

## Root causes

### 1. Whole-file attempt wasted roughly 25 minutes

The previous direct-inference limit was three hours, so this two-hour file entered whole-file FunASR inference before chunk fallback. The strategy itself explains why the costly attempt was allowed. The exact reason that historical model call returned empty cannot be established: its raw output was not persisted. It must not be attributed conclusively to VAD, memory, or a library version from the log alone.

The new threshold is ten minutes. Longer audio enters checkpointed chunking before generation. Exceptions after a long run starts propagate for resume rather than silently launching another whole-file backend/CPU attempt.

### 2. Chunk 1 and chunk 4 collapsed to one line

Two reproducible parser defects were present:

1. The CJK timestamp fallback returned the entire parent text as one segment in screen-caption mode. The sentence-info consistency comparison was case-sensitive; `Go` versus `go` rejected otherwise equivalent sentence text and triggered that fallback.
2. Native token counts differ from character counts: a Latin word is one timestamp token. Furthermore, the latest runtime raw data contains punctuation-only sentence-info entries with many timestamps. Concatenated lexical equality alone does not establish sentence-to-clock alignment. In chunk 1, punctuation-only tails carry 18 and 7 timestamp pairs around 588–597 s.

The normalizer now prefers the parent's exact native token/timestamp alignment when counts match, preserves original punctuation/text, and groups those observed clocks into cues. All 13 latest raw chunks match this native-token count exactly. Sentence-info remains a fallback when parent alignment cannot be established; lexical comparison is case-insensitive without discarding substantive text differences.

Replaying actual 0–30 m raw output through the reconstructed old parser gives one segment; corrected parsing gives 1,059. The 90–120 m raw result gives one versus 1,116. Recognized lexical text is identical in both comparisons. The exact historical chunk-1/chunk-4 raw results are unavailable, so this reproduces the failure mechanism, not a bit-for-bit replay of those lost calls.

### 3. Why 30 + 36 clock attempts fixed nothing

Unknown text fragments and overlapping clocks were being treated as recognition targets. Contextual Whisper frequently returned different text, which correctly failed the exact-text requirement of clock repair. Repeating that strategy could not safely repair the clock.

Clock rescue now selects only missing/weak alignment or words crossing pauses, does not launch Whisper for those warnings, stops a repeated zero-fix pass, and retains the bounded consecutive-unproductive-window limit. A persistent ledger shares failed crop/engine/model/language/parameter attempts across clock and gap repair. The latest user runtime already showed 8 clock attempts / 0 fixes instead of the earlier 66; corrected raw parsing removes its four weak-alignment blockers.

Whisper fallback is restricted to FSMN-confirmed, threshold-sized uncovered speech gaps. Known `zh` is passed from the primary ASR result when configuration leaves language unset. Models remain cached; load count, load time, inference time and effective device are logged separately. Previous telemetry is cleared before each call so a failure cannot inherit the prior call's measurements.

### 4. Why the user did not receive working subtitles after gate failure

The final main SRT was saved only after the gate, and the server treated caption review as a generic pipeline exception. Review artifacts existed but the main working file and job state were misleading.

Working source SRT is now saved after each successful chunk, before repairs and before the gate. Review artifacts include source, packed-needs-review, unresolved rows, speech map and summary. `CaptionReviewRequired` exposes `REVIEW_REQUIRED`; the pipeline logs a review warning without a generic traceback and the job manager records `review_required`. A fresh run clears stale review status, preventing a later successful run from inheriting it.

## Long-audio strategy

- Default 600 s cores with 1.5 s context overlap. Prefer a nearby VAD silence within 5 s when available; otherwise use the bounded nominal boundary.
- Diagnose empty output, implausibly sparse text/timestamps, giant cues and weak timestamp coverage against measured speech. A true silent chunk can remain empty.
- Recursively split suspicious parents near speech boundaries, bounded by minimum 60 s children and depth 4. Unresolved leaves remain explicit review blockers.
- Persist raw output before parsing, diagnostics, normalized segments/marks and per-chunk SRT atomically. Checkpoint identity includes audio SHA-256, model/device identity, language, caption options and chunk policy.
- Resume healthy chunks without inference. Parser-version changes can reuse compatible raw output while rebuilding normalized checkpoints.
- Assign exact aligned tokens to chunk cores by token midpoint; retain their original observed clocks. This avoids duplicating overlapping context and does not invent timestamps.
- Failure-injection tests cover two successes followed by a crash, resume at the failed chunk, suspicious-parent splitting, two-hour whole-file skip, parser migration and token ownership.

Ten minutes is retained for checkpoint frequency and compatibility with the user's 13 completed chunks. The fastest single CPU fixture was 15 minutes; speed alone does not justify using 30-minute cores or invalidating the existing checkpoints.

## Runtime evidence

Actual configured Paraformer + FSMN VAD + punctuation model, not a substitute model. Raw outputs and metrics are in project-root `_tmp/asr_benchmark_v2`, `_tmp/asr_benchmark_90`, `_tmp/asr_benchmark_gpu`; consolidated evidence is `docs/asr_benchmark_results.json`.

- CPU, starting at 0: 5 m / 19.453 s; 7.5 m / 26.985 s; 10 m / 37.375 s; 15 m / 49.031 s; 20 m / 71.312 s; 30 m / 107.750 s inference. Cold model load 18.812 s. Corrected parser cue counts: 158, 222, 303, 500, 678, 1,059 respectively.
- CPU, starting at 90 m: 5 m / 33.469 s; 15 m / 114.203 s; 30 m / 196.266 s. Corrected counts: 196, 552, 1,116. Cold load 31.546 s. Some measurements overlapped test activity; these are observed wall times, not controlled hardware speed claims.
- RTX 3060, effective `cuda:0`, starting at 0: 5 m / 10.125 s, cold load 35.750 s, 158 segments. CPU/GPU inference was measured separately from loading. Production device was not changed based on this one sample.
- Every bounded fixture is structurally healthy. This does **not** mean the subtitle quality gate passed.
- Initial CPU-prefix metric files were collected before the second parser fix. `new_parser_segments` and lexical equivalence in the consolidated report are offline replays; its original `normalized_segment_count` and coverage remain the measurements made at collection time. Do not mix those into a single before/after quality claim.

Old total time: logs establish roughly 25 minutes wasted before chunking, approximately 296.46 s extraction, and later expensive rescue. A reliable full end-to-end total cannot be reconstructed from the available partial logs. New total time: not measured for a fresh two-hour end-to-end job; it would be misleading to extrapolate fixture RTF into a claimed completed total.

## Coverage and forensic interpretation

Media coverage divides subtitle time by all media time. Speech coverage intersects subtitles with the union of VAD speech and divides by speech time. They answer different questions.

The user's 17:46 report: speech 6,404.19 s; covered 5,591.55 s; unresolved 812.64 s; speech coverage 87.3108%. Its breakdown is 152.785 s significant gaps plus 659.855 s below-threshold gaps. Its gate has 92 regions / 167.96 s: four weak alignment, 35 unknown fragments and 53 speech gaps.

The corrected raw-only replay: speech coverage 86.2745%, unresolved 879.007 s; significant gaps 100.284 s; subthreshold remainder 778.723 s. Gate union is 111.234 s because it includes unknown-fragment regions as well as significant speech gaps. The smaller significant-gap total and disappearance of weak alignment are useful evidence, but overall coverage is not directly comparable: earlier rescue additions are absent and finer observed clocks remove inflated cue spans.

The uncovered-gap scan now unions overlapping VAD ranges before thresholding and uses the same covered-time union as the coverage metric. Previously small fragments could be thresholded separately, producing inconsistent counts. Short VAD-minus-caption intervals include pauses, padding and timing uncertainty; 879 s of this metric does not prove 879 s of missing spoken words. Strict confirmed gaps remain blocked.

`docs/asr_gap_forensics.json` preserves the older 475.65 s investigation, including neighboring text, detector evidence, attempts and the two giant source cues. Its older threshold counts are historical evidence, not recomputed current counts. The latest review's `unresolved.json` is the current actionable region list. FSMN does not provide a calibrated confidence score here; no confidence has been fabricated.

## Validation and limitations

ASR packing/gate/job tests in this continuation passed (81 tests, 1 skipped). A later full `unittest discover` ran 681 tests, 1 skipped; two failures were in `test_dub_editor_ui` font-size assertions and are unrelated to ASR packing. Browser response detection was not modified.

Still requiring acceptance: listening review of the remaining 32 confirmed speech gaps; human-referenced quality comparison before changing the default fallback model/device. Context-overlap ownership is regression-tested, but a controlled 0 s versus 1.5 s overlap quality experiment has not been completed. A fresh GUI ASR run must load this Python (restart the server if an older process is still running); checkpoints and the attempt ledger are reused.

## Targeted Whisper comparison

Evidence: `_tmp/asr_gap_benchmark/results.json` and `auto_language_sample.json`. Three retained VAD-confirmed historical gaps at 1425.543–1434.683 s, 3061.05–3062.44 s and 5396.52–5398.61 s, cropped with 1 s context. Nine real calls, CPU/int8, explicit `zh`; a tenth call measures auto language with the small model already warm.

- large-v3 inference: 20.891, 12.984, 14.093 s; accepted target timing: 6.54, 0, 1.46 s. First load 10.484 s.
- medium inference: 6.718, 5.938, 8.344 s; accepted target timing: 0, 0, 0 s. First load 30.422 s (cache/network resolution included).
- small inference: 3.672, 1.938, 2.609 s; accepted target timing: 6.66, 0, 1.74 s. First load 11.391 s (cache/network resolution included).
- Every model's load count remains one across its three calls; subsequent load time is zero. Auto language on the first crop with warm small takes 5.657 s versus 3.672 s for explicit `zh` inference. This single sample supports avoiding unnecessary detection, not a universal speed guarantee.

Accepted timing means passing the existing target-window timing filter, not passing lexical quality or the entire caption gate. Transcripts disagree (e.g. `炼器` versus `练技`); no reference establishes equal quality. Therefore the default fallback remains unchanged. Results were kept as benchmark evidence rather than spliced into the film without the full merge/quality gates.

## Extraction profile

Evidence: `_tmp/asr_extraction_results.json`. Same downloaded MP4, first 60 s, three repetitions, measured after model benchmarks and before final tests. Median wall times: FFmpeg startup 0.045 s; decode-to-null 0.151 s; decode/resample-to-null 0.168 s; decode/loudnorm/resample-to-null 2.041 s. These are end-to-end stages and must not be added together as independent CPU costs.

Normalized output: FLAC level 5 median 1.974 s / 1,306,177 bytes; FLAC level 0 2.027 s / 1,365,720 bytes; PCM 2.065 s / 1,920,108 bytes. Decoded PCM SHA-256 is identical for all three candidates. Disk copy plus flush takes roughly 4–8 ms. On this fixture, normalization dominates; changing compression level does not establish a useful speed improvement. Loudness normalization and production encoding were retained. The existing full extracted audio was reused rather than recomputed.

## Reproduction commands

Use `venv\Scripts\python.exe`; system Python does not have the required model dependencies. The long-audio benchmark additionally uses `psutil` (7.2.2 installed in this venv). Fixture arguments reject durations above 30 minutes.

```powershell
venv\Scripts\python.exe scripts/benchmark_asr_long_audio.py 'output/一亩灵田修长生 [BV1GAY56VEwU]/_tmp/audio16k.flac' --out _tmp/asr_benchmark_v2 --device cpu --minutes 5 7.5 10 15 20 30
venv\Scripts\python.exe scripts/benchmark_asr_long_audio.py 'output/一亩灵田修长生 [BV1GAY56VEwU]/_tmp/audio16k.flac' --out _tmp/asr_benchmark_90 --device cpu --start 5400 --minutes 5 15 30
venv\Scripts\python.exe scripts/replay_asr_checkpoints.py 'output/一亩灵田修长生 [BV1GAY56VEwU]/_tmp/asr-repair-fwg5mmx1.json'
venv\Scripts\python.exe -m unittest discover -s tests
```

Completed fixtures are skipped by the long-audio benchmark script. Raw replay creates a new evidence directory and does not invoke ASR. The ordinary application ASR path resumes its compatible checkpoints; it must be restarted to load these Python changes if an older server process is still running.

`docs/asr_latest_gap_forensics.json` is the 90-region raw-replay snapshot. It is historical. The current actionable list is `_tmp/caption-review-mrqotoyz/unresolved.json` (32 speech gaps, 49.61 s).

## Acceptance checklist

- [x] Two-hour inputs skip whole-file generation; proactive chunking has a regression test.
- [x] Suspicious parents split with bounded depth; speech-aware boundaries and overlap ownership are implemented and tested.
- [x] Atomic chunk checkpoints and working source survive injected failure and gate failure.
- [x] Resume avoids completed-chunk inference; 13 real raw checkpoints were migrated without generation.
- [x] Whisper fallback requires confirmed gaps and receives known source language.
- [x] Failed attempts are deduplicated; a zero-fix clock pass does not launch an equivalent second pass.
- [x] Actual 0–30 m and 90–120 m fixtures were run independently and raw outputs retained.
- [x] Review remains blocking; source/packed/unresolved artifacts are preserved.
- [x] Isolated lexicon-supported vocatives/names are no longer dropped from the pack.
- [x] Under-aligned short cues with observed marks are kept; extra-mark 1–2 character cues still withhold.
- [x] Production resume reused 13 checkpoints and ran gap rescue (599.3 s wall).
- [ ] All remaining regions resolved and final film passes quality gate — **not achieved**, 32 confirmed speech-gap blockers remain (49.61 s).
- [ ] Fresh end-to-end total runtime and human-referenced quality acceptance — not claimed from fixture timings.
