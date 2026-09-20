# Khắc phục hồi quy nhận dạng lại: 33 mục kiểm tra — 20/09/2026

## Nguyên nhân đã xác nhận

Bản sửa trước xác định câu mới bằng định danh đối tượng Python. `_rescue_gaps` có thể sao chép/gộp câu ở ngoài cửa sổ nhận dạng; những câu đó bị nhận nhầm là câu mới. Code xóa mốc thoại của chúng nhưng kết quả nhận dạng cục bộ không có mốc mới để bù lại.

Đối chiếu dữ liệu thật của phim BV1Habz6dEJf: 542 mốc mất, 528 mốc ngoài cửa sổ nhận dạng. Mốc mất đầu tiên ở 203,13 giây, khớp cảnh báo 00:03:23.050 trong ảnh. Trước sửa: 77.349 mốc; sau lỗi: 76.815 mốc. Các test trước dùng recognizer mock giữ nguyên đối tượng câu ở xa nên không phát hiện được lỗi này.

## Sửa code

`_isolate_retry_result` giới hạn kết quả được ghi nhận vào đúng cửa sổ nhận dạng. Câu nguồn ở ngoài được lấy lại nguyên vẹn từ bản nguồn đầu vào; kết quả mới băng qua ranh giới không được dùng để ghi đè câu lân cận. Bộ lọc thay mốc thoại kiểm tra cả phạm vi thời gian và câu được giữ, không suy luận câu ở xa là câu mới chỉ vì định danh đối tượng đổi.

Thêm ba test: ASR sao chép tất cả câu; ASR sửa chữ ở xa; kết quả vượt ranh giới. Kiểm thử tích hợp xác nhận mốc của câu ở xa được giữ nguyên.

## Xác minh bằng dữ liệu phim

- Bản lưu trước hồi quy: 1 mục cần kiểm tra.
- Bản lưu bị lỗi: 33 mục trên UI/API; 64 dòng unresolved nội bộ, gồm 63 lỗi đóng phụ đề và 1 cảnh báo bổ sung của pipeline.
- Chạy lại đóng phụ đề với chữ/mốc thoại đã khôi phục: còn 1 mục; không phát sinh lỗi ở xa.
- Giữ nguyên toàn bộ chữ, thời gian và mốc thoại ngoài cửa sổ 14052,2–14055,92 giây.
- Bằng chứng có cấu trúc: [caption_retry_isolation_20260920.json](caption_retry_isolation_20260920.json).
- Công cụ tái hiện: scripts/replay_caption_retry_isolation.py. Đọc baseline và bản lỗi/backup; không sửa input.

## Khôi phục dữ liệu

Đã sao lưu và khôi phục 8 file: nguồn SRT và speechmap của review đang dùng, phụ đề đóng thử, review/unresolved, working SRT, speechmap dùng chung của phim, project JSON. Giữ nguyên cấu hình phụ đề, vùng mờ và logo. Không có bản dịch Việt trong project ở thời điểm phục hồi. Tám checksum sau khôi phục đều khớp manifest.

Backup nằm dưới output của phim, `_tmp/retry-regression-backup-20260920-181126/manifest.json`; mỗi file gốc có bản sao và SHA-256 trước/sau. Các review cũ khác được giữ nguyên để đối chiếu. Ứng dụng đã đóng trước lúc phục hồi nên không ghi đè tác vụ đang chạy.

## Giới hạn còn lại

Đoạn gốc 03:54:14.350–03:54:14.900 vẫn thiếu mốc cho “滑。” và chưa được đánh dấu là tạp âm. Đã chạy Paraformer thật trên đoạn audio 11 giây từ 14050 đến 14061: kết quả tách “滑” và “头” ở mốc khác nhau; chưa đủ để khẳng định đã khôi phục đúng hoàn toàn cụm thoại. Không tự thay bản nguồn bằng kết quả thử này.

Lỗi hồi quy làm tăng 1 lên 33 đã được tái hiện và khắc phục. Điều đó không đồng nghĩa chất lượng nhận dạng của đoạn gốc đã được giải quyết. Khi mở lại ứng dụng, code mới mới có hiệu lực.

## Kiểm thử

Nhóm test_caption*.py: 36 test đạt. Node test_caption_review_ui.js: 3 test đạt. Bộ tổng: 943 test trong 164,635 giây, 941 đạt và 2 bỏ qua do thiếu dữ liệu mẫu. Log: `_tmp/caption_isolation_full_tests.log`.
