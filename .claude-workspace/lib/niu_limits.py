"""Claude plan usage limits (5-hour and weekly), as last reported by Claude Code's status line (bin/niu-statusline)."""
import json
import os
import threading
import time
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
_api = {"at": 0.0, "rate_limits": None}
_api_lock = threading.Lock()


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
        token = json.loads(_credentials_path().read_text())["claudeAiOauth"]["accessToken"]
        if not isinstance(token, str) or not token:
            return None
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
                                                   "User-Agent": "niu-heimar-dashboard"})
        with _opener.open(req, timeout=timeout) as r:
            body = json.loads(r.read(65536))
    except Exception:  # noqa: BLE001 - any failure (network, HTTP, parse) just means no API numbers
        return None
    out = {}
    for key in ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet"):
        b = body.get(key) if isinstance(body, dict) else None
        if isinstance(b, dict) and isinstance(b.get("utilization"), (int, float)):
            out[key] = {"used_percentage": b["utilization"], "resets_at": b.get("resets_at")}
    return out or None


def _api_limits(now, fetch=None):
    """Cached (5 min) usage from the API when enabled; (rate_limits, fetched_at) or (None, 0)."""
    if os.environ.get("NIU_CLAUDE_USAGE_API") == "0":
        return None, 0
    with _api_lock:  # dashboard requests run in threads: one fetch per refresh window
        if now - _api["at"] > API_REFRESH_SECS:
            _api["at"] = now  # also throttles retries after a failure
            got = (fetch or _fetch_usage)()
            if got:
                _api["rate_limits"], _api["fetched"] = got, now
        return _api["rate_limits"], _api.get("fetched", 0)


def claude_limits(now=None, fetch=None):
    """{'available': bool, 'updated', 'stale', 'source', 'windows': [{key, label, used, resets_at}]}.

    Newest of: the terminal status line's last report, and the usage API (unless disabled)."""
    now = now or time.time()
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
    if not data:
        return {"available": False}
    rl = data.get("rate_limits") or {}
    windows = []
    for key, label in (("five_hour", "5-hour"), ("seven_day", "Weekly"), ("seven_day_opus", "Weekly Opus"),
                       ("seven_day_sonnet", "Weekly Sonnet")):
        b = rl.get(key)
        if not isinstance(b, dict):
            continue
        try:
            used = float(b.get("used_percentage"))
        except (TypeError, ValueError):
            continue
        reset = _epoch(b.get("resets_at"))
        if reset and reset < now:
            used = 0.0  # the window has rolled over since the last report
        windows.append({"key": key, "label": label, "used": round(used, 1), "resets_at": reset})
    updated = data.get("updated", 0)
    return {"available": bool(windows), "updated": updated, "source": source, "stale": now - updated > STALE_SECS, "windows": windows}
