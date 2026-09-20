# Sửa lõi nhận dạng lại phụ đề — 20/09/2026

Phạm vi: luồng kiểm tra đoạn thiếu lời/mốc thoại trong tab Lồng tiếng video, theo ảnh người dùng cung cấp. Không coi lần sửa này là đã giải quyết toàn bộ báo cáo AUDIT_20260920.md.

## Hành vi sau sửa

- Nhận dạng lại loại chữ cũ khỏi đầu vào cứu khoảng trống trong cửa sổ cần xử lý. Trước đây chữ cũ có thể khiến máy kết luận đã có phụ đề và không gọi nhận dạng lại.
- Đoạn dưới 1,2 giây có thêm ngữ cảnh; nhận dạng lại cả câu nguồn giao với cửa sổ ngữ cảnh, vẫn chia lát tối đa 12 giây.
- Khi chỉ phục hồi được một phần câu nguồn, giữ chữ nguồn cũ và giữ cổng kiểm tra. Không dùng kết quả bị loại để xác nhận chữ cũ là hợp lệ.
- Cổng kiểm tra yêu cầu phủ đủ đoạn, hoặc đủ các vùng có thoại do VAD xác nhận. Một câu chạm vào vùng lỗi không còn xóa cả vùng lỗi; các vùng khác vẫn được giữ. Khoảng lặng đã có bằng chứng không cần bịa phụ đề để lấp đầy.
- Mốc lời nói mới thay mốc cũ tại câu được thay thế; các mốc ngoài vùng đó được giữ. Không cộng chồng hai bộ mốc vào cùng câu.
- Hủy sau ASR hoặc trong VAD được truyền đúng và không ghi checkpoint kết quả sau tín hiệu hủy đã quan sát. Lỗi tải project và hủy khi tác vụ chưa chạy đều giải phóng trạng thái running. Sửa trường hợp A22 trong báo cáo kiểm toán trước.
- Ghi từng SRT qua file tạm rồi thay thế nguyên tử. Đây không phải transaction nguyên tử cho cả nhóm nhiều file.
- API từ chối thời gian âm/NaN/vô cực và chặn xác nhận tạp âm khi tác vụ đang chạy.
- Giao diện hiển thị mili giây, khóa nút thao tác xung đột khi bận, cập nhật lại nút khi trạng thái thay đổi. Không nhận phản hồi kiểm tra cũ khi thư mục kết quả đã thay đổi. Lưu review_gaps trong snapshot để không vô hiệu hóa cache mỗi nhịp refresh.

## Kiểm chứng

- Chạy toàn bộ unittest tại mốc sửa đầu: 937 test / 162,294 giây; 935 đạt, 2 bỏ qua vì thiếu artifact mẫu.
- Sau sửa cuối: nhóm test_caption*.py có 33 test đạt; test_http_runtime.py có 24 test đạt.
- Thêm 15 test lõi trong test_caption_retry_core.py, 3 test HTTP và 3 test Node cho giao diện kiểm tra phụ đề.
- Node test_story_ui_state.js đạt; kiểm tra cú pháp JavaScript và biên dịch Python đạt.
- Log: _tmp/core_retry_full_tests.log, _tmp/caption_final_tests.log, _tmp/caption_http_tests.log.

Các test mới sử dụng media tạm, mock ASR/VAD và cổng kiểm tra thật; test HTTP chạy server cục bộ tạm. Chưa chạy lại ASR trên chính phim trong ảnh, nên chưa xác nhận chất lượng nhận dạng tiếng Trung của đoạn đó. Nếu mô hình vẫn không trả đủ lời và mốc đáng tin cậy, cổng kiểm tra sẽ tiếp tục giữ đoạn để kiểm tra thay vì tự bỏ qua.
