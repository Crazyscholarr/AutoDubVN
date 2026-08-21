import json
import os
import sys
import tempfile
import textwrap
import unittest

from autodub import story_writer


class StoryWriterBridge(unittest.TestCase):
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
                a = p.parse_args()
                folder = os.path.join(os.path.dirname(__file__), "out")
                os.makedirs(folder, exist_ok=True)
                script = os.path.join(folder, "KICH_BAN_DOC.txt")
                with open(script, "w", encoding="utf-8") as f:
                    f.write("Ngày xưa có một câu chuyện.")
                with open(a.result_json, "w", encoding="utf-8") as f:
                    json.dump({"results": [{"title": a.title[0], "folder": folder,
                                             "words": 6, "meta": {}}]}, f)
                print(">>> Hoàn tất (1/1)")
            ''').strip()
            with open(fake_run, "w", encoding="utf-8") as f:
                f.write(source)
            cfg = {"tao_kich_ban": {
                "tool_dir": tmp, "python": sys.executable, "timeout_minutes": 10}}
            progress = []
            result = story_writer.generate(
                "Tiêu đề thử", cfg,
                progress=lambda done, total, msg: progress.append((done, total)))
            self.assertTrue(result["script_path"].endswith("KICH_BAN_DOC.txt"))
            self.assertEqual(result["words"], 6)
            self.assertEqual(progress, [(1, 1)])

    def test_bao_ro_khi_thieu_cong_cu(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(story_writer.StoryWriterError, "Không thấy công cụ"):
                story_writer.generate(
                    "Tiêu đề", {"tao_kich_ban": {"tool_dir": tmp,
                                                   "python": sys.executable}})


if __name__ == "__main__":
    unittest.main()
