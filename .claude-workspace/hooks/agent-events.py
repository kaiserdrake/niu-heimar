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

import re  # noqa: E402

RE_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,59}")
TAIL_BYTES = 256 * 1024


def model_from_transcript(path):
    """Model of the last assistant message in a Claude Code transcript (JSONL under ~/.claude), or None."""
    if not isinstance(path, str) or not path.endswith(".jsonl"):
        return None
    try:
        p = Path(path).resolve()
        root = (Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")).resolve()
        if root not in p.parents or not p.is_file():  # no FIFOs / devices: never block the hook
            return None
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"model"' not in line:
            continue
        try:
            m = (json.loads(line).get("message") or {}).get("model")
        except (ValueError, AttributeError):
            continue
        if isinstance(m, str) and RE_MODEL.fullmatch(m) and m != "<synthetic>":
            return m
    return None


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
        m = ti.get("model")
        want = f"@{m}" if isinstance(m, str) and RE_MODEL.fullmatch(m) else ""  # per-spawn model override
        s = f"{ti.get('subagent_type', '')}{want}: {ti.get('description', '')}".strip(": ")
    elif tool == "WebFetch":
        s = (ti.get("url") or "").split("?")[0]
    elif tool == "Skill":
        s = ti.get("skill") or ti.get("command") or ti.get("name") or ""
    elif tool.startswith("mcp__niu-local__"):
        s = ti.get("query") or ti.get("question") or ti.get("focus") or ti.get("base") or ""
    elif tool == "SendMessage":
        s = f"to: {ti.get('to') or ti.get('recipient') or ''}"
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
    elif event in ("SessionStart", "UserPromptSubmit"):
        # Session identity for the dashboard: title, model, and (for niu-agent runs) the local model and task.
        # Never the prompt text itself.
        from niu_events import redact
        info = {"title": redact(str(data.get("session_title") or ""))[:80] or None,
                "model": str(data.get("model") or "")[:60] or None}
        if event == "SessionStart":
            info["source"] = str(data.get("source") or "") or None
            label = os.environ.get("NIU_AGENT_LABEL", "")
            if label.startswith("local:"):
                info["local"] = label[6:]
                info["task"] = os.environ.get("NIU_AGENT_TASK") or None
        info = {k: v for k, v in info.items() if v}
        summary = json.dumps(info, separators=(",", ":")) if info else ""
    elif event == "SessionEnd":
        summary = str(data.get("reason") or "")
    elif event in ("Stop", "SubagentStop"):
        # Model actually used (last assistant message): the session's current model, or the subagent's.
        m = model_from_transcript(data.get("agent_transcript_path") if event == "SubagentStop" else data.get("transcript_path"))
        summary = json.dumps({"model": m}, separators=(",", ":")) if m else ""

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
