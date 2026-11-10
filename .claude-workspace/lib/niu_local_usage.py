"""Counts-only ledger of lite-mode local-model calls (stdlib only).

`record()` appends one JSON line per `/api/chat` call to `local-usage.jsonl` next to events.db:
  {"v":1,"id":"<uuid4 hex>","ts":<int>,"ep":"gpu","model":"qwen3:14b","task":"ask","domain":"PERSONAL","in":1234,"out":210}
Only an endpoint *name*, a model name, a task enum, a domain name and two counts are ever written: never a prompt, an answer,
a URL, a path, a key or exception text. The file is 0600 in a 0700 folder, opened with O_NOFOLLOW, and rotated by size (one old
generation). `record()` never raises: a ledger problem must not change a tool call. niu_usage reads the ledger with `parse_line()`,
which re-validates every field.
"""
import json
import os
import re
import stat
import sys
import time
import uuid
from pathlib import Path

from niu_events import db_path

TASKS = ("search", "diff", "ask", "logs", "ci", "git")
RE_EP = re.compile(r"[A-Za-z0-9_.-]{1,40}")
RE_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}")
RE_DOMAIN = re.compile(r"[A-Za-z0-9 ._()-]{1,64}")
RE_ID = re.compile(r"[0-9a-f]{32}")
MAX_COUNT = 10_000_000
ROTATE_AT = 512 * 1024
MAX_LINE = 512
KEYS = {"v", "id", "ts", "ep", "model", "task", "domain", "in", "out"}


def disabled():
    return os.environ.get("NIU_USAGE_COLLECT") == "0" or os.environ.get("NIU_USAGE_TOKENS") == "0"


def ledger_path() -> Path:
    return Path(os.environ.get("NIU_LOCAL_USAGE_LEDGER") or db_path().parent / "local-usage.jsonl")


def _count(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= MAX_COUNT else None


def _fullmatch(rx, v):
    return v if isinstance(v, str) and rx.fullmatch(v) else None


def parse_line(raw, now=None):
    """Validate one ledger line (bytes or str). Returns a clean dict {id, ts, ep, model, task, domain, in, out} or None.

    Unknown keys, bad types, out-of-range counts and anything not matching the allowlists reject the whole line."""
    try:
        o = json.loads(raw)
    except (ValueError, RecursionError, TypeError):
        return None
    if not isinstance(o, dict) or not set(o) <= KEYS or o.get("v") != 1:
        return None
    now = time.time() if now is None else now
    ts = o.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, int) or not 1_500_000_000 < ts < now + 2 * 86400:
        return None
    rid, ep, model, task = (_fullmatch(RE_ID, o.get("id")), _fullmatch(RE_EP, o.get("ep")),
                            _fullmatch(RE_MODEL, o.get("model")), o.get("task"))
    if rid is None or ep is None or model is None or task not in TASKS:
        return None
    dom = o.get("domain")
    if dom is not None and _fullmatch(RE_DOMAIN, dom) is None:
        return None
    tin, tout = _count(o.get("in", 0)), _count(o.get("out", 0))
    if tin is None or tout is None or ("in" not in o and "out" not in o):
        return None
    return {"id": rid, "ts": ts, "ep": ep, "model": model, "task": task, "domain": dom, "in": tin, "out": tout}


def _line(endpoint, model, task, domain, res, now):
    if not isinstance(res, dict):
        return None
    tin, tout = _count(res.get("prompt_eval_count")), _count(res.get("eval_count"))
    if tin is None and tout is None:
        return None
    rec = {"v": 1, "id": uuid.uuid4().hex, "ts": int(now), "ep": _fullmatch(RE_EP, endpoint),
           "model": _fullmatch(RE_MODEL, model), "task": task if task in TASKS else None,
           "domain": _fullmatch(RE_DOMAIN, domain), "in": tin or 0, "out": tout or 0}
    if rec["ep"] is None or rec["model"] is None or rec["task"] is None:
        return None
    data = (json.dumps(rec, separators=(",", ":")) + "\n").encode()
    return data if len(data) <= MAX_LINE else None


def _debug(e):
    if os.environ.get("NIU_DASH_DEBUG"):
        print(f"niu-local-usage: {type(e).__name__}", file=sys.stderr)


def record(endpoint, model, task, domain, res, now=None):
    """Append one counts line. Returns True if written. Never raises; a no-op when usage collection is switched off."""
    try:
        if disabled():
            return False
        data = _line(endpoint, model, task, domain, res, time.time() if now is None else now)
        if data is None:
            return False
        p = ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                return False
            if st.st_size > ROTATE_AT and _rotate(fd, p, st):
                os.close(fd)
                fd = -1
                fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600)
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                    return False
            os.write(fd, data)
            return True
        finally:
            if fd >= 0:
                os.close(fd)
    except Exception as e:  # noqa: BLE001 - the ledger must never change a tool call
        _debug(e)
        return False


def _rotate(fd, p, st):
    """Move the full file to `<name>.1` (replacing the older generation). Returns True if this call rotated it; skipped
    (False) if another process holds the lock or already rotated."""
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError):
        return False
    try:
        cur = os.stat(p, follow_symlinks=False)
        if (cur.st_dev, cur.st_ino) == (st.st_dev, st.st_ino):   # not already rotated by someone else
            os.replace(p, p.with_name(p.name + ".1"))
            return True
    except OSError:
        pass
    return False
