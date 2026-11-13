# Session details: an agent architecture map (all available agents) above the recent activity
Status: proposed · Date: 2026-11-12 · Request: "Is it possible to show not only agents that already run but also common/default/available agents ... Can you show the view when I click the session in the SESSIONS section ... at the bottom recent activity; when an activity is selected it highlights the agent involved. On live, it uses the animation of dataflow that is currently also being used in AGENT INTERACTION."

Scope: niu-heimar (`/home/norman/WORKSPACE`, config under `.claude-workspace/`), one PR. Line numbers are from `main` on 2026-11-12 (`1426e0e`). The "Agent interactions" panel (`web/index.html:80-88`, `web/app.js:265-646`) keeps its look and behaviour.

## Decision summary (read this first)
| # | Topic | Decision |
|---|---|---|
| 1 | Where it shows | Clicking a session card (`app.js:207`) opens the existing `#details` side panel. The panel gets a `wide` class and shows **the architecture map on top and the session's recent activity below**. A click on a node in Agent interactions (`app.js:435`, `{sid, aid}`) opens the same view with that agent preselected. |
| 2 | Which agents | **All agents the session could use**: main session, user, the `/develop` and `/dual-review` skills, every agent definition (project `.claude/agents`, domain `<DOMAIN>/.claude/agents`, shared `.claude-workspace/agents`, user `~/.claude/agents`), the built-ins `Explore`, `Plan` and `general-purpose`, and the niu-local Ollama tools. Agents that ran are drawn solid; the running ones also pulse. Agents that didn't run are dimmed and labelled "available". |
| 3 | Catalog source | **Server side.** A new `niu_events.agent_catalog()` reads the frontmatter (`name`, `model`, optional `effort`, `description`) with an mtime cache, through a new read-only `GET /api/agents?sid=`. The browser fetches it once each time the panel opens and caches it for 60 s. It is not added to `/api/state`, because the SSE stream re-sends that on every change. |
| 4 | Layout | Fixed layers from top to bottom. **L0**: user and main session. **L1, dispatch**: `/develop`, `/dual-review`, Agent tool, niu-local MCP. **L2, workers**: one cluster under each dispatcher; the `/develop` cluster is designer → *approval* gate → implementer. **L3**: "Main session: integrate and report". Boxes are dashed with a source badge and a `model · effort` label. Static flow edges go between the layers. |
| 5 | Activity → highlight | The activity list is built **in the browser from the session's structured data**: session and subagent tool ticks, subagent start and stop, local agent runs, waits and hand-offs. Feed summaries are attached where they match. Each row carries `nodes[]` and `edges[]`, using the mapping table in Design C. Selecting a row (click, or Enter / arrow keys) highlights those nodes and edges and dims the rest. Clicking a map node highlights its rows. |
| 6 | Live animation | Reuse `packet()` and `burst()` (`app.js:493-517`). They get one optional `layer` argument (plus `w`/`h` for `burst`), and the defaults keep today's calls identical. The map keeps its own `AM` state (`nodes`, `edges`, `seen`, `first`) and diffs it on each push, the same way `renderGraph` does (`:596-645`). Spawn packets go main → dispatcher → agent, tool calls burst on the agent, results go agent → L3. Reduced motion is handled as today: `reducedMotion()`, `.calm`, `prefers-reduced-motion`. |
| 7 | Security | `/api/agents` is GET-only and unpaired, like `/api/state`. `sid` is checked with `RE_SID`. The response has no absolute paths. Descriptions are cut to one sentence of at most 160 characters, stripped of control characters, and passed through `redact()`. Files are read only from the agent and skill directories, symlinks must resolve inside the workspace or `~/.claude`, and reads are capped at 4 KB. The UI builds everything with `svg()` / `h()` / `textContent`, and the smoke test's innerHTML trap covers the new section. |

**Where the code differs from what the request assumes (adjusted here, please confirm):**
1. **"Recent activity" today is often just tool names.** `showDetails` (`app.js:870-885`) filters `STATE.feed` by session, but the feed holds only the newest 60 events across all sessions (`niu-dash:234-238`, `niu_state.py:411-417`). For a quiet session it falls back to the last 15 tool ticks, which carry no agent attribution. The new list is built from `s.tools`, `s.subagents[].tools`, the local runs and `s.chain.handoffs`, so every row knows its agent (up to `MAX_TICKS = 400` per actor, `niu_state.py:10`).
2. **The skill that dispatched an agent is not recorded.** The events have `Skill` ticks (`niu_state.py:169` looks for `develop`) but no link between a spawn and a skill. Dispatchers are therefore inferred by **cluster membership**: designer and implementer belong to `/develop`, the reviewers to `/dual-review`, everything else to the Agent tool. A dispatcher counts as "used" when a child ran, or when its `Skill` tick or `s.pipeline` exists. An implementer started directly is still drawn under `/develop`. The tooltip says "usually started by /develop".
3. **Built-in agents inherit the session model.** Per the current Claude Code docs, Explore, Plan and general-purpose all default to `inherit` (Explore stopped forcing Haiku). The label shows `inherits · <session model>`.
4. **There are no domain agent directories today.** `<DOMAIN>/.claude/agents/` is empty or missing for WORK, PERSONAL and HOBBY. `claude-init` links shared and domain agents into each project's `.claude/agents` (`bin/claude-init:35-37`). The catalog reads the project directory first, which already covers the links, and the domain and shared directories as fallbacks, for sessions whose project isn't linked (for example `(workspace)` sessions).
5. **Effort is not known for the main session**, and no agent sets `effort:` today (`agents/*.md:2-5`). The label shows effort only when the frontmatter has it.

## Context
- **Sessions list.** `renderSessions` (`app.js:178-209`) builds the cards as escaped HTML. Each card's `onclick` calls `showDetails({sid})`. `render()` calls `showDetails(SELECTED, true)` on every SSE push (`:156`), and `Esc` closes the panel (`:138`). The panel is `#details` (`index.html:193`), fixed on the right at 420 px wide (`style.css:420`).
- **Session data** (`niu_state.to_json`, `niu_state.py:374-419`):
  - per subagent: `subagents[] {id, type, source, start, stop, task, parent, current, tools[[ts,tool]], model, model_src}`
  - per session: `tools`, `waits`, `messages`, `ran`, `pending` (project agents not yet run, `:397-399`), `chain {steps, groups, handoffs, review_rounds}` (`:255-301`), `pipeline` (the /develop stages, `:304-358`), `model`, `local`, `parent_session`, `task`.
  - Local Ollama runs are separate sessions with `parent_session` (`app.js:290-298`).
- **Definitions.** `niu_events.agent_source()` / `agent_model()` (`lib/niu_events.py:120-165`) already resolve the source and the `model:` frontmatter with an mtime cache. `project_agents()` (`:168-172`) lists the project's agents. The niu-local tool list lives in `bin/niu-local-mcp:20-67`.
- **Graph engine.** `svg()` (`app.js:58`), `graphNode` / `graphEdge` (`:424-491`) bound to the global `G` and `#graph`, `packet()` / `burst()` (`:493-517`) drawing into `G.layers`, the CSS classes `.gflow.active`, `.pk-*`, `.rp-*` and the markers `#ar*` (`:275-279`), and the themed tooltip via `data-tip` on an allowlist of boxes (`:239`).
- **Tests.**
  - `tests/web_test.py` seeds real hook events and runs `tests/web_smoke.js` under node against a fake DOM (`web_smoke.js:1-48`), with the innerHTML trap at `:22-23`.
  - `tests/live_test.py` covers `niu_state` with `check()` style asserts.

## Requirements
- Must: show every available agent for the session's project and domain, active or available, with model and effort from the definition.
- Must: draw the flows: /develop (designer → user approval → implementer → main), /dual-review (both reviewers in parallel → main), the Agent tool (built-ins and other agents → main), and niu-local (tool → main).
- Must: keep a recent activity list under the map. Selecting a row highlights its agent(s) and edge(s), with mouse and keyboard.
- Must: animate live sessions with the existing packet and burst look. Respect Reduce motion and `prefers-reduced-motion`. Follow the theme variables, the light and dark palettes and the `data-tip` tooltips.
- Must not: change how Agent interactions looks or behaves. Add a POST route or a token. Return absolute paths, file contents beyond the trimmed frontmatter, or anything from `.env*` / protected paths. Use `innerHTML` with any server or definition string in the new code.

## Options considered
1. **Hard-code the agent list in `app.js`.** Pros: no server change. Cons: it drifts from `agents/*.md`, can't see project or domain agents, and the model labels go stale. **Rejected.**
2. **Add the catalog to every session in `/api/state`.** Pros: no new route. Cons: about 2-4 KB per session, re-sent on every SSE push (`niu-dash:300+`), and memoized per build for data that changes about once a week. **Rejected.**
3. **`GET /api/agents?sid=`, fetched when the panel opens, with an mtime-cached frontmatter parser.** Pros: one source of truth, cheap, testable in Python. Cons: one more route, and a short "loading" state. **Chosen.**

For the renderer:
- **(a) Generalize the whole `G` engine into a factory.** It touches every line of the Agent interactions code, which is the risk the request wants avoided.
- **(b) Copy the engine.** That duplicates the packet and SMIL code.
- **(c) A small `AM` renderer of its own** (vertical layout, dashed boxes) that **reuses `svg`, `packet`, `burst`, `TOOL_CLASS`, the markers and the CSS**, with one default-valued parameter added to `packet` and `burst`.

**Chosen: (c)**, the smallest change that shares the animation without touching the existing graph's behaviour.

## Design

### A. Catalog (server, `lib/niu_events.py`, `bin/niu-dash`)
```python
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")
EFFORTS = frozenset("low medium high xhigh max".split())
BUILTIN = [("general-purpose", "All tools; complex multi-step work and code changes."),
           ("Explore", "Read-only search of the codebase."), ("Plan", "Read-only research for plan mode.")]
LOCAL_TOOLS = [("local_search", "search"), ("local_diff_review", "diff review"), ("local_ask", "Q&A"),
               ("local_logs", "logs"), ("local_ci", "CI"), ("local_git", "git"), ("local_status", "status")]
FLOW_SKILLS = ("develop", "dual-review")

def _frontmatter(f) -> dict | None      # 4 KB head, mtime cache (replaces the body of agent_model's cache)
def agent_catalog(project_dir, domain, roots=None) -> dict
```
- **Search order** (the first file with a given `name` wins, the same as Claude Code's precedence): project `.claude/agents/*.md` → `WS/<domain>/.claude/agents/*.md` → `SHARED/agents/*.md` → `~/.claude/agents/*.md` → `BUILTIN`. `source` comes from the existing `agent_source()` (project / domain / shared / user / builtin). `roots` overrides the four directories in tests.
- **File rules.** Only regular `*.md` files whose stem matches `NAME_RE`, at most 64 per directory. Symlinks must resolve under `WS` or `~/.claude`; anything else is skipped. Names starting with `.env` are skipped.
- **Per agent:** `{name, source, model (agent_model's regex) | "inherit" | None, effort (∈ EFFORTS) | None, description}`. `description` is the text up to the first `. ` / newline, at most 160 characters, with `\p{C}` removed, through `redact()`.
- **Skills:** `SHARED/skills/*/SKILL.md`, domain and project skills, `{name, source, description}`. Only `FLOW_SKILLS` get map nodes; the others go into a footer line.
- **Local tools:** `LOCAL_TOOLS` plus `available: domain != "WORK"` for `local_ci` (its description says it isn't available in WORK). A test keeps the list in sync with `bin/niu-local-mcp`.
- **Response:** `{"sid", "agents": [...], "skills": [...], "local_tools": [...], "generated_at"}`. Each list is capped and the response stays under 16 KB.
- **Route** in `do_GET` (`niu-dash:262-298`): `elif path == "/api/agents":`
  1. `sid` must match `RE_SID`, else 400.
  2. Look the session up in `live(self._args()[0]).sessions` (after `refresh()`), else 404.
  3. Answer `niu_events.agent_catalog(s["project_dir"], s["domain"])`.
  
  No pairing (the same exposure as `/api/state`).

### B. Map model and layout (`web/app.js`, new section `// ---------- session agent map ----------` placed before `showDetails`)
- **Node ids:** `user`, `main`, `out` (L3), `d:develop`, `d:dual-review`, `d:document`, `d:agent`, `d:local`, `gate:approve`, `a:<name>`, `l:<tool>`.
- **Clusters:**
  - `d:develop` → `[a:designer, gate:approve, a:implementer]`, in one row joined left to right.
  - `d:dual-review` → `[a:security-reviewer, a:test-reviewer]`.
  - `d:document` → `[a:doc-writer]` (Haiku). Added 2026-11-13 with the `/document` skill.
  - `d:agent` → the built-ins and every other catalog agent, plus any agent type that ran but isn't in the catalog (source from `subagents[].source`).
  - `d:local` → `LOCAL_TOOLS`.
  
  A cluster with no catalog entry, for example a project without the designer, is still drawn when the session used it. Otherwise it is left out.
- **Static edges** `{id, a, b, kind}`:

  | Flow | Edges |
  |---|---|
  | Main | `user→main` (prompt) |
  | /develop | `main→d:develop`, `d:develop→a:designer`, `a:designer→gate:approve`, `gate:approve→a:implementer`, `a:implementer→out` |
  | /dual-review | `main→d:dual-review`, `d:dual-review→a:<each reviewer>`, `a:<each reviewer>→out` |
  | /document | `main→d:document`, `d:document→a:doc-writer`, `a:doc-writer→out` |
  | Agent tool | `main→d:agent`, `d:agent→a:<x>`, `a:<x>→out` |
  | niu-local | `main→d:local`, `d:local→l:<t>`, `l:<t>→out` |
  | Approval | `gate:approve↔user`, a dotted side edge for the approval |
- **State per node:**
  - `running`: a live subagent of that type, a live local run, or a session in the `working` state for `main`. `running` also implies used.
  - `used`: it ran in this session, or the cluster was used (contradiction 2). The gate is used when the `pipeline` approve stage is `done` or `active`.
  - `avail`: otherwise. Drawn with `opacity: .45` and a dashed border, with "available" in the sub-line.
  - Edges are `active` / `used` / `avail` the same way, from their endpoints.
- **Layout:** node size `AW = 150`, `AH = 46`. Clusters sit side by side under their dispatcher, and each cluster's children stack in rows of at most 3 (`/develop` stays one row). The `viewBox` is computed, and the SVG gets `width: 100%`, so it scales to the panel. There is no pan or zoom; it fits by design, at about 760 × 420 for the shared agents. Edges are vertical cubic béziers from bottom-centre to top-centre, or horizontal inside the `/develop` row, built by `amPath(a, b)`.
- **Node drawing** (`amNode`, in place by id like `graphNode`):
  - a dashed `rect.ambox` and a badge `src-<source>`, reusing the `.gnode .badge.src-*` colours
  - the name
  - `model · effort`, where the model goes through `shortModel` (`inherits` for inherit / None); `.guess` styling when it comes from the definition rather than the transcript
  - a sub-line with `running 2m` / `ran 3× · last 14:02` / `available`
  - `data-tip` = `name\ndescription\nsource · model\nusually started by /develop` (for the cluster members)
  
  Classes: `amnode running|used|avail|hl|fade`.
- **Panel layout:** `#details.wide` is `width: min(780px, calc(100vw - 24px))`. Inside it: the header (title, id, domain), `#amap` (the map, about 55 % of the height), a one-line legend (spawn, hand-off, local, result) reusing the `.legend` styles, then `#amfeed` (the activity list, scrolling).

### C. Activity rows and the highlight mapping
`amActivity(s, lk)` returns the newest 40 rows, sorted by `ts` descending: `{key, ts, text, nodes: [], edges: []}`. `key` is stable (`t:<actor>:<ts>:<i>`) so a selection survives re-renders.

| Source | Row text | `nodes` | `edges` |
|---|---|---|---|
| `s.tools` tick, tool `Agent`/`Task` (the type from a matching feed `summary` "type: desc", or the next subagent started within 5 s) | "spawn designer" | `main`, `d:<cluster>`, `a:<type>` | `main→d:<c>`, `d:<c>→a:<type>` |
| `s.tools` tick, tool `Skill` (summary contains `develop` / `dual-review`) | "/develop" | `main`, `d:develop` | `main→d:develop` |
| `s.tools` tick, `mcp__niu-local__<t>` | "local_search" | `main`, `d:local`, `l:<t>` | `main→d:local`, `d:local→l:<t>` |
| `s.tools` tick, `AskUserQuestion` after the designer finished | "asked for approval" | `main`, `gate:approve`, `user` | `a:designer→gate:approve`, `gate:approve↔user` |
| `s.tools` tick, anything else | "Edit app.js" (tool + feed summary when the ts and tool match) | `main` | none |
| `subagents[].tools` tick | "test-reviewer · Bash" | `a:<type>` | `d:<c>→a:<type>` |
| subagent start / stop | "designer started" / "designer done in 4m" | `a:<type>` (stop: also `out`) | spawn edge / `a:<type>→out` |
| local run (`localKids`) start / end | "search agent: “query”" | `l:<tool from parent_tool or TASK_NAME key>` | `d:local→l:<t>` / `l:<t>→out` |
| `s.waits` | "waiting for you" | `user`, `main` | `user→main` |
| `chain.handoffs` | "hand-off: design → implement" | `from`, `to` (mapped to `a:` / `main`) | the matching static edge |

- **Selecting.** `AM.sel = key`. Nodes in `row.nodes` get `hl`, all others get `fade`; edges in `row.edges` get `hl` and replay one `packet` along each (unless reduced motion). Selecting the row again, or `Backspace`, clears it.
- **Clicking a map node** sets `AM.focus = nodeId`. The rows that touch it get `on`, the others are muted, and the first one scrolls into view. Clicking it again clears it.
- **Keyboard.** The list is a `role="listbox"` with roving `tabindex` (the same pattern as `tpFocusRow`, `app.js:2755`). Up and Down move and select, Home and End jump. `Esc` keeps its global meaning and closes the panel.
- **Agent-interactions click.** `showDetails({sid, aid})` preselects `AM.focus = a:<type of aid>`.

### D. Live updates and animation
- **First open.** `showDetails(sel)` builds the panel once with `h()` / `svg()` (no innerHTML), fetches `/api/agents?sid=` (60 s cache in `AM.cat`), and draws the used nodes right away. Available nodes appear when the catalog arrives. If the fetch fails, the map shows only the used agents and the line "Available agents could not be loaded."
- **Refresh** (`showDetails(sel, true)` on each push): the panel is updated in place (`amNode` / `amEdge` by id, the list rebuilt with the selection kept). The panel is not rebuilt, so SMIL packets survive.
- **Animation**, only when the session is not `gone(s)`, and never on first paint or on the first render after switching sessions (`AM.first`). The seen-counters work like `renderGraph`:
  - a new subagent sends `packet(main→d:c, "spawn")`, then 450 ms later `packet(d:c→a:x, "spawn")`;
  - new subagent tool ticks get `burst(a:x, TOOL_CLASS(tool), tool, {layer: AM.fx, w: AW, h: AH})`, at most the last 3;
  - a subagent stop sends `packet(a:x→out, "result")`;
  - a new local run sends `packet(d:local→l:t, "local")`, and its end sends `packet(l:t→out, "result")`;
  - a new hand-off sends `packet(<edge>, "handoff")`;
  - new session tool ticks get a burst on `main`;
  - while a node is `running`, its incoming edge gets `.gflow.active`.
- **Shared helpers.** `packet(pathId, cls, {reverse, delay, dur, layer = G.layers.packets})` and `burst(n, cls, text, {layer = G.layers.fx, w = NW, h = NH} = {})`. These are the only edits to the existing graph code, and the defaults are identical. Path ids use a separate prefix (`am<n>`) so they can't collide with `ge<n>`.
- **Markers.** The map has its own `<defs>` with ids `am-ar*`, because the existing `#ar*` markers live inside `#graph`'s SVG, which is removed when the graph is empty (`app.js:575-576`).
- **Tooltips.** Add `"amap"` and `"amfeed"` to the tooltip allowlist (`app.js:239`). `#details` exists from page load, but the two inner boxes are created once at load as empty children of `#details`, so the listeners can attach.

### E. Failure modes
| Situation | Behaviour |
|---|---|
| `/api/agents` 404 (the session left the window) or a network error | Used agents only, plus a muted note. Retried on the next open. |
| Agent type ran but has no definition (a plugin, a deleted file) | Drawn under the Agent tool with `source` from the subagent; tooltip "definition not found". |
| A frontmatter file is malformed or huge | Skipped, or drawn with `model: None` and description "–". Never an error response. |
| A local session ended or stale with "Show ended" off | Its node stays `used`. No packet. |
| A session with more than about 20 catalog agents | The Agent-tool cluster wraps into more rows. The map grows taller and `#amap` scrolls. |

## Changes
| File | Change |
|---|---|
| `.claude-workspace/lib/niu_events.py` | `NAME_RE`, `EFFORTS`, `BUILTIN`, `LOCAL_TOOLS`, `FLOW_SKILLS`; `_frontmatter()` with the mtime cache, used by `agent_model()` (same results); `agent_catalog(project_dir, domain, roots=None)`. |
| `.claude-workspace/bin/niu-dash` | `GET /api/agents?sid=` (RE_SID, live-state lookup, 400 / 404); a docstring line. |
| `.claude-workspace/web/app.js` | `packet()` / `burst()` get the optional `layer` / `w` / `h` (defaults unchanged). New section `// ---------- session agent map ----------`: `AM` state, `amCatalog(sid)`, `amModel(s, cat)` (nodes, edges, states), `amLayout`, `amPath`, `amNode`, `amEdge`, `amActivity(s, lk)`, `amSelect(key)`, `amFocus(id)`, `amAnimate(s)`, `amKey(e)`. `showDetails` is rewritten to build the panel with `h()` / `svg()` (no innerHTML) and call the map code; it keeps the `SELECTED` semantics and the close button. Tooltip allowlist += `amap`, `amfeed`. |
| `.claude-workspace/web/index.html` | No structural change (`#details` stays). Optionally two empty children `<div id="amap">` / `<div id="amfeed">` inside `#details`, so the tooltip listeners attach at load. |
| `.claude-workspace/web/style.css` | `.mc .details.wide`; `.amap svg { width: 100% }`; `.amnode .ambox` (dashed, `var(--border)`), `.amnode.used`, `.amnode.running .ambox { stroke: var(--ok) }` + halo, `.amnode.avail { opacity: .45 }`, `.amnode.hl .ambox { stroke: var(--accent); stroke-width: 2.2 }`, `.amnode.fade { opacity: .25 }`, `.amedge.avail`, `.amedge.hl`, `.amgate` (diamond), `.amfeed` rows (`.on`, `[aria-selected=true]`). `.calm` and `@media (prefers-reduced-motion)` rules for the halo and flow, next to `style.css:155-159`. |
| `.claude-workspace/tests/catalog_test.py` (new), `.claude-workspace/tests/catalog.test.sh` (new) | Unit tests for `agent_catalog`; see the test plan. |
| `.claude-workspace/tests/web_test.py` | `/api/agents` route checks; writes `agents.json` (the real catalog for `sess-web1`) for the smoke run and passes it as an extra argument. |
| `.claude-workspace/tests/web_smoke.js` | An `/api/agents` fetch stub; `amChecks()`; an innerHTML trap over the new section; "agent map ok" in the output. |

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| The Agent interactions panel changes by accident | The only edits there are default-valued parameters. The smoke test snapshots `[...G.nodes.keys()]` and the edge ids before and after opening the map and asserts they are equal. The existing graph checks stay. |
| Re-rendering on every SSE push costs CPU or kills animations | In-place updates by id; the activity list rebuilds at most 40 rows. The catalog is fetched only on open. |
| Agent descriptions leak something to unpaired viewers | First sentence only, at most 160 characters, `redact()`; the same files are already in git or symlinked. Open question 1 offers pairing-only. |
| The inferred dispatcher is wrong (an implementer started directly) | Documented in the tooltip ("usually started by /develop"). The activity row still highlights the right agent. |
| The local tool list drifts from `niu-local-mcp` | A test compares `LOCAL_TOOLS` with the `"name": "local_…"` entries in `bin/niu-local-mcp`. |
| A symlink in an agents directory points outside the workspace | It must resolve under `WS` or `~/.claude`, or it is skipped (tested). |
| A wide panel covers the graph | It is already an overlay; `Esc` and Close work as today. On narrow screens the width is clamped to `100vw - 24px`. |

## Test plan
- **Unit (Python), `tests/catalog_test.py`** (run by `bash .claude-workspace/tests/catalog.test.sh`). It uses temp roots and `check()` style like `live_test.py`, and covers:
  - **Precedence:** a project override of `designer` wins over shared, `source` is right, and `general-purpose`, `Explore` and `Plan` are present as `builtin` with `inherit`.
  - **Frontmatter:** model and effort are parsed; a bad effort becomes None; no frontmatter gives `model None`; a description over 160 characters is cut at the first sentence; `"<script>"`, control characters and `token=abc123…` come out redacted or stripped.
  - **Hostile files:** a 1 MB file reads only 4 KB; a name `../x.md` / `.env.md` is skipped; a symlink to `/etc/passwd` is skipped; more than 64 files are capped.
  - **Cache:** the mtime cache hits, and a re-read happens after touching the file.
  - **Local tools:** `LOCAL_TOOLS` names equal the names parsed from `bin/niu-local-mcp`, and `local_ci` is `available: False` for WORK.
  - **Compatibility:** `agent_model()` still returns the same values for the shared agents.
- **Integration (`tests/web_test.py`):**
  - `GET /api/agents?sid=sess-web1` → 200, and it includes `designer`, `implementer`, `security-reviewer`, `test-reviewer`, `Explore`, `Plan`, `general-purpose` and the `local_search` tool. `sid=../x` → 400, an unknown sid → 404.
  - The response body contains neither `str(WS)` nor `/home/`, and stays under 16 KB.
  - The existing route, CSP and stream checks stay green.
- **Front end (`tests/web_smoke.js` via `web_test.py`, needs node):**
  - **Open the panel:** `showDetails({sid: s0.id})` with the fixture catalog makes `#details` wide; every catalog agent has an `AM.nodes` entry; the reviewers that ran in the seed are `used`/`running`; `designer` is `avail`; the `d:dual-review` edges are `used`.
  - **Activity:** it has a row "test-reviewer · Bash", and selecting it gives `hl` on `a:test-reviewer` and on edge `d:dual-review→a:test-reviewer`, with `fade` on `a:designer`. Selecting a `local_search` row highlights `l:local_search`.
  - **Refresh:** `showDetails(sel, true)` keeps `AM.sel`.
  - **Live:** push a new subagent `Explore` and re-render; a spy on `packet` records `main→d:agent` then `d:agent→a:Explore`. With `$("calm").checked = true`, no packets are drawn. With the session ended, no packets either.
  - **Isolation:** `G.nodes` / `G.edges` keys are unchanged by opening the panel.
  - **innerHTML trap:** enabled for the whole agent-map section, plus a source scan of the section (`// ---------- session agent map` to `function showDetails` end) for `innerHTML`, `insertAdjacentHTML`, `outerHTML`, `document.write`, `eval(` and `new Function`, as for Topics (`web_smoke.js:336-338`).
  - **Catalog failure:** the stub rejects; the map shows the used agents plus the note.
- **Commands:** `bash .claude-workspace/tests/catalog.test.sh`, `bash .claude-workspace/tests/web.test.sh`, `bash .claude-workspace/tests/live.test.sh` (unchanged, still green).
- **Manual (live dashboard):**
  - Click a session that ran `/develop`: the designer → approval → implementer chain is lit and the reviewers are dimmed.
  - Start an `Explore` agent: the packets travel main → Agent tool → Explore, and the result goes to the bottom node.
  - Arrow through the activity list and watch the highlight move.
  - Turn on Reduce motion: no packets, no halo.
  - Check the dark theme.

## Rollout
1. One niu-heimar PR from a feature branch. The description says `Reviews: deferred to the feature release`. Because `.claude-workspace/` is edited, offer `/workspace-sync` (workspace CLAUDE.md §6).
2. No flag, migration or config. Restart `niu-dash serve` to pick up the new route; the old front end ignores it.
3. Rollback: revert the PR. Nothing persistent is written; `store` keys are unchanged.

## Open questions
1. **Pairing for `/api/agents`?** Default: **no**, the same as `/api/state`; descriptions are one trimmed sentence of repo config. Alternative: paired browsers get descriptions, unpaired ones get names and models only.
2. **User agents (`~/.claude/agents`).** Include them? Default: **yes**, as `source: user`, because `agent_model()` already reads them and the session can use them.
3. **Other skills.** Should skills without a known flow (for example `/workspace-sync`) become L1 nodes? Default: **no**, they go in a muted footer line "Other skills: …", to keep the map readable.
4. **`local_status`.** Show it as a node? Default: **yes, but small**, because it is a tool the session can call; it never spawns a local agent run.
5. **Map placement.** Side panel (wide `#details`, proposed) or the full centre area in place of Agent interactions while a session is selected? Default: **side panel**, so Agent interactions stays visible and unchanged.
6. **Activity depth.** 40 rows from in-memory session data (limited by the `since` window and `MAX_TICKS`), or a new per-session events query for the full history? Default: **40 rows, in memory**. A paged `/api/sessions/<sid>/events` can come later.
