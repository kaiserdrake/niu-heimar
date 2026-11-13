---
name: document
description: Documentation workflow. Use when the user asks to write, update or fix documentation (or runs /document <task>) - README and docs/ pages, setup and usage guides, CLAUDE.md / LEARNINGS.md wording, docstrings and comments, or bringing docs in line with code that already changed. The doc-writer agent (Haiku) does the writing; you scope, check and report. Not for changes to behaviour, which go through /develop.
---

# /document: scope → write (Haiku) → check → report

You are the orchestrator. The `doc-writer` agent (Haiku) writes; you keep your own work to scoping, checking and
reporting, so docs work costs Haiku rather than Opus or Sonnet tokens. Keep the user informed with a one-line update
per step. Never commit, push, or open a merge request without asking.

## 1. Scope
- Work out which files to write or change and what the docs must cover. For a broad "what needs documenting" question
  use the `Explore` agent, `local_search` or `local_git` (what changed since the docs were last touched), not many
  reads in the main session.
- If the request needs code, config or test changes, it isn't a docs task: say so and suggest `/develop`. A doc that
  goes with a feature still being built waits until the code pieces are done.
- Ask the user only when the audience or scope is genuinely unclear (for example a new guide with no obvious home).

## 2. Write (delegate to `doc-writer` agents, Haiku)
- Give each piece to a `doc-writer` agent with a brief: the files it owns, the code or config it documents (paths),
  what to add or change, the audience, and what done looks like. Point it at any design doc under `docs/design/`.
- Pieces whose files don't overlap run **in parallel, in one message**. One file has one writer.
- Do one- or two-line wording fixes yourself; don't spawn an agent for them.

## 3. Check
- Read each report. For every "couldn't verify" item, check it yourself or drop the claim. Spot-check the commands,
  paths and flags the new text mentions against the code (Grep/Read).
- Make sure the diff touches only documentation and comments (`git diff --stat`). Revert anything else and say so.
- Run the project's docs checks if it has any (markdown lint, link check, `workspace-lint` in niu-heimar).
- Don't run `security-reviewer` / `test-reviewer` here. They run once, when the complete feature is released
  (workspace CLAUDE.md §2).

## 4. Report
- Summarise the files changed, what each now covers, any claims dropped or left open, and the checks run. Say the
  change is **not yet reviewed**.
