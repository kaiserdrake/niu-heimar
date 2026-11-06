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
statusline(sample)
lim = claude_limits()
w = {x["key"]: x for x in lim["windows"]}
check(lim["available"] and not lim["stale"], "limits available and fresh")
check(w["five_hour"]["used"] == 23.4 and w["five_hour"]["label"] == "5-hour", "5-hour window")
check(w["seven_day"]["label"] == "Weekly" and w["seven_day"]["resets_at"] > now, "weekly window with ISO reset time")
check(claude_limits(now=now + 20 * 60)["stale"], "older than 15 min -> stale")
check({x["key"]: x for x in claude_limits(now=now + 7200)["windows"]}["five_hour"]["used"] == 0.0, "5-hour window rolled over")
check(claude_limits.__module__ and not claude_limits(now=now)["stale"], "fresh again at report time")

# 3b. opt-in usage API source (for the desktop app, which never runs the status line)
import niu_limits  # noqa: E402
api = {"five_hour": {"used_percentage": 12.0, "resets_at": "2099-01-01T00:00:00Z"}}
calls = []
fake = lambda: (calls.append(1), api)[1]  # noqa: E731
(tmp / "state" / "claude-limits.json").write_text(json.dumps({"rate_limits": sample["rate_limits"], "updated": now - 3600}))
os.environ.pop("NIU_CLAUDE_USAGE_API", None)
check(claude_limits(now=now, fetch=fake)["source"] == "statusline" and not calls, "API source is off by default")
os.environ["NIU_CLAUDE_USAGE_API"] = "1"
lim = claude_limits(now=now, fetch=fake)
check(lim["source"] == "api" and not lim["stale"] and lim["windows"][0]["used"] == 12.0, f"stale status line -> API numbers: {lim}")
claude_limits(now=now + 60, fetch=fake)
check(len(calls) == 1, "API is polled at most every 5 minutes")
(tmp / "state" / "claude-limits.json").write_text(json.dumps({"rate_limits": sample["rate_limits"], "updated": now + 100}))
check(claude_limits(now=now + 120, fetch=fake)["source"] == "statusline", "fresh status line wins over the API")
creds = tmp / "creds.json"
creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-secret"}}))
os.environ["NIU_CLAUDE_CREDENTIALS"] = str(creds)
check(niu_limits._fetch_usage(url="http://127.0.0.1:1/x", timeout=1) is None, "unreachable API -> None, no exception")
creds.write_text("garbage")
check(niu_limits._fetch_usage(url="http://127.0.0.1:1/x", timeout=1) is None, "bad credentials file -> None")
(tmp / "state" / "claude-limits.json").write_text(json.dumps({"rate_limits": sample["rate_limits"], "updated": "bad"}))
niu_limits._api.update(at=0.0, rate_limits=None)
check(claude_limits(now=now + 9999, fetch=lambda: None)["source"] == "statusline", "corrupt 'updated' does not crash; failed fetch keeps status line")
os.environ.pop("NIU_CLAUDE_USAGE_API"); os.environ.pop("NIU_CLAUDE_CREDENTIALS")

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
