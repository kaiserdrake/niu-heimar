#!/usr/bin/env python3
"""Guard for projects whose folder is BOTH the dev checkout and the live docker compose stack.

Active only when a project has `.claude/deploy-mode` (found by walking up from cwd, from `cd`/`-C`/`-f` targets
and from any path in the command, stopping at $WORKSPACE). No marker -> silent, exit 0.

  live-stack.py --session-start   SessionStart: banner for the user + additionalContext for Claude
  live-stack.py --guard           PreToolUse: ask/deny risky compose, docker, git and live-path writes

Marker format: `key: value` lines, `#` comments. `mode: live-dev-same-dir`; `live-path: <rel path>` (repeatable).
Any unknown mode is treated as live (fail safe). This is a seatbelt against honest mistakes, not a sandbox.
"""
import glob
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

WS = Path(os.environ.get("WORKSPACE") or Path(__file__).resolve().parents[2]).resolve()

COMPOSE_ASK = {"up", "down", "stop", "start", "restart", "build", "create", "rm", "kill", "pause", "unpause",
               "pull", "push", "run", "exec", "watch", "scale", "cp", "attach"}
DOCKER_ASK = {"stop", "restart", "kill", "rm", "start", "exec", "update", "rmi", "tag", "cp", "commit", "load",
              "pause", "unpause", "rename"}
GIT_ASK = {"checkout", "switch", "stash", "reset", "restore", "clean", "pull", "merge", "rebase", "cherry-pick", "am",
           "apply", "revert", "rm", "mv", "checkout-index", "read-tree", "submodule", "bisect", "sparse-checkout",
           "subtree"}
WRITE_VERBS = {"sed", "cp", "mv", "rm", "tee", "install", "truncate", "ln", "rsync", "dd", "patch", "perl",
               "chmod", "chown", "touch", "mkdir", "rmdir", "unlink", "shred", "curl", "wget", "tar", "unzip", "sponge"}
COMPOSE_VALUE_FLAGS = {"-f", "--file", "-p", "--project-name", "--profile", "--project-directory", "--env-file",
                       "--ansi", "--parallel", "-c", "--context", "-H", "--host", "--progress"}
DOCKER_VALUE_FLAGS = {"--context", "-c", "-H", "--host", "-l", "--log-level", "--config", "--tlscacert",
                      "--tlscert", "--tlskey"}
WRAPPER_VALUE_FLAGS = {"sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "--user", "--group", "--host",
                                "--prompt", "--chdir"},
                       "doas": {"-u", "-C"}, "env": {"-u", "-C", "-S"}, "nice": {"-n"}, "ionice": {"-c", "-n", "-p"},
                       "stdbuf": {"-i", "-o", "-e"}, "timeout": {"-s", "-k", "--signal", "--kill-after"},
                       "xargs": {"-n", "-I", "-P", "-d", "-L", "-s", "-E", "-a", "--max-args", "--replace",
                                 "--max-procs", "--delimiter"},
                       "setsid": set(), "watch": {"-n", "-d"}, "flock": {"-w", "-E"}, "time": {"-f", "-o"}, "unbuffer": set(),
                       "nohup": set()}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
KEYWORDS = {"then", "do", "else", "elif", "if", "while", "until", "!", "{", "(", "time", "command", "exec", "nohup",
            "builtin"}  # (`time` is handled as a wrapper so its -p/-v flags are skipped)


# ---------- marker / roots ----------
def expand(p):
    """expandvars + expanduser that never raises (e.g. ~nosuchuser)."""
    try:
        return Path(os.path.expandvars(str(p))).expanduser()
    except (RuntimeError, ValueError, OSError):
        return Path(str(p))


def find_root(start):
    try:
        p = Path(start).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    for d in [p, *p.parents]:
        if (d / ".claude" / "deploy-mode").is_file():
            return d
        if d == WS:
            break
    return None


def all_roots(cwd=None):
    out = set()
    if cwd:
        r = find_root(cwd)
        if r:
            out.add(r)
    for m in glob.glob(str(WS / "*" / "*" / ".claude" / "deploy-mode")):
        out.add(Path(m).parent.parent.resolve())
    return out


def read_marker(root):
    mode, live = "", []
    try:
        lines = (root / ".claude" / "deploy-mode").read_text(errors="replace").splitlines()
    except OSError:
        return "unknown", []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (s.strip() for s in line.split(":", 1))
        if k == "mode":
            mode = v
        elif k == "live-path" and v:
            live.append(v)
    return mode or "unknown", live


def is_worktree(root):
    return (root / ".git").is_file()  # a linked worktree has a .git *file*; the live checkout has a directory


def live_status(root):
    """Return (state, running, total): state is here | elsewhere | stopped | unknown. Used for messages only."""
    def ps(*flt):
        cmd = ["docker", "ps", "-a", *flt, "--format",
               "{{.Names}}\t{{.State}}\t{{.Label \"com.docker.compose.project.working_dir\"}}"]
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=3)
        if r.returncode != 0:
            raise OSError(r.stderr)
        return [ln.split("\t") for ln in r.stdout.splitlines() if ln.strip()]
    real = os.path.realpath(root)
    try:
        here = ps("--filter", f"label=com.docker.compose.project.working_dir={real}")
        if here:
            return "here", sum(1 for r in here if len(r) > 1 and r[1] == "running"), len(here)
        if is_worktree(root):
            return "elsewhere", 0, 0
        return "stopped", 0, 0
    except (OSError, subprocess.SubprocessError):
        return "unknown", 0, 0


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def session_start(data):
    root = find_root(data.get("cwd") or os.getcwd())
    if not root:
        return
    mode, live = read_marker(root)
    state, run, tot = live_status(root)
    name = root.name
    paths = ", ".join(live) or "(none listed)"
    if is_worktree(root):
        banner = f"{name} is marked live, but this is a git worktree (dev copy); the live stack is in the main checkout."
        ctx = (f"{name} is marked {mode}, but this folder is a git worktree, so it is a dev copy. compose commands "
               "still collide with the live containers (fixed container_name); ask the user before using them.")
    elif state == "here":
        banner = f"LIVE STACK: {name} runs from this folder ({run}/{tot} containers up). Changes here hit production."
        ctx = (f"{name} is marked {mode} and its containers run from this folder ({run}/{tot} up). "
               f"Files under {paths} are read live. docker compose lifecycle commands here affect production. "
               "See CLAUDE.md, section 'LIVE STACK'.")
    elif state == "stopped":
        banner = f"LIVE STACK: {name} is marked live; no containers from this folder exist (stack down)."
        ctx = f"{name} is marked {mode}; no containers from this folder exist (stack down). Treat it as the live folder."
    else:
        banner = f"LIVE STACK: {name} is marked live; could not query Docker, treating this folder as live."
        ctx = f"{name} is marked {mode}; could not query Docker, so treat this folder as live."
    emit({"systemMessage": banner,
          "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}})


# ---------- command parsing ----------
def decide(decision, reason):
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                 "permissionDecisionReason": reason}})


def split_commands(cmd):
    """Split on ; & | newline ( ) ` outside quotes. Skips comments and heredoc bodies. Redirects (2>&1, &>) are not
    separators. If a quote is left open, the naive split is added too, so an apostrophe cannot hide a command."""
    cmd = cmd.replace("\\\n", " ")
    parts, buf, q, i, pending = [], [], None, 0, []
    while i < len(cmd):
        c = cmd[i]
        prev = cmd[i - 1] if i > 0 else "\n"
        if q:
            buf.append(c)
            if c == q:
                q = None
            elif c == "\\" and q == '"' and i + 1 < len(cmd):
                i += 1
                buf.append(cmd[i])
        elif c in "'\"":
            q = c
            buf.append(c)
        elif c == "\\" and i + 1 < len(cmd):
            buf.append(c)
            i += 1
            buf.append(cmd[i])
        elif c == "#" and prev in " \t\n;&|()":
            while i + 1 < len(cmd) and cmd[i + 1] != "\n":
                i += 1
        elif c == "<" and cmd.startswith("<<<", i):
            buf.append("<<<")
            i += 2
        elif c == "$" and cmd.startswith("$((", i):
            e = cmd.find("))", i)
            e = len(cmd) - 1 if e == -1 else e + 1
            buf.append(cmd[i:e + 1])
            i = e
        elif c == "|" and prev == ">":
            buf.append(c)
        elif c == "<" and cmd.startswith("<<", i):
            m = re.match(r"<<-?\s*\\?(['\"]?)([\w.-]+)\1", cmd[i:])
            if m and not m.group(2).isdigit():
                pending.append(m.group(2))
                buf.append(m.group(0))
                i += len(m.group(0)) - 1
            else:
                buf.append(c)
        elif c == "&" and ((i > 0 and cmd[i - 1] == ">") or (i + 1 < len(cmd) and cmd[i + 1] == ">")):
            buf.append(c)
        elif c in ";&|\n()`":
            parts.append("".join(buf))
            buf = []
            if c == "\n":
                line_start = cmd.rfind("\n", 0, i) + 1
                to_shell = bool(re.search(r"(^|[\s|;&(])((ba|z|da|k)?sh|ssh|xargs)(\s|$)", cmd[line_start:i]))
                while pending:
                    w = pending.pop(0)
                    j = i + 1
                    body = []
                    while j <= len(cmd):
                        e = cmd.find("\n", j)
                        e = len(cmd) if e == -1 else e
                        line = cmd[j:e]
                        j = e + 1
                        if line.strip() == w or e == len(cmd):
                            break
                        body.append(line)
                    if to_shell:  # the body is a script fed to a shell: check it
                        parts += split_commands("\n".join(body))
                    i = j - 1
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    if q:  # unbalanced quote: also look at a naive split
        parts += re.split(r"[;&|\n()`]", cmd)
    return [x for x in parts if x.strip()]


def flatten(cmd, depth=0):
    """Split into simple commands, recursing into `sh -c '...'`, `bash -lc ...` and `eval ...` payloads."""
    out = []
    for part in split_commands(cmd):
        st = strip_wrappers(tokens(part))
        payload = None
        if st and depth < 3:
            nm = os.path.basename(st[0])
            if nm in SHELLS:
                k = 1
                while k < len(st) and st[k][:1] in ("-", "+"):
                    if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", st[k]) and k + 1 < len(st):
                        payload = st[k + 1]
                        break
                    k += 2 if st[k] in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file") else 1
            elif nm == "eval":
                payload = " ".join(st[1:])
        if payload is not None:
            out += flatten(payload, depth + 1)
        else:
            out.append(part)
    return out


def tokens(part):
    part = part.strip().lstrip("({!").rstrip(")}").strip()
    try:
        return shlex.split(part)
    except ValueError:
        return part.split()


def strip_wrappers(t):
    """Drop VAR=x, keywords and wrapper commands (with their flags/arguments). Returns remaining tokens."""
    while t:
        name = os.path.basename(t[0])
        if re.match(r"^\w+=", t[0]) or (t[0] in KEYWORDS and name != "time"):
            t = t[1:]
        elif name in WRAPPER_VALUE_FLAGS or name == "time":
            vf = WRAPPER_VALUE_FLAGS.get(name, set())
            i = 1
            while i < len(t) and t[i].startswith("-"):
                i += 2 if t[i] in vf else 1
            if name in ("timeout", "flock") and i < len(t):
                i += 1  # the duration / lock file
            while name == "env" and i < len(t) and re.match(r"^\w+=", t[i]):
                i += 1
            t = t[i:]
        else:
            break
    return t


def classify(part):
    """Return a list of (decision, reason) for one simple command. decision in deny|ask|git|write-target."""
    t = strip_wrappers(tokens(part))
    if not t:
        return []
    name = os.path.basename(t[0])
    if name == "git":
        i = 1
        while i < len(t) and t[i].startswith("-"):
            i += 2 if t[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
        if i + 1 < len(t) and t[i] == "stash" and t[i + 1] in ("list", "show"):
            return []
        if i < len(t) and t[i] in GIT_ASK:
            return [("git", f"'git {t[i]}' here rewrites live files")]
        return []
    if name.endswith("deploy.sh") or (name in SHELLS | {"source", "."} and any(x.endswith("deploy.sh") for x in t[1:4])):
        return [("ask", "deploy.sh changes the running deployment")]
    if name not in ("docker", "docker-compose", "podman", "podman-compose"):
        return []
    rest = t[1:]
    i = 0
    while name in ("docker", "podman") and i < len(rest) and rest[i].startswith("-"):  # docker global flags
        i += 2 if (rest[i] in DOCKER_VALUE_FLAGS and "=" not in rest[i]) else 1
    rest = rest[i:]
    proj = any(x.startswith("COMPOSE_PROJECT_NAME=") for x in tokens(part))
    if name in ("docker", "podman"):
        if not rest:
            return []
        head = rest[0]
        if head == "compose":
            rest = rest[1:]
        else:
            if head == "volume":
                if len(rest) > 1 and rest[1] in ("rm", "remove", "prune"):
                    return [("deny", f"'docker volume {rest[1]}' could delete named volumes holding live data")]
                return []
            if head in ("system", "image", "container", "network", "builder") and len(rest) > 1 and rest[1] == "prune":
                return [("deny", f"'docker {head} prune' could delete live data")]
            if head in ("container", "image") and len(rest) > 1:
                rest = rest[1:]
                head = rest[0]
            if head == "network" and len(rest) > 1 and rest[1] in ("connect", "disconnect", "rm", "remove"):
                return [("host", f"'docker network {rest[1]}' changes the running deployment")]
            if head == "remove":
                head = "rm"
            if head == "run" and any(re.search(r"(^|[ =])[\w.-]+:/", a) and not a.startswith(("/", ".", "~"))
                                     or re.search(r"(source|src)=[^/.~,]", a) or a.startswith("--volumes-from")
                                     for a in rest[1:]):
                return [("host", "'docker run' uses a named volume or another container's volumes; may be live data")]
            if head in DOCKER_ASK:
                return [("host", f"'docker {head}' changes the running deployment")]
            return []
    sub, args = None, []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("-p", "--project-name") or a.startswith("--project-name="):
            proj = True
        if a in COMPOSE_VALUE_FLAGS:
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            sub, args = a, rest[i + 1:]
            break
    if sub is None:
        return []
    if sub == "down" and any(a.startswith("--volumes") or (a.startswith("-") and not a.startswith("--") and "v" in a)
                             for a in args):
        return [("deny", "'docker compose down -v' deletes the named volumes holding live data")]
    if sub in COMPOSE_ASK:
        return [("host" if proj else "ask", f"'docker compose {sub}' here changes the running deployment")]
    return []


def cd_target(st):
    args = [a for a in st[1:] if not (a.startswith("-") and a != "-")]
    return args[0] if args else "."


def redirect_targets(part):
    """Targets of unquoted > / >> / >| redirects, including ones glued to the previous word (echo x>file)."""
    out, i, q = [], 0, None
    while i < len(part):
        c = part[i]
        if q:
            if c == q:
                q = None
        elif c in "'\"":
            q = c
        elif c == "\\":
            i += 1
        elif c == ">" and not part.startswith(">&", i):
            i += 1
            while i < len(part) and part[i] in ">|":
                i += 1
            while i < len(part) and part[i] in " \t":
                i += 1
            j, qq = i, None
            while j < len(part):
                if qq:
                    if part[j] == qq:
                        qq = None
                elif part[j] in "'\"":
                    qq = part[j]
                elif part[j] in " \t;|&<>":
                    break
                j += 1
            try:
                w = shlex.split(part[i:j])
            except ValueError:
                w = [part[i:j]]
            if w and w[0] and w[0] != "/dev/null" and not w[0].startswith("&"):
                out.append(w[0])
            i = j - 1
        i += 1
    return out


def write_targets(st):
    """(path, ancestor_check) pairs a write command may modify. Globs are expanded by the caller."""
    if not st:
        return [], False
    verb = os.path.basename(st[0])
    args = st[1:]
    nonflag = [a for a in args if not a.startswith("-")]
    out, cwd_write = [], False
    if verb in ("sed", "perl"):
        if any(re.match(r"^-[a-zA-Z]*i", x) or x.startswith("--in-place") for x in args):
            out += [(a, True) for a in nonflag]
    elif verb in ("cp", "rsync", "install", "ln"):
        if nonflag:
            out.append((nonflag[-1], True))
        out += [(args[k + 1], True) for k, x in enumerate(args) if x in ("-t", "--target-directory") and k + 1 < len(args)]
    elif verb == "tar":
        cluster = args[0] if args and not args[0].startswith("--") else ""
        extract = "--extract" in args or bool(re.match(r"^-?[a-zA-Z]*x", cluster))
        c_vals = [args[k + 1] for k, x in enumerate(args) if x in ("-C", "--directory") and k + 1 < len(args)]
        if extract:
            out += [(v, True) for v in c_vals]
            cwd_write = not c_vals
        elif re.match(r"^-?[a-zA-Z]*c", cluster) and "f" in cluster and nonflag:
            out.append((nonflag[0], True))  # the archive being created
    elif verb == "unzip":
        if not any(x in ("-l", "-t", "-v", "-p", "-Z") for x in args):
            out += [(a, True) for a in nonflag]
            cwd_write = "-d" not in args
    elif verb == "patch":
        cwd_write = True
        out += [(a, True) for a in nonflag]
    elif verb == "dd":
        out += [(a[3:], True) for a in args if a.startswith("of=")]
    elif verb == "wget":
        out += [(a, True) for a in nonflag]
        cwd_write = not any(a.startswith(("-O", "-P")) for a in args)
    elif verb == "curl":
        out += [(a, True) for a in nonflag]
        cwd_write = any(a in ("-O", "-J", "--remote-name") for a in args)
    elif verb == "find":
        if any(x in args for x in ("-delete", "-exec", "-execdir", "-fprint")):
            out += [(a, True) for a in nonflag]
    elif verb in WRITE_VERBS:
        out += [(a, True) for a in nonflag]
    if verb in WRITE_VERBS or verb == "find":
        for x in args:
            if x.startswith("--") and "=" in x and "/" in x.split("=", 1)[1]:
                out.append((x.split("=", 1)[1], False))
    return out, cwd_write


def part_dirs(t, cwd):
    """Directories a command part refers to: the running cwd plus -C/-f/--project-directory values and path tokens."""
    out = [cwd]
    for j, a in enumerate(t):
        val = None
        a = re.sub(r"^([0-9]*>>?|&>>?|<)", "", a)
        if not a:
            continue
        if re.match(r"^\w+=", a):
            val = a.split("=", 1)[1]
        elif a in ("-C", "-f", "--file", "--project-directory", "--git-dir", "--work-tree") and j + 1 < len(t):
            val = t[j + 1]
        elif "=" in a and a.startswith("--"):
            val = a.split("=", 1)[1]
        elif a.startswith("-f") and len(a) > 2 and not a.startswith("--"):
            val = a[2:]
        elif "/" in a or a.startswith(("~", ".")):
            val = a
        if val:
            p = expand(val)
            out.append(str(p if p.is_absolute() else Path(cwd) / p))
    return out


def under(path, base):
    return path == base or base in path.parents


def guard(data):
    ti = data.get("tool_input")
    ti = ti if isinstance(ti, dict) else {}
    tool = str(data.get("tool_name", ""))
    cwd = str(data.get("cwd") or os.getcwd())
    roots_all = all_roots(cwd)
    if not roots_all:
        return

    if tool == "Bash":
        cmd = ti.get("command")
        if cmd is None:
            return
        if not isinstance(cmd, str):
            return decide("ask", "LIVE STACK: unreadable command; asking to be safe.")
        cur = cwd
        asks, git_hits = [], []
        for part in flatten(cmd):
            t = tokens(part)
            st = strip_wrappers(t)
            if st and st[0] in ("cd", "pushd") and len(st) > 1:  # follow `cd X` for the following parts
                p = expand(cd_target(st))
                cur = str(p if p.is_absolute() else Path(cur) / p)
                continue
            roots = {r for r in (find_root(d) for d in part_dirs(t, cur)) if r}
            for dec, why in classify(part):
                if dec == "deny":  # volume/system deletion: refuse whenever any live project exists on this machine
                    name = (next(iter(roots)) if roots else sorted(roots_all)[0]).name
                    return decide("deny", f"LIVE STACK ({name}): {why}.")
                if dec == "host":  # acts on the whole docker host (fixed container names, named volumes)
                    asks.append(((sorted(roots) or sorted(roots_all))[0].name, why))
                    continue
                if not roots:
                    continue
                live = [r for r in roots if not (dec == "git" and is_worktree(r))]
                if live:
                    (git_hits if dec == "git" else asks).append((sorted(live)[0].name, why))
            # shell writes to a live path: write-command targets (with ancestors and globs) and redirect targets
            wroots = roots | roots_all  # a rename/delete of the folder itself can be run from anywhere
            if wroots:
                tg, cwd_write = write_targets(st)
                targets = list(tg) + [(r_, False) for r_ in redirect_targets(part)]
                if cwd_write:
                    targets.append((".", True))
                verb = os.path.basename(st[0]) if st else ""
                for r in sorted(wroots):
                    if is_worktree(r):
                        continue
                    _, lp = read_marker(r)
                    bases = [(r / x).resolve() for x in lp + [".claude/deploy-mode"]]
                    hit = False
                    for a, anc in targets:
                        pa = expand(a)
                        full = pa if pa.is_absolute() else Path(cur) / pa
                        cands = [full]
                        if any(ch in str(full) for ch in "*?["):
                            cands += [Path(m) for m in glob.glob(str(full))]
                            cands.append(Path(os.path.dirname(re.split(r"[*?\[]", str(full))[0] + "x")))
                        for cand in cands:
                            pd = Path(os.path.realpath(cand))
                            if any(under(pd, b) or (anc and under(b, pd)) for b in bases):
                                hit = True
                                break
                        if hit:
                            break
                    if hit:
                        asks.append((r.name, f"{verb or 'a redirect'} writes to a path the running stack reads live"))
                        break
        for name, why in asks + git_hits:
            return decide("ask", f"LIVE STACK ({name}): {why}.")
        return

    path = ti.get("file_path") or ti.get("notebook_path") or ti.get("path")
    if path and tool in ("Edit", "MultiEdit", "Write", "NotebookEdit") and isinstance(path, str):
        p = expand(path)
        full = (p if p.is_absolute() else Path(cwd) / p).resolve()
        root = find_root(full)
        if not root or is_worktree(root):
            return
        _, live = read_marker(root)
        if full == (root / ".claude" / "deploy-mode").resolve():
            return decide("ask", f"LIVE STACK ({root.name}): editing the deploy-mode marker switches this guard off.")
        for lp in live:
            if under(full, (root / lp).resolve()):
                return decide("ask", f"LIVE STACK ({root.name}): {lp} is read live by the running stack; saving is a deploy.")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
        data = data if isinstance(data, dict) else {}
    except ValueError:
        data = {}
    try:
        if mode == "--session-start":
            session_start(data)
        elif mode == "--guard":
            guard(data)
    except Exception as exc:  # fail safe: a broken guard must not silently allow
        if mode == "--guard":
            try:
                relevant = bool(all_roots(data.get("cwd") if isinstance(data.get("cwd"), str) else None))
            except Exception:
                relevant = True
            if relevant:
                decide("ask", f"LIVE STACK: guard error ({type(exc).__name__}); asking to be safe.")
    sys.exit(0)


if __name__ == "__main__":
    main()
