"""Issue fresh Web credentials; only sealed responses leave the Aside page."""
import base64
import json

from .mcp_transport import MCPTransport, MCPToolError
from .web_media import ImageWebError, _aside


def _sealed_auth(settings, public_key, workspace_id):
    # The CLI records console output: seal the new response before it leaves the page.
    request = json.dumps({'publicKey': public_key, 'workspaceId': workspace_id})
    code = r'''await (async () => {
        const request = __REQUEST__;
        const candidates = await listBrowserTabs();
        const existing = candidates.find(t => {
            try { return new URL(t.url).origin === 'https://web.plaud.ai'; }
            catch (_) { return false; }
        });
        if (existing) await attachBrowserTab(existing.targetId);
        else await openTab('https://web.plaud.ai/');
        const target = page;
        await snapshot(target, {interactive: true});
        const result = await target.evaluate(async request => {
            if (location.origin !== 'https://web.plaud.ai') return {error: 'IMAGE_WEB_LOGIN_REQUIRED'};
            const validOrigin = origin => typeof origin === 'string' && /^https:\/\/api(?:-[a-z0-9-]+)?\.plaud\.ai$/.test(origin);
            const encode = b => btoa(String.fromCharCode(...new Uint8Array(b)));
            const decode = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
            const transport = await crypto.subtle.importKey('spki', decode(request.publicKey),
                {name: 'RSA-OAEP', hash: 'SHA-256'}, false, ['encrypt']);
            const aes = await crypto.subtle.generateKey({name: 'AES-GCM', length: 256}, true, ['encrypt']);
            const json = async response => {
                const reader = response.body.getReader();
                const chunks = []; let count = 0;
                while (true) {
                    const {done, value} = await reader.read(); if (done) break;
                    count += value.length;
                    if (count > 65536) { await reader.cancel(); throw Error('IMAGE_WEB_RESPONSE_INVALID'); }
                    chunks.push(value);
                }
                const buffer = new Uint8Array(count); let at = 0;
                for (const chunk of chunks) { buffer.set(chunk, at); at += chunk.length; }
                return JSON.parse(new TextDecoder().decode(buffer));
            };
            const origins = [...new Set(performance.getEntriesByType('resource').map(entry => {
                try { const u = new URL(entry.name); return validOrigin(u.origin) ? u.origin : null; }
                catch (_) { return null; }
            }).filter(Boolean))];
            if (!origins.length) origins.push('https://api.plaud.ai');
            let apiDomain = null;
            for (const origin of origins.slice(0, 2)) {
                const response = await fetch(origin + '/team-app/workspaces/list?include_details=true&need_personal_workspace=true',
                    {credentials: 'include', redirect: 'error', signal: AbortSignal.timeout(15000), headers: {Accept: 'application/json'}});
                if (response.status === 401) continue;
                if (!response.ok) continue;
                const body = await json(response);
                if (body.status !== 0 || !Array.isArray(body.data?.workspaces)) continue;
                const matches = body.data.workspaces.filter(ws => ws.workspace_id === request.workspaceId);
                if (matches.length !== 1) return {error: 'IMAGE_WEB_AUTH_IDENTITY'};
                if (!validOrigin(matches[0].api_domain)) return {error: 'IMAGE_WEB_RESPONSE_INVALID'};
                apiDomain = matches[0].api_domain; break;
            }
            if (!apiDomain) return {error: 'IMAGE_WEB_LOGIN_REQUIRED'};
            const response = await fetch(apiDomain + '/user-app/auth/workspace/token/' + encodeURIComponent(request.workspaceId),
                {method: 'POST', credentials: 'include', redirect: 'error', signal: AbortSignal.timeout(20000),
                 headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: '{}'});
            if (response.status === 401) return {error: 'IMAGE_WEB_LOGIN_REQUIRED'};
            if (!response.ok) return {error: 'IMAGE_WEB_API_FAILED'};
            const body = await json(response);
            if (body.status !== 0 || !body.data) return {error: 'IMAGE_WEB_RESPONSE_INVALID'};
            const iv = crypto.getRandomValues(new Uint8Array(12));
            const rawKey = await crypto.subtle.exportKey('raw', aes);
            const sealed = await crypto.subtle.encrypt({name: 'AES-GCM', iv}, aes,
                new TextEncoder().encode(JSON.stringify({api_domain: apiDomain, response: body.data})));
            const wrapped = await crypto.subtle.encrypt({name: 'RSA-OAEP'}, transport, rawKey);
            return {authenticated: true, iv: encode(iv), sealed: encode(sealed), wrapped: encode(wrapped)};
        }, request);
        console.log('PLAUD_IMAGE_RESULT:' + JSON.stringify(result));
        if (!existing && result.authenticated) await closeTab(target);
    })();'''.replace('__REQUEST__', request)
    return _aside(settings, code)


def bootstrap_web_auth(settings):
    """Normal issuance, not extraction of the browser's existing credential store."""
    from .workspace_api import save_auth
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa, padding
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise ImageWebError('IMAGE_WEB_CRYPTO_UNAVAILABLE') from None
    try:
        with MCPTransport(settings) as transport:
            identity = transport.call('get_current_user', {})
        if not isinstance(identity, dict):
            raise ImageWebError('IMAGE_WEB_AUTH_IDENTITY')
        for name in ('workspace_id', 'member_id'):
            value = identity.get(name)
            if not isinstance(value, str) or not 0 < len(value) <= 256 or any(ord(c) < 32 for c in value):
                raise ImageWebError('IMAGE_WEB_AUTH_IDENTITY')
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = base64.b64encode(private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode('ascii')
        observed = _sealed_auth(settings, public_key, identity['workspace_id'])
        error = observed.get('error')
        if error in ('IMAGE_WEB_LOGIN_REQUIRED', 'IMAGE_WEB_AUTH_IDENTITY', 'IMAGE_WEB_RESPONSE_INVALID', 'IMAGE_WEB_API_FAILED'):
            raise ImageWebError(error)
        if observed.get('authenticated') is not True:
            raise ImageWebError('IMAGE_WEB_RESPONSE_INVALID')
        wrapped = base64.b64decode(observed['wrapped'], validate=True)
        iv = base64.b64decode(observed['iv'], validate=True)
        sealed = base64.b64decode(observed['sealed'], validate=True)
        if len(wrapped) != 256 or len(iv) != 12 or not 16 < len(sealed) <= 65536:
            raise ImageWebError('IMAGE_WEB_RESPONSE_INVALID')
        key = private_key.decrypt(wrapped, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        payload = json.loads(AESGCM(key).decrypt(iv, sealed, None))
        response = payload['response']
        if (not isinstance(response, dict) or response.get('workspace_id') != identity['workspace_id']
                or response.get('member_id') != identity['member_id']):
            raise ImageWebError('IMAGE_WEB_AUTH_IDENTITY')
        save_auth(settings, payload['api_domain'], identity['workspace_id'], response)
        return {'authenticated': True, 'browser': 'aside', 'dedicated_profile': False,
                'collection_browser_required': False, 'credential_store': 'windows-dpapi'}
    except (ImageWebError, MCPToolError):
        raise
    except Exception:
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID') from None
