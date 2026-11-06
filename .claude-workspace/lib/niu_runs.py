"""Fixed, read-only command runners for niu-local digest tasks (logs / ci / git). Stdlib only.

The SERVER runs commands from the fixed argv templates below (no shell, validated arguments); the local model only
ever sees the cleaned, redacted text. See docs/design/2026-11-06-niu-local-run-tools.md.
"""
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
from pathlib import Path

import niu_ollama as no

RUN_TASKS = {"logs", "ci", "git"}
HOOKS = no.SHARED / "hooks"
DEFAULT_TIMEOUTS = {"docker": 20, "gh": 60, "git": 30}
DEFAULT_CAP_BYTES = 2_000_000


class RunError(Exception):
    pass


# ---------- output cleaning ----------

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]{0,4096}(?:\x07|\x1b\\)|[\x00-\x08\x0b-\x1f\x7f]")


def strip_ansi(s):
    return ANSI.sub("", s)


# ---------- redaction ----------

_KEYS = (r"[A-Z0-9_.-]{0,40}(?:pass(?:word|wd)?|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential|auth|"
         r"session|dsn|conn(?:ection)?[_-]?str(?:ing)?)[A-Z0-9_.-]{0,40}")
_TOKENS = [
    r"gh[pousr]_[A-Za-z0-9]{36,}", r"github_pat_[A-Za-z0-9_]{50,}", r"glpat-[A-Za-z0-9_-]{20,}",
    r"sk-ant-[A-Za-z0-9_-]{20,}", r"sk-[A-Za-z0-9_-]{20,}", r"xox[abposr]-[A-Za-z0-9-]{10,}",
    r"(?:A3T|AKIA|ASIA)[A-Z0-9]{16}", r"AIza[0-9A-Za-z_-]{35}", r"hf_[A-Za-z0-9]{30,}", r"npm_[A-Za-z0-9]{36}",
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
]
RE_PEM = re.compile(r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----(?:(?!-----BEGIN )[\s\S]){0,20000}?-----END [A-Z ]{0,40}PRIVATE KEY-----")
RE_AUTH = re.compile(r"(?i)\b(authorization|proxy-authorization|x-api-key|cookie|set-cookie)\s*[:=]\s*\S.*")
RE_URLCRED = re.compile(r"([a-z][a-z0-9+.-]{0,30}://)[^/\s:@]{0,256}:[^/\s]{0,512}@", re.I)
RE_TOKEN = re.compile("|".join(_TOKENS))
RE_KV_HEAD = re.compile(r"(?i)(?<![A-Z0-9_.-])([A-Z0-9_.-]{1,200}+)([\"']?\s*(?:=>|[=:])\s*)")
RE_KV_VAL = re.compile(r"\"[^\"]*\"|'[^']*'|\S+")
RE_KW = re.compile(r"pass(?:word|wd)?|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential|auth|session|dsn|"
                   r"conn(?:ection)?[_-]?str(?:ing)?", re.I)
RE_PEM_OPEN = re.compile(r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----(?:\n[+\- ]?[A-Za-z0-9+/=]{16,200}){0,500}")
RE_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
RE_PGPASS = re.compile(r"(?i)\b(PGPASSWORD|MYSQL_PWD)\s+\S+")
RE_ENVLINE = re.compile(r"(?m)^([ \t]*(?:[\d:.TZ+-]+[ \t]+)?(?:export[ \t]+)?[A-Z][A-Z0-9_]{2,})=(\S+)")
RE_ENTROPY = re.compile(r"[A-Za-z0-9+/_=-]{40,}")
RE_KEYNAME = re.compile(_KEYS, re.I)
MAX_LINE = 8000
SKIP_VALUES = {"", "null", "none", "true", "false", '""', "''"}


def _sub_count(rx, repl, text, counter):
    def f(m):
        counter[0] += 1
        return repl(m) if callable(repl) else repl
    return rx.sub(f, text)


def _kv_repl(m):
    v = m.group(3)
    if v.lower() in SKIP_VALUES or v.startswith("[REDACTED"):
        return m.group(0)
    return f"{m.group(1)}{m.group(2)}[REDACTED:value]"


def _redact_kv(text, n):
    """Redact values of secret-looking keys. Only secret keys read a value, so scanning stays linear
    and a non-secret key never hides a later secret (url=...?password=x is still caught)."""
    out, pos = [], 0
    while True:
        m = RE_KV_HEAD.search(text, pos)
        if not m:
            break
        v = RE_KV_VAL.match(text, m.end()) if RE_KW.search(m.group(1)) else None
        if not v:
            out.append(text[pos:m.end()])
            pos = m.end()
            continue
        val = v.group(0)
        if val.lower() in SKIP_VALUES or val.startswith("[REDACTED"):
            out.append(text[pos:v.end()])
        else:
            n[0] += 1
            out.append(text[pos:m.end()] + "[REDACTED:value]")
        pos = v.end()
    out.append(text[pos:])
    return "".join(out)


def redact(text, entropy=True):
    """Return (text, number_of_redactions). Order matters; see the design doc."""
    n = [0]
    # Bound regex work: nobody needs 8 KB lines summarized, and long attacker-controlled runs are the ReDoS surface.
    if len(text) > MAX_LINE:
        text = "\n".join(l if len(l) <= MAX_LINE else l[:MAX_LINE] + " [line truncated]" for l in text.split("\n"))
    text = _sub_count(RE_PEM, "[REDACTED:private-key]", text, n)
    text = _sub_count(RE_PEM_OPEN, "[REDACTED:private-key]", text, n)
    text = _sub_count(RE_AUTH, lambda m: f"{m.group(1)}: [REDACTED:header]", text, n)
    text = _sub_count(RE_URLCRED, lambda m: f"{m.group(1)}[REDACTED]@", text, n)
    text = _sub_count(RE_TOKEN, "[REDACTED:token]", text, n)
    text = _sub_count(RE_BEARER, lambda m: f"{m.group(1)} [REDACTED:token]", text, n)
    text = _sub_count(RE_PGPASS, lambda m: f"{m.group(1)} [REDACTED:value]", text, n)

    text = _redact_kv(text, n)

    def envline(m):
        if m.group(2).startswith("[REDACTED"):
            return m.group(0)
        n[0] += 1
        return f"{m.group(1)}=[REDACTED:env]"
    text = RE_ENVLINE.sub(envline, text)
    # Values of sensitive-looking variables in the server's own environment (compared, never printed).
    secrets = [v for k, v in os.environ.items() if (RE_KEYNAME.fullmatch(k) or k in ("GH_TOKEN", "GITHUB_TOKEN")) and len(v) >= 8]
    for v in sorted(set(secrets), key=len, reverse=True):
        if v in text:
            n[0] += text.count(v)
            text = text.replace(v, "[REDACTED:env-value]")
    if entropy:
        def ent(m):
            s = m.group(0)
            if re.fullmatch(r"[0-9a-fA-F]+", s) or not (re.search(r"[A-Z]", s) and re.search(r"[a-z]", s) and re.search(r"\d", s)):
                return s
            n[0] += 1
            return "[REDACTED:high-entropy]"
        text = RE_ENTROPY.sub(ent, text)
    return text, n[0]


# ---------- validation ----------

RE_SERVICE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
RE_SINCE = re.compile(r"^([0-9]{1,4})([smhd])$")
RE_RANGE_SIDE = r"(?:[A-Za-z0-9_/@{}~^-]|\.(?!\.)){1,100}"
RE_RANGE = re.compile(rf"^({RE_RANGE_SIDE})(?:(\.{{2,3}})({RE_RANGE_SIDE}))?$")
RE_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
RE_DIGITS = re.compile(r"^[0-9]{1,20}$")
RE_LINES = re.compile(r"^([0-9]{1,6}),([0-9]{1,6})$")
RE_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
RE_CID = re.compile(r"^[0-9a-f]{12,64}$")


def _intval(v, name, lo, hi, default=None):
    if v is None or v == "":
        if default is None:
            raise RunError(f"{name} is required")
        return default
    try:
        i = int(v)
    except (TypeError, ValueError):
        raise RunError(f"{name} must be an integer")
    if not lo <= i <= hi:
        raise RunError(f"{name} must be between {lo} and {hi}")
    return i


def validate_service(s):
    if not isinstance(s, str) or not RE_SERVICE.match(s):
        raise RunError("service must match ^[a-z0-9][a-z0-9_.-]{0,62}$")
    return s


def validate_since(s):
    if not s:
        return None
    m = RE_SINCE.match(str(s))
    if not m:
        raise RunError("since must look like 30m, 2h or 1d")
    num, unit = int(m.group(1)), m.group(2)
    return f"{num * 24}h" if unit == "d" else f"{num}{unit}"


def validate_digits(v, name):
    if not RE_DIGITS.match(str(v)):
        raise RunError(f"{name} must be digits only")
    return str(v)


def validate_lines(s):
    if not s:
        return None
    m = RE_LINES.match(str(s))
    if not m:
        raise RunError("lines must look like START,END")
    a, b = int(m.group(1)), int(m.group(2))
    if a < 1 or a > b or b - a > 2000:
        raise RunError("lines must satisfy 1 <= START <= END and at most 2000 lines")
    return f"{a},{b}"


def validate_path(p, root):
    """Relative path inside root that the guard allows. Returns the repo-relative string."""
    p = str(p or "")
    if not p or len(p) > 500 or p.startswith(("-", ":", "/", "~")) or "\x00" in p:
        raise RunError("path must be a relative path inside the project (no leading - : / ~)")
    full = (Path(root) / p).resolve()
    if full != Path(root) and Path(root) not in full.parents:
        raise RunError("path must stay inside the project")
    if not no.guard_allows(full, root):
        raise RunError("path is protected")
    return str(full.relative_to(root)) if full != Path(root) else "."


# ---------- running commands ----------

def _which(name):
    exe = shutil.which(name)
    if not exe:
        raise RunError(f"{name} is not installed")
    return exe


def _preflight(argv, cwd):
    """Defense in depth: run the rendered command through the same hooks an agent's Bash call would face."""
    if argv[0] not in ("docker", "gh", "git"):
        raise RunError(f"refused: {argv[0]} is not an allowed program")
    if argv[0] == "docker" and (len(argv) < 2 or argv[1] not in ("ps", "logs")):
        raise RunError(f"refused: docker {argv[1] if len(argv) > 1 else ''} is not an allowed subcommand")
    payload = json.dumps({"tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": shlex.join(argv)}})
    try:
        r = subprocess.run(["bash", str(HOOKS / "block-env.sh")], input=payload, text=True, capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        raise RunError("guard timed out; refusing to run")
    if r.returncode != 0:
        raise RunError(f"refused by guard: {(r.stderr or '').strip()[:160]}")
    live = HOOKS / "live-stack.py"
    if not live.exists():
        raise RunError("live-stack guard missing; refusing to run")
    try:
        r = subprocess.run([shutil.which("python3") or "python3", str(live), "--guard"], input=payload, text=True,
                           capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        raise RunError("live-stack guard timed out; refusing to run")
    if r.returncode != 0 or r.stdout.strip():
        raise RunError(f"refused by live-stack guard: {(r.stdout or r.stderr).strip()[:160]}")


def _run(argv, cwd, timeout, cap_bytes, env=None, ok_codes=(0,)):
    """Run one preflighted argv with no shell. Returns (cleaned_text, notes)."""
    _preflight(argv, cwd)
    argv = [_which(argv[0]), *argv[1:]]
    try:
        p = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, env=env, start_new_session=True)
    except OSError as e:
        raise RunError(f"{Path(argv[0]).name} could not start: {e}")
    notes, timed_out = [], threading.Event()

    def kill():
        timed_out.set()
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
    timer = threading.Timer(timeout, kill)
    timer.start()
    try:
        data = p.stdout.read(cap_bytes + 1)
        if len(data) > cap_bytes:
            data = data[:cap_bytes]
            notes.append(f"output cut at {cap_bytes} bytes")
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        kill()
    finally:
        timer.cancel()
        try:
            p.stdout.close()
        except OSError:
            pass
    if timed_out.is_set():
        raise RunError(f"{Path(argv[0]).name} timed out after {timeout}s")
    text = strip_ansi(data.decode("utf-8", errors="replace"))
    if p.returncode not in ok_codes and not notes:
        first = redact(text.strip().splitlines()[0][:160] if text.strip() else "")[0]
        raise RunError(f"{Path(argv[0]).name} exited {p.returncode}: {first}")
    return text, notes


def _cfg_run(cfg):
    t = dict(DEFAULT_TIMEOUTS)
    t.update(cfg.get("run_timeouts", {}))
    return t, max(1000, min(int(cfg.get("run_capture_bytes", DEFAULT_CAP_BYTES)), 5_000_000))


GIT_RO = ["--no-pager", "--literal-pathspecs", "-c", "core.fsmonitor=false", "-c", "core.quotepath=off",
          "-c", "diff.external=", "-c", "color.ui=never", "-c", "log.showSignature=false", "-c", "gpg.program=false",
          "-c", "gpg.ssh.program=false", "-c", "gpg.x509.program=false"]


class Ctx:
    def __init__(self, cwd, cfg):
        self.cfg = cfg
        self.timeouts, self.cap = _cfg_run(cfg)
        try:
            top = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            raise RunError("git timed out")
        self.root = Path(top.stdout.strip() or cwd).resolve()
        self.commands, self.notes = [], []

    def git(self, *args, ok_codes=(0,)):
        argv = ["git", "-C", str(self.root), *GIT_RO, *args]
        self.commands.append(shlex.join(["git", *args]))
        text, notes = _run(argv, self.root, self.timeouts["git"], self.cap, ok_codes=ok_codes)
        self.notes += notes
        return text

    def rev(self, ref):
        """Resolve a user-supplied ref to a commit SHA; the caller's string is never used afterwards."""
        try:
            r = subprocess.run(["git", "-C", str(self.root), "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"],
                               capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            raise RunError("git timed out")
        sha = r.stdout.strip()
        if r.returncode != 0 or not RE_SHA.match(sha):
            raise RunError(f"unknown ref: {ref[:60]}")
        return sha


def validate_range(ctx, s, default):
    """Returns ('A..B' | 'A...B' | 'SHA' | None, rendered) built only from resolved SHAs."""
    if not s:
        return default
    m = RE_RANGE.match(str(s))
    if not m or m.group(1).startswith("-") or (m.group(3) or "").startswith("-"):
        raise RunError("range must be REF, A..B or A...B (letters, digits, . _ / @ { } ~ ^ -; no leading -)")
    a = ctx.rev(m.group(1))
    if m.group(2):
        return f"{a}{m.group(2)}{ctx.rev(m.group(3))}"
    return a


# ---------- diff splitting (guard-filtered) ----------

RE_DIFF_HDR = re.compile(r"^diff --git a/(.+?) b/(.+)$")
RE_ANY_HDR = re.compile(r"^diff --git .*$", re.M)


def filter_diff(text, root):
    """Replace every per-file section the guard refuses (or whose header we cannot parse, e.g. quoted names) with a marker."""
    hdrs = list(RE_ANY_HDR.finditer(text))
    if not hdrs:
        return text
    out = [text[:hdrs[0].start()]]
    for i, m in enumerate(hdrs):
        end = hdrs[i + 1].start() if i + 1 < len(hdrs) else len(text)
        h = RE_DIFF_HDR.match(m.group(0))
        ok = bool(h) and not m.group(0).startswith('diff --git "') and all(no.guard_allows(Path(root) / n, root) for n in {h.group(1), h.group(2)})
        out.append(text[m.start():end] if ok else f"### {h.group(2) if h else 'unparsed header'}\n[skipped: protected or unparsable path]\n")
    return "".join(out)


# ---------- logs ----------

def _docker(ctx, *args):
    ctx.commands.append(shlex.join(["docker", *args]))
    text, notes = _run(["docker", *args], ctx.root, ctx.timeouts["docker"], ctx.cap)
    ctx.notes += notes
    return text


def gather_logs(args, ctx):
    service = validate_service(args.service)
    tail = _intval(args.tail, "tail", 1, 2000, default=200)
    since = validate_since(args.since)
    wd = os.path.realpath(ctx.root)
    wd_filter = f"label=com.docker.compose.project.working_dir={wd}"
    out = _docker(ctx, "ps", "-a", "--no-trunc", "--filter", wd_filter,
                  "--filter", f"label=com.docker.compose.service={service}", "--format", "{{.ID}}\t{{.Names}}\t{{.State}}")
    rows = [l.split("\t") for l in out.splitlines() if l.strip()]
    rows = [r for r in rows if len(r) == 3 and RE_CID.match(r[0])][:3]
    if not rows:
        svc = _docker(ctx, "ps", "-a", "--filter", wd_filter, "--format", '{{.Label "com.docker.compose.service"}}')
        names = sorted({s.strip() for s in svc.splitlines() if s.strip()})
        hint = ""
        if (ctx.root / ".git").is_file():
            hint = " (this is a linked worktree; the stack runs from the main checkout and is not followed)"
        raise RunError(f"no container for service '{service}' in this project{hint}; services: {', '.join(names) or 'none'}")
    parts = []
    for cid, name, state in rows:
        logargs = ["logs", "--timestamps", "--tail", str(tail)] + (["--since", since] if since else []) + [cid]
        parts.append(f"### {name} ({state})\n{_docker(ctx, *logargs)}")
    if (ctx.root / ".claude" / "deploy-mode").exists():
        ctx.notes.append("live stack: read-only logs of production containers")
    return "\n".join(parts), "tail"


# ---------- ci ----------

def _gh_env():
    env = dict(os.environ)
    env.update({"GH_PAGER": "cat", "PAGER": "cat", "NO_COLOR": "1", "GH_PROMPT_DISABLED": "1",
                "GH_NO_UPDATE_NOTIFIER": "1", "GH_SPINNER_DISABLED": "1"})
    return env


def _gh(ctx, *args, ok_codes=(0,)):
    ctx.commands.append(shlex.join(["gh", *args]))
    text, notes = _run(["gh", *args], ctx.root, ctx.timeouts["gh"], ctx.cap, env=_gh_env(), ok_codes=ok_codes)
    ctx.notes += notes
    return text


def gather_ci(args, ctx):
    if no.domain_of(ctx.root) == "WORK":
        raise RunError("local_ci uses gh; WORK projects use glab (WORK/CLAUDE.md)")
    kind = args.kind
    if kind == "failed_run":
        run_id = validate_digits(args.run_id, "run_id") if args.run_id else None
        if not run_id:
            branch = ctx.git("rev-parse", "--abbrev-ref", "HEAD").strip()
            if not RE_BRANCH.match(branch) or branch.startswith("-") or branch == "HEAD":
                raise RunError("cannot determine a branch; pass run_id")
            lst = _gh(ctx, "run", "list", "--branch", branch, "--status", "failure", "--limit", "1",
                      "--json", "databaseId,displayTitle,workflowName,headSha,url")
            try:
                rows = json.loads(lst or "[]")
                run_id = validate_digits(rows[0]["databaseId"], "run id")
            except (ValueError, IndexError, KeyError, TypeError):
                raise RunError(f"no failed run found on branch {branch}")
        meta = _gh(ctx, "run", "view", run_id, "--json", "name,displayTitle,conclusion,headBranch,headSha,jobs")
        head = ""
        try:
            j = json.loads(meta)
            head = f"run {run_id}: {j.get('displayTitle')} [{j.get('conclusion')}] @ {str(j.get('headSha'))[:8]}\n" + \
                   "".join(f"- job {x.get('name')}: {x.get('conclusion')}\n" for x in j.get("jobs", []))
        except ValueError:
            pass
        return head + _gh(ctx, "run", "view", run_id, "--log-failed"), "tail"
    pr = [validate_digits(args.pr, "pr")] if args.pr else []
    if kind == "pr_checks":
        out = _gh(ctx, "pr", "checks", *pr, ok_codes=(0, 8))
        if not out.strip():
            raise RunError("gh pr checks returned nothing")
        return out, "head"
    if kind == "pr_diff":
        return filter_diff(_gh(ctx, "pr", "diff", *pr, "--color", "never"), ctx.root), "head"
    raise RunError("kind must be failed_run, pr_checks or pr_diff")


# ---------- git ----------

def gather_git(args, ctx):
    kind = args.kind
    path = validate_path(args.path, ctx.root) if args.path else None
    if kind == "blame":
        if not path or path == ".":
            raise RunError("blame needs a file path")
        if not (ctx.root / path).is_file():
            raise RunError("blame path must be a file")
        sha = validate_range(ctx, args.range, ctx.rev("HEAD"))
        if ".." in sha:
            raise RunError("blame takes a single ref")
        lines = validate_lines(args.lines)
        out = ctx.git("blame", "--no-textconv", "--date=short", "-w", *(["-L", lines] if lines else []), sha, "--", path)
        return out, "head"
    if kind == "log":
        n = _intval(args.max_commits, "max_commits", 1, 200, default=50)
        rng = validate_range(ctx, args.range, ctx.rev("HEAD"))
        fmt = "--format=%h %ad %an%n    %s"
        out = ctx.git("log", "--no-color", "--no-show-signature", "--date=short", fmt, "-n", str(n), "--stat", "--no-ext-diff", "--no-textconv",
                      rng, *(["--", path] if path else []))
        if args.patch:
            shas = ctx.git("log", "--no-show-signature", "--format=%H", "-n", str(min(n, 20)), rng, *(["--", path] if path else [])).split()
            for sha in shas:
                if not RE_SHA.match(sha):
                    continue
                names = [x for x in ctx.git("show", "--no-show-signature", "--name-only", "--format=", sha).splitlines() if x]
                allowed = [x for x in names if no.guard_allows(ctx.root / x, ctx.root)]
                if allowed:
                    out += "\n" + ctx.git("show", "--no-show-signature", "--no-ext-diff", "--no-textconv", "--format=%h %s", sha, "--", *allowed)
        return filter_diff(out, ctx.root), "head"
    if kind == "diff":
        default = f"{no.default_base(ctx.root)}..{ctx.rev('HEAD')}"
        rng = validate_range(ctx, args.range, default)
        names = [x for x in ctx.git("diff", "--name-only", "--no-ext-diff", "--no-textconv", rng,
                                    *(["--", path] if path else [])).splitlines() if x]
        parts = []
        for name in names:
            if not no.guard_allows(ctx.root / name, ctx.root):
                parts.append(f"### {name}\n[skipped: protected path]\n")
                continue
            parts.append(ctx.git("diff", "--no-ext-diff", "--no-textconv", rng, "--", name))
        return ("".join(parts) or "(no changes in range)"), "head"
    raise RunError("kind must be log, diff or blame")


# ---------- entry point ----------

def gather(task, args, cwd, cfg):
    """Run the fixed commands for a task. Returns {'text','commands','redactions','notes','keep'} or raises RunError."""
    ctx = Ctx(cwd, cfg)
    text, keep = {"logs": gather_logs, "ci": gather_ci, "git": gather_git}[task](args, ctx)
    if len(text) > ctx.cap:  # total cap across all commands, applied BEFORE redaction (bounds regex work and memory)
        cut = len(text) - ctx.cap
        text = f"[truncated {cut} chars]\n{text[-ctx.cap:]}" if keep == "tail" else f"{text[:ctx.cap]}\n[truncated {cut} chars]"
        ctx.notes.append(f"total output cut at {ctx.cap} chars")
    text, n = redact(text, entropy=(task != "git"))
    return {"text": text, "commands": ctx.commands, "redactions": n, "notes": ctx.notes, "keep": keep}


def fit(text, limit, keep):
    """Cut to the context budget: keep the tail for logs/CI logs, the head for diffs and history."""
    if len(text) <= limit:
        return text
    cut = len(text) - limit
    return f"[truncated {cut} chars]\n{text[-limit:]}" if keep == "tail" else f"{text[:limit]}\n[truncated {cut} chars]"
