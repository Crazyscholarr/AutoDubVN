# Gemini response detection P0 — 2026-09-15

## Root cause đã kiểm chứng

Chạy trực tiếp trên `E:\Video\AutoDubVN`, Edge + `browser_profile` của ứng dụng.
Không thay model, cấu hình `wait_reply: 240`, ASR, TTS hay logic quality gate.

Đường lỗi tái hiện với `Return exactly:\n{"ok":true}` hai lần liên tiếp:

1. Trước sửa, request 1 nhận `{"ok":true}`; user_count=1, model_count=1.
2. Request 2: `_ask_once()` dùng `_user_has_prompt(page, msg) or _is_generating(page)`
   làm `already`. Prompt cũ trùng nội dung nên request mới bị bỏ qua gửi.
3. `_wait_reply()` dùng `last_before` để loại text bằng response cũ, dù DOM vẫn có
   JSON. Kết quả rỗng sau deadline; user_count và model_count vẫn bằng 1.
4. Exception `hết thời gian chờ mà chưa thấy trả lời` bị catch bởi vòng retry gửi;
   cuối vòng gói thành `không gửi được sau 4 lần`. `classify_exception()` ưu tiên
   token này nên đổi lỗi response thành `SEND_FAILURE`.

Repro dừng ngay sau kết quả rỗng, trước khi vòng retry cũ chạy tiếp. Đây là một
đường false-timeout đã tái hiện trực tiếp; không suy diễn rằng mọi lần lỗi trong
pipeline trước đó đều có cùng nguyên nhân khi chưa có snapshot của lần đó.

Ngoài ra, bộ snapshot đang sửa dở trong workspace chưa được nối vào `_ask_once()`:
baseline vẫn là max số lượng của nhiều selector, không phải tập node model thật;
completion vẫn có nhánh nhận JSON ngay khi parse được dù generation còn active.

## Old selector và DOM hiện tại

Selector trong HEAD `4e04b21:autodub/translate.py`:

- `model-response`
- `message-content.model-response-text`
- `.model-response-text`
- `div.markdown`

DOM live đã quan sát:

```text
infinite-scroller[data-test-id="chat-history-container"]
  user-query
  model-response
    response-container
      model-response-content
        structured-content-container.model-response-text
          message-content#message-content-id-r_<turn-id>
            div#model-response-message-contentr_<turn-id>.markdown
      message-actions / copy-button / thumb-up-button
```

Khi sinh: có `pending-response` trước `model-response`; nút
`aria-label="Ngừng tạo câu trả lời"` xuất hiện. Sau hoàn tất, pending biến mất,
Stop biến mất, nội dung ổn định và action controls hiện ra.

`model-response` **không chết**: live probe đếm được 1 node sau câu trả lời nhỏ.
`message-content.model-response-text` là giả định sai về hai tag/class cùng node:
class `model-response-text` nằm trên `structured-content-container`, không nằm
trên `message-content`. Vì các selector khác vẫn tìm được response, không thể
quy toàn bộ lỗi cho selector này.

Kiểm tra lại trực tiếp bằng `document.querySelectorAll()` trong conversation
10 batch lúc 22:52: `model-response=10`, `message-content=10`,
`.model-response-text=10`, `response-container=10`,
`message-content.model-response-text=0`. Response cuối có ID
`message-content-id-r_1cb358f8f64058a0`, innerText/textContent cùng 452 ký tự,
role/aria-label=null, shadowRoot=false. Dump giới hạn HTML và 100 ký tự preview
được lưu tại `_tmp/gemini_current_selectors.json`.

Live probe ghi nhận trong lúc sinh: `model-response.innerText` mới có 13 ký tự
(“Gemini đã nói”), trong khi `textContent` đã có 53 ký tự gồm JSON. Sau hoàn tất,
`message-content.innerText` và `textContent` đều 40 ký tự ở probe có trường `probe`.
Các node response đã kiểm tra không có shadowRoot; không thêm shadow crawler.
Không dùng visibility/viewport làm gate phát hiện response.

## Cách sửa

- `_CONV_SNAPSHOT_JS`: scope vào root hội thoại; không fallback toàn body;
  đọc nội dung trong message content, tách action labels; giữ số của HTML `<ol>`.
- `_new_models()` / `has_new_response()`: so ID/fingerprint node và thứ tự mới;
  nhận được pending/empty node; không phụ thuộc JSON parse hay text phải khác A.
- `extract_response_text()`: chỉ đọc node mới; hai response có cùng JSON vẫn hợp lệ.
- `_ask_once()`: snapshot trước gửi; user_count tăng mới là SEND_ACK. Không dùng
  prompt cũ hay composer rỗng để ACK request mới.
- `_wait_reply()`: poll snapshot mới mỗi 250 ms; phát hiện started ngay; chỉ trả
  final khi generation tắt và text ổn định 2.5 s. JSON cắt khi đã hoàn tất được
  giao cho parser phân loại, không đổi thành “chưa có response”.
- Deadline ACK riêng 12 s (ngân sách ACK trước đó); response-start và generation
  riêng, cùng bị chặn bởi ngân sách response tổng hiện có. Không tăng timeout.
- `_INFLIGHT`: giữ baseline gốc khi chờ lỗi; lần gọi lại cùng request chỉ rescan
  lượt đó. Không gửi trùng, không mở chat mới để che lỗi. Request khác khi lượt
  trước chưa xử lý xong bị chặn rõ ràng.
- `GeminiResponseError`: giữ kind, state, send_ack và diagnostic; taxonomy ưu tiên
  lỗi có kiểu trước các token văn bản cũ. Semantic caller dừng retry khi lỗi
  response/ACK, không đẩy xuống nhánh SEND_FAILURE.
- Log PARSED và VALIDATED ở caller thực sự parse/validate; không giả vờ detector
  đã validate semantic. Validator và quality gate không bị sửa.

State sequence:

```text
COMPOSER_READY → PROMPT_INSERTED → SEND_TRIGGERED → SEND_ACKNOWLEDGED
→ WAITING_MODEL_RESPONSE → MODEL_RESPONSE_STARTED → MODEL_RESPONSE_STREAMING
→ MODEL_RESPONSE_COMPLETE → RESPONSE_EXTRACTED → PARSED → VALIDATED
```

Các lỗi riêng: `SEND_ACK_TIMEOUT`, `RESPONSE_START_TIMEOUT`,
`RESPONSE_COMPLETION_TIMEOUT`, `RESPONSE_EXTRACTION_FAILURE`,
`RESPONSE_DETECTION_FAILURE`. Parser giữ taxonomy JSON hiện có.

## Files changed trong vòng P0 này

- `autodub/translate/browser.py`
- `autodub/translate/jsonutil.py`
- `autodub/translate/semantic.py` — logging và propagation lỗi; không đổi gate.
- `tests/test_translate_budget.py` — mock snapshot/clock và sửa assertion cũ
  cho phép nhận kết quả trước khi generation kết thúc.
- `tests/test_gemini_response_detection.py`
- `scripts/gemini_response_repro.py`
- `scripts/gemini_response_acceptance.py`
- `docs/GEMINI_RESPONSE_P0.md`
- `docs/TRANSLATION_BROWSER_REGRESSION.md` — đánh dấu phần browser cũ là lịch sử.

Repo đã có nhiều thay đổi và package `autodub/translate/` chưa được track trước
vòng này. Git history xác nhận bản committed là module đơn; không có commit
first-bad cho package đang chạy. Không reset/revert các thay đổi sẵn có.

## Tests

- Hai request JSON giống nhau sau sửa: cả hai PASS; count 0 → 1 → 2.
- 12 test P0: real DOM bằng Playwright Edge headless + fake clock cho state flow.
  Bao phủ sidebar/action noise, node rỗng, pending → model, rerender cùng ID,
  response B giống A nhưng khác ID, offscreen/innerText rỗng, root mất, streaming
  100 → 200 → 400 → 450, response-start/completion timeout, mock detector lỗi sau
  ACK, rescan không submit và semantic retry không resend.
- 29 tests trong `test_translate_budget.py`: PASS.
- Full suite: `venv\Scripts\python.exe -m unittest discover -s tests`:
  **636 tests, OK (skipped=1), 118.343 s**. Log: `_tmp/gemini_suite_final.log`.
- Một skip hiện có do thiếu dữ liệu ASR/bản đồ thoại của test phim mẫu;
  các test E2E FFmpeg vẫn chạy.

## Bằng chứng

- `_tmp/gemini_dom_complete.json` / `.png`: DOM live, innerText/textContent,
  custom elements, action nodes và shadowRoot.
- `_tmp/gemini_response_repro/result.json`, `request-1.png`, `request-2.png`:
  hai request liên tiếp sau sửa.
- `_tmp/gemini_acceptance/results.json`: state, ACK/start/generation timings,
  chars, parse, gate, result; mỗi batch có raw text và screenshot riêng.
- `_tmp/gemini_acceptance.log`: log state machine của lần acceptance cuối.

Computer Use thử đối chiếu cửa sổ live nhưng tự dừng vì không xác minh được URL
Windows. Không tiếp tục thao tác bằng Computer Use. Bằng chứng DOM và ảnh ở trên
được lấy bởi chính Playwright của ứng dụng.

## 10-batch result

Chạy batch dịch thật từ 30 cue đầu SRT hiện có, 3 cue/batch, cùng browser/session,
prompt `TRANSLATE` và `translated_validator()` production. Không chạy ASR/TTS hay
pipeline 1083 cue. Acceptance đầu tiên đạt 10/10; kiểm tra lại bản deadline cuối
được ghi bên dưới. `generation_ms` đo từ bắt đầu wait sau ACK tới extract final,
bao gồm chờ text ổn định 2.5 s; không phải thời gian inference riêng của model.

| batch | send_ack_ms | response_start_ms | generation_ms* | chars | parse | gate | result |
|---|---:|---:|---:|---:|---|---|---|
| 1 | 265 | 265 | 8078 | 329 | PASS | PASS | PASS |
| 2 | 78 | 78 | 104156 | 451 | PASS | PASS | PASS |
| 3 | 93 | 93 | 17641 | 664 | PASS | PASS | PASS |
| 4 | 93 | 109 | 18657 | 574 | PASS | PASS | PASS |
| 5 | 109 | 125 | 9219 | 574 | PASS | PASS | PASS |
| 6 | 281 | 297 | 81016 | 557 | PASS | PASS | PASS |
| 7 | 140 | 156 | 27625 | 464 | PASS | PASS | PASS |
| 8 | 296 | 296 | 38500 | 518 | PASS | PASS | PASS |
| 9 | 297 | 297 | 9265 | 547 | PASS | PASS | PASS |
| 10 | 125 | 157 | 12532 | 452 | PASS | PASS | PASS |

**Kết quả bản mã cuối: 10/10 PASS, 0 false response timeout, 0 duplicate send.**
Mỗi batch chỉ gọi `_ask_once` một lần; log user count tăng 0 → 10.
*Định nghĩa generation_ms như phần trên.
