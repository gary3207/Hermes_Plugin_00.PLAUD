"""Safe error publication, without a live board or account."""
import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import plaud_kanban as job


class ErrorDetailTest(unittest.TestCase):
    def setUp(self):
        import sys
        path = patch.object(sys, 'path', [str(Path(__file__).resolve().parents[1]), *sys.path])
        path.start()
        self.addCleanup(path.stop)

    def test_translation_failure_keeps_original_but_worker_remains_failed(self):
        from contextlib import nullcontext
        from plaud_obsidian import engine
        from plaud_obsidian.translation import Translator
        class Source:
            def list_records(self):
                return [{'id': 'a' * 32}]
            def read_record(self, row):
                return dict(id=row['id'], title='Synthetic', created_at='2026-01-01T00:00:00Z',
                            artifacts=[dict(key='Summary-fixture', kind='Summary', text='Keep all 25 servers.')],
                            pending=[])
        calls = []
        def complete(**kwargs):
            calls.append(kwargs)
            raise TimeoutError('PRIVATE_MESSAGE_MUST_NOT_LEAK')
        with tempfile.TemporaryDirectory() as directory, patch('time.sleep'):
            root = Path(directory)
            (root/'vault').mkdir()
            run = root/'run'
            run.mkdir()
            config = dict(state=str(root/'state'), vault=str(root/'vault'), output='notes')
            with patch.object(job, 'await_gate'):
                report = job.worker(run, runtime=lambda: (
                    engine, config, nullcontext(Source()), Translator(complete, root/'state')))
            self.assertEqual(report['outcome'], 'failed')
            self.assertEqual(report['failure_phase'], 3)
            self.assertEqual(report['verified'], 1)
            self.assertFalse(report['verification_errors'])
            failures = report['failed']
            assert isinstance(failures, list)
            self.assertIn('TRANSLATION_TRANSPORT_UNAVAILABLE', failures[0])
            self.assertEqual(len(calls), 2)
            note = next((root/'vault/notes').rglob('*.md')).read_text()
            self.assertIn('Keep all 25 servers.', note)
            self.assertIn('번역 미완료', note)
            self.assertNotIn('PRIVATE_MESSAGE_MUST_NOT_LEAK', (run/'events.jsonl').read_text())
            body = job.card_body(dict(started='fixture', directory=str(run), run=1, token='fixture'), report)
            self.assertIn('TRANSLATION_TRANSPORT_UNAVAILABLE', body)

    def test_local_id_error_reaches_log_and_card_without_identifier(self):
        with tempfile.TemporaryDirectory() as directory:
            details = job.diagnostic(directory,'inventory',ValueError('PLAUD_ID_INVALID'))
            self.assertEqual('PLAUD_ID_INVALID',details[0]['code'])
            self.assertIn('f_',details[0]['message'])
            event = json.loads((Path(directory)/'events.jsonl').read_text())
            self.assertEqual(details[0],event['causes'][0]['detail'])
            body = job.card_body(dict(started='fixture',directory=directory,run=1,token='fixture'),
                dict(outcome='failed',phase=2,ended='fixture',failed=['PLAUD_ID_INVALID'],errors=details))
            self.assertIn(details[0]['message'],body)

    def test_startup_diagnostic_survives_missing_engine(self):
        import sys
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(sys.modules, {'plaud_obsidian.engine':None}):
                self.assertEqual([],job.diagnostic(directory,'startup',RuntimeError('PLAUD_PLUGIN_NOT_LOADED')))
            event = json.loads((Path(directory)/'events.jsonl').read_text())
            self.assertEqual('RuntimeError',event['causes'][0]['error_class'])

    def test_inventory_error_reaches_private_log_and_terminal_report(self):
        from plaud_obsidian import engine
        from plaud_obsidian.mcp_transport import unwrap
        class Source:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def list_records(self):
                return unwrap({'isError':True, 'content':[{'type':'text', 'text':
                    'Failed to list files: Error: Not authenticated. Please login first.'}]}, 'list_files')
        class Translator:
            cache_identity = 'fixture'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'vault').mkdir()
            run = root/'run'
            run.mkdir()
            config = dict(state=str(root/'state'),vault=str(root/'vault'),output='notes')
            with patch.object(job,'await_gate'):
                report = job.worker(run, runtime=lambda:(engine,config,Source(),Translator()))
            self.assertEqual('failed',report['outcome'])
            self.assertEqual(2,report['failure_phase'])
            self.assertEqual('PLAUD_AUTH_REQUIRED',report['errors'][0]['code'])
            events = [json.loads(line) for line in (run/'events.jsonl').read_text().splitlines()]
            event = next(event for event in events if event.get('component') == 'inventory')
            self.assertEqual('list_files',event['causes'][0]['detail']['tool'])
            saved = json.loads((run/'result.json').read_text())
            self.assertEqual(report['errors'],saved['errors'])

    def test_card_explains_authentication_and_aside_recovery(self):
        active = dict(started='fixture', directory='fixture', run=1, token='fixture')
        report = dict(outcome='failed', phase=2, ended='fixture',
                      failed=['PLAUD_AUTH_REQUIRED'], errors=[dict(
                          code='PLAUD_AUTH_REQUIRED', tool='list_files',
                          message='PLAUD login is required.', auth_required=True)])
        body = job.card_body(active, report)
        self.assertIn('PLAUD login is required.', body)
        self.assertIn('list_files', body)
        self.assertIn('Aside', body)
        self.assertIn('재로그인', body)


if __name__ == '__main__':
    unittest.main()
