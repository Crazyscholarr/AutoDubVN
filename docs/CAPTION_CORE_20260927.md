# Lõi phụ đề Trung: tra mốc và giới hạn dòng — 27/09/2026

## Lỗi tái hiện và sửa

1. `SpeechMap.window` sao chép toàn bộ đuôi danh sách cho từng truy vấn, dù chỉ
   cần vài mốc. Với video dài, chi phí tăng theo số cue nhân số mốc. Đã thay bằng
   chỉ mục midpoint dựng một lần và tìm nhị phân; chỉ đọc các mốc trúng truy vấn.
   Trả về theo thứ tự start như trước. Chỉ mục được dựng lại khi load/shift/scale/
   merge tạo SpeechMap; code sử dụng không sửa trực tiếp danh sách `marks`.
2. Cách lùi cố định hai mốc bỏ sót observation dài bao quanh các observation ngắn.
   Ví dụ `(0,20)` bị bỏ khỏi cửa sổ `[9,11]`. Chỉ mục mới khớp phép lọc toàn bộ
   theo midpoint, kể cả mốc chồng nhau. NaN/Infinity bị loại trước khi tạo chỉ mục.
3. Protected word/atomic token vượt hard limit vẫn được xuất nguyên dòng.
   Cụm 26 chữ từng lọt qua giới hạn 14. Token nay giữ các mốc ký tự đã căn;
   khi quá dài sẽ tách theo các mốc này trước khi bộ tối ưu chia cue chạy.
   Không đổi mặc định độ dài/nhịp câu; không chia tỷ lệ lại trên cả dòng đã căn.
   Một observation đơn lẻ dài bất thường được ghi review, không tự tạo clock giả.

## Bằng chứng

- 8 regression test trong `tests/test_caption_core_20260927.py`: oracle toàn bộ
  với 2.000 mốc ngẫu nhiên và 300 cửa sổ, mốc lồng nhau, finite clocks, biên,
  shift/scale, protected phrase quá dài/chậm và nhiều độ dài 15–85 ký tự.
- Fixture `tests/fixtures/caption_replay.json`: 30 cue gốc → 31 cue; so sánh code
  trước/sau cho kết quả **giống hệt text/start/end** trên fixture này.
- `python scripts/benchmark_caption_windows.py`: 180.000 mốc, 10.000 truy vấn;
  bản cũ 13,184s, bản mới 0,056s, khoảng 236 lần trong phép đo này.
  Chi phí dựng SpeechMap 0,157s; kết quả truy vấn khớp hoàn toàn trên dữ liệu
  không chồng mốc. Số đo phụ thuộc máy/tải hệ thống và chỉ dành cho lookup,
  không phải tuyên bố tăng tốc ASR/TTS/render toàn pipeline.
- Full suite: 972 test, 968 đạt, 4 bỏ qua, 0 lỗi (191,81s),
  `_tmp/caption_core_20260927_tests.log`. Một test quét nhiều độ dài được bổ sung
  sau khi lượt đầy đủ đã bắt đầu; cả nhóm 8 regression test đã chạy riêng và đạt.

## Phạm vi chưa xác minh

Thư mục output hiện không có video/SRT phim cũ để phát lại end-to-end lỗi người
dùng đang thấy. Các kết quả trên chứng minh các lỗi cụ thể đã tái hiện, chưa xác
nhận chúng là toàn bộ nguyên nhân của “lỗi lớn” được báo. Cần video/SRT hoặc mốc
thời gian cụ thể để kiểm chứng tiếp. Không ghi đè phụ đề/dữ liệu video hiện có;
không thay engine ASR, provider dịch hoặc giảm kiểm tra chất lượng để chạy nhanh.
