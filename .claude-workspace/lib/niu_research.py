"""Research tab data: polls research-dispatch's read-only monitor endpoint and re-shapes it for the dashboard.

Design: docs/design/2026-11-07-dashboard-research-tab.md. Rules this module keeps:
- The only outbound target is `<validated loopback base URL>/api/v1/monitor/summary`. The base URL comes from local config,
  never from a request. No proxies, no redirects, a response size cap, a timeout.
- The bearer token is read fresh from a 0600 file (never a symlink, never someone else's) for each fetch. It is kept in
  local variables only: never logged, cached, put in an exception or sent anywhere but that one request header.
- Everything that leaves this module is rebuilt from an allowlist (`shape`): unknown keys are dropped, enums and strings
  are checked, numbers are clamped. Messages are fixed strings, never `str(exc)`. Nothing is written to stdout or stderr.
"""
import http.client
import json
import math
import os
import re
import stat
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from niu_events import db_path

SCHEMA = "monitor.v1"
PATH = "/api/v1/monitor/summary"
MAX_BODY = 128 * 1024
MIN_POLL_SECS = 60            # the server never asks research-dispatch more often than this (tests lower it)
FORCE_GAP_SECS = 10           # a manual refresh is allowed at most this often
ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {"enabled": True, "url": "http://127.0.0.1:8080", "poll_secs": 60, "timeout_secs": 4}

URL_RE = re.compile(r"http://(?:127\.0\.0\.1|\[::1\]):([1-9][0-9]{0,4})")
TOKEN_RE = re.compile(r"rd_pat_[A-Za-z0-9_-]{20,128}")
TS_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
DAY_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
SLOT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}\[[A-Za-z0-9_+/-]{1,40}\]|manual:[0-9a-f-]{36}")
UUID_RE = re.compile(r"[0-9a-f-]{36}")
MODEL_RE = re.compile(r"[a-z0-9.-]{1,40}")
TZ_RE = re.compile(r"[A-Za-z0-9_+/-]{1,40}")
CRON_RE = re.compile(r"[0-9*/, -]{1,40}")
VERSION_RE = re.compile(r"[0-9A-Za-z.+-]{1,32}")

RUN_STATUS = frozenset("waiting_gate running succeeded partial failed skipped_gate aborted_quota".split())
AGENT_STATUS = frozenset("pending running succeeded failed skipped timed_out aborted_quota".split())
AGENT_TYPES = ("general", "hobby", "fintrend")
DECISIONS = frozenset(("run", "run_conservative", "retry", "skip"))
REASONS = frozenset("ok ok_conservative weekly_low low_quota probe_warning no_signal manual_cap grace_expired kill_switch".split())
SOURCES = frozenset(("usage_api", "probe", "none", "rate_limit_event"))
ERRORS = frozenset("timeout aborted_quota max_turns max_budget structure_invalid no_sources rate_limited auth interrupted network internal".split())
TRIGGERS = frozenset(("cron", "manual", "catchup"))
MODES = frozenset(("normal", "conservative"))
DIGEST = frozenset("not_built pending held_quiet_hours sent partial failed dead nothing_new suppressed".split())

MESSAGES = {
    "disabled": "The Research tab is turned off (research.json: enabled is false).",
    "not_configured": "No monitor token yet. Run `niu-dash research-token` in a terminal on this machine and paste the token.",
    "loading": "Checking research-dispatch…",
    "ok": "",
    "unreachable": "research-dispatch is not reachable on {addr}. Is the container running? (`docker compose ps` in PERSONAL/research-dispatch)",
    "unauthorized": "Token rejected: check API_TOKENS in research-dispatch and re-run `niu-dash research-token`.",
    "incompatible": "research-dispatch has no compatible monitor endpoint (needs monitor.v1).",
}
ERROR_MESSAGES = {
    "url": "research URL must be http://127.0.0.1:PORT",
    "perms": "The token file must be a regular file you own with mode 600 (and not a symlink).",
    "format": "The token file does not hold a research-dispatch token. Re-run `niu-dash research-token`.",
    "server": "research-dispatch returned an error.",
    "toolarge": "research-dispatch sent a response that is too large.",
    "badjson": "research-dispatch sent a response that could not be read.",
    "internal": "Could not read the research-dispatch status.",
}

LOCK = threading.Lock()
RESEARCH = {"cache": None, "ok_data": None, "ok_at": 0.0, "busy": False, "last_force": 0.0}
_clock = time.time            # tests replace this


# ---------- config, URL, token ----------

def load_config():
    """First readable file among $NIU_RESEARCH_CONFIG, research.json (gitignored), research.template.json; bad values fall back."""
    cfg = dict(DEFAULTS)
    paths = ([Path(os.environ["NIU_RESEARCH_CONFIG"])] if os.environ.get("NIU_RESEARCH_CONFIG") else []) + \
        [ROOT / "research.json", ROOT / "research.template.json"]
    for p in paths:
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if isinstance(data.get("enabled"), bool):
            cfg["enabled"] = data["enabled"]
        # Only the user's own, gitignored config may choose where the token is sent; the tracked
        # template never sets the URL, so editing it cannot redirect the token.
        if p.suffix == ".json" and not p.name.endswith("template.json"):
            cfg["url"] = data["url"] if isinstance(data.get("url"), str) else ""
        for key, lo, hi in (("poll_secs", MIN_POLL_SECS, 3600), ("timeout_secs", 1, 10)):
            v = data.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                cfg[key] = max(lo, min(hi, v))
        break
    cfg["poll_secs"] = max(MIN_POLL_SECS, cfg["poll_secs"])
    return cfg


def validate_url(u):
    """The base URL if it is exactly http://127.0.0.1:PORT or http://[::1]:PORT (PORT 1-65535), else None."""
    if not isinstance(u, str):
        return None
    m = URL_RE.fullmatch(u)
    return u if m and int(m.group(1)) <= 65535 else None


def token_path():
    p = os.environ.get("NIU_RESEARCH_TOKEN_FILE")
    return Path(p) if p else db_path().parent / "research-dispatch.token"


class TokenProblem(Exception):
    """cause: missing | perms | format. Carries no file content."""
    def __init__(self, cause):
        super().__init__(cause)
        self.cause = cause


def valid_token(s):
    return isinstance(s, str) and TOKEN_RE.fullmatch(s) is not None


def read_token():
    """The token string. Refuses symlinks, non-regular files, files owned by someone else and anything group/world accessible."""
    try:
        fd = os.open(token_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        raise TokenProblem("missing") from None
    except OSError:
        raise TokenProblem("perms") from None   # includes a symlink (ELOOP) or an unreadable file
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise TokenProblem("perms")
        raw = os.read(fd, 512)
    finally:
        os.close(fd)
    try:
        text = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        raise TokenProblem("format") from None
    if not valid_token(text):
        raise TokenProblem("format")
    return text


def write_token(token):
    """Atomic 0600 write into the (0700) state directory."""
    if not valid_token(token):
        raise ValueError("not a research-dispatch token")
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    tmp.replace(p)


def remove_token():
    try:
        token_path().unlink()
        return True
    except FileNotFoundError:
        return False


# ---------- fetch ----------

class _Fail(Exception):
    def __init__(self, state, cause=None):
        super().__init__(state)
        self.state, self.cause = state, cause


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The bearer token must only ever go to the configured loopback URL, never to a redirect target."""
    def redirect_request(self, *a, **k):
        return None


def _opener():
    # ProxyHandler({}) switches off http_proxy & co: loopback traffic must never leave through a proxy.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def fetch(cfg, token):
    """The parsed monitor.v1 document. Raises _Fail(state, cause); never anything that holds the token."""
    base = validate_url(cfg.get("url"))
    if not base:
        raise _Fail("error", "url")
    req = urllib.request.Request(base + PATH, method="GET", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "niu-heimar-dashboard"})
    timeout = cfg.get("timeout_secs", 4)
    deadline = time.monotonic() + 2 * timeout
    try:
        with _opener().open(req, timeout=timeout) as r:
            if time.monotonic() > deadline:
                raise _Fail("unreachable")
            if r.status != 200:
                raise _Fail("error", "server")
            if r.headers.get_content_type() != "application/json":
                raise _Fail("incompatible")
            body = b""
            while len(body) <= MAX_BODY:
                chunk = r.read(min(16384, MAX_BODY + 1 - len(body)))
                if not chunk:
                    break
                body += chunk
                if time.monotonic() > deadline:
                    raise _Fail("unreachable")
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
        if code in (401, 403):
            raise _Fail("unauthorized") from None
        if code == 404 or 300 <= code < 400:
            raise _Fail("incompatible") from None
        raise _Fail("error", "server") from None
    except (urllib.error.URLError, OSError):  # refused, timed out, reset, unresolvable
        raise _Fail("unreachable") from None
    except http.client.HTTPException:
        raise _Fail("error", "badjson") from None
    if len(body) > MAX_BODY:
        raise _Fail("error", "toolarge")
    try:
        doc = json.loads(body)
    except (ValueError, RecursionError):
        raise _Fail("error", "badjson") from None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise _Fail("incompatible")
    return doc


# ---------- allowlist reshaping (pure) ----------

def _enum(v, allowed):
    return v if isinstance(v, str) and v in allowed else "unknown"


def _nenum(v, allowed):
    return None if v is None else _enum(v, allowed)


def _bool(v):
    return v if isinstance(v, bool) else None


def _number(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _pct(v):
    n = _number(v)
    return None if n is None else round(max(0.0, min(100.0, n)), 1)


def _cnt(v):
    n = _number(v)
    return None if n is None or n != int(n) else int(max(0, min(n, 10 ** 12)))


def _usd(v):
    n = _number(v)
    return None if n is None else round(max(0.0, min(n, 1e9)), 4)


def _re(v, pat):
    return v if isinstance(v, str) and len(v) <= 128 and pat.fullmatch(v) else None


def _ts(v):
    if not isinstance(v, str) or len(v) > 32 or not TS_RE.fullmatch(v):
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _list(v, n):
    return [x for x in v[:n] if isinstance(x, dict)] if isinstance(v, list) else []


def _dict(v):
    return v if isinstance(v, dict) else {}


def _window(w):
    w = _dict(w)
    return {"used_pct": _pct(w.get("used_pct")), "resets_at": _ts(w.get("resets_at"))} if w else None


def _preview(p):
    if not isinstance(p, dict):
        return None
    f = _dict(p.get("floors"))
    return {"checked_at": _ts(p.get("checked_at")), "decision": _enum(p.get("decision"), DECISIONS),
            "reason": _enum(p.get("reason"), REASONS), "allow": _bool(p.get("allow")), "conservative": _bool(p.get("conservative")),
            "source": _enum(p.get("source"), SOURCES), "five_hour": _window(p.get("five_hour")) if p.get("five_hour") else None,
            "seven_day": _window(p.get("seven_day")) if p.get("seven_day") else None, "expected_run_pct": _pct(p.get("expected_run_pct")),
            "floors": {"five_hour_min_remaining_pct": _pct(f.get("five_hour_min_remaining_pct")),
                       "weekly_min_remaining_pct": _pct(f.get("weekly_min_remaining_pct")),
                       "abort_remaining_pct": _pct(f.get("abort_remaining_pct"))}}


def _pending(p):
    if not isinstance(p, dict):
        return None
    return {"run_id": _re(p.get("run_id"), UUID_RE), "slot_key": _re(p.get("slot_key"), SLOT_RE), "attempts": _cnt(p.get("attempts")),
            "max_attempts": _cnt(p.get("max_attempts")), "next_check_at": _ts(p.get("next_check_at")),
            "grace_until": _ts(p.get("grace_until")), "last_reason": _nenum(p.get("last_reason"), REASONS)}


def _gate_row(g):
    return {"taken_at": _ts(g.get("taken_at")), "source": _enum(g.get("source"), SOURCES), "decision": _enum(g.get("decision"), DECISIONS),
            "reason": _nenum(g.get("reason"), REASONS), "five_hour_used": _pct(g.get("five_hour_used")),
            "seven_day_used": _pct(g.get("seven_day_used")), "run_id": _re(g.get("run_id"), UUID_RE)}


def _agent(a):
    fb = _dict(a.get("filtered_by"))
    return {"agent_type": _enum(a.get("agent_type"), AGENT_TYPES), "status": _enum(a.get("status"), AGENT_STATUS),
            "attempts": _cnt(a.get("attempts")), "research_model": _re(a.get("research_model"), MODEL_RE),
            "structure_model": _re(a.get("structure_model"), MODEL_RE), "started_at": _ts(a.get("started_at")),
            "finished_at": _ts(a.get("finished_at")), "duration_s": _cnt(a.get("duration_s")),
            "num_turns": _cnt(a.get("num_turns")), "max_turns": _cnt(a.get("max_turns")),
            "web_searches": _cnt(a.get("web_searches")), "max_web_searches": _cnt(a.get("max_web_searches")),
            "web_fetches": _cnt(a.get("web_fetches")), "tokens_in": _cnt(a.get("tokens_in")), "tokens_out": _cnt(a.get("tokens_out")),
            "cache_read": _cnt(a.get("cache_read")), "equiv_usd": _usd(a.get("equiv_usd")),
            "items_kept": _cnt(a.get("items_kept")), "items_filtered": _cnt(a.get("items_filtered")),
            "filtered_by": {"duplicate": _cnt(fb.get("duplicate")), "excluded": _cnt(fb.get("excluded")),
                            "no_update": _cnt(fb.get("no_update"))} if fb else None,
            "uncited_dropped": _cnt(a.get("uncited_dropped")), "permission_denials": _cnt(a.get("permission_denials")),
            "error_code": _nenum(a.get("error_code"), ERRORS)}


def _compact_agent(a):
    return {"agent_type": _enum(a.get("agent_type"), AGENT_TYPES), "status": _enum(a.get("status"), AGENT_STATUS),
            "started_at": _ts(a.get("started_at")), "finished_at": _ts(a.get("finished_at")),
            "error_code": _nenum(a.get("error_code"), ERRORS)}


def _digest(d):
    if not isinstance(d, dict):
        return None
    http_status = _cnt(d.get("last_http_status"))
    return {"status": _enum(d.get("status"), DIGEST), "sections_total": _cnt(d.get("sections_total")),
            "sections_sent": _cnt(d.get("sections_sent")), "next_attempt_at": _ts(d.get("next_attempt_at")),
            "last_http_status": http_status if http_status is not None and 100 <= http_status <= 599 else None}


def _run_head(r):
    return {"id": _re(r.get("id"), UUID_RE), "slot_key": _re(r.get("slot_key"), SLOT_RE), "trigger": _enum(r.get("trigger"), TRIGGERS),
            "mode": _enum(r.get("mode"), MODES), "status": _enum(r.get("status"), RUN_STATUS),
            "started_at": _ts(r.get("started_at")), "finished_at": _ts(r.get("finished_at"))}


def _run(r):
    if not isinstance(r, dict):
        return None
    return {**_run_head(r), "scheduled_for": _ts(r.get("scheduled_for")), "duration_s": _cnt(r.get("duration_s")),
            "gate_attempts": _cnt(r.get("gate_attempts")), "gate_reason": _nenum(r.get("gate_reason"), REASONS),
            "gate_source": _nenum(r.get("gate_source"), SOURCES), "five_hour_used_before": _pct(r.get("five_hour_used_before")),
            "five_hour_used_after": _pct(r.get("five_hour_used_after")), "window_share_pct": _pct(r.get("window_share_pct")),
            "error_code": _nenum(r.get("error_code"), ERRORS), "agents": [_agent(a) for a in _list(r.get("agents"), 3)],
            "digest": _digest(r.get("digest"))}


def _compact_run(r):
    return {**_run_head(r), "agents": [_compact_agent(a) for a in _list(r.get("agents"), 3)]}


def _ledger_day(d):
    return {"day": _re(d.get("day"), DAY_RE), "runs": _cnt(d.get("runs")), "sessions": _cnt(d.get("sessions")),
            "probes": _cnt(d.get("probes")), "tokens_in": _cnt(d.get("tokens_in")), "tokens_out": _cnt(d.get("tokens_out")),
            "web_searches": _cnt(d.get("web_searches")), "equiv_usd": _usd(d.get("equiv_usd"))}


def shape(raw, now=None):
    """Rebuild a monitor.v1 document from an allowlist. Times become epoch seconds; anything off-spec becomes None or "unknown"."""
    raw = _dict(raw)
    svc, sch, gate, led = (_dict(raw.get(k)) for k in ("service", "schedule", "gate", "ledger"))
    feat, health = _dict(svc.get("features")), _dict(svc.get("health"))
    agents = [a for a in AGENT_TYPES if isinstance(feat.get("agents"), list) and a in feat["agents"]]
    return {
        "schema": SCHEMA,
        "generated_at": _ts(raw.get("generated_at")) or now,
        "service": {"version": _re(svc.get("version"), VERSION_RE),
                    "features": {"runs": feat.get("runs") is True, "digest": feat.get("digest") is True, "agents": agents},
                    "health": {k: _bool(health.get(k)) for k in ("db", "migrations", "scheduler", "claude_auth")}},
        "schedule": {"enabled": _bool(sch.get("enabled")), "running": _bool(sch.get("running")), "cron": _re(sch.get("cron"), CRON_RE),
                     "timezone": _re(sch.get("timezone"), TZ_RE), "next_slot_key": _re(sch.get("next_slot_key"), SLOT_RE),
                     "next_slot_at": _ts(sch.get("next_slot_at")), "manual_runs_today": _cnt(sch.get("manual_runs_today")),
                     "manual_runs_per_day": _cnt(sch.get("manual_runs_per_day"))},
        "gate": {"preview": _preview(gate.get("preview")), "pending": _pending(gate.get("pending")),
                 "recent": [_gate_row(g) for g in _list(gate.get("recent"), 12)]},
        "current_run": _run(raw.get("current_run")),
        "last_run": _run(raw.get("last_run")),
        "recent_runs": [_compact_run(r) for r in _list(raw.get("recent_runs"), 6)],
        "ledger": {"today": _ledger_day(_dict(led.get("today"))) if isinstance(led.get("today"), dict) else None,
                   "last_7_days": [_ledger_day(d) for d in _list(led.get("last_7_days"), 7)]},
    }


# ---------- cached poller ----------

def _result(state, message, cfg, now, cause=None):
    """The /api/research envelope. The last good data is kept (greyed out as stale) while the state is not ok."""
    with LOCK:
        data, ok_at, busy = RESEARCH["ok_data"], RESEARCH["ok_at"], RESEARCH["busy"]
        cache = RESEARCH["cache"]
    poll = (cfg or DEFAULTS)["poll_secs"]
    if state in ("disabled", "not_configured"):
        data = None
    stale = bool(data) and (state != "ok" or now - ok_at > 3 * poll)
    return {"state": state, "cause": cause, "message": message, "data": data, "fetched_at": cache["at"] if cache else None,
            "ok_at": ok_at or None, "stale": stale, "refreshing": busy, "server_now": now}


def _message(state, cfg, cause=None):
    if state == "error":
        return ERROR_MESSAGES.get(cause, ERROR_MESSAGES["internal"])
    addr = (cfg.get("url") or "").replace("http://", "")
    return MESSAGES[state].format(addr=addr if validate_url(cfg.get("url")) else "the configured address")


def _refresh():
    """One upstream call (runs in a daemon thread); stores the outcome. Never raises."""
    state, cause, data = "error", "internal", None
    try:
        cfg = load_config()
        if not validate_url(cfg["url"]):
            raise _Fail("error", "url")
        data = shape(fetch(cfg, read_token()), _clock())
        state, cause = "ok", None
    except _Fail as f:
        state, cause = f.state, f.cause
    except TokenProblem as t:
        state, cause = ("not_configured", None) if t.cause == "missing" else ("error", t.cause)
    except Exception:  # noqa: BLE001 - never let the poller die, and never describe the failure (it could hold the token)
        state, cause = "error", "internal"
    now = _clock()
    with LOCK:
        RESEARCH["cache"] = {"state": state, "cause": cause, "at": now}
        if state == "ok":
            RESEARCH["ok_data"], RESEARCH["ok_at"] = data, now
        RESEARCH["busy"] = False


def summary(force=False):
    """The /api/research answer. Cheap: local checks happen here, the network call happens in a background thread
    (at most one per poll_secs, only while someone asks; a manual refresh at most once per FORCE_GAP_SECS)."""
    now = _clock()
    try:
        cfg = load_config()
        if not cfg["enabled"]:
            return _result("disabled", MESSAGES["disabled"], cfg, now)
        if not validate_url(cfg["url"]):
            return _result("error", ERROR_MESSAGES["url"], cfg, now, "url")
        try:
            read_token()
        except TokenProblem as t:
            if t.cause == "missing":
                return _result("not_configured", MESSAGES["not_configured"], cfg, now)
            return _result("error", ERROR_MESSAGES[t.cause], cfg, now, t.cause)
        with LOCK:
            cache = RESEARCH["cache"]
            due = cache is None or now - cache["at"] >= cfg["poll_secs"]
            if force and now - RESEARCH["last_force"] >= FORCE_GAP_SECS:
                due, RESEARCH["last_force"] = True, now
            start = due and not RESEARCH["busy"]
            if start:
                RESEARCH["busy"] = True
        if start:
            threading.Thread(target=_refresh, daemon=True).start()
        with LOCK:
            cache = RESEARCH["cache"]
        if cache is None:
            return _result("loading", MESSAGES["loading"], cfg, now)
        return _result(cache["state"], _message(cache["state"], cfg, cache["cause"]), cfg, now, cache["cause"])
    except Exception:  # noqa: BLE001
        return {"state": "error", "cause": "internal", "message": ERROR_MESSAGES["internal"], "data": None, "fetched_at": None,
                "ok_at": None, "stale": False, "refreshing": False, "server_now": now}
