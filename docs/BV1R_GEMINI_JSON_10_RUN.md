# BV1r Gemini JSON — 10-run dump replay

Date: 2026-09-17. Film: `BV1rF4Q6EEUT`. ASR already ACCEPTED (99.98% speech coverage). This gate is translation JSON salvage only. Not a live 113-lô Gemini run.

## Verdict

**ACCEPTED 10/10.** Replay of 10 dump fixtures, 10 identical trials, all PASS.

Live evidence (same job `translation-debug`, 90 `.txt` dumps): 74/74 former `MALFORMED_JSON` extract as `translated_sentences`; 16 `MODEL_REFUSAL`; 0 leftover malformed/truncated.

## Why the GUI died

`TranslationIncomplete: thiếu 30/113 lô` after ~290/1377 cues. Shape mix:

- `MALFORMED_JSON`: Gemini wrote dialogue quotes as raw `"` inside `text_vi` (`""Ông chủ...""`) and sometimes spliced a second `{"translated_sentences"` onto an unfinished object. Format repair asked the model to rewrite JSON; it usually repeated the same quotes. Split-batch then chat-rot followed.
- Short refusals (`không được lập trình/thiết kế`, `nằm ngoài khả năng … lập trình`) were `NON_JSON_RESPONSE`, so they burned the format-repair slot.
- `RESPONSE_EXTRACTION_FAILURE`: `model-response` host existed, inner markdown text empty, `generating=False`, wait burned the full 240s. Then `SEND_ACK_TIMEOUT` and send_streak stopped the rest.

## What changed

Local unwrap, not invented translation:

1. Escape interior `"` / raw newlines in JSON strings. A `"` closes only when the next non-space is `,` `:` `}` `]` or EOF.
2. If the first `{` still fails, parse the last complete `{"translated_sentences": ...}` candidate.
3. Do **not** invent missing braces. Truncated prefixes stay `TRUNCATED_JSON`. `"b": }` stays `MALFORMED_JSON`.
4. Refusal regex covers lập trình / thiết kế / “nằm ngoài khả năng … lập trình” / “chỉ có thể tạo văn bản”.
5. Snapshot `pickText` may use host `textContent` only if it contains `{` (Copy labels stay empty).
6. Empty idle host after 12s → `RESPONSE_EXTRACTION_FAILURE` instead of 240s.

## 10 fixtures

| # | File | Expect |
|---|---|---|
| 1 | `1789629544-malformed_json.txt` | salvage `translated_sentences` |
| 2 | `1789629672-malformed_json.txt` | last object after restart |
| 3 | `1789629777-malformed_json.txt` | nested restart |
| 4 | `1789629852-malformed_json.txt` | quotes + restart |
| 5 | `1789629888-malformed_json.txt` | quotes + newline |
| 6 | `1789630780-malformed_json.txt` | splice mid-key |
| 7 | `1789629580-non_json_response.txt` | `MODEL_REFUSAL` |
| 8 | `1789629740-model_refusal.txt` | `MODEL_REFUSAL` |
| 9 | `1789629869-non_json_response.txt` | `MODEL_REFUSAL` |
| 10 | inline valid JSON | extract as-is |

Command: `venv\Scripts\python.exe -m unittest tests.test_bv1r_gemini_json tests.test_jsonutil tests.test_response_classification tests.test_gemini_response_detection`

## Limits

- Quote-then-comma inside a value (`"được",`) can still look like a string close. Not seen in this 74-dump set.
- Salvage does not skip Gemini refusals. Those still retry once with TASK_CLARIFICATION.
- Empty-DOM fail-fast needs a later live send to confirm SEND_ACK_TIMEOUT storms shrink. Dump replay cannot exercise the browser loop.
