# niu-heimar

Claude Code configuration for `~/WORKSPACE`. It provides common rules for every project, plus
domain-specific setup for **WORK** (corporate GitLab), **PERSONAL** (GitHub, docker-compose,
GHCR images) and **HOBBY** (GitHub, game projects).

Only config is tracked here. Projects inside `WORK/`, `PERSONAL/` and `HOBBY/` are their own repos.

## What you get
| Rule | How it's enforced |
|---|---|
| Agents can't read `.env` | `permissions.deny` in `~/.claude/settings.json`, plus the `hooks/block-env.sh` PreToolUse hook (also catches `cat .env`, `printenv`, `docker compose config` …). Applies only under this folder. |
| Two mandatory reviewers | `security-reviewer` and `test-reviewer` agents, the `/dual-review` skill that runs both in parallel, and a rule in `CLAUDE.md`. They run once, when a complete feature is released (or when you ask), on the whole feature; not per PR and not after every edit. |
| Top model for planning | `"model": "opusplan"` in every domain (Opus in plan mode, Sonnet for execution). |
| Memory per domain | `autoMemoryDirectory` → `<DOMAIN>/.claude/memory/`, plus curated `<DOMAIN>/.claude/LEARNINGS.md`. |
| Local MCPs and agents | `<DOMAIN>/.claude/mcp.json` + `enabledMcpjsonServers` allow-list. See `.claude-workspace/mcp/README.md`. |

## Install (new machine)
```bash
git clone git@github.com:kaiserdrake/niu-heimar.git ~/WORKSPACE
~/WORKSPACE/.claude-workspace/bin/install
```
`install` renders the `*.template.json` files with the clone path, merges the rules into
`~/.claude/settings.json` (a backup goes to `~/.claude/backups/`), puts `bin/` on your PATH (`~/.zshenv`),
and re-links existing projects. You can run it again at any time.

## Per project
```bash
cd ~/WORKSPACE/PERSONAL/my-app && claude-init   # once per project
cdc                                              # = claude-init -q && claude
```
`claude-init` symlinks the domain settings (`.claude/settings.local.json`), the reviewer and domain agents,
the skills, and the domain `.mcp.json` into the project. It also adds the links to `.git/info/exclude`,
so they never reach the project's own repo. Use `claude-init --check` to verify.

Why this step is needed: Claude Code inherits `CLAUDE.md` from parent folders, but **settings, agents and MCP
config are only read from the folder the session starts in**.

## Layout
```
CLAUDE.md                     common rules (inherited by every project)
.claude-workspace/
  agents/                     shared agents (designer, implementer, security-reviewer, test-reviewer)
  skills/                     dual-review, workspace-sync
  hooks/block-env.sh          .env guard
  bin/                        install, claude-init, workspace-status/update/lint
  tests/                      hook tests (run in CI)
  user-settings.template.json fragment merged into ~/.claude/settings.json
<DOMAIN>/CLAUDE.md            domain rules
<DOMAIN>/.claude/
  settings.domain.template.json → rendered to settings.domain.json (gitignored)
  agents/                     domain-only agents
  mcp.json                    domain MCP servers (WORK's is gitignored)
  LEARNINGS.md                curated lessons (WORK's is gitignored)
  memory/                     auto-memory (gitignored)
```

## Common changes
- **Change the models**: edit `<DOMAIN>/.claude/settings.domain.template.json`, either `"model"` or
  `env.ANTHROPIC_DEFAULT_OPUS_MODEL` / `env.ANTHROPIC_DEFAULT_SONNET_MODEL`, then run `install`.
- **Add a domain agent**: put `<DOMAIN>/.claude/agents/<name>.md` (with frontmatter `name`, `description`,
  `model`) in place, then run `install`.
- **Protect a project's own secrets or personal data**: list them in `<project>/.claude/protected-paths`, one path or
  glob per line, relative to the project root. A directory protects everything inside it. The guard hook then blocks
  agents from reading or editing them, and from naming them in Bash commands, just like `.env`. Example:
  ```
  .vault.pass
  config/*.secret.json
  data/personal/
  ```
- **Add a domain**: create `<NAME>/CLAUDE.md` and `<NAME>/.claude/settings.domain.template.json`, then run `install`.

## Maintenance
- Every change goes through a PR into `main`. `/workspace-sync` (in any Claude session) lints, branches,
  commits and opens the PR.
- On session start, a hook tells you if this clone is behind `origin` or has uncommitted changes.
  `workspace-update` pulls and re-installs (`--ref vX.Y.Z` pins a release).
- CI (`ci.yml`) runs `workspace-lint`: shellcheck, JSON validity, frontmatter, and the hook tests.
- `maintain.yml` runs weekly. Claude compares this config with the latest Claude Code changelog and opens
  a PR when something should change. It needs the repo secret `ANTHROPIC_API_KEY` (set it with `gh secret set ANTHROPIC_API_KEY -R kaiserdrake/niu-heimar`, which prompts for the value). Until then it skips.
- Dependabot keeps Actions up to date. release-please tags releases and writes `CHANGELOG.md`.

## Design-first development and local Ollama agents
```
/develop <task>
  designer (Opus) → docs/design/<date>-<slug>.md → you approve → implementer agents (Sonnet, parallel) → tests
  (→ optional local_diff_review, Ollama) → report "done, not yet reviewed"

/workspace-sync  (or the domain's PR/MR flow)          # once per batch of features
  lint → branch, commit, PR (reviews are deferred to the feature release; `/dual-review` runs them on the whole feature)
```
- **designer** (`agents/designer.md`, Opus): researches, compares options, and writes the design document. It never edits code.
- **Local agents** (`bin/niu-agent`, exposed to sessions by the `niu-local` MCP server): `local_search`, `local_diff_review`,
  `local_ask` and `local_status`. They run as real, read-only Claude Code agents on Ollama.
- **Digest tools** `local_logs` (compose service logs), `local_ci` (failed GitHub runs, PR checks, PR diff; not in WORK) and
  `local_git` (log/diff/blame): the server (`lib/niu_runs.py`) runs a fixed read-only command with validated arguments and no
  shell, strips terminal codes, drops guard-protected files, and redacts secrets. The model then gets only that text in one
  `/api/chat` call (**digest mode**, no tools), so instructions hidden in logs or PRs have nothing to act on. Raw output is never returned.
  - **Agent mode** (GPU box): headless `claude -p` against Ollama's Anthropic-compatible API. It uses an **isolated config dir**
    (`~/.local/state/niu-heimar/ollama-claude`) that never holds Anthropic credentials, with read-only tools (Read, Grep, Glob, read-only git) and the
    same guard hooks. These sessions appear on the dashboard tagged `local · <model>`.
  - **Lite mode** (local CPU, the fallback): gathers context itself (`git grep` / `git diff`, honouring the guard and
    `protected-paths`) and makes one `/api/chat` call. A full agent loop is too slow for a 3B model on CPU.
- **Endpoints** are in `.claude-workspace/ollama.template.json`. Copy it to `ollama.json` (gitignored) to change models
  or modes, or override the URLs with `OLLAMA_REMOTE_BASE_URL` / `OLLAMA_LOCAL_BASE_URL`. The GPU box comes first, with
  automatic fallback to the local CPU.
- From a shell: `niu-agent status`, `niu-agent probe`, `niu-agent search "…"`, `niu-agent diff --base main`, `niu-agent logs --service=web`,
  `niu-agent ci --kind=failed_run` and `niu-agent git --kind=log --range=HEAD~10..HEAD`.

**One-time setup**
1. Publish the local container on loopback: add `ports: ["127.0.0.1:11434:11434"]` to the `ollama-local` service,
   then `docker compose up -d ollama-local`.
2. GPU box: set `OLLAMA_CONTEXT_LENGTH=65536` and restart Ollama. Claude Code's instructions are about 20k tokens, so a
   smaller limit silently cuts them off. `niu-agent probe` reports the effective context length.
3. Install the standalone CLI (`curl -fsSL https://claude.ai/install.sh | bash`). Without it, niu-agent falls back to
   the desktop app's bundled copy, whose path changes with app updates.

## Agent dashboard
```bash
agents-dash          # live TUI (or `cdash`: opens in a tmux split when inside tmux)
agents-dash --once   # plain-text snapshot; --json for scripts; --domain WORK; --since 7d; --all
```
- Shows a live tree (domain → project → session → subagents), with each agent's state, how long it has been running, and its current tool.
  Each agent has a tag showing where it comes from (`project`, `domain`, `shared`, `user`, `builtin`). A project's agents that haven't run yet
  show as `not yet run`.
- The "today" line counts sessions and subagent runs. It also marks release sessions whose edits haven't been reviewed yet ("release without both reviewers"; informational: the mandatory reviewers run when a complete feature is released). A session counts as performing a release when one of its tool calls is `gh release` (create/upload/edit; not list/view/download), `git tag <name>` (creating a tag; `git tag -l`, `--list`, `-d` and a bare `git tag` do not count), `release-please`, or the `dual-review` skill (a heuristic over the first line of each Bash command and the skill name; a release done by other means is not detected). Release sessions carry a small "release" tag, with a check mark once both reviewers ran. Edit-only sessions are never flagged.
- Nested projects (e.g. `HOBBY/games/voxel-mod`) work: the project is the nearest folder with `.claude/` or `.git`.
  Its own agents in `.claude/agents/` sit next to the linked shared reviewers.
- **Pull requests**: a panel lists open PRs for niu-heimar and for every linked project whose origin is on GitHub
  (via `gh`, refreshed every 60s, or press `r`). Press `p` to focus the panel, `j`/`k` to select, `o` to open in the browser,
  and `m` to merge. A merge is a squash merge that deletes the branch, and runs only when every check passed and GitHub
  reports the PR as mergeable. Status is re-checked live before merging, and you confirm with `y`. From a shell:
  `agents-dash --prs` and `agents-dash --merge kaiserdrake/niu-heimar#4`. Merging needs an interactive terminal,
  so agents can't merge through the dashboard. WORK GitLab MRs aren't shown yet (planned through `glab`).
- **How it works**: async hooks (`hooks/agent-events.py`) record session, subagent and tool events into SQLite at
  `~/.local/state/niu-heimar/events.db` (mode 600, local only). Events are only recorded under this workspace.
  The hooks never block Claude.
- **Privacy**: no prompt text and no tool output is stored. Tool targets are trimmed and secrets are redacted
  (tokens, `KEY=…`, auth headers, URL credentials). History is kept for 7 days (`NIU_EVENTS_RETENTION_DAYS`).

## Web dashboard
Same data as `agents-dash`, in a browser: live session cards, an **agent interaction graph** (session → the subagents it
spawned, labelled with each task, plus dashed lines for messages), a **timeline** with one lane per agent and tool-call ticks,
the PR panel with **Open/Merge** buttons, and today's stats.

```bash
.claude-workspace/bin/install --web        # systemd user service on 127.0.0.1:8765, plus linger
```
Open `http://127.0.0.1:8765` on this machine.

**From other devices (Tailscale):** the server only listens on localhost. Publish it to your tailnet once:
```bash
sudo tailscale set --operator=$USER
tailscale serve --bg --https=8443 http://127.0.0.1:8765
tailscale serve status        # https://<host>.<tailnet>.ts.net:8443
```
This requires MagicDNS and HTTPS certificates to be enabled in the Tailscale admin console. To stop sharing it: `tailscale serve --https=8443 off`.

**Pairing and merging:** anyone on your tailnet can view the dashboard. Merge buttons work only in a *paired* browser:
run `niu-dash pair` here (an interactive terminal is required), then enter the 6-digit code on that device. List or remove paired
browsers with `niu-dash devices` and `niu-dash revoke ID|all`. A merge uses the same rule as the TUI (green only, re-checked live,
squash merge that deletes the branch). You confirm by typing the 4-digit code the dialog shows. The code is single-use,
expires after 2 minutes, and allows 3 tries. Merges are logged in the event feed. The dotenv guard
hook also blocks agents from reading the dashboard's device credentials or calling its merge and pair endpoints. That's a policy guard,
not a hard security boundary against processes running as your own user.

**Claude plan limits** (5-hour / weekly) come from two sources, newest wins:
- the `niu-statusline` status line, which terminal Claude Code sessions run (the desktop app doesn't);
- Anthropic's usage API, **on by default**. The dashboard and `niu-notify` read the OAuth token Claude Code already
  keeps in `~/.claude/.credentials.json`, call `api.anthropic.com` at most every 5 minutes, and keep only the
  percentages; the token is never stored, logged or refreshed. It is used when the status line's last report is older
  than 2 minutes. The endpoint is undocumented, so if it fails the status line's numbers are shown. To turn it off,
  set `NIU_CLAUDE_USAGE_API=0` in the service's environment (`systemctl --user edit niu-dash`, and the same for
  `niu-notify`, adding `Environment=NIU_CLAUDE_USAGE_API=0` under `[Service]`).

**Research tab** (monitors the valkyrie-dispatch agents in `PERSONAL/valkyrie-dispatch`): the next scheduled run with a
countdown, the usage gate (the same bars as the Claude limits, with the gate's floors marked), today's quota ledger,
36-hour run lanes per agent, the last run per agent and recent errors. The research agents are Claude Agent SDK sessions in
a container with no hooks, so the Agents tab can't see them; this tab polls valkyrie-dispatch's read-only
`GET /api/v1/monitor/summary` (schema `monitor.v1`) instead. It carries only numbers, enums and times: no prompts, item
titles, URLs or free-text errors. The dashboard makes at most one call a minute, and only while someone has it open.
The tab can't start, stop or change a run. A run still `running` after 45 minutes is labelled "possibly stuck" with a
restart hint. Times are shown in your browser's time zone.

*One-time setup* (after valkyrie-dispatch has the monitor endpoint):
1. `cd PERSONAL/valkyrie-dispatch && docker compose exec app rd gen-token --name niu-dash --scope monitor`
2. Add the printed `niu-dash:monitor:<sha256>` entry to `API_TOKENS` in valkyrie-dispatch's own env file yourself, then
   `docker compose up -d`.
   Use only a `monitor`-scope token here, never a `read` or `admin` one: the dashboard sends it to the configured URL.
3. Run `.claude-workspace/bin/niu-dash research-token` in a terminal and paste the token. It is read without echo and saved
   to `~/.local/state/niu-heimar/research-dispatch.token` (mode 600, next to the device credentials; not a `.env` file).
   `niu-dash research-token --remove` deletes it. The token is read again on every fetch, so rotating it needs no restart.

*Configuration*: copy `.claude-workspace/research.template.json` to `research.json` (gitignored) to change it:
`{"enabled": true, "url": "http://127.0.0.1:8080", "poll_secs": 60, "timeout_secs": 4}`. The URL must be exactly
`http://127.0.0.1:PORT` or `http://[::1]:PORT`; anything else is refused. `"enabled": false` hides the tab and stops all calls.
Redirects and proxy variables are ignored, so the token only ever goes to that address. The dotenv guard hook also blocks
agents from reading the token file, editing `research.json` (it names where the token is sent) or running `niu-dash research-token`.

*What the tab says when something is wrong*: **not configured** shows the three setup steps; **not reachable** means the
container is down (`docker compose ps` in valkyrie-dispatch); **token rejected** means the token is missing from
`API_TOKENS` or was revoked (redo step 1 to 3); **incompatible version** means valkyrie-dispatch has no `monitor.v1`
endpoint yet (update it); an **error** says which check failed (for example the token file's permissions). In every case
the last good data stays on screen, greyed out, with the time it is from. The monitor token's scope is checked with
`GET /api/v1/auth/whoami` (cached 5 minutes): a token that is not `monitor` scope shows **token scope too broad** and is
never used. An older valkyrie-dispatch without `whoami` is tolerated.

### Research tab, Topics
A **Status | Topics** switch in the Research panel header opens the Topics view: list, add, edit, enable/disable and
archive/restore the research topics held by valkyrie-dispatch. Design:
`.claude-workspace/docs/design/2026-11-07-dashboard-topics-panel.md`.

- **Fuzzy search.** The search box matches across name, keywords and excluded terms and tolerates typos ("dutrte" finds
  Duterte), accents, initials ("ff reso" finds Final Fantasy Resonance) and dropped letters ("ollama mdls"). Every word
  you type must match, best matches come first, the name's matching letters are highlighted, Escape clears it. It runs
  in the browser on the loaded list, so it makes no requests. Excluded terms are searched too (a topic can be found by
  a term it excludes), and a "Matches keyword …" / "Matches excluded …" line under the row shows what matched.
- **Pairing is required, even to look.** Topic text is personal and untrusted, so `GET /api/topics` and every write route
  need the paired-browser cookie (a local `curl` can fake the CSRF header, not the cookie). An unpaired browser sees "Pair
  this browser" and no topic data. Pair with `niu-dash pair`.
- **A topics-only token.** valkyrie-dispatch has a `topics` scope that can read and write topics and nothing else (no
  feedback, usage, monitor, `DELETE`, runs). The dashboard checks the scope with `whoami` before every use (cached 5
  minutes) and **refuses any token whose scope is not exactly `topics`**. No endpoint here can start a research run.
- **Edits are confirmed, not optimistic.** The row changes only after the server answers. Edits send `If-Match` (the topic's
  `updated_at`), so a stale form cannot overwrite a change made from the CLI, duenext or a feedback mute: you get "changed
  elsewhere, reload?". "Remove" is the API's soft delete, shown as **Archive / Restore**; nothing is hard-deleted.
- **Limits:** name 1 to 120 characters, 1 to 20 keywords, 0 to 50 excluded terms (1 to 80 characters each, one per line),
  weight 0.1 to 5, none of `<>`{}` or control characters. The dashboard checks these first; valkyrie-dispatch stays
  authoritative. Topics made with `rd topics add` can exceed them and are flagged "CLI-only limits" until shortened.
- **Rate limits and audit:** 10 changes a minute and 100 a day per paired browser here, 60 an hour per token in
  valkyrie-dispatch. Every change adds a `TopicChange` row to the Events tab with the topic's ID prefix, the changed field
  names and the device name: never topic text.

*One-time setup* (needs the valkyrie-dispatch `topics` scope):
1. `cd PERSONAL/valkyrie-dispatch && docker compose exec app rd gen-token --name niu-dash-topics --scope topics`
2. Add the printed `niu-dash-topics:topics:<sha256>` entry to `API_TOKENS` in valkyrie-dispatch's own env file yourself,
   then `docker compose up -d`.
3. Run `.claude-workspace/bin/niu-dash research-token --scope topics` in a terminal and paste the token. It is saved to
   `~/.local/state/niu-heimar/research-dispatch.topics.token` (mode 600; a separate file from the monitor token). The setter
   asks valkyrie-dispatch for the token's scope afterwards and **deletes the file again** if it is not `topics`.
   `niu-dash research-token --scope topics --remove` deletes it.
4. Pair the browser if needed, then open Research, then Topics.

*States:* **not paired** (pair this browser), **not configured** (setup steps), **scope too broad / wrong scope** (red,
nothing is sent), **not reachable** (the last list stays, greyed out; writes are off), **token rejected**, **incompatible**
(valkyrie-dispatch has no `whoami` yet; deploy it first) and **error** (a fixed message per cause). The dotenv guard hook
also blocks agents from reading the topics token file, running `niu-dash research-token`, or calling `/api/topics`.
To revoke at once, remove the `niu-dash-topics` entry from `API_TOKENS` and restart valkyrie-dispatch.

Service: `systemctl --user status|restart|stop niu-dash`. Logs: `journalctl --user -u niu-dash`.

## Discord notifications
`niu-notify` is a small background service that posts to your own Discord server when something changes, so you hear about
it even when you can't reach the dashboard. It is **outbound only**: Discord webhooks, no bot, and no commands are accepted
from Discord. Every message is a fixed template. Endpoint URLs, error text and this machine's hostname are never sent,
secrets are redacted, mentions and link previews are off, and it alerts only on changes, with cooldowns and an hourly cap.
Design: `.claude-workspace/docs/design/2026-11-06-discord-notifications.md`.

| Channel | What lands there | Suggested notification setting |
|---|---|---|
| `#needs-you` | A session is waiting for your permission or reply (after 60 s) | All messages |
| `#activity` | A long session finished | Muted |
| `#ci-prs` | PR checks failing, changes requested, ready to merge, blocked on token scope | Mentions only |
| `#local-llm` | Ollama endpoint down / back up; no GPU endpoint up | Mentions only |
| `#claude-limits` | 5-hour / weekly usage crossed 80 % / 95 % (works with the desktop app via the usage API, see above) | All messages |
| `#alerts` | Notifier started, dashboard down / back, rate-limit summary, webhook rejected, source errors | All messages |

One-time setup (you do the secret parts yourself; never paste webhook URLs into a Claude session):
1. In Discord: **Create a server** → *Create My Own* → *For me and my friends* (e.g. `niu-heimar`). Don't create invites. Turn on 2FA for your account.
2. Create a category `niu-heimar` with the six text channels above.
3. For each channel: *Edit Channel* → *Integrations* → *Webhooks* → *New Webhook*, name it `niu-heimar`, *Copy Webhook URL*.
4. Copy `.claude-workspace/.env.example` to the secrets file beside it (same name without `.example`), paste each URL after its
   `DISCORD_WEBHOOK_*` name, and `chmod 600` it. A route whose variable is empty is off; `needs-you` falls back to the alerts webhook.
5. Install the service: `.claude-workspace/bin/install --notify` (writes `niu-notify.service`, creates `discord.json` from the
   template, enables linger). In `.claude-workspace/discord.json`, list your `ollama.json` endpoint names under `endpoints`:
   set an endpoint that isn't always on to `"notify": "silent"` (with e.g. `"down_after_polls": 10`) or `"off"`; `display`
   changes the name shown.
6. `niu-notify check` shows each route as `ok` / `missing` / `invalid`. `systemctl --user restart niu-notify` posts
   "notifier started" in `#alerts`. (`niu-notify test` sends a test message, but needs the variables in its environment, which only the service has.)
7. Discord notification settings: server → *Only @mentions*, suppress @everyone and roles; then per channel as in the table. Allow push on your phone.

Tuning in `discord.json`: `wait_delay_secs` (60) before a wait is pushed, `wait_cooldown_secs` (120) between pushes for the same session, `cooldown_secs` (900) for other alerts, `max_per_hour` (30), `quiet_hours`. Per-domain detail is set in `discord.json` → `domains` (`full`, `minimal` = domain name only and no PR messages, or `off`).
Rotate a webhook by deleting it in Discord, creating a new one, updating the secrets file and restarting the service.
Service: `systemctl --user status|restart|stop niu-notify`. Logs: `journalctl --user -u niu-notify` (route names and HTTP codes only).
