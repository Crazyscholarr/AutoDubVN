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


if __name__ == "__main__":
    unittest.main()
