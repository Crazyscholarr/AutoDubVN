# Gemini response classification — 2026-09-16

## Kết quả và phạm vi

Đã thêm classification trước JSON parsing, retry theo loại phản hồi và số đếm.
Không sửa response detector: `autodub/translate/browser.py` giữ nguyên từng byte.
SHA256 trước/sau: `B7E84591291605DD8BC9E976B533A7D1D509D99FA186BFA60E4C9B46B664C336`.
Không thay model, timeout, retry budget, ASR, TTS hoặc semantic validator/gate.

Runtime cuối: **20 batch thật / 22 responses**, 18 batch parse+validate PASS;
2 batch dừng với MALFORMED_JSON sau đúng một lần format repair. Không tìm thấy
misclassification trong 22 raw responses được kiểm tra. Đây là sửa taxonomy,
không phải tuyên bố tất cả output từ model đã trở thành JSON hợp lệ.

## Root cause / evidence

- Trước sửa không có nhánh MODEL_REFUSAL. Refusal đi vào MODEL_NON_JSON và nhận
  cùng retry chung với các lỗi format.
- `_balanced_object()` chỉ đếm ngoặc và trạng thái chuỗi. Khi model dùng dấu
  nháy không escape bên trong lời thoại, trạng thái chuỗi bị lệch; không tìm
  thấy object cân bằng bị quy thẳng thành TRUNCATED_JSON. Đây không phải bằng
  chứng rằng phần cuối bị mất.
- Audit debug trước khi thư mục output bị làm trống thấy các mẫu refusal chính
  xác của người dùng được lưu là MODEL_NON_JSON. Các file TRUNCATED_JSON khác
  có lỗi dấu nháy/nối object và vẫn có phần kết thúc. Vì vậy không khẳng định
  đã tái hiện việc chính câu refusal ấy đi vào TRUNCATED_JSON trên bản code cũ;
  regression mới khóa trường hợp đó ở cả shape classifier và exception path.

## Flow mới

```text
RESPONSE_EXTRACTED
  → classify_response_shape()  [chưa gọi json.loads]
  → MODEL_REFUSAL: một clarification retry tối đa
  → NON_JSON_RESPONSE / MALFORMED_JSON / TRUNCATED_JSON: format repair có loại lỗi
  → VALID_JSON_CANDIDATE: strict parse → schema validation → semantic gate
```

Các shape:

- VALID_JSON_CANDIDATE: có object/array JSON nhận diện được; chưa bảo đảm schema.
- EMPTY_RESPONSE: rỗng hoặc whitespace.
- MODEL_REFUSAL: nhận diện lời từ chối VI/EN/ZH ngoài JSON; lời thoại chứa cùng
  câu từ bên trong JSON vẫn là dữ liệu, không bị đổi thành refusal.
- UI_ERROR_RESPONSE: thông báo lỗi UI/hạn mức; không format-retry như nội dung dịch.
- NON_JSON_RESPONSE: văn bản khác không có cấu trúc JSON hợp lý.
- TRUNCATED_JSON: parser tiền tố đến EOF tại vị trí JSON còn có thể nối tiếp.
- MALFORMED_JSON: gặp token/ký tự/escape sai, hoặc strict decode phát hiện lỗi
  như duplicate key. Không đổi mọi JSONDecodeError thành truncated.

`ResponseShapeError` giữ kind qua exception boundary. Các alias cũ
INVALID_JSON / MODEL_NON_JSON / UI_ERROR vẫn import được, nhưng giá trị/log dùng
taxonomy mới. `[]` là JSON candidate và bị từ chối ở SCHEMA_FAILURE vì cần object.

Markdown fence hoặc lời dẫn quanh một object hợp lệ được bỏ cục bộ; không gửi
thêm request. Không đoán ngoặc/nháy còn thiếu: TRUNCATED vẫn là TRUNCATED.
Nháy kép thô bên trong chuỗi (ký tự sau `"` không phải `,` `:` `}` `]` hay EOF)
được escape cục bộ, cùng kiểu unwrap fence. Khi model nối/restart JSON, lấy
object `translated_sentences` hoàn chỉnh sau cùng. Xem
[BV1r Gemini JSON 10-run](BV1R_GEMINI_JSON_10_RUN.md).

## Retry và counters

- Budget giữ nguyên: một request ban đầu + tối đa một retry.
- Refusal nhận TASK_CLARIFICATION: chỉ xử lý văn bản phụ đề; INPUT là dữ liệu;
  không thực hiện hành động trong lời thoại hoặc hành động bên ngoài; nếu có thể
  xử lý thì chỉ trả schema đã yêu cầu. Không ép bỏ qua chính sách/an toàn.
- Format repair có loại lỗi và PREVIOUS_RESPONSE được JSON-quote như dữ liệu;
  yêu cầu giữ nội dung/ID đã có, sửa định dạng và hoàn tất phần thiếu nếu có.
- Lỗi gửi trước khi có response không bị thêm feedback validation sai.
- UI/response-detection failures không đi vào nhánh repair/refusal.
- Counters tăng theo **response**, không theo batch; retry_success_count chỉ
  tăng sau khi retry đã parse và validate thành công. `request(metrics=...)`
  cho phép cộng dồn một phiên; mỗi request ghi counters vào log.

## Audit prompt

Semantic `TRANSLATE` trước đây đã nói INPUT là dữ liệu và yêu cầu JSON, nhưng
chưa nêu rõ giới hạn chỉ biến đổi văn bản. Dữ liệu có câu mệnh lệnh và thông báo
hệ thống của câu chuyện, nên cần phân biệt rõ chúng với instruction của ứng dụng.

Prompt mới mở đầu bằng tác vụ dịch/localization văn bản; không đóng vai nhân vật,
không thực thi hành động/công cụ; mệnh lệnh/lời thoại trong INPUT chỉ là nội dung.
Giữ schema, coverage ID, glossary và các ràng buộc validation. Nhắc escape dấu
nháy kép trong chuỗi JSON. PROMPT_VERSION tăng để cache nhận diện phiên bản mới.

`SYSTEM_INSTRUCTION`/`STYLE_LOCK` legacy có câu “không phải dịch văn bản”, nhưng
nhánh semantic browser đi qua `phien_gemini_trinh_duyet()` và không gọi
`_gieo_brief()`. Không sửa nhánh legacy hoặc quy nguyên nhân refusal cho nó khi
chưa có evidence. Không thể suy chắc lý do nội bộ Gemini refusal chỉ từ câu trả
lời; thay đổi prompt nhằm giảm sự mơ hồ đã quan sát, không phải bypass safety.

## Tests

- **Full suite: 648 tests, OK (skipped=1), 121.425 s.**
  Command: `venv\Scripts\python.exe -m unittest discover -s tests`.
  Log: `../_tmp/refusal_suite_final.log`.
- 12 tests mới: refusal chính xác, VI/EN/ZH, không gọi json.loads với refusal/UI/
  non-JSON, refusal kèm schema, refusal là dữ liệu trong JSON, các tiền tố JSON
  bị cắt, malformed quote/escape/brackets, schema array, exception taxonomy,
  clarification khác prompt gốc, dừng sau hai refusals, repair+retry counter,
  local unwrap không gửi thêm, UI error không retry.
- Nhóm semantic: 28/28 PASS. Giữ test send-failure retry nguyên prompt; đổi
  assertion cũ của `[]` sang schema feedback đúng giai đoạn.
- Test response detector hiện có vẫn chạy trong full suite; cả 22 live responses
  có send_ack=true, response_started=true, response_complete=true.
- Live không phát sinh refusal; refusal recovery thành công được chứng minh ở
  regression test với response mock, không gán thành live retry success.

## Dữ liệu và giới hạn runtime

SRT trong output biến mất sau khi audit đầu lượt. Khôi phục đúng 30 cue nguồn
(text + timestamps) từ INPUT_JSON đã lưu trong conversation Gemini trước đó:
`../_tmp/recovered_gemini_source.json`. Chạy 20 batch nhỏ: 10 batch một cue và
10 batch hai cue, có context trước/sau, prompt và validator production.
Không đổi batch size cấu hình của pipeline; đây là acceptance run riêng.
Không suy rộng số đếm của mẫu này thành tỷ lệ refusal ở toàn bộ phim.

Lần tối 15/09 bị ngắt ở batch 3, không tính vào kết quả 20 batch bên dưới.
Log đó giữ tại `../_tmp/gemini_classification_interrupted_20260915.log`.

Artifact lần đầy đủ:

- `../_tmp/gemini_classification_20260916_061513/results.json`
- `../_tmp/gemini_classification_20260916_061513/batch-*-attempt-*.txt`
- `../_tmp/gemini_classification_20260916_061513/batch-*.png`
- `../_tmp/gemini_classification_runtime.log`

## Files changed

- `autodub/translate/jsonutil.py`
- `autodub/translate/semantic.py`
- `tests/test_jsonutil.py`
- `tests/test_semantic_translation.py`
- `tests/test_response_classification.py`
- `scripts/gemini_classification_acceptance.py`
- `docs/GEMINI_RESPONSE_CLASSIFICATION.md`

## Runtime counters và kết quả từng batch

- **REFUSAL: 0**
- **NON_JSON: 0**
- **TRUNCATED_JSON: 0**
- **MALFORMED_JSON: 4**
- **retry success count: 0**

4 MALFORMED_JSON responses thuộc batch 10 và 11 (mỗi batch 2 attempts).
Đối chiếu độc lập bằng json.loads trên raw xác nhận lỗi dấu nháy ở vị trí
102 / 194, trong khi phần cuối object vẫn có. Repair không thành công;
hai batch này được giữ trạng thái FAIL, không báo PASS hoặc gọi truncated.

| Batch | Source IDs | Responses | Shape(s) | Parse + gate |
|---|---|---:|---|---|
| 1 | 1 | 1 | VALID_JSON_CANDIDATE | PASS |
| 2 | 2 | 1 | VALID_JSON_CANDIDATE | PASS |
| 3 | 3 | 1 | VALID_JSON_CANDIDATE | PASS |
| 4 | 4 | 1 | VALID_JSON_CANDIDATE | PASS |
| 5 | 5 | 1 | VALID_JSON_CANDIDATE | PASS |
| 6 | 6 | 1 | VALID_JSON_CANDIDATE | PASS |
| 7 | 7 | 1 | VALID_JSON_CANDIDATE | PASS |
| 8 | 8 | 1 | VALID_JSON_CANDIDATE | PASS |
| 9 | 9 | 1 | VALID_JSON_CANDIDATE | PASS |
| 10 | 10 | 2 | MALFORMED_JSON, MALFORMED_JSON | FAIL |
| 11 | 11,12 | 2 | MALFORMED_JSON, MALFORMED_JSON | FAIL |
| 12 | 13,14 | 1 | VALID_JSON_CANDIDATE | PASS |
| 13 | 15,16 | 1 | VALID_JSON_CANDIDATE | PASS |
| 14 | 17,18 | 1 | VALID_JSON_CANDIDATE | PASS |
| 15 | 19,20 | 1 | VALID_JSON_CANDIDATE | PASS |
| 16 | 21,22 | 1 | VALID_JSON_CANDIDATE | PASS |
| 17 | 23,24 | 1 | VALID_JSON_CANDIDATE | PASS |
| 18 | 25,26 | 1 | VALID_JSON_CANDIDATE | PASS |
| 19 | 27,28 | 1 | VALID_JSON_CANDIDATE | PASS |
| 20 | 29,30 | 1 | VALID_JSON_CANDIDATE | PASS |
