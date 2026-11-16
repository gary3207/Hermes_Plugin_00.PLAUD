"""Offline Summary media tests; no Vault, credentials, or transport."""
import importlib
import importlib.util
import base64
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

C1 = 'c_' + 'aB09' * 8
C2 = 'oc_su_' + '1234abcd' * 4 + '_i12'
RID = 'of_' + 'aB09' * 8


def ref(card_id):
    return '[](plaud://image?type=summaryCard&id=' + card_id + ')'


class SummaryMediaTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / 'test-sandboxes'
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name) / 'vault'
        self.vault.mkdir()
        self.output = self.vault / 'Meeting Minutes'
        self.record = {'id': RID, 'start_at': '2026-01-01T00:30:00+09:00'}
        self.png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN1sAAAAASUVORK5CYII=')

    def test_store_original_png_receipt_and_source_wallclock_path(self):
        from plaud_obsidian import media
        self.assertTrue(callable(getattr(media, 'store_summary_image', None)), 'store helper missing')
        receipt = media.store_summary_image(self.vault, self.output, self.record, C1, self.png, 'image/png')
        relative = 'Meeting Minutes/_attachments/2026/01/' + RID + '/' + C1 + '.png'
        self.assertEqual(receipt, {'card_id': C1, 'path': relative, 'sha256': hashlib.sha256(self.png).hexdigest(),
                                   'byte_count': len(self.png), 'content_type': 'image/png'})
        self.assertEqual((self.vault / relative).read_bytes(), self.png)
        self.assertEqual(list((self.vault / relative).parent.iterdir()), [self.vault / relative])

    def test_store_rejects_invalid_images_and_accepts_matching_raster_types(self):
        from plaud_obsidian import media
        for data, mime in [(b'<html>login</html>', 'image/png'), (b'<svg/>', 'image/svg+xml'),
                           (b'arbitrary bytes', 'image/jpeg'), (self.png, 'image/jpeg'),
                           (self.png, 'text/html'), (b'', 'image/png'),
                           (b'x' * (20 * 1024 * 1024 + 1), 'image/png')]:
            with self.subTest(mime=mime, size=len(data)), self.assertRaisesRegex(ValueError, 'INVALID_SUMMARY_IMAGE'):
                media.store_summary_image(self.vault, self.output, self.record, C1, data, mime)
        jpeg = bytes.fromhex('ffd8ffe000104a46494600010100000100010000ffd9')
        webp = base64.b64decode('UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA')
        for card, data, mime, ext in [(C1, self.png, 'IMAGE/PNG; charset=binary', 'png'),
                                      (C2, jpeg, 'image/jpeg', 'jpg'),
                                      ('c_' + 'b' * 32, webp, 'image/webp', 'webp')]:
            with self.subTest(mime=mime):
                receipt = media.store_summary_image(self.vault, self.output, self.record, card, data, mime)
                self.assertTrue(receipt['path'].endswith('.' + ext))
                self.assertEqual(receipt['content_type'], mime.split(';')[0].lower())
                self.assertEqual((self.vault / receipt['path']).read_bytes(), data)

    def test_ownership_receipt_required_idempotent_and_hash_checked_updates(self):
        from plaud_obsidian import media
        receipt = media.store_summary_image(self.vault, self.output, self.record, C1, self.png, 'image/png')
        target = self.vault / receipt['path']
        os.utime(target, ns=(1_600_000_000_123456789, 1_600_000_000_123456789))
        before = target.stat().st_mtime_ns
        with self.assertRaisesRegex(RuntimeError, 'UNRELATED_DESTINATION_EXISTS'):
            media.store_summary_image(self.vault, self.output, self.record, C1, self.png, 'image/png')
        self.assertEqual(media.store_summary_image(self.vault, self.output, self.record, C1,
                                                   self.png, 'image/png', previous=receipt), receipt)
        self.assertEqual(target.stat().st_mtime_ns, before)
        changed = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        for field, value in [('card_id', C2), ('path', 'elsewhere.png'), ('sha256', '0' * 64),
                              ('byte_count', 1), ('content_type', 'image/jpeg')]:
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, 'SUMMARY_IMAGE_OWNERSHIP_MISMATCH'):
                media.store_summary_image(self.vault, self.output, self.record, C1, changed, 'image/png',
                                          previous={**receipt, field: value})
            self.assertEqual(target.read_bytes(), self.png)
        updated = media.store_summary_image(self.vault, self.output, self.record, C1, changed, 'image/png', previous=receipt)
        self.assertEqual(updated['sha256'], hashlib.sha256(changed).hexdigest())
        self.assertEqual(target.read_bytes(), changed)
        target.write_bytes(b'user changed this')
        with self.assertRaisesRegex(RuntimeError, 'SUMMARY_IMAGE_OWNERSHIP_MISMATCH'):
            media.store_summary_image(self.vault, self.output, self.record, C1, self.png, 'image/png', previous=updated)
        self.assertEqual(target.read_bytes(), b'user changed this')

    def test_storage_rejects_unsafe_literal_ids_before_creating_paths(self):
        from plaud_obsidian import media
        for card in ['../escape', 'c_' + 'a' * 33, C1 + ' ', 'oc_su_' + 'a' * 32 + '_i0/evil']:
            with self.subTest(card=card), self.assertRaisesRegex(ValueError, 'INVALID_SUMMARY_CARD_ID'):
                media.store_summary_image(self.vault, self.output, self.record, card, self.png, 'image/png')
        for rid in ['../escape', RID + ' ', str(self.vault / 'escape')]:
            with self.subTest(rid=rid), self.assertRaisesRegex(RuntimeError, 'INVALID_RECORD_ID'):
                media.store_summary_image(self.vault, self.output, {**self.record, 'id': rid}, C1, self.png, 'image/png')
        self.assertFalse(self.output.exists())

    def test_storage_rejects_output_escapes_and_non_directory_ancestors(self):
        from plaud_obsidian import media
        for output in [self.vault.parent / 'outside', self.vault, self.output / '..' / 'escaped', Path('relative')]:
            with self.subTest(output=str(output)), self.assertRaisesRegex(RuntimeError, 'UNSAFE_SUMMARY_IMAGE_PATH'):
                media.store_summary_image(self.vault, output, self.record, C1, self.png, 'image/png')
        self.output.mkdir()
        (self.output / '_attachments').write_text('unrelated', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'UNSAFE_SUMMARY_IMAGE_PATH'):
            media.store_summary_image(self.vault, self.output, self.record, C1, self.png, 'image/png')
        self.assertEqual((self.output / '_attachments').read_text(), 'unrelated')

    @unittest.skipUnless(os.name == 'nt', 'Windows junction check')
    def test_storage_rejects_real_junction_even_if_target_is_inside_vault(self):
        import subprocess
        from plaud_obsidian import media
        self.output.mkdir()
        elsewhere = self.vault / 'elsewhere'
        elsewhere.mkdir()
        link = self.output / '_attachments'
        made = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(elsewhere)], capture_output=True)
        self.assertEqual(made.returncode, 0, 'junction fixture creation failed')
        self.addCleanup(os.rmdir, link)
        with self.assertRaisesRegex(RuntimeError, 'UNSAFE_SUMMARY_IMAGE_PATH'):
            media.store_summary_image(self.vault, self.output, self.record, C1, self.png, 'image/png')
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_atomic_publish_never_clobbers_new_or_changed_unrelated_bytes(self):
        from unittest.mock import patch
        from plaud_obsidian import media
        target = self.output / '_attachments/2026/01' / RID / (C1 + '.png')
        make_temp = tempfile.mkstemp
        def competing_write(*args, **kwargs):
            result = make_temp(*args, **kwargs)
            target.write_bytes(b'unrelated concurrent write')
            return result
        for update in [False, True]:
            with self.subTest(update=update):
                previous = None
                if update:
                    target.unlink()
                    previous = media.store_summary_image(self.vault, self.output, self.record, C1, self.png, 'image/png')
                changed = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
                with patch('tempfile.mkstemp', side_effect=competing_write):
                    with self.assertRaisesRegex(RuntimeError, 'UNRELATED_DESTINATION_EXISTS|SUMMARY_IMAGE_OWNERSHIP_MISMATCH'):
                        media.store_summary_image(self.vault, self.output, self.record, C1, changed, 'image/png', previous=previous)
                self.assertEqual(target.read_bytes(), b'unrelated concurrent write')
                self.assertEqual(list(target.parent.glob('.plaud-*')), [])

    def test_embed_once_per_card_preserves_original_refs_and_removes_only_owned_blocks(self):
        from plaud_obsidian import media
        self.assertTrue(callable(getattr(media, 'embed_summary_images', None)), 'embed helper missing')
        receipts = [media.store_summary_image(self.vault, self.output, self.record, card, self.png, 'image/png')
                    for card in [C1, C2]]
        body = '# Original\n' + ref(C1) + '\nPersonal ![[keep.png]]\n' + ref(C2) + '\n# 번역\n' + ref(C1)
        rendered = media.embed_summary_images(body, receipts + [receipts[0]])
        for receipt in receipts:
            self.assertEqual(rendered.count('![[' + receipt['path'] + ']]'), 1)
            self.assertEqual(rendered.count(ref(receipt['card_id'])), body.count(ref(receipt['card_id'])))
        self.assertIn('Personal ![[keep.png]]', rendered)
        self.assertEqual(media.embed_summary_images(rendered, receipts), rendered)
        self.assertEqual(media.strip_summary_image_embeds(rendered), body)
        self.assertEqual(media.embed_summary_images(rendered, []), body)
        self.assertEqual(len(list(self.output.rglob('*.png'))), 2)

    def test_embed_rejects_unsafe_receipt_paths_and_conflicting_duplicate_cards(self):
        from plaud_obsidian import media
        receipt = media.store_summary_image(self.vault, self.output, self.record, C1, self.png, 'image/png')
        for path in ['../escape.png', '/absolute.png', 'C:/escape.png', 'folder\\escape.png',
                     'folder/../escape.png', 'folder/evil]]\n![[secret.png', 'folder/x#anchor.png']:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'INVALID_SUMMARY_IMAGE_RECEIPT'):
                media.embed_summary_images(ref(C1), [{**receipt, 'path': path}])
        with self.assertRaisesRegex(ValueError, 'INVALID_SUMMARY_IMAGE_RECEIPT'):
            media.embed_summary_images(ref(C1), [receipt, {**receipt, 'sha256': '0' * 64}])
        with self.assertRaisesRegex(ValueError, 'INVALID_SUMMARY_CARD_REF'):
            media.embed_summary_images(ref('../escape'), [receipt])

    def test_source_date_convention_unknown_date_and_missing_file_recovery(self):
        from plaud_obsidian import engine, media
        for index, value in enumerate(['2026-01-01T00:30:00+09:00', '2025-12-31T23:30:00Z',
                                       1767223800, 1767223800000, 'invalid', None, True, float('inf')]):
            with self.subTest(value=value):
                record = {'id': f'{index:032x}', 'start_at': value}
                receipt = media.store_summary_image(self.vault, self.output, record, C2, self.png, 'image/png')
                date_parts = engine.readable_target(self.output, record, 'Summary').parent.relative_to(self.output).parts
                target = self.vault / receipt['path']
                self.assertEqual(target.parent, self.output.joinpath('_attachments', *date_parts, record['id']))
                target.unlink()
                self.assertEqual(media.store_summary_image(self.vault, self.output, record, C2, self.png,
                                                           'image/png', previous=receipt), receipt)
                self.assertEqual(target.read_bytes(), self.png)
        created = {'id': RID, 'created_at': '2024-02-29T12:00:00'}
        receipt = media.store_summary_image(self.vault, self.output, created, C1, self.png, 'image/png')
        self.assertIn('/_attachments/2024/02/' + RID + '/', receipt['path'])

    def test_atomic_failure_preserves_owned_image_and_cleans_temporary_file(self):
        from unittest.mock import patch
        from plaud_obsidian import media
        receipt = media.store_summary_image(self.vault, self.output, self.record, C1, self.png, 'image/png')
        target = self.vault / receipt['path']
        changed = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        with patch('os.replace', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError):
                media.store_summary_image(self.vault, self.output, self.record, C1, changed, 'image/png', previous=receipt)
        self.assertEqual(target.read_bytes(), self.png)
        self.assertEqual(list(target.parent.glob('.plaud-*')), [])

    def test_storage_rejects_file_symlinks_including_dangling_links(self):
        from plaud_obsidian import media
        target = self.output / '_attachments/2026/01' / RID / (C1 + '.png')
        target.parent.mkdir(parents=True)
        unrelated = self.vault.parent / 'personal.png'
        unrelated.write_bytes(self.png)
        try:
            target.symlink_to(unrelated)
        except OSError as exc:
            self.skipTest('symlink creation unavailable: ' + str(exc.winerror if os.name == 'nt' else exc.errno))
        with self.assertRaisesRegex(RuntimeError, 'UNSAFE_SUMMARY_IMAGE_PATH'):
            media.store_summary_image(self.vault, self.output, self.record, C1, self.png, 'image/png')
        unrelated.unlink()
        with self.assertRaisesRegex(RuntimeError, 'UNSAFE_SUMMARY_IMAGE_PATH'):
            media.store_summary_image(self.vault, self.output, self.record, C1, self.png, 'image/png')
        self.assertFalse(unrelated.exists())

    def test_owned_blocks_can_be_removed_after_crlf_normalization(self):
        from plaud_obsidian import media
        receipt = media.store_summary_image(self.vault, self.output, self.record, C1, self.png, 'image/png')
        body = 'Original\n' + ref(C1) + '\nEnd'
        rendered = media.embed_summary_images(body, [receipt]).replace('\n', '\r\n')
        self.assertEqual(media.strip_summary_image_embeds(rendered), body.replace('\n', '\r\n'))
        untouched = '\n<!-- hermes-plaud:summary-image ' + C1 + ' -->\nUser text\n<!-- /hermes-plaud:summary-image -->'
        self.assertEqual(media.strip_summary_image_embeds(untouched), untouched)

    def test_distinct_actual_empty_label_refs_keep_literal_ids_and_order(self):
        self.assertIsNotNone(importlib.util.find_spec('plaud_obsidian.media'), 'media helpers are missing')
        media = importlib.import_module('plaud_obsidian.media')
        body = '\n'.join([ref(C2), '[named](plaud://image?type=summaryCard&id=' + C1 + ')',
                          ref(C1), ref(C2), '[](plaud://image?type=other&id=anything)'])
        self.assertEqual(media.summary_card_ids(body), [C2, C1])

    def test_malformed_relevant_refs_are_rejected_not_normalized(self):
        from plaud_obsidian import media
        for card in ['../escape', 'c_' + 'a' * 31, 'C_' + 'a' * 32,
                     C1 + '&extra=x', C1 + '%2f', 'oc_su_' + 'a' * 32 + '_i-1']:
            with self.subTest(card=card), self.assertRaisesRegex(ValueError, 'INVALID_SUMMARY_CARD_REF'):
                media.summary_card_ids(ref(card))
        for text in ['[](plaud://image?type=summaryCard)', ref(C1)[:-1]]:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'INVALID_SUMMARY_CARD_REF'):
                media.summary_card_ids(text)


    def test_onedrive_cloud_tags_are_not_confused_with_path_redirects(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from plaud_obsidian import media
        import stat
        actual = Path.lstat
        self.output.mkdir()
        for tag in [0x9000001A, 0x9000101A, 0x9000F01A, 0xA0000003, 0xA000000C]:
            def cloud(path, *args, **kwargs):
                info = actual(path, *args, **kwargs)
                if path == self.output:
                    return SimpleNamespace(st_mode=info.st_mode,
                                           st_file_attributes=info.st_file_attributes | stat.FILE_ATTRIBUTE_REPARSE_POINT,
                                           st_reparse_tag=tag)
                return info
            with self.subTest(tag=tag), patch.object(Path, 'lstat', cloud):
                target = self.output / '_attachments/2026/01' / RID / (C1 + '.png')
                if tag in (0xA0000003, 0xA000000C):
                    with self.assertRaisesRegex(RuntimeError, 'UNSAFE_SUMMARY_IMAGE_PATH'):
                        media._safe_path(self.vault, self.output, target)
                else:
                    media._safe_path(self.vault, self.output, target)


if __name__ == '__main__':
    unittest.main()
