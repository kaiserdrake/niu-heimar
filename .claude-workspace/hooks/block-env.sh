#!/usr/bin/env bash
# PreToolUse guard: stop agents from touching .env files under the workspace.
# Exit 2 = block (stderr is shown to Claude). Exit 0 = allow.
set -uo pipefail

WS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
input="$(cat)"

jqr() { jq -r "$1 // empty" <<<"$input" 2>/dev/null; }

tool="$(jqr '.tool_name')"
cwd="$(jqr '.cwd')"

# Every string field the tool could use to reach a file.
targets="$(jq -r '[.tool_input.file_path, .tool_input.path, .tool_input.notebook_path,
                   .tool_input.pattern, .tool_input.glob, .tool_input.command]
                  | map(select(. != null)) | .[]' <<<"$input" 2>/dev/null)"

under_ws() { [[ "$1" == "$WS" || "$1" == "$WS"/* ]]; }

in_scope=false
under_ws "${cwd:-/nonexistent}" && in_scope=true
if ! $in_scope; then
  while IFS= read -r t; do
    [[ "$t" == /* ]] && under_ws "$t" && in_scope=true
    # Bash commands that mention the workspace path anywhere
    [[ "$t" == *"$WS"* ]] && in_scope=true
  done <<<"$targets"
fi
$in_scope || exit 0

block() {
  echo "BLOCKED by workspace policy: $1. .env files are off-limits to agents. Use .env.example to see variable names, and ask the user for any real values." >&2
  exit 2
}

# Remove the allowed template names first, then look for any remaining .env token.
cleaned="$(sed -E 's/\.env\.(example|sample|template|dist)([^A-Za-z0-9_-]|$)/\2/g' <<<"$targets")"
envre='(^|[^A-Za-z0-9_])\.env(\.[A-Za-z0-9_-]+)?([^A-Za-z0-9_-]|$)'
if grep -Eq "$envre" <<<"$cleaned"; then
  block "$tool references a .env file"
fi
# Wildcards that would expand to .env files (e.g. `cat .e*`, `.env*`)
if grep -Eq '(^|[^A-Za-z0-9_])\.e(n(v)?)?\*' <<<"$cleaned"; then
  block "$tool uses a wildcard that matches .env files"
fi

if [[ "$tool" == "Bash" ]]; then
  cmd="$(jqr '.tool_input.command')"
  if grep -Eq '(^|[;&|(`]|\$\()[[:space:]]*(sudo[[:space:]]+)?(printenv|export[[:space:]]+-p|declare[[:space:]]+-x)([[:space:]]|$|[;&|)])' <<<"$cmd" \
     || grep -Eq '(^|[;&|(`]|\$\()[[:space:]]*env[[:space:]]*($|[;&|>)])' <<<"$cmd"; then
    block "dumping the environment"
  fi
  if grep -Eq 'docker([[:space:]]+|-)compose([[:space:]]+[^;&|]*)?[[:space:]]config' <<<"$cmd"; then
    block "'docker compose config' prints interpolated secrets"
  fi
  if grep -Eq '/proc/[^[:space:]]*/environ' <<<"$cmd"; then
    block "reading process environment"
  fi
fi

exit 0
