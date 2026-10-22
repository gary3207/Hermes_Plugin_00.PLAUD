"""Offline transport recovery; no account data or model requests."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from plaud_obsidian.translation import Translator


class RecoveryTests(unittest.TestCase):
    def test_real_host_owned_task_gate_and_no_progress_defaults(self):
        from agent.plugin_llm import PluginLlm, PluginLlmTrustError
        from agent.auxiliary_client import _get_task_no_progress_timeout, _CodexStreamGuard
        from hermes_cli.plugins import PluginContext, PluginManager
        from hermes_cli.plugins_manifest import parse_manifest_file
        from plaud_obsidian.translation import register_task
        source = Path(__file__).resolve().parents[1]
        manifest = parse_manifest_file(source/'plugin.yaml', source, 'user', '')
        assert manifest is not None
        manager = PluginManager()
        context = PluginContext(manifest, manager)
        register_task(context)
        captured = []
        def caller(**kwargs):
            captured.append(kwargs)
            response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"units":[{"id":"0","language":"en","translation_ko":"안녕하세요"}]}'))])
            return 'openai-codex', 'gpt-6.1-sol', response
        facade = PluginLlm(plugin_id=context.plugin_id, sync_caller=caller)
        with patch('hermes_cli.plugins._ensure_plugins_discovered', return_value=manager), \
                patch('hermes_cli.config.load_config_readonly', return_value={}), tempfile.TemporaryDirectory() as directory:
            self.assertEqual(_get_task_no_progress_timeout('plaud_translation'), 120)
            guard = _CodexStreamGuard(SimpleNamespace(), 180, no_progress_timeout=120)
            self.assertEqual(guard.no_progress_timeout, 120)
            result = Translator(facade.complete_structured, directory).translate([{'id': '0', 'text': 'Hello'}])
            self.assertEqual(result[0]['translation_ko'], '안녕하세요')
            self.assertEqual(captured[0]['task'], 'plaud_translation')
            self.assertIsNone(captured[0]['provider_override'])
            self.assertIsNone(captured[0]['model_override'])
            with self.assertRaises(PluginLlmTrustError):
                PluginLlm(plugin_id='unrelated-plugin', sync_caller=caller).complete(
                    messages=[{'role': 'user', 'content': 'fixture'}], task='plaud_translation')

    def test_partial_chunk_cache_survives_transport_failure_without_retry_storm(self):
        from plaud_obsidian.engine import TranslationUnavailable
        calls = []
        failing = [True]
        def complete(**kwargs):
            units = json.loads(kwargs['input'][0]['text'])['units']
            calls.append([u['text'] for u in units])
            if failing[0] and any(u['text'] == 'Bad' for u in units):
                raise TimeoutError('private')
            return SimpleNamespace(text=json.dumps({'units': [
                {'id': u['id'], 'language': 'en', 'translation_ko': '정상 번역'} for u in units]}),
                model='synthetic', provider='synthetic')
        with tempfile.TemporaryDirectory() as directory, patch('time.sleep'):
            translator = Translator(complete, directory, max_chars=5)
            units = [{'id': 'a', 'text': 'Good'}, {'id': 'b', 'text': 'Bad'}, {'id': 'c', 'text': 'Later'}]
            with self.assertRaises(TranslationUnavailable) as raised:
                translator.translate(units)
            self.assertEqual([r['translation_ko'] for r in raised.exception.rows],
                             ['정상 번역', '[원문참조]', '[원문참조]'])
            self.assertEqual(calls, [['Good'], ['Bad'], ['Bad']])
            self.assertEqual(len(list((Path(directory)/'translation-cache').glob('*.json'))), 1)
            failing[0] = False
            self.assertTrue(all(r['translation_ko'] == '정상 번역' for r in translator.translate(units)))
            self.assertEqual(calls, [['Good'], ['Bad'], ['Bad'], ['Bad'], ['Later']])

    def test_auth_schema_and_unknown_errors_do_not_retry_or_create_fallback_receipts(self):
        import httpx
        from openai import APIStatusError
        errors = [ValueError('PRIVATE_VALUE'), PermissionError('PRIVATE_AUTH'), RuntimeError('PRIVATE_UNKNOWN')]
        errors += [APIStatusError('PRIVATE_HTTP', response=httpx.Response(
            status, request=httpx.Request('POST', 'https://offline.invalid')), body=None)
            for status in (400, 401, 403, 429)]
        for error in errors:
            calls = []
            def complete(**kwargs):
                calls.append(kwargs)
                raise error
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(type(error)):
                    Translator(complete, directory).translate([{'id': 'a', 'text': 'Private source'}])
                self.assertEqual(len(calls), 1)
                self.assertFalse(list((Path(directory)/'translation-cache').glob('*.json')))
                events = (Path(directory)/'translation-events.jsonl').read_text()
                self.assertNotIn('PRIVATE_', events)
                self.assertNotIn('Private source', events)

    def test_transport_exhaustion_saves_original_and_later_repairs_only_pending_note(self):
        from plaud_obsidian.engine import sync, read_note
        from test_engine import FixtureSource
        class Source(FixtureSource):
            def read_record(self, row):
                record = super().read_record(row)
                record['artifacts'][1]['text'] = 'Summary: Keep all 25 servers.'
                return record
        unavailable = [True]
        calls = []
        def complete(**kwargs):
            units = json.loads(kwargs['input'][0]['text'])['units']
            calls.append(units)
            if unavailable[0] and any(u['text'].startswith('Summary:') for u in units):
                raise TimeoutError('PRIVATE_NETWORK_MESSAGE_MUST_NOT_LEAK')
            return SimpleNamespace(text=json.dumps({'units': [
                {'id': u['id'], 'language': 'en', 'translation_ko': '서버 25대를 유지합니다.'}
                for u in units]}), model='synthetic', provider='synthetic')
        with tempfile.TemporaryDirectory() as directory, patch('time.sleep'):
            root = Path(directory)
            (root/'vault').mkdir()
            settings = dict(vault=str(root/'vault'), output='notes', state=str(root/'state'))
            translator = Translator(complete, root/'state')
            first = sync(settings, Source(), translator)
            self.assertEqual(first['created'], 3)
            self.assertEqual(len(first['failed']), 1)
            self.assertTrue(first['failed'][0]['error'].startswith('TRANSLATION_TRANSPORT_UNAVAILABLE'))
            output = root/'vault/notes'
            summary = next(output.glob('*Summary.md'))
            meta, body = read_note(summary.read_bytes())
            self.assertEqual(meta['plaud_translation'], 'incomplete-transport')
            self.assertIn('Summary: Keep all 25 servers.', body)
            self.assertIn('[원문참조]', body)
            self.assertIn('번역 미완료', body)
            self.assertEqual(len(list((root/'state/render-cache').glob('*.md'))), 2)
            neighbors = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in output.glob('*.md') if p != summary}
            unavailable[0] = False
            repaired = sync(settings, Source(), translator)
            self.assertFalse(repaired['failed'])
            self.assertEqual((repaired['updated'], repaired['unchanged']), (1, 2))
            self.assertEqual(read_note(summary.read_bytes())[0]['plaud_translation'], 'full-translation-unreviewed')
            self.assertNotIn('[원문참조]', read_note(summary.read_bytes())[1])
            for p, snapshot in neighbors.items():
                self.assertEqual((p.read_bytes(), p.stat().st_mtime_ns), snapshot)
            count = len(calls)
            self.assertEqual(sync(settings, Source(), translator)['unchanged'], 3)
            self.assertEqual(len(calls), count)
            self.assertNotIn('PRIVATE_NETWORK_MESSAGE_MUST_NOT_LEAK', (root/'state/translation-events.jsonl').read_text())

    def test_plugin_registers_its_own_task_and_request_uses_it(self):
        import importlib.util
        from test_plugin import Context
        entry = Path(__file__).resolve().parents[1]/'__init__.py'
        spec = importlib.util.spec_from_file_location('plaud_recovery_entry', entry)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ctx = Context()
        module.register(ctx)
        tasks = ctx.tasks
        self.assertIn('plaud_translation', tasks)
        self.assertEqual(tasks['plaud_translation']['defaults']['no_progress_timeout'], 120)
        self.assertEqual(tasks['plaud_translation']['defaults']['provider'], 'auto')
        self.assertNotIn('model', tasks['plaud_translation']['defaults'])
        captured = []
        def complete(**kwargs):
            captured.append(kwargs)
            return SimpleNamespace(text='{"units":[{"id":"0","language":"en","translation_ko":"안녕하세요"}]}',
                                   model='synthetic', provider='synthetic')
        with tempfile.TemporaryDirectory() as directory:
            Translator(complete, directory).translate([{'id': '0', 'text': 'Hello'}])
        self.assertEqual(captured[0]['task'], 'plaud_translation')

    def test_timeout_reenters_facade_once_and_keeps_success_cache(self):
        calls = []
        def complete(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise TimeoutError('synthetic private source must not be logged')
            units = json.loads(kwargs['input'][0]['text'])['units']
            return SimpleNamespace(text=json.dumps({'units': [
                {'id': u['id'], 'language': 'en', 'translation_ko': '서버 25대를 유지합니다.'}
                for u in units]}), model='synthetic', provider='synthetic')
        with tempfile.TemporaryDirectory() as directory, patch('time.sleep'):
            translator = Translator(complete, directory)
            identity = translator.cache_identity
            units = [{'id': 'source', 'text': 'Keep all 25 servers.'}]
            rows = translator.translate(units)
            self.assertEqual(rows[0]['translation_ko'], '서버 25대를 유지합니다.')
            self.assertEqual(len(calls), 2)
            self.assertEqual(translator.translate(units), rows)
            self.assertEqual(len(calls), 2)
            self.assertEqual(translator.cache_identity, identity)
            self.assertEqual(len(list((Path(directory)/'translation-cache').glob('*.json'))), 1)


if __name__ == '__main__':
    unittest.main()
