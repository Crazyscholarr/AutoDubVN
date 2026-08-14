"""Kiểm tra bộ prompt VIẾT KỊCH BẢN TỪ TIÊU ĐỀ (4A -> 4B x6 -> 4C).

Không gọi mạng: dùng một hàm hỏi-đáp giả lập để chạy trọn quy trình.

Chạy: python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub import kich_ban as kb

THIET_KE_MAU = """1. CÂU HỎI CỐT LÕI
Vì sao con dâu bỏ đi giữa đêm?

8. SÁU CHƯƠNG
Chương 1: Cái nồi cơm nguội - 2.000 từ
   Ba dòng nội dung xảy ra trong chương.
**Chương 2: Lời hứa trước sân** (2.000 từ)
Chương 3: Đêm mưa đầu tháng — 2.000 từ
Chương 4: Tờ giấy trong tủ thờ - 2.000 từ
Chương 5: Cái hộp thiếc - 2.200 từ
Chương 6: Sáng hôm sau - 1.800 từ

9. MƯỜI CHI TIẾT VẬT THỂ
Hộp thuốc huyết áp, chai dầu gió xanh, nền gạch bông.
"""


class DocBanThietKe(unittest.TestCase):
    def test_doc_du_sau_chuong_moi_kieu_dinh_dang(self):
        chs = kb.doc_danh_sach_chuong(THIET_KE_MAU)
        self.assertEqual([c.so for c in chs], [1, 2, 3, 4, 5, 6])
        self.assertEqual(chs[0].ten, "Cái nồi cơm nguội")
        self.assertEqual(chs[1].ten, "Lời hứa trước sân")   # bỏ dấu in đậm
        self.assertEqual(chs[2].ten, "Đêm mưa đầu tháng")   # dấu gạch dài
        self.assertEqual([c.so_tu for c in chs],
                         [2000, 2000, 2000, 2000, 2200, 1800])

    def test_thiet_ke_hong_thi_dung_phan_bo_mac_dinh(self):
        chs = kb.doc_danh_sach_chuong("model tra ve linh tinh")
        self.assertEqual(len(chs), 6)
        self.assertEqual(sum(c.so_tu for c in chs), 12000)

    def test_doc_duoc_khi_model_tra_ve_chuoi_json_mot_dong(self):
        """Model thật (glm-5.2) trả cả bản thiết kế trong một chuỗi, ký tự xuống
        dòng viết thành hai ký tự \\n, và nội dung chương nằm ngay sau tên."""
        thiet_ke = (
            '"8. SÁU CHƯƠNG\\nChương 1: Những ngày ốm đau. Bà Bảy nằm liệt '
            'giường, con dâu Sáu về quê chăm mẹ chồng. Số từ mục tiêu: 2.000 từ.'
            '\\nChương 2: Những giọt nước mắt giấu kín. Sáu chăm bà ngày đêm. '
            'Số từ mục tiêu: 2.000 từ.\\nChương 5: Tờ giấy cũ trong gầm giường. '
            'Bà Bảy gọi Sáu vào. Số từ mục tiêu: 2.200 từ."')
        chs = kb.doc_danh_sach_chuong(thiet_ke)
        self.assertEqual([c.so for c in chs], [1, 2, 5])
        self.assertEqual(chs[0].ten, "Những ngày ốm đau")
        self.assertEqual(chs[2].ten, "Tờ giấy cũ trong gầm giường")
        self.assertEqual([c.so_tu for c in chs], [2000, 2000, 2200])

    def test_khong_nham_muc_khac_thanh_ten_chuong(self):
        chs = kb.doc_danh_sach_chuong(
            "Chương 1: Tên thật - 2.000 từ\nChương 1: trùng số\n")
        self.assertEqual(len(chs), 1)


class DemTuVaMocThoiGian(unittest.TestCase):
    def test_khong_tinh_dong_danh_dau_so_tu(self):
        self.assertEqual(kb.dem_tu("Một hai ba bốn năm.\n[Số từ: 5]"), 5)

    def test_bo_ky_hieu_markdown(self):
        self.assertEqual(kb.dem_tu("**Ông Tám** nói: *hổng có gì*"), 6)

    def test_moc_thoi_gian_theo_135_tu_moi_phut(self):
        moc = kb.moc_thoi_gian([2000, 2000, 2000, 2000, 2200, 1800])
        self.assertEqual(moc[0], "00:00")
        self.assertEqual(moc[1], "14:49")          # 2000/135 phút
        self.assertEqual(len(moc), 6)

    def test_duoi_200_tu_lay_dung_phan_cuoi(self):
        van = " ".join(str(i) for i in range(1, 501))
        duoi = kb.duoi_200_tu(van)
        self.assertEqual(len(duoi.split()), 200)
        self.assertTrue(duoi.endswith("500"))


class NoiDungPrompt(unittest.TestCase):
    def test_4a_co_du_9_muc_va_tieu_de(self):
        p = kb.prompt_thiet_ke("Mẹ chồng giấu sổ đỏ 20 năm")
        self.assertIn("Mẹ chồng giấu sổ đỏ 20 năm", p)
        for muc in ("1. CÂU HỎI CỐT LÕI", "5. BA NHỊP LEO THANG",
                    "6. CÚ LẬT MẶT", "8. SÁU CHƯƠNG",
                    "9. MƯỜI CHI TIẾT VẬT THỂ"):
            self.assertIn(muc, p)

    def test_4b_ep_so_tu_va_liet_ke_tu_cam(self):
        p = kb.prompt_chuong(THIET_KE_MAU, 5, "Cái hộp thiếc", 2200, "…đuôi cũ")
        self.assertIn("Độ dài bắt buộc: 2200 từ", p)
        self.assertIn("…đuôi cũ", p)
        for tu in ("tổng tài", "trọng sinh", "thiên kim"):
            self.assertIn(tu, p)

    def test_4b_chuong_1_ghi_ro_chua_co_duoi(self):
        p = kb.prompt_chuong(THIET_KE_MAU, 1, "Cái nồi cơm nguội", 2000, "")
        self.assertIn("chưa có", p)

    def test_4c_dung_dung_toc_do_doc(self):
        p = kb.prompt_kiem_tra("nội dung truyện", tu_moi_phut=150)
        self.assertIn("150 từ mỗi phút", p)

    def test_prompt_anh_ep_phong_cach_va_cam(self):
        p = kb.prompt_anh(THIET_KE_MAU, so_canh=12, kho="9:16")
        self.assertIn("12 mô tả ảnh", p)
        self.assertIn("aspect ratio 9:16", p)
        self.assertIn(kb.NEO_PHONG_CACH_ANH, p)
        self.assertIn("no close-up faces", p)


class ChayTronQuyTrinh(unittest.TestCase):
    def _ask_gia_lap(self, so_tu_moi_chuong=2100, ngan_lan_dau=False):
        state = {"n": 0, "da_ngan": False}

        def ask(prompt: str) -> str:
            state["n"] += 1
            if "Chỉ lập bản thiết kế" in prompt:
                return THIET_KE_MAU
            if prompt.startswith("Chương ") and "thiếu" in prompt:
                return "bù " + " ".join(["từ"] * so_tu_moi_chuong)
            if "VIẾT CHƯƠNG" in prompt:
                if ngan_lan_dau and not state["da_ngan"]:
                    state["da_ngan"] = True
                    return "ngắn " + " ".join(["từ"] * 300)
                return "Tên chương\n" + " ".join(["từ"] * so_tu_moi_chuong) \
                    + "\n[Số từ: 2100]"
            if "Hãy kiểm tra theo 10 tiêu chí" in prompt:
                return "1. ĐẠT ..."
            if "mô tả ảnh" in prompt:
                return ("1. A wide shot of the village at dawn with a bamboo "
                        "bed near the canal, warm light\n"
                        "2. A medium shot over the shoulder of an old woman "
                        "holding a green medicine box at dusk\n"
                        "NEGATIVE: no text")
            return ""
        return ask, state

    def test_chay_du_sau_chuong_va_gom_kich_ban(self):
        ask, state = self._ask_gia_lap()
        kq = kb.viet_truyen("Tiêu đề thử", ask)
        self.assertEqual(len(kq.chuong), 6)
        self.assertEqual(kq.ten_chuong[4], "Cái hộp thiếc")
        self.assertGreater(kq.tong_tu, 12000)
        self.assertGreater(kq.phut_doc, 80)
        self.assertEqual(len(kq.canh_anh), 2)
        self.assertTrue(kq.kiem_tra)
        # 1 lần 4A + 6 lần 4B + 1 lần 4C + 1 lần prompt ảnh
        self.assertEqual(state["n"], 9)

    def test_chuong_qua_ngan_thi_tu_nhac_viet_bu(self):
        ask, state = self._ask_gia_lap(ngan_lan_dau=True)
        kq = kb.viet_truyen("Tiêu đề thử", ask, lam_kiem_tra=False,
                            lam_prompt_anh=False)
        self.assertEqual(len(kq.chuong), 6)
        self.assertGreater(kq.so_tu[0], 1700)     # đã viết bù
        self.assertEqual(state["n"], 1 + 6 + 1)   # thêm 1 lượt nhắc bù

    def test_thieu_thiet_ke_thi_dung_ngay(self):
        with self.assertRaises(RuntimeError):
            kb.viet_truyen("Tiêu đề", lambda _p: "")

    def test_bao_cao_canh_bao_khi_thieu_tu_va_co_tu_cam(self):
        def ask(prompt: str) -> str:
            if "Chỉ lập bản thiết kế" in prompt:
                return THIET_KE_MAU
            if "VIẾT CHƯƠNG" in prompt:
                return "Chương ngắn có tổng tài trong đó " + \
                    " ".join(["từ"] * 100)
            return ""
        kq = kb.viet_truyen("Tiêu đề", ask, lam_kiem_tra=False,
                            lam_prompt_anh=False, so_lan_bu=0)
        bc = kb.bao_cao(kq)
        self.assertIn("CẢNH BÁO", bc)
        self.assertIn("tổng tài", bc)


class LocVanChuong(unittest.TestCase):
    def test_bo_loi_dan_va_dong_dem_tu(self):
        van = kb.loc_van_chuong(
            "Chắc chắn rồi, đây là chương 1:\nÔng Tám ngồi đó.\n[Số từ: 4]")
        self.assertNotIn("Chắc chắn rồi", van)
        self.assertNotIn("Số từ", van)
        self.assertIn("Ông Tám ngồi đó.", van)


if __name__ == "__main__":
    unittest.main(verbosity=1)
