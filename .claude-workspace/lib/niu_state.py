"""Shared session/subagent model built from the event store (used by agents-dash and niu-dash)."""
import json
import re
import threading
import time
from datetime import datetime

from niu_events import EDIT_TOOLS, MANDATORY, agent_model, agent_source, connect, project_agents

MAX_TICKS = 400


def new_sub(e):
    return {"type": e["agent_type"] or "subagent", "start": e["ts"], "stop": None, "current": None,
            "tools": [], "task": "", "parent": None, "model": None, "model_req": None, "model_ctx": None}


STALE_SECS = 30 * 60
# A session waiting for the user (permission prompt, question) stays "needs you" much longer before it counts as gone.
WAITING_STALE_SECS = 12 * 3600


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


SYSTEM_SESSIONS = {"web"}  # events written by the dashboard itself (e.g. PR merges): feed only, not a session


def _session_info(summary):
    """SessionStart/UserPromptSubmit summary: JSON {title, model, local, task} (or the older 'local:<model>')."""
    summary = summary or ""
    if summary.startswith("{"):
        try:
            return json.loads(summary)
        except ValueError:
            return {}
    if summary.startswith("local:"):
        return {"local": summary[6:]}
    return {}


def build(events, now):
    sessions = {}
    open_local = {}  # project_dir -> open niu-local tool calls [{sid, ts, tool, query}]
    for e in events:
        sid = e["session_id"] or "?"
        if sid in SYSTEM_SESSIONS:
            continue
        if e["event"] == "PreToolUse" and (e["tool"] or "").startswith("mcp__niu-local__"):
            open_local.setdefault(e["project_dir"] or "", []).append(
                {"sid": sid, "ts": e["ts"], "tool": e["tool"].split("__")[-1], "query": e["summary"] or "", "id": e["tool_use_id"]})
        elif e["event"] == "PostToolUse" and (e["tool"] or "").startswith("mcp__niu-local__"):
            calls = open_local.get(e["project_dir"] or "", [])
            for c in calls:
                if c["sid"] == sid and (not e["tool_use_id"] or c["id"] == e["tool_use_id"]):
                    calls.remove(c)
                    break
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

        if ev in ("SessionStart", "UserPromptSubmit"):
            info = _session_info(e["summary"])
            for k in ("title", "model", "local", "task"):
                if info.get(k):
                    s[k] = info[k]
        if ev == "SessionStart":
            s["ended"] = False
            if s.get("local"):
                # A niu-agent run started while a session had a niu-local tool call open in this project:
                # that session is its caller.
                for call in reversed(open_local.get(s["project_dir"], [])):
                    if call["sid"] != sid and e["ts"] >= call["ts"] - 2:
                        s["parent_session"], s["parent_tool"], s["query"] = call["sid"], call["tool"], call["query"]
                        break
            set_state("idle")
        elif ev == "UserPromptSubmit":
            set_state("working")
        elif ev == "PreToolUse":
            if aid and sub is None:  # tool call from a subagent we missed the start of
                sub = s["subagents"][aid] = new_sub(e)
            target = sub if sub else s
            if not sub and e["tool"] == "Skill" and "develop" in (e["summary"] or ""):
                s.setdefault("develop_at", e["ts"])
            ticks = target.setdefault("tools", [])
            if len(ticks) < MAX_TICKS:
                ticks.append((e["ts"], e["tool"]))
            if e["tool"] in ("Agent", "Task"):
                stype, _, desc = (e["summary"] or "").partition(": ")
                stype, _, want = stype.rpartition("@") if "@" in stype else (stype, "", "")
                want = want if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,59}", want or "") else None
                s["pending_spawns"].setdefault(stype or "general-purpose", []).append((desc, aid, want or None))
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
                sub["task"], sub["parent"], sub["model_req"] = queue.pop(0)
            sub["model_ctx"] = s.get("model")  # what an agent that inherits the model runs on
            set_state("working")
        elif ev == "SubagentStop" and aid:
            sub = sub or s["subagents"].setdefault(aid, new_sub(e))
            sub["stop"], sub["current"] = e["ts"], None
            s["ran"].add(sub["type"])
            sub["model"] = _session_info(e["summary"]).get("model") or sub.get("model")
        elif ev == "Notification":
            set_state("waiting")
            s["waits"].append(e["ts"])
            s["note"] = e["summary"]
        elif ev == "Stop":
            set_state("idle")
            s["model"] = _session_info(e["summary"]).get("model") or s.get("model")
            s["current"] = None
            for sb in s["subagents"].values():
                if sb["stop"] is None:
                    sb["stop"], sb["current"] = e["ts"], None
                    s["ran"].add(sb["type"])
        elif ev == "SessionEnd":
            s["ended"] = True
            set_state("ended")
    for s in sessions.values():
        quiet = now - s["last"]
        if not s["ended"] and s["state"] != "idle" and quiet > (WAITING_STALE_SECS if s["state"] == "waiting" else STALE_SECS):
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


REVIEWERS = ("security-reviewer", "test-reviewer")
HANDOFF_LABEL = {"designer": "design", "implement": "code changes", "security-reviewer": "verdict",
                 "test-reviewer": "verdict", "diff": "findings", "search": "findings", "ask": "answer"}


def chain(s, local_kids):
    """Ordered steps a session ran (agents, local agents, its own implement stretches), grouped where they ran in
    parallel, plus hand-offs between consecutive groups. Agents never talk directly: the session passes each
    step's result on to the next; that's what a hand-off shows."""
    steps = [{"id": aid, "kind": "agent", "name": sb["type"], "start": sb["start"], "end": sb["stop"]}
             for aid, sb in s["subagents"].items()]
    steps += [{"id": l["id"], "kind": "local", "name": l.get("task") or "local", "start": l["first"],
               "end": l["last"] if l["ended"] else None} for l in local_kids]
    # the session's own edits between agent steps = "implement" stretches
    bounds = sorted(st["start"] for st in steps)
    edits = [ts for ts, t in s.get("tools", []) if t in EDIT_TOOLS]
    runs = {}
    for ts in edits:
        k = sum(1 for b in bounds if b <= ts)
        runs.setdefault(k, []).append(ts)
    for k, ts_list in runs.items():
        open_ = s["state"] == "working" and k == len(bounds) and s.get("current") and s["current"][0] in EDIT_TOOLS
        steps.append({"id": f"impl:{s['id']}:{k}", "kind": "implement", "name": "implement", "start": min(ts_list),
                      "end": None if open_ else max(ts_list), "edits": len(ts_list)})
    steps.sort(key=lambda st: st["start"])
    groups = []
    for st in steps:
        g = groups[-1] if groups else None
        # parallel if it started before every member of the current group had finished
        if g and any(m["end"] is None or st["start"] < m["end"] for m in g) and st["kind"] != "implement" \
                and all(m["kind"] != "implement" for m in g):
            g.append(st)
        else:
            groups.append([st])
    # a review round = one (parallel) group that ran at least one mandatory reviewer
    rounds = 0
    for g in groups:
        if any(m["name"] in REVIEWERS for m in g):
            rounds += 1
            for m in g:
                if m["name"] in REVIEWERS:
                    m["round"] = rounds
    handoffs = []
    for a, b in zip(groups, groups[1:]):
        if any(m["end"] is None for m in a):
            continue
        pairs = [(x, y) for x in a for y in b][:6]
        for x, y in pairs:
            handoffs.append({"from": x["id"], "to": y["id"], "label": HANDOFF_LABEL.get(x["name"], "result"),
                             "at": y["start"]})
    return {"steps": steps, "groups": [[m["id"] for m in g] for g in groups], "handoffs": handoffs,
            "review_rounds": rounds}


def develop_stages(s, ch):
    """Progress of a /develop run (design → approve → implement → local review → review → report), or None."""
    steps = ch["steps"]
    designers = [st for st in steps if st["name"] == "designer"]
    if not designers and not s.get("develop_at"):
        return None
    t0 = min([st["start"] for st in designers] + ([s["develop_at"]] if s.get("develop_at") else []))
    after = lambda t: [st for st in steps if st["start"] >= t]  # noqa: E731
    stages = []

    def add(name, status, at=None):
        stages.append({"stage": name, "status": status, "at": at})

    d_end = None
    if not designers:
        add("design", "active" if s["state"] == "working" else "pending")
    elif any(st["end"] is None for st in designers):
        add("design", "active", designers[0]["start"])
    else:
        d_end = max(st["end"] for st in designers)
        add("design", "done", d_end)
    is_impl = lambda st: st["kind"] == "implement" or st["name"] == "implementer"  # noqa: E731 - own edits or Sonnet workers
    impl = [st for st in after(d_end or t0) if is_impl(st)] if d_end else []
    asked = [ts for ts, t in s.get("tools", []) if t == "AskUserQuestion" and d_end and ts >= d_end]
    if not d_end:
        add("approve", "pending")
    elif impl:
        add("approve", "done", asked[0] if asked else impl[0]["start"])
    else:
        add("approve", "active", asked[0] if asked else d_end)
    i_start = impl[0]["start"] if impl else None
    later = [st for st in after(i_start) if not is_impl(st)] if i_start else []
    if not impl:
        add("implement", "pending")
    else:
        running = any(st["end"] is None for st in impl if st["name"] == "implementer")
        add("implement", "done" if later or (not running and any(st["end"] for st in impl) and s["state"] != "working") else "active", i_start)
    localrev = [st for st in later if st["kind"] == "local" and st["name"] == "diff"]
    revs = [st for st in later if st["name"] in REVIEWERS]
    if localrev:
        add("local review", "active" if any(st["end"] is None for st in localrev) else "done", localrev[0]["start"])
    else:
        add("local review", "skipped" if revs else "pending")
    idle = s["state"] in ("idle", "ended")
    if not revs:
        # reviewers run once per PR (CLAUDE.md §2): a finished build without them is not "pending"
        built = next(st["status"] for st in stages if st["stage"] == "implement") == "done"
        add("review", "skipped" if built and idle else "pending")
    elif any(st["end"] is None for st in revs) or not all(any(r["name"] == n for r in revs) for n in REVIEWERS):
        add("review", "active", revs[0]["start"])
    else:
        add("review", "done", max(st["end"] for st in revs))
    review_done = stages[-1]["status"] in ("done", "skipped")
    add("report", "done" if review_done and idle else "active" if review_done else "pending")
    return stages


def agent_model_for(s, sb):
    """{model, model_src}: actual (from its transcript) > spawn override > agent definition > inherited."""
    if sb.get("model"):
        return {"model": sb["model"], "model_src": "actual"}
    if sb.get("model_req"):
        return {"model": sb["model_req"], "model_src": "requested"}
    m = agent_model(s["project_dir"], sb["type"])
    if m and m != "inherit":
        return {"model": m, "model_src": "definition"}
    m = sb.get("model_ctx") or s.get("model")
    return {"model": m, "model_src": "inherited"} if m else {"model": None, "model_src": None}


def to_json(sessions, events, now, domain=None, show_ended=True, feed_n=60):
    """JSON-ready state for the web dashboard."""
    out = []
    kids_of = {}
    for s in sessions.values():
        if s.get("parent_session"):
            kids_of.setdefault(s["parent_session"], []).append(s)
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
                **agent_model_for(s, sb),
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
            "waits": s.get("waits", []), "messages": s.get("messages", []), "local": s.get("local"),
            "title": s.get("title"), "model": s.get("model"), "task": s.get("task"),
            "parent_session": s.get("parent_session"), "parent_tool": s.get("parent_tool"), "query": s.get("query"),
            **(lambda ch: {"chain": ch, "pipeline": develop_stages(s, ch)})(chain(s, kids_of.get(s["id"], []))),
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


# ---------- shared, incremental live state ----------

class LiveState:
    """Keeps the events of one time window in memory and rebuilds the model only when needed.

    Each refresh() reads only rows newer than the last seen id (one indexed query), trims rows that
    fell out of the window, and rebuilds when something changed or every IDLE_REBUILD seconds (for
    stale/idle states). A rebuild that took t seconds delays the next one by at least THROTTLE * t,
    so CPU stays bounded on very large histories no matter how many viewers are connected.
    """
    IDLE_REBUILD = 30.0
    THROTTLE = 10.0

    def __init__(self, since_secs):
        self.since = since_secs
        self.events, self.max_id = [], 0
        self.sessions, self.version, self.built_at, self.cost = {}, 0, 0.0, 0.0
        self.dirty = True
        self.lock = threading.Lock()
        self._json = {}

    def _read_new(self, cutoff):
        try:
            con = connect(readonly=True)
        except FileNotFoundError:
            return []
        try:
            rows = con.execute("SELECT * FROM events WHERE id > ? AND ts >= ? ORDER BY id",
                               (self.max_id, cutoff)).fetchall()
            if not rows:
                # Detect pruning or a reset store (max id went backwards).
                top = con.execute("SELECT max(id) FROM events").fetchone()[0] or 0
                if top < self.max_id:
                    self.events, self.max_id, self.dirty = [], 0, True
                    rows = con.execute("SELECT * FROM events WHERE ts >= ? ORDER BY id", (cutoff,)).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]

    def refresh(self, force=False):
        with self.lock:
            now = time.time()
            cutoff = now - self.since
            new = self._read_new(cutoff)
            if new:
                in_order = not self.events or new[0]["ts"] >= self.events[-1]["ts"]
                self.events.extend(new)
                if not in_order or any(new[i]["ts"] > new[i + 1]["ts"] for i in range(len(new) - 1)):
                    self.events.sort(key=lambda e: (e["ts"], e["id"]))
                self.max_id = max(self.max_id, max(e["id"] for e in new))
                self.dirty = True
            drop = 0
            while drop < len(self.events) and self.events[drop]["ts"] < cutoff:
                drop += 1
            if drop:
                del self.events[:drop]
                self.dirty = True
            due = now - self.built_at >= max(1.0, self.THROTTLE * self.cost)
            idle_due = now - self.built_at >= self.IDLE_REBUILD
            if self.version == 0 or force or (due and (self.dirty or idle_due)):
                t0 = time.perf_counter()
                self.sessions = build(self.events, now)
                self.cost = time.perf_counter() - t0
                self.built_at, self.dirty = now, False
                self.version += 1
                self._json = {}
            return self.version

    def json_for(self, domain=None, feed_n=60):
        """to_json() of the current build, memoized per (domain, feed length) until the next rebuild."""
        with self.lock:
            key = (domain or "", feed_n)
            if key not in self._json:
                self._json[key] = to_json(self.sessions, self.events, self.built_at, domain, feed_n=feed_n)
            return self._json[key]


_LIVE = {}
_LIVE_LOCK = threading.Lock()


def live(since_secs):
    """Process-wide LiveState per window, shared by every viewer."""
    with _LIVE_LOCK:
        if since_secs not in _LIVE:
            _LIVE[since_secs] = LiveState(since_secs)
        return _LIVE[since_secs]


def mark_dirty():
    """Ask every live window to rebuild on its next refresh (e.g. after the server logs an event)."""
    with _LIVE_LOCK:
        for lv in _LIVE.values():
            lv.dirty = True
            lv.built_at = 0.0  # skip the throttle once so the change shows immediately
