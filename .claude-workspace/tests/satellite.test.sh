#!/usr/bin/env bash
# Tests for `install --satellite` and the multi-machine lines of workspace-status, in a sandbox:
# a copy of the workspace config, a temp HOME/XDG dirs, and systemctl/loginctl replaced by logging stubs.
set -uo pipefail
REAL="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd -P)"
REAL_HOME="$HOME"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
pass=0 fail=0
ok()  { pass=$((pass+1)); }
bad() { fail=$((fail+1)); echo "FAIL: $*"; }
t() { if "$@"; then ok; else bad "$*"; fi; }
tn() { if "$@"; then bad "(expected failure) $*"; else ok; fi; }

# Existence check in Python (prints 1/0), so the real files are only probed, never read.
exists() { python3 -c 'import os,sys; print(int(os.path.exists(os.path.join(*sys.argv[1:]))))' "$@"; }

mkws() {  # copy the tracked parts of the workspace config into $1 (no secrets, no local state)
  local w="$1" d; mkdir -p "$w/.claude-workspace"
  for d in bin lib hooks agents mcp skills web; do
    [[ -d "$REAL/.claude-workspace/$d" ]] && cp -r "$REAL/.claude-workspace/$d" "$w/.claude-workspace/"
  done
  cp "$REAL"/.claude-workspace/*.template.json "$w/.claude-workspace/"
}
mkenv() {  # $1 = sandbox root; sets the sandbox environment in the current shell
  local c
  export HOME="$1/home" XDG_CONFIG_HOME="$1/home/.config" XDG_STATE_HOME="$1/state" USER="${USER:-tester}"
  export NIU_EVENTS_DB="$1/state/events.db" NIU_USAGE_DB="$1/state/usage.db" NIU_FORWARD_DB="$1/state/forward.db"
  export NIU_AGENT_CONFIG_DIR="$1/state/ollama-claude" NIU_HOST_LABEL=vanaheim
  unset NIU_FORWARD_CONFIG NIU_FORWARD_TOKEN
  mkdir -p "$HOME/.claude" "$XDG_CONFIG_HOME" "$XDG_STATE_HOME" "$1/stubs"
  for c in systemctl loginctl; do
    printf '#!/usr/bin/env bash\necho "%s $*" >>"%s/calls.log"\nexit 0\n' "$c" "$1/stubs" >"$1/stubs/$c"
    chmod +x "$1/stubs/$c"
  done
  : >"$1/stubs/calls.log"
  PATH="$1/stubs:$ORIG_PATH"
}
ORIG_PATH="$PATH"

before_cfg="$(exists "$REAL/.claude-workspace" forward.json)"
before_unit="$(exists "$REAL_HOME/.config/systemd/user" niu-forward.service)"
before_dash="$(exists "$REAL_HOME/.config/systemd/user" niu-dash.service)"

# ---------- satellite ----------
S="$T/sat"; mkdir -p "$S"
mkws "$S/ws"; mkenv "$S"
INST="$S/ws/.claude-workspace/bin/install"
WSTAT="$S/ws/.claude-workspace/bin/workspace-status"
CFG="$S/ws/.claude-workspace/forward.json"
UD="$HOME/.config/systemd/user"
UNIT="$UD/niu-forward.service"

out1="$("$INST" --satellite 2>&1)"; rc=$?
if [[ $rc -eq 0 ]]; then ok; else bad "install --satellite exit $rc"; echo "$out1"; fi
t test -f "$CFG"
t test "$(stat -c %a "$CFG")" = 600
t cmp -s "$CFG" "$S/ws/.claude-workspace/forward.template.json"
t test -f "$UNIT"
t grep -qx 'NoNewPrivileges=true' "$UNIT"
t grep -qx 'Restart=on-failure' "$UNIT"
t grep -qxF "ExecStart=$S/ws/.claude-workspace/bin/niu-forward run" "$UNIT"
t grep -q 'daemon-reload' "$S/stubs/calls.log"
t grep -q 'enable --now niu-forward.service' "$S/stubs/calls.log"
t grep -q '^loginctl ' "$S/stubs/calls.log"
t test ! -e "$UD/niu-dash.service"
t test ! -e "$UD/niu-notify.service"
t test "$(ls "$UD")" = niu-forward.service
tn grep -qE 'niu-dash|niu-notify' "$S/stubs/calls.log"
t test ! -e "$S/ws/.claude-workspace/discord.json"

# a user edit to the config survives a second run; the mode returns to 600; the unit is rewritten identically
chmod 644 "$CFG"
python3 - "$CFG" <<'PY'
import json, sys
p = sys.argv[1]
c = json.load(open(p))
c["url"] = "https://hub.example.ts.net:8443"
json.dump(c, open(p, "w"))
PY
cp "$UNIT" "$S/unit.1"; : >"$S/stubs/calls.log"
out2="$("$INST" --satellite 2>&1)"; rc=$?
if [[ $rc -eq 0 ]]; then ok; else bad "second install --satellite exit $rc"; echo "$out2"; fi
t grep -q 'hub.example.ts.net' "$CFG"
t test "$(stat -c %a "$CFG")" = 600
tn grep -q '^created' <<<"$out2"
t cmp -s "$UNIT" "$S/unit.1"
t test "$(ls "$UD")" = niu-forward.service
t grep -q 'enable --now niu-forward.service' "$S/stubs/calls.log"
tn grep -qE 'niu-dash|niu-notify' "$S/stubs/calls.log"

# workspace-status shows a forwarder line (the url is not paired here, so it may be a "problem" line, but it is there)
st="$(cd "$S/ws" && "$WSTAT" 2>&1)"
t grep -q 'forwarder' <<<"$st"
tn grep -q 'paired machines' <<<"$st"

# ---------- hub: a machine store with 2 machines prints a count only ----------
H="$T/hub"; mkdir -p "$H"
mkws "$H/ws"; mkenv "$H"
mkdir -p "$XDG_STATE_HOME/niu-heimar"
HASH1="9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
HASH2="2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae"
python3 - "$XDG_STATE_HOME/niu-heimar/machines.json" "$HASH1" "$HASH2" <<'PY'
import json, sys
p, h1, h2 = sys.argv[1:4]
json.dump({"machines": [{"label": "Vanaheim", "token_hash": h1, "created": 1},
                         {"label": "Alfheim", "token_hash": h2, "created": 2}]}, open(p, "w"))
PY
hs="$(cd "$H/ws" && "$H/ws/.claude-workspace/bin/workspace-status" 2>&1)"
t grep -qF 'paired machines: 2' <<<"$hs"
tn grep -qF "$HASH1" <<<"$hs"
tn grep -qF "$HASH2" <<<"$hs"
tn grep -q 'forwarder' <<<"$hs"

# ---------- nothing escaped the sandbox ----------
t test "$(exists "$REAL/.claude-workspace" forward.json)" = "$before_cfg"
t test "$(exists "$REAL_HOME/.config/systemd/user" niu-forward.service)" = "$before_unit"
t test "$(exists "$REAL_HOME/.config/systemd/user" niu-dash.service)" = "$before_dash"

echo "satellite: $pass passed, $fail failed"
[[ $fail -eq 0 ]]
