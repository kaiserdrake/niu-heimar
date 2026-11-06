#!/usr/bin/env bash
# Tests for hooks/agent-events.py + bin/agents-dash, including a nested project with its own agents.
set -uo pipefail
WS="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd -P)"
SH="$WS/.claude-workspace"
tmp="$(mktemp -d)"; export NIU_EVENTS_DB="$tmp/events.db"
FIX="$WS/HOBBY/.niu-test-fixture/voxel-mod"     # nested: HOBBY/<group>/<project>
cleanup() { rm -rf "$tmp" "$WS/HOBBY/.niu-test-fixture"; }
trap cleanup EXIT
pass=0 fail=0
ok()   { pass=$((pass+1)); }
bad()  { fail=$((fail+1)); echo "FAIL: $*"; }
has()  { grep -qF -- "$2" <<<"$1" && ok || bad "expected '$2'"; }
hasnt(){ grep -qF -- "$2" <<<"$1" && bad "did not expect '$2'" || ok; }

# Fixture: nested git project with 3 workflow agents of its own
mkdir -p "$FIX/.claude/agents" "$FIX/src"
git -C "$FIX" init -q
for a in asset-pipeline build-runner release-packager; do
  printf -- '---\nname: %s\ndescription: test\nmodel: sonnet\n---\nTest agent.\n' "$a" >"$FIX/.claude/agents/$a.md"
done
[[ -f "$WS/HOBBY/.claude/settings.domain.json" ]] || "$SH/bin/install" --no-user-settings --no-shell >/dev/null
"$SH/bin/claude-init" -q "$FIX" && ok || bad "claude-init on nested project"
[[ -L "$FIX/.claude/agents/security-reviewer.md" && -f "$FIX/.claude/agents/build-runner.md" && ! -L "$FIX/.claude/agents/build-runner.md" ]] \
  && ok || bad "reviewers linked next to project agents"
st="$(git -C "$FIX" status --porcelain --untracked-files=all -- .claude .mcp.json)"
grep -qE 'security-reviewer|test-reviewer|settings.local|skills/|mcp.json' <<<"$st" && bad "links visible to project git: $st" || ok
grep -q 'build-runner.md' <<<"$st" && ok || bad "project's own agents should stay visible to its git"

ev() { # event [agent_id agent_type] [tool] [tool_input-json] [cwd]
  local e="$1" aid="${2:-}" at="${3:-}" tool="${4:-}" ti="${5:-{\}}" cwd="${6:-$FIX/src}"
  jq -nc --arg e "$e" --arg s "${SID:-sess-1111}" --arg c "$cwd" --arg aid "$aid" --arg at "$at" --arg t "$tool" --argjson ti "$ti" \
    '{hook_event_name:$e,session_id:$s,cwd:$c,tool_name:$t,tool_input:$ti,tool_use_id:("tu-"+$t)}
     + (if $aid=="" then {} else {agent_id:$aid,agent_type:$at} end)' | "$SH/hooks/agent-events.py"
}

SID=sess-1111
ev SessionStart; ev UserPromptSubmit
ev PreToolUse "" "" Edit "{\"file_path\":\"$FIX/src/biome.py\"}"; ev PostToolUse "" "" Edit "{}"
ev SubagentStart a1 asset-pipeline
ev PreToolUse a1 asset-pipeline Bash '{"command":"blender -b --python export.py API_KEY=abc123def"}'
ev SubagentStart a2 security-reviewer
ev PreToolUse a2 security-reviewer Bash '{"command":"curl -H \"Authorization: Bearer sk-livesecret123456\" https://u:pw@x.io/h"}'

out="$("$SH/bin/agents-dash" --once)"
has "$out" "HOBBY"
has "$out" ".niu-test-fixture/voxel-mod"
has "$out" "● working"
has "$out" "asset-pipeline"
has "$out" "[project]"
has "$out" "[shared]"
has "$out" "● running"
has "$out" "blender -b --python export.py API_KEY=****"
hasnt "$out" "abc123def"
hasnt "$out" "sk-livesecret"
hasnt "$out" "u:pw@"
has "$out" "build-runner"
has "$out" "○ not yet run"

# Redaction keeps paths readable but masks opaque tokens
r="$(cd "$SH/lib" && python3 -c 'import sys,niu_events as n;print(n.redact(sys.argv[1]))' "/home/u/WORKSPACE/.claude-workspace/bin/agents-dash tok=1 x 9f8e7d6c5b4a39281706f5e4d3c2b1a0ffeeddcc")"
has "$r" ".claude-workspace/bin/agents-dash"
hasnt "$r" "9f8e7d6c5b4a"

# Subagent finishes; session stops without test-reviewer -> flagged
ev SubagentStop a1 asset-pipeline; ev SubagentStop a2 security-reviewer; ev Stop
out="$("$SH/bin/agents-dash" --once)"
has "$out" "○ idle"
has "$out" "edited without both reviewers"
has "$out" "1 without reviewers: HOBBY/.niu-test-fixture/voxel-mod"

# Second session runs both reviewers -> not flagged
SID=sess-2222
ev SessionStart; ev UserPromptSubmit; ev PreToolUse "" "" Write '{"file_path":"x.py"}'
ev SubagentStart b1 security-reviewer; ev SubagentStart b2 test-reviewer
ev SubagentStop b1 security-reviewer; ev SubagentStop b2 test-reviewer; ev Stop
json="$("$SH/bin/agents-dash" --json)"
[[ "$(jq -r '.[] | select(.id=="sess-2222") | .flagged' <<<"$json")" == "false" ]] && ok || bad "reviewed session not flagged"
[[ "$(jq -r '.[] | select(.id=="sess-1111") | .flagged' <<<"$json")" == "true" ]] && ok || bad "unreviewed session flagged"

# Waiting for permission
SID=sess-3333; ev SessionStart; ev UserPromptSubmit
jq -nc --arg c "$FIX" '{hook_event_name:"Notification",session_id:"sess-3333",cwd:$c,notification_type:"permission_prompt",message:"needs permission"}' | "$SH/hooks/agent-events.py"
has "$("$SH/bin/agents-dash" --once)" "◐ waiting for you"

# Outside the workspace: nothing recorded
before="$(python3 -c "import sqlite3,sys;print(sqlite3.connect(sys.argv[1]).execute('select count(*) from events').fetchone()[0])" "$NIU_EVENTS_DB")"
SID=sess-out ev PreToolUse "" "" Bash '{"command":"ls"}' "/tmp"
after="$(python3 -c "import sqlite3,sys;print(sqlite3.connect(sys.argv[1]).execute('select count(*) from events').fetchone()[0])" "$NIU_EVENTS_DB")"
[[ "$before" == "$after" ]] && ok || bad "event outside workspace was recorded"

# Retention: old rows pruned on SessionStart
python3 -c "import sqlite3,sys,time;c=sqlite3.connect(sys.argv[1]);c.execute(\"insert into events(ts,event,session_id) values(?,'Stop','old')\",(time.time()-8*86400,));c.commit()" "$NIU_EVENTS_DB"
SID=sess-4444 ev SessionStart
old="$(python3 -c "import sqlite3,sys;print(sqlite3.connect(sys.argv[1]).execute(\"select count(*) from events where session_id='old'\").fetchone()[0])" "$NIU_EVENTS_DB")"
[[ "$old" == "0" ]] && ok || bad "8-day-old event not pruned"
[[ "$(stat -c %a "$NIU_EVENTS_DB")" == "600" ]] && ok || bad "db mode is not 600"

# Malformed input never fails
echo 'not json' | "$SH/hooks/agent-events.py" && ok || bad "malformed input exit code"

echo "agent-events: $pass passed, $fail failed"
((fail == 0))
