"""Artificial credentials exercise the normal-issuance transfer, never an account."""
import base64
import importlib
import json
import sys
import types
import unittest
from unittest.mock import patch, MagicMock


class WorkspaceLoginTests(unittest.TestCase):
    def test_bad_envelope_cannot_publish_credentials_or_leak_response(self):
        module = importlib.import_module('plaud_obsidian.workspace_login')
        transport = MagicMock()
        transport.__enter__.return_value.call.return_value = {'workspace_id': 'ws_fixture', 'member_id': 'mem_fixture'}
        store = types.ModuleType('plaud_obsidian.workspace_api')
        save = MagicMock()
        store.__dict__['save_auth'] = save
        with patch.object(module, 'MCPTransport', return_value=transport), \
                patch.object(module, '_sealed_auth', return_value={'authenticated': True, 'wrapped': 'ARTIFICIAL-DO-NOT-EMIT'}), \
                patch.dict(sys.modules, {'plaud_obsidian.workspace_api': store}):
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_INVALID$') as error:
                module.bootstrap_web_auth({})
        self.assertNotIn('ARTIFICIAL-DO-NOT-EMIT', str(error.exception))
        save.assert_not_called()

    def test_missing_identity_is_rejected_before_browser(self):
        module = importlib.import_module('plaud_obsidian.workspace_login')
        transport = MagicMock()
        transport.__enter__.return_value.call.return_value = {'workspace_id': False, 'member_id': 'mem_fixture'}
        store = types.ModuleType('plaud_obsidian.workspace_api')
        store.__dict__['save_auth'] = MagicMock()
        with patch.object(module, 'MCPTransport', return_value=transport), \
                patch.object(module, '_sealed_auth') as browser, \
                patch.dict(sys.modules, {'plaud_obsidian.workspace_api': store}):
            with self.assertRaisesRegex(RuntimeError, '^IMAGE_WEB_AUTH_IDENTITY$'):
                module.bootstrap_web_auth({})
        browser.assert_not_called()

    def test_bootstrap_script_issues_new_auth_and_never_reads_browser_credentials(self):
        module = importlib.import_module('plaud_obsidian.workspace_login')
        with patch.object(module, '_aside', return_value={'error': 'IMAGE_WEB_LOGIN_REQUIRED'}) as browser:
            module._sealed_auth({}, 'PUBLIC-FIXTURE-KEY', 'ws_fixture')
        code = browser.call_args.args[1]
        for forbidden in ['localStorage', 'sessionStorage', 'document.cookie', 'storageState']:
            self.assertNotIn(forbidden, code)
        self.assertIn('/user-app/auth/workspace/token/', code)
        self.assertIn('crypto.subtle.encrypt', code)
        self.assertLess(code.index('crypto.subtle.encrypt'), code.index('console.log'))

    def test_new_tokens_are_decrypted_privately_and_only_safe_result_is_returned(self):
        try:
            module = importlib.import_module('plaud_obsidian.workspace_login')
        except ModuleNotFoundError:
            module = None
        self.assertIsNotNone(module, 'A normal Web token bootstrap is not implemented')
        assert module is not None
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        response = {'workspace_token': 'artificial-access-not-a-real-token',
                    'refresh_token': 'artificial-refresh-not-a-real-token',
                    'workspace_id': 'ws_fixture', 'member_id': 'mem_fixture',
                    'expires_in': 86400, 'refresh_expires_in': 2592000}
        transferred = {'api_domain': 'https://api-apne1.plaud.ai', 'response': response}

        def sealed(settings, public_key, workspace_id):
            self.assertEqual(workspace_id, 'ws_fixture')
            public = serialization.load_der_public_key(base64.b64decode(public_key))
            assert isinstance(public, rsa.RSAPublicKey)
            key = AESGCM.generate_key(256)
            iv = b'fixture-only'
            encode = lambda b: base64.b64encode(b).decode('ascii')
            return {'authenticated': True, 'sealed': encode(AESGCM(key).encrypt(iv, json.dumps(transferred).encode(), None)),
                    'wrapped': encode(public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))),
                    'iv': encode(iv)}

        transport = MagicMock()
        transport.__enter__.return_value.call.return_value = {'workspace_id': 'ws_fixture', 'member_id': 'mem_fixture'}
        saved = []
        store = types.ModuleType('plaud_obsidian.workspace_api')
        store.__dict__['save_auth'] = lambda *args: saved.append(args) or {'authenticated': True}
        with patch.object(module, 'MCPTransport', return_value=transport), patch.object(module, '_sealed_auth', side_effect=sealed), patch.dict(sys.modules, {'plaud_obsidian.workspace_api': store}):
            result = module.bootstrap_web_auth({'fixture': True})
        self.assertEqual(saved, [({'fixture': True}, transferred['api_domain'], 'ws_fixture', response)])
        self.assertTrue(result['authenticated'])
        self.assertNotIn(response['workspace_token'], json.dumps(result))
        self.assertNotIn(response['refresh_token'], json.dumps(result))


if __name__ == '__main__':
    unittest.main()
