# Sửa xác nhận tạp âm bị bỏ qua — 20/09/2026

Nguyên nhân: `missing_speech_marks` không nằm trong ACKABLE_REASONS của backend, trong khi UI tô xanh chỉ dựa vào độ phủ thời gian. Vì vậy xác nhận được ghi vào non_speech.json nhưng bộ lọc vẫn giữ lỗi chặn.

Đã bổ sung loại missing_speech_marks vào nhóm được người dùng xác nhận không phải thoại. Các xác nhận lưu từ phiên bản cũ dưới reason unresolved_speech_gap vẫn có hiệu lực theo phạm vi thời gian. Không mở khóa các lỗi cấu trúc invalid_clock, invalid_source_clock hoặc overlapping_clock bằng xác nhận tạp âm.

Giao diện dùng trường remaining do backend trả về để quyết định trạng thái đã xác nhận. Không còn tô xanh chỉ vì thời gian khớp khi backend vẫn chặn.

Đã đọc non_speech.json thật của phim BV1Habz6dEJf: xác nhận 14054,35–14054,9 vẫn còn; với bộ lọc mới, các review còn dữ liệu tại thời điểm kiểm tra trả remaining=[] mà không ghi lại hoặc thay đổi xác nhận. Thư mục review-wwstsyk8 trong ảnh hiện trống khi kiểm tra; đã đối chiếu hai review còn dữ liệu của cùng đoạn và cùng file xác nhận dùng chung.

Kiểm thử: 7 test nonspeech, 25 test HTTP, 36 test caption, 4 test Node đều đạt. Ca HTTP mới dùng xác nhận cũ đã lưu, GET trả remaining=[], POST tiếp tục với ranges=[] thành công và chỉ lên lịch translate/tts/render. Không gọi dịch vụ dịch/TTS thật trong test.

Cần mở lại ứng dụng để backend nạp code mới. Sau đó dùng nút “Tạp âm / hiệu ứng — tiếp tục dịch”; không cần đánh dấu lại hoặc chạy nhận dạng lại.
