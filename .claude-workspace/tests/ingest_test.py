#!/usr/bin/env python3
"""Tests for niu_ingest (hub-side validation/storage of rows pushed by a satellite) and the events.db migration."""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ["NIU_EVENTS_DB"] = str(Path(tmp) / "events.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import niu_events as ne  # noqa: E402
import niu_ingest as ni  # noqa: E402
import niu_usage  # noqa: E402

passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


NOW = time.time()
L = "Vanaheim"


def ev(src, event="PreToolUse", sid="sat1", **kw):
    r = {"src_id": src, "ts": NOW - 5, "event": event, "session_id": sid, "domain": "PERSONAL", "project": "app",
         "project_dir": "PERSONAL/app", "agent_id": None, "agent_type": None, "tool": "Bash", "tool_use_id": "tu1", "summary": "ls"}
    r.update(kw)
    return r


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def doc(rel="PERSONAL/app/.claude-proposals/x.md", text="# T\nStatus: proposed\n", **kw):
    r = {"rel": rel, "sha": sha(text), "mtime": NOW - 10, "title": "T", "status": "proposed", "content": text}
    r.update(kw)
    return r


# ---------- migration of an old-schema DB, in place ----------
old = Path(tmp) / "old.db"
c = sqlite3.connect(old)
c.executescript("""
CREATE TABLE events (id INTEGER PRIMARY KEY, ts REAL NOT NULL, event TEXT NOT NULL, session_id TEXT, domain TEXT,
  project TEXT, project_dir TEXT, agent_id TEXT, agent_type TEXT, tool TEXT, tool_use_id TEXT, summary TEXT);
CREATE TABLE design_snapshots (id INTEGER PRIMARY KEY, ts REAL NOT NULL, rel TEXT NOT NULL, sha TEXT NOT NULL,
  session_id TEXT, agent_type TEXT, tool TEXT, content TEXT NOT NULL);
INSERT INTO events(ts,event,session_id,domain) VALUES(1,'Stop','old1','PERSONAL');
INSERT INTO events(ts,event,session_id,domain,project,tool,summary) VALUES(2,'PreToolUse','old1','WORK','p','Bash','ls');
INSERT INTO events(ts,event,session_id,domain) VALUES(3,'SessionStart','old2','HOBBY');
INSERT INTO design_snapshots(ts,rel,sha,content) VALUES(1,'a/.claude-proposals/z.md','x','c');
INSERT INTO design_snapshots(ts,rel,sha,content) VALUES(2,'a/.claude-proposals/y.md','y','d');
""")
c.commit()
c.close()
os.environ["NIU_EVENTS_DB"] = str(old)
con = ne.connect()
cols = {r[1] for r in con.execute("PRAGMA table_info(events)")}
check({"host", "src_id"} <= cols, f"events migrated in place: {cols}")
check("host" in {r[1] for r in con.execute("PRAGMA table_info(design_snapshots)")}, "design_snapshots.host added")
idx = {r[1] for r in con.execute("PRAGMA index_list(events)")}
check({"events_host_src", "events_host"} <= idx, f"indexes: {idx}")
check(con.execute("PRAGMA user_version").fetchone()[0] == ne.SCHEMA_VERSION, "user_version set")
check(con.execute("SELECT COUNT(*), MAX(host) FROM events").fetchone()[:] == (3, None), "old rows kept with host NULL")
check([tuple(r) for r in con.execute("SELECT ts, event, session_id, domain, host, src_id FROM events ORDER BY ts")]
      == [(1, "Stop", "old1", "PERSONAL", None, None), (2, "PreToolUse", "old1", "WORK", None, None),
          (3, "SessionStart", "old2", "HOBBY", None, None)], "old events intact, host and src_id NULL")
check([tuple(r) for r in con.execute("SELECT rel, sha, content, host, src_id FROM design_snapshots ORDER BY ts")]
      == [("a/.claude-proposals/z.md", "x", "c", None, None), ("a/.claude-proposals/y.md", "y", "d", None, None)],
      "old design_snapshots intact, host and src_id NULL")
dcols = {r[1] for r in con.execute("PRAGMA table_info(design_snapshots)")}
check({"host", "src_id"} <= dcols, f"design_snapshots migrated: {dcols}")
didx = {r[1] for r in con.execute("PRAGMA index_list(design_snapshots)")}
check({"design_snapshots_host_src", "design_snapshots_host_rel"} <= didx, f"design_snapshots indexes: {didx}")
check(con.execute("SELECT 1 FROM remote_designs LIMIT 1").fetchone() is None, "remote_designs table exists")
con.close()
con = ne.connect()  # second connect is a no-op
check(con.execute("PRAGMA user_version").fetchone()[0] == ne.SCHEMA_VERSION, "re-connect keeps the version")
check(con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3 and con.execute("SELECT COUNT(*) FROM design_snapshots").fetchone()[0] == 2,
      "re-connect keeps the row counts")
con.close()
os.environ["NIU_EVENTS_DB"] = str(Path(tmp) / "events.db")
con = ne.connect()

# ---------- helpers ----------
check(ne.show_domain("(workspace)", "Vana") == "Vana" and ne.show_domain("(workspace)") == ne.host_label()
      and ne.show_domain("WORK", "x") == "WORK", "show_domain host arg")
check(ne.is_remote({"host": "A"}) and not ne.is_remote({"host": None}) and not ne.is_remote({}) and not ne.is_remote({"host": ""}),
      "is_remote on dicts")
con.execute("INSERT INTO events(ts,event,host) VALUES(1,'Stop','H')")
row = con.execute("SELECT host FROM events").fetchone()
check(ne.is_remote(row), "is_remote on sqlite Row")
con.execute("DELETE FROM events")
con.commit()
check(ni.label_ok("Vanaheim", "Midgard") and not ni.label_ok("midgard", "Midgard") and not ni.label_ok("a b", "x")
      and not ni.label_ok("", "x") and not ni.label_ok("a" * 33, "x") and not ni.label_ok(None, "x"), "label_ok")

# ---------- events ----------
r = ni.ingest_events(con, L, [ev(1), ev(2, "SessionStart", summary=json.dumps({"title": "hi", "model": "claude-x", "evil": "y", "source": "startup"}))])
check(r == {"accepted": 2, "skipped": 0}, f"good events accepted: {r}")
s = con.execute("SELECT * FROM events WHERE src_id=2").fetchone()
check(s["host"] == L and s["project_dir"] == "Vanaheim:PERSONAL/app", f"host/project_dir assigned: {dict(s)}")
check(json.loads(s["summary"]) == {"title": "hi", "model": "claude-x", "source": "startup"}, f"SessionStart summary rebuilt: {s['summary']}")
check(ni.ingest_events(con, L, [ev(1), ev(2)]) == {"accepted": 0, "skipped": 2}, "re-send is idempotent")
check(con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2, "no duplicate rows")
check(ni.cursor(con, L) == {"events": 2, "snapshots": 0}, "cursor after events")
check(ni.cursor(con, "Other") == {"events": 0, "snapshots": 0}, "cursor of another machine")

bad = [ev(10, "Bogus"), ev(11, "PRMergeDone"), ev(12, sid="web"), ev(13, sid="bad sid!"), ev(14, ts=NOW - 9 * 86400),
       ev(15, ts=NOW + 4000), ev(16, project="../x"), ev(17, project_dir="/abs/path"), ev(18, project_dir="a/../b"),
       ev(19, tool="a b"), ev(20, domain="bad dom"), ev("x"), ev(22, agent_id="../a"), ev(23, ts="now"), "notadict",
       ev(None), ev(True), ev(-1)]
r = ni.ingest_events(con, L, bad)
check(r["accepted"] == 0 and r["skipped"] == len(bad), f"bad events rejected: {r}")
# PRMerge variants must not slip through under the allow-list
check(con.execute("SELECT COUNT(*) FROM events WHERE event LIKE 'PRMerge%'").fetchone()[0] == 0, "no PRMerge stored")

ni.ingest_events(con, L, [ev(30, "Notification", summary="token ghp_abcdefghijklmnopqrstuvwxyz0123 leaked " + "y" * 300)])
s = con.execute("SELECT summary FROM events WHERE src_id=30").fetchone()[0]
check("ghp_" not in s and len(s) <= 120, f"summary re-redacted and capped: {s!r}")
ni.ingest_events(con, L, [ev(31, summary="Authorization: Bearer abcdef\x07\x1b[31m")])
s = con.execute("SELECT summary FROM events WHERE src_id=31").fetchone()[0]
check("abcdef" not in s and "\x1b" not in s and "\x07" not in s, f"headers/control chars stripped: {s!r}")
ni.ingest_events(con, L, [ev(32, "Stop", summary="not json")])
check(con.execute("SELECT summary FROM events WHERE src_id=32").fetchone()[0] == "", "Stop non-JSON summary dropped")

# many rows: capped
many = [ev(1000 + i) for i in range(ni.MAX_ROWS["events"] + 7)]
r = ni.ingest_events(con, L, many)
check(r["accepted"] == ni.MAX_ROWS["events"] and r["skipped"] == 7, f"row cap: {r}")

# ownership: hub session and other-host session are skipped
con.execute("INSERT INTO events(ts,event,session_id,domain,host) VALUES(?,?,?,?,NULL)", (NOW, "SessionStart", "hubsid", "PERSONAL"))
con.commit()
r = ni.ingest_events(con, L, [ev(40, sid="hubsid"), ev(41, event="SessionEnd", sid="hubsid")])
check(r["accepted"] == 0 and r["skipped"] == 2, f"hub sid skipped: {r}")
ni.ingest_events(con, "Other", [ev(1, sid="othersid")])
r = ni.ingest_events(con, L, [ev(42, sid="othersid")])
check(r["accepted"] == 0, f"other-host sid skipped: {r}")
r = ni.ingest_events(con, "Other", [ev(2, sid="othersid")])
check(r["accepted"] == 1, "owner can continue its session")
check(con.execute("SELECT COUNT(*) FROM events WHERE session_id='hubsid'").fetchone()[0] == 1, "hub session untouched")
# same src_id under two hosts is fine
check(con.execute("SELECT COUNT(*) FROM events WHERE src_id=1").fetchone()[0] == 2, "(host, src_id) unique per host")
try:
    ni.ingest_events(con, "bad label", [ev(1)])
    check(False, "bad label raises")
except ValueError:
    check(True, "bad label raises")

# ---------- designs ----------
r = ni.ingest_designs(con, L, [doc()])
check(r == {"accepted": 1, "skipped": 0}, f"design accepted: {r}")
d = con.execute("SELECT * FROM remote_designs").fetchone()
check(d["host"] == L and d["title"] == "T" and d["status"] == "proposed", f"design stored: {dict(d)}")
ni.ingest_designs(con, L, [doc(text="# T2\nmore\n")])
check(con.execute("SELECT COUNT(*) FROM remote_designs").fetchone()[0] == 1
      and con.execute("SELECT title FROM remote_designs").fetchone()[0] == "T", "upsert by (host, rel)")
ni.ingest_designs(con, L, [doc(title="T2", text="# T2\nmore\n")])
check(con.execute("SELECT sha FROM remote_designs").fetchone()[0] == sha("# T2\nmore\n"), "design updated")
ni.ingest_designs(con, "Other", [doc()])
check(con.execute("SELECT COUNT(*) FROM remote_designs").fetchone()[0] == 2, "same rel on two hosts")
cases = {
    "sha mismatch": doc(sha="0" * 64),
    "sha upper/short": doc(sha="abc"),
    "dotdot rel": doc(rel="PERSONAL/../x/.claude-proposals/a.md"),
    "absolute rel": doc(rel="/etc/.claude-proposals/a.md"),
    "env name": doc(rel="PERSONAL/.claude-proposals/." + "env.md"),
    "bad name": doc(rel="PERSONAL/.claude-proposals/a.txt"),
    "subfolder": doc(rel="PERSONAL/.claude-proposals/sub/a.md"),
    "not proposals": doc(rel="PERSONAL/docs/design/a.md"),
    "WORK": doc(rel="WORK/app/.claude-proposals/a.md"),
    "work lower": doc(rel="work/.claude-proposals/a.md"),
    "oversize": doc(text="x" * (ni.DESIGN_MAX_BYTES + 1)),
    "no content": {**doc(), "content": None},
    "bad mtime": doc(mtime="x"),
}
for name, row in cases.items():
    r = ni.ingest_designs(con, L, [row])
    check(r["accepted"] == 0 and r["skipped"] == 1, f"design rejected ({name}): {r}")
# control chars stripped, sha computed over the cleaned text
dirty = "# C\x00ln\x1b\nx\n"
clean = "# Cln\nx\n"
r = ni.ingest_designs(con, L, [doc(rel=".claude-proposals/c.md", text=clean, content=dirty)])
check(r["accepted"] == 1, "control chars stripped before sha check")
check(con.execute("SELECT content FROM remote_designs WHERE rel='.claude-proposals/c.md'").fetchone()[0] == clean, "stored content is clean")
r = ni.ingest_designs(con, L, [doc(), doc("PERSONAL/b/.claude-proposals/b.md")])
check(r["accepted"] == 1 and r["skipped"] == 1, f"one doc per request: {r}")
r = ni.ingest_designs(con, L, [{"rel": "PERSONAL/app/.claude-proposals/x.md", "deleted": True}])
check(r["accepted"] == 1 and con.execute("SELECT COUNT(*) FROM remote_designs WHERE host=? AND rel LIKE 'PERSONAL/app/%'", (L,)).fetchone()[0] == 0, "deleted removes")
check(con.execute("SELECT COUNT(*) FROM remote_designs WHERE host='Other'").fetchone()[0] == 1, "delete scoped to host")
r = ni.ingest_designs(con, L, [{"rel": "WORK/.claude-proposals/x.md", "deleted": True}])
check(r["accepted"] == 0, "delete of WORK rel refused")

# ---------- snapshots ----------
def snap(i, text, rel="PERSONAL/app/.claude-proposals/s.md", **kw):
    r = {"src_id": i, "ts": NOW - 100 + i, "rel": rel, "sha": sha(text), "session_id": "sat1", "agent_type": "designer",
         "tool": "Write", "content": text}
    r.update(kw)
    return r


r = ni.ingest_snapshots(con, L, [snap(1, "a"), snap(2, "b")])
check(r == {"accepted": 2, "skipped": 0}, f"snapshots accepted: {r}")
check(ni.ingest_snapshots(con, L, [snap(1, "a")]) == {"accepted": 0, "skipped": 1}, "snapshot re-send idempotent")
check(ni.cursor(con, L)["snapshots"] == 2, "snapshot cursor")
r = ni.ingest_snapshots(con, L, [snap(3, "c", sha="1" * 64), snap(4, "d", rel="WORK/x/.claude-proposals/a.md"),
                                 snap(5, "e", rel="P/../.claude-proposals/a.md"), snap(6, "f", ts=NOW - 40 * 86400),
                                 snap(7, "g", session_id="bad sid"), snap(8, "h", src_id="x")])
check(r["accepted"] == 0 and r["skipped"] == 6, f"bad snapshots rejected: {r}")
r = ni.ingest_snapshots(con, L, [snap(10, "o", session_id="hubsid")])
check(r["accepted"] == 1 and con.execute("SELECT session_id FROM design_snapshots WHERE src_id=10").fetchone()[0] is None,
      "snapshot of a foreign session is stored without the session id")
ni.ingest_snapshots(con, L, [snap(100 + i, f"v{i}") for i in range(ni.SNAPSHOT_KEEP + 5)][:50])
check(con.execute("SELECT COUNT(*) FROM design_snapshots WHERE host=? AND rel=?", (L, "PERSONAL/app/.claude-proposals/s.md")).fetchone()[0]
      == ni.SNAPSHOT_KEEP, "snapshot prune keeps 40 per (host, rel)")
# local snapshot prune is scoped to the local (host NULL) rows
ne.WS_ORIG = ne.WS
ws = Path(tmp) / "ws"
(ws / "PERSONAL" / "app" / ".claude-proposals").mkdir(parents=True)
ne.WS = ws
f = ws / "PERSONAL" / "app" / ".claude-proposals" / "s.md"
before = con.execute("SELECT COUNT(*) FROM design_snapshots WHERE host=?", (L,)).fetchone()[0]
for i in range(ne.SNAPSHOT_KEEP + 3):
    f.write_text(f"local {i}\n")
    ne.snapshot_design(con, str(f), str(f.parent.parent), "loc", "designer", "Write")
check(con.execute("SELECT COUNT(*) FROM design_snapshots WHERE host IS NULL").fetchone()[0] == ne.SNAPSHOT_KEEP, "local prune keeps 40")
check(con.execute("SELECT COUNT(*) FROM design_snapshots WHERE host=?", (L,)).fetchone()[0] == before, "local prune leaves remote rows")
ne.WS = ne.WS_ORIG

# ---------- tokens delegate ----------
calls = []
orig = getattr(niu_usage, "ingest_rows", None)
niu_usage.ingest_rows = lambda label, rows: calls.append((label, list(rows))) or {"accepted": len(rows), "skipped": 1}
r = ni.ingest_tokens(L, [{"mkey": "a"}] * 3)
check(calls == [(L, [{"mkey": "a"}] * 3)] and r == {"accepted": 3, "skipped": 1}, f"tokens delegate: {calls} {r}")
r = ni.ingest_tokens(L, [{}] * (ni.MAX_ROWS["tokens"] + 4))
check(r["accepted"] == ni.MAX_ROWS["tokens"] and r["skipped"] == 5, f"token row cap: {r}")
if orig is not None:
    niu_usage.ingest_rows = orig
else:
    del niu_usage.ingest_rows

print(f"ingest: {passed} passed, {failed} failed")
os.system(f"rm -rf {tmp}")
sys.exit(1 if failed else 0)
