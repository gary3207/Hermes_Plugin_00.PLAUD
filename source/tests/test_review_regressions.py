"""Offline regressions for independent review findings; no account/model calls."""
from pathlib import Path
import tempfile
import unittest
from plaud_obsidian import engine, provider, translation
from test_engine import FixtureSource, FixtureTranslator, RID


class ReviewTests(unittest.TestCase):
    def test_source_links_and_paths_preserved_but_transport_metadata_excluded(self):
        body = 'Read https://example.com/docs and C:\\notes\\file.md.'
        self.assertEqual(provider.safe_text(body), body)
        meta = provider.metadata({'id': RID, 'name': body, 'presigned_url': 'private'})
        self.assertEqual(meta['title'], body)
        self.assertNotIn('presigned_url', meta)

    def test_filename_alone_does_not_authorize_legacy_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / 'vault'
            output = vault / 'Meeting Minutes'
            output.mkdir(parents=True)
            target = output / f'2025-01-01_12-00-00-{RID}-Transcript.md'
            target.write_bytes(b'Unrelated personal note')
            report = engine.sync({'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')},
                                 FixtureSource(), FixtureTranslator())
            self.assertEqual(target.read_bytes(), b'Unrelated personal note')
            self.assertEqual(report['failed'][0]['error'], 'UNRELATED_DESTINATION_EXISTS')

    def test_destination_appearing_during_translation_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / 'vault'
            vault.mkdir()
            target = vault / 'Meeting Minutes' / f'{RID}-Transcript.md'
            class ConcurrentTranslator(FixtureTranslator):
                def translate(self, units):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b'Concurrent unrelated note')
                    return super().translate(units)
            report = engine.sync({'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')},
                                 FixtureSource(), ConcurrentTranslator())
            self.assertEqual(target.read_bytes(), b'Concurrent unrelated note')
            self.assertEqual(report['failed'][0]['error'], 'DESTINATION_CHANGED_DURING_IMPORT')

    def test_mixed_language_cannot_pass_as_korean_without_translation(self):
        unit = {'id': '0', 'text': '안녕하세요. Never remove this English paragraph.'}
        with self.assertRaisesRegex(RuntimeError, 'SOURCE_LANGUAGE_MISMATCH'):
            translation.validate([{'id': '0', 'language': 'ko', 'translation_ko': None}], [unit])
        translation.validate([{'id': '0', 'language': 'ko', 'translation_ko': None}],
                             [{'id': '0', 'text': '안녕하세요. 모두 유지하세요.'}])

    def test_policy_change_invalidates_notes_and_missing_render_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / 'vault'
            vault.mkdir()
            settings = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            translator = FixtureTranslator()
            translator.cache_identity = 'review-v1'
            engine.sync(settings, FixtureSource(), translator)
            (vault / 'Meeting Minutes' / f'{RID}-Transcript.md').unlink()
            translator.cache_identity = 'review-v2'
            before = translator.calls
            report = engine.sync(settings, FixtureSource(), translator)
            self.assertEqual(report['unchanged'], 0)
            self.assertEqual(translator.calls - before, 3)


if __name__ == '__main__':
    unittest.main()
