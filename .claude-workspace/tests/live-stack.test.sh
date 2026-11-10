#!/usr/bin/env bash
# Tests for hooks/live-stack.py (fake docker on PATH). Run from anywhere.
set -uo pipefail
H="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd -P)/hooks/live-stack.py"
T="$(cd "$(mktemp -d)" && pwd -P)"; trap 'rm -rf "$T"' EXIT
export WORKSPACE="$T"
mkdir -p "$T/DOM/live/.claude" "$T/DOM/live/docker/proxy/site" "$T/DOM/live/docker/app" "$T/DOM/live/.git" \
  "$T/DOM/plain" "$T/DOM/wt" "$T/bin" "$T/other"
printf 'mode: live-dev-same-dir\nlive-path: docker/proxy/\n' > "$T/DOM/live/.claude/deploy-mode"
mkdir -p "$T/DOM/wt/.claude"; cp "$T/DOM/live/.claude/deploy-mode" "$T/DOM/wt/.claude/"; echo "gitdir: x" > "$T/DOM/wt/.git"
ROOT="$T/DOM/live"; WT="$T/DOM/wt"
cat > "$T/bin/docker" <<'D'
#!/usr/bin/env bash
case "${FAKE_DOCKER:-here}" in
  fail) exit 1 ;;
  stopped) exit 0 ;;
  *) [[ "$*" == *"working_dir=$FAKE_ROOT"* ]] && printf 'a\trunning\t%s\nb\texited\t%s\n' "$FAKE_ROOT" "$FAKE_ROOT" ;;
esac
exit 0
D
chmod +x "$T/bin/docker"
export PATH="$T/bin:$PATH" FAKE_ROOT="$ROOT"
pass=0 fail=0
chk() { # name expected-substring|EMPTY docker-mode hook-mode tool cwd input-json
  local out ok
  out=$(jq -nc --arg t "$5" --arg c "$6" --argjson ti "$7" '{tool_name:$t,cwd:$c,tool_input:$ti}' \
    | FAKE_DOCKER="$3" python3 "$H" "$4")
  if [[ "$2" == EMPTY ]]; then [[ -z "$out" ]] && ok=1 || ok=0; else [[ "$out" == *"$2"* ]] && ok=1 || ok=0; fi
  if ((ok)); then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: $out"; fi
}
g() { chk "$1: $3" "$2" "${4:-stopped}" --guard Bash "${5:-$ROOT}" "$(jq -nc --arg c "$3" '{command:$c}')"; }
A='"ask"'; D='"deny"'
# no marker anywhere near
chk "no marker" EMPTY here --guard Bash "$T/DOM/plain" '{"command":"echo hi"}'
chk "session no marker" EMPTY here --session-start Bash "$T/DOM/plain" '{}'
# session start
chk "session here context" '"additionalContext"' here --session-start Bash "$ROOT" '{}'
chk "session here counts" '1/2 containers up' here --session-start Bash "$ROOT" '{}'
chk "session here banner" 'LIVE STACK:' here --session-start Bash "$ROOT" '{}'
chk "session worktree" 'git worktree' here --session-start Bash "$WT" '{}'
chk "session stopped" 'stack down' stopped --session-start Bash "$ROOT" '{}'
chk "session unknown" 'could not query' fail --session-start Bash "$ROOT" '{}'
# compose / docker
g up "$A" 'docker compose -f docker-compose.yml up -d'
g up-file-eq "$A" 'docker compose --file=x.yml up'
g up-fX "$A" 'docker compose -fx.yml up'
g restart "$A" 'cd x && docker compose restart proxy'
g dc-hyphen "$A" 'docker-compose build'
g stop "$A" 'docker stop demo-proxy'
g exec "$A" 'docker compose exec app sh'
g run "$A" 'docker compose run --rm app true'
g container-rm "$A" 'docker container rm -f demo-proxy'
g ctx-flag "$A" 'docker --context default compose up -d'
g host-flag "$A" 'docker -H tcp://h:1 stop x'
g abs-bin "$A" '/usr/bin/docker compose up -d'
g timeout "$A" 'timeout 300 docker compose up -d --build'
g nohup "$A" 'nohup docker compose up -d'
g sudo-u "$A" 'sudo -u root docker compose up -d'
g env-flags "$A" 'env -i A=b docker compose up -d'
g bash-c "$A" 'bash -c "docker compose down"'
g subshell "$A" '(docker compose restart proxy)'
g bg-amp "$A" 'sleep 1 & docker compose up -d'
g if-then "$A" 'if true; then docker compose up -d; fi'
g continuation "$A" $'docker compose \\\n up -d'
# shellcheck disable=SC2016
g cmdsub "$A" 'echo $(docker compose up -d)'
g deploy "$A" './deploy.sh'
g bash-deploy "$A" 'bash deploy.sh'
g run-volume "$A" 'docker run --rm -v demo_data:/d busybox rm -rf /d'
g down-v "$D" 'docker compose down -v'
g down-volumes "$D" 'docker compose down --volumes'
g down-volumes-eq "$D" 'docker compose down --volumes=true'
g down-tv "$D" 'docker compose down -tv'
g down-orphans "$A" 'docker compose down --remove-orphans'
g volume-rm "$D" 'docker volume rm demo_data'
g volume-remove "$D" 'docker volume remove demo_data'
g volume-prune "$D" 'docker volume prune -f'
g sys-prune "$D" 'docker system prune -af'
g ctx-volume "$D" 'docker --context default volume rm demo_data'
g logs EMPTY 'docker compose logs -f'
g ps EMPTY 'docker compose ps'
g dev-build EMPTY 'docker build -t demo-dev/x .'
g quoted-sep EMPTY 'echo "a;b" && git status'
g redirect-2to1 EMPTY 'docker compose ps 2>&1 | tail'
# deny works from any cwd while a live project exists on this machine
g deny-any-cwd "$D" 'docker volume rm demo_data' stopped "$T/other"
# cwd outside the project
g cd-into "$A" "cd $ROOT && docker compose up -d" stopped "$T/other"
g proj-dir "$A" "docker compose --project-directory $ROOT up -d" stopped "$T/other"
g git-C "$A" "git -C $ROOT checkout main" stopped "$T/other"
g other-cwd-noop EMPTY 'docker compose up -d' stopped "$T/other"
# git
for s in checkout switch stash reset restore clean pull merge rebase apply revert rm mv; do g "git-$s" "$A" "git $s x"; done
g git-C-flag "$A" 'git -C . -c a=b checkout main'
g git-abs "$A" '/usr/bin/git checkout main'
g git-status EMPTY 'git status && git diff && git log'
g git-worktree EMPTY 'git checkout main' stopped "$WT"
g git-stopped "$A" 'git checkout main' stopped
# shell writes to live path
g sed-i "$A" 'sed -i s/a/b/ docker/proxy/Caddyfile'
g redirect "$A" 'echo x > docker/proxy/site/a.json'
g rm-live "$A" 'rm -rf docker/proxy/site'
g cat-live EMPTY 'cat docker/proxy/Caddyfile'
g sed-app EMPTY 'sed -i s/a/b/ docker/app/server.py'
g redirect-nospace "$A" 'echo x >docker/proxy/a'
g redirect-append "$A" 'echo x >>docker/proxy/a'
g redirect-fd "$A" 'echo x 2>docker/proxy/a'
g rm-marker "$A" 'rm .claude/deploy-mode'
g mv-marker "$A" 'mv .claude/deploy-mode /tmp/x'
# quoted payloads: separators inside quotes, combined -c flags, eval
g bash-c-and "$A" 'bash -c "docker compose up -d && echo done"'
g bash-c-semi "$A" "bash -c 'cd x; docker compose restart'"
g sh-c-volume "$D" 'sh -c "docker volume rm demo_data && echo ok"'
g sh-c-volume-semi "$D" 'sh -c "docker volume rm x; true"'
g bash-lc "$A" 'bash -lc "docker compose up -d"'
g bash-ec "$A" 'bash -ec "docker compose up -d"'
g sh-xc "$A" "sh -xc 'docker stop demo-proxy'"
g bash-lc-volume "$D" 'bash -lc "docker volume rm x"'
g bash-c-cd "$A" "bash -c 'cd $ROOT && docker compose up -d'" stopped "$T/other"
g eval-q "$A" 'eval "docker compose up -d"'
g eval-bare "$A" 'eval docker compose up -d'
g quoted-amp-safe EMPTY 'echo "a && docker compose up" && git status'
# wrappers and docker flags
g xargs-volume "$D" 'docker volume ls -q | xargs -n 1 docker volume rm'
g xargs-I "$D" 'xargs -I {} docker volume rm {}'
g xargs-stop "$A" 'docker ps -aq | xargs -n 1 docker stop' stopped "$T/other"
g setsid "$A" 'setsid docker compose up -d'
g doas "$A" 'doas docker compose up -d'
g sudo-long "$A" 'sudo --user root docker compose up -d'
g tlscacert "$A" 'docker --tlscacert ca.pem compose up -d'
g tls-volume "$D" 'docker --tls --tlscert c volume rm a'
# host-wide docker commands are guarded from any cwd
g host-stop "$A" 'docker stop demo-proxy' stopped "$T/other"
g host-rm "$A" 'docker rm -f demo-proxy' stopped "$T/other"
g host-exec "$A" 'docker exec demo-proxy sh' stopped "$T/other"
g host-project "$A" 'docker compose -p demo down' stopped "$T/other"
g host-project-env "$A" 'COMPOSE_PROJECT_NAME=demo docker compose up -d' stopped "$T/other"
g host-run-vol "$A" 'docker run -v demo_data:/d busybox rm -rf /d' stopped "$T/other"
g host-run-mount "$A" 'docker run --rm --mount source=demo_data,target=/d busybox true' stopped "$T/other"
g host-volumes-from "$A" 'docker run --volumes-from demo-proxy busybox true' stopped "$T/other"
g host-container-run "$A" 'docker container run -v demo_data:/d busybox true' stopped "$T/other"
g host-run-bind EMPTY 'docker run --rm -v ./x:/d busybox true' stopped "$T/other"
# the host-wide label names the live stacks (worktrees left out), not just the alphabetically first one
mkdir -p "$T/DOM/alpha/.claude"; printf 'mode: live-dev-same-dir\n' > "$T/DOM/alpha/.claude/deploy-mode"
g host-label-outside 'LIVE STACK (docker host; live: alpha, live)' 'docker stop demo-proxy' stopped "$T/other"
g host-label-outside-ask "$A" 'docker stop demo-proxy' stopped "$T/other"
g host-label-inside 'LIVE STACK (live)' 'docker stop demo-proxy' stopped "$ROOT"
for n in b1 b2 b3; do mkdir -p "$T/DOM/$n/.claude"; printf 'mode: live-dev-same-dir\n' > "$T/DOM/$n/.claude/deploy-mode"; done
g host-label-capped 'LIVE STACK (docker host; live: alpha, b1, b2, b3, +1 more)' 'docker stop demo-proxy' stopped "$T/other"
rm -rf "$T/DOM/alpha" "$T/DOM/b1" "$T/DOM/b2" "$T/DOM/b3"
g compose-file-env "$A" "COMPOSE_FILE=$ROOT/docker-compose.yml docker compose up -d" stopped "$T/other"
g git-bisect "$A" 'git bisect start'
g git-sparse "$A" 'git sparse-checkout set x'
# round 3: hyphenated compose with flags, comments/heredocs/apostrophes, shell options, parent dirs
g dc-hyphen-f "$A" 'docker-compose -f docker-compose.yml up -d'
g dc-hyphen-file "$A" 'docker-compose --file x.yml up -d'
g dc-hyphen-down-v "$D" 'docker-compose -f x.yml down -v'
g podman-compose "$A" 'podman-compose -f x.yml up -d'
g dc-hyphen-p-down-v "$D" 'docker-compose -p demo down -v' stopped "$T/other"
g comment-apostrophe "$A" $'# it\'s needed\ndocker compose up -d'
g comment-apostrophe-vol "$D" $'# don\'t panic\ndocker volume rm x'
g heredoc-apostrophe "$A" $'cat <<EOF > /tmp/notes\nit\'s fine\nEOF\ndocker compose up -d'
g heredoc-body-safe EMPTY $'cat > /tmp/msg <<\'X\'\ndocker system prune is dangerous\nX\necho done'
g heredoc-body-compose EMPTY $'cat <<\'X\'\ndocker compose up -d\nX'
g hash-in-word EMPTY 'echo a#b && git status'
g open-quote-naive "$A" $'echo "unclosed\ndocker compose up -d'
g bash-o-pipefail "$A" 'bash -o pipefail -c "docker compose up -d"'
g bash-O-extglob "$A" 'bash -O extglob -c "docker compose up -d"'
g pushd "$A" "pushd $ROOT && docker compose up -d" stopped "$T/other"
# shellcheck disable=SC2016
g cd-var "$A" 'cd $WORKSPACE/DOM/live && docker compose up -d' stopped "$T/other"
g flock "$A" 'flock /tmp/l docker compose up -d'
g time-v "$A" '/usr/bin/time -v docker compose up -d'
g source-deploy "$A" 'source ./deploy.sh'
g rm-parent "$A" 'rm -rf docker'
g rm-claude "$A" 'mv .claude .claude.bak'
g cd-rm-bare "$A" 'cd docker && rm -rf proxy'
g ln-bare "$A" 'cd docker; ln -sf /tmp/x proxy'
g rsync-delete "$A" 'rsync -a --delete ../wt/ ./'
g cp-into-root "$A" 'cp -r /tmp/new/* .'
g curl-o "$A" 'curl -o docker/proxy/x http://h/y'
g tar-live "$A" 'tar -xf a.tar -C docker/proxy/site'
g find-delete "$A" 'find docker/proxy -name x -delete'
g find-readonly EMPTY 'find docker/proxy -name x'
g ls-devnull EMPTY 'ls docker/proxy 2>/dev/null'
g cat-to-tmp EMPTY 'cat docker/proxy/Caddyfile > /tmp/x'
g grep-2to1 EMPTY 'grep -c x docker/proxy/a 2>&1'
g stash-list EMPTY 'git stash list'
g stash-show EMPTY 'git stash show -p'
g stash-push "$A" 'git stash push'
g docker-pause "$A" 'docker pause demo-proxy' stopped "$T/other"
g docker-rename "$A" 'docker rename demo-proxy old' stopped "$T/other"
g net-disconnect "$A" 'docker network disconnect net demo-proxy' stopped "$T/other"
g git-subtree "$A" 'git subtree pull --prefix=x r main'
# round 4: globs, glued redirects, cwd writes, heredoc to shell, parsing edge cases
# shellcheck disable=SC2016
{
g glob-docker "$A" 'rm -rf docker/*'
g glob-star "$A" 'rm -rf *'
g glob-prefix "$A" 'rm -rf docker/prox*'
g glob-app EMPTY 'rm -rf docker/app/*.pyc'
g glued-redirect "$A" 'echo x>docker/proxy/a'
g glued-printf "$A" "printf 'a\\n'>docker/proxy/a"
g glued-heredoc "$A" $'cat <<EOF>docker/proxy/a\nx\nEOF'
g clobber-redirect "$A" 'echo x >| docker/proxy/a'
g redirect-quoted-safe EMPTY 'echo "a>docker/proxy/a"'
g patch-cwd "$A" 'patch -p1 < /tmp/fix.diff'
g tar-extract-cwd "$A" 'tar xf /tmp/backup.tar'
g tar-extract-C EMPTY 'tar -xf a.tar -C /tmp/elsewhere'
g tar-extract-C-live "$A" 'tar -xf a.tar -C docker/proxy'
g unzip-cwd "$A" 'unzip -o /tmp/x.zip'
g wget-cwd "$A" 'wget http://h/f'
g curl-O "$A" 'curl -O http://h/f'
g dd-of "$A" 'dd if=/dev/zero of=docker/proxy/x'
g cp-target-dir "$A" 'cp --target-directory=docker/proxy f'
g heredoc-to-bash "$A" $'bash <<\'EOF\'\ndocker compose up -d\nEOF'
g heredoc-bash-s "$A" $'bash -s <<EOF\ndocker compose up -d\nEOF'
g heredoc-pipe-bash "$A" $'cat <<EOF | bash\ndocker compose up -d\nEOF'
g heredoc-escaped-word EMPTY $'cat <<\\EOF\ndocker compose up -d\nEOF'
g here-string EMPTY $'grep x <<< hello\necho done'
g here-string-hides "$A" $'grep x <<< hello\ndocker compose up -d'
g arithmetic-shift "$A" $'echo $((1<<2))\ndocker compose up -d'
g cd-dashdash "$A" 'cd -- docker && rm -rf proxy'
g tilde-unknown EMPTY 'ls ~nosuchuser_zz/x'
g sed-n-live EMPTY 'sed -n 1,20p docker/proxy/Caddyfile'
g cp-from-live EMPTY 'cp docker/proxy/Caddyfile /tmp/x'
g rsync-from-live EMPTY 'rsync -a docker/proxy/ /tmp/bk/'
g tar-create-from-live EMPTY 'tar -czf /tmp/b.tgz docker/proxy'
g cp-into-live "$A" 'cp /tmp/x docker/proxy/Caddyfile'
g mv-from-live "$A" 'mv docker/proxy/Caddyfile /tmp/x'
# the folder itself renamed/deleted/overwritten from its parent or from anywhere else
g rename-folder "$A" 'mv live live.old' stopped "$T/DOM"
g rm-folder "$A" 'rm -rf live' stopped "$T/DOM"
g cp-over-folder "$A" 'cp -r /tmp/x live' stopped "$T/DOM"
g rename-folder-abs "$A" "mv $ROOT $ROOT.old" stopped "$T/other"
g parent-unrelated-write EMPTY 'rm -f plain/x' stopped "$T/DOM"
g other-cwd-write EMPTY 'rm -rf x' stopped "$T/other"
}
# Edit tools
chk "edit live path" "$A" stopped --guard Edit "$ROOT" "{\"file_path\":\"$ROOT/docker/proxy/site/tools.json\"}"
chk "edit relative" "$A" stopped --guard Edit "$ROOT" '{"file_path":"docker/proxy/Caddyfile"}'
chk "write live" "$A" stopped --guard Write "$ROOT" "{\"file_path\":\"$ROOT/docker/proxy/x\"}"
chk "notebook live" "$A" stopped --guard NotebookEdit "$ROOT" "{\"notebook_path\":\"$ROOT/docker/proxy/n.ipynb\"}"
chk "edit app" EMPTY stopped --guard Edit "$ROOT" "{\"file_path\":\"$ROOT/docker/app/server.py\"}"
chk "edit prefix non-match" EMPTY stopped --guard Edit "$ROOT" "{\"file_path\":\"$ROOT/docker/proxyfoo/x\"}"
chk "edit in worktree" EMPTY stopped --guard Edit "$WT" "{\"file_path\":\"$WT/docker/proxy/x\"}"
chk "edit marker" "$A" stopped --guard Edit "$ROOT" "{\"file_path\":\"$ROOT/.claude/deploy-mode\"}"
# robustness: bad input must not crash silently into allow
chk "bad command type" "$A" stopped --guard Bash "$ROOT" '{"command":["docker","compose","up"]}'
chk "bad tool_input" EMPTY stopped --guard Bash "$ROOT" '"str"'
chk "empty stdin" EMPTY stopped --guard Bash "$ROOT" '{}'
chk "numeric command" "$A" stopped --guard Bash "$ROOT" '{"command":123}'
chk "list stdin" EMPTY stopped --guard Bash "$ROOT" '[1,2]'
chk "NUL in path" "$A" stopped --guard Edit "$ROOT" '{"file_path":"a\u0000b"}'
chk "NUL in bash target" "$A" stopped --guard Bash "$ROOT" '{"command":"rm docker/proxy/a\u0000b"}'
printf 'mode: \xff\xfe\nlive-path: docker/proxy/\n' > "$ROOT/.claude/deploy-mode"
chk "binary marker" "$A" stopped --guard Edit "$ROOT" "{\"file_path\":\"$ROOT/docker/proxy/x\"}"
echo "live-stack: $pass passed, $fail failed"
((fail==0))
