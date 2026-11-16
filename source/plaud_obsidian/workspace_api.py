"""Browser-free PLAUD workspace API and current-user DPAPI credentials."""
from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import re
import time

from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .web_media import ImageWebError, PublicHTTPSHandler, _ids, _plain_path, validate_image_url

MAX_API_BYTES = 2 * 1024 * 1024
MAX_AUTH_BYTES = 65536


class _Unauthorized(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ImageWebError('IMAGE_WEB_API_FAILED')


def _request(auth, endpoint, *, refresh=False):
    headers = {'Authorization': 'Bearer ' + auth['refresh_token' if refresh else 'access_token'],
               'Accept': 'application/json', 'X-Scope-Type': 'workspace',
               'X-Scope-Id': auth['workspace_id'],
               'User-Agent': 'Hermes-PLAUD-Obsidian (direct API client)'}
    request = Request(_origin(auth['api_domain']) + endpoint, headers=headers,
                      data=b'{}' if refresh else None, method='POST' if refresh else 'GET')
    if refresh:
        request.add_header('Content-Type', 'application/json')
    opener = build_opener(ProxyHandler({}), PublicHTTPSHandler(debuglevel=0), _NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            if response.status == 401:
                raise _Unauthorized
            if response.status != 200:
                raise ImageWebError('IMAGE_WEB_API_FAILED')
            raw = response.read(MAX_API_BYTES + 1)
            if len(raw) > MAX_API_BYTES:
                raise ImageWebError('IMAGE_WEB_RESPONSE_INVALID')
    except HTTPError as error:
        with error:
            unauthorized = error.code == 401
        if unauthorized:
            raise _Unauthorized from None
        raise ImageWebError('IMAGE_WEB_API_FAILED') from None
    try:
        result = json.loads(raw)
        if isinstance(result, dict) and type(result.get('status')) is int and result['status'] == 401:
            raise _Unauthorized
        if not isinstance(result, dict) or type(result.get('status')) is not int or result['status'] != 0:
            raise ValueError
        if not isinstance(result.get('data'), dict):
            raise ValueError
        return result['data']
    except _Unauthorized:
        raise
    except Exception:
        raise ImageWebError('IMAGE_WEB_RESPONSE_INVALID') from None


def _refresh(path, auth):
    try:
        data = _request(auth, '/user-app/auth/workspace/refresh/' + auth['workspace_id'], refresh=True)
        response = dict(workspace_id=auth['workspace_id'], refresh_token=auth['refresh_token'],
                        refresh_expires_in=auth['refresh_expires_at'] - time.time())
        response.update(data)
        rotated = _normalize(auth['api_domain'], auth['workspace_id'], response)
        if 'refresh_expires_in' not in data:
            rotated['refresh_expires_at'] = auth['refresh_expires_at']
    except _Unauthorized:
        raise ImageWebError('IMAGE_WEB_LOGIN_REQUIRED') from None
    except ImageWebError as error:
        if error.args in (('IMAGE_WEB_AUTH_INVALID',), ('IMAGE_WEB_RESPONSE_INVALID',)):
            raise ImageWebError('IMAGE_WEB_LOGIN_REQUIRED') from None
        raise
    _write_auth(path, rotated)
    return rotated


class WorkspaceAPI:
    def __init__(self, settings):
        self.settings = settings

    def image_url(self, record_id, card_id):
        _ids(record_id, card_id)
        try:
            path = _auth_path(self.settings)
            with _auth_lock(path):
                auth = _load_auth(path)
                now = time.time()
                if auth['refresh_expires_at'] <= now:
                    raise ImageWebError('IMAGE_WEB_LOGIN_REQUIRED')
                refreshed = auth['expires_at'] <= now + 60
                if refreshed:
                    auth = _refresh(path, auth)
                try:
                    data = _request(auth, '/file-app/v4/files/detail/' + record_id)
                except _Unauthorized:
                    if refreshed:
                        raise ImageWebError('IMAGE_WEB_LOGIN_REQUIRED') from None
                    auth = _refresh(path, auth)
                    try:
                        data = _request(auth, '/file-app/v4/files/detail/' + record_id)
                    except _Unauthorized:
                        raise ImageWebError('IMAGE_WEB_LOGIN_REQUIRED') from None
            try:
                if data['meta']['file_id'] != record_id or not isinstance(data['relation_content_mapping'], dict):
                    raise ValueError
                return validate_image_url(data['relation_content_mapping'][card_id])
            except Exception:
                raise ImageWebError('IMAGE_WEB_RESPONSE_INVALID') from None
        except ImageWebError:
            raise
        except Exception:
            raise ImageWebError('IMAGE_WEB_API_FAILED') from None

def _dpapi(data, *, decrypt=False):
    if os.name != 'nt':
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    try:
        crypt = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        source = Blob(len(data), buffer)
        target = Blob()
        if decrypt:
            call = crypt.CryptUnprotectData
            call.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        else:
            call = crypt.CryptProtectData
            call.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        call.restype = wintypes.BOOL
        if not call(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise ValueError
        try:
            if not 0 < target.size <= MAX_AUTH_BYTES:
                raise ValueError
            return ctypes.string_at(target.data, target.size)
        finally:
            ctypes.memset(target.data, 0, target.size)
            kernel.LocalFree(target.data)
    except Exception:
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID') from None


def _origin(value):
    if not isinstance(value, str) or not re.fullmatch(r'https://api(?:-[a-z0-9-]+)?\.plaud\.ai', value):
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID')
    return value


def _workspace(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,159}', value):
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID')
    return value


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[\x21-\x7e]{1,8192}', value):
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID')
    return value


def _positive(value) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID')
    return value


def _normalize(api_domain, workspace_id, response):
    if not isinstance(response, dict) or response.get('workspace_id') != workspace_id:
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID')
    now = time.time()
    access = response.get('workspace_token')
    if access is None:
        access = response.get('access_token')
    return dict(version=1, api_domain=_origin(api_domain), workspace_id=_workspace(workspace_id),
                access_token=_token(access), refresh_token=_token(response.get('refresh_token')),
                expires_at=_positive(now + _positive(response.get('expires_in'))),
                refresh_expires_at=_positive(now + _positive(response.get('refresh_expires_in'))))


def _auth_path(settings):
    from .engine import paths
    try:
        paths(settings)
        path = Path(settings['state']) / 'web-auth' / 'credentials.bin'
        _plain_path(path)
        _plain_path(path.parent / 'writer.lock')
        return path
    except Exception:
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID') from None


@contextmanager
def _auth_lock(path):
    from .engine import writer_lock
    try:
        _plain_path(path)
        _plain_path(path.parent / 'writer.lock')
    except Exception:
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID') from None
    with ExitStack() as stack:
        try:
            stack.enter_context(writer_lock(path.parent))
        except PermissionError:
            # Windows may reject writer.lock's initial read before its explicit lock attempt.
            raise ImageWebError('IMAGE_WEB_AUTH_BUSY') from None
        except RuntimeError as error:
            if type(error) is RuntimeError and error.args == ('IMPORT_ALREADY_RUNNING',):
                raise ImageWebError('IMAGE_WEB_AUTH_BUSY') from None
            raise
        yield


def _write_auth(path, auth):
    from .engine import atomic_write
    raw = json.dumps(auth, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if not 0 < len(raw) <= MAX_AUTH_BYTES:
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID')
    encrypted = _dpapi(raw)
    if not 0 < len(encrypted) <= MAX_AUTH_BYTES:
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID')
    _plain_path(path)
    atomic_write(path, encrypted)


def _load_auth(path):
    _plain_path(path)
    if not path.exists():
        raise ImageWebError('IMAGE_WEB_LOGIN_REQUIRED')
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_AUTH_BYTES:
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID')
    try:
        with path.open('rb') as stream:
            encrypted = stream.read(MAX_AUTH_BYTES + 1)
        if len(encrypted) > MAX_AUTH_BYTES:
            raise ValueError
        raw = _dpapi(encrypted, decrypt=True)
        if not 0 < len(raw) <= MAX_AUTH_BYTES:
            raise ValueError
        auth = json.loads(raw)
        if not isinstance(auth, dict) or type(auth.get('version')) is not int or auth['version'] != 1:
            raise ValueError
        _origin(auth.get('api_domain'))
        _workspace(auth.get('workspace_id'))
        for key in ('access_token', 'refresh_token'):
            _token(auth.get(key))
        for key in ('expires_at', 'refresh_expires_at'):
            _positive(auth.get(key))
        return auth
    except Exception:
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID') from None


def save_auth(settings, api_domain, workspace_id, response):
    """Persist a newly issued workspace response; return no credentials."""
    try:
        auth = _normalize(api_domain, workspace_id, response)
        path = _auth_path(settings)
        with _auth_lock(path):
            _write_auth(path, auth)
        return {key: auth[key] for key in ('api_domain', 'workspace_id', 'expires_at', 'refresh_expires_at')}
    except ImageWebError as error:
        if error.args == ('IMAGE_WEB_AUTH_BUSY',):
            raise
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID') from None
    except Exception:
        raise ImageWebError('IMAGE_WEB_AUTH_INVALID') from None
