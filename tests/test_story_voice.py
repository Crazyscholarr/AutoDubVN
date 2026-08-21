import unittest

from autodub import story_voice, tts


class PhanTichTruyen(unittest.TestCase):
    def test_nhan_dien_chuyen_gia_dinh_nhieu_doi_thoai(self):
        text = (
            'Bà Sáu ôm con dâu rồi gọi: "Mẹ chờ con về lâu lắm rồi."\n\n'
            '"Con xin lỗi mẹ", chị Hạnh đáp.\n\n'
            'Đứa cháu nép bên bà nội, cả gia đình ngồi lại bên mâm cơm.'
        )
        result = story_voice.analyse_story(text)
        self.assertEqual(result["genre"], "gia_dinh")
        self.assertGreater(result["dialogue_ratio"], 45)
        self.assertEqual(result["character_focus"], "nu")


class DeXuatGiong(unittest.TestCase):
    def test_chuyen_gia_dinh_uu_tien_giong_nu_am_ro(self):
        voices = [
            {"id": "multi_male_felipe_uranus_bigtts", "name": "Giọng Nam Trầm"},
            {"id": "vi_female_huong", "name": "Giọng Nữ Phổ Thông"},
            {"id": "BV075_streaming_robot_dsp", "name": "Robot VN"},
        ]
        text = " ".join(["Bà mẹ ôm con dâu và đứa cháu trong căn nhà gia đình."] * 8)
        result = story_voice.recommend_voices(text, voices, "capcut")
        self.assertEqual(result["recommendations"][0]["id"], "vi_female_huong")
        self.assertNotIn("BV075_streaming_robot_dsp",
                         [x["id"] for x in result["recommendations"]])

    def test_chuyen_bi_an_uu_tien_giong_nam_tram(self):
        voices = [
            {"id": "vi_female_huong", "name": "Giọng Nữ Phổ Thông"},
            {"id": "multi_male_felipe_uranus_bigtts", "name": "Giọng Nam Trầm"},
        ]
        text = " ".join(["Bí mật mất tích và vật chứng che giấu khiến cả xóm nghi ngờ."] * 8)
        result = story_voice.recommend_voices(text, voices, "capcut")
        self.assertEqual(result["analysis"]["genre"], "bi_an")
        self.assertEqual(result["recommendations"][0]["id"],
                         "multi_male_felipe_uranus_bigtts")

    def test_catalog_capcut_co_day_du_nhieu_giong_viet(self):
        voices = tts.list_voices("capcut")
        self.assertGreaterEqual(len(voices), 20)
        self.assertEqual(len(voices), len({v["id"] for v in voices}))
        self.assertFalse(any(v["id"].startswith("vi-") and v["id"].endswith("Neural")
                             for v in voices))


class DanGiongNhanVat(unittest.TestCase):
    DESIGN = """3. NHÂN VẬT

Bà Bảy, 69 tuổi, người vợ hiền nhưng hay lo lắng.

Ông Tám, 72 tuổi, người chồng điềm đạm, giọng trầm.

Con Na, 10 tuổi, đứa cháu gái nhỏ trong nhà.

4. CÂU MỞ ĐẦU

Một buổi sáng cả nhà nghe tiếng gọi ngoài sân.
"""

    def test_doc_tuoi_gioi_tinh_tu_ban_thiet_ke(self):
        chars = story_voice.parse_character_bible(self.DESIGN)
        by_name = {x["name"]: x for x in chars}
        self.assertEqual(by_name["Bà Bảy"]["gender"], "nu")
        self.assertEqual(by_name["Bà Bảy"]["age_group"], "lon_tuoi")
        self.assertEqual(by_name["Con Na"]["gender"], "nu")
        self.assertEqual(by_name["Con Na"]["age_group"], "tre_em")

    def test_loi_ke_giu_narrator_thoai_duoc_gan_dung_nhan_vat(self):
        text = (
            "Trời vừa sáng, căn nhà vẫn còn im lặng.\n\n"
            "Bà Bảy gọi lớn:\n\n"
            '"Ông Tám ơi, ông dậy chưa?"\n\n'
            "Ông Tám chậm rãi đáp:\n\n"
            '"Tui dậy rồi đây."'
        )
        result = story_voice.analyse_dialogue(text, self.DESIGN)
        turns = [x for x in result["utterances"] if x["kind"] == "dialogue"]
        self.assertEqual([x["speaker_name"] for x in turns], ["Bà Bảy", "Ông Tám"])
        self.assertEqual(result["assignment_coverage"], 100.0)
        self.assertEqual(result["utterances"][0]["speaker"], "narrator")

    def test_cau_thoai_mo_ho_dung_giong_ke_thay_vi_doan_bua(self):
        result = story_voice.analyse_dialogue(
            '"Không ai được bước vào căn phòng đó."', self.DESIGN)
        self.assertEqual(result["utterances"][0]["speaker"], "narrator")
        self.assertEqual(result["assignment_coverage"], 0.0)

    def test_tu_chon_giong_theo_tuoi_va_gioi_tinh(self):
        voices = [
            {"id": "nu_am", "name": "Giọng Nữ Trầm"},
            {"id": "nu_sang", "name": "Giọng Nữ Phổ Thông"},
            {"id": "nam_tram", "name": "Giọng Nam Trầm"},
            {"id": "BV074_streaming_dsp", "name": "Giọng Bé"},
        ]
        text = (
            "Bà Bảy nói:\n\n\"Tui sẽ chờ ở nhà.\"\n\n"
            "Ông Tám đáp:\n\n\"Bà cứ yên tâm.\"\n\n"
            "Con Na reo lên:\n\n\"Con cũng ở lại với ngoại.\""
        )
        result = story_voice.plan_story_voices(
            text, voices, engine="capcut", design_text=self.DESIGN,
            narrator_voice="nu_sang", auto_narrator=False)
        cast = {x["character"]: x for x in result["cast"]}
        self.assertEqual(cast["Ông Tám"]["voice_id"], "nam_tram")
        self.assertEqual(cast["Con Na"]["voice_id"], "BV074_streaming_dsp")
        self.assertNotEqual(cast["Bà Bảy"]["voice_id"], result["narrator"]["id"])
        self.assertEqual(len(result["cast"]), len({x["voice_id"] for x in result["cast"]}))

    def test_timeline_giu_thong_tin_nhan_vat(self):
        timeline = tts.build_narration_timeline(
            ["Lời kể.", "Xin chào."], [1.0, 1.5],
            [{"speaker_name": "Người kể", "kind": "narration"},
             {"speaker_name": "Bà Bảy", "kind": "dialogue"}])
        self.assertEqual(timeline[1]["speaker_name"], "Bà Bảy")
        self.assertEqual(timeline[1]["kind"], "dialogue")


if __name__ == "__main__":
    unittest.main()
