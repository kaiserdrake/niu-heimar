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
CREATE TABLE IF NOT EXISTS design_snapshots (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, rel TEXT NOT NULL, sha TEXT NOT NULL,
  session_id TEXT, agent_type TEXT, tool TEXT, content TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS design_snapshots_rel ON design_snapshots(rel, ts);
CREATE TABLE IF NOT EXISTS remote_designs (
  host TEXT NOT NULL, rel TEXT NOT NULL, sha TEXT NOT NULL, mtime REAL, title TEXT, status TEXT,
  content TEXT NOT NULL, seen REAL, PRIMARY KEY(host, rel));
"""

SCHEMA_VERSION = 1  # PRAGMA user_version of events.db; < 1 -> add the multi-machine columns (additive only)

PROPOSALS_DIR = ".claude-proposals"  # <project>/.claude-proposals/*.md; web/app.js and hooks/agent-events.py keep a literal
DESIGN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.md")
DESIGN_MAX_BYTES = 512 * 1024
SNAPSHOT_KEEP = 40


def host_label() -> str:
    """Name shown for the workspace itself (default: this machine's hostname, e.g. Midgard)."""
    import socket
    name = os.environ.get("NIU_HOST_LABEL") or socket.gethostname().split(".")[0]
    return name[:1].upper() + name[1:] if name else "(workspace)"


def show_domain(d: str, host=None) -> str:
    """Display name of a domain; the (workspace) domain is named after the machine it lives on."""
    return (host or host_label()) if d == "(workspace)" else (d or "?")


def is_remote(x) -> bool:
    """True when a row, dict or session carries a non-empty host (it came from a paired machine, not this one)."""
    try:
        h = x["host"]
    except (KeyError, IndexError, TypeError):
        h = getattr(x, "host", None)
    return isinstance(h, str) and bool(h)


def _migrate(con):
    """Additive multi-machine migration; the hook path pays one PRAGMA read once the DB is migrated."""
    if con.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
        return
    for table, col, typ in (("events", "host", "TEXT"), ("events", "src_id", "INTEGER"),
                            ("design_snapshots", "host", "TEXT"), ("design_snapshots", "src_id", "INTEGER")):
        if col not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:  # another process added it first
                if col not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
                    raise
    con.executescript(
        "CREATE UNIQUE INDEX IF NOT EXISTS events_host_src ON events(host, src_id) WHERE host IS NOT NULL;"
        "CREATE INDEX IF NOT EXISTS events_host ON events(host, ts);"
        "CREATE UNIQUE INDEX IF NOT EXISTS design_snapshots_host_src ON design_snapshots(host, src_id) WHERE host IS NOT NULL;"
        "CREATE INDEX IF NOT EXISTS design_snapshots_host_rel ON design_snapshots(host, rel, ts);"
        f"PRAGMA user_version={SCHEMA_VERSION}")


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
        _migrate(con)
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
FLOW_SKILLS = ("develop", "dual-review", "document")
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


# ---------- design doc draft snapshots (written by hooks/agent-events.py) ----------

def read_regular(path, max_bytes=DESIGN_MAX_BYTES):
    """Text of a regular file (no symlink in the last component, no FIFO/device, size-capped), else None.

    Decoded as UTF-8 with replacement; control characters other than newline and tab are dropped.
    """
    import stat
    fd = None
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
            return None
        raw = b""
        while len(raw) <= max_bytes:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            raw += chunk
        if len(raw) > max_bytes:
            return None
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)
    text = raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))


def _in_proposals(file_path):
    fp = file_path.replace("\\", "/")
    return ("/" + PROPOSALS_DIR + "/") in fp or fp.startswith(PROPOSALS_DIR + "/")


def snapshot_design(con, file_path, project_dir, sid, agent_type, tool):
    """Store the current text of a .claude-proposals/*.md file written by an agent. True if a row was inserted.

    Skips anything else (other paths, outside the workspace, subfolders), identical repeats, and
    does nothing when NIU_DESIGN_SNAPSHOT_DAYS=0. Keeps the newest 40 per doc and prunes by age.
    """
    import hashlib
    try:
        days = float(os.environ.get("NIU_DESIGN_SNAPSHOT_DAYS", "30"))
    except ValueError:
        days = 30.0
    if days <= 0 or not isinstance(file_path, str) or not _in_proposals(file_path):
        return False
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(project_dir or os.getcwd()) / p
    try:
        p = p.resolve()
    except (OSError, RuntimeError):
        return False
    if (p.parent.name != PROPOSALS_DIR or not DESIGN_NAME_RE.fullmatch(p.name)
            or p.name.startswith("." + "env") or WS not in p.parents):
        return False
    text = read_regular(p)
    if text is None:
        return False
    rel = p.relative_to(WS).as_posix()
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with con:
        last = con.execute("SELECT sha FROM design_snapshots WHERE host IS NULL AND rel=? ORDER BY ts DESC, id DESC LIMIT 1", (rel,)).fetchone()
        if last and last[0] == sha:
            return False
        now = time.time()
        con.execute("INSERT INTO design_snapshots(ts,rel,sha,session_id,agent_type,tool,content) VALUES(?,?,?,?,?,?,?)",
                    (now, rel, sha, sid, agent_type or "", tool or "", text))
        con.execute("DELETE FROM design_snapshots WHERE host IS NULL AND rel=? AND id NOT IN "
                    "(SELECT id FROM design_snapshots WHERE host IS NULL AND rel=? ORDER BY ts DESC, id DESC LIMIT ?)",
                    (rel, rel, SNAPSHOT_KEEP))
        con.execute("DELETE FROM design_snapshots WHERE host IS NULL AND ts < ?", (now - days * 86400,))
    return True
