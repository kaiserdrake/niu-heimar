#!/usr/bin/env python3
"""Tests for the Research tab backend: lib/niu_research.py (config, URL and token checks, fetch, reshaping, cache) and the
`niu-dash research-token` command. A fake research-dispatch runs on a random loopback port. Fake tokens only; never the
real token file, never port 8080."""
import http.client
import json
import os
import pty
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
SH = WS / ".claude-workspace"
FX = SH / "tests" / "fixtures" / "research"
SCHEMA = WS / "PERSONAL" / "valkyrie-dispatch" / "schemas" / "monitor.v1.json"
tmp = Path(tempfile.mkdtemp())
TOKEN = "rd_pat_TESTTOKEN_" + "a" * 24
TOKEN_FILE = tmp / "state" / "research-dispatch.token"
CFG_FILE = tmp / "research.json"
os.environ.update(NIU_EVENTS_DB=str(tmp / "state" / "events.db"), NIU_RESEARCH_TOKEN_FILE=str(TOKEN_FILE),
                  NIU_TOPICS_TOKEN_FILE=str(tmp / "state" / "research-dispatch.topics.token"),
                  NIU_RESEARCH_CONFIG=str(CFG_FILE), NIU_CLAUDE_USAGE_API="0", NIU_CLAUDE_CREDENTIALS="/nonexistent/creds.json")
for k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(k, None)
sys.path.insert(0, str(SH / "lib"))
import niu_research as nr  # noqa: E402

passed = failed = 0
notes = []


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


class Fake(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FakeHandler)
        self.seen, self.mode, self.who, self.who_mode = [], {}, [], {}
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        s = self.server
        if self.path == "/api/v1/auth/whoami":   # scope check: scripted separately so the monitor request counts stay exact
            s.who.append({"auth": self.headers.get("Authorization")})
            m = s.who_mode
            body = m.get("body", b'{"name":"niu-dash","scope":"monitor"}')
            self.send_response(m.get("status", 200))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        s.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "ua": self.headers.get("User-Agent")})
        m = s.mode
        if m.get("delay"):
            time.sleep(m["delay"])
        body = m.get("body", b"")
        self.send_response(m.get("status", 200))
        for k, v in m.get("headers", {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", m.get("ctype", "application/json"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass


up, capture = Fake(), Fake()
M1 = (FX / "m1.json").read_bytes()
M2 = (FX / "m2.json").read_bytes()
HOSTILE = (FX / "hostile.json").read_bytes()
NOW = [1_000_000.0]
nr._clock = lambda: NOW[0]


def config(url=None, **kw):
    CFG_FILE.write_text(json.dumps({"enabled": True, "url": url or up.url, "poll_secs": 60, "timeout_secs": 2, **kw}))


def token_file(content=TOKEN + "\n", mode=0o600):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if TOKEN_FILE.is_symlink() or TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
    TOKEN_FILE.write_text(content)
    os.chmod(TOKEN_FILE, mode)


def reset():
    nr.RESEARCH.update(cache=None, ok_data=None, ok_at=0.0, busy=False, last_force=0.0, scope=None)
    NOW[0] += 1000
    up.seen.clear(), up.who.clear(), capture.seen.clear()
    up.who_mode = {}


def settle():
    for _ in range(100):
        if not nr.RESEARCH["busy"]:
            return
        time.sleep(0.05)


def poll(**mode):
    """Fresh cache, one fetch against the fake with this scripted answer, return the settled summary."""
    reset()
    up.mode = mode
    nr.summary()
    settle()
    return nr.summary()


# ---------- 1. config and URL ----------
good = ["http://127.0.0.1:18080", "http://[::1]:18080", "http://127.0.0.1:1", "http://127.0.0.1:65535"]
bad = ["http://localhost:8080", "http://10.0.0.5:8080", "http://127.0.0.1.nip.io:8080", "http://u:p@127.0.0.1:8080", "http://127.0.0.1:8080/x",
       "http://127.0.0.1:8080/", "http://127.0.0.1:8080?x=1", "https://example.com", "https://127.0.0.1:8080", "http://127.0.0.1:0",
       "http://127.0.0.1:70000", "http://127.0.0.1:08080", "http://127.0.0.1", "http://127.0.0.1:8080\n", "", None, 5, "http://[::2]:8080"]
check(all(nr.validate_url(u) == u for u in good), "loopback URLs accepted")
check(all(nr.validate_url(u) is None for u in bad), f"bad URLs refused: {[u for u in bad if nr.validate_url(u)]}")
token_file()
for u in ("http://localhost:" + up.url.rsplit(":", 1)[1], "https://example.com", "http://127.0.0.1:0"):
    reset()
    config(url=u)
    r = nr.summary()
    check(r["state"] == "error" and r["cause"] == "url" and "http://127.0.0.1:PORT" in r["message"], f"bad URL gives an error state: {u} {r['state']}")
    check(not up.seen and not capture.seen, "no request for a bad URL")
CFG_FILE.write_text("{not json")
check(nr.load_config()["url"] == "http://127.0.0.1:8080", "unreadable config falls back to the template")
CFG_FILE.write_text(json.dumps({"enabled": "yes", "url": "http://127.0.0.1:9", "poll_secs": 1, "timeout_secs": 999}))
c = nr.load_config()
check(c["enabled"] is True and c["poll_secs"] == nr.MIN_POLL_SECS and c["timeout_secs"] == 10, f"config values are clamped: {c}")
config(enabled=False)
reset()
r = nr.summary()
check(r["state"] == "disabled" and not up.seen, "disabled: no call")
config()

# ---------- 2. token file ----------
TOKEN_FILE.unlink()
reset()
r = nr.summary()
check(r["state"] == "not_configured" and not up.seen, "missing token file: not_configured and no request")
token_file(mode=0o644)
reset()
r = nr.summary()
check(r["state"] == "error" and r["cause"] == "perms" and not up.seen, f"mode 0644 refused: {r['state']} {r['cause']}")
token_file(mode=0o640)
check(nr.summary()["cause"] == "perms", "group-readable refused")
TOKEN_FILE.unlink()
real = tmp / "real.token"
real.write_text(TOKEN)
os.chmod(real, 0o600)
TOKEN_FILE.symlink_to(real)
reset()
r = nr.summary()
check(r["state"] == "error" and r["cause"] == "perms" and not up.seen, f"symlink refused: {r['state']} {r['cause']}")
TOKEN_FILE.unlink()
os.mkdir(TOKEN_FILE)
check(nr.summary()["cause"] == "perms", "a directory is refused")
os.rmdir(TOKEN_FILE)
for junk in ("hello", "rd_pat_short", "rd_pat_" + "x" * 200, TOKEN + " extra", "\xff\xfe"):
    token_file(content=junk)
    reset()
    r = nr.summary()
    check(r["state"] == "error" and r["cause"] == "format" and not up.seen, f"bad token content refused: {junk[:12]!r} {r['state']}")
token_file()
r = poll(body=M1)
check(r["state"] == "ok", f"valid token: ok ({r['state']} {r['cause']})")
check(len(up.seen) == 1 and up.seen[0]["auth"] == f"Bearer {TOKEN}" and up.seen[0]["path"] == "/api/v1/monitor/summary", f"token sent once, to the fixed path: {up.seen}")
check(up.seen[0]["ua"] == "niu-heimar-dashboard", "user agent")
check(TOKEN not in json.dumps(r), "token not in the summary")

# ---------- 3. states ----------
r = poll(body=M1)
check(r["state"] == "ok" and r["data"]["current_run"] is None and r["data"]["service"]["features"]["runs"] is False and not r["stale"],
      "m1: ok, no current run, runs feature off")
check(r["data"]["gate"]["preview"]["decision"] == "run" and len(r["data"]["gate"]["recent"]) == 2, "m1 gate data")
for status, state in ((401, "unauthorized"), (403, "unauthorized"), (404, "incompatible")):
    r = poll(status=status, body=b'{"detail":"x"}')
    check(r["state"] == state and r["data"] is None, f"HTTP {status} -> {state}: {r['state']}")
check("Token rejected" in poll(status=401)["message"], "unauthorized message is the fixed text")
r = poll(status=302, headers={"Location": capture.url + "/steal"})
check(r["state"] == "incompatible", f"redirect -> incompatible: {r['state']}")
check(not capture.seen, f"redirect target received nothing (token never forwarded): {capture.seen}")
r = poll(status=500, body=b"boom sk-ant-api03-LEAK")
check(r["state"] == "error" and r["cause"] == "server" and "sk-ant" not in json.dumps(r), f"500 -> error with a fixed message: {r['state']}")
reset()
config(timeout_secs=1)
up.mode = {"delay": 2.5, "body": M1}
t0 = time.time()
nr.summary()
settle()
r = nr.summary()
check(r["state"] == "unreachable" and time.time() - t0 < 2.4, f"slow upstream -> unreachable within the timeout: {r['state']} {time.time() - t0:.1f}s")
config()
s = socket.socket()
s.bind(("127.0.0.1", 0))
dead = s.getsockname()[1]
s.close()
reset()
config(url=f"http://127.0.0.1:{dead}")
nr.summary()
settle()
r = nr.summary()
check(r["state"] == "unreachable" and f"127.0.0.1:{dead}" in r["message"], f"closed port -> unreachable: {r['state']} {r['message']}")
config()
r = poll(body=b"x" * (200 * 1024))
check(r["state"] == "error" and r["cause"] == "toolarge", f"200 KiB body -> error: {r['state']} {r['cause']}")
r = poll(body=b"<html></html>", ctype="text/html")
check(r["state"] == "incompatible", f"text/html -> incompatible: {r['state']}")
r = poll(body=b'{"schema":"monitor.v2"}')
check(r["state"] == "incompatible", f"other schema -> incompatible: {r['state']}")
r = poll(body=b"not json")
check(r["state"] == "error" and r["cause"] == "badjson", f"bad JSON -> error: {r['state']}")
r = poll(body=b"[1,2]")
check(r["state"] == "incompatible", "JSON array -> incompatible")
r = poll(status=429, body=b"{}")
check(r["state"] == "error", "other 4xx -> error")

# ---------- 3b. scope check of the monitor token (whoami) ----------
reset()
up.mode = {"body": M1}
nr.summary()
settle()
check(nr.summary()["state"] == "ok" and len(up.who) == 1 and up.who[0]["auth"] == f"Bearer {TOKEN}", f"monitor scope: ok, one whoami call: {len(up.who)}")
NOW[0] += 61
nr.summary()
settle()
check(len(up.who) == 1 and len(up.seen) == 2, "whoami result cached for 300 s")
NOW[0] += 301
nr.summary()
settle()
check(len(up.who) == 2, "whoami asked again after 300 s")
for scope in ("admin", "read", "topics"):
    reset()
    up.mode = {"body": M1}
    up.who_mode = {"body": json.dumps({"name": "x", "scope": scope}).encode()}
    nr.summary()
    settle()
    r = nr.summary()
    check(r["state"] == "too_powerful" and not up.seen and "monitor-scope" in r["message"], f"{scope} token refused, no monitor request: {r['state']} {len(up.seen)}")
reset()
up.mode = {"body": M1}
up.who_mode = {"status": 404, "body": b"{}"}
nr.summary()
settle()
check(nr.summary()["state"] == "ok" and len(up.seen) == 1, "whoami 404 (older deploy) is tolerated")
reset()
up.mode = {"body": M1}
up.who_mode = {"status": 401, "body": b"{}"}
nr.summary()
settle()
check(nr.summary()["state"] == "unauthorized" and not up.seen, "whoami 401 -> unauthorized")

# ---------- 4. no proxies ----------
for k in ("http_proxy", "HTTP_PROXY", "all_proxy"):
    os.environ[k] = capture.url
r = poll(body=M1)
for k in ("http_proxy", "HTTP_PROXY", "all_proxy"):
    os.environ.pop(k)
check(r["state"] == "ok" and len(up.seen) == 1 and not capture.seen, f"env proxies ignored: up={len(up.seen)} proxy={len(capture.seen)}")

# ---------- 5. cache ----------
reset()
up.mode = {"body": M1}
nr.summary()
settle()
check(len(up.seen) == 1, "first call fetches")
for _ in range(5):
    r = nr.summary()
check(len(up.seen) == 1 and r["state"] == "ok", f"calls within poll_secs share one request: {len(up.seen)}")
NOW[0] += 5
nr.summary(force=True)
settle()
check(len(up.seen) == 2, "manual refresh fetches")
NOW[0] += 3
nr.summary(force=True)
settle()
check(len(up.seen) == 2, "manual refresh is throttled to once per 10 s")
NOW[0] += 8
nr.summary(force=True)
settle()
check(len(up.seen) == 3, "manual refresh allowed again after 10 s")
ok_at = nr.summary()["ok_at"]
NOW[0] += 30
nr.summary()
settle()
check(len(up.seen) == 3, "no refresh before poll_secs")
NOW[0] += 40
up.mode = {"status": 500}
first = nr.summary()  # stale-while-revalidate: answers at once with the old cache and starts a refresh
check(first["state"] == "ok" and first["data"] is not None, "stale cache served immediately")
settle()
r = nr.summary()
check(r["state"] == "error" and r["stale"] is True and r["data"] is not None and r["data"]["gate"]["preview"] is not None,
      f"after a failure the last good data is kept as stale: {r['state']} stale={r['stale']}")
check(r["ok_at"] == ok_at + 0 or r["ok_at"] == nr.RESEARCH["ok_at"], "ok_at kept")
check(r["ok_at"] < NOW[0], "ok_at is the last success, not the failure")
up.mode = {"body": M1}
NOW[0] += 61
nr.summary()
settle()
r = nr.summary()
check(r["state"] == "ok" and not r["stale"], "recovers after the upstream is back")
NOW[0] += 3 * 60 + 5   # old data in an ok state beyond 3 x poll_secs is flagged stale
up.mode = {"delay": 0, "body": M1}
nr.RESEARCH["busy"] = True   # pretend a refresh is running so the answer is the cached one
r = nr.summary()
nr.RESEARCH["busy"] = False
check(r["stale"] is True, "ok data older than 3 x poll_secs is stale")

# ---------- 6. reshaping ----------
EPOCH = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()  # noqa: E731
TS = __import__("re").compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def roundtrip(raw, out, path="$"):
    """Every field of the (spec-valid) fixture survives shape(); times become epoch seconds."""
    if isinstance(raw, dict):
        if not isinstance(out, dict):
            return [f"{path}: not a dict"]
        errs = []
        for k, v in raw.items():
            if k not in out:
                errs.append(f"{path}.{k}: dropped")
            else:
                errs += roundtrip(v, out[k], f"{path}.{k}")
        return errs
    if isinstance(raw, list):
        if not isinstance(out, list) or len(out) != len(raw):
            return [f"{path}: list differs"]
        return [e for a, b in zip(raw, out) for e in roundtrip(a, b, path + "[]")]
    if isinstance(raw, str) and TS.fullmatch(raw):
        return [] if out == EPOCH(raw) else [f"{path}: {raw} -> {out}"]
    return [] if out == raw else [f"{path}: {raw!r} -> {out!r}"]


m1, m2 = json.loads(M1), json.loads(M2)
for name, raw in (("m1", m1), ("m2", m2)):
    errs = roundtrip(raw, nr.shape(raw, 5.0))
    check(not errs, f"{name} round-trips every documented field: {errs[:5]}")
sh2 = nr.shape(m2)
check(sh2["last_run"]["agents"][0]["started_at"] == EPOCH("2026-11-06T15:00:12Z"), "times converted to epoch")
check(nr.shape({}, 7.0)["generated_at"] == 7.0 and nr.shape("junk")["gate"]["recent"] == [], "garbage input does not crash shape()")

hostile = json.loads(HOSTILE)
out = nr.shape(hostile)
blob = json.dumps(out)
for needle in ("prompt_text", "transcript", "Secret title", "evil.example", "discord.com", "sk-ant", "onerror", "<img", "<script", "Ignore previous",
               "webhook", "rd_pat_", "javascript:", "Another secret", "error_summary", "LEAK", "<b>", "agent said"):
    check(needle not in blob, f"hostile payload: {needle!r} does not survive shape()")
check(out["last_run"]["status"] == "unknown", f"bad enum -> unknown: {out['last_run']['status']}")
check(out["gate"]["preview"]["decision"] == "unknown", "bad decision -> unknown")
check(out["gate"]["preview"]["five_hour"]["used_pct"] == 100, f"900% clamped to 100: {out['gate']['preview']['five_hour']}")
check(out["last_run"]["slot_key"] is None and out["service"]["version"] is None, "bad slot key / version -> None")
check(out["last_run"]["agents"][0]["research_model"] is None, "bad model id -> None")
check(out["last_run"]["agents"][1]["error_code"] == "unknown" and out["last_run"]["agents"][1]["tokens_in"] == 0, "bad error code; negative count clamped to 0")
check(out["ledger"]["today"]["equiv_usd"] is None, "non-numeric USD -> None")
check(len(blob) < 20000, "huge strings do not inflate the output")
hb, hs = out["brief"], out["schedule"]
check(hb["state"] == "unknown" and hb["date"] is None and hb["items"] == 0 and hb["updates"] is None and hb["revision"] == 10 ** 12
      and hb["published_at"] is None and hb["stale"] is None and hb["stale_reason"] == "unknown", f"hostile brief -> unknown/None: {hb}")
check(hs["next_slot_kind"] == "unknown" and hs["brief_at"] is None and hs["next_brief_at"] is None, f"hostile schedule additions: {hs}")
check(out["gate"]["pending"]["kind"] == "unknown" and out["last_run"]["kind"] == "unknown" and out["last_run"]["deadline_at"] is None,
      "hostile kind -> unknown; bad deadline -> None")
ha = out["last_run"]["agents"][0]
check(ha["scout_searches"] == 0 and ha["topics_scouted"] is None and ha["topics_changed"] is None, f"hostile scout counters: {ha}")
check("brief" not in m1 and nr.shape(m1)["brief"] is None and nr.shape(m1)["schedule"]["next_slot_kind"] is None,
      "the additive fields are optional (m1 has none)")
sh = nr.shape(m2)
check(sh["brief"]["state"] == "ready" and sh["brief"]["stale_reason"] == "comprehensive_failed" and sh["last_run"]["kind"] == "comprehensive"
      and sh["schedule"]["brief_at"] == "08:00" and sh["gate"]["recent"][1]["reason"] == "session_cap", "m2 additive fields survive shape()")
ex = sh["recent_runs"][0]
check(ex["kind"] == "explore" and ex["slot_key"] == "explore:2026-11-07", f"m2 explore run survives shape(): {ex}")
check(nr.shape({"schedule": {"next_slot_kind": "explore"}})["schedule"]["next_slot_kind"] == "unknown", "explore is not a slot kind")
hr = out["recent_runs"][0]
mk = "explore:manual:00000000-0000-4000-8000-0000000000e2"
check(nr.shape({"recent_runs": [{"slot_key": mk, "kind": "explore"}]})["recent_runs"][0]["slot_key"] == mk, "manual explore slot key survives shape()")
check(hr["kind"] == "explore" and hr["slot_key"] is None, f"hostile explore slot key -> None: {hr}")
r = poll(body=HOSTILE)
check(r["state"] == "ok" and "sk-ant" not in json.dumps(r) and "onerror" not in json.dumps(r), "hostile payload through the poller")

# the allowlist and enums follow the schema research-dispatch publishes
if SCHEMA.exists():
    schema = json.loads(SCHEMA.read_text())
    try:
        import jsonschema
        for name, raw in (("m1", m1), ("m2", m2)):
            try:
                jsonschema.validate(raw, schema)
                check(True, "")
            except jsonschema.ValidationError as e:
                check(False, f"fixture {name} is not valid monitor.v1: {e.message[:120]}")
    except ImportError:
        notes.append("jsonschema not installed: fixtures were not validated against monitor.v1.json")
    defs = schema["$defs"]
    # monitor.v1's additive companion / explore blocks are not shown yet (left to the Research tab redesign,
    # docs/design/2026-11-10-research-tab-companion.md): shape() drops them, so their keys are not expected.
    deferred = {"CompanionModel", "ExploreModel"}
    names = (set(schema["properties"]) - {"companion", "explore"}) | \
        {k for n, d in defs.items() if n not in deferred for k in d.get("properties", {})}

    def keys(x):
        return {k for d in (x if isinstance(x, list) else [x]) for k in (d if isinstance(d, dict) else {})} | \
            {k for v in (x.values() if isinstance(x, dict) else x if isinstance(x, list) else []) for k in keys(v)}
    got = keys(nr.shape(m2)) | keys(nr.shape(m1))
    check(got == names, f"shape() keys match the schema's property names: extra={got - names} missing={names - got}")

    def enum(d, p):
        e = defs[d]["properties"][p]
        return set(e.get("enum") or next(x["enum"] for x in e["anyOf"] if "enum" in x))
    check(nr.RUN_STATUS == enum("CompactRun", "status"), "run status enum matches the schema")
    check(nr.AGENT_STATUS == enum("CompactAgent", "status"), "agent status enum matches the schema")
    check(nr.DECISIONS == enum("GatePreviewModel", "decision") and nr.REASONS == enum("GatePreviewModel", "reason"), "decision / reason enums match")
    check(nr.ERRORS == enum("CompactAgent", "error_code"), "error code enum matches the schema")
    check(nr.DIGEST == enum("DigestModel", "status"), "digest enum matches the schema")
    check(nr.SOURCES == enum("GatePreviewModel", "source") | enum("GateRecent", "source"), "gate source enums match the schema")
    check(nr.KINDS == enum("CompactRun", "kind") == enum("GatePending", "kind") and nr.SLOT_KINDS == enum("Schedule", "next_slot_kind"),
          "run kind enums match (run rows include explore, slots do not)")
    check(nr.BRIEF_STATES == enum("BriefModel", "state") and nr.STALE_REASONS == enum("BriefModel", "stale_reason"), "brief enums match")
    check(nr.TRIGGERS == enum("CompactRun", "trigger") and nr.MODES == enum("CompactRun", "mode"), "trigger / mode enums match")
else:
    notes.append("PERSONAL/valkyrie-dispatch/schemas/monitor.v1.json not found: fixtures and allowlist not checked against it")

# ---------- 7. the real server and the research-token command ----------
env = dict(os.environ, PATH=os.environ["PATH"])
port = (lambda s: (s.bind(("127.0.0.1", 0)), s.getsockname()[1], s.close())[1])(socket.socket())
config()
token_file()
srv = subprocess.Popen([str(SH / "bin" / "niu-dash"), "serve", "--port", str(port)], env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, text=True)
ORIGIN = f"http://127.0.0.1:{port}"


def req(method, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    r = c.getresponse()
    raw = r.read()
    return r.status, raw


try:
    for _ in range(50):
        try:
            if req("GET", "/healthz")[0] == 200:
                break
        except OSError:
            time.sleep(0.1)
    up.mode, up.seen[:] = {"status": 401, "body": b"{}"}, []
    seen = []
    for _ in range(40):
        st, raw = req("GET", "/api/research")
        js = json.loads(raw)
        seen.append(raw)
        if js["state"] != "loading":
            break
        time.sleep(0.1)
    check(js["state"] == "unauthorized", f"served by niu-dash: unauthorized ({js['state']})")
    up.mode = {"status": 500, "body": b"internal sk-ant-LEAK"}
    st, raw = req("POST", "/api/research/refresh", {"X-Niu-CSRF": "1", "Origin": ORIGIN})
    seen.append(raw)
    time.sleep(1.0)
    st, raw = req("GET", "/api/research")
    seen.append(raw)
    check(json.loads(raw)["state"] == "error", f"served by niu-dash: error after 500 ({json.loads(raw)['state']})")
    seen.append(req("GET", "/api/state")[1])
    check(req("POST", "/api/research/refresh", {"Origin": ORIGIN})[0] == 403, "refresh without the CSRF header -> 403")
    check(req("POST", "/api/research/refresh", {"X-Niu-CSRF": "1", "Origin": "https://evil.example"})[0] == 403, "cross-origin refresh -> 403")
    check(req("POST", "/api/research/refresh", {"X-Niu-CSRF": "1", "Origin": ORIGIN})[0] == 200, "refresh with the CSRF header -> 200")
    check(all(TOKEN.encode() not in b for b in seen) and b"sk-ant" not in b"".join(seen), "token / upstream text not in any dashboard response")
    check(up.seen and all(x["auth"] == f"Bearer {TOKEN}" for x in up.seen), "dashboard sent the token to the fake upstream")
finally:
    srv.terminate()
    try:
        err = srv.communicate(timeout=5)[1]
    except subprocess.TimeoutExpired:
        srv.kill()
        err = srv.communicate()[1]
check(TOKEN not in (err or "") and "sk-ant" not in (err or ""), f"token not in the server's stderr: {(err or '')[:200]!r}")


def tty_run(args, typed):
    """Run niu-dash on a real pseudo-terminal (getpass reads /dev/tty); returns (exit code, terminal output)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(str(SH / "bin" / "niu-dash"), [str(SH / "bin" / "niu-dash"), *args], env)
    out, sent = b"", typed is None
    deadline = time.time() + 15
    while time.time() < deadline:
        if select.select([fd], [], [], 0.2)[0]:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                chunk = b""
            out += chunk
        if not sent and b"token:" in out:
            os.write(fd, typed.encode() + b"\n")
            sent = True
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            try:
                while select.select([fd], [], [], 0.2)[0]:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        break
                    out += chunk
            except OSError:
                pass
            return os.waitstatus_to_exitcode(status), out.decode(errors="replace")
    try:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return os.waitstatus_to_exitcode(status), out.decode(errors="replace")
        os.kill(pid, 9)
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    return -1, out.decode(errors="replace")


TOKEN_FILE.unlink()
r = subprocess.run([str(SH / "bin" / "niu-dash"), "research-token"], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
check(r.returncode != 0 and not TOKEN_FILE.exists(), "research-token refused without a terminal")
new = "rd_pat_" + "Zy9" * 8 + "WXYZ"
code, out = tty_run(["research-token"], new)
check(code == 0 and "saved (rd_pat_...WXYZ)" in out, f"research-token saves and shows only the last 4 characters: {code} {out!r}")
check(new not in out, "the typed token is not echoed back")
check(TOKEN_FILE.exists() and oct(TOKEN_FILE.stat().st_mode)[-3:] == "600" and TOKEN_FILE.read_text().strip() == new, "token file written with mode 600")
check(oct(TOKEN_FILE.parent.stat().st_mode)[-3:] == "700", "state directory is 700")
check(nr.read_token() == new, "read_token reads what research-token wrote")
code, out = tty_run(["research-token"], "hello")
check(code != 0 and "nothing saved" in out and TOKEN_FILE.read_text().strip() == new, f"a malformed token is refused and the old one kept: {code} {out!r}")
code, out = tty_run(["research-token", "--remove"], None)
check(code == 0 and not TOKEN_FILE.exists(), f"--remove deletes the token file: {code} {out!r}")

# the setter asks valkyrie-dispatch for the token's scope and deletes a wrong one again
TOPICS_FILE = Path(os.environ["NIU_TOPICS_TOKEN_FILE"])
config()
up.who.clear()
up.who_mode = {"body": b'{"name":"niu-dash-topics","scope":"topics"}'}
code, out = tty_run(["research-token", "--scope", "topics"], new)
check(code == 0 and "scope checked: topics" in out and TOPICS_FILE.exists() and not TOKEN_FILE.exists(), f"topics token stored in its own file: {code} {out!r}")
check(oct(TOPICS_FILE.stat().st_mode)[-3:] == "600" and up.who and up.who[-1]["auth"] == f"Bearer {new}", "topics token file is 0600; scope asked once")
check("research-dispatch topics token:" in out, "prompt names the topics token")
up.who_mode = {"body": b'{"name":"adm","scope":"admin"}'}
code, out = tty_run(["research-token", "--scope", "topics"], new)
check(code != 0 and "scope admin" in out and not TOPICS_FILE.exists(), f"admin token for topics: deleted again: {code} {out!r}")
code, out = tty_run(["research-token"], new)
check(code != 0 and not TOKEN_FILE.exists(), f"admin token for the monitor file: deleted again: {code} {out!r}")
up.who_mode = {"status": 404, "body": b"{}"}
code, out = tty_run(["research-token", "--scope", "topics"], new)
check(code == 0 and "could not check" in out and TOPICS_FILE.exists(), f"old deploy without whoami: kept with a note: {code} {out!r}")
code, out = tty_run(["research-token", "--scope", "topics", "--remove"], None)
check(code == 0 and not TOPICS_FILE.exists(), "--remove --scope topics deletes the topics file")

print(f"research: {passed} passed, {failed} failed")
for n in notes:
    print("note:", n)
subprocess.run(["rm", "-rf", str(tmp)])
sys.exit(1 if failed else 0)
