import json
import hashlib
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from autodub import story_writer


class StoryWriterBridge(unittest.TestCase):
    def test_missing_first_letter_resumes_without_browser(self):
        title = "Nghe Mà Thấm : CON ÉP CHA KÝ GIẤY BÁN ĐẤT NHẬN NGAY BÀI HỌC CAY ĐẮNG | Kể Chuyện Làng Quê"
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "run.py").touch()
            folder = self._saved_story(tmp, "old_ref", title)
            cfg = {"tao_kich_ban": {"tool_dir": tmp, "python": sys.executable}}
            with mock.patch.object(story_writer.subprocess, "Popen") as launch:
                result = story_writer.generate(title[1:], cfg)
            self.assertEqual(result["folder"], str(folder))
            self.assertEqual(result["title"], title)
            launch.assert_not_called()
            self.assertIsNone(story_writer.find_saved_story(title[2:], cfg))

    def _saved_story(self, root, folder, title, words=12000, brief=""):
        directory = Path(root) / "output" / folder
        directory.mkdir(parents=True)
        text = "truyện " * words
        (directory / "KICH_BAN_DOC.txt").write_text(text, encoding="utf-8")
        (directory / "thong_tin.json").write_text(json.dumps({
            "tieu_de": title, "script_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "source_brief_sha256": hashlib.sha256(brief.encode()).hexdigest() if brief else "",
            "kiem_tra_tu_dong": {"dialogue_target": True, "banned_terms": {}}
        }), encoding="utf-8")
        return directory

    def test_restart_uses_valid_old_script_without_launching_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "run.py").touch()
            good = self._saved_story(tmp, "old_ref", "Cùng tiêu đề", brief="chất liệu")
            self._saved_story(tmp, "new", "Cùng tiêu đề", words=8952)
            cfg = {"tao_kich_ban": {"tool_dir": tmp, "python": sys.executable}}
            with mock.patch.object(story_writer.subprocess, "Popen") as launch:
                result = story_writer.generate("cùng tiêu đề", cfg)
            self.assertEqual(result["folder"], str(good))
            launch.assert_not_called()

    def test_saved_script_must_match_brief_and_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = self._saved_story(tmp, "old", "Truyện", brief="A")
            cfg = {"tao_kich_ban": {"tool_dir": tmp}}
            self.assertIsNone(story_writer.find_saved_story("Truyện", cfg, "B"))
            self.assertIsNone(story_writer.find_saved_story("Truyện khác", cfg))
            (folder / "KICH_BAN_DOC.txt").write_text("changed", encoding="utf-8")
            self.assertIsNone(story_writer.find_saved_story("Truyện", cfg, "A"))

    def test_different_saved_material_requires_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._saved_story(tmp, "a", "Truyện", brief="A")
            self._saved_story(tmp, "b", "Truyện", brief="B")
            with self.assertRaisesRegex(story_writer.StoryWriterError, "nhiều bản"):
                story_writer.find_saved_story("Truyện", {"tao_kich_ban": {"tool_dir": tmp}})

    def test_truyen_cau_hinh_cta_toi_cli_va_tu_bo_sung_vi_tri_thu_hai(self):
        cmd = ["python", "run.py"]
        story_writer._append_cta_args(cmd, {
            "enabled": True, "text": "Đây là {channel}.",
            "positions": [25], "speed": 9,
        })
        self.assertIn("--cta-text", cmd)
        self.assertEqual(cmd[cmd.index("--cta-text") + 1], "Đây là {channel}.")
        self.assertEqual(cmd[cmd.index("--cta-positions") + 1], "25,12")
        self.assertEqual(cmd[cmd.index("--cta-speed") + 1], "2.0")

    def test_tat_cta_truyen_dung_co_tat(self):
        cmd = ["python", "run.py"]
        story_writer._append_cta_args(cmd, {"enabled": False})
        self.assertEqual(cmd[-1], "--no-channel-cta")

    def test_nhan_dung_kich_ban_doc_tu_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_run = os.path.join(tmp, "run.py")
            source = textwrap.dedent(r'''
                import argparse, json, os
                p = argparse.ArgumentParser()
                p.add_argument("-t", "--title", action="append")
                p.add_argument("--result-json")
                p.add_argument("--source-brief-file")
                a = p.parse_args()
                folder = os.path.join(os.path.dirname(__file__), "out")
                os.makedirs(folder, exist_ok=True)
                script = os.path.join(folder, "KICH_BAN_DOC.txt")
                with open(script, "w", encoding="utf-8") as f:
                    f.write("Ngày xưa có một câu chuyện.")
                with open(a.result_json, "w", encoding="utf-8") as f:
                    brief = (open(a.source_brief_file, encoding="utf-8").read()
                             if a.source_brief_file else "")
                    json.dump({"results": [{"title": a.title[0], "folder": folder,
                                             "words": 6, "meta": {"brief": brief}}]}, f)
                print(">>> Hoàn tất (1/1)")
            ''').strip()
            with open(fake_run, "w", encoding="utf-8") as f:
                f.write(source)
            cfg = {"tao_kich_ban": {
                "tool_dir": tmp, "python": sys.executable, "timeout_minutes": 10}}
            progress = []
            result = story_writer.generate(
                "Tiêu đề thử", cfg,
                rewrite_brief="Hook mở đầu và cú lật đã Việt hóa.",
                progress=lambda done, total, msg: progress.append((done, total)))
            self.assertTrue(result["script_path"].endswith("KICH_BAN_DOC.txt"))
            self.assertEqual(result["words"], 6)
            self.assertEqual(result["meta"]["brief"],
                             "Hook mở đầu và cú lật đã Việt hóa.")
            self.assertEqual(progress, [(1, 1)])
            # Một phiên mới chỉ nhập tiêu đề vẫn truyền đúng chất liệu đã lưu.
            resumed = story_writer.generate("Tiêu đề thử", cfg)
            self.assertEqual(resumed["meta"]["brief"],
                             "Hook mở đầu và cú lật đã Việt hóa.")

    def test_bao_ro_khi_thieu_cong_cu(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(story_writer.StoryWriterError, "Không thấy công cụ"):
                story_writer.generate(
                    "Tiêu đề", {"tao_kich_ban": {"tool_dir": tmp,
                                                   "python": sys.executable}})


if __name__ == "__main__":
    unittest.main()
