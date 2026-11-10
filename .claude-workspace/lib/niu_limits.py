"""Claude plan usage limits (5-hour and weekly), as last reported by Claude Code's status line (bin/niu-statusline)."""
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from niu_events import db_path

STALE_SECS = 15 * 60
API_PREFERRED_AFTER_SECS = 2 * 60  # a status-line report older than this loses to the API (unless disabled)
# Second source, on by default (set NIU_CLAUDE_USAGE_API=0 to turn it off): the same usage numbers Claude's own apps show, fetched from Anthropic
# with the OAuth login Claude Code already holds. It works for the Claude Code desktop app, which never runs the
# terminal status line. Read-only: the token is never stored, logged or refreshed, and only percentages are kept.
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
API_REFRESH_SECS = 5 * 60
_api = {"at": 0.0, "rate_limits": None, "error": None}
_api_lock = threading.Lock()
# When the token has expired, ask Claude Code to renew it: `claude auth status` (no model call, no usage), at most once an
# hour, in a background thread. Claude Code does the refresh and writes its own credentials file; this module never
# touches the refresh token. NIU_CLAUDE_AUTO_RENEW=0 turns it off.
RENEW_EVERY_SECS = 60 * 60
RENEW_TIMEOUT_SECS = 30
_renew = {"at": 0.0, "running": False, "result": None}


def _epoch(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) / (1000 if v > 1e12 else 1)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The bearer token must only ever go to the pinned URL, never to a redirect target."""
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _credentials_path():
    return Path(os.environ.get("NIU_CLAUDE_CREDENTIALS") or Path.home() / ".claude" / ".credentials.json")


def _fetch_usage(url=USAGE_URL, timeout=8):
    """Plan usage from Anthropic, shaped like the status line's rate_limits block; None on any problem."""
    try:
        oauth = json.loads(_credentials_path().read_text())["claudeAiOauth"]
        token = oauth["accessToken"]
        if not isinstance(token, str) or not token:
            _api["error"] = "no_login"
            return None
        exp = _epoch(oauth.get("expiresAt"))
        if exp is not None and exp <= time.time():
            _api["error"] = "expired"  # only Claude Code renews it; an expired token is never sent
            return None
    except Exception:  # noqa: BLE001 - missing or unreadable credentials file
        _api["error"] = "no_login"
        return None
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
                                                   "User-Agent": "niu-heimar-dashboard"})
        with _opener.open(req, timeout=timeout) as r:
            body = json.loads(r.read(65536))
    except urllib.error.HTTPError as e:
        _api["error"] = "rejected" if e.code in (401, 403) else "unreachable"
        return None
    except Exception:  # noqa: BLE001 - any other failure (network, parse) just means no API numbers
        _api["error"] = "unreachable"
        return None
    out = {}
    for key in ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet"):
        b = body.get(key) if isinstance(body, dict) else None
        if isinstance(b, dict) and isinstance(b.get("utilization"), (int, float)):
            out[key] = {"used_percentage": b["utilization"], "resets_at": b.get("resets_at")}
    _api["error"] = None if out else "unreachable"
    return out or None


def _claude_bin():
    """The claude CLI: on PATH, else where the native installer puts it (a systemd user service's PATH lacks ~/.local/bin)."""
    found = shutil.which("claude")
    if found:
        return found
    for p in (Path.home() / ".local" / "bin" / "claude", Path.home() / ".claude" / "local" / "claude"):
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def _token_expired():
    """True/False from the credentials file's expiresAt; None when unknown."""
    try:
        exp = _epoch(json.loads(_credentials_path().read_text())["claudeAiOauth"].get("expiresAt"))
    except Exception:  # noqa: BLE001
        return None
    return None if exp is None else exp <= time.time()


def _renew_worker(run):
    try:
        exe = _claude_bin()
        if not exe:
            _renew["result"] = "no_cli"
            return
        try:
            run([exe, "auth", "status"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=RENEW_TIMEOUT_SECS, check=False, cwd=str(Path.home()))
        except Exception:  # noqa: BLE001 - timeout, missing binary, ...
            _renew["result"] = "failed"
            return
        if _token_expired() is False:
            _renew["result"] = "renewed"
            with _api_lock:
                _api["at"] = 0.0  # fetch again on the next read instead of waiting out the 5 minutes
        else:
            _renew["result"] = "failed"
    finally:
        _renew["running"] = False


def _maybe_renew(now, run=None, wait=False):
    """Start one renewal attempt if the token has expired, auto-renew is on and the last attempt is over an hour old."""
    if os.environ.get("NIU_CLAUDE_AUTO_RENEW") == "0" or _renew["running"] or now - _renew["at"] < RENEW_EVERY_SECS:
        return
    _renew.update(at=now, running=True, result=None)
    t = threading.Thread(target=_renew_worker, args=(run or subprocess.run,), daemon=True, name="niu-token-renew")
    t.start()
    if wait:
        t.join()


def api_error():
    """Why the usage API gave no numbers on its last attempt: 'off', 'expired', 'renew_failed', 'rejected', 'no_login', 'unreachable', or None."""
    if os.environ.get("NIU_CLAUDE_USAGE_API") == "0":
        return "off"
    err = _api.get("error")
    if err == "expired" and _renew["result"] in ("failed", "no_cli"):
        return "renew_failed"
    return err


def _api_limits(now, fetch=None):
    """Cached (5 min) usage from the API when enabled; (rate_limits, fetched_at) or (None, 0)."""
    if os.environ.get("NIU_CLAUDE_USAGE_API") == "0":
        return None, 0
    with _api_lock:  # dashboard requests run in threads: one fetch per refresh window
        if now - _api["at"] > API_REFRESH_SECS:
            _api["at"] = now  # also throttles retries after a failure
            got = (fetch or _fetch_usage)()
            if got:
                _api["rate_limits"], _api["fetched"], _api["error"] = got, now, None
            elif _api.get("error") == "expired":
                _maybe_renew(now)
        return _api["rate_limits"], _api.get("fetched", 0)


LIMIT_KEYS = (("five_hour", "5-hour"), ("seven_day", "Weekly"), ("seven_day_opus", "Weekly Opus"), ("seven_day_sonnet", "Weekly Sonnet"))


def _select(now, fetch=None):
    """Source selection: (updated, source, rate_limits dict) from the newest of the status line's report and the usage API."""
    source = "statusline"
    try:
        data = json.loads((db_path().parent / "claude-limits.json").read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("updated"), (int, float)):
        data["updated"] = 0
    if now - data.get("updated", 0) > API_PREFERRED_AFTER_SECS:
        rl_api, at = _api_limits(now, fetch)
        if rl_api and at > data.get("updated", 0):
            data, source = {"rate_limits": rl_api, "updated": at}, "api"
    rl = data.get("rate_limits")
    return data.get("updated", 0), source, rl if isinstance(rl, dict) else {}


def _raw_windows(rl):
    out = {}
    for key, _label in LIMIT_KEYS:
        b = rl.get(key)
        if not isinstance(b, dict):
            continue
        try:
            used = float(b.get("used_percentage"))
        except (TypeError, ValueError):
            continue
        out[key] = {"used": used, "resets_at": _epoch(b.get("resets_at"))}
    return out


def latest_reading(now=None, fetch=None):
    """(updated, source, {key: {'used': float, 'resets_at': epoch|None}}) as last reported, or None.

    Raw values: no rounding and no 'window rolled over, so 0' substitution (claude_limits applies that for display)."""
    updated, source, rl = _select(now or time.time(), fetch)
    windows = _raw_windows(rl)
    return (updated, source, windows) if windows else None


def claude_limits(now=None, fetch=None):
    """{'available': bool, 'updated', 'stale', 'source', 'windows': [{key, label, used, resets_at}]}.

    Newest of: the terminal status line's last report, and the usage API (unless disabled)."""
    now = now or time.time()
    updated, source, rl = _select(now, fetch)
    raw = _raw_windows(rl)
    windows = []
    for key, label in LIMIT_KEYS:
        if key not in raw:
            continue
        used, reset = raw[key]["used"], raw[key]["resets_at"]
        if reset and reset < now:
            used = 0.0  # the window has rolled over since the last report
        windows.append({"key": key, "label": label, "used": round(used, 1), "resets_at": reset})
    return {"available": bool(windows), "updated": updated, "source": source, "stale": now - updated > STALE_SECS, "windows": windows}
