import unittest
from unittest.mock import patch

from plaud_obsidian import engine
from plaud_obsidian.engine import safe_error
from plaud_obsidian.mcp_transport import MCPToolError, unwrap
from plaud_obsidian.provider import NativePlaud


def error_result(text):
    return {'isError': True, 'content': [{'type': 'text', 'text': text}]}


class SafeMCPErrorTests(unittest.TestCase):
    def test_inventory_auth_error_reaches_caller_with_constant_diagnostic(self):
        source = NativePlaud({})
        result = error_result('Failed to list files: Error: Not authenticated. Please login first.')
        with patch.object(source.transport, '_request', return_value=result):
            with self.assertRaises(MCPToolError) as caught:
                source.list_records()
        exc = caught.exception
        self.assertEqual(str(exc), 'PLAUD_AUTH_REQUIRED')
        self.assertTrue(hasattr(exc, 'diagnostic'))
        self.assertEqual(exc.diagnostic, {
            'code': 'PLAUD_AUTH_REQUIRED',
            'message': 'PLAUD authentication required; log in to the configured MCP account.',
            'tool': 'list_files', 'auth_required': True, 'retryable': False,
        })
        self.assertEqual(engine.error_diagnostic(exc), exc.diagnostic)
        self.assertEqual(safe_error(exc), 'PLAUD_AUTH_REQUIRED: ' + exc.diagnostic['message'])
        self.assertEqual(exc.args, ('PLAUD_AUTH_REQUIRED',))


    def test_known_http_errors_emit_only_allowlisted_status_and_message(self):
        from plaud_obsidian.mcp_transport import unwrap
        for status, phrase, code, retryable in [
            (401, 'Unauthorized', 'PLAUD_AUTH_REQUIRED', False),
            (403, 'Forbidden', 'PLAUD_HTTP_ERROR', False),
            (404, 'Not Found', 'PLAUD_HTTP_ERROR', False),
            (408, 'Request Timeout', 'PLAUD_HTTP_ERROR', True),
            (429, 'Too Many Requests', 'PLAUD_RATE_LIMITED', True),
            (500, 'Internal Server Error', 'PLAUD_HTTP_ERROR', True),
            (502, 'Bad Gateway', 'PLAUD_HTTP_ERROR', True),
            (503, 'Service Unavailable', 'PLAUD_HTTP_ERROR', True),
            (504, 'Gateway Timeout', 'PLAUD_HTTP_ERROR', True),
        ]:
            with self.subTest(status=status), self.assertRaises(MCPToolError) as caught:
                unwrap(error_result(f'Failed to list files: Error: API error: {status} {phrase}'), tool='list_files')
            diagnostic = engine.error_diagnostic(caught.exception)
            self.assertEqual(str(caught.exception), code)
            self.assertEqual(diagnostic['status'], status)
            self.assertIs(diagnostic['retryable'], retryable)
            self.assertNotIn(phrase, diagnostic['message'])


    def test_known_network_error_uses_constant_message_without_cause(self):
        from plaud_obsidian.mcp_transport import unwrap
        with self.assertRaises(MCPToolError) as caught:
            unwrap(error_result('Failed to list files: TypeError: fetch failed'), tool='list_files')
        self.assertEqual(engine.error_diagnostic(caught.exception), {
            'code': 'PLAUD_NETWORK_ERROR', 'message': 'PLAUD network request failed; check connectivity and retry.',
            'tool': 'list_files', 'retryable': True,
        })


    def test_unknown_payloads_never_escape_exception_or_diagnostic(self):
        import json
        secret = 'private-meeting Bearer token-secret https://private.invalid/?token=secret'
        for result in [None, [], error_result(secret), error_result('Failed to list files: Error: ' + secret),
                       error_result('Failed to list files: Error: API error: 401 ' + secret),
                       error_result('Failed to list files: TypeError: fetch failed | cause: ' + secret),
                       {'isError': True, 'content': [None]},
                       {'isError': True, 'content': [{'type': 'text', 'text': secret}] * 2},
                       {'isError': True, 'content': [{'type': 'text', 'text': {'secret': secret}}]}]:
            with self.subTest(result=result), self.assertRaises(MCPToolError) as caught:
                unwrap(result, tool=secret)
            exc = caught.exception
            self.assertEqual(str(exc), 'MCP_TOOL_ERROR')
            self.assertEqual(engine.error_diagnostic(exc), {
                'code': 'MCP_TOOL_ERROR', 'message': 'PLAUD MCP tool failed; no recognized safe diagnostic.', 'tool': None,
            })
            self.assertNotIn(secret, repr(exc) + safe_error(exc) + json.dumps(vars(exc)))
            diagnostic = exc.diagnostic
            diagnostic['message'] = secret
            self.assertNotIn(secret, json.dumps(engine.error_diagnostic(exc)))

    def test_untrusted_exception_diagnostic_is_not_read(self):
        class UntrustedError(RuntimeError):
            @property
            def diagnostic(self):
                raise AssertionError('arbitrary diagnostic attribute was read')
        exc = UntrustedError('private meeting text')
        self.assertEqual(engine.error_diagnostic(exc), {})
        self.assertEqual(safe_error(exc), 'UntrustedError')
        self.assertEqual(safe_error(RuntimeError('MCP_TIMEOUT')), 'MCP_TIMEOUT')
        self.assertEqual(safe_error(ValueError('INVALID_RECORD_ID')), 'INVALID_RECORD_ID')

    def test_record_failure_report_persists_safe_structured_diagnostic(self):
        import json
        import os
        from pathlib import Path
        import tempfile
        from test_engine import FixtureSource, FixtureTranslator

        source = FixtureSource()
        def fail(row):
            return unwrap(error_result('Failed to get file: Error: Not authenticated. Please login first.'), tool='get_file')
        source.read_record = fail
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as directory:
            root = Path(directory)
            (root / 'vault').mkdir()
            settings = {'vault': str(root / 'vault'), 'output': 'notes', 'state': str(root / 'state')}
            report = engine.sync(settings, source, FixtureTranslator())
            self.assertIn('diagnostic', report['failed'][0])
            self.assertEqual(report['failed'][0]['diagnostic']['code'], 'PLAUD_AUTH_REQUIRED')
            self.assertEqual(report['failed'][0]['diagnostic']['tool'], 'get_file')
            self.assertEqual(json.loads((root / 'state/last-report.json').read_text()), report)
            self.assertEqual(report['targets'], [])


if __name__ == '__main__':
    unittest.main()
