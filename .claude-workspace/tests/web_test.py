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
ev("PreToolUse", tool_name="SendMessage", tool_input={"to": "deploy-bot", "message": "hi"}, tool_use_id="t5")

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
        rs_path.write_text(json.dumps({n: envelope(n) for n in ("m1", "m2", "hostile")}))
        # Topics panel payloads: the fixtures passed through the server's own shape_topic() like /api/topics does
        import niu_topics  # noqa: E402

        def tp_env(name):
            rows = json.loads((SH / "tests" / "fixtures" / "topics" / f"{name}.json").read_text())
            return {"state": "ok", "cause": None, "message": "", "topics": [t for t in map(niu_topics.shape_topic, rows) if t], "archived_truncated": False,
                    "inactive_truncated": False, "states_supported": True, "dropped": 0, "loaded_at": now, "stale": False, "limits": niu_topics.LIMITS, "server_now": now}
        tp_path = tmp / "topics.json"
        tp_path.write_text(json.dumps({"ok": tp_env("list_ok"), "hostile": tp_env("hostile"), "overlimit": tp_env("cli_overlimit"),
                                       "not_paired": {"state": "not_paired", "message": niu_topics.MESSAGES["not_paired"], "topics": []}}))
        r = subprocess.run([node, str(SH / "tests" / "web_smoke.js"), str(SH / "web" / "app.js"), str(st_path), str(rs_path), str(tp_path)],
                           capture_output=True, text=True, timeout=60)
        check(r.returncode == 0 and "rendered" in r.stdout and "llm ok" in r.stdout and "research ok" in r.stdout and "topics ok" in r.stdout and "timeline ok" in r.stdout and "flag ok" in r.stdout,
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
