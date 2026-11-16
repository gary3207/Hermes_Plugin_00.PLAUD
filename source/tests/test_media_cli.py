"""Backfill avoids source import/translation; image-login dispatches authentication."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_plugin import Context


class MediaCommandTests(unittest.TestCase):
    def setUp(self):
        entry = Path(__file__).resolve().parents[1] / '__init__.py'
        spec = importlib.util.spec_from_file_location('plaud_media_entry_test', entry)
        assert spec is not None and spec.loader is not None
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_images_command_only_backfills_existing_media(self):
        module = self.module
        args = module.parser().parse_args(['images'])
        report = {'notes_updated': 1, 'failed': []}
        with patch.object(module, 'settings', return_value={'state': 'fixture'}), \
                patch.object(module, 'paths'), patch.object(module, 'NativePlaud') as mcp, \
                patch.object(module, 'Translator') as translator, \
                patch.object(module, 'sync_existing_media', return_value=report) as backfill:
            result = json.loads(module.execute(Context(), args))
        self.assertEqual(result, report)
        backfill.assert_called_once_with({'state': 'fixture'})
        mcp.assert_not_called()
        translator.assert_not_called()

    def test_settings_preserves_selected_external_aside_profile(self):
        ctx = Context()
        ctx.values['aside_profile'] = '11111111-2222-4333-8444-555555555555'
        config = self.module.settings(ctx)
        self.assertEqual(config.get('aside_profile'), ctx.values['aside_profile'])

    def test_image_login_dispatches_scoped_web_auth_without_import_or_translation(self):
        module = self.module
        args = module.parser().parse_args(['image-login'])
        report = {'authenticated': True, 'browser': 'aside', 'collection_browser_required': False}
        with patch.object(module, 'settings', return_value={'state': 'fixture'}), \
                patch.object(module, 'paths'), patch.object(module, 'NativePlaud') as mcp, \
                patch.object(module, 'Translator') as translator, \
                patch('plaud_obsidian.web_media.open_login', return_value=report) as login:
            result = json.loads(module.execute(Context(), args))
        self.assertEqual(result, report)
        login.assert_called_once_with({'state': 'fixture'})
        mcp.assert_not_called()
        translator.assert_not_called()


if __name__ == '__main__':
    unittest.main()
