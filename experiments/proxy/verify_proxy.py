"""Offline dummy-account integration and Windows idle measurement of pinned fork."""
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
MODEL = 'claude-sonnet-4-6'
events = []
modes = {'A': 'ok', 'B': 'ok'}


def upstream(label):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            events.append({'account': label, 'model': data.get('model'), 'path': self.path})
            if modes[label] == 'quota':
                self.send_response(429)
                self.send_header('retry-after', '60')
                payload = {'type': 'error', 'error': {'type': 'rate_limit_error', 'message': 'Dummy quota exhausted'}}
            else:
                self.send_response(200)
                payload = {'id': 'msg_dummy', 'type': 'message', 'role': 'assistant', 'model': MODEL,
                           'content': [{'type': 'text', 'text': f'Response from {label}'}],
                           'stop_reason': 'end_turn', 'stop_sequence': None,
                           'usage': {'input_tokens': 1, 'output_tokens': 3}}
            body = json.dumps(payload).encode()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run():
    servers = [upstream(label) for label in ['A', 'B']]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    config = {
        'host': '127.0.0.1', 'port': port, 'auth-dir': str(ROOT / 'dummy-auth'),
        'api-keys': ['dummy-local-key'], 'debug': False, 'logging-to-file': False,
        'usage-statistics-enabled': False, 'request-log': False,
        'remote-management': {'allow-remote': False, 'secret-key': 'dummy-management-key',
                              'disable-control-panel': True, 'disable-auto-update-panel': True},
        'request-retry': 0, 'max-retry-credentials': 2, 'max-retry-interval': 1,
        'quota-exceeded': {'switch-project': True, 'switch-preview-model': False},
        'routing': {'strategy': 'fill-first', 'session-affinity': False},
        'disable-claude-cloak-mode': True,
        'claude-api-key': [
            {'api-key': 'dummy-' + label, 'priority': 10 if label == 'A' else 0,
             'base-url': f'http://127.0.0.1:{server.server_port}', 'proxy-url': 'direct',
             'cloak': {'mode': 'never'}, 'models': [{'name': MODEL, 'alias': MODEL}]}
            for label, server in zip(['A', 'B'], servers)]}
    # JSON is valid YAML, avoiding a dependency for this isolated test.
    path = ROOT / 'dummy-config.yaml'
    path.write_text(json.dumps(config, indent=2))
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(
        ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'MANAGEMENT_PASSWORD', 'HOME_JWT',
         'PGSTORE_', 'GITSTORE_', 'OBJECTSTORE_', 'S3STORE_', 'REDIS_'))}
    env['NO_PROXY'] = '127.0.0.1,localhost'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(route, data=None, management=False, method=None):
        headers = {'Authorization': 'Bearer ' + ('dummy-management-key' if management else 'dummy-local-key'),
                   'Content-Type': 'application/json'}
        req = urllib.request.Request(f'http://127.0.0.1:{port}' + route,
                                     json.dumps(data).encode() if data is not None else None,
                                     headers, method=method)
        with opener.open(req, timeout=15) as response:
            return json.load(response)

    def turn():
        return request('/v1/messages', {'model': MODEL, 'max_tokens': 32, 'stream': False,
                                       'messages': [{'role': 'user', 'content': 'Hello'}]})

    log = (ROOT / 'server.log').open('w')
    process = subprocess.Popen([str(ROOT / 'cli-proxy-api.exe'), '-config', str(path), '-local-model'],
                               cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    results = {'pid': process.pid, 'model': MODEL}
    try:
        for _ in range(100):
            try:
                results['models'] = request('/v1/models')
                break
            except Exception:
                if process.poll() is not None:
                    raise RuntimeError('Proxy exited; inspect server.log')
                time.sleep(.1)
        else:
            raise RuntimeError('Proxy failed readiness')
        results['first_response'] = turn()
        assert events[-1]['account'] == 'A', events
        request('/v0/management/claude-api-key', {'index': 1, 'value': {'priority': 20}}, True, 'PATCH')
        time.sleep(1)
        results['manual_response'] = turn()
        assert events[-1]['account'] == 'B', events
        request('/v0/management/claude-api-key', {'index': 1, 'value': {'priority': 0}}, True, 'PATCH')
        time.sleep(1)
        modes['A'] = 'quota'
        start = len(events)
        results['failover_response'] = turn()
        results['failover_events'] = events[start:]
        assert [e['account'] for e in events[start:]] == ['A', 'B'], events
        assert all(e['model'] == MODEL for e in events), events
        # All values from OS counters, no forced working-set trimming.
        measurement = subprocess.run(['powershell', '-NoProfile', '-Command',
            f'$p=Get-Process -Id {process.pid}; $cpu=$p.TotalProcessorTime.TotalSeconds; '
            '$samples=@(); 1..30 | ForEach-Object { Start-Sleep -Seconds 1; $p.Refresh(); '
            '$samples += [pscustomobject]@{WorkingSetBytes=$p.WorkingSet64; PrivateBytes=$p.PrivateMemorySize64} }; '
            '@{Samples=$samples; CpuSeconds=($p.TotalProcessorTime.TotalSeconds-$cpu); DurationSeconds=30} | ConvertTo-Json -Depth 4'],
            capture_output=True, text=True, check=True)
        results['idle'] = json.loads(measurement.stdout)
        results['status'] = 'PASS'
    finally:
        process.terminate()
        process.wait(timeout=10)
        log.close()
        for server in servers:
            server.shutdown()
            server.server_close()
        results['all_events'] = events
        (ROOT / 'verification.json').write_text(json.dumps(results, indent=2))
    print(json.dumps({k: v for k, v in results.items() if k != 'idle'}, indent=2))
    samples = results['idle']['Samples']
    print('Peak idle working set MiB:', max(s['WorkingSetBytes'] for s in samples) / 1048576)
    print('Peak idle private bytes MiB:', max(s['PrivateBytes'] for s in samples) / 1048576)
    print('CPU seconds over 30 seconds:', results['idle']['CpuSeconds'])


if __name__ == '__main__':
    run()
