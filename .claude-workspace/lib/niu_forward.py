"""niu-forward: push this machine's events, token usage and design proposals to a hub niu-dash (stdlib only).

Satellite side of the multi-machine feature. Hooks keep writing only the local SQLite DB (the spool); this module
tails it and POSTs batches to the hub's /api/ingest with a per-machine bearer token. Logs hold only counts and
error classes, never row content, the token, or the URL path.
"""
import hashlib
import json
import os
import random
import re
import sqlite3
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import niu_events as ne  # noqa: E402
import niu_ollama  # noqa: E402

SHARED = ne.SHARED
TEMPLATE_NAME = "forward.template.json"
EVENT_BATCH = 500
TOKEN_BATCH = 2000
TOKEN_DAYS = 35
SENT_TOK_DAYS = 40
BACKOFF_MIN, BACKOFF_MAX = 2.0, 300.0
BODY_LIMIT = {"events": 256 * 1024, "tokens": 256 * 1024, "snapshots": 256 * 1024, "designs": 600 * 1024}
SOFT = 0.9  # split a batch locally when its body exceeds this share of the hub's limit
MAX_LOOPS = 40  # batches per kind per pass
RE_LABEL = re.compile(r"[A-Za-z0-9_-]{1,32}")
RE_HOST = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+ts\.net")
DEFAULT_DOMAINS = ["(workspace)", "PERSONAL", "HOBBY"]
LOG_EVERY = 3600.0


class ConfigError(ValueError):
    """Config/URL problem. Messages never contain the token."""


class Retry(Exception):
    """Transient failure (network, 5xx, 429). args[0] is the error class (no content)."""


class Refused(Exception):
    """401/403 or an unusable token file: stop sending until re-paired."""


# ---------- paths and config ----------

def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "niu-heimar"


def db_path() -> Path:
    return Path(os.environ["NIU_FORWARD_DB"]) if os.environ.get("NIU_FORWARD_DB") else state_dir() / "forward.db"


def config_path() -> Path:
    return Path(os.environ["NIU_FORWARD_CONFIG"]) if os.environ.get("NIU_FORWARD_CONFIG") else SHARED / "forward.json"


def default_token_path() -> Path:
    return state_dir() / "forward-token"


def check_url(url) -> str:
    """Normalised base URL (scheme://host[:port]) or ConfigError. Only https://*.ts.net and loopback http are allowed."""
    if not isinstance(url, str) or not url or len(url) > 200 or any(c.isspace() or ord(c) < 32 for c in url):
        raise ConfigError("url: missing or invalid")
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        raise ConfigError("url: invalid") from None
    host = (u.hostname or "").lower()
    if u.username is not None or u.password is not None or u.query or u.fragment or u.path not in ("", "/"):
        raise ConfigError("url: must be scheme://host[:port] only")
    if u.scheme == "https":
        if not RE_HOST.fullmatch(host):
            raise ConfigError("url: https hosts must be <name>.ts.net")
    elif u.scheme == "http":
        if host not in ("127.0.0.1", "localhost") or port is None:
            raise ConfigError("url: http is only allowed for 127.0.0.1/localhost with a port")
    else:
        raise ConfigError("url: scheme must be https")
    if port is not None and not (0 < port < 65536):
        raise ConfigError("url: bad port")
    return f"{u.scheme}://{host}" + (f":{port}" if port is not None else "")


def load_config(path=None, require_url=True) -> dict:
    p = Path(path) if path else config_path()
    try:
        if p.stat().st_size > 16384:
            raise ConfigError("config: too large")
        raw = json.loads(p.read_text("utf-8"))
    except FileNotFoundError:
        raise ConfigError("config: missing (run install --satellite)") from None
    except (OSError, ValueError):
        raise ConfigError("config: unreadable or not JSON") from None
    if not isinstance(raw, dict):
        raise ConfigError("config: must be an object")
    cfg = {"url": None}
    if raw.get("url") or require_url:
        cfg["url"] = check_url(raw.get("url"))
    tf = os.environ.get("NIU_FORWARD_TOKEN") or raw.get("token_file") or str(default_token_path())
    if not isinstance(tf, str):
        raise ConfigError("token_file: must be a string")
    cfg["token_file"] = os.path.expanduser(tf)
    send = raw.get("send", {})
    if not isinstance(send, dict):
        raise ConfigError("send: must be an object")
    cfg["send"] = {k: bool(send.get(k, True)) for k in ("events", "tokens", "designs")}
    doms = raw.get("design_domains", DEFAULT_DOMAINS)
    if not isinstance(doms, list) or not all(isinstance(d, str) and d for d in doms):
        raise ConfigError("design_domains: must be a list of names")
    # user decision: WORK design docs are never forwarded, whatever the config says
    cfg["design_domains"] = [d for d in doms if d.strip().upper() != "WORK"]
    return cfg


def read_token(cfg) -> str:
    """The bearer token (0600, owner-only, no symlink). Raises Refused('token_<cause>')."""
    try:
        key = niu_ollama.read_api_key({"api_key_file": cfg["token_file"]})
    except niu_ollama.KeyProblem as e:
        raise Refused(f"token_{e}") from None
    if not key:
        raise Refused("token_missing")
    return key


def write_token(path, token) -> None:
    """Create the token file 0600 (O_NOFOLLOW; replaces an existing regular file atomically)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, token.encode("ascii") + b"\n")
    finally:
        os.close(fd)
    os.replace(tmp, p)


def set_config_url(url) -> None:
    """Write `url` into forward.json (created from the template when missing), keeping other keys; mode 0600."""
    p = config_path()
    try:
        cur = json.loads(p.read_text("utf-8"))
        if not isinstance(cur, dict):
            cur = {}
    except (OSError, ValueError):
        try:
            cur = json.loads((SHARED / TEMPLATE_NAME).read_text("utf-8"))
        except (OSError, ValueError):
            cur = {"send": {"events": True, "tokens": True, "designs": True}, "design_domains": DEFAULT_DOMAINS}
    cur["url"] = url
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2)
        f.write("\n")
    os.replace(tmp, p)


# ---------- state ----------

class State:
    """forward.db (0600): cursor(kind, value), sent_tok(mkey, ts), sent_design(rel, sha)."""

    def __init__(self, path=None):
        p = Path(path) if path else db_path()
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        new = not p.exists()
        self.con = sqlite3.connect(p, timeout=5)
        if new:
            os.chmod(p, 0o600)
        self.con.executescript(
            "CREATE TABLE IF NOT EXISTS cursor(kind TEXT PRIMARY KEY, value);"
            "CREATE TABLE IF NOT EXISTS sent_tok(mkey TEXT PRIMARY KEY, ts REAL);"
            "CREATE TABLE IF NOT EXISTS sent_design(rel TEXT PRIMARY KEY, sha TEXT);")
        self.con.commit()

    def get(self, kind, default=None):
        r = self.con.execute("SELECT value FROM cursor WHERE kind=?", (kind,)).fetchone()
        return default if r is None else r[0]

    def put(self, kind, value):
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO cursor(kind, value) VALUES(?,?)", (kind, value))

    def tok_sent(self, mkey) -> bool:
        return self.con.execute("SELECT 1 FROM sent_tok WHERE mkey=?", (mkey,)).fetchone() is not None

    def mark_tok(self, rows):
        with self.con:
            self.con.executemany("INSERT OR REPLACE INTO sent_tok(mkey, ts) VALUES(?,?)", [(r["mkey"], r["ts"]) for r in rows])

    def prune(self, now):
        with self.con:
            self.con.execute("DELETE FROM sent_tok WHERE ts < ?", (now - SENT_TOK_DAYS * 86400,))

    def designs(self) -> dict:
        return dict(self.con.execute("SELECT rel, sha FROM sent_design"))

    def set_design(self, rel, sha):
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO sent_design(rel, sha) VALUES(?,?)", (rel, sha))

    def del_design(self, rel):
        with self.con:
            self.con.execute("DELETE FROM sent_design WHERE rel=?", (rel,))

    def close(self):
        self.con.close()


# ---------- HTTP ----------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib re-sends custom headers (Authorization) on redirects, even cross-host: refuse them instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused (bearer token attached)", headers, fp)


# no proxies: the token must only ever go to the configured host
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def backoff_base(failures: int) -> float:
    """2 s, 4 s, 8 s ... capped at 300 s, for the n-th consecutive failure (n >= 1)."""
    return min(BACKOFF_MAX, BACKOFF_MIN * (2 ** max(0, failures - 1)))


def backoff_delay(failures: int, rnd=random.random) -> float:
    """backoff_base with jitter in [0.5, 1.0] of the base (never above the cap)."""
    return backoff_base(failures) * (0.5 + 0.5 * rnd())


def _log(msg):
    print(f"niu-forward: {msg}", file=sys.stderr, flush=True)


def ws_rel(project_dir):
    """Workspace-relative project path ('' = the workspace itself), or None when outside the workspace / empty."""
    if not isinstance(project_dir, str) or not project_dir or not os.path.isabs(project_dir):
        return None
    try:
        p = Path(project_dir).resolve()
        ws = Path(ne.WS).resolve()
        rel = p.relative_to(ws).as_posix()
    except (OSError, RuntimeError, ValueError):
        return None
    return "" if rel == "." else rel


def domain_allowed(domain, cfg) -> bool:
    d = (domain or "").strip()
    return bool(d) and d.upper() != "WORK" and d in cfg["design_domains"]


class Forwarder:
    def __init__(self, cfg, state=None, scan=None, rnd=random.random, sleep=time.sleep, clock=time.time, timeout=15.0, pace=0.0):
        self.cfg, self.state = cfg, state or State()
        self.base = cfg["url"]
        self.scan, self.rnd, self.sleep, self.clock, self.timeout, self.pace = scan, rnd, sleep, clock, timeout, pace
        self.batch = {"events": EVENT_BATCH, "tokens": TOKEN_BATCH, "snapshots": 1, "designs": 1}
        self.token = None
        self.failures = 0
        self.last_class = None
        self.sent = {"events": 0, "tokens": 0, "designs": 0, "snapshots": 0}
        self.skipped = 0
        self.dropped = 0

    # -- transport --
    def _http(self, method, path, body=None):
        hdrs = {"Authorization": f"Bearer {self.token}"}
        if body is not None:
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=body, headers=hdrs, method=method)
        try:
            with _OPENER.open(req, timeout=self.timeout) as r:
                status, raw = r.status, r.read(1 << 20)
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                raw = e.read(1 << 16)
            except OSError:
                raw = b""
            if 300 <= status < 400:
                raise Retry("redirect") from None
        except (urllib.error.URLError, OSError) as e:
            reason = e.reason if isinstance(e, urllib.error.URLError) else e
            raise Retry("net_" + type(reason).__name__) from None
        if self.pace:
            self.sleep(self.pace)
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        return status, data if isinstance(data, dict) else {}

    def _send(self, kind, rows):
        """POST rows; splits on 413 / oversize, isolates on 400. Returns (accepted, skipped). Raises Retry/Refused."""
        if not rows:
            return 0, 0
        body = json.dumps({"v": 1, "kind": kind, "rows": rows}, separators=(",", ":")).encode()
        if len(rows) > 1 and len(body) > BODY_LIMIT[kind] * SOFT:
            return self._split(kind, rows)
        status, data = self._http("POST", "/api/ingest", body)
        if status == 200:
            return int(data.get("accepted") or 0), int(data.get("skipped") or 0)
        if status == 413:
            if len(rows) > 1:
                self.batch[kind] = max(1, len(rows) // 2)
                return self._split(kind, rows)
            return 0, 1  # a single row the hub refuses as too big
        if status == 400:
            if len(rows) > 1:  # isolate: a single bad row must not wedge the cursor
                a = s = 0
                for r in rows:
                    x, y = self._send(kind, [r])
                    a, s = a + x, s + y
                return a, s
            return 0, 1
        if status in (401, 403, 404):
            raise Refused(f"http_{status}")
        raise Retry(f"http_{status}")

    def _split(self, kind, rows):
        h = len(rows) // 2
        a1, s1 = self._send(kind, rows[:h])
        a2, s2 = self._send(kind, rows[h:])
        return a1 + a2, s1 + s2

    def _count(self, kind, acc, skip, n=None):
        self.sent[kind] += acc
        self.skipped += skip

    # -- events --
    def _read_events(self, after, limit):
        try:
            con = ne.connect(readonly=True)
        except (FileNotFoundError, sqlite3.Error):
            return []
        try:
            has_host = any(r[1] == "host" for r in con.execute("PRAGMA table_info(events)"))
            q = ("SELECT id,ts,event,session_id,domain,project,project_dir,agent_id,agent_type,tool,tool_use_id,summary "
                 "FROM events WHERE id > ?" + (" AND host IS NULL" if has_host else "") + " ORDER BY id LIMIT ?")
            return [dict(r) for r in con.execute(q, (int(after), int(limit)))]
        except sqlite3.Error:
            return []
        finally:
            con.close()

    def event_rows(self, rows):
        """Local events.db rows -> ingest rows with a WS-relative project_dir; rows outside the workspace are dropped."""
        out = []
        for r in rows:
            rel = ws_rel(r.get("project_dir"))
            if rel is None:
                self.dropped += 1
                continue
            row = {k: r.get(k) for k in ("ts", "event", "session_id", "domain", "project", "agent_id", "agent_type",
                                         "tool", "tool_use_id", "summary")}
            row["src_id"] = r["id"]
            row["project_dir"] = rel
            out.append(row)
        return out

    def _resume_cursor(self, key, hub_key):
        cur = self.state.get(key)
        if cur is not None:
            return int(cur)
        status, data = self._http("GET", "/api/ingest/cursor")
        if status in (401, 403, 404):
            raise Refused(f"http_{status}")
        if status != 200:
            raise Retry(f"http_{status}")
        sn = data.get("server_now")
        if isinstance(sn, (int, float)) and not isinstance(sn, bool):
            self.state.put("skew", round(float(sn) - self.clock(), 1))
        cur_obj = data.get("cursor") if isinstance(data.get("cursor"), dict) else data
        v = cur_obj.get(hub_key)
        v = int(v) if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0
        self.state.put(key, v)
        return v

    def push_events(self):
        cur = self._resume_cursor("events", "events")
        for _ in range(MAX_LOOPS):
            raw = self._read_events(cur, EVENT_BATCH)
            if not raw:
                return
            rows = self.event_rows(raw)
            for i in range(0, len(rows), max(1, self.batch["events"])):
                a, s = self._send("events", rows[i:i + max(1, self.batch["events"])])
                self._count("events", a, s)
            cur = raw[-1]["id"]
            self.state.put("events", cur)
            if len(raw) < EVENT_BATCH:
                return

    # -- tokens --
    def push_tokens(self):
        import niu_usage
        if self.scan:
            try:
                self.scan()
            except Exception as e:  # noqa: BLE001 - usage scan problems must not stop the forwarder
                _log(f"usage scan failed: {type(e).__name__}")
        since = self.clock() - TOKEN_DAYS * 86400
        for _ in range(MAX_LOOPS):
            rows = niu_usage.export_rows(since, TOKEN_BATCH, after_mkeys=self.state.tok_sent)
            if not rows:
                return
            step = max(1, self.batch["tokens"])
            for i in range(0, len(rows), step):
                chunk = rows[i:i + step]
                a, s = self._send("tokens", chunk)
                self._count("tokens", a, s)
                self.state.mark_tok(chunk)
            if len(rows) < TOKEN_BATCH:
                return
        self.state.prune(self.clock())

    # -- designs and snapshots --
    def push_designs(self):
        import niu_designs
        try:
            idx = niu_designs.index(force=True)
        except Exception as e:  # noqa: BLE001
            _log(f"design index failed: {type(e).__name__}")
            return
        sent = self.state.designs()
        want = set()
        for entry in idx.values():
            if not domain_allowed(entry.get("domain"), self.cfg) or str(entry["rel"]).split("/", 1)[0].upper() == "WORK":
                continue
            want.add(entry["rel"])
            text = niu_designs.read_doc(entry)
            if text is None:
                continue
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if sent.get(entry["rel"]) == sha:
                continue
            row = {"rel": entry["rel"], "sha": sha, "mtime": entry.get("mtime") or 0, "title": entry.get("title") or "",
                   "status": entry.get("status") or "", "content": text}
            a, s = self._send("designs", [row])
            self._count("designs", a, s)
            self.state.set_design(entry["rel"], sha)  # a rejected doc is not retried until it changes
        for rel in sorted(set(sent) - want):  # deletion propagation
            a, s = self._send("designs", [{"rel": rel, "deleted": True}])
            self._count("designs", a, s)
            self.state.del_design(rel)

    def _read_snapshots(self, after, limit):
        try:
            con = ne.connect(readonly=True)
        except (FileNotFoundError, sqlite3.Error):
            return []
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(design_snapshots)")}
            q = ("SELECT id,ts,rel,sha,session_id,agent_type,tool,content FROM design_snapshots WHERE id > ?"
                 + (" AND host IS NULL" if "host" in cols else "") + " ORDER BY id LIMIT ?")
            return [dict(r) for r in con.execute(q, (int(after), int(limit)))]
        except sqlite3.Error:
            return []
        finally:
            con.close()

    def push_snapshots(self):
        cur = self._resume_cursor("snapshots", "snapshots")
        for _ in range(MAX_LOOPS):
            raw = self._read_snapshots(cur, 20)
            if not raw:
                return
            for r in raw:
                rel = r["rel"]
                first = rel.split("/", 1)[0]
                dom = "(workspace)" if first == ne.PROPOSALS_DIR else first
                if domain_allowed(dom, self.cfg):
                    row = {"src_id": r["id"], "ts": r["ts"], "rel": rel, "sha": r["sha"], "session_id": r["session_id"],
                           "agent_type": r["agent_type"], "tool": r["tool"], "content": r["content"]}
                    a, s = self._send("snapshots", [row])
                    self._count("snapshots", a, s)
                else:
                    self.dropped += 1
                cur = r["id"]
                self.state.put("snapshots", cur)
            if len(raw) < 20:
                return

    # -- passes --
    def once(self, events=True, slow=True) -> dict:
        """One pass over everything that is enabled. Never raises; returns counts plus `error` (a class) when it stopped."""
        self.sent = {k: 0 for k in self.sent}
        self.skipped = self.dropped = 0
        res = {"error": None}
        try:
            self.token = read_token(self.cfg)
            send = self.cfg["send"]
            if events and send["events"]:
                self.push_events()
            if slow and send["tokens"]:
                self.push_tokens()
            if slow and send["designs"]:
                self.push_designs()
                self.push_snapshots()
            self.failures = 0
            self.last_class = None
            self.state.put("last_ok", self.clock())
            self.state.put("last_error", "")
        except Refused as e:
            res["error"] = str(e)
            res["refused"] = True
        except Retry as e:
            res["error"] = str(e)
            self.failures += 1
        except sqlite3.Error as e:
            res["error"] = "db_" + type(e).__name__
            self.failures += 1
        self.last_class = res["error"]
        if res["error"]:
            self.state.put("last_error", res["error"])
        res.update(self.sent)
        res["skipped"], res["dropped"] = self.skipped, self.dropped
        return res

    def run(self, should_stop=lambda: False):
        """Service loop: events every 2 s, tokens/designs every 60 s; backoff on errors; pause on 401/403."""
        last_slow = 0.0
        paused_until = 0.0
        last_logged = 0.0
        tok_sig = None
        while not should_stop():
            now = self.clock()
            sig = self._token_sig()
            if paused_until and sig != tok_sig:
                paused_until = 0.0  # the token file changed (re-paired)
            if now < paused_until:
                self.sleep(2.0)
                continue
            slow = now - last_slow >= 60.0
            res = self.once(events=True, slow=slow)
            if slow and not res["error"]:
                last_slow = now
            if res["error"]:
                if res.get("refused"):
                    tok_sig = sig
                    paused_until = now + LOG_EVERY
                    if now - last_logged >= LOG_EVERY:
                        _log(f"not paired / refused ({res['error']}); re-pair with `niu-forward pair`")
                        last_logged = now
                    continue
                d = backoff_delay(self.failures, self.rnd)
                _log(f"push failed: {res['error']}; retrying in {d:.0f}s")
                self.sleep(d)
                continue
            n = sum(res[k] for k in ("events", "tokens", "designs", "snapshots"))
            if n:
                _log("sent " + ", ".join(f"{k}={res[k]}" for k in ("events", "tokens", "designs", "snapshots") if res[k])
                     + f" skipped={res['skipped']}")
            self.sleep(2.0)

    def _token_sig(self):
        try:
            st = os.stat(self.cfg["token_file"])
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None


# ---------- pairing and status ----------

def pair(url, code, timeout=15.0):
    """Exchange a pairing code for a token. Returns the machine label; writes the token file and the url into the config."""
    base = check_url(url)
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{4,12}", code.strip()):
        raise ConfigError("code: expected the digits shown on the hub")
    body = json.dumps({"code": code.strip()}).encode()
    req = urllib.request.Request(base + "/api/machine/pair", data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            data = json.loads(r.read(1 << 16) or b"{}")
    except urllib.error.HTTPError as e:
        raise ConfigError(f"pairing refused (http {e.code})") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ConfigError(f"pairing failed ({type(getattr(e, 'reason', e)).__name__})") from None
    tok, label = (data.get("token"), data.get("label")) if isinstance(data, dict) else (None, None)
    if (not isinstance(tok, str) or not tok or len(tok) > 512 or not tok.isascii() or not tok.isprintable() or " " in tok
            or not isinstance(label, str) or not RE_LABEL.fullmatch(label)):
        raise ConfigError("pairing failed (unexpected response)")
    tf = os.environ.get("NIU_FORWARD_TOKEN")
    if not tf:
        try:
            tf = json.loads(config_path().read_text("utf-8")).get("token_file")
        except (OSError, ValueError, AttributeError):
            tf = None
    write_token(os.path.expanduser(tf or str(default_token_path())), tok)
    set_config_url(base)
    st = State()
    st.put("label", label)
    with st.con:  # a new pairing starts from the hub's high-water marks
        st.con.execute("DELETE FROM cursor WHERE kind IN ('events','snapshots')")
    st.close()
    return label


def status(cfg=None) -> dict:
    """Facts for `niu-forward status` / workspace-status. Never the token, never the URL path."""
    out = {"paired": False, "host": None, "label": None, "error": None, "last_ok": None, "backlog": None, "token": "missing", "skew": None}
    try:
        cfg = cfg or load_config()
    except ConfigError as e:
        out["error"] = f"config: {e}"
        return out
    out["host"] = urllib.parse.urlsplit(cfg["url"]).hostname
    try:
        read_token(cfg)
        out["token"] = "ok"
    except Refused as e:
        out["token"] = str(e)[6:] or "invalid"
    st = State()
    try:
        out["label"] = st.get("label")
        out["last_ok"] = st.get("last_ok")
        out["error"] = st.get("last_error") or None
        out["paired"] = out["token"] == "ok"
        cur = st.get("events")
        out["cursor"] = {"events": cur, "snapshots": st.get("snapshots")}
        out["backlog"] = _backlog(int(cur or 0))
        out["skew"] = st.get("skew")
    finally:
        st.close()
    return out


def _backlog(after):
    try:
        con = ne.connect(readonly=True)
    except (FileNotFoundError, sqlite3.Error):
        return 0
    try:
        has_host = any(r[1] == "host" for r in con.execute("PRAGMA table_info(events)"))
        return con.execute("SELECT COUNT(*) FROM events WHERE id > ?" + (" AND host IS NULL" if has_host else ""), (after,)).fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        con.close()


def status_line(s) -> str:
    """One line for workspace-status; starts with 'ok' or 'problem'."""
    if s.get("host") is None:
        return f"problem: {s.get('error')}"
    ago = ""
    if s.get("last_ok"):
        ago = f", last success {int(max(0, time.time() - float(s['last_ok'])))}s ago"
    base = f"{s['host']}, backlog {s['backlog']}{ago}"
    if not s["paired"]:
        return f"problem: not paired ({s['token']}) -> {base}"
    if s.get("error"):
        return f"problem: {s['error']} -> {base}"
    return f"ok: paired, {base}"
