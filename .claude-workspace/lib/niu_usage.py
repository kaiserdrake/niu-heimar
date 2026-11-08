"""Claude plan-usage history and token statistics for the dashboard (stdlib only).

`usage.db` (next to events.db, mode 0600) keeps samples of the 5-hour and weekly limits so the Events tab can draw the
weekly graph, and per-message token counts read from Claude Code transcripts for the Token usage panel. The only writer
is the background thread started by `niu-dash serve` (or `niu-dash usage-scan`); HTTP requests only read, through
read-only connections. Nothing secret is ever stored: limit rows hold a key, a time, a percentage and a reset time; token
rows hold numbers, ids, model names and agent types, never transcript text, prompts or paths outside the workspace.
"""
import hashlib
import json
import math
import os
import re
import sqlite3
import stat as _stat
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from niu_events import db_path as _events_db_path

KEYS = ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet")
LABELS = {"five_hour": "5-hour", "seven_day": "Weekly", "seven_day_opus": "Weekly Opus", "seven_day_sonnet": "Weekly Sonnet"}
SOURCES = ("api", "statusline")
SAMPLE_EVERY = 5 * 60          # one sample attempt per 5 minutes (the usage API itself is throttled to the same rate)
STALE_SECS = 15 * 60           # a reading older than this is not "the present": never stored
HEARTBEAT_SECS = 10 * 60       # an unchanged value is still stored this often, to prove coverage
MIN_CHANGE = 0.1               # percentage points
RESET_JUMP_SECS = 3600         # resets_at moving by more than this is a new window
GAP_SECS = 25 * 60             # more than this between two rows is drawn as a gap
RETENTION_DAYS = 70
PRUNE_EVERY = 3600
DAYS_MIN, DAYS_MAX, DAYS_DEFAULT = 7, 70, 35
MAX_POINTS = 900
PARTIAL_SECS = 6 * 3600        # last sample this long before the reset: the week's end value is only a lower bound
CURRENT_STALE_SECS = 30 * 60
MEMO_SECS = 60
MAX_WEEKS = 12

# ----- token scanner (B2) -----
SCAN_EVERY = 60                    # seconds between scan passes inside the background thread
SCAN_BYTES = 32 * 1024 * 1024      # read budget per pass
SCAN_SECS = 1.5                    # wall-time budget per pass
SCAN_MAX_FILES = 5000
SCAN_MAX_AGE = 90 * 86400          # only files modified within this window are read
MAX_LINE = 8 * 1024 * 1024         # longer lines are skipped and counted
META_MAX = 16 * 1024               # agent-<id>.meta.json size cap
CHUNK = 1024 * 1024
TOK_RETENTION_DAYS = 90
MAX_TOKENS_FIELD = 10_000_000
RANGES = ("today", "7d", "30d")
TOP_AGENTS, TOP_MODELS = 12, 8
RE_UUID = re.compile(r"[0-9a-f-]{36}")
RE_AGENT_ID = re.compile(r"[A-Za-z0-9]{1,40}")
RE_MSG_ID = re.compile(r"msg_[A-Za-z0-9]{8,64}")
RE_REQ_ID = re.compile(r"req_[A-Za-z0-9]{8,64}")
RE_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,59}")
RE_AGENT_TYPE = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
RE_DOMAIN_Q = re.compile(r"[A-Za-z0-9 ._()-]{1,64}")
OUTSIDE = "(outside)"
FAMILIES = ("opus", "sonnet", "haiku", "other")

SCHEMA = """
CREATE TABLE IF NOT EXISTS limit_samples (
  key TEXT NOT NULL CHECK (key IN ('five_hour','seven_day','seven_day_opus','seven_day_sonnet')),
  ts REAL NOT NULL,
  used REAL NOT NULL CHECK (used BETWEEN 0 AND 100),
  resets_at REAL,
  source TEXT NOT NULL CHECK (source IN ('api','statusline')),
  PRIMARY KEY (key, ts)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS limit_samples_ts ON limit_samples(ts);
CREATE TABLE IF NOT EXISTS tok_files (
  id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, dev INTEGER, ino INTEGER, size INTEGER, mtime_ns INTEGER,
  offset INTEGER NOT NULL DEFAULT 0, skipping INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL CHECK (kind IN ('main','sub')),
  session_id TEXT, agent_id TEXT, agent_type TEXT, domain TEXT, project TEXT, last_seen REAL);
CREATE TABLE IF NOT EXISTS tok_models (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, family TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tok_msgs (
  mkey INTEGER PRIMARY KEY,
  ts REAL NOT NULL, file_id INTEGER NOT NULL, model_id INTEGER NOT NULL,
  inp INTEGER NOT NULL, outp INTEGER NOT NULL, cw INTEGER NOT NULL, cr INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS tok_msgs_ts ON tok_msgs(ts);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""
SCHEMA_VERSION = 2

# Fixed, content-free summaries of what went wrong; exception text is never exposed.
ERR_NONE, ERR_READ, ERR_DB, ERR_OTHER = "none", "reading", "database", "other"
_stats = {"errors": 0, "last": ERR_NONE, "state": "stopped"}
_stats_lock = threading.Lock()


def collect_disabled():
    return os.environ.get("NIU_USAGE_COLLECT") == "0"


def tokens_disabled():
    return collect_disabled() or os.environ.get("NIU_USAGE_TOKENS") == "0"


def db_path() -> Path:
    if os.environ.get("NIU_USAGE_DB"):
        return Path(os.environ["NIU_USAGE_DB"])
    return _events_db_path().parent / "usage.db"


def connect(readonly: bool = False) -> sqlite3.Connection:
    p = db_path()
    if readonly:
        if not p.exists():
            raise FileNotFoundError(p)
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2)
    else:
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not p.exists():
            os.close(os.open(p, os.O_WRONLY | os.O_CREAT, 0o600))  # created 0600 from the start, whatever the umask
            os.chmod(p, 0o600)
        con = sqlite3.connect(p, timeout=2)
        uv = con.execute("PRAGMA user_version").fetchone()[0]
        if uv == 0 and not con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
            con.execute("PRAGMA auto_vacuum=INCREMENTAL")  # only effective before the first table exists
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)  # CREATE IF NOT EXISTS only: a version 1 file gains the token tables, its samples are untouched
        if uv < SCHEMA_VERSION:
            con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    con.row_factory = sqlite3.Row
    return con


# ---------- writer ----------

def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def record_limits(con, reading, now=None):
    """Store one reading `(updated, source, {key: {'used', 'resets_at'}})`; returns the number of rows inserted.

    A row is stamped with the reading's own `updated` time, never the insert time, so the same reading is stored once."""
    now = time.time() if now is None else now
    if not reading:
        return 0
    try:
        updated, source, windows = reading
    except (TypeError, ValueError):
        return 0
    updated = _num(updated)
    if updated is None or updated <= 0 or source not in SOURCES or not isinstance(windows, dict):
        return 0
    if now - updated > STALE_SECS or updated > now + 300:
        return 0
    added = 0
    for key in KEYS:
        w = windows.get(key)
        used = _num(w.get("used")) if isinstance(w, dict) else None
        if used is None:
            continue
        used = min(100.0, max(0.0, used))
        reset = _num(w.get("resets_at"))
        reset = round(reset / 60) * 60 if reset else None
        last = con.execute("SELECT ts, used, resets_at FROM limit_samples WHERE key=? ORDER BY ts DESC LIMIT 1", (key,)).fetchone()
        if last is not None:
            if updated <= last["ts"]:
                continue
            moved = (reset is None) != (last["resets_at"] is None) or (
                reset is not None and abs(reset - last["resets_at"]) > RESET_JUMP_SECS)
            if not (abs(used - last["used"]) >= MIN_CHANGE - 1e-9 or moved or updated - last["ts"] >= HEARTBEAT_SECS):
                continue
        cur = con.execute("INSERT OR IGNORE INTO limit_samples(key, ts, used, resets_at, source) VALUES (?,?,?,?,?)",
                          (key, updated, used, reset, source))
        added += cur.rowcount
    if added:
        con.execute("INSERT OR IGNORE INTO meta(k, v) VALUES ('collecting_since', ?)", (str(int(updated)),))
    con.commit()
    return added


def record(reading, now=None):
    """Open usage.db, store the reading, close it."""
    con = connect()
    try:
        return record_limits(con, reading, now)
    finally:
        con.close()


def prune(con, now=None):
    """Drop samples older than the retention; returns rows deleted."""
    now = time.time() if now is None else now
    n = con.execute("DELETE FROM limit_samples WHERE ts < ?", (now - RETENTION_DAYS * 86400,)).rowcount
    cut = now - TOK_RETENTION_DAYS * 86400
    t = con.execute("DELETE FROM tok_msgs WHERE ts < ?", (cut,)).rowcount
    t += con.execute("DELETE FROM tok_files WHERE last_seen IS NOT NULL AND last_seen < ?", (cut,)).rowcount
    t += con.execute("DELETE FROM tok_msgs WHERE file_id NOT IN (SELECT id FROM tok_files)").rowcount
    con.commit()
    if n or t:
        try:
            con.execute("PRAGMA incremental_vacuum")
        except sqlite3.DatabaseError:
            pass
    return n


def _count_error(kind):
    with _stats_lock:
        _stats["errors"] += 1
        _stats["last"] = kind


def recorder_status():
    with _stats_lock:
        return {"state": _stats["state"], "errors": _stats["errors"], "last_error": _stats["last"]}


class Recorder(threading.Thread):
    """Daemon thread: one sample attempt per `interval`, never raises. `reading(now)` returns what niu_limits.latest_reading does."""

    def __init__(self, interval=SAMPLE_EVERY, reading=None, clock=time.time, stop=None, scan=None, scan_every=SCAN_EVERY, scanner=None):
        super().__init__(name="niu-usage-recorder", daemon=True)
        self.interval, self.clock = interval, clock
        self.scan = (not tokens_disabled()) if scan is None else bool(scan)   # second periodic task: the transcript token scanner
        self.scan_every, self._scanner = scan_every, scanner
        self.stop_event = stop or threading.Event()
        self._reading = reading
        self._last_prune = 0.0

    def _read(self, now):
        if self._reading is not None:
            return self._reading(now)
        import niu_limits
        return niu_limits.latest_reading(now)

    def tick(self):
        """One attempt; returns rows inserted (0 on any problem, which is counted)."""
        now = self.clock()
        try:
            reading = self._read(now)
        except Exception:  # noqa: BLE001 - the recorder must never die
            _count_error(ERR_READ)
            return 0
        added = 0
        try:
            con = connect()
            try:
                added = record_limits(con, reading, now)
                if now - self._last_prune >= PRUNE_EVERY:
                    prune(con, now)
                    self._last_prune = now
            finally:
                con.close()
        except sqlite3.Error:
            _count_error(ERR_DB)
        except Exception:  # noqa: BLE001
            _count_error(ERR_OTHER)
        return added

    def scan_tick(self):
        """One scanner pass; never raises. SQLite busy or any other problem skips the tick (counted, content-free)."""
        try:
            (self._scanner or scan_once)(now=self.clock())
        except sqlite3.Error as e:
            _count_error(ERR_DB)
            _debug(e)
        except Exception as e:  # noqa: BLE001 - the scanner must never kill the thread
            _count_error(ERR_OTHER)
            _debug(e)

    def run(self):
        with _stats_lock:
            _stats["state"] = "running"
        step = min(self.interval, self.scan_every) if self.scan else self.interval
        last_sample = last_scan = None
        try:
            while not self.stop_event.is_set():
                now = self.clock()
                if last_sample is None or now - last_sample >= self.interval - 1e-6:
                    last_sample = now
                    self.tick()
                if self.scan and (last_scan is None or now - last_scan >= self.scan_every - 1e-6):
                    last_scan = now
                    self.scan_tick()
                if self.stop_event.wait(step):
                    break
        finally:
            with _stats_lock:
                _stats["state"] = "stopped"

    def stop(self):
        self.stop_event.set()


_bg = None
_bg_lock = threading.Lock()


def start_background(**kw):
    """Start the single recorder thread (idempotent). Returns it, or None when NIU_USAGE_COLLECT=0."""
    global _bg
    if collect_disabled():
        return None
    with _bg_lock:
        if _bg is None or not _bg.is_alive():
            _bg = Recorder(**kw)
            _bg.start()
        return _bg


def stop_background(timeout=2):
    global _bg
    with _bg_lock:
        t, _bg = _bg, None
    if t is not None:
        t.stop()
        t.join(timeout)


# ---------- token scanner (the only reader of transcripts) ----------
# Nothing from a transcript is ever printed or logged. With NIU_DASH_DEBUG set, only exception class names and file ids go to stderr.

def _debug(exc, file_id=None):
    if os.environ.get("NIU_DASH_DEBUG"):
        print(f"niu-usage: {type(exc).__name__}" + (f" file#{file_id}" if file_id is not None else ""), file=sys.stderr)


def claude_root():
    """`<CLAUDE_CONFIG_DIR or ~/.claude>/projects`, or None when it is missing or itself a symlink."""
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
    try:
        root = base.resolve() / "projects"
        if _stat.S_ISLNK(os.lstat(root).st_mode) or not root.is_dir():
            return None
        return root
    except (OSError, RuntimeError):
        return None


def _open_confined(path, root, uid=None):
    """Open `path` read-only for scanning, or return None. Refuses symlinks (O_NOFOLLOW), anything that is not a regular file
    (FIFOs and devices are rejected before a read), files owned by another uid, and paths that resolve outside `root`."""
    uid = os.getuid() if uid is None else uid
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except (OSError, ValueError):
        return None
    ok = False
    try:
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode) or st.st_uid != uid:
            return None
        try:
            real = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            real = os.path.realpath(path)
        if not real.startswith(os.path.realpath(root).rstrip(os.sep) + os.sep):
            return None
        ok = True
        return fd
    finally:
        if not ok:
            os.close(fd)


def _agent_type(meta_path, agent_id, root, uid=None):
    """Agent type of a subagent: the `agentType` key of its meta.json (read through the same checks, 16 KiB cap), else the
    events.db row for that agent id, else 'subagent'. Only a value matching RE_AGENT_TYPE is ever kept."""
    fd = _open_confined(meta_path, root, uid)
    if fd is not None:
        try:
            raw = os.read(fd, META_MAX + 1)
        except OSError:
            raw = b""
        finally:
            os.close(fd)
        if 0 < len(raw) <= META_MAX:
            try:
                v = json.loads(raw).get("agentType")
            except (ValueError, AttributeError, RecursionError):
                v = None
            if isinstance(v, str) and RE_AGENT_TYPE.fullmatch(v):
                return v
    if isinstance(agent_id, str) and RE_AGENT_ID.fullmatch(agent_id):
        try:
            import niu_events
            ec = niu_events.connect(readonly=True)
            try:
                r = ec.execute("SELECT agent_type FROM events WHERE agent_id=? AND agent_type IS NOT NULL LIMIT 1", (agent_id,)).fetchone()
            finally:
                ec.close()
            if r and isinstance(r[0], str) and RE_AGENT_TYPE.fullmatch(r[0]):
                return r[0]
        except (OSError, sqlite3.Error):
            pass
    return "subagent"


def norm_model(name):
    """(short name, family): strip 'claude-' and a trailing '-YYYYMMDD'. Anything that is not a plain model name becomes 'other'."""
    if not isinstance(name, str) or not RE_MODEL.fullmatch(name):
        return "other", "other"
    short = re.sub(r"-\d{8}$", "", name[7:] if name.startswith("claude-") else name) or "other"
    low = short.lower()
    fam = next((f for f in ("opus", "sonnet", "haiku") if f in low), "other")
    return short, fam


def _mkey(mid):
    return int.from_bytes(hashlib.sha256(mid.encode()).digest()[:8], "big", signed=True)


def _int_field(usage, key):
    v = usage.get(key, 0)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > MAX_TOKENS_FIELD:
        return None
    return v


def _parse_ts(v, fallback):
    if isinstance(v, str) and len(v) <= 40:
        try:
            t = datetime.fromisoformat(v).timestamp()
            if 1.5e9 < t < time.time() + 86400 * 2:
                return t
        except (ValueError, OverflowError, OSError):
            pass
    return fallback


CORRUPT = False


def _extract(line, fallback_ts=0.0):
    """Allowlist extraction from one transcript line. Returns a dict, None (not a usage line: ignored) or CORRUPT.

    Only ids, the model name, the four usage numbers, the timestamp and the cwd leave this function; everything else is dropped."""
    if b'"usage"' not in line or b'"assistant"' not in line:
        return None
    try:
        o = json.loads(line)
    except (ValueError, RecursionError):
        return CORRUPT
    if not isinstance(o, dict) or o.get("type") != "assistant":
        return None
    m = o.get("message")
    if not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
        return None
    mid = m.get("id")
    if not (isinstance(mid, str) and RE_MSG_ID.fullmatch(mid)):
        mid = o.get("requestId")
        if not (isinstance(mid, str) and RE_REQ_ID.fullmatch(mid)):
            return None
    model = m.get("model")
    if model == "<synthetic>":
        return None
    u = m["usage"]
    if not any(k in u for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")):
        return None
    vals = [_int_field(u, k) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
    if None in vals:
        return CORRUPT
    cwd = o.get("cwd")
    return {"mkey": _mkey(mid), "model": model if isinstance(model, str) else None, "vals": vals,
            "ts": _parse_ts(o.get("timestamp"), fallback_ts), "side": o.get("isSidechain") is True,
            "cwd": cwd if isinstance(cwd, str) and 0 < len(cwd) <= 4096 and os.path.isabs(cwd) else None}


def _locate(cwd):
    """(domain, project) for a cwd. Outside the workspace: ('(outside)', None), and the path is dropped."""
    try:
        import niu_events
        d, p, _dir = niu_events.locate(cwd)
    except (OSError, ValueError, RuntimeError):
        return OUTSIDE, None
    if d is None:
        return OUTSIDE, None
    return d[:80], (p[:120] if p else None)


def _discover(root, now):
    """Accepted transcript files under root: [(path, kind, session_id, agent_id, st)], newest first, at most SCAN_MAX_FILES.
    Only `<slug>/<uuid>.jsonl` and `<slug>/<uuid>/subagents/agent-<id>.jsonl`; symlinks are never followed."""
    out = []
    cutoff = now - SCAN_MAX_AGE

    def entries(d):
        try:
            with os.scandir(d) as it:
                return list(it)
        except OSError:
            return []

    for slug in entries(root):
        try:
            if not slug.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        for e in entries(slug.path):
            try:
                if e.is_file(follow_symlinks=False) and e.name.endswith(".jsonl") and RE_UUID.fullmatch(e.name[:-6]):
                    st = e.stat(follow_symlinks=False)
                    if st.st_mtime >= cutoff:
                        out.append((e.path, "main", e.name[:-6], None, st))
                elif e.is_dir(follow_symlinks=False) and RE_UUID.fullmatch(e.name):
                    for sd in entries(e.path):
                        if sd.name != "subagents" or not sd.is_dir(follow_symlinks=False):
                            continue
                        for f in entries(sd.path):
                            if (f.name.startswith("agent-") and f.name.endswith(".jsonl") and RE_AGENT_ID.fullmatch(f.name[6:-6])
                                    and f.is_file(follow_symlinks=False)):
                                st = f.stat(follow_symlinks=False)
                                if st.st_mtime >= cutoff:
                                    out.append((f.path, "sub", e.name, f.name[6:-6], st))
            except OSError:
                continue
    out.sort(key=lambda x: x[4].st_mtime, reverse=True)
    return out[:SCAN_MAX_FILES]


_scan_lock = threading.Lock()


def _file_key(root, path):
    """The DB key of a transcript: a hash of its path relative to the root. The folder name encodes the session's cwd, so the
    real path (which may be outside the workspace) is never stored."""
    return "h:" + hashlib.sha256(os.path.relpath(path, root).encode("utf-8", "surrogateescape")).hexdigest()[:32]


def _meta_set(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)", (k, str(v)))


def _meta_int(con, k):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    try:
        return int(r[0]) if r else 0
    except (TypeError, ValueError):
        return 0


def reset_offsets(con):
    """`usage-scan --full`: forget every offset so each file is read again (dedup makes that safe)."""
    con.execute("UPDATE tok_files SET offset=0, size=NULL, mtime_ns=NULL, skipping=0")
    _meta_set(con, "corrupt_lines", 0)
    _meta_set(con, "skipped_files", 0)
    con.commit()


class _Pass:
    def __init__(self, root, uid, budget_bytes, budget_secs, clock, now):
        self.root, self.uid, self.now = root, uid, now
        self.bytes_left, self.deadline, self.clock = budget_bytes, clock() + budget_secs, clock
        self.messages = self.corrupt = self.skipped = self.bytes = 0
        self.models = {}

    def out_of_budget(self):
        return self.bytes_left <= 0 or self.clock() >= self.deadline


UPSERT = ("INSERT INTO tok_msgs(mkey, ts, file_id, model_id, inp, outp, cw, cr) VALUES (?,?,?,?,?,?,?,?) "
          "ON CONFLICT(mkey) DO UPDATE SET inp=max(inp, excluded.inp), outp=max(outp, excluded.outp), cw=max(cw, excluded.cw), cr=max(cr, excluded.cr)")


def _model_id(con, ps, raw):
    name, fam = norm_model(raw)
    mid = ps.models.get(name)
    if mid is None:
        con.execute("INSERT OR IGNORE INTO tok_models(name, family) VALUES (?, ?)", (name, fam))
        mid = con.execute("SELECT id FROM tok_models WHERE name=?", (name,)).fetchone()[0]
        ps.models[name] = mid
    return mid


def _scan_file(con, ps, path, kind, session_id, agent_id, est, row):
    """Read the new complete lines of one transcript. Returns (file_id, finished, bytes_left). May raise; the caller isolates the file."""
    fd = _open_confined(path, ps.root, ps.uid)
    if fd is None:
        ps.skipped += 1
        return None, True, 0
    try:
        st = os.fstat(fd)            # taken before reading: anything appended later shows up as a size change next pass
        offset, skipping = (row["offset"], row["skipping"]) if row else (0, 0)
        if row and ((row["dev"], row["ino"]) != (st.st_dev, st.st_ino) or st.st_size < offset):
            offset, skipping = 0, 0   # rotated or rewritten
        agent_type = row["agent_type"] if row else ("session" if kind == "main" else None)
        domain, project = (row["domain"], row["project"]) if row else (None, None)
        if kind == "sub" and agent_type in (None, "subagent"):
            agent_type = _agent_type(os.path.join(os.path.dirname(path), f"agent-{agent_id}.meta.json"), agent_id, ps.root, ps.uid)
        if kind == "sub" and domain is None:
            r = con.execute("SELECT domain, project FROM tok_files WHERE session_id=? AND kind='main' AND domain IS NOT NULL LIMIT 1", (session_id,)).fetchone()
            if r:
                domain, project = r[0], r[1]
        if row:
            fid = row["id"]
        else:
            fid = con.execute("INSERT INTO tok_files(path, kind, session_id, agent_id, agent_type, domain, project, last_seen) VALUES (?,?,?,?,?,?,?,?)",
                              (_file_key(ps.root, path), kind, session_id, agent_id, agent_type, domain, project, ps.now)).lastrowid
        side = {"id": None}
        fallback_ts = est.st_mtime
        batch = []

        def side_id():
            if side["id"] is None:
                sp = _file_key(ps.root, path) + "#side"
                con.execute("INSERT OR IGNORE INTO tok_files(path, kind, session_id, agent_type, domain, project, last_seen) VALUES (?, 'sub', ?, 'subagent', ?, ?, ?)",
                            (sp, session_id, domain, project, ps.now))
                side["id"] = con.execute("SELECT id FROM tok_files WHERE path=?", (sp,)).fetchone()[0]
            return side["id"]

        def handle(line):
            nonlocal domain, project
            r = _extract(line, fallback_ts)
            if r is None:
                return
            if r is CORRUPT:
                ps.corrupt += 1
                return
            if r["cwd"] and domain is None:
                domain, project = _locate(r["cwd"])
            if r["ts"] < ps.now - TOK_RETENTION_DAYS * 86400:
                return
            target = side_id() if (r["side"] and kind == "main") else fid
            batch.append((r["mkey"], r["ts"], target, _model_id(con, ps, r["model"]), *r["vals"]))
            ps.messages += 1

        os.lseek(fd, offset, os.SEEK_SET)
        base, carry, finished = offset, b"", True
        while True:
            if ps.out_of_budget():
                finished = False
                break
            chunk = os.read(fd, min(CHUNK, ps.bytes_left))
            if not chunk:
                break
            ps.bytes_left -= len(chunk)
            ps.bytes += len(chunk)
            if skipping:
                nl = chunk.find(b"\n")
                if nl < 0:
                    base += len(chunk)
                    continue
                skipping = 0
                ps.corrupt += 1            # the oversized line, counted once it ends
                base += nl + 1
                data = chunk[nl + 1:]
            else:
                data = carry + chunk
            start = 0
            while True:
                nl = data.find(b"\n", start)
                if nl < 0:
                    break
                if nl - start > MAX_LINE:
                    ps.corrupt += 1
                else:
                    handle(data[start:nl])
                start = nl + 1
            base += start
            carry = data[start:]
            if len(carry) > MAX_LINE:       # an oversized line is still being written: skip it without keeping it
                skipping = 1
                base += len(carry)
                carry = b""
        if batch:
            con.executemany(UPSERT, batch)
        if side["id"] is not None:
            con.execute("UPDATE tok_files SET domain=?, project=?, last_seen=? WHERE id=?", (domain, project, ps.now, side["id"]))
        con.execute("UPDATE tok_files SET dev=?, ino=?, size=?, mtime_ns=?, offset=?, skipping=?, agent_type=?, domain=?, project=?, last_seen=? WHERE id=?",
                    (st.st_dev, st.st_ino, st.st_size if finished else None, st.st_mtime_ns if finished else None, base, skipping,
                     agent_type, domain, project, ps.now, fid))
        con.commit()
        return fid, finished, max(0, st.st_size - base)
    finally:
        os.close(fd)


def scan_once(budget_bytes=SCAN_BYTES, budget_secs=SCAN_SECS, now=None, clock=time.monotonic, full=False, uid=None, root=None, con=None):
    """One incremental pass over the transcripts, within a byte and a time budget. Returns counts only.

    Raises sqlite3.Error when the database is busy (the caller skips the tick). Never raises for a bad file."""
    now = time.time() if now is None else now
    root = root or claude_root()
    res = {"files": 0, "messages": 0, "corrupt_lines": 0, "skipped_files": 0, "bytes": 0, "files_done": 0, "files_total": 0,
           "bytes_pending": 0, "scanning": False, "busy": False, "root": root is not None}
    if root is None:
        return res
    if not _scan_lock.acquire(blocking=False):
        res["busy"] = True
        return res
    own = con is None
    try:
        if own:
            con = connect()
        if full:
            reset_offsets(con)
        known = {r["path"]: r for r in con.execute("SELECT id, path, dev, ino, size, mtime_ns, offset, skipping, agent_type, domain, project, last_seen FROM tok_files")}
        ps = _Pass(root, os.getuid() if uid is None else uid, budget_bytes, budget_secs, clock, now)
        found = _discover(root, now)
        done, pending_bytes, touch = 0, 0, []
        for path, kind, sid, aid, est in found:
            row = known.get(_file_key(root, path))
            if row and row["size"] == est.st_size and row["mtime_ns"] == est.st_mtime_ns and (row["dev"], row["ino"]) == (est.st_dev, est.st_ino):
                done += 1
                if row["last_seen"] is None or now - row["last_seen"] > 3600:
                    touch.append((now, row["id"]))
                continue
            if ps.out_of_budget():
                pending_bytes += max(0, est.st_size - (row["offset"] if row else 0))
                continue
            try:
                fid, finished, left = _scan_file(con, ps, path, kind, sid, aid, est, row)
                res["files"] += fid is not None
                if finished:
                    done += 1
                else:
                    pending_bytes += left
            except sqlite3.OperationalError:
                con.rollback()
                raise
            except Exception as e:  # noqa: BLE001 - a bad file never stops the pass
                con.rollback()
                ps.models.clear()
                ps.skipped += 1
                _debug(e, row["id"] if row else None)
                done += 1   # not retried until it changes
        if touch:
            con.executemany("UPDATE tok_files SET last_seen=? WHERE id=?", touch)
        total = len(found)
        _meta_set(con, "corrupt_lines", _meta_int(con, "corrupt_lines") + ps.corrupt)
        _meta_set(con, "skipped_files", _meta_int(con, "skipped_files") + ps.skipped)
        _meta_set(con, "last_scan", int(now))
        _meta_set(con, "scan_progress", json.dumps({"files_done": done, "files_total": total, "bytes_pending": pending_bytes, "scanning": done < total}))
        con.commit()
        res.update(messages=ps.messages, corrupt_lines=ps.corrupt, skipped_files=ps.skipped, bytes=ps.bytes, files_done=done,
                   files_total=total, bytes_pending=pending_bytes, scanning=done < total)
        return res
    finally:
        _scan_lock.release()
        if own and con is not None:
            con.close()


def scan_to_completion(full=False, budget_secs=5.0, max_passes=100000, now=None):
    """Foreground passes until nothing is pending (the `niu-dash usage-scan` command). Returns summed counts."""
    tot = {"files": 0, "messages": 0, "corrupt_lines": 0, "skipped_files": 0, "files_total": 0, "stored": 0}
    for i in range(max_passes):
        r = scan_once(budget_bytes=256 * 1024 * 1024, budget_secs=budget_secs, full=full and i == 0, now=now)
        if r["busy"]:
            time.sleep(0.5)
            continue
        for k in ("files", "messages", "corrupt_lines", "skipped_files"):
            tot[k] += r[k]
        tot["files_total"] = r["files_total"]
        if not r["scanning"] or (r["bytes"] == 0 and r["files"] == 0):
            break
    try:
        c = connect(readonly=True)
        try:
            tot["stored"] = c.execute("SELECT count(*) FROM tok_msgs").fetchone()[0]
        finally:
            c.close()
    except (OSError, sqlite3.Error):
        pass
    return tot


# ---------- reader ----------

def clamp_days(v):
    """Integer 7-70; anything that is not an integer gives the default."""
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return DAYS_DEFAULT
    return min(DAYS_MAX, max(DAYS_MIN, n))


def weeks(samples, now):
    """Finished weekly windows from seven_day samples [(ts, used, resets_at)].

    Samples are grouped by resets_at (a jump of more than an hour is a new window). For each window that has reset,
    ended_pct is the highest value seen, and partial is true when sampling stopped more than 6 h before the reset."""
    groups = []
    for ts, used, reset in sorted(s for s in samples if s[2] is not None):
        if groups and abs(reset - groups[-1]["first"]) <= RESET_JUMP_SECS:
            g = groups[-1]
            g["max"], g["last"], g["resets_at"] = max(g["max"], used), ts, reset
        else:
            groups.append({"first": reset, "resets_at": reset, "max": used, "last": ts})
    out = []
    for g in groups:
        if g["resets_at"] <= now:
            out.append({"resets_at": int(g["resets_at"]), "ended_pct": round(g["max"], 1), "last_sample": int(g["last"]),
                        "partial": g["resets_at"] - g["last"] > PARTIAL_SECS})
    return out


def _segments(rows):
    segs, prev = [], None
    for ts, used in rows:
        if prev is None or ts - prev > GAP_SECS:
            segs.append([])
        segs[-1].append((ts, used))
        prev = ts
    return segs


def _points(rows, start, end):
    """[[ts, used], ..., None, ...]: None marks a gap. Over MAX_POINTS the series is bucketed, keeping each bucket's max."""
    segs = _segments(rows)
    if sum(len(s) for s in segs) > MAX_POINTS:
        width = max(1.0, (end - start) / MAX_POINTS)
        small = []
        for seg in segs:
            best = {}
            for ts, used in seg:
                b = int((ts - start) // width)
                if b not in best or used > best[b][1]:
                    best[b] = (ts, used)
            small.append([best[b] for b in sorted(best)])
        segs = small
    out = []
    for seg in segs:
        if out:
            out.append(None)
        out.extend([int(ts), round(used, 1)] for ts, used in seg)
    return out


def _tz_name():
    tz = os.environ.get("TZ")
    if tz:
        return tz.lstrip(":")[:64]
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1][:64]
    except OSError:
        pass
    return time.tzname[0][:16]


def history(days=None, now=None):
    """Payload of GET /api/usage/history (read-only). Never raises; failures give a fixed `error` state."""
    now = time.time() if now is None else now
    days = clamp_days(days if days is not None else DAYS_DEFAULT)
    base = {"state": "ok", "now": int(now), "days": days, "gap_secs": GAP_SECS, "tz": _tz_name(), "collecting_since": None,
            "series": {k: None for k in KEYS}, "resets": [], "weeks": [], "current": None, "recorder": recorder_status()}
    if collect_disabled():
        return {**base, "state": "disabled"}
    try:
        con = connect(readonly=True)
    except FileNotFoundError:
        return {**base, "state": "empty"}
    except sqlite3.Error:
        return {**base, "state": "error"}
    try:
        start = now - days * 86400
        rows = con.execute("SELECT key, ts, used, resets_at, source FROM limit_samples WHERE ts >= ? ORDER BY ts", (start,)).fetchall()
        wk_rows = con.execute("SELECT ts, used, resets_at FROM limit_samples WHERE key='seven_day' ORDER BY ts").fetchall()
        since = con.execute("SELECT v FROM meta WHERE k='collecting_since'").fetchone()
        last_sd = con.execute("SELECT ts, used, resets_at, source FROM limit_samples WHERE key='seven_day' ORDER BY ts DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        return {**base, "state": "error"}
    finally:
        con.close()
    if not rows and not wk_rows:
        return {**base, "state": "empty"}
    by = {k: [] for k in KEYS}
    for r in rows:
        by[r["key"]].append((r["ts"], r["used"]))
    series = {k: ({"label": LABELS[k], "points": _points(v, start, now)} if v else None) for k, v in by.items()}
    wks = [w for w in weeks([(r["ts"], r["used"], r["resets_at"]) for r in wk_rows], now) if w["last_sample"] >= start][-MAX_WEEKS:]
    first = rows[0]["ts"] if rows else wk_rows[0]["ts"]
    try:
        collecting = int(float(since["v"])) if since else int(first)
    except (TypeError, ValueError):
        collecting = int(first)
    current = None
    if last_sd is not None:
        current = {"used": round(last_sd["used"], 1), "resets_at": None if last_sd["resets_at"] is None else int(last_sd["resets_at"]),
                   "updated": int(last_sd["ts"]), "source": last_sd["source"], "stale": now - last_sd["ts"] > CURRENT_STALE_SECS}
    return {**base, "collecting_since": collecting, "series": series, "resets": [w["resets_at"] for w in wks], "weeks": wks, "current": current}


_memo = {}
_memo_lock = threading.Lock()


def history_cached(days=None, now=None):
    """history() memoised per `days` for 60 s, so polling browsers cost one query a minute."""
    days = clamp_days(days if days is not None else DAYS_DEFAULT)
    t = time.time() if now is None else now
    with _memo_lock:
        hit = _memo.get(days)
        if hit and 0 <= t - hit[0] < MEMO_SECS:
            return hit[1]
    payload = history(days, t)
    with _memo_lock:
        _memo[days] = (t, payload)
    return payload


# ---------- token statistics reader ----------

def clamp_range(v):
    return v if v in RANGES else "7d"


def _shares(totals):
    """Percentages with one decimal that add up to exactly 100.0 (largest remainder); all zero when there is nothing."""
    s = sum(totals)
    if s <= 0:
        return [0.0] * len(totals)
    raw = [t * 1000 / s for t in totals]
    fl = [int(math.floor(x)) for x in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - fl[i], reverse=True)[:1000 - sum(fl)]:
        fl[i] += 1
    return [x / 10 for x in fl]


def _midnight(ts):
    d = datetime.fromtimestamp(ts)
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def _starts(now):
    """Local-midnight starts for today / 7d / 30d (the last two cover 7 and 30 calendar days including today)."""
    m = _midnight(now)
    return {"today": m.timestamp(), "7d": (m - timedelta(days=6)).timestamp(), "30d": (m - timedelta(days=29)).timestamp()}


def _empty_tokens(state, rng, now, domain=""):
    st = _starts(now)
    return {"state": state, "range": rng, "since": int(st[rng]), "now": int(now), "tz": _tz_name(), "domain": domain,
            "kpis": {r: {"total": 0, "output": 0} for r in RANGES},
            "totals": {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "total": 0, "messages": 0, "sessions": 0},
            "by_agent": [], "by_model": [], "matrix": {"agents": [], "models": [], "total": []}, "daily": [],
            "coverage": {"oldest": None, "last_scan": None, "scanning": False, "files_done": 0, "files_total": 0, "corrupt_lines": 0, "bytes_pending": 0}}


def _kinds(i, o, w, r):
    return {"input": i, "output": o, "cache_write": w, "cache_read": r, "total": i + o + w + r}


def _fold(rows, limit):
    """Top `limit` rows by total, the rest folded into one 'other (n)' row (kept as the last row)."""
    rows = sorted(rows, key=lambda x: (-x["total"], x["name"]))
    if len(rows) <= limit:
        return rows
    rest = rows[limit:]
    other = {"name": f"other ({len(rest)})", "family": "other", "messages": sum(x["messages"] for x in rest), "sessions": set().union(*(x["sessions"] for x in rest))}
    for k in ("input", "output", "cache_write", "cache_read", "total"):
        other[k] = sum(x[k] for x in rest)
    return rows[:limit] + [other]


def tokens(range_=None, domain=None, now=None):
    """Payload of GET /api/usage/tokens (read-only). Never raises; failures give a fixed `error` state. No paths, no titles, no text."""
    now = time.time() if now is None else now
    rng = clamp_range(range_)
    domain = domain if isinstance(domain, str) and RE_DOMAIN_Q.fullmatch(domain) else ""
    if tokens_disabled():
        return _empty_tokens("disabled", rng, now)
    try:
        con = connect(readonly=True)
    except FileNotFoundError:
        return _empty_tokens("empty", rng, now)
    except sqlite3.Error:
        return _empty_tokens("error", rng, now)
    try:
        try:
            doms = {r[0] for r in con.execute("SELECT DISTINCT COALESCE(domain, ?) FROM tok_files", (OUTSIDE,))}
        except sqlite3.OperationalError:
            return _empty_tokens("empty", rng, now)          # a version 1 file: the scanner has not run yet
        if domain not in doms:
            domain = ""
        out = _empty_tokens("ok", rng, now, domain)
        st = _starts(now)
        since, until = st[rng], now + 1
        dom_sql, dom_arg = (" AND COALESCE(f.domain, ?) = ?", [OUTSIDE, domain]) if domain else ("", [])
        base = "FROM tok_msgs t JOIN tok_files f ON f.id = t.file_id JOIN tok_models m ON m.id = t.model_id WHERE t.ts >= ? AND t.ts < ?" + dom_sql

        k = con.execute("SELECT " + ", ".join(f"COALESCE(SUM(CASE WHEN t.ts >= {float(st[r])!r} THEN t.inp+t.outp+t.cw+t.cr END),0),"
                                              f"COALESCE(SUM(CASE WHEN t.ts >= {float(st[r])!r} THEN t.outp END),0)" for r in RANGES)
                        + " " + base, [st["30d"], until, *dom_arg]).fetchone()
        for i, r in enumerate(RANGES):
            out["kpis"][r] = {"total": int(k[2 * i]), "output": int(k[2 * i + 1])}

        agents, models, cells, tot, msgs = {}, {}, {}, [0, 0, 0, 0], 0
        for a, mn, fam, i, o, w, r_, n in con.execute(
                "SELECT COALESCE(f.agent_type, 'subagent'), m.name, m.family, SUM(t.inp), SUM(t.outp), SUM(t.cw), SUM(t.cr), COUNT(*) " + base
                + " GROUP BY COALESCE(f.agent_type, 'subagent'), t.model_id", [since, until, *dom_arg]):
            v = _kinds(i, o, w, r_)
            for d, key, extra in ((agents, a, {}), (models, mn, {"family": fam})):
                e = d.setdefault(key, {"name": key, "family": "other", "input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "total": 0,
                                       "messages": 0, "sessions": set(), **extra})
                for kk, vv in v.items():
                    e[kk] += vv
                e["messages"] += n
            cells[(a, mn)] = v["total"]
            tot = [tot[0] + i, tot[1] + o, tot[2] + w, tot[3] + r_]
            msgs += n
        sessions = set()
        for a, mn, sid in con.execute("SELECT DISTINCT COALESCE(f.agent_type, 'subagent'), m.name, f.session_id " + base, [since, until, *dom_arg]):
            sessions.add(sid)
            if a in agents:
                agents[a]["sessions"].add(sid)
            if mn in models:
                models[mn]["sessions"].add(sid)
        out["totals"] = {**_kinds(*tot), "messages": msgs, "sessions": len(sessions)}

        arows = _fold(list(agents.values()), TOP_AGENTS)
        mrows = _fold(list(models.values()), TOP_MODELS)
        for rows, key in ((arows, "by_agent"), (mrows, "by_model")):
            for row, sh in zip(rows, _shares([x["total"] for x in rows])):
                row["share"] = sh
        out["by_agent"] = [{"agent_type": x["name"], **{k: x[k] for k in ("input", "output", "cache_write", "cache_read", "total", "share", "messages")},
                            "sessions": len(x["sessions"])} for x in arows]
        out["by_model"] = [{"model": x["name"], "family": x["family"], **{k: x[k] for k in ("input", "output", "cache_write", "cache_read", "total", "share")}} for x in mrows]

        # matrix: at most 12 agent rows x 8 model columns; rows past the limit are folded into 'other (n)' so the table still adds up
        def mfold(keys, totals, limit):
            ranked = sorted(keys, key=lambda x: (-totals[x], x))
            return (ranked, {x: x for x in ranked}) if len(ranked) <= limit else (
                ranked[:limit - 1] + [f"other ({len(ranked) - limit + 1})"], {**{x: x for x in ranked[:limit - 1]}, **{x: f"other ({len(ranked) - limit + 1})" for x in ranked[limit - 1:]}})
        a_tot = {x: agents[x]["total"] for x in agents}
        m_tot = {x: models[x]["total"] for x in models}
        ak, amap = mfold(list(agents), a_tot, TOP_AGENTS)
        mk, mmap = mfold(list(models), m_tot, TOP_MODELS)
        grid = [[0] * len(mk) for _ in ak]
        for (a, mn), v in cells.items():
            grid[ak.index(amap[a])][mk.index(mmap[mn])] += v
        out["matrix"] = {"agents": ak, "models": mk, "total": grid}

        # daily buckets by local day (hours for 'today'), stacked by model family
        fmt = "%Y-%m-%d %H" if rng == "today" else "%Y-%m-%d"
        got = {}
        for key, fam, n in con.execute("SELECT strftime(?, t.ts, 'unixepoch', 'localtime'), m.family, SUM(t.inp+t.outp+t.cw+t.cr) " + base + " GROUP BY 1, 2",
                                       [fmt, since, until, *dom_arg]):
            got.setdefault(key, {})[fam if fam in FAMILIES else "other"] = int(n)
        daily = []
        if rng == "today":
            m0 = _midnight(now)
            slots = [(m0 + timedelta(hours=h), "%Y-%m-%d %H", lambda d: d.strftime("%H:00")) for h in range(24)]
        else:
            n_days = 7 if rng == "7d" else 30
            m0 = _midnight(now) - timedelta(days=n_days - 1)
            slots = [(m0 + timedelta(days=d), "%Y-%m-%d", lambda d: f"{d.strftime('%a')} {d.day}") for d in range(n_days)]
        for dt, kf, lf in slots:
            fam = {f: got.get(dt.strftime(kf), {}).get(f, 0) for f in FAMILIES}
            daily.append({"t": int(dt.timestamp()), "label": lf(dt), "by_family": fam, "total": sum(fam.values())})
        out["daily"] = daily

        meta = dict(con.execute("SELECT k, v FROM meta WHERE k IN ('last_scan','scan_progress','corrupt_lines')").fetchall())
        try:
            prog = json.loads(meta.get("scan_progress") or "{}")
        except ValueError:
            prog = {}
        oldest = con.execute("SELECT MIN(ts) FROM tok_msgs").fetchone()[0]
        has_any = con.execute("SELECT 1 FROM tok_msgs LIMIT 1").fetchone() is not None
        scanning = bool(prog.get("scanning"))
        def _n(v):
            return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else 0
        try:
            last_scan = int(float(meta["last_scan"])) if "last_scan" in meta else None
        except (TypeError, ValueError):
            last_scan = None
        out["coverage"] = {"oldest": None if oldest is None else int(oldest), "last_scan": last_scan, "scanning": scanning,
                           "files_done": _n(prog.get("files_done")), "files_total": _n(prog.get("files_total")),
                           "bytes_pending": _n(prog.get("bytes_pending")), "corrupt_lines": _n(int(meta["corrupt_lines"])) if str(meta.get("corrupt_lines", "")).isdigit() else 0}
        if scanning:
            out["state"] = "scanning"
        elif not has_any:
            out["state"] = "empty"
        return out
    except sqlite3.Error:
        return _empty_tokens("error", rng, now)
    finally:
        con.close()


_tok_memo = {}


def tokens_cached(range_=None, domain=None, now=None):
    """tokens() memoised per (range, domain) for 60 s; the memo is small and bounded."""
    rng = clamp_range(range_)
    dom = domain if isinstance(domain, str) and RE_DOMAIN_Q.fullmatch(domain) else ""
    t = time.time() if now is None else now
    key = (rng, dom)
    with _memo_lock:
        hit = _tok_memo.get(key)
        if hit and 0 <= t - hit[0] < MEMO_SECS:
            return hit[1]
    payload = tokens(rng, dom, t)
    with _memo_lock:
        if len(_tok_memo) >= 64:
            _tok_memo.clear()
        _tok_memo[key] = (t, payload)
    return payload
