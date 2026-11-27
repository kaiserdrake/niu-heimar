#!/usr/bin/env python3
"""End-to-end: the real `niu-dash serve` (hub, state dir A) and the real niu_forward (satellite, state dir B).

Pairs a machine, forwards events/tokens/designs, survives a hub outage, resumes from the hub's cursor when the
satellite's forward.db is lost, and reports `refused` after the machine is revoked. No network beyond 127.0.0.1."""
import hashlib
import http.client
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

SH = Path(__file__).resolve().parents[1]
tmp = Path(tempfile.mkdtemp()).resolve()
(tmp / "home").mkdir()
os.environ["HOME"] = str(tmp / "home")  # inherited by the hub subprocess too: the real home is never touched
A, B = tmp / "hub", tmp / "sat"
for d in (A, B):
    (d / "state").mkdir(parents=True)
(tmp / "no-claude").mkdir()

# hub environment (a subprocess); the satellite runs in this process with its own environment
hub_env = dict(os.environ, XDG_STATE_HOME=str(A / "state"), NIU_EVENTS_DB=str(A / "state" / "events.db"),
               NIU_USAGE_DB=str(A / "state" / "usage.db"), NIU_HOST_LABEL="midgard", CLAUDE_CONFIG_DIR=str(tmp / "no-claude"),
               NIU_CLAUDE_USAGE_API="0", NIU_CLAUDE_CREDENTIALS="/nonexistent/creds.json",
               NIU_RESEARCH_TOKEN_FILE=str(tmp / "none.token"), NIU_RESEARCH_CONFIG=str(tmp / "none.json"),
               NIU_TOPICS_TOKEN_FILE=str(A / "state" / "topics.token"))
for k in ("NIU_FORWARD_DB", "NIU_FORWARD_CONFIG", "NIU_FORWARD_TOKEN", "NIU_USAGE_COLLECT", "NIU_USAGE_TOKENS"):
    hub_env.pop(k, None)
os.environ.update(XDG_STATE_HOME=str(B / "state"), NIU_EVENTS_DB=str(B / "state" / "events.db"),
                  NIU_USAGE_DB=str(B / "state" / "usage.db"), NIU_FORWARD_DB=str(B / "state" / "forward.db"),
                  NIU_FORWARD_CONFIG=str(B / "forward.json"), NIU_FORWARD_TOKEN=str(B / "state" / "forward-token"),
                  NIU_HOST_LABEL="vanaheim", CLAUDE_CONFIG_DIR=str(tmp / "no-claude"))
sys.path.insert(0, str(SH / "lib"))
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


sha = lambda x: hashlib.sha256(x.encode()).hexdigest()  # noqa: E731
port = (lambda s: (s.bind(("127.0.0.1", 0)), s.getsockname()[1], s.close())[1])(socket.socket())
URL = f"http://127.0.0.1:{port}"
MFILE = A / "state" / "machines.json"
srv = None


def req(method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    if data:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    try:
        js = json.loads(raw)
    except ValueError:
        js = None
    return r.status, dict(r.getheaders()), js, raw


def start_hub():
    global srv
    srv = subprocess.Popen([str(SH / "bin" / "niu-dash"), "serve", "--port", str(port)], env=hub_env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            if req("GET", "/healthz")[0] == 200:
                return
        except OSError:
            time.sleep(0.1)
    raise SystemExit("hub did not start")


def stop_hub():
    srv.terminate()
    try:
        srv.wait(5)
    except subprocess.TimeoutExpired:
        srv.kill()
        srv.wait()


def inject_pair(label, code):
    d = json.loads(MFILE.read_text()) if MFILE.exists() else {"machines": []}
    d["pair"] = {"code_hash": sha(code), "expires": time.time() + 300, "attempts": 0, "label": label}
    MFILE.write_text(json.dumps(d))
    os.chmod(MFILE, 0o600)


def hub_events():
    con = sqlite3.connect(A / "state" / "events.db")
    try:
        return con.execute("SELECT src_id, event, project_dir FROM events WHERE host='Vanaheim' ORDER BY src_id").fetchall()
    finally:
        con.close()


def sessions_of(js):
    s = (js or {}).get("sessions", [])
    return list(s.values()) if isinstance(s, dict) else s


# ---------- satellite fixtures: a fake workspace, events, usage rows, two proposals ----------
WSD = tmp / "ws"
for d, name in (("PERSONAL/app", "p.md"), ("WORK/w", "w.md")):
    (WSD / d / ".claude").mkdir(parents=True)
    (WSD / d / ".claude" / "settings.local.json").write_text("{}")
    (WSD / d / ".claude-proposals").mkdir()
    (WSD / d / ".claude-proposals" / name).write_text(f"# Title {name}\nStatus: proposed\nbody of {name}\n")
ne.WS = WSD
PDIR = WSD / "PERSONAL" / "app"
SID = str(uuid.uuid4())
sat = ne.connect()
COLS = "ts,event,session_id,domain,project,project_dir,agent_id,agent_type,tool,tool_use_id,summary"


def add_event(event, tool=None, summary=""):
    sat.execute(f"INSERT INTO events({COLS}) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (time.time() - 5, event, SID, "PERSONAL", "app", str(PDIR), None, None, tool, None, summary))
    sat.commit()


add_event("SessionStart")
add_event("UserPromptSubmit")
add_event("PreToolUse", "Bash", "ls")

uc = niu_usage.connect()
KEY = "h:" + "ab" * 16
uc.execute("INSERT INTO tok_files(id, path, kind, session_id, domain, project) VALUES(1,?,?,?,?,?)", (KEY, "main", SID, "PERSONAL", "app"))
uc.execute("INSERT INTO tok_models(id, name, family) VALUES(1,?,?)", ("claude-sonnet-4-5", "sonnet"))
now = time.time()
for i in range(3):
    uc.execute("INSERT INTO tok_msgs VALUES(?,?,?,?,?,?,?,?)", (1000 + i, now - 60 - i, 1, 1, 100, 10, 5, 1))
uc.commit()
uc.close()
EXPECT = {"input": 300, "output": 30, "messages": 3}


def new_forwarder():
    return nf.Forwarder(nf.load_config(), state=nf.State(), sleep=lambda s: None)


# ---------- hub up, pair through the real route ----------
start_hub()
try:
    inject_pair("Vanaheim", "12345678")
    label = nf.pair(URL, "12345678")
    check(label == "Vanaheim", f"pair() returns the hub-assigned label: {label}")
    check(oct(Path(os.environ["NIU_FORWARD_TOKEN"]).stat().st_mode & 0o777) == "0o600", "token file is 0600")
    check(json.loads(MFILE.read_text())["machines"][0]["label"] == "Vanaheim", "hub stored the machine")

    fw = new_forwarder()
    res = fw.once()  # a fresh pairing starts from the hub's high-water marks (0): everything is forwarded
    check(res["error"] is None, f"first pass: {res}")
    check(res["events"] == 3 and res["tokens"] == 3 and res["designs"] == 1, f"first pass counts: {res}")

    # ---------- hub views ----------
    st, _, js, _ = req("GET", "/api/state?host=Vanaheim")
    mine = sessions_of(js)
    check(st == 200 and len(mine) == 1, f"/api/state?host=Vanaheim shows one session: {st} {mine}")
    if mine:
        s = mine[0]
        check(s.get("host") == "Vanaheim", f"session host: {s.get('host')}")
        pd = s.get("project_dir") or ""
        check(not pd.startswith("/") and str(tmp) not in pd, f"project_dir is not an absolute path: {pd!r}")
    check("Vanaheim" in (js or {}).get("hosts", []), "hosts lists Vanaheim")
    st, _, js, _ = req("GET", "/api/state?host=midgard")
    check(st == 200 and not sessions_of(js), "the hub's own view does not include the remote session")

    st, _, js, _ = req("GET", "/api/usage/tokens?host=Vanaheim&range=7d")
    t = (js or {}).get("totals", {})
    check(st == 200 and js["state"] == "ok" and t.get("input") == EXPECT["input"] and t.get("output") == EXPECT["output"]
          and t.get("messages") == EXPECT["messages"], f"token totals for Vanaheim: {st} {js and js.get('state')} {t}")
    st, _, js, _ = req("GET", "/api/usage/tokens?host=midgard&range=7d")
    check(st == 200 and (js or {}).get("totals", {}).get("messages") == 0, "hub-only token view is empty")

    # paired browser cookie (real `niu-dash pair` on a tty, as web_test.py does)
    r = subprocess.run(["script", "-qec", f"{SH / 'bin' / 'niu-dash'} pair --name e2e-browser", "/dev/null"], env=hub_env,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    m = re.search(r"Pairing code: (\d{3}) (\d{3})", r.stdout)
    check(m is not None, "browser pairing code printed")
    st, hd, _, _ = req("POST", "/api/pair", {"code": "".join(m.groups()) if m else "0"}, {"X-Niu-CSRF": "1", "Origin": URL})
    cookie = hd.get("Set-Cookie", "").split(";")[0]
    check(st == 200 and cookie, f"browser paired: {st}")
    st, _, js, _ = req("GET", "/api/designs", headers={"Cookie": cookie})
    docs = [d for d in (js or {}).get("docs", []) if d.get("host") == "Vanaheim"]
    rels = sorted(d["rel"] for d in docs)
    check(st == 200 and rels == ["PERSONAL/app/.claude-proposals/p.md"], f"/api/designs lists the PERSONAL doc for Vanaheim: {st} {rels}")
    check("w.md" not in json.dumps(js), "the WORK doc is not on the hub")

    # ---------- hub down: error, cursor unchanged; then catch-up without duplicates ----------
    ids = [e[0] for e in hub_events()]
    check(ids == [1, 2, 3], f"hub holds src_ids 1..3: {ids}")
    cur0 = fw.state.get("events")
    check(int(cur0) == 3, f"events cursor after the first pass: {cur0}")
    stop_hub()
    add_event("PreToolUse", "Edit", "a.py")
    add_event("PostToolUse", "Edit", "a.py")
    res = fw.once()
    check(res["error"] is not None and not res.get("refused") and res["events"] == 0, f"hub down: {res}")
    check(fw.state.get("events") == cur0, "cursor unchanged after a failed pass")
    start_hub()
    res = fw.once()
    check(res["error"] is None and res["events"] == 2, f"after the restart the forwarder catches up: {res}")
    ev = hub_events()
    check([e[0] for e in ev] == [1, 2, 3, 4, 5], f"no duplicate events on the hub: {[e[0] for e in ev]}")
    check(all(not (e[2] or "").startswith("/") for e in ev), "hub project_dirs are workspace-relative")

    # ---------- forward.db lost: resume from the hub cursor, not from zero ----------
    tok = Path(os.environ["NIU_FORWARD_TOKEN"]).read_text().strip()
    st, _, js, _ = req("GET", "/api/ingest/cursor", headers={"Authorization": f"Bearer {tok}"})
    check(st == 200 and js["cursor"]["events"] == 5, f"hub cursor reply shape: {st} {js}")
    fw.state.close()
    for suffix in ("", "-wal", "-shm"):
        Path(os.environ["NIU_FORWARD_DB"] + suffix).unlink(missing_ok=True)
    time.sleep(3)  # let the hub's per-machine rate bucket refill
    fw = new_forwarder()
    sent_events = []
    orig = fw._send
    fw._send = lambda kind, rows: (sent_events.append(len(rows)) if kind == "events" else None, orig(kind, rows))[1]
    check(fw.state.get("events") is None, "no local cursor after deleting forward.db")
    res = fw.once()
    check(res["error"] is None, f"pass without forward.db: {res}")
    check(fw.state.get("events") == 5 and not sent_events,
          f"resumed from the hub cursor, nothing re-sent: cursor={fw.state.get('events')} sent={sent_events}")
    add_event("Stop")
    time.sleep(2)
    res = fw.once()
    check(res["error"] is None and res["events"] == 1 and sent_events == [1], f"only the new event is sent: {res} {sent_events}")
    ids = [e[0] for e in hub_events()]
    check(ids == [1, 2, 3, 4, 5, 6], f"still no duplicates after the resume: {ids}")
    st, _, js, _ = req("GET", "/api/usage/tokens?host=Vanaheim&range=7d")
    check(js["totals"]["messages"] == EXPECT["messages"] and js["totals"]["input"] == EXPECT["input"],
          f"tokens not double counted after the re-send: {js['totals']}")

    # ---------- revoke: the forwarder is refused ----------
    inject_pair("Asgard", "87654321")  # a second machine keeps the ingest routes alive (401, not 404, for a revoked token)
    st, _, js, _ = req("POST", "/api/machine/pair", {"code": "87654321"})
    check(st == 200 and (js or {}).get("label") == "Asgard", f"second machine paired: {st}")
    r = subprocess.run([str(SH / "bin" / "niu-dash"), "machine", "revoke", "Vanaheim"], env=hub_env, capture_output=True, text=True)
    check("revoked 1" in r.stdout, f"revoke: {r.stdout!r} {r.stderr!r}")
    add_event("Stop")
    time.sleep(2)
    res = fw.once()
    check(res.get("refused") is True and res["error"] == "http_401", f"revoked machine is refused: {res}")
    check(fw.state.get("events") == 6, "cursor unchanged when refused")
finally:
    if srv and srv.poll() is None:
        stop_hub()

print(f"e2e_multi: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
