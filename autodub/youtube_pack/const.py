"""Hằng số thumbnail + prompt mô tả YouTube."""
from __future__ import annotations

import re

THUMB_W = 1280
THUMB_H = 720
LINE1_COLOR = (227, 28, 37)      # đỏ tươi
LINE2_COLOR = (218, 165, 32)     # vàng đậm
STROKE_COLOR = (0, 0, 0)

YEAR_RE = re.compile(r"(\d{1,2})\s*(NĂM|NAM|THÁNG|THANG|NGÀY|NGAY)", re.I)
SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")
TWIST_CUT_RE = re.compile(
    r"(cú lật|lật mặt|hóa ra|hoá ra|sự thật là|kết cục|"
    r"cuối cùng mới|chất liệu cú lật)",
    re.I,
)
HASHTAG_RE = re.compile(r"#\w+")

FIXED_FOOTER = (
    "🕗 Kênh đăng 2 câu chuyện mỗi ngày: 08:00 sáng và 20:00 tối.\n"
    "👉 Đăng ký kênh để không bỏ lỡ câu chuyện tối nay.\n"
    "Nội dung là truyện hư cấu, biên soạn lại từ nhiều câu chuyện đời thường "
    "nhằm mục đích giải trí và chia sẻ. Mọi tên nhân vật và địa danh đã được "
    "thay đổi. Giọng đọc được hỗ trợ bởi công nghệ đọc tự động.\n"
    "#kechuyendemkhuya #tamsutuoigia #chuyenlangque"
)

THUMBNAIL_STYLE_LOCK = """
Ảnh thumbnail YouTube 16:9, photorealistic, cinematic realism, phong cách phim
truyền hình gia đình Việt Nam nhưng hình ảnh phải rõ, gắt, cảm xúc mạnh, dễ
nhìn trên điện thoại.

Cận cảnh từ ngực trở lên. Nhân vật chính duy nhất chiếm khoảng 40–45% ảnh,
đặt lệch hẳn về bên PHẢI khung hình. Chừa toàn bộ nửa TRÁI sạch, ít chi tiết,
tối nhẹ hơn để đặt chữ lớn sau này.

Ánh sáng chiều ấm vàng, chiếu mạnh vào khuôn mặt để nổi nếp nhăn và cảm xúc.
Tăng tương phản khuôn mặt, cảm giác bi kịch gia đình, đau lòng nhưng không quá tối.
Background làm mờ mạnh, chỉ đủ nhận ra bối cảnh. Không để nhân vật phụ tranh chú ý.

Biểu cảm phải rất rõ ngay cả khi thumbnail thu nhỏ.

Không tạo phong cách nghệ thuật, không anime, không tranh vẽ, không da nhựa,
không glamour, không quá nhiều nhân vật, không bố cục tối giản, không khung viền,
không watermark, KHÔNG sinh bất kỳ chữ nào trong ảnh. Giấy tờ chỉ là đạo cụ,
không được có chữ đọc được.
""".strip()

DESCRIPTION_PROMPT = """
Bạn là người viết mô tả video YouTube cho kênh audio "Gốc Mít Kể Chuyện".
BỐI CẢNH KÊNH
Kênh kể chuyện đêm khuya bằng giọng đọc, chủ đề: chuyện gia đình, mẹ chồng nàng dâu, vợ chồng tuổi già, con cái hiếu nghĩa, nhân quả báo ứng, chuyện làng quê Việt Nam.
Khán giả chính là nam nữ từ 45 tuổi trở lên, phần lớn là phụ nữ, nghe khi làm việc nhà hoặc trước khi đi ngủ.
Họ thích giọng văn mộc mạc, tình cảm, không thích văn hoa hay từ ngữ trẻ trung.
THÔNG TIN TẬP NÀY
- Tiêu đề video: {title}
- Nội dung truyện: {summary}
- Thời lượng: {duration}
HÃY VIẾT MÔ TẢ THEO ĐÚNG CẤU TRÚC 6 KHỐI SAU
Khối 1 — Ba dòng đầu (đây là phần hiện ra trước khi người xem bấm "xem thêm", quan trọng nhất):
- Dòng 1: một câu hỏi hoặc câu khẳng định gây tò mò, lấy trực tiếp từ tình huống gay nhất của truyện. Không quá 15 từ.
- Dòng 2: nhắc lại chủ đề bằng cụm từ khóa tự nhiên, phải chứa "kể chuyện đêm khuya" hoặc "tâm sự tuổi già".
- Dòng 3: một câu mời nghe, có nêu thời lượng.
Khối 2 — Giới thiệu truyện, 4 đến 6 câu:
Kể tình huống mở đầu và mâu thuẫn chính. Dừng lại đúng trước cú lật mặt, tuyệt đối không tiết lộ kết thúc.
Viết bằng giọng mộc mạc, câu ngắn. Không dùng từ như "tổng tài", "trà xanh", "bạch nguyệt quang", "hào môn", "ngôn tình".
Khối 3 — Mốc thời gian nội dung:
Chia truyện thành 4 đến 6 chương, mỗi chương một dòng theo dạng 00:00 Tên chương
Đặt tên chương gợi tò mò, không đặt tên chung chung như "Phần 1".
Khối 4 — Câu hỏi cho phần bình luận:
Một câu hỏi mở về chính tình huống trong truyện, kiểu "Nếu là quý vị, quý vị sẽ làm gì?". Đây là phần kéo bình luận, viết cho người 45+ dễ trả lời.
Khối 5 — Khối từ khóa, viết thành câu liền mạch chứ không liệt kê thô:
Phải chứa tự nhiên các cụm: kể chuyện đêm khuya, đọc truyện đêm khuya, tâm sự tuổi già, chuyện làng quê, truyện audio, chuyện thầm kín.
Khối 6 — Phần cố định, copy y nguyên:
{footer}
YÊU CẦU CHUNG
- Tổng độ dài 250 đến 400 từ
- Câu ngắn, dễ đọc, không dùng dấu gạch ngang nối câu dài
- Không viết hoa cả câu trong phần mô tả
- Không hứa hẹn hay nói quá kiểu "hay nhất mọi thời đại"
- Chỉ trả về phần mô tả, không giải thích gì thêm
- Không viết dòng ghi chú, tự nhận xét hay hướng dẫn sửa sau phần mô tả
- Tên chương gợi tò mò, không tiết lộ sự thật hay kết cục
- Tự thêm 2 hashtag phù hợp với tập này, khác với 3 hashtag cố định ở khối 6
""".strip()
