# Sửa lựa chọn chất lượng Bilibili — 27/09/2026

Phạm vi: AutoDubVN. Đã đọc và chạy bộ lấy luồng của ứng dụng ở
`E:\ứng dụng video`; không sửa mã hay cấu hình của ứng dụng tham khảo.

## Nguyên nhân

- API cũ có phản hồi mang nhãn 720P nhưng danh sách DASH chỉ có 480P/360P.
  Bộ so sánh dùng nhãn chung thay vì chất lượng của luồng thực sự được chọn.
- Điểm cộng theo số CDN/loại container có thể làm luồng thấp thắng luồng cao.
- Bộ tải trực tiếp chưa dùng cách lấy metadata bằng yt-dlp của ứng dụng tham
  khảo và chưa nhận cấu hình cookie trình duyệt từ `download_video`.
- Tab Công cụ Video mặc định 480P khi chưa có lựa chọn lưu trước đó.

## Thay đổi

- Dùng yt-dlp lấy danh sách và chọn luồng, ưu tiên độ phân giải rồi mới codec;
  chỉ chuyển đúng luồng hình/tiếng đã chọn sang bộ tải khối có kiểm tra Range,
  validator và SHA-256. Không phóng to 480P thành 1080P.
- Giữ API cũ làm dự phòng; so sánh chất lượng luồng thực tế trước, CDN và
  container chỉ phân định khi chất lượng bằng nhau. Giữ giới hạn khi người
  dùng chủ động chọn 480/720/1080.
- Truyền cấu hình cookie trình duyệt vào khâu lấy metadata. Nếu không đọc
  được cookie trình duyệt, thông báo và thử luồng công khai; vẫn giữ cookie
  file nếu đã cấu hình. Không ghi cookie hay URL CDN có chữ ký vào báo cáo.
- Hiện chất lượng đã chọn và độ phân giải thực do FFprobe đọc sau khi tải.
  Nếu mức được cấp thấp hơn mức yêu cầu cụ thể, hiển thị thông báo rõ ràng.
- Mặc định tab Công cụ Video là chất lượng tốt nhất; thêm lựa chọn 1080P.
  Lựa chọn người dùng đã lưu được giữ nguyên.
- Dò mirror COS/Ali nội địa sớm hơn. Vẫn đo tốc độ và chỉ ghép các CDN có cùng
  kích thước và hash mẫu; không bỏ kiểm tra toàn vẹn để ép tải nhanh.

## Đối chiếu ứng dụng tham khảo và media thật

Cả hai môi trường dùng yt-dlp 2026.08.19. Chạy đúng lớp BilibiliDL và tùy chọn
1080P của ứng dụng tham khảo, không dùng cookie, cho kết quả tại thời điểm thử:

- `BV1gehE6DEjb`: chọn `30064+30280`, 1280×720. Đây là video người dùng báo
  AutoDubVN tải 480P. Chưa có bằng chứng nguồn này cấp 1080P trong phiên thử.
- `BV1yYbE6eEnB`: chọn `30080+30280`, 1920×1080. File lưu bên ứng dụng tham
  khảo cũng được FFprobe xác nhận 1920×1080.

Đối chiếu chi tiết: `_tmp/compare_reference_quality.json`.

Với AutoDubVN đã sửa:

- Video `BV1gehE6DEjb` đã tải đủ bản 1280×720 bằng đường API dự phòng được sửa:
  651.688.348 byte, 3186,968231 giây, H.264 + AAC. FFmpeg giải mã toàn bộ exit 0,
  stderr rỗng. Lượt tải 456,281 giây. File trong `downloads/high_quality/`;
  số liệu ở `_tmp/bilibili_quality_upgrade.json`.
- Bộ lấy metadata mới chọn đúng 1920×1080 cho `BV1yYbE6eEnB`. Đã dùng bộ tải
  Range để nhận 16 MiB hình và 2 MiB tiếng, ghép mẫu 8,133313 giây. FFprobe:
  H.264 1920×1080 + AAC; FFmpeg giải mã mẫu exit 0, stderr rỗng.
- Lượt mẫu HD: metadata 1,250 giây; dò CDN hình 3,843 giây; truyền 16 MiB hình
  4,313 giây. Tổng lấy metadata, dò CDN, tải hai mẫu, ghép và kiểm tra: 14,109
  giây. Đây là mẫu ngắn, không đại diện tốc độ duy trì tải cả phim.
- Mẫu phát được: `_tmp/bilibili_1080_sample/sample_1080.mp4`; báo cáo và log:
  `_tmp/bilibili_1080_sample.json`, `_tmp/bilibili_1080_sample.log`.
- Một lượt tải cả luồng HD 1,7 GiB trước khi đổi thứ tự dò mirror gặp CDN chậm
  và đã chủ động dừng. Chưa kiểm tra giải mã toàn bộ phim HD 1,7 GiB. Không
  suy ra rằng mọi CDN đều nhanh từ lượt mẫu thành công.

## Kiểm thử hồi quy

- Sáu test tái hiện chọn nhầm chất lượng đều thất bại trước sửa, đạt sau sửa.
  Thêm bốn test cho adapter yt-dlp, thiếu audio, cookie bị khóa và chuyển luồng
  1080P sang bộ tải có kiểm tra toàn vẹn.
- Lượt toàn bộ: **997 test, 992 đạt, 5 bỏ qua, 0 thất bại**, 147,108 giây,
  dùng `venv\Scripts\python.exe`. Log `_tmp/bilibili_hd_full_suite.log`.
- Năm bài bỏ qua thiếu media/artifact ASR/dịch mẫu; không tính là đã kiểm thử
  trực tiếp các dịch vụ đó.
- Sau thay đổi thứ tự mirror cuối cùng: **45 test Bilibili đạt**. Log
  `_tmp/bilibili_hd_tests_final.log`.
- Lỗi UI chập chờn `refresh is not defined` từng xuất hiện trong lượt trước
  chưa tái hiện lại; xem `BILIBILI_TRANSFER_20260927.md`. Không tuyên bố đã sửa.

Ứng dụng đang mở cần khởi động lại để nạp Python mới. Không tự dừng tác vụ của
người dùng; các file 480P đã tải trước đó không tự biến thành bản HD.
