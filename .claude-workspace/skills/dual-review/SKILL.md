---
name: dual-review
description: Run the two mandatory reviewers (security-reviewer and test-reviewer) in parallel on the whole PR diff and merge their verdicts. Use before opening a PR/MR or pushing a branch for review (workspace-sync calls it), or when the user asks for a review. Not after every edit.
---

# Dual review

1. Work out what to review: the user's target (files, branch, PR), or else **everything the PR will contain**:
   the branch changes against its base plus uncommitted and untracked files
   (`git status`, `git diff --merge-base origin/HEAD`, `git log origin/HEAD..`). Give the reviewers the list of
   features in the batch (from the commit messages or design docs) so they know what each part is meant to do.
2. Pick the models. Both reviewers default to Sonnet (their agent files). Run `security-reviewer` on **Opus**
   (pass `model: "opus"` to the Agent tool) when the PR diff is risky:
   - it touches hooks or the guard (`hooks/`, `block-env.sh`, `.claude/protected-paths`), permissions or settings templates;
   - it handles secrets, tokens, credentials, webhooks or redaction;
   - it touches auth, pairing, merge or other endpoints that act on the user's behalf;
   - it changes CI workflows, Dockerfiles, compose files or deploy scripts;
   - or the diff is large (roughly 800+ changed lines of code).
   Say which model each reviewer runs on and why. `test-reviewer` stays on Sonnet (Opus only if the user asks).
   In a fix round, re-run a reviewer on the same model it used before.
3. In **one message**, launch both subagents in parallel with the Agent tool:
   - `security-reviewer`, given the target and any context about what the change is meant to do.
   - `test-reviewer`, given the same target.
   Also launch any domain-specific reviewers listed in the domain CLAUDE.md.
   If `.claude/deploy-mode` exists, tell both reviewers the folder is the live deployment.
4. When both return, write a combined report:
   - `SECURITY: …` and `TESTS: …` verdict lines, as returned.
   - Blocking findings first, deduplicated, each with `file:line`.
   - Non-blocking notes after.
5. If anything is blocking, fix it (or ask the user for a waiver). Then **re-run only
   the reviewer(s) that blocked** until both pass (at most 3 rounds, then ask the user).
   Batch the fixes: address every blocking finding of a round before re-running.
6. Don't open the PR while a blocking finding remains without a waiver; record waivers in the PR description.
