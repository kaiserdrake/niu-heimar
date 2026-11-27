#!/usr/bin/env python3
"""Tests for niu_forward (satellite forwarder) against a tiny in-process fake hub on 127.0.0.1.

The real end-to-end run against niu-dash lives elsewhere; here the hub only records what it receives."""
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

tmp = Path(tempfile.mkdtemp()).resolve()
TOKEN = "tok_" + "A1b2C3d4" * 5
state = tmp / "state"
os.environ.update(NIU_EVENTS_DB=str(state / "events.db"), NIU_USAGE_DB=str(state / "usage.db"),
                  NIU_FORWARD_DB=str(state / "forward.db"), NIU_FORWARD_CONFIG=str(tmp / "cfg.json"),
                  NIU_FORWARD_TOKEN=str(tmp / "token"), XDG_STATE_HOME=str(state))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import niu_events as ne  # noqa: E402
import niu_forward as nf  # noqa: E402
import niu_usage  # noqa: E402

passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


# ---------- a fake workspace ----------
WSD = tmp / "ws"
for d, name in (("PERSONAL/app", "p.md"), ("WORK/w", "w.md"), ("HOBBY/h", "h.md")):
    (WSD / d / ".claude").mkdir(parents=True)
    (WSD / d / ".claude" / "settings.local.json").write_text("{}")
    (WSD / d / ".claude-proposals").mkdir()
    (WSD / d / ".claude-proposals" / name).write_text(f"# Title {name}\nStatus: proposed\nbody of {name}\n")
ne.WS = WSD  # niu_designs and niu_forward both read ne.WS at call time
OUTSIDE = tmp / "elsewhere"
OUTSIDE.mkdir()


def write_cfg(url, **extra):
    cfg = {"url": url, "token_file": str(tmp / "token"), "send": {"events": True, "tokens": True, "designs": True},
           "design_domains": ["(workspace)", "PERSONAL", "HOBBY"]}
    cfg.update(extra)
    Path(os.environ["NIU_FORWARD_CONFIG"]).write_text(json.dumps(cfg))


def write_token(mode=0o600):
    p = tmp / "token"
    if p.is_symlink() or p.exists():
        p.unlink()
    p.write_text(TOKEN + "\n")
    p.chmod(mode)


# ---------- fake hub ----------
class Hub:
    def __init__(self):
        self.reqs, self.script, self.cursor = [], None, {"events": 0, "snapshots": 0}
        hub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw) if raw else None
                hub.reqs.append({"method": self.command, "path": self.path, "auth": self.headers.get("Authorization"),
                                 "body": body, "raw": raw})
                status, obj, hdrs = 200, None, {}
                if hub.script:
                    r = hub.script(self.command, self.path, body)
                    if r:
                        status, obj, hdrs = (r + ({},))[:3] if len(r) == 2 else r
                if obj is None:
                    if self.path == "/api/ingest/cursor":
                        obj = dict(hub.cursor)
                    else:
                        rows = (body or {}).get("rows", [])
                        obj = {"ok": True, "accepted": len(rows), "skipped": 0, "cursor": {}}
                out = json.dumps(obj).encode()
                self.send_response(status)
                for k, v in hdrs.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = _do

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def posts(self, kind=None):
        return [r for r in self.reqs if r["method"] == "POST" and r["path"] == "/api/ingest"
                and (kind is None or r["body"]["kind"] == kind)]

    def rows(self, kind):
        return [row for r in self.posts(kind) for row in r["body"]["rows"]]

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


def fresh_forwarder(hub, **kw):
    """New state + config for each scenario (forward.db removed)."""
    for suffix in ("", "-wal", "-shm"):
        Path(os.environ["NIU_FORWARD_DB"] + suffix).unlink(missing_ok=True)
    write_cfg(hub.url)
    write_token()
    kw.setdefault("sleep", lambda s: None)
    return nf.Forwarder(nf.load_config(), state=nf.State(), **kw)


def add_event(sql_con, pdir, host=None, summary="CANARY_SUMMARY", tool="Bash", ts=None):
    cols = "ts,event,session_id,domain,project,project_dir,agent_id,agent_type,tool,tool_use_id,summary"
    vals = (ts or time.time() - 5, "PreToolUse", "sat1", "PERSONAL", "app", str(pdir), None, None, tool, "tu1", summary)
    if host:
        sql_con.execute(f"INSERT INTO events({cols},host,src_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", vals + (host, 1))
    else:
        sql_con.execute(f"INSERT INTO events({cols}) VALUES(?,?,?,?,?,?,?,?,?,?,?)", vals)
    sql_con.commit()


# ---------- URL allow-list ----------
for good in ("https://hub.tail1234.ts.net", "https://hub.tail1234.ts.net:8443", "https://Hub.Tail.ts.net:8443/",
             "http://127.0.0.1:8765", "http://localhost:9000"):
    try:
        nf.check_url(good)
        check(True, "")
    except nf.ConfigError:
        check(False, f"url accepted: {good}")
for bad in ("http://hub.tail1234.ts.net:8443", "https://example.com", "https://evil.ts.net.example.com", "https://ts.net",
            "http://127.0.0.1", "http://10.0.0.5:8765", "ftp://x.ts.net", "https://user:pw@hub.t.ts.net", "https://hub.t.ts.net/path",
            "https://hub.t.ts.net/?q=1", "http://127.0.0.1.evil.com:80", "", None, "https://dashboard-hub.invalid:8443",
            "https://hub.t.ts.net:99999", "https://hub.t.ts.net\n"):
    try:
        nf.check_url(bad)
        check(False, f"url refused: {bad!r}")
    except nf.ConfigError:
        check(True, "")
check(nf.check_url("https://Hub.Tail.ts.net:8443/") == "https://hub.tail.ts.net:8443", "url is normalised")

# ---------- config ----------
write_cfg("https://hub.t.ts.net", design_domains=["PERSONAL", "work", "WORK", "(workspace)"])
c = nf.load_config()
check(c["design_domains"] == ["PERSONAL", "(workspace)"], f"WORK never in design_domains: {c['design_domains']}")
Path(os.environ["NIU_FORWARD_CONFIG"]).write_text("not json")
try:
    nf.load_config()
    check(False, "bad json refused")
except nf.ConfigError:
    check(True, "")
write_cfg("http://example.com:80")
try:
    nf.load_config()
    check(False, "load-time url refusal")
except nf.ConfigError:
    check(True, "")
try:
    nf.load_config(tmp / "missing.json")
    check(False, "missing config")
except nf.ConfigError:
    check(True, "")

# ---------- token file checks ----------
cfg = {"token_file": str(tmp / "token")}
write_token()
check(nf.read_token(cfg) == TOKEN, "good token read")


def refused(cfg):
    try:
        nf.read_token(cfg)
        return None
    except nf.Refused as e:
        return str(e)


write_token(0o644)
check(refused(cfg) == "token_perms", "0644 token refused")
write_token()
real = tmp / "real-token"
real.write_text(TOKEN)
real.chmod(0o600)
(tmp / "token").unlink()
(tmp / "token").symlink_to(real)
check(refused(cfg) == "token_perms", "symlinked token refused")
(tmp / "token").unlink()
check(refused(cfg) == "token_missing", "missing token refused")
write_token()
_uid = os.getuid
os.getuid = lambda: _uid() + 1
try:
    check(refused(cfg) == "token_perms", "token owned by someone else refused")
finally:
    os.getuid = _uid
(tmp / "token").write_text("")
(tmp / "token").chmod(0o600)
check(refused(cfg) == "token_format", "empty token refused")
write_token()

# ---------- backoff ----------
check([nf.backoff_base(n) for n in range(1, 10)] == [2, 4, 8, 16, 32, 64, 128, 256, 300], "backoff schedule 2..300")
check(nf.backoff_base(50) == 300, "backoff capped")
check(nf.backoff_delay(3, lambda: 1.0) == 8 and nf.backoff_delay(3, lambda: 0.0) == 4, "jitter within [50%,100%]")
check(all(nf.backoff_delay(n) <= 300 for n in range(1, 40)), "jittered delay never above the cap")

# ---------- events: WS-relative project_dir, drop outside rows, host IS NULL only ----------
hub = Hub()
con = ne.connect()
add_event(con, WSD / "PERSONAL" / "app", tool="T1")
add_event(con, WSD, tool="T2")
add_event(con, OUTSIDE, tool="T3")
add_event(con, "", tool="T4")
add_event(con, WSD / "PERSONAL" / "app", host="Other", tool="T5")  # a row ingested from another machine: never re-forwarded
add_event(con, WSD / "HOBBY" / "h", tool="T6")
fw = fresh_forwarder(hub)
res = fw.once(slow=False)
check(res["error"] is None, f"once ok: {res}")
rows = hub.rows("events")
check(sorted(r["tool"] for r in rows) == ["T1", "T2", "T6"], f"outside/empty/foreign rows dropped: {[r['tool'] for r in rows]}")
dirs = {r["tool"]: r["project_dir"] for r in rows}
check(dirs == {"T1": "PERSONAL/app", "T2": "", "T6": "HOBBY/h"}, f"project_dir is WS-relative: {dirs}")
check(all(str(tmp) not in r["raw"].decode() for r in hub.reqs), "no absolute path on the wire")
check(all(r["auth"] == f"Bearer {TOKEN}" for r in hub.reqs), "bearer token sent")
check(all(TOKEN not in r["raw"].decode() for r in hub.reqs), "token never in a body")
check(all(isinstance(r["src_id"], int) for r in rows), "src_id is the local events.id")
check(res["dropped"] == 2 and res["events"] == 3, f"counts: {res}")
n_before = len(hub.posts())
res = fw.once(slow=False)
check(len(hub.posts()) == n_before and res["events"] == 0, "cursor advanced: nothing re-sent")
all_ids = [r[0] for r in con.execute("SELECT id FROM events WHERE host IS NULL ORDER BY id")]
check(fw.state.get("events") == all_ids[-1], "cursor = last acknowledged local id")

# new events after the cursor
add_event(con, WSD / "PERSONAL" / "app", tool="T7")
fw.once(slow=False)
check([r["tool"] for r in hub.rows("events")][-1] == "T7", "new event forwarded")

# ---------- resume from the hub's cursor when local state is lost ----------
hub.reqs.clear()
hub.cursor = {"events": all_ids[1], "snapshots": 0}
fw2 = fresh_forwarder(hub)
fw2.once(slow=False)
sent_src = sorted(r["src_id"] for r in hub.rows("events"))
check(all(i > all_ids[1] for i in sent_src) and sent_src, f"resumed above the hub cursor: {sent_src}")
check(any(r["path"] == "/api/ingest/cursor" and r["method"] == "GET" for r in hub.reqs), "asked the hub for its cursor")

# ---------- offline hub: cursor unchanged, backoff counted; catch up afterwards ----------
fw3 = fresh_forwarder(hub)
fw3.once(slow=False)
cur0 = fw3.state.get("events")
hub.stop()
add_event(con, WSD / "PERSONAL" / "app", tool="T8")
res = fw3.once(slow=False)
check(res["error"] and res["error"].startswith("net_"), f"offline -> net_ error class: {res['error']}")
check(fw3.state.get("events") == cur0 and fw3.failures == 1, "cursor unchanged while offline")
fw3.once(slow=False)
check(fw3.failures == 2, "failures accumulate")
check("127.0.0.1" not in str(res["error"]), "error class carries no host")

# ---------- 413 halves the batch; 400 isolates bad rows; 401/403 refuse ----------
hub = Hub()
hub.cursor = {"events": 0, "snapshots": 0}
many = ne.connect()
for i in range(8):
    add_event(many, WSD / "PERSONAL" / "app", tool=f"B{i}")


def small_only(method, path, body):
    if path == "/api/ingest" and len(body["rows"]) > 2:
        return 413, {"error": "too large"}


hub.script = small_only
fw = fresh_forwarder(hub)
res = fw.once(slow=False)
check(res["error"] is None, f"413 handled: {res}")
check(fw.batch["events"] <= 4 and fw.batch["events"] >= 1, f"batch halved after 413: {fw.batch['events']}")
accepted_tools = {r["tool"] for r in hub.rows("events")}
check({f"B{i}" for i in range(8)} <= accepted_tools, "every row eventually delivered after 413")

hub.reqs.clear()


def one_bad(method, path, body):
    if path == "/api/ingest" and any(r.get("tool") == "B3" for r in body["rows"]):
        return 400, {"error": "bad row"}


hub.script = one_bad
fw = fresh_forwarder(hub)
res = fw.once(slow=False)
check(res["error"] is None and res["skipped"] >= 1, f"400 -> isolate, not an error: {res}")
check(fw.state.get("events") == max(r[0] for r in many.execute("SELECT id FROM events WHERE host IS NULL")),
      "cursor advanced past the bad row")
good_tools = {r["tool"] for r in hub.rows("events")}
check(good_tools >= {f"B{i}" for i in range(8) if i != 3} and "B3" in good_tools, "bad row was tried alone; others got through")
singles = [r for r in hub.posts("events") if any(x["tool"] == "B3" for x in r["body"]["rows"])]
check(any(len(r["body"]["rows"]) == 1 for r in singles), "bad row isolated in a request of its own")

for code in (401, 403, 404):  # 404 = hub has no paired machine (lone machine revoked)
    hub.reqs.clear()
    hub.script = lambda m, p, b, code=code: (code, {"error": "no"})
    fw = fresh_forwarder(hub)
    fw.state.put("events", 0)
    res = fw.once(slow=False)
    check(res["error"] == f"http_{code}" and res.get("refused"), f"{code} -> refused: {res['error']}")
    check(fw.state.get("events") == 0, f"{code}: cursor not advanced")
    check(len(hub.posts()) == 1, f"{code}: stops after the first refusal")

hub.script = lambda m, p, b: (503, {"error": "busy"})
fw = fresh_forwarder(hub)
fw.state.put("events", 0)
res = fw.once(slow=False)
check(res["error"] == "http_503" and fw.failures == 1 and fw.state.get("events") == 0, "5xx -> retry class, cursor unchanged")

# ---------- redirects are never followed (token must not leave) ----------
other = Hub()
hub.script = lambda m, p, b: (302, {}, {"Location": f"{other.url}/api/ingest"})
fw = fresh_forwarder(hub)
fw.state.put("events", 0)
res = fw.once(slow=False)
check(res["error"] == "redirect", f"redirect refused: {res['error']}")
check(not other.reqs, "token was not forwarded to the redirect target")
other.stop()

# ---------- logs: counts and error classes only ----------
hub.script = None
fw = fresh_forwarder(hub, clock=time.time)
n = {"i": 0}


class Stop(Exception):
    pass


def stop_after(limit):
    def should_stop():
        n["i"] += 1
        return n["i"] > limit
    return should_stop


buf = io.StringIO()
with redirect_stderr(buf):
    fw.run(should_stop=stop_after(2))
log = buf.getvalue()
check("sent " in log and "events=" in log, f"run logs counts: {log!r}")
check("CANARY_SUMMARY" not in log and TOKEN not in log and "127.0.0.1" not in log, "log has no row content, token or URL")
hub.script = lambda m, p, b: (401, {})
hub.reqs.clear()
fw = fresh_forwarder(hub)
fw.state.put("events", 0)
n["i"] = 0
buf = io.StringIO()
with redirect_stderr(buf):
    fw.run(should_stop=stop_after(3))
check(buf.getvalue().count("not paired / refused") == 1, "401 logged once, loop kept alive")
check(len(hub.posts()) <= 3, "401: no hammering while paused")
hub.script = None

# ---------- designs: domain filter (WORK never), change, deletion ----------
fw = fresh_forwarder(hub)
hub.reqs.clear()
write_cfg(hub.url, design_domains=["(workspace)", "PERSONAL", "HOBBY", "WORK"])
fw = nf.Forwarder(nf.load_config(), state=fw.state, sleep=lambda s: None)
fw.token = TOKEN
fw.state.put("events", 10**9)
fw.state.put("snapshots", 10**9)
res = fw.once(events=False, slow=True)
niu_usage_export = niu_usage.export_rows
rels = sorted(r["rel"] for r in hub.rows("designs"))
check(rels == ["HOBBY/h/.claude-proposals/h.md", "PERSONAL/app/.claude-proposals/p.md"], f"designs filtered, WORK refused: {rels}")
d = {r["rel"]: r for r in hub.rows("designs")}["PERSONAL/app/.claude-proposals/p.md"]
check(d["sha"] == hashlib.sha256(d["content"].encode()).hexdigest() and d["title"] == "Title p.md" and d["status"] == "proposed",
      "design row has sha/title/status/content")
check("WORK" not in json.dumps([r["body"] for r in hub.posts()]), "no WORK path anywhere on the wire")
n0 = len(hub.rows("designs"))
fw.once(events=False, slow=True)
check(len(hub.rows("designs")) == n0, "unchanged designs not re-sent")
pf = WSD / "PERSONAL/app/.claude-proposals/p.md"
pf.write_text("# Title p.md\nStatus: approved\nchanged\n")
fw.once(events=False, slow=True)
check(len(hub.rows("designs")) == n0 + 1 and hub.rows("designs")[-1]["status"] == "approved", "changed design re-sent")
pf.unlink()
fw.once(events=False, slow=True)
check(hub.rows("designs")[-1] == {"rel": "PERSONAL/app/.claude-proposals/p.md", "deleted": True}, f"deletion propagated: {hub.rows('designs')[-1]}")
n1 = len(hub.rows("designs"))
fw.once(events=False, slow=True)
check(len(hub.rows("designs")) == n1, "deletion sent once")
check("PERSONAL/app/.claude-proposals/p.md" not in fw.state.designs(), "sent_design forgets deleted docs")

# ---------- snapshots: only allowed domains, by id cursor ----------
snap = ne.connect()
for rel in ("PERSONAL/app/.claude-proposals/p.md", "WORK/w/.claude-proposals/w.md", ".claude-proposals/root.md"):
    text = f"# S\nbody {rel}\n"
    snap.execute("INSERT INTO design_snapshots(ts,rel,sha,session_id,agent_type,tool,content) VALUES(?,?,?,?,?,?,?)",
                 (time.time() - 3, rel, hashlib.sha256(text.encode()).hexdigest(), "sat1", "designer", "Write", text))
snap.commit()
hub.reqs.clear()
fw.state.put("snapshots", 0)
fw.once(events=False, slow=True)
srels = sorted(r["rel"] for r in hub.rows("snapshots"))
check(srels == [".claude-proposals/root.md", "PERSONAL/app/.claude-proposals/p.md"], f"snapshots filtered (WORK skipped): {srels}")
check(all("src_id" in r and "content" in r for r in hub.rows("snapshots")), "snapshot rows carry src_id and content")
n2 = len(hub.rows("snapshots"))
fw.once(events=False, slow=True)
check(len(hub.rows("snapshots")) == n2, "snapshots not re-sent")

# ---------- tokens: batches of unsent mkeys, then nothing ----------
fake_rows = [{"mkey": f"mk{i:04d}", "ts": time.time() - i, "model": "claude-x", "inp": 1, "outp": 2, "cw": 0, "cr": 0,
              "file": {"key": "h:" + "0" * 32, "kind": "main", "session_id": "sat1", "agent_id": "", "agent_type": "",
                       "domain": "PERSONAL", "project": "app", "source": "claude"}} for i in range(5)]


def stub_export(since, limit, after_mkeys=None):
    return [r for r in fake_rows if not (after_mkeys and after_mkeys(r["mkey"]))][:limit]


niu_usage.export_rows = stub_export
hub.reqs.clear()
res = fw.once(events=False, slow=True)
check(sorted(r["mkey"] for r in hub.rows("tokens")) == [r["mkey"] for r in fake_rows], "tokens sent")
fw.once(events=False, slow=True)
check(len(hub.rows("tokens")) == 5, "sent mkeys are not re-sent")
check(fw.state.tok_sent("mk0001") and not fw.state.tok_sent("zz"), "sent_tok recorded")

# an oversize token batch splits locally (hub body cap 256 KB)
big = [dict(fake_rows[0], mkey=f"big{i:05d}", file=dict(fake_rows[0]["file"], project="p" * 190)) for i in range(2000)]
fake_rows[:] = big
hub.reqs.clear()
fw.once(events=False, slow=True)
check(all(len(r["raw"]) <= 256 * 1024 for r in hub.posts("tokens")), "no token request above the hub body limit")
check(len(hub.rows("tokens")) == 2000, f"all oversize-batch rows delivered: {len(hub.rows('tokens'))}")
niu_usage.export_rows = niu_usage_export

# send switches
write_cfg(hub.url, send={"events": False, "tokens": False, "designs": False})
fw = nf.Forwarder(nf.load_config(), state=fw.state, sleep=lambda s: None)
hub.reqs.clear()
fw.once()
check(not hub.posts(), "send switches off -> nothing sent")

# ---------- pairing ----------
write_cfg("https://old.t.ts.net")
(tmp / "token").unlink(missing_ok=True)


def pair_script(m, p, b):
    if p == "/api/machine/pair":
        return (200, {"token": "newtoken" + "x" * 30, "label": "Vanaheim"}) if b.get("code") == "12345678" else (403, {"error": "bad"})


hub.script = pair_script
try:
    nf.pair("https://example.com", "12345678")
    check(False, "pair refuses non-tailnet urls")
except nf.ConfigError:
    check(True, "")
try:
    nf.pair(hub.url, "99999999")
    check(False, "wrong code refused")
except nf.ConfigError as e:
    check("403" in str(e) and "token" not in str(e).lower(), f"wrong code: {e}")
check(not (tmp / "token").exists(), "no token file after a failed pairing")
label = nf.pair(hub.url, "12345678")
check(label == "Vanaheim", "pair returns the label")
check(oct((tmp / "token").stat().st_mode & 0o777) == "0o600", "token file written 0600")
check((tmp / "token").read_text().strip() == "newtoken" + "x" * 30, "token stored")
check(json.loads(Path(os.environ["NIU_FORWARD_CONFIG"]).read_text())["url"] == hub.url, "url written into the config")
check(oct(Path(os.environ["NIU_FORWARD_CONFIG"]).stat().st_mode & 0o777) == "0o600", "config rewritten 0600")
check(nf.read_token({"token_file": str(tmp / "token")}).startswith("newtoken"), "stored token passes the read checks")
hub.script = lambda m, p, b: (200, {"token": "bad token with spaces", "label": "Vanaheim"})
try:
    nf.pair(hub.url, "12345678")
    check(False, "malformed pairing response refused")
except nf.ConfigError:
    check(True, "")
hub.script = lambda m, p, b: (200, {"token": "tok" * 10, "label": "bad/label"})
try:
    nf.pair(hub.url, "12345678")
    check(False, "bad label refused")
except nf.ConfigError:
    check(True, "")

# ---------- status ----------
hub.script = None
write_cfg(hub.url)
write_token()
fw = nf.Forwarder(nf.load_config(), state=fw.state, sleep=lambda s: None)
fw.once(slow=False)
s = nf.status()
check(s["paired"] and s["host"] == "127.0.0.1" and s["error"] is None and s["last_ok"], f"status: {s}")
line = nf.status_line(s)
check(line.startswith("ok: paired") and TOKEN not in line and "/api" not in line, f"status line: {line}")
write_token(0o644)
check(nf.status_line(nf.status()).startswith("problem: not paired"), "status flags a bad token file")
check(oct(Path(os.environ["NIU_FORWARD_DB"]).stat().st_mode & 0o777) == "0o600", "forward.db is 0600")

hub.stop()
print(f"forward: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
