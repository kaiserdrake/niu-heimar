"""Hub-side validation and storage of rows pushed by a satellite machine (stdlib only).

Everything here is pure apart from the DB writes: the satellite is untrusted, so every field is checked against an
allow-list, summaries are redacted again, `host` is the label the hub assigned (never read from a row), and a
session id owned by another host (or by this machine) is never overwritten. Each ingest_* returns
{"accepted": n, "skipped": m}.
"""
import hashlib
import json
import math
import re
import time
import unicodedata

import niu_events as ne
from niu_events import DESIGN_MAX_BYTES, DESIGN_NAME_RE, NAME_RE, SNAPSHOT_KEEP, redact
from niu_state import SYSTEM_SESSIONS

EVENTS = frozenset("SessionStart SessionEnd UserPromptSubmit PreToolUse PostToolUse SubagentStart SubagentStop Stop Notification".split())
MAX_ROWS = {"events": 500, "tokens": 2000, "designs": 1, "snapshots": 50}
TS_PAST, TS_FUTURE = 8 * 86400, 300
SNAP_PAST = 31 * 86400
MAX_SRC_ID = 2 ** 53

RE_LABEL = re.compile(r"[A-Za-z0-9_-]{1,32}")
RE_SID = re.compile(r"[A-Za-z0-9_-]{1,80}")  # same as bin/niu-dash
RE_TOOL = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
RE_DOMAIN = re.compile(r"[A-Za-z0-9_()-]{1,40}")
RE_STATUS = re.compile(r"[A-Za-z0-9_.-]{1,20}")
RE_SHA = re.compile(r"[0-9a-f]{64}")
_SEG = r"[A-Za-z0-9_()][A-Za-z0-9_.()-]{0,63}"
RE_REL = re.compile(rf"(?:{_SEG}/){{0,5}}{re.escape(ne.PROPOSALS_DIR)}/[^/]+")
_MODEL_RE = ne._MODEL_RE


def label_ok(label, hub_label) -> bool:
    """A machine label: [A-Za-z0-9_-]{1,32} and not the hub's own label (case-insensitive)."""
    return (isinstance(label, str) and bool(RE_LABEL.fullmatch(label))
            and label.lower() != str(hub_label or "").lower())


def _need_label(label):
    if not isinstance(label, str) or not RE_LABEL.fullmatch(label):
        raise ValueError("bad machine label")


def _clean_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))


def _line(text, cap):
    """One redacted line: control characters dropped, redacted, capped."""
    text = "".join(" " if c in "\r\n\t" else c for c in text)
    text = "".join(c for c in text if unicodedata.category(c)[0] != "C")
    return redact(text)[:cap]


def _int(v, lo=0, hi=MAX_SRC_ID):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def rel_path(v, cap=200):
    """A workspace-relative path with no '..', not absolute, no control characters; '' allowed. None if invalid."""
    if not isinstance(v, str) or len(v) > cap or v.startswith("/") or "\\" in v or ".." in v:
        return None
    if any(unicodedata.category(c).startswith("C") for c in v):
        return None
    return v


def design_rel_ok(rel) -> bool:
    """A design doc path: [<domain>/<project>/...]/.claude-proposals/<name>.md; never WORK, '..' or a dotenv name."""
    if not isinstance(rel, str) or len(rel) > 400 or ".." in rel or not RE_REL.fullmatch(rel):
        return False
    parts = rel.split("/")
    name = parts[-1]
    if not DESIGN_NAME_RE.fullmatch(name) or name.lower().startswith("." + "env"):
        return False
    return parts[0].upper() != "WORK"


def _summary(event, raw):
    if not isinstance(raw, str) or not raw:
        return ""
    if event in ("SessionStart", "UserPromptSubmit", "Stop", "SubagentStop"):
        # JSON the hook builds: rebuild it from the known keys only
        if not raw.startswith("{"):
            return ""
        try:
            d = json.loads(raw)
        except ValueError:
            return ""
        if not isinstance(d, dict):
            return ""
        caps = {"title": 80, "model": 60, "source": 40, "local": 60, "task": 120}
        if event in ("Stop", "SubagentStop"):
            caps = {"model": 60}
        out = {}
        for k, cap in caps.items():
            v = d.get(k)
            if isinstance(v, str):
                v = _line(v, cap).strip()
                if k == "model" and not _MODEL_RE.fullmatch(v):
                    continue
                if v:
                    out[k] = v
        return json.dumps(out, separators=(",", ":")) if out else ""
    return _line(raw, 120)


def valid_event(row, now=None):
    """The cleaned event dict (project_dir kept as `pdir`, unprefixed) or None."""
    if not isinstance(row, dict):
        return None
    now = time.time() if now is None else now
    ev, sid, ts = row.get("event"), row.get("session_id"), row.get("ts")
    if ev not in EVENTS or not isinstance(sid, str) or not RE_SID.fullmatch(sid) or sid in SYSTEM_SESSIONS:
        return None
    if not _num(ts) or not (now - TS_PAST <= ts <= now + TS_FUTURE) or not _int(row.get("src_id")):
        return None
    dom = row.get("domain")
    if not isinstance(dom, str) or not RE_DOMAIN.fullmatch(dom):
        return None
    out = {"ts": float(ts), "event": ev, "session_id": sid, "domain": dom, "src_id": row["src_id"]}
    out["project"] = rel_path(row.get("project") or "")
    out["pdir"] = rel_path(row.get("project_dir") or "")
    if out["project"] is None or out["pdir"] is None:
        return None
    for k, rx in (("agent_id", NAME_RE), ("agent_type", NAME_RE), ("tool", RE_TOOL), ("tool_use_id", NAME_RE)):
        v = row.get(k)
        if v in (None, ""):
            out[k] = None
        elif isinstance(v, str) and rx.fullmatch(v):
            out[k] = v
        else:
            return None
    out["summary"] = _summary(ev, row.get("summary"))
    return out


def _slice(rows, kind):
    """(rows within the per-request cap, number of rows over the cap)."""
    if not isinstance(rows, list):
        return [], 0
    cap = MAX_ROWS[kind]
    return rows[:cap], max(0, len(rows) - cap)


def _owned_elsewhere(con, label, sid, cache):
    if sid not in cache:
        cache[sid] = con.execute("SELECT 1 FROM events WHERE session_id=? AND (host IS NULL OR host<>?) LIMIT 1",
                                 (sid, label)).fetchone() is not None
    return cache[sid]


def ingest_events(con, label, rows, now=None):
    _need_label(label)
    rows, skip = _slice(rows, "events")
    acc, cache = 0, {}
    with con:
        for r in rows:
            e = valid_event(r, now)
            if e is None or _owned_elsewhere(con, label, e["session_id"], cache):
                skip += 1
                continue
            cur = con.execute(
                "INSERT OR IGNORE INTO events(ts,event,session_id,domain,project,project_dir,agent_id,agent_type,tool,tool_use_id,summary,host,src_id)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (e["ts"], e["event"], e["session_id"], e["domain"], e["project"], f"{label}:{e['pdir']}", e["agent_id"],
                 e["agent_type"], e["tool"], e["tool_use_id"], e["summary"], label, e["src_id"]))
            if cur.rowcount:
                acc += 1
                cache[e["session_id"]] = False
            else:
                skip += 1
    return {"accepted": acc, "skipped": skip}


def _doc(row):
    """Validate rel/sha/content of a design or snapshot row. Returns (rel, sha, cleaned text) or None."""
    rel, sha, content = row.get("rel"), row.get("sha"), row.get("content")
    if not design_rel_ok(rel) or not isinstance(sha, str) or not RE_SHA.fullmatch(sha) or not isinstance(content, str):
        return None
    if len(content) > DESIGN_MAX_BYTES:
        return None
    try:
        text = _clean_text(content)
        raw = text.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if len(raw) > DESIGN_MAX_BYTES or hashlib.sha256(raw).hexdigest() != sha:
        return None
    return rel, sha, text


def ingest_designs(con, label, rows, now=None):
    _need_label(label)
    rows, skip = _slice(rows, "designs")
    acc, now = 0, time.time() if now is None else now
    with con:
        for r in rows:
            if not isinstance(r, dict):
                skip += 1
                continue
            if r.get("deleted") is True:
                if not design_rel_ok(r.get("rel")):
                    skip += 1
                    continue
                con.execute("DELETE FROM remote_designs WHERE host=? AND rel=?", (label, r["rel"]))
                acc += 1
                continue
            d, mt = _doc(r), r.get("mtime")
            if d is None or not _num(mt) or not (0 <= mt <= now + TS_FUTURE):
                skip += 1
                continue
            title = r.get("title") if isinstance(r.get("title"), str) else ""
            status = r.get("status") if isinstance(r.get("status"), str) else ""
            status = status if RE_STATUS.fullmatch(status) else ""
            con.execute("INSERT OR REPLACE INTO remote_designs(host,rel,sha,mtime,title,status,content,seen) VALUES(?,?,?,?,?,?,?,?)",
                        (label, d[0], d[1], float(mt), _line(title, 200), status, d[2], now))
            acc += 1
    return {"accepted": acc, "skipped": skip}


def ingest_snapshots(con, label, rows, now=None):
    _need_label(label)
    rows, skip = _slice(rows, "snapshots")
    acc, now = 0, time.time() if now is None else now
    cache, touched = {}, set()
    with con:
        for r in rows:
            d = _doc(r) if isinstance(r, dict) else None
            ts = r.get("ts") if d else None
            if d is None or not _int(r.get("src_id")) or not _num(ts) or not (now - SNAP_PAST <= ts <= now + TS_FUTURE):
                skip += 1
                continue
            sid = r.get("session_id")
            if sid in ("", None):
                sid = None
            elif not isinstance(sid, str) or not RE_SID.fullmatch(sid):
                skip += 1
                continue
            elif sid in SYSTEM_SESSIONS or _owned_elsewhere(con, label, sid, cache):
                sid = None
            agent, tool = r.get("agent_type"), r.get("tool")
            agent = agent if isinstance(agent, str) and NAME_RE.fullmatch(agent) else ""
            tool = tool if isinstance(tool, str) and RE_TOOL.fullmatch(tool) else ""
            cur = con.execute("INSERT OR IGNORE INTO design_snapshots(ts,rel,sha,session_id,agent_type,tool,content,host,src_id)"
                              " VALUES(?,?,?,?,?,?,?,?,?)", (float(ts), d[0], d[1], sid, agent, tool, d[2], label, r["src_id"]))
            if cur.rowcount:
                acc += 1
                touched.add(d[0])
            else:
                skip += 1
        for rel in touched:
            con.execute("DELETE FROM design_snapshots WHERE host=? AND rel=? AND id NOT IN "
                        "(SELECT id FROM design_snapshots WHERE host=? AND rel=? ORDER BY ts DESC, id DESC LIMIT ?)",
                        (label, rel, label, rel, SNAPSHOT_KEEP))
        con.execute("DELETE FROM design_snapshots WHERE host=? AND ts < ?", (label, now - SNAP_PAST))
    return {"accepted": acc, "skipped": skip}


def ingest_tokens(label, rows):
    """Validation and storage live in niu_usage.ingest_rows(label, rows); this only caps the row count."""
    _need_label(label)
    import niu_usage
    rows, extra = _slice(rows, "tokens")
    res = dict(niu_usage.ingest_rows(label, rows) or {})
    res["accepted"] = int(res.get("accepted", 0))
    res["skipped"] = int(res.get("skipped", 0)) + extra
    return res


def cursor(con, label):
    """The hub's high-water marks for a machine: max src_id of its events and snapshots (0 = none)."""
    _need_label(label)
    out = {}
    for key, table in (("events", "events"), ("snapshots", "design_snapshots")):
        v = con.execute(f"SELECT MAX(src_id) FROM {table} WHERE host=?", (label,)).fetchone()[0]
        out[key] = int(v or 0)
    return out
