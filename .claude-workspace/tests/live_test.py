#!/usr/bin/env python3
"""Tests for niu_state.LiveState (shared incremental cache used by niu-dash and agents-dash)."""
import os
import sys
import tempfile
import time
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ["NIU_EVENTS_DB"] = str(Path(tmp) / "events.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from niu_events import connect  # noqa: E402
from niu_state import LiveState, build, load, to_json  # noqa: E402

passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


con = connect()


def ins(event, sid="s1", ts=None, **kw):
    con.execute("INSERT INTO events(ts,event,session_id,domain,project,project_dir,agent_id,agent_type,tool,summary)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (ts or time.time(), event, sid, "PERSONAL", "app", "/x", kw.get("aid"), kw.get("at"), kw.get("tool"), kw.get("s", "")))
    con.commit()


def summary(sessions):
    return {sid: (s["state"], sorted(s["subagents"]), len(s["tools"]), s["edited"]) for sid, s in sessions.items()}


ins("SessionStart"); ins("UserPromptSubmit"); ins("PreToolUse", tool="Edit")
ins("SubagentStart", aid="a1", at="security-reviewer")

lv = LiveState(86400)
v1 = lv.refresh()
full = build(load(86400), time.time())
check(summary(lv.sessions) == summary(full), "first build matches a full rebuild")
check(lv.refresh() == v1, "no new events -> no rebuild")

ins("PreToolUse", aid="a1", at="security-reviewer", tool="Bash", s="npm audit")
check(lv.refresh() == v1, "rebuild throttled within 1s")
time.sleep(1.05)
v2 = lv.refresh()
check(v2 == v1 + 1, "new event -> rebuild after throttle window")
check(lv.sessions["s1"]["subagents"]["a1"]["current"][0] == "Bash", "incremental event applied")
check(summary(lv.sessions) == summary(build(load(86400), time.time())), "incremental state matches a full rebuild")
js = lv.json_for(None)
check(js is lv.json_for(None), "per-domain JSON memoized until next rebuild")
check(lv.json_for("WORK")["sessions"] == [], "domain filter applied")

# Out-of-order timestamp (async hooks can insert slightly late)
ins("PreToolUse", sid="s2", ts=time.time() - 5, tool="Read")
time.sleep(1.05)
lv.refresh()
check(all(lv.events[i]["ts"] <= lv.events[i + 1]["ts"] for i in range(len(lv.events) - 1)), "events kept in time order")
check("s2" in lv.sessions, "late event included")

# Window trimming
short = LiveState(3)
ins("SessionStart", sid="old", ts=time.time() - 10)
ins("SessionStart", sid="fresh")
short.refresh()
check("old" not in short.sessions and "fresh" in short.sessions, "events outside the window are dropped")

# Store reset/prune (ids go backwards) is detected
con.execute("DELETE FROM events"); con.commit()
ins("SessionStart", sid="after-reset")
time.sleep(1.05)
lv.refresh()
check(set(lv.sessions) == {"after-reset"}, f"reset detected: {set(lv.sessions)}")

# Idle rebuild keeps stale/idle states moving even without events
LiveState.IDLE_REBUILD = 0.5
v = lv.refresh(); time.sleep(1.1)
check(lv.refresh() == v + 1, "idle rebuild after IDLE_REBUILD")

# Throttle scales with cost
lv.cost = 0.3  # pretend the last build took 300 ms -> next build at least 3 s later
lv.dirty = True; lv.built_at = time.time()
v = lv.refresh()
check(lv.refresh() == v, "slow builds back off (throttle * cost)")

print(f"live: {passed} passed, {failed} failed")
os.system(f"rm -rf {tmp}")
sys.exit(1 if failed else 0)
