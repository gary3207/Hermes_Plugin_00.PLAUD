"""Synthetic end-to-end Summary media checks; never use real auth or Vault."""
import base64
import tempfile
from pathlib import Path
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from types import SimpleNamespace

from plaud_obsidian import engine
from test_engine import FixtureSource, FixtureTranslator, RID

CARD = 'c_' + 'b' * 32
REFERENCE = '[](plaud://image?type=summaryCard&id=' + CARD + ')'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5E3ioAAAAASUVORK5CYII=')


class MediaSource(FixtureSource):
    def __init__(self):
        super().__init__()
        self.media_calls = 0
        self.fail_media = False
        self.image_bytes = PNG
        self.expected_cards = []

    def read_record(self, row):
        record = super().read_record(row)
        record['artifacts'][1]['text'] += '\n\n' + REFERENCE
        return record

    def read_summary_image(self, record_id, card_id, previous=None, *, expected_card_ids=None):
        self.media_calls += 1
        self.expected_cards.append(expected_card_ids)
        if self.fail_media:
            raise RuntimeError('PLAUD_IMAGE_DOWNLOAD_FAILED')
        assert (record_id, card_id) == (RID, CARD)
        return {'data': self.image_bytes, 'content_type': 'image/png', 'etag': 'synthetic-v1'}


class EngineMediaTests(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(__file__).resolve().parents[1] / 'test-sandboxes'
        self.scratch.mkdir(exist_ok=True)

    def test_default_source_receives_complete_literal_record_card_set(self):
        other = 'oc_su_' + 'c' * 32 + '_i1'
        class Multiple(MediaSource):
            def read_record(self, row):
                record = super().read_record(row)
                record['artifacts'].append({'key': 'Summary-second', 'kind': 'Summary',
                                            'text': REFERENCE.replace(CARD, other)})
                return record

            def read_summary_image(self, record_id, card_id, previous=None, *, expected_card_ids=None):
                self.expected_cards.append(expected_card_ids)
                return {'data': PNG, 'content_type': 'image/png'}

        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            vault = root / 'vault'
            vault.mkdir()
            config = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            source = Multiple()
            report = engine.sync(config, source, FixtureTranslator(), media_source=source)
            self.assertFalse(report['failed'])
            self.assertEqual(source.expected_cards, [sorted([CARD, other])] * 2)

    def test_media_receipts_are_opt_in_without_changing_text_only_headers(self):
        record = {'id': RID, 'title': 'Synthetic meeting'}
        artifact = {'key': 'Summary-test', 'kind': 'Summary'}
        plain = engine.make_note(record, artifact, 'source', 'body')
        self.assertNotIn('plaud_media', engine.read_note(plain)[0])
        receipt = {'card_id': CARD, 'path': 'Meeting Minutes/_attachments/cover.png', 'sha256': 'a' * 64}
        media = engine.make_note(record, artifact, 'source', 'body', media=[receipt], media_pending=True)
        metadata, body = engine.read_note(media)
        self.assertEqual(metadata['plaud_media'], [receipt])
        self.assertEqual(metadata['plaud_media_status'], 'incomplete')
        self.assertEqual(body, 'body')

    def test_media_authentication_error_is_actionable_and_unknown_details_never_escape(self):
        report = {'failed': []}
        engine.media_failure(report, RID, RuntimeError('IMAGE_WEB_LOGIN_REQUIRED'))
        diagnostic = report['failed'][0]['diagnostic']
        self.assertTrue(diagnostic['auth_required'])
        self.assertFalse(diagnostic['retryable'])
        self.assertIn('image-login', diagnostic['message'])
        self.assertIn('Aside', diagnostic['message'])
        self.assertNotIn('전용 창', diagnostic['message'])
        report = {'failed': []}
        secret = 'https://example.invalid/image?token=private-value'
        engine.media_failure(report, RID, RuntimeError(secret))
        self.assertNotIn(secret, str(report))
        self.assertEqual(report['failed'][0]['error'], 'SUMMARY_IMAGE_FAILED')
        for code in ['IMAGE_WEB_CARD_AMBIGUOUS', 'IMAGE_WEB_IMAGE_IDENTITY', 'IMAGE_WEB_PROFILE_INVALID',
                     'IMAGE_WEB_AUTH_INVALID', 'IMAGE_WEB_AUTH_BUSY', 'IMAGE_WEB_AUTH_IDENTITY',
                     'IMAGE_WEB_API_FAILED', 'IMAGE_WEB_CRYPTO_UNAVAILABLE']:
            report = {'failed': []}
            engine.media_failure(report, RID, RuntimeError(code))
            self.assertEqual(report['failed'][0]['diagnostic']['code'], code)

    def test_default_web_source_is_lazy_and_does_not_require_a_kanban_source_method(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            vault = root / 'vault'
            vault.mkdir()
            config = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            image_source = MediaSource()
            with patch('plaud_obsidian.web_media.WebMedia', return_value=nullcontext(image_source)) as web:
                engine.sync(config, FixtureSource(), FixtureTranslator())
                web.assert_not_called()
                source = MediaSource()
                proxy = SimpleNamespace(list_records=source.list_records, read_record=source.read_record)
                report = engine.sync(config, proxy, FixtureTranslator())
                self.assertFalse(report['failed'])
                web.assert_called_once_with(config)
                self.assertEqual(image_source.media_calls, 1)

    def test_image_is_saved_and_embedded_without_retranslating_repeat_or_missing_asset(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            vault = root / 'vault'
            vault.mkdir()
            config = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            source, translator = MediaSource(), FixtureTranslator()
            first = engine.sync(config, source, translator, media_source=source)
            self.assertFalse(first['failed'])
            assets = list((vault / 'Meeting Minutes/_attachments/2026/01' / RID).glob('*.png'))
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0].read_bytes(), PNG)
            notes = list((vault / 'Meeting Minutes').rglob('* - Summary.md'))
            self.assertEqual(len(notes), 1)
            note = notes[0]
            meta, body = engine.read_note(note.read_bytes())
            self.assertIn(REFERENCE, body)
            self.assertIn('![[Meeting Minutes/_attachments/2026/01/', body)
            self.assertIn('서버 25대를 모두 유지합니다.', body)
            self.assertEqual(meta['plaud_render_sha256'], engine.digest(body.encode('utf-8')))
            calls = translator.calls
            before_note = note.read_bytes(), note.stat().st_mtime_ns
            before_image = assets[0].read_bytes(), assets[0].stat().st_mtime_ns
            same = engine.sync(config, source, translator, media_source=source)
            self.assertFalse(same['failed'])
            self.assertEqual(translator.calls, calls)
            self.assertEqual((note.read_bytes(), note.stat().st_mtime_ns), before_note)
            self.assertEqual((assets[0].read_bytes(), assets[0].stat().st_mtime_ns), before_image)
            assets[0].unlink()  # Only this test-created synthetic image.
            repaired = engine.sync(config, source, translator, media_source=source)
            self.assertFalse(repaired['failed'])
            self.assertEqual(assets[0].read_bytes(), PNG)
            self.assertEqual(translator.calls, calls)
            self.assertEqual((note.read_bytes(), note.stat().st_mtime_ns), before_note)


    def test_media_only_backfill_preserves_translation_and_neighbors_with_backup(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            vault = root / 'vault'
            vault.mkdir()
            config = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            source, translator = MediaSource(), FixtureTranslator()
            source.fail_media = True
            initial = engine.sync(config, source, translator, media_source=source)
            self.assertTrue(initial['failed'])
            notes = [Path(p) for p in initial['targets']]
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in notes}
            calls = translator.calls
            source.fail_media = False
            repaired = engine.sync_existing_media(config, media_source=source)
            self.assertFalse(repaired['failed'])
            self.assertEqual(repaired['notes_updated'], 1)
            self.assertEqual(translator.calls, calls)
            for p in notes:
                if p.name.endswith(' - Summary.md'):
                    old_meta, old_body = engine.read_note(before[p][0])
                    meta, body = engine.read_note(p.read_bytes())
                    from plaud_obsidian.media import strip_summary_image_embeds
                    self.assertEqual(strip_summary_image_embeds(body), old_body)
                    self.assertEqual(meta['plaud_source_sha256'], old_meta['plaud_source_sha256'])
                    self.assertEqual(meta['plaud_media_status'], 'complete')
                else:
                    self.assertEqual((p.read_bytes(), p.stat().st_mtime_ns), before[p])
            backups = list((root / 'state/backups').rglob('*.md'))
            self.assertEqual(len(backups), 1)
            summary = next(p for p in notes if p.name.endswith(' - Summary.md'))
            self.assertEqual(backups[0].read_bytes(), before[summary][0])
            after = summary.read_bytes(), summary.stat().st_mtime_ns
            repeated = engine.sync_existing_media(config, media_source=source)
            self.assertFalse(repeated['failed'])
            self.assertEqual(repeated['notes_updated'], 0)
            self.assertEqual((summary.read_bytes(), summary.stat().st_mtime_ns), after)

    def test_backfill_forwards_all_owned_summary_cards_for_the_record(self):
        other = 'c_' + 'd' * 32
        received = []
        def image(record_id, card_id, previous=None, *, expected_card_ids=None):
            received.append(expected_card_ids)
            return {'data': PNG, 'content_type': 'image/png'}
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            vault = root / 'vault'
            output = vault / 'Meeting Minutes'
            output.mkdir(parents=True)
            config = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            for key, card in [('Summary-first', CARD), ('Summary-second', other)]:
                data = engine.make_note({'id': RID}, {'key': key, 'kind': 'Summary'}, 'source',
                                        REFERENCE.replace(CARD, card))
                (output / (RID + '-' + key + '.md')).write_bytes(data)
            report = engine.sync_existing_media(config, media_source=SimpleNamespace(read_summary_image=image))
            self.assertFalse(report['failed'])
            self.assertEqual(received, [sorted([CARD, other])] * 2)

    def test_incomplete_backfill_card_inventory_never_resolves_owned_sibling(self):
        other_card = 'c_' + 'd' * 32
        unrelated_rid = 'f_' + 'e' * 32
        for failure in ['malformed', 'duplicate']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(dir=self.scratch) as directory:
                root = Path(directory)
                vault = root / 'vault'
                output = vault / 'Meeting Minutes'
                output.mkdir(parents=True)
                config = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
                originals = {}
                for key, card in [('Summary-first', CARD), ('Summary-second', other_card)]:
                    body = REFERENCE.replace(CARD, card)
                    if key == 'Summary-first' and failure == 'malformed':
                        body += '\n' + REFERENCE.replace(CARD, '../invalid')
                    data = engine.make_note({'id': RID}, {'key': key, 'kind': 'Summary'}, 'source', body)
                    target = output / (RID + '-' + key + '.md')
                    target.write_bytes(data)
                    originals[target] = data
                    if key == 'Summary-first' and failure == 'duplicate':
                        duplicate = output / 'duplicate' / target.name
                        duplicate.parent.mkdir()
                        duplicate.write_bytes(data)
                        originals[duplicate] = data
                neighbor = output / (unrelated_rid + '-Summary-neighbor.md')
                neighbor.write_bytes(engine.make_note({'id': unrelated_rid},
                    {'key': 'Summary-neighbor', 'kind': 'Summary'}, 'source', REFERENCE))
                calls = []
                def image(record_id, card_id, previous=None, *, expected_card_ids=None):
                    calls.append((record_id, card_id, expected_card_ids))
                    return {'data': PNG, 'content_type': 'image/png'}
                report = engine.sync_existing_media(config, media_source=SimpleNamespace(read_summary_image=image))
                self.assertEqual(calls, [(unrelated_rid, CARD, [CARD])])
                self.assertEqual(report['notes_updated'], 1)
                self.assertIn('IMAGE_WEB_CARD_AMBIGUOUS', [x['error'] for x in report['failed']])
                self.assertTrue(all(path.read_bytes() == data for path, data in originals.items()))

    def test_image_failure_keeps_original_translation_and_good_existing_note(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            vault = root / 'vault'
            vault.mkdir()
            config = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            source, translator = MediaSource(), FixtureTranslator()
            source.fail_media = True
            failed = engine.sync(config, source, translator, media_source=source)
            self.assertEqual(failed['failed'][0]['error'], 'PLAUD_IMAGE_DOWNLOAD_FAILED')
            summary = next(Path(p) for p in failed['targets'] if p.endswith(' - Summary.md'))
            meta, body = engine.read_note(summary.read_bytes())
            self.assertIn(source.text, body)
            self.assertIn('서버 25대를 모두 유지합니다.', body)
            self.assertEqual(meta['plaud_media_status'], 'incomplete')
            source.fail_media = False
            engine.sync(config, source, translator, media_source=source)
            before = summary.read_bytes(), summary.stat().st_mtime_ns
            calls = translator.calls
            source.fail_media = True
            failed = engine.sync(config, source, translator, media_source=source)
            self.assertTrue(failed['failed'])
            self.assertEqual((summary.read_bytes(), summary.stat().st_mtime_ns), before)
            self.assertEqual(translator.calls, calls)


    def test_media_only_backfill_refuses_legacy_filename_without_exact_owner(self):
        for field, value in [('plaud_plugin', 'different-plugin'), ('plaud_plugin', None),
                             ('plaud_record_id', 'f_' + 'c' * 32), ('plaud_artifact_key', 'Summary-other')]:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory(dir=self.scratch) as directory:
                root = Path(directory)
                vault = root / 'vault'
                output = vault / 'Meeting Minutes'
                output.mkdir(parents=True)
                config = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
                raw = engine.make_note({'id': RID}, {'key': 'Summary-test', 'kind': 'Summary'}, 'source', REFERENCE)
                import json
                meta, body = engine.read_note(raw)
                old = (field + ': ' + json.dumps(meta[field])).encode()
                raw = raw.replace(old, (field + ': ' + json.dumps(value)).encode())
                target = output / (RID + '-Summary-test.md')
                target.write_bytes(raw)
                source = MediaSource()
                report = engine.sync_existing_media(config, media_source=source)
                self.assertTrue(report['failed'])
                self.assertEqual(target.read_bytes(), raw)
                self.assertEqual(source.media_calls, 0)

    def test_malformed_media_reference_does_not_drop_summary_or_later_highlights(self):
        class Malformed(MediaSource):
            def read_record(self, row):
                record = super().read_record(row)
                record['artifacts'][1]['text'] = record['artifacts'][1]['text'].replace(CARD, '../bad')
                return record
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            vault = root / 'vault'
            vault.mkdir()
            config = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            source = Malformed()
            report = engine.sync(config, source, FixtureTranslator(), media_source=source)
            self.assertEqual(report['created'], 3)
            self.assertEqual(report['failed'][0]['error'], 'INVALID_SUMMARY_CARD_REF')
            summary = next(Path(p) for p in report['targets'] if p.endswith(' - Summary.md'))
            meta, body = engine.read_note(summary.read_bytes())
            self.assertIn('id=../bad', body)
            self.assertIn('서버 25대를 모두 유지합니다.', body)
            self.assertEqual(meta['plaud_media_status'], 'incomplete')


    def test_private_pending_receipt_recovers_interrupted_first_publish_and_replacement(self):
        for update in (False, True):
            with self.subTest(update=update), tempfile.TemporaryDirectory(dir=self.scratch) as directory:
                root = Path(directory)
                vault = root / 'vault'
                vault.mkdir()
                config = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
                source, translator = MediaSource(), FixtureTranslator()
                if update:
                    self.assertFalse(engine.sync(config, source, translator, media_source=source)['failed'])
                    source.image_bytes = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
                save = engine.save_json
                def interrupted(path, value):
                    if Path(path).parent.name == 'media-cache' and '_pending' not in value:
                        raise OSError('synthetic interruption after image publication')
                    return save(path, value)
                with patch.object(engine, 'save_json', side_effect=interrupted):
                    failed = engine.sync(config, source, translator, media_source=source)
                self.assertTrue(failed['failed'])
                calls = translator.calls
                repaired = engine.sync(config, source, translator, media_source=source)
                self.assertFalse(repaired['failed'])
                self.assertEqual(translator.calls, calls)
                image = next((vault / 'Meeting Minutes/_attachments').rglob('*.png'))
                self.assertEqual(image.read_bytes(), source.image_bytes)
                summary = next((vault / 'Meeting Minutes').rglob('* - Summary.md'))
                meta, body = engine.read_note(summary.read_bytes())
                self.assertEqual(meta['plaud_media_status'], 'complete')
                self.assertEqual(meta['plaud_render_sha256'], engine.digest(body.encode('utf-8')))


    def test_failed_publication_never_authorizes_adopting_identical_unrelated_image(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            vault = root / 'vault'
            image = vault / 'Meeting Minutes/_attachments/2026/01' / RID / (CARD + '.png')
            image.parent.mkdir(parents=True)
            image.write_bytes(PNG)
            config = {'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(root / 'state')}
            source, translator = MediaSource(), FixtureTranslator()
            for _ in range(2):
                report = engine.sync(config, source, translator, media_source=source)
                self.assertTrue(report['failed'])
                self.assertEqual(image.read_bytes(), PNG)


if __name__ == '__main__':
    unittest.main()
