"""niu-notify: outbound-only Discord webhook notifications for niu-heimar (stdlib only).

Design: docs/design/2026-11-06-discord-notifications.md. Layers:
  observe() -> plain dicts from the same sources the dashboard uses (hostnames / URLs / errors dropped here)
  diff()    -> pure: previous state + observation -> (new state, [messages]); transitions, debounce, cooldown
  render()  -> fixed template + validated fields -> redacted, scrubbed webhook payload
  Dispatcher -> routing, quiet hours, hourly cap, 429 / 5xx / 401-404 handling
Webhook URLs come only from os.environ (systemd's EnvironmentFile=); this module never opens .env.
"""
import copy
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from niu_events import SHARED, db_path

ROUTES = ("needs-you", "alerts", "limits", "llm", "ci", "activity")  # also the send priority order
RE_WEBHOOK = re.compile(r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/[0-9]{17,21}/[A-Za-z0-9_-]{60,100}")
RE_ENVNAME = re.compile(r"DISCORD_WEBHOOK_[A-Z_]{1,40}")
RE_DOMAIN = re.compile(r"[A-Z][A-Z0-9_]{0,15}")
RE_PROJECT = re.compile(r"[A-Za-z0-9._/-]{1,60}")
RE_REF = re.compile(r"([A-Za-z0-9_.-]{1,39})/([A-Za-z0-9_.-]{1,100})#([0-9]{1,7})")
RE_LABEL = re.compile(r"[A-Za-z0-9 _-]{1,30}")  # no dots / colons: a label must not be a hostname or IP
RE_TITLE_DROP = re.compile(r"[^A-Za-z0-9 .,:;_()/#+'-]")
MAX_CONTENT = 400
FLAG_SUPPRESS_EMBEDS = 4
FLAG_SILENT = 4096
LIMIT_LABELS = {"five_hour": "5-hour", "seven_day": "Weekly", "seven_day_opus": "Weekly Opus",
                "seven_day_sonnet": "Weekly Sonnet"}
SOURCES = ("sessions", "github", "llm", "limits", "dashboard")
DOMAIN_MODES = ("full", "minimal", "off")
NOTIFY_MODES = ("on", "silent", "off")

TEMPLATES = {
    "wait-permission": "[needs you] {where}: a session needs your permission",
    "wait-reply": "[needs you] {where}: a session is waiting for your reply",
    "wait-other": "[needs you] {where}: a session is waiting for you",
    "done": "[done] {where}: session finished after {dur}",
    "pr-failing": "[ci] {ref}: {n} failing check(s) · {title}\n{pr_url}",
    "pr-changes": "[ci] {ref}: changes requested · {title}\n{pr_url}",
    "pr-ready": "[ci] {ref}: ready to merge · {title}\n{pr_url}",
    "pr-blocked": "[ci] {ref}: blocked: gh token needs the workflow scope\n{pr_url}",
    "llm-down": "[llm] {label} is down",
    "llm-up": "[llm] {label} is back up",
    "llm-no-gpu": "[llm] no GPU endpoint is up; local agents use {label}",
    "llm-none": "[llm] no GPU endpoint is up, and no local endpoint either",
    "limit": "[limits] {window} usage at {pct}% (resets {reset})",
    "dash-down": "[alerts] dashboard is not responding on this machine",
    "dash-up": "[alerts] dashboard is back",
    "started": "[alerts] notifier started; {n_wait} waiting, {n_review} unreviewed",
    "suppressed": "[alerts] {n} notifications suppressed (rate limit)",
    "route-disabled": "[alerts] route {route} disabled: webhook rejected (HTTP {code})",
    "source-error": "[alerts] cannot read {source}",
    "test": "[test] niu-notify test message for #{route}",
}
PR_KINDS = {"pr-failing": "fail", "pr-changes": "changes", "pr-ready": "ready", "pr-blocked": "scope"}

DEFAULTS = {
    "poll_secs": 30,
    "routes": {"needs-you": "DISCORD_WEBHOOK_NEEDS_YOU", "activity": "DISCORD_WEBHOOK_ACTIVITY",
               "ci": "DISCORD_WEBHOOK_CI", "llm": "DISCORD_WEBHOOK_LLM",
               "limits": "DISCORD_WEBHOOK_LIMITS", "alerts": "DISCORD_WEBHOOK_ALERTS"},
    "silent_routes": ["activity", "ci", "llm"],
    "domains": {"PERSONAL": "full", "HOBBY": "full", "(workspace)": "full", "WORK": "full"},
    "endpoints": {},
    "wait_delay_secs": 60, "done_min_secs": 600, "down_after_polls": 2,
    "limit_thresholds": [80, 95], "pr_refresh_secs": 300,
    "cooldown_secs": 900, "wait_cooldown_secs": 120, "max_per_hour": 30, "error_cooldown_secs": 3600,
    "dash_port": 8765, "dash_check": "auto",
    "quiet_hours": None,
}


class ConfigError(ValueError):
    pass


# ---------- logging (never the URL, payload or exception text) ----------

_SCRUB_URLS = []


def scrub(text, urls=None):
    """Replace every known webhook URL and its token part with [webhook]."""
    for u in sorted(set(urls if urls is not None else _SCRUB_URLS), key=len, reverse=True):
        if not u:
            continue
        text = text.replace(u, "[webhook]")
        tok = u.rstrip("/").rsplit("/", 1)[-1]
        if len(tok) >= 20:
            text = text.replace(tok, "[webhook]")
    return text


def _log(msg):
    print(scrub(str(msg)), file=sys.stderr, flush=True)


# ---------- configuration ----------

def config_path():
    if os.environ.get("NIU_NOTIFY_CONFIG"):
        return Path(os.environ["NIU_NOTIFY_CONFIG"])
    p = SHARED / "discord.json"
    return p if p.exists() else SHARED / "discord.template.json"


def load_config(path=None):
    """DEFAULTS overlaid with discord.json (or the template); raises ConfigError on anything unsafe or malformed."""
    path = Path(path) if path else config_path()
    cfg = copy.deepcopy(DEFAULTS)
    if path.exists():
        try:
            user = json.loads(path.read_text())
        except ValueError as e:
            raise ConfigError(f"{path.name}: not valid JSON (line {getattr(e, 'lineno', '?')})") from None
        if not isinstance(user, dict):
            raise ConfigError(f"{path.name}: expected a JSON object")
        for k, v in user.items():
            if k.startswith("_"):
                continue
            if k in ("routes", "domains") and isinstance(v, dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    for k, typ in (("routes", dict), ("domains", dict), ("endpoints", dict), ("limit_thresholds", list), ("silent_routes", list)):
        if not isinstance(cfg[k], typ):
            raise ConfigError(f"{k} must be a JSON {'object' if typ is dict else 'array'}")
    for route, env in cfg["routes"].items():
        if route not in ROUTES:
            raise ConfigError(f"unknown route {route!r}")
        if not isinstance(env, str) or "://" in env or "/" in env:
            raise ConfigError(f"routes.{route} must name an environment variable, never hold a URL")
        if not RE_ENVNAME.fullmatch(env):
            raise ConfigError(f"routes.{route} must look like DISCORD_WEBHOOK_NAME")
    for d, m in cfg["domains"].items():
        if m not in DOMAIN_MODES:
            raise ConfigError(f"domains.{d} must be one of {', '.join(DOMAIN_MODES)}")
    for name, ep in cfg["endpoints"].items():
        if not isinstance(ep, dict) or ep.get("notify", "on") not in NOTIFY_MODES:
            raise ConfigError(f"endpoints.{name}.notify must be one of {', '.join(NOTIFY_MODES)}")
        dap = ep.get("down_after_polls", 1)
        if not isinstance(dap, int) or isinstance(dap, bool) or dap < 1:
            raise ConfigError(f"endpoints.{name}.down_after_polls must be a positive integer")
        if not isinstance(ep.get("display", ""), str):
            raise ConfigError(f"endpoints.{name}.display must be a string")
    for k in ("poll_secs", "wait_delay_secs", "done_min_secs", "down_after_polls", "pr_refresh_secs",
              "cooldown_secs", "wait_cooldown_secs", "max_per_hour", "error_cooldown_secs", "dash_port"):
        if not isinstance(cfg[k], (int, float)) or isinstance(cfg[k], bool) or cfg[k] < 0:
            raise ConfigError(f"{k} must be a non-negative number")
    cfg["poll_secs"] = max(5, cfg["poll_secs"])
    cfg["max_per_hour"] = max(1, int(cfg["max_per_hour"]))
    if not all(isinstance(t, (int, float)) and not isinstance(t, bool) and 0 < t <= 100 for t in cfg["limit_thresholds"]):
        raise ConfigError("limit_thresholds must be percentages")
    cfg["limit_thresholds"] = sorted(cfg["limit_thresholds"])
    for r in cfg["silent_routes"]:
        if r not in ROUTES:
            raise ConfigError(f"silent_routes: unknown route {r!r}")
    qh = cfg["quiet_hours"]
    if qh is not None:
        if not isinstance(qh, dict) or not all(re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", str(qh.get(k, ""))) for k in ("start", "end")):
            raise ConfigError("quiet_hours needs start and end as HH:MM")
        exc = qh.get("except", [])
        if not isinstance(exc, list) or any(r not in ROUTES for r in exc):
            raise ConfigError("quiet_hours.except must be a list of route names")
    return cfg


def webhook_status(cfg, env=None):
    """{route: 'ok' | 'missing' | 'invalid' | 'fallback'} without ever returning a value."""
    env = os.environ if env is None else env
    out = {}
    for route in ROUTES:
        v = env.get(cfg["routes"].get(route, ""), "")
        out[route] = "missing" if not v else "ok" if RE_WEBHOOK.fullmatch(v) else "invalid"
    if out["needs-you"] == "missing" and out["alerts"] == "ok":
        out["needs-you"] = "fallback"
    return out


def drop_webhook_env(cfg, env=None):
    """Remove the webhook variables from this process's environment so child processes (gh, git) never inherit them."""
    env = os.environ if env is None else env
    for k in [k for k in env if k.startswith("DISCORD_WEBHOOK_") or k in cfg["routes"].values()]:
        env.pop(k, None)


def webhook_urls(cfg, env=None):
    """{route: url} for valid webhooks only; needs-you falls back to alerts."""
    env = os.environ if env is None else env
    urls = {}
    for route in ROUTES:
        v = env.get(cfg["routes"].get(route, ""), "")
        if v and RE_WEBHOOK.fullmatch(v):
            urls[route] = v
    if "needs-you" not in urls and "alerts" in urls:
        urls["needs-you"] = urls["alerts"]
    return urls


# ---------- field rules ----------

def where(domain, project, cfg):
    """'DOMAIN/project' per the domain's mode; None when the domain is off."""
    domain = domain or "(workspace)"
    mode = cfg["domains"].get(domain, "full")
    if mode == "off":
        return None
    d = "workspace" if domain == "(workspace)" else domain if RE_DOMAIN.fullmatch(domain) else "(domain)"
    if mode == "minimal" or not project:
        return d
    if RE_PROJECT.fullmatch(project) and ".." not in project and not project.startswith("/"):
        return f"{d}/{project}"
    return f"{d}/(project)"


def sanitize_title(title):
    from niu_runs import redact
    t = redact(str(title or ""))[0]
    t = RE_TITLE_DROP.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()[:60].strip()
    return f"`{t}`" if t else "(no title)"


def sanitize_label(label, fallback):
    label = str(label or "")
    return label if RE_LABEL.fullmatch(label) else fallback


def _hhmm(ts):
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return "?"


def _fields(msg, cfg):
    """Validated template fields, or None to drop the message."""
    from niu_state import dur
    f, out = msg.get("f", {}), {}
    if "domain" in f or "project" in f:
        w = where(f.get("domain"), f.get("project"), cfg)
        if w is None:
            return None
        out["where"] = w
    if "ref" in f:
        m = RE_REF.fullmatch(str(f["ref"]))
        if not m:
            return None
        out["ref"] = m.group(0)
        out["pr_url"] = f"https://github.com/{m.group(1)}/{m.group(2)}/pull/{m.group(3)}"
    if "title" in f:
        out["title"] = sanitize_title(f["title"])
    if "label" in f:
        out["label"] = sanitize_label(f["label"], "endpoint")
    for k in ("n", "n_wait", "n_review", "code", "pct"):
        if k in f:
            try:
                out[k] = int(round(float(f[k])))
            except (TypeError, ValueError):
                return None
    if "secs" in f:
        out["dur"] = dur(f["secs"])
    if "window" in f:
        if f["window"] not in LIMIT_LABELS:
            return None
        out["window"] = LIMIT_LABELS[f["window"]]
    if "reset" in f:
        out["reset"] = _hhmm(f["reset"])
    if "route" in f:
        if f["route"] not in ROUTES:
            return None
        out["route"] = f["route"]
    if "source" in f:
        if f["source"] not in SOURCES:
            return None
        out["source"] = f["source"]
    return out


def render(msg, cfg, urls=()):
    """Message -> Discord payload (dict) or None (dropped). Only fixed templates; no embeds, no mentions."""
    from niu_runs import redact
    tpl = TEMPLATES.get(msg.get("tpl"))
    if tpl is None:
        return None
    fields = _fields(msg, cfg)
    if fields is None:
        return None
    try:
        content = tpl.format(**fields)
    except (KeyError, IndexError, ValueError):
        return None
    red, n = redact(content)
    if n:
        if "title" in fields:
            fields["title"] = "(redacted)"
            content = tpl.format(**fields)
            red, n = redact(content)
        content = red
    content = scrub(content, list(urls) + _SCRUB_URLS)[:MAX_CONTENT]
    flags = FLAG_SUPPRESS_EMBEDS | (FLAG_SILENT if msg.get("silent") else 0)
    return {"content": content, "username": "niu-heimar", "allowed_mentions": {"parse": []}, "flags": flags}


# ---------- observe ----------

def _dash_up(cfg):
    check = cfg.get("dash_check", "auto")
    if check is False or check == "off":
        return None
    if check == "auto" and not (Path.home() / ".config" / "systemd" / "user" / "niu-dash.service").exists():
        return None
    try:
        with _opener.open(f"http://127.0.0.1:{int(cfg['dash_port'])}/healthz", timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _sessions():
    import niu_state
    lv = niu_state.live(6 * 3600)
    lv.refresh()
    return lv.json_for(None, 1)["sessions"]


def _prs(cfg, now):
    import niu_prs
    cache = niu_prs.load_cache()
    if not cache or now - (cache.get("fetched") or 0) > cfg["pr_refresh_secs"]:
        cache = niu_prs.fetch_all()
    return niu_prs.summarize(cache)


def _llm():
    import niu_ollama
    return niu_ollama.status()


def _limits(now):
    import niu_limits
    lim = niu_limits.claude_limits(now)
    return lim.get("windows", []) if lim.get("available") else []


def observe(cfg, now, urls, sources=None):
    """Plain observation dict. A failing source becomes None plus an entry in 'errors' (never raises)."""
    src = {"sessions": _sessions, "prs": lambda: _prs(cfg, now), "llm": _llm, "limits": lambda: _limits(now),
           "dash": lambda: _dash_up(cfg)}
    src.update(sources or {})
    obs = {"errors": []}
    names = {"sessions": "sessions", "prs": "github", "llm": "llm", "limits": "limits", "dash": "dashboard"}
    for key, fn in src.items():
        if key == "prs" and "ci" not in urls:
            obs[key] = None
            continue
        try:
            obs[key] = fn()
        except Exception:  # noqa: BLE001 - a broken source is reported, never fatal
            obs[key] = None
            obs["errors"].append(names.get(key, key))
    if obs.get("sessions") is not None:
        obs["sessions"] = [{k: s.get(k) for k in ("id", "domain", "project", "state", "since", "ended", "note", "flagged")}
                           for s in obs["sessions"]]
    if obs.get("llm") is not None:
        # url and error can hold private hostnames / IPs: dropped here, never reach diff or render.
        obs["llm"] = [{"name": e.get("name"), "label": e.get("label"), "mode": e.get("mode", "agent"),
                       "priority": i + 1, "up": bool(e.get("up"))} for i, e in enumerate(obs["llm"])]
    return obs


# ---------- diff (pure) ----------

def _pr_index(prs):
    idx = {}
    for r in (prs or {}).get("repos", []):
        dom = (r.get("label") or "(workspace)").split("/", 1)
        for p in r.get("prs", []):
            idx[p.get("ref")] = (dom[0], dom[1] if len(dom) > 1 else "", p)
    return idx


def _same_reset(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 600  # the status line and the usage API report the same reset with jitter
    return a == b


def diff(prev, obs, cfg, now):
    """(new_state, [msg]). msg = {key, route, tpl, f, silent}. The first run only records a baseline."""
    from niu_actions import actions
    st = copy.deepcopy(prev) if prev else {}
    first = not st.get("baselined")
    keys = st.setdefault("keys", {})
    recent = {k: t for k, t in st.setdefault("recent", {}).items() if now - t < max(cfg["cooldown_secs"], cfg["error_cooldown_secs"])}
    st["recent"] = recent
    msgs = []

    def fire(key, msg, cooldown=None):
        if first:
            return False
        cd = cooldown if cooldown is not None else cfg["wait_cooldown_secs"] if key.startswith("wait:") else cfg["cooldown_secs"]
        if key in recent and now - recent[key] < cd:
            return False
        recent[key] = now
        msgs.append({"key": key, **msg})
        return True

    def track(prefix, active):
        """active: {key: (msg, delay_secs, polls, recover_msg|None)}; clears keys under prefix that went away."""
        for key, (msg, delay, polls, recover) in active.items():
            k = keys.get(key)
            if k is None:
                k = keys[key] = {"since": now, "polls": 0, "sent": first, "told": False}
            k["polls"] += 1
            k["recover"] = recover
            if not k["sent"] and k["polls"] >= polls and now - k["since"] >= delay:
                # Blocked by the cooldown -> stays unsent and is retried on a later poll while still active.
                k["sent"] = k["told"] = fire(key, msg)
        for key in [k for k in keys if k.startswith(prefix) and k not in active]:
            k = keys.pop(key)
            if k.get("told") and k.get("recover"):
                fire(key + ":up", k["recover"], cooldown=0)  # only after a delivered "down": never held back

    sessions = obs.get("sessions")
    prs = obs.get("prs")
    acts = actions({"sessions": sessions or [], "host": "workspace"}, prs or {"repos": []})
    by_sid = {s["id"]: s for s in sessions or []}
    if sessions is not None:
        active = {}
        for a in acts:
            s = by_sid.get((a.get("target") or {}).get("session"))
            if not s:
                continue
            f = {"domain": s.get("domain"), "project": s.get("project") or ""}
            if a["kind"] == "waiting":
                note = (s.get("note") or "").lower()
                tpl = "wait-permission" if "permission" in note else "wait-reply" if "idle" in note else "wait-other"
                active[a["id"]] = ({"route": "needs-you", "tpl": tpl, "f": f}, cfg["wait_delay_secs"], 1, None)
        track("wait:", {k: v for k, v in active.items() if k.startswith("wait:")})
        # working -> idle after a long run = "done"
        old, cur = st.get("sess", {}), {}
        for s in sessions:
            p, state = old.get(s["id"]), s.get("state")
            if state == "waiting" and "idle" in (s.get("note") or "").lower():
                state = "rest"  # the idle prompt after a turn: the run is over even if the idle state was never polled
            busy = p and p.get("state") in ("working", "waiting")
            carried = p.get("work_start") if busy else None
            if state == "working":
                cur[s["id"]] = {"state": state, "work_start": carried or s.get("since")}
                continue
            if state == "waiting":
                # waiting mid-run keeps the run's start; waiting from idle (the idle prompt) is not work
                cur[s["id"]] = {"state": state, "work_start": carried}
                continue
            cur[s["id"]] = {"state": state, "work_start": None}
            if state in ("idle", "rest") and busy and p.get("work_start"):
                secs = (s.get("since") or now) - p["work_start"]
                if secs >= cfg["done_min_secs"]:
                    fire(f"done:{s['id']}:{int(p['work_start'])}",
                         {"route": "activity", "tpl": "done",
                          "f": {"domain": s.get("domain"), "project": s.get("project") or "", "secs": secs}})
        st["sess"] = cur

    if prs is not None:
        idx = _pr_index(prs)
        active = {}
        for a in acts:
            if a["kind"] not in PR_KINDS:
                continue
            ref = (a.get("target") or {}).get("pr")
            dom, proj, p = idx.get(ref, ("(workspace)", "", {}))
            if cfg["domains"].get(dom, "full") != "full":
                continue  # PR/CI messages carry repo and title: only for domains in full mode
            f = {"ref": ref, "domain": dom, "project": proj, "title": p.get("title", "")}
            if a["kind"] == "pr-failing":
                f["n"] = (p.get("checks") or {}).get("failed", 0)
            active[a["id"]] = ({"route": "ci", "tpl": a["kind"], "f": f}, 0, 1, None)
        for pre in ("fail:", "changes:", "ready:", "scope:"):
            track(pre, {k: v for k, v in active.items() if k.startswith(pre)})

    llm = obs.get("llm")
    if llm is not None:
        active = {}
        for e in llm:
            epc = cfg["endpoints"].get(e["name"], {})
            mode = epc.get("notify", "on")
            if mode == "off":
                continue
            label = sanitize_label(epc.get("display") or e.get("label") or e.get("name"), f"endpoint {e['priority']}")
            silent = mode == "silent"
            if not e["up"]:
                active[f"llm:{e['name']}"] = ({"route": "llm", "tpl": "llm-down", "f": {"label": label}, "silent": silent}, 0,
                                              int(epc.get("down_after_polls", cfg["down_after_polls"])),
                                              {"route": "llm", "tpl": "llm-up", "f": {"label": label}, "silent": silent})
        agents = [e for e in llm if e.get("mode") == "agent"]
        if agents and not any(e["up"] for e in agents):
            fb = next((e for e in llm if e["up"]), None)
            msg = ({"route": "llm", "tpl": "llm-no-gpu",
                    "f": {"label": sanitize_label((cfg["endpoints"].get(fb["name"], {}).get("display") or fb.get("label")), "local")}}
                   if fb else {"route": "llm", "tpl": "llm-none", "f": {}})
            active["llm:no-gpu"] = (msg, 0, cfg["down_after_polls"], None)
        track("llm:", active)

    dash = obs.get("dash")
    if dash is not None:
        track("dash", {"dash": ({"route": "alerts", "tpl": "dash-down", "f": {}}, 0, 2,
                                {"route": "alerts", "tpl": "dash-up", "f": {}})} if not dash else {})

    windows = obs.get("limits")
    if windows is not None:
        lim = st.setdefault("limits", {})
        for w in windows:
            key, used, reset = w.get("key"), w.get("used"), w.get("resets_at")
            if key not in LIMIT_LABELS or not isinstance(used, (int, float)):
                continue
            rec = lim.get(key)
            if rec and not _same_reset(rec.get("resets_at"), reset):
                rec = None
            crossed = max((t for t in cfg["limit_thresholds"] if used >= t), default=None)
            if crossed is not None and (not rec or crossed > rec["t"]):
                lim[key] = {"t": crossed, "resets_at": reset}
                fire(f"limit:{key}:{crossed}:{int(reset or 0)}",
                     {"route": "limits", "tpl": "limit", "f": {"window": key, "pct": used, "reset": reset}})
            elif crossed is None and rec is None:
                lim.pop(key, None)

    for src in obs.get("errors", []):
        fire(f"sys:err:{src}", {"route": "alerts", "tpl": "source-error", "f": {"source": src}},
             cooldown=cfg["error_cooldown_secs"])

    st["baselined"] = True
    return st, msgs


# ---------- sending ----------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A webhook URL (it is the credential) must never be forwarded to a redirect target."""
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _retry_after(headers, body):
    try:
        v = json.loads(body or b"{}").get("retry_after")
        if isinstance(v, (int, float)):
            return float(v)
    except (ValueError, AttributeError):
        pass
    try:
        return float((headers or {}).get("Retry-After"))
    except (TypeError, ValueError):
        return None


class UrllibSender:
    """POST JSON to a webhook. Returns (status, retry_after); status 0 = network error. Never raises with the URL."""
    def post(self, url, payload):
        if not RE_WEBHOOK.fullmatch(url):
            return 0, None
        req = urllib.request.Request(url + "?wait=true", data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "niu-heimar-notify"})
        try:
            with _opener.open(req, timeout=10) as r:
                r.read(4096)
                return r.status, None
        except urllib.error.HTTPError as e:
            try:
                body = e.read(4096)
            except OSError:
                body = b""
            return e.code, _retry_after(e.headers, body)
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
            return 0, None


def in_quiet_hours(qh, now):
    if not qh:
        return False
    t = datetime.fromtimestamp(now).strftime("%H:%M")
    s, e = qh["start"], qh["end"]
    return s <= t < e if s <= e else (t >= s or t < e)


class Dispatcher:
    def __init__(self, sender, urls, cfg, clock=time.time, sleep=time.sleep, log=_log):
        self.sender, self.urls, self.cfg = sender, dict(urls), cfg
        self.clock, self.sleep, self.log = clock, sleep, log
        self.sent_times = []          # timestamps of posts in the last hour (global cap)
        self.suppressed = 0
        self.pending = []             # [(msg, attempts)] after 5xx / network errors
        self.disabled = set()         # webhook URLs rejected with 401/403/404
        self.last_post = {}
        self.backoff = {}             # url -> time until which Discord asked us to wait (429)
        _SCRUB_URLS[:] = sorted(set(_SCRUB_URLS) | set(self.urls.values()))

    def _room(self, now):
        self.sent_times = [t for t in self.sent_times if now - t < 3600]
        return len(self.sent_times) < self.cfg["max_per_hour"]

    def _post(self, url, payload):
        wait = 1.0 - (self.clock() - self.last_post.get(url, 0))
        if wait > 0:
            self.sleep(wait)
        status, ra = self.sender.post(url, payload)
        self.last_post[url] = self.clock()
        return status, ra

    def _deliver(self, msg, attempts=0, count=True):
        """Returns True when posted. Handles 429 (one retry), 5xx (queued, max 3 tries), 401/403/404 (route off)."""
        route = msg["route"]
        url = self.urls.get(route)
        if not url or url in self.disabled:
            return False
        now = self.clock()
        if now < self.backoff.get(url, 0):
            self.suppressed += 1
            return False
        if count and not self._room(now):
            self.suppressed += 1
            return False
        silent = msg.get("silent") or route in self.cfg["silent_routes"] or (
            in_quiet_hours(self.cfg.get("quiet_hours"), now) and route not in (self.cfg["quiet_hours"] or {}).get("except", []))
        payload = render({**msg, "silent": silent}, self.cfg, self.urls.values())
        if payload is None:
            return False
        status, ra = self._post(url, payload)
        if status == 429:
            self.sleep(min(ra if ra is not None else 1.0, 60.0))
            status, ra = self._post(url, payload)
            if status == 429:
                self.backoff[url] = self.clock() + min(ra if ra is not None else 60.0, 3600.0)
                self.suppressed += 1
                self.log(f"niu-notify: {route}: rate limited by Discord, message dropped")
                return False
        if 200 <= status < 300:
            if count:
                self.sent_times.append(now)
            return True
        if status == 0 or status >= 500:
            if attempts + 1 < 3:
                self.pending.append((msg, attempts + 1))
            self.log(f"niu-notify: {route}: send failed (HTTP {status or 'network error'}), attempt {attempts + 1}/3")
            return False
        if status in (401, 403, 404):
            self.disabled.add(url)
            shared = [r for r in ROUTES if self.urls.get(r) == url]
            self.log(f"niu-notify: {', '.join(shared)}: webhook rejected (HTTP {status}); disabled until restart")
            for r in shared:
                if r != "alerts":
                    self._deliver({"key": f"sys:disabled:{r}", "route": "alerts", "tpl": "route-disabled",
                                   "f": {"route": r, "code": status}}, count=False)
            return False
        self.log(f"niu-notify: {route}: webhook returned HTTP {status}; message dropped")
        return False

    def send(self, msgs):
        retry, self.pending = self.pending, []
        order = {r: i for i, r in enumerate(ROUTES)}
        sent = 0
        for msg, attempts in retry:
            sent += self._deliver(msg, attempts)
        for msg in sorted(msgs, key=lambda m: order.get(m["route"], 99)):
            sent += self._deliver(msg)
        if self.suppressed and self._room(self.clock()):
            n, self.suppressed = self.suppressed, 0
            sent += self._deliver({"key": "sys:suppressed", "route": "alerts", "tpl": "suppressed", "f": {"n": n}})
        return sent


# ---------- state ----------

def state_path():
    return db_path().parent / "notify-state.json"


def load_state(path=None):
    try:
        st = json.loads((path or state_path()).read_text())
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(st, path=None):
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(st, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


class Notifier:
    def __init__(self, cfg, sender=None, env=None, sources=None, path=None, clock=time.time, sleep=time.sleep, log=_log):
        self.cfg, self.sources, self.path, self.clock = cfg, sources, path, clock
        self.urls = webhook_urls(cfg, env)
        if env is None:
            drop_webhook_env(cfg)
        self.dispatcher = Dispatcher(sender or UrllibSender(), self.urls, cfg, clock=clock, sleep=sleep, log=log)
        self.state = load_state(path)
        self.started = False

    def poll(self):
        now = self.clock()
        obs = observe(self.cfg, now, self.urls, self.sources)
        self.state, msgs = diff(self.state, obs, self.cfg, now)
        if not self.started:
            self.started = True
            sess = obs.get("sessions") or []
            msgs.insert(0, {"key": "sys:started", "route": "alerts", "tpl": "started",
                            "f": {"n_wait": sum(1 for s in sess if s.get("state") == "waiting" and not s.get("ended")),
                                  "n_review": sum(1 for s in sess if s.get("flagged") and not s.get("ended"))}})
        sent = self.dispatcher.send(msgs)
        save_state(self.state, self.path)
        return sent


def unit_text(shared=None):
    shared = Path(shared or SHARED)
    return f"""[Unit]
Description=niu-heimar Discord notifier (outbound webhooks only)
After=network-online.target

[Service]
ExecStart={shared}/bin/niu-notify run
EnvironmentFile=-{shared}/.env
Restart=on-failure
RestartSec=30
NoNewPrivileges=true
PrivateTmp=true
UMask=0077
LockPersonality=true
SystemCallArchitectures=native
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK

[Install]
WantedBy=default.target
"""
