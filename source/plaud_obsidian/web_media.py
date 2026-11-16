"""Original Summary images by HTTPS; external Aside is used only to authenticate."""
from pathlib import Path
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import stat
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .mcp_transport import RECORD_ID_PATTERN
from .media import CARD_ID_PATTERN, MAX_IMAGE_BYTES, _image_extension


class ImageWebError(RuntimeError):
    """Only fixed, non-sensitive diagnostic codes cross this boundary."""


def _ids(record_id, card_id):
    if not isinstance(record_id, str) or not re.fullmatch(RECORD_ID_PATTERN, record_id):
        raise ValueError('PLAUD_ID_INVALID')
    if not isinstance(card_id, str) or not re.fullmatch(CARD_ID_PATTERN, card_id):
        raise ValueError('INVALID_SUMMARY_CARD_ID')


def _firstparty(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.hostname is not None
            and (parsed.hostname == 'plaud.ai' or parsed.hostname.endswith('.plaud.ai'))
            and parsed.port in (None, 443) and not parsed.username and not parsed.password)


def validate_image_url(url):
    try:
        if not isinstance(url, str) or len(url) > 16384 or re.search(r'[\x00-\x20\x7f\\\\]', url):
            raise ValueError
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        # shortcut: only PLAUD-named buckets; extend after a verified storage-host change.
        storage = re.fullmatch(r'plaud(?:-[a-z0-9-]+)?\.(?:s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com|oss-[a-z0-9-]+\.aliyuncs\.com)', host)
        known_storage = host == 'apne1-prod-plaud-content-storage.s3-accelerate.amazonaws.com'
        if (not (_firstparty(url) or (parsed.scheme == 'https' and (storage or known_storage)))
                or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment):
            raise ValueError
        return url
    except Exception:
        raise ImageWebError('IMAGE_URL_UNSAFE') from None


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        validate_image_url('https://' + self.host)
        addresses = socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ImageWebError('IMAGE_URL_UNSAFE')
        raw = socket.create_connection((addresses[0][4][0], 443), self.timeout, self.source_address)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(PublicHTTPSConnection, request, context=self._context)


class ImageRedirect(HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_image_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _etag(value):
    return value if isinstance(value, str) and re.fullmatch(r'(?:W/)?"[A-Za-z0-9._:-]{1,128}"', value) else None


def download_image(url, previous=None):
    """Download without browser auth, cookies or ambient proxies; preserve bytes.

    Internal ``previous`` contains bytes read from a validated local ownership receipt.
    """
    validate_image_url(url)
    reusable = None
    if isinstance(previous, dict) and _etag(previous.get('etag')):
        try:
            _image_extension(previous.get('data'), previous.get('content_type'))
            reusable = previous
        except ValueError:
            pass
    try:
        opener = build_opener(ProxyHandler({}), PublicHTTPSHandler(debuglevel=0), ImageRedirect())
        headers = {'Accept': 'image/png,image/jpeg,image/webp'}
        if reusable:
            headers['If-None-Match'] = reusable['etag']
        request = Request(url, headers=headers)
        deadline = time.monotonic() + 45
        with opener.open(request, timeout=10) as response:
            mime = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
            length = response.headers.get('Content-Length')
            if (response.status != 200 or mime not in ('image/png', 'image/jpeg', 'image/webp')
                    or (length is not None and (not length.isdigit() or not 0 < int(length) <= MAX_IMAGE_BYTES))):
                raise ImageWebError('IMAGE_DOWNLOAD_INVALID')
            data = bytearray()
            while len(data) <= MAX_IMAGE_BYTES and time.monotonic() < deadline:
                chunk = response.read1(min(65536, MAX_IMAGE_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            else:
                raise ImageWebError('IMAGE_DOWNLOAD_INVALID')
            data = bytes(data)
            if length is not None and len(data) != int(length):
                raise ImageWebError('IMAGE_DOWNLOAD_INVALID')
            try:
                _image_extension(data, mime)
            except ValueError:
                raise ImageWebError('IMAGE_DOWNLOAD_INVALID') from None
            return {'data': data, 'content_type': mime, 'etag': _etag(response.headers.get('ETag'))}
    except HTTPError as error:
        with error:
            if error.code == 304 and reusable:
                return {'data': None, 'content_type': reusable['content_type'], 'etag': reusable['etag']}
        raise ImageWebError('IMAGE_DOWNLOAD_FAILED') from None
    except ImageWebError:
        raise
    except Exception:
        raise ImageWebError('IMAGE_DOWNLOAD_FAILED') from None


def _plain_path(path):
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ImageWebError('IMAGE_WEB_BROWSER_OWNERSHIP')


def _aside_command(settings):
    executable = Path(os.environ.get('LOCALAPPDATA', '')) / 'Aside/CLI/current/aside.exe'
    if not executable.is_absolute() or not executable.is_file():
        raise ImageWebError('IMAGE_WEB_BROWSER_UNAVAILABLE')
    command = [str(executable), '--host', 'local']
    profile = settings.get('aside_profile')
    if profile is not None and profile != '':
        if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', profile):
            raise ImageWebError('IMAGE_WEB_PROFILE_INVALID')
        command.extend(['--profile', profile])
    return command


def _aside(settings, code):
    command = _aside_command(settings)
    try:
        result = subprocess.run([command[0], 'repl', *command[1:], '--json', code],
                                capture_output=True, text=True, encoding='utf-8', timeout=120,
                                stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ImageWebError('IMAGE_WEB_FAILED')
        if not isinstance(result.stdout, str) or len(result.stdout) > 65536:
            raise ImageWebError('IMAGE_WEB_PROTOCOL')
        run = json.loads(result.stdout)['run']
        if run.get('error') is not None:
            raise ImageWebError('IMAGE_WEB_FAILED')
        lines = [line.removeprefix('PLAUD_IMAGE_RESULT:') for item in run['content']
                 if item.get('type') == 'text' for line in item['text'].splitlines()
                 if line.startswith('PLAUD_IMAGE_RESULT:')]
        if len(lines) != 1:
            raise ImageWebError('IMAGE_WEB_PROTOCOL')
        observed = json.loads(lines[0])
        if not isinstance(observed, dict):
            raise ImageWebError('IMAGE_WEB_PROTOCOL')
        return observed
    except subprocess.TimeoutExpired:
        raise ImageWebError('IMAGE_WEB_TIMEOUT') from None
    except ImageWebError:
        raise
    except Exception:
        raise ImageWebError('IMAGE_WEB_PROTOCOL') from None


class WebMedia:
    """Keep the importer interface while resolving originals with workspace auth."""
    def __init__(self, settings):
        from .engine import paths
        paths(settings)
        _plain_path(Path(settings['state']))
        self.settings = settings

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def _previous(self, record_id, card_id, receipt):
        try:
            from .engine import paths
            output, _ = paths(self.settings)
            vault = Path(self.settings['vault']).resolve()
            if not isinstance(receipt, dict) or receipt.get('card_id') != card_id or not _etag(receipt.get('etag')):
                return None
            target = vault / receipt['path']
            _plain_path(target)
            relative = target.relative_to(output / '_attachments')
            if ('..' in relative.parts or target.parent.name != record_id
                    or target.name != card_id + '.' + {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}[receipt['content_type']]
                    or not target.is_file() or not 0 < target.stat().st_size <= MAX_IMAGE_BYTES):
                return None
            data = target.read_bytes()
            _image_extension(data, receipt['content_type'])
            if len(data) != receipt['byte_count'] or hashlib.sha256(data).hexdigest() != receipt['sha256']:
                return None
            return {'data': data, 'content_type': receipt['content_type'], 'etag': receipt['etag']}
        except Exception:
            return None

    def read_summary_image(self, record_id, card_id, previous=None, *, expected_card_ids):
        _ids(record_id, card_id)
        # shortcut: retain single-card import coverage; expand with multi-card integration tests.
        if (not isinstance(expected_card_ids, (list, tuple, set, frozenset))
                or len(expected_card_ids) != 1 or card_id not in expected_card_ids):
            raise ImageWebError('IMAGE_WEB_CARD_AMBIGUOUS')
        try:
            from .workspace_api import WorkspaceAPI
            url = WorkspaceAPI(self.settings).image_url(record_id, card_id)
            return download_image(url, self._previous(record_id, card_id, previous))
        except ImageWebError:
            raise
        except Exception:
            raise ImageWebError('IMAGE_WEB_FAILED') from None


def open_login(settings):
    """Issue fresh API credentials; expose Aside if the user must sign in first."""
    WebMedia(settings)
    from .workspace_login import bootstrap_web_auth
    try:
        return bootstrap_web_auth(settings)
    except ImageWebError as error:
        if str(error) != 'IMAGE_WEB_LOGIN_REQUIRED':
            raise
    command = _aside_command(settings)
    try:
        result = subprocess.run([*command, 'https://web.plaud.ai/'], capture_output=True,
                                text=True, encoding='utf-8', timeout=30, stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ImageWebError('IMAGE_WEB_FAILED')
        return {'opened': True, 'authenticated': False, 'browser': 'aside', 'dedicated_profile': False,
                'retry_command': 'hermes plaud image-login'}
    except subprocess.TimeoutExpired:
        raise ImageWebError('IMAGE_WEB_TIMEOUT') from None
    except ImageWebError:
        raise
    except Exception:
        raise ImageWebError('IMAGE_WEB_BROWSER_UNAVAILABLE') from None
