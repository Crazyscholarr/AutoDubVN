"""Cờ NVENC cho file xuất: nén tử tế, tua được — không dùng chế độ livestream."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub.utils import nvenc_encode_args, nvenc_gop_frames


class NvencEncodeArgs(unittest.TestCase):
    def test_khong_dung_che_do_livestream(self):
        args = nvenc_encode_args(20, fps=24)
        self.assertEqual(args[args.index("-preset") + 1], "p4")
        self.assertEqual(args[args.index("-tune") + 1], "hq")
        self.assertNotIn("p1", args)
        self.assertNotIn("ll", args)
        self.assertIn("-g", args)
        self.assertIn("-bf", args)
        self.assertEqual(int(args[args.index("-g") + 1]), 48)

    def test_gop_theo_fps(self):
        self.assertEqual(nvenc_gop_frames(30), 60)
        self.assertEqual(nvenc_gop_frames(24), 48)
        self.assertEqual(nvenc_gop_frames(None), 48)
        args = nvenc_encode_args(20, fps=30)
        self.assertEqual(int(args[args.index("-g") + 1]), 60)

    def test_hw_full_khong_pix_fmt(self):
        args = nvenc_encode_args(20, pix_fmt=None)
        self.assertNotIn("-pix_fmt", args)
        self.assertIn("-profile:v", args)

    def test_tran_bitrate_1080p(self):
        args = nvenc_encode_args(20, fps=30, width=1920, height=1080)
        self.assertEqual(int(args[args.index("-maxrate") + 1]), 8_000_000)
        self.assertEqual(int(args[args.index("-bufsize") + 1]), 16_000_000)

    def test_khong_tran_khi_chua_biet_khung_hinh(self):
        args = nvenc_encode_args(20, fps=24)
        self.assertNotIn("-maxrate", args)
