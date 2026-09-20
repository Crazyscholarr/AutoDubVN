"""Catalog nguồn và parser HTML bài tham khảo."""
from __future__ import annotations

import base64
import html
import json
import re
from typing import Dict, Iterable, List, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

REFERENCE_SOURCES = [
    {"key": "vnexpress_tamsu", "name": "VnExpress Tâm sự",
     "address": "https://vnexpress.net/tam-su", "type": "Việt - chuyện thật",
     "why": "Bạn đọc tự kể chuyện hôn nhân, gia đình, con cái.", "priority": "Cao",
     "notes": "Chỉ giữ tình huống; viết lại hoàn toàn.", "lang": "vi", "domain": "vnexpress.net"},
    {"key": "dantri_tinhyeu", "name": "Dân trí - Tình yêu Giới tính",
     "address": "https://dantri.com.vn/tinh-yeu-gioi-tinh", "type": "Việt - chuyện thật",
     "why": "Nhiều chất liệu tâm sự gia đình và người lớn tuổi.", "priority": "Cao",
     "notes": "Đổi tên, địa danh và toàn bộ cách kể.", "lang": "vi", "domain": "dantri.com.vn"},
    {"key": "webtretho_honnhan", "name": "Webtretho - Hôn nhân gia đình",
     "address": "https://www.webtretho.vn/f/chuyen-hon-nhan-gia-dinh", "type": "Việt - diễn đàn",
     "why": "Nhiều tình huống mẹ chồng nàng dâu, ngoại tình, ly hôn.", "priority": "Rất cao",
     "notes": "Nội dung diễn đàn cần biên tập nặng.", "lang": "vi", "domain": "webtretho.vn"},
    {"key": "phunuonline", "name": "Phụ nữ Online - Tuổi xế chiều",
     "address": "https://www.phunuonline.com.vn", "type": "Việt - chuyện thật",
     "why": "Tái hôn, cô đơn và tình cảm tuổi 60-70.", "priority": "Trung bình",
     "notes": "Chỉ dùng làm chất liệu tình huống.", "lang": "vi", "domain": "phunuonline.com.vn"},
    {"key": "youtube_comments", "name": "Bình luận khán giả kênh chuyện đời",
     "address": "Kênh Hương Quê Kể Chuyện; Tuổi Già An Nhiên", "type": "Việt - bình luận khán giả",
     "why": "Khán giả tự kể tình huống, đúng tệp tuổi và nhu cầu nghe.", "priority": "Rất cao",
     "notes": "Chỉ đọc để lấy mô-típ; không chép bình luận hay nhận diện người dùng.",
     "lang": "vi", "domain": "youtube.com"},
    {"key": "zhihu_yanxuan", "name": "知乎盐选故事 (Zhihu)",
     "address": "https://www.zhihu.com", "type": "Trung - truyện ngắn",
     "why": "Kho lớn về 婆媳, 中老年 và tranh chấp gia đình.", "priority": "Rất cao",
     "notes": "Bắt buộc Việt hóa mạnh; không sao chép bản dịch.", "lang": "zh", "domain": "zhihu.com",
     "search_suffix": "真实经历 盐选故事", "narrative_style": "Hook nhanh · tâm sự và truyện ngắn",
     "content_form": "short_story"},
    {"key": "douban_groups", "name": "豆瓣小组 · Chuyện đời thật",
     "address": "https://www.douban.com/group/", "type": "Trung - diễn đàn",
     "why": "Nhiều bài ngôi thứ nhất về gia đình, hôn nhân và cha mẹ.", "priority": "Rất cao",
     "notes": "Chỉ lấy tình huống; không sao chép lời kể hay thông tin nhận dạng.",
     "lang": "zh", "domain": "www.douban.com", "search_suffix": "小组 真实经历",
     "narrative_style": "Ngôi thứ nhất · trải nghiệm thật", "content_form": "experience",
     "direct_search": "douban_groups"},
    {"key": "douban_read", "name": "豆瓣阅读 · Truyện gia đình",
     "address": "https://read.douban.com", "type": "Trung - văn học chọn lọc",
     "why": "Truyện gia đình có lớp lang, nhân vật và góc nhìn văn học đa dạng.", "priority": "Cao",
     "notes": "Dùng cấu trúc xung đột; viết lại hoàn toàn thành đời sống Việt.",
     "lang": "zh", "domain": "read.douban.com", "search_suffix": "家庭故事 小说",
     "narrative_style": "Văn học · nhiều lớp nhân vật", "content_form": "literary",
     "direct_search": "douban_read"},
    {"key": "660i_story", "name": "660i 故事大全",
     "address": "https://660i.com/story", "type": "Trung - truyện ngắn",
     "why": "Có kho 民间故事 và 鬼故事 phù hợp nhánh tâm linh ông bà kể.",
     "priority": "Cao", "notes": "Chỉ lấy mô-típ dân gian; kể lại như truyền miệng và không cổ súy mê tín.", "lang": "zh", "domain": "660i.com",
     "search_suffix": "民间故事 鬼故事 老人讲", "narrative_style": "Dân gian · kỳ bí · ông bà kể", "content_form": "folklore",
     "direct_search": "site_listing",
     "listing_urls": ["https://660i.com/?s={query}", "https://660i.com/story"]},
    {"key": "gushi365", "name": "故事365 · Dân gian",
     "address": "https://www.gushi365.com", "type": "Trung - dân gian",
     "why": "Kho 民间故事, 神话传说, chuyện ma làng quê khá dày.",
     "priority": "Cao", "notes": "Lấy mô-típ dân gian; viết lại giọng ông bà kể, không khẳng định có thật.",
     "lang": "zh", "domain": "gushi365.com",
     "search_suffix": "民间故事 鬼故事 农村传说", "narrative_style": "Dân gian · truyền miệng", "content_form": "folklore",
     "direct_search": "site_listing",
     "listing_urls": ["https://www.gushi365.com/?s={query}", "https://www.gushi365.com/"]},
    {"key": "minjian6mj", "name": "6mj 民间故事",
     "address": "https://www.6mj.com", "type": "Trung - dân gian",
     "why": "Chuyên 民间故事, nhân quả, miếu làng, nghĩa địa.",
     "priority": "Trung bình", "notes": "Kể lại như ký ức làng quê; không cổ súy mê tín.",
     "lang": "zh", "domain": "6mj.com",
     "search_suffix": "民间故事 灵异 报应", "narrative_style": "Dân gian · nhân quả", "content_form": "folklore",
     "direct_search": "site_listing",
     "listing_urls": ["https://www.6mj.com/?s={query}", "https://www.6mj.com/"]},
    {"key": "dantri_tamlinh", "name": "Dân trí · Tâm linh / chuyện lạ",
     "address": "https://dantri.com.vn", "type": "Việt - chuyện lạ",
     "why": "Chuyện lạ, tâm linh, ký ức làng quê gần tệp Gốc Mít.",
     "priority": "Cao", "notes": "Chỉ lấy mô-típ; viết lại giọng ông bà kể, không khẳng định có thật.",
     "lang": "vi", "domain": "dantri.com.vn",
     "search_suffix": "tâm linh chuyện lạ làng quê ông bà",
     "narrative_style": "Ký sự · chuyện lạ · làng quê", "content_form": "folklore"},
    {"key": "vnexpress_tamlinh", "name": "VnExpress · Tâm linh",
     "address": "https://vnexpress.net", "type": "Việt - chuyện lạ",
     "why": "Bài tâm linh, báo mộng, chuyện lạ đời thường.",
     "priority": "Trung bình", "notes": "Đổi tên, địa danh; kể lại như truyền miệng.",
     "lang": "vi", "domain": "vnexpress.net",
     "search_suffix": "tâm linh chuyện lạ báo mộng làng quê",
     "narrative_style": "Ký sự · tâm linh đời thường", "content_form": "folklore"},
    {"key": "fanqie", "name": "番茄小说 (Fanqie)",
     "address": "https://fanqienovel.com", "type": "Trung - truyện dài",
     "why": "Truyện đô thị gia đình dài tập, phù hợp làm series.", "priority": "Thấp",
     "notes": "Chỉ dùng ý tưởng/mô-típ; cần cắt gọn và viết mới.", "lang": "zh", "domain": "fanqienovel.com",
     "search_suffix": "现实 家庭 小说", "narrative_style": "Dài tập · hook dày", "content_form": "serial"},
    {"key": "qidian", "name": "起点中文网 (Qidian)",
     "address": "https://www.qidian.com", "type": "Trung - truyện dài",
     "why": "Truyện hiện thực dài tập, có nhịp chương và tuyến nhân vật rõ.", "priority": "Trung bình",
     "notes": "Chỉ lấy cơ chế xung đột; bỏ mô-típ xa lạ với đời sống Việt.",
     "lang": "zh", "domain": "qidian.com", "search_suffix": "现实 家庭 小说",
     "narrative_style": "Dài tập · nhịp chương rõ", "content_form": "serial",
     "direct_search": "qidian_mobile"},
    {"key": "hongxiu", "name": "红袖读书 (Hongxiu)",
     "address": "https://www.hongxiu.com", "type": "Trung - truyện nữ/gia đình",
     "why": "Mạnh về hôn nhân, mẹ chồng nàng dâu, nuôi con và cảm xúc phụ nữ.", "priority": "Cao",
     "notes": "Giữ mâu thuẫn đời thường; bỏ ngôn tình quá đà và Việt hóa toàn bộ.",
     "lang": "zh", "domain": "hongxiu.com", "search_suffix": "现实 家庭 婚姻 小说",
     "narrative_style": "Nữ tính · tình cảm gia đình", "content_form": "serial",
     "direct_search": "hongxiu"},
    {"key": "qimao", "name": "七猫中文网 (Qimao)",
     "address": "https://www.qimao.com", "type": "Trung - truyện dài miễn phí",
     "why": "Truyện đại chúng có hook sớm, cao trào liên tục và số liệu độ phổ biến.", "priority": "Cao",
     "notes": "Ưu tiên bài có điểm/lượt đọc cao; không bê nguyên tình tiết hay thuật ngữ mạng.",
     "lang": "zh", "domain": "qimao.com", "search_suffix": "家庭 婚姻 小说",
     "narrative_style": "Đại chúng · hook nhanh · nhiều cao trào", "content_form": "serial",
     "direct_search": "qimao"},
    {"key": "zongheng", "name": "纵横中文网 (Zongheng)",
     "address": "https://www.zongheng.com", "type": "Trung - truyện dài",
     "why": "Bổ sung truyện hiện thực, phụng dưỡng và xung đột nhiều thế hệ.", "priority": "Trung bình",
     "notes": "Lọc kỹ thể loại; chỉ dùng chất liệu gia đình phù hợp khán giả 45+.",
     "lang": "zh", "domain": "zongheng.com", "search_suffix": "现实 家庭 养老 小说",
     "narrative_style": "Dài tập · xung đột nhiều thế hệ", "content_form": "serial",
     "direct_search": "zongheng"},
    {"key": "reddit_aita", "name": "Reddit r/AmItheAsshole",
     "address": "https://www.reddit.com/r/AmItheAsshole", "type": "Anh - chuyện thật",
     "why": "Xung đột gia đình có cấu trúc rõ và nhiều cú lật.", "priority": "Trung bình",
     "notes": "Phải Việt hóa mạnh bối cảnh và chuẩn mực ứng xử.", "lang": "en", "domain": "reddit.com"},
]

CHINESE_KEYWORDS = [
    {"keyword": "农村老人 灵异故事", "meaning": "Chuyện tâm linh do người già ở quê kể", "topic": "Tâm linh ông bà kể", "note": "Không khí làng quê, lời kể của ông bà", "source_group": "spiritual"},
    {"keyword": "老人讲 亲身经历 怪事", "meaning": "Người già kể chuyện lạ từng trải qua", "topic": "Tâm linh ông bà kể", "note": "Ưu tiên ngôi kể hồi tưởng, trải nghiệm đời xưa", "source_group": "spiritual"},
    {"keyword": "乡村怪谈 老人讲述", "meaning": "Chuyện kỳ bí làng quê do người già kể", "topic": "Tâm linh làng quê", "note": "Hợp giọng kể đêm khuya bên bếp lửa", "source_group": "spiritual"},
    {"keyword": "托梦 真实经历 老人", "meaning": "Người già kể chuyện báo mộng", "topic": "Báo mộng và tình thân", "note": "Giữ sắc thái truyền miệng, không khẳng định có thật", "source_group": "spiritual"},
    {"keyword": "民间因果报应 老人故事", "meaning": "Chuyện nhân quả dân gian của người già", "topic": "Nhân quả tâm linh", "note": "Ưu tiên bài học sống, không cổ súy mê tín", "source_group": "spiritual"},
    {"keyword": "奶奶讲的 鬼故事", "meaning": "Chuyện ma bà kể", "topic": "Chuyện bà kể", "note": "Mô-típ ký ức tuổi thơ và gia đình", "source_group": "spiritual"},
    {"keyword": "土地庙 灵异 农村", "meaning": "Chuyện lạ ở miếu thổ địa làng", "topic": "Miếu làng", "note": "Hợp giọng kể bên bếp lửa, không cổ súy mê tín", "source_group": "spiritual"},
    {"keyword": "祖坟 报应 故事", "meaning": "Tranh mồ mả tổ tiên rồi gặp quả báo", "topic": "Mồ mả và nhân quả", "note": "Nút thắt đất đai + tâm linh, gần tệp 45+", "source_group": "spiritual"},
    {"keyword": "旧宅 闹鬼 搬家", "meaning": "Chuyển vào nhà cũ rồi chuyện lạ", "topic": "Nhà cũ kỳ bí", "note": "Thừa kế nhà, không khí đêm khuya", "source_group": "spiritual"},
    {"keyword": "祭祖 怪事 真实", "meaning": "Chuyện lạ lúc giỗ tổ, cúng ông bà", "topic": "Giỗ tổ", "note": "Bàn thờ, hương khói, lời kể truyền miệng", "source_group": "spiritual"},
    {"keyword": "村口大树 灵异", "meaning": "Cây đa / cây cổ đầu làng và chuyện lạ", "topic": "Cây thiêng làng quê", "note": "Mô-típ dân gian dễ Việt hóa", "source_group": "spiritual"},
    {"keyword": "古井 闹鬼 传说", "meaning": "Giếng cổ làng và chuyện oan hồn", "topic": "Giếng làng", "note": "Truyền thuyết làng, kể lại như ký ức", "source_group": "spiritual"},
    {"keyword": "半夜敲门 农村 怪事", "meaning": "Đêm khuya có tiếng gõ cửa mà không có người", "topic": "Tiếng gõ cửa đêm", "note": "Hook lạnh gáy, hợp audio đêm khuya", "source_group": "spiritual"},
    {"keyword": "亡妻托梦 真实经历", "meaning": "Vợ/chồng đã mất báo mộng", "topic": "Báo mộng tình thân", "note": "Tình cảm tuổi già, không khẳng định có thật", "source_group": "spiritual"},
    {"keyword": "祖先显灵 不孝", "meaning": "Con bất hiếu rồi gặp điềm lạ từ ông bà", "topic": "Tổ tiên và bất hiếu", "note": "Ghép tâm linh với đạo hiếu", "source_group": "spiritual"},
    {"keyword": "七月半 鬼节 故事", "meaning": "Chuyện rằm tháng bảy, cô hồn", "topic": "Rằm tháng bảy", "note": "Mùa vụ kể chuyện tâm linh rõ", "source_group": "spiritual"},
    {"keyword": "改坟 出事 民间", "meaning": "Cải táng / dời mộ rồi nhà gặp chuyện", "topic": "Cải táng", "note": "Mô-típ làng quê rất quen với khán giả Việt", "source_group": "spiritual"},
    {"keyword": "烧纸 看见故人", "meaning": "Đốt vàng mã rồi thấy người đã khuất", "topic": "Vàng mã và người âm", "note": "Kể như ký ức, không hù dọa đậm", "source_group": "spiritual"},
    {"keyword": "小孩看见 去世的奶奶", "meaning": "Trẻ nhỏ thấy bà/ông đã mất", "topic": "Trẻ thấy người âm", "note": "Góc tình thân, hợp giọng ông bà kể", "source_group": "spiritual"},
    {"keyword": "夜路遇见死人 真实", "meaning": "Đi đêm gặp người đã mất", "topic": "Đi đêm gặp chuyện lạ", "note": "Truyền miệng làng xã", "source_group": "spiritual"},
    {"keyword": "香火断了 报应", "meaning": "Dứt hương khói bàn thờ rồi nhà sinh chuyện", "topic": "Hương khói tổ tiên", "note": "Bài học thờ cúng, không cổ súy mê tín", "source_group": "spiritual"},
    {"keyword": "抢风水宝地 祖坟", "meaning": "Tranh đất phong thủy / phần mộ", "topic": "Tranh đất mồ", "note": "Xung đột gia tộc + tâm linh", "source_group": "spiritual"},
    {"keyword": "河边冤魂 民间传说", "meaning": "Oan hồn bến sông, người đuối nước", "topic": "Sông nước làng quê", "note": "Truyền thuyết dân gian, kể lại nhẹ", "source_group": "spiritual"},
    {"keyword": "老房子继承 闹鬼", "meaning": "Thừa kế nhà cũ rồi chuyện không yên", "topic": "Thừa kế nhà kỳ bí", "note": "Ghép gia sản với tâm linh", "source_group": "spiritual"},
    {"keyword": "遗照 眼睛会动", "meaning": "Ảnh thờ / di ảnh có điềm lạ", "topic": "Ảnh thờ", "note": "Chi tiết bàn thờ rất hợp kênh audio", "source_group": "spiritual"},
    {"keyword": "丧事 怪事 农村", "meaning": "Đám tang quê và chuyện lạ", "topic": "Đám tang làng", "note": "Không khí giỗ chạp, lời kể người già", "source_group": "spiritual"},
    {"keyword": "chuyện ma làng quê ông bà kể", "meaning": "Chuyện ma truyền miệng làng quê Việt", "topic": "Tâm linh ông bà kể", "note": "Tìm trên báo Việt; kể lại, không khẳng định có thật", "source_group": "spiritual_vi"},
    {"keyword": "báo mộng người thân đã khuất", "meaning": "Người thân mất báo mộng", "topic": "Báo mộng và tình thân", "note": "Nguồn Việt, giọng ký ức gia đình", "source_group": "spiritual_vi"},
    {"keyword": "miếu làng chuyện tâm linh", "meaning": "Chuyện lạ ở miếu làng Việt", "topic": "Miếu làng", "note": "Gần đời sống thôn quê", "source_group": "spiritual_vi"},
    {"keyword": "ngày giỗ chuyện lạ", "meaning": "Chuyện tâm linh quanh ngày giỗ", "topic": "Giỗ tổ", "note": "Bàn thờ, họ hàng, đêm khuya", "source_group": "spiritual_vi"},
    {"keyword": "nghĩa địa làng quê chuyện thật", "meaning": "Chuyện lạ nghĩa địa quê", "topic": "Nghĩa địa làng", "note": "Chỉ lấy mô-típ, viết lại nhẹ", "source_group": "spiritual_vi"},
    {"keyword": "婆媳矛盾 故事", "meaning": "Mâu thuẫn mẹ chồng nàng dâu", "topic": "Mẹ chồng nàng dâu", "note": "Từ khóa gốc, dùng nhiều nhất"},
    {"keyword": "婆媳 真实经历", "meaning": "Trải nghiệm thật mẹ chồng nàng dâu", "topic": "Mẹ chồng nàng dâu", "note": "Tình huống người thật kể"},
    {"keyword": "赡养纠纷 故事", "meaning": "Tranh chấp phụng dưỡng cha mẹ", "topic": "Con cái bất hiếu", "note": "Tranh luận trách nhiệm chăm cha mẹ"},
    {"keyword": "儿女不孝 老人", "meaning": "Con cái bất hiếu, người già", "topic": "Con cái bất hiếu", "note": "Chủ đề xung đột mạnh"},
    {"keyword": "中老年情感故事", "meaning": "Chuyện tình cảm trung niên và cao tuổi", "topic": "Tình yêu xế chiều", "note": "Đúng tệp U60-U70"},
    {"keyword": "老年夫妻 感情", "meaning": "Tình cảm vợ chồng già", "topic": "Vợ chồng tuổi già", "note": "Có thể ghép nhân quả"},
    {"keyword": "老伴去世 再婚", "meaning": "Bạn đời mất, tái hôn", "topic": "Cha mẹ tái hôn", "note": "Con cái phản đối tái hôn"},
    {"keyword": "空巢老人 故事", "meaning": "Người già sống một mình", "topic": "Cô đơn tuổi già", "note": "Con đi xa, viện dưỡng lão"},
    {"keyword": "遗产 分家 纠纷", "meaning": "Tranh chấp thừa kế chia gia sản", "topic": "Tranh chấp thừa kế", "note": "Tình huống gia đình nhiều nút thắt"},
    {"keyword": "出轨 报应 中年", "meaning": "Ngoại tình và quả báo tuổi trung niên", "topic": "Nhân quả ngoại tình", "note": "Kết hợp với tuyến gia đình"},
    {"keyword": "农村 婆媳 故事", "meaning": "Mẹ chồng nàng dâu nông thôn", "topic": "Mẹ chồng nàng dâu", "note": "Dễ chuyển sang làng quê Việt"},
    {"keyword": "保姆 雇主 老人 故事", "meaning": "Người giúp việc và ông chủ già", "topic": "Giúp việc và người già", "note": "Tuyến đời thường giàu tình tiết"},
    {"keyword": "养老院 真实故事", "meaning": "Chuyện thật ở viện dưỡng lão", "topic": "Cô đơn tuổi già", "note": "Chất liệu cảm động"},
    {"keyword": "重男轻女 遗产", "meaning": "Trọng nam khinh nữ trong chia thừa kế", "topic": "Tranh chấp thừa kế", "note": "Hợp khán giả phụ nữ 45+"},
    {"keyword": "女婿 岳母 矛盾", "meaning": "Mâu thuẫn con rể và mẹ vợ", "topic": "Gia đình thông gia", "note": "Nhánh ít người làm, nên thử"},
    {"keyword": "黄昏恋 子女反对 故事", "meaning": "Tình yêu xế chiều bị con cái phản đối", "topic": "Tái hôn tuổi già", "note": "Xung đột thế hệ và cảm xúc rõ"},
    {"keyword": "退休夫妻 矛盾 真实经历", "meaning": "Mâu thuẫn vợ chồng sau nghỉ hưu", "topic": "Vợ chồng tuổi già", "note": "Đời thường, gần tệp U60-U70"},
    {"keyword": "兄弟姐妹 养老 分摊", "meaning": "Anh chị em chia trách nhiệm phụng dưỡng", "topic": "Con cái báo hiếu", "note": "Nhiều tuyến nhân vật đối lập"},
    {"keyword": "老人卖房 养老 子女", "meaning": "Người già bán nhà dưỡng già và con cái", "topic": "Nhà cửa tuổi già", "note": "Tài sản và quyền tự quyết"},
    {"keyword": "二婚家庭 财产纠纷", "meaning": "Tranh chấp tài sản gia đình tái hôn", "topic": "Tái hôn và tài sản", "note": "Hợp kịch bản nhiều cú lật"},
    {"keyword": "亲家 矛盾 彩礼", "meaning": "Mâu thuẫn hai bên thông gia và sính lễ", "topic": "Thông gia", "note": "Mở rộng ngoài mẹ chồng nàng dâu"},
    {"keyword": "拆迁款 兄弟姐妹 纠纷", "meaning": "Anh chị em tranh tiền đền bù", "topic": "Tranh chấp gia sản", "note": "Xung đột cụ thể, hook mạnh"},
    {"keyword": "农村留守老人 真实故事", "meaning": "Chuyện thật người già ở lại nông thôn", "topic": "Cô đơn tuổi già", "note": "Dễ chuyển sang làng quê Việt"},
    {"keyword": "保姆 遗嘱 老人", "meaning": "Người giúp việc, di chúc và người già", "topic": "Giúp việc và ông bà chủ", "note": "Bí mật và tranh chấp thừa kế"},
    {"keyword": "老年人被骗 养老钱", "meaning": "Người già bị lừa tiền dưỡng già", "topic": "Lừa đảo tuổi già", "note": "Chủ đề mới, có giá trị cảnh tỉnh"},
]

def _metric_number(value) -> int:
    text = str(value or "").strip().replace(",", "").replace(" ", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*([万亿kKmM]?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    multiplier = {"万": 10_000, "亿": 100_000_000,
                  "k": 1_000, "K": 1_000, "m": 1_000_000,
                  "M": 1_000_000}.get(match.group(2), 1)
    return max(0, int(number * multiplier))


def _text_metrics(text: str) -> Dict[str, int]:
    raw = str(text or "")
    read_patterns = (
        r"被浏览\s*(?:\*\*)?([\d,.]+\s*[万亿]?)",
        r"(?:阅读量|浏览量|热度|人气|点击量|总点击|views?|viewCount)\D{0,16}([\d,.]+\s*[万亿kKmM]?)",
    )
    vote_patterns = (
        r"([\d,.]+\s*[万亿]?)\s*人赞同",
        r"(?:点赞|总推荐|推荐票|收藏|voteup_count)\D{0,12}([\d,.]+\s*[万亿kKmM]?)",
    )
    comment_patterns = (
        r"([\d,.]+\s*[万亿]?)\s*条评论",
        r"comment_count\D{0,12}([\d,.]+\s*[万亿kKmM]?)",
    )
    reads = max((_metric_number(m.group(1)) for pattern in read_patterns
                 for m in re.finditer(pattern, raw, flags=re.I)), default=0)
    votes = max((_metric_number(m.group(1)) for pattern in vote_patterns
                 for m in re.finditer(pattern, raw, flags=re.I)), default=0)
    comments = max((_metric_number(m.group(1)) for pattern in comment_patterns
                    for m in re.finditer(pattern, raw, flags=re.I)), default=0)
    engagement = votes + comments
    return {"read_count": reads, "engagement_count": engagement}


def reference_catalog() -> List[Dict]:
    """Trả về bản sao catalog để API/UI không sửa dữ liệu gốc."""
    return [dict(row) for row in REFERENCE_SOURCES]


def chinese_keyword_catalog() -> List[Dict]:
    return [dict(row) for row in CHINESE_KEYWORDS]


def _clean_html_fragment(value: str) -> str:
    value = re.sub(r"<(script|style)[^>]*>[\s\S]*?</\1>", " ", value or "",
                   flags=re.I)
    value = re.sub(r"<br\s*/?>|</(?:p|div|li|h[1-6])>", "\n", value,
                   flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t]+", " ", value)
    return re.sub(r"\n\s*\n+", "\n\n", value).strip()


def _decode_bing_link(value: str) -> str:
    """Giải mã link ``bing.com/ck/a?...&u=a1BASE64`` thành URL nguồn.

    Bing hay để href tương đối ``/ck/l?...``; nếu không gắn host thì không
    giải mã được và mọi kết quả ``site:zhihu.com`` bị lọc mất.
    """
    link = html.unescape(str(value or "")).strip()
    if link.startswith("/ck/") or link.startswith("/cc/"):
        link = "https://www.bing.com" + link
    try:
        parsed = urlparse(link)
        host = (parsed.netloc or "").lower()
        if host and not host.endswith("bing.com"):
            return link
        if not host and not parsed.path.startswith("/ck"):
            return link
        encoded = (parse_qs(parsed.query).get("u") or [""])[0]
        encoded = unquote(encoded)
        if encoded.startswith("a1"):
            encoded = encoded[2:]
        if not encoded:
            return link
        encoded += "=" * ((4 - len(encoded) % 4) % 4)
        decoded = base64.urlsafe_b64decode(encoded).decode("utf-8", "replace")
        return decoded if decoded.startswith(("http://", "https://")) else link
    except Exception:
        return link


def _url_from_cite(cite_html: str, domain: str) -> str:
    """Bing thường ghi URL thật trong ``<cite>zhihu.com › question › 1</cite>``."""
    text = _clean_html_fragment(cite_html)
    text = re.sub(r"\s*[›»·|]\s*", "/", text)
    text = re.sub(r"\s+", "", text).split("...")[0].strip().rstrip("/")
    if not text:
        return ""
    if not text.startswith("http"):
        text = "https://" + text.lstrip("/")
    return text if _domain_matches(text, domain) else ""


def _unwrap_search_href(value: str) -> str:
    """Lấy URL đích từ link theo dõi Bing/DuckDuckGo."""
    link = html.unescape(str(value or "")).strip()
    if "uddg=" in link:
        parsed = urlparse(link)
        encoded = (parse_qs(parsed.query).get("uddg") or [""])[0]
        if encoded:
            return unquote(encoded)
    return _decode_bing_link(link)


def _domain_matches(url: str, domain: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().strip(".")
        wanted = str(domain or "").lower().strip(".")
        return bool(host and wanted and (host == wanted or host.endswith("." + wanted)))
    except Exception:
        return False


_SKIP_LISTING_PATH = re.compile(
    r"(login|tag|category|author|search|about|contact|privacy|wp-|feed|sitemap)",
    re.I)


def _pick_domain_url(candidates, domain: str) -> str:
    for raw in candidates:
        link = _unwrap_search_href(raw)
        if _domain_matches(link, domain):
            return link
    return ""


def _parse_bing_algo_block(block: str, domain: str) -> Optional[Dict]:
    title = ""
    h2 = re.search(
        r'<h2[^>]*>[\s\S]*?<a[^>]+href=["\']([^"\']+)["\'][^>]*>'
        r'([\s\S]*?)</a>', block, flags=re.I)
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', block)
    if h2:
        title = _clean_html_fragment(h2.group(2))
        hrefs = [h2.group(1)] + hrefs
    if not title:
        titled = re.search(
            r'<a[^>]+class=["\'][^"\']*\b(?:tilk|b_title)\b[^"\']*["\'][^>]*>'
            r'([\s\S]*?)</a>', block, flags=re.I)
        if titled:
            title = _clean_html_fragment(titled.group(1))
    if not title:
        for body in re.findall(
                r'<a[^>]+href=["\'][^"\']+["\'][^>]*>([\s\S]*?)</a>', block, flags=re.I):
            text = _clean_html_fragment(body)
            if len(text) >= 6:
                title = text
                break
    cite = re.search(r"<cite[^>]*>([\s\S]*?)</cite>", block, flags=re.I)
    candidates = list(hrefs)
    if cite:
        reconstructed = _url_from_cite(cite.group(1), domain)
        if reconstructed:
            candidates.append(reconstructed)
    link = _pick_domain_url(candidates, domain)
    if not link or not title:
        return None
    desc_match = re.search(r"<p[^>]*>([\s\S]*?)</p>", block, flags=re.I)
    excerpt = _clean_html_fragment(desc_match.group(1) if desc_match else "")
    return {"kind": "reference", "provider": "bing", "title": title,
            "url": link, "excerpt": excerpt[:700], "channel": domain,
            "duration": 0}


def _looks_like_article(url: str, title: str) -> bool:
    path = (urlparse(url).path or "").lower()
    if len(title or "") < 8:
        return False
    if path in {"", "/"} or _SKIP_LISTING_PATH.search(path):
        return False
    return bool(re.search(r"\d|\.html|story|post|article|chapter|truyen", path))


def _compact_chinese_query(keyword: str) -> str:
    """Rút cụm chủ đề để ô tìm kiếm nội bộ không bị quá khớp chính xác."""
    chunks = re.findall(r"[\u3400-\u9fff]+", str(keyword or ""))
    compact = "".join(chunks)
    for generic in ("真实经历", "故事大全", "故事", "小说", "经历"):
        compact = compact.replace(generic, "")
    return (compact or str(keyword or "").strip())[:16]


def _focused_chinese_query(keyword: str) -> str:
    compact = _compact_chinese_query(keyword)
    focus_terms = (
        "灵异故事", "鬼故事", "乡村怪谈", "托梦", "因果报应", "灵异", "怪事",
        "土地庙", "祖坟", "闹鬼", "祭祖", "古井", "半夜敲门", "亡妻托梦",
        "显灵", "七月半", "改坟", "烧纸", "香火", "风水", "冤魂", "旧宅",
        "遗照", "丧事", "村口大树",
        "婆媳", "儿女不孝", "赡养", "中老年", "老年夫妻", "黄昏恋", "再婚",
        "空巢老人", "遗产", "出轨", "保姆", "养老院", "重男轻女", "女婿",
        "退休夫妻", "兄弟姐妹", "老人卖房", "二婚家庭", "亲家", "拆迁款",
        "留守老人", "老年人被骗",
    )
    return next((term for term in focus_terms if term in compact), compact)


_SPIRITUAL_TERMS = (
    "灵异故事", "鬼故事", "乡村怪谈", "怪谈", "托梦", "因果报应",
    "报应", "闹鬼", "鬼魂", "亡灵", "阴间", "冥界", "灵异", "怪事",
    "土地庙", "祖坟", "祭祖", "古井", "半夜敲门", "显灵", "七月半",
    "改坟", "烧纸", "香火", "冤魂", "遗照", "丧事", "鬼节", "风水",
    "tâmlinh", "chuyệnma", "báomộng", "điềmbáo", "ngườiâm",
    "miếulàng", "ngàygiỗ", "nghĩađịa", "hươngkhói", "vàngmã",
    "oanhồn", "bànthờ", "cảitáng",
)
_ORAL_TELLER_TERMS = (
    "老人讲", "奶奶讲", "爷爷讲", "婆婆讲", "老人讲述",
    "奶奶", "爷爷", "祖辈", "ôngbàkể", "bàkể", "ôngkể",
)
_ORAL_RURAL_TERMS = (
    "农村", "乡村", "民间", "老人", "奶奶", "爷爷", "祖辈", "讲述", "老人讲",
    "ôngbà", "ngườigià", "làngquê", "dângian",
)
_TOPIC_TERMS = (
    *_SPIRITUAL_TERMS, *_ORAL_RURAL_TERMS,
    "婆媳矛盾", "婆媳", "矛盾", "赡养纠纷", "赡养", "儿女不孝",
    "中老年", "老年夫妻", "黄昏恋", "再婚", "空巢老人", "遗产",
    "出轨", "保姆", "养老院", "重男轻女", "女婿", "岳母", "退休夫妻",
    "兄弟姐妹", "老人卖房", "二婚家庭", "亲家", "彩礼", "拆迁款",
    "留守老人", "老年人被骗", "养老钱",
)


def _search_text(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", str(value or "").casefold())


def _reference_relevance(row: Dict, query: str,
                         article: Optional[Dict] = None) -> Dict:
    """Chấm đúng chủ đề trước độ nổi tiếng, có cổng cứng cho truyện tâm linh.

    Trước đây một tiểu thuyết hàng trăm triệu lượt đọc có thể đứng đầu chỉ vì
    công cụ tìm kiếm trả nó về. Điểm này dùng tiêu đề/đoạn trích/toàn văn và,
    với preset tâm linh ông bà kể, buộc bài phải có dấu hiệu kỳ bí. Chỉ khi
    chính câu tìm có ngôi kể ông/bà/người già thì mới bắt thêm chất lời kể đó;
    không loại truyện miếu, giỗ, nghĩa địa chỉ vì thiếu chữ «老人».
    """
    article = article or {}
    title = _search_text(" ".join((str(row.get("title") or ""),
                                   str(article.get("title") or ""))))
    excerpt = _search_text(str(row.get("excerpt") or ""))
    content = _search_text(str(article.get("rawContent") or ""))
    compact = _search_text(query)
    terms = list(dict.fromkeys(term for term in _TOPIC_TERMS if term in compact))
    if not terms and compact:
        terms = [compact]

    score = 0
    matched = []
    if compact:
        if compact in title:
            score += 55
        elif compact in excerpt:
            score += 30
        elif compact in content:
            score += 16
    for term in terms:
        hit = False
        if term in title:
            score += 24
            hit = True
        if term in excerpt:
            score += 10
            hit = True
        if term in content:
            score += 5
            hit = True
        if hit:
            matched.append(term)

    spiritual_query = any(term in compact for term in _SPIRITUAL_TERMS)
    haystack = title + excerpt + content
    spiritual_hit = any(term in haystack for term in _SPIRITUAL_TERMS)
    teller_hit = any(term in haystack for term in _ORAL_TELLER_TERMS)
    context_hit = any(term in haystack for term in _ORAL_RURAL_TERMS)
    accepted = (not spiritual_query or spiritual_hit)
    if spiritual_query:
        score += 22 if spiritual_hit else 0
        score += 10 if context_hit else 0
        score += 8 if teller_hit else 0
    return {
        "relevance_score": max(0, min(100, int(score))),
        "relevance_terms": matched[:8],
        "relevance_accepted": bool(accepted),
        "strict_relevance": bool(spiritual_query),
    }


def _rank_direct_rows(rows: Iterable[Dict], query: str, limit: int) -> List[Dict]:
    compact = _compact_chinese_query(query)
    terms = [compact[index:index + 2] for index in range(0, len(compact), 2)
             if len(compact[index:index + 2]) == 2]

    def score(row):
        title = str(row.get("title") or "")
        excerpt = str(row.get("excerpt") or "")
        return (sum(title.count(term) for term in terms) * 10 +
                sum(excerpt.count(term) for term in terms))

    return sorted((dict(row) for row in rows), key=score, reverse=True)[:limit]


def _html_search_links(page: str, base_url: str, domain: str,
                       path_pattern: str, limit: int) -> List[Dict]:
    rows, seen = [], set()
    pattern = re.compile(
        r'<a\b([^>]*?)href=["\']([^"\']+)["\']([^>]*)>([\s\S]*?)</a>', re.I)
    for match in pattern.finditer(page or ""):
        attrs = (match.group(1) or "") + " " + (match.group(3) or "")
        link = html.unescape(match.group(2) or "").strip()
        link = urljoin(base_url, link if not link.startswith("//") else "https:" + link)
        if (not _domain_matches(link, domain) or link in seen or
                not re.search(path_pattern, urlparse(link).path, flags=re.I)):
            continue
        title_attr = re.search(r'\btitle=["\']([^"\']+)["\']', attrs, flags=re.I)
        title = _clean_html_fragment(title_attr.group(1) if title_attr else match.group(4))
        if len(title) < 2:
            continue
        nearby = (page[max(0, match.start() - 300):
                       min(len(page), match.end() + 700)])
        excerpt = _clean_html_fragment(nearby)
        metrics = _text_metrics(nearby)
        seen.add(link)
        rows.append({
            "kind": "reference", "provider": "site_search", "title": title,
            "url": link, "excerpt": excerpt[:700], "channel": domain,
            "duration": 0, **metrics,
        })
        if len(rows) >= limit:
            break
    return rows


def _json_ld_article(page: str) -> Dict:
    def candidates(value):
        if isinstance(value, list):
            for item in value:
                yield from candidates(item)
        elif isinstance(value, dict):
            yield value
            yield from candidates(value.get("@graph"))

    for raw in re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>'
            r'([\s\S]*?)</script>', page or "", flags=re.I):
        try:
            payload = json.loads(html.unescape(raw).strip())
        except Exception:
            continue
        for item in candidates(payload):
            body = item.get("articleBody") or item.get("text") or ""
            if len(_clean_html_fragment(str(body))) < 180:
                continue
            author = item.get("author") or ""
            if isinstance(author, list):
                author = ", ".join(str(x.get("name") or "") for x in author
                                   if isinstance(x, dict))
            elif isinstance(author, dict):
                author = author.get("name") or ""
            metrics = _text_metrics(json.dumps(item, ensure_ascii=False))
            interactions = item.get("interactionStatistic") or []
            if isinstance(interactions, dict):
                interactions = [interactions]
            for stat in interactions if isinstance(interactions, list) else []:
                if not isinstance(stat, dict):
                    continue
                count = _metric_number(stat.get("userInteractionCount"))
                kind = str((stat.get("interactionType") or {}).get("@type")
                           if isinstance(stat.get("interactionType"), dict)
                           else stat.get("interactionType") or "").lower()
                if "view" in kind:
                    metrics["read_count"] = max(metrics["read_count"], count)
                else:
                    metrics["engagement_count"] += count
            return {
                "title": _clean_html_fragment(str(
                    item.get("headline") or item.get("name") or "")),
                "content": _clean_html_fragment(str(body)),
                "author": _clean_html_fragment(str(author)),
                "published_at": str(item.get("datePublished") or ""),
                **metrics,
            }
    return {}


def _html_article(page: str) -> Dict:
    structured = _json_ld_article(page)
    if structured:
        return structured
    title = ""
    for pattern in (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title',
        r'<title[^>]*>([\s\S]*?)</title>',
    ):
        match = re.search(pattern, page or "", flags=re.I)
        if match:
            title = _clean_html_fragment(match.group(1))
            break
    candidates = []
    for pattern in (
        r'<article\b[^>]*>([\s\S]*?)</article>',
        r'<main\b[^>]*>([\s\S]*?)</main>',
        r'<(?:div|section)[^>]+(?:class|id)=["\'][^"\']*'
        r'(?:RichText|article-body|article-content|post-content|story-content|entry-content)'
        r'[^"\']*["\'][^>]*>([\s\S]*?)</(?:div|section)>',
    ):
        for value in re.findall(pattern, page or "", flags=re.I):
            text = _clean_html_fragment(value)
            if len(text) >= 180:
                candidates.append(text)
    content = max(candidates, key=len) if candidates else ""
    return {"title": title, "content": content, "author": "",
            "published_at": "", **_text_metrics(page)}
