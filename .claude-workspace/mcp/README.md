# Local MCP servers and agents

Each domain has its own MCP config at `<DOMAIN>/.claude/mcp.json`. `claude-init` links it
into every project as `.mcp.json`, unless the project has its own.

## Add a server
1. Add it to `<DOMAIN>/.claude/mcp.json` (local only), or to `mcp.template.json` to commit it:
   ```json
   {
     "mcpServers": {
       "local-tools": { "type": "stdio", "command": "/path/to/server", "args": [], "env": { "TOKEN": "${LOCAL_TOOLS_TOKEN}" } },
       "my-agent":    { "type": "http",  "url": "http://localhost:8765/mcp" }
     }
   }
   ```
   Pass secrets with `${ENV_VAR}` expansion. Never write them inline.
2. Allow it by name in `<DOMAIN>/.claude/settings.domain.template.json` →
   `enabledMcpjsonServers`. Servers that aren't listed stay disabled (`enableAllProjectMcpServers: false`),
   so a cloned repo can't quietly add servers.
3. Run `.claude-workspace/bin/install` to re-render, then restart the session.

## Local agents
- **Subagents** (prompt files): add `<DOMAIN>/.claude/agents/<name>.md` and re-run `claude-init`.
- **Agents running as local services**: expose them over MCP (http/stdio) as above, so
  Claude can call them as tools.

Each domain has a committed `mcp.template.json` (shared servers such as `niu-local`). `install` renders it into the gitignored `mcp.json` and keeps any servers you added there locally, so company endpoints in `mcp.json` are never committed.
