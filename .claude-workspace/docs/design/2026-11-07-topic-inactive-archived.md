# Topics: three states (Active, Inactive, Archived) with hold-to-archive
Status: proposed · Date: 2026-11-08 · Request: split today's single "Active vs Archived" switch into Active / Inactive (same switch) and Archived (separate press-and-hold icon button), with the archived state stored in valkyrie-dispatch.
Amends: `docs/design/2026-11-07-dashboard-topics-panel.md` (§1 audit, §2 field table, §3 `list_topics`/`map_upstream_error`, §4 "Enable/disable" and "Remove").

## Decision (read this first)
- **Backend (valkyrie-dispatch).** Add `topics.is_archived BOOLEAN NOT NULL DEFAULT false` and `archived_at` to the topics table. A CHECK constraint enforces the invariant **archived ⇒ not active**. Run selection, feedback, the 200-per-agent cap and the prompt builder keep reading `is_active` alone and need no change.
  - `PATCH {is_archived:true}` and `DELETE` archive a topic.
  - `PATCH {is_archived:false}` restores it to **Inactive**.
  - `PATCH {is_active:true}` on an archived topic unarchives it and makes it Active, with the cap checked. This keeps the old restore and duplicate-restore flows working.
  - An Alembic data migration marks every existing `is_active=false` row as archived.
  - `TopicOut` and the 409 `duplicate_topic` body gain `is_archived`.
  - `GET /topics` gains `state=active|inactive|archived|current|all`.
  - `whoami` gains `features: ["topic_states"]`, so the dashboard can tell a new backend from an old one.
- **Dashboard.**
  - **Active row:** the switch is on.
  - **Inactive row:** the switch is off, the row carries an "inactive" badge, and it stays in the normal list.
  - **Archived row:** shown only with "Show archived". It has a "Restore" button where the switch was and an "archived" badge.
  - Active and Inactive rows also get a monochrome **archive icon button**, built with the existing `TP_ICON`/`tpIcon` helper, next to the pencil Edit button. The user holds it for 1000 ms (pointer, or Space/Enter) while a fill sweeps across. Releasing early cancels.
  - **The dialog loses its two-click "Archive topic" button**, so the hold button is the only way to archive.
  - Counts and the cap count **active only**. Group headers add "n inactive".
- **Restore goes to Inactive, not Active.** A restore then never fails on the cap and never quietly puts a topic back into the next twice-daily run. Turning it on is one more click on the same switch. The duplicate-name "restore" in the dialog still makes the topic Active, because the user was trying to create that topic.
- **Rollout:** backend first. A dashboard pointed at an older backend (no `topic_states` in whoami) falls back to today's two-state behaviour: switch-off still means archived, there is no hold button, and a notice explains why.

## Context
Backend line references are from the valkyrie-dispatch working tree, which is on `feat/m2a-runs-scheduler`. `main` is at `886b701` (`.git/logs/HEAD:30`). M2a adds one commit `5ae90d3` on top, plus uncommitted `pyproject.toml` and `api/app.py`. The topic files below are the same as on main except `api/app.py`, so re-check line numbers there on the new branch. Dashboard references are from `.claude-workspace` on `feat/dashboard-topics-ux`, which branches from `origin/main` and includes #40.

**valkyrie-dispatch today**
- `db/models.py:84-85`: `is_active` (default True) and `deactivated_at`. Index `ix_topics_agent_type_is_active` (`:71`). Constraint names follow `ck_%(table_name)s_%(constraint_name)s` (`db/base.py:12-18`). Migration 0001 writes the full names, for example `ck_topics_weight_range`.
- `db/repositories/topics.py`:
  - `update_topic` allows `{name, keywords, excluded_terms, weight, is_active}` (`:129`) and sets `deactivated_at` on change (`:150-154`).
  - `deactivate_topic` is the soft delete (`:159-161`).
  - `create_topic` (CLI path) **reactivates** an inactive same-name row (`:98-109`).
  - `list_topics_page` filters on `is_active` only (`:164-177`), and `count_active` counts `is_active` (`:180-186`).
- `services/topics.py`:
  - The cap check runs only on an inactive → active transition (`:107-108`).
  - `DuplicateTopicError(existing_id, is_active)` (`:30-34`, `:67-73`).
  - `deactivate()` (`:115-117`) backs DELETE.
- `api/routers/topics.py`:
  - `GET ""` takes `is_active: bool = True` (`:98`), so active and inactive rows can't be listed together.
  - `_audit_action` maps `{is_active}` to `activate`/`deactivate` (`:58-61`).
  - DELETE is admin-only, soft, and **not audited** (`:174-177`).
- `api/schemas.py`: `TopicPatch` has `is_active` (`:83`) and is `extra="forbid"` (`:66-67`). `TopicOut` (`:113-127`).
- `api/app.py:142-150`: the 409 `duplicate_topic` body is `{existing_id, is_active}`. `:179` maps the cap to `topic_cap_reached`. `:181-183` maps `InactiveTopicError` to `topic_inactive`.
- Consumers of "active":
  - `orchestrator/run.py:260` and `llm/cli.py:55` use `repo.list_topics(..., True)`.
  - `services/feedback.py:68` refuses feedback on inactive topics.
  - `cli.py:85-89`: `rd topics list` prints `[inactive]`. `cli.py:115-134`: `rd topics remove` prints "deactivated".
- `api/routers/auth.py:16-23`: `WhoAmI{name, scope}`.
- Migrations: `0001` and `0002` on main. **M2a adds `0003_runs.py` (down_revision `"0002"`)** and `tests/db/test_migration_0003.py`. Tests migrate to head (`tests/conftest.py:51-52`).
- Route-scope table: `tests/api/test_route_scopes.py:21-39`. No new route is added here.

**Dashboard today**
- The topic proxy lives in **`lib/niu_topics.py`**. `lib/niu_research.py` supplies only the transport and `WHOAMI_PATH`.
  - `PATCH_KEYS` (`:53`), `_query` allowlist (`:118-130`), `_check_fields` (`:301-333`), `shape_topic` (`:400-418`).
  - `map_upstream_error` duplicate mapping (`:434-438`) and `_write_action` (`:591-596`).
  - `list_topics` makes 3 active calls plus 1 `is_active=false` call (`:547-561`).
  - `check_scope` reads only `scope` from whoami (`:215-237`).
- `bin/niu-dash:259-263,315-323` routes `/api/topics` and `/api/topics/{create,update,refresh}` generically. **No change is needed there.**
- `web/app.js`:
  - `TP` state (`:1347`, including `armed`).
  - `renderTopics` (`:1572`): chips count `is_active` (`:1588-1589`), the pool hides `!is_active` unless `TP.archived` (`:1603`), group headers and ordering (`:1614-1616`).
  - **`TP_ICON` and `tpIcon(paths)` (`:1630-1638`)** build monochrome `svg.tpico` line icons (`stroke: currentColor`, `web/style.css:540-541`). The Edit button uses them: `h("button","ghost tiny tpicon tpedit","",{"aria-label":…, "data-tip":"Edit"})` plus `tpIcon(TP_ICON.edit)` (`:1699-1700`).
  - `tpRow` (`:1640-1705`): switch only on active rows, Restore on the rest (`:1644-1655`), "archived" badge when `!is_active` (`:1668`).
  - `tpToggle` (`:1708`), cap message (`:1724`), `tpDialog` hides or arms `tparchive` (`:1756-1757`), `tpBusy` (`:1768`), `tpFormFail` duplicate restore (`:1777-1778`).
  - `tprestoredup` handler (`:1815`), two-click `tparchive` handler (`:1824-1839`), the `tparch` checkbox (`:1852`).
- `web/index.html:112`: "Show archived". `:174-178`: `tprestoredup`, `tparchhelp`, `tparchive`.
- `web/style.css`: `.tp-archived` (`:517`), `.tpswitch` (`:520-525`), `.b-off` (`:529`), `.tpicon`/`svg.tpico` (`:540-541`), reduced-motion blocks (`:154`, `:235`, `:454`).
- Tests:
  - `tests/topics_test.py`: the fake upstream filters on `is_active` (`:106`), `KEYS` (`:245`), the duplicate mapping (`:268-270`), the audit regex and actions (`:523-536`).
  - `tests/web_smoke.js`: archived toggle (`:233-234`), toggle (`:335-344`), duplicate restore (`:362-367`), two-click archive (`:380-398`). Its fake `setTimeout` is a no-op (`:33`).
  - `tests/web_test.py`: fake upstream (`:232`, `:326`).
  - Line numbers in the tests may have moved with #40.

## Requirements
- **Must:**
  - Three states, stored in valkyrie-dispatch. Inactive is excluded from runs exactly as archived is today.
  - The same per-row switch moves between Active and Inactive.
  - Archiving is a separate, deliberate hold gesture (about 1 s, visible progress, early release cancels, keyboard accessible) on a monochrome icon button.
  - Archived topics are hidden unless "Show archived" is on, and they can be restored.
  - Existing callers keep working unchanged: duenext, the CLI, an old dashboard, `PATCH is_active=true` restore, `DELETE`.
  - The `topics` scope can archive and restore through PATCH. It still **cannot** call DELETE.
- **Must not:**
  - Touch the uncommitted M2a changes, or branch from `feat/m2a-runs-scheduler`.
  - Let any state combination have `is_active ∧ is_archived`.
  - Add a hard delete, a new route reachable by `topics`, or topic text in any audit line.
  - Let the dashboard send `is_archived` or `state` to a backend that doesn't support them (`extra="forbid"` would answer 422, and FastAPI **silently ignores** an unknown `state` query parameter and would return wrong rows).

## Options considered
**State model**
1. **`is_archived` + `archived_at`, with archived ⇒ not active** (chosen).
   - Pros: additive. Every reader of `is_active` (runs, feedback, cap, CLI dry run) stays correct unchanged.
   - Cons: two booleans with one forbidden combination, guarded by a CHECK constraint and a schema validator.
2. **A `status` enum column replacing `is_active`.**
   - Pros: one source of truth.
   - Cons: it rewrites the index, the repository, the orchestrator query, feedback, `TopicOut` and every client, and it breaks the existing `is_active` contract. Too large.
3. **Dashboard-only archive flag.** Rejected by the user. The CLI and duenext wouldn't see it.

**Where Restore lands**
1. **Inactive** (chosen). It never hits the cap, and it never re-enters the next run unannounced. It is one switch flip from Active.
2. **Active.** One fewer click, but a restore can fail with 409 cap, and it silently changes the next briefing.

**How the dashboard detects an older backend**
1. **`whoami.features`** (chosen). It is explicit, already fetched and cached for 300 s (`niu_topics.py:215-237`), and unambiguous when the topic list is empty.
2. **Look for an `is_archived` key in list rows.** Ambiguous with zero rows, and it needs a `state` query first, which an old backend ignores.
3. **Probe a PATCH and catch the 422.** That writes as a side effect. Rejected.

**Dialog archive button**
1. **Remove it** (chosen). One archive gesture with one safety pattern. The row icon sits right next to the pencil Edit icon.
2. **Keep a second hold button in the dialog.** It duplicates the control and the tests.

## Design

### 1. valkyrie-dispatch

**State transitions (authoritative)**
| Request | From Active | From Inactive | From Archived |
|---|---|---|---|
| `PATCH {is_active:false}` | Inactive (`deactivated_at=now`) | no-op | no-op (stays Archived) |
| `PATCH {is_active:true}` | no-op | Active (cap checked) | **Active** (unarchive and activate, cap checked) |
| `PATCH {is_archived:true}` | Archived (`deactivated_at=now`, `archived_at=now`) | Archived (`archived_at=now`) | no-op |
| `PATCH {is_archived:false}` | no-op | no-op | **Inactive** (`archived_at=NULL`) |
| `PATCH {is_active:false, is_archived:true}` | Archived | Archived | no-op |
| `PATCH {is_active:true, is_archived:false}` | no-op | Active (cap) | Active (cap) |
| `PATCH {is_active:false, is_archived:false}` | Inactive | no-op | Inactive |
| `PATCH {is_active:true, is_archived:true}` | **422** (validator) | 422 | 422 |
| `DELETE /topics/{id}` (admin) | Archived | Archived | no-op (204, idempotent) |
| `rd topics add` with the same name (CLI `create_topic`) | conflict | Active, values replaced (today's behaviour) | Active, values replaced, `is_archived` cleared |

- Other fields remain editable in every state through the API. The dashboard hides Edit on archived rows.
- `updated_at` changes on every real transition (`onupdate`), so `If-Match`/`ETag` need no change. A no-op PATCH doesn't change it, as today.

**Schema and model**
- `db/models.py` `Topic`:
  - `is_archived: Mapped[bool] = mapped_column(default=False, server_default=sa.false())`
  - `archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)`
  - `CheckConstraint("NOT (is_archived = 1 AND is_active = 1)", name="archived_not_active")`
- `api/schemas.py`:
  - `TopicPatch.is_archived: bool | None = None`.
  - `_non_empty_and_no_nulls` also raises `"is_active and is_archived cannot both be true"`, which yields 422 `validation_error`.
  - `TopicOut.is_archived: bool`. `archived_at` is not exposed (Open question 2).
- `api/routers/auth.py`: `WhoAmI.features: list[str]`, the constant `["topic_states"]`. Same for every scope, fixed strings only.

**Migration `migrations/versions/0004_topic_archived.py`** (revision `"0004"`, down_revision `"0002"`; see Risks for why it isn't `0003`)
```python
def upgrade() -> None:
    with op.batch_alter_table("topics") as b:
        b.add_column(sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("archived_at", UTCDateTime(), nullable=True))
    # Every inactive row today was archived through the old UI, DELETE or `rd topics remove`.
    op.execute("UPDATE topics SET is_archived = 1, archived_at = COALESCE(deactivated_at, updated_at) WHERE is_active = 0")
    with op.batch_alter_table("topics") as b:
        b.create_check_constraint("ck_topics_archived_not_active", "NOT (is_archived = 1 AND is_active = 1)")

def downgrade() -> None:
    with op.batch_alter_table("topics") as b:
        b.drop_constraint("ck_topics_archived_not_active", type_="check")
        b.drop_column("archived_at")
        b.drop_column("is_archived")
```
- The raw `UPDATE` leaves `updated_at` alone, so existing ETags stay valid.
- Downgrade collapses Inactive and Archived into today's single "inactive". That is the old semantics.

**Repository (`db/repositories/topics.py`)**
- `update_topic` gets `allowed` plus `"is_archived"`. The `is_active` block (`:150-154`) is replaced by `_apply_state(topic, is_active, is_archived)`, which implements the table above.
- `create_topic` reactivation (`:106-107`) also clears `is_archived` and `archived_at`.
- New `archive_topic(session, id)`. `deactivate_topic` stays (it now means Inactive).
- `list_topics` / `list_topics_page` take `state: Literal["active","inactive","archived","current","all"] | None`:
  - `active` = `is_active`;
  - `inactive` = `NOT is_active AND NOT is_archived`;
  - `archived` = `is_archived`;
  - `current` = `NOT is_archived`;
  - `all` = no filter.
  The legacy `is_active` argument is kept: `False` still means "not active" (inactive and archived).
- `count_active` is unchanged.

**Service (`services/topics.py`)**
- `DuplicateTopicError(existing_id, is_active, is_archived)`.
- The cap check at `:107` is unchanged. It already covers archived → active. `{is_archived:false}` never checks the cap.
- `deactivate()` is renamed `archive()`. `list(..., state=None)` passes the state through.

**Router (`api/routers/topics.py`)**
- `GET ""` adds `state: TopicState | None = None`. If `state` is set and `"is_active" in request.query_params`, it returns 422. Without `state`, today's behaviour is unchanged. `limit ≤ 500` stays.
- `_audit_action`:
  - only `{is_active, is_archived}` keys present: `archive` if `is_archived` is true, else `activate` if `is_active` is true, else `restore` if `is_archived` is false, else `deactivate`;
  - otherwise `update`.
- `DELETE` takes `principal: Admin` (still admin only). It calls `svc.archive` and logs `topic_changed action=archive fields=["is_archived"]`. That closes today's unaudited DELETE.
- `api/app.py` duplicate handler adds `is_archived=exc.is_archived`. The `is_active` key is kept, so the payload is additive.

**CLI (`cli.py`)**
- `topics list`:
  - Flags: `[inactive]` when not active and not archived, `[archived]` when archived.
  - `--all` lists everything.
  - New `--state …`. The default stays `active`.
- `topics remove` archives and prints `archived <name>`.
- New `topics restore <id>` restores to Inactive.

**Unchanged on purpose**
- The orchestrator (`orchestrator/run.py:260`), `llm/cli.py:55`, feedback (`services/feedback.py:68`, which still refuses Inactive and Archived with `topic_inactive`), monitor, the route table, scopes, and both rate limits.

### 2. Dashboard

**Server (`lib/niu_topics.py`)**
- `check_scope` also caches `features` from whoami. It keeps only known strings (`{"topic_states"}`) and returns `{"states": bool}`.
- `_query` allows `state` in `("active","inactive","archived","current","all")`.
- `list_topics`:
  - **With `states`:** 3 × `{agent_type, state:"active", limit:200}`, then `{state:"inactive", limit:200}` and `{state:"archived", limit:200}`.
  - **Legacy:** today's 4 calls.
  - The envelope adds `states_supported` and `inactive_truncated`.
- `shape_topic`:
  - Adds `is_archived` (strict bool when present).
  - If it is missing, `is_archived = not is_active` (legacy).
  - `is_active ∧ is_archived` drops the row, counted in `dropped`.
- `PATCH_KEYS` adds `is_archived` (strict bool in `_check_fields`). `validate_patch` rejects both-true with the new `FIELD_CODES` entry `bad_combo`.
- `write`: if the body has `is_archived` and the backend lacks `states`, it refuses with `incompatible` (409) **before** any upstream call, and audits that as refused.
- `_write_action` mirrors the backend: `create|update|activate|deactivate|archive|restore`.
- `map_upstream_error` duplicate: adds `existing_archived` (`b["is_archived"] is True`, or `not existing_active` when the key is absent).

**Row layout per state** (`web/app.js` `tpRow`, `web/style.css`)
| State | Left control | Badge | Row style | Right side |
|---|---|---|---|---|
| Active | switch, on | none | normal | weight, pencil **Edit**, **archive icon (hold)** |
| Inactive | switch, off | `inactive` (`b-off`) | `.tp-inactive` (opacity .8, solid border) | weight, pencil **Edit**, **archive icon (hold)** |
| Archived (only with Show archived) | **Restore** button | `archived` (`b-off`) | `.tp-archived` (today's dashed, .6) | weight only. No Edit and no archive icon. |
| Legacy backend | today's behaviour | today's | today's | no archive icon |

- **Ordering in a group:** Active, then Inactive, then Archived.
- **Switch:** `tpToggle(t, active)` is unchanged and sends `{is_active}`. Off now means Inactive. The cap message (`:1724`) becomes "… already has 200 active topics. Switch one off first."
- **Restore:** a new `tpRestore(t)` sends `{is_archived:false}` with `if_match`. The row message reads "Restored as inactive. Switch it on to research it again."
- **Hold-to-archive button** (`tpHoldBtn(t)`, reusing the icon helper):
  - Add `TP_ICON.archive = ["M3 4h18v4H3Z", "M5 8v12h14V8", "M10 12h4"]`, a lidded box drawn as monochrome strokes.
  - `const b = h("button", "ghost tiny tpicon tphold", "", { "aria-label": `Archive topic: ${t.name}`, "aria-describedby": "tpholdhelp", "data-tip": "Hold to archive" }); b.append(tpIcon(TP_ICON.archive), h("span", "tphold-fill", "", { "aria-hidden": "true" }));`
  - It is placed after the Edit button. The same `.tpicon`/`svg.tpico` CSS gives it the same size and colour.
  - `#tpholdhelp` is a static, visually hidden span in `index.html`: "Press and hold for one second to archive. Archived topics stop being researched and can be restored."
  - **Start:** `pointerdown` (`e.button === 0`), or `keydown` with `" "`/`"Enter"` and `!e.repeat` (calls `preventDefault`). Start adds the class `holding` and sets `TP.hold = { id, timer: setTimeout(done, TP_HOLD_MS) }` with `TP_HOLD_MS = 1000`. `TP.hold` replaces `TP.armed`.
  - **Cancel:** `pointerup`, `pointerleave`, `pointercancel`, `keyup` of the same key, `blur`, `visibilitychange`, and **every `renderTopics()` call** all clear the timer and remove `holding`. Cancelling on render prevents a stale button from firing after the list is rebuilt.
  - **Quick press:** a release in under 300 ms shows the row hint "Hold to archive". `click` never archives, which also covers Enter's synthetic click. `contextmenu` calls `preventDefault`.
  - **`done()`:** re-checks `TP.hold.id === t.id` and that the topic isn't busy, then calls `tpArchive(t)`. That sends `{is_archived:true}` with `if_match`, and the update is confirmed, not optimistic. On success `#tpmsg` (`aria-live`, `index.html:117`) reads `Archived "<name>". Turn on Show archived to restore it.`, set through `textContent`.
  - **CSS:**
    - `.tphold { position:relative; overflow:hidden; touch-action:none; user-select:none; -webkit-touch-callout:none }`
    - `.tphold-fill { position:absolute; inset:0; transform:scaleX(0); transform-origin:left; background:currentColor; opacity:.25; pointer-events:none }`
    - `.tphold.holding .tphold-fill { transform:scaleX(1); transition:transform 1s linear }`
    - Reduced motion: `steps(4)`.
    - No inline styles, so the CSP is unchanged.
- **Dialog:** remove `tparchive` and `tparchhelp` (`index.html:176,178`), their handler (`app.js:1824-1839`), `TP.armed`, and the references at `:1756-1757` and `:1768`.
- **Duplicate restore** (`:1777-1778`, `:1815`) still sends `{is_active:true}`, so the topic becomes Active. The label is "Restore the archived one" if `existing_archived`, else "Switch the existing one on".
- **Counts:**
  - Chips stay "General 12/200": **active only**, the cap.
  - Group header: `General · 12/200 · 3 inactive`.
  - The search pool holds active and inactive rows, plus archived rows when "Show archived" is on.
- **Legacy banner** (`#tphead`, when `states_supported === false`): "valkyrie-dispatch is older than this dashboard: switching a topic off archives it, and Inactive is unavailable."
- **New messages:** `TP_MSG.incompatible` "valkyrie-dispatch is too old to archive separately; nothing was changed." `TP_FIELD_MSG.bad_combo` "A topic cannot be active and archived."

### 3. Data flow (unchanged transport)
```
row switch    ─► POST /api/topics/update {id, if_match, fields:{is_active}}          ─► PATCH /api/v1/topics/<uuid> {is_active}
hold 1 s      ─► POST /api/topics/update {id, if_match, fields:{is_archived:true}}   ─► PATCH … {is_archived:true}
Restore       ─► POST /api/topics/update {id, if_match, fields:{is_archived:false}}  ─► PATCH … {is_archived:false}
list          ─► GET /api/topics ─► whoami (scope + features) ─► 3×state=active, state=inactive, state=archived
```

## Changes

### PR A: kaiserdrake/valkyrie-dispatch, branch `feat/topic-archive-state` from `origin/main`
Use a **separate worktree**, so the dirty `feat/m2a-runs-scheduler` checkout isn't touched:
`git -C ~/WORKSPACE/PERSONAL/valkyrie-dispatch fetch origin && git -C ~/WORKSPACE/PERSONAL/valkyrie-dispatch worktree add ../valkyrie-dispatch-archive -b feat/topic-archive-state origin/main`

| File | Change |
|---|---|
| `migrations/versions/0004_topic_archived.py` | **New.** Columns, data migration, CHECK constraint. |
| `src/research_dispatch/db/models.py` | `is_archived`, `archived_at`, the CHECK constraint. |
| `src/research_dispatch/db/repositories/topics.py` | `_apply_state`, `is_archived` in `allowed`, `archive_topic`, `create_topic` clears the archive, the `state` filter. |
| `src/research_dispatch/services/topics.py` | `DuplicateTopicError.is_archived`, `archive()`, `list(..., state)`. |
| `src/research_dispatch/api/schemas.py` | `TopicPatch.is_archived` plus the both-true validator. `TopicOut.is_archived`. `TopicState`. |
| `src/research_dispatch/api/routers/topics.py` | `state` query plus the 422 on mixing, `_audit_action`, DELETE audited with an `Admin` principal calling `svc.archive`. |
| `src/research_dispatch/api/routers/auth.py` | `WhoAmI.features`. |
| `src/research_dispatch/api/app.py` | The duplicate handler adds `is_archived` (re-check line numbers on main). |
| `src/research_dispatch/cli.py` | `list` flags and `--state`, `remove` archives, `restore`. |
| `README.md`, `CLAUDE.md` | The state table and the `state` query. A CLAUDE.md rule: "archived ⇒ not active (`ck_topics_archived_not_active`); 'researched' means `is_active`". |

### PR B: kaiserdrake/niu-heimar (`feat/dashboard-topics-ux` or a follow-up branch, through `/workspace-sync`)
| File | Change |
|---|---|
| `.claude-workspace/lib/niu_topics.py` | `features` in `check_scope`, `state` in `_query`, the `list_topics` branches, `shape_topic.is_archived`, `PATCH_KEYS`, `bad_combo`, the `incompatible` write guard, `_write_action`, `existing_archived`, `states_supported`/`inactive_truncated`. |
| `.claude-workspace/web/app.js` | `TP_ICON.archive`, `tpHoldBtn`/`tpArchive`/`tpRestore`, `TP.hold` (replaces `TP.armed`), cancel on render, three-state `tpRow`, counts and group header, removal of the dialog archive, the duplicate label, messages, the legacy banner. |
| `.claude-workspace/web/index.html` | Remove `tparchhelp` and `tparchive`. Add the hidden `#tpholdhelp`. |
| `.claude-workspace/web/style.css` | `.tp-inactive`, `.tphold`, `.tphold-fill`, `.holding`, a reduced-motion rule, a visually-hidden helper if none exists. |
| `.claude-workspace/tests/topics_test.py`, `tests/web_test.py`, `tests/web_smoke.js`, `tests/fixtures/topics/list_ok.json`, `tests/fixtures/topics/list_legacy.json` (new) | See Test plan. |
| `.claude-workspace/docs/design/2026-11-07-dashboard-topics-panel.md` | Add a line under Status: "Amended by 2026-11-07-topic-inactive-archived.md". |

`bin/niu-dash`, `lib/niu_research.py` and `hooks/block-env.sh` are **not** changed.

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| **Two Alembic heads**: M2a's `0003` and this `0004` both revise `0002`, and `upgrade_head` (`tests/conftest.py:52`) fails with multiple heads. | **If PR A merges first:** when M2a rebases onto main it adds `0005_merge_heads.py` with `down_revision = ("0003", "0004")`. No existing file is edited, so a prod DB at `0004` and a local M2a DB at `0003` both upgrade cleanly. **If M2a merges first:** PR A changes its `down_revision` to `"0003"`. The two touch different tables, so either order is safe. M2a's CI fails until this is done, which makes it impossible to forget. |
| The data migration archives a topic the user had only "paused" | Before this change there was no paused state. Restore takes it to Inactive. |
| An old dashboard against the new backend: switch-off gives Inactive, but the old UI labels it "archived" | The topic leaves runs either way. The label is fixed once PR B ships. |
| duenext or scripts calling DELETE | Same effect as before, now audited. |
| An accidental archive on touch | `pointerleave`/`pointercancel` cancel, it takes 1 s, and Restore exists. |
| A re-render during a hold leaves a stale timer | `renderTopics()` cancels the hold, and `done()` re-checks the id. |
| More than 200 inactive or 200 archived topics | `inactive_truncated`/`archived_truncated`, and "use `rd topics list --state …`". |
| Token power | Unchanged: `is_active:false` could already hide a topic. DELETE stays admin only, and archiving is rate-limited and audited. |

## Test plan
**valkyrie-dispatch (pytest)**
1. `tests/db/test_migration_0004.py` (new, modelled on `test_migration_0003.py`):
   - Seed at `0002`: active, inactive with `deactivated_at`, inactive without.
   - Upgrade. Archived flags and `archived_at` are correct, and `updated_at` is unchanged.
   - A raw `UPDATE` that sets both flags raises `IntegrityError`.
   - Downgrade to `0002` works. `alembic check` is clean.
2. `tests/test_models.py`: a table-driven `_apply_state` test over the transition table, including the timestamps. `create_topic` on an archived name gives Active.
3. `tests/api/test_topics_api.py`:
   - The shape key set (`:28-37`) adds `is_archived`.
   - New `test_three_states`, which also checks that `ETag` changes at each step. Both-true gives 422.
   - `test_reactivation_via_patch_keeps_weight` (`:92-99`) stays green unchanged.
   - The `state` filters, including mixing with `is_active` (422) and the legacy `is_active=false` (inactive and archived).
   - The duplicate test (`:76-89`) gains `is_archived` and an inactive-twin case.
   - The cap test (`:125-140`): Restore at the cap gives 200 (Inactive), then `is_active:true` gives 409.
   - The audit test (`:415-448`): `archive`, `restore`, DELETE gives `archive` (admin), and no `SECRET` appears.
   - A `topics` token can PATCH `is_archived`, and DELETE still gives 403 (`test_route_scopes.py`, unchanged).
4. `tests/api/test_auth_api.py`: keys `{name, scope, features}`, with `features == ["topic_states"]`.
5. `tests/test_cli.py`: the list flags, `remove` archives, `restore`, `--state current`.
6. On the M2a rebase: an orchestrator rig assertion that inactive and archived topics are both excluded.

**Dashboard**
1. `tests/topics_test.py`:
   - The fake upstream answers whoami with `features` (switchable to legacy) and honours `state` and `is_archived`.
   - Validation rows: `is_archived` bool ok, a string gives `not_bool`, both-true gives `bad_combo`.
   - `KEYS` adds `is_archived`. `shape_topic` legacy derivation and dropping of both-true rows.
   - Exact list queries in both modes.
   - Legacy plus `is_archived` gives 409 `incompatible` with **zero** upstream PATCH hits.
   - `existing_archived` mapping. Audit regex and actions include `archive|restore`.
2. `tests/web_test.py`: a paired update with `{is_archived:true}` gives 200. Unpaired gives 401.
3. `tests/web_smoke.js`:
   - **Three states:** the inactive row shows the switch off, the `inactive` badge and `.tphold`. The archived row is hidden by default. With Show archived it has Restore, and neither `.tphold` nor `.tpedit`.
   - **Icon:** `.tphold` contains an `svg.tpico` (the shared `tpIcon` helper) and no text node apart from its aria-label.
   - **Counts:** active only in the chips. "1 inactive" in the group header.
   - **Hold:**
     - Override the sandbox `setTimeout` to capture the callback, and record `clearTimeout`.
     - `pointerdown` adds `holding` and makes no request. `pointerup` clears the timer, makes no request, and shows the hint.
     - The callback sends exactly one `{is_archived:true}` with `if_match`.
     - Space keydown works, and `repeat:true` doesn't start a second timer. `click` alone never sends.
     - `renderTopics()` during a hold clears the timer.
   - **Restore** sends `{is_archived:false}`. Switch-off sends `{is_active:false}`, and the row then shows `inactive`.
   - **Dialog:** there is no archive control. This replaces the two-click test at `:380-398`.
   - **Duplicate:** the button label follows `existing_archived`, and the request is still `{is_active:true}`.
   - **Legacy:** the banner shows and no `.tphold` exists.
   - The existing "no `innerHTML` in Topics code" check covers the new functions.

**Manual (both deployed)**
1. After `rd migrate`, `rd topics list --all` shows old rows as `[archived]`.
2. Switch off: the row stays visible as `inactive`, and `rd topics list --state inactive` agrees. Switch it back on.
3. Hold the archive icon: the fill sweeps, releasing at about 0.5 s cancels, and a full hold archives. Repeat with Tab and Space, and with Enter.
4. Show archived, then Restore: the row comes back as Inactive. Check a phone long press and reduced motion.
5. Events tab: `archive`/`restore` rows with IDs only.

## Rollout
1. **PR A** in its own worktree: `uv run pytest`, `ruff`, `mypy --strict src`, `alembic upgrade head && alembic check`, then `/dual-review`. Merge and release. Then run `rd backup`, `docker compose pull && docker compose up -d`, and `rd migrate` at start.
2. **M2a coordination:** add the `0005` merge revision on rebase (see Risks).
3. **PR B** through `/workspace-sync` and `/dual-review`, then `systemctl --user restart niu-dash`. It also works against an old backend (legacy mode).
4. **Rollback:**
   - PR B: revert it.
   - PR A: `alembic downgrade 0002` and the previous image, or restore the backup.

## Open questions (with defaults)
1. **Feedback on Inactive topics** (digest-link likes or mutes): **Default: still refused** (`services/feedback.py:68`). It could be relaxed later to refuse archived topics only.
2. **Expose `archived_at`** ("archived 3 days ago")? **Default: not now.** It is stored, so exposing it later is additive.
3. **CLI `activate`/`deactivate` commands?** **Default: no.**
4. **Undo toast after archive?** **Default: no.** The hold plus Restore is enough.
5. **Hold duration:** **Default: a fixed `TP_HOLD_MS = 1000`.**
6. **Ship PR A only together with PR B**, to skip the brief window where switch-off is labelled "archived"? **Default: no.** The topic leaves runs either way.
