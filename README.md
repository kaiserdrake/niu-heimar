# niu-heimar

Claude Code configuration for `~/WORKSPACE`. It provides common rules for every project, plus
domain-specific setup for **WORK** (corporate GitLab), **PERSONAL** (GitHub, docker-compose,
GHCR images) and **HOBBY** (GitHub, game projects).

Only config is tracked here. Projects inside `WORK/`, `PERSONAL/` and `HOBBY/` are their own repos.

## What you get
| Rule | How it's enforced |
|---|---|
| Agents can't read `.env` | `permissions.deny` in `~/.claude/settings.json`, plus the `hooks/block-env.sh` PreToolUse hook (also catches `cat .env`, `printenv`, `docker compose config` …). Applies only under this folder. |
| Two mandatory reviewers | `security-reviewer` and `test-reviewer` agents, the `/dual-review` skill that runs both in parallel, and a rule in `CLAUDE.md`. |
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
  agents/                     shared agents (security-reviewer, test-reviewer)
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
  designer (Opus) → docs/design/<date>-<slug>.md → you approve → implement →
  local_diff_review (Ollama) → security-reviewer + test-reviewer (Claude) → fix loop (≤3) → report
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
- The "today" line counts sessions and subagent runs. It also flags any session that edited files without running both mandatory reviewers.
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

Service: `systemctl --user status|restart|stop niu-dash`. Logs: `journalctl --user -u niu-dash`.
