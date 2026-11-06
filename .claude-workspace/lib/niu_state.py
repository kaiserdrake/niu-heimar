"""Shared session/subagent model built from the event store (used by agents-dash and niu-dash)."""
import time
from datetime import datetime

from niu_events import EDIT_TOOLS, MANDATORY, agent_source, connect, project_agents

MAX_TICKS = 400


def new_sub(e):
    return {"type": e["agent_type"] or "subagent", "start": e["ts"], "stop": None, "current": None,
            "tools": [], "task": "", "parent": None}


STALE_SECS = 30 * 60


def parse_since(s):
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if s and s[-1] in units:
        return float(s[:-1]) * units[s[-1]]
    return float(s)


def dur(secs):
    secs = max(0, int(secs))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    if secs < 86400:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
    return f"{secs // 86400}d{(secs % 86400) // 3600}h"


def load(since_secs):
    try:
        con = connect(readonly=True)
    except FileNotFoundError:
        return []
    try:
        rows = con.execute("SELECT * FROM events WHERE ts >= ? ORDER BY ts, id",
                           (time.time() - since_secs,)).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def build(events, now):
    sessions = {}
    for e in events:
        sid = e["session_id"] or "?"
        s = sessions.get(sid)
        if s is None:
            s = sessions[sid] = {
                "id": sid, "domain": e["domain"] or "?", "project": e["project"] or "",
                "project_dir": e["project_dir"] or "", "first": e["ts"], "last": e["ts"],
                "state": "idle", "since": e["ts"], "current": None, "edited": False,
                "subagents": {}, "ran": set(), "ended": False,
                "tools": [], "waits": [], "pending_spawns": {}, "messages": [],
            }
        s["last"] = e["ts"]
        ev, aid = e["event"], e["agent_id"]
        sub = s["subagents"].get(aid) if aid else None

        def set_state(st, s=s, e=e):
            if s["state"] != st:
                s["state"], s["since"] = st, e["ts"]

        if ev == "SessionStart":
            s["ended"] = False
            set_state("idle")
        elif ev == "UserPromptSubmit":
            set_state("working")
        elif ev == "PreToolUse":
            if aid and sub is None:  # tool call from a subagent we missed the start of
                sub = s["subagents"][aid] = new_sub(e)
            target = sub if sub else s
            ticks = target.setdefault("tools", [])
            if len(ticks) < MAX_TICKS:
                ticks.append((e["ts"], e["tool"]))
            if e["tool"] in ("Agent", "Task"):
                stype, _, desc = (e["summary"] or "").partition(": ")
                s["pending_spawns"].setdefault(stype or "general-purpose", []).append((desc, aid))
            elif e["tool"] == "SendMessage":
                s["messages"].append({"ts": e["ts"], "from": aid or sid, "to": (e["summary"] or "").removeprefix("to: ")})
            target["current"] = (e["tool"], e["summary"], e["ts"], e["tool_use_id"])
            if e["tool"] in EDIT_TOOLS:
                s["edited"] = True
            if s["state"] in ("idle", "waiting"):
                set_state("working")
        elif ev == "PostToolUse":
            target = sub if sub else s
            cur = target.get("current")
            if cur and (not e["tool_use_id"] or cur[3] == e["tool_use_id"]):
                target["current"] = None
            if s["state"] == "waiting":
                set_state("working")
        elif ev == "SubagentStart" and aid:
            sub = s["subagents"][aid] = new_sub(e)
            queue = s["pending_spawns"].get(sub["type"]) or s["pending_spawns"].get("general-purpose") or []
            if queue:
                sub["task"], sub["parent"] = queue.pop(0)
            set_state("working")
        elif ev == "SubagentStop" and aid:
            sub = sub or s["subagents"].setdefault(aid, new_sub(e))
            sub["stop"], sub["current"] = e["ts"], None
            s["ran"].add(sub["type"])
        elif ev == "Notification":
            set_state("waiting")
            s["waits"].append(e["ts"])
            s["note"] = e["summary"]
        elif ev == "Stop":
            set_state("idle")
            s["current"] = None
            for sb in s["subagents"].values():
                if sb["stop"] is None:
                    sb["stop"], sb["current"] = e["ts"], None
                    s["ran"].add(sb["type"])
        elif ev == "SessionEnd":
            s["ended"] = True
            set_state("ended")
    for s in sessions.values():
        if not s["ended"] and now - s["last"] > STALE_SECS and s["state"] != "idle":
            s["state"] = "stale"
        s["flagged"] = s["edited"] and s["state"] in ("idle", "ended", "stale") and not all(m in s["ran"] for m in MANDATORY)
    return sessions


def today_stats(sessions, now):
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    today = [s for s in sessions.values() if s["last"] >= midnight]
    runs = {}
    for s in today:
        for sb in s["subagents"].values():
            if sb["start"] >= midnight:
                runs[sb["type"]] = runs.get(sb["type"], 0) + 1
    edited = [s for s in today if s["edited"]]
    reviewed = [s for s in edited if all(m in s["ran"] for m in MANDATORY)]
    flagged = [s for s in today if s["flagged"]]
    return {
        "sessions": len(today), "subagent_runs": sum(runs.values()),
        "runs": sorted(runs.items(), key=lambda kv: -kv[1]),
        "edited": len(edited), "reviewed": len(reviewed), "flagged": [s["id"] for s in flagged],
        "flagged_projects": sorted({f"{s['domain']}/{s['project']}" for s in flagged}),
    }


def to_json(sessions, events, now, domain=None, show_ended=True, feed_n=60):
    """JSON-ready state for the web dashboard."""
    out = []
    for s in sorted(sessions.values(), key=lambda s: -s["last"]):
        if domain and s["domain"] != domain:
            continue
        if s["ended"] and not show_ended:
            continue
        subs = []
        for aid, sb in sorted(s["subagents"].items(), key=lambda kv: kv[1]["start"]):
            subs.append({
                "id": aid, "type": sb["type"], "source": agent_source(s["project_dir"], sb["type"]),
                "start": sb["start"], "stop": sb["stop"], "task": sb.get("task", ""),
                "parent": sb.get("parent") or s["id"],
                "current": list(sb["current"][:3]) if sb["current"] else None,
                "tools": sb.get("tools", []),
            })
        ran_or_running = s["ran"] | {sb["type"] for sb in s["subagents"].values()}
        pending = [] if s["ended"] else [
            {"type": n, "source": agent_source(s["project_dir"], n)}
            for n in project_agents(s["project_dir"]) if n not in ran_or_running]
        out.append({
            "id": s["id"], "domain": s["domain"], "project": s["project"], "state": s["state"],
            "since": s["since"], "first": s["first"], "last": s["last"], "ended": s["ended"],
            "current": list(s["current"][:3]) if s["current"] else None, "note": s.get("note"),
            "edited": s["edited"], "flagged": s["flagged"], "ran": sorted(s["ran"]),
            "subagents": subs, "pending": pending, "tools": s.get("tools", []),
            "waits": s.get("waits", []), "messages": s.get("messages", []),
        })
    feed = []
    for e in reversed(events):
        if domain and e["domain"] != domain:
            continue
        feed.append({k: e[k] for k in ("ts", "event", "session_id", "domain", "project", "agent_id", "agent_type", "tool", "summary")})
        if len(feed) >= feed_n:
            break
    return {"now": now, "sessions": out, "today": today_stats(sessions, now), "feed": feed,
            "domains": sorted({s["domain"] for s in sessions.values()})}
