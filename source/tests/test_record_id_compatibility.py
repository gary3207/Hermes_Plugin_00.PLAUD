"""Literal f_ IDs through inventory, transport, storage and repeat indexing."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_engine import FixtureTranslator
from plaud_obsidian import engine, provider
from plaud_obsidian.mcp_transport import MCPTransport, RECORD_ID_PATTERN


class RecordIDCompatibilityTest(unittest.TestCase):
    def test_legacy_f_filename_is_recognized_without_rewriting(self):
        rid = 'f_'+'a'*32
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/(rid+'-Transcript.md')
            target.write_bytes(b'legacy fixture')
            self.assertEqual([target],engine.existing_targets(root)[(rid,'Transcript')])
            self.assertEqual(b'legacy fixture',target.read_bytes())

    def test_malformed_ids_stay_rejected_with_safe_local_diagnostic(self):
        for bad in (' f_'+'a'*32, 'f_'+'a'*32+'\n', 'F_'+'a'*32,
                    'f_'+'a'*31, 'f_'+'a'*33, 'f_'+'z'*32, '../f_'+'a'*32):
            with self.subTest(length=len(bad)):
                with self.assertRaises(ValueError) as failure:
                    provider.valid_id(bad)
                detail = engine.error_diagnostic(failure.exception)
                self.assertEqual('PLAUD_ID_INVALID',detail['code'])
                self.assertIn('f_',detail['message'])
                self.assertNotIn(bad,json.dumps(detail))
                self.assertFalse(detail['retryable'])
                self.assertEqual(provider.ID_PATTERN.pattern.removesuffix(r'\Z'),
                                 RECORD_ID_PATTERN)
                transport = MCPTransport({})
                with patch.object(transport,'_request') as request:
                    with self.assertRaises(ValueError):
                        transport.call('get_file',{'file_id':bad})
                    request.assert_not_called()
        with self.assertRaises(RuntimeError) as failure:
            engine.record_id('f_'+'a'*31)
        self.assertEqual('INVALID_RECORD_ID',engine.error_diagnostic(failure.exception)['code'])

    def test_f_id_import_and_repeat_preserve_literal_identity(self):
        rid = 'f_' + 'aB12' * 8
        row = {'id':rid,'name':'Synthetic f record','created_at':'2026-01-01T12:00:00Z'}
        file = dict(row, source_list=[{'data_type':'transaction','data_content':json.dumps([
            {'speaker':'A','start_time':0,'content':'Keep all 25 servers.'}])}], note_list=[])
        def response(method, params, **kwargs):
            self.assertEqual('tools/call',method)
            if params['name'] == 'list_files':
                data = {'page':1,'data':[row]}
            else:
                self.assertEqual('get_file',params['name'])
                self.assertEqual(rid,params['arguments']['file_id'])
                data = file
            return {'content':[{'type':'text','text':json.dumps(data)}]}
        source = provider.NativePlaud({})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'vault').mkdir()
            settings = dict(vault=str(root/'vault'),output='notes',state=str(root/'state'))
            translator = FixtureTranslator()
            with patch.object(source.transport,'_request',side_effect=response):
                first = engine.sync(settings,source,translator)
                self.assertEqual([],first['failed'])
                self.assertEqual(1,first['created'])
                target = Path(first['targets'][0])
                meta, _ = engine.read_note(target.read_bytes())
                self.assertEqual(rid,meta['plaud_record_id'])
                self.assertEqual([target],engine.existing_targets(target.parent)[(rid,'Transcript')])
                original = target.read_bytes(),target.stat().st_mtime_ns,translator.calls
                again = engine.sync(settings,source,translator)
                self.assertEqual(1,again['unchanged'])
                self.assertEqual((0,0),(again['created'],again['updated']))
                self.assertEqual(original,(target.read_bytes(),target.stat().st_mtime_ns,translator.calls))


if __name__ == '__main__':
    unittest.main()
