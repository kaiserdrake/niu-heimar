#!/usr/bin/env python3
"""Tests for the Weekly usage history: lib/niu_usage.py (recorder, weeks, history), the niu_limits.latest_reading() source,
and GET /api/usage/history in bin/niu-dash. Temp stores only; never the real login, network or ~/.claude."""
import http.client
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
SH = WS / ".claude-workspace"
FX = SH / "tests" / "fixtures" / "usage"
tmp = Path(tempfile.mkdtemp())
state = tmp / "state"
os.environ.update(NIU_EVENTS_DB=str(state / "events.db"), NIU_USAGE_DB=str(state / "sub" / "usage.db"), NIU_CLAUDE_USAGE_API="0",
                  NIU_CLAUDE_CREDENTIALS="/nonexistent/creds.json", TZ="Asia/Tokyo",
                  NIU_USAGE_TOKENS="0", CLAUDE_CONFIG_DIR=str(tmp / "no-claude"))   # the transcript scanner has its own suite (tokens_test.py)
os.environ.pop("NIU_USAGE_COLLECT", None)
sys.path.insert(0, str(SH / "lib"))
import niu_limits  # noqa: E402
import niu_usage as U  # noqa: E402

passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


def fresh(name):
    """A new empty usage.db path for one scenario."""
    os.environ["NIU_USAGE_DB"] = str(tmp / name / "usage.db")
    return U.db_path()


def rd(updated, used, resets=1_800_000_000, source="api", **extra):
    w = {"seven_day": {"used": used, "resets_at": resets}}
    w.update(extra)
    return (updated, source, w)


def rows(key="seven_day"):
    c = U.connect(readonly=True)
    try:
        return c.execute("SELECT ts, used, resets_at, source FROM limit_samples WHERE key=? ORDER BY ts", (key,)).fetchall()
    finally:
        c.close()


T0 = 1_762_560_000.0

# 1. file and directory permissions; the schema
p = fresh("perm")
check(U.record(rd(T0, 10), now=T0 + 5) == 1, "first reading stored")
check(stat.S_IMODE(p.stat().st_mode) == 0o600, f"usage.db mode 600: {oct(p.stat().st_mode)}")
check(stat.S_IMODE(p.parent.stat().st_mode) == 0o700, "usage dir mode 700")
con = U.connect(readonly=True)
check(con.execute("PRAGMA user_version").fetchone()[0] == 3, "user_version 3 (limit samples and token tables)")
idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
check("limit_samples_ts" in idx, "ts index exists")
try:
    con.execute("INSERT INTO limit_samples VALUES('seven_day',1,5,NULL,'api')")
    check(False, "read-only connection must not write")
except sqlite3.Error:
    check(True, "read-only connection cannot write")
con.close()
check(U.db_path().name == "usage.db", "usage.db name")
os.environ.pop("NIU_USAGE_DB")
check(U.db_path() == state / "usage.db", "default path sits next to events.db")
os.environ["NIU_USAGE_DB"] = str(p)

# 2. sampler rules
fresh("rec")
now = T0
U.record(rd(now, 10.0, 1_800_000_000), now=now)
check(U.record(rd(now + 60, 10.0, 1_800_000_000), now=now + 60) == 0, "unchanged value inserts nothing")
check(U.record(rd(now + 300, 10.05, 1_800_000_000), now=now + 300) == 0, "change under 0.1 inserts nothing")
check(U.record(rd(now + 400, 10.1, 1_800_000_000), now=now + 400) == 1, "change of 0.1 inserts")
check(U.record(rd(now + 400, 55.0, 1_800_000_000), now=now + 450) == 0, "a repeated 'updated' is never stored twice")
check(U.record(rd(now + 300, 77.0, 1_800_000_000), now=now + 450) == 0, "an older 'updated' is not stored")
check(U.record(rd(now + 400 + 599, 10.1, 1_800_000_000), now=now + 400 + 599) == 0, "no heartbeat before 10 min")
check(U.record(rd(now + 400 + 600, 10.1, 1_800_000_000), now=now + 400 + 600) == 1, "heartbeat after 10 min")
check([r["ts"] for r in rows()] == [now, now + 400, now + 1000], f"ts is the reading's own updated time: {[r['ts'] for r in rows()]}")
check(U.record(rd(now + 2000, 30.0), now=now + 2000 + 901) == 0, "a reading older than 15 min is not stored")
check(U.record(rd(now + 5000, 30.0), now=now + 4000) == 0, "a reading from the future is not stored")
# reset jitter and a new window
base = now + 3000
U.record(rd(base, 20.0, 1_800_000_030), now=base)
check(U.record(rd(base + 60, 20.0, 1_800_000_070), now=base + 60) == 0, "resets_at jitter of 40 s is the same window")
check(rows()[-1]["resets_at"] % 60 == 0, "resets_at rounded to 60 s")
check(U.record(rd(base + 120, 20.0, 1_800_000_030 + 7 * 86400), now=base + 120) == 1, "resets_at +7 d is a new window")
# clamping and junk
U.record(rd(base + 200, 150.0, None), now=base + 200)
check(rows()[-1]["used"] == 100.0 and rows()[-1]["resets_at"] is None, "values clamp to 0-100, null reset kept")
U.record(rd(base + 260, -4.0, None), now=base + 260)
check(rows()[-1]["used"] == 0.0, "negative clamps to 0")
bad = [None, (), ("x", "api", {}), (base + 300, "bogus", {"seven_day": {"used": 5}}), (base + 300, "api", {"seven_day": {"used": float("nan")}}),
       (base + 300, "api", {"seven_day": {"used": "7"}}), (base + 300, "api", {"nope": {"used": 5}}), (float("inf"), "api", {})]
check(all(U.record(b, now=base + 300) == 0 for b in bad), "malformed readings are ignored")
check(U.record((base + 400, "statusline", {"five_hour": {"used": 3, "resets_at": 1}, "seven_day_opus": {"used": 4, "resets_at": None}}), now=base + 400) == 2,
      "other keys are stored too")
check(rows("five_hour")[0]["source"] == "statusline", "source stored")

# 3. weeks()
D = 86400
r1, r2 = 1_000_000 + 7 * D, 1_000_000 + 14 * D
s = [(r1 - 7 * D + i * 600, i / 10, r1 + (i % 3) * 20) for i in range(0, 800)]   # reset jitter inside one window
s += [(r2 - 7 * D + i * 600, 5 + i / 20, r2) for i in range(0, 800)]
wk = U.weeks(s, now=r2 + 100)
check(len(wk) == 2, f"two finished weeks: {wk}")
check(wk[0]["ended_pct"] == round(max(x[1] for x in s[:800]), 1) and wk[1]["ended_pct"] == round(max(x[1] for x in s[800:]), 1), "ended_pct is the max before the reset")
check(wk[0]["partial"] is True, "samples stopped about 1.5 days before the reset: partial")
check(len(U.weeks(s, now=r2 - 10)) == 1 and abs(U.weeks(s, now=r2 - 10)[0]["resets_at"] - r1) < 60, "unfinished week is not listed")
late = [(r1 - 8 * 3600, 67.0, r1), (r1 - 9 * 3600, 60.0, r1)]
w = U.weeks(late, now=r1 + 1)
check(w[0]["partial"] is True and w[0]["ended_pct"] == 67.0, "last sample 8 h before the reset is partial")
check(U.weeks([(r1 - 6 * 3600, 50.0, r1)], now=r1 + 1)[0]["partial"] is False, "last sample 6 h before is not partial")
check(U.weeks([(1, 5.0, None)], now=10**9) == [], "samples without a reset time are not grouped")
check(U.weeks([], now=1) == [], "no samples, no weeks")

# 4. history(): states, gaps, reset boundaries, clamping, downsampling
fresh("hist")
check(U.history(35, now=T0)["state"] == "empty", "missing db gives empty")
check(U.history("abc")["days"] == 35 and U.history(9999)["days"] == 70 and U.history(1)["days"] == 7 and U.history("21")["days"] == 21, "days clamping")
check(U.clamp_days(None) == 35 and U.clamp_days("") == 35 and U.clamp_days("-5") == 7 and U.clamp_days("1e3") == 35, "days parsing")
now = T0 + 20 * D
reset_a = now - 8 * D
reset_b = now + 2 * D
c = U.connect()
ins = "INSERT INTO limit_samples VALUES(?,?,?,?,?)"
t = now - 12 * D
n = 0
while t < now - 8 * D - 3600:                       # week A: ends 83%, sampling stops 1 h before the reset
    c.execute(ins, ("seven_day", t, min(83.0, 5 + n * 0.2), reset_a, "api")); t += 600; n += 1
t = now - 8 * D + 600
while t < now - 3 * D:                              # first part of week B
    c.execute(ins, ("seven_day", t, 10 + (t - (now - 8 * D)) / D, reset_b, "api")); t += 600
t += 40 * 60                                        # a 40 minute hole
while t <= now - 60:
    c.execute(ins, ("seven_day", t, 30.0, reset_b, "api")); t += 600
c.execute(ins, ("seven_day_opus", now - 60, 12.0, reset_b, "api"))
c.execute("INSERT INTO meta VALUES('collecting_since', ?)", (str(int(now - 12 * D)),))
c.commit()
c.close()
h = U.history(35, now=now)
check(h["state"] == "ok" and h["gap_secs"] == 1500, "history ok")
pts = h["series"]["seven_day"]["points"]
check(None in pts and pts.count(None) >= 2, f"gaps are null: {pts.count(None)}")   # the reset-day hole and the 40 min hole
hole = [i for i, p in enumerate(pts) if p is None]
check(all(pts[i - 1] is not None and pts[i + 1] is not None for i in hole), "no consecutive nulls")
check(all(pts[i + 1][0] - pts[i - 1][0] > 1500 for i in hole), "every null sits in a real gap of more than 25 min")
check(h["series"]["five_hour"] is None and h["series"]["seven_day_sonnet"] is None, "unreported keys are null")
check(h["series"]["seven_day_opus"]["points"] == [[int(now - 60), 12.0]], "opus series")
check(len(h["weeks"]) == 1 and h["weeks"][0]["resets_at"] == int(reset_a) and h["weeks"][0]["ended_pct"] == 83.0, f"week ended: {h['weeks']}")
check(h["weeks"][0]["partial"] is False, "sampling 1 h before the reset is not partial")
check(h["resets"] == [int(reset_a)], "reset boundary listed")
check(h["current"]["used"] == 30.0 and h["current"]["resets_at"] == int(reset_b) and h["current"]["stale"] is False, f"current: {h['current']}")
check(h["collecting_since"] == int(now - 12 * D), "collecting_since from meta")
check(h["recorder"]["state"] in ("stopped", "running") and h["recorder"]["last_error"] in ("none", "reading", "database", "other"), "recorder enum")
check(U.history(35, now=now + 3600)["current"]["stale"] is True, "current is stale after 30 min without readings")
import niu_limits  # noqa: E402
niu_limits._api["error"] = "expired"
cur_stale = U.history(35, now=now + 3600)["current"]
check(cur_stale.get("api_error") in ("expired", "off"), f"stale current carries the usage API error: {cur_stale}")
check("api_error" not in U.history(35, now=now)["current"], "fresh current has no api_error")
niu_limits._api["error"] = None
# the same data with a last sample 8 h before the reset reads as partial
c = U.connect()
c.execute("DELETE FROM limit_samples WHERE key='seven_day' AND ts > ?", (reset_a - 3600 * 8,))
c.commit()
c.close()
hw = U.history(35, now=now)
check(hw["weeks"][0]["partial"] is True, "week with samples ending 8 h before the reset is partial")
# downsampling keeps the peak
fresh("big")
c = U.connect()
start = T0 - 30 * D
c.executemany(ins, [("seven_day", start + i * 250, 50.0 if i != 7777 else 99.0, T0 + D, "api") for i in range(10000)])
c.commit()
c.close()
hb = U.history(35, now=T0)
bp = hb["series"]["seven_day"]["points"]
real = [p for p in bp if p is not None]
check(len(real) <= 900, f"downsampled to at most 900 points: {len(real)}")
check(max(p[1] for p in real) == 99.0, "the peak survives downsampling")
check(len(json.dumps(hb)) < 60_000, "payload stays small")
# retention prune
c = U.connect()
c.execute(ins, ("seven_day", T0 - 71 * D, 5.0, None, "api"))
c.execute(ins, ("seven_day", T0 - 69 * D, 5.0, None, "api"))
n = U.prune(c, now=T0)
check(n == 1 and c.execute("SELECT count(*) FROM limit_samples WHERE ts < ?", (T0 - 70 * D,)).fetchone()[0] == 0, "rows older than 70 days are pruned")
check(c.execute("SELECT count(*) FROM limit_samples WHERE ts = ?", (T0 - 69 * D,)).fetchone()[0] == 1, "newer rows kept")
c.close()

# 5. corrupt and missing db
p = fresh("corrupt")
p.parent.mkdir(parents=True)
p.write_bytes(b"this is not a sqlite database" * 50)
check(U.history(35, now=T0)["state"] == "error", "corrupt db gives error state (fixed message, no exception text)")
check("not a database" not in json.dumps(U.history(35, now=T0)), "no exception text in payload")
before = U.recorder_status()["errors"]
rec = U.Recorder(reading=lambda now: rd(now, 5.0), clock=lambda: T0)
check(rec.tick() == 0 and U.recorder_status()["errors"] == before + 1 and U.recorder_status()["last_error"] == "database", "corrupt db: error counted, tick does not raise")
fresh("nodb")
check(U.history(35, now=T0)["state"] == "empty", "no db gives empty")
os.environ["NIU_USAGE_COLLECT"] = "0"
check(U.history(35, now=T0)["state"] == "disabled" and U.start_background() is None, "NIU_USAGE_COLLECT=0 disables history and the thread")
del os.environ["NIU_USAGE_COLLECT"]


# 6. recorder thread with an injected clock and stop event (no real sleeping)
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

    def set(self):
        self.left = 0


fresh("thread")
clk = {"t": T0}
seq = iter([rd(T0, 10.0), rd(T0, 10.0), rd(T0 + 600, 10.0), "boom", rd(T0 + 1200, 40.0)])


def source(now):
    v = next(seq)
    if v == "boom":
        raise RuntimeError("secret-looking failure text sk-ant-xyz")
    return v


stop = FakeStop(clk, 5)
errs0 = U.recorder_status()["errors"]
r = U.Recorder(interval=300, reading=source, clock=lambda: clk["t"], stop=stop)
check(r.daemon is True and r.name == "niu-usage-recorder", "daemon thread")
r.run()
check(stop.waits == [300] * 5, f"one attempt per 5 minutes: {stop.waits}")
check([x["ts"] for x in rows()] == [T0, T0 + 600, T0 + 1200] or [x["ts"] for x in rows()] == [T0, T0 + 1200], f"recorded: {[x['ts'] for x in rows()]}")
check(U.recorder_status()["errors"] == errs0 + 1 and U.recorder_status()["last_error"] == "reading", "a failing source is counted, not raised")
check("sk-ant" not in json.dumps(U.recorder_status()), "error enum carries no exception text")
check(U.recorder_status()["state"] == "stopped", "state is stopped after run() returns")
stopped = U.Recorder(reading=lambda n: None, stop=type("S", (), {"is_set": lambda s: True, "wait": lambda s, t: True})())
stopped.run()
check(True, "an already-stopped recorder returns at once")
# real thread, stop event preset: starts and ends without waiting
import threading  # noqa: E402
ev = threading.Event()
ev.set()
th = U.Recorder(reading=lambda n: None, stop=ev)
th.start()
th.join(5)
check(not th.is_alive(), "thread ends when stopped")
# the real source: latest_reading() from a status line file, through the recorder
fresh("real")
(state).mkdir(parents=True, exist_ok=True)
(state / "claude-limits.json").write_text((FX / "claude-limits.json").read_text())
rr = U.Recorder(clock=lambda: 1762559900 + 30)
check(rr.tick() == 3, "default source (latest_reading) feeds the recorder")
check({r_["ts"] for r_ in rows()} == {1762559900.0} and rows("seven_day_opus")[0]["used"] == 20.25, "rows from the status line file")
check(rows()[0]["source"] == "statusline", "source recorded")
(state / "claude-limits.json").unlink()

# 7. latest_reading: raw values, no zeroing of a rolled-over window
(state / "claude-limits.json").write_text(json.dumps({"updated": 1000, "rate_limits": {"seven_day": {"used_percentage": 41.26, "resets_at": 1500}}}))
lr = niu_limits.latest_reading(now=1200)
check(lr == (1000, "statusline", {"seven_day": {"used": 41.26, "resets_at": 1500.0}}), f"latest_reading: {lr}")
check(niu_limits.latest_reading(now=2000)[2]["seven_day"]["used"] == 41.26, "no rollover substitution in the raw reading")
check(niu_limits.claude_limits(now=2000)["windows"][0]["used"] == 0.0, "claude_limits still zeroes it")
(state / "claude-limits.json").unlink()
check(niu_limits.latest_reading(now=1200) is None, "no source gives None")
# a fake API source (never the network)
niu_limits._api.update(at=0.0, rate_limits=None)
os.environ.pop("NIU_CLAUDE_USAGE_API")
fake = lambda: {"five_hour": {"used_percentage": 9.5, "resets_at": "2026-11-08T09:00:00Z"}}  # noqa: E731
got = niu_limits.latest_reading(now=5_000_000, fetch=fake)
check(got is not None and got[1] == "api" and got[2]["five_hour"]["used"] == 9.5 and got[2]["five_hour"]["resets_at"] > 1e9, f"api reading: {got}")
calls = []
niu_limits.latest_reading(now=5_000_100, fetch=lambda: calls.append(1))
check(calls == [], "at most one API call per 5 minutes")
os.environ["NIU_CLAUDE_USAGE_API"] = "0"

# 8. no token or credential text can reach the DB or the payloads
secret = "sk-ant-oat01-CANARYSECRET"
creds = tmp / "creds.json"
creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": secret}}))
os.environ["NIU_CLAUDE_CREDENTIALS"] = str(creds)
fresh("canary")
U.record(rd(T0, 33.0), now=T0)
blob = b"".join(f.read_bytes() for f in U.db_path().parent.glob("usage.db*"))
check(secret.encode() not in blob, "no token text in usage.db")
check(secret not in json.dumps(U.history(35, now=T0)), "no token text in the history payload")
src = (SH / "lib" / "niu_usage.py").read_text()
check("accessToken" not in src and "credentials" not in src.lower() and "Authorization" not in src, "niu_usage never touches credentials")
os.environ["NIU_CLAUDE_CREDENTIALS"] = "/nonexistent/creds.json"

# 9. history_cached: one query per minute per `days`
fresh("memo")
U.record(rd(T0, 10.0), now=T0)
U._memo.clear()
a = U.history_cached(35, now=T0 + 10)
U.record(rd(T0 + 400, 60.0), now=T0 + 400)
b = U.history_cached(35, now=T0 + 40)
check(a is b, "second call within 60 s is served from the memo")
c2 = U.history_cached(35, now=T0 + 80)
check(c2 is not a and c2["current"]["used"] == 60.0, "memo expires after 60 s")
check(U.history_cached("junk", now=T0 + 80)["days"] == 35 and U.history_cached(9999, now=T0 + 80)["days"] == 70, "memo keyed by the clamped days")

# 10. the endpoint
fresh("http")
t_now = time.time()
c = U.connect()
for i in range(30):
    c.execute(ins, ("seven_day", t_now - 3 * 3600 + i * 360, 10 + i, t_now + 3 * D, "api"))
c.commit()
c.close()
srv_env = dict(os.environ, NIU_HOST_LABEL="testhost")
srv_env.pop("NIU_USAGE_COLLECT", None)
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
    return r_.status, dict(r_.getheaders()), js


try:
    for _ in range(50):
        try:
            if req("GET", "/healthz")[0] == 200:
                break
        except OSError:
            time.sleep(0.1)
    st, hd, js = req("GET", "/api/usage/history?days=35")
    check(st == 200 and js["state"] == "ok" and js["days"] == 35, f"endpoint ok: {st} {js and js.get('state')}")
    check(hd.get("Content-Type") == "application/json" and "default-src 'self'" in hd.get("Content-Security-Policy", ""), "json + CSP headers")
    check(len(js["series"]["seven_day"]["points"]) == 30 and js["current"]["used"] == 39.0, "points and current from the db")
    check(js["recorder"]["state"] == "running", f"niu-dash serve started the recorder: {js['recorder']}")
    check(req("GET", "/api/usage/history?days=9999")[2]["days"] == 70, "days=9999 -> 70")
    check(req("GET", "/api/usage/history?days=abc")[2]["days"] == 35, "days=abc -> 35")
    check(req("GET", "/api/usage/history?days=-1")[2]["days"] == 7, "days=-1 -> 7")
    check(req("GET", "/api/usage/history?days=3&days=50")[2]["days"] == 7, "first days value wins")
    check(req("GET", "/api/usage/history")[2]["days"] == 35, "default 35")
    check(req("GET", "/api/usage/history?days=%3Cscript%3E")[2]["days"] == 35, "hostile value -> default")
    c = U.connect()
    c.execute(ins, ("seven_day", t_now - 10, 90.0, t_now + 3 * D, "api"))
    c.commit()
    c.close()
    check(req("GET", "/api/usage/history?days=35")[2]["current"]["used"] == 39.0, "second request within 60 s is cached")
    check(req("GET", "/api/usage/history?days=36")[2]["current"]["used"] == 90.0, "cache is per days value")
    for m in ("POST", "PUT", "DELETE"):
        check(req(m, "/api/usage/history")[0] in (404, 405, 501), f"{m} not served")
    check(req("GET", "/api/usage/other")[0] == 404, "no other usage routes")
    check("sk-ant" not in json.dumps(js), "no credential text in the payload")
finally:
    srv.terminate()
    try:
        srv.wait(5)
    except subprocess.TimeoutExpired:
        srv.kill()
# collection off -> disabled, and nothing is written
fresh("off")
port = (lambda s: (s.bind(("127.0.0.1", 0)), s.getsockname()[1], s.close())[1])(socket.socket())
srv = subprocess.Popen([str(SH / "bin" / "niu-dash"), "serve", "--port", str(port)], env=dict(srv_env, NIU_USAGE_COLLECT="0"),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
try:
    for _ in range(50):
        try:
            if req("GET", "/healthz")[0] == 200:
                break
        except OSError:
            time.sleep(0.1)
    check(req("GET", "/api/usage/history")[2]["state"] == "disabled", "endpoint reports disabled")
    check(not U.db_path().exists(), "no usage.db is created when collection is off")
finally:
    srv.terminate()
    srv.wait(5)

print(f"usage: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
