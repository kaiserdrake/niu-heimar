"""Topics panel backend: lists and edits the research topics held by valkyrie-dispatch.

Design: docs/design/2026-11-07-dashboard-topics-panel.md. Rules this module keeps:
- The token is a `topics`-scope token in its own 0600 file. Before it is used, GET /api/v1/auth/whoami must say the scope is
  exactly `topics`; admin, read, monitor or anything else means nothing is sent with it.
- Outbound requests go to the configured loopback URL only (same transport as niu_research: no proxy, no redirect, a timeout, a
  size cap). Paths are constants plus a canonical lowercase UUID; queries are built from enums and ints.
- Topic text is untrusted. Everything returned is rebuilt from an allowlist with display caps (`shape_topic`); upstream error
  text is never passed on; messages are fixed strings. Audit rows hold topic IDs and field names only, never topic text.
- Nothing here starts a research run, deletes a topic, or touches feedback, usage or monitor routes.
"""
import collections
import hashlib
import http.client
import json
import math
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import niu_research as nr
from niu_events import connect, db_path

_Fail = nr._Fail
TokenProblem = nr.TokenProblem

BASE = "/api/v1/topics"
WHOAMI = nr.WHOAMI_PATH
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TS_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z")
AGENT_TYPES = ("general", "hobby", "fintrend")
METHODS = ("GET", "POST", "PATCH")
FORBIDDEN = frozenset("<>`{}  ")

MAX_LIST_BODY = 1024 * 1024
MAX_SMALL_BODY = 64 * 1024
MAX_REQUEST = 16 * 1024          # valkyrie-dispatch's own request cap
SCOPE_TTL = 300                  # whoami result is cached per token for this long
LIST_TTL = 10
REFRESH_GAP = 5
PER_AGENT_LIMIT = 200            # valkyrie-dispatch allows at most this many active topics per agent type

# Limits the API enforces (authoritative there; mirrored here and in the front end) and what the read path will display.
LIMITS = {"name": 120, "term": 80, "keywords": 20, "excluded_terms": 50, "weight_min": 0.1, "weight_max": 5.0,
          "show_name": 200, "show_term": 100, "show_list": 50, "active_per_agent": PER_AGENT_LIMIT}
CREATE_KEYS = ("agent_type", "name", "keywords", "excluded_terms", "weight")
PATCH_KEYS = ("name", "keywords", "excluded_terms", "weight", "is_active")
FIELD_ALLOW = frozenset(CREATE_KEYS + PATCH_KEYS)
FIELD_CODES = frozenset("required too_long too_short too_many too_few forbidden_char bad_enum out_of_range not_bool not_list "
                        "empty unknown toolarge".split())

STATES = ("ok", "not_paired", "not_configured", "disabled", "wrong_scope", "too_powerful", "unreachable", "unauthorized",
          "incompatible", "error")
MESSAGES = {
    "ok": "",
    "not_paired": "Pair this browser to view and edit topics (menu, then Pair). Topics are personal, so they are not shown to unpaired viewers.",
    "not_configured": "No topics token yet. Create one in valkyrie-dispatch with `rd gen-token --name niu-dash-topics --scope topics`, "
                      "add it to API_TOKENS, restart, then run `niu-dash research-token --scope topics` in a terminal on this machine.",
    "disabled": "The Research tab is turned off (research.json: enabled is false).",
    "wrong_scope": "This token has scope {scope}. The dashboard only accepts a topics-scope token. "
                   "Remove it with `niu-dash research-token --scope topics --remove`.",
    "too_powerful": "This token has scope {scope}. The dashboard only accepts a topics-scope token. "
                    "Remove it with `niu-dash research-token --scope topics --remove`.",
    "unreachable": "valkyrie-dispatch is not reachable on {addr}. Is the container running?",
    "unauthorized": "Token rejected: check API_TOKENS and re-run `niu-dash research-token --scope topics`.",
    "incompatible": "valkyrie-dispatch is too old for the Topics panel (needs /auth/whoami).",
}
ERROR_MESSAGES = {
    "url": "research URL must be http://127.0.0.1:PORT",
    "perms": "The token file must be a regular file you own with mode 600 (and not a symlink).",
    "format": "The token file does not hold a valkyrie-dispatch token. Re-run `niu-dash research-token --scope topics`.",
    "server": "valkyrie-dispatch returned an error.",
    "toolarge": "valkyrie-dispatch sent a response that is too large.",
    "badjson": "valkyrie-dispatch sent a response that could not be read.",
    "internal": "Could not read the topics.",
}
# upstream/dashboard code -> HTTP status the dashboard answers a write with
HTTP_FOR = {"invalid": 400, "toolarge": 400, "duplicate": 409, "cap": 409, "changed": 412, "not_found": 404, "rate_limited": 429}

LOCK = threading.Lock()
LIST_LOCK = threading.Lock()
_clock = time.time            # tests replace this
SCOPES = {}                   # sha256(token) -> {"scope", "at"}
CACHE = {"result": None, "at": 0.0, "last_force": 0.0, "good": None}


# ---------- token ----------

def token_path():
    p = os.environ.get("NIU_TOPICS_TOKEN_FILE")
    return Path(p) if p else db_path().parent / "research-dispatch.topics.token"


def read_token():
    return nr._read_token_at(token_path())


def write_token(token):
    nr._write_token_at(token_path(), token)


def remove_token():
    try:
        token_path().unlink()
        return True
    except FileNotFoundError:
        return False


# ---------- transport ----------

def _query(q):
    out = []
    for k, v in (q or {}).items():
        if k == "agent_type" and v in AGENT_TYPES:
            pass
        elif k == "is_active" and v in ("true", "false"):
            pass
        elif k in ("limit", "offset") and isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100_000:
            pass
        else:
            raise _Fail("error", "internal")
        out.append((k, v))
    return urlencode(out)


def _path_ok(path):
    return path in (WHOAMI, BASE) or (isinstance(path, str) and path.startswith(BASE + "/") and UUID_RE.fullmatch(path[len(BASE) + 1:]) is not None)


def _call(cfg, token, method, path, body=None, query=None, if_match=None, max_body=MAX_SMALL_BODY):
    """One request to valkyrie-dispatch. Returns (status, parsed JSON or None). HTTP error statuses are returned, not raised;
    raises _Fail(state, cause) for transport problems, redirects and anything oversized. Never anything that holds the token."""
    base = nr.validate_url(cfg.get("url"))
    if not base:
        raise _Fail("error", "url")
    if method not in METHODS or not _path_ok(path):
        raise _Fail("error", "internal")
    url = base + path + (("?" + _query(query)) if query else "")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "niu-heimar-dashboard"}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        if len(data) > MAX_REQUEST:
            raise _Fail("error", "toolarge")
        headers["Content-Type"] = "application/json"
    if if_match is not None:
        if not (isinstance(if_match, str) and TS_RE.fullmatch(if_match)):
            raise _Fail("error", "internal")
        headers["If-Match"] = f'"{if_match}"'
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    timeout = cfg.get("timeout_secs", 4)
    deadline = time.monotonic() + 2 * timeout
    status, raw, ctype = 0, b"", ""

    def read(r):
        buf = b""
        while len(buf) <= max_body:
            chunk = r.read(min(16384, max_body + 1 - len(buf)))
            if not chunk:
                break
            buf += chunk
            if time.monotonic() > deadline:
                raise _Fail("unreachable")
        return buf
    try:
        with nr._opener().open(req, timeout=timeout) as r:
            status, ctype = r.status, r.headers.get_content_type()
            raw = read(r)
    except urllib.error.HTTPError as e:
        status, ctype = e.code, e.headers.get_content_type() if e.headers else ""
        try:
            raw = read(e)
        finally:
            e.close()
        if 300 <= status < 400:
            raise _Fail("incompatible") from None
    except (urllib.error.URLError, OSError):  # refused, timed out, reset, unresolvable
        raise _Fail("unreachable") from None
    except http.client.HTTPException:
        raise _Fail("error", "badjson") from None
    if len(raw) > max_body:
        raise _Fail("error", "toolarge")
    if not raw:
        return status, None
    if ctype != "application/json":
        if 200 <= status < 300:
            raise _Fail("incompatible")
        return status, None
    try:
        return status, json.loads(raw)
    except (ValueError, RecursionError):
        if 200 <= status < 300:
            raise _Fail("error", "badjson") from None
        return status, None


def _status_fail(status):
    """The _Fail for a non-2xx answer to a read."""
    if status == 401:
        return _Fail("unauthorized")
    if status == 403:
        return _Fail("wrong_scope", "other")
    if status == 404 or 300 <= status < 400:
        return _Fail("incompatible")
    return _Fail("error", "server")


def check_scope(cfg, token):
    """Raises _Fail unless whoami says the token's scope is exactly `topics` (cached per token for SCOPE_TTL)."""
    key = hashlib.sha256(token.encode()).hexdigest()
    now = _clock()
    with LOCK:
        hit = SCOPES.get(key)
    if hit and now - hit["at"] < SCOPE_TTL:
        scope = hit["scope"]
    else:
        status, doc = _call(cfg, token, "GET", WHOAMI)
        if status != 200:
            raise _status_fail(status)
        scope = doc.get("scope") if isinstance(doc, dict) else None
        if not isinstance(scope, str) or len(scope) > 32:
            raise _Fail("incompatible")
        with LOCK:
            if len(SCOPES) > 8:
                SCOPES.clear()
            SCOPES[key] = {"scope": scope, "at": now}
    if scope == "topics":
        return
    shown = scope if scope in ("monitor", "read", "admin") else "other"
    raise _Fail("too_powerful" if scope in ("admin", "read") else "wrong_scope", shown)


# ---------- validation (pure) ----------

def _bad_char(ch):
    return ch in FORBIDDEN or unicodedata.category(ch)[0] == "C"


def _text(v, cap):
    """(clean string, None) or (None, code). Strip, collapse spaces, 1..cap code points, no forbidden characters."""
    if not isinstance(v, str):
        return None, "required"
    if any(_bad_char(c) for c in v):
        return None, "forbidden_char"
    s = " ".join(v.split())
    if not s:
        return None, "too_short"
    if len(s) > cap:
        return None, "too_long"
    return s, None


def _terms(v, lo, hi):
    if not isinstance(v, list):
        return None, "not_list"
    out, seen = [], set()
    for t in v:
        if isinstance(t, str) and not t.strip():
            continue
        s, err = _text(t, LIMITS["term"])
        if err == "too_short":      # only exotic whitespace: same as a blank line
            continue
        if err:
            return None, {"forbidden_char": "forbidden_char", "too_long": "too_long"}.get(err, "not_list")
        if s.casefold() not in seen:
            seen.add(s.casefold())
            out.append(s)
    if len(out) < lo:
        return None, "too_few"
    if len(out) > hi:
        return None, "too_many"
    return out, None


def _finite(v):
    """True for a real finite number; a huge int (beyond float range) counts as not finite instead of raising."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:
        return False


def _weight(v):
    if not _finite(v):
        return None, "out_of_range"
    w = round(float(v), 2)
    if not LIMITS["weight_min"] <= w <= LIMITS["weight_max"]:
        return None, "out_of_range"
    return w, None


def _check_fields(d, keys, required):
    """Allowlist validation shared by create and patch. Returns (clean, errors)."""
    clean, errs = {}, {}
    for k in d:
        if k not in keys:
            errs[k if isinstance(k, str) and len(k) <= 40 and k.isidentifier() else "?"] = "unknown"
    for k in keys:
        if k not in d:
            if k in required:
                errs[k] = "required"
            continue
        v = d[k]
        if k == "agent_type":
            if isinstance(v, str) and v in AGENT_TYPES:
                clean[k] = v
            else:
                errs[k] = "bad_enum"
        elif k == "name":
            clean[k], err = _text(v, LIMITS["name"])
        elif k == "keywords":
            clean[k], err = _terms(v, 1, LIMITS["keywords"])
        elif k == "excluded_terms":
            clean[k], err = _terms(v, 0, LIMITS["excluded_terms"])
        elif k == "weight":
            clean[k], err = _weight(v)
        elif k == "is_active":
            if isinstance(v, bool):
                clean[k] = v
            else:
                errs[k] = "not_bool"
        if k in ("name", "keywords", "excluded_terms", "weight") and err:
            errs[k] = err
    return clean, errs


def _sized(clean):
    return len(json.dumps(clean, ensure_ascii=False).encode()) <= MAX_REQUEST


def validate_create(d):
    """(clean_body, None) or (None, {field: code}). Unknown keys are refused."""
    if not isinstance(d, dict):
        return None, {"body": "empty"}
    clean, errs = _check_fields(d, CREATE_KEYS, ("agent_type", "name", "keywords"))
    if errs:
        return None, errs
    clean.setdefault("excluded_terms", [])
    clean.setdefault("weight", 1.0)
    if not _sized(clean):
        return None, {"body": "toolarge"}
    return clean, None


def validate_patch(d):
    """(clean_body, None) or (None, {field: code}). Only the editable fields; agent_type is immutable."""
    if not isinstance(d, dict) or not d:
        return None, {"fields": "empty"}
    clean, errs = _check_fields(d, PATCH_KEYS, ())
    if errs:
        return None, errs
    if not _sized(clean):
        return None, {"body": "toolarge"}
    return clean, None


# ---------- reshaping the read path (pure) ----------

def _show(s, cap):
    """(display string, exceeds the API?) for a stored string: controls become U+FFFD, long text is cut with an ellipsis."""
    over = False
    out = []
    for ch in s:
        if unicodedata.category(ch)[0] == "C" or ch in "  ":
            out.append("�")
            over = True
        else:
            out.append(ch)
            if ch in FORBIDDEN:
                over = True
    s = "".join(out)
    if len(s) > cap:
        s, over = s[:cap - 1] + "…", True
    return s, over


def _shown_terms(v, api_cap, api_max):
    if not isinstance(v, list):
        return [], False
    over = len(v) > api_max
    out = []
    for t in v[:LIMITS["show_list"]]:
        if not isinstance(t, str):
            continue
        s, o = _show(t, LIMITS["show_term"])
        out.append(s)
        over = over or o or len(s) > api_cap
    return out, over or len(v) > LIMITS["show_list"]


def shape_topic(raw):
    """A topic row rebuilt from an allowlist, or None when its id / agent type / flags are off-spec."""
    if not isinstance(raw, dict):
        return None
    tid, agent = raw.get("id"), raw.get("agent_type")
    if not (isinstance(tid, str) and UUID_RE.fullmatch(tid)) or agent not in AGENT_TYPES or not isinstance(raw.get("name"), str):
        return None
    active, w = raw.get("is_active"), raw.get("weight")
    if not isinstance(active, bool) or not _finite(w):
        return None
    name, over = _show(raw["name"], LIMITS["show_name"])
    over = over or len(name) > LIMITS["name"]
    kw, o1 = _shown_terms(raw.get("keywords"), LIMITS["term"], LIMITS["keywords"])
    ex, o2 = _shown_terms(raw.get("excluded_terms"), LIMITS["term"], LIMITS["excluded_terms"])
    upd = raw.get("updated_at")
    return {"id": tid, "agent_type": agent, "name": name, "keywords": kw, "excluded_terms": ex,
            "weight": round(max(0.0, min(5.0, float(w))), 2), "is_active": active,
            "updated_at": upd if isinstance(upd, str) and len(upd) <= 40 and TS_RE.fullmatch(upd) else None,
            "over_api_limits": bool(over or o1 or o2 or not LIMITS["weight_min"] <= w <= LIMITS["weight_max"])}


def _shape_all(rows):
    if not isinstance(rows, list):
        raise _Fail("error", "badjson")
    out = [t for t in (shape_topic(r) for r in rows) if t]
    return out, len(rows) - len(out)


# ---------- upstream errors -> fixed dashboard codes ----------

def map_upstream_error(status, body):
    """{"code": ..., extras} from an upstream error. Upstream message / msg text is never passed on."""
    b = body if isinstance(body, dict) else {}
    err = b.get("error") if isinstance(b.get("error"), str) else ""
    if status == 409 and err == "duplicate_topic":
        out = {"code": "duplicate", "existing_active": b.get("is_active") is True}
        if isinstance(b.get("existing_id"), str) and UUID_RE.fullmatch(b["existing_id"]):
            out["existing_id"] = b["existing_id"]
        return out
    if status == 409 and err == "topic_cap_reached":
        return {"code": "cap"}
    if status == 412:
        return {"code": "changed"}
    if status == 404:
        return {"code": "not_found"}
    if status == 413:
        return {"code": "toolarge"}
    if status == 429:
        return {"code": "rate_limited"}
    if status in (400, 422):
        fields = {}
        for d in b.get("details") if isinstance(b.get("details"), list) else []:
            loc = d.get("loc") if isinstance(d, dict) else None
            f = loc[-1] if isinstance(loc, list) and loc else None
            if isinstance(f, str) and f in FIELD_ALLOW:
                fields[f] = "invalid"
        return {"code": "invalid", "fields": fields}
    if status == 401:
        return {"code": "unauthorized"}
    if status == 403:
        return {"code": "wrong_scope"}
    return {"code": "error", "cause": "server"}


def _fail_payload(f):
    if f.state == "error":
        return {"code": "error", "cause": f.cause if f.cause in ERROR_MESSAGES else "internal"}
    if f.state in STATES:
        return {"code": f.state, **({"scope": f.cause} if f.cause in ("monitor", "read", "admin", "other") else {})}
    return {"code": "error", "cause": "internal"}


# ---------- audit and rate limit ----------

def audit(action, topic_id, fields, device, ok, code=None):
    """One `events` row (Events tab). Holds the action, a topic id prefix, field names and the device name: no topic text."""
    name = "".join(c for c in str((device or {}).get("name") or "browser") if c.isprintable())[:40]
    fl = ",".join(f for f in sorted(fields) if re.fullmatch(r"[a-z_]{1,20}", f))
    tid = topic_id if isinstance(topic_id, str) and UUID_RE.fullmatch(topic_id) else "00000000"
    summary = f"{action} {tid[:8]} [{fl}] by {name}{' (' + code + ')' if code else ''}"[:120]
    try:
        con = connect()
        with con:
            con.execute("INSERT INTO events(ts,event,session_id,domain,project,tool,summary) VALUES(?,?,?,?,?,?,?)",
                        (time.time(), "TopicChange" if ok else "TopicChangeRefused", "web", "PERSONAL", "valkyrie-dispatch", "topics", summary))
        con.close()
    except Exception:  # noqa: BLE001 - an audit failure must not break the user's change
        pass


class RateLimiter:
    """Sliding windows per key; hit() returns False (and records nothing) when any window is full."""
    def __init__(self, windows=((60, 10), (86400, 100)), clock=time.time):
        self.windows, self.clock, self.hits, self.lock = windows, clock, collections.defaultdict(collections.deque), threading.Lock()

    def hit(self, key):
        now = self.clock()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.windows[-1][0]:
                q.popleft()
            for secs, limit in self.windows:
                if sum(1 for t in q if now - t <= secs) >= limit:
                    return False
            q.append(now)
            return True


WRITES = RateLimiter(clock=lambda: _clock())


# ---------- list ----------

def _setup():
    """(cfg, token) or raises _Fail / TokenProblem."""
    cfg = nr.load_config()
    if not cfg["enabled"]:
        raise _Fail("disabled")
    if not nr.validate_url(cfg["url"]):
        raise _Fail("error", "url")
    return cfg, read_token()


def _envelope(state, cause=None, scope=None, cfg=None):
    if state == "error":
        msg = ERROR_MESSAGES.get(cause, ERROR_MESSAGES["internal"])
    else:
        addr = ((cfg or {}).get("url") or "").replace("http://", "")
        msg = MESSAGES.get(state, "").format(addr=addr if nr.validate_url((cfg or {}).get("url")) else "the configured address",
                                             scope=scope or "other")
    good = CACHE["good"] if state in ("unreachable", "unauthorized", "incompatible", "error") else None
    return {"state": state, "cause": cause, "message": msg, "topics": good["topics"] if good else [],
            "archived_truncated": good["archived_truncated"] if good else False, "dropped": good["dropped"] if good else 0,
            "loaded_at": good["loaded_at"] if good else None, "stale": bool(good), "limits": LIMITS, "server_now": _clock()}


def list_topics(force=False):
    """The /api/topics answer (callers must have checked pairing). Cached for LIST_TTL; one upstream burst at a time."""
    with LIST_LOCK:
        now = _clock()
        if not force and CACHE["result"] and now - CACHE["at"] < LIST_TTL:
            return CACHE["result"]
        cfg = None
        try:
            cfg, token = _setup()
            check_scope(cfg, token)
            rows, dropped = [], 0
            for agent in AGENT_TYPES:
                status, doc = _call(cfg, token, "GET", BASE, query={"agent_type": agent, "is_active": "true", "limit": PER_AGENT_LIMIT},
                                    max_body=MAX_LIST_BODY)
                if status != 200:
                    raise _status_fail(status)
                part, d = _shape_all(doc)
                rows += part
                dropped += d
            status, doc = _call(cfg, token, "GET", BASE, query={"is_active": "false", "limit": PER_AGENT_LIMIT}, max_body=MAX_LIST_BODY)
            if status != 200:
                raise _status_fail(status)
            part, d = _shape_all(doc)
            rows += part
            dropped += d
            good = {"topics": rows, "archived_truncated": isinstance(doc, list) and len(doc) >= PER_AGENT_LIMIT, "dropped": dropped,
                    "loaded_at": now}
            res = {"state": "ok", "cause": None, "message": "", **good, "stale": False, "limits": LIMITS, "server_now": now}
            CACHE.update(result=res, at=now, good=good)
            return res
        except _Fail as f:
            state, cause = f.state, f.cause
        except TokenProblem as t:
            state, cause = ("not_configured", None) if t.cause == "missing" else ("error", t.cause)
        except Exception:  # noqa: BLE001 - never describe the failure (it could hold the token)
            state, cause = "error", "internal"
        CACHE["result"] = None
        return _envelope(state, cause, cause if state in ("wrong_scope", "too_powerful") else None, cfg)


def refresh():
    """A manual refresh: drops the cache, at most once per REFRESH_GAP seconds."""
    now = _clock()
    force = now - CACHE["last_force"] >= REFRESH_GAP
    if force:
        CACHE["last_force"] = now
    return list_topics(force=force)


def invalidate():
    CACHE.update(result=None, at=0.0)


# ---------- writes ----------

def _write_action(kind, body):
    if kind == "create":
        return "create"
    if set(body) == {"is_active"}:
        return "activate" if body["is_active"] else "deactivate"
    return "update"


def write(kind, device, b):
    """Create or update one topic for a paired device. Returns (http status, JSON payload). The caller has already checked the
    device cookie and the CSRF / origin headers."""
    def refuse(code, status=None, **extra):
        return status or HTTP_FOR.get(code, 502), {"ok": False, "code": code, **extra}
    if not isinstance(b, dict):
        return refuse("invalid", fields={"body": "empty"})
    dev = device or {}
    if not WRITES.hit(dev.get("id", "?")):
        audit("update" if kind == "update" else "create", b.get("id") if kind == "update" else None, [], device, False, "rate_limited")
        return refuse("rate_limited")
    tid = if_match = None
    if kind == "create":
        if set(b) - set(CREATE_KEYS):
            return refuse("invalid", fields={"body": "unknown"})
        body, errs = validate_create(b)
        method, path = "POST", BASE
    else:
        if set(b) - {"id", "if_match", "fields"}:
            return refuse("invalid", fields={"body": "unknown"})
        tid = b.get("id")
        if not (isinstance(tid, str) and UUID_RE.fullmatch(tid)):
            return refuse("invalid", fields={"id": "required"})
        im = b.get("if_match")
        if_match = im if isinstance(im, str) and TS_RE.fullmatch(im) else None   # passed back verbatim only if it is a timestamp
        body, errs = validate_patch(b.get("fields"))
        method, path = "PATCH", f"{BASE}/{tid}"
    if errs:
        if errs == {"body": "toolarge"}:
            return refuse("toolarge")
        return refuse("invalid", fields={k: v for k, v in errs.items() if v in FIELD_CODES})
    action = _write_action(kind, body)
    try:
        cfg, token = _setup()
        check_scope(cfg, token)
        status, doc = _call(cfg, token, method, path, body=body, if_match=if_match)
    except _Fail as f:
        p = _fail_payload(f)
        audit(action, tid, body, device, False, p["code"])
        return refuse(p.pop("code"), **p)
    except TokenProblem as t:
        code, extra = ("not_configured", {}) if t.cause == "missing" else ("error", {"cause": t.cause})
        audit(action, tid, body, device, False, code)
        return refuse(code, **extra)
    except Exception:  # noqa: BLE001
        audit(action, tid, body, device, False, "error")
        return refuse("error", cause="internal")
    topic = shape_topic(doc) if status in (200, 201) else None
    if status in (200, 201):
        invalidate()   # the upstream write happened even if its reply is unreadable, so drop the cached list
    if topic is None:
        p = map_upstream_error(status, doc) if status not in (200, 201) else {"code": "error", "cause": "format"}
        audit(action, tid, body, device, False, p["code"])
        return refuse(p.pop("code"), **p)
    invalidate()
    audit(action, topic["id"], body, device, True)
    return 200, {"ok": True, "topic": topic}
