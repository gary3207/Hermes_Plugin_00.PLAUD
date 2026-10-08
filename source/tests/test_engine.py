"""Offline, synthetic end-to-end import and change-detection checks."""
import importlib.util
import tempfile
from pathlib import Path
import unittest


RID = "of_" + "a" * 32


class FixtureSource:
    def __init__(self):
        self.text = "Keep all 25 servers."

    def list_records(self):
        return [{"id": RID}]

    def read_record(self, row):
        return {"id": RID, "title": "Synthetic meeting", "created_at": "2026-01-01T12:00:00Z",
                "artifacts": [{"key": "Transcript", "kind": "Transcript", "text": self.text,
                               "units": [{"id": "0", "speaker": "Alice", "timestamp": "00:01",
                                          "text": self.text}]},
                              {"key": "Summary-test", "kind": "Summary", "text": self.text},
                              {"key": "Highlights", "kind": "Highlights", "text": self.text}],
                "pending": []}


class FixtureTranslator:
    cache_identity = "fixture-v1"
    def __init__(self):
        self.calls = 0

    def translate(self, units):
        self.calls += 1
        return [{"id": u["id"], "language": "en", "translation_ko": "서버 25대를 모두 유지합니다."}
                for u in units]


class EngineTests(unittest.TestCase):
    def test_all_first_then_only_changed_with_original_and_complete_translation(self):
        self.assertIsNotNone(importlib.util.find_spec("plaud_obsidian.engine"), "Import engine is missing")
        from plaud_obsidian.engine import sync
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            vault.mkdir()
            settings = {"vault": str(vault), "output": "Meeting Minutes", "state": str(root / "state")}
            source, translator = FixtureSource(), FixtureTranslator()
            first = sync(settings, source, translator)
            self.assertEqual(first["created"], 3)
            paths = sorted((vault / "Meeting Minutes").glob("*.md"))
            self.assertEqual(len(paths), 3)
            for path in paths:
                body = path.read_text(encoding="utf-8")
                self.assertIn(source.text, body)
                self.assertIn("서버 25대를 모두 유지합니다.", body)
            calls = translator.calls
            mtimes = [p.stat().st_mtime_ns for p in paths]
            same = sync(settings, source, translator)
            self.assertEqual(same["unchanged"], 3)
            self.assertEqual(translator.calls, calls)
            self.assertEqual([p.stat().st_mtime_ns for p in paths], mtimes)
            source.text = "Keep all 25 servers. Do not change the deployment date."
            changed = sync(settings, source, translator)
            self.assertEqual(changed["updated"], 3)
            self.assertEqual(len(list((vault / "Meeting Minutes").glob("*.md"))), 3)
            for path in paths:
                self.assertIn(source.text, path.read_text(encoding="utf-8"))

    def test_one_changed_artifact_and_missing_file_leave_neighbors_untouched(self):
        from plaud_obsidian.engine import sync
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / 'vault'
            vault.mkdir()
            settings = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            source, translator = FixtureSource(), FixtureTranslator()
            first = sync(settings, source, translator)
            targets = [Path(p) for p in first['targets']]
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in targets}

            class ChangedSummary(FixtureSource):
                def read_record(self, row):
                    record = super().read_record(row)
                    record['artifacts'][1]['text'] += ' Do not remove any servers.'
                    return record

            changed = sync(settings, ChangedSummary(), translator)
            self.assertEqual((changed['updated'], changed['unchanged'], changed['created']), (1, 2, 0))
            self.assertFalse(changed['failed'])
            for target in targets:
                if target.name.endswith(' - Transcript.md') or target.name.endswith(' - Highlights.md'):
                    self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before[target.name])
            missing = next(p for p in targets if p.name.endswith(' - Transcript.md'))
            original = missing.read_bytes()
            missing.unlink()  # Only this test-created file inside the temporary vault.
            calls = translator.calls
            restored = sync(settings, ChangedSummary(), translator)
            self.assertEqual((restored['created'], restored['unchanged']), (1, 2))
            self.assertFalse(restored['failed'])
            self.assertEqual(missing.read_bytes(), original)
            self.assertEqual(translator.calls, calls)

    def test_migrates_owned_legacy_filename_without_creating_duplicate(self):
        from plaud_obsidian.engine import make_note, sync
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            output = vault / "Meeting Minutes" / "2025"
            output.mkdir(parents=True)
            legacy = output / f"2025-01-01_12-00-00-{RID}-Transcript.md"
            legacy.write_bytes(make_note({"id": RID}, {"key": "Transcript", "kind": "Transcript"},
                                         "old-source-hash", "Old provider-derived transcript"))
            settings = {"vault": str(vault), "output": "Meeting Minutes", "state": str(root / "state")}
            result = sync(settings, FixtureSource(), FixtureTranslator())
            self.assertEqual(result["updated"], 1)
            self.assertEqual(result['renamed'], 1)
            self.assertFalse(legacy.exists())
            readable = output / '2026-01-01 12-00-00 - Synthetic meeting - Transcript.md'
            self.assertIn("Keep all 25 servers.", readable.read_text(encoding="utf-8"))
            self.assertFalse((vault / "Meeting Minutes" / f"{RID}-Transcript.md").exists())


if __name__ == "__main__":
    unittest.main()
