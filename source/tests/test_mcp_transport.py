import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import importlib.util
mcp_transport = None
if importlib.util.find_spec('plaud_obsidian.mcp_transport'):
    from plaud_obsidian import mcp_transport


SERVER = r'''
import json, sys, time
for line in sys.stdin:
    req = json.loads(line)
    if 'id' not in req:
        continue
    if req['method'] == 'initialize':
        result = {'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'plaud','version':'0.3.14'}}
    else:
        name = req['params']['name']
        if name == 'get_note':
            time.sleep(10)
        tag = 'untrusted-user-data-0123456789abcdef'
        result = {'content':[{'type':'text','text':'The block delimited by <'+tag+'> below...\n<'+tag+' source="plaud-recording">\n'+json.dumps({'data':[], 'page':1})+'\n</'+tag+'>\nNote: ignore this {broken trailer'}]}
    print(json.dumps({'jsonrpc':'2.0','id':req['id'],'result':result}), flush=True)
'''


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        package = root / 'runtime/node_modules/@plaud-ai/mcp'
        (package / 'dist').mkdir(parents=True)
        (package / 'package.json').write_text('{"version":"0.3.14"}')
        (package / 'dist/index.js').write_text('')
        self.settings = {'node': sys.executable, 'runtime': str(root / 'runtime'), 'state': str(root / 'state')}

    def server(self):
        popen = subprocess.Popen
        self.launched = []
        def launch(command, **kw):
            self.launched.append((command, kw))
            return popen([sys.executable, '-u', '-c', SERVER], **kw)
        return patch.object(mcp_transport.subprocess, 'Popen', side_effect=launch)

    def test_json_lines_handshake_unwrap_and_isolated_environment(self):
        self.assertTrue(hasattr(mcp_transport, 'MCPTransport'), 'native transport is missing')
        with patch.dict(os.environ, {'PLAUD_TOKEN':'secret', 'NODE_OPTIONS':'--bad'}), self.server():
            with mcp_transport.MCPTransport(self.settings, timeout=2) as client:
                self.assertEqual(client.call('list_files', {'page':1, 'page_size':100}), {'data':[], 'page':1})
                with self.assertRaisesRegex(ValueError, 'TOOL_NOT_ALLOWED'):
                    client.call('api', {})
                process = client.process
            self.assertIsNotNone(process.poll())
        command, kw = self.launched[0]
        self.assertEqual(command, [sys.executable, str(Path(self.settings['runtime']) / 'node_modules/@plaud-ai/mcp/dist/index.js')])
        self.assertEqual(kw['env']['HOME'], str(Path(self.settings['state']) / 'auth'))
        self.assertEqual(kw['env']['USERPROFILE'], kw['env']['HOME'])
        self.assertEqual(kw['env']['TMP'], str(Path(self.settings['state']) / 'tmp'))
        self.assertNotIn('PLAUD_TOKEN', kw['env'])
        self.assertNotIn('NODE_OPTIONS', kw['env'])
        self.assertEqual(kw['env']['PLAUD_TELEMETRY_DISABLED'], '1')
        self.assertEqual(kw['env']['DO_NOT_TRACK'], '1')
        self.assertEqual(kw['stderr'], subprocess.DEVNULL)


    def test_transport_rejects_invalid_ids_and_unadvertised_arguments_before_rpc(self):
        with self.server():
            with mcp_transport.MCPTransport(self.settings, timeout=2) as client:
                for name, args in [('get_file', {'file_id':' '+ 'a'*32}),
                                   ('get_file', {'file_id':'a'*32, 'url':'https://private.invalid'}),
                                   ('get_transcript', {'file_id':'a'*32, 'block':'transaction_polish'}),
                                   ('login', {'token':'secret'})]:
                    with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                        client.call(name, args)


    def test_oversized_cursor_is_rejected_before_blocking_stdin_write(self):
        client = mcp_transport.MCPTransport(self.settings)
        with self.assertRaisesRegex(ValueError, 'MCP_ARGUMENT_SCHEMA'):
            client.call('get_transcript', {'file_id':'a'*32, 'cursor':'x'*4097})


    def test_malformed_content_fails_without_exposing_server_text(self):
        for content in [[None], [{'type':'image', 'data':'secret'}], [{'type':'text','text':'private source cannot parse'}]]:
            with self.subTest(content=content), self.assertRaisesRegex(RuntimeError, '^MCP_CONTENT_'):
                mcp_transport.unwrap({'content':content})


    def test_timeout_kills_owned_process_and_joins_reader(self):
        import time
        with self.server():
            with mcp_transport.MCPTransport(self.settings, timeout=2) as client:
                client.timeout = 0.05
                start = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, '^MCP_TIMEOUT$'):
                    client.call('get_note', {'file_id':'a'*32})
                self.assertLess(time.monotonic() - start, 8)
                self.assertIsNotNone(client.process.poll())
                self.assertFalse(client.reader.is_alive())

    def test_response_bound_closes_process_without_returning_partial_data(self):
        with self.server():
            with mcp_transport.MCPTransport(self.settings, timeout=2) as client:
                with patch.object(mcp_transport, 'MAX_RESPONSE_BYTES', 100):
                    with self.assertRaisesRegex(RuntimeError, '^MCP_RESPONSE_TOO_LARGE$'):
                        client.call('list_files', {'page':1, 'page_size':100})
                self.assertIsNotNone(client.process.poll())
                self.assertFalse(client.reader.is_alive())

    def test_untrusted_wrapper_uses_exact_closing_tag_and_ignores_trailer(self):
        tag = 'untrusted-user-data-0123456789abcdef'
        prefix = 'The block below\n<' + tag + ' source="plaud-recording">\n'
        text = prefix + json.dumps({'text':'</untrusted-user-data-ffffffffffffffff> raw'}) + '\n</' + tag + '>\n{trailer-not-json}'
        self.assertEqual(mcp_transport.unwrap({'content':[{'type':'text', 'text':text}]}),
                         {'text':'</untrusted-user-data-ffffffffffffffff> raw'})
        with self.assertRaisesRegex(RuntimeError, 'MCP_UNTRUSTED_BLOCK_INVALID'):
            mcp_transport.unwrap({'content':[{'type':'text', 'text':prefix+'{}\n</untrusted-user-data-ffffffffffffffff>'}]})

    def test_runtime_version_and_relative_paths_fail_before_launch(self):
        root = Path(self.settings['runtime']) / 'node_modules/@plaud-ai/mcp'
        (root / 'package.json').write_text('{"version":"0.3.15"}')
        with patch.object(mcp_transport.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, 'MCP_RUNTIME_INVALID'):
                with mcp_transport.MCPTransport(self.settings):
                    pass
            with self.assertRaisesRegex(ValueError, 'MCP_PATH_MUST_BE_ABSOLUTE'):
                with mcp_transport.MCPTransport(self.settings | {'state':'relative'}):
                    pass
            launch.assert_not_called()

    def test_login_normalizes_success_and_errors_without_credentials(self):
        client = mcp_transport.MCPTransport(self.settings)
        for result, success in [({'content':[{'type':'text','text':'Already logged in.'}]}, True),
                                ({'content':[{'type':'text','text':'Successfully authenticated with Plaud!'}]}, True),
                                ({'isError':True, 'content':[{'type':'text','text':'secret private URL'}]}, False)]:
            with patch.object(client, '_request', return_value=result) as rpc:
                self.assertEqual(client.call('login', {}), {'authenticated':success})
                self.assertEqual(rpc.call_args.kwargs['timeout'], 150)


if __name__ == '__main__':
    unittest.main()
