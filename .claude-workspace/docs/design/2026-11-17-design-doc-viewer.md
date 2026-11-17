# Dashboard: read design docs as rendered markdown, and compare revisions
Status: proposed · Date: 2026-11-17 · Request: "Can we add feature in the dashboard to view design plan in rendered markdown? If possible have a means to view changes between revisions?"

Scope: niu-heimar (`/home/norman/WORKSPACE`, config under `.claude-workspace/`), one PR, this machine only. Line numbers are from `main` at `d805b5d`.

## Decision summary (read this first)
| # | Topic | Decision |
|---|---|---|
| 1 | Where it shows | A new **Designs** tab (`#designs`) next to Agents / Events / Research. On the left is a list of docs grouped by domain/project, newest first. On the right is the rendered doc with a heading outline. Deep link: `#designs/<docId>`. The session panel links to it (see 6). |
| 2 | Which files | Only `docs/design/*.md`, one level deep, in **`.claude-workspace/docs/design/`** and in **linked projects** (a `.claude/settings.local.json` exists, the same rule as `niu_prs.discover`, `lib/niu_prs.py:38-52`), plus the `project_dir` of any session in the live window. The browser never sends a path. It sends an opaque `docId` that the server looks up in an index it built itself. |
| 3 | Rendering | **On the server, a small stdlib markdown parser** (`lib/niu_designs.py`) that returns a **JSON block tree**, not HTML. The browser builds DOM nodes with `h()` / `textContent` (`app.js:2089`). There is no `innerHTML`, no CDN and no build step, and the CSP stays `script-src 'self'` (`niu-dash:164-166`). Raw HTML in a doc is shown as literal text. |
| 4 | Revisions | Three sources, merged into one list and deduplicated by content hash: **(a) git commits** of the file (`git log --follow`, at most 20); **(b) the working copy** when it is untracked or differs from HEAD; **(c) draft snapshots** that `hooks/agent-events.py` captures on every `PostToolUse` Write/Edit/MultiEdit of a design doc, into a new `design_snapshots` table in `events.db`. Source (c) is the only one that sees the `/develop` approve → "Change it" rewrites of an untracked doc, and that loop is the main reason to compare revisions. |
| 5 | Diff | `GET /api/designs/diff?id=&a=&b=` uses `difflib` on the server and returns two views: **Rendered**, a block-level diff (paragraphs, headings, code blocks) with row-level diffs inside tables and item-level diffs inside lists, plus word-level marks inside changed paragraphs and headings; and **Lines**, a unified diff with 3 lines of context. The header line shows `+N −M lines` and "Changed sections: Design, Changes, …". |
| 6 | Session tie-in | `/api/state` is not changed. `GET /api/designs?sid=` returns the docs that session wrote, from its `Write`/`Edit` events whose summary is `docs/design/*.md`. The session panel header (`showDetails`, `app.js:1293-1308`) gets a "Design: \<title\>" link, and the designer node tooltip says "click to open the design". |
| 7 | Security | **Every `/api/designs*` route needs a paired browser**, the same as `/api/topics` (`niu-dash:281-285`). Docs hold project internals, including WORK, and the tailnet can reach unpaired GETs. Files are checked twice, when the index is built and again on every read: name regex, no `.env*`, a regular file opened with `O_NOFOLLOW`, the resolved path must sit directly under the project's resolved `docs/design/`, at most 512 KB, and `niu_ollama.guard_allows()` must pass (the same rules as the hook, so `.claude/protected-paths` is honoured). Links are limited to `https?:`, `#anchor` and sibling `*.md` files (opened as viewer links), and the browser checks the scheme again. Images are shown as `[image: alt]`. |

**Where the code differs from what the request assumes (please confirm):**
1. **Git alone can't show the "Change it" revisions.** A doc in the approval loop is usually untracked (for example `docs/design/2026-11-17-multi-machine-setup-and-dashboard.md` today) and is rewritten in place, so git has nothing until it is committed. That is why 4(c) exists. Edits made through `Bash` (`sed`, `cat >`) are not captured; the working copy still shows the latest text.
2. **There is no "pre-edit" copy for docs that already exist.** The first snapshot is taken *after* the first Write/Edit once this feature ships. For tracked docs, HEAD covers the earlier state. Open question 2 covers an optional best-effort pre-edit capture.
3. **The session doesn't record which doc the designer wrote.** It is derived from the event summaries (`agent-events.py:64-65` stores the path relative to the project for Write/Edit). Summaries are cut to 120 characters, so a match is accepted only when it ends in `.md`.

## Context
- **Server.** `bin/niu-dash` is a stdlib `ThreadingHTTPServer` on 127.0.0.1, published with `tailscale serve`.
  - `do_GET` routes: `niu-dash:254-311`. Headers and CSP: `:157-169`. Pairing check: `device_for(self._cookie())` (`:91-103`), already used for paired-only GETs (`/api/topics`, `:281-285`).
  - The GET for `/api/agents` (`:266-276`) shows the `sid` → live-session pattern (`RE_SID`, `live().sessions`).
- **Event pipeline.** `hooks/agent-events.py` runs async on every hook event (`user-settings.template.json:43-50, 108-118`). It writes one row per event into `events.db` (`niu_events.db_path`, `lib/niu_events.py:46-50`), and prunes rows older than 7 days on `SessionStart` (`agent-events.py:139-141`).
  - Agents can't read the state directory: `block-env.sh:24` blocks `niu-heimar/*`.
- **Helpers to reuse.**
  - `niu_events`: `WS`, `SHARED`, `under_ws()` (`:71-76`), `locate()` (`:79-102`), `redact()` (`:117-120`), `EDIT_TOOLS` (`:13`).
  - `niu_ollama`: `guard_allows(path, cwd)` (`lib/niu_ollama.py:187-194`, which runs `block-env.sh` with a synthetic Read) and the hardened `_git()` (`:197-199`, `core.fsmonitor=false`, `diff.external=`).
- **Front end.** Vanilla JS, no build step.
  - Views: `setView()` (`app.js:116-129`), tabs in `index.html:17-21`, and the grid by `data-view` (`style.css:338-473`).
  - Session panel: `showDetails` / `amRender` (`app.js:1272-1308`). DOM helper: `h()` (`app.js:2089-2095`).
  - The smoke test (`tests/web_smoke.js`) traps `innerHTML` and scans sections for `innerHTML`/`eval(`.
- **Related open design.** `2026-11-17-multi-machine-setup-and-dashboard.md` adds peer data to `/api/state`/`/api/stream` and a `machine` tag. This design adds **no fields to `/api/state`** and touches neither the stream nor `to_json`, so the two don't conflict. Docs on asgard are out of scope (Open question 5).

## Requirements
- Must: list and render every design doc of the workspace config and of the linked projects, with headings, paragraphs, nested lists, GFM tables, fenced code, blockquotes, rules, and inline code/bold/italic/strike/links. The output must be readable in the light and dark themes.
- Must: list a doc's revisions (commits, the working copy, drafts) and show a rendered diff and a line diff between any two of them.
- Must: link from a session to the design docs it wrote.
- Must not: serve anything other than `docs/design/*.md` under the allowed roots. That rules out path traversal, symlink escape, `.env*`, files under `.claude/protected-paths`, FIFOs and devices.
- Must not: execute or inject markup from a doc. No `innerHTML`, `insertAdjacentHTML`, `javascript:`/`data:` URLs or remote images.
- Must not: show doc content to unpaired browsers, or return absolute paths (the API uses paths relative to `WS`).
- Must not: slow down or break a Claude session. The hook stays async, has a size cap, never raises, and always exits 0.
- Must not: change `/api/state`, `/api/stream`, the Agent interactions panel or the CSP.

## Options considered
**Rendering**
1. **Vendored client library (marked + DOMPurify, about 70 KB, pinned).** Pros: complete CommonMark/GFM. Cons: it needs `innerHTML`, which goes against the repo-wide no-innerHTML rule and the smoke trap. It also means vendoring and updating third-party code, and the diff would need a JS diff library as well.
2. **Python `markdown` / `mistune` on the server, sanitised to HTML.** Cons: the repo is stdlib-only (`niu_events.py:1`), and the browser would still need `innerHTML`.
3. **A stdlib subset parser on the server that returns a JSON block tree, drawn with `h()`.** Pros: it is XSS-safe by construction (no HTML string exists anywhere), it is testable in Python, and it reuses the block tree for the rendered diff with `difflib`. Cons: it covers only a subset of markdown (about 350 lines). The designer template uses only that subset.

**Chosen: 3**, because it keeps the existing security model (no innerHTML, CSP unchanged) and gives the diff a structure to work on.

**Revisions**
1. **Git only.** Cons: it misses the untracked approval-loop rewrites, the main use case.
2. **Snapshots taken by the dashboard** (poll mtimes). Cons: it only works while `niu-dash` runs, and it misses quick successive rewrites.
3. **Git + working copy + hook snapshots in `events.db`.** Pros: it captures every agent write wherever it happens, and the store is outside the agents' reach (`block-env.sh:24`). Cons: one more table, and a small hook change.

**Chosen: 3**, because it is the only option that sees the drafts. It costs about 30 lines in the hook.

## Design

### A. Doc index and safe reads (`lib/niu_designs.py`, new, stdlib only)
```python
NAME_RE  = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.md")
MAX_BYTES = 512 * 1024
MAX_DOCS  = 400
def roots(extra_dirs=()) -> list[tuple[str, str, Path]]   # (domain, project, project_dir)
def index(extra_dirs=(), base=WS) -> dict[str, dict]      # docId -> {id, domain, project, name, title, status, mtime, rel, project_dir}
def read_doc(entry) -> str | None                          # re-validates, O_NOFOLLOW, size cap
def doc_id(rel: str) -> str                                # "d" + sha256(rel)[:16]
```
- **Roots.**
  - `(workspace)` / `SHARED/docs/design`.
  - For each non-dot domain directory under `WS`, every `**/.claude/settings.local.json` at depth ≤ 7, skipping `node_modules`. This is the same walk as `niu_prs.discover`, so factor it into `niu_designs.linked_projects()` and have `niu_prs` call it.
  - `extra_dirs` are session `project_dir`s from `live()`, accepted only if `under_ws()`.
- **Files.** `os.scandir(project_dir/"docs"/"design")` without recursion. A file is kept only if:
  - its name matches `NAME_RE` and doesn't start with `.env`;
  - `entry.is_file(follow_symlinks=False)` is true, or it is a symlink whose `resolve()` parent equals the resolved `docs/design`;
  - the resolved `docs/design` lies inside the resolved `project_dir`, which lies inside `WS`;
  - its size is ≤ `MAX_BYTES`;
  - `guard_allows(path, project_dir)` passes. The result is cached by `(path, mtime)` because it costs a subprocess per file.
- **Title and status.** The title is the first `# ` line and the status is the `Status: <word>` token (`proposed|approved|implemented|superseded`, anything else is shown as given, up to 20 characters), both read from the first 4 KB.
- **Cache.** The index is rebuilt at most every 30 s, or once on an unknown `docId`.
- **Reads.**
  - `os.open(path, O_RDONLY|O_NOFOLLOW|O_NONBLOCK)` on the resolved path, then `fstat` must show `S_ISREG` and size ≤ `MAX_BYTES`.
  - Decode as UTF-8 with `errors="replace"`, and strip `\p{C}` except `\n` and `\t`.
  - The guard check is repeated, so a file that became protected after indexing is refused.
- **API fields.** `rel` is relative to `WS` (`HOBBY/games/voxel-mod/docs/design/x.md`). `project_dir` never leaves the server.

### B. Revisions (`niu_designs.revisions(entry)`, `niu_designs.revision_text(entry, rev)`)
Revision tokens are validated with `RE_REV = r"work|git:[0-9a-f]{40}|snap:[0-9]{1,12}"`, and a token is accepted only if it appears in `revisions(entry)`.

| Token | Source | Label |
|---|---|---|
| `work` | the file on disk | "Working copy" plus "uncommitted" / "untracked" when it differs from HEAD |
| `snap:<id>` | `design_snapshots` rows for this `rel` | "Draft 14:02 · designer" (agent type, or "main session") |
| `git:<sha>` | `_git(docsdir, "log", "--follow", "-n", "20", "--format=%H%x09%ct%x09%s", "--name-only", "--", name)` | "a1b2c3d · 2026-11-12 · subject" (subject through `redact()`, at most 80 characters) |

- **Git content.** `git -C <docsdir> show <sha>:<repo-relative name from --name-only>` (this follows renames). The output is capped at `MAX_BYTES`, and the `-c` flags are the same as `niu_ollama._git`.
  - A repo-relative name must end in `.md` and contain `docs/design/`, otherwise that revision is dropped.
  - Docs outside a git repo simply have no `git:` revisions.
- **Order and dedup.** Revisions are sorted by time, newest first. Each content is hashed with sha256, and equal contents collapse to one entry. The preferred label is `git:` over `work` over `snap:`, and the dropped labels appear as `also: …`.
- **Defaults in the UI.** "Compare" preselects `b = newest` and `a = the previous distinct revision`.

### C. Draft snapshots (`hooks/agent-events.py`, `lib/niu_events.py`)
- **Schema**, added to `niu_events.SCHEMA`:
  ```sql
  CREATE TABLE IF NOT EXISTS design_snapshots (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, rel TEXT NOT NULL, sha TEXT NOT NULL,
    session_id TEXT, agent_type TEXT, tool TEXT, content TEXT NOT NULL);
  CREATE INDEX IF NOT EXISTS design_snapshots_rel ON design_snapshots(rel, ts);
  ```
- **Capture.** In `main()`, after the event insert, when `event == "PostToolUse"` and `tool in {"Write", "Edit", "MultiEdit"}`, call `niu_events.snapshot_design(con, file_path, project_dir, session_id, agent_type, tool)`. It:
  - resolves `file_path` and requires `parent.name == "design"`, `parent.parent.name == "docs"`, a file name matching `NAME_RE`, and a path under `WS`;
  - reads the file with the same `O_NOFOLLOW` / `S_ISREG` / 512 KB rules (the guard has already allowed this Write in `PreToolUse`, so no subprocess is needed);
  - skips the snapshot when `sha` equals the newest stored `sha` for that `rel`;
  - inserts the row, then keeps the newest **40 per `rel`** and deletes rows older than `NIU_DESIGN_SNAPSHOT_DAYS` (default **30**; `0` turns capture off).
  
  The content is the file as written: no redaction, since the design rules already forbid secrets in docs. The snapshot is stored only on this machine, in a database agents can't read.
- **Failure.** Every exception is swallowed (the existing `try` at `:145-151`). A slow or locked database delays only the async hook.

### D. Markdown → block tree (`niu_designs.parse(text)`)
- **Blocks.**
  - `{"t":"h","l":1-6,"id":slug,"c":inl}`
  - `{"t":"p","c":inl}`
  - `{"t":"ul"|"ol","start":n,"items":[[blocks]]}`, nested by indentation, with lazy continuation lines
  - `{"t":"table","align":["l"|"c"|"r"|""],"head":[inl],"rows":[[inl]]}`, where `\|` and pipes inside backticks don't split cells
  - `{"t":"code","lang":str≤20,"text":str}`, for fenced ``` and ~~~ blocks
  - `{"t":"quote","c":[blocks]}`
  - `{"t":"hr"}`
- **Inlines.** A plain string, or one of:
  - `{"t":"code","text"}`, `{"t":"b"|"i"|"s","c":inl}`, `{"t":"br"}`
  - `{"t":"a","href":str,"c":inl}`, or `{"t":"a","doc":docId,"frag":str,"c":inl}` for a sibling `*.md` link
  - `{"t":"img","alt":str}`, rendered as `[image: alt]`
- **Parser.** The inline parser is a left-to-right scanner with a delimiter stack, not nested regexes, so its time is linear and it can't backtrack catastrophically.
- **Limits.** Nesting depth ≤ 8, ≤ 5,000 blocks, ≤ 200 table rows × 20 columns. Anything beyond is cut and ends with `{"t":"p","c":["… (truncated)"]}`.
- **Allowed hrefs.** `^https?://` (shown with a `↗` mark, `rel="noopener noreferrer"`, `target="_blank"`), `^#[A-Za-z0-9_-]{1,80}$`, and a relative `NAME_RE` that resolves to a doc in the same `docs/design` (it becomes `doc`). Any other href becomes plain text.
- **Raw HTML** (`<div>`, `<script>`, `<!-- -->`) stays literal text in the string nodes.
- **Heading ids.** Slugs are lowercased and limited to `[a-z0-9-]`, then deduplicated with `-2`, `-3`.

### E. Diff (`niu_designs.diff(a_text, b_text)`)
- **`lines`.** `difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=False)` → hunks of `[{op: "eq"|"del"|"ins", a: n|null, b: n|null, text}]` with 3 lines of context. Long equal runs collapse into `{op: "skip", n}`. The output is capped at 4,000 lines.
- **`blocks`.** Both texts are parsed, and the top-level blocks are keyed by `json.dumps(block)`, then run through `SequenceMatcher`.
  - `equal` → `{op:"eq", block}`
  - `insert` / `delete` → `{op:"ins"|"del", block}`
  - `replace` → pairs the blocks of the same type in order:
    - `p`/`h` → `{op:"chg", block: new, words: [[op, text]…]}` (a word diff of the plain text, shown instead of the inline formatting for that block)
    - `table` with the same column count → `{op:"chg", block: new, rows: [op per row], old_rows: [deleted rows]}`
    - `ul`/`ol` → the same at item level
    - anything else → `del` old + `ins` new
- **`stats`.** `{added, removed, sections: [heading text of the nearest preceding h1-h3 for each changed block, unique]}`.
- **Cost.** At most 2 × 512 KB of input. `SequenceMatcher` with `autojunk=False` is quadratic in the worst case, so when either side has more than 5,000 lines the diff falls back to `difflib.unified_diff` (`lines` only, `blocks: null`, note "too large for the rendered diff").

### F. HTTP routes (`bin/niu-dash`, in `do_GET`, all GET, all **paired only**)
| Route | Answer |
|---|---|
| `GET /api/designs[?sid=ID]` | `{docs: [{id, domain, project, name, title, status, mtime, rel, revisions: n}], session_docs: [id…]}`. With `sid` (checked with `RE_SID`), `session_docs` lists the docs that session wrote: events with `session_id = sid`, `tool ∈ EDIT_TOOLS`, `summary` matching `(^|/)docs/design/[^/]+\.md$`, resolved against the session's `project_dir` and matched to the index. An unknown sid gives `session_docs: []`. |
| `GET /api/designs/doc?id=&rev=` | `{id, title, status, rel, rev, revisions: [{rev, ts, label, kind, also}], blocks, outline: [{l, id, text}]}`. `rev` defaults to `work`. |
| `GET /api/designs/diff?id=&a=&b=` | `{id, a, b, stats, lines, blocks}` |

- **Status codes.** Unpaired → 401 `{state: "not_paired"}`. A bad `id`/`rev` format → 400. An unknown id or rev → 404. A file that fails a read check → 404 `{error: "unavailable"}` (no detail, so the response doesn't reveal which check failed).
- **Size.** Responses go through `_json`, so the CSP and headers are unchanged. A response is at most about 2 MB. Bigger docs are already refused by `MAX_BYTES`.
- **`ids`.** `id` must match `^d[0-9a-f]{16}$` and is never joined to a path.

### G. Front end (`web/app.js` new section `// ---------- design docs ----------`, `web/index.html`, `web/style.css`)
- **Tab.** `<button id="tab-designs">Designs</button>`. `setView` accepts `"designs"`, and the hash is `#designs` or `#designs/<id>[/<rev>|/diff/<a>/<b>]`. `.mcgrid[data-view="designs"]` shows `.p-designs` (the left column stays) and hides the graph, timeline, feed, PRs and usage panels, the same pattern as research at `style.css:468-473`.
- **Panel `.p-designs`.**
  - **List pane (`#dslist`):** a search box (filters the title and file name on the client), grouped by `domain / project`, with a status badge (`badge b-ok` for approved/implemented, `b-warn` for proposed) and the relative time.
  - **Doc pane (`#dsdoc`):** a header with the title, `rel`, a revision `<select id="dsrev">`, a **Compare** button and a segmented control **Rendered | Lines** (shown in compare mode). On wide screens a sticky outline from `outline` sits on the right.
- **Renderer.** `dsBlocks(blocks) → Node[]` and `dsInl(inl) → Node[]` use only `h()` and `document.createTextNode`.
  - `a` nodes: `setAttribute("href", …)` only after `/^(https?:\/\/|#)/.test(href)` (the check is repeated in the browser). `doc` links call `dsOpen(docId, frag)`.
  - Tables are wrapped in `.dstablewrap` (horizontal scroll). Code goes in `pre > code` with no highlighting.
- **Diff view.**
  - **Rendered:** `.dsins` (green left bar and tint), `.dsdel` (red, struck through, collapsed under "show removed" when longer than 6 lines), and `.dschg` with word spans `ins`/`del`.
  - **Lines:** a two-column gutter (old/new line numbers) with `+`/`−` rows.
  - A summary chip "+12 −4 · Design, Changes" sits above.
  - Keyboard: `n` / `p` jump to the next or previous change.
- **Unpaired.** "Pair this browser to read design docs (menu → This browser)." with a button that opens the menu (`toggleMenu(true)`).
- **Session tie-in.**
  - `showDetails` fetches `/api/designs?sid=` once per open (cached 30 s, failures ignored; unpaired gets nothing). If `session_docs` isn't empty, `#amhead` gets `h("a", "dslink", "Design: <title>")` (`href = #designs/<id>`), one per doc, at most 3.
  - In `amNode` (or a click handler on `a:designer`), clicking the designer node while the session has docs opens the newest. Its `data-tip` gets the line "click to open the design".
- **Live updates.** No SSE change. While the Designs tab shows the working copy, `render()` (called on every push) compares `STATE.feed` for a newer Write/Edit on the open `rel` and refetches the doc, at most once every 5 s. It keeps the scroll position and shows the chip "updated 14:05".

### H. Failure modes
| Situation | Behaviour |
|---|---|
| Not paired | The list pane shows the pairing hint. No request returns content. |
| Doc deleted or now protected | 404 "unavailable". The list drops it on the next index rebuild, and the open view says "This doc is no longer available." |
| Not a git repo / git missing / timeout (30 s cap, `_git`) | Only the `work` and `snap:` revisions. The revision list says "git history unavailable". |
| Snapshot table missing (an old db) | `CREATE TABLE IF NOT EXISTS` on the next `connect()`. Until then the list has no drafts. |
| Doc over 512 KB, or a malformed table | Refused (over the size) / rendered as a paragraph (malformed). Never a 500. |

## Changes
| File | Change |
|---|---|
| `.claude-workspace/lib/niu_designs.py` (new) | `NAME_RE`, `MAX_BYTES`, `linked_projects()`, `roots()`, `index()` (30 s cache), `doc_id()`, `read_doc()`, `revisions()`, `revision_text()`, `parse()` (block tree), `outline()`, `diff()`, `session_docs(con, sid, project_dir, idx)`. |
| `.claude-workspace/lib/niu_events.py` | `design_snapshots` table and index in `SCHEMA`. `snapshot_design(con, file_path, project_dir, sid, agent_type, tool)` with dedup, cap 40 per rel, `NIU_DESIGN_SNAPSHOT_DAYS`. |
| `.claude-workspace/lib/niu_prs.py` | `discover()` uses `niu_designs.linked_projects()` (same output). |
| `.claude-workspace/hooks/agent-events.py` | After the insert: on `PostToolUse` of Write/Edit/MultiEdit, call `snapshot_design(...)` inside its own `try`. |
| `.claude-workspace/bin/niu-dash` | Routes `/api/designs`, `/api/designs/doc`, `/api/designs/diff` (paired only, `RE_DOC`, `RE_REV`, 400/401/404). Docstring lines. `import niu_designs`. |
| `.claude-workspace/web/index.html` | The `tab-designs` button. A `<section class="panel p-designs">` with `#dslist`, `#dsdoc`, `#dsoutline`. |
| `.claude-workspace/web/app.js` | `setView` and hash handling for `designs`. New section `// ---------- design docs ----------`: `DS` state, `loadDesigns()`, `dsOpen(id, rev)`, `dsCompare(a, b)`, `dsBlocks`, `dsInl`, `dsDiffBlocks`, `dsDiffLines`, `dsKey`. `showDetails` adds the design links. The designer node gets a click and tip. `render()` gets the working-copy refresh hook. |
| `.claude-workspace/web/style.css` | `.mcgrid[data-view="designs"]` layout. `.p-designs`, `.dsmd` typography (headings, tables, `pre`, `blockquote`, `code`) using the theme variables. `.dsins`, `.dsdel`, `.dschg ins/del`, `.dsline`, `.dslink`. Dark-theme checks. |
| `.claude-workspace/tests/designs_test.py` (new), `.claude-workspace/tests/designs.test.sh` (new) | Unit tests, see the test plan. |
| `.claude-workspace/tests/agent-events.test.sh` | Snapshot capture cases. |
| `.claude-workspace/tests/web_test.py`, `.claude-workspace/tests/web_smoke.js` | Route and pairing checks; the front-end smoke test with the innerHTML trap. |
| `.claude-workspace/README.md` (or the dashboard section of the docs) | A short "Designs tab" paragraph (hand it to `doc-writer`). |

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| Path traversal or a symlink escape exposes another file | The browser sends only an opaque id from a server-built index. The path is validated at index time and again on read (resolved parent equality, `O_NOFOLLOW`, `S_ISREG`). `guard_allows()` covers `.env*` and `protected-paths`. Each case has a test. |
| XSS through doc content or links | No HTML strings exist at any point (JSON tree → `h()`/`textContent`). The href allowlist is enforced on the server and again in the browser. The smoke innerHTML trap and the source scan cover the section. The CSP is unchanged. |
| WORK design docs visible on the tailnet | Every route is paired-only. Open question 1 offers a domain opt-out. |
| Parser gaps make docs look wrong | The subset matches the designer template. A golden test renders every doc in `.claude-workspace/docs/design/` and asserts no `truncated` marker and the expected counts of headings and tables. Unknown syntax falls back to a literal paragraph. |
| The hook slows sessions | It is async, one extra small file read (≤ 512 KB) and one insert, only for design-doc writes. A test times 100 non-design events and checks there is no snapshot work. |
| Snapshot growth | 40 per doc, 30 days, deduplicated by sha. About 1 MB for a busy month. |
| Diff CPU on huge docs | The size cap, plus the fallback to `unified_diff` above 5,000 lines. |
| Conflict with the multi-machine design | No change to `/api/state`, `/api/stream` or `to_json`. Only new routes and a new tab. Whichever lands second rebases `setView`/`index.html` tabs trivially. |

## Test plan
- **Unit, `tests/designs_test.py`** (run by `bash .claude-workspace/tests/designs.test.sh`). It uses temp roots and `check()` asserts, in the style of `catalog_test.py`.
  - **Index:** finds docs in the shared directory, in linked projects and in extra dirs. It skips `../x.md`, `.env.md`, `notes.txt`, `sub/dir/x.md`, a FIFO, a 600 KB file, a symlink to `/etc/passwd`, a symlink to `../../secret.md`, and a file listed in `.claude/protected-paths` (`guard_allows` is false). `doc_id` is stable, and the response has no absolute path.
  - **Parse:**
    - headings with deduplicated slugs, nested lists (3 levels), a table with `\|` and a backtick pipe, alignment, fenced code with a language;
    - `<script>alert(1)</script>` stays a literal string;
    - `[x](javascript:alert(1))`, `[x](data:…)` and `[x](//evil)` become plain text, `[x](other.md#sec)` becomes `doc` + `frag`, and `![a](http://x/y.png)` becomes `img` alt only;
    - 10,000 nested `*` / `[` characters parse in under 1 s (linear);
    - a golden pass over every real doc in `.claude-workspace/docs/design/`.
  - **Revisions:** in a temp git repo, 2 commits + a modified working copy + 2 snapshots (one equal to HEAD) give 4 distinct entries, with the labels deduplicated. A rename followed with `--follow` shows the old content. Not a repo → only `work`/`snap`. A bad token is rejected.
  - **Diff:** a changed paragraph → `chg` with word ops; one changed table cell → table `chg` with one `del` row + one `ins` row; an added section → `ins` blocks and `stats.sections` contains it; 6,000 lines → `blocks: null`.
  - **Snapshots:** `snapshot_design` stores the first write, skips an identical second write, caps at 40, prunes by age, and ignores `docs/design/sub/x.md`, `docs/x.md` and a path outside `WS`. `NIU_DESIGN_SNAPSHOT_DAYS=0` disables it.
- **Hook, `tests/agent-events.test.sh`:** feed a `PostToolUse` Write JSON for a temp project's `docs/design/a.md` → one `design_snapshots` row. A Bash event → none. Malformed input → exit 0.
- **Integration, `tests/web_test.py`:** seed a linked fixture project under `PERSONAL/.niu-test-web/app` with `docs/design/t.md` and a protected `docs/design/secret.md`.
  - Unpaired: `/api/designs` → 401, and a forged CSRF header is still 401.
  - Paired: the list includes `t.md` but not `secret.md`; `/api/designs/doc?id=` → blocks; `id=../x` → 400; an unknown id → 404; `rev=git:zzz` → 400; `?sid=sess-web1` after a seeded Write event lists `t.md`.
  - Diff between `work` and a seeded snapshot → 200 with `stats`.
  - The body contains neither `str(WS)` nor `/home/`. The CSP header is unchanged.
- **Front end, `tests/web_smoke.js`:**
  - Render the fixture blocks: the nodes are created and an `a` with an injected `javascript:` href has no `href`.
  - The innerHTML trap is on for the section, plus the source scan of `// ---------- design docs` for `innerHTML|insertAdjacentHTML|outerHTML|document.write|eval(|new Function`.
  - The diff view adds the `dsins`/`dsdel` classes. `setView("designs")` sets `data-view`. `showDetails` adds a `dslink` when the stub returns `session_docs`.
  - Output line "design docs ok".
- **Commands:** `bash .claude-workspace/tests/designs.test.sh`, `bash .claude-workspace/tests/agent-events.test.sh`, `bash .claude-workspace/tests/web.test.sh`, `bash .claude-workspace/tests/prs.test.sh` (the discover refactor), and `bash .claude-workspace/tests/catalog.test.sh` (unchanged).
- **Manual:**
  - Run `/develop` on a toy task, answer "Change it" once, open Designs → Compare: the drafts appear, and the rendered diff shows the changed sections.
  - Open this doc and the session-agent-map doc: their tables and code render.
  - Check the dark theme, a narrow screen (the outline hides), and an unpaired browser (hint only).

## Rollout
1. One niu-heimar PR from a feature branch. The description has `Reviews: deferred to the feature release`. Because `.claude-workspace/` is edited, offer `/workspace-sync` (CLAUDE.md §6). The security reviewer at release should look at `niu_designs.read_doc`, the href allowlist and the hook change.
2. No migration step: the table is created by `CREATE TABLE IF NOT EXISTS`. The hook change takes effect in new hook runs, with no `install` needed because the settings already point at the script. Restart `niu-dash serve` for the routes.
3. To turn off draft capture, set `NIU_DESIGN_SNAPSHOT_DAYS=0`.
4. Rollback: revert the PR. The leftover `design_snapshots` table is harmless; `DROP TABLE design_snapshots` removes it.

## Open questions
1. **WORK docs.** Show WORK design docs to paired browsers (default **yes**: the same machine and the same user, paired only), or hide WORK unless `NIU_DESIGNS_DOMAINS` includes it?
2. **Pre-edit capture.** Also snapshot the current file on `PreToolUse` Edit/Write when that doc has no snapshot and is untracked, so the first draft written before this feature (or by Bash) is kept? Default **no**: the async `PreToolUse` hook can race the write, so the capture would be best-effort only.
3. **Bash edits.** Capture design-doc writes done through `Bash` by having the dashboard snapshot on mtime change while it runs? Default **no**: the working copy already shows the latest text.
4. **Plain-markdown view.** Add a "Source" toggle (the raw text in a `pre`) next to Rendered? It is cheap (`rev` text is already on the server). Default **yes**, if it fits the PR.
5. **Multi-machine.** Once the asgard exporter exists, should asgard's design docs be listed (that needs content export, which the current multi-machine design forbids)? Default **out of scope**; this feature is machine-local.
6. **Snapshot retention.** 30 days / 40 per doc. Is that enough, or should a doc's drafts be kept until it is committed?
