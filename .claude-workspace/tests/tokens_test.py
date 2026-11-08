#!/usr/bin/env python3
"""Tests for the transcript token scanner and GET /api/usage/tokens: lib/niu_usage.py and `niu-dash usage-scan`.

Every transcript here is a FAKE one built in a temp dir (the planted canary strings must never reach usage.db, a payload,
stdout or stderr). The real ~/.claude is never touched: CLAUDE_CONFIG_DIR always points at the temp tree."""
import http.client
import io
import json
import os
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
SH = WS / ".claude-workspace"
tmp = Path(tempfile.mkdtemp())
state = tmp / "state"
CLAUDE = tmp / "claude"
os.environ.update(NIU_EVENTS_DB=str(state / "events.db"), NIU_USAGE_DB=str(state / "usage.db"), CLAUDE_CONFIG_DIR=str(CLAUDE),
                  NIU_CLAUDE_USAGE_API="0", NIU_CLAUDE_CREDENTIALS="/nonexistent/creds.json", TZ="Asia/Tokyo")
os.environ.pop("NIU_USAGE_COLLECT", None)
os.environ.pop("NIU_USAGE_TOKENS", None)
os.environ.pop("NIU_DASH_DEBUG", None)
time.tzset()
sys.path.insert(0, str(SH / "lib"))
import niu_events  # noqa: E402
import niu_usage as U  # noqa: E402

passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


D = 86400
# yesterday 09:00 in Asia/Tokyo: recent enough for the 90-day windows of the real clock, in the past, and inside the 7d / 30d ranges
T0 = ((int(time.time()) + 9 * 3600) // D) * D - 9 * 3600 - D + 9 * 3600
CANARIES = ["CANARY-PROMPT-7f3a", "sk-ant-api03-FAKEFAKE", "/home/u/secret-project", "secret-project"]
SLUG = "-home-u-WORKSPACE-PERSONAL-demo"
CWD_IN = str(WS / "PERSONAL" / "demo")
CWD_OUT = "/home/u/secret-project"
UU = {n: f"{n:08x}-0000-4000-8000-000000000000" for n in range(1, 9)}


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def line(mid, usage, ts, model="claude-opus-5-5-20261101", cwd=CWD_IN, side=False, text="hello", agent=None, req=None):
    u = {"input_tokens": usage[0], "output_tokens": usage[1], "cache_creation_input_tokens": usage[2], "cache_read_input_tokens": usage[3],
         "cache_creation": {"ephemeral_5m_input_tokens": usage[2]}, "service_tier": "standard"}
    o = {"type": "assistant", "isSidechain": side, "cwd": cwd, "timestamp": iso(ts), "sessionId": "s", "requestId": req or "req_" + mid[4:],
         "message": {"id": mid, "model": model, "role": "assistant", "content": [{"type": "text", "text": text}], "usage": u}}
    if agent:
        o["agentId"] = agent
    return json.dumps(o)


def put(rel, lines, final_newline=True):
    p = CLAUDE / "projects" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + ("\n" if final_newline else ""))
    os.utime(p, (T0, T0))
    return p


CANARY_TXT = "CANARY-PROMPT-7f3a sk-ant-api03-FAKEFAKE /home/u/secret-project"
A = "msg_AAAAAAAA1"


def build():
    import shutil
    shutil.rmtree(CLAUDE, ignore_errors=True)
    put(f"{SLUG}/{UU[1]}.jsonl", [
        json.dumps({"type": "user", "cwd": CWD_IN, "message": {"role": "user", "content": CANARY_TXT}}),
        line(A, (100, 10, 1000, 5000), T0, text=CANARY_TXT),             # one API response written as 3 lines
        line(A, (100, 50, 1000, 5000), T0, text=CANARY_TXT),
        line(A, (100, 40, 1000, 5000), T0),                              # a lower output value must not win
        line("msg_BBBBBBBB2", (1, 2, 3, 4), T0 - 2 * D, model="claude-sonnet-5-5"),
        '{"type":"assistant","message":{"usage":{"input_tokens":5,"output_tokens"' + ' ' + CANARY_TXT,   # corrupt (cut off) but contains both markers
        line("msg_SYNTH0001", (50, 50, 50, 50), T0, model="<synthetic>"),
        json.dumps({"type": "user", "toolUseResult": {"agentId": "a1", "totalTokens": 999999999, "usage": {"input_tokens": 999999}, "status": "assistant"}}),
        line("msg_CCCCCCCC3", (9, 9, 9, 9), T0, model="claude-haiku-5-5-20261101")[:-20],     # half-written: no newline, cut
    ], final_newline=False)
    sub = f"{SLUG}/{UU[1]}/subagents"
    put(f"{sub}/agent-a1.jsonl", [line("msg_SUBAAAAA1", (5, 5, 5, 5), T0, side=True, agent="a1")])
    (CLAUDE / "projects" / sub / "agent-a1.meta.json").write_text(json.dumps({"agentType": "implementer", "description": CANARY_TXT, "pickedEffortModel": "x"}))
    put(f"{sub}/agent-a2.jsonl", [line("msg_SUBAAAAA2", (6, 6, 6, 6), T0, side=True, agent="a2")])
    put(f"{sub}/agent-a3.jsonl", [line("msg_SUBAAAAA3", (1, 0, 0, 0), T0 - D, model="claude-haiku-5-5", side=True, agent="a3")])
    (CLAUDE / "projects" / sub / "agent-a3.meta.json").write_text(json.dumps({"agentType": "../../x"}))
    put(f"{SLUG}/{UU[2]}.jsonl", [line(A, (100, 50, 1000, 5000), T0), line("msg_DDDDDDDD4", (7, 0, 0, 0), T0, model="claude-haiku-5-5")])   # resumed session
    put(f"-home-u-secret-project/{UU[3]}.jsonl", [line("msg_OOOOOOOO5", (2, 2, 2, 2), T0 - 20 * D, model="claude-sonnet-5-5", cwd=CWD_OUT, text=CANARY_TXT)])
    put(f"{SLUG}/{UU[4]}.jsonl", [line("msg_EEEEEEEE6", (3, 3, 3, 3), T0, side=True), line("msg_FFFFFFFF7", (1, 1, 1, 1), T0)])         # old format
    put(f"{SLUG}/{UU[5]}.jsonl", [line("msg_HOSTILE01", (1, 1, 1, 1), T0, model="<img onerror=x>"), line("msg_HOSTILE02", (10 ** 12, 1, 1, 1), T0),
                                  line("msg_HOSTILE03", (1, 1, 1, 1), T0, model="claude-opus-5-5\" onload=\"x")])


def reset_db():
    for f in state.glob("usage.db*"):
        f.unlink()
    U._memo.clear()
    U._tok_memo.clear()


def scan(**kw):
    return U.scan_once(now=T0 + 3600, **kw)


def full_scan(**kw):
    r = None
    for _ in range(300):
        r = scan(**kw)
        if not r["scanning"]:
            break
    return r


def db_rows(sql, *a):
    c = U.connect(readonly=True)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def totals_by_agent(p):
    return {x["agent_type"]: x["total"] for x in p["by_agent"]}


# ---------- 1. schema migration 1 -> 2 ----------
reset_db()
state.mkdir(parents=True, exist_ok=True)
c = sqlite3.connect(state / "usage.db")
c.executescript("CREATE TABLE limit_samples (key TEXT NOT NULL, ts REAL NOT NULL, used REAL NOT NULL, resets_at REAL, source TEXT NOT NULL, PRIMARY KEY (key, ts)) WITHOUT ROWID;"
                "CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT); INSERT INTO limit_samples VALUES('seven_day', 100, 41.5, 200, 'api'); PRAGMA user_version=1;")
c.commit()
c.close()
os.chmod(state / "usage.db", 0o600)
check(U.tokens(now=T0)["state"] == "empty", "a version 1 file reads as empty, not an error")
con = U.connect()
check(con.execute("PRAGMA user_version").fetchone()[0] == 2, "user_version migrated 1 -> 2")
check(con.execute("SELECT used FROM limit_samples WHERE key='seven_day'").fetchone()[0] == 41.5, "existing samples untouched")
names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
check({"tok_files", "tok_msgs", "tok_models", "meta", "limit_samples"} <= names, "token tables created")
con.close()
U.connect().close()
check(sqlite3.connect(state / "usage.db").execute("PRAGMA user_version").fetchone()[0] == 2, "migration is idempotent")
reset_db()

# ---------- 2. scanner on the fixture tree ----------
build()
stat_before = {p: p.stat().st_mtime_ns for p in (CLAUDE / "projects").rglob("*") if p.is_file()}
r = full_scan()
check(not r["scanning"] and r["files_total"] == 8 and r["files_done"] == 8, f"all 8 files scanned: {r}")
check(r["corrupt_lines"] == 2 and r["messages"] == 14, f"pass counts: {r}")
check({p: p.stat().st_mtime_ns for p in (CLAUDE / "projects").rglob("*") if p.is_file()} == stat_before, "the scanner never writes to the transcripts")
p = U.tokens("30d", "", now=T0 + 3600)
check(p["state"] == "ok", f"state ok: {p['state']}")
a = totals_by_agent(p)
# A: split lines (3) count once with the max of each field (6150); C is cut off so not counted yet
check(a.get("session") == 6150 + 10 + 7 + 4 + 4 + 4 + 8, f"session total (A once, B, D, F, O, two hostile-model lines): {a}")
check(a.get("implementer") == 20, f"meta.json agentType: {a}")
check(a.get("subagent") == 24 + 1 + 12, f"no meta -> 'subagent', bad agentType -> 'subagent', isSidechain in a main file -> 'subagent': {a}")
check("designer" not in a and not any("/" in k or ".." in k for k in a), "hostile agentType normalised")
check(p["totals"]["messages"] == 11 and p["totals"]["total"] == sum(a.values()), f"totals: {p['totals']}")
check(p["totals"]["input"] + p["totals"]["output"] + p["totals"]["cache_write"] + p["totals"]["cache_read"] == p["totals"]["total"], "kinds add up to total")
check(p["totals"]["cache_read"] > 5000 and p["totals"]["sessions"] >= 5, "cache read counted, sessions counted")
corrupt = db_rows("SELECT v FROM meta WHERE k='corrupt_lines'")[0][0]
check(corrupt == "2", f"corrupt lines: the cut-off line and the 10^12 count: {corrupt}")
check(not any("synth" in x["model"] or "SYNTH" in x["model"] for x in p["by_model"]), "<synthetic> skipped")
check(db_rows("SELECT count(*) FROM tok_msgs WHERE mkey = ?", U._mkey("msg_SYNTH0001"))[0][0] == 0, "no synthetic row")
check(all(x["total"] < 999999 for x in p["by_agent"]), "task-result totalTokens ignored")
models = {x["model"]: x for x in p["by_model"]}
check("opus-5-5" in models and models["opus-5-5"]["family"] == "opus" and "sonnet-5-5" in models and "haiku-5-5" in models, f"models normalised: {list(models)}")
check("other" in models and models["other"]["family"] == "other" and not any("<" in m or '"' in m for m in models), f"hostile model names become 'other': {list(models)}")
check(db_rows("SELECT f.kind, f.agent_type FROM tok_msgs t JOIN tok_files f ON f.id=t.file_id WHERE t.mkey=?", U._mkey(A))[0][0] == "main", "resumed session: counted once, attributed to the first file")
rows = {x["domain"] or "": x for x in map(dict, db_rows("SELECT domain, project, count(*) n FROM tok_files GROUP BY domain, project"))}
check(set(rows) >= {"PERSONAL", "(outside)"}, f"domains: {list(rows)}")
check(all(r_["project"] is None for r_ in rows.values() if r_["domain"] == "(outside)"), "outside the workspace: no project")
check(db_rows("SELECT count(*) FROM tok_files WHERE domain='(outside)' AND project IS NOT NULL")[0][0] == 0, "no project stored for outside cwd")
# half-written last line waits; once the newline arrives it counts
f1 = CLAUDE / "projects" / SLUG / f"{UU[1]}.jsonl"
before = U.tokens("30d", "", now=T0 + 3600)["totals"]["messages"]
scan()
check(U.tokens("30d", "", now=T0 + 3600)["totals"]["messages"] == before, "a pass over an unchanged tree changes nothing")
full_c = line("msg_CCCCCCCC3", (9, 9, 9, 9), T0, model="claude-haiku-5-5-20261101")
with open(f1, "ab") as fh:
    fh.write(b"\n")
    fh.write(full_c.encode()[-20:][:0])
U._tok_memo.clear()
scan()
check(U.tokens("30d", "", now=T0 + 3600)["totals"]["messages"] == before, "newline after a cut-off line: still corrupt, counted once more as corrupt at most")
with open(f1, "ab") as fh:
    fh.write(full_c.encode() + b"\n")
scan()
check(U.tokens("30d", "", now=T0 + 3600)["totals"]["messages"] == before + 1, "an appended complete line is counted")
off = db_rows("SELECT offset, size FROM tok_files WHERE session_id=? AND kind='main'", UU[1])[0]
check(off[0] == f1.stat().st_size == off[1], f"offset advanced to the end: {tuple(off)}")
# a half-written line (no newline) is not consumed
with open(f1, "ab") as fh:
    fh.write(line("msg_PARTIAL01", (4, 4, 4, 4), T0).encode()[:80])
scan()
check(db_rows("SELECT offset FROM tok_files WHERE session_id=? AND kind='main'", UU[1])[0][0] == off[0], "partial line: offset stays at the last newline")
with open(f1, "ab") as fh:
    fh.write(line("msg_PARTIAL01", (4, 4, 4, 4), T0).encode()[80:] + b"\n")
scan()
check(db_rows("SELECT count(*) FROM tok_msgs WHERE mkey=?", U._mkey("msg_PARTIAL01"))[0][0] == 1, "the finished half-written line is counted once")
# agent type fallback: events.db lookup
ev = niu_events.connect()
ev.execute("INSERT INTO events(ts,event,agent_id,agent_type) VALUES(?,?,?,?)", (T0, "SubagentStart", "a2", "designer"))
ev.commit()
ev.close()
full_scan(full=True)
a = totals_by_agent(U.tokens("30d", "", now=T0 + 3600))
check(a.get("designer") == 24 and a.get("subagent") == 13, f"events.db fallback gives designer to agent a2: {a}")
ev = niu_events.connect()
ev.execute("DELETE FROM events")
ev.commit()
ev.close()

# ---------- 3. idempotency, --full, rewrites ----------
t_a = U.tokens("30d", "", now=T0 + 3600)
for _ in range(2):
    scan()
check(U.tokens("30d", "", now=T0 + 3600)["totals"] == t_a["totals"], "repeated passes give the same totals")
full_scan(full=True)
U._tok_memo.clear()
t_b = U.tokens("30d", "", now=T0 + 3600)
check(t_b["totals"] == t_a["totals"] and totals_by_agent(t_b) == totals_by_agent(t_a), "--full gives the same totals")
check(db_rows("SELECT v FROM meta WHERE k='corrupt_lines'")[0][0] == "3", "--full resets the corrupt counter, then recounts (the cut-off line is complete now)")
f2 = CLAUDE / "projects" / SLUG / f"{UU[2]}.jsonl"
f2.write_text(line("msg_DDDDDDDD4", (7, 0, 0, 0), T0, model="claude-haiku-5-5") + "\n")     # shorter file: size < offset
os.utime(f2, (T0 + 5, T0 + 5))
scan()
f4 = CLAUDE / "projects" / SLUG / f"{UU[4]}.jsonl"
body = f4.read_text()
new = f4.with_name("new.tmp")
new.write_text(body)
os.replace(new, f4)                                                              # same content, new inode
os.utime(f4, (T0 + 6, T0 + 6))
scan()
check(U.tokens("30d", "", now=T0 + 3600)["totals"] == t_a["totals"], "a truncated and a replaced file leave the totals unchanged (dedup)")
check(db_rows("SELECT count(*) FROM tok_msgs")[0][0] == t_a["totals"]["messages"], "no duplicate rows")

# ---------- 4. aggregates, ranges, domain ----------
NOW = T0 + 3600
pt = U.tokens("today", "", now=NOW)
p7 = U.tokens("7d", "", now=NOW)
p30 = U.tokens("30d", "", now=NOW)
tot = lambda p_: p_["totals"]["total"]  # noqa: E731
check(tot(pt) == 6150 + 20 + 24 + 7 + 12 + 4 + 4 + 4 + 36 + 16 and pt["range"] == "today" and tot(p7) == tot(pt) + 10 + 1 and tot(p30) == tot(p7) + 8, f"range totals: {tot(pt)} {tot(p7)} {tot(p30)}")
check(p7["kpis"]["today"]["total"] == tot(pt) and p7["kpis"]["7d"]["total"] == tot(p7) and p7["kpis"]["30d"]["total"] == tot(p30), "kpis match the three ranges")
check(p7["kpis"]["30d"]["output"] == p30["totals"]["output"], "kpi output")
for pp in (pt, p7, p30):
    for key in ("by_agent", "by_model"):
        check(abs(sum(x["share"] for x in pp[key]) - 100) <= 0.1, f"{pp['range']} {key} shares sum to 100: {sum(x['share'] for x in pp[key])}")
check(len(pt["daily"]) == 24 and len(p7["daily"]) == 7 and len(p30["daily"]) == 30, f"daily buckets {len(pt['daily'])} {len(p7['daily'])} {len(p30['daily'])}")
midnight_jst = (-9 * 3600) % D
check(all(d["t"] % D == midnight_jst for d in p7["daily"]) and all(d["t"] % 3600 == 0 for d in pt["daily"]), "buckets aligned to local midnight (Asia/Tokyo)")
check(p7["daily"][-1]["t"] == T0 - 9 * 3600 and p7["daily"][-1]["total"] == tot(pt) and p7["daily"][-1]["label"] == f"{datetime.fromtimestamp(T0).strftime('%a')} {datetime.fromtimestamp(T0).day}", f"last day = today: {p7['daily'][-1]}")
check(sum(d["total"] for d in p7["daily"]) == tot(p7) and sum(d["total"] for d in p30["daily"]) == tot(p30) and sum(d["total"] for d in pt["daily"]) == tot(pt), "daily buckets add up")
check(all(set(d["by_family"]) == {"opus", "sonnet", "haiku", "other"} for d in p7["daily"]), "daily split by model family")
check(pt["daily"][9]["total"] == tot(pt) and pt["daily"][9]["label"] == "09:00", "hourly bucket for today")
check(p7["matrix"]["agents"][0] == "session" and len(p7["matrix"]["total"]) == len(p7["matrix"]["agents"]) and sum(map(sum, p7["matrix"]["total"])) == tot(p7), "matrix adds up to the total")
check(p7["by_agent"][0]["agent_type"] == "session" and p7["by_agent"][0]["sessions"] >= 3, "agent ranking and sessions")
pd = U.tokens("30d", "PERSONAL", now=NOW)
check(pd["domain"] == "PERSONAL" and tot(pd) == tot(p30) - 8, f"domain=PERSONAL filters out (outside): {tot(pd)}")
check(tot(U.tokens("30d", "(outside)", now=NOW)) == 8 and U.tokens("30d", "(outside)", now=NOW)["domain"] == "(outside)", "domain=(outside)")
bg = U.tokens("30d", "bogus", now=NOW)
check(bg["domain"] == "" and tot(bg) == tot(p30), "unknown domain gives all, domain ''")
check(U.tokens("../../x", "<script>", now=NOW)["range"] == "7d" and U.tokens("../../x", "<script>", now=NOW)["domain"] == "", "bad range and domain fall back")
check(U.tokens(None, None, now=NOW)["range"] == "7d", "default range 7d")
cov = p7["coverage"]
check(cov["scanning"] is False and cov["files_total"] == 8 and cov["oldest"] == T0 - 20 * D and cov["last_scan"] and cov["corrupt_lines"] >= 2, f"coverage: {cov}")
check(set(p7) == {"state", "range", "since", "now", "tz", "domain", "kpis", "totals", "by_agent", "by_model", "matrix", "daily", "coverage"}, f"payload keys: {sorted(p7)}")
check(p7["since"] == T0 - 9 * 3600 - 6 * D and p7["tz"] == "Asia/Tokyo", "since is local midnight 6 days back")

# folding: 15 agent types, 10 models
reset_db()
c = U.connect()
for i in range(15):
    c.execute("INSERT INTO tok_files(id, path, kind, session_id, agent_type, domain) VALUES(?,?,?,?,?,?)", (i + 1, f"/x/{i}", "sub", f"sess{i}", f"agent{i:02d}", "PERSONAL"))
for i in range(10):
    c.execute("INSERT INTO tok_models(id, name, family) VALUES(?,?,?)", (i + 1, f"model-{i}", ["opus", "sonnet", "haiku", "other"][i % 4]))
n = 0
for i in range(15):
    for j in range(10):
        n += 1
        c.execute("INSERT INTO tok_msgs VALUES(?,?,?,?,?,?,?,?)", (n, T0, i + 1, j + 1, 100 - i, 10, 5, 1000 - j))
c.commit()
c.close()
pf = U.tokens("7d", "", now=NOW)
check(len(pf["by_agent"]) == 13 and pf["by_agent"][-1]["agent_type"] == "other (3)" and len(pf["by_model"]) == 9 and pf["by_model"][-1]["model"] == "other (2)", f"top 12 + other / top 8 + other: {len(pf['by_agent'])} {len(pf['by_model'])}")
check(abs(sum(x["share"] for x in pf["by_agent"]) - 100) <= 0.1 and abs(sum(x["share"] for x in pf["by_model"]) - 100) <= 0.1, "shares with folded rows sum to 100")
check(len(pf["matrix"]["agents"]) <= 12 and len(pf["matrix"]["models"]) <= 8 and sum(map(sum, pf["matrix"]["total"])) == pf["totals"]["total"], "matrix at most 12 x 8 and still adds up")
check(sum(x["total"] for x in pf["by_agent"]) == pf["totals"]["total"], "folded rows keep the totals")
check(U._shares([1, 1, 1]) == [33.4, 33.3, 33.3] and sum(U._shares([1, 2, 4, 8, 13])) == 100.0 and U._shares([0, 0]) == [0.0, 0.0], "share rounding")

# ---------- 5. confinement ----------
build()
reset_db()
root = U.claude_root()
check(root == CLAUDE.resolve() / "projects", "root is CLAUDE_CONFIG_DIR/projects")
proj = CLAUDE / "projects" / SLUG
os.symlink(proj / f"{UU[1]}.jsonl", proj / f"{UU[6]}.jsonl")                       # symlinked file
os.symlink(proj, CLAUDE / "projects" / "-link-to-project")                         # symlinked project dir
os.symlink(CLAUDE / "projects" / SLUG / UU[1], proj / UU[7])                       # symlinked session dir
os.mkfifo(proj / f"{UU[8]}.jsonl")                                                 # a FIFO named like a transcript
outside = tmp / "elsewhere"
outside.mkdir()
(outside / "x.jsonl").write_text(line("msg_OUTSIDE01", (9, 9, 9, 9), T0) + "\n")
os.symlink(outside / "x.jsonl", proj / "agent-link.jsonl")
t0 = time.time()
r = full_scan()
check(time.time() - t0 < 10, "the FIFO does not block the scan")
check(r["files_total"] == 8, f"symlinks and FIFO are not listed: {r['files_total']}")
check(db_rows("SELECT count(*) FROM tok_msgs WHERE mkey=?", U._mkey("msg_OUTSIDE01"))[0][0] == 0, "nothing read through a symlink")
check(db_rows("SELECT count(*) FROM tok_files")[0][0] == 9, "8 files plus the side row of the old-format file")
check(not any(r_[0].startswith("/") or "projects" in r_[0] or "home" in r_[0] for r_ in db_rows("SELECT path FROM tok_files")), "tok_files keys are hashes, not paths")
fx = U._open_confined
real = proj / f"{UU[1]}.jsonl"
fd = fx(str(real), root)
check(fd is not None, "a regular file under root opens")
if fd is not None:
    os.close(fd)
check(fx(str(proj / f"{UU[6]}.jsonl"), root) is None, "symlinked file refused (O_NOFOLLOW)")
t0 = time.time()
check(fx(str(proj / f"{UU[8]}.jsonl"), root) is None and time.time() - t0 < 5, "FIFO refused at once")
check(fx(str(outside / "x.jsonl"), root) is None, "regular file outside root refused")
check(fx(str(real), root, uid=os.getuid() + 1) is None, "file owned by another uid refused")
check(fx(str(real), outside) is None, "resolved path must lie under the root")
check(fx(str(proj / "missing.jsonl"), root) is None and fx("/dev/null", root) is None, "missing file / device refused")
check(fx(str(proj / UU[7] / "agent-a1.jsonl"), root) is None, "path through a symlinked directory refused")
meta = proj / UU[1] / "subagents" / "agent-a1.meta.json"
check(U._agent_type(str(meta), "a1", root) == "implementer", "meta read through the checks")
big = tmp / "big.meta.json"
big.write_text(json.dumps({"agentType": "toolarge", "pad": "x" * 20000}))
check(U._agent_type(str(big), "zz", root) == "subagent", "meta over 16 KiB or outside root is ignored")
meta2 = proj / UU[1] / "subagents" / "agent-a9.meta.json"
for bad in ('{"agentType": "../../x"}', '{"agentType": 5}', "[1]", "not json", '{"agentType": "' + "a" * 90 + '"}', '{"agentType": "<b>x</b>"}'):
    meta2.write_text(bad)
    check(U._agent_type(str(meta2), "a9", root) == "subagent", f"bad meta value gives subagent: {bad[:24]}")
meta2.write_text('{"agentType": "code-reviewer_2.x:y"}')
check(U._agent_type(str(meta2), "a9", root) == "code-reviewer_2.x:y", "valid agent type kept")
# a symlinked projects dir is refused as a root
bad_cfg = tmp / "badcfg"
bad_cfg.mkdir()
os.symlink(CLAUDE / "projects", bad_cfg / "projects")
os.environ["CLAUDE_CONFIG_DIR"] = str(bad_cfg)
check(U.claude_root() is None and U.scan_once(now=NOW)["root"] is False, "symlinked projects dir: no scan")
os.environ["CLAUDE_CONFIG_DIR"] = str(tmp / "nonexistent")
check(U.claude_root() is None, "missing config dir: no scan")
os.environ["CLAUDE_CONFIG_DIR"] = str(CLAUDE)

# ---------- 6. budgets (injected clock) ----------
build()
reset_db()
full = None
r1 = scan(budget_bytes=1024)
check(r1["scanning"] is True and r1["bytes"] <= 1024 and r1["bytes_pending"] > 0, f"byte budget stops the first pass early: {r1}")
cov = U.tokens("30d", "", now=NOW)
check(cov["state"] == "scanning" and cov["coverage"]["scanning"] and cov["coverage"]["files_total"] == 8 and cov["coverage"]["files_done"] < 8, f"scanning state with progress: {cov['state']} {cov['coverage']}")
passes = 1
while True:
    rr = scan(budget_bytes=1024)
    passes += 1
    if not rr["scanning"] or passes > 400:
        break
check(not rr["scanning"] and passes > 3, f"repeated passes converge ({passes})")
conv = U.tokens("30d", "", now=NOW)
reset_db()
full_scan()
check(U.tokens("30d", "", now=NOW)["totals"] == conv["totals"] and conv["state"] == "ok", "budgeted passes converge to the full totals")
reset_db()
ticks = {"n": 0}


def clock():
    ticks["n"] += 1
    return ticks["n"] * 0.7


rt = scan(budget_secs=1.5, clock=clock)
check(rt["scanning"] is True and rt["files"] < 8, f"time budget stops the pass: files={rt['files']}")
check(U.scan_once(now=NOW, budget_bytes=0)["files"] == 0, "zero byte budget reads nothing")
# an oversized line is skipped and counted, also when it spans several passes; its text never reaches the DB
reset_db()
build()
old_max = U.MAX_LINE
U.MAX_LINE = 2048
put(f"{SLUG}/{UU[1]}.jsonl", [line("msg_BIGLINE001", (1, 1, 1, 1), T0, text="x" * 5000 + CANARY_TXT), line("msg_AFTERBIG01", (2, 2, 2, 2), T0)])
for f in list((CLAUDE / "projects" / SLUG).glob("*.jsonl")):
    if f.name != f"{UU[1]}.jsonl":
        f.unlink()
import shutil  # noqa: E402
shutil.rmtree(CLAUDE / "projects" / SLUG / UU[1], ignore_errors=True)
r = None
for _ in range(50):
    r = scan(budget_bytes=3000)
    if not r["scanning"]:
        break
check(db_rows("SELECT count(*) FROM tok_msgs WHERE mkey=?", U._mkey("msg_BIGLINE001"))[0][0] == 0 and db_rows("SELECT count(*) FROM tok_msgs WHERE mkey=?", U._mkey("msg_AFTERBIG01"))[0][0] == 1,
      "oversized line skipped, the next line counted")
check(db_rows("SELECT v FROM meta WHERE k='corrupt_lines'")[0][0] == "1", "oversized line counted once")
U.MAX_LINE = old_max

# ---------- 7. privacy ----------
build()
reset_db()
env = dict(os.environ, NIU_DASH_DEBUG="1")
run = subprocess.run([str(SH / "bin" / "niu-dash"), "usage-scan", "--full"], env=env, capture_output=True, text=True, timeout=120)
check(run.returncode == 0, f"usage-scan exits 0: {run.stderr[:200]}")
out_lines = run.stdout.strip().splitlines()
check(len(out_lines) == 3 and re.fullmatch(r"files read: \d+ \(of \d+ found\)", out_lines[0]) and re.fullmatch(r"messages: \d+ \(stored: \d+\)", out_lines[1])
      and re.fullmatch(r"corrupt lines: \d+", out_lines[2]), f"usage-scan prints counts only: {out_lines}")
check("files read: 8 (of 8 found)" == out_lines[0] and out_lines[2] == "corrupt lines: 2", f"counts: {out_lines}")
blob = b"".join(f.read_bytes() for f in state.glob("usage.db*"))
payloads = json.dumps([U.tokens(r_, d_, now=NOW) for r_ in U.RANGES for d_ in ("", "PERSONAL", "(outside)")])
streams = run.stdout + run.stderr
for cn in CANARIES:
    check(cn.encode() not in blob, f"canary {cn!r} not in usage.db or WAL")
    check(cn not in payloads, f"canary {cn!r} not in payloads")
    check(cn not in streams, f"canary {cn!r} not in usage-scan output")
check(str(CLAUDE) not in payloads and str(tmp) not in payloads and UU[1] not in payloads and "path" not in payloads, "payloads carry no filesystem path or session id")
check(b"../../x" not in blob and b"secret-project" not in blob and b"<img" not in blob, "hostile and outside strings are not stored")
check(CWD_IN.encode() not in blob, "no raw cwd stored")
# a file that raises: only the exception class name goes to stderr
orig = U._extract
U._extract = lambda *a, **k: (_ for _ in ()).throw(RuntimeError(CANARY_TXT))
reset_db()
buf = io.StringIO()
os.environ["NIU_DASH_DEBUG"] = "1"
with redirect_stderr(buf):
    rr = full_scan()
os.environ.pop("NIU_DASH_DEBUG")
U._extract = orig
check("RuntimeError" in buf.getvalue() and not any(cn in buf.getvalue() for cn in CANARIES) and not re.search(r"/tmp|/home", buf.getvalue()), f"debug output: class name and file id only: {buf.getvalue()[:120]!r}")
check(rr["skipped_files"] >= 1 and rr["files_total"] == 8, "per-file errors are isolated and counted")
reset_db()
# permissions of usage.db and its sidecar files
full_scan()
for f in state.glob("usage.db*"):
    check(stat.S_IMODE(f.stat().st_mode) & 0o077 == 0, f"{f.name} is private: {oct(f.stat().st_mode)}")

# ---------- 8. env switches and the background thread ----------
os.environ["NIU_USAGE_TOKENS"] = "0"
check(U.tokens(now=NOW)["state"] == "disabled" and U.Recorder(reading=lambda n: None).scan is False, "NIU_USAGE_TOKENS=0 disables the scanner and the payload")
sr = subprocess.run([str(SH / "bin" / "niu-dash"), "usage-scan"], env=dict(os.environ), capture_output=True, text=True, timeout=30)
check(sr.returncode != 0 and "off" in sr.stderr, "usage-scan refuses when disabled")
del os.environ["NIU_USAGE_TOKENS"]
os.environ["NIU_USAGE_COLLECT"] = "0"
check(U.tokens(now=NOW)["state"] == "disabled", "NIU_USAGE_COLLECT=0 disables token stats too")
del os.environ["NIU_USAGE_COLLECT"]


class FakeStop:
    def __init__(self, clock, ticks):
        self.clock, self.left, self.waits = clock, ticks, []

    def is_set(self):
        return self.left <= 0

    def wait(self, secs):
        self.waits.append(secs)
        self.clock["t"] += secs
        self.left -= 1
        return self.left <= 0


clk = {"t": 1000.0}
calls = {"samples": 0, "scans": 0}


def reading(now):
    calls["samples"] += 1
    return None


def scanner(now=None):
    calls["scans"] += 1
    if calls["scans"] == 2:
        raise sqlite3.OperationalError("database is locked")
    if calls["scans"] == 3:
        raise RuntimeError(CANARY_TXT)


rec = U.Recorder(interval=300, reading=reading, clock=lambda: clk["t"], stop=FakeStop(clk, 11), scan=True, scan_every=60, scanner=scanner)
rec.run()
check(calls["scans"] == 11 and calls["samples"] == 3, f"scan every 60 s, limit sample every 300 s inside one thread: {calls}")
check(rec.stop_event is not None and U.recorder_status()["state"] == "stopped", "thread state after run")
check(CANARY_TXT not in json.dumps(U.recorder_status()), "scanner failures are not exposed")
rec2 = U.Recorder(interval=300, reading=reading, clock=lambda: clk["t"], stop=FakeStop(clk, 3), scan=False)
rec2.run()
check(rec2.stop_event is not None, "scan=False: only the sampler runs")
# concurrent passes: a second caller gets 'busy' instead of a second writer
reset_db()
got = {}
gate = threading.Event()
orig_discover = U._discover


def slow_discover(*a, **k):
    gate.wait(5)
    return orig_discover(*a, **k)


U._discover = slow_discover
th = threading.Thread(target=lambda: got.setdefault("a", scan()))
th.start()
time.sleep(0.3)
second = scan()
gate.set()
th.join(10)
U._discover = orig_discover
check(second["busy"] is True and got["a"]["busy"] is False, "a second concurrent scan is refused (one writer)")
# a locked database skips the tick
reset_db()
U.connect().close()
lock = sqlite3.connect(state / "usage.db", timeout=1)
lock.execute("BEGIN EXCLUSIVE")
rec3 = U.Recorder(scan=True)
e0 = U.recorder_status()["errors"]
t0 = time.time()
orig_to = U.connect
U.connect = lambda readonly=False: (lambda c: c)(sqlite3.connect(state / "usage.db", timeout=0.2))
rec3.scan_tick()
U.connect = orig_to
lock.rollback()
lock.close()
check(U.recorder_status()["errors"] == e0 + 1 and U.recorder_status()["last_error"] == "database" and time.time() - t0 < 5, "SQLite busy: the tick is skipped and counted, no raise")

# ---------- 9. the endpoint ----------
build()
reset_db()
full_scan()
srv_env = dict(os.environ, NIU_HOST_LABEL="testhost", CLAUDE_CONFIG_DIR=str(tmp / "no-claude"))   # the server's own scanner finds nothing
port = (lambda s: (s.bind(("127.0.0.1", 0)), s.getsockname()[1], s.close())[1])(socket.socket())
srv = subprocess.Popen([str(SH / "bin" / "niu-dash"), "serve", "--port", str(port)], env=srv_env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def req(method, path):
    cn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    cn.request(method, path, headers={"Host": f"127.0.0.1:{port}", "X-Niu-CSRF": "1", "Origin": f"http://127.0.0.1:{port}"})
    r_ = cn.getresponse()
    raw = r_.read()
    try:
        js = json.loads(raw)
    except ValueError:
        js = None
    return r_.status, dict(r_.getheaders()), js, raw


try:
    for _ in range(50):
        try:
            if req("GET", "/healthz")[0] == 200:
                break
        except OSError:
            time.sleep(0.1)
    st, hd, js, raw = req("GET", "/api/usage/tokens?range=30d")
    check(st == 200 and js["state"] == "ok" and js["range"] == "30d" and js["totals"]["messages"] == 11 and hd.get("Content-Type") == "application/json", f"endpoint ok: {st} {js and js.get('state')}")
    check("default-src 'self'" in hd.get("Content-Security-Policy", ""), "CSP unchanged")
    check(req("GET", "/api/usage/tokens")[2]["range"] == "7d", "default range 7d")
    check(req("GET", "/api/usage/tokens?range=../../x")[2]["range"] == "7d", "bad range -> 7d")
    check(req("GET", "/api/usage/tokens?range=today&range=30d")[2]["range"] == "today", "first range value wins")
    check(req("GET", "/api/usage/tokens?range=30d&domain=PERSONAL")[2]["domain"] == "PERSONAL", "domain passes through")
    check(req("GET", "/api/usage/tokens?range=30d&domain=%3Cscript%3E")[2]["domain"] == "", "hostile domain -> all")
    check(req("GET", "/api/usage/tokens?range=30d&domain=" + "a" * 5000)[0] == 200, "long domain answered normally")
    n0 = js["totals"]["messages"]
    c = U.connect()
    c.execute("INSERT INTO tok_msgs VALUES(?,?,?,?,?,?,?,?)", (12345, time.time(), 1, 1, 1, 1, 1, 1))
    c.commit()
    c.close()
    check(req("GET", "/api/usage/tokens?range=30d")[2]["totals"]["messages"] == n0, "second request within 60 s is cached")
    check(req("GET", "/api/usage/tokens?range=7d")[2]["range"] == "7d", "cache is per range")
    for m in ("POST", "PUT", "DELETE"):
        check(req(m, "/api/usage/tokens")[0] in (404, 405, 501), f"{m} not served")
    check(b"secret-project" not in raw and b"CANARY" not in raw and str(tmp).encode() not in raw, "endpoint payload carries no path or transcript text")
finally:
    srv.terminate()
    try:
        srv.wait(5)
    except subprocess.TimeoutExpired:
        srv.kill()
reset_db()
port = (lambda s: (s.bind(("127.0.0.1", 0)), s.getsockname()[1], s.close())[1])(socket.socket())
srv = subprocess.Popen([str(SH / "bin" / "niu-dash"), "serve", "--port", str(port)], env=dict(srv_env, NIU_USAGE_TOKENS="0"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
try:
    for _ in range(50):
        try:
            if req("GET", "/healthz")[0] == 200:
                break
        except OSError:
            time.sleep(0.1)
    check(req("GET", "/api/usage/tokens")[2]["state"] == "disabled", "endpoint reports disabled with NIU_USAGE_TOKENS=0")
finally:
    srv.terminate()
    srv.wait(5)
reset_db()
check(U.tokens(now=NOW)["state"] == "empty", "no usage.db: empty")
p_ = state / "usage.db"
p_.write_bytes(b"this is not a sqlite database" * 50)
check(U.tokens(now=NOW)["state"] == "error" and "not a database" not in json.dumps(U.tokens(now=NOW)), "corrupt db: fixed error state, no exception text")
p_.unlink()

print(f"tokens: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
