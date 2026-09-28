# Sửa dịch phụ đề — 27/09/2026

Đã đối chiếu mã trong `E:\ứng dụng video\core\translate` (batch, engine,
prompt, validation và Gemini web), chỉ sửa dự án AutoDubVN. Tham khảo cách
dịch theo ID, cửa sổ 30 cue / 10 cue ngữ cảnh, ngân sách độ dài và chỉ sửa phần
chưa đạt. Giữ bộ kiểm tra ID, mốc thời gian, tên/số và cache của AutoDubVN.

## Lỗi xác nhận và thay đổi

- Lô cấu hình 10 cue thường chỉ chứa 6 cue vì ranh nhóm nguồn, khiến 30 cue
  thử nghiệm phải gọi API 5 lần. Mặc định mới 30 cue / tối đa 60 giây, giới hạn
  ký tự đầu vào vẫn áp dụng. Cập nhật ba tham số batch/context tương ứng trong
  config máy này; không đổi provider, model hay khóa API.
- Mô hình cũ trả cả đoạn Việt rồi chương trình chia theo thời lượng, có thể
  dồn ý và tạo dòng rất dài. Yêu cầu mới trả `id,text_vi` cho từng cue, đọc cả
  cửa sổ để hiểu mạch. Timestamp được gắn từ nguồn, mô hình không được đổi.
  Dạng câu ghép cũ vẫn được kiểm tra và hỗ trợ; `cue_texts` nếu có chỉ được dùng
  khi nối lại bảo toàn từ, tên và số.
- Ngữ cảnh trước có cả nguồn và bản Việt đã qua kiểm tra; ngữ cảnh sau chỉ có
  nguồn. Tên liên quan đến ngữ cảnh cũng được đưa vào glossary của yêu cầu.
- Sáu test mới tái hiện lỗi trước sửa: dịch lại câu đạt, mất phần đạt khi hủy,
  thiếu ngữ cảnh Việt, thiếu ngân sách độ dài, chèn sai tên, thiếu tham số nhóm
  trong khóa cache. Cả sáu đều thất bại với code cũ.
- Lưu từng câu đã qua kiểm tra trước khi thử lại. Yêu cầu sửa chỉ chứa các ID
  còn lỗi, phần đạt chuyển thành ngữ cảnh chỉ đọc. Trùng ID hoặc câu vượt nhóm
  không được đưa vào checkpoint; sau khi ghép vẫn kiểm tra toàn bộ kết quả.
- Bỏ tự chèn tên bị thiếu vào đầu câu. Ví dụ nguồn “我不是七月” không được sửa
  máy móc “Tôi không phải.” thành “Thất Nguyệt Tôi không phải.”; yêu cầu mô hình
  sửa lại đúng câu. Các test cũ từng chấp nhận chèn đầu câu đã đổi sang yêu cầu
  giữ vai trò ngữ pháp hoặc từ chối bản thiếu tên.
- Sửa retry cấp pipeline: bộ dịch có thể đã thay một phần nguồn thành tiếng
  Việt trước khi báo thiếu lô. Lượt sau phải dùng bản nguồn bất biến và resume
  cache, không đem tiếng Việt đã dịch làm nguồn mới.
- Rút gọn tối đa một lượt/lô, chỉ yêu cầu câu quá dài. Giữ các bản rút gọn đạt
  ngay cả khi một câu khác chưa ngắn hơn. Không cắt chuỗi để ép chiều dài;
  kiểm tra tên, số và dấu phủ định; nếu không đạt giữ bản đủ nghĩa và ghi nhận.
- Sửa cấu hình GUI: “Số dòng mỗi lượt” giờ ghi đúng `semantic_batch_cues` khi
  bật dịch semantic. Trước đây ô này chỉ chỉnh `chunk_size` không được dùng
  trong nhánh đó. `keep_source_timing` cũng từng bị danh sách cho phép bỏ qua.
  Thêm tùy chọn rút gọn và dùng lại bản dịch cũ; kiểm tra lưu/tải lại qua Edge.

## Thử dịch API thật và giới hạn kết luận

Dùng cùng 30 cue nguồn (101–130) từ ASR hiện có của `BV1RSYR6AE3F`, cùng
Xkiro `qwen/qwen3.5-flash:free`, temperature 0,2. Dữ liệu thử nằm riêng trong
`_tmp/translation_compare_20260927`, không ghi đè bản dịch sản xuất.

- Bản cũ: 5 lần gọi, 39,891 giây, 1.126 ký tự Việt; dòng dài nhất 192 ký tự;
  5 dòng vượt 40 ký tự/giây, đỉnh 87,4 ký tự/giây.
- Bản mới: 3 lần gọi (dịch, sửa ID còn chữ Hán, rút gọn), 36,328 giây,
  853 ký tự Việt; dòng dài nhất 58 ký tự; không dòng nào vượt 40 ký tự/giây,
  đỉnh 31,0 ký tự/giây. Giữ nguyên toàn bộ 30 ID và start/end; không còn chữ Hán.
- Tái phát ba phản hồi API đã lưu trên mã cuối cùng sau bổ sung kiểm tra
  phủ định/tên/số cho kết quả giống hệt bản thử thật. `verified.srt`,
  `verified.json`, `_tmp/translation_replay_final.log` là bằng chứng.
- Lượt thử trung gian giữ schema câu ghép với lô 30 chậm hơn và phải dừng sau
  8 request; đã bỏ cách đó trong yêu cầu dịch chính. Log vẫn được giữ để đối
  chiếu, không lấy kết quả trung gian làm thành tích.
- Giảm số request là đo được; giảm thời gian lượt cuối khoảng 9%, không đủ để
  suy ra hệ số tăng tốc cho cả phim. API miễn phí/mạng có độ trễ biến động.
- Vẫn có 14 dòng vượt ngân sách mềm 18 ký tự/giây × 1,15 (bản cũ 12 dòng,
  nhưng độ quá tải cực đại lớn hơn nhiều). Không cắt mất nghĩa để ép mọi dòng
  qua ngưỡng. Bản dịch vẫn phụ thuộc chất lượng ASR/model; test ID/tên/số không
  chứng minh mọi câu đều đúng nghĩa. Cụm chuyên biệt/ASR sai như 朋友圈,
  借轨 cần ngữ cảnh phim hoặc glossary đã xác nhận để dịch chắc chắn.

## Kiểm thử và sử dụng

- 15 test hồi quy mới trong `tests/test_translation_flow_20260927.py`: câu
  đạt không gửi lại, resume sau hủy, compact JSON, ngữ cảnh, ngân sách,
  trùng ID/cache hỏng, alignment, số/phủ định và retry từ nguồn.
- 53 test semantic hiện có đạt; 6 test cấu hình và 6 test UI Edge đạt.
- 7 test JavaScript đạt, kiểm tra cú pháp `ui/js/dub-panel.js` đạt.
- Lượt toàn bộ cuối: **1.014 test, 1.009 đạt, 5 bỏ qua, 0 thất bại**, 164,976
  giây bằng `venv\Scripts\python.exe`. Log `_tmp/translation_verified_20260927.log`.
  Những bài thiếu media/artifact được đánh dấu bỏ qua, không coi là đã chạy.

Khởi động lại AutoDubVN để nạp mã mới. Trong tab Dịch, bỏ chọn “Dùng lại bản
dịch đã có” khi muốn tạo bản theo logic mới rồi chọn “Chạy dịch”. Cache của
prompt cũ có phiên bản khác; cache câu đạt của logic mới vẫn dùng để tiếp tục
lượt bị gián đoạn. Không cần xóa ASR hoặc nhận dạng lại video.
