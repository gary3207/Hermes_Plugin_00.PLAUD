"""Readable filenames and lossless, no-model legacy name migration."""
from pathlib import Path
import tempfile
import unittest
from plaud_obsidian import engine
from test_engine import FixtureSource, FixtureTranslator, RID


class ReadableTitleTests(unittest.TestCase):
    def test_new_names_and_legacy_migration_preserve_bytes_mtime_and_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            vault = root / 'vault'
            vault.mkdir()
            settings = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            source, translator = FixtureSource(), FixtureTranslator()
            first = engine.sync(settings, source, translator)
            self.assertFalse(first['failed'])
            targets = [Path(p) for p in first['targets']]
            for path in targets:
                self.assertTrue(path.name.startswith('2026-01-01 12-00-00 - Synthetic meeting - '))
                self.assertNotIn(RID, path.name)
            original = next(p for p in targets if p.name.endswith('Transcript.md'))
            data, mtime = original.read_bytes(), original.stat().st_mtime_ns
            legacy = original.with_name(f'{RID}-Transcript.md')
            original.rename(legacy)
            calls = translator.calls
            repeat = engine.sync(settings, source, translator)
            self.assertFalse(repeat['failed'])
            self.assertEqual((repeat['unchanged'], repeat['renamed']), (3, 1))
            self.assertEqual(translator.calls, calls)
            self.assertFalse(legacy.exists())
            self.assertEqual((original.read_bytes(), original.stat().st_mtime_ns), (data, mtime))
            self.assertEqual(engine.sync(settings, source, translator)['renamed'], 0)
            original.unlink()
            restored = engine.sync(settings, source, translator)
            self.assertFalse(restored['failed'])
            self.assertEqual(original.read_bytes(), data)
            self.assertEqual(translator.calls, calls)

    def test_renamed_record_updates_filename_without_duplicates(self):
        class RenamedSource(FixtureSource):
            def read_record(self, row):
                return dict(super().read_record(row), title='New meeting title')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'vault').mkdir()
            settings = {'vault': str(root / 'vault'), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            engine.sync(settings, FixtureSource(), FixtureTranslator())
            result = engine.sync(settings, RenamedSource(), FixtureTranslator())
            self.assertFalse(result['failed'])
            files = list((root / 'vault/Meeting Minutes').rglob('*.md'))
            self.assertEqual(len(files), 3)
            self.assertTrue(all('New meeting title' in p.name for p in files))

    def test_invalid_characters_long_titles_and_collisions_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            record = {'id': RID, 'title': '../회의: "검토" / [결정] # ^ ? * | ' + '긴제목' * 200,
                      'start_at': '2026-10-08T10:59:29.703000'}
            target = engine.readable_target(output, record, 'Transcript')
            self.assertEqual(target.parent, output / '2026' / '10')
            self.assertLessEqual(len(target.name.encode('utf-8')), 200)
            self.assertTrue(target.name.startswith('2026-10-08 10-59-29 - '))
            for char in '<>:"/\\|?*[]#^':
                self.assertNotIn(char, target.name)
            target.parent.mkdir(parents=True)
            target.write_bytes(b'Unrelated personal note')
            alternative = engine.readable_target(output, record, 'Transcript')
            self.assertNotEqual(target, alternative)
            self.assertEqual(target.read_bytes(), b'Unrelated personal note')
            alternative.write_bytes(b'Also unrelated')
            with self.assertRaisesRegex(RuntimeError, 'UNRELATED_DESTINATION_EXISTS'):
                engine.readable_target(output, record, 'Transcript')

    def test_multiple_owned_copies_block(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = engine.make_note({'id': RID}, {'key': 'Transcript', 'kind': 'Transcript'}, 'source', 'original')
            for name in ('Human title.md', 'Duplicate.md'):
                (root / name).write_bytes(data)
            index = engine.existing_targets(root)
            with self.assertRaisesRegex(RuntimeError, 'MULTIPLE_EXISTING_TARGETS'):
                engine.target_path(root, RID, 'Transcript', index)

    def test_offline_migration_backs_up_and_preserves_unrelated_notes(self):
        from rename_notes import migrate
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / 'vault/Meeting Minutes'
            output.mkdir(parents=True)
            settings = {'vault': str(root / 'vault'), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            record = FixtureSource().read_record({'id': RID})
            data = engine.make_note(record, record['artifacts'][0], 'source', 'Original and 한국어 번역 [원문참조]')
            legacy = output / f'{RID}-Transcript.md'
            legacy.write_bytes(data)
            (output / 'Personal.md').write_bytes(b'Unrelated note')
            planned = migrate(settings)
            self.assertEqual(planned['planned'], 1)
            self.assertTrue(legacy.exists())
            result = migrate(settings, apply=True)
            self.assertEqual((result['renamed'], result['content_changed'], result['model_calls']), (1, 0, 0))
            self.assertTrue(Path(result['backup']).is_file())
            self.assertFalse(legacy.exists())
            self.assertEqual(Path(result['paths'][0]['new']).read_bytes(), data)
            self.assertEqual((output / 'Personal.md').read_bytes(), b'Unrelated note')
            self.assertEqual(migrate(settings, apply=True)['renamed'], 0)
            named = Path(result['paths'][0]['new'])
            named.write_bytes(named.read_bytes() + b'Edited')
            with self.assertRaisesRegex(RuntimeError, 'MODIFIED_GENERATED_NOTE'):
                migrate(settings, apply=True)


if __name__ == '__main__':
    unittest.main()
