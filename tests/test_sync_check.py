"""Kiểm tra khớp giọng Việt với hình trước khi xuất cả phim."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autodub.srt_utils import Segment
from autodub.video.sync_check import (
    check_dub_sync,
    max_start_drift,
    pick_cue_segments,
    verdict_from_metrics,
)


class VerdictKhớpHình(unittest.TestCase):
    def test_ok_khi_lech_sat_khong(self):
        self.assertEqual(verdict_from_metrics(0.0, 0.0, 1.0, 0, 8), "ok")

    def test_fail_khi_lech_qua_5s(self):
        self.assertEqual(verdict_from_metrics(6.0, 0.0, 1.0, 0, 8), "fail")

    def test_fail_khi_track_ngan_hoac_thieu_loi_dung_moc(self):
        self.assertEqual(verdict_from_metrics(0.0, 3.0, 1.0, 0, 8), "fail")
        self.assertEqual(verdict_from_metrics(0.0, 0.0, 0.2, 3, 8), "fail")
        self.assertEqual(verdict_from_metrics(0.0, 0.0, 0.2, 0, 8), "fail")

    def test_duoi_phim_dai_ngan_vai_giay_chi_canh_bao(self):
        self.assertEqual(
            verdict_from_metrics(0.0, 9.57, 0.75, 0, 8, picture_s=10044.2),
            "warn")
        self.assertEqual(
            verdict_from_metrics(0.0, 5730.0, 0.0, 0, 8, picture_s=5850.0),
            "fail")
        self.assertEqual(
            verdict_from_metrics(0.0, 3.0, 1.0, 0, 8, picture_s=10.0),
            "fail")

    def test_khong_fail_early_khi_van_co_loi_dung_moc(self):
        self.assertEqual(verdict_from_metrics(0.0, 0.0, 1.0, 3, 8), "warn")
        self.assertEqual(verdict_from_metrics(0.0, 0.0, 0.8, 7, 8), "warn")

    def test_warn_khi_lech_nhe(self):
        self.assertEqual(verdict_from_metrics(0.5, 0.0, 1.0, 0, 8), "warn")


class CueVaDrift(unittest.TestCase):
    def test_max_start_drift(self):
        a = Segment(1, 10, 12, "một"); a.placed_start = 10.0
        b = Segment(2, 20, 22, "hai"); b.placed_start = 22.0
        self.assertAlmostEqual(max_start_drift([a, b]), 2.0)

    def test_pick_cue_trai_deu(self):
        segs = [Segment(i, i * 10, i * 10 + 2, f"câu {i}") for i in range(20)]
        picked = pick_cue_segments(segs, 5)
        self.assertEqual(len(picked), 5)
        self.assertEqual(picked[0].index, 0)
        self.assertEqual(picked[-1].index, 19)


class CheckKhongFfmpegPreview(unittest.TestCase):
    def test_bao_cao_tu_placement(self):
        segs = [Segment(1, 1, 2, "xin chào")]
        segs[0].placed_start = 1.0
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("autodub.video.sync_check.ffprobe_duration", return_value=10.0), \
                 mock.patch("autodub.video.sync_check.mean_volume_db", return_value=-16.0), \
                 mock.patch("autodub.video.sync_check.export_sync_previews", return_value=[]):
                report = check_dub_sync(
                    "video.mp4", os.path.join(td, "missing.wav"), segs,
                    out_dir=td, duration=10.0, stem="phim", make_previews=False,
                    placements_max_drift=0.0)
            self.assertEqual(report["verdict"], "ok")
            self.assertFalse(report["block_render"])
            self.assertTrue((Path(td) / "phim.kiem_tra_khop.json").is_file())


class GiaoDienKiemTra(unittest.TestCase):
    def test_gui_co_nut_ba_doan_mau(self):
        root = Path(__file__).resolve().parents[1]
        panel = (root / "ui" / "js" / "dub-panel.js").read_text(encoding="utf-8")
        run = (root / "ui" / "js" / "dub-run.js").read_text(encoding="utf-8")
        http = (root / "autodub" / "server" / "http_api.py").read_text(encoding="utf-8")
        self.assertIn("Kiểm tra khớp hình (3 đoạn mẫu)", panel)
        self.assertIn("sync_check", panel)
        self.assertIn("dubSyncCheckHtml", panel)
        self.assertIn("force_export", panel)
        self.assertIn("vietsub_dub.mp4", panel)
        self.assertIn("sync_check", http)
        self.assertIn('j.result_status==="SYNC_CHECK_FAILED"', run)
        self.assertIn("cần kiểm tra khớp hình", run)


if __name__ == "__main__":
    unittest.main()
