#!/usr/bin/env bash
# Tests for the PR panel and merge guard, using a fake `gh` (never touches GitHub).
set -uo pipefail
WS="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd -P)"
SH="$WS/.claude-workspace"
tmp="$(mktemp -d)"; export NIU_EVENTS_DB="$tmp/state/events.db"
FIX="$WS/PERSONAL/.niu-test-prs/app"
cleanup() { rm -rf "$tmp" "$WS/PERSONAL/.niu-test-prs"; }
trap cleanup EXIT
pass=0 fail=0
ok()  { pass=$((pass+1)); }
bad() { fail=$((fail+1)); echo "FAIL: $*"; }

# Fake gh: logs every call; PR state comes from $FAKE_PR (green|failing|pending)
mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
echo "$*" >>"$FAKE_GH_LOG"
case "${FAKE_PR:-green}" in
  green)   roll='[{"__typename":"CheckRun","name":"lint","status":"COMPLETED","conclusion":"SUCCESS"}]' ;;
  failing) roll='[{"__typename":"CheckRun","name":"lint","status":"COMPLETED","conclusion":"FAILURE"}]' ;;
  pending) roll='[{"__typename":"CheckRun","name":"lint","status":"IN_PROGRESS","conclusion":null}]' ;;
esac
pr='{"number":7,"title":"feat: thing","url":"https://github.com/o/r/pull/7","isDraft":false,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","updatedAt":"2026-11-06T00:00:00Z","statusCheckRollup":'"$roll"'}'
case "$1 $2" in
  "pr list") echo "[$pr]" ;;
  "pr view") echo "$pr" ;;
  "pr merge") echo "merged" ;;
  *) echo "unexpected: $*" >&2; exit 1 ;;
esac
GH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" FAKE_GH_LOG="$tmp/gh.log"
: >"$FAKE_GH_LOG"

# 1. green/blocked logic
cd "$SH/lib" && python3 - <<'PY' && ok || bad "is_green logic"
import niu_prs as p
def pr(roll, **kw): return dict({"isDraft": False, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "statusCheckRollup": roll}, **kw)
run = lambda c, s="COMPLETED": {"__typename": "CheckRun", "status": s, "conclusion": c}
assert p.is_green(pr([run("SUCCESS"), run("SKIPPED")]))[0]
assert p.is_green(pr([]))[1] == "ready (no checks)"
assert not p.is_green(pr([run("FAILURE")]))[0]
assert "pending" in p.is_green(pr([run(None, "IN_PROGRESS")]))[1]
assert not p.is_green(pr([{"__typename": "StatusContext", "state": "PENDING"}]))[0]
assert not p.is_green(pr([run("SUCCESS")], isDraft=True))[0]
assert not p.is_green(pr([run("SUCCESS")], mergeable="CONFLICTING"))[0]
assert not p.is_green(pr([run("SUCCESS")], mergeStateStatus="BLOCKED"))[0]
assert p.parse_ref("kaiserdrake/niu-heimar#4") == ("kaiserdrake/niu-heimar", 4)
wf = pr([run("SUCCESS")], mergeStateStatus="BLOCKED", files=[{"path": ".github/workflows/ci.yml"}])
ok, why = p.is_green(wf, scopes={"repo", "gist"})
assert not ok and "'workflow' scope" in why, why
assert p.is_green(dict(wf, mergeStateStatus="CLEAN"), scopes={"repo", "workflow"})[0]
assert "blocked" in p.is_green(dict(wf, files=[{"path": "src/x.py"}]), scopes={"repo"})[1]
PY
cd "$WS" || exit 1

# 2. discovery: workspace repo + a linked GitHub project (nested)
mkdir -p "$FIX" && git -C "$FIX" init -q && git -C "$FIX" remote add origin git@github.com:someone/app.git
"$SH/bin/claude-init" -q "$FIX"
out="$(cd "$SH/lib" && python3 -c 'import niu_prs;print(niu_prs.discover())')"
grep -q "someone/app" <<<"$out" && grep -q "PERSONAL/.niu-test-prs/app" <<<"$out" && ok || bad "discover linked GitHub project: $out"

# 3. --prs lists via gh and writes the cache (mode 600)
out="$("$SH/bin/agents-dash" --prs)"
grep -q "app#7" <<<"$out" && grep -q "● ready" <<<"$out" && ok || bad "--prs output: $out"
[[ "$(stat -c %a "$tmp/state/prs.json")" == "600" ]] && ok || bad "cache mode"
FAKE_PR=failing "$SH/bin/agents-dash" --prs | grep -q "✗ 1 failing" && ok || bad "failing PR shown"

# 4. --merge refuses without a terminal (agents can't merge through it)
: >"$FAKE_GH_LOG"
"$SH/bin/agents-dash" --merge someone/app#7 </dev/null >/dev/null 2>&1 && bad "non-tty merge allowed" || ok
grep -q "pr merge" "$FAKE_GH_LOG" && bad "gh pr merge called without tty" || ok

# 5. tty + y merges a green PR with squash + delete-branch
: >"$FAKE_GH_LOG"
(sleep 1; printf 'y\n') | script -qec "$SH/bin/agents-dash --merge someone/app#7" /dev/null >/dev/null 2>&1
grep -q "pr merge 7 -R someone/app --squash --delete-branch" "$FAKE_GH_LOG" && ok || bad "green merge not called: $(cat "$FAKE_GH_LOG")"

# 6. tty + n cancels
: >"$FAKE_GH_LOG"
(sleep 1; printf 'n\n') | script -qec "$SH/bin/agents-dash --merge someone/app#7" /dev/null >/dev/null 2>&1
grep -q "pr merge" "$FAKE_GH_LOG" && bad "merge ran after answering n" || ok

# 7. failing / pending PRs are never merged, even after y
for st in failing pending; do
  : >"$FAKE_GH_LOG"
  (sleep 1; printf 'y\n') | FAKE_PR=$st script -qec "$SH/bin/agents-dash --merge someone/app#7" /dev/null >/dev/null 2>&1
  grep -q "pr merge" "$FAKE_GH_LOG" && bad "$st PR merged" || ok
done

echo "prs: $pass passed, $fail failed"
((fail == 0))
