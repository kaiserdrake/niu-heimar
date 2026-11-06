#!/usr/bin/env python3
"""Tests for niu-notify (lib/niu_notify.py, bin/niu-notify): templates, privacy, transitions, rate limits, secrets.

No network: a FakeSender records payloads. Webhook URLs below are syntactically valid fakes.
"""
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
from pathlib import Path

SH = Path(__file__).resolve().parents[1]
tmp = Path(tempfile.mkdtemp())
os.environ["NIU_EVENTS_DB"] = str(tmp / "state" / "events.db")
os.environ["NIU_NOTIFY_CONFIG"] = str(tmp / "discord.json")
os.environ["NIU_CLAUDE_USAGE_API"] = "0"
os.environ["NIU_CLAUDE_CREDENTIALS"] = "/nonexistent/creds.json"
sys.path.insert(0, str(SH / "lib"))
import niu_notify as nn  # noqa: E402

passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


TOKEN = "T0K3N" + "aB1-" * 15 + "xyz"           # 68 chars, fake
URL = {r: f"https://discord.com/api/webhooks/1234567890123456{i:02d}/{TOKEN}{i}" for i, r in enumerate(nn.ROUTES)}
ENV = {nn.DEFAULTS["routes"][r]: u for r, u in URL.items()}


class FakeSender:
    def __init__(self, script=None):
        self.posts, self.script = [], list(script or [])

    def post(self, url, payload):
        self.posts.append((url, payload))
        return self.script.pop(0) if self.script else (204, None)


def cfg_with(**over):
    (tmp / "discord.json").write_text(json.dumps(over))
    return nn.load_config()


def sess(sid, state="idle", domain="PERSONAL", project="app", since=None, note=None, flagged=False):
    return {"id": sid, "domain": domain, "project": project, "state": state, "since": since or 0, "ended": False,
            "note": note, "flagged": flagged}


def obs(sessions=(), prs=None, llm=None, limits=None, dash=None, errors=()):
    return {"sessions": list(sessions), "prs": prs, "llm": llm, "limits": limits, "dash": dash, "errors": list(errors)}


def contents(msgs, cfg):
    return [nn.render(m, cfg)["content"] for m in msgs if nn.render(m, cfg)]


cfg = cfg_with()
NOW = 1_800_000_000.0

# 1. payload shape
p = nn.render({"route": "alerts", "tpl": "dash-down", "f": {}}, cfg)
check(p["allowed_mentions"] == {"parse": []}, "mentions disabled")
check(p["flags"] & nn.FLAG_SUPPRESS_EMBEDS and not p["flags"] & nn.FLAG_SILENT, f"embeds suppressed, not silent: {p}")
check(p["username"] == "niu-heimar" and set(p) == {"content", "username", "allowed_mentions", "flags"}, f"no embeds/avatar/tts: {p}")
check(nn.render({"route": "llm", "tpl": "llm-down", "f": {"label": "GPU"}, "silent": True}, cfg)["flags"] & nn.FLAG_SILENT, "silent flag")
check(nn.render({"route": "x", "tpl": "nope", "f": {}}, cfg) is None, "unknown template dropped")

# 2. sanitizing
evil = "@everyone <@123> [x](http://evil) **b** `c`\nghp_" + "A1b2" * 9
p = nn.render({"route": "ci", "tpl": "pr-failing", "f": {"ref": "o/r#5", "domain": "PERSONAL", "project": "r", "title": evil, "n": 2}}, cfg)
c = p["content"]
check("@" not in c and "<" not in c and "](" not in c and "**" not in c and "ghp_" not in c, f"title sanitized: {c!r}")
check(c.count("`") == 2 and c.count("\n") == 1 and c.endswith("https://github.com/o/r/pull/5"), f"code span, url from ref: {c!r}")
check(nn.render({"route": "ci", "tpl": "pr-ready", "f": {"ref": "o/r#5 http://evil", "title": "x"}}, cfg) is None, "bad ref dropped")
check(nn.where("PERSONAL", "../x", cfg) == "PERSONAL/(project)" and nn.where("PERSONAL", "a b", cfg) == "PERSONAL/(project)", "bad project")
check(nn.where("(workspace)", "", cfg) == "workspace", "workspace label is never the hostname")
check(nn.where("evil<@1>", "p", cfg) == "(domain)/p", "bad domain")
check(len(nn.render({"route": "ci", "tpl": "pr-ready", "f": {"ref": "o/r#5", "title": "x" * 500}}, cfg)["content"]) <= 400, "cap")
check(nn.sanitize_label("gpu-box.lan:11434", "endpoint 1") == "endpoint 1", "label with ':' falls back")

# 3. privacy: domain modes, hostnames, IPs
cfgm = cfg_with(domains={"WORK": "minimal"})
st, m = nn.diff({}, obs(), cfgm, NOW)
st, m = nn.diff(st, obs([sess("w1", "waiting", "WORK", "acme-portal", note="needs permission")]), cfgm, NOW + 100)
st, m = nn.diff(st, obs([sess("w1", "waiting", "WORK", "acme-portal", note="needs permission")]), cfgm, NOW + 200)
cs = contents(m, cfgm)
check(cs == ["[needs you] WORK: a session needs your permission"], f"WORK minimal: {cs}")
prs = {"repos": [{"label": "WORK/acme-portal", "repo": "acme/portal", "prs": [
    {"ref": "acme/portal#1", "number": 1, "title": "Fix ACME billing", "checks": {"failed": 1}, "green": False}]}]}
st0, _ = nn.diff({}, obs(prs={"repos": []}), cfgm, NOW)
_, m = nn.diff(st0, obs(prs=prs), cfgm, NOW + 1)
check(m == [], f"WORK minimal: no PR messages: {m}")
_, m = nn.diff(st0, obs(prs=prs), cfg, NOW + 1)
check(any("acme/portal#1" in c and "Fix ACME billing" in c for c in contents(m, cfg)), f"WORK full (default) includes PR: {m}")
cfgo = cfg_with(domains={"WORK": "off"})
st, _ = nn.diff({}, obs(), cfgo, NOW)
st, m = nn.diff(st, obs([sess("w1", "waiting", "WORK", "x", note="permission")]), cfgo, NOW + 100)
st, m = nn.diff(st, obs([sess("w1", "waiting", "WORK", "x", note="permission")]), cfgo, NOW + 200)
check(contents(m, cfgo) == [], "WORK off sends nothing")

cfg = cfg_with()
raw_llm = [{"name": "remote", "label": "GPU", "url": "http://10.1.2.3:11434", "mode": "agent", "up": False,
            "error": "[Errno -2] Name or service not known: gpu-box.lan"},
           {"name": "local", "label": "CPU", "url": "http://127.0.0.1:11434", "mode": "lite", "up": True, "error": None}]
o = nn.observe(cfg, NOW, {}, {"sessions": lambda: [], "llm": lambda: raw_llm, "limits": lambda: [], "dash": lambda: None})
check("url" not in json.dumps(o["llm"]) and "gpu-box" not in json.dumps(o) and "10.1.2.3" not in json.dumps(o), f"observe drops url/error: {o}")
st, _ = nn.diff({}, obs(), cfg, NOW)
st, m1 = nn.diff(st, o, cfg, NOW + 30)
st, m2 = nn.diff(st, o, cfg, NOW + 60)
allc = " ".join(contents(m1 + m2, cfg))
check("10.1.2.3" not in allc and "gpu-box" not in allc and "11434" not in allc, f"no host/IP/port: {allc}")
check(allc == "[llm] GPU is down [llm] no GPU endpoint is up; local agents use CPU" or
      sorted(contents(m1 + m2, cfg)) == sorted(["[llm] GPU is down", "[llm] no GPU endpoint is up; local agents use CPU"]),
      f"down after 2 polls + no-gpu: {allc}")
hostname = socket.gethostname().split(".")[0]
st, _ = nn.diff({}, obs(), cfg, NOW)
st, _ = nn.diff(st, obs([sess("s9", "waiting", "(workspace)", "", note="idle")]), cfg, NOW + 1)
_, m = nn.diff(st, obs([sess("s9", "waiting", "(workspace)", "", note="idle")]), cfg, NOW + 120)
c = contents(m, cfg)
check(c == ["[needs you] workspace: a session is waiting for your reply"] and (len(hostname) < 3 or hostname not in c[0]), f"workspace: {c}")

# 4. transitions
w = [sess("s1", "waiting", note="Claude needs your permission to use Bash")]
st, m = nn.diff({}, obs(w), cfg, NOW)
check(m == [], "first run only baselines")
_, m = nn.diff(st, obs(w), cfg, NOW + 100)
check(m == [], "baselined wait is not re-sent")
st, _ = nn.diff({}, obs(), cfg, NOW)
st, m = nn.diff(st, obs(w), cfg, NOW + 1)
check(m == [], "wait not sent before wait_delay_secs")
st2, m = nn.diff(st, obs(), cfg, NOW + 30)
check(m == [] and "wait:s1" not in st2["keys"], "wait that clears early sends nothing")
st, m = nn.diff(st, obs(w), cfg, NOW + 70)
check([x["tpl"] for x in m] == ["wait-permission"] and m[0]["route"] == "needs-you", f"wait after delay: {m}")
st, m = nn.diff(st, obs(w), cfg, NOW + 100)
check(m == [], "same observation twice -> one message")

ep = lambda up, name="remote", mode="agent": {"name": name, "label": "GPU", "mode": mode, "priority": 1, "up": up}  # noqa: E731
loc = {"name": "local", "label": "CPU", "mode": "lite", "priority": 2, "up": True}
st, _ = nn.diff({}, obs(llm=[ep(True), loc]), cfg, NOW)
st, m = nn.diff(st, obs(llm=[ep(False), loc]), cfg, NOW + 30)
check(m == [], "endpoint down 1 poll: nothing")
st, m = nn.diff(st, obs(llm=[ep(False), loc]), cfg, NOW + 60)
check("llm-down" in [x["tpl"] for x in m] and "llm-no-gpu" in [x["tpl"] for x in m], f"down 2 polls: {m}")
st, m = nn.diff(st, obs(llm=[ep(False), loc]), cfg, NOW + 90)
check(m == [], "no-gpu fires once")
st, m = nn.diff(st, obs(llm=[ep(True), loc]), cfg, NOW + 120)
check([x["tpl"] for x in m] == ["llm-up"], f"back up: {m}")
st, _ = nn.diff({}, obs(llm=[ep(False), loc]), cfg, NOW)
st, m = nn.diff(st, obs(llm=[ep(True), loc]), cfg, NOW + 30)
check(m == [], "no 'back up' when the down was only a baseline")
cfgs = cfg_with(endpoints={"remote": {"notify": "silent"}, "alf": {"notify": "off"}})
st, _ = nn.diff({}, obs(llm=[ep(True), ep(True, "alf"), loc]), cfgs, NOW)
for t in (30, 60):
    st, m = nn.diff(st, obs(llm=[ep(False), ep(False, "alf"), loc]), cfgs, NOW + t)
downs = [x for x in m if x["tpl"] == "llm-down"]
check(len(downs) == 1 and downs[0]["silent"], f"silent endpoint sent silent, off endpoint not at all: {m}")

cfg = cfg_with()
lim = lambda u, r=NOW + 3600: [{"key": "five_hour", "label": "5-hour", "used": u, "resets_at": r}]  # noqa: E731
st, _ = nn.diff({}, obs(limits=lim(10)), cfg, NOW)
st, m79 = nn.diff(st, obs(limits=lim(79)), cfg, NOW + 1)
st, m81 = nn.diff(st, obs(limits=lim(81)), cfg, NOW + 2)
st, m90 = nn.diff(st, obs(limits=lim(90)), cfg, NOW + 3)
st, m95 = nn.diff(st, obs(limits=lim(95)), cfg, NOW + 4)
st, mnew = nn.diff(st, obs(limits=lim(85, NOW + 20000)), cfg, NOW + 5)
check((len(m79), len(m81), len(m90), len(m95), len(mnew)) == (0, 1, 0, 1, 1), f"limit crossings {len(m79), len(m81), len(m90), len(m95), len(mnew)}")
check(contents(m81, cfg)[0].startswith("[limits] 5-hour usage at 81% (resets "), contents(m81, cfg))

st, _ = nn.diff({}, obs([sess("d1", "idle", since=NOW - 5000)]), cfg, NOW)
st, _ = nn.diff(st, obs([sess("d1", "working", since=NOW + 10)]), cfg, NOW + 20)
st, _ = nn.diff(st, obs([sess("d1", "waiting", since=NOW + 300)]), cfg, NOW + 310)
st, m = nn.diff(st, obs([sess("d1", "idle", since=NOW + 670)]), cfg, NOW + 680)
check(contents([x for x in m if x["tpl"] == "done"], cfg) == ["[done] PERSONAL/app: session finished after 11m00s"], f"done: {m}")
st, _ = nn.diff(st, obs([sess("d1", "working", since=NOW + 700)]), cfg, NOW + 700)
st, m = nn.diff(st, obs([sess("d1", "idle", since=NOW + 820)]), cfg, NOW + 830)
check([x for x in m if x["tpl"] == "done"] == [], "short run: no done")

st, _ = nn.diff({}, obs(dash=True), cfg, NOW)
st, m1 = nn.diff(st, obs(dash=False), cfg, NOW + 30)
st, m2 = nn.diff(st, obs(dash=False), cfg, NOW + 60)
st, m3 = nn.diff(st, obs(dash=True), cfg, NOW + 90)
check((m1, [x["tpl"] for x in m2], [x["tpl"] for x in m3]) == ([], ["dash-down"], ["dash-up"]), f"dash {m1} {m2} {m3}")
st, m = nn.diff(st, obs(errors=["github"]), cfg, NOW + 100)
st, m2 = nn.diff(st, obs(errors=["github"]), cfg, NOW + 200)
check(len(m) == 1 and m[0]["tpl"] == "source-error" and m2 == [], "source error once per hour")
o = obs(w)
o["sessions"] = None
st_keep, _ = nn.diff({"baselined": True, "keys": {"wait:s1": {"since": NOW, "polls": 3, "sent": True, "told": True}}}, o, cfg, NOW)
check("wait:s1" in st_keep["keys"], "unreadable source does not clear its keys")

# 5. rate limiting and HTTP errors
cfg = cfg_with(max_per_hour=5)
clock = [NOW]
fs = FakeSender()
d = nn.Dispatcher(fs, URL, cfg, clock=lambda: clock[0], sleep=lambda s: None, log=lambda m: None)
msgs = [{"key": f"r:{i}", "route": "activity", "tpl": "wait-reply", "f": {"domain": "HOBBY", "project": f"p{i}"}} for i in range(50)]
d.send(msgs)
check(len(fs.posts) == 5 and d.suppressed == 45, f"hourly cap: {len(fs.posts)} posts, {d.suppressed} suppressed")
clock[0] += 3601
d.send([])
check(len(fs.posts) == 6 and "45 notifications suppressed" in fs.posts[-1][1]["content"] and fs.posts[-1][0] == URL["alerts"],
      f"suppression summary: {fs.posts[-1]}")
check(fs.posts[0][1]["flags"] & nn.FLAG_SILENT, "silent route sends silently")

cfg = cfg_with()
fs = FakeSender([(429, 0.01), (204, None)])
d = nn.Dispatcher(fs, URL, cfg, clock=time.time, sleep=lambda s: None, log=lambda m: None)
d.send([{"key": "k", "route": "alerts", "tpl": "dash-down", "f": {}}])
check(len(fs.posts) == 2, f"429 then retry: {len(fs.posts)}")
logs = []
fs = FakeSender([(404, None)])
d = nn.Dispatcher(fs, URL, cfg, clock=time.time, sleep=lambda s: None, log=logs.append)
d.send([{"key": "k", "route": "llm", "tpl": "llm-down", "f": {"label": "GPU"}}])
check(len(fs.posts) == 2 and fs.posts[1][0] == URL["alerts"] and "route llm disabled" in fs.posts[1][1]["content"], f"404: {fs.posts}")
d.send([{"key": "k2", "route": "llm", "tpl": "llm-down", "f": {"label": "GPU"}}])
check(len(fs.posts) == 2, "disabled route not used again")
check(not any(TOKEN in l or "discord.com" in l for l in logs), f"logs carry no URL: {logs}")
fs = FakeSender([(500, None), (500, None), (500, None), (500, None)])
d = nn.Dispatcher(fs, URL, cfg, clock=time.time, sleep=lambda s: None, log=lambda m: None)
d.send([{"key": "k", "route": "alerts", "tpl": "dash-down", "f": {}}])
d.send([])
d.send([])
d.send([])
check(len(fs.posts) == 3, f"5xx retried on later polls, at most 3 tries: {len(fs.posts)}")
check(nn.webhook_urls(cfg, {"DISCORD_WEBHOOK_ALERTS": URL["alerts"]}) == {"alerts": URL["alerts"], "needs-you": URL["alerts"]},
      "needs-you falls back to alerts only")

# 6. secrets
bad = ["http://discord.com/api/webhooks/123456789012345678/" + TOKEN,
       "https://discord.com.evil.io/api/webhooks/123456789012345678/" + TOKEN,
       "https://evil.io/?u=discord.com/api/webhooks/123456789012345678/" + TOKEN,
       "https://discord.com/api/webhooks/123456789012345678/short",
       URL["alerts"] + "\n"]
check(all(nn.webhook_status(cfg, {"DISCORD_WEBHOOK_ALERTS": b})["alerts"] == "invalid" for b in bad), "invalid webhook URLs rejected")
check(nn.UrllibSender().post(bad[1], {}) == (0, None), "sender refuses a non-Discord URL")
for badcfg in ({"routes": {"alerts": URL["alerts"]}}, {"routes": {"alerts": "PATH"}}, {"domains": {"WORK": "maybe"}}):
    (tmp / "discord.json").write_text(json.dumps(badcfg))
    try:
        nn.load_config()
        check(False, f"config refused: {badcfg}")
    except nn.ConfigError as e:
        check(TOKEN not in str(e), "config error does not echo the URL")
check(nn.scrub(f"x {URL['ci']} y {URL['ci'].rsplit('/', 1)[1]} z", [URL["ci"]]) == "x [webhook] y [webhook] z", "scrub URL and token")

cfg_with()
penv = dict(os.environ, **ENV, NIU_NOTIFY_CONFIG=str(tmp / "discord.json"))
penv["HOME"] = str(tmp / "home")
r = subprocess.run([sys.executable, str(SH / "bin" / "niu-notify"), "check"], env=penv, capture_output=True, text=True, timeout=60)
check(r.returncode == 0 and sum(l.endswith(" ok") for l in r.stdout.splitlines()) == 6, f"check ok: {r.returncode} {r.stdout} {r.stderr}")
check(TOKEN not in r.stdout + r.stderr and "discord.com" not in r.stdout + r.stderr, "check prints no URL")
r = subprocess.run([sys.executable, str(SH / "bin" / "niu-notify"), "check"], env=dict(penv, DISCORD_WEBHOOK_CI="nope " + TOKEN),
                   capture_output=True, text=True, timeout=60)
check("invalid" in r.stdout and TOKEN not in r.stdout + r.stderr and r.returncode == 1, f"check invalid: {r.stdout}")

# `once` with a sender that fails with the URL in the exception: nothing leaks to output or state.
probe = tmp / "probe.py"
probe.write_text(f"""
import sys, urllib.error
sys.path.insert(0, {str(SH / 'lib')!r})
import niu_notify as nn
class Boom:
    def post(self, url, payload):
        try:
            raise urllib.error.URLError("cannot reach " + url)
        except urllib.error.URLError:
            return 0, None
def bad_sessions():
    raise RuntimeError("leak " + {URL['alerts']!r})
n = nn.Notifier(nn.load_config(), sender=Boom(), sources={{"sessions": bad_sessions, "llm": lambda: [], "limits": lambda: [],
                "dash": lambda: None, "prs": lambda: {{"repos": []}}}})
n.poll(); n.poll()
print("ok")
""")
r = subprocess.run([sys.executable, str(probe)], env=penv, capture_output=True, text=True, timeout=60)
stp = tmp / "state" / "notify-state.json"
check(r.stdout.strip() == "ok", f"probe ran: {r.stdout} {r.stderr}")
check(TOKEN not in r.stdout + r.stderr and stp.exists() and TOKEN not in stp.read_text() and "discord" not in stp.read_text(),
      "no URL in output or state file")
check(oct(stp.stat().st_mode & 0o777) == "0o600", f"state file 0600: {oct(stp.stat().st_mode)}")

# urllib error paths of the real sender (local server, no internet): redirect is not followed; 429 retry_after parsed.
hits = []


class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        hits.append(self.path)
        if self.path.startswith("/redir"):
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.end_headers()
        else:
            body = b'{"retry_after": 2.5}'
            self.send_response(429)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
try:
    nn._opener.open(urllib.request.Request(base + "/redir", data=b"{}", method="POST"), timeout=5)
    check(False, "redirect followed")
except urllib.error.HTTPError as e:
    check(e.code == 302 and hits == ["/redir"], f"redirect not followed: {hits}")
check(nn._retry_after({}, b'{"retry_after": 2.5}') == 2.5 and nn._retry_after({"Retry-After": "3"}, b"") == 3.0, "retry_after parsing")
srv.shutdown()

# 7. integration: events.db -> observe -> diff -> dispatch
from niu_events import connect  # noqa: E402
con = connect()
t0 = time.time()
for ts, ev, tool, s in ((t0 - 200, "SessionStart", None, ""), (t0 - 190, "UserPromptSubmit", None, ""),
                        (t0 - 180, "PreToolUse", "Bash", "ls"), (t0 - 120, "Notification", None, "Claude needs your permission to use Bash")):
    con.execute("INSERT INTO events(ts,event,session_id,domain,project,project_dir,tool,summary) VALUES(?,?,?,?,?,?,?,?)",
                (ts, ev, "int1", "HOBBY", "garden", "/x", tool, s))
con.commit()
cfg = cfg_with(wait_delay_secs=0)
stp.unlink()
fs = FakeSender()
clock = [t0]
n = nn.Notifier(cfg, sender=fs, env=ENV, sources={"llm": lambda: [], "limits": lambda: [], "dash": lambda: None,
                                                    "prs": lambda: {"repos": []}}, clock=lambda: clock[0])
n.dispatcher.sleep = lambda s: None
n.state = {"baselined": True}
n.poll()
cs = [(u, p["content"]) for u, p in fs.posts]
check((URL["alerts"], "[alerts] notifier started; 1 waiting, 0 unreviewed") in cs, f"started: {cs}")
check((URL["needs-you"], "[needs you] HOBBY/garden: a session needs your permission") in cs, f"integration needs-you: {cs}")
clock[0] += 30
n.poll()
check(len(fs.posts) == 2, "no repeat on next poll")

# 9. regressions from review
cfg = cfg_with()
# done must not count time the user spent replying after an idle-prompt wait
st, _ = nn.diff({}, obs([sess("r1", "idle", since=NOW - 50)]), cfg, NOW)
st, _ = nn.diff(st, obs([sess("r1", "working", since=NOW + 10)]), cfg, NOW + 20)
st, m = nn.diff(st, obs([sess("r1", "idle", since=NOW + 690)]), cfg, NOW + 700)
check([x["tpl"] for x in m] == ["done"], f"first run done: {m}")
st, _ = nn.diff(st, obs([sess("r1", "waiting", since=NOW + 750, note="idle")]), cfg, NOW + 760)
st, _ = nn.diff(st, obs([sess("r1", "working", since=NOW + 3990)]), cfg, NOW + 4000)
st, m = nn.diff(st, obs([sess("r1", "idle", since=NOW + 4090)]), cfg, NOW + 4100)
check([x for x in m if x["tpl"] == "done"] == [], f"reply wait not counted as work: {m}")
st, _ = nn.diff(st, obs([sess("r1", "ended", since=NOW + 5000)]), cfg, NOW + 5000)
# a second wait within the cooldown is deferred, not lost
pw = lambda: [sess("c1", "waiting", note="needs your permission")]  # noqa: E731
st, _ = nn.diff({}, obs(), cfg, NOW)
st, _ = nn.diff(st, obs(pw()), cfg, NOW + 1)
st, m = nn.diff(st, obs(pw()), cfg, NOW + 70)
check(len(m) == 1, "first wait sent")
st, _ = nn.diff(st, obs([sess("c1", "working")]), cfg, NOW + 100)
st, _ = nn.diff(st, obs(pw()), cfg, NOW + 110)
st, m = nn.diff(st, obs(pw()), cfg, NOW + 175)
check(m == [], "second wait inside wait cooldown held")
st, m = nn.diff(st, obs(pw()), cfg, NOW + 200)
check([x["tpl"] for x in m] == ["wait-permission"], f"second wait sent once the cooldown passes: {m}")
# generic cooldown expiry re-fires a key; error cooldown expires
st, _ = nn.diff({}, obs(), cfg, NOW)
st, _ = nn.diff(st, obs(errors=["llm"]), cfg, NOW + 1)
_, m = nn.diff(st, obs(errors=["llm"]), cfg, NOW + 3700)
check(len(m) == 1, "error re-reported after its cooldown")
# limits: resets_at jitter does not re-fire; straight to 96 sends one (95)
st, _ = nn.diff({}, obs(limits=lim(10)), cfg, NOW)
st, m1 = nn.diff(st, obs(limits=lim(85, NOW + 3600.4)), cfg, NOW + 1)
st, m2 = nn.diff(st, obs(limits=lim(86, NOW + 3599.7)), cfg, NOW + 2)
check(len(m1) == 1 and m2 == [], "resets_at jitter tolerated")
st, _ = nn.diff({}, obs(limits=lim(10)), cfg, NOW)
st, m = nn.diff(st, obs(limits=lim(96)), cfg, NOW + 1)
check(len(m) == 1 and "95" in m[0]["key"], f"jump to 96 -> one message: {m}")
# review track, PR kinds, PR domain off
st, _ = nn.diff({}, obs(), cfg, NOW)
_, m = nn.diff(st, obs([sess("v1", "idle", flagged=True)]), cfg, NOW + 1)
check(m == [], f"unreviewed edits send nothing (reviews run per PR): {m}")
prs2 = {"repos": [{"label": "HOBBY/site", "repo": "o/site", "prs": [
    {"ref": "o/site#2", "number": 2, "title": "a", "review": "CHANGES_REQUESTED", "checks": {"failed": 0}},
    {"ref": "o/site#3", "number": 3, "title": "b", "green": True, "checks": {"failed": 0}},
    {"ref": "o/site#4", "number": 4, "title": "c", "needs_scope": "workflow", "checks": {"failed": 0}}]}]}
st, _ = nn.diff({}, obs(prs={"repos": []}), cfg, NOW)
_, m = nn.diff(st, obs(prs=prs2), cfg, NOW + 1)
check(sorted(x["tpl"] for x in m) == ["pr-blocked", "pr-changes", "pr-ready"], f"PR kinds: {m}")
cfgh = cfg_with(domains={"HOBBY": "off"})
_, m = nn.diff(st, obs(prs=prs2), cfgh, NOW + 1)
check(m == [], "PRs of an off domain are dropped")
# llm-none and display override
cfgd = cfg_with(endpoints={"remote": {"display": "Big GPU"}})
st, _ = nn.diff({}, obs(), cfgd, NOW)
for t in (30, 60):
    st, m = nn.diff(st, obs(llm=[ep(False), dict(loc, up=False)]), cfgd, NOW + t)
cs = sorted(contents(m, cfgd))
check("[llm] Big GPU is down" in cs and "[llm] no GPU endpoint is up, and no local endpoint either" in cs, f"llm-none/display: {cs}")
check(nn.sanitize_label("gpu.tail1234.ts.net", "endpoint 1") == "endpoint 1" and nn.sanitize_label("192.168.1.20", "e") == "e",
      "labels that look like hostnames / IPs fall back")
# quiet hours
cfgq = cfg_with(quiet_hours={"start": "23:00", "end": "07:00", "except": ["needs-you"]})
night = time.mktime(time.strptime("2026-11-06 02:00", "%Y-%m-%d %H:%M"))
day = time.mktime(time.strptime("2026-11-06 12:00", "%Y-%m-%d %H:%M"))
check(nn.in_quiet_hours(cfgq["quiet_hours"], night) and not nn.in_quiet_hours(cfgq["quiet_hours"], day), "overnight window")
fs = FakeSender()
d = nn.Dispatcher(fs, URL, cfgq, clock=lambda: night, sleep=lambda s: None, log=lambda m: None)
d.send([{"key": "a", "route": "alerts", "tpl": "dash-down", "f": {}},
        {"key": "b", "route": "needs-you", "tpl": "wait-reply", "f": {"domain": "HOBBY", "project": "x"}}])
fl = {u: p["flags"] for u, p in fs.posts}
check(fl[URL["alerts"]] & nn.FLAG_SILENT and not fl[URL["needs-you"]] & nn.FLAG_SILENT, f"quiet hours silent except needs-you: {fl}")
# dispatcher: 429 twice -> backoff, summary not sent into the backoff; 401; status 0 retry; fallback shared URL disabled
cfg = cfg_with()
clock = [NOW]
fs = FakeSender([(429, 5), (429, 5)])
d = nn.Dispatcher(fs, URL, cfg, clock=lambda: clock[0], sleep=lambda s: None, log=lambda m: None)
d.send([{"key": "a", "route": "alerts", "tpl": "dash-down", "f": {}}])
check(len(fs.posts) == 2 and d.suppressed == 1, f"429 twice: dropped, no summary into backoff: {len(fs.posts)}")
clock[0] += 10
d.send([])
check(len(fs.posts) == 3 and "1 notifications suppressed" in fs.posts[-1][1]["content"], "summary after backoff")
fs = FakeSender([(401, None)])
d = nn.Dispatcher(fs, URL, cfg, clock=time.time, sleep=lambda s: None, log=lambda m: None)
d.send([{"key": "a", "route": "ci", "tpl": "pr-ready", "f": {"ref": "o/r#1", "title": "x"}}])
check(URL["ci"] in d.disabled and "route ci disabled" in fs.posts[-1][1]["content"], "401 disables")
fs = FakeSender([(0, None), (204, None)])
d = nn.Dispatcher(fs, URL, cfg, clock=time.time, sleep=lambda s: None, log=lambda m: None)
d.send([{"key": "a", "route": "alerts", "tpl": "dash-down", "f": {}}])
d.send([])
check(len(fs.posts) == 2 and d.pending == [], "network error retried next poll")
fb = nn.webhook_urls(cfg, {"DISCORD_WEBHOOK_ALERTS": URL["alerts"]})
logs = []
fs = FakeSender([(404, None)])
d = nn.Dispatcher(fs, fb, cfg, clock=time.time, sleep=lambda s: None, log=logs.append)
d.send([{"key": "a", "route": "needs-you", "tpl": "wait-reply", "f": {"domain": "HOBBY", "project": "x"}}])
check(logs and "needs-you" in logs[0] and "alerts" in logs[0], f"shared webhook disable names both routes: {logs}")
check(nn.webhook_status(cfg, {"DISCORD_WEBHOOK_ALERTS": URL["alerts"], "DISCORD_WEBHOOK_NEEDS_YOU": "bad"})["needs-you"] == "invalid",
      "invalid needs-you is reported, not hidden as fallback")
# config validation
for badcfg in ({"limit_thresholds": 80}, {"routes": "x"}, {"endpoints": {"a": {"down_after_polls": "a"}}},
               {"endpoints": {"a": {"notify": "loud"}}}, {"quiet_hours": {"start": "1", "end": "2"}},
               {"quiet_hours": {"start": "23:00", "end": "07:00", "except": "needs-you"}}):
    (tmp / "discord.json").write_text(json.dumps(badcfg))
    try:
        nn.load_config()
        check(False, f"bad config accepted: {badcfg}")
    except nn.ConfigError:
        check(True, "")
# webhook env removed before child processes run
cfg = cfg_with()
envd = dict(ENV, PATH="/bin", DISCORD_WEBHOOK_EXTRA="x")
nn.drop_webhook_env(cfg, envd)
check(envd == {"PATH": "/bin"}, f"webhook env dropped: {envd}")

# CLI: unit and once (no webhooks, no network: empty ollama config, temp HOME)
(tmp / "ollama-empty.json").write_text('{"endpoints": {}}')
cenv = {k: v for k, v in os.environ.items() if not k.startswith("DISCORD_WEBHOOK_")}
cenv.update(HOME=str(tmp / "home"), NIU_OLLAMA_CONFIG=str(tmp / "ollama-empty.json"))
r = subprocess.run([sys.executable, str(SH / "bin" / "niu-notify"), "unit"], env=cenv, capture_output=True, text=True, timeout=60)
check(r.returncode == 0 and f"ExecStart={SH}/bin/niu-notify run" in r.stdout, f"cli unit: {r.stdout} {r.stderr}")
r = subprocess.run([sys.executable, str(SH / "bin" / "niu-notify"), "once"], env=cenv, capture_output=True, text=True, timeout=60)
check(r.returncode == 0 and "no valid webhooks" in r.stderr, f"cli once without webhooks: {r.returncode} {r.stderr}")

# flap: a delayed "down" (held by cooldown) must still get its "up"
cfg = cfg_with()
st, _ = nn.diff({}, obs(llm=[ep(True), loc]), cfg, NOW)
seq = []
for t, up in ((30, False), (60, False), (90, True), (100, False), (120, False), (960, False), (970, True)):
    st, m = nn.diff(st, obs(llm=[ep(up), loc]), cfg, NOW + t)
    seq += [x["tpl"] for x in m if x["key"].startswith("llm:remote")]
check(seq == ["llm-down", "llm-up", "llm-down", "llm-up"], f"flap keeps down/up paired: {seq}")
st, _ = nn.diff({}, obs(dash=True), cfg, NOW)
seq = []
for t, up in ((30, False), (60, False), (90, True), (100, False), (130, False), (970, False), (980, True)):
    st, m = nn.diff(st, obs(dash=up), cfg, NOW + t)
    seq += [x["tpl"] for x in m]
check(seq == ["dash-down", "dash-up", "dash-down", "dash-up"], f"dash flap paired: {seq}")
# working -> idle-prompt wait without the idle state ever polled: run ends at the wait
st, _ = nn.diff({}, obs([sess("h1", "idle", since=NOW - 9)]), cfg, NOW)
st, _ = nn.diff(st, obs([sess("h1", "working", since=NOW + 5)]), cfg, NOW + 5)
st, m1 = nn.diff(st, obs([sess("h1", "waiting", since=NOW + 750, note="idle")]), cfg, NOW + 760)
st, _ = nn.diff(st, obs([sess("h1", "working", since=NOW + 3990)]), cfg, NOW + 3990)
st, m2 = nn.diff(st, obs([sess("h1", "idle", since=NOW + 4090)]), cfg, NOW + 4100)
d1 = [x for x in m1 if x["tpl"] == "done"]
check(len(d1) == 1 and d1[0]["f"]["secs"] == 745 and [x for x in m2 if x["tpl"] == "done"] == [], f"idle-prompt ends run: {m1} {m2}")

# 8. unit text and install wiring (static: running install would touch systemd)
u = nn.unit_text("/opt/ws/.claude-workspace")
check("ExecStart=/opt/ws/.claude-workspace/bin/niu-notify run" in u and "EnvironmentFile=-/opt/ws/.claude-workspace/.env" in u
      and "NoNewPrivileges=true" in u and "RestrictAddressFamilies=" in u and "DISCORD_WEBHOOK_" not in u, f"unit: {u}")
inst = (SH / "bin" / "install").read_text()
blk = inst.split("if $notify; then", 1)
check(len(blk) == 2 and "notify=false" in inst and "--notify) notify=true" in inst, "install has an opt-in --notify flag")
check("niu-notify.service" not in blk[0], "install touches niu-notify.service only with --notify")
check("cat " not in blk[1].split("\nfi\n")[0] and "source " not in blk[1].split("\nfi\n")[0], "install never reads the secrets file")
tpl = json.loads((SH / "discord.template.json").read_text())
check(all(nn.RE_ENVNAME.fullmatch(v) for v in tpl["routes"].values()) and "https://" not in json.dumps(tpl), "template has no URLs")
ex = (SH / ".env.example").read_text()
check(all(f"{v}=\n" in ex for v in tpl["routes"].values()), ".env.example lists every route variable, empty")

subprocess.run(["rm", "-rf", str(tmp)])
print(f"notify: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
