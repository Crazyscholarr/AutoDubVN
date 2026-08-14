"""Kiểm tra luồng làm video kể chuyện: nhạc nền và dựng video từ ảnh.

Chỉ kiểm phần tính toán thuần, không gọi ffmpeg hay mạng, để chạy được ở mọi máy.

Chạy: python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub import nhac_nen as nn
from autodub import slideshow as ss


class GiayPhepNhac(unittest.TestCase):
    def test_chi_nhan_cc0_va_public_domain(self):
        for ok in ("CC0", "Public domain", "PD-old-70", "CC-Zero"):
            self.assertTrue(nn._giay_phep_hop_le(ok), ok)

    def test_tu_choi_giay_phep_doi_ghi_cong_hoac_khong_ro(self):
        for xau in ("CC BY 4.0", "CC BY-SA 3.0", "GFDL", "Fair use", "", "   "):
            self.assertFalse(nn._giay_phep_hop_le(xau), repr(xau))


class LocMauNhacCu(unittest.TestCase):
    def test_bo_mau_nhac_cu_roi(self):
        """Kho nhạc cổ điển lẫn nhiều file chỉ có vài nốt đàn, không làm nền được."""
        for t in ("File:1st violin B 03.wav", "File:2nd violin F 03.wav",
                  "File:cello A 12.flac", "Piano C 04.ogg"):
            self.assertTrue(nn._la_mau_nhac_cu(t), t)

    def test_giu_lai_ban_nhac_that(self):
        for t in ("File:Ambient music test, Yamaha CK61.flac",
                  "File:Bach - Goldberg Variations BWV988 - 01. Aria.mp3",
                  "File:Allegro de Concert Op. 46 in A Major.mp3"):
            self.assertFalse(nn._la_mau_nhac_cu(t), t)


class BoLocTronNhac(unittest.TestCase):
    def test_khong_de_amix_tu_chuan_hoa(self):
        """Thiếu normalize=0 thì giọng đọc tụt mất một nửa chỉ vì thêm nhạc."""
        self.assertIn("normalize=0", nn.build_filter(-20.0, 60.0, True))
        self.assertIn("normalize=0", nn.build_filter(-20.0, 60.0, False))

    def test_co_ducking_thi_phai_nhan_doi_luong_giong(self):
        """Giọng vừa là tiếng chính vừa là tín hiệu điều khiển nên phải asplit."""
        filt = nn.build_filter(-20.0, 60.0, True)
        self.assertIn("asplit=2", filt)
        self.assertIn("sidechaincompress", filt)

    def test_khong_ducking_thi_khong_dung_sidechain(self):
        filt = nn.build_filter(-20.0, 60.0, False)
        self.assertNotIn("sidechaincompress", filt)

    def test_fade_out_dat_dung_cuoi_bai(self):
        filt = nn.build_filter(-20.0, 100.0, False, fade=3.0)
        self.assertIn("afade=t=out:st=97.00:d=3.00", filt)

    def test_tat_fade_thi_khong_chen_afade(self):
        self.assertNotIn("afade", nn.build_filter(-20.0, 60.0, False, fade=0))

    def test_ratio_khong_vuot_nguong_an_toan(self):
        self.assertIn("ratio=20.0", nn.build_filter(-20.0, 60.0, True, duck_ratio=999))
        self.assertIn("ratio=1.0", nn.build_filter(-20.0, 60.0, True, duck_ratio=-5))


class MucAmNhac(unittest.TestCase):
    def test_gioi_han_muc_am_trong_khoang_an_toan(self):
        self.assertEqual(nn._lam_tron_db(-999), nn.DB_FLOOR)
        self.assertEqual(nn._lam_tron_db(50), nn.DB_CEIL)
        self.assertEqual(nn._lam_tron_db(-38), -38)


class ChiaThoiLuongAnh(unittest.TestCase):
    def test_tong_thoi_luong_khop_tuyet_doi(self):
        """Lệch một chút thôi là khung hình cuối bị đen."""
        for so_anh, tong in ((3, 10.0), (4, 20.5), (7, 123.456), (1, 5.0)):
            phan = ss.chia_thoi_luong(so_anh, tong)
            self.assertEqual(len(phan), so_anh)
            self.assertAlmostEqual(sum(phan), tong, places=3)

    def test_moi_anh_deu_co_thoi_gian_duong(self):
        for phan in ss.chia_thoi_luong(9, 10.0):
            self.assertGreater(phan, 0)

    def test_it_anh_truyen_dai_thi_quay_vong(self):
        """3 ảnh cho 10 phút mà không quay vòng thì mỗi tấm đứng im 200 giây."""
        so_canh = ss.so_canh_nen_dung(600, 3)
        self.assertGreater(so_canh, 3)
        self.assertLessEqual(600 / so_canh, ss.MAX_GIAY_MOI_ANH)

    def test_mot_anh_thi_chay_suot_khong_cat_vun(self):
        self.assertEqual(ss.so_canh_nen_dung(900, 1), 1)

    def test_dung_het_anh_nguoi_dung_chon(self):
        self.assertEqual(ss.so_canh_nen_dung(30, 10), 10)

    def test_video_ngan_van_dung_it_nhat_mot_anh(self):
        self.assertEqual(ss.so_canh_nen_dung(2.0, 10), 1)

    def test_khong_de_canh_ngan_hon_muc_toi_thieu(self):
        for tong, co in ((10.0, 50), (5.0, 9), (600.0, 2)):
            so_canh = ss.so_canh_nen_dung(tong, co)
            self.assertGreaterEqual(tong / so_canh, ss.MIN_GIAY_MOI_ANH - 1e-6)


class SapXepAnh(unittest.TestCase):
    def test_anh2_dung_truoc_anh10(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("anh1.png", "anh2.png", "anh10.png", "ghichu.txt"):
                open(os.path.join(d, name), "wb").close()
            ten = [os.path.basename(p) for p in ss.liet_ke_anh([d])]
            self.assertEqual(ten, ["anh1.png", "anh2.png", "anh10.png"])

    def test_bo_qua_file_khong_phai_anh(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "doc.txt"), "wb").close()
            self.assertEqual(ss.liet_ke_anh([d]), [])

    def test_khong_lay_trung_mot_anh_hai_lan(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.png")
            open(p, "wb").close()
            self.assertEqual(len(ss.liet_ke_anh([p, p, d])), 1)


class KhungHinhAnh(unittest.TestCase):
    def test_anh_lech_ti_le_duoc_dat_giua_tren_nen_mo(self):
        """Hai dải đen nhìn rẻ tiền, nên phần thiếu lấp bằng chính ảnh làm nền."""
        filt = ss._bo_loc_khung_hinh(1920, 1080)
        self.assertIn("boxblur", filt)
        self.assertIn("overlay=(W-w)/2:(H-h)/2", filt)
        self.assertIn("force_original_aspect_ratio=decrease", filt)


if __name__ == "__main__":
    unittest.main(verbosity=1)
