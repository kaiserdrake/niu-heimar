#!/usr/bin/env bash
# Tests for hooks/block-env.sh. Run from anywhere.
set -uo pipefail
WS="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd -P)"
H="$WS/.claude-workspace/hooks/block-env.sh"
P="$WS/PERSONAL/demo"
pass=0 fail=0

run() { # expect tool cwd input-json-fragment
  local exp="$1" tool="$2" cwd="$3" ti="$4" got
  jq -nc --arg t "$tool" --arg c "$cwd" --argjson ti "$ti" '{tool_name:$t,cwd:$c,tool_input:$ti}' \
    | bash "$H" >/dev/null 2>&1
  got=$?
  if [[ "$got" == "$exp" ]]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL (want $exp got $got): $tool $ti"; fi
}
b() { run 2 "$@"; }  # blocked
a() { run 0 "$@"; }  # allowed

b Read "$P" "{\"file_path\":\"$P/.env\"}"
b Read "$P" "{\"file_path\":\"$P/.env.production\"}"
b Read "$P" "{\"file_path\":\"$P/config/.env.local\"}"
a Read "$P" "{\"file_path\":\"$P/.env.example\"}"
a Read "$P" "{\"file_path\":\"$P/.env.sample\"}"
a Read "$P" "{\"file_path\":\"$P/src/env.ts\"}"
a Read "$P" "{\"file_path\":\"$P/.envrc.md\"}"
b Edit "$P" "{\"file_path\":\"$P/.env\"}"
b Write "$P" '{"file_path":".env"}'
b Grep "$P" '{"pattern":"SECRET","path":".env"}'
b Glob "$P" '{"pattern":"**/.env*"}'
b Bash "$P" '{"command":"cat .env"}'
b Bash "$P" '{"command":"source ./.env && npm start"}'
b Bash "$P" '{"command":"grep TOKEN .env.local"}'
b Bash "$P" '{"command":"cp .env /tmp/x"}'
b Bash "$P" '{"command":"cat .e*"}'
b Bash "$P" '{"command":"printenv"}'
b Bash "$P" '{"command":"env | grep KEY"}'
b Bash "$P" '{"command":"docker compose config"}'
b Bash "$P" '{"command":"docker-compose -f a.yml config"}'
b Bash "$P" '{"command":"cat /proc/self/environ"}'
a Bash "$P" '{"command":"cat .env.example"}'
a Bash "$P" '{"command":"node -e \"console.log(process.env.NODE_ENV)\""}'
a Bash "$P" '{"command":"env NODE_ENV=test npm test"}'
a Bash "$P" '{"command":"docker compose up -d --build"}'
a Bash "$P" '{"command":"python -m venv .venv"}'
a Bash "$P" '{"command":"ls -la"}'
# Web dashboard: credentials and merge/pair endpoints are user-only
b Read "$P" '{"file_path":"/home/u/.local/state/niu-heimar/web-sessions.json"}'
b Bash "$P" '{"command":"cat ~/.local/state/niu-heimar/web-sessions.json"}'
b Bash "$P" '{"command":"curl -X POST -H X-Niu-CSRF:1 http://127.0.0.1:8765/api/prs/merge -d {}"}'
b Bash "$P" '{"command":"curl -X POST http://127.0.0.1:8765/api/pair"}'
a Bash "$P" '{"command":"curl -s http://127.0.0.1:8765/api/state"}'
# Outside the workspace: not our business
a Read "/tmp/other" '{"file_path":"/tmp/other/.env"}'
a Bash "/tmp/other" '{"command":"cat .env"}'
# Outside cwd, but targeting the workspace
b Read "/tmp/other" "{\"file_path\":\"$P/.env\"}"
b Bash "/tmp/other" "{\"command\":\"cat $P/.env\"}"

echo "block-env: $pass passed, $fail failed"
((fail == 0))
