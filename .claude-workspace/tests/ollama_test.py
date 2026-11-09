#!/usr/bin/env python3
"""Tests for niu-agent / niu-local-mcp with a fake Ollama server and a fake `claude` (no network, no real models)."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
SH = WS / ".claude-workspace"
AGENT = SH / "bin" / "niu-agent"
MCP = SH / "bin" / "niu-local-mcp"
tmp = Path(tempfile.mkdtemp())
passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


# ---------- fake Ollama ----------
class Fake:
    def __init__(self, models, key=None):
        self.models, self.chats, self.up, self.key = list(models), [], True, key
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

            def _authed(self):
                if fake.key and self.headers.get("Authorization") != f"Bearer {fake.key}":
                    self._send({"error": "unauthorized"}, 401)
                    return False
                return True

            def do_GET(self):
                if not self._authed():
                    return
                if self.path == "/api/version":
                    return self._send({"version": "0.99.0"})
                if self.path == "/api/tags":
                    return self._send({"models": [{"name": m} for m in fake.models]})
                self._send({"error": "nf"}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if not self._authed():
                    return
                if self.path == "/api/chat":
                    fake.chats.append(body)
                    return self._send({"message": {"role": "assistant", "content": "LITE ANSWER src/a.py:1"}})
                self._send({"error": "nf"}, 404)

        self.srv = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.srv.shutdown(); self.srv.server_close()


remote = Fake(["qwen3-coder:30b", "gpt-oss:20b"])
local = Fake(["qwen2.5-coder:3b"])

# ---------- fake claude ----------
(tmp / "bin").mkdir()
fake_claude = tmp / "bin" / "claude"
fake_claude.write_text(r'''#!/usr/bin/env python3
import json, os, sys
rec = {"argv": sys.argv[1:], "cwd": os.getcwd(), "env": {k: os.environ.get(k) for k in (
    "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN",
    "NIU_AGENT_LABEL", "CLAUDECODE", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")}}
open(os.environ["FAKE_CLAUDE_LOG"], "a").write(json.dumps(rec) + "\n")
if os.environ.get("FAKE_CLAUDE_TEXTCALL"):
    print(json.dumps({"type": "result", "is_error": False, "result": "I'll search.\n<function=Grep>\n<parameter=pattern>x</parameter>\n</function>\n</tool_call>"})); sys.exit(0)
if os.environ.get("FAKE_CLAUDE_FAIL"):
    print(json.dumps({"type": "result", "is_error": True, "result": "model error"})); sys.exit(1)
print(json.dumps({"type": "result", "is_error": False, "result": "AGENT ANSWER src/a.py:1", "num_turns": 3}))
''')
fake_claude.chmod(0o755)
log = tmp / "claude.log"

# ---------- fixture project (inside the workspace so the guard applies) ----------
proj = WS / "PERSONAL" / ".niu-test-ollama" / "app"
(proj / "src").mkdir(parents=True, exist_ok=True)
(proj / ".claude").mkdir(exist_ok=True)
(proj / "src" / "a.py").write_text("def LiveThing():\n    return 1\n")
(proj / "secret.txt").write_text("LiveThing TOPSECRET-VALUE\n")
(proj / ".claude" / "protected-paths").write_text("secret.txt\n")
for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"]):
    subprocess.run(["git", "-C", str(proj), *args], check=True, capture_output=True)
(proj / "dot").write_text("x")  # untracked noise
envfile = proj / (".e" + "nv")
envfile.write_text("API_TOKEN=ENVSECRET\n")

state = tmp / "state"
# Pin the endpoint config to the template: the user's local ollama.json (gitignored) must not change results.
base_env = dict(os.environ, NIU_EVENTS_DB=str(state / "events.db"), NIU_OLLAMA_CONFIG=str(WS / ".claude-workspace" / "ollama.template.json"), NIU_AGENT_CONFIG_DIR=str(tmp / "agentcfg"),
                FAKE_CLAUDE_LOG=str(log), OLLAMA_REMOTE_BASE_URL=remote.url, OLLAMA_LOCAL_BASE_URL=local.url,
                PATH=f"{tmp / 'bin'}:{os.environ['PATH']}", CLAUDE_CODE_OAUTH_TOKEN="PARENT-OAUTH-SECRET",
                ANTHROPIC_API_KEY="PARENT-API-KEY", CLAUDECODE="1")


def agent(*args, extra=None):
    env = dict(base_env, **(extra or {}))
    (state / "ollama-health.json").unlink(missing_ok=True)
    r = subprocess.run([sys.executable, str(AGENT), *args, "--json", "--cwd", str(proj)], env=env, capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout)
    except ValueError:
        print("niu-agent output:", r.stdout, r.stderr)
        return {}


def last_claude():
    lines = log.read_text().splitlines() if log.exists() else []
    return json.loads(lines[-1]) if lines else {}


try:
    # 1. agent mode on the GPU endpoint, isolated from the parent's credentials
    res = agent("search", "where is LiveThing defined?")
    check(res.get("ok") and res.get("endpoint") == "remote" and res.get("mode") == "agent", f"remote agent mode: {res}")
    check(res.get("model") == "gpt-oss:20b" and "AGENT ANSWER" in res.get("answer", ""), "model + answer")
    rec = last_claude(); env = rec.get("env", {}); argv = rec.get("argv", [])
    check(env.get("ANTHROPIC_BASE_URL") == remote.url, "child talks to Ollama")
    check(env.get("ANTHROPIC_AUTH_TOKEN") == "ollama", "plain Ollama endpoint gets the dummy token")
    check(env.get("ANTHROPIC_API_KEY") == "" and env.get("CLAUDE_CODE_OAUTH_TOKEN") is None, f"parent credentials not passed: {env}")
    check(env.get("CLAUDE_CONFIG_DIR") == str(tmp / "agentcfg"), "isolated config dir")
    check(env.get("CLAUDECODE") is None, "nested-session marker stripped")
    check(env.get("NIU_AGENT_LABEL") == "local:gpt-oss:20b", "dashboard label")
    check(rec.get("cwd") == str(proj), "runs in the project")
    i = argv.index("--disallowedTools") if "--disallowedTools" in argv else -1
    allowed = argv[argv.index("--allowedTools") + 1:i] if "--allowedTools" in argv and i > 0 else []
    check(set(allowed) == {"Read", "Grep", "Glob"}, f"only read tools allowed: {allowed}")
    check(i > 0 and all(t in argv[i:] for t in ("Bash", "Write", "Edit", "WebFetch")), "shell/write/web tools denied")
    check("agent-scope.py" in json.dumps(json.loads((tmp / "agentcfg" / "settings.json").read_text())["hooks"]["PreToolUse"][0]), "scope hook first")
    check("--strict-mcp-config" in argv, "no MCP servers in the child (no recursion)")
    cfg = json.loads((tmp / "agentcfg" / "settings.json").read_text())
    check("block-env.sh" in json.dumps(cfg["hooks"]["PreToolUse"]), "guard hook in isolated config")
    check(not list((tmp / "agentcfg").glob("*credentials*")), "no credentials in isolated config")

    # 1b. scope hook: read-only, inside the project only
    def scope(tool, ti, cwd=str(proj)):
        r = subprocess.run([sys.executable, str(SH / "hooks" / "agent-scope.py")],
                           input=json.dumps({"tool_name": tool, "cwd": cwd, "tool_input": ti}),
                           text=True, capture_output=True, env=dict(os.environ, NIU_AGENT_ROOT=str(proj)))
        return r.returncode
    check(scope("Read", {"file_path": str(proj / "src" / "a.py")}) == 0, "scope: read inside project")
    check(scope("Read", {"file_path": "src/a.py"}) == 0, "scope: relative read inside project")
    check(scope("Read", {"file_path": str(Path.home() / ".ssh" / "id_ed25519")}) == 2, "scope: no reads outside project")
    check(scope("Read", {"file_path": "../../../../etc/passwd"}) == 2, "scope: no ../ escape")
    check(scope("Grep", {"pattern": "x", "path": "/etc"}) == 2, "scope: no grep outside project")
    check(scope("Glob", {"pattern": "/home/**/*.pem"}) == 2, "scope: no absolute glob")
    check(scope("Glob", {"pattern": "src/**/*.py"}) == 0, "scope: glob inside project")
    check(scope("Bash", {"command": "git diff --output=/tmp/x"}) == 2, "scope: no shell at all")
    check(scope("Write", {"file_path": "src/x.py"}) == 2, "scope: no writes")

    # 1c. diff prompt carries the guard-filtered diff (the agent has no shell)
    (proj / "src" / "a.py").write_text("def LiveThing():\n    return 3\n")
    (proj / "secret.txt").write_text("LiveThing DIFF-SECRET\n")
    agent("diff", "--base", "HEAD")
    prompt = last_claude().get("argv", ["", ""])[1]
    check("return 3" in prompt and "DIFF-SECRET" not in prompt and "skipped: protected path" in prompt, "agent diff prompt is guard-filtered")
    subprocess.run(["git", "-C", str(proj), "checkout", "-q", "--", "."], check=True)

    # 2. refuses to run if credentials ever appear in the isolated config -> falls back to lite
    (tmp / "agentcfg" / ".credentials.json").write_text("{}")
    res = agent("search", "LiveThing")
    check(res.get("mode") == "lite" and any("refusing" in n for n in res.get("notes", [])), f"credentials guard: {res.get('notes')}")
    (tmp / "agentcfg" / ".credentials.json").unlink()

    # 3. agent failure -> lite on the same endpoint
    res = agent("search", "LiveThing", extra={"FAKE_CLAUDE_FAIL": "1"})
    check(res.get("endpoint") == "remote" and res.get("mode") == "lite", f"agent failure falls back to lite: {res}")

    # 3b. a tool call printed as text (model can't drive Claude Code's tools) -> lite instead of a junk answer
    res = agent("search", "LiveThing", extra={"FAKE_CLAUDE_TEXTCALL": "1"})
    check(res.get("mode") == "lite" and any("tool call as text" in n for n in res.get("notes", [])), f"text tool call detected: {res.get('notes')}")

    # 4. lite context honours the guard: no protected file, no dotenv
    sent = json.dumps(remote.chats[-1]) if remote.chats else ""
    check("src/a.py" in sent, "lite search context includes tracked hit")
    check("TOPSECRET" not in sent and "secret.txt" not in sent, "protected-paths excluded from lite context")
    check("ENVSECRET" not in sent, "dotenv excluded from lite context")

    # 5. model missing on GPU -> local CPU
    remote.models = ["llama3.1:8b", "qwen3-coder:30b"]
    res = agent("search", "LiveThing")
    check(res.get("endpoint") == "local" and res.get("mode") == "lite", f"missing model -> local: {res}")
    check(any("lacks gpt-oss:20b" in n for n in res.get("notes", [])), "says why it fell back")
    remote.models = ["qwen3-coder:30b", "gpt-oss:20b"]

    # 6. diff in lite mode skips protected files
    (proj / "src" / "a.py").write_text("def LiveThing():\n    return 2\n")
    (proj / "secret.txt").write_text("LiveThing CHANGED-SECRET\n")
    res = agent("diff", "--base", "HEAD", "--endpoint", "local")
    sent = json.dumps(local.chats[-1]) if local.chats else ""
    check(res.get("ok") and res.get("mode") == "lite", "local diff review")
    check("return 2" in sent and "CHANGED-SECRET" not in sent and "skipped: protected path" in sent, "diff context honours guard")

    # 7. GPU box down -> local; both down -> clear failure
    remote.stop()
    res = agent("search", "LiveThing")
    check(res.get("endpoint") == "local" and any("remote unreachable" in n for n in res.get("notes", [])), f"remote down -> local: {res.get('notes')}")
    local.stop()
    res = agent("search", "LiveThing")
    check(res.get("ok") is False and len(res.get("notes", [])) >= 2, "both down -> not ok, with reasons")

    # 8. MCP server round trip (endpoints down: tools still answer, with errors)
    p = subprocess.Popen([sys.executable, str(MCP)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                         env=dict(base_env, NIU_PROJECT_DIR=str(proj)))

    def rpc(i, method, params=None):
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}) + "\n"); p.stdin.flush()
        return json.loads(p.stdout.readline())

    init = rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})
    check(init["result"]["serverInfo"]["name"] == "niu-local", "MCP initialize")
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"); p.stdin.flush()
    tools = {t["name"] for t in rpc(2, "tools/list")["result"]["tools"]}
    check(tools == {"local_search", "local_diff_review", "local_ask", "local_status", "local_logs", "local_ci", "local_git"}, f"MCP tools: {tools}")
    st = rpc(3, "tools/call", {"name": "local_status", "arguments": {}})
    check("remote" in st["result"]["content"][0]["text"], "local_status tool")
    sr = rpc(4, "tools/call", {"name": "local_search", "arguments": {"query": "LiveThing"}})
    check(sr["result"]["isError"] is True and "unreachable" in sr["result"]["content"][0]["text"], "search reports endpoints down")
    esc_ = rpc(5, "tools/call", {"name": "local_search", "arguments": {"query": "x", "path": "../../.."}})
    check(esc_["result"]["isError"] is True and "inside the project" in esc_["result"]["content"][0]["text"], "path cannot escape project")
    unk = rpc(6, "tools/call", {"name": "rm_rf", "arguments": {}})
    check("error" in unk, "unknown tool rejected")
    miss = rpc(7, "tools/call", {"name": "local_search", "arguments": {}})
    check(miss["result"]["isError"] is True and "missing argument" in miss["result"]["content"][0]["text"], "missing argument reported")
    bad = rpc(8, "tools/call", {"name": "local_logs", "arguments": {"service": "-rf"}})
    check(bad["result"]["isError"] is True and "service must match" in bad["result"]["content"][0]["text"], "local_logs rejects a flag-like service")
    bl = rpc(9, "tools/call", {"name": "local_git", "arguments": {"kind": "blame"}})
    check(bl["result"]["isError"] is True and "blame needs a file path" in bl["result"]["content"][0]["text"], "local_git blame needs a path")
    p.stdin.close(); p.wait(5)

    # ---------- api_key_file: endpoints behind an authenticating gateway ----------
    gw = Fake(["qwen2.5-coder:3b"], key="gw-test-key-123")
    keyf = tmp / "gw.key"
    keyf.write_text("gw-test-key-123\n"); os.chmod(keyf, 0o600)
    gwcfg = tmp / "gw-ollama.json"

    def gw_probe(key_file, cfg_path=gwcfg):
        gwcfg.write_text(json.dumps({"endpoints": {"gw": {"url": gw.url, "api_key_file": str(key_file), "mode": "lite",
                                                           "models": {"ask": "qwen2.5-coder:3b"}}}}))
        (state / "ollama-health.json").unlink(missing_ok=True)
        code = ("import json,sys; sys.path.insert(0, sys.argv[1]); import niu_ollama as no; "
                "ep = no.load_config()['endpoints']['gw']; h = no.health(ep, force=True); "
                "ans = no.lite_run(ep, 'qwen2.5-coder:3b', 'ask', 'q', 'ctx', 10) if h['up'] else None; "
                "print(json.dumps({'up': h['up'], 'error': h['error'], 'answer': ans, 'has_key': 'api_key_file' in ep}))")
        env = dict(base_env, NIU_OLLAMA_CONFIG=str(cfg_path))
        r = subprocess.run([sys.executable, "-c", code, str(SH / "lib")], env=env, capture_output=True, text=True, timeout=30)
        try:
            return json.loads(r.stdout)
        except ValueError:
            print("gw probe output:", r.stdout, r.stderr)
            return {}

    ok = gw_probe(keyf)
    check(ok.get("up") is True and "LITE ANSWER" in (ok.get("answer") or ""), f"key file: health and /api/chat send the bearer key ({ok})")
    os.chmod(keyf, 0o644)
    loose = gw_probe(keyf)
    check(loose.get("up") is False and loose.get("error") == "api key file: perms", f"key file readable by others is refused ({loose})")
    os.chmod(keyf, 0o600)
    link = tmp / "gw-link.key"; link.symlink_to(keyf)
    sym = gw_probe(link)
    check(sym.get("up") is False and sym.get("error") == "api key file: perms", f"symlinked key file is refused ({sym})")
    miss_k = gw_probe(tmp / "nope.key")
    check(miss_k.get("up") is False and miss_k.get("error") == "api key file: missing", f"missing key file reported ({miss_k})")
    gwcfg.write_text(json.dumps({"endpoints": {"gw": {"url": gw.url, "api_key_file": str(keyf), "mode": "agent",
                                                       "models": {"search": "qwen2.5-coder:3b"}}}}))
    ga = agent("search", "where is LiveThing defined?", extra={"NIU_OLLAMA_CONFIG": str(gwcfg)})
    genv = last_claude().get("env", {})
    check(ga.get("ok") and ga.get("endpoint") == "gw" and ga.get("mode") == "agent", f"agent mode through the gateway: {ga}")
    check(genv.get("ANTHROPIC_BASE_URL") == gw.url and genv.get("ANTHROPIC_AUTH_TOKEN") == "gw-test-key-123",
          "agent mode sends the gateway key as ANTHROPIC_AUTH_TOKEN")
    fake_shared = tmp / "fake-shared"; fake_shared.mkdir()
    (fake_shared / "ollama.template.json").write_text(json.dumps({"endpoints": {"gw": {"url": gw.url, "api_key_file": str(keyf)}}}))
    code = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import niu_ollama as no; "
            "no.SHARED = Path(sys.argv[2]); print('api_key_file' in no.load_config()['endpoints']['gw'])")
    env = {k: v for k, v in base_env.items() if k != "NIU_OLLAMA_CONFIG"}
    r = subprocess.run([sys.executable, "-c", code, str(SH / "lib"), str(fake_shared)], env=env, capture_output=True, text=True, timeout=30)
    check(r.stdout.strip() == "False", f"tracked template cannot set api_key_file ({r.stdout.strip()} {r.stderr[-200:]})")
    gw.stop()
finally:
    subprocess.run(["rm", "-rf", str(tmp), str(WS / "PERSONAL" / ".niu-test-ollama")])

print(f"ollama: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
