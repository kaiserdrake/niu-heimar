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
b Bash "$P" '{"command":"curl -X POST -H X-Niu-CSRF:1 http://127.0.0.1:8765/api/sessions/dismiss -d {}"}'
a Bash "$P" '{"command":"curl -s http://127.0.0.1:8765/api/state"}'
# research-dispatch monitor token: file and setter command are user-only; the read-only endpoint stays open
b Read "$P" '{"file_path":"/home/u/.local/state/niu-heimar/research-dispatch.token"}'
b Bash "$P" '{"command":"cat ~/.local/state/niu-heimar/research-dispatch.token"}'
b Bash "$P" '{"command":"head -c 20 /home/u/.local/state/niu-heimar/research-dispatch.token"}'
b Bash "$P" '{"command":".claude-workspace/bin/niu-dash research-token"}'
b Bash "$P" '{"command":"niu-dash research-token --remove"}'
b Bash "$P" '{"command":"cd x && niu-dash research-token"}'
b Bash "$P" '{"command":"cat ~/.local/state/niu-heimar/research-dispatch.t*"}'
b Bash "$P" '{"command":"cat ~/.local/state/niu-heimar/*"}'
b Bash "$P" '{"command":"niu-dash \"research-token\" --remove"}'
b Bash "$P" '{"command":"python3 -c \"import niu_research; print(niu_research.read_token())\""}'
b Write "$P" '{"file_path":"/ws/.claude-workspace/research.json","content":"{}"}'
b Edit "$P" '{"file_path":"/ws/.claude-workspace/research.template.json"}'
b Bash "$P" '{"command":"echo {} > .claude-workspace/research.json"}'
a Bash "$P" '{"command":"curl -s http://127.0.0.1:8765/api/research"}'
a Bash "$P" '{"command":"niu-dash devices"}'
# research-dispatch topics token and the dashboard topics routes: user-only too
b Read "$P" '{"file_path":"/home/u/.local/state/niu-heimar/research-dispatch.topics.token"}'
b Bash "$P" '{"command":"cat ~/.local/state/niu-heimar/research-dispatch.topics.token"}'
b Bash "$P" '{"command":"niu-dash research-token --scope topics"}'
b Bash "$P" '{"command":".claude-workspace/bin/niu-dash research-token --scope topics --remove"}'
b Bash "$P" '{"command":"curl -X POST -H X-Niu-CSRF:1 http://127.0.0.1:8765/api/topics/update -d {}"}'
b Bash "$P" '{"command":"curl -X POST http://127.0.0.1:8765/api/topics/create"}'
b Bash "$P" '{"command":"curl http://127.0.0.1:8765/api/topics"}'
a Bash "$P" '{"command":"python3 .claude-workspace/tests/topics_test.py"}'
# Outside the workspace: not our business
a Read "/tmp/other" '{"file_path":"/tmp/other/.env"}'
a Bash "/tmp/other" '{"command":"cat .env"}'
# Outside cwd, but targeting the workspace
b Read "/tmp/other" "{\"file_path\":\"$P/.env\"}"
b Bash "/tmp/other" "{\"command\":\"cat $P/.env\"}"

# ---------- per-project .claude/protected-paths ----------
Q="$WS/PERSONAL/.niu-test-protected/svc"
mkdir -p "$Q/.claude" "$Q/var/worker/private" "$Q/src"
printf '# test\n.vault.pass\n.app_cfg.ini\nvar/worker/private/\n*.secret.json\n' >"$Q/.claude/protected-paths"
trap 'rm -rf "$WS/PERSONAL/.niu-test-protected"' EXIT
b Read "$Q" "{\"file_path\":\"$Q/.vault.pass\"}"
b Read "$Q" '{"file_path":".app_cfg.ini"}'
b Edit "$Q" "{\"file_path\":\"$Q/.app_cfg.ini\"}"
b Read "$Q" "{\"file_path\":\"$Q/var/worker/private/2026-10/report.csv\"}"
b Read "$Q/src" '{"file_path":"../var/worker/private/people.csv"}'
b Read "$Q" "{\"file_path\":\"$Q/config/prod.secret.json\"}"
b Grep "$Q" '{"pattern":"total","path":"var/worker/private"}'
b Bash "$Q" '{"command":"cat .vault.pass"}'
b Bash "$Q" '{"command":"gpg --passphrase-file ./.vault.pass -d backup.gpg"}'
b Bash "$Q" '{"command":"ls var/worker/private/"}'
b Bash "$Q" '{"command":"head -5 worker/private/people.csv"}'
b Bash "/tmp" "{\"command\":\"cat $Q/.app_cfg.ini\"}"
b Read "/tmp" "{\"file_path\":\"$Q/.vault.pass\"}"
a Read "$Q" "{\"file_path\":\"$Q/src/app.py\"}"
a Read "$Q" "{\"file_path\":\"$Q/.claude/protected-paths\"}"
a Bash "$Q" '{"command":"docker compose ps"}'
a Bash "$Q" '{"command":"ls src data"}'
a Bash "$Q" '{"command":"grep -r total src/"}'
a Read "$P" "{\"file_path\":\"$P/.vault.pass\"}"   # other projects: not protected unless listed

echo "block-env: $pass passed, $fail failed"
((fail == 0))
