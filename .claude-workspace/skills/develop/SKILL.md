---
name: develop
description: Design-first development workflow. Use when the user asks to build, add or change a feature (or runs /develop <task>). The designer agent (Opus) plans, the user approves, then you implement; a local Ollama agent does a first-pass diff review, and the two mandatory Claude reviewers gate completion.
---

# /develop: design → approve → build → review

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

## 3. Implement
- Follow the approved design. If you have to deviate, say so and why, and update the design document's "Changes" table.
- For broad codebase lookups during implementation, prefer `local_search` (local model) over reading many files.
- Add or extend tests as listed in the design's test plan.

## 4. Local diff review (advisory)
- Call `local_diff_review` (with `focus` set from the design's risks, if any). It runs on the GPU box, or the local CPU as a
  fallback, and says which.
- Fix the findings that are real. Note the ones you're ignoring and why. This step never blocks completion on its own.
- If `local_diff_review` is unavailable (both endpoints down), say so and continue.

## 5. Mandatory review (Claude)
- Run the `dual-review` skill: `security-reviewer` and `test-reviewer` in parallel.

## 6. Fix loop (at most 3 rounds)
- If either reviewer reports blocking findings, fix them, then re-run only the reviewer(s) that blocked.
- After 3 rounds with blockers still open, stop and ask the user how to proceed.

## 7. Report
- Summarise: what changed (files), design document path, local review findings fixed or ignored,
  reviewer verdicts (`SECURITY: …`, `TESTS: …`), and anything left open.
- Offer the next step (commit, or open a merge request / PR per the domain's rules), and wait for the user.
