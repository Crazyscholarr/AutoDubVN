import os
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autodub import story_images
from autodub.server import manual_api


class StoryImagePackTests(unittest.TestCase):
    def _image(self, folder, name, payload):
        path = Path(folder) / name
        path.write_bytes(payload)
        return str(path)

    def test_pack_luu_prompt_va_khoi_phuc_anh_dung_thu_tu(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._image(tmp, "anh10.png", b"ten")
            b = self._image(tmp, "anh2.jpg", b"two")
            pack = story_images.create_pack(
                "Chuyện nhà", master_prompt="Lập cảnh", scene_prompts=["Prompt cảnh một đủ dài để kiểm tra.",
                                                                        "Prompt cảnh hai đủ dài để kiểm tra."],
                image_paths=[a, b], scene_count=2, root=tmp)
            images = story_images.resolve_images(pack["manifest_path"])
            self.assertEqual([Path(x).name for x in images], ["scene_001.png", "scene_002.jpg"])
            self.assertEqual(Path(images[0]).read_bytes(), b"ten")
            self.assertEqual(Path(images[1]).read_bytes(), b"two")
            self.assertTrue(Path(pack["prompt_file"]).is_file())
            self.assertIn("scene_001.png", Path(pack["prompt_file"]).read_text(encoding="utf-8"))
            browser_prompt = story_images.public_summary(pack, include_prompt=True)["prompt_text"]
            self.assertIn("Bạn đang ở chế độ tạo ảnh", browser_prompt)
            self.assertIn("Prompt cảnh một đủ dài", browser_prompt)
            self.assertNotIn("PROMPT TỔNG", browser_prompt)

    def test_dao_anh_trong_chinh_pack_khong_ghi_de_nguon(self):
        with tempfile.TemporaryDirectory() as tmp:
            one = self._image(tmp, "one.png", b"one")
            two = self._image(tmp, "two.png", b"two")
            pack = story_images.create_pack("Đảo cảnh", image_paths=[one, two],
                                            scene_count=2, root=tmp)
            old = story_images.resolve_images(pack["manifest_path"])
            story_images.attach_images(pack["manifest_path"], [old[1], old[0]])
            new = story_images.resolve_images(pack["manifest_path"])
            self.assertEqual(Path(new[0]).read_bytes(), b"two")
            self.assertEqual(Path(new[1]).read_bytes(), b"one")

    def test_xoa_het_khong_tu_nap_lai_file_cu_con_tren_dia(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self._image(tmp, "old.png", b"old")
            pack = story_images.create_pack(
                "Xoá ảnh", image_paths=[image], scene_count=1, root=tmp)
            old = story_images.resolve_images(pack["manifest_path"])
            self.assertEqual(len(old), 1)

            cleared = story_images.attach_images(pack["manifest_path"], [])
            self.assertEqual(cleared["ready_count"], 0)
            self.assertEqual(cleared["scenes"][0]["status"], "missing")
            # Giữ file vật lý để thao tác có thể phục hồi, nhưng không được
            # cho nó xuất hiện lại trong danh sách hay lúc dựng video.
            self.assertTrue(Path(old[0]).is_file())
            self.assertEqual(story_images.resolve_images(
                pack["manifest_path"]), [])

    def test_anh_da_xoa_khong_xuat_hien_lai_khi_xep_theo_chuong(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self._image(tmp, "old.png", b"old")
            pack = story_images.create_pack(
                "Xoá khỏi lịch", image_paths=[image], scene_count=1, root=tmp)
            story_images.attach_images(pack["manifest_path"], [])
            self.assertEqual(story_images.expand_for_chapters(
                pack["manifest_path"], [100], total_duration=60), [])

    def test_api_run_all_nhan_image_pack_khi_khong_co_anh_truc_tiep(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self._image(tmp, "scene.png", b"png")
            pack = story_images.create_pack("API", image_paths=[image],
                                            scene_count=1, root=tmp)
            payload = {"image_pack": pack["manifest_path"]}
            self.assertEqual(len(manual_api._story_image_inputs(payload)), 1)

    def test_tieu_de_moi_khong_duoc_muon_anh_cua_truyen_cu(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_image = self._image(tmp, "old.png", b"old story")
            pack = story_images.create_pack(
                "Truyện cũ", image_paths=[old_image], scene_count=1, root=tmp)
            payload, images, old_title = manual_api._generated_story_image_inputs({
                "story_title": "Truyện mới",
                "auto_images": True,
                "image_pack": pack["manifest_path"],
                "anh": story_images.resolve_images(pack["manifest_path"]),
            }, "Truyện mới")
            self.assertEqual(images, [])
            self.assertEqual(payload["anh"], [])
            self.assertEqual(payload["image_pack"], "")
            self.assertEqual(old_title, "Truyện cũ")

    def test_cung_tieu_de_duoc_tiep_tuc_goi_anh_dang_do(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self._image(tmp, "scene.png", b"same story")
            pack = story_images.create_pack(
                "Chuyện Bà Bảy", image_paths=[image], scene_count=2, root=tmp,
                scene_prompts=["Prompt cảnh một đủ dài để tạo ảnh.",
                               "Prompt cảnh hai đủ dài để tạo ảnh."])
            payload, images, old_title = manual_api._generated_story_image_inputs({
                "auto_images": True,
                "image_pack": pack["manifest_path"],
                "anh": story_images.resolve_images(pack["manifest_path"]),
            }, "chuyện bà bảy")
            self.assertEqual(images, [])
            self.assertEqual(payload["anh"], [])
            self.assertEqual(payload["image_pack"], pack["manifest_path"])
            self.assertEqual(old_title, "")

    def test_cung_tieu_de_du_anh_moi_duoc_chuyen_sang_dung_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self._image(tmp, "scene.png", b"complete story")
            pack = story_images.create_pack(
                "Chuyện hoàn tất", image_paths=[image], scene_count=1, root=tmp)
            payload, images, old_title = manual_api._generated_story_image_inputs({
                "auto_images": True, "image_pack": pack["manifest_path"],
            }, "chuyện hoàn tất")
            self.assertEqual(len(images), 1)
            self.assertEqual(old_title, "")

    def test_tat_tu_tao_anh_thi_cho_phep_dung_bo_anh_da_chon(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self._image(tmp, "manual.png", b"manual")
            pack = story_images.create_pack(
                "Bộ ảnh chọn tay", image_paths=[image], scene_count=1, root=tmp)
            payload, images, old_title = manual_api._generated_story_image_inputs({
                "auto_images": False,
                "image_pack": pack["manifest_path"],
            }, "Một truyện khác")
            self.assertEqual(len(images), 1)
            self.assertEqual(payload["image_pack"], pack["manifest_path"])
            self.assertEqual(old_title, "")

    def test_api_khong_mo_prompt_pack_khac_tieu_de(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = story_images.create_pack(
                "Truyện đang dở", master_prompt="Prompt cũ",
                scene_count=1, root=tmp)
            result, code = manual_api.api_story_image_pack({
                "manifest_path": pack["manifest_path"],
                "expected_title": "Truyện hoàn toàn mới",
                "include_prompt": True,
            })
            self.assertEqual(code, 409)
            self.assertIn("Truyện đang dở", result["error"])

    def test_parse_prompt_danh_so_cua_gemini(self):
        raw = "**1.** A sufficiently detailed first cinematic scene.\n2) A sufficiently detailed second cinematic scene.\nNEGATIVE: no text"
        self.assertEqual(len(story_images.parse_scene_prompts(raw)), 2)

    def test_che_do_browser_khong_muon_nham_api_key_dich_cu(self):
        cfg = {
            "translation": {"provider": "gemini", "gemini_api_key": "old-key"},
            "tao_anh": {"provider": "browser"},
        }
        _image_cfg, provider, api_key, _model = \
            manual_api._story_image_generation_config(cfg)
        self.assertEqual(provider, "browser")
        self.assertEqual(api_key, "")
        with mock.patch("autodub.translate._api_call") as api_call, \
                mock.patch.object(
                    story_images, "generate_scene_prompts_gemini_browser",
                    return_value=[]) as browser_call:
            prompts = story_images.generate_scene_prompts(
                "Master prompt đủ dài", cfg, expected_count=14)
        self.assertEqual(prompts, [])
        api_call.assert_not_called()
        browser_call.assert_called_once()

    def test_cau_hinh_browser_anh_dung_chung_profile_phan_dich(self):
        cfg = {
            "translation": {
                "browser_profile": "profile-dung-chung",
                "browser_channel": "chrome",
            },
            "tao_anh": {"provider": "browser", "wait_image_seconds": 321},
        }
        settings = story_images.gemini_browser_settings(cfg)
        self.assertTrue(settings["profile_dir"].endswith("profile-dung-chung"))
        self.assertEqual(settings["channel"], "chrome")
        self.assertEqual(settings["url"], story_images.GEMINI_WEB_URL)
        self.assertEqual(settings["timeout"], 321)

    def test_mac_dinh_anh_web_khong_con_cho_240_giay_ba_luot(self):
        settings = story_images.gemini_browser_settings({"tao_anh": {
            "provider": "browser"}})
        self.assertEqual(settings["timeout"], 90)
        self.assertEqual(settings["retries"], 2)

    def test_prepare_luot_sau_dung_lai_manifest_va_khong_rut_prompt_lai(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self._image(tmp, "scene.png", b"existing" * 200)
            script = Path(tmp) / "KICH_BAN_DOC.txt"
            script.write_text("Ngày xưa có một câu chuyện.", encoding="utf-8")
            pack = story_images.create_pack(
                "Chuyện tiếp tục", image_paths=[image], scene_count=2,
                scene_prompts=["Prompt cảnh một đủ dài để tạo ảnh.",
                               "Prompt cảnh hai đủ dài để tạo ảnh."],
                script_path=str(script), root=tmp)
            result = {"title": "Chuyện tiếp tục", "script_path": str(script),
                      "design_path": ""}
            payload = {"story_title": "Chuyện tiếp tục", "auto_images": True,
                       "image_pack": pack["manifest_path"]}
            cfg = {"tao_anh": {"provider": "browser",
                                "wait_image_seconds": 45,
                                "browser_retries": 1}}
            with mock.patch.object(story_images, "generate_scene_prompts") as prompts, \
                 mock.patch.object(
                     story_images, "generate_images_gemini_browser",
                     side_effect=story_images.GeminiBrowserError("tạm dừng")) as resume:
                images, used_pack = manual_api._prepare_generated_story_images(
                    result, payload, cfg)
            self.assertEqual(images, [])
            self.assertEqual(used_pack, pack["manifest_path"])
            prompts.assert_not_called()
            self.assertEqual(resume.call_args.args[0], pack["manifest_path"])

    def test_gemini_browser_tai_tung_canh_va_cap_nhat_manifest(self):
        class FakeSession:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def _sleep(self, _seconds):
                pass

            def generate_scene(self, index, _prompt, images_dir, _aspect,
                               heartbeat=None):
                images_dir.mkdir(parents=True, exist_ok=True)
                path = images_dir / ("scene_%03d.png" % index)
                path.write_bytes(("image-%d" % index).encode() * 300)
                return path

        with tempfile.TemporaryDirectory() as tmp:
            pack = story_images.create_pack(
                "Browser", scene_prompts=[
                    "Prompt cảnh một đủ dài để Gemini tạo ảnh minh họa.",
                    "Prompt cảnh hai đủ dài để Gemini tạo ảnh minh họa.",
                ], scene_count=2, root=tmp)
            updates = []
            with mock.patch.object(story_images, "_GeminiWebImageSession",
                                   FakeSession):
                result = story_images.generate_images_gemini_browser(
                    pack["manifest_path"], str(Path(tmp) / "profile"),
                    request_gap=0,
                    progress=lambda done, total, message: updates.append(
                        (done, total, message)))
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["ready_count"], 2)
            self.assertEqual(len(story_images.resolve_images(
                pack["manifest_path"])), 2)
            self.assertEqual([x[:2] for x in updates], [(1, 2), (2, 2)])

    def test_loi_luot_dau_thi_mo_chat_sach_va_thu_lai(self):
        class RecoveringSession:
            recovered = 0
            calls = 0

            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def _sleep(self, _seconds):
                pass

            def recover_after_failure(self, _index, _reason):
                type(self).recovered += 1

            def generate_scene(self, index, _prompt, images_dir, _aspect,
                               heartbeat=None):
                type(self).calls += 1
                if type(self).calls == 1:
                    raise story_images.GeminiBrowserError("kẹt lượt đầu")
                images_dir.mkdir(parents=True, exist_ok=True)
                path = images_dir / ("scene_%03d.png" % index)
                path.write_bytes(b"image" * 300)
                return path

        with tempfile.TemporaryDirectory() as tmp:
            pack = story_images.create_pack(
                "Phục hồi", scene_prompts=["Prompt cảnh đủ dài để tạo ảnh."],
                scene_count=1, root=tmp)
            with mock.patch.object(story_images, "_GeminiWebImageSession",
                                   RecoveringSession):
                result = story_images.generate_images_gemini_browser(
                    pack["manifest_path"], str(Path(tmp) / "profile"),
                    max_retries=2, request_gap=0)
            self.assertEqual(result["ready_count"], 1)
            self.assertEqual(RecoveringSession.recovered, 1)
            self.assertEqual(RecoveringSession.calls, 2)

    def test_lap_anh_theo_tung_chuong_khong_quay_vong_xuyen_truyen(self):
        with tempfile.TemporaryDirectory() as tmp:
            sources = [self._image(tmp, f"c{i}.png", str(i).encode())
                       for i in range(1, 7)]
            pack = story_images.create_pack("Sáu chương", image_paths=sources,
                                            scene_count=6, root=tmp)
            planned = story_images.expand_for_chapters(
                pack["manifest_path"], [1, 2, 3, 4, 5, 6],
                total_duration=525.0, max_seconds=25.0)
            self.assertEqual(len(planned), 21)
            payloads = [Path(path).read_bytes() for path in planned]
            # Mỗi chương nằm thành một khối liên tục; ảnh chương 1 không quay
            # lại sau khi đã chuyển sang chương 2.
            self.assertEqual(payloads, sorted(payloads))

    def test_doc_anh_inline_tu_gemini_interactions_api(self):
        raw = b"jpeg" * 400

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"steps": [{
                    "type": "model_output",
                    "content": [{"type": "image", "mime_type": "image/jpeg",
                                 "data": base64.b64encode(raw).decode("ascii")}],
                }]}).encode("utf-8")

        with mock.patch.object(story_images.urllib.request, "urlopen",
                               return_value=Response()) as urlopen:
            got, mime = story_images._gemini_image_request(
                "A detailed cinematic scene", "secret", "image-model", "16:9")
        self.assertEqual(got, raw)
        self.assertEqual(mime, "image/jpeg")
        request = urlopen.call_args.args[0]
        sent = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent["response_format"]["aspect_ratio"], "16:9")
        self.assertNotIn("secret", request.full_url)

    def test_tao_anh_gemini_luu_tung_canh_vao_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = story_images.create_pack(
                "Tự tạo ảnh", scene_prompts=["Prompt cảnh đủ dài để tạo ảnh."],
                scene_count=1, root=tmp)
            with mock.patch.object(
                    story_images, "_gemini_image_request",
                    return_value=(b"image" * 300, "image/jpeg")):
                result = story_images.generate_images_gemini(
                    pack["manifest_path"], "secret", request_gap=0)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["ready_count"], 1)
            images = story_images.resolve_images(pack["manifest_path"])
            self.assertEqual(len(images), 1)
            self.assertTrue(images[0].endswith("scene_001.jpg"))


if __name__ == "__main__":
    unittest.main()
