# P1 ASR gap repair investigation

## Reproduction and root causes

Verified against the installed FunASR **1.4.2**, its local `auto/auto_model.py`,
and the retained audio for **BV1nmbp66EmA** (1,983.381375 seconds). The dependency
file excludes 1.3.9 but does not pin one version; the tests specify the supported
native timestamp contract explicitly.

Seven of eight replayed candidate regions returned `text="", timestamp=[]`.
Their shape was exactly `list(len=1, first=dict(keys=[key, text, timestamp]))`.
The eighth region returned usable timestamps. FunASR's VAD path deliberately
constructs this empty record when no speech is detected. The old exception
misclassified it as a timestamp parsing error; key names could not reveal this.
The original logs alone cannot establish the values for every historical call.

Separate, reproducible implementation problems were found:

- Short native millisecond pairs could be guessed as seconds based on magnitude.
- Any nonempty sentence-info list suppressed the parent timestamp fallback,
  even when those sentence records could not be normalized.
- Segment and speechmap extraction parsed output separately. Repair added marks
  before dedupe/acceptance, including marks from rejected or context-only cues.
- Every round could retry the same audio and engine parameters. Progress meant
  added line count rather than a reduction in uncovered target duration.
- FFmpeg non-silent candidates were called speech; music and effects triggered
  recognition without independent speech evidence.
- The packing gate was unrelated to the displayed media coverage percentage:
  `ensure_complete` blocked when **any review row had `withheld=True`**.

The historical review contains 193 withheld rows: 179 `word_crosses_pause`,
5 isolated fragments, 4 weak alignments, 3 missing-mark windows, 1 oversized
token and 1 overlapping clock. Lexical packing can join characters across ASR
cue edges and then discover that their observed marks cross a pause. For example,
the source places 上 at 36.34–38.00 and 面 at 38.38–40.22; the combined word
上面 crosses the configured 0.32-second pause limit. This is distinct from an
empty ASR gap. The new code does not silently erase these source words or loosen
that gate to make the job pass.

## Behavior after the change

`normalize_funasr_result` produces segments and observed marks in seconds,
plus bounded diagnostics and `ASR_EMPTY`, `ASR_TEXT_NO_TIMESTAMP`,
`ASR_INVALID_TIMESTAMP` or `ASR_VALID`. Native pair lists/tuples are milliseconds;
Nano `timestamps` dictionaries are seconds. Adapters can declare `timestamp_unit`
and `timestamp_origin`; native results remain relative to the input clip. Cropped
repair adds the extraction offset once. No unit or absolute origin is guessed
from numeric magnitude. A supplied single token mark is retained; a sentence
boundary alone is not relabeled as a word mark.

The ladder uses a 0.35-second context window, a 1-second context window, direct
recognition with the already loaded Paraformer for confirmed short speech, and
the configured existing fallback engine. Targets are at most 20 seconds. Text
from context is trimmed only when exact observed token/text alignment permits it.
Otherwise the candidate is rejected. Accepted subtitles alone contribute marks.
Fingerprints include the crop, engine and parameters, including direct/VAD mode.
An unavailable engine is disabled for the rest of that repair run. Each round
rescans coverage and stops without meaningful duration improvement.

The loaded FSMN VAD is reused for independent speech detection. Its ranges are
estimated speech evidence, not perfect ground truth. When VAD is unavailable,
FFmpeg produces explicitly labeled non-silent candidates, which do not by
themselves trigger the speech-gap gate. Nearby targets never merge across an
existing subtitle; context supplies acoustic continuity without claiming that
intervening text is missing.

Withheld caption clocks receive a separate bounded rescue. A replacement must
preserve the exact original spoken text, supply observed timestamps and pass
local packing. Text disagreement remains review material. Twelve consecutive
unproductive windows stop this additional pass. No coarse subtitle fallback is
enabled: the observed failing records contain no trusted text, and text alone
provides insufficient confidence to invent alignment.

The strict final gate runs after hallucination filtering. It retains withheld
packing rules, rejects corrupted source clocks, and blocks unresolved VAD speech
gaps of at least the configured `speech_gap_seconds` (1.2 seconds by default).
Every run saves repair attempts, rounds, failure reasons and coverage separately.
A blocked run additionally saves source/packed SRT, speechmap, `review.json`,
and `summary.json` with the actual rules, thresholds and duration/count totals.

## Real replay and limitations

Full recognition and repair were rerun on the same 33-minute audio. The speech
detector selected five initial repair regions; three were improved in round one.
The final gate still blocked 194 rows: 181 word/pause conflicts, five isolated
fragments, one oversized token and seven significant speech gaps after packing.
Their union was 234.78 seconds, longest union 22.49 seconds. These are **blocked
review intervals**, not 234.78 seconds of proven missing speech. VAD-estimated
speech coverage was 78.38%; this percentage is descriptive, not the abort rule.

The configured faster-whisper fallback failed to import because Windows
Application Control blocked a DLL. This is now recorded once per repair run;
the code does not change that system policy. An exhaustive diagnostic clock
replay made 202 distinct attempts with no accepted exact-text replacement and
took 442.33 seconds including main ASR. This evidence motivated the final
12-unproductive-window limit; the bounded replay and its report are retained in
`data/asr_p1_bounded_replay.json`. Replaying the saved full-ASR fixture with this
limit made 24 attempts in 57.56 seconds, then stopped with the same 194 review
rows. It did not repeat primary full-file recognition. **The real video is not approved for
Translation/TTS, and this investigation does not claim that every DoD item is
complete.** Remaining source/clock conflicts require review or a working
alternative recognition/alignment backend.

## Performance and verification

On the first 60 seconds of the original MP4, three-run median FFmpeg times were:
startup 0.041 s; decode-to-null 0.104 s; decode/resample-to-null 0.119 s;
decode/loudnorm/resample-to-null 1.521 s. Complete FLAC level 5 extraction took
1.516 s, FLAC level 0 1.521 s and WAV 1.511 s. All three decoded to the same PCM
SHA-256. These are measured end-to-end stages, not additive internal CPU times;
ASR was also active during this benchmark. Loudness normalization dominates;
changing the lossless format offers no demonstrated material gain, so FLAC level
5 remains unchanged. The mono validation decode remains for existing phase safety.

Audio reuse now recognizes a valid WAV/FLAC sibling, validates mono/sample rate
and duration, and records source identity, trim and filter parameters to prevent
subsequent stale reuse. Legacy files without provenance still use validation
within their existing job directory before registering that identity.

The eight-region context benchmark made 16 recognition calls with **one** model
construction (including the cached VAD/punctuation bundle). Exact crops yielded
seven empty results and one valid result. One-second padding yielded text for all
eight, but only two subtitle candidates fell inside the missing target regions.
This demonstrates why accepting every padded transcript would duplicate context.

112 focused tests passed, covering normalization, clipping/offsets, merge/rescan,
retry bounds, unavailable engines, non-speech, strict gates, clock rescue, model
reuse, audio reuse and existing caption/media/pipeline regressions. The bounded
observations are stored in `tests/fixtures/funasr_p1_observed.json`.

Reproducible tools:

```powershell
venv/Scripts/python.exe scripts/audit_asr_regions.py AUDIO REGIONS_JSON --output audit.json
venv/Scripts/python.exe scripts/benchmark_asr_extraction.py VIDEO --seconds 60 --output benchmark.json
venv/Scripts/python.exe -m unittest discover -s tests -p 'test_asr*.py'
```

Local evidence is retained in `data/asr_p1_reproduction.json`,
`data/asr_p1_context_benchmark.json`, `data/asr_p1_extraction_benchmark.json`,
`data/asr_p1_rerun_final.json`, and `data/asr_p1_tests.log`. Original job artifacts
were preserved; replays create new review directories.
