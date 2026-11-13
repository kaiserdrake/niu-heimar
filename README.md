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
  agents/                     shared agents (designer, implementer, doc-writer, security-reviewer, test-reviewer)
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
- **doc-writer** (`agents/doc-writer.md`, Haiku): documentation-only pieces (README/docs, guides, docstrings, comments). It
  checks every claim against the code and never changes behaviour, so docs work costs Haiku rather than Sonnet tokens.
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
- Lite-mode calls append their token counts to `local-usage.jsonl` in the state folder (counts only, no prompts or answers, mode 0600,
  about 1 MiB cap) for the dashboard's *Token usage*; `NIU_USAGE_TOKENS=0` turns it off.

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

**LLMs usage** (Events tab, between *Pull requests* and the event feed): one panel with *Weekly usage* and *Token usage*
side by side (stacked under 1200 px), their headers on one row, collapsed and expanded together with one button.

**Weekly usage** (in *LLMs usage*; Claude only, since it is the plan limit): a graph of the weekly Claude usage %
over the last 2, 5 or 10 weeks. It shows each weekly reset as a dashed marker, an **"ended N%"** label per finished week
(the highest value seen before it reset; **"ended ≥N%"** when sampling stopped more than 6 hours before the reset), the
Opus and Sonnet weekly lines only when Anthropic reports them, an optional 5-hour line (the *5-hour* checkbox, off by
default), a dotted **pace** line that projects the current week to its reset, and a plain-text summary underneath. Until the
history covers the chosen range, the graph starts at the first sample instead of showing empty weeks.
- *Zoom:* drag across the graph (mouse, pen or a sideways touch drag) to zoom to that stretch of time; spans of 3 days or
  less show hours. Double-click the graph or press **Reset zoom** to go back. The zoom stays through the automatic
  refresh and is cleared when you pick 2w, 5w or 10w or reload the page; it only redraws data already loaded.
- *Where it comes from:* while `niu-dash serve` runs (also with no browser open), a background thread reads the Claude limits
  every 5 minutes (the same two sources as above) and stores a sample in `~/.local/state/niu-heimar/usage.db` (mode 600,
  next to `events.db`). A sample is stored only when the value changes, plus a heartbeat every 10 minutes; each row carries the
  reading's own time, so a stale reading is never stored twice. Only the limit name, a time, a percentage and the reset time
  are kept: never the OAuth token or any session data. Samples are kept for 70 days and then deleted.
- *History starts when the dashboard first runs:* earlier weeks can't be recovered. While the dashboard is down, or when no
  terminal Claude session runs and the usage API is off or unreachable, no samples arrive; the graph draws those stretches
  (more than 25 minutes without a sample) as hatched **gaps** instead of joining the points.
- *Why readings stopped:* the "No new readings" note names the reason when it's known. **The Claude login token has
  expired** means only Claude Code renews it, since the dashboard never sends an expired token or touches the refresh
  token. So the dashboard asks Claude Code to renew it: it runs `claude auth status` (no model call, no usage) in the
  background, at most once an hour, and fetches again as soon as the token is valid. If that doesn't renew it, the note says
  so: run `claude -p hi` in a terminal, and readings return within 5 minutes. `NIU_CLAUDE_AUTO_RENEW=0` turns the automatic
  renewal off. Other reasons: the token was rejected, no login was found, the API is off (`NIU_CLAUDE_USAGE_API=0`), or it's
  unreachable.
- *Background calls:* the usage API is now also called while no browser is open, still at most once every 5 minutes (288 a day).
  `NIU_CLAUDE_USAGE_API=0` turns those calls off (the status line still feeds the graph during terminal sessions). To stop
  recording completely, set `NIU_USAGE_COLLECT=0` (`systemctl --user edit niu-dash`, `Environment=NIU_USAGE_COLLECT=0`) and
  restart; the panel then says so. To start over, delete `usage.db*` in the state folder. The browser fetches
  `GET /api/usage/history?days=35` (7 to 70, cached for 60 seconds, read-only) only while the Events tab is visible.

**Token usage** (in *LLMs usage*, next to *Weekly usage*; the line under the boxes ends with the update time and how far back the data goes): how many tokens Claude Code and the local models (Ollama, through `niu-agent`) used, by agent type and by model. The *All / Claude / Local* switch in its header filters the whole section (default *All*; your choice is remembered in the browser). With *All*, the boxes add a "local N" line when there is local usage and the line under them splits Claude from local; local rows show up as `local:search`, `local:diff`, `local:ask`, `local:logs`, `local:ci`, `local:git` (agent-mode and lite runs of the same task share one row; failed runs count) with their own colour in *Per day*. Local tokens cost no plan quota and are not part of the Weekly %. *Today / 7 days / 30 days* boxes show the totals
(with the output share). *By agent type* and *By model* rank horizontal bars split into **input, output, cache write and
cache read**, each with its share. The *metric* picker (Total, Output, Input + cache write, Cache read) re-ranks the rows and
recomputes the shares; *Per day* stacks the totals by model family (opus, sonnet, haiku, local, other; hourly for *Today*), and the
*Agent × model table* is the same data as plain numbers. The research bot's own tokens (from the Research tab's ledger) appear
as a muted row, outside the totals and shares (hidden under *Local*). The header's domain filter applies; *All domains* includes sessions outside
the workspace as `(outside)`.
- *Cache reads dominate the totals:* every call re-reads the conversation from the prompt cache, so a long session adds many
  millions of cache-read tokens. Use the *Output* metric to see what the models wrote. Tokens are not the plan's limit
  units: the weekly % above stays the authoritative measure.
- *What is read:* the Claude Code transcripts, `<CLAUDE_CONFIG_DIR or ~/.claude>/projects/<folder>/<session>.jsonl` and the
  subagent files `<folder>/<session>/subagents/agent-<id>.jsonl` (type from the sibling `agent-<id>.meta.json`). Only those two
  shapes, regular files you own, no symlinks. From each assistant line only the message id, model name, the four token
  counters and the time are kept; the working folder is used once to find the domain and project (nothing outside the
  workspace is kept). Prompts, replies, file paths and the task-result `totalTokens` summaries are never stored, shown or
  logged. A response written as several lines is counted once. Sessions the dashboard did not observe (other devices,
  claude.ai chats, transcripts Claude Code already deleted, 30 days by default) are missing, and so are `niu-agent probe`, Ollama calls made outside `niu-agent`, and the research bot (shown separately).
- *Local models:* agent-mode runs are read from the `niu-agent` transcripts in `<NIU_AGENT_CONFIG_DIR or state folder/ollama-claude>/projects`
  with the same rules as above. Lite-mode calls are read from `local-usage.jsonl` in the state folder (counts, endpoint name, model,
  task and domain only; see *Local agents*).
- *When:* the same background thread as above scans every 60 seconds, incrementally (each file's read position is remembered),
  within 32 MiB / 1.5 seconds per pass, so the first backfill takes a few minutes and the panel shows
  "Reading transcripts for the first time: N of M files". Rows are kept for 90 days in `usage.db`. `NIU_USAGE_TOKENS=0`
  turns scanning off (`NIU_USAGE_COLLECT=0` turns off both recorders); the panel then says so. The browser fetches
  `GET /api/usage/tokens?range=today|7d|30d&domain=` (cached for 60 seconds, read-only; nothing over HTTP can start a scan)
  only while the Events tab is visible.
- *`niu-dash usage-scan [--full]`* reads everything in the foreground and prints counts only (files, messages, corrupt
  lines); `--full` forgets all read positions and reads every transcript again (the totals do not change). To start over,
  delete `usage.db*` in the state folder.

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
archive/restore the research topics held by valkyrie-dispatch.

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
