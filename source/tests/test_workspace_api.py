"""Artificial credentials only; no browser or live network."""
import importlib.util
from email.message import Message
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class FixtureCase(unittest.TestCase):
    def setUp(self):
        from hermes_constants import get_hermes_home
        scratch = Path(os.environ.get('TMPDIR') or get_hermes_home() / 'cache/scratch')
        scratch.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'vault').mkdir()
        self.settings = {'vault': str(root / 'vault'), 'output': 'notes', 'state': str(root / 'state')}
        network = patch('socket.socket.connect', side_effect=AssertionError('live network forbidden'))
        network.start()
        self.addCleanup(network.stop)
        if os.name != 'nt' and importlib.util.find_spec('plaud_obsidian.workspace_api'):
            from plaud_obsidian import workspace_api
            codec = patch.object(workspace_api, '_dpapi', side_effect=lambda data, decrypt=False: data[::-1])
            codec.start()
            self.addCleanup(codec.stop)

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('plaud_obsidian.workspace_api'), 'workspace credential store is missing')
        from plaud_obsidian import workspace_api
        return workspace_api

    def response(self, **changes):
        return dict(workspace_id='ws_artificial', workspace_token='ARTIFICIAL-ACCESS-SECRET',
                    refresh_token='ARTIFICIAL-REFRESH-SECRET', expires_in=86400,
                    refresh_expires_in=2592000, **changes)


class StoreTests(FixtureCase):
    def test_real_dpapi_save_is_opaque_and_roundtrips(self):
        api = self.module()
        if os.name != 'nt':
            self.skipTest('Windows DPAPI only')
        result = api.save_auth(self.settings, 'https://api-apne1.plaud.ai', 'ws_artificial', self.response())
        path = Path(self.settings['state']) / 'web-auth' / 'credentials.bin'
        cipher = path.read_bytes()
        self.assertNotIn(b'ARTIFICIAL', cipher)
        self.assertLessEqual(len(cipher), 65536)
        self.assertNotIn('SECRET', repr(result))
        auth = api._load_auth(path)
        self.assertEqual(auth['access_token'], 'ARTIFICIAL-ACCESS-SECRET')
        self.assertEqual(auth['refresh_token'], 'ARTIFICIAL-REFRESH-SECRET')
        self.assertEqual(auth['workspace_id'], 'ws_artificial')
        self.assertEqual(auth['version'], 1)
        self.assertEqual({p.name for p in path.parent.iterdir()}, {'credentials.bin', 'writer.lock'})


RID = 'f_' + 'a' * 32
CARD = 'c_' + 'b' * 32
URL = 'https://files.plaud.ai/original.png?signature=ARTIFICIAL'
ORIGIN = 'https://api-apne1.plaud.ai'


class APITests(FixtureCase):
    def boot(self, **changes):
        api = self.module()
        response = self.response()
        response.update(changes)
        api.save_auth(self.settings, ORIGIN, 'ws_artificial', response)
        return api

    def detail(self, rid=RID, card=CARD):
        return {'status': 0, 'data': {'meta': {'file_id': rid}, 'relation_content_mapping': {card: URL}}}

    def transport(self, api, replies):
        import io
        calls = []
        class Response(io.BytesIO):
            status = 200
            headers = {}
        class Opener:
            def open(self, request, timeout=None):
                calls.append(request)
                reply = replies.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return Response(json.dumps(reply).encode())
        return calls, patch.object(api, 'build_opener', return_value=Opener())

    def test_valid_get_uses_literal_ids_scope_and_truthful_user_agent(self):
        api = self.boot()
        self.assertTrue(hasattr(api, 'WorkspaceAPI'), 'browser-free workspace client is missing')
        for rid, card in [(RID, CARD), ('of_' + 'c' * 32, 'oc_su_' + 'c' * 32 + '_i1'), ('D' * 32, CARD)]:
            calls, transport = self.transport(api, [self.detail(rid, card)])
            with transport, patch('socket.create_connection', side_effect=AssertionError('live network forbidden')), \
                    patch('subprocess.run', side_effect=AssertionError('browser forbidden')):
                self.assertEqual(api.WorkspaceAPI(self.settings).image_url(rid, card), URL)
            request = calls[0]
            self.assertEqual(request.full_url, ORIGIN + '/file-app/v4/files/detail/' + rid)
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(dict(request.header_items()), {'Authorization': 'Bearer ARTIFICIAL-ACCESS-SECRET',
                             'Accept': 'application/json', 'X-scope-type': 'workspace',
                             'X-scope-id': 'ws_artificial', 'User-agent': 'Hermes-PLAUD-Obsidian (direct API client)'})

    def test_exact_meta_and_card_mapping_required(self):
        api = self.boot()
        self.assertTrue(hasattr(api, 'WorkspaceAPI'), 'browser-free workspace client is missing')
        variants = [self.detail('f_' + 'c' * 32), self.detail(card='c_' + 'c' * 32),
                    {'status': 0, 'data': {}}, {'status': 1, 'message': 'ARTIFICIAL-ACCESS-SECRET'},
                    {'status': False, 'data': self.detail()['data']},
                    {'status': '-419', 'message': 'ARTIFICIAL-ACCESS-SECRET'},
                    {'status': 403, 'message': 'ARTIFICIAL-ACCESS-SECRET'},
                    {'status': 429, 'message': 'ARTIFICIAL-ACCESS-SECRET'},
                    {'status': 0, 'data': {'meta': {'file_id': RID}, 'relation_content_mapping': {CARD: 'https://evil.invalid/secret'}}}]
        for reply in variants:
            with self.subTest(reply=reply):
                calls, transport = self.transport(api, [reply])
                with transport, self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_RESPONSE_INVALID$'):
                    api.WorkspaceAPI(self.settings).image_url(RID, CARD)
                self.assertEqual(len(calls), 1)


    def refresh_reply(self, **changes):
        data = self.response()
        data.update(workspace_token='ARTIFICIAL-ROTATED-ACCESS', refresh_token='ARTIFICIAL-ROTATED-REFRESH')
        data.update(changes)
        return {'status': 0, 'data': data}

    def test_expiring_access_refreshes_once_then_gets_with_rotated_credentials(self):
        api = self.boot(expires_in=30)
        calls, transport = self.transport(api, [self.refresh_reply(), self.detail()])
        with transport:
            self.assertEqual(api.WorkspaceAPI(self.settings).image_url(RID, CARD), URL)
        self.assertEqual([r.get_method() for r in calls], ['POST', 'GET'])
        self.assertEqual(calls[0].full_url, ORIGIN + '/user-app/auth/workspace/refresh/ws_artificial')
        self.assertEqual(calls[0].data, b'{}')
        self.assertEqual(calls[0].get_header('Authorization'), 'Bearer ARTIFICIAL-REFRESH-SECRET')
        self.assertEqual(calls[0].get_header('X-scope-type'), 'workspace')
        self.assertEqual(calls[0].get_header('X-scope-id'), 'ws_artificial')
        self.assertEqual(calls[1].get_header('Authorization'), 'Bearer ARTIFICIAL-ROTATED-ACCESS')
        saved = api._load_auth(Path(self.settings['state']) / 'web-auth/credentials.bin')
        self.assertEqual(saved['refresh_token'], 'ARTIFICIAL-ROTATED-REFRESH')

    def test_refresh_omitted_fields_preserve_previous_and_save_before_failed_get(self):
        api = self.boot(expires_in=30)
        path = Path(self.settings['state']) / 'web-auth/credentials.bin'
        previous = api._load_auth(path)
        reply = {'status': 0, 'data': {'access_token': 'ARTIFICIAL-ROTATED-ACCESS', 'expires_in': 86400}}
        calls, transport = self.transport(api, [reply, OSError('ARTIFICIAL-ACCESS-SECRET')])
        from plaud_obsidian import engine
        original = engine.atomic_write
        events = []
        def write(target, data):
            events.append(len(calls))
            return original(target, data)
        with transport, patch.object(engine, 'atomic_write', side_effect=write), \
                self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_API_FAILED$'):
            api.WorkspaceAPI(self.settings).image_url(RID, CARD)
        saved = api._load_auth(path)
        self.assertEqual(events, [1])
        self.assertEqual(saved['access_token'], 'ARTIFICIAL-ROTATED-ACCESS')
        self.assertEqual(saved['refresh_token'], previous['refresh_token'])
        self.assertEqual(saved['refresh_expires_at'], previous['refresh_expires_at'])

    def test_workspace_expiry_envelope_refreshes_before_local_ttl(self):
        api = self.boot()
        calls, transport = self.transport(api, [
            {'status': -419, 'message': 'ARTIFICIAL-ACCESS-SECRET'},
            self.refresh_reply(), self.detail()])
        with transport, patch('subprocess.run', side_effect=AssertionError('browser forbidden')):
            self.assertEqual(api.WorkspaceAPI(self.settings).image_url(RID, CARD), URL)
        self.assertEqual([r.get_method() for r in calls], ['GET', 'POST', 'GET'])
        self.assertEqual(calls[1].get_header('Authorization'), 'Bearer ARTIFICIAL-REFRESH-SECRET')
        self.assertEqual(calls[2].get_header('Authorization'), 'Bearer ARTIFICIAL-ROTATED-ACCESS')
        saved = api._load_auth(Path(self.settings['state']) / 'web-auth/credentials.bin')
        self.assertEqual(saved['refresh_token'], 'ARTIFICIAL-ROTATED-REFRESH')

    def test_http_401_get_refresh_is_bounded(self):
        from urllib.error import HTTPError
        api = self.boot()
        def unauthorized():
            return HTTPError(ORIGIN, 401, 'ARTIFICIAL-ACCESS-SECRET', Message(), None)
        calls, transport = self.transport(api, [unauthorized(), self.refresh_reply(), unauthorized()])
        with transport, self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_LOGIN_REQUIRED$'):
            api.WorkspaceAPI(self.settings).image_url(RID, CARD)
        self.assertEqual([r.get_method() for r in calls], ['GET', 'POST', 'GET'])

    def test_workspace_expiry_retry_is_bounded_and_keeps_rotated_auth(self):
        api = self.boot()
        calls, transport = self.transport(api, [
            {'status': -419}, self.refresh_reply(), {'status': -419}])
        with transport, self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_LOGIN_REQUIRED$') as caught:
            api.WorkspaceAPI(self.settings).image_url(RID, CARD)
        self.assertEqual([r.get_method() for r in calls], ['GET', 'POST', 'GET'])
        self.assertNotIn('ARTIFICIAL', str(caught.exception))
        saved = api._load_auth(Path(self.settings['state']) / 'web-auth/credentials.bin')
        self.assertEqual(saved['refresh_token'], 'ARTIFICIAL-ROTATED-REFRESH')

    def test_expiry_after_proactive_refresh_does_not_refresh_twice(self):
        api = self.boot(expires_in=30)
        calls, transport = self.transport(api, [self.refresh_reply(), {'status': -419}])
        with transport, self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_LOGIN_REQUIRED$'):
            api.WorkspaceAPI(self.settings).image_url(RID, CARD)
        self.assertEqual([r.get_method() for r in calls], ['POST', 'GET'])

    def test_invalid_refresh_requires_login_without_overwriting_auth(self):
        from urllib.error import HTTPError
        api = self.boot(expires_in=30)
        path = Path(self.settings['state']) / 'web-auth/credentials.bin'
        previous = path.read_bytes()
        for reply in [HTTPError(ORIGIN, 401, 'secret', Message(), None), {'status': 401, 'message': 'secret'},
                      {'status': -419, 'message': 'secret'}, {'status': -420, 'message': 'secret'},
                      self.refresh_reply(expires_in=False), self.refresh_reply(workspace_id='other')]:
            calls, transport = self.transport(api, [reply])
            with transport, self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_LOGIN_REQUIRED$'):
                api.WorkspaceAPI(self.settings).image_url(RID, CARD)
            self.assertEqual(len(calls), 1)
            self.assertEqual(path.read_bytes(), previous)

    def test_expired_refresh_requires_login_without_http(self):
        api = self.module()
        with patch.object(api.time, 'time', return_value=1000000):
            self.boot(refresh_expires_in=1)
        with patch.object(api.time, 'time', return_value=1000002), patch.object(api, 'build_opener') as build, \
                self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_LOGIN_REQUIRED$'):
            api.WorkspaceAPI(self.settings).image_url(RID, CARD)
        build.assert_not_called()


    def test_path_reparse_rejection_is_auth_invalid_before_read_or_lock(self):
        api = self.boot()
        with patch.object(api, '_plain_path', side_effect=api.ImageWebError('IMAGE_WEB_BROWSER_OWNERSHIP')), \
                patch.object(Path, 'open') as read, patch.object(api, 'build_opener') as build:
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_INVALID$'):
                api.WorkspaceAPI(self.settings).image_url(RID, CARD)
        read.assert_not_called()
        build.assert_not_called()

    def test_missing_corrupt_oversized_and_dpapi_failure_are_safe(self):
        api = self.module()
        with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_LOGIN_REQUIRED$'):
            api.WorkspaceAPI(self.settings).image_url(RID, CARD)
        path = Path(self.settings['state']) / 'web-auth/credentials.bin'
        for raw in (b'not a DPAPI blob ARTIFICIAL-SECRET', b'x' * 65537, b''):
            path.write_bytes(raw)
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_INVALID$'):
                api.WorkspaceAPI(self.settings).image_url(RID, CARD)
        self.boot()
        before = path.read_bytes()
        with patch.object(api, '_dpapi', side_effect=OSError('ARTIFICIAL-SECRET')):
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_INVALID$'):
                api.WorkspaceAPI(self.settings).image_url(RID, CARD)
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_INVALID$'):
                api.save_auth(self.settings, ORIGIN, 'ws_artificial', self.response())
        self.assertEqual(path.read_bytes(), before)

    def test_credential_size_checked_before_decryption(self):
        api = self.boot()
        path = Path(self.settings['state']) / 'web-auth/credentials.bin'
        path.write_bytes(b'x' * 65537)
        with patch.object(api, '_dpapi') as decrypt, self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_INVALID$'):
            api.WorkspaceAPI(self.settings).image_url(RID, CARD)
        decrypt.assert_not_called()

    def test_bad_ids_fail_before_loading_auth_or_network(self):
        api = self.module()
        for rid, card, code in [(' ' + RID, CARD, 'PLAUD_ID_INVALID'), (RID, 'c_bad', 'INVALID_SUMMARY_CARD_ID')]:
            with patch.object(api, '_auth_path') as load, self.assertRaisesRegex(ValueError, '^' + code + '$'):
                api.WorkspaceAPI(self.settings).image_url(rid, card)
            load.assert_not_called()

    def test_bad_origins_tokens_ttl_and_workspace_preserve_good_state(self):
        api = self.boot()
        path = Path(self.settings['state']) / 'web-auth/credentials.bin'
        before = path.read_bytes()
        origins = ['http://api.plaud.ai', 'https://api.plaud.ai/', 'https://api.plaud.ai:443',
                   'https://API.plaud.ai', 'https://api.plaud.ai?secret', 'https://api.plaud.ai#secret',
                   'https://user:secret@api.plaud.ai', 'https://api.plaud.ai.evil.invalid',
                   'https://web.plaud.ai', 'https://api.plaud.ai\n']
        responses = [dict(self.response(), **change) for change in (
            {'workspace_token': ''}, {'workspace_token': 'bad\r\nsecret'}, {'refresh_token': None},
            {'workspace_id': 'another'}, {'expires_in': True}, {'expires_in': float('nan')},
            {'expires_in': float('inf')}, {'expires_in': '86400'}, {'expires_in': -1},
            {'refresh_expires_in': False}, {'refresh_expires_in': 0})]
        for origin, response in [(origin, self.response()) for origin in origins] + [(ORIGIN, r) for r in responses]:
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_INVALID$'):
                api.save_auth(self.settings, origin, 'ws_artificial', response)
            self.assertEqual(path.read_bytes(), before)

    def test_null_workspace_token_uses_access_token_but_empty_does_not(self):
        api = self.boot(workspace_token=None, access_token='ARTIFICIAL-ALTERNATE')
        path = Path(self.settings['state']) / 'web-auth/credentials.bin'
        self.assertEqual(api._load_auth(path)['access_token'], 'ARTIFICIAL-ALTERNATE')
        with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_INVALID$'):
            self.boot(workspace_token='', access_token='ARTIFICIAL-ALTERNATE')

    def test_auth_lock_busy_is_sanitized_and_does_not_touch_credentials(self):
        api = self.boot()
        from plaud_obsidian.engine import writer_lock
        path = Path(self.settings['state']) / 'web-auth/credentials.bin'
        before = path.read_bytes()
        with writer_lock(path.parent):
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_BUSY$'):
                api.WorkspaceAPI(self.settings).image_url(RID, CARD)
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_BUSY$'):
                self.boot()
        self.assertEqual(path.read_bytes(), before)

    def test_transport_errors_are_single_attempt_and_never_delete_auth(self):
        from urllib.error import HTTPError
        api = self.boot()
        path = Path(self.settings['state']) / 'web-auth/credentials.bin'
        before = path.read_bytes()
        for error in [HTTPError(ORIGIN, 429, 'ARTIFICIAL-SECRET', Message(), None),
                      HTTPError(ORIGIN, 503, 'ARTIFICIAL-SECRET', Message(), None), OSError('ARTIFICIAL-SECRET')]:
            calls, transport = self.transport(api, [error])
            with transport, self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_API_FAILED$') as caught:
                api.WorkspaceAPI(self.settings).image_url(RID, CARD)
            self.assertNotIn('ARTIFICIAL', str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)
            self.assertEqual(len(calls), 1)
            self.assertEqual(path.read_bytes(), before)

    def test_http_boundaries_use_no_proxies_public_tls_and_fail_closed_redirects(self):
        api = self.boot()
        calls, transport = self.transport(api, [self.detail()])
        with transport as build:
            api.WorkspaceAPI(self.settings).image_url(RID, CARD)
        handlers = build.call_args.args
        proxy = next(h for h in handlers if isinstance(h, api.ProxyHandler))
        self.assertEqual(vars(proxy)['proxies'], {})
        https = next(h for h in handlers if isinstance(h, api.PublicHTTPSHandler))
        self.assertEqual(vars(https)['_debuglevel'], 0)
        redirect = next(h for h in handlers if isinstance(h, api._NoRedirect))
        for target in [ORIGIN + '/next', 'https://evil.invalid/secret']:
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_API_FAILED$'):
                redirect.redirect_request(calls[0], None, 302, 'ARTIFICIAL-SECRET', {}, target)

    def test_response_body_is_bounded_and_malformed_json_sanitized(self):
        import io
        api = self.boot()
        for raw in [b'x' * (api.MAX_API_BYTES + 1), b'ARTIFICIAL-SECRET', b'[]']:
            class Response(io.BytesIO):
                status = 200
            with patch.object(api, 'build_opener') as build:
                build.return_value.open.return_value = Response(raw)
                with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_RESPONSE_INVALID$'):
                    api.WorkspaceAPI(self.settings).image_url(RID, CARD)
            self.assertEqual(build.return_value.open.call_args.kwargs['timeout'], 15)


    def test_authenticated_plaintext_schema_is_revalidated_before_http(self):
        api = self.boot()
        path = Path(self.settings['state']) / 'web-auth/credentials.bin'
        good = api._load_auth(path)
        for change in [{'version': True}, {'expires_at': False}, {'refresh_expires_at': float('nan')},
                       {'api_domain': 'https://evil.invalid'}, {'workspace_id': '../secret'},
                       {'access_token': ''}, {'refresh_token': None}]:
            raw = json.dumps(dict(good, **change)).encode()
            path.write_bytes(api._dpapi(raw))
            with patch.object(api, 'build_opener') as build, \
                    self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_INVALID$'):
                api.WorkspaceAPI(self.settings).image_url(RID, CARD)
            build.assert_not_called()

    def test_no_dpapi_fails_closed_without_plaintext_fallback(self):
        api = self.module()
        with patch.object(api.os, 'name', 'posix'), self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_INVALID$'):
            api._dpapi(b'ARTIFICIAL-SECRET')
        self.assertFalse(Path(self.settings['state']).exists())

    def test_expiry_refresh_does_not_refresh_again_after_401(self):
        from urllib.error import HTTPError
        api = self.boot(expires_in=30)
        calls, transport = self.transport(api, [self.refresh_reply(), HTTPError(ORIGIN, 401, 'secret', Message(), None)])
        with transport, self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_LOGIN_REQUIRED$'):
            api.WorkspaceAPI(self.settings).image_url(RID, CARD)
        self.assertEqual([r.get_method() for r in calls], ['POST', 'GET'])

    def test_actual_junction_auth_directory_is_rejected(self):
        if os.name != 'nt':
            self.skipTest('Windows junction only')
        import _winapi
        api = self.module()
        state = Path(self.settings['state'])
        state.mkdir()
        target = state.parent / 'junction-target'
        target.mkdir()
        (target / 'credentials.bin').write_bytes(b'ARTIFICIAL-SECRET')
        junction = state / 'web-auth'
        _winapi.CreateJunction(str(target), str(junction))
        try:
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_INVALID$'):
                api.WorkspaceAPI(self.settings).image_url(RID, CARD)
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_INVALID$'):
                self.boot()
            self.assertEqual((target / 'credentials.bin').read_bytes(), b'ARTIFICIAL-SECRET')
            self.assertFalse((target / 'writer.lock').exists())
        finally:
            junction.rmdir()


if __name__ == '__main__':
    unittest.main()
