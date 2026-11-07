#!/usr/bin/env python3
"""Tests for the Topics panel backend: lib/niu_topics.py (validation, reshaping, transport, scope gate, errors, audit, rate limit)
and the /api/topics routes of `niu-dash serve`. A fake valkyrie-dispatch runs on a random loopback port. Fake tokens only; never
the real token files, never port 8080."""
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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WS = Path(__file__).resolve().parents[2]
SH = WS / ".claude-workspace"
FX = SH / "tests" / "fixtures" / "topics"
tmp = Path(tempfile.mkdtemp())
TOKEN = "rd_pat_TESTTOPICS_" + "b" * 24
TOKEN_FILE = tmp / "state" / "research-dispatch.topics.token"
CFG_FILE = tmp / "research.json"
os.environ.update(NIU_EVENTS_DB=str(tmp / "state" / "events.db"), NIU_TOPICS_TOKEN_FILE=str(TOKEN_FILE),
                  NIU_RESEARCH_TOKEN_FILE=str(tmp / "state" / "monitor.token"), NIU_RESEARCH_CONFIG=str(CFG_FILE),
                  NIU_CLAUDE_USAGE_API="0", NIU_CLAUDE_CREDENTIALS="/nonexistent/creds.json")
for k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(k, None)
sys.path.insert(0, str(SH / "lib"))
import niu_topics as nt  # noqa: E402

passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


ID1 = "00000001-0000-4000-8000-000000000001"
WHOAMI = "/api/v1/auth/whoami"


class Fake(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.log, self.scope, self.who_status, self.script, self.topics = [], "topics", 200, {}, []
        self.reset()
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def reset(self):
        self.log.clear()
        self.scope, self.who_status, self.script = "topics", 200, {}
        self.topics = json.loads((FX / "list_ok.json").read_text())

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def hits(self, path_prefix="/api/v1/topics"):
        return [x for x in self.log if x["path"].startswith(path_prefix)]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, status, obj=None, raw=None, headers=None, ctype="application/json"):
        body = raw if raw is not None else json.dumps(obj).encode() if obj is not None else b""
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if body:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def handle_any(self, method):
        s, u = self.server, urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        entry = {"method": method, "path": u.path, "query": q, "auth": self.headers.get("Authorization"),
                 "if_match": self.headers.get("If-Match"), "body": json.loads(raw) if raw else None, "ctype": self.headers.get("Content-Type")}
        s.log.append(entry)
        if u.path == WHOAMI:
            return self.reply(s.who_status, {"name": "fake", "scope": s.scope})
        if method in s.script:
            sc = s.script[method]
            return self.reply(sc.get("status", 200), sc.get("obj"), sc.get("raw"), sc.get("headers"), sc.get("ctype", "application/json"))
        if method == "GET":
            rows = [t for t in s.topics if t["is_active"] == (q.get("is_active", "true") == "true")
                    and ("agent_type" not in q or t["agent_type"] == q["agent_type"])]
            return self.reply(200, rows[:int(q.get("limit", "100"))])
        if method == "POST":
            t = {"id": "0000ffff-0000-4000-8000-00000000ffff", "is_active": True, "updated_at": "2026-11-07T09:00:00.000001Z", **entry["body"]}
            s.topics.append(t)
            return self.reply(201, t)
        tid = u.path.rsplit("/", 1)[1]
        t = next((x for x in s.topics if x["id"] == tid), None)
        if t is None:
            return self.reply(404, {"error": "not_found", "message": "topic not found"})
        if entry["if_match"] is not None and entry["if_match"] != f'"{t["updated_at"]}"':
            return self.reply(412, {"error": "topic_changed", "message": "SECRETMSG"})
        t.update(entry["body"])
        t["updated_at"] = "2026-11-07T10:00:00.000002Z"
        return self.reply(200, t, headers={"ETag": f'"{t["updated_at"]}"'})

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PATCH(self):
        self.handle_any("PATCH")

    def do_DELETE(self):
        self.handle_any("DELETE")


up, capture = Fake(), Fake()
NOW = [2_000_000.0]
nt._clock = lambda: NOW[0]
DEV = {"id": "dev1", "name": "laptop"}
DEV2 = {"id": "dev2", "name": "phone"}


def config(url=None, **kw):
    CFG_FILE.write_text(json.dumps({"enabled": True, "url": url or up.url, "poll_secs": 60, "timeout_secs": 2, **kw}))


def token_file(content=TOKEN + "\n", mode=0o600):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if TOKEN_FILE.is_symlink() or TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
    TOKEN_FILE.write_text(content)
    os.chmod(TOKEN_FILE, mode)


def reset(keep_good=False):
    NOW[0] += 1000
    up.reset()
    capture.reset()
    nt.SCOPES.clear()
    good = nt.CACHE["good"] if keep_good else None
    nt.CACHE.update(result=None, at=0.0, last_force=0.0, good=good)
    nt.WRITES = nt.RateLimiter(clock=lambda: NOW[0])
    config()
    token_file()


cfg_for = lambda: nt.nr.load_config()  # noqa: E731
FAIL = nt._Fail


def raises(fn, state=None, cause=None):
    try:
        fn()
    except FAIL as f:
        return (state is None or f.state == state) and (cause is None or f.cause == cause)
    return False


# ---------- 1. validation tables ----------
BASE = {"agent_type": "general", "name": "Name", "keywords": ["kw"]}
OKB = {**BASE, "excluded_terms": [], "weight": 1.0}
uniq = lambda n, ln=80, ch="x": [f"{i:03d}" + ch * (ln - 3) for i in range(n)]  # noqa: E731
CREATE = [
    (BASE, OKB),
    ({**BASE, "name": ""}, {"name": "too_short"}), ({**BASE, "name": "   "}, {"name": "too_short"}),
    ({**BASE, "name": "a"}, {**OKB, "name": "a"}), ({**BASE, "name": "x" * 120}, {**OKB, "name": "x" * 120}),
    ({**BASE, "name": "x" * 121}, {"name": "too_long"}), ({**BASE, "name": "  a \t b  "}, {"name": "forbidden_char"}),
    ({**BASE, "name": "  a    b  "}, {**OKB, "name": "a b"}),
    ({**BASE, "name": "a<b"}, {"name": "forbidden_char"}), ({**BASE, "name": "a`b"}, {"name": "forbidden_char"}),
    ({**BASE, "name": "a{b"}, {"name": "forbidden_char"}), ({**BASE, "name": "a}b"}, {"name": "forbidden_char"}),
    ({**BASE, "name": "a>b"}, {"name": "forbidden_char"}), ({**BASE, "name": "a b"}, {"name": "forbidden_char"}),
    ({**BASE, "name": "a b"}, {"name": "forbidden_char"}), ({**BASE, "name": "a\x00b"}, {"name": "forbidden_char"}),
    ({**BASE, "name": "a‮b"}, {"name": "forbidden_char"}), ({**BASE, "name": 5}, {"name": "required"}),
    ({k: v for k, v in BASE.items() if k != "name"}, {"name": "required"}),
    ({**BASE, "keywords": []}, {"keywords": "too_few"}), ({**BASE, "keywords": ["a"]}, {**OKB, "keywords": ["a"]}),
    ({**BASE, "keywords": uniq(20)}, {**OKB, "keywords": uniq(20)}), ({**BASE, "keywords": uniq(21)}, {"keywords": "too_many"}),
    ({**BASE, "keywords": ["x" * 81]}, {"keywords": "too_long"}), ({**BASE, "keywords": ["x" * 80]}, {**OKB, "keywords": ["x" * 80]}),
    ({**BASE, "keywords": ["Foo", "foo", " FOO "]}, {**OKB, "keywords": ["Foo"]}),
    ({**BASE, "keywords": ["", "a", "  "]}, {**OKB, "keywords": ["a"]}),
    ({**BASE, "keywords": ["a,b"]}, {**OKB, "keywords": ["a,b"]}), ({**BASE, "keywords": "a"}, {"keywords": "not_list"}),
    ({**BASE, "keywords": ["a\nb"]}, {"keywords": "forbidden_char"}), ({**BASE, "keywords": ["a<b"]}, {"keywords": "forbidden_char"}),
    ({**BASE, "keywords": [5]}, {"keywords": "not_list"}),
    ({k: v for k, v in BASE.items() if k != "keywords"}, {"keywords": "required"}),
    ({**BASE, "excluded_terms": uniq(50)}, {**OKB, "excluded_terms": uniq(50)}),
    ({**BASE, "excluded_terms": uniq(51)}, {"excluded_terms": "too_many"}),
    ({**BASE, "excluded_terms": ["a{"]}, {"excluded_terms": "forbidden_char"}),
    ({**BASE, "weight": 0.09}, {"weight": "out_of_range"}), ({**BASE, "weight": 0.1}, {**OKB, "weight": 0.1}),
    ({**BASE, "weight": 5.0}, {**OKB, "weight": 5.0}), ({**BASE, "weight": 5.01}, {"weight": "out_of_range"}),
    ({**BASE, "weight": float("nan")}, {"weight": "out_of_range"}), ({**BASE, "weight": float("inf")}, {"weight": "out_of_range"}),
    ({**BASE, "weight": "1"}, {"weight": "out_of_range"}), ({**BASE, "weight": True}, {"weight": "out_of_range"}),
    ({**BASE, "weight": 2}, {**OKB, "weight": 2.0}), ({**BASE, "weight": 1.2345}, {**OKB, "weight": 1.23}),
    ({**BASE, "agent_type": "admin"}, {"agent_type": "bad_enum"}), ({**BASE, "agent_type": "hobby"}, {**OKB, "agent_type": "hobby"}),
    ({k: v for k, v in BASE.items() if k != "agent_type"}, {"agent_type": "required"}),
    ({**BASE, "notes": "x"}, {"notes": "unknown"}), ({**BASE, "is_active": True}, {"is_active": "unknown"}),
    ({**BASE, "keywords": [f"{i:03d}" + "😀" * 77 for i in range(20)], "excluded_terms": [f"{i:03d}" + "😀" * 77 for i in range(50)]},
     {"body": "toolarge"}),
    ("not a dict", {"body": "empty"}),
]
for raw, want in CREATE:
    body, errs = nt.validate_create(raw)
    got = body if errs is None else errs
    check(got == want, f"validate_create({str(raw)[:70]!r}) -> {str(got)[:90]} (want {str(want)[:90]})")
PATCH = [
    ({}, {"fields": "empty"}), ([], {"fields": "empty"}), (None, {"fields": "empty"}),
    ({"agent_type": "hobby"}, {"agent_type": "unknown"}), ({"notes": "x"}, {"notes": "unknown"}), ({"id": ID1}, {"id": "unknown"}),
    ({"is_active": "true"}, {"is_active": "not_bool"}), ({"is_active": 1}, {"is_active": "not_bool"}),
    ({"is_active": False}, {"is_active": False}), ({"is_active": True}, {"is_active": True}),
    ({"name": "New"}, {"name": "New"}), ({"name": "a<"}, {"name": "forbidden_char"}), ({"name": ""}, {"name": "too_short"}),
    ({"keywords": []}, {"keywords": "too_few"}), ({"excluded_terms": []}, {"excluded_terms": []}),
    ({"weight": 3}, {"weight": 3.0}), ({"weight": 9}, {"weight": "out_of_range"}),
    ({"name": "A", "weight": 0.5, "is_active": True}, {"name": "A", "weight": 0.5, "is_active": True}),
    ({"keywords": [f"{i:03d}" + "😀" * 77 for i in range(20)], "excluded_terms": [f"{i:03d}" + "😀" * 77 for i in range(50)]},
     {"body": "toolarge"}),
]
for raw, want in PATCH:
    body, errs = nt.validate_patch(raw)
    got = body if errs is None else errs
    check(got == want, f"validate_patch({str(raw)[:70]!r}) -> {str(got)[:90]} (want {str(want)[:90]})")
check(len(CREATE) + len(PATCH) >= 60, "validation table is large enough")

# ---------- 2. shape_topic ----------
hostile = json.loads((FX / "hostile.json").read_text())
shaped = [nt.shape_topic(r) for r in hostile]
KEYS = {"id", "agent_type", "name", "keywords", "excluded_terms", "weight", "is_active", "updated_at", "over_api_limits"}
good = [s for s in shaped if s]
check(all(set(s) == KEYS for s in good), "shape_topic output keys are exactly the allowlist")
blob = json.dumps(good)
for needle in ("Ignore previous", "rd_pat_", "secret", "prompt_text", "AAAAAAAA", "../usage", "bad agent", "weight nan", "active string"):
    check(needle not in blob, f"hostile row: {needle!r} does not survive shape_topic")
check(len([s for s in shaped if s is None]) == 8, f"bad id / agent / weight / flag / non-dict rows dropped: {sum(1 for s in shaped if s is None)}")
first = good[0]
check(first["name"] == "<img src=x onerror=alert(1)>" and first["over_api_limits"], "hostile text kept literally (rendered with textContent), flagged")
check("\x00" not in first["keywords"][1] and "�" in first["keywords"][1], "control characters become U+FFFD")
big = next(s for s in good if s["name"].startswith("N"))
check(len(big["name"]) == 200 and big["name"].endswith("…") and len(big["keywords"]) == 50 and len(big["excluded_terms"][0]) == 100
      and big["over_api_limits"], "display caps: name 200, 50 terms, term 100, flagged")
ctrl = next(s for s in good if s["id"].startswith("0000000a"))
check("\x07" not in ctrl["name"] and "‮" not in ctrl["name"] and ctrl["updated_at"] is None, "control / bidi characters replaced; bad updated_at dropped")
over = nt.shape_topic(json.loads((FX / "cli_overlimit.json").read_text())[0])
check(over and over["over_api_limits"] and len(over["name"]) == 180 and len(over["keywords"]) == 30, "CLI topic over the API limits is kept and flagged")
okrow = nt.shape_topic(json.loads((FX / "list_ok.json").read_text())[0])
check(okrow and not okrow["over_api_limits"] and okrow["updated_at"] == "2026-11-07T08:00:00.123456Z", "normal topic is not flagged")
check(nt.shape_topic({**json.loads((FX / "list_ok.json").read_text())[0], "id": "AAAAAAAA-0000-4000-8000-AAAAAAAAAAAA"}) is None, "uppercase id dropped")

# ---------- 3. error mapping ----------
MAP = [
    (409, {"error": "duplicate_topic", "message": "SECRETMSG", "existing_id": ID1, "is_active": False},
     {"code": "duplicate", "existing_active": False, "existing_id": ID1}),
    (409, {"error": "duplicate_topic", "existing_id": "../x", "is_active": True}, {"code": "duplicate", "existing_active": True}),
    (409, {"error": "topic_cap_reached", "message": "SECRETMSG"}, {"code": "cap"}), (409, {"error": "conflict"}, {"code": "error", "cause": "server"}),
    (412, {"error": "topic_changed", "message": "SECRETMSG"}, {"code": "changed"}), (404, {}, {"code": "not_found"}),
    (413, {}, {"code": "toolarge"}), (429, {"error": "rate_limited"}, {"code": "rate_limited"}),
    (422, {"error": "validation_error", "details": [{"loc": ["body", "name"], "msg": "SECRETMSG"}, {"loc": ["body", "evil"], "msg": "SECRETMSG"},
                                                    {"loc": ["body", "keywords", "0"], "msg": "x"}, "junk"]},
     {"code": "invalid", "fields": {"name": "invalid"}}),
    (422, None, {"code": "invalid", "fields": {}}), (401, {}, {"code": "unauthorized"}), (403, {}, {"code": "wrong_scope"}),
    (500, {"message": "SECRETMSG"}, {"code": "error", "cause": "server"}), (502, "junk", {"code": "error", "cause": "server"}),
]
for status, body, want in MAP:
    got = nt.map_upstream_error(status, body)
    check(got == want and "SECRETMSG" not in json.dumps(got), f"map_upstream_error({status}) -> {got}")

# ---------- 4. paths and SSRF ----------
reset()
token_file()
cfg = cfg_for()
BADIDS = ["../usage/gate", "%2e%2e", "00000000-0000-0000-0000-00000000000G", "AAAAAAAA-0000-4000-8000-AAAAAAAAAAAA", ID1 + "?x=1", ID1 + "/feedback", "", " " + ID1, ID1 + "\n"]
for bad in BADIDS:
    check(raises(lambda b=bad: nt._call(cfg, TOKEN, "PATCH", "/api/v1/topics/" + b, body={}), "error", "internal"), f"_call refuses path id {bad!r}")
    st, payload = nt.write("update", DEV, {"id": bad, "fields": {"name": "x"}})
    check(st == 400 and payload["code"] == "invalid", f"write refuses id {bad!r}: {st}")
for bad_path in ("/api/v1/usage/gate", "/api/v1/monitor/summary", "/api/v1/topics/" + ID1 + "/feedback", "api/v1/topics", "/api/v1/topics/"):
    check(raises(lambda p=bad_path: nt._call(cfg, TOKEN, "GET", p), "error", "internal"), f"_call refuses path {bad_path!r}")
for m in ("DELETE", "PUT", "get", "OPTIONS"):
    check(raises(lambda mm=m: nt._call(cfg, TOKEN, mm, "/api/v1/topics"), "error", "internal"), f"_call refuses method {m}")
for q in ({"agent_type": "admin"}, {"agent_type": "../x"}, {"is_active": "yes"}, {"limit": "5"}, {"limit": True}, {"x": 1}, {"offset": -1}):
    check(raises(lambda qq=q: nt._call(cfg, TOKEN, "GET", "/api/v1/topics", query=qq), "error", "internal"), f"_call refuses query {q}")
check(raises(lambda: nt._call(cfg, TOKEN, "PATCH", f"/api/v1/topics/{ID1}", body={}, if_match="x\r\nEvil: 1"), "error", "internal"), "If-Match must be a timestamp")
check(not up.log, f"no request was made for any of the bad paths / ids / queries: {up.log}")
check(raises(lambda: nt._call({**cfg, "url": "http://localhost:80"}, TOKEN, "GET", "/api/v1/topics"), "error", "url"), "bad base URL refused")
check(raises(lambda: nt._call(cfg, TOKEN, "POST", "/api/v1/topics", body={"name": "é" * 9000}), "error", "toolarge") and not up.log, "request body over 16 KiB refused locally")
# redirects and proxies
reset()
up.script = {"GET": {"status": 302, "headers": {"Location": capture.url + "/steal"}}}
res = nt.list_topics()
check(res["state"] == "incompatible" and not capture.log, f"redirect -> incompatible and nothing reaches the target: {res['state']} {capture.log}")
reset()
for k in ("http_proxy", "HTTP_PROXY", "all_proxy"):
    os.environ[k] = capture.url
res = nt.list_topics()
for k in ("http_proxy", "HTTP_PROXY", "all_proxy"):
    os.environ.pop(k)
check(res["state"] == "ok" and not capture.log and up.hits(), f"env proxies ignored: {res['state']} proxy hits {len(capture.log)}")

# ---------- 5. scope gate ----------
for scope, state in (("admin", "too_powerful"), ("read", "too_powerful"), ("monitor", "wrong_scope"), ("weird", "wrong_scope")):
    reset()
    up.scope = scope
    res = nt.list_topics()
    check(res["state"] == state and not up.hits() and res["topics"] == [] and "topics-scope" in res["message"], f"{scope} token -> {state}, no topic request: {res['state']} {len(up.hits())}")
    st, payload = nt.write("create", DEV, BASE)
    check(st == 502 and payload["code"] == state and not up.hits(), f"write with a {scope} token refused before any topic request: {st} {payload}")
    check(TOKEN not in json.dumps(res) + json.dumps(payload), "token not in the answer")
reset()
up.scope = "admin"
check("scope admin" in nt.list_topics()["message"], "message names the scope")
up.scope = "weird<script>"
nt.SCOPES.clear()
check("<script>" not in nt.list_topics()["message"], "unknown scope names are not echoed")
reset()
up.who_status = 404
res = nt.list_topics()
check(res["state"] == "incompatible" and "/auth/whoami" in res["message"] and not up.hits(), f"whoami 404 -> incompatible: {res['state']}")
reset()
nt.list_topics(force=True)
nt.list_topics(force=True)
check(len([x for x in up.log if x["path"] == WHOAMI]) == 1, "whoami cached within 300 s: one call for two lists")
NOW[0] += 301
nt.list_topics(force=True)
check(len([x for x in up.log if x["path"] == WHOAMI]) == 2, "whoami asked again after 300 s")

# ---------- 6. list, cache, states ----------
reset()
res = nt.list_topics()
check(res["state"] == "ok" and len(res["topics"]) == 4 and res["dropped"] == 0 and not res["stale"] and not res["archived_truncated"],
      f"list: 3 active + 1 archived: {res['state']} {len(res['topics'])}")
check(sum(1 for t in res["topics"] if not t["is_active"]) == 1 and res["limits"]["name"] == 120 and res["limits"]["active_per_agent"] == 200, "archived row and limits present")
calls = [(x["query"].get("agent_type"), x["query"].get("is_active"), x["query"].get("limit")) for x in up.hits()]
check(calls == [("general", "true", "200"), ("hobby", "true", "200"), ("fintrend", "true", "200"), (None, "false", "200")], f"four list calls: {calls}")
check(all(x["auth"] == f"Bearer {TOKEN}" and x["method"] == "GET" for x in up.log), "token sent as a bearer header, GETs only")
n = len(up.log)
nt.list_topics()
check(len(up.log) == n, "second list within 10 s is cached")
NOW[0] += 11
nt.list_topics()
check(len(up.log) > n, "cache expires after 10 s")
n = len(up.log)
nt.refresh()
check(len(up.log) > n, "refresh forces a reload")
n = len(up.log)
nt.refresh()
check(len(up.log) == n, "refresh is throttled to once per 5 s")
NOW[0] += 6
nt.refresh()
check(len(up.log) > n, "refresh allowed again after 5 s")
reset()
up.topics = [{**json.loads((FX / "list_ok.json").read_text())[3], "id": f"{i:08x}-0000-4000-8000-{i:012x}"} for i in range(1, 201)]
res = nt.list_topics()
check(res["archived_truncated"] is True and len(res["topics"]) == 200, "200 archived topics -> archived_truncated")
reset()
up.script = {"GET": {"obj": hostile}}
res = nt.list_topics()
check(res["state"] == "ok" and res["dropped"] > 0 and all(set(t) == KEYS and nt.UUID_RE.fullmatch(t["id"]) for t in res["topics"])
      and "Ignore previous" not in json.dumps(res), "hostile list through list_topics")
reset()
up.script = {"GET": {"obj": {"not": "a list"}}}
res = nt.list_topics()
check(res["state"] == "error" and res["cause"] == "badjson", f"object instead of a list -> error: {res['state']} {res['cause']}")
# states
reset()
TOKEN_FILE.unlink()
res = nt.list_topics()
check(res["state"] == "not_configured" and not up.log and "--scope topics" in res["message"], "no token file -> not_configured and no request")
token_file(mode=0o644)
res = nt.list_topics()
check(res["state"] == "error" and res["cause"] == "perms" and not up.log, "mode 0644 refused")
TOKEN_FILE.unlink()
real = tmp / "real.token"
real.write_text(TOKEN)
os.chmod(real, 0o600)
TOKEN_FILE.symlink_to(real)
res = nt.list_topics()
check(res["state"] == "error" and res["cause"] == "perms" and not up.log, "symlink refused")
TOKEN_FILE.unlink()
for junk in ("hello", "rd_pat_short", TOKEN + " extra"):
    token_file(content=junk)
    res = nt.list_topics()
    check(res["state"] == "error" and res["cause"] == "format" and not up.log, f"bad token content refused: {junk[:10]!r}")
config(enabled=False)
token_file()
check(nt.list_topics()["state"] == "disabled" and not up.log, "disabled research config -> disabled")
for url in ("http://localhost:1", "https://example.com", "http://127.0.0.1:0"):
    config(url=url)
    res = nt.list_topics()
    check(res["state"] == "error" and res["cause"] == "url" and not up.log, f"bad URL {url} -> error/url")
# unreachable keeps the last good list, marked stale
reset()
first = nt.list_topics()
s = socket.socket()
s.bind(("127.0.0.1", 0))
dead = s.getsockname()[1]
s.close()
NOW[0] += 20
nt.SCOPES.clear()
config(url=f"http://127.0.0.1:{dead}")
res = nt.list_topics()
check(res["state"] == "unreachable" and res["stale"] is True and len(res["topics"]) == len(first["topics"]) and f"127.0.0.1:{dead}" in res["message"]
      and res["loaded_at"] == first["loaded_at"], f"unreachable: last good list, stale: {res['state']} stale={res['stale']}")
config()
for status, state in ((401, "unauthorized"),):
    reset()
    up.script = {"GET": {"status": status, "obj": {"error": "x", "message": "SECRETMSG"}}}
    res = nt.list_topics()
    check(res["state"] == state and "SECRETMSG" not in json.dumps(res), f"list HTTP {status} -> {state}")
reset()
up.script = {"GET": {"status": 500, "raw": b"boom " + TOKEN.encode()}}
res = nt.list_topics()
check(res["state"] == "error" and res["cause"] == "server" and TOKEN not in json.dumps(res), "HTTP 500 -> error/server with a fixed message")
reset()
up.script = {"GET": {"raw": b"[" + b"1," * (600 * 1024) + b"1]"}}
res = nt.list_topics()
check(res["state"] == "error" and res["cause"] == "toolarge", f"a list over 1 MiB -> error/toolarge: {res['state']} {res['cause']}")
reset()
up.script = {"GET": {"raw": b"<html>", "ctype": "text/html"}}
check(nt.list_topics()["state"] == "incompatible", "non-JSON list -> incompatible")
reset()
up.script = {"GET": {"raw": b"{not json"}}
check(nt.list_topics()["cause"] == "badjson", "bad JSON -> badjson")
reset()
config(timeout_secs=1)
t0 = time.time()
# a server that accepts but never answers
silent = socket.socket()
silent.bind(("127.0.0.1", 0))
silent.listen(5)
config(url=f"http://127.0.0.1:{silent.getsockname()[1]}", timeout_secs=1)
res = nt.list_topics()
silent.close()
check(res["state"] == "unreachable" and time.time() - t0 < 4, f"timeout -> unreachable: {res['state']} {time.time() - t0:.1f}s")
config()

# ---------- 7. writes, If-Match, rate limit, audit ----------
reset()
st, p = nt.write("create", DEV, {"agent_type": "general", "name": "  New   topic ", "keywords": ["A", "a", "b"], "weight": 2})
post = [x for x in up.log if x["method"] == "POST"]
check(st == 200 and p["ok"] and p["topic"]["name"] == "New topic" and nt.CACHE["result"] is None, f"create ok, cache invalidated: {st} {p}")
check(len(post) == 1 and post[0]["body"] == {"agent_type": "general", "name": "New topic", "keywords": ["A", "b"], "excluded_terms": [], "weight": 2.0}
      and post[0]["path"] == "/api/v1/topics" and post[0]["auth"] == f"Bearer {TOKEN}" and "json" in post[0]["ctype"], "the clean body is what is sent")
check(len([x for x in up.log if x["path"] == WHOAMI]) == 1, "scope checked once for the write")
st, p = nt.write("create", DEV, {**BASE, "notes": "x"})
check(st == 400 and p["code"] == "invalid" and len(up.log) == 2, "unknown key refused locally")
st, p = nt.write("create", DEV, {**BASE, "name": "a<b"})
check(st == 400 and p == {"ok": False, "code": "invalid", "fields": {"name": "forbidden_char"}} and len(up.log) == 2, f"validation error: field + code: {p}")
st, p = nt.write("create", DEV, {**BASE, "keywords": [f"{i:03d}" + "😀" * 77 for i in range(20)], "excluded_terms": [f"{i:03d}" + "😀" * 77 for i in range(50)]})
check(st == 400 and p["code"] == "toolarge", "too much text -> toolarge")
st, p = nt.write("update", DEV, {"id": ID1, "if_match": "2026-11-07T08:00:00.123456Z", "fields": {"is_active": False}})
patch = [x for x in up.log if x["method"] == "PATCH"]
check(st == 200 and p["topic"]["is_active"] is False and patch[0]["if_match"] == '"2026-11-07T08:00:00.123456Z"' and patch[0]["body"] == {"is_active": False}
      and patch[0]["path"] == f"/api/v1/topics/{ID1}", f"update sends If-Match and only changed fields: {st} {patch}")
st, p = nt.write("update", DEV, {"id": ID1, "if_match": "2026-11-07T08:00:00.123456Z", "fields": {"name": "Other"}})
check(st == 412 and p == {"ok": False, "code": "changed"} and "SECRETMSG" not in json.dumps(p), f"stale If-Match -> 412 changed: {st} {p}")
st, p = nt.write("update", DEV, {"id": ID1, "if_match": "evil\r\nX: 1", "fields": {"name": "Other"}})
check(st == 200 and [x for x in up.log if x["method"] == "PATCH"][-1]["if_match"] is None, "a bad if_match is dropped, not forwarded")
st, p = nt.write("update", DEV, {"id": ID1, "fields": {"agent_type": "hobby"}})
check(st == 400 and p["code"] == "invalid", "agent_type cannot be patched")
st, p = nt.write("update", DEV, {"id": ID1, "fields": {}})
check(st == 400, "empty patch refused")
st, p = nt.write("update", DEV, {"id": ID1, "extra": 1, "fields": {"name": "x"}})
check(st == 400, "unknown top-level key refused")
nt.WRITES = nt.RateLimiter(clock=lambda: NOW[0])   # the ten above used up this minute
st, p = nt.write("update", DEV, {"id": "00000002-0000-4000-8000-0000000000ff", "fields": {"name": "x"}})
check(st == 404 and p["code"] == "not_found", "unknown topic -> not_found")
reset()
up.script = {"POST": {"status": 409, "obj": {"error": "duplicate_topic", "message": "SECRETMSG", "existing_id": ID1, "is_active": False}}}
st, p = nt.write("create", DEV, BASE)
check(st == 409 and p == {"ok": False, "code": "duplicate", "existing_active": False, "existing_id": ID1}, f"duplicate mapping: {st} {p}")
up.script = {"POST": {"status": 409, "obj": {"error": "topic_cap_reached"}}}
check(nt.write("create", DEV, BASE)[1]["code"] == "cap", "cap mapping")
up.script = {"POST": {"status": 201, "obj": {"id": "NOPE"}}}
st, p = nt.write("create", DEV, BASE)
check(st == 502 and p["code"] == "error" and p["cause"] == "format", "malformed upstream topic -> error/format")
up.script = {"POST": {"status": 500, "raw": TOKEN.encode()}}
st, p = nt.write("create", DEV, BASE)
check(st == 502 and TOKEN not in json.dumps(p), "upstream 500 -> 502 without upstream text")
# unreachable write
config(url=f"http://127.0.0.1:{dead}")
nt.SCOPES.clear()
st, p = nt.write("create", DEV, BASE)
check(st == 502 and p["code"] == "unreachable", "write while unreachable -> unreachable")
config()
# rate limit: 10 per minute per device, 100 per day, devices independent
reset()
codes = [nt.write("create", DEV, {**BASE, "name": f"T{i}"})[0] for i in range(10)]
n = len(up.log)
st, p = nt.write("create", DEV, {**BASE, "name": "T11"})
check(codes == [200] * 10 and st == 429 and p["code"] == "rate_limited" and len(up.log) == n, f"11th write in a minute -> 429, no upstream call: {codes} {st}")
check(nt.write("create", DEV2, BASE)[0] == 200, "a second device is independent")
NOW[0] += 61
check(nt.write("create", DEV, BASE)[0] == 200, "the window slides")
nt.WRITES = nt.RateLimiter(clock=lambda: NOW[0])
res = []
for i in range(101):
    NOW[0] += 7
    res.append(nt.write("create", DEV, {**BASE, "name": f"D{i}"})[0])
check(res[:100] == [200] * 100 and res[100] == 429, f"100 writes a day: {res.count(200)} ok, last {res[-1]}")
# audit rows: ids and field names only
con = sqlite3.connect(os.environ["NIU_EVENTS_DB"])
rows = con.execute("SELECT event, summary, tool, project FROM events WHERE event LIKE 'TopicChange%'").fetchall()
con.close()
check(rows and all(r[2] == "topics" and r[3] == "valkyrie-dispatch" for r in rows), f"audit rows exist: {len(rows)}")
okrows = [r[1] for r in rows if r[0] == "TopicChange"]
pat = re.compile(r"^(create|update|activate|deactivate) [0-9a-f]{8} \[[a-z_,]*\] by laptop$")
check(okrows and all(pat.match(s) for s in okrows) or any(pat.match(s) for s in okrows) and all(s.endswith(("by laptop", "by phone")) for s in okrows),
      f"audit summaries match: {okrows[:3]}")
reset()
nt.write("create", DEV, {**BASE, "name": "SECRET-NAME", "keywords": ["SECRET-KW"]})
nt.write("update", DEV, {"id": ID1, "fields": {"keywords": ["SECRET-KW2"], "weight": 1.5}})
nt.write("update", DEV, {"id": ID1, "fields": {"is_active": False}})
nt.write("update", DEV, {"id": ID1, "fields": {"is_active": True}})
con = sqlite3.connect(os.environ["NIU_EVENTS_DB"])
rows = [r[0] for r in con.execute("SELECT summary FROM events WHERE event='TopicChange' ORDER BY id DESC LIMIT 4").fetchall()][::-1]
refused = con.execute("SELECT COUNT(*) FROM events WHERE event='TopicChangeRefused'").fetchone()[0]
con.close()
actions = [r.split()[0] for r in rows]
check(actions == ["create", "update", "deactivate", "activate"], f"actions recorded: {actions}")
check(all(pat.match(r) for r in rows) and "[keywords,weight]" in rows[1] and "SECRET" not in " ".join(rows), f"audit has ids and field names only: {rows}")
check(refused >= 1, "refusals (rate limit, 4xx) are audited too")
con = sqlite3.connect(os.environ["NIU_EVENTS_DB"])
allsum = " ".join(r[0] for r in con.execute("SELECT summary FROM events WHERE event LIKE 'TopicChange%'"))
con.close()
check("SECRET" not in allsum and "New topic" not in allsum, "no topic text anywhere in the audit rows")

# nothing in any fake log is outside the topics / whoami contract (no runs, no feedback, no DELETE)
allowed = re.compile(r"^/api/v1/(auth/whoami|topics(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?)$")
check(all(allowed.match(x["path"]) and x["method"] in ("GET", "POST", "PATCH") for x in up.log + capture.log), "only whoami and topics paths, only GET / POST / PATCH")

# ---------- 8. the real server ----------
reset()
COOKIE = "TESTCOOKIE" + "c" * 30
(tmp / "state").mkdir(parents=True, exist_ok=True)
(tmp / "state" / "web-sessions.json").write_text(json.dumps({"pair": None, "devices": [
    {"id": "abcd1234", "name": "testdev", "hash": hashlib.sha256(COOKIE.encode()).hexdigest(), "created": time.time(), "last_seen": time.time()}]}))
os.chmod(tmp / "state" / "web-sessions.json", 0o600)
port = (lambda s: (s.bind(("127.0.0.1", 0)), s.getsockname()[1], s.close())[1])(socket.socket())
srv = subprocess.Popen([str(SH / "bin" / "niu-dash"), "serve", "--port", str(port)], env=dict(os.environ), stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, text=True)
ORIGIN = f"http://127.0.0.1:{port}"
POSTH = {"X-Niu-CSRF": "1", "Origin": ORIGIN}
AUTH = {**POSTH, "Cookie": f"niu_session={COOKIE}"}
seen = []


def req(method, path, body=None, headers=None, raw=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    seen.append(out)
    try:
        js = json.loads(out)
    except ValueError:
        js = None
    return r.status, js


err = ""
try:
    for _ in range(50):
        try:
            if req("GET", "/healthz")[0] == 200:
                break
        except OSError:
            time.sleep(0.1)
    st, js = req("GET", "/api/topics")
    check(st == 401 and js["state"] == "not_paired" and js["topics"] == [], f"GET /api/topics without a cookie -> 401 not_paired: {st}")
    check(req("GET", "/api/topics", headers=POSTH)[0] == 401, "forged CSRF / origin headers do not replace the cookie")
    check(req("GET", "/api/topics", headers={"Cookie": "niu_session=wrong"})[0] == 401, "wrong cookie -> 401")
    check(not up.log, "no upstream request for an unpaired caller")
    st, js = req("GET", "/api/topics", headers={"Cookie": f"niu_session={COOKIE}"})
    check(st == 200 and js["state"] == "ok" and len(js["topics"]) == 4, f"paired GET /api/topics: {st} {js and js.get('state')}")
    body = {"agent_type": "hobby", "name": "Via server", "keywords": ["k"]}
    check(req("POST", "/api/topics/create", body, {"Origin": ORIGIN})[0] == 403, "create without CSRF header -> 403")
    check(req("POST", "/api/topics/create", body, {"X-Niu-CSRF": "1", "Origin": "https://evil.example"})[0] == 403, "cross-origin create -> 403")
    check(req("POST", "/api/topics/create", body, POSTH)[0] == 401, "create with CSRF and origin but no cookie -> 401")
    check(req("POST", "/api/topics/update", {"id": ID1, "fields": {"name": "x"}}, POSTH)[0] == 401, "update without a cookie -> 401")
    check(req("POST", "/api/topics/refresh", {}, POSTH)[0] == 401, "refresh without a cookie -> 401")
    check(not [x for x in up.log if x["method"] != "GET"], "no write reached upstream so far")
    st, js = req("POST", "/api/topics/create", body, AUTH)
    check(st == 200 and js["ok"] and js["topic"]["name"] == "Via server", f"paired create -> 200: {st} {js}")
    st, js = req("POST", "/api/topics/update", {"id": ID1, "if_match": "2026-11-07T08:00:00.123456Z", "fields": {"is_active": False}}, AUTH)
    check(st == 200 and js["topic"]["is_active"] is False, f"paired update -> 200: {st} {js}")
    check(req("POST", "/api/topics/refresh", {}, AUTH)[0] == 200, "paired refresh -> 200")
    # strict body reader
    st, js = req("POST", "/api/topics/create", None, AUTH, raw=b"{" + b" " * 20000 + b"}")
    check(st == 413 and js["code"] == "toolarge", f"body over 16 KiB -> 413: {st}")
    st, js = req("POST", "/api/topics/create", None, AUTH, raw=b"{not json")
    check(st == 400 and js["code"] == "badjson", f"bad JSON -> 400: {st}")
    st, js = req("POST", "/api/topics/create", None, AUTH, raw=b"[1]")
    check(st == 400, "non-object body -> 400")
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall((f"POST /api/topics/create HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-Niu-CSRF: 1\r\nOrigin: {ORIGIN}\r\nCookie: niu_session={COOKIE}\r\n"
               "Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n").encode())
    first_line = s.recv(200).split(b"\r\n")[0]
    s.close()
    check(b" 413 " in first_line, f"no Content-Length -> 413: {first_line!r}")
    # the token never leaks, whatever upstream says (the fake echoes the Authorization header into its error bodies)
    for status in (401, 403, 500):
        up.script = {m: {"status": status, "obj": {"error": "x", "message": f"echo {TOKEN}"}} for m in ("GET", "POST", "PATCH")}
        nt_req = [req("POST", "/api/topics/refresh", {}, AUTH), req("POST", "/api/topics/update", {"id": ID1, "fields": {"name": "n"}}, AUTH)]
        time.sleep(0.05)
        req("GET", "/api/topics", headers={"Cookie": f"niu_session={COOKIE}"})
        check(all(r[0] in (200, 502) for r in nt_req), f"upstream {status}: handled ({[r[0] for r in nt_req]})")
    req("GET", "/api/state")
    check(all(TOKEN.encode() not in b for b in seen), "token not in any dashboard response (incl. /api/state)")
    # rate limit through the server: 10 per minute per paired device, no upstream call for the refused ones
    up.reset()
    results = [req("POST", "/api/topics/create", {**body, "name": f"R{i}"}, AUTH)[0] for i in range(14)]
    check(429 in results and results.index(429) <= 10 and results[-1] == 429, f"server rate limit: {results}")
    check(len([x for x in up.log if x["method"] == "POST"]) == results.count(200), "refused writes made no upstream call")
finally:
    srv.terminate()
    try:
        err = srv.communicate(timeout=5)[1]
    except subprocess.TimeoutExpired:
        srv.kill()
        err = srv.communicate()[1]
check(TOKEN not in (err or "") and COOKIE not in (err or ""), f"token / cookie not in the server's stderr: {(err or '')[:200]!r}")

print(f"topics: {passed} passed, {failed} failed")
subprocess.run(["rm", "-rf", str(tmp)])
sys.exit(1 if failed else 0)
