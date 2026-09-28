# Khắc phục lượt dịch dừng ở 2310/2312 cue

## Bằng chứng từ log ngày 27/09

- App khởi động 18:37:08, tải được video 1080P BV1RSYR6AE3F.
- Lượt dịch đầu kết thúc 20:55:17: 2308/2312 cue, thiếu 1095, 1116,
  1193, 1314. Lượt thử lại kết thúc 21:49:16: 2310/2312 cue, còn 1193, 1314.
- Hai cue cuối bị chặn vì bản dịch còn chữ Trung, không phải lỗi tải video.
- Báo cáo cache ghi `semantic-v4-compact`. Glossary lượt hai chứa các khóa
  tiếng Việt như `Lý Lạc Bình`, xác nhận tiếng Việt đã bị đưa lại làm nguồn.
  Tiến độ báo 2312/2312 là vị trí đã duyệt, không phải số cue thành công.
- Đối chiếu SHA-256 khóa yêu cầu v4 với nguồn `.src.srt`, cấu hình cũ
  10 cue / 45 giây / 2 cue ngữ cảnh và danh tính provider khôi phục được
  416 batch thành công, tổng 2308 cue. Không lấy bản dịch lại từ nguồn Việt.

## Mã đã sửa

- Pipeline thử lại từ bản sao nguồn bất biến; không đưa các cue đã dịch vào
  ASR/nguồn dịch. Phần sửa này đã có trên đĩa nhưng app trong log chạy mã cũ.
- Cache semantic nay gắn với toàn bộ nguồn, ID, mốc tuyệt đối, nhóm/người nói,
  glossary đầu vào, cấu hình, gợi ý phim/tên và provider/model. Không phụ
  thuộc tóm tắt hoặc ngữ cảnh Việt do model sinh ra. Sửa một lỗ phía trước
  không làm mất cache các batch đã đạt phía sau; cache vẫn phải qua validator
  với glossary hiện tại. Thay nguồn hoặc đồng hồ làm cache vô hiệu.
- Báo tiến độ theo số cue hoàn tất. Lỗi chữ Trung chỉ rõ ID và cụm còn sót.
- Lượt sửa chữ Trung thử prompt dịch lại từ nguồn, không trích lại câu sai
  khiến model tiếp tục sao chép lỗi. Không bỏ kiểm tra chữ Trung để qua bước.
- Bản dịch mới thay thế cache không hợp lệ vẫn được kiểm tra độ dài, dù bản
  cache cũ từng có cờ `length_checked`.

## Khôi phục video đang dở

- Giữ 2308 cue của lượt gốc. Sửa thủ công theo nguồn và ngữ cảnh 4 cue:
  1095, 1116, 1193, 1314. Đây không phải kết quả API tự sửa thành công.
- Hai lần thử API có giới hạn 4 request/lần vẫn lặp các cụm như 死死抓住,
  扑向. Không tiếp tục tiêu tốn request để lặp cùng lỗi và không tuyên bố
  đã giải quyết chất lượng của model miễn phí trong mọi trường hợp.
- Lưu đủ 2312 cue vào file `.vi.srt` cạnh `.src.srt` trong thư mục output
  của BV1RSYR6AE3F, kèm metadata nhóm câu. Không ghi đè bản dịch có sẵn khác.
- Kiểm tra toàn bộ: 0 cue rỗng, 0 cue còn chữ Trung, ID/mốc khớp nguồn;
  đọc lại file và sidecar bằng chính `restore_metadata` +
  `reuse_translated_cues`: dùng lại 2312/2312, 0 cue cần dịch, 0 API call.
- Bản sao dễ mở: `_tmp/translation_recovery_20260927/recovered.vi.srt`.
  Báo cáo có hash nguồn/đích và 4 bản sửa:
  `_tmp/translation_recovery_20260927/recovery_report.json`.

Chất lượng ngữ nghĩa của 2308 câu cũ được giữ nguyên; kiểm tra ID, mốc và
chữ Trung không chứng minh tất cả câu đều dịch đúng. Chưa chạy TTS/render
toàn phim trong lượt sửa này.

## Sử dụng

Khởi động lại AutoDubVN để nạp mã mới. Với video đã khôi phục này, **giữ bật
“Dùng lại bản dịch đã có”** trong tab Dịch rồi chạy tiếp. Không cần xóa cache
hay nhận dạng lại. Hướng dẫn bỏ chọn reuse trong báo cáo trước chỉ áp dụng
khi muốn chủ động dịch mới toàn bộ, không áp dụng cho lần khôi phục này.

## Kiểm thử

- Ba hồi quy tái hiện trước sửa: cache batch sau bị mất khi sửa lỗ trước,
  mốc tuyệt đối đổi vẫn dùng bản dịch cache, thông báo không chỉ rõ chữ sót.
- Thêm kiểm tra ngữ cảnh nguồn đổi, retry không trích câu sai, tiến độ còn lỗ.
- Lượt cuối: **1020 test, 1015 đạt, 5 bỏ qua, 0 thất bại**, 165,564 giây.
  Chạy bằng `venv\Scripts\python.exe -X utf8 -m unittest discover -s tests`.
  Log: `_tmp/translation_recovery_verified_tests.log`. Những bài thiếu
  media/artifact được bỏ qua; không tính chúng là đã kiểm thử thành công.
