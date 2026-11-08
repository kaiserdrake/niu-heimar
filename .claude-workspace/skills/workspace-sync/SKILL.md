---
name: workspace-sync
description: Lint, commit and open a PR for a batch of niu-heimar workspace config changes (rules, agents, skills, hooks, domain templates). The two reviewers run only when a complete feature is released or the user asks. Use when the user wants to send the batched workspace changes as a PR, or says sync/save/publish the workspace setup.
---

# Workspace sync

The workspace root is the nearest parent folder of the current directory that contains
`.claude-workspace/`. Run every command below in that root (`git -C <root> ...`).

Changes can be batched: several features may sit uncommitted on `main`, or be committed one by one on a
feature branch (`feat/<slug>` / `chore/<slug>`), before a single PR. Reviews are not part of a normal PR: they run
when a complete feature is released, or when the user asks (workspace CLAUDE.md §2).

1. `git status --porcelain` and `git log origin/main..HEAD`. If both are empty, say so and stop.
2. Lint: `.claude-workspace/bin/workspace-lint`. If it fails, fix the problem (or report it) before continuing.
   Name any failure that is pre-existing and unrelated rather than hiding it.
3. Show the user the diff summary (`git diff --stat origin/main`, plus untracked files), grouped by feature. Make sure
   no memory, `.env`, rendered `settings.domain.json`, or WORK-confidential file is
   staged (`.gitignore` should already exclude them). Leave out changes that belong to another session's unfinished work
   unless the user includes them.
4. Do not run the reviewers for an ordinary PR. Run the `dual-review` skill only when this PR is the release of a complete
   feature (the user says so, or it is the release PR) or the user asks for it; then review the whole feature range.
   Fix blocking findings in batches, re-run only the reviewer that blocked (at most 3 rounds), or record the user's waiver
   for the PR description. In every other case note in the PR description that reviews are deferred to the feature release.
5. Ask the user before committing. If on `main`, create a branch `chore/<short-slug>` (or `feat/…`) from up-to-date
   `main`. Commit with conventional-commit messages (`feat:`, `fix:`, `chore:`, `docs:` …); one commit per feature
   is fine. Push with `-u origin`.
6. Open a PR: `gh pr create` with a body listing each feature, the test results, and either the reviewer verdicts
   (`SECURITY: …`, `TESTS: …`) and any waivers, or the line `Reviews: deferred to the feature release`. Without `gh`, print `https://github.com/kaiserdrake/niu-heimar/compare/<branch>?expand=1`.
7. Switch back to `main`. Never push to `main` directly.
