"""Giữ nhịp SRT Trung: 1 câu gốc = 1 câu Việt, không gộp rồi chia lại."""
import unittest
from pathlib import Path

from autodub.srt_utils import keep_source_timing
from autodub.server.projects import (
    _polish_project_vi,
    _prepare_project_src_for_translation,
)


class KeepSourceTiming(unittest.TestCase):
    def test_mac_dinh_bat(self):
        self.assertTrue(keep_source_timing())
        self.assertTrue(keep_source_timing({}))
        self.assertTrue(keep_source_timing({"merge_source_fragments": True}))

    def test_tat_ro(self):
        self.assertFalse(keep_source_timing({"keep_source_timing": False}))

    def test_prepare_khong_gop_khi_giu_nhip(self):
        pr = {"segments": [
            {"start": 0.0, "end": 1.0, "src": "他，们快不行了。", "vi": ""},
            {"start": 1.0, "end": 2.0, "src": "个妇女多嘴。", "vi": ""},
        ]}
        delta = _prepare_project_src_for_translation(
            pr, {"keep_source_timing": True, "merge_source_fragments": True,
                 "split_on_punctuation": True})
        self.assertEqual(delta, 0)
        self.assertEqual(len(pr["segments"]), 2)

    def test_polish_khong_chia_lai_khi_giu_nhip(self):
        pr = {"segments": [
            {"start": 0.0, "end": 2.0, "src": "你好。", "vi": "Xin chào bạn."},
            {"start": 2.0, "end": 4.0, "src": "我来了。", "vi": "Tôi đến đây."},
        ]}
        self.assertIsNone(_polish_project_vi(
            pr, {"keep_source_timing": True, "polish_subtitles": True}))
        self.assertEqual(len(pr["segments"]), 2)

    def test_gui_va_config_co_tuy_chon(self):
        root = Path(__file__).resolve().parents[1]
        panel = (root / "ui" / "js" / "dub-panel.js").read_text(encoding="utf-8")
        cfg = (root / "config.example.yaml").read_text(encoding="utf-8")
        pipe = (root / "autodub" / "server" / "pipeline.py").read_text(encoding="utf-8")
        self.assertIn("keep_source_timing", panel)
        self.assertIn("1 câu Trung = 1 câu Việt", panel)
        self.assertIn("keep_source_timing: true", cfg)
        self.assertIn("caption_style: screen", cfg)
        self.assertIn("force_export", pipe)
        self.assertNotIn("skip_full", pipe)


class GiuMetadataSemantic(unittest.TestCase):
    def test_segments_from_project_giu_nhom_cau(self):
        from autodub.server.projects import _segments_from_project, _finalize_project_vi
        from autodub.vi_cues import finalize_spoken_vi_cues
        pr = {"segments": [
            {"start": 0.0, "end": 1.2, "src": "因为我",
             "vi": "Vì tôi 迟迟 không về.", "semantic_group": "0:s1",
             "allowed_source_names": []},
            {"start": 1.3, "end": 2.5, "src": "回来了",
             "vi": "đã về nhà.", "semantic_group": "0:s1",
             "allowed_source_names": []},
        ]}
        segs = _segments_from_project(pr)
        self.assertEqual(segs[0].semantic_group, "0:s1")
        self.assertEqual(segs[1].semantic_group, "0:s1")
        self.assertEqual(finalize_spoken_vi_cues(segs, {"vi_beautify": False}), 1)
        self.assertEqual(segs[0].text, "Vì tôi mãi không về.")
        self.assertEqual(segs[1].text, "đã về nhà.")
        n = _finalize_project_vi(pr, {"vi_beautify": False, "keep_source_timing": True})
        self.assertGreaterEqual(n, 1)
        self.assertEqual(pr["segments"][0]["vi"], "Vì tôi mãi không về.")
        self.assertEqual(pr["segments"][0]["start"], 0.0)
        self.assertEqual(pr["segments"][1]["start"], 1.3)
        self.assertEqual(len(pr["segments"]), 2)

    def test_thieu_semantic_group_thi_finalize_moi_chia_lai(self):
        from autodub.vi_cues import finalize_spoken_vi_cues
        from autodub.srt_utils import Segment
        segs = [
            Segment(1, 0.0, 1.2, "Phải noi gương Lý Bác Khởi, biết"),
            Segment(2, 1.3, 2.5, "chưa hả?"),
        ]
        n = finalize_spoken_vi_cues(segs, {"vi_beautify": False})
        self.assertGreater(n, 0)
        self.assertFalse(segs[0].text.endswith(" biết"))


if __name__ == "__main__":
    unittest.main()
