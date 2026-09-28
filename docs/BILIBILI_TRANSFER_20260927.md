# Sửa tốc độ tải Bilibili — 27/09/2026

Phạm vi: `autodub/bilibili_direct.py` trong `E:\Video\AutoDubVN`.
Đã đọc cách tải ở `E:\ứng dụng video\core\media\segments.py` và
`core\media\bilibili.py`; áp dụng hàng đợi khối liên tục, không sửa dự án tham khảo.

## Nguyên nhân và thay đổi

- Trước đây đo hết mọi CDN với mẫu 2 MiB, phải chờ cả máy chủ chậm. Nay mẫu
  256 KiB, ngân sách chung 8 giây/lượt; kết thúc sớm sau khi có hai bản tương
  thích đủ nhanh. Nếu chưa có kết quả, thử thêm một lượt mẫu 32 KiB. Không
  đợi các request chậm còn lại kết thúc mới trả kết quả chọn CDN.
- Thay hàng đợi theo đợt bằng hàng đợi liên tục: khối nào xong thì ghi đúng
  offset và cấp khối tiếp theo, không chờ khối đầu tiên của cả đợt.
- Lưu và xác minh SHA-256 từng khối, kể cả các khối nằm sau một khoảng còn
  thiếu. Khi nối lại chỉ tải khối thiếu/hỏng. Xác minh lại byte trên đĩa với
  hash khối nhận được trước khi công nhận hoàn tất.
- CDN trả sai Range/validator bị loại khỏi lượt tải. Lỗi tạm thời hạ ưu tiên,
  dùng chung trạng thái giữa các worker, hạn chế thông báo lỗi lặp.
- Với request If-Range, phản hồi 206 thiếu ETag không tự động bị coi là thay
  nội dung; ETag được trả về mà khác vẫn bị chặn. Vẫn kiểm tra mã 206,
  Content-Range, kích thước và hash. Tham chiếu điều kiện If-Range:
  [RFC 9110 §13.1.5](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.5).
- Tốc độ sau resume chỉ tính byte nhận trong lượt mới, không tính byte cũ.

## Kiểm thử tái hiện

Năm test ban đầu đều thất bại trước sửa: probe bị giữ bởi CDN treo, hàng đợi
chờ theo đợt, mất khối tốt sau lỗ hổng, báo sai khi thiếu ETag, tái sử dụng CDN
đã đổi ETag. Sau sửa cả năm đạt; bổ sung hai test cho ghi nhận khối thành công
khi một khối khác lỗi và phát hiện byte trên đĩa bị hỏng.

- 35 test Bilibili đạt: `_tmp/bilibili_suite_final.log`.
- 23 test downloader/stall recovery đạt: `_tmp/download_suite.log`.
- Runtime dùng đúng `venv\Scripts\python.exe` của ứng dụng.
- Lượt đầy đủ cuối: 987 test, 982 đạt, 5 bỏ qua do thiếu media/artifact mẫu,
  0 thất bại, 151,662 giây. Log: `_tmp/bilibili_final_suite_20260927.log`.
- Một lượt đầy đủ trước đó báo lỗi UI `refresh is not defined` ở test đổi
  provider và reload. Chạy riêng 5 test UI, lặp 12 lần test reload và lượt đầy
  đủ cuối đều đạt. Đã bổ sung stack trace vào chẩn đoán test; chưa xác định
  nguyên nhân lỗi chập chờn này, không coi nó là lỗi UI đã sửa.

## Video thật BV1gehE6DEjb

- Mẫu thử 8 MiB: metadata 3,015 giây, dò CDN 1,110 giây, truyền mẫu 0,937 giây.
  Đây là mẫu ngắn, không phải tốc độ duy trì cả phim.
- Tải đầy đủ 480P cả hình/tiếng và ghép: 165,625 giây; file 326.438.206 byte.
- FFprobe: H.264 + AAC, thời lượng 3186,968231 giây.
- FFmpeg giải mã toàn bộ: exit 0, stderr rỗng.
- Có gặp CDN chậm, trả thiếu khối và đổi ETag trong lượt thử thật; bộ tải phục
  hồi và hoàn tất. Lượt kiểm tra header riêng xác nhận mirror HW đổi ETag
  giữa vùng đầu và vùng 20/52 MiB; không bỏ kiểm tra validator để ép tải tiếp.
- Dữ liệu: `_tmp/bilibili-live-20260927.json`,
  `_tmp/bilibili-full-20260927.json`, `_tmp/bilibili_full_verify.log`.

Không suy ra hệ số tăng tốc cố định: log cũ và lượt kiểm tra mới chạy khác thời
điểm, điều kiện CDN/mạng có thể thay đổi. Bản đang mở phải được khởi động lại
để nạp code Python mới; không tự dừng lượt tải người dùng đang chạy.
