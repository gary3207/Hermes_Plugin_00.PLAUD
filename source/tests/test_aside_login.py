"""Offline contract for the Aside OAuth launcher."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class AsideLoginTest(unittest.TestCase):
    def test_node_executes_official_sdk_callback_contract(self):
        import shutil
        import subprocess
        from plaud_obsidian import aside_login
        node = shutil.which('node')
        if not node:
            self.skipTest('Node required for offline SDK contract')
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            runtime = state/'runtime'
            dist = runtime/'node_modules/@plaud-ai/mcp/dist'
            dist.mkdir(parents=True)
            (dist.parent/'package.json').write_text(json.dumps({'version':'0.3.14','type':'module'}))
            (dist/'index.js').write_text('import { getClient } from "./client.js";\nimport { runOAuthCallback } from "./oauth.js";')
            (dist/'client.js').write_text('export const getClient = () => ({auth:{getAccessToken:async()=>null,createAuthorizationRequest:()=>({url:"https://web.plaud.ai/platform/oauth?fixture=1",codeVerifier:"private-fixture",state:"expected"}),exchangeCode:async()=>{}},getCurrentUser:async()=>({})});')
            (dist/'oauth.js').write_text('export const runOAuthCallback = async o => {if(o.port!==8199||o.expectedState!=="expected")throw Error("contract");o.onListening();await o.exchangeCode("do-not-log-code");return {status:"success"};};')
            completed = []
            original = subprocess.run
            def capture(*args, **kwargs):
                result = original(*args, **dict(kwargs, stdout=subprocess.PIPE))
                completed.append(result)
                return result
            with patch.object(aside_login.subprocess,'run',side_effect=capture):
                self.assertEqual(0, aside_login.run_login(dict(state=str(state),runtime=str(runtime),node=node)))
            events = [json.loads(line) for line in completed[0].stdout.decode().splitlines()]
            self.assertEqual(['awaiting_user','authenticated'], [event['status'] for event in events])
            self.assertNotIn('private-fixture',completed[0].stdout.decode())
            self.assertNotIn('do-not-log-code',completed[0].stdout.decode())

    def test_reuses_pinned_sdk_and_isolated_auth_home(self):
        from plaud_obsidian import aside_login
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            dist = state/'runtime/node_modules/@plaud-ai/mcp/dist'
            dist.mkdir(parents=True)
            (dist.parent/'package.json').write_text(json.dumps({'version':'0.3.14'}))
            (dist/'index.js').write_text('import { getClient } from "./client.js";\nimport { runOAuthCallback } from "./oauth.js";')
            for name in ('client.js','oauth.js'):
                (dist/name).write_text('// offline fixture')
            with patch.object(aside_login.subprocess, 'run') as run:
                run.return_value.returncode = 0
                self.assertEqual(0, aside_login.run_login({'state':str(state), 'runtime':str(dist.parent.parent.parent.parent), 'node':'fixture-node'}))
            args, kwargs = run.call_args
            self.assertEqual('fixture-node', args[0][0])
            self.assertEqual(str(state/'auth'), kwargs['env']['HOME'])
            self.assertEqual(str(state/'auth'), kwargs['env']['USERPROFILE'])
            self.assertNotIn('PLAUD_ACCESS_TOKEN', kwargs['env'])
            self.assertIn('runOAuthCallback', args[0][-1])
            self.assertNotIn('open(url)', args[0][-1])
            self.assertIn('authorization_url', args[0][-1])


if __name__ == '__main__':
    unittest.main()
