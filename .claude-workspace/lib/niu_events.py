"""Shared helpers for the niu-heimar agent event store (stdlib only)."""
import json
import os
import re
import sqlite3
import time
import unicodedata
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


NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")
EFFORTS = frozenset("low medium high xhigh max".split())
BUILTIN = [("general-purpose", "All tools; complex multi-step work and code changes."),
           ("Explore", "Read-only search of the codebase."), ("Plan", "Read-only research for plan mode.")]
LOCAL_TOOLS = [("local_search", "search"), ("local_diff_review", "diff review"), ("local_ask", "Q&A"),
               ("local_logs", "logs"), ("local_ci", "CI"), ("local_git", "git"), ("local_status", "status")]
FLOW_SKILLS = ("develop", "dual-review")
_READ_CAP = 4000
_MODEL_RE = re.compile(r"[A-Za-z0-9._:\[\]-]{1,60}")
_FM_CACHE = {}


def _frontmatter(f):
    """Top-level `key: value` pairs of the frontmatter in the first 4 KB of f (mtime cached).

    Returns {} when the file has no frontmatter and None when it can't be stat-ed."""
    f = Path(f)
    try:
        mt = f.stat().st_mtime
    except OSError:
        return None
    hit = _FM_CACHE.get(str(f))
    if hit and hit[0] == mt:
        return hit[1]
    fm = {}
    try:
        with open(f, "rb") as fh:
            head = fh.read(_READ_CAP).decode("utf-8", "replace")
    except OSError:
        head = ""
    if head.startswith("---"):
        for line in head.split("\n---", 1)[0].splitlines()[1:]:
            m = re.match(r"([A-Za-z_-]{1,40}):[ \t]*(.*)$", line)
            if m and m.group(1) not in fm:
                fm[m.group(1)] = m.group(2).strip()
    if len(_FM_CACHE) > 512:
        _FM_CACHE.clear()
    _FM_CACHE[str(f)] = (mt, fm)
    return fm


def _fm_model(fm):
    m = _MODEL_RE.match((fm or {}).get("model", "").strip("\"' "))
    return m.group(0) if m else None


def agent_model(project_dir: str, agent_type: str):
    """The `model:` frontmatter of an agent definition (project, then user agents), or None."""
    if not agent_type or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", agent_type):
        return None
    for d in ([Path(project_dir) / ".claude" / "agents"] if project_dir else []) + [Path.home() / ".claude" / "agents"]:
        fm = _frontmatter(d / f"{agent_type}.md")
        if fm is None:
            continue
        return _fm_model(fm)
    return None


def project_agents(project_dir: str):
    d = Path(project_dir) / ".claude" / "agents" if project_dir else None
    if not d or not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.md") if p.exists())


def _describe(text, limit=160):
    """One sentence, at most `limit` chars, no control characters or angle brackets, redacted."""
    text = "".join(" " if c in "\r\n\t" else c for c in (text or ""))
    text = "".join(c for c in text if unicodedata.category(c)[0] != "C" and c not in "<>").strip().strip("\"'").strip()
    text = re.split(r"\.(?:\s|$)", text, 1)[0]
    text = " ".join(redact(text).split())
    return text[:limit].rstrip() if text else "-"


def _inside(p, bases):
    return any(p == b or b in p.parents for b in bases)


def _scan(d, bases, per_dir=64, skills=False):
    """[(name, file)] for regular, safe files in an agents (or skills) directory."""
    out = []
    try:
        entries = sorted(d.iterdir())
    except OSError:
        return out
    for e in entries:
        if len(out) >= per_dir:
            break
        stem = e.name if skills else (e.stem if e.suffix == ".md" else "")
        if not stem or not NAME_RE.fullmatch(stem):
            continue
        f = e / "SKILL.md" if skills else e
        try:
            real = f.resolve()
            if not _inside(real, bases) or not real.is_file():
                continue
        except (OSError, RuntimeError):
            continue
        out.append((stem, f))
    return out


def agent_catalog(project_dir, domain, roots=None):
    """Every agent, skill and local tool a session in this project could use (no absolute paths).

    roots (tests): {"project", "domain", "shared", "user": agents dirs, "allowed": [bases symlinks may resolve into]}."""
    home_claude = Path.home() / ".claude"
    r = dict(roots or {})
    if "project" not in r:
        r["project"] = Path(project_dir) / ".claude" / "agents" if project_dir else None
    if "domain" not in r:
        ok = bool(domain) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", domain)
        r["domain"] = WS / domain / ".claude" / "agents" if ok else None
    r.setdefault("shared", SHARED / "agents")
    r.setdefault("user", home_claude / "agents")
    allowed = [Path(x).resolve() for x in r.get("allowed", [WS, home_claude])]
    shared_root = Path(r["shared"]).resolve().parent

    def src(base, f):
        if base == "project" and f.is_symlink():
            return "shared" if _inside(f.resolve(), [shared_root]) else "domain"
        return base

    agents, seen = [], set()
    for base in ("project", "domain", "shared", "user"):
        if not r.get(base):
            continue
        for name, f in _scan(Path(r[base]), allowed):
            if name in seen:
                continue
            seen.add(name)
            fm = _frontmatter(f) or {}
            eff = fm.get("effort", "").strip("\"' ").lower()
            agents.append({"name": name, "source": src(base, f), "model": _fm_model(fm), "effort": eff if eff in EFFORTS else None,
                           "description": _describe(fm.get("description"))})
    for name, desc in BUILTIN:
        if name not in seen:
            seen.add(name)
            agents.append({"name": name, "source": "builtin", "model": "inherit", "effort": None, "description": desc})

    skills, sseen = [], set()
    for base in ("project", "domain", "shared", "user"):
        if not r.get(base):
            continue
        for name, f in _scan(Path(r[base]).parent / "skills", allowed, skills=True):
            if name in sseen:
                continue
            sseen.add(name)
            skills.append({"name": name, "source": src(base, f.parent), "flow": name in FLOW_SKILLS,
                           "description": _describe((_frontmatter(f) or {}).get("description"))})

    local = [{"name": n, "description": d, "available": not (n == "local_ci" and domain == "WORK")} for n, d in LOCAL_TOOLS]
    out = {"agents": agents[:80], "skills": skills[:40], "local_tools": local, "generated_at": time.time()}
    # keep the body small: shorten descriptions, then drop the tail of the lists
    for step in range(3):
        if len(json.dumps(out)) <= 15000:
            break
        if step == 0:
            for lst in ("agents", "skills"):
                for x in out[lst]:
                    x["description"] = x["description"][:60]
        elif step == 1:
            out["skills"] = [x for x in out["skills"] if x["flow"]] + [x for x in out["skills"] if not x["flow"]][:5]
        else:
            out["agents"] = out["agents"][:40]
    return out
