"""Claude plan-usage history for the dashboard (stdlib only).

`usage.db` (next to events.db, mode 0600) keeps samples of the 5-hour and weekly limits so the Events tab can draw the
weekly graph. The only writer is the recorder thread started by `niu-dash serve`; HTTP requests only read, through
read-only connections. Nothing secret is ever stored: the rows hold a limit key, a time, a percentage and a reset time.
"""
import math
import os
import sqlite3
import threading
import time
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

SCHEMA = """
CREATE TABLE IF NOT EXISTS limit_samples (
  key TEXT NOT NULL CHECK (key IN ('five_hour','seven_day','seven_day_opus','seven_day_sonnet')),
  ts REAL NOT NULL,
  used REAL NOT NULL CHECK (used BETWEEN 0 AND 100),
  resets_at REAL,
  source TEXT NOT NULL CHECK (source IN ('api','statusline')),
  PRIMARY KEY (key, ts)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS limit_samples_ts ON limit_samples(ts);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

# Fixed, content-free summaries of what went wrong; exception text is never exposed.
ERR_NONE, ERR_READ, ERR_DB, ERR_OTHER = "none", "reading", "database", "other"
_stats = {"errors": 0, "last": ERR_NONE, "state": "stopped"}
_stats_lock = threading.Lock()


def collect_disabled():
    return os.environ.get("NIU_USAGE_COLLECT") == "0"


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
        if con.execute("PRAGMA user_version").fetchone()[0] == 0:
            if not con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
                con.execute("PRAGMA auto_vacuum=INCREMENTAL")  # only effective before the first table exists
            con.execute("PRAGMA user_version=1")
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
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
    con.commit()
    if n:
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

    def __init__(self, interval=SAMPLE_EVERY, reading=None, clock=time.time, stop=None):
        super().__init__(name="niu-usage-recorder", daemon=True)
        self.interval, self.clock = interval, clock
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

    def run(self):
        with _stats_lock:
            _stats["state"] = "running"
        try:
            while not self.stop_event.is_set():
                self.tick()
                if self.stop_event.wait(self.interval):
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
