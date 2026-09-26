"""Claude Code and Codex adapters: read/write the live login and fetch real usage.

Endpoints, headers and response shapes follow the vendored Codex Vitals clients
(codex-vitals-source: ClaudeUsageClient.swift, codex_api.py). Standard library only.
"""
import base64
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .vault import atomic_write

TIMEOUT = 15


class ProviderError(Exception):
    """Short, user-facing problem description."""

    def __init__(self, message, retry_after=None, relogin=False):
        super().__init__(message)
        self.retry_after, self.relogin = retry_after, relogin


@dataclass
class LiveLogin:
    identity: str   # stable key (account UUID / ChatGPT account id, else email)
    email: str
    plan: str
    secret: dict    # everything needed to restore this login later


def _http(method, url, headers, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = Request(url, data=data, method=method, headers=dict(headers, **({"Content-Type": "application/json"} if data else {})))
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read() or b"null")
    except HTTPError as error:
        retry = error.headers.get("Retry-After") if error.headers else None
        if error.code == 429:
            raise ProviderError("Usage API is rate limiting; will retry", retry_after=float(retry) if retry and retry.isdigit() else 300)
        if error.code in (401, 403):
            raise ProviderError("Login expired", relogin=True)
        raise ProviderError(f"Usage API error {error.code}")
    except (URLError, TimeoutError, OSError):
        raise ProviderError("Offline or usage API unreachable")
    except ValueError:
        raise ProviderError("Unexpected usage API response")


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _iso_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _jwt_payload(token):
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (AttributeError, IndexError, ValueError):
        return {}


# ---------- subscription renewal / end (best effort) ----------
DATE_KEYS = ("current_period_end", "renews_at", "renewal_date", "next_billing_date", "next_billing_at",
             "next_charge_date", "period_end", "billing_period_end", "subscription_expires_at", "expires_at",
             "active_until", "chatgpt_subscription_active_until")
CANCEL_DATE_KEYS = ("cancel_at", "cancels_at", "ends_at")


def _ts(value):
    """ISO string or epoch (s/ms) -> epoch seconds, or None."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else float(value)
    return _iso_ts(value)


def key_paths(data, prefix=""):
    """Field names only (never values), for diagnosing which fields an endpoint offers."""
    paths = []
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.append(path)
            paths += key_paths(value, path)
    elif isinstance(data, list) and data:
        paths += key_paths(data[0], prefix + "[]")
    return paths


def subscription_from(data):
    """(renews_or_ends_at, ends) from an account/billing payload.

    ends: True when set to end, False when it says it will renew, None when unknown.
    """
    found, ends = None, None
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                k = str(key).lower()
                if k in CANCEL_DATE_KEYS and _ts(value):
                    found, ends = _ts(value), True
                elif k in DATE_KEYS and found is None and _ts(value):
                    found = _ts(value)
                elif k == "cancel_at_period_end" and isinstance(value, bool):
                    ends = True if value else (ends if ends else False)
                elif k in ("will_renew", "auto_renew", "autorenew", "is_auto_renew") and isinstance(value, bool):
                    ends = True if not value else (ends if ends else False)
                elif k in ("subscription_status",) and str(value).lower() in ("canceled", "cancelled", "non_renewing", "ending"):
                    ends = True
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return found, ends


def _plan_name(raw):
    names = {"max": "Max", "pro": "Pro", "plus": "Plus", "team": "Team", "enterprise": "Enterprise",
             "business": "Business", "free": "Free", "prolite": "Pro Lite", "edu": "Edu"}
    return names.get(str(raw or "").lower().replace("_", "").replace(" ", ""), str(raw or "").title())


class Claude:
    name = "claude"
    last_credits = None
    USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
    TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
    CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    SHARED_KEYS = ("mcpOAuth", "mcpOAuthClientConfig", "mcpXaaIdp", "mcpXaaIdpConfig", "pluginSecrets")

    def __init__(self, config_dir=None, home=None):
        home = Path(home) if home else Path.home()
        custom = config_dir or os.environ.get("CLAUDE_CONFIG_DIR")
        self.config_dir = Path(custom) if custom else home / ".claude"
        self.config_file = (self.config_dir / ".claude.json") if custom else home / ".claude.json"
        self.credentials_file = self.config_dir / ".credentials.json"

    def signature(self):
        return _mtime(self.credentials_file), _mtime(self.config_file)

    def read_live(self):
        credentials, config = _read_json(self.credentials_file), _read_json(self.config_file)
        oauth = (credentials or {}).get("claudeAiOauth")
        account = (config or {}).get("oauthAccount")
        if not isinstance(oauth, dict) or not oauth.get("accessToken") or not isinstance(account, dict):
            return None
        email = account.get("emailAddress") or ""
        identity = account.get("accountUuid") or email
        if not identity:
            return None
        return LiveLogin(identity, email, self.plan(oauth), {"credentials": credentials, "oauthAccount": account})

    @staticmethod
    def plan(oauth):
        plan = _plan_name(oauth.get("subscriptionType"))
        tier = str(oauth.get("rateLimitTier") or "")
        for multiple in ("20x", "5x"):
            if multiple in tier:
                return f"{plan} {multiple}"
        return plan

    def write_live(self, secret):
        live = _read_json(self.credentials_file) or {}
        credentials = {k: v for k, v in secret["credentials"].items() if k not in self.SHARED_KEYS}
        credentials.update({k: live[k] for k in self.SHARED_KEYS if k in live})  # MCP logins stay put
        config = _read_json(self.config_file)
        if config is None and self.config_file.exists():
            raise ProviderError("Claude's config file is unreadable; not switching")
        config = config or {}
        config["oauthAccount"] = secret["oauthAccount"]
        atomic_write(self.credentials_file, json.dumps(credentials, indent=2).encode(), private=True)
        atomic_write(self.config_file, json.dumps(config, indent=2).encode())

    def fetch(self, secret, allow_refresh):
        """Return (windows, plan, updated_secret_or_None)."""
        updated = None
        oauth = secret["credentials"]["claudeAiOauth"]
        expires = (oauth.get("expiresAt") or 0) / 1000
        if allow_refresh and expires and expires - time.time() < 300:
            secret = updated = self.refresh(secret)
            oauth = secret["credentials"]["claudeAiOauth"]
        try:
            _, body = _http("GET", self.USAGE_URL, {"Authorization": "Bearer " + oauth["accessToken"],
                                                  "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json",
                                                  "User-Agent": "account-switcher"})
        except ProviderError as error:
            if not (error.relogin and allow_refresh and updated is None):
                raise
            secret = updated = self.refresh(secret)
            oauth = secret["credentials"]["claudeAiOauth"]
            _, body = _http("GET", self.USAGE_URL, {"Authorization": "Bearer " + oauth["accessToken"],
                                                  "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json",
                                                  "User-Agent": "account-switcher"})
        self.last_credits = self.credits(body or {})
        return self.windows(body or {}), self.plan(oauth), updated

    def refresh(self, secret):
        oauth = secret["credentials"]["claudeAiOauth"]
        if not oauth.get("refreshToken"):
            raise ProviderError("Login expired; sign in again", relogin=True)
        try:
            _, token = _http("POST", self.TOKEN_URL, {"Accept": "application/json"},
                             {"grant_type": "refresh_token", "refresh_token": oauth["refreshToken"], "client_id": self.CLIENT_ID})
        except ProviderError as error:
            if error.relogin or "error 400" in str(error):
                raise ProviderError("Login expired; sign in again", relogin=True)
            raise
        expected = secret["oauthAccount"].get("accountUuid")
        actual = ((token or {}).get("account") or {}).get("uuid")
        if expected and actual and expected != actual:
            raise ProviderError("Refresh returned a different account", relogin=True)
        fresh = dict(oauth, accessToken=token["access_token"], expiresAt=int((time.time() + int(token.get("expires_in", 3600))) * 1000))
        if token.get("refresh_token"):
            fresh["refreshToken"] = token["refresh_token"]
        if token.get("scope"):
            fresh["scopes"] = token["scope"].split()
        return dict(secret, credentials=dict(secret["credentials"], claudeAiOauth=fresh))

    @staticmethod
    def windows(body):
        rows = []
        for key, label, field in (("five_hour", "5-hour", "five_hour"), ("weekly", "Weekly", "seven_day")):
            window = body.get(field)
            if isinstance(window, dict) and window.get("utilization") is not None:
                rows.append({"key": key, "label": label, "used": float(window["utilization"]),
                             "resetsAt": _iso_ts(window.get("resets_at")), "scope": "account"})
        seen = set()
        for limit in body.get("limits") or []:
            model = (((limit or {}).get("scope") or {}).get("model") or {}).get("display_name")
            if limit.get("kind") == "weekly_scoped" and model and limit.get("percent") is not None and model not in seen:
                seen.add(model)
                rows.append({"key": "model-" + model.lower().replace(" ", "-"), "label": f"Weekly · {model}",
                             "used": float(limit["percent"]), "resetsAt": _iso_ts(limit.get("resets_at")), "scope": "model"})
        overage = body.get("seven_day_overage_included")
        if not any("fable" in m.lower() for m in seen) and isinstance(overage, dict) and overage.get("utilization") is not None:
            # Codex Vitals reads this field as the Fable weekly cap when no scoped limit is listed.
            rows.append({"key": "model-fable", "label": "Weekly · Fable", "used": float(overage["utilization"]),
                         "resetsAt": _iso_ts(overage.get("resets_at")), "scope": "model"})
        return rows

    PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"

    def subscription(self, secret):
        """(at, ends, field names) from Claude's account profile; best effort, at most daily."""
        oauth = secret["credentials"]["claudeAiOauth"]
        _, body = _http("GET", self.PROFILE_URL, {"Authorization": "Bearer " + oauth["accessToken"],
                                                  "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json",
                                                  "User-Agent": "account-switcher"})
        at, ends = subscription_from(body or {})
        return at, ends, key_paths(body or {})

    @staticmethod
    def credits(body):
        """Extra usage (pay-as-you-go credits) from the usage response, if reported."""
        extra = body.get("extra_usage")
        if not isinstance(extra, dict):
            return None
        info = {"kind": "extra", "enabled": bool(extra.get("is_enabled"))}
        for src, dst in (("monthly_limit", "limit"), ("used_credits", "used"), ("utilization", "utilization")):
            if isinstance(extra.get(src), (int, float)):
                info[dst] = float(extra[src])
        return info

    def login_command(self, directory):
        return ["claude", "auth", "login"], {"CLAUDE_CONFIG_DIR": str(directory)}

    def isolated(self, directory):
        return Claude(config_dir=directory)


class Codex:
    name = "codex"
    last_credits = None
    USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
    TOKEN_URL = "https://auth.openai.com/oauth/token"
    CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
    ROLES = {18000: ("five_hour", "5-hour"), 604800: ("weekly", "Weekly"), 2592000: ("monthly", "30-day")}

    def __init__(self, codex_home=None, home=None):
        home = Path(home) if home else Path.home()
        self.codex_home = Path(codex_home or os.environ.get("CODEX_HOME") or home / ".codex")
        self.auth_file = self.codex_home / "auth.json"

    def signature(self):
        return (_mtime(self.auth_file),)

    def read_live(self):
        auth = _read_json(self.auth_file)
        tokens = (auth or {}).get("tokens")
        if not isinstance(tokens, dict) or not tokens.get("access_token"):
            return None  # API-key mode or signed out: nothing to switch
        claims = _jwt_payload(tokens.get("id_token"))
        info = claims.get("https://api.openai.com/auth") or {}
        email = claims.get("email") or (claims.get("https://api.openai.com/profile") or {}).get("email") or ""
        account_id = tokens.get("account_id") or info.get("chatgpt_account_id")
        identity = account_id or email
        if not identity:
            return None
        return LiveLogin(identity, email, _plan_name(info.get("chatgpt_plan_type")), {"auth": auth})

    def write_live(self, secret):
        atomic_write(self.auth_file, json.dumps(secret["auth"], indent=2).encode(), private=True)

    def fetch(self, secret, allow_refresh):
        updated = None
        tokens = secret["auth"]["tokens"]
        last = _iso_ts(secret["auth"].get("last_refresh"))
        if allow_refresh and (last is None or time.time() - last > 8 * 86400):
            secret = updated = self.refresh(secret)
            tokens = secret["auth"]["tokens"]
        try:
            body = self._usage(tokens)
        except ProviderError as error:
            if not (error.relogin and allow_refresh and updated is None):
                raise
            secret = updated = self.refresh(secret)
            tokens = secret["auth"]["tokens"]
            body = self._usage(tokens)
        claims = _jwt_payload(tokens.get("id_token")).get("https://api.openai.com/auth") or {}
        plan = _plan_name(body.get("plan_type") or claims.get("chatgpt_plan_type"))
        self.last_credits = self.credits(body)
        return self.windows(body), plan, updated

    @staticmethod
    def credits(body):
        credits = body.get("credits")
        if not isinstance(credits, dict):
            return None
        info = {"kind": "credits", "enabled": bool(credits.get("has_credits")) or bool(credits.get("unlimited")),
                "unlimited": bool(credits.get("unlimited"))}
        try:
            if credits.get("balance") is not None:
                info["balance"] = float(credits["balance"])
        except (TypeError, ValueError):
            pass
        return info

    def _usage(self, tokens):
        headers = {"Authorization": "Bearer " + tokens["access_token"], "User-Agent": "codex-cli",
                   "Accept": "application/json", "Cache-Control": "no-cache"}
        if tokens.get("account_id"):
            headers["ChatGPT-Account-Id"] = tokens["account_id"]
        _, body = _http("GET", self.USAGE_URL, headers)
        return body if isinstance(body, dict) else {}

    def refresh(self, secret):
        tokens = secret["auth"]["tokens"]
        if not tokens.get("refresh_token"):
            raise ProviderError("Login expired; sign in again", relogin=True)
        try:
            _, body = _http("POST", self.TOKEN_URL, {"Cache-Control": "no-cache"},
                            {"client_id": self.CLIENT_ID, "grant_type": "refresh_token",
                             "refresh_token": tokens["refresh_token"], "scope": "openid profile email"})
        except ProviderError as error:
            raise ProviderError("Login expired; sign in again", relogin=True) if error.relogin else error
        fresh = dict(tokens, access_token=body.get("access_token") or tokens["access_token"],
                     refresh_token=body.get("refresh_token") or tokens["refresh_token"])
        if body.get("id_token"):
            fresh["id_token"] = body["id_token"]
        stamp = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        return {"auth": dict(secret["auth"], tokens=fresh, last_refresh=stamp)}

    @classmethod
    def windows(cls, body):
        limits = body.get("rate_limit") if isinstance(body.get("rate_limit"), dict) else {}
        rows = []
        for field in ("primary_window", "secondary_window"):
            window = limits.get(field)
            if not isinstance(window, dict) or window.get("used_percent") is None:
                continue
            seconds = int(window.get("limit_window_seconds") or 0)
            key, label = cls.ROLES.get(seconds, (f"window-{seconds}", f"{round(seconds / 3600)}-hour"))
            rows.append({"key": key, "label": label, "used": float(window["used_percent"]),
                         "resetsAt": float(window["reset_at"]) if window.get("reset_at") else None, "scope": "account"})
        if limits.get("limit_reached") and rows and all(r["used"] < 100 for r in rows):
            max(rows, key=lambda r: r["used"])["used"] = 100.0  # provider says blocked even if rounding says otherwise
        order = {"five_hour": 0, "weekly": 1, "monthly": 2}
        return sorted(rows, key=lambda r: order.get(r["key"], 3))

    CHECK_URL = "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"

    def subscription(self, secret):
        """(at, ends, field names): paid-through date from the login token; cancellation best effort."""
        tokens = secret["auth"]["tokens"]
        claims = _jwt_payload(tokens.get("id_token")).get("https://api.openai.com/auth") or {}
        at = _ts(claims.get("chatgpt_subscription_active_until"))
        ends, paths = None, ["id_token." + k for k in claims]
        headers = {"Authorization": "Bearer " + tokens["access_token"], "User-Agent": "codex-cli", "Accept": "application/json"}
        if tokens.get("account_id"):
            headers["ChatGPT-Account-Id"] = tokens["account_id"]
        try:
            _, body = _http("GET", self.CHECK_URL, headers)
            accounts = (body or {}).get("accounts") if isinstance(body, dict) else None
            account = accounts.get(tokens.get("account_id") or "") if isinstance(accounts, dict) else None
            source = account if isinstance(account, dict) else (body or {})
            api_at, ends = subscription_from(source.get("entitlement", source) if isinstance(source, dict) else {})
            at = api_at or at
            paths += key_paths(body or {})
        except ProviderError:
            pass  # the token date alone is still useful
        return at, ends, paths

    def login_command(self, directory):
        return ["codex", "login"], {"CODEX_HOME": str(directory)}

    def isolated(self, directory):
        return Codex(codex_home=directory)


PROVIDERS = {"claude": Claude, "codex": Codex}
