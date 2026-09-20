# Bilibili adaptive CDN download

## Behavior

- Collect all `url`, `baseUrl`, `base_url`, `backupUrl` and `backup_url` candidates for the selected representation, with deduplication and CDN host validation. Do not substitute fabricated mirror hosts or truncate the backup list.
- Probe each candidate with a 2 MiB GET (the complete body for smaller files). Measure actual bytes/time, including connection setup. Up to six probes run concurrently; their measurements can share the available bandwidth.
- Choose the fastest successful probe. A server ignoring Range is measured too, then downloaded as a single stream.
- For sufficiently large Range-capable streams, download 2 MiB chunks with up to 12 concurrent workers. Require HTTP 206, exact Content-Range offsets/total and body length. Use a strong ETag with If-Range when available.
- Share CDN health across workers. A slow or failed chunk is retried against the next candidate, with a 30-second ranking cooldown for the failed CDN and at most two rounds over candidates. Completed chunks remain intact.
- Observe throughput over two-second windows. The per-connection threshold is the larger of 128 KiB/s divided by 12 and 20% of that CDN's probe speed divided by 12. Socket timeouts and bounded chunk deadlines also trigger retry.
- Only combine CDN chunks when the probed file size and prefix SHA-256 match. Write chunks in order, keep a SHA-256 manifest for resumable prefixes, and verify final disk size and SHA-256 before promoting the partial file. Existing cached files require matching integrity metadata and a full disk hash check.

Range validation follows [HTTP Semantics, RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#name-range-requests).

## Verification — 2026-09-16

- `python -m unittest discover -s tests -p 'test_bilibili*.py'`: 22 tests passed.
- `python -m unittest discover -s tests`: 655 tests, OK, one skipped (115.744 seconds).
- Real local HTTP transfers cover slow-CDN failover, shared ranking, invalid offsets, short bodies, parallel assembly, corrupt cached/partial files, and non-Range fallback.
- Live Bilibili probe for `BV1nmbp66EmA`: selected representation returned one CDN, `upos-hz-mirrorakam.akamaized.net`; 2 MiB measured at 895,071 bytes/s (about 0.85 MiB/s); Range supported; total size 224,798,015 bytes. Evidence: `_tmp/bilibili_cdn_live.json`.

## Limits

The live check was a sample probe, not a complete video download. This API response supplied only one CDN, so it cannot establish a real multi-CDN speed improvement or diagnose VNPT routing. Automatic switching requires another compatible URL supplied by Bilibili. Multi-CDN switching was verified against local HTTP servers.

SHA-256 verifies received data against disk contents and validates resumable chunks. Bilibili did not supply an authoritative full-file hash in this check; matching prefixes and sizes do not prove that two remote files have identical unprobed tails. Non-Range servers use a single full download and cannot switch offsets mid-transfer.

## Source checksum verification

When a selected playurl representation explicitly supplies a nonempty `md5`, `sha1`, or `sha256` field, the downloader validates its hexadecimal format and compares every supplied checksum with the complete downloaded stream before promoting the partial file. A mismatch raises an error. Cache hits are checked against current source checksums too. DASH video and audio are verified separately before muxing; their checksums do not apply to the resulting muxed MP4.

The `.integrity.json` sidecar records `source_verification.status` as `verified` or `unavailable`, alongside the checksum values and source `bilibili_playurl`. No source checksum is inferred from an ETag, URL signature, or the locally calculated SHA-256. Empty/missing fields mean unavailable; malformed nonempty fields are rejected.

Follow-up live inspection of `BV1nmbp66EmA` on 2026-09-16 found no explicit checksum fields in either sampled playurl response. A one-byte Range request to its CDN returned ETag `"7acda1b9996ae25a7ba73d186409279c-6"` and Content-Range, but no Content-MD5, Digest, Repr-Digest, or Content-Digest header. Consequently this video cannot currently be reported as verified against an upstream full-file checksum. Evidence: `_tmp/bilibili_origin_hash_live.json`.

Regression coverage includes valid/invalid checksum fields, rejection before file promotion, comparison of multiple digests, revalidation on cache hits, and explicit unavailable status. The Bilibili test subset now contains 24 passing tests.

Full validation after this addition: `venv\Scripts\python.exe -m unittest discover -s tests` ran 657 tests in 119.194 seconds: OK, one skipped. An earlier attempt with system Python failed due to missing project dependencies; use the project virtual environment.

## Runtime stall recovery — 2026-09-16

The supplied log for `BV1GmYT6FE2S` showed a direct-download failure at bytes 88080384–90177535 and a later yt-dlp audio stall near 76.7 MiB. It did not contain the underlying socket exception, so the precise network cause is unconfirmed.

- Single-CDN failures now explicitly report retrying the same CDN, with the exception type. They no longer claim a CDN switch without another candidate.
- After bounded per-chunk retries fail, the direct downloader reduces concurrency from 12 to 4 to 1, validating and resuming the saved prefix each time. Exhaustion still falls back through the existing downloader flow.
- Bilibili yt-dlp downloads have a byte-progress watchdog: each 60-second period must advance the high-water byte count by at least 64 KiB. Repeated/regressing counters and log chatter do not reset it. A stall stops the subprocess with a connection timeout error, activating the existing bounded retry with partial-file resume. Watchdog state resets per invocation/stream and is inactive after stream completion/during merging.
- The public video retried the exact failed 2 MiB range successfully in 1.0 seconds; this is a targeted range check, not proof that the entire video now downloads without stalls. Evidence: `_tmp/stall_live_range.json`.
- Targeted verification: 19 downloader tests and 24 Bilibili tests passed, including actual subprocess termination and verified-prefix recovery plumbing.
- Full project suite after the stall fix: 660 tests in 151.437 seconds, OK, one skipped (`venv\Scripts\python.exe -m unittest discover -s tests`).

### Follow-up: cached video followed by a stalled audio connection

The next runtime log exposed a gap: yt-dlp reported video format 100026 as finished (already downloaded), which disabled the watchdog before audio format 30280 produced its first progress event.

The watchdog now has explicit connection, downloading, between-streams and postprocessing states. Initial connection has a 120-second limit; the gap after a finished stream has a 60-second limit. Duplicate finished events do not extend that limit. A new stream starts its own byte-progress window. Only actual merger/file-output events disable network monitoring; process completion ends monitoring naturally. Existing bounded resume retries handle timeouts.

For a sole CDN, a low measured speed no longer triggers the CDN-switch cutoff: there is no alternative to switch to. Socket timeouts, the bounded chunk deadline (120 seconds in this case), exact Range checks and integrity checks still apply. Retry messages now include the specific error text with URLs redacted, rather than just `RuntimeError`.

Targeted tests: 21 downloader tests and 24 Bilibili tests passed, including the exact cached-video/absent-audio sequence, duplicate completion events, merge exclusion and single-CDN speed handling.

### Full-video runtime verification

A full direct-download attempt reproduced a concrete short-body error at bytes 897581056–899678207: the CDN repeatedly returned only 736,270 of the requested 2,097,152 bytes, even after reducing concurrency to 4 and 1. The attempt ended after 202.48 seconds and retained the verified prefix.

Short-body failures now also attempt distinct 256 KiB subranges. Every subrange must satisfy the same offset, total, ETag and length checks; the incomplete response itself is discarded. This recovery is bounded by the existing two candidate rounds and does not recursively split failing small ranges. A regression test verifies both successful reconstruction and bounded failure. There are now 22 passing downloader tests.

The resumed live run completed the video in another 87.0 seconds, without yt-dlp fallback. Final file: 1,015,871,266 bytes; ffprobe confirmed 1280×720 video and an audio stream. Local SHA-256: `718091e5be1ac07eaa8cb9ec20f11fc9ac42c0c85e0b92443da0ac81dec26c5a`. Upstream checksum remains unavailable. The live resumed run did not encounter another short-body failure, so live recovery by subranges is not established; that branch is covered by regression tests.

Evidence: `_tmp/stall_transition_live_full.json` (initial failure), `_tmp/stall_transition_live_resume.json` (completed resume), and `_tmp/stall_transition_live_verified.json` (media/integrity results). The file is retained under `_tmp/bilibili_runtime_full/`.

Final full suite: 663 tests in 120.687 seconds, OK, one skipped, using the project virtual environment.
