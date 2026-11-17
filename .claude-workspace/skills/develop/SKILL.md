---
name: develop
description: Design-first development workflow. Use when the user asks to build, add or change a feature (or runs /develop <task>). The designer agent (Opus) plans, the user approves, then you implement and test. Several features can be batched on one branch; the two mandatory Claude reviewers run once, when the complete feature is released.
---

# /develop: design → approve → build → test (review at feature release)

You are the orchestrator. Run these steps in order and keep the user informed with a one-line update per step.
Never commit, push, or open a merge request without asking.

## 1. Design (designer agent, Opus)
- Launch the `designer` subagent with the user's request verbatim, plus any constraints from the conversation.
- When it returns, check that the proposal exists under `.claude-proposals/` and that nothing else changed
  (`git status --porcelain`; in projects the proposal itself is git-excluded and won't show). If the designer touched
  other files, revert them and say so.

## 2. Approval gate
- Show the user the design's decision, the files it changes, and its risks, in at most 10 lines, with the proposal path.
- Ask with AskUserQuestion: **Approve** / **Change it** (they describe what) / **Stop**.
  - Change it: send the feedback back to the designer, then repeat this step.
  - Stop: end here and leave the proposal in place.
- Don't start implementing before an explicit approval.

## 3. Implement (delegate to Sonnet `implementer` agents)
- Split the design's "Changes" table into pieces that each own a set of files. Give each piece to an `implementer`
  agent (Sonnet) with a brief: the proposal path, the files it owns, what to change, the tests to add or run, and what
  done looks like. Launch pieces whose files don't overlap **in parallel, in one message**; run dependent pieces in order.
- Give documentation-only pieces (README/docs, guides, docstrings, comments) to the `doc-writer` agent (Haiku) instead,
  with the same kind of brief. Docs that describe new code usually run after the code pieces are done.
- You (the orchestrator) don't write the bulk of the code. Do only small glue edits yourself, then integrate: check each
  report, run the combined tests, and fix the seams.
- If a piece has to deviate from the design, say so and why, and update the proposal's "Changes" table.
- For broad codebase lookups, use the `Explore` agent or `local_search` (local model) rather than reading many files.

## 4. Test, then stop (no Claude reviewers here)
- Run the tests for what you changed and fix failures. That is the per-change gate.
- `local_diff_review` is optional (free, advisory): use it for larger or risky changes, with `focus` from the design's
  risks. Fix the real findings, note the ignored ones. If both endpoints are down, skip it.
- Do **not** run `security-reviewer` / `test-reviewer` here or at PR time. They run once, when the complete feature is released (workspace CLAUDE.md §2).

## 4b. Remove the proposal (approved + implemented)
- Proposals are not kept once their plan is implemented. Timing depends on where the proposal lives:
  - **Workspace repo** (`.claude-proposals/` at the workspace root, tracked): when every piece of the design is built
    and its tests pass, delete the proposal in the same change (`git rm`, or plain delete if it was never committed), so
    it leaves with the PR that implements it.
  - **Linked projects** (`.claude-proposals/`, git-excluded, so there is no git copy): keep it until the feature is
    released. Delete it after `/dual-review` has read it (workspace CLAUDE.md §2), with a plain delete.
  - If the design is built over several batches, delete it after the last one (in a project, after the feature release).
- Before deleting, move anything durable that the code doesn't show (a convention, a pitfall, an operating note) into
  the project's CLAUDE.md or README, and drop any pointers to the proposal (`See .claude-proposals/...`, `Design: ...`).
- Keep a proposal that was stopped or not yet fully implemented; delete it only when it is implemented or the user says so.

## 5. Report
- Summarise: what changed (files), the proposal's status (removed and its path in the workspace repo; kept until the
  feature release in a project), test results, local review findings fixed or ignored,
  and that the change is **not yet reviewed** (the reviewers run when the complete feature is released).
- Offer the next step and wait: build the next piece of the feature, or open the PR (`/workspace-sync` for the workspace
  repo, the domain's PR/MR flow elsewhere) with `Reviews: deferred to the feature release` in the description. If this
  piece completes the feature, offer to run `/dual-review` on the whole feature before it is released.
  Never commit, push or open a PR without asking.
