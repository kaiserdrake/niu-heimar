---
name: dual-review
description: Run the two mandatory reviewers (security-reviewer and test-reviewer) in parallel on the current changes and merge their verdicts. Use after any code change, before calling a task done, or when the user asks for a review.
---

# Dual review

1. Work out what to review: the user's target (files, branch, PR), or else the
   uncommitted and branch changes (`git status`, `git diff --merge-base HEAD origin/HEAD` or `git diff HEAD`).
2. In **one message**, launch both subagents in parallel with the Agent tool:
   - `security-reviewer`, given the target and any context about what the change is meant to do.
   - `test-reviewer`, given the same target.
   Also launch any domain-specific reviewers listed in the domain CLAUDE.md.
3. When both return, write a combined report:
   - `SECURITY: …` and `TESTS: …` verdict lines, as returned.
   - Blocking findings first, deduplicated, each with `file:line`.
   - Non-blocking notes after.
4. If anything is blocking, fix it (or ask the user for a waiver). Then **re-run only
   the reviewer(s) that blocked** until both pass.
5. Don't report the task done while a blocking finding remains without a waiver.
