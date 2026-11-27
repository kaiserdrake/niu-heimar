#!/usr/bin/env python3
"""End-to-end tests for niu-dash (web dashboard). Uses a temp event store and a fake gh; never touches GitHub."""
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
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
SH = WS / ".claude-workspace"
tmp = Path(tempfile.mkdtemp())
env = dict(os.environ, NIU_EVENTS_DB=str(tmp / "state" / "events.db"),
           FAKE_GH_LOG=str(tmp / "gh.log"), FAKE_PR_FILE=str(tmp / "pr_state"), NIU_HOST_LABEL="testhost",
           NIU_USAGE_COLLECT="0", NIU_USAGE_DB=str(tmp / "state" / "usage.db"), CLAUDE_CONFIG_DIR=str(tmp / "no-claude"),  # no background sampling or transcript scanning in tests
           NIU_CLAUDE_USAGE_API="0", NIU_CLAUDE_CREDENTIALS="/nonexistent/creds.json",  # never the real login
           NIU_RESEARCH_TOKEN_FILE=str(tmp / "no-such-research.token"), NIU_RESEARCH_CONFIG=str(tmp / "no-such-research.json"),  # nor the real token
           NIU_TOPICS_TOKEN_FILE=str(tmp / "state" / "topics-test.token"))
(tmp / "bin").mkdir()
(tmp / "pr_state").write_text("green")
gh = tmp / "bin" / "gh"
gh.write_text(r'''#!/usr/bin/env bash
echo "$*" >>"$FAKE_GH_LOG"
st="$(cat "$FAKE_PR_FILE")"
case "$st" in
  green)   roll='[{"__typename":"CheckRun","name":"lint","status":"COMPLETED","conclusion":"SUCCESS"}]' ;;
  failing) roll='[{"__typename":"CheckRun","name":"lint","status":"COMPLETED","conclusion":"FAILURE"}]' ;;
esac
pr='{"number":7,"title":"feat: thing","url":"https://github.com/o/r/pull/7","isDraft":false,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","updatedAt":"2026-11-06T00:00:00Z","headRefName":"feat/x","author":{"login":"me"},"statusCheckRollup":'"$roll"'}'
case "$1 $2" in
  "pr list") echo "[$pr]" ;;
  "pr view") echo "$pr" ;;
  "pr merge") echo merged ;;
  *) exit 1 ;;
esac
''')
gh.chmod(0o755)
env["PATH"] = f"{tmp / 'bin'}:{env['PATH']}"
CWD = str(WS / "PERSONAL" / ".niu-test-web" / "app")
Path(CWD).mkdir(parents=True, exist_ok=True)
(Path(CWD) / ".git").mkdir(exist_ok=True)

passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


def ev(event, **kw):
    data = {"hook_event_name": event, "session_id": "sess-web1", "cwd": CWD, **kw}
    subprocess.run([str(SH / "hooks" / "agent-events.py")], input=json.dumps(data), text=True, env=env, check=True)


# Seed: session spawns two reviewers in parallel, one sends a message
ev("SessionStart"); ev("UserPromptSubmit")
ev("PreToolUse", tool_name="Edit", tool_input={"file_path": f"{CWD}/a.py"}, tool_use_id="t1")
ev("PreToolUse", tool_name="Agent", tool_input={"subagent_type": "security-reviewer", "description": "Review auth change"}, tool_use_id="t2")
ev("PreToolUse", tool_name="Agent", tool_input={"subagent_type": "test-reviewer", "description": "Run the tests"}, tool_use_id="t3")
ev("SubagentStart", agent_id="ag1", agent_type="security-reviewer")
ev("SubagentStart", agent_id="ag2", agent_type="test-reviewer")
ev("PreToolUse", agent_id="ag2", agent_type="test-reviewer", tool_name="Bash", tool_input={"command": "pytest -q"}, tool_use_id="t4")
ev("PreToolUse", tool_name="Write", tool_input={"file_path": f"{CWD}/.claude-proposals/t.md"}, tool_use_id="t6")
ev("PreToolUse", tool_name="SendMessage", tool_input={"to": "deploy-bot", "message": "hi"}, tool_use_id="t5")

# Design docs fixture: a linked project (settings.local.json) with one readable and one protected doc
DD = Path(CWD) / ".claude-proposals"
DD.mkdir(parents=True, exist_ok=True)
(Path(CWD) / ".claude").mkdir(exist_ok=True)
(Path(CWD) / ".claude" / "settings.local.json").write_text("{}")
(Path(CWD) / ".claude" / "protected-paths").write_text(".claude-proposals/secret.md\n")
(DD / "t.md").write_text("# Test design\nStatus: proposed\n\nHello **world**.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
(DD / "secret.md").write_text("# Secret\n\nnot for the browser\n")
# lib/niu_designs.py is written in parallel; when it is missing, serve a minimal stand-in so the routes can still be tested.
if not (SH / "lib" / "niu_designs.py").exists():
    (tmp / "stubs").mkdir()
    (tmp / "stubs" / "niu_designs.py").write_text(r'''
import hashlib, os, sqlite3
from pathlib import Path
def index(extra_dirs=(), base=None):
    out = {}
    for d in extra_dirs:
        for f in sorted((Path(d) / ".claude-proposals").glob("*.md")):
            if f.name == "secret.md":
                continue
            rel = str(f.relative_to(base or os.environ["NIU_STUB_WS"]))
            i = "d" + hashlib.sha256(rel.encode()).hexdigest()[:16]
            out[i] = {"id": i, "domain": "PERSONAL", "project": "app", "name": f.name, "title": "Test design", "status": "proposed", "mtime": f.stat().st_mtime, "rel": rel, "project_dir": str(d), "path": str(f)}
    return out
def _snaps(e):
    con = sqlite3.connect(os.environ["NIU_EVENTS_DB"])
    try:
        return con.execute("SELECT id, ts, content FROM design_snapshots WHERE rel = ?", (e["rel"],)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
def revisions(e):
    return [{"rev": "work", "ts": 2, "label": "Working copy", "kind": "work", "also": []}] + [{"rev": f"snap:{i}", "ts": t, "label": "Draft", "kind": "snap", "also": []} for i, t, _ in _snaps(e)]
def revision_text(e, rev):
    if rev == "work":
        return Path(e["path"]).read_text()
    return next((c for i, _, c in _snaps(e) if f"snap:{i}" == rev), None)
def parse(t):
    return [{"t": "p", "c": [ln]} for ln in t.splitlines() if ln]
def outline(b):
    return []
def diff(a, b):
    return {"stats": {"added": 1, "removed": 1, "sections": []}, "lines": [], "blocks": []}
def session_docs(con, sid, project_dir, idx):
    rows = con.execute("SELECT summary FROM events WHERE session_id = ? AND summary LIKE '%.claude-proposals/%.md'", (sid,)).fetchall()
    return [i for i, e in idx.items() if any(r[0].endswith(e["rel"].split("/.claude-proposals/")[-1]) or r[0].endswith(".claude-proposals/" + e["name"]) for r in rows)]
''')
    env["PYTHONPATH"] = str(tmp / "stubs")
    env["NIU_STUB_WS"] = str(WS)

port = (lambda s: (s.bind(("127.0.0.1", 0)), s.getsockname()[1], s.close())[1])(socket.socket())
srv = subprocess.Popen([str(SH / "bin" / "niu-dash"), "serve", "--port", str(port)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
ORIGIN = f"http://127.0.0.1:{port}"


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


POSTH = {"X-Niu-CSRF": "1", "Origin": ORIGIN}

try:
    for _ in range(50):
        try:
            if req("GET", "/healthz")[0] == 200:
                break
        except OSError:
            time.sleep(0.1)

    # Server only listens on loopback
    out = subprocess.run(["ss", "-ltnH", f"sport = :{port}"], capture_output=True, text=True).stdout
    local = [ln.split()[3] for ln in out.splitlines() if ln.strip()]
    check(local and all(a.startswith("127.0.0.1:") for a in local), f"binds loopback only: {out}")

    # Page + security headers
    st, hd, _, raw = req("GET", "/")
    check(st == 200 and b"niu-heimar" in raw, "index page served")
    check("default-src 'self'" in hd.get("Content-Security-Policy", ""), "CSP header")
    check(req("GET", "/../bin/niu-dash")[0] == 404, "no path traversal")
    css = req("GET", "/style.css")[3]
    check(b"[hidden] { display: none !important; }" in css, "hidden attribute always wins over display rules")
    check(b'id="menu"' in raw and b'id="pairform"' in raw, "pairing lives in the menu")
    check(b'class="leftcol"' in raw and b'id="needspanel"' in raw and b'id="prs"' in raw and b'id="llmcard"' in raw,
          "needs-you / PRs / LLMs live in the left column")
    check(b'id="pulltab"' not in raw and b'id="drawer"' not in raw, "no pull-out drawer")
    check(b'id="tab-events"' in raw and b'id="feed"' in raw, "event feed has its own tab")
    check(b'id="tab-research"' in raw and b'id="rslanes"' in raw and b'id="rsdot"' in raw and b"p-research" in raw, "Research tab markup")
    check(b'style="' not in raw and b"onclick=" not in raw and b"<script>" not in raw, "no inline styles, handlers or scripts in the page")
    check(hd.get("Content-Security-Policy", "").startswith("default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'"),
          "CSP header unchanged")
    check(b'data-view="research"' in css, "Research view styles served")
    # Weekly usage panel (Events tab): markup, route, and the render code never uses innerHTML
    check(b"p-usage" in raw and b'id="usagegraph"' in raw and b'id="usagesum"' in raw and b'aria-live="polite"' in raw, "Weekly usage panel markup")
    check(raw.index(b'id="prs"') < raw.index(b'id="usagebody"') < raw.index(b'id="feed"'), "Weekly usage sits between Pull requests and the Event feed")
    check(b".p-usage" in css and b'"left usage"' in css and b"tokens" not in css.split(b"grid-template-areas")[1].split(b";")[0] and b".usagegroup" in css and b"max-width: 1199px" in css, "Events grid: one LLMs usage area; weekly | tokens side by side inside it, stacked under 1200px")
    check(b"grid-template-rows: subgrid" in css and b'id="tokupd"' not in raw, "Weekly and Token usage headers share one row; the token status is not in its header")
    usage_html = raw.split(b'id="usagepanel"')[1].split(b"</section>")[0]
    check(b'id="tokpanel"' in usage_html and b'id="usagegraph"' in usage_html and b'id="toktoggle"' not in raw and usage_html.count(b"aria-expanded") == 1, "Weekly and Token usage share one panel and one collapse button")
    check(b'id="tokpanel"' in raw and b'id="tokbody"' in raw and b'id="toksum"' in raw and b'id="tokmetric"' in raw and b'aria-live="polite"' in raw, "Token usage panel markup")
    tokp = raw.split(b'id="tokpanel"')[1].split(b"</section>")[0]
    check(all(b'id="tok-s-%s"' % k in tokp for k in (b"all", b"claude", b"local")) and b'aria-label="Token usage source"' in tokp and b".fam-local" in css, "Token usage source switch (All / Claude / Local) in the sub-header and .fam-local in the CSS")
    check(raw.index(b'id="usagebody"') < raw.index(b'id="tokbody"') < raw.index(b'id="feed"'), "Token usage sits after Weekly usage and before the Event feed")
    check(b" onclick=" not in raw and b" style=" not in raw.split(b'id="tokpanel"')[1].split(b"</section>")[0], "no inline handlers or styles in the Token usage markup")
    appjs = req("GET", "/app.js")[3].decode()
    ub = appjs[appjs.index("// ---------- Weekly usage panel"):appjs.index("// ---------- Research tab")]
    check("innerHTML" not in ub and "insertAdjacentHTML" not in ub and "title=" not in ub and "setAttribute(\"title\"" not in ub, "usage render code: no innerHTML, no native titles")
    check(re.search(rb'<button id="usagezoomreset"[^>]*\bhidden\b', raw) is not None, "Reset zoom button is in the page and starts hidden")
    check(b"touch-action: pan-y" in css, "usage graph keeps vertical touch scrolling (touch-action: pan-y)")
    check('"llmcard", "usagegraph", "tokbody"' in appjs, "usage graph and token panel are bound to the themed tooltip")
    st, _, js, _ = req("GET", "/api/usage/history")
    check(st == 200 and js["state"] == "disabled", f"usage history: disabled when collection is off: {js}")
    for q in ("?days=-1", "?days=abc", "?days=%3Cscript%3E", "?days=9999", "?range=../../x"):
        check(req("GET", "/api/usage/history" + q)[0] == 200, f"usage history {q} answered normally")
    check(req("POST", "/api/usage/history", {}, POSTH)[0] == 404, "no POST on /api/usage/*")
    st, _, js, _ = req("GET", "/api/usage/tokens")
    check(st == 200 and js["state"] == "disabled" and js["range"] == "7d" and js["domain"] == "", f"usage tokens: disabled when collection is off: {js}")
    for q in ("?range=../../x", "?range=%3Cscript%3E&domain=%3Cscript%3E", "?range=30d&domain=" + "a" * 3000, "?domain=bogus"):
        check(req("GET", "/api/usage/tokens" + q)[0] == 200, f"usage tokens {q[:40]} answered normally")
    check(req("GET", "/api/usage/tokens?range=today")[2]["range"] == "today", "range=today accepted")
    check(req("GET", "/api/usage/tokens?source=local")[0] == 200 and req("GET", "/api/usage/tokens?source=%3Cscript%3E")[0] == 200, "usage tokens: source param answered normally")
    check("&source=" in appjs and "niu.tokens.source" in appjs, "front end sends source only when set and stores the choice")
    check(req("POST", "/api/usage/tokens", {}, POSTH)[0] == 404, "no POST on /api/usage/tokens")
    check(b"tokens" in appjs.encode() and "/api/usage/tokens?range=" in appjs, "front end uses /api/usage/tokens")
    st, _, js, rawr = req("GET", "/api/research")
    check(st == 200 and js["state"] == "not_configured" and js["data"] is None, f"/api/research without a token file: {js and js.get('state')}")
    check(req("POST", "/api/research/refresh", {}, {"Origin": ORIGIN})[0] == 403, "research refresh without CSRF header -> 403")
    check(req("POST", "/api/research/refresh", {}, {"X-Niu-CSRF": "1", "Origin": "https://evil.example"})[0] == 403, "research refresh cross-origin -> 403")
    check(req("POST", "/api/research/refresh", {}, POSTH)[0] == 200, "research refresh with CSRF header -> 200 (no pairing needed)")
    check(b"mission control" not in raw.lower().replace(b"<title>", b""), "no 'mission control' subtitle")
    check(b'id="rsview-topics"' in raw and b'id="rsview-status"' in raw and b'id="tpdlg"' in raw and b'id="tpbody"' in raw and b'id="tplist"' in raw,
          "Topics markup: sub-view switch, list and dialog")
    check(b"tpswitch" in css and b".tprow" in css, "Topics styles served")
    # Topics routes: every one needs the paired-device cookie, the list GET included
    st, _, js, rawt = req("GET", "/api/topics")
    check(st == 401 and js["state"] == "not_paired" and js["topics"] == [], f"GET /api/topics without a cookie -> 401 not_paired: {st}")
    check(req("GET", "/api/topics", headers=POSTH)[0] == 401, "forged CSRF / origin headers do not replace the cookie on the list GET")
    tbody = {"agent_type": "general", "name": "x", "keywords": ["k"]}
    check(req("POST", "/api/topics/create", tbody, {"Origin": ORIGIN})[0] == 403, "topics create without CSRF header -> 403")
    check(req("POST", "/api/topics/create", tbody, {"X-Niu-CSRF": "1", "Origin": "https://evil.example"})[0] == 403, "topics create cross-origin -> 403")
    for p, b in (("/api/topics/create", tbody), ("/api/topics/update", {"id": "x", "fields": {}}), ("/api/topics/refresh", {})):
        check(req("POST", p, b, POSTH)[0] == 401, f"{p} with CSRF and origin but no cookie -> 401")

    # State: links between session and subagents, tasks, messages
    st, _, js, _ = req("GET", "/api/state")
    s = next((x for x in js["sessions"] if x["id"] == "sess-web1"), None)
    check(s is not None, "session in state")
    subs = {a["type"]: a for a in s["subagents"]} if s else {}
    check(subs.get("security-reviewer", {}).get("task") == "Review auth change", "spawn task linked (security)")
    check(subs.get("test-reviewer", {}).get("task") == "Run the tests", "spawn task linked (tests)")
    check(subs.get("test-reviewer", {}).get("parent") == "sess-web1", "parent is the session")
    check(subs.get("test-reviewer", {}).get("current", [None])[0] == "Bash", "subagent current tool")
    check(s and s["messages"] and s["messages"][0]["to"] == "deploy-bot", "message edge")
    check(s and s["state"] == "working" and s["edited"], "session state")
    short = req("GET", "/api/state?feed=20")[2]["feed"]
    full = req("GET", "/api/state?feed=500")[2]["feed"]
    check(len(short) <= 20 and len(full) >= len(short), f"feed length parameter: {len(short)} / {len(full)}")
    check(req("GET", "/api/state?feed=abc")[0] == 200, "bad feed parameter falls back")
    check(js.get("host") == "Testhost", f"host label in state: {js.get('host')}")

    # Agent catalog: unpaired GET, no absolute paths, small
    st, _, ag, raw = req("GET", "/api/agents?sid=sess-web1")
    names = {a["name"] for a in (ag or {}).get("agents", [])}
    check(st == 200 and {"designer", "implementer", "security-reviewer", "test-reviewer", "Explore", "Plan", "general-purpose"} <= names,
          f"/api/agents lists the agents: {st} {sorted(names)}")
    check("local_search" in {t["name"] for t in (ag or {}).get("local_tools", [])} and (ag or {}).get("sid") == "sess-web1", "/api/agents lists the local tools")
    check(str(WS).encode() not in raw and b"/home/" not in raw and len(raw) < 16384, f"/api/agents has no paths and is small ({len(raw)} bytes)")
    check(req("GET", "/api/agents?sid=../x")[0] == 400 and req("GET", "/api/agents")[0] == 400, "/api/agents bad sid -> 400")
    check(req("GET", "/api/agents?sid=" + "a" * 81)[0] == 400, "/api/agents long sid -> 400")
    check(req("GET", "/api/agents?sid=nope-unknown")[0] == 404, "/api/agents unknown sid -> 404")
    check(req("POST", "/api/agents", {}, POSTH)[0] in (404, 405, 501), "/api/agents is GET only")
    agents_path = tmp / "agents.json"
    agents_path.write_text(json.dumps(ag))

    # SSE emits a snapshot
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/api/stream", headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    line = r.fp.readline()
    check(r.status == 200 and line.startswith(b"data: ") and b"sess-web1" in line, "SSE snapshot")
    c.close()

    # PRs from fake gh
    time.sleep(1.5)
    st, _, js, _ = req("GET", "/api/prs")
    prs = [p for r in js["repos"] for p in r["prs"]]
    check(any(p["green"] for p in prs) or js["repos"] == [], "PR list shape")
    ev("Notification", notification_type="permission_prompt", message="needs permission")
    time.sleep(1.1)
    st, _, js, _ = req("GET", "/api/state")
    kinds = {a["kind"] for a in js.get("actions", [])}
    check("waiting" in kinds, f"waiting session is an action: {kinds}")
    check(js["actions"] and js["actions"][0]["priority"] == 3, "highest priority first")

    # Merge guards
    body = {"repo": "o/r", "number": 7, "confirm": "o/r#7"}
    check(req("POST", "/api/prs/merge", body, {"Origin": ORIGIN})[0] == 403, "merge without CSRF header -> 403")
    check(req("POST", "/api/prs/merge", body, {"X-Niu-CSRF": "1", "Origin": "https://evil.example"})[0] == 403, "cross-origin -> 403")
    check(req("POST", "/api/prs/merge", body, POSTH)[0] == 401, "merge without pairing -> 401")

    # Pairing: refused without a terminal, works with a code from a real tty
    r = subprocess.run([str(SH / "bin" / "niu-dash"), "pair"], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    check(r.returncode != 0, "pair refused without tty")
    check(req("POST", "/api/pair", {"code": "000000"}, POSTH)[0] == 400, "pair without pairing in progress")
    r = subprocess.run(["script", "-qec", f"{SH / 'bin' / 'niu-dash'} pair --name test-laptop", "/dev/null"],
                       env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    m = re.search(r"Pairing code: (\d{3}) (\d{3})", r.stdout)
    check(m is not None, f"pair prints a code: {r.stdout!r}")
    code = "".join(m.groups()) if m else ""
    wrong = "111111" if code != "111111" else "222222"
    check(req("POST", "/api/pair", {"code": wrong}, POSTH)[0] == 403, "wrong code -> 403")
    st, hd, js, _ = req("POST", "/api/pair", {"code": code}, POSTH)
    cookie = hd.get("Set-Cookie", "")
    check(st == 200 and "HttpOnly" in cookie and "SameSite=Strict" in cookie, f"pair ok + cookie flags: {cookie}")
    tok = cookie.split(";")[0]
    auth = {**POSTH, "Cookie": tok}
    store = json.loads((tmp / "state" / "web-sessions.json").read_text())
    check(tok.split("=", 1)[1] not in json.dumps(store), "only token hash stored")
    check(oct((tmp / "state" / "web-sessions.json").stat().st_mode)[-3:] == "600", "credential file mode 600")
    check(req("GET", "/api/me", headers={"Cookie": tok})[2]["paired"] is True, "/api/me paired")

    # Design docs: every route is paired only; ids and revisions are validated; no absolute paths leave the server
    for p in ("/api/designs", "/api/designs/doc?id=d0123456789abcdef", "/api/designs/diff?id=d0123456789abcdef&a=work&b=work"):
        check(req("GET", p)[0] == 401, f"{p} without a cookie -> 401")
        check(req("GET", p, headers=POSTH)[0] == 401, f"{p} with forged CSRF/origin headers but no cookie -> 401")
    st, _, js, _ = req("GET", "/api/designs")
    check(js and js.get("state") == "not_paired" and js.get("docs") == [], "unpaired list says not_paired and carries no docs")
    ck = {"Cookie": tok}
    con = sqlite3.connect(env["NIU_EVENTS_DB"])
    con.execute("CREATE TABLE IF NOT EXISTS design_snapshots (id INTEGER PRIMARY KEY, ts REAL NOT NULL, rel TEXT NOT NULL, sha TEXT NOT NULL,"
                " session_id TEXT, agent_type TEXT, tool TEXT, content TEXT NOT NULL)")
    rel = str((DD / "t.md").relative_to(WS))
    con.execute("INSERT INTO design_snapshots (ts, rel, sha, session_id, agent_type, tool, content) VALUES (?,?,?,?,?,?,?)",
                (time.time() - 60, rel, "x" * 8, "sess-web1", "designer", "Write", "# Test design\nStatus: proposed\n\nHello.\n"))
    con.commit()
    con.close()
    st, hd, js, rawd = req("GET", "/api/designs", headers=ck)
    mine = [d for d in (js or {}).get("docs", []) if d["name"] == "t.md"]
    check(st == 200 and len(mine) == 1, f"paired list has t.md: {st}")
    check(all(d["name"] != "secret.md" for d in (js or {}).get("docs", [])), "protected doc is not listed")
    check(mine and set(("id", "title", "status", "rel", "revisions")) <= set(mine[0]) and "project_dir" not in mine[0], "list entry shape, no project_dir")
    did = mine[0]["id"] if mine else "d0000000000000000"
    check(str(WS).encode() not in rawd and b"/home/" not in rawd, "list has no absolute path")
    check("default-src 'self'" in hd.get("Content-Security-Policy", ""), "CSP unchanged on /api/designs")
    st, _, js, _ = req("GET", "/api/designs?sid=sess-web1", headers=ck)
    check(st == 200 and did in js.get("session_docs", []), f"session_docs lists the doc the session wrote: {js and js.get('session_docs')}")
    st, _, js, _ = req("GET", "/api/designs?sid=nope-9", headers=ck)
    check(st == 200 and js.get("session_docs") == [], "unknown sid -> empty session_docs")
    check(req("GET", "/api/designs?sid=../x", headers=ck)[0] == 400, "bad sid -> 400")
    st, _, js, rawd = req("GET", f"/api/designs/doc?id={did}", headers=ck)
    check(st == 200 and js["rev"] == "work" and js["blocks"] and "outline" in js and js["revisions"], f"doc defaults to the working copy: {st}")
    check(str(WS).encode() not in rawd and b"/home/" not in rawd and b"project_dir" not in rawd, "doc has no absolute path")
    snap = next((r["rev"] for r in (js or {}).get("revisions", []) if r["rev"].startswith("snap:")), None)
    check(snap is not None, "draft snapshot appears in the revision list")
    check(req("GET", f"/api/designs/doc?id={did}&rev={snap}", headers=ck)[0] == 200, "doc at a snapshot revision")
    for bad in ("../x", "D0123456789abcdef", "d123", "", "d0123456789abcdef/../x"):
        check(req("GET", f"/api/designs/doc?id={bad}", headers=ck)[0] == 400, f"bad id {bad!r} -> 400")
    check(req("GET", "/api/designs/doc", headers=ck)[0] == 400, "missing id -> 400")
    for bad in ("git:zzz", "snap:x", "../work", "git:" + "A" * 40, "WORK"):
        check(req("GET", f"/api/designs/doc?id={did}&rev={bad}", headers=ck)[0] == 400, f"bad rev {bad!r} -> 400")
    check(req("GET", "/api/designs/doc?id=d0000000000000000", headers=ck)[0] == 404, "unknown id -> 404")
    check(req("GET", f"/api/designs/doc?id={did}&rev=snap:999999", headers=ck)[0] == 404, "unknown snapshot -> 404")
    check(req("GET", f"/api/designs/doc?id={did}&rev=git:{'a' * 40}", headers=ck)[0] == 404, "git rev not in the list -> 404")
    st, _, js, rawd = req("GET", f"/api/designs/diff?id={did}&a={snap}&b=work", headers=ck)
    check(st == 200 and "stats" in js and "lines" in js and "blocks" in js and js["a"] == snap and js["b"] == "work", f"diff snapshot..work: {st}")
    check(str(WS).encode() not in rawd and b"/home/" not in rawd, "diff has no absolute path")
    check(req("GET", f"/api/designs/diff?id={did}&a=work", headers=ck)[0] == 400, "diff without b -> 400")
    check(req("GET", f"/api/designs/diff?id={did}&a=work&b=nope", headers=ck)[0] == 400, "diff bad rev -> 400")
    check(req("GET", f"/api/designs/diff?id=zz&a=work&b=work", headers=ck)[0] == 400, "diff bad id -> 400")
    check(req("GET", f"/api/designs/diff?id=d0000000000000000&a=work&b=work", headers=ck)[0] == 404, "diff unknown id -> 404")
    check(req("GET", f"/api/designs/diff?id={did}&a=work&b=snap:999999", headers=ck)[0] == 404, "diff unknown rev -> 404")

    # Topics through a fake valkyrie-dispatch: a paired browser can list and create; CSP is unchanged
    import threading  # noqa: E401
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class FakeRD(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, obj):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(200, {"name": "t", "scope": "topics"} if self.path.startswith("/api/v1/auth/whoami") else [])

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            self._send(201, {"id": "0000aaaa-0000-4000-8000-00000000aaaa", "is_active": True, "updated_at": "2026-11-07T09:00:00Z", **data})
    fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeRD)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    Path(env["NIU_RESEARCH_CONFIG"]).write_text(json.dumps({"enabled": True, "url": f"http://127.0.0.1:{fake.server_address[1]}", "timeout_secs": 2}))
    tfile = Path(env["NIU_TOPICS_TOKEN_FILE"])
    tfile.parent.mkdir(parents=True, exist_ok=True)
    tfile.write_text("rd_pat_WEBTEST_" + "t" * 24 + "\n")
    os.chmod(tfile, 0o600)
    cookie_only = {"Cookie": tok}
    st, _, js, _ = req("GET", "/api/topics", headers=cookie_only)
    check(st == 200 and js["state"] == "ok" and js["topics"] == [], f"paired GET /api/topics: {st} {js and js.get('state')}")
    st, _, js, _ = req("POST", "/api/topics/create", {"agent_type": "general", "name": "Web test", "keywords": ["k"]}, auth)
    check(st == 200 and js["ok"] and js["topic"]["name"] == "Web test", f"paired create through the fake: {st} {js}")
    st, _, js, _ = req("POST", "/api/topics/create", {"agent_type": "general", "name": "a<b", "keywords": ["k"]}, auth)
    check(st == 400 and js["code"] == "invalid" and js["fields"] == {"name": "forbidden_char"}, f"invalid create refused locally: {st} {js}")
    st, hd, _, _ = req("GET", "/api/topics", headers=cookie_only)
    check(hd.get("Content-Security-Policy", "").startswith("default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'"),
          "CSP header unchanged on the topics route")
    fake.shutdown()
    tfile.unlink()

    # Merge with pairing: the host issues a single-use 4-digit code that must be typed back
    open(env["FAKE_GH_LOG"], "w").close()
    target = {"repo": "o/r", "number": 7}
    check(req("POST", "/api/prs/merge-code", target, POSTH)[0] == 401, "merge code needs pairing")
    check(req("POST", "/api/prs/merge", {**target, "confirm": "1234"}, auth)[0] == 400, "merge without a code issued -> 400")
    st, _, js, _ = req("POST", "/api/prs/merge-code", target, auth)
    code = js.get("code", "")
    check(st == 200 and re.fullmatch(r"\d{4}", code or "") is not None, f"4-digit code issued: {js}")
    wrong = "0000" if code != "0000" else "1111"
    st, _, js, _ = req("POST", "/api/prs/merge", {**target, "confirm": wrong}, auth)
    check(st == 400 and "wrong code" in js.get("error", ""), "wrong code -> 400")
    check("pr merge" not in Path(env["FAKE_GH_LOG"]).read_text(), "no gh merge on wrong code")
    (tmp / "pr_state").write_text("failing")
    st, _, js, _ = req("POST", "/api/prs/merge", {**target, "confirm": code}, auth)
    check(st == 409 and "failing" in js.get("error", ""), f"failing PR refused: {st} {js}")
    check("pr merge" not in Path(env["FAKE_GH_LOG"]).read_text(), "no gh merge for failing PR")
    check(req("POST", "/api/prs/merge", {**target, "confirm": code}, auth)[0] == 400, "code is single-use")
    # three wrong codes burn the code
    code3 = req("POST", "/api/prs/merge-code", target, auth)[2]["code"]
    bad = "0000" if code3 != "0000" else "1111"
    tries = [req("POST", "/api/prs/merge", {**target, "confirm": bad}, auth)[0] for _ in range(3)]
    check(tries == [400, 400, 429], f"attempt limit: {tries}")
    check(req("POST", "/api/prs/merge", {**target, "confirm": code3}, auth)[0] == 400, "code dropped after attempt limit")
    (tmp / "pr_state").write_text("green")
    code2 = req("POST", "/api/prs/merge-code", target, auth)[2]["code"]
    st, _, js, _ = req("POST", "/api/prs/merge", {**target, "confirm": code2}, auth)
    check(st == 200, f"green PR merged: {st} {js}")
    check("pr merge 7 -R o/r --squash --delete-branch" in Path(env["FAKE_GH_LOG"]).read_text(), "gh merge called correctly")
    st, _, js, _ = req("GET", "/api/state")
    check(any(e["event"] == "PRMerge" for e in js["feed"]), "merge logged in feed")

    # Front-end smoke run: app.js renders sessions, graph and timeline from this state without a runtime error
    import glob, shutil  # noqa: E401
    node = shutil.which("node") or next(iter(sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/node")))), None)
    if node:
        st_path = tmp / "state.json"
        smoke = req("GET", "/api/state")[2]
        if smoke["sessions"]:  # also render dismissable cards: one stale, one waiting and quiet for over 30 min
            base = smoke["sessions"][0]
            smoke["sessions"] += [{**base, "id": "smoke-stale", "state": "stale", "ended": False, "last": smoke["now"] - 4000},
                                  {**base, "id": "smoke-wait", "state": "waiting", "ended": False, "last": smoke["now"] - 4000}]
        st_path.write_text(json.dumps(smoke))
        # Research tab payloads: the fixtures re-timed to "now", passed through the server's own shape() like /api/research does
        sys.path.insert(0, str(SH / "lib"))
        import niu_research  # noqa: E402
        from datetime import datetime, timezone  # noqa: E402
        ts_re = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
        now = time.time()

        def retime(x, delta):
            if isinstance(x, dict):
                return {k: retime(v, delta) for k, v in x.items()}
            if isinstance(x, list):
                return [retime(v, delta) for v in x]
            if isinstance(x, str) and ts_re.fullmatch(x):
                t = datetime.strptime(x, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() + delta
                return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return x

        def envelope(name):
            raw = json.loads((SH / "tests" / "fixtures" / "research" / f"{name}.json").read_text())
            gen = datetime.strptime(raw["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
            raw = retime(raw, int(now) - gen)
            return {"state": "ok", "cause": None, "message": "", "data": niu_research.shape(raw), "fetched_at": now, "ok_at": now,
                    "stale": False, "refreshing": False, "server_now": now}

        rs_path = tmp / "research.json"
        rs_path.write_text(json.dumps({n: envelope(n) for n in ("m1", "m2", "m3", "hostile")}))
        # Topics panel payloads: the fixtures passed through the server's own shape_topic() like /api/topics does
        import niu_topics  # noqa: E402

        sys.path.insert(0, str(SH / "tests"))
        import topics_fixtures  # noqa: E402

        def tp_env(name, rows=None, meta=False):
            if rows is None:
                rows = json.loads((SH / "tests" / "fixtures" / "topics" / f"{name}.json").read_text())
            return {"state": "ok", "cause": None, "message": "", "topics": [t for t in (niu_topics.shape_topic(r, meta=meta) for r in rows) if t], "archived_truncated": False,
                    "inactive_truncated": False, "states_supported": True, "meta_supported": meta, "dropped": 0, "loaded_at": now, "stale": False,
                    "limits": niu_topics.LIMITS, "server_now": now}
        tp_path = tmp / "topics.json"
        # many: 300 rows with the upstream meta (the server's own ranks); many_legacy: the same rows from a valkyrie-dispatch without topic_meta
        tp_path.write_text(json.dumps({"ok": tp_env("list_ok"), "hostile": tp_env("hostile"), "overlimit": tp_env("cli_overlimit"),
                                       "many": tp_env("many", topics_fixtures.many_topics(300, 7, True), True),
                                       "many_legacy": tp_env("many_legacy", topics_fixtures.many_topics(300, 7, False), False),
                                       "not_paired": {"state": "not_paired", "message": niu_topics.MESSAGES["not_paired"], "topics": []}}))
        r = subprocess.run([node, str(SH / "tests" / "web_smoke.js"), str(SH / "web" / "app.js"), str(st_path), str(rs_path), str(tp_path),
                            str(SH / "tests" / "fixtures" / "usage" / "history.json"), str(SH / "tests" / "fixtures" / "usage" / "tokens.json"),
                            str(agents_path)],  # LAST argument: the real /api/agents body for sess-web1
                           capture_output=True, text=True, timeout=60)
        check(r.returncode == 0 and "rendered" in r.stdout and "llm ok" in r.stdout and "research ok" in r.stdout and "research status ok" in r.stdout and "topics ok" in r.stdout and "topics view ok" in r.stdout and "timeline ok" in r.stdout and "flag ok" in r.stdout and "usage ok" in r.stdout and "tokens ok" in r.stdout and "agent map ok" in r.stdout,
              f"app.js renders without errors (Agents, Timeline, Research and Topics views): {r.stdout.strip()} {r.stderr.strip()[:300]}")
    else:
        print("web: node not found, front-end smoke run skipped")

    # Dismiss a session the hooks never saw end: paired browsers only; it comes back if it does anything again
    check(req("POST", "/api/sessions/dismiss", {"session": "sess-web1"}, POSTH)[0] == 401, "dismiss needs pairing")
    check(req("POST", "/api/sessions/dismiss", {"session": "sess-web1"}, {"Origin": ORIGIN, "Cookie": tok})[0] == 403,
          "dismiss without CSRF header -> 403")
    check(req("POST", "/api/sessions/dismiss", {"session": "../x"}, auth)[0] == 400, "dismiss validates the id")
    check(req("POST", "/api/sessions/dismiss", {"session": "web"}, auth)[0] == 400, "system session can't be dismissed")
    check(req("POST", "/api/sessions/dismiss", {"session": "nope-1"}, auth)[0] == 404, "unknown session -> 404")
    check(req("POST", "/api/sessions/dismiss", ["sess-web1"], auth)[0] == 400, "non-object body -> 400")
    check(req("POST", "/api/sessions/dismiss", {"session": "sess-web1"}, auth)[0] == 200, "dismiss ok")
    check(req("POST", "/api/sessions/dismiss", {"session": "sess-web1"}, auth)[0] == 200, "repeat dismiss ok")
    con = sqlite3.connect(env["NIU_EVENTS_DB"])
    n_end = con.execute("SELECT COUNT(*) FROM events WHERE session_id='sess-web1' AND event='SessionEnd'").fetchone()[0]
    con.close()
    check(n_end == 1, f"repeat dismiss adds no second row: {n_end}")
    time.sleep(1.1)
    s = next(x for x in req("GET", "/api/state")[2]["sessions"] if x["id"] == "sess-web1")
    check(s["ended"] and s["state"] == "ended", f"dismissed session is ended: {s['state']}")
    ev("PreToolUse", tool_name="Bash", tool_input={"command": "ls"}, tool_use_id="t9")
    time.sleep(1.1)
    s = next(x for x in req("GET", "/api/state")[2]["sessions"] if x["id"] == "sess-web1")
    check(not s["ended"] and s["state"] == "working", f"activity after dismiss reopens it: {s['state']}")

    # ---------- Paired machines (multi-machine ingest) ----------
    import hashlib  # noqa: E401
    MFILE = tmp / "state" / "machines.json"
    sha = lambda x: hashlib.sha256(x.encode()).hexdigest()  # noqa: E731
    ING = {"Host": f"127.0.0.1:{port}"}
    TSH = {"Host": "hub.example.ts.net", "Tailscale-User-Login": "me@example.com"}
    row1 = {"event": "SessionStart", "session_id": "rem-sess1", "ts": time.time(), "src_id": 1, "domain": "PERSONAL", "project": "app",
            "project_dir": "app", "summary": ""}

    def bearer(t, extra=None):
        return {"Authorization": f"Bearer {t}", **(extra or {})}

    def mreq(method, path, body=None, headers=None):
        time.sleep(0.55)   # stay under the per-machine rate limit (2/s sustained) between ordinary calls
        return req(method, path, body, headers)

    def inject_pair(label, code):
        MFILE.parent.mkdir(parents=True, exist_ok=True)
        d = json.loads(MFILE.read_text()) if MFILE.exists() else {"machines": []}
        d["pair"] = {"code_hash": sha(code), "expires": time.time() + 300, "attempts": 0, "label": label}
        MFILE.write_text(json.dumps(d))
        os.chmod(MFILE, 0o600)

    # no machine paired: the routes do not exist
    check(not MFILE.exists(), "no machine store before pairing")
    check(req("POST", "/api/ingest", {"v": 1, "kind": "events", "rows": []}, bearer("x"))[0] == 404, "ingest: 404 when no machine is paired")
    check(req("GET", "/api/ingest/cursor", headers=bearer("x"))[0] == 404, "ingest cursor: 404 when no machine is paired")
    check(req("POST", "/api/machine/pair", {"code": "12345678"})[0] == 404, "machine pair: 404 when no pairing is in progress")
    check(req("GET", "/api/state?host=Vanaheim")[0] == 400, "host filter: unknown host -> 400")
    check(req("GET", "/api/state?host=testhost")[0] == 200 and req("GET", "/api/state?host=")[0] == 200, "host filter: hub label and empty value are fine")

    # `niu-dash machine add` needs a terminal; label rules; real tty flow
    r = subprocess.run([str(SH / "bin" / "niu-dash"), "machine", "add", "Asgard"], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    check(r.returncode != 0 and not MFILE.exists(), "machine add refused without a terminal")
    for bad in ("Testhost", "testhost", "bad label!", "x" * 33):
        r = subprocess.run(["script", "-qec", f"{SH / 'bin' / 'niu-dash'} machine add '{bad}'", "/dev/null"], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        check("Pairing code" not in r.stdout, f"machine add refuses label {bad!r}")
    r = subprocess.run(["script", "-qec", f"{SH / 'bin' / 'niu-dash'} machine add Asgard", "/dev/null"], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    m = re.search(r"Pairing code for Asgard: (\d{4}) (\d{4})", r.stdout)
    check(m is not None, f"machine add prints an 8-digit code: {r.stdout!r}")
    acode = "".join(m.groups()) if m else "00000000"

    # pairing route: checks in order
    check(req("POST", "/api/machine/pair", {"code": acode}, {"Cookie": "a=b"})[0] == 403, "machine pair with a Cookie -> 403")
    check(req("POST", "/api/machine/pair", {"code": acode}, {"Origin": ORIGIN})[0] == 403, "machine pair with an Origin -> 403")
    check(req("POST", "/api/machine/pair", {"code": acode}, {"Host": "hub.example.ts.net"})[0] == 403, "machine pair: non-loopback Host without tailnet identity -> 403")
    wrongc = "11111111" if acode != "11111111" else "22222222"
    check(req("POST", "/api/machine/pair", {"code": wrongc}, ING)[0] == 403, "machine pair: wrong code -> 403")
    st, _, js, _ = req("POST", "/api/machine/pair", {"code": acode}, TSH)
    check(st == 200 and js.get("label") == "Asgard" and len(js.get("token", "")) > 30, f"machine pair through the real CLI code: {st}")
    atok = (js or {}).get("token", "")
    store = json.loads(MFILE.read_text())
    check(atok and atok not in json.dumps(store) and store["machines"][0]["hash"] == sha(atok), "only the token hash is stored")
    check(store["machines"][0]["login"] == "me@example.com", "login seen at pairing is recorded")
    check(oct(MFILE.stat().st_mode)[-3:] == "600", "machine store mode 600")
    check(req("POST", "/api/machine/pair", {"code": acode}, ING)[0] == 404, "machine pair: single use (no pairing in progress afterwards)")
    r = subprocess.run([str(SH / "bin" / "niu-dash"), "machines"], env=env, capture_output=True, text=True)
    check("Asgard" in r.stdout and atok not in r.stdout, "machines lists the label, never the token")
    # Asgard is pinned to me@example.com; remove it and test with an unpinned machine injected through the store
    subprocess.run([str(SH / "bin" / "niu-dash"), "machine", "revoke", "Asgard"], env=env, capture_output=True)
    check(mreq("GET", "/api/ingest/cursor", headers=bearer(atok, ING))[0] == 404, "revoked machine token is dead (no machine left: 404)")
    # attempts limit
    inject_pair("Vanaheim", "12345678")
    sts = [req("POST", "/api/machine/pair", {"code": "00000000"}, ING)[0] for _ in range(6)]
    check(sts[-1] == 429 and sts[0] == 403, f"machine pair attempt limit: {sts}")
    check(req("POST", "/api/machine/pair", {"code": "12345678"}, ING)[0] == 404, "pairing is cancelled after too many attempts")
    inject_pair("Vanaheim", "12345678")
    st, _, js, _ = req("POST", "/api/machine/pair", {"code": "12345678"}, ING)
    mtok = (js or {}).get("token", "")
    check(st == 200 and js.get("label") == "Vanaheim" and mtok, f"machine pair via injected code (loopback): {st}")
    check(json.loads(MFILE.read_text())["machines"][0]["login"] == "", "no login recorded for loopback pairing")

    # ingest: gate order
    body1 = {"v": 1, "kind": "events", "rows": [row1]}
    check(req("POST", "/api/ingest", body1, ING)[0] == 401, "ingest without a bearer -> 401")
    check(req("POST", "/api/ingest", body1, bearer("nope", ING))[0] == 401, "ingest with a wrong bearer -> 401")
    check(req("POST", "/api/ingest", body1, bearer(mtok, {**ING, "Cookie": "niu_session=x"}))[0] == 403, "ingest with a Cookie -> 403")
    check(req("POST", "/api/ingest", body1, bearer(mtok, {**ING, "Origin": ORIGIN}))[0] == 403, "ingest with an Origin -> 403")
    check(req("POST", "/api/ingest", body1, bearer(mtok, {"Host": "hub.example.ts.net"}))[0] == 403, "ingest: non-loopback Host without identity header -> 403")
    check(req("GET", "/api/ingest/cursor", headers=bearer(mtok, {"Host": "hub.example.ts.net"}))[0] == 403, "cursor: non-loopback Host without identity header -> 403")
    # a paired browser cookie cannot ingest, and a browser bearer-less POST with the cookie is refused outright
    check(req("POST", "/api/ingest", body1, {**ING, "Cookie": tok})[0] == 403, "browser cookie cannot ingest (403)")
    check(req("POST", "/api/ingest", body1, bearer(tok.split("=", 1)[1], ING))[0] == 401, "browser token as bearer cannot ingest (401)")
    check(req("POST", "/api/ingest", body1, {**POSTH, "Cookie": tok})[0] == 403, "browser cookie + CSRF + Origin cannot ingest (403)")

    # ingest: success, idempotence, ownership, cursor
    st, _, js, _ = mreq("POST", "/api/ingest", body1, bearer(mtok, ING))
    check(st == 200 and js["accepted"] == 1 and js["cursor"]["events"] == 1, f"ingest events: {st} {js}")
    st, _, js, _ = mreq("POST", "/api/ingest", body1, bearer(mtok, ING))
    check(st == 200 and js["accepted"] == 0 and js["skipped"] == 1, f"re-send is idempotent: {js}")
    own = {"v": 1, "kind": "events", "rows": [{**row1, "session_id": "sess-web1", "src_id": 2}]}
    st, _, js, _ = mreq("POST", "/api/ingest", own, bearer(mtok, ING))
    check(st == 200 and js["accepted"] == 0 and js["skipped"] == 1, f"a hub session id is never taken over: {js}")
    st, _, js, _ = mreq("GET", "/api/ingest/cursor", headers=bearer(mtok, TSH))
    check(st == 200 and js["cursor"]["events"] == 1, f"cursor through the tailnet identity header: {st} {js}")
    check(mreq("POST", "/api/ingest", {"v": 2, "kind": "events", "rows": []}, bearer(mtok, ING))[0] == 400, "bad version -> 400")
    check(mreq("POST", "/api/ingest", {"v": 1, "kind": "nope", "rows": []}, bearer(mtok, ING))[0] == 400, "bad kind -> 400")
    st, _, js, _ = mreq("POST", "/api/ingest", {"v": 1, "kind": "tokens", "rows": [{
        "mkey": 12345, "ts": time.time() - 60, "model": "claude-sonnet-4-5", "inp": 10, "outp": 20, "cw": 0, "cr": 0,
        "file": {"key": "h:" + "a" * 32, "kind": "main", "session_id": None, "agent_id": None, "agent_type": None,
                 "domain": "PERSONAL", "project": "app", "source": "claude"}}]}, bearer(mtok, ING))
    check(st == 200 and js["accepted"] == 1, f"ingest tokens: {st} {js}")
    st, _, js, _ = mreq("POST", "/api/ingest", {"v": 1, "kind": "designs", "rows": [{"rel": "WORK/x/.claude-proposals/a.md", "sha": "0" * 64,
                        "mtime": time.time(), "title": "t", "status": "p", "content": "x"}]}, bearer(mtok, ING))
    check(st == 200 and js["accepted"] == 0, f"WORK / bad-sha design refused: {js}")

    # the live view shows the remote session; read-only
    time.sleep(1.2)
    st, _, js, _ = req("GET", "/api/state?host=Vanaheim")
    rs = [x for x in (js or {}).get("sessions", []) if x["id"] == "rem-sess1"]
    check(st == 200 and rs and rs[0].get("host") == "Vanaheim", f"/api/state?host= shows the remote session: {st}")
    check(all(x["id"] != "sess-web1" for x in (js or {}).get("sessions", [])), "host filter hides hub sessions")
    js = req("GET", "/api/state?host=testhost")[2]
    check(all(x["id"] != "rem-sess1" for x in js["sessions"]) and any(x["id"] == "sess-web1" for x in js["sessions"]), "hub filter hides remote sessions")
    js = req("GET", "/api/state")[2]
    check(any(x["id"] == "rem-sess1" for x in js["sessions"]) and "Vanaheim" in (js.get("hosts") or []), "unfiltered state has both hosts")
    check(req("GET", "/api/state?host=nope")[0] == 400 and req("GET", "/api/stream?host=nope")[0] == 400, "unknown host -> 400 on state and stream")
    check(req("GET", "/api/usage/tokens?host=nope")[0] == 400, "unknown host -> 400 on usage tokens")
    check(req("GET", "/api/usage/tokens?host=Vanaheim")[0] == 200 and req("GET", "/api/usage/tokens?host=testhost")[0] == 200, "usage tokens accepts known hosts")
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/api/stream?host=Vanaheim", headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    line = r.fp.readline()
    check(r.status == 200 and line.startswith(b"data: ") and b"rem-sess1" in line and b"sess-web1" not in line, "SSE with host filter")
    c.close()
    st, _, js, rawr = req("GET", "/api/agents?sid=rem-sess1")
    check(st == 200 and js == {"sid": "rem-sess1", "remote": True, "host": "Vanaheim", "agents": [], "skills": [], "local_tools": []}, f"/api/agents remote branch: {js}")
    check(mreq("POST", "/api/sessions/dismiss", {"session": "rem-sess1"}, auth)[0] == 403, "dismiss refused for a remote session (403)")
    con = sqlite3.connect(env["NIU_EVENTS_DB"])
    check(con.execute("SELECT COUNT(*) FROM events WHERE session_id='rem-sess1' AND event='SessionEnd'").fetchone()[0] == 0, "no dismissal row for a remote session")
    con.close()
    rtext = "# Remote design\nStatus: proposed\n\nFrom the satellite.\n"
    rrel = "PERSONAL/app/.claude-proposals/r.md"
    st, _, js, _ = mreq("POST", "/api/ingest", {"v": 1, "kind": "designs", "rows": [{"rel": rrel, "sha": hashlib.sha256(rtext.encode()).hexdigest(),
                        "mtime": time.time(), "title": "Remote design", "status": "proposed", "content": rtext}]}, bearer(mtok, ING))
    check(st == 200 and js["accepted"] == 1, f"ingest designs: {st} {js}")
    st, _, js, rawd = req("GET", "/api/designs?sid=rem-sess1", headers={"Cookie": tok})
    check(st == 200 and b"Vanaheim:" not in rawd, f"designs for a remote session do not use its project_dir as a local path: {st}")
    rdocs = [d for d in (js or {}).get("docs", []) if d.get("host") == "Vanaheim"]
    check(len(rdocs) == 1 and rdocs[0]["rel"] == rrel, f"remote design listed with its host: {rdocs}")
    check(all("host" in d for d in js.get("docs", [])) and any(d.get("host") is None for d in js["docs"]), "every listed doc carries host (null = hub)")
    st, _, js, _ = req("GET", f"/api/designs/doc?id={rdocs[0]['id']}", headers={"Cookie": tok}) if rdocs else (0, 0, {}, 0)
    check(st == 200 and "From the satellite" in js.get("text", ""), f"remote doc readable from the DB copy: {st}")

    # machine token on browser routes: every one refuses it
    mc = {"Cookie": f"niu_session={mtok}"}
    for pth in ("/api/designs", "/api/topics", "/api/designs/doc?id=d0123456789abcdef"):
        check(req("GET", pth, headers=mc)[0] == 401, f"machine token as a cookie on {pth} -> 401")
        check(req("GET", pth, headers=bearer(mtok))[0] == 401, f"machine bearer on {pth} -> 401")
    for pth, b in (("/api/prs/merge", {"repo": "o/r", "number": 7, "confirm": "1234"}), ("/api/prs/merge-code", {"repo": "o/r", "number": 7}),
                   ("/api/sessions/dismiss", {"session": "sess-web1"}), ("/api/topics/create", {"agent_type": "general", "name": "x", "keywords": ["k"]})):
        check(req("POST", pth, b, {**POSTH, **mc})[0] == 401, f"machine token as a cookie on POST {pth} -> 401")
        check(req("POST", pth, b, {**POSTH, **bearer(mtok)})[0] == 401, f"machine bearer on POST {pth} -> 401")
    check(req("GET", "/api/me", headers=mc)[2]["paired"] is False, "machine token is not a paired browser")

    # body limits
    big = {"v": 1, "kind": "events", "rows": [], "pad": "x" * 300_000}
    check(mreq("POST", "/api/ingest", big, bearer(mtok, ING))[0] == 413, "events body over 256 KB -> 413")
    check(mreq("POST", "/api/ingest", {**big, "kind": "designs"}, bearer(mtok, ING))[0] == 200, "designs body of 300 KB is within its 600 KB limit")
    check(mreq("POST", "/api/ingest", {**big, "kind": "designs", "pad": "x" * 700_000}, bearer(mtok, ING))[0] == 413, "designs body over 600 KB -> 413")
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    time.sleep(0.6)
    c.request("POST", "/api/ingest", body=b"not json", headers={**bearer(mtok, ING), "Content-Length": "8"})
    check(c.getresponse().status == 400, "bad JSON -> 400")
    c.close()

    # login pinning: a machine paired through the tailnet only accepts that login
    d = json.loads(MFILE.read_text())
    d["machines"][0]["login"] = "me@example.com"
    MFILE.write_text(json.dumps(d))
    time.sleep(0.6)
    check(req("GET", "/api/ingest/cursor", headers=bearer(mtok, {"Host": "h.ts.net", "Tailscale-User-Login": "other@example.com"}))[0] == 403, "mismatching login -> 403")
    check(mreq("GET", "/api/ingest/cursor", headers=bearer(mtok, TSH))[0] == 200, "matching login -> 200")
    check(mreq("GET", "/api/ingest/cursor", headers=bearer(mtok, ING))[0] == 403, "pinned login: loopback without the header -> 403")
    d["machines"][0]["login"] = ""
    MFILE.write_text(json.dumps(d))

    # rate limit: 10 burst, then 429
    time.sleep(6)
    codes = [req("GET", "/api/ingest/cursor", headers=bearer(mtok, ING))[0] for _ in range(16)]
    check(codes[0] == 200 and 429 in codes and codes.count(200) <= 13, f"per-machine rate limit: {codes}")

    # purge removes that machine's rows; revoke kills the token
    con = sqlite3.connect(env["NIU_USAGE_DB"])
    n_tok = con.execute("SELECT COUNT(*) FROM tok_files WHERE path LIKE 'remote:Vanaheim:%'").fetchone()[0]
    con.close()
    check(n_tok == 1, f"ingested token file stored under remote:<label>: {n_tok}")
    r = subprocess.run([str(SH / "bin" / "niu-dash"), "machine", "purge", "Vanaheim"], env=env, capture_output=True, text=True)
    con = sqlite3.connect(env["NIU_EVENTS_DB"])
    left = con.execute("SELECT COUNT(*) FROM events WHERE host='Vanaheim'").fetchone()[0]
    hub = con.execute("SELECT COUNT(*) FROM events WHERE host IS NULL").fetchone()[0]
    con.close()
    con = sqlite3.connect(env["NIU_USAGE_DB"])
    left_t = con.execute("SELECT COUNT(*) FROM tok_files WHERE path LIKE 'remote:Vanaheim:%'").fetchone()[0]
    left_m = con.execute("SELECT COUNT(*) FROM tok_msgs").fetchone()[0]
    con.close()
    check(r.returncode == 0 and left == 0 and hub > 0 and left_t == 0 and left_m == 0, f"purge: {r.stdout!r} {r.stderr!r} {left} {hub} {left_t} {left_m}")
    check(json.loads(MFILE.read_text())["machines"], "purge keeps the pairing")
    time.sleep(1.0)
    subprocess.run([str(SH / "bin" / "niu-dash"), "machine", "revoke", "all"], env=env, capture_output=True)
    check(req("GET", "/api/ingest/cursor", headers=bearer(mtok, ING))[0] == 404, "after revoke all: ingest is invisible again (404)")
    check(req("GET", "/api/state?host=Vanaheim")[0] == 400, "after revoke the host is unknown again")

    # Revoke + rate limit
    subprocess.run([str(SH / "bin" / "niu-dash"), "revoke", "all"], env=env, capture_output=True)
    check(req("POST", "/api/prs/merge", body, auth)[0] == 401, "revoked cookie -> 401")
    subprocess.run(["script", "-qec", f"{SH / 'bin' / 'niu-dash'} pair", "/dev/null"], env=env, capture_output=True, stdin=subprocess.DEVNULL)
    codes = [req("POST", "/api/pair", {"code": "999999" if code != "999999" else "888888"}, POSTH)[0] for _ in range(6)]
    check(codes[-1] == 429, f"pairing rate limit: {codes}")
finally:
    srv.terminate()
    try:
        srv.wait(5)
    except subprocess.TimeoutExpired:
        srv.kill()
    subprocess.run(["rm", "-rf", str(tmp), str(WS / "PERSONAL" / ".niu-test-web")])

print(f"web: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
