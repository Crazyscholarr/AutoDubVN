"""Kiểm tra BẢN ĐỒ THOẠI - phần chống "voice trượt khỏi hình".

Bệnh cũ: mọi chỗ chia một dòng phụ đề thành nhiều dòng đều chia thời gian theo
TỈ LỆ SỐ KÝ TỰ trên cả ô, nên ranh giới rơi vào giữa quãng lặng và dòng sau phát
sớm/muộn vài giây so với miệng nhân vật. Bản đồ thoại giữ mốc thời gian THẬT của
từng ký tự ASR nghe được để chia đúng chỗ.

Chạy: python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub import asr, speechmap, srt_utils
from autodub.srt_utils import Segment


def moc(start: float, so_luong: int, buoc: float = 0.2):
    """Tạo `so_luong` mốc ký tự liền nhau bắt đầu từ `start`."""
    return [(start + i * buoc, start + (i + 1) * buoc) for i in range(so_luong)]


class MocCoBan(unittest.TestCase):
    def setUp(self):
        speechmap.clear_active()

    tearDown = setUp

    def test_gom_moc_thanh_cum_noi(self):
        m = speechmap.SpeechMap(moc(1.0, 5) + moc(9.0, 5))
        self.assertEqual(m.bursts_in(0.0, 20.0), [(1.0, 2.0), (9.0, 10.0)])

    def test_moc_lon_xon_va_hong_van_dung_duoc(self):
        m = speechmap.SpeechMap([(2.0, 1.5), (1.0, 1.2), None, ("x", "y"),
                                 (1.0, 1.2)])
        self.assertEqual(len(m), 2)              # bỏ rác, bỏ trùng
        self.assertEqual(m.marks[0], (1.0, 1.2))

    def test_onset_chi_hut_trong_pham_vi_cho_phep(self):
        m = speechmap.SpeechMap(moc(5.0, 6))
        self.assertAlmostEqual(m.onset(5.2, tol=0.35), 5.0)
        self.assertIsNone(m.onset(9.0, tol=0.35))

    def test_luu_va_doc_lai_khong_mat_moc(self):
        m = speechmap.SpeechMap(moc(1.0, 4))
        with tempfile.TemporaryDirectory() as td:
            p = speechmap.default_path(td, "phim")
            self.assertTrue(m.save(p))
            lai = speechmap.SpeechMap.load(p)
            self.assertEqual(lai.marks, m.marks)
        self.assertIsNone(speechmap.SpeechMap.load(
            os.path.join(tempfile.gettempdir(), "khong_ton_tai_123.json")))


class ChiaTheoMocThat(unittest.TestCase):
    def setUp(self):
        speechmap.clear_active()

    tearDown = setUp

    def test_ranh_gioi_nhay_ve_dung_cho_co_tieng(self):
        """Ô 1-11s nhưng chỉ nói ở 1-3s và 9-11s: chia đôi phải ra mốc 9s."""
        m = speechmap.SpeechMap(moc(1.0, 10) + moc(9.0, 10))
        bounds = m.slice_window(1.0, 11.0, [10, 10])
        self.assertIsNotNone(bounds)
        self.assertAlmostEqual(bounds[0][0], 1.0)
        self.assertAlmostEqual(bounds[1][0], 9.0, places=2)
        # cách chia theo tỉ lệ sẽ ra 6.0 - lệch 3 giây so với lúc có tiếng
        self.assertGreater(abs(6.0 - bounds[1][0]), 2.5)

    def test_moc_qua_thua_thi_tra_none_de_lui_ve_chia_ti_le(self):
        m = speechmap.SpeechMap(moc(1.0, 3))
        self.assertIsNone(m.slice_window(1.0, 12.0, [5, 5]))

    def test_luon_tang_dan_va_khong_tran_khoi_o_goc(self):
        m = speechmap.SpeechMap(moc(0.0, 30))
        bounds = m.slice_window(0.0, 6.0, [1, 1, 1, 1])
        self.assertEqual(len(bounds), 4)
        self.assertAlmostEqual(bounds[0][0], 0.0)
        self.assertAlmostEqual(bounds[-1][1], 6.0)
        for (a1, b1), (a2, _) in zip(bounds, bounds[1:]):
            self.assertLessEqual(a1, a2)
            self.assertLessEqual(b1, a2 + 1e-6)

    def test_mot_phan_thi_giu_nguyen_o(self):
        m = speechmap.SpeechMap(moc(0.0, 10))
        self.assertEqual(m.slice_window(0.0, 2.0, [7]), [(0.0, 2.0)])

    def test_cong_tac_moi_truong_tat_ban_do(self):
        speechmap.set_active(speechmap.SpeechMap(moc(1.0, 20)))
        os.environ["AUTODUB_TAT_BAN_DO_THOAI"] = "1"
        try:
            self.assertIsNone(speechmap.get_active())
            self.assertIsNone(speechmap.slice_window(1.0, 5.0, [1, 1]))
        finally:
            os.environ.pop("AUTODUB_TAT_BAN_DO_THOAI", None)
        self.assertIsNotNone(speechmap.get_active())


class ApVaoCacBuocChiaPhuDe(unittest.TestCase):
    """Ba chỗ từng chia theo tỉ lệ: tách theo dấu câu, polish, và split_long."""

    def setUp(self):
        speechmap.clear_active()
        # Nói ở 1-3s rồi im tới 9s, nói tiếp 9-11s.
        self.map = speechmap.SpeechMap(moc(1.0, 10) + moc(9.0, 10))

    tearDown = lambda self: speechmap.clear_active()  # noqa: E731

    def test_tach_theo_dau_cau_bam_moc_that(self):
        seg = Segment(1, 1.0, 11.0, "Câu đầu ở đây. Câu sau ở đây.")
        cu = srt_utils.split_segment_on_punctuation(seg)
        speechmap.set_active(self.map)
        moi = srt_utils.split_segment_on_punctuation(seg)
        self.assertEqual(len(cu), len(moi), 2)
        self.assertGreater(abs(cu[1].start - 9.0), 2.0)     # cách cũ lệch
        self.assertAlmostEqual(moi[1].start, 9.0, places=2)  # cách mới đúng

    def test_polish_bam_moc_that(self):
        segs = [Segment(1, 1.0, 11.0,
                        "Một câu tiếng Việt khá dài để buộc phải chia. "
                        "Và một câu nữa cũng dài tương tự như vậy.")]
        cu = srt_utils.polish_translated_segments(list(segs), max_chars=48)
        speechmap.set_active(self.map)
        moi = srt_utils.polish_translated_segments(list(segs), max_chars=48)
        self.assertGreater(len(moi), 1)
        self.assertEqual(len(cu), len(moi))
        self.assertAlmostEqual(moi[1].start, 9.0, places=2)
        self.assertGreater(abs(cu[1].start - moi[1].start), 1.5)

    def test_split_long_cua_asr_bam_moc_that(self):
        text = "第一句话在这里。" + "第二句话也在这里。"
        cu = asr.split_long(text, 1.0, 11.0, max_chars=10)
        speechmap.set_active(self.map)
        moi = asr.split_long(text, 1.0, 11.0, max_chars=10)
        self.assertEqual(len(cu), len(moi), 2)
        self.assertAlmostEqual(moi[1].start, 9.0, places=2)

    def test_khong_co_ban_do_thi_giu_hanh_vi_cu(self):
        seg = Segment(1, 0.0, 10.0, "Câu một. Câu hai.")
        a = srt_utils.split_segment_on_punctuation(seg)
        b = srt_utils.split_segment_on_punctuation(seg)
        self.assertEqual([(x.start, x.end) for x in a],
                         [(x.start, x.end) for x in b])


class NeoLaiBanDichCu(unittest.TestCase):
    """File .vi.srt cũ có chữ tốt (đã sửa tay) nhưng mốc thời gian chia theo tỉ
    lệ nên đang lệch: neo lại mốc, giữ nguyên chữ."""

    def setUp(self):
        speechmap.set_active(speechmap.SpeechMap(moc(1.0, 10) + moc(9.0, 10)))

    tearDown = lambda self: speechmap.clear_active()  # noqa: E731

    def test_neo_lai_moc_ma_khong_doi_chu(self):
        src = [Segment(1, 1.0, 11.0, "câu gốc")]
        vi = [Segment(1, 1.0, 6.0, "Phần đầu tiên."),
              Segment(2, 6.0, 11.0, "Phần thứ hai.")]
        doi = srt_utils.reanchor_translated_segments(vi, src)
        self.assertGreater(doi, 0)
        self.assertAlmostEqual(vi[1].start, 9.0, places=2)
        self.assertEqual(vi[1].text, "Phần thứ hai.")
        self.assertIsNone(vi[1].placed_start)

    def test_khong_co_ban_do_thi_khong_doi_gi(self):
        speechmap.clear_active()
        src = [Segment(1, 1.0, 11.0, "câu gốc")]
        vi = [Segment(1, 1.0, 6.0, "A."), Segment(2, 6.0, 11.0, "B.")]
        self.assertEqual(srt_utils.reanchor_translated_segments(vi, src), 0)
        self.assertEqual(vi[1].start, 6.0)

    def test_mot_dong_moi_o_thi_giu_nguyen(self):
        src = [Segment(1, 1.0, 3.0, "a"), Segment(2, 9.0, 11.0, "b")]
        vi = [Segment(1, 1.0, 3.0, "A."), Segment(2, 9.0, 11.0, "B.")]
        self.assertEqual(srt_utils.reanchor_translated_segments(vi, src), 0)


class GiuCauGocKhiChiaLaiTrongApp(unittest.TestCase):
    """Regression: bước polish của GUI từng ghi src="" cho mọi dòng, làm bảng
    'Sửa từng dòng' mất hết tiếng Trung và bấm Dịch lại thì không còn gì để dịch."""

    def test_src_giu_o_dong_con_dau_tien(self):
        from autodub.server.projects import _src_theo_moc
        rows = [{"start": 0.0, "end": 5.0, "src": "第一句"},
                {"start": 5.0, "end": 9.0, "src": "第二句"}]
        polished = [Segment(1, 0.0, 2.5, "Dòng 1a"),
                    Segment(2, 2.5, 5.0, "Dòng 1b"),
                    Segment(3, 5.0, 9.0, "Dòng 2")]
        self.assertEqual(_src_theo_moc(rows, polished),
                         ["第一句", "", "第二句"])

    def test_polish_cua_gui_khong_xoa_src(self):
        from autodub.server.projects import _polish_project_vi
        speechmap.clear_active()
        pr = {"segments": [
            {"start": 0.0, "end": 10.0, "src": "原文",
             "vi": "Một câu tiếng Việt khá dài để buộc phải chia. "
                   "Và một câu nữa cũng dài tương tự như vậy."}]}
        _polish_project_vi(pr, {"polish_subtitles": True, "polish_max_chars": 48})
        self.assertGreater(len(pr["segments"]), 1)
        self.assertEqual(pr["segments"][0]["src"], "原文")


class MocTuKetQuaFunASR(unittest.TestCase):
    def test_doc_moc_ky_tu_tu_output_funasr(self):
        res = [{"key": "a", "text": "xin chào",
                "timestamp": [[50, 170], [170, 350], [350, 530]],
                "sentence_info": [
                    {"text": "xin chào", "start": 50, "end": 530,
                     "timestamp": [[50, 170], [170, 350], [350, 530]]}]}]
        marks = asr._marks_from_funasr(res)
        self.assertTrue(marks)
        self.assertAlmostEqual(marks[0][0], 0.05, places=3)
        self.assertAlmostEqual(marks[-1][1], 0.53, places=3)
        # mốc trùng từ sentence_info không được nhân đôi
        self.assertEqual(len(speechmap.SpeechMap(marks)), 3)

    def test_moc_it_hon_hai_thi_bo_qua(self):
        self.assertEqual(asr._marks_from_funasr([{"timestamp": [[0, 100]]}]), [])


class ThamSoProvider(unittest.TestCase):
    """Regression: chạy dòng lệnh với provider nvidia từng gửi key/model của
    Gemini tới endpoint NVIDIA và chết với HTTP 404."""

    def test_nvidia_lay_dung_key_va_model(self):
        from autodub import translate
        tr = {"gemini_api_key": "GEM", "gemini_model": "gemini-x",
              "nvidia_api_key": "nvapi-1", "nvidia_model": "z-ai/glm-5.2",
              "nvidia_base_url": "https://integrate.api.nvidia.com/v1",
              "nvidia_timeout": 300}
        key, model, base, timeout = translate.api_params_for_provider(tr, "nvidia")
        self.assertEqual(key, "nvapi-1")
        self.assertEqual(model, "z-ai/glm-5.2")
        self.assertIn("integrate.api.nvidia.com", base)
        self.assertEqual(timeout, 300)

    def test_gui_va_cli_dung_chung_mot_bang(self):
        from autodub import translate
        from autodub.server.config_api import _translation_api_params
        tr = {"inferx_api_key": "ix", "inferx_model": "deepseek-v4-flash"}
        for provider in ("nvidia", "inferx", "tokenrouter",
                         "tokenrouter_gemini", "gemini", "khong_biet"):
            self.assertEqual(translate.api_params_for_provider(tr, provider),
                             _translation_api_params(tr, provider), provider)


if __name__ == "__main__":
    unittest.main(verbosity=1)
