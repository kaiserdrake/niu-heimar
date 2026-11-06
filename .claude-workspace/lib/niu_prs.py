"""Open pull requests for the workspace repo and linked GitHub projects (stdlib + gh CLI).

Merging is deliberately user-only: it needs an interactive terminal and a typed "y",
and is refused unless every check passed and GitHub reports the PR as mergeable.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from niu_events import WS, db_path

GH_FIELDS = "number,title,author,headRefName,url,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,updatedAt,files"
SCOPES = None  # gh token scopes, filled by fetch_all() (None = unknown)
OK_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
GH_RE = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def cache_path() -> Path:
    return db_path().parent / "prs.json"


def _git_origin(path: Path):
    try:
        out = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = GH_RE.search(out.stdout.strip()) if out.returncode == 0 else None
    return f"{m.group(1)}/{m.group(2)}" if m else None


def discover():
    """Return [(label, owner/repo)] for the workspace repo and linked GitHub projects."""
    found = {}
    ws_repo = _git_origin(WS)
    if ws_repo:
        found[ws_repo] = "(workspace)"
    for d in sorted(p for p in WS.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for link in d.glob("**/.claude/settings.local.json"):
            if "node_modules" in link.parts or len(link.relative_to(d).parts) > 7:
                continue
            proj = link.parent.parent
            repo = _git_origin(proj)
            if repo and repo not in found:
                found[repo] = f"{d.name}/{proj.relative_to(d)}"
    return [(label, repo) for repo, label in found.items()]


def check_summary(pr):
    passed = failed = pending = 0
    for c in pr.get("statusCheckRollup") or []:
        if c.get("__typename") == "StatusContext" or c.get("state"):
            st = (c.get("state") or "").upper()
            if st == "SUCCESS":
                passed += 1
            elif st in ("PENDING", "EXPECTED", ""):
                pending += 1
            else:
                failed += 1
        elif (c.get("status") or "").upper() != "COMPLETED":
            pending += 1
        elif (c.get("conclusion") or "").upper() in OK_CONCLUSIONS:
            passed += 1
        else:
            failed += 1
    return passed, failed, pending


def touches_workflows(pr):
    return any((f.get("path") or "").startswith(".github/workflows/") for f in pr.get("files") or [])


def is_green(pr, scopes=None):
    """(ok, reason). Green = not draft, mergeable, no failing or pending checks."""
    scopes = SCOPES if scopes is None else scopes
    passed, failed, pending = check_summary(pr)
    if pr.get("isDraft"):
        return False, "draft"
    if failed:
        return False, f"{failed} check(s) failing"
    if pending:
        return False, f"{pending} check(s) pending"
    if pr.get("mergeable") != "MERGEABLE":
        return False, f"not mergeable ({(pr.get('mergeable') or 'unknown').lower()})"
    if touches_workflows(pr) and scopes is not None and "workflow" not in scopes:
        return False, "gh token lacks 'workflow' scope (PR edits workflows): run gh auth refresh -s workflow, or merge on GitHub"
    if pr.get("mergeStateStatus") in ("BLOCKED", "DIRTY", "BEHIND"):
        return False, f"merge state {pr['mergeStateStatus'].lower()}"
    return True, "ready" if passed else "ready (no checks)"


def _gh(args, timeout=30):
    if not shutil.which("gh"):
        raise RuntimeError("gh is not installed")
    out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout,
                         cwd=tempfile.gettempdir())
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip().splitlines()[-1] if (out.stderr or out.stdout).strip() else "gh failed")
    return out.stdout


def token_scopes():
    """Scopes of the gh token, or None when they can't be read."""
    try:
        out = _gh(["api", "-i", "user"], timeout=15)
    except (RuntimeError, subprocess.TimeoutExpired, OSError):
        return None
    for line in out.splitlines():
        if line.lower().startswith("x-oauth-scopes:"):
            return {x.strip() for x in line.split(":", 1)[1].split(",") if x.strip()}
    return None


def fetch_all():
    """Query gh for every discovered repo, write the cache, return it."""
    global SCOPES
    SCOPES = token_scopes()
    data = {"fetched": time.time(), "repos": [], "error": None, "scopes": sorted(SCOPES) if SCOPES is not None else None}
    try:
        repos = discover()
    except OSError as e:
        repos, data["error"] = [], str(e)
    for label, repo in repos:
        entry = {"label": label, "repo": repo, "prs": [], "error": None}
        try:
            entry["prs"] = json.loads(_gh(["pr", "list", "-R", repo, "--state", "open", "--limit", "30", "--json", GH_FIELDS]))
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            entry["error"] = str(e)[:120]
        data["repos"].append(entry)
    p = cache_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    return data


def load_cache():
    global SCOPES
    try:
        data = json.loads(cache_path().read_text())
    except (OSError, ValueError):
        return None
    if SCOPES is None and data.get("scopes") is not None:
        SCOPES = set(data["scopes"])
    return data


def flat(cache):
    """[(label, repo, pr)] across repos, newest first per repo."""
    rows = []
    for r in (cache or {}).get("repos", []):
        for pr in sorted(r["prs"], key=lambda p: p.get("updatedAt") or "", reverse=True):
            rows.append((r["label"], r["repo"], pr))
    return rows


def parse_ref(ref):
    m = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", ref.strip())
    if not m:
        raise ValueError("expected OWNER/REPO#NUMBER, e.g. kaiserdrake/niu-heimar#4")
    return m.group(1), int(m.group(2))


def fresh(repo, number):
    return json.loads(_gh(["pr", "view", str(number), "-R", repo, "--json", GH_FIELDS]))


def can_prompt():
    try:
        return os.isatty(sys.stdin.fileno()) and os.isatty(sys.stdout.fileno())
    except (OSError, ValueError):
        return False


def merge(repo, number):
    """Re-check the PR live, then squash-merge it and delete the branch. Returns a message."""
    global SCOPES
    if SCOPES is None:
        SCOPES = token_scopes()
    pr = fresh(repo, number)
    ok, reason = is_green(pr)
    if not ok:
        raise RuntimeError(f"{repo}#{number} not merged: {reason}")
    _gh(["pr", "merge", str(number), "-R", repo, "--squash", "--delete-branch"], timeout=60)
    return f"merged {repo}#{number}: {pr.get('title', '')[:60]}"


def summarize(cache, refreshing=False):
    """JSON-ready PR list with check counts and the green/blocked verdict per PR."""
    cache = cache or {"fetched": 0, "repos": []}
    repos = []
    for r in cache.get("repos", []):
        items = []
        for pr in r.get("prs", []):
            passed, failed, pending = check_summary(pr)
            ok, reason = is_green(pr)
            items.append({"number": pr["number"], "title": pr.get("title", ""), "url": pr.get("url"),
                          "author": (pr.get("author") or {}).get("login"), "branch": pr.get("headRefName"),
                          "draft": pr.get("isDraft"), "updated": pr.get("updatedAt"),
                          "review": pr.get("reviewDecision") or None,
                          "checks": {"passed": passed, "failed": failed, "pending": pending},
                          "green": ok, "reason": reason, "ref": f"{r['repo']}#{pr['number']}",
                          "needs_scope": "workflow" if "'workflow' scope" in reason else None})
        repos.append({"label": r["label"], "repo": r["repo"], "error": r.get("error"), "prs": items})
    return {"fetched": cache.get("fetched", 0), "refreshing": refreshing, "error": cache.get("error"), "repos": repos,
            "scopes": cache.get("scopes")}
