"""Claude Code status line (installed by LimitSwitch while it runs).

Claude Code runs the status line command after each reply and passes it the session's data,
including the live 5-hour and weekly usage of the signed-in account (rate_limits). That is how
the app follows Claude usage live: no tokens, no API calls. This script
  1. hands rate_limits to the running app (loopback, a fraction of a second at most), then
  2. prints the status line: the user's own status line command if they had one (same input,
     its output unchanged), else the app's compact line (account and what's left).
Standard library only; it must not import the app. When the app isn't running it still runs
the user's own command, so their status line never breaks.
"""
import json
import subprocess
import sys
from urllib.request import ProxyHandler, Request, build_opener


def report(state, data):
    limits = data.get("rate_limits")
    try:
        body = json.dumps({"rate_limits": limits if isinstance(limits, dict) else None,
                           "session": str(data.get("session_id") or "")[:100]}).encode()
        request = Request(state["url"].rsplit("/", 1)[0] + "/statusline", data=body, method="POST",
                          headers={"Authorization": "Bearer " + state["token"], "Content-Type": "application/json"})
        with build_opener(ProxyHandler({})).open(request, timeout=0.6) as response:
            answer = json.load(response)
        return answer.get("line") if isinstance(answer, dict) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def run_previous(command, raw):
    try:
        done = subprocess.run(command, shell=True, input=raw, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return
    sys.stdout.buffer.write(done.stdout)


def main(argv):
    raw = sys.stdin.buffer.read()
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        data = {}
    try:
        with open(argv[1], encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError, IndexError):
        state = {}
    line = report(state, data if isinstance(data, dict) else {}) if state.get("url") else None
    previous = state.get("statusline")
    if previous:
        run_previous(previous, raw)
    elif line:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
