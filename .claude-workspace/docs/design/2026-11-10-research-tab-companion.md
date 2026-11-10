# Research tab: companion, explore and a day schedule (Status), plus a Topics view that scales to 300 topics
Status: proposed · Date: 2026-11-10 · Request: "write design to update niu-heimar Research tab to reflect the new redesign feature of valkyrie-dispatch. I think need to change how topics are shown in case there are many topics curated already."

Scope: niu-heimar (`/home/norman/WORKSPACE`, config under `.claude-workspace/`), one PR. It also needs one small, additive valkyrie-dispatch PR (`PERSONAL/valkyrie-dispatch`). The niu-heimar side works without it and shows less. This document is self-contained. Line numbers are from `main` on 2026-11-10, with the `explore` entry in `KINDS` (`lib/niu_research.py:56`) treated as landed.

## Decision summary (read this first)
| # | Topic | Decision |
|---|---|---|
| 1 | Status: companion and explore | Move them out of the header tooltips (`web/app.js:1740-1754`) into two new **cards**, `#rscomp` "Companion (#hermes)" and `#rsexplore` "Explore", next to Gate and Quota. Each value is an enum mapped to a fixed label, or a number. The header keeps two short chips. `shape()` already allowlists both blocks (`lib/niu_research.py:459-497`), so this is mostly UI. |
| 2 | Status: explore runs | Add an **`explore` lane** to the 36 h lanes graph that draws run-level bars, because explore runs have no agent rows. `rsRun` shows a one-line explore summary instead of an empty agent table. The tab badge is computed from the newest *research-kind* run, so an explore run can no longer hide a failed overnight run. An explore failure is amber, never red. |
| 3 | Status: day schedule | A new **"Next 24 h" strip** (`#rssched`) above the lanes, with one marker per job: overnight, brief, explore, delta, reflection. Weekly discovery and check-ins appear as a text line. Markers come from a new additive **`schedule.jobs`** list in monitor.v1 (job enum, `HH:MM`, weekday enum, enabled, `next_at`). Without that list the strip falls back to what monitor.v1 already has (research hours from `cron`, `next_slot_*`, `next_brief_at`) and says that the other times need a newer valkyrie-dispatch. |
| 4 | Topics: data | **Effective weight, run rank and "in the run" come from valkyrie-dispatch**, as additive fields on the existing topics routes (topics scope). They are not computed in the browser (reasons in contradiction 2). Origin, trial, 30-day likes/dislikes and active-note count come the same way, as enums, timestamps and counts. niu-dash pages through the list (`limit=100`, `offset`), so 300 or more topics load without the 200-row truncation. |
| 5 | Topics: UI | A compact table-like list grouped by agent (collapsible headers with counts), plus filters: agent, state, run (in the run / below the cut-off), origin, trial. Sorts: run order (default), base weight, name, last updated, newest. Search stays the existing fuzzy matcher. Each group renders **50 rows at a time** ("Show 50 more"), with no virtual scrolling. A **cut-off divider** sits after the last topic in the run in run order, and every active row carries a `#rank` / "below cut-off" badge. Keyboard: `/` focuses search, arrow keys move between rows, Enter opens Edit. Create, edit, toggle, archive and restore, with `If-Match`, are unchanged. After any write the list is re-fetched, because ranks move. |
| 6 | Data boundaries | niu-dash keeps exactly two tokens, `monitor` and `topics`. Nothing per topic goes into monitor.v1: `/api/research` is served to **unpaired** viewers (`bin/niu-dash:266-267`), while `/api/topics` needs pairing (`:268-272`). Proposal, note and explore text stays in Discord. The dashboard shows counts only and has **no #hermes link**, because a link needs guild and channel ids that monitor.v1 must not carry and niu-dash has no config for. |
| 7 | Security | No change to the transport or token handling. Every new field is rebuilt from an allowlist (enum, regex, clamp) in `shape()` / `shape_topic()`. The UI builds everything with `h()` / `svg()` and `textContent`. The existing innerHTML trap in `tests/web_smoke.js:22-23` covers the Topics section, and this design extends it to the new Status cards. |
| 8 | Rollout | **niu-heimar first, valkyrie-dispatch second.** niu-heimar must already accept the new fields, or `research_test.py`'s schema-sync check fails as soon as the local valkyrie-dispatch checkout has the new schema. Both sides degrade cleanly in either order. |

**Where the code contradicts the request (adjusted here, please confirm):**
1. **Companion and explore are already partly shown.** `shape()` allowlists both blocks (`lib/niu_research.py:459-497`), and `rsHead` shows them as two header chips whose details sit in tooltips (`web/app.js:1740-1754`). This design turns them into visible cards. It does not add new plumbing.
2. **Effective weight cannot be computed in the browser.** `TopicOut` has no `weight_updated_at` (`src/research_dispatch/api/schemas.py:117-128`). The half-life is valkyrie-dispatch's `WEIGHT_HALF_LIFE_DAYS` setting (`core/settings.py:38`, default 30), which niu-dash cannot see; it must not read the env file. The researched cut-off also breaks ties on `name.casefold()` and then the id (`prompts/builder.py:245`). Many topics sit at exactly 1.0, so ties are common. Python's casefold and code-point order cannot be reproduced in JS on the display-shaped name (`niu_topics._show` replaces and truncates characters). So the server sends `effective_weight`, `run_rank` and `in_run`.
3. **"Researched" means two things.** valkyrie-dispatch's CLAUDE.md says "researched" means `is_active` alone (runs, feedback, cap). The top-20 rule is the prompt builder's cut: `MAX_TOPICS = 20` (`prompts/allocation.py:8`), applied per agent over active topics (`orchestrator/run.py:355`, `prompts/builder.py:237-246`). The UI keeps "Active" for the state and uses **"in the run (top 20)"** / **"below the cut-off"** for the rank. The midday delta scout looks only at the top 6 of those (`SCOUT_MAX_TOPICS`, `builder.py:28`); a tooltip says so.
4. **The weight shown today is the stored base weight, not the decayed one.** The pipeline design says `GET /topics` returns the effective weight (`docs/design/2026-11-06-research-dispatch-pipeline.md:244`), but `TopicOut.weight` serialises the column. The UI labels the two clearly: "1.8 (base 2.4)".
5. **Origin is not a topic column.** It can only be derived from `topic_proposals` (`op='add'`, `status='applied'`, `applied_topic_id`). Decided proposals are purged after 90 days, so origin becomes `null` ("added directly, or more than 90 days ago"). That also covers topics made in niu-dash, the CLI or the API.
6. **Explore runs are invisible in the lanes and can mask failures.** Explore runs have no `agent_runs` rows, so the lanes graph, which draws one bar per agent (`web/app.js:1923-1941`), never shows them, and `rsRun` renders an empty table. `last_run` is the newest finished run of *any* kind (`monitor/build.py:342-349`). A succeeded 10:30 explore run therefore replaces a failed 02:00 overnight run in the badge logic (`web/app.js:1691-1698`).
7. **The Topics list truncates at 200 inactive and 200 archived topics across all agents** (`lib/niu_topics.py:563-577`). With 300 topics, the inactive list can be cut. This design pages through instead. The API allows `limit` up to 500 (`api/routers/topics.py:107`), but at 500 the 1 MiB body cap (`niu_topics.MAX_LIST_BODY`) can be hit by long CLI-created rows. Pages of 100 stay under about 650 KiB in the worst case.
8. **monitor.v1 has no times for explore, reflection or discovery.** `Schedule` (`monitor/models.py:122-134`) only has `cron` (the research hours, `"0 2,12 * * *"`, from `Settings.cron_compat`, `core/settings.py:566-573`), `next_slot_*`, `brief_at` and `next_brief_at`. The strip therefore needs the additive `schedule.jobs` field.
9. **A stale message.** `TP_MSG.changed` still names duenext (`web/app.js:2075`). DueNext is no longer a consumer. The new text is "This topic changed elsewhere (CLI, Discord or feedback). Reload it?".
10. **The companion design says the `topics` scope is "unchanged"** (valkyrie-dispatch `docs/design/2026-11-10-companion-hermes-style.md:11`). This design adds response fields to the existing topic routes. It adds no routes and no capabilities, so `tests/api/test_route_scopes.py` is untouched. Still, it is a contract change the valkyrie-dispatch owner should approve (Open question 1).

## Context
- **Status view today.** `renderResearch` (`web/app.js:1674-1682`) draws the head (`rsHead`), Gate, Quota, the 36 h lanes, the gate log, the last runs and errors. Everything is built with `h()` / `svg()` and `textContent` (`:1651-1657`). The data is `/api/research`, which `niu_research.summary()` serves (poller, `shape()` allowlist, monitor token only, `whoami` must say `monitor`, `:285-302`). That route needs no pairing.
- **Topics view today.** `renderTopics` (`web/app.js:2285-2346`) shows agent chips with `n/200`, a fuzzy search (`tpMatch`, `:2202-2219`), a "Show archived" checkbox, and one group per agent sorted active → inactive → archived. Rows render in full, with no paging, in the server's order (weight DESC, name: `db/repositories/topics.py:245-247`). Writes go through `POST /api/topics/{create,update}` (`bin/niu-dash:333-343`). Those need a paired device cookie and `_post_ok()` (`:328`, the existing origin / CSRF header check), then `niu_topics.write()` with `If-Match` = the row's `updated_at` (`lib/niu_topics.py:621-679`). The backend uses a topics-scope token only, checked through `whoami` with a feature list (`check_scope`, `:219-244`; valkyrie-dispatch `api/routers/auth.py:16` `FEATURES = ["topic_states"]`).
- **What valkyrie-dispatch added (H1-H5).** The #hermes bot with brief posting, feedback selects, Ollama chat and proposals applied only on the owner's Apply. Nightly reflection into preference notes. Weekly discovery and trial or stale check-ins. Daily explore through SearXNG → Ollama → Haiku. Run kinds: comprehensive, delta, standard, explore. Default times: comprehensive 02:00, brief 08:00, explore 10:30, delta 12:00, reflection 21:00, discovery Sun 10:00, each from settings (`core/settings.py:64-103`). Topics can now be created from Discord proposals, discovery and explore, so the list grows.
- **Hard rules this design keeps.** monitor.v1 holds only numbers, enums, timestamps, model ids and slot keys (valkyrie-dispatch CLAUDE.md "Monitor endpoint"). niu-dash re-shapes it through an allowlist and never passes free text through. The `topics` scope never gets a non-topic route (`tests/api/test_route_scopes.py:118-121`). niu-dash never holds a `companion`, `digest`, `read` or `admin` token.

## Requirements
- Must: show the companion and explore blocks as readable cards. Show explore runs in current and recent runs and in the lanes. Show a day-schedule strip of all jobs.
- Must: make 50-300 topics usable. That means search, filters (agent, state, run cut-off, origin, trial), sorts (run order, weight, name, last updated), agent grouping with counts, compact density, paging, keyboard use and a clear top-20 cut-off per agent.
- Must: keep create, edit, toggle, archive and restore, with `If-Match`, 409 duplicate, 412 reload and 429 rate limit, working exactly as today.
- Must: degrade cleanly against a valkyrie-dispatch without the new fields, and against one that is down. Today's stale-list behaviour stays.
- Must not: add a token or scope to niu-dash. Put per-topic data, free text, URLs or Discord ids into monitor.v1 or `/api/research`. Pass any upstream string through without an allowlist. Use `innerHTML` (or similar) with server data. Add any route that starts a run. Read `.env`, token files, `research.json` or other protected paths.

## Options considered
1. **Effective weight in the browser** from `weight` + `weight_updated_at` + a half-life copied into `research.json`. Pros: no topics API change. Cons: duplicates a server setting that can drift; needs `weight_updated_at` from the API anyway; cannot reproduce the tie-break (contradiction 2), so the cut-off would sometimes be wrong. **Rejected.**
2. **Per-topic stats in monitor.v1** (a list of topic ids with counts). Pros: no topics-route change. Cons: `/api/research` is unpaired, so topic ids and per-topic activity would reach unpaired viewers; it breaks monitor.v1's "aggregates only" spirit and its 32 KiB cap (`monitor/models.py:20`). **Rejected.**
3. **Additive fields on the existing topics routes**, behind a whoami feature `topic_meta`, plus additive `schedule.jobs` in monitor.v1. Pros: one source of truth for the cut-off; paired-only data stays paired-only; no new route or scope. Cons: one small valkyrie-dispatch PR and a contract note. **Chosen.**

For the long list: **paged groups ("Show 50 more")** were chosen over virtual scrolling (rows vary in height, focus and screen-reader order break, and it is more code) and over server-side paging (the fuzzy search needs the full list in memory; 300 rows is small).

**Chosen:** option 3 with paged groups, because it is the smallest change that keeps the cut-off exactly right, respects the paired/unpaired split, and reuses the existing renderer, search and write flows.

## Design

### A. monitor.v1 additions consumed (shape side, `lib/niu_research.py`)
New constants and helpers next to the existing enums (`:50-67`):
```python
JOBS = frozenset("comprehensive delta brief explore reflect discovery checkins".split())
WEEKDAYS = frozenset("mon tue wed thu fri sat sun".split())

def _job(j):
    return {"job": _enum(j.get("job"), JOBS), "at": _re(j.get("at"), HHMM_RE), "weekday": _nenum(j.get("weekday"), WEEKDAYS),
            "enabled": _bool(j.get("enabled")), "next_at": _ts(j.get("next_at"))}
```
In `shape()` the schedule dict gains `"jobs": [_job(j) for j in _list(sch.get("jobs"), 8)] if isinstance(sch.get("jobs"), list) else None`. `None` means an older server, `[]` means no jobs. `_companion` gains `"last_discovery_at": _ts(c.get("last_discovery_at"))` (optional upstream field, see "Changes needed in valkyrie-dispatch" V1b). Nothing else in `shape()` changes. Unknown keys are still dropped, and unknown enums become `"unknown"`.

### B. Status view (`web/app.js` Research section, `web/index.html`, `web/style.css`)
- **Layout.** `.rsgrid` (`index.html:162-165`) gets two more cards, `<div id="rscomp" class="rscard">` and `<div id="rsexplore" class="rscard">`, and the grid becomes `grid-template-columns: repeat(auto-fit, minmax(240px, 1fr))`. A new `<div id="rssched" class="rssched">` goes between `.rsgrid` and `#rslanes`. `renderResearch` calls `rsComp(r)`, `rsExplore(r)` and `rsSched(r)` after `rsQuota`, and `tests/web_smoke.js`'s `RS_IDS` gains the three ids.
- **`rsComp`** (when `d.companion` is null: "Companion not available in this valkyrie-dispatch version."). Rows:
  - **Bot:** a pill from `RS_BOT` (up = ok, stale = warn, unknown = muted), plus "Ollama" from `RS_OLLAMA` (down = warn).
  - **Brief delivery:** `bot` → "posted by the bot", `webhook` → "posted by webhook".
  - **Feedback, last 7 days:** `feedback_7d`, with "(n from chat)".
  - **Waiting in #hermes:** `pending_proposals` proposals, `candidate_notes` notes to keep or drop. Amber when greater than 0.
  - **Active notes:** `active_notes`.
  - **Last reflection:** `rsWhen(last_reflect_at)` or "never". Amber "overdue" when a `reflect` job is enabled and `last_reflect_at` is more than 26 h old.
  - **Last discovery:** shown only if `last_discovery_at` is present.
  - Footer, muted, fixed text: "Proposals, notes and picks are reviewed in #hermes on Discord." No link.
- **`rsExplore`**. When `d.explore` is null: "Explore is off (EXPLORE_ENABLED) or not available in this version." Otherwise:
  - last run `rsWhen(last_run_at)` with a badge from `RS_EXPLORE` (succeeded/quiet = ok, skipped_gate = warn, failed/aborted_quota = bad) and `RS_ERROR[last_error]`;
  - a "Today" funnel line `queries_today queries → results_today results → items_today picks → posted_today posted`;
  - "7 days: n liked, n disliked";
  - "Topic ideas from picks, 30 days: `topic_proposals_30d`".
- **Header.** `rsHead` keeps two short chips, "companion up/stale" and "explore n posted today", with no long tooltips (the cards carry the detail).
- **`rsSched`: "Next 24 h" strip.** An SVG about 28 px tall spanning `rsNow()` to `rsNow() + 24 h`, with hour ticks every 3 h via `hm()`.
  - With `schedule.jobs` present: one marker per job whose `next_at` falls inside the window. Each marker is a letter tag from `RS_JOB_LETTER = {comprehensive: "C", delta: "D", brief: "B", explore: "E", reflect: "R", discovery: "W", checkins: "W"}`, with the tooltip "`label` · `at` (`timezone`) · in `dur`". A disabled job draws hollow and muted with "off". A line below lists jobs outside the window, for example "Discovery + check-ins: Sun 10:00 (in 4 d)". Discovery and check-ins share one line when they have the same `next_at`.
  - Without `schedule.jobs` (fallback): research markers come forward from `next_slot_at` using the `cron` hours (a forward twin of `rsSlots`, `app.js:1883-1897`; only the next one is labelled with `next_slot_kind`, the rest show as "research"), and the brief marker comes from `next_brief_at`. A muted note says: "Explore, reflection and discovery times need a newer valkyrie-dispatch."
  - All labels are fixed strings from maps. No server string is printed except values that already passed `_re` (`at`, `timezone`).
- **Lanes.** `rsLanes` (`app.js:1898-1953`) gets a fourth lane `{key: "explore", label: "explore"}` after `fintrend`. For every run in `rsRuns(d)` with `kind === "explore"` and a `started_at`, it draws one run-level bar `started_at → finished_at` (or `now` while running), with `RS_LEVEL[run.status]` and the tooltip "explore · status · times · slot". Agent bars of explore runs are not expected, and are skipped if present.
- **`rsRun`.** When `run.kind === "explore"` it shows the head line as today, then `h("div", "rsline muted", "Explore run: see the Explore card for today's counts.")` instead of the agent table. `rsDigest` is already null for explore (`monitor/build.py:232-233`).
- **`rsBadge`.** It first computes `lastResearch`: the newest of `recent_runs`, `last_run` and `current_run` (deduplicated by id, as in `rsRuns`) whose `kind !== "explore"`, by `finished_at || started_at`. The red and amber rules today run against `lastResearch` instead of `d.last_run`. New amber triggers: the newest explore run failed or aborted_quota; `companion.bot === "stale"`; `companion.ollama === "down"`. Explore never makes the badge red.
- **`rsErrors`.** It also lists explore runs with status `failed` or `aborted_quota` as `who: "explore"`. The label is `RS_ERROR[run.error_code]` when the full run model is available (`last_run` / `current_run`), else `RS_STATUS[run.status]`.

### C. Topics backend (`lib/niu_topics.py`)
- **Feature detection.** `FEATURES = frozenset({"topic_states", "topic_meta"})`. `check_scope()` returns `{"states": ..., "meta": "topic_meta" in feats}`.
- **Paging.** `list_topics()` keeps its query plan (per-agent active, then inactive, then archived; or the legacy plan), but each query runs as a page loop:
  ```python
  PAGE = 100; MAX_PAGES = 10          # 1000 rows per query at most
  for q in queries:
      for p in range(MAX_PAGES):
          status, doc = _call(cfg, token, "GET", BASE, query={**q, "limit": PAGE, "offset": p * PAGE}, max_body=MAX_LIST_BODY)
          ...; if len(doc) < PAGE: break
      else: truncated[q_key] = True
  ```
  Rows are deduplicated by `id`, first seen wins. Ordering is weight DESC, so a write between pages can shift a row; the next refresh fixes it. `inactive_truncated` / `archived_truncated` keep their meaning (now "more than 1000"). The active per-agent cap stays `PER_AGENT_LIMIT = 200` (the server's `MAX_ACTIVE_PER_AGENT`), so active needs at most two pages. `_query` already validates `limit` and `offset` ints (`:129`).
- **`shape_topic` meta.** The output gains one key, `meta`. It is `None` unless the backend has `topic_meta`; otherwise:
  ```python
  ORIGINS = frozenset(("chat", "discovery", "explore"))
  "meta": {"effective_weight": clamp_round(raw.get("effective_weight"), 0.0, 5.0, 2),   # None if not finite
           "run_rank": int 1..10000 or None (None unless is_active),
           "in_run": bool or None (forced None unless is_active; False if run_rank is None),
           "origin": raw origin if in ORIGINS else None,
           "trial_until": TS_RE-checked string or None,
           "created_at": TS_RE-checked or None, "weight_updated_at": TS_RE-checked or None,
           "likes_30d": int 0..10**6 or None, "dislikes_30d": same, "notes_active": int 0..1000 or None}
  ```
  Rows stay valid without meta: a missing or bad meta value becomes `None`, never a dropped row. The shaper takes `meta_supported` as an argument. `_shape_all` / `write()` pass the `check_scope` result, so a PATCH reply is shaped the same way as the list.
- **Envelope.** It gains `"meta_supported": bool`, cached in `good` like `states_supported`. `LIMITS` is unchanged. niu-dash never hard-codes the cut-off of 20; the UI counts `in_run === true` per agent.

### D. Topics view (`web/app.js` Topics section, `web/index.html` `#tpbody`, `web/style.css`)
- **State** (`TP`, `app.js:2058`). It gains `state: "current"|"active"|"inactive"|"archived"|"all"` (replaces `archived`), `run: "any"|"in"|"below"`, `origin: "any"|"chat"|"discovery"|"explore"|"none"`, `trial: false`, `sort: "run"|"weight"|"name"|"updated"|"newest"|"match"`, `density: "compact"|"comfortable"`, `shown: Map(agent → n)`, `collapsed: Set(agent)`. Filters, sort, density and collapsed groups persist in `store` (`niu.tp.view`); the search text does not. Any filter, sort or search change resets `shown`.
- **Toolbar** (`index.html:147-155`). The `tparch` checkbox is replaced by `<select id="tpstate">` (Current = active + inactive, the default and today's behaviour; Active; Inactive; Archived; All). New controls: `<select id="tprun">` (Any / In the run / Below the cut-off), `<select id="tporigin">` (Any / Chat / Discovery / Explore / Added directly), `<button id="tptrial" aria-pressed>` "On trial (n)", `<select id="tpsort">`, and `<button id="tpdensity" aria-pressed>`. When `meta_supported` is false, `tprun`, `tporigin` and `tptrial` are hidden, `run` falls back to `weight`, and one muted line says: "Update valkyrie-dispatch to see run order, the top-20 cut-off and where topics came from." The agent chips keep "General n/200" and add "· top k" from the meta counts.
- **Sort.**
  - `run`: active rows by `meta.run_rank` ascending, then inactive, then archived, each by name. Without meta it falls back to `weight`.
  - `weight`: base weight DESC. `name`: `localeCompare` with `sensitivity: "base"`. `updated`: `updated_at` DESC. `newest`: `meta.created_at` DESC (hidden without meta).
  - A non-empty search switches the sort to `match` (score, as today, `app.js:2322-2325`) unless the user picked a sort after typing. The select shows "Best match".
- **Grouping.** One group per agent, in `TP_AGENTS` order. The header is a `<button class="tpgroup" aria-expanded>` with fixed text built from counts: "General · 27 active · 20 in the run · 7 below · 4 inactive · 2 on trial". It toggles `collapsed`. Agents with no rows after filtering are omitted (as today).
- **Cut-off.** With `sort === "run"` and meta present, the renderer inserts a separator row right after the last `in_run` row of each group when the group also has active rows below it: `h("div", "tpcut", "Research cut-off: the 20 above are researched each night; the 7 below are active but not in the run", {role: "separator"})`. The numbers are the counts. Every active row also shows a rank badge, `#n` (ok) or "below cut-off" (warn). The tooltip is fixed: "Overnight and manual runs research the top 20 active topics per agent by decayed weight; the midday delta scouts the top 6 of those." With `run === "below"`, the group header says "showing the k active topics outside the run".
- **Rows.**
  - **Compact** (default, one line, CSS grid): switch or Restore · rank badge · name (match highlight as today) · origin badge (chat / discovery / explore; none when `null`) · trial badge ("trial, 4 d left" when `trial_until` is in the future, "check-in due" when it has passed) · up to 3 keyword chips + "+n" · weight cell "1.8" with the bar on the effective weight and the tooltip "effective 1.8, base 2.4, decays toward 1.0" · "+3 / −1" (30 d likes / dislikes, tooltip "30 days of feedback", hidden when both are 0 or null) · notes count (tooltip "n preference notes in use") · relative `updated_at` · Edit / hold-to-archive actions.
  - **Comfortable**: today's `tpRow` layout plus the same badges.
  - Every value is `textContent`. Names keep `data-tip` for the full text. The CSS clamps names with `text-overflow: ellipsis`.
- **Paging.** Each group renders its first `TP_PAGE = 50` rows (after filter and sort), then `h("button", "ghost tiny tpmore", "Show 50 more (n left)")` and "Show all". At most about 150 rows are in the DOM by default. `#tpcount` shows "Showing 50 of 140 · 212 total".
- **Search cost.** `fzPrep` results are cached per topic object in a `WeakMap` that is rebuilt when `TP.data` changes, so a keystroke over 300 topics does not re-fold every name. The 120 ms debounce stays.
- **Keyboard.**
  - `/` focuses `#tpfilter` when no input, textarea or dialog has focus. `Esc` in search clears it (existing).
  - `ArrowDown` from search goes to the first row. Rows are `tabindex="-1"` with a roving `tabindex="0"`. `ArrowUp` / `ArrowDown` / `Home` / `End` move between rows and skip separators and group headers, and `Esc` in the list goes back to search.
  - `Enter` on a row opens `tpDialog(t)` when `tpWritable()`. The switch, Restore, Edit and the hold button stay in normal tab order, and nothing toggles on a bare key press.
  - Group headers are buttons (Space / Enter toggles them).
- **Writes.** `tpChange`, `tpDialog`, `tpPost`, `tpReload` and `tprestoredup` stay as they are, with the same `if_match: t.updated_at`. A new step: after any successful write that sends `weight`, `is_active` or `is_archived` (or a create), `loadTopics()` runs once, after `tpApply`. `niu_topics.write()` has already invalidated the cache (`:672-677`), so this is a fresh, non-forced GET and ranks, the cut-off and counts update. Name, keyword and excluded-term edits keep the local `tpApply` only. The `TP_MSG.changed` text is updated (contradiction 9).

### E. Failure modes
| Situation | Status view | Topics view |
|---|---|---|
| valkyrie-dispatch down | As today: last good data greyed out with "as of hh:mm" (`_result`, `niu_research.py:508-518`); the strip and cards are greyed too. | As today: stale list, writes disabled (`tpWritable`), banner. |
| Older valkyrie-dispatch without `schedule.jobs` | Fallback strip plus a note. | n/a |
| Without `companion` / `explore` | Cards say "not available in this version" / "off". | n/a |
| Without `topic_meta` | n/a | No rank, cut-off, origin or trial. Run sort falls back to weight, plus a one-line note. Everything else works. |
| Hostile or off-spec values | Become `"unknown"` / `None` and render as "?" or "–". | A bad meta field becomes `None`; the row is kept. |
| More than 1000 rows in one state | n/a | The existing "Showing … use `rd topics list`" notes (`app.js:2299-2300`), with the number changed to 1000. |
| Write races a page loop | n/a | Duplicates are deduplicated by id; the next refresh shows a missed row. |

### F. Security notes
- Transport, token storage, `whoami` scope gates and the loopback-only URL are unchanged (`niu_research.py:1-10`, `niu_topics.py:1-11`). niu-dash still sends only GET / POST / PATCH to fixed paths. Page queries use `_query`'s int check. No new outbound host.
- `/api/research` stays unpaired and still carries only aggregates. Schedule times are low-sensitivity and are the same kind of data as `brief_at` today. `/api/topics` stays paired-only, and the new per-topic counts travel only there.
- No new free text. `origin`, `job` and `weekday` are closed enums. Times pass `TS_RE` / `HHMM_RE`. Counts and weights are clamped. The front end never builds HTML from strings. The Topics section must keep passing the `topicsSrc` scan for `innerHTML`, `insertAdjacentHTML`, `outerHTML`, `document.write`, `eval(` and `new Function` (`tests/web_smoke.js:336-338`). The Status section gets the same scan (`// ---------- Research tab` up to `// ---------- Topics panel`).
- CSRF and pairing: no new POST route. `/api/topics/refresh` and the writes keep `_post_ok()` plus the device cookie (`bin/niu-dash:328-343`).
- No #hermes link: a Discord URL would need guild and channel ids. monitor.v1 must not carry Discord ids, the hostile test bans `discord.com` in shaped output (`tests/research_test.py:374-376`), and the research config is a protected path that agents cannot edit. Open question 3.

## Changes needed in valkyrie-dispatch (one small additive PR)
All additive and optional (defaults `None`). There are no new routes, scopes or migrations. Reviews are deferred to the feature release, per the workspace rule.

| ID | File | Change |
|---|---|---|
| V1a | `src/research_dispatch/monitor/models.py` | `class ScheduleJob(_Strict): job: Literal["comprehensive","delta","brief","explore","reflect","discovery","checkins"]; at: Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]; weekday: Literal["mon","tue","wed","thu","fri","sat","sun"] \| None = None; enabled: bool; next_at: Ts \| None = None`. `Schedule.jobs: Annotated[list[ScheduleJob], Field(max_length=8)] \| None = None`. |
| V1a | `src/research_dispatch/monitor/build.py` | Fill `jobs` from `Settings`: `comprehensive_time`, each of `delta_times`, `brief_time`, `explore_time`, `reflect_time`, `discovery_day` / `discovery_time` (for `discovery` and `checkins`). `enabled` = `scheduler_enabled` AND the job's own flag (`explore_enabled`, `reflect_enabled`, `discovery_enabled`, `checkins_enabled`; none for comprehensive, delta and brief). `next_at` = the next instant in `schedule_tz` (weekly for discovery and check-ins), reusing `core/timeutil` helpers. No hours are hard-coded. |
| V1b (optional) | `orchestrator/discover.py`, `db/repositories/preferences.py`, `monitor/models.py`, `monitor/build.py` | `companion_state` key `last_discover`, set when `discover()` finishes (also on a skip-free run with 0 proposals), exposed as `CompanionModel.last_discovery_at: Ts \| None = None`. |
| V1 | `schemas/monitor.v1.json`, `tests/monitor/` | Re-export (`export_schema`). Tests: the jobs list for default settings, disabled flags, two `DELTA_AT` values, weekday next instant across DST, schema sync. |
| V2 | `src/research_dispatch/api/schemas.py` | `TopicOut` gains optional fields: `created_at`, `weight_updated_at` (both `format_ts`), `effective_weight: float \| None` (rounded to 3), `run_rank: int \| None`, `in_run: bool \| None`, `origin: Literal["chat","discovery","explore"] \| None`, `trial_until: datetime \| None`, `likes_30d`, `dislikes_30d`, `notes_active: int \| None`. No text fields. |
| V2 | `src/research_dispatch/prompts/allocation.py`, `prompts/builder.py` | Extract `rank_for_run(topics, now, half_life_days) -> list[(eff, topic)]` with the builder's key `(-eff, name.casefold(), str(id))`. `build_general_prompt` / the hobby builder use it (`[:MAX_TOPICS]`), so the dashboard rank and the prompt cut cannot drift. |
| V2 | `src/research_dispatch/services/topics.py` | `meta_for(topics, now) -> dict[UUID, TopicMeta]`. Ranks come from all active topics of each agent present (`repo.list_topics(s, agent, True)`) through `rank_for_run` with `settings.weight_half_life_days`. `in_run = rank <= MAX_TOPICS`. Origin and trial: the newest `topic_proposals` row with `op='add'`, `status='applied'` and `applied_topic_id IN ids`; `trial_until = review_at` when origin is discovery or explore and `reviewed_at IS NULL`. `likes_30d` / `dislikes_30d`: `feedback_events` grouped by topic, action like/dislike, last 30 days. `notes_active`: `preference_notes` with `status='active'`, `scope='topic'`, grouped. Four grouped queries per call. |
| V2 | `src/research_dispatch/api/routers/topics.py`, `api/routers/auth.py` | `list_topics`, `get_topic`, `create_topic` and `patch_topic` merge `meta_for` into `TopicOut`. `FEATURES = ["topic_states", "topic_meta"]`. |
| V2 | `tests/api/test_topics_meta.py` (new), `tests/prompts/test_prompt_allocation.py` | Ranks equal the builder's top-20 set and order (ties at 1.0, casefold names). Inactive and archived rows get `run_rank=None`. Origin/trial from proposals, null after purge. Counts. The `TopicOut` key set is an exact allowlist (no text beyond `name`, `keywords`, `excluded_terms`). `test_route_scopes.py` is unchanged and still green. |
| V2 | `README.md` ("Topics API", "Scopes"), `CLAUDE.md` (Monitor endpoint / Topic states) | Document the fields and the "in the run" vs "active" wording. Add the PR to the release-review debt list. |

## Changes (niu-heimar), split for parallel implementers
Pieces P1 and P2 are independent. P3 depends only on the shape contract in Design A, and P4 only on Design C, so all four can start at once. P3 and P4 both edit `web/app.js`, `web/index.html` and `web/style.css`, but in separate sections: P3 between `// ---------- Research tab` and `// ---------- Topics panel`, P4 between `// ---------- Topics panel` and `// ---------- end Topics panel`. Merge P3 before P4 if they conflict.

| Piece | File | Change |
|---|---|---|
| P1 | `.claude-workspace/lib/niu_research.py` | `JOBS`, `WEEKDAYS`, `_job()`; `schedule.jobs` (None vs list); `companion.last_discovery_at`. |
| P1 | `.claude-workspace/tests/fixtures/research/m3.json` (new) | m2 plus `schedule.jobs` (8 entries, one disabled, discovery on `sun`) and `companion.last_discovery_at`. |
| P1 | `.claude-workspace/tests/fixtures/research/hostile.json` | Add: 50 jobs, `job: "rm -rf"`, `at: "25:99"`, `weekday: "funday"`, `next_at: "<script>"`, `enabled: "yes"`, and a `jobs` that is a string. |
| P1 | `.claude-workspace/tests/research_test.py` | Round-trip m3; hostile jobs → `"unknown"` / `None`, at most 8; `jobs` is None for m1/m2. Schema checks tolerate an older local schema: when `"ScheduleJob" not in defs`, skip jsonschema validation of m3 and allow `extra ⊆ {"jobs", "job", "at", "weekday", "enabled", "next_at", "last_discovery_at"}` in the keys test (`:426-433`). When it is present, assert `JOBS` / `WEEKDAYS` equal the schema enums. |
| P2 | `.claude-workspace/lib/niu_topics.py` | `topic_meta` feature; page loop (`PAGE=100`, `MAX_PAGES=10`, dedup); `shape_topic(raw, meta=False)` with the `meta` allowlist; `meta_supported` in the envelope and in `good`; `write()` shapes with the same flag. |
| P2 | `.claude-workspace/tests/topics_fixtures.py` (new) | `many_topics(n=300, seed=7, meta=True)`: deterministic rows over 3 agents and all 3 states, about 30 % at weight 1.0 (ties), casefold-sensitive names (`"Straße"`, `"STRASSE"`), all origins, trials past and future, hostile names in 5 % of rows. |
| P2 | `.claude-workspace/tests/fixtures/topics/list_meta.json` (new), `.claude-workspace/tests/fixtures/topics/hostile.json` | 5 rows with meta. Hostile meta: `run_rank: -3`, `"1e309"`, `origin: "<img>"`, `in_run: true` on an inactive row, `likes_30d: 1e12`, `trial_until: "javascript:"`. |
| P2 | `.claude-workspace/tests/topics_test.py` | The fake upstream honours `offset` (`:116`). A 300-topic list loads in full with `inactive_truncated=False`, and the expected call list (`:377`) includes page offsets. More than 1000 in one state → truncated. Dedup across pages. `KEYS` (`:264`) gains `meta`. Meta is None without the feature and shaped with it. Hostile meta is clamped or None and the row is kept. A PATCH reply carries meta. |
| P3 | `.claude-workspace/web/app.js` (Research section) | `rsComp`, `rsExplore`, `rsSched` (with fallback), `RS_JOB_LETTER` / `RS_JOB_LABEL`, the explore lane, the explore `rsRun` line, `rsBadge` on `lastResearch` with amber-only explore, bot and ollama triggers, explore rows in `rsErrors`, slimmer header chips. |
| P3 | `.claude-workspace/web/index.html`, `.claude-workspace/web/style.css` | `#rscomp`, `#rsexplore`, `#rssched`; `.rsgrid` auto-fit; `.rssched` marker styles (hollow = off). |
| P3 | `.claude-workspace/tests/web_smoke.js`, `.claude-workspace/tests/web_test.py` | `RS_IDS` += `rscomp`, `rsexplore`, `rssched`. Status-section source scan for innerHTML and the like. m3 passed through `shape()` (as `:328-352` does for m1/m2). Checks: the strip shows 5 markers within 24 h and the discovery text line; the fallback note when `jobs` is None; an explore lane bar for the m2 explore run; with fixture m2 changed so that last_run = explore succeeded and an earlier comprehensive failed, the badge is still red. Hostile values render as "?" with no innerHTML write. |
| P4 | `.claude-workspace/web/app.js` (Topics section) | New `TP` fields and persistence; toolbar handlers; sort and filter functions (pure, testable: `tpFilter(list, TP)`, `tpSort(list, TP)`, `tpGroups(list, TP)`); cut-off separator; compact and comfortable rows; badges; group collapse; paging; `WeakMap` fzPrep cache; keyboard navigation; reload after rank-moving writes; `TP_MSG.changed` text. |
| P4 | `.claude-workspace/web/index.html`, `.claude-workspace/web/style.css` | Toolbar controls (replace `#tparch`); `.tpgroup` button, `.tpcut`, `.tprow.compact` grid, rank, origin and trial badges, `.tpmore`. |
| P4 | `.claude-workspace/tests/web_smoke.js`, `.claude-workspace/tests/web_test.py` | `web_test.py` adds `many` (300 rows from `topics_fixtures.many_topics`, through `shape_topic(meta=True)`) and `many_legacy` (no meta) to the topics fixture file. Checks: at most 50 rows per group plus "Show 50 more"; "Show all" renders the whole group; the separator appears exactly after rank 20 in each group that has more than 20 active; `run=below` shows only rank > 20; origin and trial filters; sort orders; the search over 300 still finds the typo cases; `/` and arrow keys move focus; Enter opens the dialog; with `many_legacy` the run controls are hidden and the note is shown; the existing `TP.archived` uses become `TP.state`. The innerHTML trap stays on for all of it. |

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| Dashboard rank differs from the prompt cut | One function (`rank_for_run`) used by both builders and `meta_for`; a valkyrie-dispatch test compares them; the UI never re-ranks. |
| Ranks go stale after an edit | Re-fetch after rank-moving writes; list cache TTL is 10 s (`LIST_TTL`); effective weight decays slowly (30-day half-life). |
| More upstream calls per list | About 7 GETs for 300 topics (was 5), still one burst per 10 s, under `LIST_LOCK`; topic reads are not rate-limited upstream (only writes, `api/routers/topics.py:90-97`). |
| Schema-sync test breaks when the valkyrie-dispatch PR lands first | niu-heimar lands first; the tolerant checks in P1 also cover the reverse order. |
| Big DOM or slow typing at 300 | Paging (about 150 rows), fzPrep cache, existing debounce. |
| `meta` leaks companion-derived counts to the topics scope | Counts only, the owner's own data, paired-only route; Open question 1 asks the owner to confirm. |
| Origin silently missing after 90 days | Shown as "added directly, or more than 90 days ago", never guessed. |

## Test plan
- **Unit (Python):**
  - `tests/research_test.py`: shape of jobs (m3, hostile, absent); tolerant schema checks.
  - `tests/topics_test.py`: paging, dedup, truncation at 1000, meta shaping, hostile meta, PATCH meta, unchanged scope gates and write tests (`:343-365`, `:505-612`).
- **Front end:**
  - `tests/web_smoke.js` through `tests/web_test.py`, with the cases in P3 and P4.
  - The innerHTML trap and source scans cover both sections.
  - The pure `tpFilter`, `tpSort` and `tpGroups` are table-tested in node, like `fuzzyScore` (`web_smoke.js:269-320`).
- **Commands:** `bash .claude-workspace/tests/research.test.sh`, `bash .claude-workspace/tests/topics.test.sh`, `bash .claude-workspace/tests/web.test.sh` (needs node; skipped otherwise).
- **Integration (manual, live stack):** with the valkyrie-dispatch PR deployed locally, check:
  - Status shows the five strip markers with the right local times, and the discovery line.
  - Companion and explore card values match `rd`'s CLI views (`rd explore show`, `rd memory list`).
  - Topics shows 20 per agent above the separator. Raising the weight of a below-cut-off topic in the dialog moves it above the line after save. A 412 still offers Reload.
  - With an old image (`docker compose` on the previous tag), both views degrade with their notes.
- **valkyrie-dispatch:** `uv run pytest`, `uv run mypy --strict src`, `uv run ruff check .`, plus the new tests in V1 and V2.

## Rollout
1. niu-heimar PR (P1-P4), one branch. The description says `Reviews: deferred to the feature release`. After editing `.claude-workspace/`, offer `/workspace-sync` (workspace CLAUDE.md §6). Nothing changes until valkyrie-dispatch sends the new fields; the fallbacks show.
2. valkyrie-dispatch PR (V1a, V2; V1b optional), from `main`, also `Reviews: deferred to the feature release`, and added to the release-review debt in its CLAUDE.md.
3. Deploy valkyrie-dispatch; reload the dashboard. The strip, cut-off and badges light up within one poll (60 s) and one topics load.
4. Rollback: revert either PR on its own. Every field is optional on both sides. No migration, flag or config change. `research.json` is untouched.

## Open questions
1. **Topics contract.** Is it acceptable that the topics scope now also returns per-topic like/dislike counts, active-note counts, origin and trial (counts and enums only)? The alternative is to ship only `effective_weight`, `run_rank` and `in_run` and leave the rest for later.
2. **Discovery time.** Add V1b (`last_discovery_at`, one tiny `companion_state` write in `discover()`), or show only the next discovery time?
3. **#hermes link.** Do you want a "Open #hermes" link? It would need an owner-set `hermes_url` in `research.json` (a protected path, so you would add it yourself) validated as `https://discord.com/channels/<digits>/<digits>`, and it would be shown to paired browsers only. The default in this design is no link.
4. **Default density and page size.** Compact rows and 50 per group are proposed. Would you rather see 100 per group, or one flat list with an agent column instead of groups?
5. **Badge severity.** Should a stale bot (no heartbeat for more than 3 min) be amber, as proposed, or red when `brief_delivery` is `bot` (the brief would then fall back to the webhook after `BRIEF_BOT_GRACE_MIN`)?
