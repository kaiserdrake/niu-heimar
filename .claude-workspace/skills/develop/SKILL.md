---
name: develop
description: Design-first development workflow. Use when the user asks to build, add or change a feature (or runs /develop <task>). The designer agent (Opus) plans, the user approves, then you implement and test. Several features can be batched on one branch; the two mandatory Claude reviewers run once, when the complete feature is released.
---

# /develop: design → approve → build → test (review at feature release)

You are the orchestrator. Run these steps in order and keep the user informed with a one-line update per step.
Never commit, push, or open a merge request without asking.

## 1. Design (designer agent, Opus)
- Launch the `designer` subagent with the user's request verbatim, plus any constraints from the conversation.
- When it returns, check that the design document exists under `docs/design/` and that nothing else changed
  (`git status --porcelain`). If the designer touched other files, revert them and say so.

## 2. Approval gate
- Show the user the design's decision, the files it changes, and its risks, in at most 10 lines, with the document path.
- Ask with AskUserQuestion: **Approve** / **Change it** (they describe what) / **Stop**.
  - Change it: send the feedback back to the designer, then repeat this step.
  - Stop: end here and leave the design document in place.
- Don't start implementing before an explicit approval.

## 3. Implement (delegate to Sonnet `implementer` agents)
- Split the design's "Changes" table into pieces that each own a set of files. Give each piece to an `implementer`
  agent (Sonnet) with a brief: the design doc path, the files it owns, what to change, the tests to add or run, and what
  done looks like. Launch pieces whose files don't overlap **in parallel, in one message**; run dependent pieces in order.
- You (the orchestrator) don't write the bulk of the code. Do only small glue edits yourself, then integrate: check each
  report, run the combined tests, and fix the seams.
- If a piece has to deviate from the design, say so and why, and update the design document's "Changes" table.
- For broad codebase lookups, use the `Explore` agent or `local_search` (local model) rather than reading many files.

## 4. Test, then stop (no Claude reviewers here)
- Run the tests for what you changed and fix failures. That is the per-change gate.
- `local_diff_review` is optional (free, advisory): use it for larger or risky changes, with `focus` from the design's
  risks. Fix the real findings, note the ignored ones. If both endpoints are down, skip it.
- Do **not** run `security-reviewer` / `test-reviewer` here or at PR time. They run once, when the complete feature is released (workspace CLAUDE.md §2).

## 5. Report
- Summarise: what changed (files), the design document path, test results, local review findings fixed or ignored,
  and that the change is **not yet reviewed** (the reviewers run when the complete feature is released).
- Offer the next step and wait: build the next piece of the feature, or open the PR (`/workspace-sync` for the workspace
  repo, the domain's PR/MR flow elsewhere) with `Reviews: deferred to the feature release` in the description. If this
  piece completes the feature, offer to run `/dual-review` on the whole feature before it is released.
  Never commit, push or open a PR without asking.
