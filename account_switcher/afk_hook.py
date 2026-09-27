"""Claude Code StopFailure hook (installed by LimitSwitch while AFK is on).

Runs in the background (asyncRewake). On a usage limit it asks the running app what to do:
  continue -> print the continuation note and exit 2, which wakes the Claude session
  wait     -> sleep until an account should have headroom again, then ask again
  anything else, or the app not running -> exit 0 and leave the session as it is
Standard library only; it must not import the app.
"""
import json
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener

LIMIT = 6 * 3600 - 120   # stay inside the hook timeout set in settings.json
NOTE = "The usage limit was reached, so the session moved to another account. Continue exactly where you left off."


def main(argv):
    if len(argv) < 2:
        return 0
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict) or event.get("error") != "rate_limit":
        return 0
    opener = build_opener(ProxyHandler({}))  # the app is on loopback: never via a proxy
    deadline = time.time() + LIMIT
    while time.time() < deadline:
        try:
            with open(argv[1], encoding="utf-8") as handle:
                state = json.load(handle)
            body = json.dumps({"provider": "claude", "session": str(event.get("session_id") or "")}).encode()
            request = Request(state["url"], data=body, method="POST",
                              headers={"Authorization": "Bearer " + state["token"], "Content-Type": "application/json"})
            with opener.open(request, timeout=180) as response:
                answer = json.load(response)
        except (OSError, ValueError, KeyError, TypeError):
            return 0  # the app is not running (or not answering): leave the session alone
        action = answer.get("action") if isinstance(answer, dict) else None
        if action == "continue":
            sys.stderr.write(str(answer.get("message") or NOTE))
            return 2
        if action == "wait":
            seconds = answer.get("seconds")
            seconds = seconds if isinstance(seconds, (int, float)) else 600
            time.sleep(max(30.0, min(float(seconds), deadline - time.time())))
            continue
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
