"""Offline boundaries only: never start a browser or call a real account."""
import base64
import json
import hashlib
import io
import os
from pathlib import Path
import socket
import tempfile
from urllib.error import HTTPError
import unittest
from unittest.mock import patch

RID = 'of_' + 'a' * 32
CARD = 'c_' + 'b' * 32
URL = 'https://files.plaud.ai/image.png?signature=DO-NOT-LOG'
from plaud_obsidian import web_media as web


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


class Response(io.BytesIO):
    status = 200
    def __init__(self, data=PNG, mime='image/png', **headers):
        super().__init__(data)
        self.headers = {'Content-Type': mime, **headers}


class DownloadTests(unittest.TestCase):
    def test_url_allowlist_rejects_credentials_local_and_arbitrary_hosts(self):
        self.assertTrue(hasattr(web, 'validate_image_url'), 'image download boundary is missing')
        for url in [URL, 'https://plaud-files.s3.us-east-1.amazonaws.com/a',
                    'https://apne1-prod-plaud-content-storage.s3-accelerate.amazonaws.com/a',
                    'https://plaud-files.oss-us-west-1.aliyuncs.com/a']:
            self.assertEqual(web.validate_image_url(url), url)
        for url in ['http://files.plaud.ai/a', 'https://files.plaud.ai:8443/a',
                    'https://u:secret@files.plaud.ai/a', 'https://127.0.0.1/a',
                    'https://files.plaud.ai.evil.com/a', 'https://arbitrary.s3.amazonaws.com/a',
                    'https://files.plaud.ai/a\r\nX:secret', 'https://files.plaud.ai/a#secret']:
            with self.subTest(url=url), self.assertRaisesRegex(RuntimeError, '^IMAGE_URL_UNSAFE$'):
                web.validate_image_url(url)

    def test_download_preserves_original_bytes_and_only_safe_etag(self):
        self.assertTrue(hasattr(web, 'download_image'), 'bounded image download is missing')
        class Opener:
            def open(self, request, timeout=None):
                self.request = request
                return Response(ETag='"opaque-123"')
        opener = Opener()
        with patch.object(web, 'build_opener', return_value=opener):
            self.assertEqual(web.download_image(URL), {'data': PNG, 'content_type': 'image/png', 'etag': '"opaque-123"'})
        self.assertEqual(opener.request.header_items(), [('Accept', 'image/png,image/jpeg,image/webp')])
        self.assertTrue(opener.request.full_url.endswith('signature=DO-NOT-LOG'))

    def test_bad_mime_size_and_transport_failure_are_sanitized(self):
        self.assertTrue(hasattr(web, 'download_image'), 'bounded image download is missing')
        for response in [Response(mime='image/svg+xml'), Response(**{'Content-Length': str(21 * 1024 * 1024)}),
                         Response(b'x' * 200), Response(**{'Content-Length': '-1'})]:
            with self.subTest(headers=response.headers), patch.object(web, 'build_opener') as build:
                build.return_value.open.return_value = response
                with self.assertRaisesRegex(RuntimeError, '^IMAGE_DOWNLOAD_INVALID$'):
                    web.download_image(URL)
        with patch.object(web, 'build_opener') as build:
            build.return_value.open.side_effect = OSError('SECRET ' + URL)
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_DOWNLOAD_FAILED$'):
                web.download_image(URL)

    def test_http_debug_output_cannot_leak_signed_urls(self):
        import http.client
        with patch.object(http.client.HTTPSConnection, 'debuglevel', 1), patch.object(web, 'build_opener') as build:
            build.return_value.open.return_value = Response()
            web.download_image(URL)
        handler = next(x for x in build.call_args.args if isinstance(x, web.PublicHTTPSHandler))
        self.assertEqual(handler._debuglevel, 0)

    def test_truncated_declared_length_is_rejected(self):
        with patch.object(web, 'build_opener') as build:
            build.return_value.open.return_value = Response(**{'Content-Length': str(len(PNG) + 1)})
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_DOWNLOAD_INVALID$'):
                web.download_image(URL)

    def test_stream_limit_without_content_length(self):
        self.assertTrue(hasattr(web, 'download_image'), 'bounded image download is missing')
        with patch.object(web, 'MAX_IMAGE_BYTES', 20), patch.object(web, 'build_opener') as build:
            build.return_value.open.return_value = Response(b'x' * 21)
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_DOWNLOAD_INVALID$'):
                web.download_image(URL)

    def test_redirect_handler_validates_destination_and_never_adds_auth(self):
        self.assertTrue(hasattr(web, 'ImageRedirect'), 'redirect boundary is missing')
        from urllib.request import Request
        redirect = web.ImageRedirect()
        req = Request(URL, headers={'Accept': 'image/png'})
        next_req = redirect.redirect_request(req, None, 302, 'moved', {}, 'https://files.plaud.ai/new')
        self.assertEqual(next_req.full_url, 'https://files.plaud.ai/new')
        for url in ['http://files.plaud.ai/new', 'https://localhost/new', 'https://evil.invalid/new']:
            with self.subTest(url=url), self.assertRaisesRegex(RuntimeError, '^IMAGE_URL_UNSAFE$'):
                redirect.redirect_request(req, None, 302, 'moved', {}, url)
        self.assertLessEqual(redirect.max_redirections, 3)

    def test_conditional_request_requires_verified_local_image_bytes(self):
        with patch.object(web, 'build_opener') as build:
            build.return_value.open.side_effect = HTTPError(URL, 304, 'SECRET', {}, None)
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_DOWNLOAD_FAILED$'):
                web.download_image(URL)
        self.assertIn('previous', __import__('inspect').signature(web.download_image).parameters,
                      'conditional image reuse is missing')
        previous = {'data': PNG, 'content_type': 'image/png', 'etag': '"opaque-123"'}
        with patch.object(web, 'build_opener') as build:
            build.return_value.open.side_effect = HTTPError(URL, 304, 'SECRET', {}, None)
            result = web.download_image(URL, previous)
            request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header('If-none-match'), '"opaque-123"')
        self.assertEqual(result, {'data': None, 'content_type': 'image/png', 'etag': '"opaque-123"'})
        with patch.object(web, 'build_opener') as build:
            build.return_value.open.return_value = Response(ETag='"https://secret/token"')
            result = web.download_image(URL, dict(previous, data=b'broken'))
            request = build.return_value.open.call_args.args[0]
        self.assertIsNone(request.get_header('If-none-match'))
        self.assertIsNone(result['etag'])

    def test_https_connection_pins_vetted_public_address(self):
        self.assertTrue(hasattr(web, 'PublicHTTPSConnection'), 'DNS/private IP boundary is missing')
        from unittest.mock import Mock
        for addresses in [[(socket.AF_INET, 1, 6, '', ('127.0.0.1', 443))],
                          [(socket.AF_INET6, 1, 6, '', ('::1', 443, 0, 0))],
                          [(socket.AF_INET, 1, 6, '', ('169.254.169.254', 443))]]:
            with self.subTest(addresses=addresses), patch.object(web.socket, 'getaddrinfo', return_value=addresses), patch.object(web.socket, 'create_connection') as connect:
                with self.assertRaisesRegex(RuntimeError, '^IMAGE_URL_UNSAFE$'):
                    web.PublicHTTPSConnection('files.plaud.ai').connect()
                connect.assert_not_called()
        tls = Mock()
        with patch.object(web.socket, 'getaddrinfo', return_value=[(socket.AF_INET, 1, 6, '', ('8.8.8.8', 443))]), patch.object(web.socket, 'create_connection') as connect:
            conn = web.PublicHTTPSConnection('files.plaud.ai', context=tls)
            conn.connect()
            self.assertEqual(connect.call_args.args[0], ('8.8.8.8', 443))
            self.assertEqual(tls.wrap_socket.call_args.kwargs['server_hostname'], 'files.plaud.ai')


PROFILE = '11111111-2222-4333-8444-555555555555'
ORIGINAL_URL = ('https://apne1-prod-plaud-content-storage.s3-accelerate.amazonaws.com/'
                'fixture/summary_poster/card_' + RID + '_original.png?signature=DO-NOT-LOG')


def cli_result(images=None, page_url=None):
    result = {'page_url': page_url or 'https://web.plaud.ai/file/' + RID,
              'images': images if images is not None else [
                  {'url': ORIGINAL_URL, 'width': 2800, 'height': 2315, 'srcset': False}]}
    from subprocess import CompletedProcess
    return CompletedProcess([], 0, json.dumps({'run': {'content': [
        {'type': 'text', 'text': 'PLAUD_IMAGE_RESULT:' + json.dumps(result)}], 'error': None}}), '')


class MediaAPITests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / 'test-sandboxes'
        scratch.mkdir(exist_ok=True)
        root = tempfile.TemporaryDirectory(dir=scratch, prefix='plaud-web-offline-')
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        (self.root / 'vault').mkdir()
        self.settings = {'state': str(self.root / 'private'), 'vault': str(self.root / 'vault'),
                         'output': 'Meetings', 'aside_profile': PROFILE}
        self.browser = web.WebMedia(self.settings)
        import sys
        import types
        from unittest.mock import Mock
        self.api = Mock()
        self.api.image_url.return_value = ORIGINAL_URL
        self.api_class = Mock(return_value=self.api)
        module = types.ModuleType('plaud_obsidian.workspace_api')
        module.__dict__['WorkspaceAPI'] = self.api_class
        modules = patch.dict(sys.modules, {'plaud_obsidian.workspace_api': module})
        modules.start()
        self.addCleanup(modules.stop)
        # Both network and process boundaries are inert; no account is accessed.
        self.exe = self.root / 'Aside/CLI/current/aside.exe'
        self.exe.parent.mkdir(parents=True)
        self.exe.touch()
        env = patch.dict(os.environ, {'LOCALAPPDATA': str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        run = patch.object(web.subprocess, 'run', return_value=cli_result())
        self.cli = run.start()
        self.addCleanup(run.stop)
        http = patch.object(web, 'build_opener')
        self.http = http.start()
        self.addCleanup(http.stop)
        self.http.return_value.open.return_value = Response()

    def read(self, card=CARD, **kwargs):
        return self.browser.read_summary_image(RID, card, expected_card_ids=[card], **kwargs)

    def test_api_mapping_is_used_without_any_browser_process(self):
        import sys
        import types
        from unittest.mock import Mock
        api = Mock()
        api.image_url.return_value = ORIGINAL_URL
        module = types.ModuleType('plaud_obsidian.workspace_api')
        module.__dict__['WorkspaceAPI'] = Mock(return_value=api)
        with patch.dict(sys.modules, {'plaud_obsidian.workspace_api': module}), \
                patch.object(web, '_aside', side_effect=AssertionError('browser use is forbidden')):
            result = self.read()
        self.assertEqual(result['data'], PNG)
        api.image_url.assert_called_once_with(RID, CARD)
        self.cli.assert_not_called()

    def test_repeated_original_reads_do_not_use_browser_processes(self):
        with self.browser as source:
            result = source.read_summary_image(RID, CARD, expected_card_ids=[CARD])
        self.assertEqual(result, {'data': PNG, 'content_type': 'image/png', 'etag': None})
        self.assertEqual(self.http.return_value.open.call_args.args[0].full_url, ORIGINAL_URL)
        self.http.return_value.open.return_value = Response()
        self.read()
        self.assertEqual(self.api.image_url.call_count, 2)
        self.cli.assert_not_called()

    def test_cli_rejects_failed_error_or_oversized_results_without_leaking_output(self):
        from subprocess import CompletedProcess
        valid = cli_result()
        with_error = json.loads(valid.stdout)
        with_error['run']['error'] = 'SECRET ' + ORIGINAL_URL
        for response in [CompletedProcess([], 1, valid.stdout, 'SECRET ' + ORIGINAL_URL),
                         CompletedProcess([], 0, json.dumps(with_error), ''),
                         CompletedProcess([], 0, valid.stdout + ' ' * 65536, '')]:
            with self.subTest(returncode=response.returncode):
                self.cli.return_value = response
                with self.assertRaisesRegex(web.ImageWebError, '^IMAGE_WEB_(FAILED|PROTOCOL)$') as error:
                    web._aside(self.settings, 'inert fixture')
                self.assertNotIn('SECRET', str(error.exception))
                self.assertNotIn(ORIGINAL_URL, str(error.exception))
        self.http.assert_not_called()

    def test_api_auth_errors_remain_actionable_without_browser_fallback(self):
        for code in ['IMAGE_WEB_LOGIN_REQUIRED', 'IMAGE_WEB_AUTH_INVALID', 'IMAGE_WEB_API_FAILED']:
            with self.subTest(code=code):
                self.api.image_url.side_effect = web.ImageWebError(code)
                with self.assertRaisesRegex(web.ImageWebError, '^' + code + '$'):
                    self.read()
        self.cli.assert_not_called()
        self.http.assert_not_called()

    def test_profile_is_optional_but_invalid_values_never_reach_cli(self):
        self.settings.pop('aside_profile')
        command = web._aside_command(self.settings)
        self.assertNotIn('--profile', command)
        self.assertNotIn(None, command)
        self.cli.reset_mock()
        for value in ['--host remote', '../other-profile', 'private\nprofile', 123, 0, False, []]:
            with self.subTest(value=value):
                self.settings['aside_profile'] = value
                with self.assertRaisesRegex(web.ImageWebError, '^IMAGE_WEB_PROFILE_INVALID$'):
                    web._aside_command(self.settings)
        self.cli.assert_not_called()

    def test_missing_web_login_opens_aside_without_claiming_authentication(self):
        self.assertTrue(hasattr(web, 'open_login'), 'external Aside login command is missing')
        from subprocess import CompletedProcess
        self.cli.return_value = CompletedProcess([], 0, '', '')
        with patch('plaud_obsidian.workspace_login.bootstrap_web_auth', side_effect=web.ImageWebError('IMAGE_WEB_LOGIN_REQUIRED')):
            result = web.open_login(self.settings)
        self.assertTrue(result['opened'])
        self.assertFalse(result['authenticated'])
        self.assertEqual(self.cli.call_args.args[0], [str(self.exe), '--host', 'local', '--profile',
                                                   PROFILE, 'https://web.plaud.ai/'])
        self.http.assert_not_called()

    def test_unsafe_api_url_cannot_reach_image_http(self):
        for url in ['http://files.plaud.ai/a', 'https://evil.invalid/image', 'https://127.0.0.1/a']:
            with self.subTest(url=url):
                self.api.image_url.return_value = url
                with self.assertRaisesRegex(web.ImageWebError, '^IMAGE_URL_UNSAFE$'):
                    self.read()
        self.http.assert_not_called()

    def test_legacy_card_uses_literal_api_identity_instead_of_filename_guessing(self):
        legacy_card = 'oc_su_' + 'a' * 32 + '_i1'
        legacy_url = ('https://apne1-prod-plaud-content-storage.s3-accelerate.amazonaws.com/'
                      'fixture/summary_poster/card_20260101010101-v2%40' + 'd' * 22
                      + '_20260101010202_abcdef12.png?X-Amz-Signature=DO-NOT-LOG')
        self.api.image_url.return_value = legacy_url
        result = self.read(legacy_card)
        self.assertEqual(result['data'], PNG)
        self.assertEqual(self.http.return_value.open.call_args.args[0].full_url, legacy_url)
        self.api.image_url.assert_called_once_with(RID, legacy_card)
        self.cli.assert_not_called()

    def test_literal_card_prefixes_and_ids_are_not_rewritten(self):
        for card in [CARD, 'oc_su_' + 'b' * 32 + '_i0']:
            with self.subTest(card=card):
                self.http.return_value.open.return_value = Response()
                self.assertEqual(self.read(card)['data'], PNG)
        self.cli.reset_mock()
        for rid, card in [(RID + '/', CARD), (RID, CARD + '?token=x'), ('of_' + RID, CARD)]:
            with self.subTest(rid=rid, card=card), self.assertRaises(ValueError):
                self.browser.read_summary_image(rid, card, expected_card_ids=[card])
        self.cli.assert_not_called()

    def test_current_single_card_contract_is_checked_before_api(self):
        for cards in [[], [CARD, 'o' + CARD], ['o' + CARD], CARD, None]:
            with self.subTest(cards=cards), self.assertRaisesRegex(web.ImageWebError, '^IMAGE_WEB_CARD_AMBIGUOUS$'):
                self.browser.read_summary_image(RID, CARD, expected_card_ids=cards)
        self.cli.assert_not_called()
        self.http.assert_not_called()

    def test_previous_receipt_requires_current_local_bytes_and_exact_card_path(self):
        self.assertTrue(hasattr(self.browser, '_previous'), 'local receipt validation is missing')
        target = self.root / 'vault/Meetings/_attachments/2026/11' / RID / (CARD + '.png')
        target.parent.mkdir(parents=True)
        target.write_bytes(PNG)
        receipt = {'path': target.relative_to(self.root / 'vault').as_posix(), 'card_id': CARD,
                   'sha256': hashlib.sha256(PNG).hexdigest(), 'byte_count': len(PNG),
                   'content_type': 'image/png', 'etag': '"local"'}
        self.assertEqual(self.browser._previous(RID, CARD, receipt),
                         {'data': PNG, 'content_type': 'image/png', 'etag': '"local"'})
        for previous in [dict(receipt, sha256='0' * 64), dict(receipt, card_id='o' + CARD),
                         dict(receipt, path='../private/secret'), dict(receipt, etag='"https://secret"')]:
            with self.subTest(previous=previous):
                self.assertIsNone(self.browser._previous(RID, CARD, previous))
        target.write_bytes(b'changed')
        self.assertIsNone(self.browser._previous(RID, CARD, receipt))


if __name__ == '__main__':
    unittest.main()
