"""Year/month routing without relocating existing meeting notes."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from plaud_obsidian import engine
from test_engine import FixtureSource, FixtureTranslator, RID


class DateFolderTests(unittest.TestCase):
    def test_one_time_folder_migration_preserves_names_bytes_mtime_and_is_repeatable(self):
        from rename_notes import migrate
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / 'vault/Meeting Minutes'
            output.mkdir(parents=True)
            settings = {'vault': str(root / 'vault'), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            record = FixtureSource().read_record({'id': RID})
            old = output / 'Existing readable meeting - Transcript.md'
            old.write_bytes(engine.make_note(record, record['artifacts'][0], 'source', 'Original and 한국어 번역'))
            before = old.read_bytes(), old.stat().st_mtime_ns
            personal = output / 'Personal.md'
            personal.write_bytes(b'Unrelated personal note')
            new = output / '2026/01' / old.name
            plan = migrate(settings, folders=True)
            self.assertEqual(plan['planned'], 1)
            self.assertTrue(old.exists())
            result = migrate(settings, apply=True, folders=True)
            self.assertEqual(result['renamed'], 1)
            self.assertTrue(Path(result['backup']).is_file())
            self.assertFalse(old.exists())
            self.assertEqual((new.read_bytes(), new.stat().st_mtime_ns), before)
            self.assertEqual(personal.read_bytes(), b'Unrelated personal note')
            self.assertEqual(engine.target_path(output, RID, 'Transcript', engine.existing_targets(output)), new)
            self.assertEqual(migrate(settings, apply=True, folders=True)['renamed'], 0)

    def test_new_import_repeat_and_existing_flat_notes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            vault = root / 'vault'
            vault.mkdir()
            output = vault / 'Meeting Minutes'
            settings = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            source, translator = FixtureSource(), FixtureTranslator()
            first = engine.sync(settings, source, translator)
            self.assertFalse(first['failed'])
            targets = [Path(p) for p in first['targets']]
            self.assertEqual(len(targets), 3)
            self.assertTrue(all(p.parent == output / '2026' / '01' for p in targets))
            # Simulate previously imported flat notes; no migration or retranslation.
            flat = [p.rename(output / p.name) for p in targets]
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in flat}
            calls = translator.calls
            repeat = engine.sync(settings, source, translator)
            self.assertFalse(repeat['failed'])
            self.assertEqual((repeat['unchanged'], repeat['renamed']), (3, 0))
            self.assertEqual(translator.calls, calls)
            self.assertEqual(len(list(output.rglob('*.md'))), 3)
            self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in flat}, before)

    def test_source_date_formats_and_unknown_date(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            moment = datetime(2025, 12, 31, 23, 30, tzinfo=timezone.utc).timestamp()
            for value, parts in [('2026-01-01T00:30:00+09:00', ('2026', '01')),
                                 ('2025-12-31T23:30:00Z', ('2025', '12')),
                                 (moment, ('2025', '12')), (moment * 1000, ('2025', '12')),
                                 ('invalid', ('날짜 미상',)), (None, ('날짜 미상',))]:
                with self.subTest(value=value):
                    record = {'id': RID, 'title': 'Meeting', 'start_at': value}
                    target = engine.readable_target(output, record, 'Transcript')
                    self.assertEqual(target.parent, output.joinpath(*parts))
            fallback = {'id': RID, 'title': 'Meeting', 'created_at': '2024-02-29T12:00:00'}
            self.assertEqual(engine.readable_target(output, fallback, 'Summary').parent,
                             output / '2024' / '02')


if __name__ == '__main__':
    unittest.main()
