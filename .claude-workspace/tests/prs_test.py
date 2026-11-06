#!/usr/bin/env python3
"""Tests for niu_prs.is_release(): only the release bot's same-repo release-please PRs count."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from niu_prs import is_release, summarize  # noqa: E402

passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


REL = "release-please--branches--main--components--niu-heimar"


def pr(branch=REL, login="app/github-actions", fork=False, **kw):
    return {"number": 1, "headRefName": branch, "author": {"login": login}, "isCrossRepository": fork, **kw}


check(is_release(pr()), "bot release-please PR is a release")
check(not is_release(pr(branch="feat/x")), "normal branch is not")
check(not is_release(pr(branch="feat/release-please--x")), "prefix only, not substring")
check(not is_release(pr(login="someone")), "human author with release branch name is not")
check(not is_release(pr(fork=True)), "fork PR with bot-like data is not")
check(not is_release({"headRefName": REL, "author": {"login": "app/github-actions"}}), "missing fork flag -> not")
check(not is_release({}), "empty -> not")
s = summarize({"fetched": 1, "repos": [{"label": "x", "repo": "o/r", "prs": [pr(), pr(branch="feat/x")]}]})
check([p["release"] for p in s["repos"][0]["prs"]] == [True, False], "summarize carries the release flag")

print(f"prs: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
