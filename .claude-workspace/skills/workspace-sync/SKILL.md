---
name: workspace-sync
description: Commit changes to the niu-heimar workspace config (rules, agents, skills, hooks, domain templates) on a branch and open a PR. Use when the workspace config was edited, or when the user says sync/save/publish the workspace setup.
---

# Workspace sync

The workspace root is the nearest parent folder of the current directory that contains
`.claude-workspace/`. Run every command below in that root (`git -C <root> ...`).

1. `git status --porcelain`. If it's clean, say so and stop.
2. Lint: `.claude-workspace/bin/workspace-lint`. If it fails, fix the problem (or report it) before continuing.
3. Show the user the diff summary (`git diff --stat`, including untracked files). Make sure
   no memory, `.env`, rendered `settings.domain.json`, or WORK-confidential file is
   staged (`.gitignore` should already exclude them).
4. Run the `dual-review` skill on the diff when scripts or hooks changed.
5. Create a branch `chore/<short-slug>` from up-to-date `main`, commit with a
   conventional-commit message (`feat:`, `fix:`, `chore:`, `docs:` …), and push with `-u origin`.
6. Open a PR: `gh pr create --fill` if `gh` is installed; otherwise print
   `https://github.com/kaiserdrake/niu-heimar/compare/<branch>?expand=1`.
7. Switch back to `main`. Never push to `main` directly.
