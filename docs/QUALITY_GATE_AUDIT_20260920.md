# Quality Gate / persisted ASR review — 20/09/2026

## Nguyên nhân và phạm vi

Lỗi ban đầu: xác nhận tạp âm được lưu nhưng `missing_speech_marks` không nằm
trong các lý do có thể giải quyết bằng xác nhận không phải thoại. Giao diện dùng
phép khớp khoảng thời gian riêng, trong khi backend còn dùng cờ `REVIEW_REQUIRED`
và các nhánh lọc riêng. Vì vậy UI và bước dịch/TTS có thể kết luận khác nhau.

Audit xác định các điểm cần thống nhất:

| Điểm gọi | Trước | Sau |
| --- | --- | --- |
| `asr/screen_pack.ensure_complete` | Tự lọc withheld | `effective_review` |
| GET `/api/caption_review` | Đọc/compact và đếm riêng | `review_for_job` |
| `ack_caption_review` | Lưu và kiểm tra riêng | Persist → readback → cùng effective state |
| `server/pipeline.run_pipeline` | Cờ job cũ có thể chặn | Đánh giá lại quyết định trên đĩa |
| `projects.get_project` | Không phục hồi review | Nạp snapshot và dựng lại trạng thái |
| CLI dùng lại SRT | Khác thư mục quyết định | Cùng `_tmp` với artifacts ASR |

## Mô hình và thứ tự ưu tiên

ASR → raw diagnostics → quyết định người dùng đã lưu → effective items → gate.
`autodub/asr/review.py` là nơi duy nhất suy ra blocker. Giữ `raw_withheld` và
`requires_review`; không dùng nhãn lỗi thô làm quyết định cuối cùng.

Các trạng thái: UNREVIEWED, CONFIRMED_SPEECH, CONFIRMED_NOISE, CONFIRMED_EFFECT,
IGNORED, RESOLVED. Hai trạng thái đầu vẫn chặn khi issue cần review. Bốn trạng
thái sau giải quyết các loại speech gap/no-clock được policy cho phép.
Lỗi cấu trúc timeline không được bỏ qua bằng xác nhận tạp âm.

Khi các khoảng quyết định chồng nhau, CONFIRMED_SPEECH ưu tiên bảo thủ; sau đó
RESOLVED, IGNORED, CONFIRMED_EFFECT, CONFIRMED_NOISE. Cùng đúng một khoảng thì
quyết định mới thay quyết định cũ. Diagnostic chạy lại không sửa quyết định.
Resolution không nhận biết được không cấp quyền tiếp tục.

ID issue dựa vào source ID + start/end làm tròn millisecond + reason, không dùng
index/object identity. Khoảng xác nhận phủ ít nhất 90%, dung sai 50ms, theo
policy tương thích cũ. Khoảng dịch chuyển đáng kể không được áp dụng xác nhận cũ.

## Lưu, mở lại và tiếp tục

`non_speech.json` v2 đọc được schema cũ và giữ các quyết định theo từng nguồn.
Giao dịch trong một backend process được khóa từ đọc tới merge, ghi atomic,
đọc lại kiểm chứng. Có bản `.bak`; lỗi ghi trả lỗi HTTP, không báo thành công.
Schema lỗi có log; phần hợp lệ vẫn đọc được; JSON hỏng không bị ghi đè im lặng.

`review_source.json` dùng đường dẫn chuẩn hóa, kích thước, mtime_ns và trim để
nhận dạng nguồn với chi phí O(1). Đổi nguồn vô hiệu hóa hiệu lực quyết định cũ
nhưng giữ dữ liệu cũ; không dùng lại SRT/audio/checkpoint của nguồn trước.
Lần đầu gắn nguồn vẫn tương thích quyết định/checkpoint cũ.

`review_latest.json` là snapshot raw diagnostics và đường dẫn artifacts hợp lệ.
Snapshot rỗng cũng có giá trị, không được thay bằng cờ lỗi cũ. Với dự án cũ,
chỉ tìm bản kiểm tra hợp lệ một lần; bỏ qua thư mục trống do lần chạy gián đoạn.
Prepared marker ngăn thao tác tiếp tục chép lại nguồn hoặc xóa bản dịch.
Tiếp tục chỉ điều phối translate/tts/render. Muốn ASR phải yêu cầu bước ASR.
CLI giữ thư mục chứa quyết định/review để lần mở lại không mất dữ liệu.

UI lấy effective state từ backend. Generation token loại kết quả GET trả muộn
sau khi xác nhận, kể cả khi đường dẫn review không đổi. Lỗi ghi không tô xanh.

## Kiểm thử và self-review

- `tests/test_review_state.py`: 18 test, gồm ma trận missing/empty + noise/effect,
  restart, recompute, deduplicate, idempotency, source change, 10 resolved/1 blocked,
  malformed data, backup, lỗi atomic write, 20 ghi đồng thời và batch 200,
  10.000 diagnostic, snapshot rỗng, thư mục review trống, checkpoint khác nguồn.
- `tests/test_http_runtime.py`: 26 test. Kịch bản 7 đoạn empty/no timestamps:
  lưu noise/effect qua HTTP, xóa queue/project trong RAM, nạp lại từ đĩa,
  0 blocker, điều phối dịch, lặp preparation và TTS vẫn giữ bản dịch.
  Spy kiểm tra không yêu cầu ASR; tác vụ dịch/TTS ngoài được mock trong test này.
  Lỗi `disk full` qua HTTP trả 500 và không tạo quyết định giả.
- `tests/test_caption*.py`: 36 test nhận dạng lại/giữ lời và word clocks đạt.
- Node: 6 test caption review và 1 entry test story UI đạt.
- Bộ Python đầy đủ còn bao gồm các bài FFmpeg end-to-end trên media tạm.
  Kết quả cuối: **965 test, 962 đạt, 3 bỏ qua, 0 lỗi** trong 137,407 giây;
  xem `_tmp/review_verified_suite.log`. Không tính các test bỏ qua là đã đạt.
- `git diff --check` không báo lỗi whitespace. Self-review đã kiểm tra các nhánh
  persistence, source invalidation, snapshot rỗng, retry, wrapper và cache UI.

Đối chiếu chỉ đọc dữ liệu phim thực: `review_real_confirmation_20260920.json`.
60 diagnostic gồm thông tin debug, chỉ 1 issue cần review; xác nhận cũ giải quyết
issue đó, **0 effective blocker**. Thư mục mới nhất `caption-review-wwstsyk8`
trống; bản hợp lệ gần nhất là `caption-review-z1dqdck0`. Không sửa/xóa confirmation
hay chạy lại ASR của phim để thực hiện kiểm chứng này.

## Giới hạn kiểm chứng

Fingerprint stat không phát hiện trường hợp cố tình thay nội dung mà giữ nguyên
đường dẫn, kích thước và mtime. Khóa giao dịch bảo vệ các thread trong một backend;
không hỗ trợ hai backend độc lập cùng ghi một project. Test restart dựng lại
queue/project trong RAM và đọc đĩa, chưa thay thế thao tác đóng/mở cửa sổ desktop.
Không chạy dịch/TTS online toàn bộ phim 4 giờ trong đợt audit này.
