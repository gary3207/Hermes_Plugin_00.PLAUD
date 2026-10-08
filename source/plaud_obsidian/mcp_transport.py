"""Bounded, read-only JSON-lines client for the pinned official PLAUD server."""
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time


READ_TOOLS = frozenset({'list_files', 'get_file', 'get_note', 'get_transcript', 'get_current_user'})
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


def unwrap(result):
    """Only the exact randomized recording-data tag ends an untrusted block."""
    if not isinstance(result, dict) or result.get('isError'):
        raise RuntimeError('MCP_TOOL_ERROR')
    content = result.get('content')
    if (not isinstance(content, list) or len(content) != 1 or
            not isinstance(content[0], dict) or content[0].get('type') != 'text'):
        raise RuntimeError('MCP_CONTENT_SCHEMA')
    text = content[0].get('text')
    if not isinstance(text, str):
        raise RuntimeError('MCP_CONTENT_SCHEMA')
    opening = re.search(r'^<(untrusted-user-data-[a-f0-9]{16}) source="plaud-recording">\r?\n', text, re.M)
    if opening:
        closing = '\n</' + opening.group(1) + '>'
        end = text.find(closing, opening.end())
        if end < 0:
            raise RuntimeError('MCP_UNTRUSTED_BLOCK_INVALID')
        text = text[opening.end():end]
    elif 'untrusted-user-data-' in text:
        raise RuntimeError('MCP_UNTRUSTED_BLOCK_INVALID')
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        raise RuntimeError('MCP_CONTENT_NOT_JSON') from None


class MCPTransport:
    """One owned stdio process; sequential calls, no ambient account overrides."""
    def __init__(self, settings, timeout=60):
        self.settings = settings
        self.timeout = timeout
        self.process = None
        self.reader = None
        self.responses = queue.Queue(maxsize=8)
        self.stopping = threading.Event()
        self.sequence = 0

    def __enter__(self):
        paths = {key: Path(self.settings[key]) for key in ('node', 'runtime', 'state')}
        if not all(path.is_absolute() for path in paths.values()):
            raise ValueError('MCP_PATH_MUST_BE_ABSOLUTE')
        package = paths['runtime'] / 'node_modules/@plaud-ai/mcp'
        try:
            version = json.loads((package / 'package.json').read_text(encoding='utf-8'))['version']
        except (OSError, ValueError, KeyError):
            raise RuntimeError('MCP_RUNTIME_MISSING') from None
        entry = package / 'dist/index.js'
        if version != '0.3.14' or not entry.is_file() or not paths['node'].is_file():
            raise RuntimeError('MCP_RUNTIME_INVALID')
        auth, tmp = paths['state'] / 'auth', paths['state'] / 'tmp'
        auth.mkdir(parents=True, exist_ok=True)
        tmp.mkdir(parents=True, exist_ok=True)
        allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE', 'LANG', 'LC_ALL'}
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        env.update(HOME=str(auth), USERPROFILE=str(auth), TMP=str(tmp), TEMP=str(tmp), TMPDIR=str(tmp),
                   PLAUD_TELEMETRY_DISABLED='1', DO_NOT_TRACK='1')
        try:
            self.process = subprocess.Popen([str(paths['node']), str(entry)], stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                            cwd=str(paths['state']), env=env)
            self.reader = threading.Thread(target=self._read_lines, daemon=True)
            self.reader.start()
            result = self._request('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                                  'clientInfo': {'name': 'plaud-obsidian', 'version': '1.0.0'}})
            if not isinstance(result, dict) or result.get('protocolVersion') not in {'2024-11-05', '2025-03-26', '2025-06-18'}:
                raise RuntimeError('MCP_INITIALIZE_SCHEMA')
            self._send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            return self
        except BaseException:
            self.close()
            raise

    def _read_lines(self):
        try:
            while not self.stopping.is_set():
                line = self.process.stdout.readline(MAX_RESPONSE_BYTES + 1)
                if not line:
                    item = RuntimeError('MCP_PROCESS_EXITED')
                elif len(line) > MAX_RESPONSE_BYTES or not line.endswith(b'\n'):
                    item = RuntimeError('MCP_RESPONSE_TOO_LARGE')
                else:
                    try:
                        item = json.loads(line)
                    except (ValueError, UnicodeError):
                        item = RuntimeError('MCP_INVALID_JSON')
                while not self.stopping.is_set():
                    try:
                        self.responses.put(item, timeout=0.1)
                        break
                    except queue.Full:
                        pass
                if isinstance(item, Exception):
                    return
        except (OSError, ValueError):
            if not self.stopping.is_set():
                try:
                    self.responses.put_nowait(RuntimeError('MCP_PIPE_ERROR'))
                except queue.Full:
                    pass

    def _send(self, message):
        try:
            self.process.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8'))
            self.process.stdin.flush()
        except (OSError, AttributeError, ValueError):
            raise RuntimeError('MCP_PIPE_ERROR') from None

    def _request(self, method, params, timeout=None):
        self.sequence += 1
        request_id = self.sequence
        try:
            self._send({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params})
            deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('MCP_TIMEOUT')
                try:
                    message = self.responses.get(timeout=remaining)
                except queue.Empty:
                    raise RuntimeError('MCP_TIMEOUT') from None
                if isinstance(message, Exception):
                    raise message
                if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                    raise RuntimeError('MCP_PROTOCOL_ERROR')
                if 'id' not in message:
                    continue
                if message['id'] != request_id or 'error' in message or 'result' not in message:
                    raise RuntimeError('MCP_RPC_ERROR')
                return message['result']
        except BaseException:
            self.close()
            raise

    def call(self, name, arguments=None):
        if name not in READ_TOOLS | {'login'}:
            raise ValueError('TOOL_NOT_ALLOWED')
        arguments = {} if arguments is None else arguments
        if not isinstance(arguments, dict):
            raise ValueError('MCP_ARGUMENT_SCHEMA')
        keys = {'list_files': {'page', 'page_size'}, 'get_file': {'file_id'}, 'get_note': {'file_id'},
                'get_transcript': {'file_id', 'block', 'cursor', 'limit'}, 'get_current_user': set(), 'login': set()}
        if set(arguments) - keys[name]:
            raise ValueError('MCP_ARGUMENT_SCHEMA')
        if name in {'get_file', 'get_note', 'get_transcript'}:
            file_id = arguments.get('file_id')
            if not isinstance(file_id, str) or not re.fullmatch(r'(?:of_)?[a-fA-F0-9]{32}', file_id):
                raise ValueError('PLAUD_ID_INVALID')
        for key, minimum, maximum in (('page', 1, 10000), ('page_size', 10, 100), ('limit', 1, 500)):
            if key in arguments:
                value = arguments[key]
                if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                    raise ValueError('MCP_ARGUMENT_SCHEMA')
        if 'cursor' in arguments and (not isinstance(arguments['cursor'], str) or
                                      not re.fullmatch(r'[A-Za-z0-9_-]{1,1024}', arguments['cursor'])):
            raise ValueError('MCP_ARGUMENT_SCHEMA')
        if name == 'get_transcript' and arguments.get('block', 'transaction') not in {'transaction', 'mark_memo'}:
            raise ValueError('MCP_BLOCK_NOT_ALLOWED')
        result = self._request('tools/call', {'name': name, 'arguments': arguments},
                               timeout=max(self.timeout, 150) if name == 'login' else None)
        if name == 'login':
            if not isinstance(result, dict) or result.get('isError'):
                return {'authenticated': False}
            messages = result.get('content', [])
            success = any(item.get('type') == 'text' and item.get('text') in
                          {'Already logged in.', 'Successfully authenticated with Plaud!'} for item in messages)
            return {'authenticated': success}
        return unwrap(result)

    def close(self):
        self.stopping.set()
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            if os.name == 'nt':
                try:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
            else:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self.reader:
            self.reader.join(timeout=1)
        for stream in (process.stdin, process.stdout):
            if stream:
                stream.close()

    def __exit__(self, *exc):
        self.close()
