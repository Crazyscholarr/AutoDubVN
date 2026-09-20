"""Kiểm thử pipeline kho truyện và workbook sản xuất."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub import content_pipeline
from autodub.content_pipeline import (
    ContentStore, analyze_many, analyze_record, append_content_calendar,
    export_excel, heuristic_analysis, load_json, load_sqlite, normalize_record,
    sample_records, _extract_json, _provider_params,
)
from autodub.server import content_api
from autodub import story_sources
from autodub.server.state import STATE
from autodub.server.manual_api import _save_story_deliverables, _save_youtube_metadata


def submit_inline(target, *, args=(), kwargs=None, **_options):
    target(*tuple(args), **dict(kwargs or {}))
    return "test-job"


class ChuanHoaKhoNguon(unittest.TestCase):
    def test_schema_pixel_acre_brook_comet(self):
        item = normalize_record({
            "id": "zh-1", "source": "zhihu", "sourceUrl": "https://z/1",
            "titleOriginal": "婆媳真实经历", "titleLocalized": "Chuyện mẹ chồng nàng dâu",
            "rawContent": "婆媳矛盾" * 100, "localizedContent": "Mẹ chồng và nàng dâu" * 100,
            "language": "zh", "wordCount": 812, "tags": ["婆媳", "老人"],
        })
        self.assertEqual(item["id"], "zh-1")
        self.assertEqual(item["source_url"], "https://z/1")
        self.assertEqual(item["content"], "Mẹ chồng và nàng dâu" * 100)
        self.assertEqual(item["language"], "zh")
        self.assertEqual(item["word_count"], 812)

    def test_doc_json_va_tu_do_bang_sqlite(self):
        with tempfile.TemporaryDirectory() as td:
            jp = os.path.join(td, "stories.json")
            with open(jp, "w", encoding="utf-8") as handle:
                json.dump({"stories": [{"titleOriginal": "A", "rawContent": "nội dung " * 30}]},
                          handle, ensure_ascii=False)
            self.assertEqual(len(load_json(jp)), 1)

            db = os.path.join(td, "stories.db")
            conn = sqlite3.connect(db)
            try:
                conn.execute("CREATE TABLE stories(id TEXT,titleOriginal TEXT,rawContent TEXT,source TEXT)")
                conn.execute("INSERT INTO stories VALUES(?,?,?,?)",
                             ("s1", "B", "婆媳矛盾" * 50, "zhihu"))
                conn.commit()
            finally:
                conn.close()
            rows = load_sqlite(db)
            self.assertEqual(rows[0]["id"], "s1")
            self.assertEqual(rows[0]["source"], "zhihu")


class PhanTichVaLuu(unittest.TestCase):
    def test_doc_json_gemini_co_markdown_loi_dan_va_dau_phay_thua(self):
        reply = '''Đây là kết quả:\n```json
        {"title_localized":"Chuyện làng quê", "outline":"Dòng 1
Dòng 2", "titles":["A",],}
        ```'''
        parsed = _extract_json(reply)
        self.assertEqual(parsed["title_localized"], "Chuyện làng quê")
        self.assertEqual(parsed["titles"], ["A"])

    def test_cuu_json_gemini_bi_cat_giua_chuoi(self):
        """Bắt đúng lỗi 15:26: object đứt giữa descriptions vẫn lấy được titles."""
        reply = (
            '{"title_localized":"Đêm giỗ","main_hook":"Cửa gõ 3 nhịp",'
            '"titles":["A","B","C","D","E","F","G","H"],'
            '"descriptions":["Cứ đúng đêm giỗ sau vụ hỏa hoạn'
        )
        parsed = _extract_json(reply)
        self.assertEqual(parsed["title_localized"], "Đêm giỗ")
        self.assertEqual(len(parsed["titles"]), 8)
        self.assertEqual(parsed["main_hook"], "Cửa gõ 3 nhịp")

    def test_gemini_sai_json_duoc_yeu_cau_xuat_lai_trong_cung_phien(self):
        source = normalize_record({
            "title": "Chuyện lạ ở ngôi miếu", "language": "vi",
            "content": "Một bí mật trong làng được phát hiện. " * 100,
        })
        payload = {
            "title_localized": "Bí mật dưới gốc đa đầu làng",
            "main_hook": "Đêm nào chiếc chuông cũ cũng tự rung.",
            "titles": [f"Tiêu đề {index}" for index in range(1, 9)],
        }
        ask = mock.Mock(side_effect=[
            "Tôi đã phân tích xong nhưng không thể trình bày dưới dạng yêu cầu.",
            json.dumps(payload, ensure_ascii=False),
        ])
        stages = []
        out = analyze_record(
            source,
            {"content_pipeline": {"browser_json_retries": 1}},
            use_ai=True,
            provider="browser",
            browser_ask=ask,
            progress=lambda stage, message, pct: stages.append(stage),
        )
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(out["title_localized"], payload["title_localized"])
        self.assertEqual(out["analysis_provider"], "browser:Gemini Web")
        self.assertEqual(stages[-1], "done")

    def test_retry_json_don_le_giu_mot_phien_trinh_duyet(self):
        source = normalize_record({
            "title": "Chuyện lạ ở ngôi miếu", "language": "vi",
            "content": "Một bí mật trong làng được phát hiện. " * 100,
        })
        payload = {
            "title_localized": "Bí mật dưới gốc đa đầu làng",
            "main_hook": "Đêm nào chiếc chuông cũ cũng tự rung.",
            "titles": [f"Tiêu đề {index}" for index in range(1, 9)],
        }
        ask = mock.Mock(side_effect=[
            "Tôi đã phân tích xong nhưng không thể trình bày dưới dạng yêu cầu.",
            json.dumps(payload, ensure_ascii=False),
        ])
        session = mock.MagicMock()
        session.__enter__.return_value = ask
        session.__exit__.return_value = False
        with mock.patch.object(content_pipeline, "_browser_analysis_session",
                               return_value=session) as factory:
            out = analyze_record(
                source,
                {"content_pipeline": {"browser_json_retries": 1}},
                use_ai=True, provider="browser")
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(out["title_localized"], payload["title_localized"])

    def test_offline_du_truong_san_xuat(self):
        item = normalize_record({
            "title": "Ba con tranh di chúc của mẹ già",
            "content": ("Mẹ già bị con bất hiếu đuổi khỏi nhà. Một tờ di chúc bí mật "
                        "bất ngờ xuất hiện trong bữa giỗ. ") * 80,
        })
        out = heuristic_analysis(item)
        self.assertEqual(len(out["titles"]), 8)
        self.assertTrue(out["title_localized"])
        self.assertTrue(out["rewrite_brief"])
        self.assertGreaterEqual(len(out["high_tension_scenes"]), 5)
        self.assertEqual(len(out["descriptions"]), 3)
        self.assertEqual(len(out["thumbnails"]), 3)
        self.assertTrue(1 <= out["hook_score"] <= 10)
        self.assertTrue(1 <= out["plot_twist_score"] <= 10)
        self.assertGreater(out["estimated_duration_minutes"], 0)

    def test_offline_nhan_chu_de_tam_linh_tu_giao_mo_dem(self):
        item = normalize_record({
            "title": "Đêm giỗ có tiếng gõ cửa",
            "content": ("Bà kể chuyện tâm linh bên bàn thờ. Đêm ngày giỗ có tiếng gõ cửa "
                        "trong nghĩa địa làng, hương khói bàn thờ vụt tắt. ") * 40,
        })
        out = heuristic_analysis(item)
        self.assertEqual(out["primary_genre"], "Tâm linh ông bà kể")
        self.assertIn("chuyện tâm linh", out["tags"])

    def test_thieu_key_tu_roi_ve_offline_khong_dung_lo(self):
        records = sample_records()[:2]
        results = asyncio.run(analyze_many(
            records, {"translation": {"provider": "zenmux",
                                      "zenmux_api_key": ""}},
            use_ai=True, provider="zenmux", concurrency=2))
        self.assertEqual(len(results), 2)
        self.assertTrue(all(len(x["titles"]) == 8 for x in results))
        self.assertTrue(all("Không có API key" in x["analysis_error"] for x in results))

    def test_ai_viet_hoa_tieu_de_va_chi_tra_ho_so_chat_lieu(self):
        source = normalize_record({
            "title": "婆媳矛盾：老人被赶出家门", "language": "zh",
            "content": "老人和三个孩子发生冲突。秘密在最后被发现。" * 300,
        })
        payload = {
            "title_localized": "Mẹ già bị ba người con đuổi khỏi nhà",
            "main_hook": "Ba người con khóa cửa không cho mẹ trở về.",
            "high_tension_scenes": ["Cảnh %d" % i for i in range(1, 6)],
            "plot_twists": ["Cú lật 1", "Cú lật 2"],
            "must_change": ["tên", "địa danh", "diễn biến", "lời văn"],
            "rewrite_brief": "Hồ sơ tiếng Việt chỉ giữ xung đột và cú lật; viết truyện mới hoàn toàn.",
            "titles": ["Nghe Mà Thấm : BA NGƯỜI CON KHÓA CỬA ĐUỔI MẸ GIÀ | Kể Chuyện Tuổi Già"] * 8,
        }
        cfg = {"translation": {
            "provider": "nvidia", "nvidia_api_key": "test-key",
            "nvidia_model": "z-ai/glm-5.2",
            "nvidia_base_url": "https://integrate.api.nvidia.com/v1",
        }}
        stages = []
        with mock.patch("autodub.translate._api_call",
                        return_value=json.dumps(payload, ensure_ascii=False)):
            out = analyze_record(
                source, cfg, use_ai=True, provider="nvidia",
                progress=lambda stage, message, pct: stages.append((stage, message, pct)))
        self.assertEqual(out["title_localized"], payload["title_localized"])
        self.assertEqual(out["rewrite_brief"], payload["rewrite_brief"])
        self.assertEqual(out["analysis_provider"], "nvidia:z-ai/glm-5.2")
        self.assertEqual(len(out["titles"]), 8)
        self.assertEqual([row[0] for row in stages],
                         ["sending", "waiting", "parsing", "done"])

    def test_loi_ai_co_ten_model_huong_sua_va_callback_error(self):
        source = normalize_record({
            "title": "Bí mật gia đình", "language": "vi",
            "content": "Một bí mật bất ngờ bị phát hiện. " * 100,
        })
        cfg = {"translation": {
            "provider": "nvidia", "nvidia_api_key": "test-key",
            "nvidia_model": "model-thu-nghiem",
        }}
        stages = []
        with mock.patch("autodub.translate._api_call",
                        side_effect=TimeoutError("request timed out")):
            out = analyze_record(
                source, cfg, use_ai=True, provider="nvidia",
                progress=lambda stage, message, pct: stages.append(stage))
        self.assertEqual(out["analysis_provider"], "heuristic-fallback")
        self.assertIn("NVIDIA / model-thu-nghiem lỗi", out["analysis_error"])
        self.assertIn("Cách sửa:", out["analysis_error"])
        self.assertIn("error", stages)

    def test_kho_y_tuong_nvidia_dung_model_nhanh(self):
        name, key, model, _base, _timeout = _provider_params({
            "translation": {
                "nvidia_api_key": "test-key",
                "nvidia_model": "deepseek-ai/deepseek-v4-pro-0813",
                "nvidia_fast_model": "nvidia/nemotron-3.5-lightning-30b-a3b",
            }
        }, "nvidia")
        self.assertEqual(name, "nvidia")
        self.assertEqual(key, "test-key")
        self.assertEqual(model, "nvidia/nemotron-3.5-lightning-30b-a3b")

    def test_kho_y_tuong_nvidia_ep_model_rieng(self):
        _name, _key, model, _base, _timeout = _provider_params({
            "translation": {
                "nvidia_api_key": "test-key",
                "nvidia_model": "deepseek-ai/deepseek-v4-pro-0813",
                "nvidia_fast_model": "nvidia/nemotron-3.5-lightning-30b-a3b",
            },
            "content_pipeline": {"nvidia_model": "moonshotai/kimi-k3"},
        }, "nvidia")
        self.assertEqual(model, "moonshotai/kimi-k3")

    def test_store_giu_chinh_tay_khi_nhap_lai_va_ghi_de_khi_phan_tich_lai(self):
        with tempfile.TemporaryDirectory() as td:
            store = ContentStore(os.path.join(td, "ideas.sqlite"))
            store.upsert([{"id": "a", "title_original": "Gốc", "hook_score": 4,
                           "selected": True, "notes": "đã sửa"}])
            store.upsert([{"id": "a", "title_original": "Nguồn nhập lại", "hook_score": 8}])
            self.assertEqual(store.get("a")["hook_score"], 4)
            self.assertEqual(store.get("a")["notes"], "đã sửa")
            store.upsert([{"id": "a", "hook_score": 9}], overwrite=True)
            self.assertEqual(store.get("a")["hook_score"], 9)
            self.assertTrue(store.get("a")["selected"])
            store.patch("a", {"notes": "sửa lần hai", "hook_score": 7})
            self.assertEqual(store.get("a")["notes"], "sửa lần hai")
            self.assertEqual(store.get("a")["hook_score"], 7)

    def test_lich_su_nhan_cung_link_du_tracking_va_khoa_da_dung(self):
        with tempfile.TemporaryDirectory() as td:
            store = ContentStore(os.path.join(td, "history.sqlite"))
            item = normalize_record({
                "title": "Chuyện đã dùng", "content": "nội dung " * 80,
                "url": "https://www.zhihu.com/question/123?utm_source=x&spm_id_from=abc",
            })
            item.update({"used_at": "2026-08-21T12:00:00", "usage_count": 1,
                         "status": "Đang viết"})
            store.upsert([item])
            history = store.source_history([
                "https://www.zhihu.com/question/123?vd_source=tracking"])
            self.assertEqual(len(history), 1)
            self.assertTrue(next(iter(history.values()))["used"])

    def test_xoa_hang_loat_bao_ve_truyen_da_dung(self):
        with tempfile.TemporaryDirectory() as td:
            store = ContentStore(os.path.join(td, "delete.sqlite"))
            store.upsert([
                {"id": "unused", "title_original": "Chưa dùng"},
                {"id": "used", "title_original": "Đã dùng",
                 "used_at": "2026-08-21T10:00:00", "usage_count": 1},
            ])
            result = store.delete_many(["unused", "used", "missing"])
            self.assertEqual(result["deleted"], ["unused"])
            self.assertEqual(result["protected"], ["used"])
            self.assertEqual(result["not_found"], ["missing"])
            self.assertIsNone(store.get("unused"))
            self.assertIsNotNone(store.get("used"))


class XuatExcel(unittest.TestCase):
    def test_workbook_du_sau_sheet_va_cong_thuc(self):
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as td:
            path = export_excel(sample_records(), os.path.join(td, "plan.xlsx"))
            wb = load_workbook(path, data_only=False)
            self.assertEqual(wb.sheetnames, [
                "Dashboard", "Lịch nội dung", "Kế hoạch sản xuất", "Cụm Series",
                "Dữ liệu gốc", "Cấu hình", "Prompt mẫu"])
            calendar = wb["Lịch nội dung"]
            self.assertEqual([calendar.cell(1, col).value for col in range(1, 10)], [
                "STT", "Ngày tạo", "Thứ", "Ca đăng", "Nhóm chủ đề",
                "Tiêu đề đề xuất", "Thời lượng mục tiêu", "Nguồn kịch bản",
                "Trạng thái"])
            self.assertTrue(str(calendar["C2"].value).startswith("=CHOOSE(WEEKDAY("))
            self.assertIn("|", str(calendar["F2"].value))
            self.assertEqual(calendar["G2"].value, "1h00 - 1h30")
            plan = wb["Kế hoạch sản xuất"]
            self.assertEqual(plan.max_column, 41)
            self.assertTrue(str(plan["L5"].value).startswith("=ROUND("))
            self.assertTrue(str(plan["R5"].value).startswith("=ROUND("))
            self.assertTrue(str(plan["T5"].value).startswith("=IF("))
            self.assertEqual(plan["V4"].value, "Tiêu đề 1")
            self.assertEqual(plan["AC4"].value, "Tiêu đề 8")

    def test_lich_noi_dung_cap_nhat_cung_id_khong_tao_dong_trung(self):
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "lich.xlsx")
            record = {
                "id": "idea-1", "source": "Qidian",
                "primary_genre": "Mẹ chồng nàng dâu",
                "best_publish_time": "20:00", "titles": ["TIÊU ĐỀ GỢI Ý"],
            }
            append_content_calendar(
                record, path, final_title="TIÊU ĐỀ ĐÃ CHỌN",
                video_path=os.path.join(td, "video.mp4"))
            append_content_calendar(
                record, path, final_title="TIÊU ĐỀ ĐÃ SỬA",
                video_path=os.path.join(td, "video.mp4"), status="Đã đăng")
            wb = load_workbook(path, data_only=False)
            sheet = wb["Lịch nội dung"]
            self.assertEqual(sheet.max_row, 2)
            self.assertEqual(sheet["F2"].value, "TIÊU ĐỀ ĐÃ SỬA")
            self.assertEqual(sheet["I2"].value, "Đã đăng")
            self.assertEqual(sheet["J2"].value, "idea-1")
            self.assertTrue(sheet.column_dimensions["J"].hidden)


class ApiEndToEnd(unittest.TestCase):
    def setUp(self):
        self._submit_patch = mock.patch.object(
            content_api, "submit_job", side_effect=submit_inline)
        self._submit_patch.start()

    def tearDown(self):
        self._submit_patch.stop()

    def test_tim_va_tai_nguon_duoc_luu_thang_vao_sqlite(self):
        with tempfile.TemporaryDirectory() as td:
            settings = {
                "config": {"translation": {}, "content_pipeline": {}},
                "database": os.path.join(td, "sources.sqlite"),
                "output_dir": td, "provider": "heuristic", "concurrency": 2,
            }
            found = [{
                "title": "婆媳矛盾真实故事", "url": "https://www.zhihu.com/question/1",
                "source_name": "知乎盐选故事 (Zhihu)", "language": "zh",
                "excerpt": "一段真实经历", "source_key": "zhihu_yanxuan",
            }]
            fetched = {
                "title": "婆媳矛盾真实故事", "rawContent": "婆媳矛盾与老人赡养。" * 120,
                "source": "知乎盐选故事 (Zhihu)",
                "sourceUrl": "https://www.zhihu.com/question/1", "language": "zh",
                "tags": ["婆媳矛盾 故事"], "notes": "Chỉ tham khảo.",
            }
            with mock.patch.object(content_api, "_settings", return_value=settings), \
                    mock.patch.object(story_sources, "search_web_references",
                                      return_value=found), \
                    mock.patch.object(story_sources, "fetch_reference_article",
                                      return_value=fetched):
                STATE["content_pipeline"].update({"working": False, "error": ""})
                response, code = content_api.api_content_search({
                    "keyword": "婆媳矛盾 故事", "source_keys": ["zhihu_yanxuan"],
                    "limit": 5, "topic": "Mẹ chồng nàng dâu"})
                self.assertEqual(code, 200)
                self._wait_done()
                rows = STATE["content_pipeline"]["search_results"]
                self.assertEqual(len(rows), 1)
                response, code = content_api.api_content_download({"items": rows})
                self.assertEqual(code, 200)
                self._wait_done()
                listing, code = content_api.api_content_list({})
                self.assertEqual(code, 200)
                self.assertEqual(listing["count"], 1)
                self.assertTrue(listing["items"][0]["selected"])
                self.assertEqual(STATE["content_pipeline"]["download_success"], 1)
                self.assertEqual(STATE["content_pipeline"]["search_results"][0]
                                 ["download_status"], "Đã tải")
                self.assertEqual(STATE["content_pipeline"]["provider"], "heuristic")
                self.assertEqual(STATE["content_pipeline"]["current_stage"], "done")
                self.assertTrue(STATE["content_pipeline"]["activity"])
                self.assertIn("offline", STATE["content_pipeline"]["status"].lower())

    def test_mau_phan_tich_chon_xuat_va_chuyen_story(self):
        with tempfile.TemporaryDirectory() as td:
            settings = {
                "config": {"translation": {}, "content_pipeline": {}},
                "database": os.path.join(td, "api.sqlite"),
                "output_dir": td, "provider": "heuristic", "concurrency": 2,
            }
            with mock.patch.object(content_api, "_settings", return_value=settings):
                STATE["content_pipeline"].update({"working": False, "error": ""})
                response, code = content_api.api_content_sample({})
                self.assertEqual(code, 200)
                self._wait_done()
                listing, code = content_api.api_content_list({})
                self.assertEqual(code, 200)
                self.assertEqual(listing["count"], 3)
                record_id = listing["items"][0]["id"]
                content_api.api_content_select({"ids": [record_id], "selected": True})
                response, code = content_api.api_content_analyze({
                    "ids": [record_id], "provider": "heuristic", "use_ai": False})
                self.assertEqual(code, 200)
                self._wait_done()
                used, code = content_api.api_content_use_story({"id": record_id})
                self.assertEqual(code, 200)
                self.assertTrue(used["story"]["title"])
                from openpyxl import load_workbook
                calendar = STATE["content_pipeline"].get("calendar_path")
                self.assertTrue(os.path.isfile(calendar))
                calendar_book = load_workbook(calendar, data_only=False)
                calendar_sheet = calendar_book["Lịch nội dung"]
                matching = [row for row in range(2, calendar_sheet.max_row + 1)
                            if calendar_sheet.cell(row, 10).value == record_id]
                self.assertEqual(len(matching), 1)
                self.assertEqual(calendar_sheet.cell(matching[0], 6).value,
                                 used["story"]["title"])
                self.assertEqual(calendar_sheet.cell(matching[0], 9).value,
                                 "Đang viết")
                blocked, code = content_api.api_content_use_story({"id": record_id})
                self.assertEqual(code, 409)
                self.assertIn("lịch sử", blocked["error"])
                self.assertTrue(content_api._store().get(record_id)["used_at"])
                response, code = content_api.api_content_export({"ids": [record_id]})
                self.assertEqual(code, 200)
                self._wait_done(timeout=10)
                self.assertTrue(os.path.isfile(STATE["content_pipeline"]["export_xlsx"]))
                self.assertTrue(os.path.isfile(STATE["content_pipeline"]["export_json"]))

    def test_tai_lai_phan_tich_va_xoa_muc_trong_hang_doi(self):
        with tempfile.TemporaryDirectory() as td:
            settings = {
                "config": {"translation": {}, "content_pipeline": {}},
                "database": os.path.join(td, "reload.sqlite"),
                "output_dir": td, "provider": "heuristic", "concurrency": 2,
            }
            store = ContentStore(settings["database"])
            store.upsert([normalize_record({
                "id": "reload-1", "title": "Bản cũ",
                "content": "Nội dung cũ. " * 100,
                "source": "Zhihu",
                "source_url": "https://www.zhihu.com/question/reload-1",
            })])
            fetched = {
                "title": "Bản nguồn mới", "rawContent": "Nội dung mới gay cấn. " * 120,
                "source": "Zhihu", "sourceUrl":
                "https://www.zhihu.com/question/reload-1", "language": "zh",
            }
            with mock.patch.object(content_api, "_settings", return_value=settings), \
                    mock.patch.object(story_sources, "fetch_reference_article",
                                      return_value=fetched) as fetch_reference:
                STATE["content_pipeline"].update({"working": False, "error": ""})
                response, code = content_api.api_content_reload({
                    "ids": ["reload-1"], "provider": "heuristic"})
                self.assertEqual(code, 200)
                self.assertEqual(response["total"], 1)
                self._wait_done()
                updated = ContentStore(settings["database"]).get("reload-1")
                self.assertIn("Nội dung mới", updated["content"])
                self.assertTrue(updated["reloaded_at"])
                self.assertEqual(STATE["content_pipeline"]["reload_success"], 1)
                self.assertTrue(fetch_reference.call_args.kwargs["force_refresh"])

                response, code = content_api.api_content_delete({"ids": ["reload-1"]})
                self.assertEqual(code, 200)
                self.assertEqual(response["deleted"], ["reload-1"])
                self.assertIsNone(ContentStore(settings["database"]).get("reload-1"))

    def _wait_done(self, timeout: float = 5.0) -> None:
        end = time.time() + timeout
        while STATE["content_pipeline"].get("working") and time.time() < end:
            time.sleep(.02)
        self.assertFalse(STATE["content_pipeline"].get("working"),
                         STATE["content_pipeline"].get("status"))
        self.assertFalse(STATE["content_pipeline"].get("error"),
                         STATE["content_pipeline"].get("error"))

    def test_metadata_youtube_di_cung_video_render(self):
        with tempfile.TemporaryDirectory() as td:
            video = os.path.join(td, "video.mp4")
            open(video, "wb").close()
            path = _save_youtube_metadata(video, {
                "name": "Tiêu đề đã duyệt", "youtube_description": "Mô tả",
                "youtube_tags": ["tuổi già", "gia đình"],
                "content_outline": "Ba phần", "content_idea_id": "idea-1",
            })
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["video_title"], "Tiêu đề đã duyệt")
            self.assertEqual(payload["youtube_tags"], ["tuổi già", "gia đình"])

    def test_render_xong_tu_ghi_tieu_de_da_chon_vao_lich_noi_dung(self):
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as td:
            database = os.path.join(td, "ideas.sqlite")
            calendar = os.path.join(td, "lich_noi_dung.xlsx")
            video = os.path.join(td, "video.mp4")
            open(video, "wb").close()
            store = ContentStore(database)
            store.upsert([{
                "id": "idea-final", "source": "Qidian",
                "primary_genre": "Mẹ chồng nàng dâu",
                "best_publish_time": "20:00",
                "titles": ["TIÊU ĐỀ GỢI Ý KHÔNG ĐƯỢC GHI"],
            }])
            cfg = {"content_pipeline": {
                "database": database, "output_dir": td,
                "calendar_file": calendar,
                "target_duration": "1h00 - 1h30",
            }, "dang_youtube": {
                "auto_thumbnail": False, "auto_description": False,
            }}
            with mock.patch("autodub.server.manual_api._load_cfg", return_value=cfg):
                metadata, saved_calendar = _save_story_deliverables(video, {
                    "name": "TIÊU ĐỀ CUỐI ĐÃ CHỌN",
                    "content_idea_id": "idea-final",
                    "youtube_description": "Mô tả đã duyệt",
                    "auto_youtube_thumbnail": False,
                    "auto_youtube_description": False,
                })
            self.assertEqual(saved_calendar, calendar)
            self.assertTrue(os.path.isfile(metadata))
            wb = load_workbook(calendar, data_only=False)
            sheet = wb["Lịch nội dung"]
            self.assertEqual(sheet["F2"].value, "TIÊU ĐỀ CUỐI ĐÃ CHỌN")
            self.assertEqual(sheet["G2"].value, "1h00 - 1h30")
            self.assertEqual(sheet["H2"].value, "Qidian")
            self.assertEqual(sheet["I2"].value, "Đã xuất video")
            self.assertEqual(ContentStore(database).get("idea-final")["status"],
                             "rendered")


if __name__ == "__main__":
    unittest.main(verbosity=2)
