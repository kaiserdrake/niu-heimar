#!/usr/bin/env python3
"""PreToolUse hook for local Ollama agents (niu-agent): keep them read-only and inside their project.

Blocks (exit 2): any Bash, any write/web tool, and Read/Grep/Glob paths that resolve outside the project
directory the agent was started in (NIU_AGENT_ROOT, else the session cwd). The workspace guard
(block-env.sh) runs as well, for dotenv files and .claude/protected-paths.
"""
import json
import os
import sys
from pathlib import Path

ALLOWED = {"Read", "Grep", "Glob", "LS"}


def block(msg):
    print(f"BLOCKED (local agent scope): {msg}", file=sys.stderr)
    sys.exit(2)


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        block("unreadable hook input")
    tool = data.get("tool_name", "")
    if tool not in ALLOWED:
        block(f"{tool or 'this tool'} is not available to local agents (read-only: Read, Grep, Glob)")
    root = Path(os.environ.get("NIU_AGENT_ROOT") or data.get("cwd") or os.getcwd()).resolve()
    ti = data.get("tool_input") or {}
    for key in ("file_path", "path", "notebook_path"):
        val = ti.get(key)
        if not val:
            continue
        p = Path(val).expanduser()
        p = (p if p.is_absolute() else root / p).resolve()
        if p != root and root not in p.parents:
            block(f"{tool} outside the project ({val})")
    pat = str(ti.get("pattern") or "")
    if tool == "Glob" and (pat.startswith("/") or pat.startswith("~") or ".." in Path(pat).parts):
        block(f"Glob pattern outside the project ({pat})")
    sys.exit(0)


if __name__ == "__main__":
    main()
