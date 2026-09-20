# Gemini browser turn-tracking fix

**FINAL STATUS: REJECTED**

Scope: Gemini Web translation detector only. ASR, TTS, downloader, JSON parser,
circuit breaker, and semantic gates were not changed. Timeouts and retry budgets
were not raised.

Live Edge profile copy: `_tmp/browser_profile_turn_repro` (GUI held the app
profile lock). Artifacts: `_tmp/gemini_turn_tracking_repro/`,
`_tmp/gemini_turn_10run/`.

## Qualification

| Run | What | Result | duplicate_send | wrong_turn |
|---|---|---|---|---|
| Repro (pre-fix `_ask_once`) | 8 short JSON sends | 2/8 | n/a (count ACK) | n/a |
| 10-run iteration 1 | prompt_hash + associated turn | **4/10** | 0 | 0 |
| 10-run iteration 2 | seq_index pairing, suffix match | **5/10** | 0 | 0 |
| Strategy B | MutationObserver on conversation root | **5/10** | 0 | 0 |
| Strategy C | new chat before every request | **3/10** | 0 | 0 |
| 20-batch soak | production semantic batches | **not run** | — | — |

Acceptance required ≥8/10, zero hard failures, then a 20-batch soak with
duplicate=0 and wrong-turn=0. The 10-run never reached 8/10, so soak did not
start.

Hard failures (duplicate send, wrong-turn mapping, stale reuse, detector crash,
cache loss): **none observed** on the 10 predefined requests. Failures were
missed responses / missed ACK, not mis-attributed JSON.

## Answers

### 1. Why did `user_count` go 2→3 then back to 2?

`infinite-scroller[data-test-id="chat-history-container"]` only keeps a short
window of mounted turns. After send, Gemini briefly mounts the new `user-query`
(count 3) then unmounts an older node so the mounted window stays ~2–5. ACK
that treated `user_count++` as truth saw 3; the next diagnostic saw 2 even
though the prompt had been accepted.

Repro request 4: ACK `2 → 3`, then `user_count=2, model_count=2`. Same pattern
on requests 6–8.

`user-query-content-0` / `user-query-content-1` (and later
`user-query-content-undefined`) are **slot ids**, reused across turns. They are
not stable turn identity.

### 2. Does Gemini use a virtualized DOM?

Yes. Evidence:

- Mounted `user-query` / `model-response` counts are non-monotonic inside a
  turn (3 then 2) and stay far below the number of sends.
- After several turns, `scrollHeight` stays in a band (~1069–1871) while more
  messages exist than mounted nodes.
- Sibling parent of both `user-query` and `model-response` is a wrapper `div`
  inside the infinite scroller, not a stable per-turn custom element with a
  unique id.
- A 10-turn conversation in an earlier dump still had 10 nodes; virtualization
  shows up once the window is larger / after repeated sends. It is real on this
  UI, not a selector myth.

### 3. Why `MODEL_RESPONSE_STARTED` / `STREAMING` with `chars=0`?

Two stacked bugs:

1. **`seen` was overloaded.** A new/pending host or a generating spinner set
   `seen=True` and jumped to `MODEL_RESPONSE_STARTED` + `STREAMING` even when
   `extract_response_text` returned empty.
2. **Extract used `models[-1]` of `_new_models`.** After the virtualizer
   dropped the new turn, the remaining node was the previous turn (filtered as
   “not new”), so `last_candidate_chars=0` while `generating=False`. Then
   `RESPONSE_EXTRACTION_FAILURE` after the existing 12s empty-after-generation
   budget.

Live diagnostic (pre-fix request 2): `seen=True`, `saw_generating=True`,
`last_candidate_chars=0`, `user_count=1`.

### 4. What did `seen` mean before the fix?

It meant “a new model/pending host appeared **or** generating was true”, and
the wait loop treated that as “the response exists”. It did **not** mean
“current-turn JSON text is present”. After the split:

- `saw_generation_signal` — spinner / pending for this turn
- `saw_response_node` — a non-pending model node for this turn
- `response_text_seen` — extracted text length > 0

`MODEL_RESPONSE_STARTED` is no longer emitted from a spinner alone.

### 5. Which selector holds the real response?

Unchanged from the P0 dump; JSON lives in:

- `message-content#message-content-id-r_*`
- `div.markdown#model-response-message-contentr_*`
- `structured-content-container.model-response-text`

`model-response` / `response-container` `innerText` includes the chrome prefix
`Gemini đã nói`. Host fallback is only used when `{` is present.
`textContent` can lead `innerText` while streaming. Snapshot `pickText` still
prefers the message-content / markdown child, then host if it contains `{`.

### 6. How does the new turn mapping work?

Each request gets `request_id` + `prompt_hash` from the normalized prompt (no
UUID injected into the prompt).

After send:

1. Find the mounted `user-query` whose text contains the full prompt, or the
   last 40 characters of the last line (not the shared first 48 characters).
2. Pair that user with its model by equal-count document order
   (`users[i] ↔ models[i]`) falling back to next-sibling `model-response` /
   `pending-response`.
3. Extract only that associated model’s message-content text.
4. `SEND_ACK` is prompt-as-user-turn, or composer-clear **plus** generation —
   not `user_count++`. If the 12s ACK loop misses but the prompt is already in
   the DOM: `SEND_ACK_RECOVERED`, no resend.
5. Duplicate guard: existing user turn + complete response → consume; user
   turn + generating / empty response → wait; resend only when the prompt is
   not in the chat.
6. Empty-after-generation: one `RECOVERY_RESCAN` (re-hash, re-pair, one
   scroll-to-latest) then `RESPONSE_EXTRACTION_FAILURE`.
7. Strategy B: `MutationObserver` on the conversation root writes
   `window.__AUTODUB_TURN` (user found, model found, text, generating). The
   wait loop merges that with the snapshot. It does not poll global counts as
   identity.

Production `_ask_once` / `_wait_reply` use this path. Offline tests that omit
`msg=` still use the old new-host helper so P0 replay stays intact.

### 7. Was there duplicate send before the fix?

Yes. `SEND_ACK_TIMEOUT` **abandoned inflight** and the outer semantic retry
submitted the same prompt again, even when the user bubble already existed
(count had already gone 2→3). Circuit breaker then saw five detector failures
and stopped (`Browser automation appears unhealthy`, missing batches).

After the fix, 10-run submit counts were **10 submits / 10 predefined
requests** on every qualification (duplicate_send=0). `SEND_ACK_RECOVERED`
does not resend.

### 8. 10-run qualification

Predefined prompts `ten-01` … `ten-10`. Not cherry-picked.

- Iteration 1: **4/10**
- Iteration 2: **5/10**
- Strategy B: **5/10**
- Strategy C (new chat per request): **3/10** (more `SEND_ACK_TIMEOUT` while
  the new-chat composer settled)

Best 10-run for the acceptance bar: **5/10**. Required: ≥8/10.

When a request passed, the state sequence was complete:

`COMPOSER_READY → PROMPT_INSERTED → SEND_TRIGGERED → SEND_ACK_PENDING →
SEND_ACKNOWLEDGED → CURRENT_USER_TURN_IDENTIFIED → GENERATION_SIGNAL_SEEN →
MODEL_RESPONSE_NODE_IDENTIFIED → MODEL_RESPONSE_STARTED → STREAMING →
STABLE → COMPLETE → RESPONSE_EXTRACTED` then parse/validate of the probe id.

Failures clustered as:

- `RESPONSE_EXTRACTION_FAILURE`: generation seen, associated model node never
  identified, recovery rescan still empty (Gemini often still had 1–5 mounted
  `model-response` nodes — pairing/watch did not attach the current prompt to
  a node that `pickText` could read).
- `RESPONSE_START_TIMEOUT`: ACK found the user turn, then the user node left
  the virtualizer window (`current_user_id` became null) and no generation
  flag was observed for 90s.
- `SEND_ACK_TIMEOUT`: prompt never appeared as a user turn within 12s
  (especially after `_mo_chat_moi`).

### 9. 20-batch soak

**Not run.** Gate is 10-run ≥8/10.

Validated semantic cache for the BV1r job was not rewritten. Missing batches
would still resume from `.translate_cache.json.semantic.json` on a later
accepted detector.

### 10. Remaining risks

- Virtualizer can unmount the current user turn after ACK; prompt_hash then
  has nothing to attach to until a remount that often never happens.
- Equal-count `users[i]↔models[i]` is wrong if Gemini mounts unequal user/model
  windows. Sibling walk is wrong if the response is not a sibling of
  `user-query`. Live failures still look like this (counts match, node not
  identified).
- `MutationObserver` on the scroller did not beat snapshot polling on the same
  pairing rules.
- Fresh-chat rotation does not fix first-turn START/ACK timeouts; N cannot be
  chosen from this benchmark (C was worse, not better).
- Gemini may accept a send (user bubble) and then not generate (rate limit /
  UI). The detector currently cannot distinguish that from a mapping miss
  without raising timeouts.
- JSON classifier (`VALID_JSON_CANDIDATE` / `PARSED` / `VALIDATED`) is still
  the right gate when text is extracted. Do not weaken it.

## What landed in production anyway

These remain in `autodub/translate/browser.py` because they are strictly
narrower than count/`[-1]` identity and the unit suite (66 tests) is green:

- Current-turn identity via prompt hash
- Associated model, not `responses[-1]`
- Split generation / node / text flags
- Multi-signal ACK + `SEND_ACK_RECOVERED` (no resend if prompt exists)
- Duplicate guard
- One recovery rescan before `RESPONSE_EXTRACTION_FAILURE`
- Per-turn MutationObserver merge
- Existing 12s ACK, 90s start cap, 12s empty-after-generation, 2.5s stability,
  send_streak circuit breaker, JSON validation — unchanged budgets

They are **not sufficient** for ACCEPTED.

## Next direction (required after REJECTED)

Do not keep patching CSS selectors.

The live miss is: **user turn identified, then the paired model text never
becomes visible to `pickText` even when `model-response` count matches**.
Next work has to capture the replacement of the current turn’s host (Playwright
locator on that turn’s wrapper, or CDP binding) or leave Gemini Web as a
best-effort path. Rotation N is not justified by this benchmark.
