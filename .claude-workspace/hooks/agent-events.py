#!/usr/bin/env python3
"""Async hook: record Claude Code session/subagent/tool events for agents-dash.

Never blocks and always exits 0. Stores no prompt text and no tool output, and
redacts anything that looks like a secret in the short target summary.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))


def summarize(tool, ti, project_dir):
    from niu_events import redact

    ti = ti if isinstance(ti, dict) else {}

    def rel(p):
        if not p:
            return ""
        try:
            return os.path.relpath(p, project_dir) if project_dir and os.path.isabs(p) else p
        except ValueError:
            return p

    if tool == "Bash":
        s = (ti.get("command") or "").strip().splitlines()
        s = s[0] if s else ""
    elif tool in ("Read", "Edit", "MultiEdit", "Write"):
        s = rel(ti.get("file_path"))
    elif tool == "NotebookEdit":
        s = rel(ti.get("notebook_path"))
    elif tool == "Grep":
        s = f"{ti.get('pattern', '')}  {rel(ti.get('path'))}".strip()
    elif tool == "Glob":
        s = ti.get("pattern", "")
    elif tool in ("Agent", "Task"):
        s = f"{ti.get('subagent_type', '')}: {ti.get('description', '')}".strip(": ")
    elif tool == "WebFetch":
        s = (ti.get("url") or "").split("?")[0]
    elif tool == "WebSearch":
        s = ti.get("query", "")
    else:
        s = ""
    return redact(str(s))[:120]


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    from niu_events import connect, locate, under_ws

    cwd = data.get("cwd") or os.getcwd()
    if not under_ws(cwd):
        return
    domain, project, project_dir = locate(cwd)
    event = data.get("hook_event_name", "")
    tool = data.get("tool_name") or ""
    summary = ""
    if event in ("PreToolUse", "PostToolUse"):
        summary = summarize(tool, data.get("tool_input"), project_dir)
    elif event == "Notification":
        from niu_events import redact
        summary = redact(str(data.get("notification_type") or data.get("message") or ""))[:120]
    elif event == "SessionStart":
        summary = str(data.get("source") or "")
    elif event == "SessionEnd":
        summary = str(data.get("reason") or "")

    con = connect()
    with con:
        con.execute(
            "INSERT INTO events(ts,event,session_id,domain,project,project_dir,agent_id,agent_type,tool,tool_use_id,summary)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), event, data.get("session_id"), domain, project, project_dir,
             data.get("agent_id"), data.get("agent_type"), tool, data.get("tool_use_id"), summary),
        )
        if event == "SessionStart":
            days = float(os.environ.get("NIU_EVENTS_RETENTION_DAYS", "7"))
            con.execute("DELETE FROM events WHERE ts < ?", (time.time() - days * 86400,))
    con.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break a Claude session
        if os.environ.get("NIU_EVENTS_DEBUG"):
            print(f"agent-events: {e}", file=sys.stderr)
    sys.exit(0)
