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

# ---------- naming, system events, local-agent linking (pure build()) ----------
import json as _json  # noqa: E402
T = time.time()


def E(dt, event, sid, **kw):
    return {"id": int(dt * 1000), "ts": T + dt, "event": event, "session_id": sid, "domain": "PERSONAL", "project": "app",
            "project_dir": "/p/app", "agent_id": kw.get("aid"), "agent_type": kw.get("at"), "tool": kw.get("tool"),
            "tool_use_id": kw.get("tuid"), "summary": kw.get("s", "")}


evs = [
    E(0, "SessionStart", "main", s=_json.dumps({"title": "Add login page", "model": "claude-opus-5-5"})),
    E(1, "UserPromptSubmit", "main"),
    E(2, "PreToolUse", "main", tool="mcp__niu-local__local_search", tuid="t1", s="where is auth handled"),
    E(3, "SessionStart", "loc1", s=_json.dumps({"local": "gpt-oss:20b", "task": "search"})),
    E(4, "PreToolUse", "loc1", tool="Grep", s="auth"),
    E(5, "SessionEnd", "loc1"),
    E(6, "PostToolUse", "main", tool="mcp__niu-local__local_search", tuid="t1"),
    E(7, "SessionStart", "loc2", s=_json.dumps({"local": "gpt-oss:20b", "task": "diff"})),  # no open call -> no parent
    E(8, "PreToolUse", "web", tool="gh", s="o/r#1: merged"),                                   # dashboard's own log entry
    E(9, "SessionStart", "old", s="local:qwen3:14b"),                                          # pre-upgrade format
]
ss = build(evs, T + 10)
check(ss["main"].get("title") == "Add login page" and ss["main"].get("model") == "claude-opus-5-5", "title and model recorded")
check(ss["loc1"].get("parent_session") == "main" and ss["loc1"].get("parent_tool") == "local_search", "local run linked to caller")
check(ss["loc1"].get("query") == "where is auth handled" and ss["loc1"].get("task") == "search", "local run carries query and task")
check(ss["loc2"].get("parent_session") is None, "no caller -> no link")
check("web" not in ss, "dashboard system events are not a session")
check(ss["old"].get("local") == "qwen3:14b", "older local label still understood")
js = to_json(ss, evs, T + 10)
check(any(e["session_id"] == "web" for e in js["feed"]), "system events still in the feed")
check(next(s for s in js["sessions"] if s["id"] == "loc1")["parent_session"] == "main", "link exported to the page")

# ---------- hand-off chain and /develop progress (simulated run) ----------
run = [
    E(0, "SessionStart", "dev", s=_json.dumps({"title": "Add --version flag"})),
    E(1, "UserPromptSubmit", "dev"),
    E(2, "PreToolUse", "dev", tool="Skill", s="develop"),
    E(3, "PreToolUse", "dev", tool="Agent", s="designer: Design --version flag"),
    E(4, "SubagentStart", "dev", aid="d1", at="designer"),
    E(5, "PreToolUse", "dev", aid="d1", at="designer", tool="Read", s="bin/niu-dash"),
    E(20, "SubagentStop", "dev", aid="d1", at="designer"),
]
st = to_json(build(run, T + 21), run, T + 21)["sessions"][0]
stages = {p["stage"]: p["status"] for p in st["pipeline"] or []}
check(stages.get("design") == "done" and stages.get("approve") == "active" and stages.get("implement") == "pending",
      f"after design: waiting for approval {stages}")

run += [
    E(25, "PreToolUse", "dev", tool="AskUserQuestion"),
    E(40, "PreToolUse", "dev", tool="Edit", s="bin/niu-dash"),
    E(41, "PreToolUse", "dev", tool="Write", s="tests/v.py"),
    E(50, "PreToolUse", "dev", tool="mcp__niu-local__local_diff_review", tuid="ld", s="error handling"),
    E(51, "SessionStart", "ldiff", s=_json.dumps({"local": "gpt-oss:20b", "task": "diff"})),
    E(70, "SessionEnd", "ldiff"),
    E(71, "PostToolUse", "dev", tool="mcp__niu-local__local_diff_review", tuid="ld"),
    E(80, "PreToolUse", "dev", tool="Agent", s="security-reviewer: review"),
    E(80.5, "PreToolUse", "dev", tool="Agent", s="test-reviewer: review"),
    E(81, "SubagentStart", "dev", aid="r1", at="security-reviewer"),
    E(81.5, "SubagentStart", "dev", aid="r2", at="test-reviewer"),
]
st = to_json(build(run, T + 90), run, T + 90)["sessions"]
dev = next(x for x in st if x["id"] == "dev")
stages = {p["stage"]: p["status"] for p in dev["pipeline"]}
check(stages == {"design": "done", "approve": "done", "implement": "done", "local review": "done", "review": "active",
                 "report": "pending"}, f"mid-review stages {stages}")
groups = dev["chain"]["groups"]
check(groups[0] == ["d1"] and groups[1][0].startswith("impl:") and groups[2] == ["ldiff"] and sorted(groups[3]) == ["r1", "r2"],
      f"steps in order, reviewers in parallel: {groups}")
hand = {(h["from"], h["to"]): h["label"] for h in dev["chain"]["handoffs"]}
impl_id = groups[1][0]
check(hand.get(("d1", impl_id)) == "design", "designer → implement carries the design")
check(hand.get((impl_id, "ldiff")) == "code changes", "implement → local diff review carries code changes")
check(hand.get(("ldiff", "r1")) == "findings" and hand.get(("ldiff", "r2")) == "findings", "local review → both reviewers")
check(not any(k[0] in ("r1", "r2") for k in hand), "no hand-off out of reviewers still running")

run += [E(120, "SubagentStop", "dev", aid="r1", at="security-reviewer"), E(125, "SubagentStop", "dev", aid="r2", at="test-reviewer"),
        E(126, "Stop", "dev")]
dev = next(x for x in to_json(build(run, T + 130), run, T + 130)["sessions"] if x["id"] == "dev")
stages = {p["stage"]: p["status"] for p in dev["pipeline"]}
check(stages.get("review") == "done" and stages.get("report") == "done", f"finished run {stages}")
check(dev["chain"]["review_rounds"] == 1 and {st["id"]: st.get("round") for st in dev["chain"]["steps"]}["r1"] == 1,
      "one review round, both reviewers in round 1")
plain = [E(0, "SessionStart", "p"), E(1, "PreToolUse", "p", tool="Edit")]
check(to_json(build(plain, T + 2), plain, T + 2)["sessions"][0]["pipeline"] is None, "no /develop -> no progress strip")

# Model per agent: actual (SubagentStop transcript) > spawn override > agent definition > inherited
import importlib.util, json as _json  # noqa: E401,E402
pdir = Path(tmp) / "proj"
(pdir / ".claude" / "agents").mkdir(parents=True)
(pdir / ".claude" / "agents" / "designer.md").write_text("---\nname: designer\ndescription: d\nmodel: opus\n---\nbody\n")
(pdir / ".claude" / "agents" / "helper.md").write_text("---\nname: helper\ndescription: d\nmodel: inherit\n---\n")
M = lambda ts, ev, **kw: {**E(ts, ev, "m", **kw), "project_dir": str(pdir)}  # noqa: E731
mrun = [M(0, "SessionStart", s='{"model":"claude-opus-5-5"}'), M(1, "UserPromptSubmit"),
        M(2, "PreToolUse", tool="Agent", s="designer: plan it"), M(3, "SubagentStart", aid="d1", at="designer"),
        M(4, "PreToolUse", tool="Agent", s="helper@haiku: quick look"), M(5, "SubagentStart", aid="h1", at="helper"),
        M(6, "PreToolUse", tool="Agent", s="helper: inherit"), M(7, "SubagentStart", aid="h2", at="helper"),
        M(8, "SubagentStop", aid="d1", at="designer", s='{"model":"claude-opus-5-5"}'),
        M(9, "Stop", s='{"model":"claude-sonnet-5-5"}')]
ms = to_json(build(mrun, T + 10), mrun, T + 10)["sessions"][0]
mm = {x["id"]: (x["model"], x["model_src"]) for x in ms["subagents"]}
check(mm["d1"] == ("claude-opus-5-5", "actual"), f"actual model from transcript: {mm}")
check(mm["h1"] == ("haiku", "requested"), f"spawn override: {mm}")
check(mm["h2"] == ("claude-opus-5-5", "inherited"), f"inherit -> session model at spawn time: {mm}")
check(ms["model"] == "claude-sonnet-5-5", f"session model follows the latest turn: {ms['model']}")
check({x["id"]: x["task"] for x in ms["subagents"]}["h1"] == "quick look", "task text without the @model part")
mrun2 = [M(0, "SessionStart"), M(2, "PreToolUse", tool="Agent", s="designer: plan"), M(3, "SubagentStart", aid="d9", at="designer")]
check(to_json(build(mrun2, T + 4), mrun2, T + 4)["sessions"][0]["subagents"][0]["model_src"] == "definition", "agent definition model")

# Hook: model of the last assistant message, only from transcripts under the Claude config dir
spec = importlib.util.spec_from_file_location("agent_events", Path(__file__).resolve().parents[1] / "hooks" / "agent-events.py")
ae = importlib.util.module_from_spec(spec); spec.loader.exec_module(ae)
cdir = Path(tmp) / "claudecfg" / "projects" / "x"
cdir.mkdir(parents=True)
os.environ["CLAUDE_CONFIG_DIR"] = str(Path(tmp) / "claudecfg")
tr = cdir / "t.jsonl"
tr.write_text("\n".join(_json.dumps(o) for o in (
    {"type": "assistant", "message": {"model": "claude-opus-5-5"}}, {"type": "user", "message": {"content": "hi"}},
    {"type": "assistant", "message": {"model": "claude-sonnet-5-5"}}, {"type": "assistant", "message": {"model": "<synthetic>"}})) + "\n")
check(ae.model_from_transcript(str(tr)) == "claude-sonnet-5-5", "last real assistant model")
outside = Path(tmp) / "elsewhere.jsonl"
outside.write_text(tr.read_text())
check(ae.model_from_transcript(str(outside)) is None, "transcripts outside the Claude config dir are ignored")
bad = cdir / "b.jsonl"
bad.write_text(_json.dumps({"message": {"model": "x y <script>"}}) + "\n")
check(ae.model_from_transcript(str(bad)) is None and ae.model_from_transcript(None) is None, "invalid model strings rejected")
check(ae.summarize("Agent", {"subagent_type": "helper", "description": "d", "model": "haiku"}, "") == "helper@haiku: d", "spawn summary carries model")
check(ae.summarize("Agent", {"subagent_type": "helper", "description": "d", "model": "a b"}, "") == "helper: d", "bad override dropped")

# /develop with Sonnet implementer agents and no reviewers (they run per PR): implement done, review skipped, report done
drun = [E(0, "SessionStart", "dv"), E(1, "UserPromptSubmit", "dv"), E(2, "SubagentStart", "dv", aid="ds", at="designer"),
        E(10, "SubagentStop", "dv", aid="ds", at="designer"), E(12, "PreToolUse", "dv", tool="AskUserQuestion"),
        E(20, "SubagentStart", "dv", aid="i1", at="implementer"), E(21, "SubagentStart", "dv", aid="i2", at="implementer"),
        E(40, "SubagentStop", "dv", aid="i1", at="implementer")]
dv = to_json(build(drun, T + 41), drun, T + 41)["sessions"][0]
check({p["stage"]: p["status"] for p in dv["pipeline"]}["implement"] == "active", f"implement active while a worker runs: {dv['pipeline']}")
drun += [E(45, "SubagentStop", "dv", aid="i2", at="implementer"), E(50, "Stop", "dv")]
dv = to_json(build(drun, T + 51), drun, T + 51)["sessions"][0]
st = {p["stage"]: p["status"] for p in dv["pipeline"]}
check((st["implement"], st["review"], st["report"]) == ("done", "skipped", "done"), f"build-only /develop run: {st}")

# Stale: a working session that went quiet for 30 min is stale; a waiting one stays waiting (needs you) for 12 h
q = [E(0, "SessionStart", "w1"), E(1, "UserPromptSubmit", "w1"), E(2, "Notification", "w1", s="permission_prompt"),
     E(0, "SessionStart", "k1"), E(1, "UserPromptSubmit", "k1")]
qs = {x["id"]: x["state"] for x in to_json(build(q, T + 3600), q, T + 3600)["sessions"]}
check(qs == {"w1": "waiting", "k1": "stale"}, f"waiting survives 1 h, working goes stale: {qs}")
qs = {x["id"]: x["state"] for x in to_json(build(q, T + 13 * 3600), q, T + 13 * 3600)["sessions"]}
check(qs["w1"] == "stale", f"waiting goes stale after 12 h: {qs}")

print(f"live: {passed} passed, {failed} failed")
os.system(f"rm -rf {tmp}")
sys.exit(1 if failed else 0)
