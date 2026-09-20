# Audio atomic-write regression (P0)

Date: 2026-09-17.

Pipeline stopped at **Tách audio**. Input MP4 opened (AV1 + AAC). ASR/FunASR never ran.

## ROOT CAUSE

Exact file: `autodub/video/extract.py`
Exact function: `_run_extract_command` (previously inlined in `extract_audio`)

Atomic write used:

```text
final = audio16k.flac
temp  = final + ".partial"   →  audio16k.flac.partial
```

FFmpeg 9 infers the muxer from the **last** suffix. `.partial` is not a media format, so muxer init fails:

```text
Unable to choose an output format for '...audio16k.flac.partial'
Error initializing the muxer: Invalid argument
```

Reproduced on at least two downloaded films (`BV1GAY56VEwU`, `BV1rF4Q6EEUT`). Not AV1, not Unicode paths, not FunASR.

Zero-byte `audio16k.flac` without sidecar was treated as a cache miss (`missing_file … size=0 không sidecar`) and retried into the same broken temp name.

## BEFORE / AFTER

| | path |
|---|---|
| BEFORE temp | `audio16k.flac.partial` |
| AFTER temp | `audio16k.<token>.partial.flac` |
| final | `audio16k.flac` (unchanged) |

Helper: `autodub.video.common.media_temp_path`.

Same rule for other media suffixes:

- `audio.wav` → `audio.<token>.partial.wav`
- `audio.m4a` → `audio.<token>.partial.m4a`
- `video.mp4` → `video.<token>.partial.mp4`

Rejected forms: `*.flac.partial`, `*.flac.tmp`, `*.flac.part`.

## WHY FFMPEG FAILED

FFmpeg chooses the output muxer from the filename extension unless `-f` is given. `.partial` is unknown, so the FLAC muxer never starts. The input demuxer had already succeeded.

## FIX

Atomic write is kept. Temp name keeps the real media suffix last.

Defense in depth in `_run_extract_command`:

1. `temp_path = media_temp_path(out_wav)` → suffix preserved
2. `audio_muxer_args(out_wav)` → explicit `-f flac` / `-f wav` / … from **requested** type (not a generic runner hardcode)
3. FFmpeg writes only to temp
4. Validate temp: exists, size > 1024, ffprobe duration > 0, audio stream, expected sample rate, mono
5. `os.replace(temp, final)` only after validation
6. Sidecar `.source.json` only after a successful `ensure_audio` extract
7. On FFmpeg / validation / cancel failure: delete temp only; valid old final is kept; size ≤ 1024 invalid final is unlinked and is not a cache hit
8. `discard_stale_media_temps` removes leftover `audio16k.flac.partial` and `audio16k.*.partial.flac` without touching the final
9. `ffprobe_duration` re-raises `InterruptedError` instead of returning `0.0`

## FILES CHANGED

- `autodub/video/common.py` — `media_temp_path`, `audio_muxer_args`, `discard_stale_media_temps`
- `autodub/video/extract.py` — atomic extract/commit/validate
- `autodub/video/__init__.py` — exports
- `autodub/utils.py` — cancel not swallowed by `ffprobe_duration`
- `tests/test_audio_atomic_write.py` — naming, regression muxer, 10-run matrix, short ASR smoke
- `tests/test_asr_audio_reuse.py` — temp now ends with `.partial.wav`

## AUDIT

Project search for `.partial` / `.tmp` / `.part` plus FFmpeg outputs:

- **Fixed:** ASR extract temp (`extract.py`). This was the only FFmpeg media writer using `path + ".partial"`.
- **Not changed:** download `.part` (yt-dlp / Bilibili CDN), JSON `.tmp` sidecars, SRT/text temps. Those do not need FFmpeg muxer inference.

Job folders no longer hold a leftover `audio16k.flac` (only `_tmp/clips`). Next GUI extract will not treat a 0-byte file as reusable.

## TEST MATRIX

| Run | Case | Result |
|-----|------|--------|
| 1 | short MP4 ASCII filename | PASS |
| 2 | short MP4 Chinese filename | PASS |
| 3 | Vietnamese Unicode path | PASS |
| 4 | spaces + `[]` path | PASS |
| 5 | existing target absent | PASS |
| 6 | existing valid cache → reuse (`Dùng lại audio đã tách`) | PASS |
| 7 | existing zero-byte target → regenerate | PASS |
| 8 | forced FFmpeg failure → old valid final survives | PASS |
| 9 | cancel extraction → no corrupt final; rerun works | PASS |
| 10 | real Bilibili files extract in scratch + 45s Paraformer smoke | PASS |

PASS COUNT: **10/10**

Hard regression unit: FFmpeg refuses `audio16k.flac.partial` and accepts `audio16k.jobid.partial.flac` (no `-f` in that probe).

Real films were extracted to a **scratch directory**, not `output/.../_tmp/audio16k.flac`, so the full-job cache was not poisoned.

## HARD GATES

| Gate | Result |
|------|--------|
| real video extraction works (both downloaded MP4s, 3s scratch extracts) | PASS |
| no corrupt final | PASS |
| atomic replace correct (temp suffix `.flac`, then replace) | PASS |
| valid old cache survives failure | PASS |
| Unicode Windows path works | PASS |
| short ASR smoke reaches recognition (Paraformer, Chinese text, 45s of `BV1rF4Q6EEUT`) | PASS |

HARD GATES: **PASS**

SHORT ASR SMOKE TEST: **PASS**

Existing suite: `python -m unittest discover -s tests -v` → **743 tests, 0 failed, 2 skipped**.

## FINAL

**ACCEPTED**

ASR chunk / VAD / coverage / Whisper / repair heuristics were not changed in this round.
