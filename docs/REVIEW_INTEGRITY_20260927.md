# Sửa lỗi review và phụ đề đầu ra — 27/09/2026

Các lỗi dưới đây đã tái hiện trước khi sửa bằng test trên thư mục tạm.
Không thay đổi media, phụ đề hoặc xác nhận trong project thật của người dùng.

1. **Thu hồi xác nhận không có tác dụng.** Hai vùng liền nhau từng bị gộp khi
   lưu. Quyết định UNREVIEWED cho một vùng không xóa hiệu lực xác nhận rộng đã
   gộp. Nay giữ nguyên ranh giới quyết định khi lưu; chỉ hợp nhất khoảng trong
   chỉ mục truy vấn. UNREVIEWED chồng vào issue phải chặn, kể cả dữ liệu cũ đã gộp.
2. **Lời thoại bị tạp âm che mất.** CONFIRMED_SPEECH chỉ được ưu tiên khi phủ 90%
   issue. Vì vậy một giây thoại thật trong vùng mười giây đã đánh dấu noise bị
   bỏ qua. Nay chỉ cần giao nhau thực sự với quyết định có thoại/thu hồi là chặn;
   không cộng dung sai 50ms cho các quyết định chặn để tránh ảnh hưởng vùng kế bên.
   Noise/effect vẫn phải đáp ứng ngưỡng phủ cũ.
3. **Clock đầu ra hỏng vẫn qua gate.** Gate chỉ kiểm tra clock nguồn. Nay kiểm
   tra cả cue đã đóng gói: NaN, Infinity, mốc âm, end <= start đều chặn. Raw clock
   giữ trong JSON chẩn đoán; không đưa cue lỗi vào file SRT artifact.
4. **Snapshot hỏng có thể khôi phục sai trạng thái.** Snapshot hiện hữu nhưng
   hỏng schema/JSON không còn được coi là project legacy rồi lấy báo cáo cũ rỗng.
   Trả blocker `review_state_corrupt`; giữ nguyên file hỏng để điều tra. Cơ chế
   đọc backup hợp lệ hiện có vẫn hoạt động.
5. **Tiếp tục làm sống lại chữ đã loại bỏ.** `packed.needs-review.srt` tồn tại
   nhưng rỗng từng khiến backend lấy `source.srt` và dịch lại hallucination.
   Nay fallback nguồn chỉ dành cho artifact legacy không có file packed.
   Packed rỗng trả lỗi rõ ràng, không khởi động dịch/TTS và không ghi SRT đích.
6. **Tiếp tục review đoạn cắt làm mất phụ đề và bản dịch dự án.** Backend từng
   nạp SRT clip-local vào toàn bộ project: cue ở giây 11 thành giây 1 khi cắt từ
   giây 10, các cue ngoài đoạn cắt bị xóa và tiếng Việt bị đặt rỗng. Nay đồng bộ
   theo span, đổi về clock video gốc và giữ cue ngoài span. Chỉ giữ bản dịch,
   speaker và metadata khi nguồn lẫn clock khớp duy nhất; nguồn đã đổi phải dịch
   lại. Regression HTTP đã thất bại trước sửa và đạt sau sửa.

## Kiểm chứng

- `tests/test_review_integrity_20260927.py`: 4 test, 7 assertion thất bại trước
  sửa (clock kiểm tra nhiều trường hợp); tất cả đạt sau sửa.
- `tests/test_http_runtime.py`: regression packed rỗng từng trả HTTP 200 và
  khởi động pipeline; sau sửa trả 400, không submit job, không tạo source đích.
  Toàn bộ 29 test HTTP đạt trong lượt kiểm thử đầy đủ cuối, gồm hai test mới
  cho giữ clock/bản dịch đoạn cắt và không tái sử dụng bản dịch khi nguồn đổi.
- Nhóm `test_review*.py`: 35 test đạt, gồm migration, source identity, ghi
  đồng thời, backup, reload, idempotency và các case mới.
- UI: 7 test đạt; log `_tmp/integrity_ui_20260927.log`.
- Bộ đầy đủ cuối: **980 test, 976 đạt, 4 bỏ qua, 0 lỗi** trong 165,005 giây;
  `_tmp/all_functions_verified_20260927.log`. Kiểm tra cú pháp Python và
  `git diff --check` đạt.

Các sửa tăng tốc tra mốc/chia dòng ngắn của lượt trước vẫn được giữ và được chạy
chung trong bộ test. Không coi test được bỏ qua là đạt. Chưa có media/SRT cụ thể
cho lỗi hiện tại của người dùng để xác minh toàn pipeline ASR/dịch/TTS online.
