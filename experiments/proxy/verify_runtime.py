"""Behavioral fixture checks, no official client or provider credentials."""
import json
from pathlib import Path
import urllib.error
import urllib.request
from runtime import ProxyFixture, MODEL


def turn(fixture, stream=False):
    body = {'model': MODEL, 'stream': stream, 'max_tokens': 32,
            'messages': [{'role': 'user', 'content': 'Hello'}]}
    request = urllib.request.Request(fixture.base_url + '/v1/messages', json.dumps(body).encode(),
        {'Authorization': 'Bearer ' + fixture.api_key, 'Content-Type': 'application/json'})
    return urllib.request.urlopen(request, timeout=10).read().decode()


def verify():
    findings = []
    fixture = ProxyFixture().start()
    directory = fixture.directory
    try:
        assert 'demo account A' in turn(fixture)
        fixture.set_active('B')
        assert 'demo account B' in turn(fixture)
        fixture.set_active('A')
        fixture.set_auto_swap(False)
        fixture.modes['A'] = 'quota'
        for _ in range(2):
            try:
                turn(fixture)
                raise AssertionError('Auto swap off unexpectedly succeeded')
            except urllib.error.HTTPError as error:
                assert error.code in (429, 503), error.code
        assert list(fixture.events)[-1]['account'] == 'A'
        findings.append('Manual A/B selection and auto-swap OFF across repeated client requests passed')
        fixture.set_auto_swap(True)
        assert 'demo account B' in turn(fixture)
        findings.append('Auto-swap ON can use reserve account passed')
        log = (directory / 'server.log').read_text()
        assert 'antigravity version refresh' not in log
        assert 'updater manifest' not in log
        findings.append('No Antigravity updater start or external attempt in patched startup log')
    finally:
        fixture.stop()
    assert not directory.exists(), directory
    fixture = ProxyFixture().start()
    try:
        fixture.modes.update(A='partial', B='quota')
        partial = turn(fixture, stream=True)
        assert 'Dummy interruption after partial text' in partial and 'message_stop' not in partial
        try:
            turn(fixture)
            raise AssertionError('Partial mode nonstream fallback unexpectedly succeeded')
        except urllib.error.HTTPError as error:
            assert error.code == 429, error.code
        fixture.settle_failure()
        resumed = turn(fixture, stream=True)
        assert 'demo account B' in resumed and 'message_stop' in resumed
        findings.append('Partial SSE, failed nonstream fallback, and settled B recovery passed')
        fixture.set_auto_swap(False)
        fixture.recover_to('A')
        assert 'demo account A' in turn(fixture)
        fixture.set_auto_swap(True)
        assert 'demo account A' in turn(fixture)
        fixture.reset_accounts()
        assert 'demo account A' in turn(fixture)
        assert fixture.modes == {'A': 'normal', 'B': 'normal'}
        findings.append('Recovery to A, retained selection when re-enabling, and reset without URL restart passed')
    finally:
        fixture.stop()
    findings.append('All fixture processes stopped and validated temporary directories removed')
    Path(__file__).with_name('runtime-verification.json').write_text(json.dumps(findings, indent=2))
    print('\n'.join(findings))


if __name__ == '__main__':
    verify()
