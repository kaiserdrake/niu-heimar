#!/usr/bin/env python3
"""Tests for niu_actions.actions(): what counts as 'needs you'."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from niu_actions import actions  # noqa: E402

passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


def sess(sid, state="working", note=None, flagged=False, ended=False, domain="PERSONAL", project="app"):
    return {"id": sid, "state": state, "note": note, "flagged": flagged, "ended": ended, "domain": domain, "project": project}


def pr(n, green=False, failed=0, review=None, scope=False, draft=False):
    return {"number": n, "ref": f"o/r#{n}", "url": f"https://x/{n}", "title": f"t{n}", "green": green, "draft": draft,
            "review": review, "needs_scope": "workflow" if scope else None, "checks": {"passed": 1, "failed": failed, "pending": 0}}


state = {"host": "Midgard", "sessions": [
    sess("s1", "waiting", "permission_prompt"),
    sess("s2", "waiting", "idle_prompt", domain="(workspace)", project=""),
    sess("s3", "idle", flagged=True),
    sess("s4", "waiting", ended=True),
    sess("s5", "working"),
]}
prs = {"repos": [{"prs": [pr(1, green=True), pr(2, failed=2), pr(3, review="CHANGES_REQUESTED"), pr(4, scope=True),
                          pr(5, draft=True, green=True), pr(6)]}]}
acts = actions(state, prs)
ids = [a["id"] for a in acts]
by = {a["id"]: a for a in acts}

check("wait:s1" in ids and "permission" in by["wait:s1"]["title"], "permission prompt needs you")
check("wait:s2" in ids and "reply" in by["wait:s2"]["title"], "idle prompt needs your reply")
check(by.get("wait:s2", {}).get("detail") == "Midgard", "workspace shown as host name")
check("review:s3" not in ids, "unreviewed edits are not a needs-you item (reviews run per PR)")
check(not any(i.endswith(":s4") for i in ids), "ended sessions ignored")
check(not any(i.endswith(":s5") for i in ids), "working session needs nothing")
check("ready:o/r#1" in ids, "green PR ready to merge")
check("fail:o/r#2" in ids and "2 failing" in by["fail:o/r#2"]["title"], "failing PR")
check("changes:o/r#3" in ids, "changes requested")
check("scope:o/r#4" in ids and by["scope:o/r#4"]["priority"] == 3, "scope-blocked PR is high priority")
check(not any("#5" in i for i in ids), "draft PR ignored")
check(not any("#6" in i for i in ids), "pending/plain PR ignored")
check([a["priority"] for a in acts] == sorted((a["priority"] for a in acts), reverse=True), "sorted by priority")
check(len(ids) == len(set(ids)), "ids unique")
check(actions({"sessions": []}, {"repos": []}) == [], "nothing -> empty")

print(f"actions: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
