#!/usr/bin/env python3
"""Tests for Claude plan-limit reporting: bin/niu-statusline, lib/niu_limits.py, and the install merge of statusLine."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
SH = WS / ".claude-workspace"
tmp = Path(tempfile.mkdtemp())
env = dict(os.environ, NIU_EVENTS_DB=str(tmp / "state" / "events.db"))
sys.path.insert(0, str(SH / "lib"))
os.environ["NIU_EVENTS_DB"] = env["NIU_EVENTS_DB"]
os.environ["NIU_CLAUDE_CREDENTIALS"] = "/nonexistent/creds.json"  # tests never touch the real login or network
passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


def statusline(payload):
    r = subprocess.run([str(SH / "bin" / "niu-statusline")], input=payload if isinstance(payload, str) else json.dumps(payload),
                       text=True, capture_output=True, env=env, timeout=20)
    return r.returncode, r.stdout.strip()


saved = tmp / "state" / "claude-limits.json"
now = time.time()
sample = {"session_id": "abc", "cwd": "/secret/path", "transcript_path": "/x.jsonl", "model": {"display_name": "Opus 5.5"},
          "rate_limits": {"five_hour": {"used_percentage": 23.4, "resets_at": now + 3600},
                          "seven_day": {"used_percentage": 41, "resets_at": "2099-01-05T09:00:00Z"}}}

# 1. status line output + what gets saved
rc, out = statusline(sample)
check(rc == 0 and out == "Opus 5.5 · 5h 23% · 7d 41%", f"status line text: {out!r}")
data = json.loads(saved.read_text())
check(set(data["rate_limits"]) == {"five_hour", "seven_day"}, "only the limit windows are saved")
check("abc" not in saved.read_text() and "/secret/path" not in saved.read_text(), "no session id, path or transcript saved")
check(oct(saved.stat().st_mode)[-3:] == "600", "limits file mode 600")

# 2. no limits (e.g. API-key auth) -> model only, file untouched; bad input never fails
saved.unlink()
rc, out = statusline({"model": {"display_name": "Sonnet 5.5"}, "rate_limits": None})
check(rc == 0 and out == "Sonnet 5.5" and not saved.exists(), f"no limits: {out!r}")
rc, out = statusline("not json")
check(rc == 0, "malformed input does not fail")

# 3. reader: windows, labels, reset parsing, staleness and rollover
from niu_limits import claude_limits  # noqa: E402
os.environ["NIU_CLAUDE_USAGE_API"] = "0"  # section 3 tests the status-line source only
statusline(sample)
lim = claude_limits()
w = {x["key"]: x for x in lim["windows"]}
check(lim["available"] and not lim["stale"], "limits available and fresh")
check(w["five_hour"]["used"] == 23.4 and w["five_hour"]["label"] == "5-hour", "5-hour window")
check(w["seven_day"]["label"] == "Weekly" and w["seven_day"]["resets_at"] > now, "weekly window with ISO reset time")
check(claude_limits(now=now + 20 * 60)["stale"], "older than 15 min -> stale")
check({x["key"]: x for x in claude_limits(now=now + 7200)["windows"]}["five_hour"]["used"] == 0.0, "5-hour window rolled over")
check(claude_limits.__module__ and not claude_limits(now=now)["stale"], "fresh again at report time")

# 3b. usage API source, on by default (for the desktop app, which never runs the status line)
import niu_limits  # noqa: E402
api = {"five_hour": {"used_percentage": 12.0, "resets_at": "2099-01-01T00:00:00Z"}}
calls = []
fake = lambda: (calls.append(1), api)[1]  # noqa: E731
(tmp / "state" / "claude-limits.json").write_text(json.dumps({"rate_limits": sample["rate_limits"], "updated": now - 3600}))
os.environ["NIU_CLAUDE_USAGE_API"] = "0"
check(claude_limits(now=now, fetch=fake)["source"] == "statusline" and not calls, "NIU_CLAUDE_USAGE_API=0 turns the API source off")
os.environ.pop("NIU_CLAUDE_USAGE_API")
niu_limits._api.update(at=0.0, rate_limits=None)
lim = claude_limits(now=now, fetch=fake)
check(lim["source"] == "api" and not lim["stale"] and lim["windows"][0]["used"] == 12.0, f"stale status line -> API numbers: {lim}")
claude_limits(now=now + 60, fetch=fake)
check(len(calls) == 1, "API is polled at most every 5 minutes")
(tmp / "state" / "claude-limits.json").write_text(json.dumps({"rate_limits": sample["rate_limits"], "updated": now + 100}))
check(claude_limits(now=now + 120, fetch=fake)["source"] == "statusline", "fresh status line wins over the API")
# the 2-minute preference boundary
(tmp / "state" / "claude-limits.json").write_text(json.dumps({"rate_limits": sample["rate_limits"], "updated": now}))
niu_limits._api.update(at=0.0, rate_limits=None); calls.clear()
check(claude_limits(now=now + 100, fetch=fake)["source"] == "statusline" and not calls, "status line under 2 min old: API not called")
niu_limits._api.update(at=0.0, rate_limits=None)
lim = claude_limits(now=now + 130, fetch=fake)
check(lim["source"] == "api" and len(calls) == 1, f"status line over 2 min old (not yet stale): API wins: {lim}")
creds = tmp / "creds.json"
creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-secret"}}))
os.environ["NIU_CLAUDE_CREDENTIALS"] = str(creds)
check(niu_limits._fetch_usage(url="http://127.0.0.1:1/x", timeout=1) is None, "unreachable API -> None, no exception")
check(niu_limits.api_error() == "unreachable", f"unreachable API is reported: {niu_limits.api_error()}")
creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-secret", "expiresAt": int((time.time() - 60) * 1000)}}))
seen = []
class _Spy(niu_limits.urllib.request.OpenerDirector):
    def open(self, *a, **k):
        seen.append(a); raise OSError("must not be called")
real_opener, niu_limits._opener = niu_limits._opener, _Spy()
check(niu_limits._fetch_usage() is None and niu_limits.api_error() == "expired" and not seen, "expired token: reported, never sent")
creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-secret", "expiresAt": int((time.time() + 3600) * 1000)}}))
niu_limits._fetch_usage()
check(len(seen) == 1 and niu_limits.api_error() == "unreachable", "unexpired token is used")
class _Deny(niu_limits.urllib.request.OpenerDirector):
    def open(self, req, *a, **k):
        raise niu_limits.urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
niu_limits._opener = _Deny()
check(niu_limits._fetch_usage() is None and niu_limits.api_error() == "rejected", "HTTP 401 -> rejected")
niu_limits._opener = real_opener
niu_limits._api.update(at=0.0, error="expired")
claude_limits(now=now + 5000, fetch=fake)
check(niu_limits.api_error() is None, "a successful fetch clears the error")
# automatic renewal through `claude auth status`
def expired_creds():
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-secret", "expiresAt": int((time.time() - 60) * 1000)}}))
fake_bin = tmp / "bin" / "claude"
fake_bin.parent.mkdir(exist_ok=True)
fake_bin.write_text("#!/bin/sh\n"); fake_bin.chmod(0o755)
real_which, niu_limits.shutil.which = niu_limits.shutil.which, lambda name: str(fake_bin)
ran = []
def renews(cmd, **kw):
    ran.append((cmd, kw))
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-new", "expiresAt": int((time.time() + 3600) * 1000)}}))
expired_creds(); niu_limits._api.update(at=123.0, error="expired"); niu_limits._renew.update(at=0.0, running=False, result=None)
niu_limits._maybe_renew(now, run=renews, wait=True)
check(ran and ran[0][0] == [str(fake_bin), "auth", "status"] and ran[0][1].get("timeout") == niu_limits.RENEW_TIMEOUT_SECS,
      f"expired token -> runs claude auth status with a timeout: {ran}")
check(niu_limits._renew["result"] == "renewed" and niu_limits._api["at"] == 0.0, "renewed token -> API refetched on next read")
niu_limits._maybe_renew(now + 60, run=renews, wait=True)
check(len(ran) == 1, "renewal is tried at most once an hour")
expired_creds(); niu_limits._renew.update(at=0.0)
niu_limits._maybe_renew(now, run=lambda cmd, **kw: None, wait=True)
check(niu_limits._renew["result"] == "failed" and niu_limits.api_error() == "renew_failed", "token still expired after the CLI -> renew_failed")
def boom(cmd, **kw):
    raise niu_limits.subprocess.TimeoutExpired(cmd, 30)
niu_limits._renew.update(at=0.0)
niu_limits._maybe_renew(now, run=boom, wait=True)
check(niu_limits._renew["result"] == "failed" and not niu_limits._renew["running"], "CLI timeout -> failed, never raises")
niu_limits.shutil.which = lambda name: None
real_home = niu_limits.Path.home
niu_limits.Path.home = staticmethod(lambda: tmp / "nohome")
niu_limits._renew.update(at=0.0)
niu_limits._maybe_renew(now, run=renews, wait=True)
check(niu_limits._renew["result"] == "no_cli" and len(ran) == 1, "no claude CLI found -> no_cli")
niu_limits.Path.home = real_home
niu_limits.shutil.which = lambda name: str(fake_bin)
os.environ["NIU_CLAUDE_AUTO_RENEW"] = "0"
niu_limits._renew.update(at=0.0, result=None)
niu_limits._maybe_renew(now, run=renews, wait=True)
check(len(ran) == 1, "NIU_CLAUDE_AUTO_RENEW=0 turns renewal off")
os.environ.pop("NIU_CLAUDE_AUTO_RENEW")
niu_limits.shutil.which = real_which
niu_limits._renew.update(at=0.0, running=False, result=None); niu_limits._api.update(error=None)
os.environ["NIU_CLAUDE_USAGE_API"] = "0"
check(niu_limits.api_error() == "off", "NIU_CLAUDE_USAGE_API=0 -> off")
os.environ.pop("NIU_CLAUDE_USAGE_API")
started = []
real_maybe, niu_limits._maybe_renew = niu_limits._maybe_renew, lambda now, **kw: started.append(now)
niu_limits._api.update(at=0.0, error="expired")
niu_limits._api_limits(now, fetch=lambda: None)
check(started == [now], "failed fetch with an expired token starts a renewal")
niu_limits._api.update(at=0.0, error="unreachable"); started.clear()
niu_limits._api_limits(now, fetch=lambda: None)
check(not started, "other failures don't start a renewal")
niu_limits._maybe_renew = real_maybe
creds.write_text("garbage")
check(niu_limits._fetch_usage(url="http://127.0.0.1:1/x", timeout=1) is None, "bad credentials file -> None")
check(niu_limits.api_error() == "no_login", "bad credentials file -> no_login")
(tmp / "state" / "claude-limits.json").write_text(json.dumps({"rate_limits": sample["rate_limits"], "updated": "bad"}))
niu_limits._api.update(at=0.0, rate_limits=None)
check(claude_limits(now=now + 9999, fetch=lambda: None)["source"] == "statusline", "corrupt 'updated' does not crash; failed fetch keeps status line")
os.environ["NIU_CLAUDE_USAGE_API"] = "0"; os.environ["NIU_CLAUDE_CREDENTIALS"] = "/nonexistent/creds.json"

# 3b. latest_reading(): the raw values behind claude_limits (used for the usage history)
from niu_limits import latest_reading  # noqa: E402
saved.write_text(json.dumps({"updated": now - 10, "rate_limits": {"seven_day": {"used_percentage": 41.26, "resets_at": now - 5},
                                                                    "five_hour": {"used_percentage": "junk"}}}))
niu_limits._api.update(at=0.0, rate_limits=None)
lr = latest_reading(now=now)
check(lr == (now - 10, "statusline", {"seven_day": {"used": 41.26, "resets_at": now - 5}}), f"latest_reading raw values, junk windows dropped: {lr}")
check(claude_limits(now=now)["windows"][0]["used"] == 0.0, "claude_limits still shows a rolled-over window as 0")
saved.unlink()
check(latest_reading(now=now) is None, "latest_reading: nothing reported -> None")
check(claude_limits(now=now) == {"available": False, "updated": 0, "source": "statusline", "stale": True, "windows": []}, "claude_limits output with no source is unchanged")

# 4. install: sets our status line, never replaces a user's own
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
settings = home / ".claude" / "settings.json"
settings.write_text("{}")
ienv = dict(os.environ, HOME=str(home), NIU_EVENTS_DB=str(tmp / "istate" / "events.db"))
subprocess.run([str(SH / "bin" / "install"), "--no-shell"], env=ienv, capture_output=True, text=True, timeout=120)
cur = json.loads(settings.read_text())
check(str(cur.get("statusLine", {}).get("command", "")).endswith("/bin/niu-statusline"), f"install sets status line: {cur.get('statusLine')}")
cur["statusLine"] = {"type": "command", "command": "my-own-statusline.sh"}
settings.write_text(json.dumps(cur))
r = subprocess.run([str(SH / "bin" / "install"), "--no-shell"], env=ienv, capture_output=True, text=True, timeout=120)
check(json.loads(settings.read_text())["statusLine"]["command"] == "my-own-statusline.sh", "user's own status line kept")
check("own statusLine" in r.stdout, "install says how to feed limits from a custom status line")

subprocess.run(["rm", "-rf", str(tmp)])
print(f"limits: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
