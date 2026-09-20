# Gemini 1095 — 2026-09-17

## Kết luận có bằng chứng

Lỗi 1095 đã được tái hiện trên Edge với profile `browser_profile`, cả khi chat chỉ có 1–2 câu rất ngắn và sau khi mở chat trống. Không có đủ bằng chứng để quy mã 1095 cho giới hạn độ dài cuộc trò chuyện.

Gemini dựng user turn tạm thời, sau đó báo snackbar 1095 và bỏ turn. Bộ dò cũ coi user turn tạm là ACK rồi tiếp tục chờ model; không đọc snackbar. Vì vậy một lỗi UI bị báo thành RESPONSE_EXTRACTION_FAILURE hoặc RESPONSE_START_TIMEOUT. Vòng ngoài bỏ lô đang chờ, gửi các lô khác vào cùng phiên lỗi và cuối cùng dừng vì thiếu lô. Đây không phải bằng chứng rằng câu từ chối đã được ghi thành bản dịch.

## Bản sửa

- Đọc thông báo lỗi đang hiển thị từ snackbar/alert, giữ nguyên mã 1095; loại nội dung prompt, model, sidebar và thông báo ẩn.
- Kiểm tra trước gửi, khi đợi ACK và khi đợi phản hồi. ACK tạm không che mất lỗi xảy ra sau đó.
- Nhận diện nhãn `Cuộc trò chuyện mới`.
- Phục hồi đúng prompt đang lỗi tối đa hai lần, nghỉ 5 rồi 15 giây chỉ khi lỗi, reload và xác nhận chat trống trước khi gửi lại.
- Hết lượt phục hồi thì chốt phiên không khỏe, không gửi các lô tiếp theo. Pipeline giữ cache đạt, phân biệt lô chưa chạy với phần hoàn tất; không tạo bản dịch từ toast lỗi.
- Log nguyên nhân và thống kê UI_ERROR_RESPONSE thay vì chỉ báo lỗi trích xuất.

## Kiểm chứng

84 kiểm thử trong test_gemini_ui_recovery, test_gemini_response_detection, test_response_classification, test_semantic_translation: PASS. Bao gồm DOM thật trên Edge headless, 1095 sau ACK, toast ẩn/nằm trong lời thoại, nhãn chat mới, retry đúng prompt, giới hạn retry, reset không trống, dừng trước lô sau, và resume chỉ gọi phần chưa cache.

Live trước backoff: câu 1 thành công, câu 2 được phục hồi thành công sau 1095, câu 3 tiếp tục lỗi sau hai lần phục hồi. Log: `_tmp/gemini_1095_live.log`.

Live có backoff: 2 câu thành công; câu 3 vẫn nhận 1095 sau cả hai lần phục hồi, dừng sau 26.74 giây. Log: `_tmp/gemini_1095_live_backoff.log`; dữ liệu `_tmp/gemini_1095_live/results.json`.

Đối chứng click nút Gửi thật thay cho JavaScript click: câu 1 thành công, câu 2 lỗi 1095. Dữ liệu và ảnh lỗi: `_tmp/gemini_1095_native/`.

## Giới hạn

Chưa đạt live qualification: không thể khẳng định dịch trơn tru toàn bộ video hoặc đã xử lý hết 113 lô. Các kiểm tra cho thấy xử lý lỗi/cache ở ứng dụng đã hoạt động, nhưng Gemini vẫn từ chối yêu cầu ngắn trong phiên thử. Không thay tài khoản, xóa profile/cache, thay model hay sửa dữ liệu video để che lỗi. Cần Gemini tiếp nhận yêu cầu ổn định để kiểm chứng toàn bộ tác vụ.
