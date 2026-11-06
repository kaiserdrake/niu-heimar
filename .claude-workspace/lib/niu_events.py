"""Shared helpers for the niu-heimar agent event store (stdlib only)."""
import os
import re
import sqlite3
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
SHARED = WS / ".claude-workspace"
MANDATORY = ("security-reviewer", "test-reviewer")
EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  event TEXT NOT NULL,
  session_id TEXT,
  domain TEXT,
  project TEXT,
  project_dir TEXT,
  agent_id TEXT,
  agent_type TEXT,
  tool TEXT,
  tool_use_id TEXT,
  summary TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_session ON events(session_id, ts);
"""


def host_label() -> str:
    """Name shown for the workspace itself (default: this machine's hostname, e.g. Midgard)."""
    import socket
    name = os.environ.get("NIU_HOST_LABEL") or socket.gethostname().split(".")[0]
    return name[:1].upper() + name[1:] if name else "(workspace)"


def show_domain(d: str) -> str:
    return host_label() if d == "(workspace)" else (d or "?")


def db_path() -> Path:
    if os.environ.get("NIU_EVENTS_DB"):
        return Path(os.environ["NIU_EVENTS_DB"])
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(state) / "niu-heimar" / "events.db"


def connect(readonly: bool = False) -> sqlite3.Connection:
    p = db_path()
    if readonly:
        if not p.exists():
            raise FileNotFoundError(p)
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2)
    else:
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        new = not p.exists()
        con = sqlite3.connect(p, timeout=2)
        if new:
            os.chmod(p, 0o600)
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row
    return con


def under_ws(path: str) -> bool:
    try:
        p = Path(path).resolve()
    except (OSError, RuntimeError):
        return False
    return p == WS or WS in p.parents


def locate(cwd: str):
    """Return (domain, project, project_dir) for a cwd under the workspace.

    The project is the nearest folder at or above cwd (below the domain folder)
    that has .claude/ or .git, so nested projects like HOBBY/games/voxel-mod work.
    """
    p = Path(cwd).resolve()
    try:
        rel = p.relative_to(WS)
    except ValueError:
        return None, None, None
    if not rel.parts or rel.parts[0].startswith("."):
        return "(workspace)", "", str(WS)
    domain = rel.parts[0]
    droot = WS / domain
    cur = p
    while cur != droot and droot in cur.parents:
        if (cur / ".claude").is_dir() or (cur / ".git").exists():
            return domain, str(cur.relative_to(droot)), str(cur)
        cur = cur.parent
    if p == droot:
        return domain, "", str(droot)
    first = droot / rel.parts[1]
    return domain, rel.parts[1], str(first)


_REDACT = [
    (re.compile(r"(?i)(://)[^/\s:@]+:[^@\s/]+@"), r"\1****@"),
    (re.compile(r"(?i)\b(authorization|x-api-key|api-key|cookie|private-token)\s*:\s*[^\"'\n]+"), r"\1: ****"),
    (re.compile(r"(?i)\bbearer\s+\S+"), "Bearer ****"),
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|AUTH|CREDENTIALS?)[A-Z0-9_]*)\s*([=:])\s*(\"[^\"]*\"|'[^']*'|\S+)"), r"\1\2****"),
    (re.compile(r"(?i)(--?(?:password|token|secret|api-key|key)(?:=|\s+))\S+"), r"\1****"),
    (re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{10,}|glpat-[A-Za-z0-9_-]{10,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})"), "****"),
    # long opaque tokens: 32+ chars mixing letters and digits (plain paths and words are left alone)
    (re.compile(r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_+-]*\d)(?=[A-Za-z0-9_+-]*[A-Za-z])[A-Za-z0-9_+-]{32,}={0,2}"), "****"),
]


def redact(text: str) -> str:
    for rx, rep in _REDACT:
        text = rx.sub(rep, text)
    return text


def agent_source(project_dir: str, agent_type: str) -> str:
    """Where an agent definition comes from: project, domain, shared, user or builtin."""
    if not agent_type:
        return ""
    if project_dir:
        f = Path(project_dir) / ".claude" / "agents" / f"{agent_type}.md"
        if f.is_symlink():
            target = f.resolve()
            if SHARED in target.parents:
                return "shared"
            return "domain"
        if f.exists():
            return "project"
    if (Path.home() / ".claude" / "agents" / f"{agent_type}.md").exists():
        return "user"
    return "builtin"


def project_agents(project_dir: str):
    d = Path(project_dir) / ".claude" / "agents" if project_dir else None
    if not d or not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.md") if p.exists())
