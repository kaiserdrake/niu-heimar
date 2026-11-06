# WORKSPACE — common rules (niu-heimar)

These rules apply to every project under this folder. Domain rules live in
`WORK/CLAUDE.md`, `PERSONAL/CLAUDE.md`, and `HOBBY/CLAUDE.md`, and may add to
these rules but never relax them.

## 1. Secrets: `.env` is off-limits
- Never read, print, grep, source, copy, edit or create `.env` / `.env.*` files,
  and never dump the environment (`printenv`, `env`, `docker compose config`).
  A hook enforces this; do not try to work around it.
- Work from `.env.example` / `.env.sample` / `.env.template` to learn which variables
  exist. When a real value is needed, ask the user to set it.
- The same applies to anything listed in a project's `.claude/protected-paths` (passphrase files, personal
  data, real ledgers and so on): don't read, list, copy or edit those paths, and don't try to work around the block.
- Never commit secrets, tokens, or private hostnames. If you find one, stop and say so.

## 2. Two reviewers are mandatory
A task that changes code is **not done** until both reviewers have run on the change:
1. `security-reviewer`: security-focused review.
2. `test-reviewer`: static analysis plus running the tests (dynamic).

Run them **in parallel** with the `dual-review` skill (`/dual-review`).
Fix every blocking finding, or get the user's explicit waiver and record it in your
final message. Domains may add more reviewers but never remove these two.

## 3. Models
Sessions use `opusplan`: the top Opus model in plan mode and Sonnet for execution.
For non-trivial work, plan first (plan mode).

## 4. Memory and learning are per domain
- Auto-memory for every project in a domain is shared under `<DOMAIN>/.claude/memory/`.
- Durable, domain-wide lessons (conventions, pitfalls, deploy quirks) go in
  `<DOMAIN>/.claude/LEARNINGS.md` as short dated bullets. Never put secrets or
  customer data there. Lessons that apply to only one project go in that project's own CLAUDE.md.

## 5. Project bootstrap
Each project needs `claude-init` once (it links the domain settings, reviewer agents,
skills and MCP config). If the SessionStart notice says a project isn't linked,
tell the user to run `claude-init` in it and restart the session.

## 6. Maintaining this setup
The workspace config is the git repo `kaiserdrake/niu-heimar` (only config is tracked,
not projects). If you edit anything under `.claude-workspace/`, a `CLAUDE.md`, or a
`<DOMAIN>/.claude/` template, offer to run `/workspace-sync` (it lints, branches and opens a PR).
Never push to `main` directly. The weekly `maintain` workflow uses the `ANTHROPIC_API_KEY` repo secret.
