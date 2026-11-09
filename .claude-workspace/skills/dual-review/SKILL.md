---
name: dual-review
description: Run the two mandatory reviewers (security-reviewer and test-reviewer) in parallel on the whole feature diff and merge their verdicts. Use when a complete feature is being released (the user says it is complete or ready to release, or a release PR / tag is about to be cut), or when the user asks for a review. Not per PR and not after every edit.
---

# Dual review

1. Work out what to review: the user's target (files, branch, PR), or else **the whole feature being released**:
   everything merged for it since the last reviewed release (`git log <last-reviewed-release>..HEAD`,
   `git diff <last-reviewed-release>...HEAD`; if no release exists yet, the feature's branch changes against its base)
   plus uncommitted and untracked files (`git status`). If the feature spans several repos, review each repo's range.
   Give the reviewers the list of PRs/features in the range (from the commit messages or design docs) so they know what
   each part is meant to do. Ask the user to confirm the range if it is not obvious.
2. Pick the models. Both reviewers run on **Sonnet** (their agent files), whatever the diff touches; this keeps
   token use down. `security-reviewer` is capped at Sonnet: never pass `model: "opus"` for it, even when asked or
   when the diff is risky. Use Opus for `test-reviewer` only when the user explicitly asks for it in chat (pass
   `model: "opus"` to the Agent tool). For a risky diff (hooks or the guard, secrets or tokens, auth or pairing, CI or
   deploy, or roughly 800+ changed lines), say so in the verdict so the user can ask for an Opus `test-reviewer` re-run.
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
6. Don't cut the release while a blocking finding remains without a waiver; record waivers in the release PR description.
