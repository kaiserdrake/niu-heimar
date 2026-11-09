# Dashboard Events tab: weekly Claude usage graph and token statistics (Part B)
Status: proposed · Date: 2026-11-08 · Request: "In Event add a section to show graph of weekly ending usage for claude" and "summary and statistics of which agent type usage most tokens and tokens usage across models."

## Decision summary (read this first)
- **"Event" = the Events tab** (`web/index.html:19`, `setView("events")`, `app.js:116-128`). Two new panels go there, between Pull requests and the Event feed: **Weekly usage** (`p-usage`) and **Token usage** (`p-tokens`). Part A (separate work) removes the meters from the Research tab. This part only adds things.
- **"Weekly ending usage" covers both readings.** The graph is a time series of the `seven_day` used % over the last 5 weeks. A dashed reset marker shows each weekly reset, and each completed week gets an **"ended N%"** label: the highest value seen in that window before it reset. Opus and Sonnet weekly lines appear only when Anthropic reports them. The 5-hour line can be toggled on and is off by default. A dotted **pace** line projects the current week to its reset.
- **Storage: a new SQLite file, `~/.local/state/niu-heimar/usage.db` (mode 0600)**, owned by a new `lib/niu_usage.py`. `events.db` stays as it is: no migration, no change to its 7-day pruning, and no lock contention with the hooks. Retention is 70 days for limit samples and 90 days for token rows.
- **One writer: a background thread in `niu-dash serve`.** The service runs under systemd with linger (`bin/install:113-136`), so it samples even when no browser is open. Every 60 s it (1) records a limits sample if the reading is new, and (2) scans transcripts incrementally within a fixed time and byte budget. The hooks, `niu-statusline` and niu-notify are not changed.
- **Limits sampling** reuses `niu_limits`, with a small refactor into a `latest_reading()` that returns the raw values. The OAuth fetch is untouched: same pinned URL, the no-redirect opener, the 64 KiB cap, and at most one call per 5 min. A row is stored only when the value or reset time changes, plus a heartbeat every 10 min. Each row's timestamp is the reading's own `updated` time, so a stale reading is never stored twice. A gap of more than 25 min between rows is drawn as a gap, never filled in. **No backfill**: history starts at deploy time.
- **Tokens: option (b), an incremental scanner over `~/.claude/projects`, run inside niu-dash.** I checked the transcripts on this machine (shape and numbers only). Subagent usage is **not** in the main transcript. It lives in `<project>/<session>/subagents/agent-<agentId>.jsonl` (`isSidechain: true`). A sibling `agent-<id>.meta.json` has `agentType`. The main transcript has none of the subagent lines, so nothing is counted twice. One API response is written as **several JSONL lines with the same `message.id` and the same `usage`** (observed: three lines, identical numbers). Dedup on `message.id` is required.
- **Privacy rule for the scanner:** it stores only numbers, ids, model names and agent types, and never text, prompts or paths in API output. The paths it reads are confined like the hook's (`hooks/agent-events.py:27-28`), plus `O_NOFOLLOW` and a regular-file check. It never logs line content.
- **Two GET endpoints, unauthenticated like the other GETs, with validated parameters and a 60 s cache:** `GET /api/usage/history?days=35` and `GET /api/usage/tokens?range=today|7d|30d&domain=`. There is no new POST, and nothing over HTTP can trigger a scan.
- **PR split:** **B1** usage history and graph, **B2** token scanner and stats. Both go to the workspace repo through `/workspace-sync` and `/dual-review`. B2's security review runs on Opus because it reads user transcripts. Build both on top of Part A, because `web/app.js` is being edited right now.

## Context
- **Claude limits today:** `claude_limits()` (`lib/niu_limits.py:80-116`) returns only the latest reading. That is either `claude-limits.json` from `bin/niu-statusline:30-44` (written while a terminal session runs) or the OAuth usage endpoint (`_api_limits`, `:67-77`, cached 300 s; `_fetch_usage` `:47-64` with `_NoRedirect` `:34-40` and `r.read(65536)` `:56`). It is served only through `GET /api/llm` (`bin/niu-dash:269-276`), which `loadLLM()` polls every 30 s while the page is visible (`app.js:919-947`). **No history is kept anywhere.** valkyrie-dispatch's `gate.recent` has at most 12 gate checks (`lib/niu_research.py:382`).
- **Events store:** `lib/niu_events.py:12-29` defines only the `events` table. `connect()` creates the schema only on a writable connection (`:50-65`). The hook prunes rows older than 7 days on SessionStart (`hooks/agent-events.py:139-141`). Hooks store no token counts. For Stop and SubagentStop they read the model from the last 256 KiB of the transcript (`:21-45`).
- **Transcripts (seen on this host, keys only):**
  - Main session: `~/.claude/projects/<cwd-slug>/<session>.jsonl`. Assistant lines carry `type:"assistant"`, `isSidechain:false`, `requestId`, `timestamp` (ISO, Z) and `message.{id, model, usage}`.
  - `usage` = `{input_tokens, cache_creation_input_tokens, cache_read_input_tokens, cache_creation:{…}, output_tokens, output_tokens_details:{thinking_tokens}, server_tool_use:{web_search_requests, web_fetch_requests}, service_tier, …}`. A nested per-iteration block repeats `output_tokens`, so read only the top-level fields.
  - Task results in the main file carry a `totalTokens` summary. **Ignore it**: it would double-count the subagent file.
  - Subagents: `<cwd-slug>/<session>/subagents/agent-<agentId>.jsonl`, with `agentId` on every line, plus `agent-<agentId>.meta.json` (`{"agentType": "designer", …, "pickedEffortModel": …}`) and `.prefix.json`.
  - `model: "<synthetic>"` lines exist (16 across all transcripts). They are not API calls.
  - Local niu-agent runs use `CLAUDE_CONFIG_DIR=~/.local/state/niu-heimar/ollama-claude` (`bin/niu-agent:50-51,100-102`), so their transcripts are outside `~/.claude/projects`. That is correct: they don't use the Claude plan.
- **Front end:** `h()` (`app.js:984-990`) and `svg()` (`:58-63`) build DOM with textContent and setAttribute. The `data-tip` tooltip is bound per container id (`:238`). The strict CSP (`bin/niu-dash:153-155`) allows no inline styles in markup, but CSSOM `el.style` is fine. The Events grid is `"left prs" "left feed"` (`web/style.css:331-335`). Under 900×600 the layout becomes a flex column stack (`:399-407`).
- **Research bot tokens** (a separate population, same plan): `ledger.today` and `ledger.last_7_days[].tokens_in/tokens_out` in the `/api/research` payload (`lib/niu_research.py:441,477`), which the front end already polls into `RESEARCH`.

## Requirements
- **Must:**
  - Show a graph of Claude weekly usage % over time, with reset markers and the value each completed week ended at.
  - Show token totals for today, 7 d and 30 d, the top agent types by tokens, tokens by model, share percentages and a per-day trend. Input, output, cache write and cache read are shown separately, plus a total.
  - Keep collecting while no browser is open, and draw gaps honestly.
  - Use stdlib only and add no new process or service.
- **Must not:**
  - Store or serve transcript content, prompts, file paths, or the OAuth token.
  - Call Anthropic's usage endpoint more than once every 5 min, or change the token handling in `_fetch_usage`.
  - Let an HTTP request trigger a scan or any write.
  - Block or slow the Claude hooks.
  - Change `events.db` schema or retention.
  - Use `innerHTML` for any new value.

## Options considered
**Where to keep history**
1. New tables in `events.db`: one file, and the hook's `connect()` would create them. Cons: the scanner's write transactions compete with the hooks' 2 s busy timeout (`niu_events.py:59`), so hook events could be dropped silently. Retention is mixed (7 d vs 90 d), and every hook's `executescript` grows.
2. **A separate `usage.db` (chosen).** It is isolated, has its own retention and `user_version`, and needs no migration. Cost: one more file and a second `connect()`.
3. JSON files: simple, but there is no dedup index and the append and rotate logic gets messy. Rejected.

**Who writes limit samples**
1. `niu-statusline`: finest resolution, but only while a terminal session runs. The desktop app never runs it (`app.js:893-894`).
2. niu-notify loop: optional (`bin/install --notify`) and only useful with Discord webhooks. It would also put the limits code into the process that holds the webhook secrets.
3. **The niu-dash background thread (chosen).** It always runs, it already owns both sources through `niu_limits`, and it is the only writer.

**Token collection**
1. (a) Parse usage in `hooks/agent-events.py` at Stop/SubagentStop. Cons:
   - It sees only workspace sessions after deploy.
   - Getting a whole turn's usage needs offset state in the hook, or whole-file reads on every Stop, which is slow on multi-MB transcripts and fragile when sessions are killed.
   - Sessions that end without Stop are missed.
   - The hook would get heavier.

   Rejected.
2. **(b) An incremental scanner inside niu-dash (chosen).** It covers the full history Claude Code still keeps (its default cleanup is 30 days), desktop and terminal sessions alike, and sessions outside the workspace. Byte offsets make each pass cheap. Dedup makes rescans idempotent. It fits the background thread already added for samples.
3. (c) A separate systemd timer or collector: better isolation, but a new unit, a new install path and a second writer to coordinate. Rejected for now. It is the fallback if the scan ever affects dashboard latency (see Risks).

**Placement in the UI**
1. A new "Usage" tab: more room, but the user asked for it in Events. Rejected.
2. **Two panels in the Events tab (chosen)**, collapsible, so the feed keeps its space.

## Design

### 1. Data flow
```
niu-statusline ─► claude-limits.json ─┐
OAuth usage API (≤1/5 min, unchanged) ┴► niu_limits.latest_reading() ─┐
~/.claude/projects/**.jsonl + agent-*.meta.json ─(scanner, budgeted)─┤
                                                                      ▼
                   niu-dash background thread (60 s tick, sole writer) ─► usage.db (0600)
                                                                      │ read-only connections
browser (Events tab, visible) ─► GET /api/usage/history · /api/usage/tokens (60 s cache) ◄┘
browser RESEARCH (already polled) ─► research-bot row (client-side)
```

### 2. `usage.db` schema (`lib/niu_usage.py`)
```sql
PRAGMA journal_mode=WAL;  PRAGMA user_version=1;
CREATE TABLE IF NOT EXISTS limit_samples (
  key TEXT NOT NULL CHECK (key IN ('five_hour','seven_day','seven_day_opus','seven_day_sonnet')),
  ts REAL NOT NULL,                      -- the reading's own 'updated' time, not the insert time
  used REAL NOT NULL CHECK (used BETWEEN 0 AND 100),
  resets_at REAL,                        -- epoch, rounded to 60 s
  source TEXT NOT NULL CHECK (source IN ('api','statusline')),
  PRIMARY KEY (key, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS tok_files (
  id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, dev INTEGER, ino INTEGER, size INTEGER, mtime_ns INTEGER,
  offset INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL CHECK (kind IN ('main','sub')),
  session_id TEXT, agent_id TEXT, agent_type TEXT, domain TEXT, project TEXT, last_seen REAL);
CREATE TABLE IF NOT EXISTS tok_models (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, family TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tok_msgs (
  mkey INTEGER PRIMARY KEY,              -- first 8 bytes of sha256(message.id), signed
  ts REAL NOT NULL, file_id INTEGER NOT NULL, model_id INTEGER NOT NULL,
  inp INTEGER NOT NULL, outp INTEGER NOT NULL, cw INTEGER NOT NULL, cr INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS tok_msgs_ts ON tok_msgs(ts);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);   -- collecting_since, last_scan, scan_progress
```
- `connect(readonly=False)` copies `niu_events.connect`: a 0700 directory, chmod 0600 on create, `timeout=2`. Readers use `mode=ro` and treat `FileNotFoundError` as state `empty`.
- **Size:** about 90 B per message row including the index. At 3,000 API calls a day over 90 days, that is roughly 25 MB. Limit samples come to under 2 MB.
- **Retention:** `prune()` runs once an hour: `limit_samples` older than 70 d, `tok_msgs` older than 90 d, and `tok_files` not seen for 90 d. It ends with `PRAGMA incremental_vacuum` (the file is created with `auto_vacuum=INCREMENTAL`).

### 3. Limits sampler
- **`niu_limits.latest_reading(now=None, fetch=None) -> (updated, source, {key: {"used": float, "resets_at": epoch|None}}) | None`.** This is the source-selection part of `claude_limits()` (`:84-100`) moved into a function as is. It returns raw values, **without** the "window rolled over, so 0" substitution (`:112-113`). `claude_limits()` calls it and keeps its exact output, so the existing `tests/limits_test.py` must pass unchanged.
- **`record_limits(con, reading, now)`**, once per tick. For each key:
  - Skip when `updated` is not newer than the newest stored `ts` for the key.
  - Skip when `now - updated > 15 min` (the existing `STALE_SECS`), because a stale reading isn't the present.
  - Otherwise insert if `used` changed by at least 0.1, or `resets_at` moved by more than 1 h, or the last row is at least 10 min old (the heartbeat that proves coverage).
  - Values are clamped to 0–100, and `resets_at` is rounded to 60 s to absorb jitter.
- **Call budget:** the thread asks `latest_reading()` every 60 s. The API path is still throttled to one call per 300 s by `_api_limits`, and `NIU_CLAUDE_USAGE_API=0` still turns it off. Change in behaviour: those calls now also happen while no browser is open (at most 288 a day). This is documented in the README.
- **No backfill.** `gate.recent` has 12 sparse points with no reset times, from a different source. Seeding it would put confident-looking lines on a mostly empty chart. `meta.collecting_since` is set on the first insert, and the graph says "collecting since …".

### 4. `GET /api/usage/history?days=35`
- `days` is an integer clamped to 7–70 (default 35). Anything else falls back to 35. The response is cached per `days` for 60 s.
```json
{"state": "ok", "now": 1762560000, "collecting_since": 1762400000, "gap_secs": 1500, "tz": "Asia/Tokyo",
 "series": {"seven_day": {"label": "Weekly", "points": [[1762400000, 12.0], [1762400600, 12.5], null, [1762410000, 14.0]]},
            "five_hour": {"label": "5-hour", "points": []},
            "seven_day_opus": null, "seven_day_sonnet": null},
 "resets": [1762473600],
 "weeks": [{"resets_at": 1762473600, "ended_pct": 83.0, "last_sample": 1762470000, "partial": false}],
 "current": {"used": 41.0, "resets_at": 1763078400, "updated": 1762559940, "source": "api", "stale": false}}
```
- **Points:** `null` marks a gap, meaning two consecutive rows more than `gap_secs` (25 min) apart. Each key is downsampled to at most 900 points. The window is split into 900 buckets and each bucket keeps its max, so peaks survive. Empty buckets become `null` only when they lie inside a real gap.
- **Size cap:** 4 keys × 900 points stays under about 60 KB. A series is `null` when the key never appeared.
- **`weeks`** (`niu_usage.weeks()`, pure and unit-tested): samples of `seven_day` are grouped by `resets_at`. For each window with `resets_at <= now`, `ended_pct` is the max `used` in the window. `partial` is true when the last sample is more than 6 h before the reset. The UI then shows "≥ N%".
- **States:** `ok`, `empty` (no `usage.db` or no rows), `disabled` (`NIU_USAGE_COLLECT=0`), `error` (a fixed message, never `str(exc)`).

### 5. Token scanner
- **Roots:** `root = (CLAUDE_CONFIG_DIR or ~/.claude).resolve() / "projects"`. Only two shapes are accepted: `root/<slug>/<uuid>.jsonl` (main) and `root/<slug>/<uuid>/subagents/agent-<id>.jsonl` (sub). `<uuid>` must match `[0-9a-f-]{36}` and `<id>` must match `[A-Za-z0-9]{1,40}`.
- **Listing:** `os.scandir` with `follow_symlinks=False`. Symlinked dirs and files are skipped. The scan reads at most 5,000 files per pass, and only files with an mtime within the last 90 days.
- **Opening:** `os.open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)`, then `fstat`. The file must be `S_ISREG` and owned by the current uid, and its resolved path must lie under `root`. FIFOs and devices are rejected before any read.
- **Incremental read:**
  - Reading starts at the stored `offset`. If `(dev, ino)` changed or `size < offset`, the offset resets to 0. Dedup makes that safe.
  - Only complete lines are consumed, so the offset advances to the last `\n`. A half-written last line waits for the next pass.
  - Lines over 8 MiB are skipped and counted.
  - Fast path: lines without both `"usage"` and `"assistant"` byte substrings are never parsed.
- **Budget per tick:** at most 32 MiB read or 1.5 s wall time, whichever comes first. The thread then yields. The first backfill therefore spreads over several minutes, and `meta.scan_progress` reports `{files_done, files_total, bytes_pending}`.
- **Extraction (allowlist)** from `json.loads(line)`. Everything else is dropped at once:
  - `message.id` (`msg_[A-Za-z0-9]{8,64}`; fall back to `requestId` `req_[A-Za-z0-9]{8,64}`; no id means skip).
  - `message.model` (`RE_MODEL` from the hook; `<synthetic>` is skipped).
  - `usage.{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}`: integers from 0 to 10,000,000. Anything else counts the line as corrupt.
  - `timestamp` (ISO; fallback the file's mtime).
  - `sessionId`, `agentId` and `cwd`. `cwd` is used only for `locate()`, read on the first line that has it, and never stored raw.
- **Dedup:**
  - `INSERT … ON CONFLICT(mkey) DO UPDATE SET outp=max(outp,excluded.outp), inp=max(inp,excluded.inp), cw=max(cw,excluded.cw), cr=max(cr,excluded.cr)`.
  - Split content-block lines collapse to one row, and partial streaming values (if a Claude Code version writes them) resolve to the final one.
  - A message copied into a resumed or forked session keeps its first `file_id`, so it is counted once and attributed to its original session.
- **Attribution (`tok_files`):**
  - `kind=main` gives `agent_type='session'`.
  - `kind=sub` gives `agentType` from the sibling `agent-<id>.meta.json`. The file is read through the same open checks, at most 16 KiB, and only that key is kept; it must match `[A-Za-z0-9_.:-]{1,80}`.
  - If meta.json has no usable type, fall back to the read-only `events.db` lookup `SELECT agent_type FROM events WHERE agent_id=? AND agent_type IS NOT NULL LIMIT 1`. If that also fails, use `'subagent'`.
  - Older transcript formats with `isSidechain:true` lines inside a main file are attributed to `'subagent'`.
  - `domain` and `project` come from `niu_events.locate(cwd)`. A cwd outside the workspace gives `domain='(outside)'` and `project=NULL`, so no outside paths are stored.
- **Models:**
  - `name` = the `shortModel` rule (strip `claude-` and `-YYYYMMDD`).
  - `family` ∈ `opus|sonnet|haiku|other`, by substring.
  - Non-Claude models count as `other`. They can only appear from proxied setups.
- **Errors:**
  - Each file is processed in its own try block, so a bad file never stops the pass.
  - Counters (`corrupt_lines`, `skipped_files`) go to `meta`.
  - Nothing goes to stdout. With `NIU_DASH_DEBUG` set, only `type(e).__name__` and the file id are printed, never line text or paths.
  - SQLite busy: skip the tick.
- **CLI:** `niu-dash usage-scan [--full]` runs passes to completion in the foreground and prints counts only (files, messages, corrupt lines). `--full` resets all offsets. This is useful for the first backfill and for debugging. It writes nothing outside `usage.db`.

### 6. `GET /api/usage/tokens?range=7d&domain=`
- **Parameters:** `range` ∈ `today|7d|30d` (default `7d`; anything else gives `7d`). `today` starts at local midnight on the host.
- **`domain`:** empty means all, including `(outside)`. Otherwise it must equal one of the distinct `tok_files.domain` values. An unknown value is treated as all and returned as `domain: ""`.
- **Caching:** 60 s per `(range, domain)`.
```json
{"state": "ok", "range": "7d", "since": 1761955200, "now": 1762560000, "tz": "Asia/Tokyo", "domain": "",
 "kpis": {"today": {"total": 4812345, "output": 120000}, "7d": {"total": 48200000, "output": 1100000}, "30d": {"total": 150000000, "output": 3900000}},
 "totals": {"input": 300000, "output": 1100000, "cache_write": 4000000, "cache_read": 42800000, "total": 48200000, "messages": 3120, "sessions": 41},
 "by_agent": [{"agent_type": "session", "input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "total": 0, "share": 61.0, "messages": 0, "sessions": 12}],
 "by_model": [{"model": "opus-5-5", "family": "opus", "input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "total": 0, "share": 70.2}],
 "matrix": {"agents": ["session", "implementer"], "models": ["opus-5-5", "sonnet-5-5"], "total": [[0, 0], [0, 0]]},
 "daily": [{"t": 1762473600, "label": "Fri 7", "by_family": {"opus": 0, "sonnet": 0, "haiku": 0, "other": 0}, "total": 0}],
 "coverage": {"oldest": 1760000000, "last_scan": 1762559990, "scanning": false, "files_done": 1930, "files_total": 1930, "corrupt_lines": 0}}
```
- **Caps:** `by_agent` has the top 12 plus an `"other (n)"` row. `by_model` has the top 8 plus other. The matrix is at most 12×8. `daily` has 24 hourly buckets for `today`, 7 for `7d` and 30 for `30d`.
- **Shares:** `share` is computed from `total` and sums to 100 ± 0.1.
- **States:** `ok`, `scanning` (first backfill not done: payload as far as it goes, plus progress), `empty`, `disabled`, `error`.
- **Background thread lifecycle:** `niu_usage.start_background()` is called from `cmd_serve` (`bin/niu-dash:441-449`). It is a single daemon thread with a module lock, so web tests and `usage-scan` can't start two writers. `NIU_USAGE_COLLECT=0` disables it, and the tests use that.

### 7. Events tab layout
```
≥1200 px wide                                            900–1199 px              <900 px or <600 px tall (scroll)
┌left──────┐┌PULL REQUESTS (max 30vh) ────────────────┐ ┌left┐┌PRs        ┐       PRs
│ sessions ││                                         │ │    │├WEEKLY USAGE┤       Weekly usage
│ needs    │└─────────────────────────────────────────┘ │    │├TOKEN USAGE ┤       Token usage
│ LLMs     │┌WEEKLY USAGE ▾──────┐┌TOKEN USAGE ▾──────┐ │    │├EVENT FEED  ┤       Event feed
│          ││ graph              ││ kpis, bars        │ │    │└────────────┘
│          │└────────────────────┘└───────────────────┘ └────┘
│          │┌EVENT FEED (rest, min 240px) ─────────────┐
└──────────┘└──────────────────────────────────────────┘
```
```css
.p-usage { grid-area: usage; } .p-tokens { grid-area: tokens; }
.mcgrid .panel.p-usage, .mcgrid .panel.p-tokens { display: none; }
.mcgrid[data-view="events"] { grid-template-columns: minmax(250px, 24%) minmax(0, 1fr) minmax(0, 1fr);
  grid-template-rows: auto auto minmax(240px, 1fr); grid-template-areas: "left prs prs" "left usage tokens" "left feed feed"; }
.mcgrid[data-view="events"] .panel.p-usage, .mcgrid[data-view="events"] .panel.p-tokens { display: flex; max-height: 38vh; }
.mcgrid[data-view="events"] .panel.p-prs { max-height: 30vh; }      /* was 42vh */
@media (max-width: 1199px) { .mcgrid[data-view="events"] { grid-template-columns: minmax(250px, 24%) minmax(0, 1fr);
  grid-template-rows: auto auto auto minmax(240px, 1fr); grid-template-areas: "left prs" "left usage" "left tokens" "left feed"; } }
/* <900×600: the existing flex stack (style.css:399-407) applies; DOM order gives PRs, usage, tokens, feed */
```
- **Markup:** the two `<section>`s go into `web/index.html` right after `p-prs` (`:95-98`), so the narrow stack is in the right order.
- **Collapsing:** each panel header has a collapse button (`aria-expanded`, saved in `store` keys `niu.usage.open` and `niu.tokens.open`, default open). A collapsed panel shrinks to its header, and the feed takes the space.

### 8. Weekly usage panel (`#usagebody`)
```
WEEKLY USAGE ▾                         [2w|5w|10w]  [ ] 5-hour   collecting since Nov 8 · updated 12:01
100%┤- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
 90%┤·····················(10% weekly floor)··································
    │        ●ended 83%   ┆        ●ended ≥67%  ┆              ╱·····pace ≈78%
    │      ╱              ┆      ╱              ┆    ╱‾‾‾╲___╱ ●now 41%
  0%┼‾‾‾‾‾‾───────────────┆──────────╳gap╳──────┆‾‾‾‾────────────────────────
     Oct 27      ↻Mon 09:00          ↻Mon 09:00         Nov 8         ↻Mon 09:00
Weekly: 41% now, resets Mon 09:00, on pace for about 78%. Last weeks ended at 83%, ≥67%.
```
- **Rendering:** `renderUsage(u)` uses only `svg()` and `h()`. The SVG gets `viewBox`, `role="img"` and an `aria-label` equal to the summary line. The width follows the container, with a redraw on the existing `resize` handler (`app.js:885-886`) when `VIEW === "events"`.
- **Axes:** the y axis is 0–100% with gridlines at 0, 50, 90 and 100 (class `axis`; 90 has the label "10% weekly floor", tying to the valkyrie gate). The x axis has day ticks (`toLocaleDateString` weekday plus day) in browser local time.
- **Lines:**
  - `seven_day`: a `<path class="ul ul-week">` in `--accent`, 2 px.
  - Opus and Sonnet: `ul-opus` (dashed) and `ul-sonnet` (dotted).
  - `five_hour`: `ul-5h` (thin, `--muted`), only when toggled on.
  - Each `null` starts a new `M` segment. A gap gets a `rect.gapband` (a hatched pattern defined in `<defs>`) with the tooltip "no samples: dashboard down, or no terminal session and the usage API off".
- **Reset markers:** a `line.reset` (dashed, `--border`) plus a `text.lbl` "↻ Mon 09:00" at each entry in `resets` and at `current.resets_at`.
- **Week end:** a `circle.wkend` at `(last_sample, ended_pct)`, with text "ended 83%" or "ended ≥67%" (when partial). The level is shown by **text and an icon shape, not only colour**: `wkend ok|warn|bad` at <70, 70–89 and ≥90, with the same thresholds as `claudeLimits` (`app.js:910`).
- **Pace:** a `line.pace` (dotted) from now to `current.resets_at`, at slope `current.used / (now - (resets_at - 7 d))`, clamped at 100, with the label "pace ≈ N%". It is hidden when less than 12 h of the window has passed.
- **Tooltips:** 1 transparent `rect.hit` per x bucket (at most 240), each with `data-tip` "Tue 14:00\nWeekly 41% · 5-hour 12%\nsource: usage API". The container id `usagegraph` is added to the binding list at `app.js:238`.
- **Text summary** (`#usagesum`, `aria-live="polite"`): the sentence shown in the wireframe. This is the accessible equivalent of the chart.
- **States:**
  - `empty`: "Collecting: the first sample appears within 5 minutes. Earlier weeks can't be recovered."
  - `disabled`: "Usage history is off (NIU_USAGE_COLLECT=0)."
  - stale (`now - current.updated > 30 min`): a warn note, "No new readings since 11:20: no terminal Claude session, and the usage API is off or unreachable."
  - `error`, or a fetch failure: "Can't read usage history from the dashboard server."
  - While loading: "Loading…".
- **Theme:** only CSS variables (`--accent`, `--muted`, `--border`, `--ok`, `--warn`, `--bad`, `--bg2`), so the light and dark blocks (`style.css:2-16,294-297`) apply. No colours are hard-coded.

### 9. Token usage panel (`#tokbody`)
```
TOKEN USAGE ▾                    [Today|7 days|30 days]   metric [Total ▾]      updated 12:01
┌Today────────┐┌7 days───────┐┌30 days──────┐
│ 4.8M        ││ 48.2M       ││ 150M        │   (.stat boxes; second line: "output 120k")
└─────────────┘└─────────────┘└─────────────┘
7 days · in 0.3M · out 1.1M · cache write 4.0M · cache read 42.8M · 3,120 calls · 41 sessions
By agent type                                   share    total    out
 session           ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇░░░░░░        61%     29.4M    700k   (12 sessions)
 implementer       ▇▇▇▇▇░                       18%      8.7M    210k
 designer          ▇▇░                           9%      4.3M    110k
 … other (4)
 research bot · valkyrie-dispatch (separate, not in totals)  in 1.2M · out 80k  → Research tab
By model
 opus-5-5          ▇▇▇▇▇▇▇▇▇▇▇▇▇               70%
 sonnet-5-5        ▇▇▇▇▇                       25%
 haiku-5-5         ▇                            5%
Per day (stacked by model family)   ▁▃▅▂▇▆▄
▸ Agent × model table
ⓘ What is counted
```
- **Rendering:** `renderTokens(t)` uses `h()` and `svg()` only.
  - Bars are SVG `rect`s, one per token kind (`tk-in`, `tk-out`, `tk-cw`, `tk-cr`), stacked inside the row's share of the width. Exact numbers sit in the row text and the `data-tip` ("implementer · 18%\ninput 40k · output 210k\ncache write 1.1M · cache read 7.3M\n412 calls · 9 sessions").
  - A legend under "By agent type" names the four kinds, so colour is never the only cue.
  - The tooltip container id `tokbody` is added at `app.js:238`.
- **Metric select:**
  - Options: `Total` (all four kinds, the default), `Output`, `Input + cache write`, `Cache read`.
  - The choice re-sorts rows and recomputes shares on the client from the per-kind fields. Shares are `metric / sum(metric)`.
  - It is saved in `niu.tokens.metric`.
- **Range:**
  - The panel has its own segmented control (`segbtn`, the same as the Research view toggle), saved in `niu.tokens.range`, default `7d`. The header `since` control (1h/24h/7d) drives the SSE feed and has no 30-day option, so it is not reused.
  - The KPI boxes always show today, 7 d and 30 d.
  - The header **domain** filter is passed through as `domain=`.
- **Per day:** SVG columns stacked by model family (`fam-opus`, `fam-sonnet`, `fam-haiku`, `fam-other`, with patterns plus a legend) and `data-tip` per column. `today` uses hourly columns.
- **Agent × model table:** a `<details>` with a plain `<table class="rstable">` (reused), with numbers as textContent. It is the non-graphic fallback.
- **Research bot row:**
  - It is built **on the client** from `RESEARCH.data.ledger` (already polled every 30 s, `app.js:1920`): `today` for Today, and the sum of `last_7_days` for 7 days.
  - For 30 days it shows "last 7 days only" in the row.
  - It is shown below the ranked rows, muted, never included in totals or shares, with a link button `setView("research")`.
  - It is hidden when `RESEARCH` isn't `ok`.
- **"What is counted"** (a static `<details>` text):
  - "Counted from Claude Code transcripts on this host (`~/.claude/projects`), for terminal and desktop sessions and their subagents. With *All domains* this includes projects outside the workspace."
  - "Not counted: Claude on other devices, claude.ai chats, local-model agents (niu-agent), and the research bot, which is shown separately. Claude Code deletes transcripts after its cleanup period (30 days by default), so older history can't be backfilled."
  - "Tokens are not the plan's limit units: the weekly % is the authoritative measure."
- **States:**
  - `scanning`: a progress line, "Reading transcripts for the first time: 412 of 1,930 files", plus the partial data.
  - `empty`: "No Claude usage found in the transcripts yet."
  - `disabled` and `error`: as in §8.
  - A fetch failure keeps the last data with "as of HH:MM".
- **Refresh:**
  - `loadUsage()` fetches both endpoints when `setView("events")` runs, when range or domain changes, and every 60 s while `VIEW === "events" && !document.hidden`.
  - It never runs on other tabs: the samples are collected on the server anyway.

### 10. Security
- **Threat: a prompt-injected agent with `curl` to loopback.**
  - It can read `/api/usage/*`. These are aggregate numbers, agent type names and model names. That is the same exposure class as `/api/research` and `/api/llm`, which are already readable without pairing.
  - It cannot write or trigger scans: there is no POST, and parameters are enums or a clamped int.
  - It gains nothing about the OAuth token, because that code path is unchanged and the token is never stored.
  - No `block-env.sh` change is needed. `usage.db` holds no secrets, the same as `events.db`.
- **Threat: hostile transcript content** (an agent can write files under `~/.claude/projects`).
  - Planted symlinks, FIFOs, devices or files owned by another uid are refused before any read (§5).
  - Huge files or lines are bounded by the per-tick budget and the 8 MiB line cap.
  - Absurd numbers make the line count as corrupt (cap: 10 M per field per message).
  - Model names and agent types are regex-validated. Anything else becomes `other` or `subagent`.
  - The UI renders everything with textContent and setAttribute.
  - Worst case: someone fakes their own token numbers. The impact is cosmetic.
- **Threat: transcript content leaking.**
  - Lines are parsed in memory, and only the allowlisted fields of §5 are kept.
  - Nothing is logged except exception class names, under `NIU_DASH_DEBUG`.
  - API payloads carry no paths, session titles or cwd. `tok_files.path` stays inside `usage.db` (0600) and is never selected by an endpoint.
  - `description` and `pickedEffortModel` in meta.json are ignored.
  - A canary test enforces all of this (Test plan).
- **Threat: DoS.**
  - Request handlers read only `usage.db` through `mode=ro`, with indexed, bounded aggregates and a 60 s memo.
  - The scanner's cost is fixed per tick, whatever the HTTP traffic.
  - If the scan ever hurts request latency, the fallback is a separate process (option c) with the same module.
- **Anthropic usage endpoint:** same function, same pinned URL, `_NoRedirect`, the 64 KiB read cap and the 300 s throttle. The only change is that calls continue while no browser is open, and `NIU_CLAUDE_USAGE_API=0` still disables them.
- **CSP:** unchanged. There are no inline styles: bars and positions are SVG attributes, and panel heights use classes.

## Changes
| File | Change |
|---|---|
| `.claude-workspace/lib/niu_usage.py` | **New.** `db_path()` (`db_path().parent / "usage.db"`, `NIU_USAGE_DB` override), `connect(readonly)`, `SCHEMA`, `record_limits()`, `weeks()`, `history(days)`, `scan_once(budget_bytes, budget_secs)`, `_open_confined()`, `_extract(line)`, `_agent_type(meta_path, agent_id)`, `norm_model()`, `tokens(range, domain)`, `prune()`, `start_background()`, `_memo` (60 s). |
| `.claude-workspace/lib/niu_limits.py` | Move source selection into `latest_reading(now, fetch)` (raw values, `updated`, `source`). `claude_limits()` calls it, and its output is unchanged. `_fetch_usage`, `_NoRedirect` and `_api_limits` are untouched. |
| `.claude-workspace/bin/niu-dash` | Import `niu_usage`. `cmd_serve` calls `niu_usage.start_background()` unless `NIU_USAGE_COLLECT=0`. Add `GET /api/usage/history` and `GET /api/usage/tokens` (B2), each with parameter validation. Add the subcommand `usage-scan [--full]` (B2). Update the docstring. |
| `.claude-workspace/web/index.html` | After `p-prs`: `<section class="panel p-usage" aria-label="Weekly Claude usage">` (header with collapse button, range segbtns, 5-hour checkbox, `#usageupd`; body `#usagebody` holding `#usagegraph` and `#usagesum`) and `<section class="panel p-tokens" aria-label="Token usage">` (B2: collapse, range segbtns, metric `<select id="tokmetric">`, `#tokupd`, body `#tokbody`). |
| `.claude-workspace/web/app.js` | `loadUsage`, `renderUsage`, `renderTokens`, `tokFmt` (reuse `rsN`), `TOK_KINDS`, `FAMILY`. `setView` calls `loadUsage()` for events. The domain `change` handler calls it too. A 60 s `setInterval` gated on `VIEW === "events"`. The resize redraw. Add `usagegraph` and `tokbody` to the tip list (`:238`). No `innerHTML` in the new code. |
| `.claude-workspace/web/style.css` | The Events grid rules of §7, plus `.ul-*`, `.reset`, `.pace`, `.gapband`, `.wkend.*`, `.tk-*`, `.fam-*`, `.hit` (`fill: transparent`), `.tokrow`, `.tokkpis` (reusing `.stat`), and `.collapsed .pb { display: none; }`. Variables only. |
| `.claude-workspace/tests/usage_test.py`, `tests/usage.test.sh` | **New.** See Test plan. The wrapper is picked up by `bin/workspace-lint`. |
| `.claude-workspace/tests/fixtures/usage/` | **New.** `projects/-home-u-WORKSPACE-PERSONAL-demo/<uuid>.jsonl` (main, with 3 split lines sharing one `msg_` id, a corrupt line, a `<synthetic>` line, a truncated last line, a `totalTokens` Task result, and the canary text), `<uuid>/subagents/agent-a1.jsonl` plus `agent-a1.meta.json` (`agentType: implementer`), `agent-a2.jsonl` without meta, a resumed `<uuid2>.jsonl` repeating one `msg_` id, and `hostile/` (model `<img onerror=x>`, agentType `../../x`, a 10^12 token count). Also `history.json` and `tokens.json` for the smoke run. |
| `.claude-workspace/tests/web_test.py` | Set `NIU_USAGE_COLLECT=0` and `NIU_USAGE_DB` to a temp path in `env` (`:18-20`). Route and parameter checks. Pass the usage fixtures to the smoke run (`:331`). |
| `.claude-workspace/tests/web_smoke.js` | Optional argv 6 and 7 (history, tokens): `setView("events")`, `renderUsage`, `renderTokens`, with the assertions in the Test plan. |
| `.claude-workspace/tests/limits_test.py` | Add `latest_reading()` cases. The existing `claude_limits` cases stay as they are. |
| `README.md` | "Web dashboard → Weekly usage and token stats": what is sampled and counted, `usage.db`, the background API calls, `NIU_USAGE_COLLECT=0` (`systemctl --user edit niu-dash`, `Environment=`), `niu-dash usage-scan`, and how to reset (delete `usage.db`). |
| `.claude-workspace/docs/design/2026-11-08-dashboard-usage-and-token-stats.md` | This document. |

Not changed: `hooks/agent-events.py`, `bin/niu-statusline`, `lib/niu_events.py`, `lib/niu_state.py`, `lib/niu_research.py`, `hooks/block-env.sh`, `bin/install`.

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| A scan stalls dashboard requests (GIL, big transcripts) | 1.5 s / 32 MiB budget per 60 s tick, the substring prefilter before `json.loads`, and offsets so steady state reads only new bytes. A test asserts the budget. If it is still noticeable, move `scan_once` into a `niu-dash usage-scan --loop` systemd unit (option c) with no schema change. |
| Undocumented transcript format changes (meta.json, `message.id`, the `subagents/` layout) | Every field is optional, with fallbacks (events.db join, `'subagent'`, `requestId`). `coverage.corrupt_lines` shows up in the UI when it is above 0. Fixtures pin the current shape. |
| Double counting (split lines, resumed sessions, Task `totalTokens`) | `mkey` from `message.id` with a max-merge, and Task results are never read. Fixture tests cover all three. |
| Undercounting (pruned transcripts, other devices) | The "What is counted" note, `coverage.oldest` shown as "data from …", and the weekly % is labelled authoritative. |
| Misleading weekly graph (gaps, partial weeks, reset jitter) | Gaps are explicit `null`s and hatched bands. `partial` gives "≥". `resets_at` is rounded, with a 1 h threshold for a new window. "Collecting since" is shown. |
| More Anthropic usage calls (24/7) | Still 1 per 5 min at most, the same function, and the existing opt-out. Documented. |
| Merge conflicts with Part A in `app.js`, `style.css` and `index.html` | Implement B after Part A merges, and rebase. The new code is in separate functions and blocks. |
| `usage.db` grows | Retention of 70 and 90 d, incremental vacuum, and an 8-byte key. About 25 MB at heavy use. |

## Test plan
**`tests/usage_test.py`** (the `check()` style; temp `NIU_USAGE_DB`, `NIU_EVENTS_DB` and `CLAUDE_CONFIG_DIR` pointing at a copy of the fixtures; `TZ=Asia/Tokyo`; never the real `~/.claude`)
1. **Sampler**, with a fake `latest_reading` sequence:
   - unchanged readings insert nothing until the 10 min heartbeat;
   - a change of 0.1 or more inserts;
   - a repeated `updated` is never inserted twice;
   - a reading older than 15 min is not inserted;
   - `ts == updated`, not now;
   - `resets_at` jitter of ±40 s gives the same window, and +7 d gives a new one;
   - values outside 0–100 are clamped.
2. **`weeks()` and `history()`:**
   - two synthetic weeks give `ended_pct` equal to the max before the reset;
   - a last sample 8 h before the reset gives `partial: true`;
   - a 40 min hole gives a `null` in `points`;
   - 10,000 samples are downsampled to 900 or fewer with the peak kept;
   - `days=9999` gives 70 and `days=abc` gives 35;
   - prune deletes rows older than 70 d.
3. **Scanner on the fixtures:**
   - the 3 split lines count once, and the max-merge holds when one line has lower `output_tokens`;
   - the corrupt line gives `corrupt_lines == 1`, and the scan continues;
   - `<synthetic>` is skipped;
   - the truncated last line is not consumed. After a newline is appended, it is counted;
   - a `msg_` id repeated in the resumed session counts once, attributed to the first file;
   - `agent-a1` gives `implementer`. `agent-a2` with an events row `agent_id=a2, agent_type=designer` gives `designer`, and without one gives `subagent`;
   - Task `totalTokens` does not change the totals;
   - a cwd outside the workspace gives `(outside)` with no project.
4. **Confinement:** a symlinked `.jsonl`, a symlinked project dir, a FIFO named `x.jsonl` (the scan returns at once and does not block), a file whose resolved path is outside root, and `agentType: "../../x"` are all refused or normalised.
5. **Idempotency and rewrites:** two passes give the same totals. `--full` gives the same totals. A file truncated and rewritten gives the same totals.
6. **Budget:** with `budget_bytes=1024`, the first pass stops early with `scanning: true`, and repeated passes converge to the full totals.
7. **Privacy canary:** fixture lines contain `CANARY-PROMPT-7f3a`, `sk-ant-api03-FAKEFAKE` and a path `/home/u/secret-project`. After a scan, none of them appears in the bytes of `usage.db` (including WAL), in `history()` or `tokens()` JSON, or in stderr with `NIU_DASH_DEBUG=1`.
8. **Aggregates:**
   - `by_agent` shares sum to 100 ± 0.1, and `other (n)` folds rows past 12;
   - `by_model` normalises `claude-opus-5-5-20261101` to `opus-5-5`;
   - with a fixed clock, `daily` for `7d` has 7 buckets aligned to local midnight in Asia/Tokyo;
   - `domain=PERSONAL` filters, and `domain=bogus` gives all with `domain: ""`.
9. **Hostile fixture:** the 10^12 count counts as corrupt, the model becomes `other`, and nothing raises.

**`tests/limits_test.py`:** `latest_reading()` returns raw values without zeroing a window that rolled over, while `claude_limits()` still zeroes it. All existing cases pass unchanged.

**`tests/web_test.py`:**
- `GET /api/usage/history` and `/api/usage/tokens` give 200 with `state` in `{empty, disabled}` (collection is off in tests). `range=../../x`, `days=-1` and `domain=<script>` are each answered normally with the defaults.
- `POST /api/usage/*` gives 404.
- The index has `p-usage`, `p-tokens`, `usagegraph` and `tokbody`. The CSP header is unchanged.
- A static check that the new render functions in `app.js` do not contain `innerHTML`.

**`tests/web_smoke.js`** (argv 6/7):
- `renderUsage(history.json)` draws at least one `path.ul-week`, a `text` with "ended 83%" and one with "≥", and a `rect.gapband`. `#usagesum` text starts with "Weekly:".
- `renderTokens(tokens.json)` lists `session` first, and the hostile model name appears as literal text.
- `{state:"empty"}` and `{state:"scanning"}` render their messages without throwing.

**Manual checks (on the user's machine):**
1. After deploy, `sqlite3 ~/.local/state/niu-heimar/usage.db 'select count(*) from limit_samples'` grows within 5 min with no browser open.
2. `niu-dash usage-scan` finishes and prints counts. Compare one session's output tokens with what Claude Code's `/cost` shows, if available.
3. Stop niu-dash for 1 h, then start it again: the graph shows a hatched gap.
4. Check light and dark themes, Reduce motion, and widths of 1400, 1000 and 400 px (a phone over the tailnet).
5. Check that the Events feed stays usable with both panels collapsed and expanded.

## Rollout
1. **Prerequisite:** Part A merged to `main`, then branch `feat/dashboard-usage-history` from it.
2. **B1:**
   - `niu_usage` (schema, sampler, `weeks`, `history`, background thread with only the sampler), the `niu_limits` refactor, `/api/usage/history`, the `p-usage` panel and the CSS grid (with the `tokens` area present but the panel hidden until B2), tests and the README.
   - `/workspace-sync`, then `/dual-review` per the workspace rules (§2 and #44). Then release, and `systemctl --user restart niu-dash`.
   - Samples start accumulating at once, which is why B1 ships first.
3. **B2:**
   - The scanner, `/api/usage/tokens`, `usage-scan`, the `p-tokens` panel, fixtures and tests.
   - `/dual-review` with **security-reviewer on Opus** (it reads user transcripts and adds new file-access code).
   - After the restart, the first backfill runs in the background, and the panel shows progress.
4. **Rollback:**
   - Revert the PR, or set `Environment=NIU_USAGE_COLLECT=0` with `systemctl --user edit niu-dash` and restart. That stops all collection, and the panels show `disabled`.
   - `rm ~/.local/state/niu-heimar/usage.db*` removes all collected data.
   - Nothing else depends on `usage.db`, and `events.db` is untouched.

## UNVERIFIED until tried on this machine
- That `agent-<id>.meta.json` `agentType` is present for all subagent runs, including built-ins, older sessions and the desktop app. It was seen on the 2026-11 files only.
- That the hook's `agent_id` equals the `<id>` in `subagents/agent-<id>.jsonl`. This matters only for the fallback join.
- Whether resumed or forked sessions copy earlier assistant lines with the same `message.id`. Dedup assumes yes, and is harmless if not.
- Whether the top-level `usage` already includes nested per-iteration usage (server-side tool loops). The design assumes the top level is the per-request total.
- Whether the Claude Code desktop app writes its transcripts to the same `~/.claude/projects` tree.
- That Anthropic's `seven_day` is a fixed window with a stable `resets_at`, not a rolling one. If it rolls, "ended at" becomes "value at each reported reset".
- The first-scan volume and time, and the dashboard's latency while scanning.
- The transcript cleanup period on this machine (`cleanupPeriodDays`), which sets how far back B2 can see.

## Open questions (defaults apply if unanswered)
1. Count sessions outside the workspace when *All domains* is selected? **Default: yes**, labelled `(outside)`, because the weekly limit is account-wide.
2. Default token metric: **Total (all four kinds)**. An "API-equivalent USD" weighting would track plan usage better, but it needs a maintained price table, so it is left out for now.
3. Show the 5-hour line by default? **Default: off** (toggle). Show the pace projection? **Default: on**.
4. Retention: **70 d samples / 90 d tokens.** Should it be longer for year-over-year trends? It costs about 10 MB per extra 90 d.
5. Seed the graph with valkyrie `gate.recent`? **Default: no** (sparse, no reset times).
6. Add local niu-agent transcripts (`ollama-claude/projects`) as a separate "local models" row? **Default: no**, because they don't use the Claude plan.
