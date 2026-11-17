"""Design docs for the dashboard's Designs tab (stdlib only).

Lists docs/design/*.md of the workspace config and of linked projects, reads them safely, keeps their
revision history (git commits, working copy, draft snapshots from hooks/agent-events.py), turns markdown
into a JSON block tree (no HTML is ever produced) and diffs two revisions.

The browser never sends a path: it sends an opaque doc id that index() built itself. Every file is
checked when the index is built and again on every read.
"""
import bisect
import difflib
import hashlib
import json
import os
import re
import subprocess
import time
import unicodedata
from pathlib import Path

import niu_events as ne
from niu_events import redact
from niu_ollama import guard_allows

NAME_RE = ne.DESIGN_NAME_RE
MAX_BYTES = ne.DESIGN_MAX_BYTES
MAX_DOCS = 400
INDEX_TTL = 30.0
RE_REV = re.compile(r"work|git:[0-9a-f]{40}|snap:[0-9]{1,12}")
RE_DOC = re.compile(r"d[0-9a-f]{16}")
MAX_DEPTH = 8
MAX_BLOCKS = 5000
MAX_ROWS = 200
MAX_COLS = 20
MAX_DIFF_LINES = 5000
MAX_DIFF_OUT = 4000
GIT_LOG_MAX = 20

_INDEX_CACHE = {}
_GUARD_CACHE = {}
_REV_CACHE = {}
_BLOB_CACHE = {}


def _base(base=None):
    return Path(base) if base else ne.WS


def _clean(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))


def doc_id(rel: str) -> str:
    return "d" + hashlib.sha256(rel.encode("utf-8")).hexdigest()[:16]


# ---------- roots and index ----------

def linked_projects(base=None):
    """[(domain, project path relative to the domain, project Path)] for projects with .claude/settings.local.json.

    Same walk niu_prs.discover always did: non-dot domain folders, depth <= 7, no node_modules.
    """
    base = _base(base)
    out = []
    try:
        domains = sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return out
    for d in domains:
        for link in d.glob("**/.claude/settings.local.json"):
            if "node_modules" in link.parts or len(link.relative_to(d).parts) > 7:
                continue
            proj = link.parent.parent
            out.append((d.name, str(proj.relative_to(d)), proj))
    return out


def _under(p: Path, base: Path):
    return p == base or base in p.parents


def roots(extra_dirs=(), base=None):
    """[(domain, project, project_dir)]: the workspace config, linked projects, then extra (session) dirs under the workspace."""
    base = _base(base)
    out = [("(workspace)", "", str(base / ".claude-workspace"))]
    seen = {out[0][2]}
    for dom, rel, proj in linked_projects(base):
        if str(proj) not in seen:
            seen.add(str(proj))
            out.append((dom, rel, str(proj)))
    try:
        b = base.resolve()
    except OSError:
        return out
    for d in extra_dirs or ():
        try:
            p = Path(d).resolve()
        except (OSError, RuntimeError, ValueError, TypeError):
            continue
        if not _under(p, b) or str(p) in seen:
            continue
        rel = p.relative_to(b).parts
        if not rel or rel[0].startswith("."):
            dom, proj = "(workspace)", ""
        else:
            dom, proj = rel[0], "/".join(rel[1:])
        seen.add(str(p))
        out.append((dom, proj, str(p)))
    return out


def _design_dir(project_dir, base):
    """Resolved docs/design of a project, or None unless it sits exactly under the resolved project, inside the workspace."""
    try:
        b = base.resolve()
        pd = Path(project_dir).resolve()
        dd = (pd / "docs" / "design").resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not _under(pd, b) or dd != pd / "docs" / "design":
        return None
    return dd


def _doc_path(dd: Path, name: str):
    """Resolved regular-file path for docs/design/<name>, or None if it escapes, is odd, or is not a plain .md name."""
    if not NAME_RE.fullmatch(name) or name.lower().startswith(".env"):
        return None
    try:
        p = (dd / name).resolve()
    except (OSError, RuntimeError):
        return None
    if p.parent != dd or not NAME_RE.fullmatch(p.name) or p.name.lower().startswith(".env"):
        return None
    return p


def _guard_ok(path, project_dir, cache=False):
    if not cache:
        return guard_allows(path, project_dir)
    try:
        key = (str(path), os.stat(path).st_mtime_ns)
    except OSError:
        return False
    if key not in _GUARD_CACHE:
        if len(_GUARD_CACHE) > 4000:
            _GUARD_CACHE.clear()
        _GUARD_CACHE[key] = guard_allows(path, project_dir)
    return _GUARD_CACHE[key]


def _read_head(path, n=4096):
    fd = None
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        import stat
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            return None
        return _clean(os.read(fd, n).decode("utf-8", "replace"))
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)


def _title_status(head):
    title = status = ""
    for line in head.split("\n"):
        if not title and line.startswith("# "):
            title = line[2:].strip()[:200]
        if not status:
            m = re.search(r"\bStatus:\s*([^\s·|,;]+)", line)
            if m:
                status = m.group(1).strip("*_`.")[:20]
        if title and status:
            break
    return title, status


def _scan(domain, project, project_dir, base, out):
    dd = _design_dir(project_dir, base)
    if dd is None or not dd.is_dir():
        return
    try:
        b = base.resolve()
        with os.scandir(dd) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        return
    for e in entries:
        if len(out) >= MAX_DOCS:
            return
        if not NAME_RE.fullmatch(e.name) or e.name.lower().startswith(".env"):
            continue
        try:
            if not e.is_file(follow_symlinks=False) and not e.is_symlink():
                continue
        except OSError:
            continue
        p = _doc_path(dd, e.name)
        if p is None:
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        import stat as _stat
        if not _stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            continue
        if not _guard_ok(p, project_dir, cache=True):
            continue
        head = _read_head(p)
        if head is None:
            continue
        rel = (dd / e.name).relative_to(b).as_posix()
        title, status = _title_status(head)
        did = doc_id(rel)
        out[did] = {"id": did, "domain": domain, "project": project, "name": e.name, "title": title or e.name,
                    "status": status, "mtime": st.st_mtime, "rel": rel, "project_dir": str(Path(project_dir).resolve())}


def index(extra_dirs=(), base=None, force=False):
    """{docId: {id, domain, project, name, title, status, mtime, rel, project_dir}}, cached for 30 s.

    project_dir is for the server only: never put it in an API response.
    """
    base = _base(base)
    key = (tuple(sorted(str(d) for d in (extra_dirs or ()))), str(base))
    hit = _INDEX_CACHE.get(key)
    if hit and not force and time.time() - hit[0] < INDEX_TTL:
        return hit[1]
    out = {}
    for dom, proj, pdir in roots(extra_dirs, base):
        _scan(dom, proj, pdir, base, out)
    if len(_INDEX_CACHE) > 50:
        _INDEX_CACHE.clear()
    _INDEX_CACHE[key] = (time.time(), out)
    return out


def lookup(did, extra_dirs=(), base=None):
    """Entry for a doc id; rebuilds the index once when the id is unknown."""
    if not isinstance(did, str) or not RE_DOC.fullmatch(did):
        return None
    return index(extra_dirs, base).get(did) or index(extra_dirs, base, force=True).get(did)


def read_doc(entry, base=None):
    """Current text of a doc after re-validating everything, or None."""
    base = _base(base)
    try:
        dd = _design_dir(entry["project_dir"], base)
        if dd is None:
            return None
        p = _doc_path(dd, entry["name"])
        if p is None or not _guard_ok(p, entry["project_dir"]):
            return None
        return ne.read_regular(p, MAX_BYTES)
    except (KeyError, TypeError, OSError):
        return None


def link_resolver(entry, idx):
    """Callable name -> docId for sibling *.md links, to pass as parse(text, link_doc=...)."""
    prefix = entry["rel"].rsplit("/", 1)[0]

    def f(name):
        if not NAME_RE.fullmatch(name):
            return None
        d = doc_id(prefix + "/" + name)
        return d if d in idx else None
    return f


# ---------- revisions ----------

def _git(cwd, *args, timeout=30):
    """Hardened read-only git (like niu_ollama._git); returns text or None. Output beyond ~MAX_BYTES*2 is refused."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat")
    try:
        r = subprocess.run(["git", "-C", str(cwd), "-c", "core.fsmonitor=false", "-c", "diff.external=", *args],
                           capture_output=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or len(r.stdout) > MAX_BYTES * 2:
        return None
    return r.stdout.decode("utf-8", "replace")


def _git_revs(entry, dd, base):
    """[(sha, ts, subject, text)] newest first, from git history of the file (follows renames); [] outside a repo."""
    key = entry["rel"]
    hit = _REV_CACHE.get(key)
    if hit and time.time() - hit[0] < INDEX_TTL and hit[2] == entry.get("mtime"):
        return hit[1]
    out = []
    log = _git(dd, "log", "--follow", "-n", str(GIT_LOG_MAX), "--format=%H%x09%ct%x09%s", "--name-only", "--", entry["name"])
    commits, cur = [], None
    for line in (log or "").split("\n"):
        m = re.match(r"([0-9a-f]{40})\t(\d{1,12})\t(.*)$", line)
        if m:
            cur = [m.group(1), int(m.group(2)), m.group(3), None]
            commits.append(cur)
        elif line.strip() and cur is not None and cur[3] is None:
            cur[3] = line.strip()
    for sha, ts, subj, path in commits:
        if (not path or not path.endswith(".md") or "docs/design/" not in path or "\n" in path
                or ".." in path.split("/") or path.startswith("/")):
            continue
        bk = (sha, path, str(dd))
        text = _BLOB_CACHE.get(bk)
        if text is None:
            raw = _git(dd, "show", f"{sha}:{path}")
            if raw is None or len(raw.encode("utf-8", "replace")) > MAX_BYTES:
                continue
            text = _clean(raw)
            if len(_BLOB_CACHE) > 600:
                _BLOB_CACHE.clear()
            _BLOB_CACHE[bk] = text
        out.append((sha, ts, redact(subj)[:80], text))
    _REV_CACHE[key] = (time.time(), out, entry.get("mtime"))
    return out


def _snap_rows(entry, con):
    close = False
    if con is None:
        try:
            con = ne.connect(readonly=True)
            close = True
        except Exception:
            return []
    try:
        return [tuple(r) for r in con.execute(
            "SELECT id, ts, agent_type, content FROM design_snapshots WHERE rel=? ORDER BY ts DESC, id DESC LIMIT ?",
            (entry["rel"], ne.SNAPSHOT_KEEP))]
    except Exception:
        return []
    finally:
        if close:
            con.close()


def _snap_label(ts, agent):
    t = time.localtime(ts)
    when = time.strftime("%H:%M", t) if time.strftime("%F", t) == time.strftime("%F") else time.strftime("%m-%d %H:%M", t)
    return f"Draft {when} · {agent or 'main session'}"


def _collect(entry, con, base):
    """All known revisions, before dedup: [{rev, ts, kind, label, text}]."""
    items = []
    dd = _design_dir(entry["project_dir"], base)
    gits = _git_revs(entry, dd, base) if dd is not None else []
    for sha, ts, subj, text in gits:
        items.append({"rev": f"git:{sha}", "ts": ts, "kind": "git", "label": f"{sha[:7]} · {time.strftime('%Y-%m-%d', time.localtime(ts))} · {subj}", "text": text})
    text = read_doc(entry, base)
    if text is not None:
        try:
            mt = os.stat(Path(dd) / entry["name"]).st_mtime
        except OSError:
            mt = entry.get("mtime") or time.time()
        if gits:
            note = "" if gits[0][3] == text else " · uncommitted"
        elif dd is not None and _git(dd, "rev-parse", "--is-inside-work-tree") is not None:
            note = " · untracked"
        else:
            note = ""
        items.append({"rev": "work", "ts": mt, "kind": "work", "label": "Working copy" + note, "text": text})
    for sid_, ts, agent, content in _snap_rows(entry, con):
        items.append({"rev": f"snap:{sid_}", "ts": ts, "kind": "snap", "label": _snap_label(ts, agent), "text": content})
    return items


def revisions(entry, con=None, base=None):
    """[{rev, ts, label, kind, also}] newest first; equal contents collapse (git > work > snap), dropped labels go to `also`."""
    base = _base(base)
    groups = {}
    for it in _collect(entry, con, base):
        groups.setdefault(hashlib.sha256(it["text"].encode("utf-8")).hexdigest(), []).append(it)
    rank = {"git": 0, "work": 1, "snap": 2}
    out = []
    for g in groups.values():
        g.sort(key=lambda i: (rank[i["kind"]], -i["ts"]))
        top = g[0]
        out.append({"rev": top["rev"], "ts": max(i["ts"] for i in g), "label": top["label"], "kind": top["kind"],
                    "also": [i["label"] for i in g[1:]]})
    out.sort(key=lambda r: -r["ts"])
    return out


def revision_text(entry, rev, con=None, base=None):
    """Text of one revision (work | git:<sha> | snap:<id>), or None if the token is bad, unknown or unreadable."""
    base = _base(base)
    if not isinstance(rev, str) or not RE_REV.fullmatch(rev):
        return None
    if rev == "work":
        return read_doc(entry, base)
    if rev.startswith("snap:"):
        for sid_, _ts, _agent, content in _snap_rows(entry, con):
            if f"snap:{sid_}" == rev:
                return content
        return None
    dd = _design_dir(entry["project_dir"], base)
    if dd is None:
        return None
    for sha, _ts, _subj, text in _git_revs(entry, dd, base):
        if f"git:{sha}" == rev:
            return text
    return None


# ---------- sessions ----------

_RE_SUMMARY = re.compile(r"(^|/)docs/design/[^/]+\.md$")


def session_docs(con, sid, project_dir, idx):
    """Doc ids a session wrote (newest write first), from its Write/Edit event summaries, matched against idx."""
    if not sid or not project_dir:
        return []
    marks = ",".join("?" for _ in ne.EDIT_TOOLS)
    try:
        rows = con.execute(f"SELECT summary, project_dir FROM events WHERE session_id=? AND tool IN ({marks}) "
                           "ORDER BY id DESC LIMIT 500", (sid, *sorted(ne.EDIT_TOOLS))).fetchall()
    except Exception:
        return []
    rels = {e["rel"]: d for d, e in idx.items()}
    base = ne.WS
    try:
        b = base.resolve()
    except OSError:
        return []
    out = []
    for summary, pdir in rows:
        if not summary or not _RE_SUMMARY.search(summary) or not NAME_RE.fullmatch(summary.rsplit("/", 1)[-1]):
            continue
        try:
            p = Path(os.path.normpath(os.path.join(project_dir or pdir or "", summary))).resolve()
            rel = p.relative_to(b).as_posix()
        except (OSError, ValueError, RuntimeError):
            continue
        d = rels.get(rel)
        if d and d not in out:
            out.append(d)
    return out


# ---------- markdown -> block tree ----------

_TAGS = {"*": ("b", "i"), "_": ("b", "i"), "~": ("s", "s")}
_SPECIAL = re.compile(r"[\\`*_~\[\]!\n]")
_DEST = re.compile(r"""\(\s*(<[^<>\n]{0,2000}>|[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)(?:\s+(?:"[^"\n]{0,300}"|'[^'\n]{0,300}'))?\s*\)""")
_HREF_WEB = re.compile(r"https?://[^\s<>\"'\\\x00-\x1f]{1,2000}")
_HREF_FRAG = re.compile(r"#[A-Za-z0-9_-]{1,80}")
_HREF_DOC = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]{0,120}\.md)(#[A-Za-z0-9_-]{1,80})?")
_ASCII_PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")


def _is_ws(c):
    return c == "" or c.isspace()


def _is_punct(c):
    return c != "" and unicodedata.category(c)[0] in "PS"


class _Frame:
    __slots__ = ("d", "n", "img", "ch", "cd")

    def __init__(self, d, n, img=False):
        self.d, self.n, self.img, self.ch, self.cd = d, n, img, [], 0


def _plain(inl):
    if isinstance(inl, str):
        return inl
    if isinstance(inl, dict):
        t = inl.get("t")
        if t == "code":
            return inl.get("text", "")
        if t == "br":
            return " "
        if t == "img":
            return inl.get("alt", "")
        return _plain(inl.get("c", []))
    return "".join(_plain(x) for x in inl)


def _inline(s, link_doc=None):
    """Left-to-right scanner with a frame stack for emphasis and links; linear time, nesting depth <= 8."""
    s = s.strip()
    n = len(s)
    runs = {}
    for m in re.finditer(r"`+", s):
        runs.setdefault(len(m.group()), []).append(m.start())
    root = _Frame("", 0)
    stack = [root]
    opened = {"*": 0, "_": 0, "~": 0, "[": 0}

    def add(node, d=0):
        f = stack[-1]
        if isinstance(node, str):
            if not node:
                return
            if f.ch and isinstance(f.ch[-1], str):
                f.ch[-1] += node
            else:
                f.ch.append(node)
        else:
            f.ch.append(node)
            f.cd = max(f.cd, d)

    def flatten(f, parent):
        lit = ("![" if f.img else "[") if f.d == "[" else f.d * f.n
        for x in ([lit] if lit else []) + f.ch:
            if isinstance(x, str):
                if parent.ch and isinstance(parent.ch[-1], str):
                    parent.ch[-1] += x
                else:
                    parent.ch.append(x)
            else:
                parent.ch.append(x)
        parent.cd = max(parent.cd, f.cd)

    def pop_to(d):
        while stack[-1].d != d:
            f = stack.pop()
            opened[f.d] -= 1
            flatten(f, stack[-1])

    i = 0
    while i < n:
        m = _SPECIAL.search(s, i)
        if not m:
            add(s[i:])
            break
        if m.start() > i:
            add(s[i:m.start()])
        i = m.start()
        c = s[i]
        if c == "\\":
            nx = s[i + 1] if i + 1 < n else ""
            if nx == "\n":
                add({"t": "br"})
                i += 2
                while i < n and s[i] == " ":
                    i += 1
            elif nx in _ASCII_PUNCT and nx:
                add(nx)
                i += 2
            else:
                add("\\")
                i += 1
        elif c == "\n":
            f = stack[-1]
            if f.ch and isinstance(f.ch[-1], str) and f.ch[-1].endswith("  "):
                f.ch[-1] = f.ch[-1].rstrip(" ")
                add({"t": "br"})
            else:
                if f.ch and isinstance(f.ch[-1], str):
                    f.ch[-1] = f.ch[-1].rstrip(" ")
                add(" ")
            i += 1
            while i < n and s[i] == " ":
                i += 1
        elif c == "`":
            j = i
            while j < n and s[j] == "`":
                j += 1
            L = j - i
            starts = runs.get(L, [])
            k = bisect.bisect_left(starts, j)
            if k < len(starts):
                body = s[j:starts[k]].replace("\n", " ")
                if len(body) > 2 and body[0] == " " and body[-1] == " " and body.strip():
                    body = body[1:-1]
                add({"t": "code", "text": body}, 1)
                i = starts[k] + L
            else:
                add("`" * L)
                i = j
        elif c in "*_~":
            j = i
            while j < n and s[j] == c:
                j += 1
            k = j - i
            if c == "~" and k != 2:
                add(c * k)
                i = j
                continue
            prev = s[i - 1] if i else ""
            nxt = s[j] if j < n else ""
            left = not _is_ws(nxt) and (not _is_punct(nxt) or _is_ws(prev) or _is_punct(prev))
            right = not _is_ws(prev) and (not _is_punct(prev) or _is_ws(nxt) or _is_punct(nxt))
            if c == "_":
                can_open = left and (not right or _is_punct(prev))
                can_close = right and (not left or _is_punct(nxt))
            else:
                can_open, can_close = left, right
            rem = k
            while rem > 0 and can_close and opened[c] > 0:
                pop_to(c)
                f = stack[-1]
                m2 = 2 if (c == "~" or (f.n >= 2 and rem >= 2)) else 1
                if f.cd + 1 > MAX_DEPTH:
                    stack.pop()
                    opened[c] -= 1
                    flatten(f, stack[-1])
                    continue
                f.ch = [{"t": _TAGS[c][0 if m2 == 2 else 1], "c": f.ch}]
                f.cd += 1
                f.n -= m2
                rem -= m2
                if f.n <= 0:
                    stack.pop()
                    opened[c] -= 1
                    flatten(f, stack[-1])
            if rem > 0:
                if can_open:
                    stack.append(_Frame(c, rem))
                    opened[c] += 1
                else:
                    add(c * rem)
            i = j
        elif c == "[" or (c == "!" and s[i + 1:i + 2] == "["):
            img = c == "!"
            stack.append(_Frame("[", 1, img))
            opened["["] += 1
            i += 2 if img else 1
        elif c == "!":
            add("!")
            i += 1
        else:  # "]"
            if opened["["] == 0:
                add("]")
                i += 1
                continue
            pop_to("[")
            f = stack.pop()
            opened["["] -= 1
            dm = _DEST.match(s, i + 1)
            if not dm:
                flatten(f, stack[-1])
                add("]")
                i += 1
                continue
            i = dm.end()
            href = dm.group(1)
            if href.startswith("<") and href.endswith(">"):
                href = href[1:-1]
            if f.img:
                add({"t": "img", "alt": _plain(f.ch)[:200]}, 1)
                continue
            node = None
            if _HREF_WEB.fullmatch(href) or _HREF_FRAG.fullmatch(href):
                node = {"t": "a", "href": href, "c": f.ch}
            else:
                dmm = _HREF_DOC.fullmatch(href)
                did = link_doc(dmm.group(1)) if dmm and link_doc else None
                if did:
                    node = {"t": "a", "doc": did, "frag": (dmm.group(2) or "")[1:], "c": f.ch}
            if node and f.cd + 1 <= MAX_DEPTH:
                add(node, f.cd + 1)
            else:
                f.n = 0
                f.d = ""
                flatten(f, stack[-1])
    while len(stack) > 1:
        f = stack.pop()
        flatten(f, stack[-1])
    return root.ch


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEAD = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*))?$")
_HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_QUOTE = re.compile(r"^ {0,3}> ?")
_LIST = re.compile(r"^( *)([-+*]|\d{1,9}[.)])( +|$)(.*)$")
_DELIM = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")


class _St:
    def __init__(self, link_doc):
        self.link_doc = link_doc
        self.count = 0
        self.trunc = False
        self.cut = False
        self.slugs = {}


def _split_row(line):
    cells, cur, in_code, i, n = [], [], 0, 0, len(line)
    while i < n:
        c = line[i]
        if c == "\\" and i + 1 < n and line[i + 1] == "|":
            cur.append("|")
            i += 2
        elif c == "`":
            j = i
            while j < n and line[j] == "`":
                j += 1
            L = j - i
            if not in_code:
                in_code = L
            elif in_code == L:
                in_code = 0
            cur.append(line[i:j])
            i = j
        elif c == "|" and not in_code:
            cells.append("".join(cur).strip())
            cur = []
            i += 1
        else:
            cur.append(c)
            i += 1
    cells.append("".join(cur).strip())
    st = line.strip()
    if st.startswith("|") and cells and cells[0] == "":
        cells.pop(0)
    if st.endswith("|") and not st.endswith("\\|") and cells and cells[-1] == "":
        cells.pop()
    return cells


def _is_table(lines, i):
    if i + 1 >= len(lines) or "|" not in lines[i] or not _DELIM.match(lines[i + 1]):
        return False
    return len(_split_row(lines[i])) == len(_split_row(lines[i + 1])) > 0


def _interrupts(line, lines=None, i=0):
    """True if this line starts a block that may interrupt a paragraph."""
    fm = _FENCE.match(line)
    if fm and not (fm.group(1)[0] == "`" and "`" in fm.group(2)):
        return True
    if _HEAD.match(line) or _HR.match(line) or _QUOTE.match(line):
        return True
    m = _LIST.match(line)
    if m and m.group(4).strip() and (not m.group(2)[0].isdigit() or m.group(2)[:-1] == "1"):
        return True
    return bool(lines is not None and _is_table(lines, i))


def _slug(st, text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "section"
    k = st.slugs.get(s, 0) + 1
    st.slugs[s] = k
    return s if k == 1 else f"{s}-{k}"


def _blocks(lines, st, depth):
    out = []
    i, n = 0, len(lines)

    def push(b):
        if st.count >= MAX_BLOCKS:
            st.trunc = True
            return
        st.count += 1
        out.append(b)

    if depth > MAX_DEPTH:
        st.cut = True
        return out
    while i < n and not st.trunc:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        fm = _FENCE.match(line)
        if fm and not (fm.group(1)[0] == "`" and "`" in fm.group(2)):
            ch, ln = fm.group(1)[0], len(fm.group(1))
            lang = re.sub(r"[^A-Za-z0-9_+.#-]", "", (fm.group(2).split() or [""])[0])[:20]
            body = []
            i += 1
            while i < n:
                cm = re.match(r"^ {0,3}(" + re.escape(ch) + "{" + str(ln) + r",})[ \t]*$", lines[i])
                i += 1
                if cm:
                    break
                body.append(lines[i - 1])
            push({"t": "code", "lang": lang, "text": "\n".join(body)})
            continue
        hm = _HEAD.match(line)
        if hm:
            text = re.sub(r"[ \t]+#+[ \t]*$", "", (hm.group(2) or "")).strip()
            if re.fullmatch(r"#+", text):
                text = ""
            inl = _inline(text, st.link_doc)
            push({"t": "h", "l": len(hm.group(1)), "id": _slug(st, _plain(inl)), "c": inl})
            i += 1
            continue
        if _HR.match(line):
            push({"t": "hr"})
            i += 1
            continue
        if _QUOTE.match(line):
            inner = []
            while i < n:
                if _QUOTE.match(lines[i]):
                    inner.append(_QUOTE.sub("", lines[i], count=1))
                elif lines[i].strip() and inner and inner[-1].strip() and not _interrupts(lines[i]):
                    inner.append(lines[i].lstrip())
                else:
                    break
                i += 1
            push({"t": "quote", "c": _blocks(inner, st, depth + 1)})
            continue
        if _is_table(lines, i):
            head = _split_row(line)
            align = []
            for c in _split_row(lines[i + 1]):
                l_, r_ = c.startswith(":"), c.endswith(":")
                align.append("c" if l_ and r_ else "l" if l_ else "r" if r_ else "")
            cols = len(head)
            if cols > MAX_COLS:
                st.cut = True
                cols = MAX_COLS
            rows = []
            i += 2
            while i < n and lines[i].strip() and "|" in lines[i] and not _interrupts(lines[i]):
                if len(rows) >= MAX_ROWS:
                    st.cut = True
                    i += 1
                    continue
                cells = _split_row(lines[i])[:cols]
                cells += [""] * (cols - len(cells))
                rows.append([_inline(c, st.link_doc) for c in cells])
                i += 1
            push({"t": "table", "align": align[:cols] + [""] * (cols - len(align)), "head": [_inline(c, st.link_doc) for c in head[:cols]], "rows": rows})
            continue
        lm = _LIST.match(line)
        if lm:
            i = _list(lines, i, st, depth, push)
            continue
        para = [line.lstrip()]
        i += 1
        while i < n and lines[i].strip() and not _interrupts(lines[i], lines, i):
            para.append(lines[i].lstrip())
            i += 1
        push({"t": "p", "c": _inline("\n".join(para), st.link_doc)})
    return out


def _list(lines, i, st, depth, push):
    n = len(lines)
    first = _LIST.match(lines[i])
    base = len(first.group(1))
    ordered = first.group(2)[0].isdigit()
    start = int(first.group(2)[:-1]) if ordered else 1
    items = []
    while i < n and not st.trunc:
        m = _LIST.match(lines[i])
        if not m or len(m.group(1)) < base or m.group(2)[0].isdigit() != ordered:
            break
        rest, spaces = m.group(4), len(m.group(3))
        ci = len(m.group(1)) + len(m.group(2)) + (1 if (not rest or spaces > 4) else spaces)
        if spaces > 4 and rest:
            rest = " " * (spaces - 1) + rest
        item = [rest]
        i += 1
        while i < n:
            ln = lines[i]
            if not ln.strip():
                j = i
                while j < n and not lines[j].strip():
                    j += 1
                if j >= n or len(lines[j]) - len(lines[j].lstrip(" ")) < ci:
                    break
                item.extend([""] * (j - i))
                i = j
                continue
            ind = len(ln) - len(ln.lstrip(" "))
            if ind >= ci:
                item.append(ln[ci:])
            elif _LIST.match(ln) or _interrupts(ln) or not item[-1].strip():
                break
            else:
                item.append(ln.lstrip())
            i += 1
        items.append(_blocks(item, st, depth + 1))
        j = i
        while j < n and not lines[j].strip():
            j += 1
        nm = _LIST.match(lines[j]) if j < n else None
        if nm and len(nm.group(1)) >= base and nm.group(2)[0].isdigit() == ordered and not _HR.match(lines[j]):
            i = j
        else:
            break
    b = {"t": "ol" if ordered else "ul", "items": items}
    if ordered:
        b["start"] = start
    push(b)
    return i


def parse(text, link_doc=None):
    """Markdown -> list of block dicts (see the design doc); raw HTML stays literal text, nothing here makes HTML."""
    st = _St(link_doc)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4).split("\n")
    blocks = _blocks(lines, st, 0)
    if st.trunc or st.cut:
        blocks.append({"t": "p", "c": ["… (truncated)"]})
    return blocks


def outline(blocks):
    """[{l, id, text}] for every heading (also inside quotes and lists), in document order."""
    out = []

    def walk(bs):
        for b in bs:
            t = b.get("t")
            if t == "h":
                out.append({"l": b["l"], "id": b["id"], "text": _plain(b["c"])})
            elif t == "quote":
                walk(b["c"])
            elif t in ("ul", "ol"):
                for it in b["items"]:
                    walk(it)
    walk(blocks)
    return out


# ---------- diff ----------

def _line_diff(a, b, autojunk):
    sm = difflib.SequenceMatcher(None, a, b, autojunk=autojunk)
    ops = sm.get_opcodes()
    added = sum(j2 - j1 for t, _i1, _i2, j1, j2 in ops if t in ("insert", "replace"))
    removed = sum(i2 - i1 for t, i1, i2, _j1, _j2 in ops if t in ("delete", "replace"))
    if all(t == "equal" for t, *_ in ops):
        return [], 0, 0
    out = []
    last = len(ops) - 1
    for k, (t, i1, i2, j1, j2) in enumerate(ops):
        if t == "equal":
            run = i2 - i1
            head = 0 if k == 0 else 3
            tail = 0 if k == last else 3
            if run <= head + tail:
                spans = [(0, run)]
            else:
                spans = [(0, head), None, (run - tail, run)]
            for sp in spans:
                if sp is None:
                    out.append({"op": "skip", "n": run - head - tail})
                    continue
                for o in range(*sp):
                    out.append({"op": "eq", "a": i1 + o + 1, "b": j1 + o + 1, "text": a[i1 + o]})
        else:
            for x in range(i1, i2):
                out.append({"op": "del", "a": x + 1, "b": None, "text": a[x]})
            for y in range(j1, j2):
                out.append({"op": "ins", "a": None, "b": y + 1, "text": b[y]})
    if len(out) > MAX_DIFF_OUT:
        rest = len(out) - MAX_DIFF_OUT
        out = out[:MAX_DIFF_OUT] + [{"op": "skip", "n": rest}]
    return out, added, removed


def _words(a, b):
    ta, tb = re.findall(r"\s+|\S+", a), re.findall(r"\s+|\S+", b)
    if len(ta) > 3000 or len(tb) > 3000:
        return [["del", a], ["ins", b]]
    out = []

    def put(op, text):
        if text:
            if out and out[-1][0] == op:
                out[-1][1] += text
            else:
                out.append([op, text])
    for t, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if t == "equal":
            put("eq", "".join(ta[i1:i2]))
        else:
            put("del", "".join(ta[i1:i2]))
            put("ins", "".join(tb[j1:j2]))
    return out


def _key(x):
    return json.dumps(x, sort_keys=True)


def _seq_ops(old, new):
    """(ops per new element, [{at, item}] for deleted old elements) from a SequenceMatcher on json keys."""
    ops, gone = [], []
    for t, i1, i2, j1, j2 in difflib.SequenceMatcher(None, [_key(x) for x in old], [_key(x) for x in new], autojunk=False).get_opcodes():
        if t == "equal":
            ops += ["eq"] * (j2 - j1)
        else:
            gone += [{"at": len(ops), "item": old[x]} for x in range(i1, i2)]
            ops += ["ins"] * (j2 - j1)
    return ops, gone


def _chg(old, new):
    t = new.get("t")
    if old.get("t") != t:
        return None
    if t in ("p", "h"):
        return {"op": "chg", "block": new, "words": _words(_plain(old["c"]), _plain(new["c"]))}
    if t == "table" and len(old["head"]) == len(new["head"]):
        rows, gone = _seq_ops(old["rows"], new["rows"])
        r = {"op": "chg", "block": new, "rows": rows, "old_rows": [{"at": g["at"], "cells": g["item"]} for g in gone]}
        if old["head"] != new["head"]:
            r["old_head"] = old["head"]
        return r
    if t in ("ul", "ol"):
        items, gone = _seq_ops(old["items"], new["items"])
        return {"op": "chg", "block": new, "items": items, "old_items": [{"at": g["at"], "item": g["item"]} for g in gone]}
    return None


def _block_diff(a, b):
    out, sections = [], []
    cur = [None]

    def mark(blk):
        if blk.get("t") == "h" and blk["l"] <= 3:
            name = _plain(blk["c"])
        else:
            name = cur[0]
        if name and name not in sections:
            sections.append(name)

    def track(blk):
        if blk.get("t") == "h" and blk["l"] <= 3:
            cur[0] = _plain(blk["c"])

    for t, i1, i2, j1, j2 in difflib.SequenceMatcher(None, [_key(x) for x in a], [_key(x) for x in b], autojunk=False).get_opcodes():
        if t == "equal":
            for blk in b[j1:j2]:
                track(blk)
                out.append({"op": "eq", "block": blk})
            continue
        olds, news = a[i1:i2], b[j1:j2]
        for k in range(max(len(olds), len(news))):
            o = olds[k] if k < len(olds) else None
            nw = news[k] if k < len(news) else None
            ch = _chg(o, nw) if o is not None and nw is not None else None
            if ch:
                track(nw)
                mark(nw)
                out.append(ch)
                continue
            if o is not None:
                mark(o)
                out.append({"op": "del", "block": o})
            if nw is not None:
                track(nw)
                mark(nw)
                out.append({"op": "ins", "block": nw})
    return out, sections


def diff(a_text, b_text):
    """{stats: {added, removed, sections}, lines, blocks, note?} between two texts (a = older, b = newer).

    lines: [{op: eq|del|ins, a, b, text} | {op: skip, n}] with 3 lines of context.
    blocks: [{op: eq|ins|del|chg, block, ...}] or None when either side has over 5,000 lines.
    """
    a, b = a_text.split("\n"), b_text.split("\n")
    big = len(a) > MAX_DIFF_LINES or len(b) > MAX_DIFF_LINES
    lines, added, removed = _line_diff(a, b, autojunk=big)
    stats = {"added": added, "removed": removed, "sections": []}
    if big:
        return {"stats": stats, "lines": lines, "blocks": None, "note": "too large for the rendered diff"}
    blocks, stats["sections"] = _block_diff(parse(a_text), parse(b_text))
    return {"stats": stats, "lines": lines, "blocks": blocks}
