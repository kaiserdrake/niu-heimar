"""Ollama endpoints for niu-heimar local agents (stdlib only).

Endpoint config: .claude-workspace/ollama.json (local, gitignored) or ollama.template.json (defaults).
URLs can be overridden with OLLAMA_REMOTE_BASE_URL / OLLAMA_LOCAL_BASE_URL.
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from niu_events import WS, db_path, locate

SHARED = WS / ".claude-workspace"
GUARD = SHARED / "hooks" / "block-env.sh"
HEALTH_TTL = 30.0


def load_config():
    paths = [Path(os.environ["NIU_OLLAMA_CONFIG"])] if os.environ.get("NIU_OLLAMA_CONFIG") else []
    for p in paths + [SHARED / "ollama.json", SHARED / "ollama.template.json"]:
        if p.exists():
            cfg = json.loads(p.read_text())
            break
    else:
        cfg = {"endpoints": {}}
    for name, ep in cfg.get("endpoints", {}).items():
        ep["name"] = name
        env = ep.get("url_env")
        if env and os.environ.get(env):
            ep["url"] = os.environ[env]
        ep["url"] = ep["url"].rstrip("/")
    return cfg


def _http(url, body=None, timeout=5.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _health_cache_path():
    return db_path().parent / "ollama-health.json"


def health(ep, force=False):
    """{'up': bool, 'version': str|None, 'models': [names], 'checked': ts, 'error': str|None} (cached HEALTH_TTL s)."""
    cache_p = _health_cache_path()
    try:
        cache = json.loads(cache_p.read_text())
    except (OSError, ValueError):
        cache = {}
    hit = cache.get(ep["url"])
    if hit and not force and time.time() - hit.get("checked", 0) < HEALTH_TTL:
        return hit
    res = {"up": False, "version": None, "models": [], "checked": time.time(), "error": None}
    try:
        res["version"] = _http(ep["url"] + "/api/version", timeout=2.0).get("version")
        res["models"] = [m["name"] for m in _http(ep["url"] + "/api/tags", timeout=3.0).get("models", [])]
        res["up"] = True
    except (urllib.error.URLError, OSError, ValueError) as e:
        res["error"] = str(getattr(e, "reason", e))[:120]
    cache[ep["url"]] = res
    try:
        cache_p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        cache_p.write_text(json.dumps(cache))
    except OSError:
        pass
    return res


def _has_model(models, name):
    return name in models or (":" not in name and f"{name}:latest" in models)


def candidates(task, domain=None, cfg=None, only=None):
    cfg = cfg or load_config()
    eps = sorted(cfg.get("endpoints", {}).values(), key=lambda e: e.get("priority", 99))
    out = []
    for ep in eps:
        if only and ep["name"] != only:
            continue
        allowed = ep.get("allowed_domains", ["*"])
        if domain and "*" not in allowed and domain not in allowed:
            continue
        model = ep.get("models", {}).get(task)
        if model:
            out.append((ep, model))
    return out


def choose(task, domain=None, cfg=None, only=None):
    """First healthy endpoint that has the model for this task: (endpoint, model, notes)."""
    notes = []
    for ep, model in candidates(task, domain, cfg, only):
        h = health(ep)
        if not h["up"]:
            notes.append(f"{ep['name']} ({ep['url']}) unreachable: {h['error']}")
            continue
        if not _has_model(h["models"], model):
            notes.append(f"{ep['name']} has no model {model}")
            continue
        return ep, model, notes
    return None, None, notes


def status():
    cfg = load_config()
    out = []
    for ep in sorted(cfg.get("endpoints", {}).values(), key=lambda e: e.get("priority", 99)):
        h = health(ep)
        out.append({"name": ep["name"], "label": ep.get("label", ep["name"]), "url": ep["url"], "mode": ep.get("mode", "agent"),
                    "up": h["up"], "version": h["version"], "error": h["error"],
                    "models": {t: {"name": m, "present": _has_model(h["models"], m)} for t, m in ep.get("models", {}).items()}})
    return out


# ---------- guard reuse: same rules as the PreToolUse hook ----------

def guard_allows(path, cwd):
    """True if the workspace guard would let an agent Read this path (dotenv + protected-paths rules)."""
    payload = json.dumps({"tool_name": "Read", "cwd": str(cwd), "tool_input": {"file_path": str(path)}})
    try:
        r = subprocess.run(["bash", str(GUARD)], input=payload, text=True, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def _git(cwd, *args, timeout=30):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout)
    return r.stdout if r.returncode == 0 else ""


def default_base(cwd):
    for ref in ("origin/HEAD", "origin/main", "main"):
        mb = _git(cwd, "merge-base", "HEAD", ref).strip()
        if mb:
            return mb
    return "HEAD"


# ---------- lite mode: gather context ourselves, one /api/chat call ----------

def _keywords(query):
    import re
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_./-]{2,}", query)
    stop = {"the", "and", "for", "where", "what", "which", "how", "are", "used", "use", "does", "with", "from", "this", "that", "into", "find"}
    seen, out = set(), []
    for w in sorted(words, key=len, reverse=True):
        lw = w.lower()
        if lw in stop or lw in seen:
            continue
        seen.add(lw)
        out.append(w)
    return out[:5]


def lite_context(task, cwd, query="", base=None, files=None, limit=14000):
    cwd = Path(cwd)
    parts, used = [], 0

    def add(header, body):
        nonlocal used
        chunk = f"### {header}\n{body.rstrip()}\n"
        if used + len(chunk) > limit:
            chunk = chunk[: max(0, limit - used)] + "\n[truncated]\n"
        parts.append(chunk)
        used += len(chunk)
        return used < limit

    if task == "diff":
        base = base or default_base(cwd)
        names = [n for n in _git(cwd, "diff", "--name-only", base).splitlines() if n]
        for n in names:
            if not guard_allows(cwd / n, cwd):
                add(n, "[skipped: protected path]")
                continue
            if not add(f"diff {n}", _git(cwd, "diff", base, "--", n)):
                break
        if not names:
            add("diff", "(no changes against base)")
    elif task == "search":
        allowed_cache = {}
        for kw in _keywords(query):
            hits = _git(cwd, "grep", "-n", "-I", "-i", "-F", "--max-count=6", "-e", kw).splitlines()[:40]
            keep = []
            for h in hits:
                f = h.split(":", 1)[0]
                if f not in allowed_cache:
                    allowed_cache[f] = guard_allows(cwd / f, cwd)
                if allowed_cache[f]:
                    keep.append(h[:240])
            if keep and not add(f"git grep '{kw}'", "\n".join(keep)):
                break
        if not parts:
            add("search", "(no matches in tracked files)")
    else:  # ask
        for f in files or []:
            p = (cwd / f) if not os.path.isabs(f) else Path(f)
            if not guard_allows(p, cwd):
                add(f, "[skipped: protected path]")
                continue
            try:
                if not add(f, p.read_text(errors="replace")[:6000]):
                    break
            except OSError as e:
                add(f, f"[unreadable: {e}]")
    return "".join(parts)


LITE_SYSTEM = {
    "search": "You answer code-search questions using ONLY the grep results given. Cite file:line for every claim. "
              "If the results don't answer the question, say what to search for next. Be concise.",
    "diff": "You review a git diff. List concrete findings (bugs, risky changes, missing tests), each with file:line and a "
            "one-line fix. Say 'no findings' if there are none. Do not restate the diff.",
    "ask": "Answer the question using the files given. Cite file:line. Be concise.",
}


def lite_run(ep, model, task, prompt, context, timeout):
    body = {"model": model, "stream": False, "options": {"temperature": 0.1},
            "messages": [{"role": "system", "content": LITE_SYSTEM[task]},
                         {"role": "user", "content": f"{prompt}\n\n{context}"}]}
    res = _http(ep["url"] + "/api/chat", body, timeout=timeout)
    return (res.get("message") or {}).get("content", "").strip()


def domain_of(cwd):
    return locate(str(cwd))[0]
