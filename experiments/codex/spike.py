"""Run official Codex exec against synthetic local Responses API. No real auth."""
import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXE = next((Path(os.environ['APPDATA']) / 'npm/node_modules/@openai').rglob('codex.exe'))


def run_case(mode):
    requests = []
    recovered = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            if self.headers.get('Content-Encoding') == 'zstd':
                raise RuntimeError('Unexpected compressed request')
            request = json.loads(body)
            requests.append({'path': self.path, 'input': request.get('input'), 'previous_response_id': request.get('previous_response_id')})
            if mode == 'quota' and len(requests) == 1:
                self.send_response(429)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Retry-After', '0')
                self.end_headers()
                self.wfile.write(b'{"error":{"type":"rate_limit_error","message":"Synthetic quota exhausted"}}')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            sequence = 0
            def emit(kind, **payload):
                nonlocal sequence
                payload.update(type=kind, sequence_number=sequence)
                sequence += 1
                self.wfile.write(('event: ' + kind + '\ndata: ' + json.dumps(payload) + '\n\n').encode())
                self.wfile.flush()
            partial = mode == 'partial' and not recovered
            response = {'id': 'resp_fixture', 'object': 'response', 'created_at': 1, 'status': 'in_progress', 'model': 'gpt-5.4', 'output': []}
            item = {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'status': 'in_progress', 'content': []}
            try:
                emit('response.created', response=response)
                emit('response.output_item.added', output_index=0, item=item)
                emit('response.content_part.added', item_id=item['id'], output_index=0, content_index=0, part={'type': 'output_text', 'text': '', 'annotations': []})
                text = 'PARTIAL_BEFORE_FAILURE' if partial else 'RECOVERED_OK'
                emit('response.output_text.delta', item_id=item['id'], output_index=0, content_index=0, delta=text)
                if partial:
                    emit('error', code='rate_limit_exceeded', message='Synthetic midstream quota exhausted', param=None)
                    self.close_connection = True
                    return
                part = {'type': 'output_text', 'text': text, 'annotations': []}
                emit('response.output_text.done', item_id=item['id'], output_index=0, content_index=0, text=text)
                emit('response.content_part.done', item_id=item['id'], output_index=0, content_index=0, part=part)
                item.update(status='completed', content=[part])
                emit('response.output_item.done', output_index=0, item=item)
                response.update(status='completed', output=[item], usage={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15, 'input_tokens_details': {'cached_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 0}})
                emit('response.completed', response=response)
            except (ConnectionError, OSError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='codex-fixture-', dir=ROOT) as isolated:
            env = {k: v for k, v in os.environ.items() if not k.startswith(('CODEX_', 'OPENAI_', 'ANTHROPIC_', 'CLAUDE_'))}
            home = Path(isolated) / 'home'
            work = Path(isolated) / 'work'
            home.mkdir()
            work.mkdir()
            env['CODEX_HOME'] = str(home)
            env['HOME'] = str(home)
            env['USERPROFILE'] = str(home)
            config = f'''model = "gpt-5.4"
model_provider = "fixture"
approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"
check_for_update_on_startup = false
[analytics]
enabled = false
[feedback]
enabled = false
[features]
plugins = false
apps = false
[model_providers.fixture]
name = "Local test fixture"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
request_max_retries = 2
stream_max_retries = {1 if mode == 'quota' else 0}
stream_idle_timeout_ms = 1000
supports_websockets = false
'''
            (home / 'config.toml').write_text(config, encoding='utf-8')
            commands = []
            def execute(args):
                cmd = [str(EXE), 'exec', *args, '--skip-git-repo-check', '--json', '--ignore-rules']
                result = subprocess.run(cmd, cwd=work, env=env, input='', capture_output=True, text=True, encoding='utf-8', timeout=60)
                events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
                commands.append({'command': cmd, 'exit_code': result.returncode, 'events': events, 'stderr': result.stderr})
                return events
            events = execute(['Say hello. Do not use tools.'])
            first = events
            if mode == 'partial':
                thread_id = next(e['thread_id'] for e in events if e['type'] == 'thread.started')
                recovered = True
                events = execute(['resume', thread_id, 'Continue'])
            report = {'mode': mode, 'requests': requests, 'commands': commands}
            (ROOT / (mode + '-results.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
            if mode == 'quota':
                assert any(e.get('type') == 'turn.failed' for e in events), 'Expected observed terminal 429 failure'
                print(json.dumps({'mode': mode, 'requests': len(requests), 'observed': 'terminal429_no_retry', 'events': events}), flush=True)
                return
            assert any(e.get('type') == 'turn.completed' for e in events), report
            assert any(e.get('item', {}).get('text') == 'RECOVERED_OK' for e in events), report
            if mode == 'partial':
                assert any(e.get('type') == 'turn.failed' for e in first), report
                assert any(e.get('thread_id') == thread_id for e in events), report
                assert 'Say hello' in json.dumps(requests[-1]['input'])
                assert 'Continue' in json.dumps(requests[-1]['input'])
            print(json.dumps({'mode': mode, 'requests': len(requests), 'commands': [{'exit_code': c['exit_code'], 'events': c['events']} for c in commands]}), flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    print(subprocess.check_output([str(EXE), '--version'], text=True).strip(), flush=True)
    # Partial/resume fixture is implemented but intentionally excluded until verified.
    for mode in ('normal', 'quota'):
        run_case(mode)
