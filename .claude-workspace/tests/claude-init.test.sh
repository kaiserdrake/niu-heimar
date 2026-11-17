#!/usr/bin/env bash
# Tests for bin/claude-init: the .claude-proposals/ exclude line (idempotent, worktrees, already-committed hint).
set -uo pipefail
WS="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd -P)"
SH="$WS/.claude-workspace"
ROOT="$WS/HOBBY/.niu-test-fixture-init"      # fixture inside a domain; removed on exit
cleanup() { rm -rf "$ROOT"; }
trap cleanup EXIT
pass=0 fail=0
ok()  { pass=$((pass+1)); }
bad() { fail=$((fail+1)); echo "FAIL: $*"; }
gi() { git -C "$1" -c user.email=t@t -c user.name=t "${@:2}"; }

[[ -f "$WS/HOBBY/.claude/settings.domain.json" ]] || "$SH/bin/install" --no-user-settings --no-shell >/dev/null
mkdir -p "$ROOT"

# 1. Plain repo: exclude line added exactly once across two runs.
P="$ROOT/plain"; mkdir -p "$P"; git -C "$P" init -q
"$SH/bin/claude-init" -q "$P" && "$SH/bin/claude-init" -q "$P" && ok || bad "claude-init on plain repo"
n="$(grep -cxF '/.claude-proposals/' "$P/.git/info/exclude")"
[[ "$n" == 1 ]] && ok || bad "exclude line count is $n, want 1"
mkdir -p "$P/.claude-proposals"; echo x >"$P/.claude-proposals/x.md"
[[ -z "$(git -C "$P" status --porcelain --untracked-files=all -- .claude-proposals)" ]] && ok || bad "proposal visible to project git"
err="$("$SH/bin/claude-init" -q "$P" 2>&1)"
grep -q 'proposals are already committed' <<<"$err" && bad "hint printed with nothing tracked" || ok

# 2. Worktree (.git is a file): the line must still land in a working exclude file.
M="$ROOT/main"; mkdir -p "$M"; git -C "$M" init -q; gi "$M" commit -q --allow-empty -m init
gi "$M" worktree add -q "$ROOT/wt" -b wt >/dev/null 2>&1
"$SH/bin/claude-init" -q "$ROOT/wt" && ok || bad "claude-init on worktree"
mkdir -p "$ROOT/wt/.claude-proposals"; echo y >"$ROOT/wt/.claude-proposals/y.md"
[[ -z "$(git -C "$ROOT/wt" status --porcelain --untracked-files=all -- .claude-proposals)" ]] && ok || bad "proposal visible in worktree"

# 3. Already-tracked proposals: hint is printed, files are not untracked.
T="$ROOT/tracked"; mkdir -p "$T/.claude-proposals"; git -C "$T" init -q
echo z >"$T/.claude-proposals/z.md"; gi "$T" add -f .claude-proposals/z.md; gi "$T" commit -q -m c
err="$("$SH/bin/claude-init" -q "$T" 2>&1)"
grep -q 'proposals are already committed' <<<"$err" && grep -q 'z.md' <<<"$err" && ok || bad "missing already-committed hint: $err"
[[ -n "$(git -C "$T" ls-files .claude-proposals)" ]] && ok || bad "tracked proposal was untracked"

# 4. Workspace .gitignore: root folder allowed, domain-level still ignored.
git -C "$WS" check-ignore -q .claude-proposals/x.md && bad "root .claude-proposals/x.md is ignored" || ok
git -C "$WS" check-ignore -q PERSONAL/foo/.claude-proposals/x.md && ok || bad "project-level proposals not ignored"
git -C "$WS" check-ignore -q PERSONAL/.claude-proposals/x.md && ok || bad "domain-level proposals not ignored"

echo "claude-init: $pass passed, $fail failed"
exit $((fail > 0))
