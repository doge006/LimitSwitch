"""Disposable real CLIProxyAPI fixture. Dummy accounts only; never loads user auth."""
import json
import os
from collections import deque
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
MODEL = 'claude-sonnet-4-6'


class ProxyFixture:
    def __init__(self, on_request=None):
        self.api_key = secrets.token_urlsafe(32)
        self.management_key = secrets.token_urlsafe(32)
        self.base_url = None
        self.events = deque(maxlen=256)
        self.modes = {'A': 'normal', 'B': 'normal'}
        self.on_request = on_request
        self.process = None
        self.servers = []
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._generation = 0
        self._active = 'A'
        self._auto_swap = True

    def _upstream(self, label):
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                mode = fixture.modes[label]
                event = {'account': label, 'model': body.get('model'), 'mode': mode,
                         'path': self.path, 'time': time.time()}
                fixture.events.append(event)
                if fixture.on_request:
                    fixture.on_request(event)
                if '/count_tokens' in self.path:
                    return self.send_json(200, {'input_tokens': 10})
                if mode == 'quota' or (mode == 'partial' and not body.get('stream')):
                    return self.send_json(429, {'type': 'error', 'error': {
                        'type': 'rate_limit_error', 'message': 'Dummy account quota exhausted'}})
                text = f'Response from demo account {label}.'
                message = {'id': 'msg_demo', 'type': 'message', 'role': 'assistant',
                           'model': body.get('model', MODEL), 'content': [], 'stop_reason': None,
                           'stop_sequence': None, 'usage': {'input_tokens': 10, 'output_tokens': 0}}
                if not body.get('stream'):
                    message.update(content=[{'type': 'text', 'text': text}], stop_reason='end_turn')
                    return self.send_json(200, message)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                def emit(kind, data):
                    self.wfile.write(('event: ' + kind + '\ndata: ' + json.dumps({'type': kind, **data}) + '\n\n').encode())
                    self.wfile.flush()
                try:
                    emit('message_start', {'message': message})
                    emit('content_block_start', {'index': 0, 'content_block': {'type': 'text', 'text': ''}})
                    emit('content_block_delta', {'index': 0, 'delta': {'type': 'text_delta', 'text': text}})
                    if mode == 'partial':
                        time.sleep(.15)
                        emit('error', {'error': {'type': 'rate_limit_error', 'message': 'Dummy interruption after partial text'}})
                        return
                    emit('content_block_stop', {'index': 0})
                    emit('message_delta', {'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
                                           'usage': {'output_tokens': 8}})
                    emit('message_stop', {})
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def send_json(self, status, data):
                encoded = json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                self.wfile.write(encoded)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return server.server_port

    def request(self, route, payload=None, management=False, method=None):
        key = self.management_key if management else self.api_key
        request = urllib.request.Request(self.base_url + route,
            json.dumps(payload).encode() if payload is not None else None,
            {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method=method)
        with self._opener.open(request, timeout=10) as response:
            return json.load(response)

    def start(self):
        self.directory = Path(tempfile.mkdtemp(prefix='fixture-', dir=ROOT))
        ports = [self._upstream(label) for label in ['A', 'B']]
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.base_url = f'http://127.0.0.1:{port}'
        config = {'host': '127.0.0.1', 'port': port, 'auth-dir': str(self.directory / 'auth'),
            'api-keys': [self.api_key], 'logging-to-file': False, 'usage-statistics-enabled': False,
            'request-retry': 0, 'max-retry-credentials': 2, 'max-retry-interval': 1,
            'remote-management': {'allow-remote': False, 'secret-key': self.management_key,
                                 'disable-control-panel': True, 'disable-auto-update-panel': True},
            'routing': {'strategy': 'fill-first', 'session-affinity': False},
            'quota-exceeded': {'switch-project': True, 'switch-preview-model': False},
            'disable-claude-cloak-mode': True,
            'claude-api-key': [{'api-key': 'dummy-' + label, 'priority': 10 if label == 'A' else 0,
                'base-url': f'http://127.0.0.1:{upstream_port}', 'proxy-url': 'direct',
                'cloak': {'mode': 'never'}, 'models': [{'name': MODEL, 'alias': MODEL}]}
                for label, upstream_port in zip(['A', 'B'], ports)]}
        config_path = self.directory / 'config.yaml'
        config_path.write_text(json.dumps(config))
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(
            ('HOME_JWT', 'MANAGEMENT_PASSWORD', 'PGSTORE_', 'GITSTORE_', 'OBJECTSTORE_', 'S3STORE_', 'REDIS_'))}
        # The original fork has an unconditional Antigravity manifest updater. Its standard
        # HTTP client obeys these dead loopback proxies. Dummy inference explicitly uses direct.
        env.update(HTTP_PROXY='http://127.0.0.1:1', HTTPS_PROXY='http://127.0.0.1:1',
                   ALL_PROXY='http://127.0.0.1:1', NO_PROXY='127.0.0.1,localhost')
        self._log = (self.directory / 'server.log').open('w')
        self.process = subprocess.Popen([str(ROOT / 'cli-proxy-api.exe'), '-config', str(config_path), '-local-model'],
            cwd=self.directory, env=env, stdout=self._log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
        for _ in range(100):
            try:
                self.request('/v1/models')
                return self
            except Exception:
                if self.process.poll() is not None:
                    break
                time.sleep(.1)
        self.stop()
        raise RuntimeError('Dummy proxy did not become ready')

    def set_active(self, account):
        if account not in ('A', 'B'):
            raise ValueError('Only dummy accounts A and B are supported')
        self._active = account
        self.request('/v0/management/claude-api-key',
                     {'index': 0 if account == 'A' else 1, 'value': {'priority': 20}}, True, 'PATCH')
        self.request('/v0/management/claude-api-key',
                     {'index': 1 if account == 'A' else 0, 'value': {'priority': 0}}, True, 'PATCH')
        if not self._auto_swap:
            self._apply_eligibility()
        time.sleep(.35)

    def settle_failure(self):
        self.recover_to('B')

    def recover_to(self, account):
        if account not in ('A', 'B'):
            raise ValueError('Only dummy accounts A and B are supported')
        other = 'B' if account == 'A' else 'A'
        self.modes.update({account: 'normal', other: 'quota'})
        # Fresh dummy identity clears previous synthetic cooldown; never used with real accounts.
        self._generation += 1
        self.request('/v0/management/claude-api-key', {'index': 0 if account == 'A' else 1, 'value': {
            'api-key': f'dummy-{account}-recovery-{self._generation}', 'priority': 30}}, True, 'PATCH')
        self.set_active(account)

    def reset_accounts(self):
        self.modes.update(A='normal', B='normal')
        self._generation += 1
        self._active = 'A'
        for index, account in enumerate(('A', 'B')):
            self.request('/v0/management/claude-api-key', {'index': index, 'value': {
                'api-key': f'dummy-{account}-reset-{self._generation}',
                'priority': 20 if account == 'A' else 0,
                'excluded-models': [] if self._auto_swap or account == 'A' else [MODEL]}}, True, 'PATCH')
        self.events.clear()
        time.sleep(.35)

    def set_auto_swap(self, enabled):
        self._auto_swap = bool(enabled)
        self.request('/v0/management/max-retry-credentials',
                     {'value': 2 if enabled else 1}, True, 'PATCH')
        self._apply_eligibility()
        time.sleep(.35)

    def _apply_eligibility(self):
        # Retry count alone only governs one request; excluding alternate model prevents
        # the official client's later retry from choosing another dummy account too.
        for index, account in enumerate(('A', 'B')):
            self.request('/v0/management/claude-api-key', {'index': index, 'value': {
                'excluded-models': [] if self._auto_swap or account == self._active else [MODEL]}}, True, 'PATCH')

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        if hasattr(self, '_log'):
            self._log.close()
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.servers.clear()
        if hasattr(self, 'directory') and self.directory.exists():
            target = self.directory.resolve()
            if target.parent != ROOT.resolve() or not target.name.startswith('fixture-'):
                raise RuntimeError('Refusing cleanup outside proxy fixture directory')
            shutil.rmtree(target)
