"""User-assisted Aside OAuth using the pinned official SDK, not copied auth code."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def run_login(settings):
    state = Path(settings['state'])
    dist = Path(settings['runtime'])/'node_modules/@plaud-ai/mcp/dist'
    package = json.loads((dist.parent/'package.json').read_text(encoding='utf-8'))
    if package.get('version') != '0.3.14':
        raise RuntimeError('MCP_RUNTIME_INVALID')
    entry = (dist/'index.js').read_text(encoding='utf-8')
    modules = {}
    for names, filename in re.findall(r'import\s*\{([^}]+)\}\s*from\s*"(\./[\w-]+\.js)"', entry):
        for name in ('getClient', 'runOAuthCallback'):
            if name in names.split(',') or re.search(r'\b' + name + r'\b', names):
                path = dist/filename
                if not path.is_file():
                    raise RuntimeError('MCP_RUNTIME_INVALID')
                modules[name] = path.resolve().as_uri()
    if set(modules) != {'getClient','runOAuthCallback'}:
        raise RuntimeError('MCP_ASIDE_LOGIN_UNSUPPORTED')
    auth, tmp = state/'auth', state/'tmp'
    auth.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    allowed = {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','SYSTEMDRIVE','LANG','LC_ALL'}
    env = {key:value for key,value in os.environ.items() if key.upper() in allowed}
    env.update(HOME=str(auth), USERPROFILE=str(auth), TMP=str(tmp), TEMP=str(tmp), TMPDIR=str(tmp),
               PLAUD_TELEMETRY_DISABLED='1', DO_NOT_TRACK='1')
    # ponytail: pinned SDK internals; fail closed on layout/version changes instead of copying OAuth.
    script = '\n'.join(f'import {{ {name} }} from {json.dumps(url)};' for name,url in modules.items()) + r'''
const emit = event => console.log(JSON.stringify(event));
try {
  const client = getClient();
  const token = await client.auth.getAccessToken();
  let authenticated = false;
  if (token) {
    try { await client.getCurrentUser(); authenticated = true; }
    catch (error) {
      if (/\b401\b|not authenticated|unauthorized/i.test(String(error?.message))) await client.auth.logout();
      else throw error;
    }
  }
  if (authenticated) {
    emit({status:'authenticated'});
  } else {
    const {url, codeVerifier, state} = client.auth.createAuthorizationRequest();
    if (new URL(url).origin !== 'https://web.plaud.ai') throw Error('OAUTH_ORIGIN_INVALID');
    const result = await runOAuthCallback({port:8199, expectedState:state, timeoutMs:600000,
      exchangeCode:code => client.auth.exchangeCode(code, codeVerifier, state),
      onListening:() => emit({status:'awaiting_user', authorization_url:url, expires_in_seconds:600})});
    if (result.status === 'success') {
      await client.getCurrentUser();
      emit({status:'authenticated'});
    } else {
      emit({status:'failed', code:'PLAUD_LOGIN_' + result.status.toUpperCase().replaceAll('-','_')});
      process.exitCode = 1;
    }
  }
} catch {
  emit({status:'failed', code:'PLAUD_LOGIN_FAILED'});
  process.exitCode = 1;
}
'''
    return subprocess.run([settings['node'], '--input-type=module', '-e', script],
                          env=env, cwd=str(state), stderr=subprocess.DEVNULL, check=False).returncode


def main():
    from hermes_constants import get_hermes_home
    parser = argparse.ArgumentParser(description='Print a PLAUD authorization URL for the Aside browser; wait for user login.')
    parser.parse_args()
    state = get_hermes_home()/'plugin-data/hermes-plaud'
    return run_login(dict(state=str(state), runtime=str(state/'runtime'), node=shutil.which('node') or 'missing-node'))


if __name__ == '__main__':
    raise SystemExit(main())
