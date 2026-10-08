"""Synthetic model boundary responses; no paid inference."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest


class TranslationTests(unittest.TestCase):
    def test_full_translation_schema_cache_and_korean_passthrough(self):
        self.assertIsNotNone(importlib.util.find_spec("plaud_obsidian.translation"), "Translation service missing")
        from plaud_obsidian.translation import Translator
        calls = []
        def complete(**kwargs):
            calls.append(kwargs)
            self.assertIn("Never summarize", kwargs["instructions"])
            incoming = json.loads(kwargs["input"][0]["text"])["units"]
            rows = [{"id": unit["id"], "language": "ko" if "안녕하세요" in unit["text"] else "en",
                     "translation_ko": None if "안녕하세요" in unit["text"] else "서버 25대를 유지하세요."}
                    for unit in incoming]
            return SimpleNamespace(text=json.dumps({"units": rows}, ensure_ascii=False), parsed={"units": rows},
                                   model="synthetic", provider="synthetic")
        with tempfile.TemporaryDirectory() as folder:
            translator = Translator(complete, Path(folder))
            units = [{"id": "u1", "text": "Keep all 25 servers."}, {"id": "u2", "text": "안녕하세요."}]
            result = translator.translate(units)
            self.assertEqual(result[0]["translation_ko"], "서버 25대를 유지하세요.")
            self.assertIsNone(result[1]["translation_ko"])
            again = translator.translate(units)
            self.assertEqual(again, result)
            self.assertEqual(len(calls), 1)


    def test_only_uncertain_unit_marked_without_retry_and_cached(self):
        from plaud_obsidian.translation import Translator
        for incorrect in ['Keep all 25 servers.', '서버를 모두 유지하세요.']:
            calls = []
            def complete(**kwargs):
                units = json.loads(kwargs['input'][0]['text'])['units']
                calls.append([u['id'] for u in units])
                rows = [{'id': u['id'], 'language': 'en',
                         'translation_ko': (incorrect if len(calls) == 1 and u['id'] == '1'
                                            else '서버 25대를 유지하세요.')} for u in units]
                return SimpleNamespace(text=json.dumps({'units': rows}, ensure_ascii=False),
                                       model='synthetic', provider='synthetic')
            with self.subTest(incorrect=incorrect), tempfile.TemporaryDirectory() as folder:
                translator = Translator(complete, folder)
                units = [{'id': 'first', 'text': 'Keep all 25 servers.'},
                         {'id': 'second', 'text': 'Keep all 25 servers.'}]
                result = translator.translate(units)
                self.assertEqual(calls, [['0', '1']])
                self.assertEqual(result[0]['translation_ko'], '서버 25대를 유지하세요.')
                self.assertTrue(result[1]['translation_ko'].endswith('[원문참조]'))
                self.assertEqual(translator.translate(units), result)
                self.assertEqual(len(calls), 1)

    def test_untranslated_unit_marked_and_original_preserved_in_markdown(self):
        from plaud_obsidian.translation import Translator
        from plaud_obsidian.documents import render_transcript, render_appendix
        calls = []
        def complete(**kwargs):
            units = json.loads(kwargs['input'][0]['text'])['units']
            calls.append(units)
            rows = [{'id': u['id'], 'language': 'en', 'translation_ko': u['text']} for u in units]
            return SimpleNamespace(text=json.dumps({'units': rows}), model='synthetic', provider='synthetic')
        with tempfile.TemporaryDirectory() as folder:
            unit = {'id': '0', 'text': 'Keep all 25 servers.', 'timestamp': '00:01', 'speaker': 'Alice'}
            result = Translator(complete, folder).translate([unit])
            self.assertEqual(result[0]['translation_ko'], '[원문참조]')
            self.assertEqual(len(calls), 1)
            for markdown in (render_transcript([unit], result), render_appendix(unit['text'], result)):
                self.assertIn(unit['text'], markdown)
                self.assertIn('[원문참조]', markdown)
            self.assertEqual(len(list((Path(folder) / 'translation-cache').glob('*.json'))), 1)

    def test_wrong_passthrough_marked_but_wrong_id_still_rejected(self):
        from plaud_obsidian.translation import Translator
        def complete(**kwargs):
            units = json.loads(kwargs['input'][0]['text'])['units']
            rows = [{'id': unit['id'], 'language': 'ko', 'translation_ko': None} for unit in units]
            return SimpleNamespace(text=json.dumps({'units': rows}), model='synthetic', provider='synthetic')
        with tempfile.TemporaryDirectory() as folder:
            result = Translator(complete, folder).translate([{'id': '0', 'text': 'Hello world.'}])
            self.assertEqual(result[0]['translation_ko'], '[원문참조]')
        def wrong_id(**kwargs):
            return SimpleNamespace(text=json.dumps({'units': [{'id': 'wrong', 'language': 'en',
                                   'translation_ko': '[원문참조]'}]}), model='synthetic', provider='synthetic')
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, 'TRANSLATION_ID_ORDER_MISMATCH'):
                Translator(wrong_id, folder).translate([{'id': '0', 'text': 'Hello world.'}])


if __name__ == "__main__":
    unittest.main()
