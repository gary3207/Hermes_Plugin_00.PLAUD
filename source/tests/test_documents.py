"""Synthetic documents only; never use private recordings in tests."""
import unittest
from plaud_obsidian.documents import render_transcript


class DocumentTests(unittest.TestCase):
    def test_translation_follows_whole_multiline_speaker_paragraph(self):
        units = [{"id": "0", "speaker": "Alice", "timestamp": "00:01:24",
                  "text": "We need to finish the review.\nDo not change the deployment date."},
                 {"id": "1", "speaker": "Bob", "timestamp": "00:01:31",
                  "text": "네, 확인하겠습니다."}]
        translations = [{"id": "0", "language": "en", "translation_ko":
                         "검토를 마쳐야 합니다.\n배포 날짜는 변경하지 마세요."},
                        {"id": "1", "language": "ko", "translation_ko": None}]
        text = render_transcript(units, translations)
        self.assertIn("Alice", text)
        self.assertIn("00:01:24", text)
        self.assertIn(units[0]["text"], text)
        self.assertLess(text.index(units[0]["text"]), text.index("검토를 마쳐야 합니다."))
        self.assertLess(text.index("배포 날짜는 변경하지 마세요."), text.index("Bob"))
        self.assertEqual(text.count("한국어 번역"), 1)
        self.assertIn(units[1]["text"], text)

    def test_summary_and_highlights_append_complete_translation_not_recap(self):
        from plaud_obsidian import documents
        render = getattr(documents, "render_appendix", None)
        self.assertIsNotNone(render, "Full translation appendix renderer is missing")
        original = "## Decisions\n\n- Keep 25 servers.\n- Do not assign an owner.\n"
        translation = "## 결정사항\n\n- 서버 25대를 유지합니다.\n- 담당자를 지정하지 마세요."
        text = render(original, [{"id": "0", "language": "en", "translation_ko": translation}])
        self.assertTrue(text.startswith(original))
        self.assertTrue(text.rstrip().endswith(translation))
        self.assertIn("## 한국어 전체 번역", text)
        self.assertEqual(render("이미 한국어인 원문\n", [{"id": "0", "language": "ko", "translation_ko": None}]),
                         "이미 한국어인 원문\n")


if __name__ == "__main__":
    unittest.main()
