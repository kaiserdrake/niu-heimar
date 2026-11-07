# niu-notify: outbound Discord notifications for the workspace
Status: approved (2026-11-06, with changes: WORK `full`, no extras) · Date: 2026-11-06 · Request: notify the user on Discord when they can't reach the dashboard (sessions waiting, reviews, CI, local LLM health, Claude limits)

## Decision (first screen)
- **A small background notifier, `bin/niu-notify`, runs as its own systemd user unit (`niu-notify.service`).** `bin/install --notify` installs it, the same way `--web` installs niu-dash. It polls the same sources the dashboard uses (event store via `niu_state.live()`, `niu_actions.actions()`, the PR cache, `niu_ollama.status()`, `niu_limits.claude_limits()`) every 30 s. It posts to Discord only when a state changes. Stdlib only.
- **Outbound only, with Discord webhooks.** There is no bot token, no gateway connection and no inbound commands. One webhook per channel. The webhook URLs are secrets and live only in `.claude-workspace/.env`, which systemd loads with `EnvironmentFile=`. The variable names are documented in a new `.claude-workspace/.env.example`. Non-secret routing goes in a gitignored `.claude-workspace/discord.json`, with a committed `discord.template.json`.
- **Messages come from allowlisted templates only.** Event text is never forwarded. Each template field is validated or sanitized. The final text goes through the existing `niu_runs.redact()`, then the webhook URLs are scrubbed out literally. Every post sends `allowed_mentions: {"parse": []}` and `SUPPRESS_EMBEDS`.
- **WORK is `full` by default (user decision, 2026-11-06).** The Discord server is private to the user, so WORK messages carry the project, repo and PR (with the sanitized, redacted title), like PERSONAL/HOBBY. Per-domain modes `full` / `minimal` / `off` remain configurable in `discord.json`. Secret redaction, hostname dropping and field sanitizing apply to every domain.
- **No Discord MCP tool in this change.** Claude-initiated messages (`discord_notify`) are deferred to an open question. The notifier already covers "session finished" and "needs you".
- **Untouched files:** `lib/niu_limits.py`, `tests/limits_test.py`, `web/app.js` (they have uncommitted edits) and `bin/niu-dash`. The notifier only calls their public functions.

## Context
- **Event store:** `lib/niu_events.py:43-65` (`db_path`, `connect(readonly=True)`). Rows come from `hooks/agent-events.py:57-104`. A `Notification` event puts a session in `waiting` (`lib/niu_state.py:153-156`). A session is `flagged` when it edited files without both reviewers (`:170`).
- **"Needs you" rules already exist:** `lib/niu_actions.py:11-46` returns `waiting` (permission / reply), `review` (flagged), `pr-blocked`, `pr-failing`, `pr-changes` and `pr-ready` items, each with a stable `id` (`wait:<sid>`, `fail:<ref>` and so on). Tests are in `tests/actions_test.py`.
- **Dashboard state:** `bin/niu-dash:202-211` builds state with `live(since).refresh()` and `json_for(domain, feed_n)` (`lib/niu_state.py:385-432`), then `actions(state, prs_json())`. The PR cache (`lib/niu_prs.py:120-160`, `prs.json`) is refreshed only while a viewer is polling (`bin/niu-dash:90-110`, `PR_REFRESH_SECS = 60`). When nobody has the dashboard open, the cache goes stale.
- **LLM health:** `lib/niu_ollama.py:111-119` `status()` returns `name`, `label`, `url`, `up` and `error`. **`url` and `error` can contain private hostnames or IPs** (`error` is the URLError reason), so the notifier must use only `name`/`label`/`up`. `health()` caches for 30 s in `ollama-health.json` (`:45-72`).
- **Limits:** `lib/niu_limits.py:77-113` `claude_limits()` returns `windows: [{key, label, used, resets_at}]`.
- **Redaction:** `lib/niu_runs.py:103-139` `redact(text, entropy=True) -> (text, n)` is the most complete version (`niu_events.redact` at `lib/niu_events.py:102-117` is the older, smaller one).
- **Hostname leak in an existing helper:** `niu_events.host_label()` (`:32-36`) returns the machine hostname, and `niu_actions` uses it for workspace sessions. The notifier must not use it.
- **How niu-dash runs:** `bin/install:112-144` (`--web`) writes `~/.config/systemd/user/niu-dash.service` (`NoNewPrivileges`, `Restart=on-failure`) and enables linger. README:138-164 documents it, including the tailnet URL, which must never appear in Discord.
- **Pinned-URL HTTP pattern:** `lib/niu_limits.py:32-38` (`_NoRedirect` opener, so a bearer never follows a redirect). The notifier copies this pattern locally rather than importing a private name from a file that is being edited.
- **Secrets:** there is no `.env.example` in the workspace yet. `.gitignore:33-35` ignores `.env` / `.env.*` and re-includes `.env.example`, and `/.claude-workspace/**` is tracked. So `.claude-workspace/.env.example` is committed and `.claude-workspace/.env` is not. `hooks/block-env.sh:39-43` lets agents touch `.env.example` and blocks `.env`.
- **WORK rules:** `WORK/CLAUDE.md:3-9`: company details stay out of committed files; "never push work code, logs or data to personal GitHub, gists or public pastebins". A personal Discord server is a third-party service in the same sense. `local_ci` already refuses in WORK (`docs/design/2026-11-06-niu-local-run-tools.md`).
- **Tests:** each `tests/*_test.py` uses a `check()` counter and temp dirs (`tests/actions_test.py:9-18`, `tests/limits_test.py:13-14` sets `NIU_EVENTS_DB`) and has a `tests/*.test.sh` wrapper. `bin/workspace-lint:46` runs every wrapper. `tests/limits_test.py:108` runs `bin/install --no-shell`, so the new flag must do nothing unless it is passed.

## Requirements
- Must: push alerts with no Claude session running, and keep running after logout (linger, as niu-dash does).
- Must: Python stdlib only (urllib, json, sqlite3 through the existing libs).
- Must: post only on state transitions, with per-key cooldowns, debouncing for flappy endpoints, a global hourly cap and Discord 429 handling.
- Must: build every message from a fixed template. Fields are typed or validated; free text (PR titles only) is redacted, sanitized and length-capped.
- Must: honour per-domain modes (`full` / `minimal` / `off`). All domains default to `full`.
- Must: treat the priority-2 GPU endpoint (not always on) as `silent` or `off`, so its normal down state doesn't spam.
- Must not: include hostnames, IPs, the tailnet/dashboard URL, endpoint URLs or errors, session titles, prompts, tool summaries, file paths, WORK project names, or secrets.
- Must not: log, print or store webhook URLs or tokens (not in journald, the state file, exceptions or `check` output).
- Must not: read `.env` in code. systemd injects the variables; the program reads `os.environ` only.
- Must not: accept any inbound command from Discord.

## Options considered
1. **MCP tool only (`discord_notify`).** Pros: tiny, and Claude chooses what to say. Cons: it only runs inside a live session, so it can't report "session waiting for permission" (that session is blocked), endpoint outages or limits while you're away. It is free text from the model, which risks leaking WORK data. **Rejected** as the main mechanism.
2. **Background notifier (webhook sender) (chosen).** Pros: true push, independent of sessions, and it reuses `niu_actions`/`niu_state`/`niu_ollama`/`niu_limits` unchanged. The whole message vocabulary is fixed and testable. Cons: one more service, plus a state file.
3. **Hybrid: notifier plus an MCP `discord_notify`.** Pros: Claude could post "design ready for approval". Cons: it adds a free-text channel to a third party. Most of the value is already covered by `waiting` and "session done" detection. **Deferred** (Open question 1). If added later, it would reuse the notifier's sender, templates, rate limiter and WORK refusal.

How it runs:
- a. **A separate systemd user unit (chosen).** It keeps alerting when niu-dash is down (it can even report that), restarts on its own, and keeps the webhook env out of the web server process.
- b. A thread inside niu-dash. One process less, but it would mean editing `bin/niu-dash`, it would put webhook secrets in the internet-facing (tailnet) process, and it dies with the dashboard.
- c. cron. A cron job has no persistent loop for debouncing, has to re-read the whole state each time, and 1-minute granularity is too slow for "waiting for permission".

Webhooks vs bot:
- **Webhooks (chosen):** outbound-only, scoped to one channel each, no gateway, and revocable per channel.
- A bot token gives full account-level API access and invites inbound command handling. It is not needed.

## Design

### 1. Components
```
bin/niu-notify run      # loop: observe -> diff -> dispatch, every poll_secs (default 30)
bin/niu-notify once     # one poll (debugging; first run only baselines)
bin/niu-notify test [--route R]   # sends the fixed "test" template to each configured route
bin/niu-notify check    # validates discord.json + which env vars are set/valid; prints names and ok/missing/invalid only
```
`lib/niu_notify.py`, three pure layers plus I/O:
- `observe(cfg, now) -> Obs`: collects plain dicts.
  - `actions(state, prs)`. The state comes from `niu_state.live(6*3600)`, `.refresh()` and `.json_for(None, 30)`, with `state["host"] = "workspace"` (never `host_label()`).
  - The PR summary is `niu_prs.summarize(niu_prs.load_cache())`. If the cache is older than `pr_refresh_secs` (default 300), `niu_prs.fetch_all()` runs first. Both are skipped when the `ci` route is unset.
  - `[{name, label, priority, up}]` from `niu_ollama.status()`. `url` and `error` are dropped right there.
  - `niu_limits.claude_limits(now)["windows"]`.
  - The dashboard check is `GET http://127.0.0.1:$NIU_DASH_PORT/healthz` (default 8765, 2 s timeout), only if `~/.config/systemd/user/niu-dash.service` exists.
  - Session working→idle durations, for "session done".
  - A failing source is recorded as `source_error[kind]` and never raises.
- `diff(prev_state, obs, cfg, now) -> (new_state, [Msg])`: pure, so the tests drive it directly.
  - Each alert key has `{status, since, polls, sent_at}`.
  - The first poll after an empty state only baselines, so nothing goes out except `notifier.started`.
- `render(Msg, cfg) -> payload`: template, then field sanitizing, then the domain mode, then redact and scrub.
- `Dispatcher(sender, cfg)`: routing, quiet hours, the rate limiter, 429 retry, and disabling a route after a 401/404.
- `Sender` protocol: `post(url, payload) -> (status:int, retry_after:float|None)`.
  - `UrllibSender` uses a local no-redirect opener, a 10 s timeout, `POST <url>?wait=true`, `Content-Type: application/json` and `User-Agent: niu-heimar-notify`.
  - Tests inject `FakeSender`.

**State file:** `db_path().parent / "notify-state.json"` (0600, written to a tmp file then `os.replace`). It holds only keys, statuses and timestamps. Keys look like `wait:<sid>`, `fail:<owner/repo#n>`, `llm:<name>` and `limit:<window>:<threshold>:<resets_at>`. There are no URLs and no text.

### 2. Discord layout (one private server, one category, 6 text channels)
| Channel | Push? (channel notification setting) | Purpose |
|---|---|---|
| `#needs-you` | **All messages** | A session is waiting for permission or a reply. |
| `#activity` | Muted / mentions only | Long sessions finished; files edited without both reviewers. |
| `#ci-prs` | Mentions only | GitHub PR checks failing, changes requested, ready to merge, blocked on token scope (never WORK). |
| `#local-llm` | Mentions only | Ollama endpoint down/up; "no GPU endpoint up". |
| `#claude-limits` | All messages | 5-hour / weekly usage crossed 80% / 95%. |
| `#alerts` | All messages | Notifier started, dashboard down/up, rate-limit suppression summary, route disabled, source errors. |

There is no category per domain: the domain is the message prefix, and WORK stays content-free. Six channels keep setup and notification tuning small.

### 3. Event to channel mapping
| Source / transition | Key | Route | Template (exact text) | WORK in `minimal` mode (default is `full`) |
|---|---|---|---|---|
| `actions` `waiting`, note has "permission"; appears and still present after `wait_delay_secs` (60) | `wait:<sid>` | needs-you | `[needs you] {where}: a session needs your permission` | `[needs you] WORK: a session needs your permission` |
| `waiting`, "idle" / other | `wait:<sid>` | needs-you | `[needs you] {where}: a session is waiting for your reply` / `... is waiting for you` | same, `where = WORK` |
| Session working→idle after working ≥ `done_min_secs` (600) | `done:<sid>:<since>` | activity | `[done] {where}: session finished after {dur}` | same, `where = WORK` |
| `actions` `review` appears | `review:<sid>` | activity | `[review] {where}: files edited without both reviewers; run /dual-review` | same, `where = WORK` |
| `pr-failing` appears | `fail:<ref>` | ci-prs | `[ci] {ref}: {n} failing check(s) · {title}` + `{pr_url}` | **dropped** |
| `pr-changes` / `pr-ready` / `pr-blocked` appear | `changes:` / `ready:` / `scope:<ref>` | ci-prs | `[ci] {ref}: changes requested · {title}` / `ready to merge` / `blocked: gh token needs the workflow scope` | **dropped** |
| Endpoint up→down (down for `down_after_polls`, 2) | `llm:<name>` | local-llm | `[llm] {label} is down` | n/a |
| Endpoint down→up (only after a down was sent) | `llm:<name>` | local-llm | `[llm] {label} is back up` | n/a |
| No endpoint with `mode: agent` up for 2 polls | `llm:no-gpu` | local-llm | `[llm] no GPU endpoint is up; local agents use {label}` (or `... and no local endpoint either`) | n/a |
| Limit window crosses a threshold upward (`[80, 95]`) | `limit:<key>:<t>:<resets_at>` | claude-limits | `[limits] {window} usage at {pct}% (resets {HH:MM})` | n/a |
| niu-dash `/healthz` fails for 2 polls / recovers | `dash` | alerts | `[alerts] dashboard is not responding on this machine` / `[alerts] dashboard is back` | n/a |
| Start / suppression / route disabled / source error (once per kind per hour) | `sys:*` | alerts | `[alerts] notifier started; {n_wait} waiting, {n_review} unreviewed` / `[alerts] {n} notifications suppressed (rate limit)` / `[alerts] route {route} disabled: webhook rejected (HTTP {code})` / `[alerts] cannot read {source}` | n/a |

- Resolved "needs you" items send nothing; the key just clears.
- Per-endpoint modes come from `discord.json` → `endpoints.<name>.notify`: `on` (default), `silent` (sent with `SUPPRESS_NOTIFICATIONS`) or `off`. The template ships with the priority-2 GPU endpoint example as `silent` with `down_after_polls: 10`.

### 4. Field rules (`render`)
- **`where`:**
  - `(workspace)` becomes `workspace`.
  - PERSONAL/HOBBY becomes `DOMAIN/project`. The project must match `^[A-Za-z0-9._/-]{1,60}$`, otherwise `DOMAIN/(project)`.
  - WORK in `minimal` mode becomes `WORK`. WORK in `off` mode drops the message.
- **`ref`:** `^[A-Za-z0-9_.-]{1,39}/[A-Za-z0-9_.-]{1,100}#[0-9]{1,7}$`, otherwise the message is dropped. `pr_url` is built from the validated ref (`https://github.com/{owner}/{repo}/pull/{n}`), never taken from the cache. The repo's `label` must not start with `WORK/`.
- **`title`** (the only free text):
  1. `niu_runs.redact(title)[0]`.
  2. Keep only `[A-Za-z0-9 .,:;_()/#+'-]`.
  3. Collapse whitespace and cap at 60 characters.
  4. Wrap in a code span.
  This drops `@`, `<`, `>`, `*`, `|`, `[`, `]` and backticks, so there are no mentions, masked links, spoilers or formatting.
- **`label`:** `discord.json endpoints.<name>.display`, else the Ollama `label`, else `name`. It must match `^[A-Za-z0-9 ._-]{1,30}$`, otherwise `endpoint {priority}`. If a label contains a hostname, the user sets `display`.
- **Numbers and times:** `pct` is rounded to an int, `dur` uses `niu_state.dur()`, and `HH:MM` is local time.
- **Final content:**
  1. Run `niu_runs.redact()` on the whole content. If it finds anything, the whole message becomes the template with every free field set to `(redacted)`.
  2. Literally replace every configured webhook URL and the token part of each URL with `[webhook]`.
  3. Cap at 400 characters.
- **Payload:** `{"content": c, "username": "niu-heimar", "allowed_mentions": {"parse": []}, "flags": 4 | (4096 if silent)}`. Flag 4 is `SUPPRESS_EMBEDS` and 4096 is `SUPPRESS_NOTIFICATIONS`. There is no `avatar_url`, `tts` or embed.

### 5. Configuration
**Secrets** (new `.claude-workspace/.env.example`, names only):
```
# niu-notify Discord webhooks (one per channel). Unset = route disabled.
DISCORD_WEBHOOK_NEEDS_YOU=
DISCORD_WEBHOOK_ACTIVITY=
DISCORD_WEBHOOK_CI=
DISCORD_WEBHOOK_LLM=
DISCORD_WEBHOOK_LIMITS=
DISCORD_WEBHOOK_ALERTS=
```
- A URL is accepted only if it matches `^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/[0-9]{17,21}/[A-Za-z0-9_-]{60,100}$`. Anything else disables the route, and `check` reports `invalid` without echoing the value.
- An unset route falls back to `DISCORD_WEBHOOK_ALERTS` only for `needs-you` (so the important alerts still arrive). Every other unset route is disabled.

**Routing** (`.claude-workspace/discord.template.json`, copied to the gitignored `discord.json`; loaded from `NIU_NOTIFY_CONFIG`, then `discord.json`, then the template):
```json
{
  "poll_secs": 30,
  "routes": {"needs-you": "DISCORD_WEBHOOK_NEEDS_YOU", "activity": "DISCORD_WEBHOOK_ACTIVITY",
             "ci": "DISCORD_WEBHOOK_CI", "llm": "DISCORD_WEBHOOK_LLM",
             "limits": "DISCORD_WEBHOOK_LIMITS", "alerts": "DISCORD_WEBHOOK_ALERTS"},
  "silent_routes": ["activity", "ci", "llm"],
  "domains": {"PERSONAL": "full", "HOBBY": "full", "(workspace)": "full", "WORK": "full"},
  "endpoints": {"remote": {"notify": "on"}, "local": {"notify": "on"}},
  "wait_delay_secs": 60, "done_min_secs": 600, "down_after_polls": 2,
  "limit_thresholds": [80, 95], "pr_refresh_secs": 300,
  "cooldown_secs": 900, "max_per_hour": 30,
  "quiet_hours": null
}
```
- `routes` values must match `^DISCORD_WEBHOOK_[A-Z_]{1,40}$`. The config names variables and never holds URLs. `check` refuses a config whose values look like URLs.
- In the user's `discord.json`, `endpoints` uses the user's own endpoint names: the priority-2 GPU endpoint gets `{"notify": "silent", "down_after_polls": 10}` (or `"off"`). The file is gitignored, so names that might be hostnames are never committed.
- `quiet_hours`, for example `{"start": "23:00", "end": "07:00", "except": ["needs-you"]}`, sends with `SUPPRESS_NOTIFICATIONS` during the window. Messages are still delivered, just with no push. It is never used to drop messages.

**Service** (`bin/install --notify`):
```
[Unit]
Description=niu-heimar Discord notifier (outbound webhooks only)
After=network-online.target
[Service]
ExecStart=$SHARED/bin/niu-notify run
EnvironmentFile=-$SHARED/.env
Restart=on-failure
RestartSec=30
NoNewPrivileges=true
PrivateTmp=true
UMask=0077
[Install]
WantedBy=default.target
```
- The installer enables linger as `--web` does, and copies `discord.template.json` to `discord.json` if it is missing.
- It warns if `$SHARED/.env` is group- or world-readable. It checks the mode with `stat -c %a` and never reads the contents.
- It prints the setup steps below.
- systemd parses `.env` as `KEY=value` lines. The program never opens it.

### 6. Rate limiting, dedup, errors
- **Transitions only:**
  - An alert fires when a key goes from absent/ok to active (after debounce), or, for llm/dash, back to ok after a sent down.
  - Re-firing the same key within `cooldown_secs` is suppressed.
- **Global cap:**
  - A token bucket allows `max_per_hour` messages (default 30).
  - Overflow is counted, and one `[alerts] N notifications suppressed` goes out when the bucket refills.
  - Messages within a poll are sent in priority order (needs-you first).
- **Discord limits:**
  - Posts are sequential, with at least 1 s between posts to the same webhook.
  - On 429, wait `min(retry_after, 60)` (JSON body `retry_after` or the `Retry-After` header) and retry once, then count the message as suppressed.
  - On 5xx or a network error, keep it for the next poll (at most 3 attempts).
  - On 401/403/404, disable the route until restart and send `route disabled` via `alerts`. This flags a deleted or rotated webhook.
- **Logging:**
  - stdout/stderr goes to journald and holds only route names, HTTP status codes, exception class names and counts. It never holds `str(exc)` of urllib errors (which can carry the URL), the payload or the content.
  - A `_log()` helper runs every line through the webhook scrub as a last guard.

### 7. User setup (one time)
1. In Discord, create a server ("Create My Own", then "For me and my friends"), named for example `niu-heimar`. Don't create invites. Turn on 2FA on your account.
2. Create a category `niu-heimar` and the six text channels from §2.
3. For each channel: open Edit Channel, then Integrations, then Webhooks, then New Webhook. Name it `niu-heimar` and copy the webhook URL.
4. Put the URLs into `~/WORKSPACE/.claude-workspace/.env` yourself, using the names in `.env.example`. Then run `chmod 600` on it. Don't paste the URLs into a Claude session.
5. Run `.claude-workspace/bin/install --notify`. Edit `.claude-workspace/discord.json` and set your endpoint names under `endpoints`. The priority-2 GPU endpoint should be `silent` or `off`. Add `display` for any label you don't want shown.
6. Run `niu-notify check`; it should report `ok` for each route. Then run `systemd-run --user --wait --collect -p EnvironmentFile=$HOME/WORKSPACE/.claude-workspace/.env $HOME/WORKSPACE/.claude-workspace/bin/niu-notify test`, or simply `systemctl --user restart niu-notify` and wait for "notifier started".
7. Notification settings:
   - Server: Notification Settings, then "Only @mentions", and suppress @everyone and roles.
   - Per channel: set `#needs-you`, `#claude-limits` and `#alerts` to "All Messages", and mute `#activity`.
   - Mobile: allow Discord push notifications.
8. To rotate a webhook: delete it in Discord, create a new one, update `.env`, and run `systemctl --user restart niu-notify`.

## Changes
| File | Change |
|---|---|
| `.claude-workspace/lib/niu_notify.py` | **New.** `load_config`, `webhook_urls()` (validated, from `os.environ`), `observe`, `diff`, `render`, `TEMPLATES`, `sanitize_*`, `scrub`, `RateLimiter`, `Dispatcher`, `UrllibSender` (no-redirect opener), `load_state`/`save_state`. Imports `niu_state`, `niu_actions`, `niu_prs`, `niu_ollama`, `niu_limits` and `niu_runs.redact` read-only. |
| `.claude-workspace/bin/niu-notify` | **New.** argparse with `run`, `once`, `test [--route]` and `check`. Handles SIGTERM cleanly (saves state). |
| `.claude-workspace/discord.template.json` | **New.** Routing defaults (§5), endpoint names `remote`/`local` matching `ollama.template.json`. |
| `.claude-workspace/.env.example` | **New.** The six `DISCORD_WEBHOOK_*` names, empty, with comments. |
| `.gitignore` | Add `/.claude-workspace/discord.json`. |
| `.claude-workspace/bin/install` | Add a `--notify` flag: write `niu-notify.service`, run daemon-reload / enable / restart, handle linger, copy `discord.json` if missing, check the `.env` mode, print setup steps. Nothing changes without the flag. |
| `.claude-workspace/tests/notify_test.py` | **New.** See Test plan. |
| `.claude-workspace/tests/notify.test.sh` | **New.** `exec python3 .../notify_test.py` (picked up by `workspace-lint:46`). |
| `README.md` | New "Discord notifications" subsection after "Web dashboard" (README:164): install, setup steps, channel table, `journalctl --user -u niu-notify`. |
| Not changed | `lib/niu_limits.py`, `tests/limits_test.py`, `web/app.js`, `bin/niu-dash`, `lib/niu_actions.py`, `lib/niu_ollama.py`. |

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| WORK data reaches Discord | User accepted (private server): WORK is `full`. Secrets are still redacted and titles sanitized; `minimal`/`off` available per domain. Tests cover `minimal` and `off`. |
| Hostnames or IPs leak (endpoint `url`/`error`, `host_label()`, tailnet URL) | `observe` drops `url`/`error`; workspace shown as `workspace`; labels regex-checked with a `display` override; no template has a URL slot except the GitHub PR URL built from a validated ref. Test feeds hostnames and IPs everywhere and greps the payloads. |
| Webhook URL leaks (logs, exceptions, state, Claude) | Only systemd loads `.env`; `_log` emits only status codes and class names, scrubbed; state has no URLs; `check` prints ok/missing/invalid only; URL regex pins the Discord host and `/api/webhooks/` path; the no-redirect opener prevents forwarding. |
| Mention or markdown injection via PR titles | `allowed_mentions.parse = []`; title charset allowlist removes `@<>[]*|` and backticks; code span; `SUPPRESS_EMBEDS`. |
| Spam (flapping endpoint, many sessions) | Debounce polls, per-key cooldown, hourly bucket with a summary, `silent`/`off` per endpoint, baseline on first run. |
| PR cache stale when nobody views the dashboard | The notifier refreshes through `niu_prs.fetch_all()` every `pr_refresh_secs` (gh auth stays in the gh child, as today). The cache write is atomic (`lib/niu_prs.py:136-140`). |
| Shared `ollama-health.json` written by two processes | Writes are small; a torn read is caught as `ValueError` and treated as a cache miss (`lib/niu_ollama.py:52-55`). A later follow-up can make that write atomic. |
| Extra Anthropic usage API calls (two processes, 5-minute cache each) | Two calls per 5 minutes at most. `NIU_CLAUDE_USAGE_API=0` can be set in the unit if unwanted (Open question 4). |
| The notifier dies silently | `Restart=on-failure`; "notifier started" on each start shows restarts. A dead-man heartbeat is Open question 3. |
| An agent tries to set or read webhook values | `block-env.sh` blocks `.env`; the setup steps tell the user to edit it themselves. |

## Test plan
`tests/notify_test.py` follows the `actions_test.py` / `limits_test.py` style (`check()`, a `tempfile.mkdtemp()` dir, `NIU_EVENTS_DB` and `NIU_NOTIFY_CONFIG` pointed into it). **No network:** `FakeSender` records `(url, payload)` and returns scripted `(status, retry_after)`. `diff` and `render` are tested on hand-built `Obs` dicts.
1. **Templates and payload:** every message has `allowed_mentions == {"parse": []}`, `flags & 4`, `username == "niu-heimar"`, `len(content) <= 400` and no `embeds`/`avatar_url`/`tts`. Silent routes and quiet hours set `flags & 4096`.
2. **Sanitizing:**
   - A PR title `@everyone <@123> [x](http://evil) **b** \`c\` ghp_<36 chars>` comes out with no `@`, `<`, `](` or backticks, and the token redacted.
   - A newline in the title is collapsed.
   - A project `../x` or `a b` becomes `(project)`, and a bad ref drops the message.
3. **Privacy:**
   - Sessions in `WORK/acme-portal` produce `WORK` messages without `acme-portal`. WORK PRs produce nothing. With `domains.WORK = "off"`, nothing is sent.
   - Ollama status with `url="http://10.1.2.3:11434"`, `error="[Errno -2] Name or service not known: gpu-box.lan"` and label `GPU`: no payload contains `10.1.2.3`, `gpu-box` or `11434`.
   - A `(workspace)` session shows `workspace`, never `socket.gethostname()`.
4. **Transitions:**
   - The same `Obs` twice gives one message. The first run baselines (only `notifier.started`).
   - A wait that clears before `wait_delay_secs` sends nothing.
   - Endpoint down for 1 poll sends nothing; down for 2 polls sends `down`; up then sends `up`. A `silent` endpoint sends with flag 4096, and `off` sends nothing.
   - "No GPU endpoint up" fires once.
   - Limits 79→81 sends one; 81→90 sends none; 95 sends one; after `resets_at` passes, a new crossing sends again.
   - Working→idle after 11 minutes sends `done`; after 2 minutes it sends nothing.
5. **Rate limiting and errors:**
   - 50 distinct alerts send at most `max_per_hour` plus one suppression summary.
   - 429 with `retry_after=0.01` then 204 gives exactly 2 posts.
   - 404 disables the route and posts `route disabled` to alerts.
   - A 500 is retried on the next poll, at most 3 times.
6. **Secrets:**
   - Set `DISCORD_WEBHOOK_LLM` to a syntactically valid fake URL (token `T0K3N...`). Run `niu-notify once` and `check` as subprocesses with a sender that raises `URLError` carrying the URL. Neither the token nor the URL appears in stdout, stderr or `notify-state.json`.
   - `check` prints `ok`/`missing`/`invalid` only.
   - Invalid URLs are rejected: `http://`, `https://discord.com.evil.io/api/webhooks/...`, `https://evil.io/?u=discord.com/api/webhooks/...`, and a too-short token.
   - A config with a URL in `routes` is refused.
   - The no-redirect opener raises on a 302 from a local `http.server` (tested on the opener directly).
7. **Integration:** write `SessionStart`, `PreToolUse Edit` and `Notification` (permission) rows into a temp `events.db` with `niu_events.connect()`. Then `observe` (with stubbed `niu_ollama.status`/`claude_limits`/`niu_prs` through monkeypatch), `diff`, and dispatch to the fake sender send one `needs-you` message.
8. **install:** in a temp `HOME` with a fake `systemctl`/`loginctl` on `PATH`, `install --no-shell --notify` writes a unit containing `EnvironmentFile=-` and `niu-notify run` and no `DISCORD_WEBHOOK_` values. Plain `install --no-shell` writes no notifier unit, so `tests/limits_test.py:108` behaviour is unchanged.
9. **Existing tests stay green:** `bin/workspace-lint` (py_compile of the new bin/lib, JSON check of `discord.template.json`, and all `tests/*.sh`).
10. **Manual checks:**
    - Run setup steps 1–7 and trigger a permission prompt in a PERSONAL session; `#needs-you` should get a push within about 90 s.
    - Stop the priority-1 GPU endpoint's Ollama; `#local-llm` gets a message after about 60 s, and another after restart.
    - Stop niu-dash; `#alerts` gets a message.
    - Run `journalctl --user -u niu-notify` and confirm no URL appears.

## Rollout
- One PR on a branch through `/workspace-sync` (this touches `.claude-workspace/`, `.gitignore` and `README.md`). Run the security-reviewer and test-reviewer through `/dual-review`. release-please bumps the minor version.
- It is opt-in: nothing runs until the user runs `install --notify` and fills in `.env`.
- Rollback: `systemctl --user disable --now niu-notify` and delete `~/.config/systemd/user/niu-notify.service`, or revert the PR. Deleting the Discord webhooks revokes all access. The state file (`~/.local/state/niu-heimar/notify-state.json`) can be removed.

## Implementation notes
- The systemd unit text is generated by `niu-notify unit` (printed to stdout) and `bin/install --notify` writes it. This keeps the unit testable without running the whole installer; the install test from the plan (item 8) becomes a test of `niu-notify unit` plus a static check that `install` only touches systemd with `--notify`.
- Workflow change (2026-11-06): reviews now run once per PR, so the `review:<sid>` alert ("files edited without both reviewers") was removed from `niu_actions` and the notifier; `#activity` carries only "session done".
- Review fixes (2026-11-06): `wait:` keys use `wait_cooldown_secs` (120) instead of the 15-minute cooldown, and a transition blocked by a cooldown stays pending and is retried while still active. "done" ignores time spent in the idle-prompt wait. Limit `resets_at` values within 600 s count as the same window. Endpoint labels may not contain dots or colons (hostname/IP guard). The notifier removes `DISCORD_WEBHOOK_*` from its own environment after reading them, so `gh`/`git` children never inherit them. A 429 backs off that webhook for `retry_after`. The unit adds `LockPersonality`, `SystemCallArchitectures=native` and `RestrictAddressFamilies`.
- Update (2026-11-06): the usage API is now on by default (`NIU_CLAUDE_USAGE_API=0` disables it), so the notifier also reads the Claude Code OAuth token to fetch usage, which makes `#claude-limits` work for desktop-app-only use. The README documents it and how to turn it off.

## Open questions (resolved 2026-11-06)
User answers: WORK = full detail (project, title, repo, PR). No extras now (no heartbeat, no `discord_notify` MCP tool). The user will connect other systems (daily briefing, due-date notifications) to the same Discord server later; those get their own channels and webhooks and are out of scope here.

Original questions:
1. **Claude-initiated messages:** add an MCP `discord_notify(kind: enum["done","blocked","design-ready"], note?: maxLength 120)` to niu-local later? It would be refused in WORK, the note would be redacted and sanitized like PR titles, and it would post to `#activity` through the same dispatcher. The proposal is to wait until the automatic alerts have been used for a while.
2. **WORK default:** is `minimal` (domain only, "needs you" and reviews) acceptable under company policy, or should WORK default to `off`? Check against COMPANY.local.md.
3. **Heartbeat:** a daily `[alerts] notifier alive; today: N sessions, M reviewed` digest at a fixed hour, so a silent notifier is noticed? It is cheap and would be off by default.
4. **Usage API:** should the notifier's unit set `NIU_CLAUDE_USAGE_API=0` and rely on status-line reports only? That avoids a second process reading the OAuth credentials, at the cost of stale limits when only the desktop app is used.
5. **PR titles in non-WORK `#ci-prs`:** keep them (sanitized, 60 characters) or show only `owner/repo#n`? Titles of others' PRs on public repos are attacker-controlled text.
6. **Existing finding, outside this change:** `ollama.template.json:4` still commits a private LAN address for the GPU endpoint (already raised in the run-tools design). The notifier never sends it, but the template should move to a placeholder.
