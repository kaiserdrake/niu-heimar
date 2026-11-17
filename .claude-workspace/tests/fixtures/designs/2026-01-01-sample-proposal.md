# Sample proposal: golden fixture for the Proposals renderer
Status: proposed · Date: 2026-01-01 · Request: a committed doc so `designs_test.py` always has a real-shaped proposal to parse.

## Decision
- Keep **one** sample proposal under `tests/fixtures/designs/`, shaped like what the `designer` agent writes.
- Real proposals in `.claude-proposals/` are checked too, when present.

## Options weighed
1. Skip the golden pass when no proposals exist: *rejected*, it would silently test nothing in CI.
2. A committed fixture: **chosen**.

> Quoted note with `inline code` and a [link](https://example.invalid/).

## Changes
| File | Change |
|---|---|
| `tests/fixtures/designs/2026-01-01-sample-proposal.md` | New fixture |
| `tests/designs_test.py` | Include fixtures in the golden pass |

## Example
```python
# a heading-like line inside a fence is not a heading
real = sorted(FIXTURES.glob("*.md"))
```

## Risks
- None; test-only.

---

## Open questions
- None.
