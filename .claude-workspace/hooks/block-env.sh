#!/usr/bin/env bash
# PreToolUse guard: stop agents from touching .env files (and each project's
# .claude/protected-paths) under the workspace.
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

# Web dashboard credentials and merge/pair endpoints are for the user, not agents.
if grep -Eq 'web-sessions\.json' <<<"$targets"; then
  echo "BLOCKED by workspace policy: dashboard device credentials are off-limits to agents." >&2
  exit 2
fi
if [[ "$tool" == "Bash" ]] && grep -Eq '/api/(prs/merge|pair|unpair)' <<<"$targets"; then
  echo "BLOCKED by workspace policy: merging or pairing through the web dashboard is reserved for the user. Ask the user to merge." >&2
  exit 2
fi

# ---------- per-project protected paths ----------
# A project can list extra secrets/personal data in <project>/.claude/protected-paths:
# one path or glob per line, relative to the project root; a directory protects its contents.
find_root() {  # nearest folder at/above $1 (inside the workspace) that has .claude/protected-paths
  local d="$1"
  [[ -d "$d" ]] || d="$(dirname "$d")"
  while under_ws "$d" && [[ "$d" != "$WS" ]]; do
    [[ -f "$d/.claude/protected-paths" ]] && { printf '%s\n' "$d"; return 0; }
    d="$(dirname "$d")"
  done
  return 1
}
abspath() { [[ "$1" == /* ]] && realpath -m -- "$1" || realpath -m -- "${cwd:-$PWD}/$1"; }

roots=()
if r="$(find_root "${cwd:-/nonexistent}")"; then roots+=("$r"); fi
file_targets="$(jq -r '[.tool_input.file_path, .tool_input.path, .tool_input.notebook_path]
                       | map(select(. != null and . != "")) | .[]' <<<"$input" 2>/dev/null)"
while IFS= read -r t; do
  [[ -n "$t" ]] || continue
  if r="$(find_root "$(abspath "$t")")"; then roots+=("$r"); fi
done <<<"$file_targets"
# absolute workspace paths mentioned inside a Bash command
while IFS= read -r t; do
  [[ -n "$t" ]] || continue
  if r="$(find_root "$t")"; then roots+=("$r"); fi
done < <(grep -oE "$WS/[^[:space:]'\"\`;|&<>()]+" <<<"$targets" 2>/dev/null)

protected_block() {
  echo "BLOCKED by workspace policy: $1 is protected by ${2#"$WS"/}/.claude/protected-paths. Ask the user if you need something from it." >&2
  exit 2
}
declare -A seen_root=()
for root in "${roots[@]}"; do
  [[ -n "${seen_root[$root]:-}" ]] && continue
  seen_root[$root]=1
  while IFS= read -r p || [[ -n "$p" ]]; do
    p="${p%%#*}"; p="$(sed -E 's/^[[:space:]]+|[[:space:]]+$//g' <<<"$p")"; p="${p#./}"; p="${p%/}"
    [[ -n "$p" ]] || continue
    # 1) file-path fields that resolve inside the protected path
    while IFS= read -r t; do
      [[ -n "$t" ]] || continue
      a="$(abspath "$t")"
      [[ "$a" == "$root"/* ]] || continue
      rel="${a#"$root"/}"
      # shellcheck disable=SC2053  # $p is intentionally a glob
      if [[ "$rel" == "$p" || "$rel" == "$p"/* || "$rel" == $p || "$rel" == $p/* ]]; then
        protected_block "$tool on '$rel'" "$root"
      fi
    done <<<"$file_targets"
    # 2) commands, search patterns and globs that name the protected path literally
    if [[ "$p" != *[\*\?\[]* ]]; then
      lit_re="$(sed -E 's/[][\.*^$+?(){}|\\/]/\\&/g' <<<"$p")"
      base="$(basename -- "$p")"
      parent="$(basename -- "$(dirname -- "$p")")"
      [[ "$parent" != "." ]] && pair_re="$(sed -E 's/[][\.*^$+?(){}|\\/]/\\&/g' <<<"$parent/$base")" || pair_re="$lit_re"
      base_re="$(sed -E 's/[][\.*^$+?(){}|\\/]/\\&/g' <<<"$base")"
      # a bare basename only counts when it's distinctive (e.g. .vault.pass), not a generic word like "data"
      if [[ "$base" == .* || "$base" == *.* ]]; then names="($lit_re|$pair_re|$base_re)"; else names="($lit_re|$pair_re)"; fi
      if grep -Eq "(^|[^A-Za-z0-9_.-])$names([^A-Za-z0-9_-]|$)" <<<"$targets"; then
        protected_block "$tool referencing '$p'" "$root"
      fi
    fi
  done <"$root/.claude/protected-paths"
done

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
