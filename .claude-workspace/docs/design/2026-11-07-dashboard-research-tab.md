# Dashboard "Research" tab: monitor the research-dispatch agents
Status: proposed · Date: 2026-11-07 · Request: "Will these research agents show in the dashboard? If not, design and implement it so they can be seen or monitored there, in a dedicated tab."

## Decision summary (read this first)
- **Answer: no, not today.** The research agents are Claude Agent SDK sessions inside the research-dispatch container. They run with `setting_sources=[]`, so no Claude Code hooks load (`PERSONAL/research-dispatch/docs/design/2026-11-06-research-dispatch-pipeline.md` §5). They also can't reach the host's `~/.local/state/niu-heimar/events.db`. The dashboard learns sessions only from that table, so they are invisible. The only trace today is indirect: their use of the plan shows up in the shared Claude limits on the LLM card (`/api/llm`, `bin/niu-dash:236-243`).
- **Data feed: option B, the dashboard polls research-dispatch.** niu-dash gets a small server-side poller (`lib/niu_research.py`) built like the PR cache (`bin/niu-dash:90-113`). It calls one fixed URL, `http://127.0.0.1:8080/api/v1/monitor/summary`, at most once every 60 s, and only while someone is viewing. It re-shapes the answer through an allowlist and serves it as `GET /api/research`. research-dispatch already owns the run data and has named tokens, and the browser never talks to research-dispatch, so the CSP stays as it is.
- **research-dispatch adds one versioned, read-only endpoint, `GET /api/v1/monitor/summary` (`monitor.v1`).** It carries only numbers, enums, timestamps, model IDs and slot keys. It never carries free text, prompts, grounded content, URLs or tokens. A new, narrower token scope, `monitor`, can read only this endpoint (`read` and `admin` include it).
- **Build now, in M1:** the endpoint with health, schedule (next slot), a gate preview (cached 5 min, never a probe), recent gate decisions and today's ledger. `current_run`, `last_run` and `recent_runs` are `null`/`[]`, with `features.runs=false`. **M2** fills in runs, agents and digest status. **M4** adds the fintrend lane.
- **Token on the host:** `~/.local/state/niu-heimar/research-dispatch.token`, mode 0600. That is next to the existing `web-sessions.json` credential file and not a `.env` file. You write it yourself with `niu-dash research-token`, which needs a terminal and reads the token with `getpass`. `block-env.sh` blocks agents from reading the file or running that command.
- **Tab UI:** a fourth layout in the existing grid (`data-view="research"`). The left column stays. On the right there are five panels: a status strip with a next-run countdown, a gate card (the same bars as the Claude limits, plus the 10% and 20% floor ticks), a quota card, 36-hour run lanes per agent (the same SVG classes as the timeline), and a last-run table and error list. The tab polls every 30 s (the server caches for 60 s). It does not use SSE.
- **No "dismiss stuck run" button.** research-dispatch already times agents out (600 s) and recovers stale runs at startup. The dashboard holds a read-only token and can't write anyway. A run that is `running` for more than 45 minutes gets a "possibly stuck" label and a restart hint.
- **Two PRs:** research-dispatch (the monitor endpoint plus the `monitor` scope) goes first. Then the workspace repo (dashboard, `block-env.sh`, docs, tests) goes through `/workspace-sync`. Each goes through `/dual-review`. The workspace PR touches a hook and a credential path, so its security review runs on Opus.

## Context
**Dashboard (host, `.claude-workspace/`)**
- `bin/niu-dash:396` is a stdlib `ThreadingHTTPServer` on `127.0.0.1:8765`. It runs as a systemd user unit (`bin/install:113-129`, no `EnvironmentFile`) and is published to the tailnet with `tailscale serve` (`bin/install:138-143`).
- **Security headers:** a strict CSP with `default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'` (`bin/niu-dash:146-148`). This means no inline scripts and no `style="..."` attributes in HTML strings. Setting `el.style.x` from JS through the CSSOM is still allowed (`app.js:231` does it).
- **Auth:** GETs are unauthenticated. POSTs need `X-Niu-CSRF: 1` plus a same-origin `Origin` (`_post_ok`, `bin/niu-dash:172-182`). Writes also need a paired-device cookie.
- **Background cache pattern:** `refresh_prs()` / `prs_json()` (`bin/niu-dash:90-113`). `PR_REFRESH_SECS = 60`. A stale cache is served at once while a daemon thread refreshes it, and nothing is fetched unless a request asks.
- **Credential files:** they live in `db_path().parent`, which is `~/.local/state/niu-heimar/` (`lib/niu_events.py:43-47`). They are written atomically with mode 0600 in a 0700 directory (`save_auth`, `bin/niu-dash:64-70`). `block-env.sh:49-53` blocks agents from `web-sessions.json`, and `:54-57` blocks Bash from the user-only dashboard endpoints.
- **Outbound HTTP pattern:** `lib/niu_limits.py:34-40` uses a no-redirect opener, a fixed URL, a body cap (`r.read(65536)`) and "any failure means no numbers" (`:57-58`). Note that `urllib`'s default opener also honours `http_proxy`. The new poller must turn proxies off explicitly. The limits code doesn't need to, because its target is a fixed https host.
- **Config pattern:** `ollama.template.json` is committed and `ollama.json` is gitignored (`lib/niu_ollama.py:21-26`, `.gitignore:30`). `discord.json` works the same way (`.gitignore:31`).
- **Front end** (`web/app.js`):
  - Tabs are `setView("agents"|"events")` (`:113-126`). The CSS switches panels on `.mcgrid[data-view]` (`style.css:325-335`).
  - Claude limits render as `limrow` + `<progress class="lim ok|warn|bad">` (`claudeLimits`, `app.js:870-897`). `loadLLM()` polls every 30 s while the page is visible (`:898-922`).
  - Timeline lanes are SVG with the classes `lane`, `bar-sub`, `running`, `axis`, `nowline` and `lbl` (`renderTimeline`, `:677-716`; `style.css:160-161`).
  - The `data-tip` tooltip is bound only to `#graph` and `#timeline` (`app.js:233`).
  - `esc()` is used for all HTML interpolation (`:5`).
- **Tests:**
  - `tests/web_test.py` starts a real `niu-dash serve` on a random port with a temp event store and a fake `gh`. It checks headers, routes and POST guards, then runs `tests/web_smoke.js` (a fake DOM, `app.js` rendered against a real `/api/state`).
  - Each `tests/*_test.py` has a `tests/*.test.sh` wrapper. `bin/workspace-lint` runs every wrapper.
  - `tests/block-env.test.sh` covers the hook.

**research-dispatch (container, `PERSONAL/research-dispatch/`)**
- Compose publishes `${BIND_ADDR:-127.0.0.1}:8080:8080` (`docker-compose.yml:9-10`). The README already documents `curl -H "Authorization: Bearer $READ_TOKEN" http://127.0.0.1:8080/api/v1/usage/gate` (`README.md:33`).
- **Tokens:** `rd_pat_` + `token_urlsafe(32)` (`core/security.py:14,32-35`). Scopes are `read` and `admin`, and `admin` implies `read` (`:12-13,74-76`). They are configured as `API_TOKENS=name:scope:sha256` (`:38-61`). `rd gen-token --name --scope` prints the token once (`cli.py:21-36`). Dependencies are `Reader` and `Admin` (`api/deps.py:36-40`).
- **M1 status (in progress):**
  - `usage/` (reader, gate, watcher) and `GET /api/v1/usage/gate` exist (`api/routers/usage.py:62-93`).
  - That route **calls Anthropic's usage endpoint live on every request** (`get_evaluator(...).evaluate()`, `:68`). No probe is wired in (`gate.py:134`, `usage/cli.py:45`), but polling it every minute would still mean one Anthropic call a minute. The monitor endpoint must not do that.
  - The tables `usage_readings` and `usage_ledger` exist (`db/models.py:87-121`), but nothing writes to them yet. `add_reading` and `bump_ledger` (`db/repositories/usage.py:16-96`) have no callers.
  - There is no `research_runs` or `agent_runs` table yet. They arrive in M2 (pipeline design §2 and Rollout).
  - `core/timeutil.py:65` already has `next_slot(after, tz, hours)`.
  - `/readyz` reports `db` and `migrations` (`api/routers/health.py:34-49`). Pipeline design §3 adds `scheduler` and `claude_auth`.
- The pipeline design §10 lists the observability views as `GET /usage/gate`, `/runs/{id}` and `/stats/topics`. This design adds a summary view built for monitoring.

## Requirements
- **Must:**
  - Give the dashboard a dedicated "Research" tab. It shows the next scheduled slot, the gate decision and why (with percentages and floors), any gate retry in progress, per-agent status for the current or last run (general, hobby, fintrend), each agent's model, times, duration, turns and searches against their caps, tokens and window share, items kept and filtered, error codes, digest delivery status, and today's quota ledger.
  - Work with partial data. Under M1 the tab shows the gate, schedule and ledger, and says "run history arrives with research-dispatch M2".
  - Degrade clearly. The tab has distinct states for not configured, unreachable, token rejected, incompatible version and stale data, and the dashboard never crashes.
  - Use stdlib only on the dashboard side, and add no new process.
- **Must not:**
  - Send any secret to the browser or to logs: no token, no webhook URL, no Claude credential.
  - Send prompts, topic text, grounded or web-derived text, transcripts, item titles or URLs, or free-form error messages.
  - Let the dashboard call anything but the configured loopback base URL plus fixed paths. It must not follow redirects or use proxies.
  - Use `.env` files or environment dumps for the token.
  - Let anything on the dashboard trigger, cancel or change a research run. The pipeline design forbids any endpoint that starts a run (§3, §9).
  - Make the Anthropic usage endpoint get called more often than once every 5 minutes because of monitoring, or ever run a Haiku probe for it.

## Options considered
**Data feed**
1. **A. Push run events into the dashboard's SQLite `events` table.** Pros: research sessions could also show up in the Agents graph. Cons:
   - It needs a bind mount of the host's `events.db` into a `read_only`, uid-10001 container, which breaks the container isolation (§9) and brings WAL locking problems across the mount.
   - The `events` schema (session, tool, 120-character summary, 7-day retention) has no place for gates, ledgers or digest status.
   - It duplicates data research-dispatch already stores.

   Rejected.
2. **B. The dashboard polls research-dispatch's HTTP API.** Pros:
   - research-dispatch stays the single owner of the data.
   - The token is read-only, and the endpoint is already on host loopback.
   - It uses the same cache-and-thread pattern as PRs.
   - Nothing changes for the browser or the CSP.
   - When research-dispatch is down, the tab says so, with no loss of data.

   Cons:
   - Data is up to about 60 s old.
   - The host needs a token on disk.

   **Chosen.**
3. **C. An authenticated POST ingest into the dashboard.** Pros: updates arrive instantly. Cons:
   - The container would have to reach the host's dashboard. That means binding niu-dash beyond `127.0.0.1` or adding `host-gateway` routing, which weakens the loopback-only rule (`web_test.py:104-107` asserts it).
   - It adds a second write credential and a new POST surface on a tailnet-published server.
   - Updates are lost while the dashboard is down, and research-dispatch gains a dependency on the dashboard.

   Rejected.

**What research-dispatch exposes**
1. **Reuse `GET /runs`, `/runs/{id}` and `/usage/gate`.** Cons:
   - It takes three or more calls per refresh.
   - `/usage/gate` calls Anthropic live every time.
   - `/runs/{id}` is aimed at duenext and the CLI, and it will grow fields this tab shouldn't carry.
   - The `read` scope also unlocks digests, which contain grounded text.
2. **A dedicated `GET /api/v1/monitor/summary` with a `monitor` scope (chosen).** One call, an exact field list, a version tag, and a token that can read nothing else. It is easy to build partially under M1.

**Refresh to the browser**
1. **Add the research data to the SSE snapshot.** Cons: every Agents-tab push gets bigger, and `_stream` would have to key on one more cache.
2. **The tab polls `/api/research` like `loadLLM` (chosen).** It is simple, it is isolated from the Agents tab, and the data changes at most once a minute anyway.

**Token location**
1. `.claude-workspace/.env` through systemd `EnvironmentFile=` (the niu-notify pattern). The request rules out `.env` here. It would also put the token in the environment of every `gh`/`git` child of niu-dash.
2. **A 0600 file in `~/.local/state/niu-heimar/` (chosen).** That directory already holds `web-sessions.json`. The token is read fresh on each fetch, so rotating it needs no restart.

## Design

### 1. Data flow
```
browser ──GET /api/research (30 s, visible only)──► niu-dash (host, 127.0.0.1:8765)
                                                     │ RESEARCH cache, refresh ≤ 1/60 s, background thread
                                                     │ token: ~/.local/state/niu-heimar/research-dispatch.token (0600)
                                                     ▼
                         GET http://127.0.0.1:8080/api/v1/monitor/summary  (Bearer rd_pat_…, scope monitor)
                                                     ▼
research-dispatch (container) ── reads SQLite (runs, agent_runs, run_items, dispatched_signals, usage_readings, usage_ledger)
                               └─ gate preview: GateEvaluator without probe, cached 300 s in-process
```

### 2. research-dispatch: `GET /api/v1/monitor/summary` (`monitor.v1`)
- **Auth:** `Monitor = Annotated[TokenEntry, Depends(require_scope("monitor"))]`. `scope_allows` becomes a hierarchy: `admin` ⊇ `read` ⊇ `monitor`. A `monitor` token gets 403 on every other route.
- **Behaviour:**
  - The response is read-only and makes no writes.
  - It has no query parameters, so it has no input to validate or abuse.
  - The usual `no-store` header and the existing error handlers apply.
  - All times are UTC ISO-8601 with `Z`.
  - Every list is bounded, and the target size is under 16 KiB (32 KiB is a test-enforced ceiling).
- **Gate preview:**
  - `usage/preview.py` `GatePreview(evaluator, ttl_s=300)` holds the last `(reading, decision, checked_at)` under an `asyncio.Lock`.
  - It is built with `GateEvaluator(reader, settings, probe=None, samples=…)`. It **never** gets a probe, so monitoring can't spend quota.
  - It is not persisted, because `usage_readings` is reserved for real gate checks and in-run samples.
- **Error codes instead of text:** `monitor/errors.py` `error_code(status, error) -> str` maps stored agent and run outcomes onto a fixed enum:
  - `timeout`, `aborted_quota`, `max_turns`, `max_budget`, `structure_invalid`, `no_sources`, `rate_limited`, `auth`, `interrupted`, `network`, `internal`.
  - Anything else becomes `internal`.
  - The free-text `error`, `error_summary`, `last_error` and `validation_errors` columns are never serialized.

**Response** (Pydantic models with `extra="forbid"`. Every field is listed, and `null` means unknown or not yet available.)
```json
{
  "schema": "monitor.v1",
  "generated_at": "2026-11-07T03:12:00Z",
  "service": {
    "version": "0.2.0",
    "features": {"runs": false, "digest": false, "agents": ["general"]},
    "health": {"db": true, "migrations": true, "scheduler": null, "claude_auth": null}
  },
  "schedule": {
    "enabled": true, "running": false, "cron": "0 0,12 * * *", "timezone": "Asia/Tokyo",
    "next_slot_key": "2026-11-07T12:00[Asia/Tokyo]", "next_slot_at": "2026-11-07T03:00:00Z",
    "manual_runs_today": 0, "manual_runs_per_day": 4
  },
  "gate": {
    "preview": {
      "checked_at": "…Z", "decision": "run", "reason": "ok", "allow": true, "conservative": false,
      "source": "usage_api",
      "five_hour": {"used_pct": 42.0, "resets_at": "…Z"}, "seven_day": {"used_pct": 61.0, "resets_at": "…Z"},
      "expected_run_pct": 5.0,
      "floors": {"five_hour_min_remaining_pct": 10, "weekly_min_remaining_pct": 20, "abort_remaining_pct": 5}
    },
    "pending": null,
    "recent": [{"taken_at": "…Z", "source": "usage_api", "decision": "run", "reason": "ok",
                "five_hour_used": 40.0, "seven_day_used": 60.0, "run_id": null}]
  },
  "current_run": null,
  "last_run": null,
  "recent_runs": [],
  "ledger": {
    "today": {"day": "2026-11-07", "runs": 0, "sessions": 0, "probes": 0, "tokens_in": 0, "tokens_out": 0,
              "web_searches": 0, "equiv_usd": 0.0},
    "last_7_days": []
  }
}
```
- **`gate.pending`** (M2), set when a `research_runs` row is `waiting_gate`: `{"run_id", "slot_key", "attempts", "max_attempts": 3, "next_check_at", "grace_until", "last_reason"}`.
- **`gate.recent`** holds at most 12 rows of `usage_readings WHERE decision IS NOT NULL`, newest first (reusing `recent_decisions`).
- **Run object** (M2), used for `current_run` and `last_run`:
```json
{
  "id": "uuid", "slot_key": "2026-11-07T00:00[Asia/Tokyo]", "trigger": "cron", "mode": "normal",
  "status": "partial", "scheduled_for": "…Z", "started_at": "…Z", "finished_at": "…Z", "duration_s": 412,
  "gate_attempts": 1, "gate_reason": null, "gate_source": "usage_api",
  "five_hour_used_before": 38.0, "five_hour_used_after": 44.5, "window_share_pct": 6.5, "error_code": null,
  "agents": [{
    "agent_type": "general", "status": "succeeded", "attempts": 1,
    "research_model": "claude-sonnet-5-5", "structure_model": "claude-haiku-5-5",
    "started_at": "…Z", "finished_at": "…Z", "duration_s": 188,
    "num_turns": 9, "max_turns": 12, "web_searches": 6, "max_web_searches": 8, "web_fetches": 0,
    "tokens_in": 91000, "tokens_out": 6200, "cache_read": 70000, "equiv_usd": 0.31,
    "items_kept": 5, "items_filtered": 3,
    "filtered_by": {"duplicate": 2, "excluded": 1, "no_update": 0}, "uncited_dropped": 1,
    "permission_denials": 0, "error_code": null
  }],
  "digest": {"status": "held_quiet_hours", "sections_total": 2, "sections_sent": 0,
             "next_attempt_at": "…Z", "last_http_status": null}
}
```
- **Sources:**
  - Agent `started_at` is `agent_runs.created_at`.
  - `duration_s` is `latency_ms / 1000`.
  - The caps come from the run's mode (the normal or conservative values in the pipeline design §5).
  - `items_*` and `filtered_by` are counts over `run_items`.
- **`digest.status`** is one of `not_built|pending|held_quiet_hours|sent|partial|failed|dead|nothing_new|suppressed`. It is derived from the run's `dispatched_signals` rows (`payload_kind='digest'`, Discord target).
- **`recent_runs`** (M2) holds at most 6 compact runs started in the last 48 h: `{id, slot_key, trigger, status, mode, started_at, finished_at, agents: [{agent_type, status, started_at, finished_at, error_code}]}`.
- **`ledger.last_7_days`** (M1) holds the rows of `usage_ledger` for the last 7 local days.
- **Enums validated on both sides:**
  - run status: `waiting_gate|running|succeeded|partial|failed|skipped_gate|aborted_quota`;
  - agent status: `pending|running|succeeded|failed|skipped|timed_out|aborted_quota`;
  - decision: `run|run_conservative|retry|skip`;
  - reason: `ok|ok_conservative|weekly_low|low_quota|probe_warning|no_signal|manual_cap|grace_expired|kill_switch`.

**What each milestone provides**
| Field | M1 (build now) | M2 | M4 |
|---|---|---|---|
| `service.health` | `db`, `migrations`. `claude_auth` once the M1 readiness check exists, else `null`. | `scheduler` | |
| `schedule` | Computed from settings with `core/timeutil.next_slot`. `running=false`. | `running` from the scheduler. Manual-run count from `research_runs`. | |
| `gate.preview` | Yes (cached, no probe) | | |
| `gate.recent` | Yes, once M1's `rd run` gate check writes `usage_readings` (the pipeline design §2 already says every gate check is recorded). Add that write in this PR if M1 merged without it. | Plus `run_id` | |
| `gate.pending` | `null` | Yes | |
| `ledger` | Yes (`rd run --dry-run` should `bump_ledger`, as above) | | |
| `current_run`, `last_run`, `recent_runs` | `null` / `[]`, `features.runs=false` | Yes, `features.runs=true` | fintrend rows |
| `digest` | absent | Yes, `features.digest=true` | |
| `features.agents` | `["general"]` | `["general","hobby"]` | plus `"fintrend"` |

### 3. Dashboard: poller and `GET /api/research`
**New `lib/niu_research.py`** (stdlib only):
- **`load_config()`** reads the first file found among `NIU_RESEARCH_CONFIG`, `research.json` (gitignored) and `research.template.json`. The template is `{"enabled": true, "url": "http://127.0.0.1:8080", "poll_secs": 60, "timeout_secs": 4}`.
- **`validate_url(u)`** accepts only `^http://(127\.0\.0\.1|\[::1\]):[0-9]{1,5}$` with a port from 1 to 65535. That rules out `localhost`, other hostnames, userinfo, paths, queries and https to anything else. Anything invalid becomes state `error` with "research URL must be http://127.0.0.1:PORT".
- **`token_path()`** is `NIU_RESEARCH_TOKEN_FILE` (a path, used by tests) or `db_path().parent / "research-dispatch.token"`.
- **`read_token()`** reads the file fresh on each fetch.
  - It refuses the file unless it is a regular file owned by the current uid with `mode & 0o077 == 0`, using `os.stat` with no `follow_symlinks`.
  - The content must match `^rd_pat_[A-Za-z0-9_-]{20,128}$`.
  - It returns `None` or the string. The value is never logged, put in exceptions or cached in a module global.
- **`fetch(cfg, token)`:**
  - The opener is `build_opener(ProxyHandler({}), _NoRedirect)`, so there are no env proxies and no redirects.
  - It sends `GET {url}/api/v1/monitor/summary` with `Authorization: Bearer …`, `Accept: application/json` and `User-Agent: niu-heimar-dashboard`.
  - The timeout is `timeout_secs`. It reads at most 128 KiB + 1 byte, and more than that is an error.
  - The response must have `Content-Type: application/json` and parse into a dict whose `schema` is `monitor.v1`.
- **`shape(raw, now) -> dict`** is a pure allowlist rebuild of the structure in §2:
  - Unknown keys are dropped.
  - Enums are checked against the lists above, and anything else becomes `"unknown"`.
  - Percentages are clamped to 0–100 and counts to non-negative ints.
  - Times are parsed into epoch seconds, which is what the front end uses everywhere else.
  - `slot_key` must match `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}\[[A-Za-z0-9_+/-]{1,40}\]$` or `^manual:[0-9a-f-]{36}$`. Model IDs must match `^[a-z0-9.-]{1,40}$`. Anything that fails becomes `null`.
  - No string from upstream passes through without a regex or enum check.
- **`summary(force=False)`** follows the `refresh_prs` model:
  - It keeps `RESEARCH = {"cache", "ok_data", "ok_at", "busy", "last_force"}`.
  - A daemon thread refreshes when the cache is older than `poll_secs`.
  - `force` is throttled to once every 10 s.
  - It returns `{state, message, data, fetched_at, ok_at, stale, refreshing, server_now}`.

**States** (each `message` is a fixed string from a table, never `str(exc)`):
| `state` | When | Tab shows |
|---|---|---|
| `disabled` | `enabled: false` | The tab button is hidden. |
| `not_configured` | No token file | Setup steps (§7) |
| `loading` | First fetch is in flight | "Checking research-dispatch…" |
| `ok` | 200 and valid `monitor.v1` | Data |
| `unreachable` | Connection refused, timeout or DNS-free socket error | "research-dispatch is not reachable on 127.0.0.1:8080. Is the container running? (`docker compose ps` in PERSONAL/research-dispatch)" |
| `unauthorized` | 401 or 403 | "Token rejected: check `API_TOKENS` in research-dispatch and re-run `niu-dash research-token`" |
| `incompatible` | 404, a redirect (3xx), wrong content type, or a `schema` other than `monitor.v1` | "research-dispatch has no compatible monitor endpoint (needs monitor.v1)" |
| `error` | 5xx, too large, bad JSON, bad URL or bad token file permissions | A fixed message per cause |

- On any failure the last good `data` is kept and returned with `stale: true`. The tab greys it out and shows "as of HH:MM". `stale` is also set when `now - ok_at > 3 × poll_secs`.
- The poller writes nothing to stdout or stderr. niu-dash's request log stays off unless `NIU_DASH_DEBUG` is set (`bin/niu-dash:134-136`), and it never sees the token in any case.

**`bin/niu-dash` changes:**
- `GET /api/research` returns `self._json(niu_research.summary())`.
- `POST /api/research/refresh` goes through `_post_ok` (CSRF header and same-origin), the same as `/api/prs/refresh`, and needs no pairing. It calls `summary(force=True)`.
- **New subcommand `niu-dash research-token [--remove]`:**
  - It requires `niu_prs.can_prompt()`, as `pair` does.
  - It reads the token with `getpass.getpass("research-dispatch monitor token: ")` and validates the format.
  - It writes atomically with mode 0600 into the 0700 state directory, then prints "saved (rd_pat_…xxxx)", showing the last 4 characters only.
  - `--remove` deletes the file.
- `cmd_serve` does not pre-fetch, so the first viewer triggers the first fetch.

### 4. Tab UI
**Markup** (`web/index.html`):
- `<button id="tab-research" class="tab" aria-pressed="false">Research <span id="rsdot" class="tabdot" hidden></span></button>`.
- One new panel container in `<main>`: `<section class="panel p-research" aria-label="Research agents">` holding the five blocks below. Static markup only; content is filled by JS with `esc()`.

**Layout** (`data-view="research"`): grid areas `"left rhead" "left rbody"`. The left column (sessions, needs-you, LLMs) stays, as in the Events tab. Under 900 px everything stacks.
1. **Status strip (`#rshead`):**
   - A health pill (`llmpill up|partial|down`): "research-dispatch ok / degraded / unreachable / not configured".
   - "Next run 12:00 Asia/Tokyo · in 3h 12m". The countdown is computed from `next_slot_at` with the server clock offset, and ticks in the existing 1 s interval only while this view is active.
   - "Scheduler off" (warn) when `schedule.enabled=false`.
   - Manual runs today, x/4.
   - "updated HH:MM:SS" and a Refresh button.
2. **Gate card (`#rsgate`):**
   - A decision pill: run (ok), run conservatively (warn), retry or skip (bad), with a fixed label for each reason (`low_quota` reads "5-hour window too full", and so on).
   - Two rows reusing the `limrow` / `progress.lim` / `limpct` markup from `claudeLimits` (`app.js:888-894`), fed with the monitor's numbers.
   - Each row has a small SVG strip under the bar that marks the floor:
     - 5-hour: a tick at `100 − 10 − expected_run_pct`, labelled "gate floor";
     - weekly: a tick at 80%, labelled "20% weekly floor".
   - The ticks are SVG `x` attributes, not inline styles, so the CSP is respected.
   - Source and age: "usage API · checked 4 min ago" or "probe". If `gate.preview` is `null`, the rows fall back to the dashboard's own `claude_limits()` from `/api/llm`, labelled "dashboard reading".
   - When `gate.pending` is set: "Waiting for the gate: attempt 2 of 3, next check 12:30, gives up at 15:00 (5-hour window too full)".
   - The last 5 `gate.recent` rows as a compact list.
3. **Quota card (`#rsquota`):**
   - Today's ledger as a key/value grid: runs, sessions, probes, web searches, tokens in/out, ≈ USD (labelled "API-equivalent estimate").
   - A 7-day mini bar chart of `equiv_usd` (SVG rects).
   - The weekly-floor reminder: "Bot stops when less than 20% of the weekly limit remains".
4. **Run lanes (`#rslanes`, SVG):**
   - The window covers the last 36 h. Lanes are `gate`, then one per entry in `features.agents`. A lane for an agent that isn't enabled yet is drawn muted with "arrives in M4" (fintrend) or "M2" (hobby).
   - Agent bars use `bar-sub` (`running` while active) plus new status classes `rs-ok`, `rs-warn` and `rs-bad`.
   - Gate checks are circles on the gate lane, coloured by decision.
   - The 00:00 and 12:00 slot lines use `axis`, and the now line uses `nowline`.
   - Tooltips use `data-tip` ("general · succeeded\n00:00:12 → 00:03:20 · 9/12 turns · 6/8 searches"). `#rslanes` is added to the tooltip binding list at `app.js:233`.
   - Under M1: one gate lane with dots and a note "Run history arrives with research-dispatch M2".
5. **Last run and errors (`#rslast`, `#rserrors`):**
   - A header line: slot key, trigger, mode, status pill, duration, window share, and digest status ("held for quiet hours until 07:00", "sent 2/2", "nothing new", "failed (HTTP 429)").
   - A table with one row per agent: agent, status, models (via the existing `shortModel`), started, duration, turns (used/max), searches (used/max), tokens in/out, ≈ USD, kept, filtered (with the dup/excl/no-update split in the title), error label.
   - `current_run`, when present, is shown above with a live elapsed time. If it has been running for more than 45 min, it is marked "possibly stuck · run `docker compose restart app` in research-dispatch; it marks the run interrupted on startup".
   - The errors list holds up to 10 entries from `recent_runs` agents with an `error_code`, plus `skipped_gate` runs. Each shows a time, agent, slot and fixed label.

**Tab badge (`#rsdot`):**
- Red: last run `failed` or `aborted_quota`, `skipped_gate` in the last 24 h, or state `unreachable` or `unauthorized`.
- Amber: `partial`, `waiting_gate` or conservative mode.
- Green, pulsing: a run is in progress (no pulse with Reduce motion).
- Hidden otherwise.

**Refresh:**
- `loadResearch()` polls `/api/research` every 30 s while `!document.hidden`, on every tab (so the badge stays current), and at once when the tab is switched.
- On `loading` it re-polls after 3 s.
- The server cache keeps upstream calls to at most one a minute.

**Front-end changes** (`web/app.js`):
- `setView` accepts `"research"`, with hash `#research` and `store` key `niu.view`.
- New functions `loadResearch`, `renderResearch`, `rsHead`, `rsGate`, `rsQuota`, `rsLanes`, `rsLast`, `rsErrors`, plus label maps `RS_REASON`, `RS_STATUS`, `RS_ERROR` and `RS_DIGEST`.
- `renderTimeline` is left unchanged. The lane drawing reuses `svg()`, the CSS classes and `hhmmss`, but is a separate function, so the Agents tab carries no risk.

**Styles** (`web/style.css`):
- grid rules for `.mcgrid[data-view="research"]`, and hiding `.p-research` in the other views;
- `.rsgrid` (gate and quota side by side);
- `.rs-ok|warn|bad` for SVG bars and dots;
- `.floor` tick styling;
- `.tabdot`;
- the table styling from `.feed`;
- the stacking rule under 900 px.

All colours come from the existing variables (`--ok`, `--warn`, `--bad`, `--accent`), so dark mode works.

**Dismissing a stuck run: not needed.**
- research-dispatch already bounds every agent (`AGENT_TIMEOUT_S=600` plus `interrupt()`) and marks stale `running` rows as `interrupted` at startup (pipeline design §1, §5).
- The dashboard token is `monitor`-only, and research-dispatch has no endpoint that changes runs (and must not get one, §3).
- The "possibly stuck" label and the restart hint cover the rare case.

### 5. Security
- **Payload content:**
  - research-dispatch serializes only the fields in §2, through `extra="forbid"` models, and error codes are enums.
  - The dashboard rebuilds the payload through `shape()`, so a future additive field on the research-dispatch side can't leak through.
  - None of these ever cross: `prompt_text`, `topic_snapshot`, `findings_json`, `source_registry`, `transcript`, `output_json`, item titles or URLs, `body`, `last_error`, webhook URLs or Discord IDs (`remote_id`), session IDs or tokens.
- **Token:**
  - The token is a dedicated named token `niu-dash` with scope `monitor`, which reads only `/monitor/summary`.
  - It is stored at `~/.local/state/niu-heimar/research-dispatch.token` with mode 0600, owner-checked and not followed through symlinks.
  - It is sent only to the validated loopback URL, with no redirects (`_NoRedirect`) and no proxies (`ProxyHandler({})`).
  - It never appears in the page, in `/api/research`, in logs or in error messages.
  - Rotation: run `rd gen-token` again, update `API_TOKENS`, restart, then run `niu-dash research-token`.
- **SSRF:**
  - The only outbound target is `{validated base}{"/api/v1/monitor/summary"}`.
  - The base comes from a local config file, never from a request.
  - The browser can't influence the URL or the path, because `/api/research` takes no parameters.
- **CSRF:** the one new POST (`/api/research/refresh`) has the same `_post_ok` guard as `/api/prs/refresh`. It only triggers a throttled re-fetch.
- **CSP:** unchanged. There is no new origin in `connect-src`, no inline style, and the floor ticks and bars are SVG attributes. The front end uses `esc()` for every value, even though `shape()` has already validated them.
- **Exposure:** like the rest of the dashboard, `/api/research` is a GET readable by anyone who can reach niu-dash (loopback and your tailnet through `tailscale serve`). That is acceptable because the payload holds only operational numbers. Item titles were left out partly for this reason (Open question 1).
- **Size caps:** research-dispatch bounds every list (12 gate rows, 6 recent runs, 7 ledger days, 3 agents), and a test keeps the response under 32 KiB. The dashboard rejects anything over 128 KiB.
- **Hook policy (`hooks/block-env.sh`):**
  - Extend the credential-file rule (`:50`) to `web-sessions\.json|research-dispatch\.token`.
  - Add a Bash rule that blocks `niu-dash research-token` ("setting the research-dispatch token is reserved for the user").
  - Agents may still `curl` `/api/research` (it is read-only), but they can't obtain the token to call research-dispatch directly.
- **Anthropic usage calls:** the cached preview (300 s) plus the dashboard's own `claude_limits()` (300 s, `niu_limits.py:18`) means at most two usage calls every 5 minutes, and never a probe.

### 6. Configuration and defaults
| Setting | Where | Default |
|---|---|---|
| Base URL | `.claude-workspace/research.json` (gitignored), else `research.template.json` | `http://127.0.0.1:8080` |
| Poll interval, server cache | same | `poll_secs: 60` |
| Upstream timeout | same | `timeout_secs: 4` |
| Show the tab | same | `enabled: true` (`false` hides it and makes no calls) |
| Token | `~/.local/state/niu-heimar/research-dispatch.token` (0600) | absent, which means `not_configured` |
| Gate preview cache (research-dispatch) | `MONITOR_GATE_PREVIEW_TTL_S` in `.env.example` | `300` |
| "Possibly stuck" threshold | `app.js` constant | 45 min |

### 7. User setup (one time, after both PRs are merged)
1. `cd ~/WORKSPACE/PERSONAL/research-dispatch && docker compose exec app rd gen-token --name niu-dash --scope monitor`
2. Add the printed `niu-dash:monitor:<sha256>` entry to `API_TOKENS` in research-dispatch's `.env` yourself. Then run `docker compose up -d`.
3. Run `~/WORKSPACE/.claude-workspace/bin/niu-dash research-token` and paste the token.
4. Open the dashboard and select the Research tab. It shows data within about 30 s.

## Changes

### PR 1: research-dispatch (`PERSONAL/research-dispatch/`)
| File | Change |
|---|---|
| `src/research_dispatch/core/security.py` | Add the scope `monitor` to `Scope` and `SCOPES`. `scope_allows`: admin ⊇ read ⊇ monitor. Update the validation message. |
| `src/research_dispatch/cli.py` | `gen-token` help and scope check accept `monitor`. |
| `src/research_dispatch/api/deps.py` | `require_monitor = require_scope("monitor")` and `Monitor = Annotated[...]`. |
| `src/research_dispatch/usage/preview.py` | **New.** `GatePreview(evaluator, ttl_s)` with `get() -> (reading, decision, checked_at)`, under a lock and with a TTL. Asserts `evaluator.probe is None`. |
| `src/research_dispatch/monitor/__init__.py`, `monitor/models.py`, `monitor/build.py`, `monitor/errors.py` | **New.** Pydantic `MonitorV1` and sub-models (`extra="forbid"`). `build_summary(session, settings, usage_settings, preview, now)` assembles the summary, with M2 parts behind `features.runs`. `error_code()` maps outcomes to the enum. |
| `src/research_dispatch/api/routers/monitor.py` | **New.** `GET /api/v1/monitor/summary` (`Monitor` dependency), with `GatePreview` on `app.state.gate_preview` (tests preset it, as with `usage_evaluator`). |
| `src/research_dispatch/api/app.py` | `include_router(monitor.router)`. |
| `src/research_dispatch/db/repositories/usage.py` | `ledger_since(session, day)` for the last 7 days. |
| `src/research_dispatch/usage/config.py` | `monitor_gate_preview_ttl_s: int = 300`. |
| M1 gate-check path (`rd run`, in `orchestrator/` or `cli.py`, wherever M1 lands it) | Write a `usage_readings` row for each gate check and `bump_ledger` after each run, if M1 does not already. |
| `schemas/monitor.v1.json` | **New.** JSON Schema exported from `MonitorV1` (`rd schemas export`), the contract the dashboard's `shape()` follows. |
| `.env.example` | `MONITOR_GATE_PREVIEW_TTL_S=300` and a comment about the `monitor` scope for the dashboard token. |
| `README.md` | A "Dashboard monitoring" section: token creation (§7 steps 1–2), what the endpoint exposes and what it never exposes. |
| `tests/api/test_monitor_api.py`, `tests/test_monitor_build.py`, `tests/test_security.py` | See Test plan. |
| M2 PR (later) | Fill in `current_run`, `last_run`, `recent_runs`, `gate.pending` and `digest` in `monitor/build.py`. Set `features.runs` and `features.digest` to true. Health `scheduler`. Tests for each. |

### PR 2: workspace repo (`.claude-workspace/`, via `/workspace-sync`)
| File | Change |
|---|---|
| `.claude-workspace/lib/niu_research.py` | **New.** `load_config`, `validate_url`, `token_path`, `read_token`, `fetch`, `shape`, `summary`, the `STATES`/`MESSAGES` tables, `_NoRedirect`, and the opener with `ProxyHandler({})`. |
| `.claude-workspace/research.template.json` | **New.** `{"enabled": true, "url": "http://127.0.0.1:8080", "poll_secs": 60, "timeout_secs": 4}` |
| `.gitignore` | Add `/.claude-workspace/research.json`. |
| `.claude-workspace/bin/niu-dash` | Import `niu_research`. Add `GET /api/research`, `POST /api/research/refresh` (`_post_ok`), and the `research-token [--remove]` subcommand. Update the usage docstring. |
| `.claude-workspace/web/index.html` | The `tab-research` button with `#rsdot`, and the `p-research` panel with `#rshead`, `#rsgate`, `#rsquota`, `#rslanes`, `#rslast` and `#rserrors`. |
| `.claude-workspace/web/app.js` | `setView` adds research, plus `loadResearch` / `renderResearch` and the helpers (§4), the tooltip binding gets `rslanes`, the countdown runs in the 1 s tick, and the label maps. |
| `.claude-workspace/web/style.css` | The research grid, `.rsgrid`, `.rs-*`, `.floor`, `.tabdot`, the table, and the narrow layout. |
| `.claude-workspace/hooks/block-env.sh` | Block `research-dispatch.token` and Bash `niu-dash research-token`. |
| `.claude-workspace/tests/research_test.py`, `tests/research.test.sh` | **New.** Poller, shaping and security tests. |
| `.claude-workspace/tests/fixtures/research/{m1.json,m2.json,hostile.json}` | **New.** monitor.v1 payloads: M1 partial, M2 full with a partial run, and hostile (extra keys, secrets, bad enums, huge strings). |
| `.claude-workspace/tests/web_test.py` | Route, tab and `not_configured` checks, and passing the research fixture to the smoke run. |
| `.claude-workspace/tests/web_smoke.js` | An optional 3rd argument (a research payload): set `RESEARCH`, call `setView("research")` and `renderResearch()`, and assert the lanes and table render. |
| `.claude-workspace/tests/block-env.test.sh` | Cases for the token file and the subcommand. |
| `README.md` | Under "Web dashboard": the Research tab, setup §7 and the troubleshooting states. |
| `docs/design/2026-11-07-dashboard-research-tab.md` | This document. |

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| The token leaks through logs, the page or errors | It is read per call into a local variable and sent only in a header to a validated loopback URL with no redirects or proxies. Messages are fixed strings. A test scans every response and stderr for the token. `block-env.sh` blocks agents from the file and from the setter command. |
| SSRF or confused-deputy use of the dashboard's token | The URL comes from local config only and must be `http://127.0.0.1:PORT` or `[::1]`. Paths are constants. `/api/research` takes no parameters. |
| Sensitive or untrusted text reaches the browser | `extra="forbid"` models and enum error codes on the research-dispatch side. The allowlist `shape()` on the dashboard side. `esc()` in the UI. A hostile fixture test covers it. |
| Monitoring eats Claude quota or hammers the usage endpoint | The preview is cached for 300 s and never uses a probe. A test asserts that respx sees one call across many requests. |
| research-dispatch is down, slow or mid-upgrade | 4 s timeout, a background refresh (requests never block), the last good data kept and marked stale, and distinct states. |
| The contract drifts between the two repos | A `schema` tag. `monitor.v1.json` is committed in research-dispatch. Dashboard fixtures are copied from it. An unknown version shows `incompatible`, not wrong data. Additive fields are ignored by `shape()`. |
| The Agents tab breaks from shared-code edits | `renderTimeline` is untouched. The research view is separate functions. The smoke run still renders the Agents view first. |
| The tailnet can see research status | Unauthenticated GETs are an existing dashboard property. The payload has no content, only operational numbers. |
| M1 never records readings or ledger rows, so the tab looks empty | The table in §2 makes those writes part of PR 1. The UI says "no gate checks recorded yet" instead of showing blank cards. |

## Test plan
**Dashboard: `tests/research_test.py`** (the `check()` style, temp dirs, `NIU_EVENTS_DB`, `NIU_RESEARCH_CONFIG` and `NIU_RESEARCH_TOKEN_FILE` set; never the real token or port 8080). A fake research-dispatch is an `http.server` on a random loopback port in a thread, with scripted responses.
1. **Config and URL:** accepts `http://127.0.0.1:18080` and `http://[::1]:18080`. Refuses `http://localhost:8080`, `http://10.0.0.5:8080`, `http://127.0.0.1.nip.io:8080`, `http://u:p@127.0.0.1:8080`, `http://127.0.0.1:8080/x`, `https://example.com` and port `0`/`70000`. The state becomes `error`, and the fake server gets no request.
2. **Token file:** missing gives `not_configured` with no request. Mode 0644 gives `error` (permissions). A symlink is refused. Bad content (`hello`) is refused. A valid fake `rd_pat_TESTTOKEN…` is sent as `Authorization: Bearer …` exactly once per refresh.
3. **States:**
   - 200 with `m1.json` gives `ok` with `data.current_run is None`.
   - 401 gives `unauthorized` and 404 gives `incompatible`.
   - 302 to a second capture server gives `incompatible`, the capture server receives nothing, and the token is never sent there.
   - 500 gives `error`. A slow response (sleep longer than the timeout) gives `unreachable`. A closed port gives `unreachable`.
   - A 200 KiB body gives `error`. `text/html` gives `incompatible`. `{"schema":"monitor.v2"}` gives `incompatible`.
4. **Proxy:** with `http_proxy` and `HTTP_PROXY` pointing at a capture server, the request still goes direct and the capture server gets nothing.
5. **Cache:**
   - Two `summary()` calls within `poll_secs` make one upstream request.
   - `force` is throttled to once every 10 s.
   - After a success then a failure, `data` is the last good payload with `stale: true`, and `ok_at` is unchanged.
6. **Shaping (pure, on fixtures):**
   - `m2.json` round-trips every documented field, with times converted to epoch.
   - `hostile.json` adds `prompt_text`, `transcript`, `items:[{title,url}]`, an `error: "sk-ant-api03-…"` string, `webhook: "https://discord.com/api/webhooks/1/abc"`, `status: "<img onerror>"`, `used_pct: 900` and a 10,000-character `slot_key`. The output contains none of those strings, and gives `status: "unknown"`, `used_pct: 100` and `slot_key: null`.
7. **No secret in output:** run `niu-dash serve` as a subprocess against the fake server, with the fake upstream returning 401 and 500. Then grep `/api/research`, `/api/state` and stderr for the token. It must not appear.

**Dashboard: `tests/web_test.py` additions**
- `GET /api/research` gives 200 with `state == "not_configured"` (the token file env points at a nonexistent path).
- `POST /api/research/refresh` without the CSRF header gives 403, cross-origin gives 403, and with `POSTH` gives 200.
- The index has `id="tab-research"` and `id="rslanes"`. The CSP header is unchanged.
- The smoke run passes `tests/fixtures/research/m2.json` as the 3rd argument. `web_smoke.js` renders the research view (`renderResearch()` with `VIEW="research"`) and asserts that `#rslast` contains "general" and that `#rslanes` has children. A second pass with `m1.json` asserts the "arrives with research-dispatch M2" text. A third pass with `{state:"unreachable"}` asserts the unreachable message.

**Dashboard: `tests/block-env.test.sh` additions**
- `Read ~/.local/state/niu-heimar/research-dispatch.token` is blocked.
- `Bash cat …/research-dispatch.token` is blocked.
- `Bash .claude-workspace/bin/niu-dash research-token` is blocked.
- `Bash curl http://127.0.0.1:8765/api/research` is allowed.

**research-dispatch: `tests/api/test_monitor_api.py`** (fakes and respx only; placeholder tokens)
- **Auth:** no token gives 401. A `monitor` token gives 200 on `/monitor/summary` and 403 on `/topics` and `/usage/gate`. `read` and `admin` tokens give 200.
- **M1 shape:**
  - `schema == "monitor.v1"`, `features.runs is False`, and `current_run is None`.
  - `schedule.next_slot_*` is computed with time-machine: at 11:59 Asia/Tokyo it is today's 12:00 key, and at 12:00:01 it is tomorrow's 00:00 key.
  - The ledger shows today's row after `bump_ledger`.
  - `gate.recent` lists the seeded `usage_readings` with a decision, newest first, at most 12.
- **Gate preview:** with a preset `GatePreview` around a counting fake reader, 5 requests within the TTL make 1 read, and moving the clock past 300 s makes 2. A preview built with a probe raises at construction.
- **Validation:** the response validates against `schemas/monitor.v1.json`, and the OpenAPI snapshot is updated.
- **Leak scan:** seed rows with secret-looking and prompt-like values in every free-text column (`error`, `prompt_text`, `last_error`, `body`, `gate_reason` detail). The serialized response contains none of `sk-ant`, `Bearer`, `discord.com/api/webhooks`, `prompt`, `transcript` or `http`, apart from enum values.
- **Size:** with maximal data (3 agents, 6 runs, 12 gate rows, 7 ledger days), `len(body) < 32768`.
- **Error mapping (`tests/test_monitor_build.py`):** a table test for `error_code()`, and an unknown value maps to `internal`.
- **Security:** `parse_api_tokens` accepts `monitor`. `scope_allows("read","monitor")` is true, and `scope_allows("monitor","read")` is false.
- **M2 PR adds:** `waiting_gate` gives `gate.pending` with `next_check_at`. A `partial` run gives per-agent counts from `run_items`. `dispatched_signals` held for quiet hours gives `digest.status == "held_quiet_hours"`. A `running` agent appears in `current_run`. `recent_runs` is limited to 48 h and 6 entries.

**Manual checks**
1. With research-dispatch stopped, the tab shows "not reachable" and the red badge, and other tabs keep working.
2. Before running `niu-dash research-token`, the tab shows the setup steps. Afterwards, data appears within 30 s.
3. Revoke the token (remove it from `API_TOKENS` and restart); the tab shows "Token rejected".
4. Run `docker compose exec app rd gate`, then a `rd run --dry-run --agents general`. The gate lane gets a dot, and the ledger counts go up.
5. Set `USAGE_MIN_REMAINING_PCT=99` (M2). The tab shows "Waiting for the gate: attempt 1 of 3, next check …".
6. Check dark mode, Reduce motion and a narrow viewport (phone over the tailnet).
7. With the browser devtools Network tab open, only `/api/research` requests are made, and the response has no token.

## Rollout
1. **PR 1, research-dispatch** (branch `feat/monitor-endpoint`): `/dual-review`, with the security reviewer on Opus because it touches token scopes. Ship it with M1 if M1 is still open, or as a small PR right after M1. Release, then `docker compose pull && docker compose up -d`.
2. **PR 2, workspace repo** (through `/workspace-sync`): `/dual-review`, with the security reviewer on Opus (hook, credential file, outbound HTTP). release-please bumps the minor version. Then `systemctl --user restart niu-dash`.
3. **Setup:** §7 (the user creates the token and pastes it).
4. **M2 PR:** extends `monitor/build.py`. The dashboard needs no change, because it already renders M2 fields when `features.runs` is true.

**Order independence:** if PR 2 ships first, the tab shows `incompatible` (404) until PR 1 is deployed. Nothing breaks.

**Rollback:**
- Set `"enabled": false` in `research.json` to hide the tab and stop the calls, or revert PR 2.
- On research-dispatch, remove the `niu-dash` entry from `API_TOKENS`, which revokes the token at once, or revert PR 1. The endpoint is additive, so duenext and other clients are unaffected.
- `niu-dash research-token --remove` deletes the host copy.

## Open questions
1. **Item titles in the tab:** should the last-run table list the kept item titles (sanitized, 80 characters, no links)? The default is **no**. Titles are web-derived text, the dashboard is readable by the whole tailnet without pairing, and Discord already has them. If you want them, gate them behind pairing (a cookie check on a separate `/api/research/items`).
2. **Needs-you integration:** should a failed, skipped or quota-aborted run also appear in the dashboard's "Needs you" list (`niu_actions`)? The default is **no**, because research-dispatch already posts ops alerts to Discord. Adding it is a small follow-up in `lib/niu_actions.py`.
3. **Live progress during a run:** M2 updates `agent_runs` only at start and finish, so the lanes show running bars without per-tool ticks. Should M2 also record per-agent turn and search counters while the run is in progress (one cheap `UPDATE` per tool call from `tool_policy.pre_tool_use`), so the tab shows "4/8 searches" live? The default is **no** for M2, and it can be revisited after a week of use.
4. **Timezone of the lanes and countdown:** the default is browser local time, with the slot key (in `SCHEDULE_TZ`) in tooltips. Switch it if you usually view the dashboard from a device in another zone.
