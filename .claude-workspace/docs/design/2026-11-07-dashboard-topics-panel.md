# Dashboard "Topics" panel: manage valkyrie-dispatch research topics
Status: proposed · Date: 2026-11-07 · Request: a panel in the workspace dashboard to list, add, edit, enable/disable and remove the research topics held by valkyrie-dispatch, without the dashboard ever holding an admin token.

## Decision (read this first)
Build **full topic management as a "Topics" sub-view of the existing Research tab**. It is backed by a **new `topics` token scope** in valkyrie-dispatch. That scope can read and write topics and nothing else: not feedback, usage, monitor, `DELETE`, or any future run or digest route. The dashboard checks the scope at runtime through a new `GET /api/v1/auth/whoami` endpoint, and it **refuses to use a token whose scope is not exactly `topics`**. That is how "never an admin token" gets enforced and not just hoped for.

The dashboard server is the only thing that talks to valkyrie-dispatch. It uses the same loopback-only, no-proxy, no-redirect, size-capped transport as `lib/niu_research.py`. Every topic route on the dashboard, **including the list GET**, needs a **paired-device cookie** on top of the CSRF header and same-origin check. The cookie is what actually stops a prompt-injected agent that has `curl`. CSRF only stops browsers.

Updates are **confirmed, not optimistic**. Edits carry an `If-Match` precondition, so a stale form can't overwrite a change made from the CLI, duenext or a feedback mute. Both servers rate-limit writes, and both audit every change by topic ID and changed field names only, never topic text.

"Remove" is the API's existing soft delete, shown as **Archive / Restore**. No hard delete is added.

The lighter fallback, if review time is short, is the same backend PR plus a dashboard PR that wires only the paired, read-only list.

## Context
**valkyrie-dispatch** (`PERSONAL/valkyrie-dispatch/`, package `research_dispatch`)
- **Scopes:**
  - Scopes are a linear rank, `monitor < read < admin` (`core/security.py:12-14`, `scope_allows` `:75-79`).
  - Tokens are written as `name:scope:sha256` (`:39-62`).
  - The dependencies are `require_monitor`, `require_read` and `require_admin` (`api/deps.py:27-42`).
  - A sideways scope such as "topics but not read" **can't be expressed** with a linear rank, so the scope model has to change (see Design §1).
- **Topic routes** (`api/routers/topics.py`):
  - `GET ""` and `GET /{id}` need `read` (`:23-37`). `GET ""` has `is_active: bool = True`, so active and archived topics can't be listed together, and `limit ≤ 500`.
  - `POST` needs `admin`, returns 201 (`:40-44`). `PATCH` needs `admin` (`:47-50`). `DELETE` needs `admin`, returns 204, and is **a soft delete** that calls `deactivate` (`:53-56`, `db/repositories/topics.py:159-161`).
  - `POST /{id}/feedback` needs `Admin` (`:59-81`).
- **Validation that applies through the API** (`api/schemas.py`):
  - `agent_type` is one of `fintrend|hobby|general` (`:22`).
  - `name` is 1–120 characters after stripping whitespace (`:40-44`).
  - Each keyword or excluded term is 1–80 characters (`:45-49`).
  - `keywords` holds 1–20 terms and `excluded_terms` 0–50 (`:57-58`).
  - `weight` is 0.1–5.0 and must be finite (`:56`, `db/models.py:32-33`).
  - The characters `<>`{}` are forbidden, as are Unicode category C and U+2028/2029 (`:25-33`).
  - Models use `extra="forbid"` (`:64-65`). `TopicCreate` has no `is_active` (`:68-73`). `TopicPatch` has no `agent_type` and rejects empty or null fields (`:76-90`).
- **`TopicOut`** returns `id, agent_type, name, keywords, weight, is_active, excluded_terms` (`:106-115`). It has **no `updated_at`**, although the column exists (`db/models.py:60`).
- **Fields the Topic table has and doesn't have:** it has `name_normalized`, `weight_updated_at`, `deactivated_at`, `created_at` and `updated_at` (`db/models.py:49-60`). It has **no `notes` column**. Notes exist only on `FeedbackEvent` (`:77`).
- **The CLI and the repository use looser limits than the API.** Names can be up to 200 characters, terms up to 100, and lists up to 50 items, with no forbidden-character check (`db/repositories/topics.py:16-18,37-56`). Topics created with `rd topics add` can therefore **exceed the API limits** and contain `<` and similar characters.
- **Other service rules:**
  - Each agent type can have at most 200 active topics (`services/topics.py:17`, mapped to 409 `topic_cap_reached` at `api/app.py:159-161`).
  - A name that collides with **an archived** topic returns 409 `duplicate_topic` with `existing_id` and `is_active:false` (`services/topics.py:61-67`, `api/app.py:132-140`). Reactivation goes through `PATCH is_active=true` (`tests/api/test_topics_api.py:88`).
  - Request bodies are capped at 16 KiB (`api/app.py:30`).
  - Writes are serialized by one `asyncio.Lock` (`services/topics.py:39-42`, `api/app.py:186-190`).
- **Topic text** ends up in prompts after `prompts/sanitize.py`. The project rule is "no endpoint starts a run" (`CLAUDE.md`, Conventions).

**Dashboard** (`.claude-workspace/`)
- **Server and POST guard:**
  - It is a stdlib `ThreadingHTTPServer` on `127.0.0.1:8765` (`bin/niu-dash:403-411`). GETs are unauthenticated.
  - `_post_ok` checks `X-Niu-CSRF: 1` and that `Origin` matches `Host` (`:176-186`). **A local `curl` can forge both.**
  - Paired-device cookie: `niu_session`, HttpOnly and `SameSite=Strict`. The store holds hashes only (`:51-89`). Pairing needs `niu-dash pair` in an interactive terminal (`:414-423`, `:372-398`).
  - Merge and dismiss require `device_for(self._cookie())` (`:287`, `:300-303`, `:334-336`).
  - The merge confirmation code is a typing check for the user, not a proof that a human is present (`:343-359`).
- `_body()` **silently truncates** at 10,000 bytes and returns `{}` on error (`:188-193`). That is unsuitable for topic payloads.
- **Audit precedent:** `log_merge` writes a row to the `events` table (`:120-129`).
- **Transport and token helpers in `lib/niu_research.py`:**
  - `validate_url` (`:109-114`), `token_path` (`:117-119`), `read_token` with O_NOFOLLOW, uid and 0600 checks (`:133-154`), `write_token` (`:157-171`), `_NoRedirect` plus `ProxyHandler({})` (`:190-198`), and `fetch` with its timeout, deadline and size cap (`:201-246`).
  - The base URL comes only from the gitignored `research.json` (`:82-106`).
- **Setter:** `niu-dash research-token [--remove]` reads the token with getpass (`bin/niu-dash:444-454`).
- **Hook** (`hooks/block-env.sh`):
  - `:24` blocks `research-dispatch\.t…`, `niu-heimar/*`, `read_token` and `research(.template).json`.
  - `:28` blocks Bash `niu-dash … research-token`.
  - `:60` blocks Bash calls to `/api/(prs/merge|pair|unpair|sessions/dismiss)`.
- **Front end:**
  - `h()` builds elements with `textContent` and `setAttribute` (`web/app.js:971-977`). The research code never renders server values as HTML (`:937-939`).
  - The `POST` helper is at `:7-11`. `ME.paired` comes from `/api/me` (`:85-89`). The Research tab is at `:980-1001` and `index.html:20,100-110`.
- **Research tab design:** `docs/design/2026-11-07-dashboard-research-tab.md`. Its monitor token sits in `~/.local/state/niu-heimar/research-dispatch.token`.

## Requirements
- **Must:**
  - List topics (active, and archived on request), add, edit (name, keywords, excluded terms, weight), enable/disable, and archive, from a paired browser.
  - Rely on a token that can do nothing but topic CRUD, and check its scope at runtime.
  - Validate input on the dashboard server (allowlist plus length caps) and again in valkyrie-dispatch, which is unchanged and stays authoritative.
  - Render every topic string with `textContent` only.
  - Show distinct states for not paired, not configured, wrong or too-powerful token, unreachable, incompatible and error. Keep the last good list, marked stale.
  - Stay stdlib-only on the dashboard side, with no new process.
- **Must not:**
  - Hold or send an `admin` or `read` token. Send any token to the browser, the logs or error text.
  - Reach `/feedback`, `/usage/*`, `/monitor/*`, `DELETE /topics/{id}`, token management, or anything that starts, cancels or changes a run.
  - Build a URL path or query from unvalidated input (no SSRF or path traversal).
  - Expose topic text to unpaired viewers. That includes the tailnet through `tailscale serve`, and local agents.
  - Write topic text to any audit log or the `events` table.

## Options considered
**What the dashboard can do**
1. **Read-only list now, edits stay in the CLI.**
   - Pros: smallest change; no write surface.
   - Cons: it still needs a new scope, because `monitor` can't read topics and `read` would later unlock digests that contain grounded text. It still needs pairing for the GET. It delivers half the request.
   - Kept as **the fallback** (Rollout, step 2a).
2. **Dashboard writes through a dedicated `topics` scope** (chosen).
   - Pros: it is what was asked. The scope gives a hard least-privilege boundary inside valkyrie-dispatch. It reuses the dashboard's existing transport and pairing.
   - Cons: a write-capable token sits on the host disk, readable by anything running as the user (see Security).
3. **Wait for the duenext UI (M5).**
   - Pros: duenext already plans topic CRUD through its server-side admin token, behind its own login, and no host token is needed.
   - Cons: it is months away, it is a multi-user web app with an admin token (a worse token than this one), and it doesn't fit "manage from the workspace dashboard".
   - Rejected for now. This design does not block it.

**How the scope fits the model**
1. **Put `topics` in the linear rank between `read` and `admin`.** Rejected: it would grant `read`, and with it future digest and run reads.
2. **Grant each scope an explicit set of capabilities** (chosen). For example, `topics` gives `{topics:read, topics:write}` and `admin` gives everything. Existing behaviour is preserved exactly, and new sideways scopes are easy to add.

**Where it lives in the UI**
1. **Its own fifth tab.** Pros: more room. Cons: it splits one service across two tabs, duplicates the reachability and config states, and adds a tab button for something used a few times a month.
2. **A sub-view of the Research tab ("Status | Topics")** (chosen). It has the same upstream, URL and config, and it is the natural place to look. The Status view stays readable without pairing, and the Topics view says "pair this browser".

**Updates**
1. **Optimistic:** flip the UI first, roll back on error. Cons: 409 cap, 409 duplicate, 412 changed and validation errors are all realistic outcomes. The server also canonicalizes terms (dedupe, trim, `clean_terms`), so the optimistic row would be wrong anyway.
2. **Confirmed** (chosen). Loopback round trips take a few milliseconds. The row shows a pending state, then the canonical record returned by the server.

**Token storage**
1. **A second 0600 file with the same checks as the monitor token** (chosen). It is a proven pattern, existing hook rules cover it, and it rotates without a restart.
2. **Memory only, pushed into the running server through a local socket.** Pros: nothing on disk. Cons: it is lost on every restart, it needs a new IPC surface, and the attacker shares the same uid, so it gains little.
3. **Reuse the monitor token file with a broader scope.** Rejected: the monitor poller polls every minute without pairing, so mixing the two would widen what that unpaired path can do.

## Design

### 1. valkyrie-dispatch: a `topics` scope, `whoami`, `If-Match`, rate limit, audit
**Capabilities** (`core/security.py`). `_RANK` is replaced by an explicit grant table:
```python
Scope = Literal["monitor", "read", "topics", "admin"]
SCOPES = ("monitor", "read", "topics", "admin")
_GRANTS: dict[str, frozenset[str]] = {
    "monitor": frozenset({"monitor"}),
    "read":    frozenset({"monitor", "read", "topics:read"}),
    "topics":  frozenset({"topics:read", "topics:write"}),
    "admin":   frozenset({"monitor", "read", "topics:read", "topics:write", "admin"}),
}
def scope_allows(granted: str, required: str) -> bool:
    return required in _GRANTS.get(granted, frozenset())
```
- `parse_api_tokens` and the `rd gen-token --scope` help and messages list `monitor, read, topics or admin` (`core/security.py:54`, `cli.py:24-28`).
- In `api/deps.py`, add `require_topics_read = require_scope("topics:read")` and `require_topics_write = require_scope("topics:write")`, plus `TopicsWriter = Annotated[TokenEntry, Depends(require_topics_write)]`.
- Existing behaviour stays the same: `read` and `admin` can still GET topics, only `admin` writes today, and `monitor` still reaches only `/monitor/summary`.

**Route requirements after the change** (`api/routers/topics.py`)
| Route | Before | After |
|---|---|---|
| `GET /api/v1/topics`, `GET /api/v1/topics/{id}` | `read` | `topics:read` (satisfied by `read`, `topics`, `admin`) |
| `POST /api/v1/topics`, `PATCH /api/v1/topics/{id}` | `admin` | `topics:write` (satisfied by `topics`, `admin`) |
| `DELETE /api/v1/topics/{id}` | `admin` | unchanged (`admin`). The dashboard archives with `PATCH is_active=false`, which has the same effect. |
| `POST /api/v1/topics/{id}/feedback` | `admin` | unchanged |
| `/usage/*`, `/monitor/*`, all future routes | as is | as is. `topics` grants none of them. |

**New `GET /api/v1/auth/whoami`** (`api/routers/auth.py`)
- It needs any valid token (`authenticate`) and returns `{"name": "<token name>", "scope": "<scope>"}`, with `no-store`.
- It returns no hashes and no other entries.
- The dashboard uses it to refuse tokens that are too powerful. The monitor poller can use it too (§3).

**`TopicOut.updated_at`** (additive) is an ISO-8601 UTC `Z` string built from the existing column. **No migration is needed.**

**`If-Match` on PATCH**
- The `PATCH` handler accepts an optional `If-Match: "<updated_at>"` header.
- `TopicService.update` compares the value to `current.updated_at` **inside the existing write lock**. On a mismatch it raises `TopicChangedError`, which maps to **412** `{"error":"topic_changed"}`.
- Without the header, it behaves as today, so duenext and the CLI are unaffected.
- `GET /{id}` and every write response send `ETag: "<updated_at>"`.

**Rate limit**
- It applies only when `principal.scope == "topics"`, so admin clients and duenext are untouched.
- It is an in-process sliding window keyed by token name (one uvicorn worker, so this is enough): **60 writes per hour**, setting `TOPICS_SCOPE_WRITES_PER_HOUR=60` in `.env.example`.
- Above the limit it returns 429 `rate_limited` with `Retry-After`.
- The clock is injectable for tests.

**Audit**
- Every POST and PATCH, from any scope, logs `topic_changed` with `token=<name>`, `scope`, `topic_id`, `action=create|update|activate|deactivate` and `fields=[sorted field names]`.
- It **never logs values**: no name, keywords, terms or weight.
- Refusals log `topic_write_refused` with the same keys plus `reason` (an error code).
- It uses the structlog logger `topics`.

**Not added:** a hard delete, a `notes` column, or anything that runs research.

### 2. Exact field set
| Field | List | Create | Edit | API rule (authoritative) | Dashboard input rule |
|---|---|---|---|---|---|
| `id` | yes | none | none | UUID | Must match `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` (lowercase, canonical) |
| `agent_type` | yes | required | **immutable** (not in `TopicPatch`) | `general|hobby|fintrend` | Enum. Default `general`. |
| `name` | yes | required | yes | 1–120 characters after strip, no forbidden characters | Strip and collapse internal whitespace. 1–120 code points. No forbidden characters. |
| `keywords` | yes | required, 1–20 | yes, 1–20 | each 1–80 | Textarea, **one term per line** (newline is category C, so it can't occur inside a term; commas can). Trim, drop empty lines, dedupe case-insensitively. 1–20 terms, each 1–80, no forbidden characters. |
| `excluded_terms` | yes (count, expandable) | optional, 0–50 | yes, 0–50 | each 1–80 | Same as keywords, 0–50 terms. |
| `weight` | yes | optional, default 1.0 | yes | 0.1–5.0, finite | Number input, step 0.1, rounded to 2 decimals, 0.1–5.0. |
| `is_active` | yes | none (always active on create) | yes, through the toggle and Archive/Restore | bool | Strict bool. |
| `updated_at` | yes (title tooltip "edited HH:MM") | none | sent as `If-Match` | new, read-only | Must match `TS_RE` with optional fractional seconds. Passed back verbatim only if it matches. |
| `notes` | — | — | — | **does not exist** | Not built. See Open question 1, where a migration would be needed. |

"Forbidden characters" means the same set the API uses: `<>`{}`, any `unicodedata.category(ch)[0] == "C"`, U+2028 and U+2029. The front end mirrors these rules for instant messages, but the server is the check that counts.

**Display caps on the read path** (topics created with the CLI can exceed the API limits):
- names up to 200 characters, terms up to 100, lists up to 50;
- control characters replaced with U+FFFD for display;
- anything longer is truncated with "…" and flagged `over_api_limits: true`.

Editing such a topic opens with a notice: "This topic was created with the CLI and exceeds what the API accepts. Shorten the highlighted fields to save."

### 3. Dashboard server
**New `lib/niu_topics.py`** (stdlib, imports transport helpers from `niu_research`):
- **Token:**
  - `token_path()` is `NIU_TOPICS_TOKEN_FILE` or `db_path().parent / "research-dispatch.topics.token"`.
  - The name matches the existing hook regex `research-dispatch\.t`, and the hook also gets an explicit rule.
  - `read_token()` and `write_token()` reuse `niu_research`'s implementation, refactored to take a path (`_read_token_at(path)`, `_write_token_at(path, token)`). The behaviour is identical.
- **`_call(cfg, token, method, path, body=None)`:**
  - The method must be `GET`, `POST` or `PATCH`.
  - `path` must be one of the constants `/api/v1/auth/whoami` and `/api/v1/topics`, or `"/api/v1/topics/" + id` where `id` matched `UUID_RE`.
  - The query string is built only from `{"agent_type": enum, "is_active": "true"|"false", "limit": int, "offset": int}` with `urlencode`.
  - The opener is `niu_research._opener()` (no proxy, no redirect). The timeout is `cfg.timeout_secs`, with a 2× deadline as in `fetch`.
  - The response cap is **1 MiB** for lists and 64 KiB otherwise. The response must be JSON.
  - It raises `_Fail(state, cause)` with fixed causes and never `str(exc)`.
  - The JSON request body is built with `json.dumps(..., ensure_ascii=False)`. It is refused locally (`toolarge`) if it exceeds 16 KiB, matching valkyrie-dispatch's cap.
- **`check_scope(cfg, token)`:**
  - It calls `whoami`, caches the result per token hash for 300 s, and requires `scope == "topics"` exactly.
  - `admin` or `read` gives state `too_powerful`, "This token has scope X. The dashboard only accepts a topics-scope token. Remove it with `niu-dash research-token --scope topics --remove`." Nothing else is sent with that token.
  - `monitor` gives state `wrong_scope`. A 404 from whoami gives `incompatible`.
- **`list_topics()`:**
  - It makes three calls for active topics (one per `agent_type`, `limit=200`, which equals the cap per agent) and one call for archived topics (`is_active=false&limit=200`), giving `archived_truncated` if 200 come back.
  - Each row is reshaped through `shape_topic()` (the allowlist and display caps in §2). Unknown keys are dropped, and rows with a bad `id` or `agent_type` are dropped and counted in `dropped`.
  - The result is cached for 10 s and invalidated by any write. No background poller and no request from an unpaired caller.
  - It returns `{state, message, topics:[…], archived_truncated, dropped, loaded_at, stale, limits:{…§2 caps…}}`. On failure it returns the last good `topics` with `stale: true`.
- **`validate_create(d)` / `validate_patch(d)`:**
  - These are pure allowlists over the fields in §2. Unknown keys mean 400.
  - They return `(clean_body, None)` or `(None, {"field": code})`, where `code` is one of `required|too_long|too_short|too_many|too_few|forbidden_char|bad_enum|out_of_range|not_bool|not_list|empty`.
- **`map_upstream_error(status, body)`** turns valkyrie-dispatch errors into fixed dashboard codes:
  - 409 `duplicate_topic` becomes `duplicate`, keeping `existing_id` only if it matches `UUID_RE`, plus `existing_active` (bool).
  - 409 `topic_cap_reached` becomes `cap`. 412 becomes `changed`. 404 becomes `not_found`. 413 becomes `toolarge`. 429 becomes `rate_limited`.
  - 422 becomes `invalid`, with field names taken from `details[].loc` only if they are in the field allowlist. Upstream `msg` text is dropped.
  - 401 becomes `unauthorized` and 403 becomes `wrong_scope`. Anything else is `error`.
- **`audit(action, topic_id, fields, device, ok, code=None)`** inserts an `events` row in the `log_merge` pattern: `event = "TopicChange"` or `"TopicChangeRefused"`, `session_id = "web"`, `domain = "PERSONAL"`, `project = "valkyrie-dispatch"`, `tool = "topics"`, and the summary `f"{action} {topic_id[:8]} [{','.join(fields)}] by {device_name}{' ('+code+')' if code else ''}"[:120]`. The summary holds IDs, field names and the device only.
- **Rate limit (in memory, per device ID):** 10 writes per minute and 100 per day. Above that the server returns 429 `rate_limited` before calling upstream.

**`bin/niu-dash` routes** (all topic routes need a paired cookie):
| Route | Guard | Behaviour |
|---|---|---|
| `GET /api/topics` | `device_for(cookie)`, else 401 `{"state":"not_paired"}` | `niu_topics.list_topics()`. **This is the only unauthenticated-to-paired GET exception in the dashboard.** It is justified because topic text is personal and untrusted. |
| `POST /api/topics/create` | `_post_ok`, paired device, rate limit | Body `{agent_type,name,keywords,excluded_terms,weight}`, then `validate_create`, `check_scope`, upstream `POST /api/v1/topics`, `audit`. Returns `{ok, topic}` or `{ok:false, code, fields?, existing_id?, existing_active?}` with status 400, 409, 412, 429 or 502. |
| `POST /api/topics/update` | same | Body `{id, if_match, fields:{name?,keywords?,excluded_terms?,weight?,is_active?}}`, then `validate_patch`, upstream `PATCH /api/v1/topics/{id}` with `If-Match` (if valid). Covers edit, enable/disable, archive and restore. |
| `POST /api/topics/refresh` | `_post_ok`, paired | Drops the 10 s cache, throttled to once every 5 s. |

- Topic routes read the body with a new `_body_strict(limit=16384)`. It returns 413 if `Content-Length` is missing or too large, and 400 on bad JSON. It never truncates. The existing `_body()` is unchanged for other routes.
- **`research-token` gets `--scope monitor|topics`** (default `monitor`, so the existing behaviour holds). The prompt reads "research-dispatch topics token: ". After saving, if research-dispatch is reachable, it calls `whoami` and **deletes the file again** if the scope isn't the expected one, printing why. `--remove --scope topics` removes the topics file.
- **Monitor hardening (same PR, small):**
  - `niu_research._refresh` calls `whoami` once every 300 s and refuses (state `too_powerful`) unless the scope is `monitor`.
  - If `whoami` returns 404 it carries on, because older deploys don't have the endpoint.
  - The fixed messages that say `PERSONAL/research-dispatch` now say `PERSONAL/valkyrie-dispatch` (`lib/niu_research.py:61`, `web/app.js:954`).

**States and messages** (fixed strings):
| `state` | Shown as |
|---|---|
| `not_paired` | "Pair this browser to view and edit topics (menu, then Pair). Topics are personal, so they are not shown to unpaired viewers." |
| `not_configured` | Setup steps (Rollout step 4) |
| `wrong_scope` / `too_powerful` | The scope message above, in red. Nothing is fetched. |
| `unreachable` | "valkyrie-dispatch is not reachable on 127.0.0.1:8080. Is the container running?" plus the last good list, greyed out, "as of HH:MM" |
| `unauthorized` | "Token rejected: check API_TOKENS and re-run `niu-dash research-token --scope topics`." |
| `incompatible` | "valkyrie-dispatch is too old for the Topics panel (needs /auth/whoami)." |
| `error` | A fixed message per cause (`toolarge`, `badjson`, `server`, `perms`, `format`, `url`, `internal`) |
| `ok` | The list |

### 4. UI (`web/index.html`, `web/app.js`, `web/style.css`)
- **Sub-navigation:** in the Research panel header (`index.html:101`), a segmented control `#rsview-status` | `#rsview-topics` with `aria-pressed`. The hash is `#research` or `#research/topics`, and the choice is stored as `niu.rsview`. The Topics view replaces `#rsbody` content with `#tpbody`, and the left column stays as it is.
- **Toolbar:**
  - Agent filter chips (All, General, Hobby, Fintrend, each with an active count, for example "General 12/200").
  - A client-side text filter (the substring stays in memory and is never sent).
  - A "Show archived" toggle.
  - "Add topic", Refresh, and "loaded HH:MM".
  - Every button that writes is `disabled` with title "Pair this browser to edit" when `!ME.paired`. In practice the whole view shows `not_paired`.
- **List (`#tplist`):** rows are grouped by agent and ordered as the API returns them (weight desc, then name). Each row has:
  - an Active switch (`<input type=checkbox role=switch>`);
  - the name (`h("span","tpname",name)`), with a "CLI-only limits" badge if `over_api_limits` is set;
  - keyword chips (the first 6, then "+n");
  - "excludes n", which expands into chips;
  - the weight as a number plus an SVG bar from 0 to 5 (a `width` attribute, so the CSP holds);
  - an Edit button.

  Archived rows (only when the toggle is on) are muted, with Restore in place of the switch.
- **Empty states:**
  - No topics at all: "No topics yet. Add one, or use `rd topics add` in valkyrie-dispatch."
  - A filter with no hits: "No topics match."
  - Fintrend with no topics: "Fintrend agent arrives in M4; topics you add now are kept."
- **Add/Edit dialog (`<dialog id="tpdlg">`, same pattern as the merge dialog):**
  - Fields: agent type (a select, disabled on edit), name, keywords and excluded terms (textareas, one per line, with live counters such as "7/20"), and weight (a number input).
  - Inline validation messages per field come from a fixed table keyed by code, for example `too_many` reads "At most 20 keywords".
  - Save sends the request and disables the form while it waits. On success the dialog closes and the row is replaced with the server's canonical record, with a short "saved" flash.
  - **On edit:** only changed fields are sent, together with `if_match`.
  - **On 409 `duplicate`:** "A topic with this name already exists." If `existing_active` is false, the dialog adds a "Restore the archived one" button, which sends `update` with `{is_active:true}` for `existing_id`.
  - **On 412 `changed`:** "This topic changed elsewhere (CLI, duenext or feedback). Reload it?" Reload re-fetches the topic and keeps the user's typed values for review. Nothing is merged automatically.
  - **On `cap`:** "General already has 200 active topics. Archive one first."
  - **On `rate_limited`:** "Too many changes in a short time. Try again in a minute."
  - **On unreachable:** "valkyrie-dispatch is not reachable; nothing was saved." The form stays filled.
- **Enable/disable:**
  - Clicking the switch disables it and shows a spinner. The server responds and the row re-renders from the response, which means the update is confirmed.
  - On error the switch returns to the server state, and the row shows the fixed message.
  - Disabling and archiving are **the same operation** (`is_active=false`), so there is one control: the switch. Archived rows are those with the switch off.
- **Remove:** in the Edit dialog, an "Archive topic" button with a two-step confirm, as the dismiss button does it (`app.js:209-223`): "Archive" turns into "Confirm archive" for 4 s. The help line says "Archived topics stop being researched and can be restored. Nothing is deleted."
- **Staleness:**
  - The list is fetched when the sub-view opens, after every write, on Refresh, and on `visibilitychange` back to visible if the list is older than 60 s. It is never polled in the background.
  - `stale: true` greys out the list and adds "as of HH:MM". All write controls are disabled while the state is not `ok`.
- **Rendering rule:** all topic values go through `h()`, `textContent` or `setAttribute` on non-URL attributes. No `innerHTML` or `esc()`-into-template is used for topic data anywhere in the Topics code, and `web_smoke.js` asserts this (Test plan).

### 5. Data flow
```
paired browser ──GET /api/topics, POST /api/topics/{create,update,refresh}──► niu-dash 127.0.0.1:8765
   (cookie niu_session, X-Niu-CSRF, same Origin)              │ device_for(cookie) · _post_ok · rate limit · validate_* · audit(events)
                                                              │ token: ~/.local/state/niu-heimar/research-dispatch.topics.token (0600, O_NOFOLLOW, uid)
                                                              │ whoami(scope == "topics") cached 300 s
                                                              ▼  ProxyHandler({}), _NoRedirect, timeout, size cap, constant paths + UUID
                         http://127.0.0.1:8080/api/v1/{auth/whoami, topics, topics/<uuid>}  (Bearer rd_pat_…, scope topics)
                                                              ▼
valkyrie-dispatch: scope_allows(topics:read|topics:write) · extra=forbid schemas · char checks · lock · If-Match · rate limit · audit log
```

## Security
**Threat model.** The main adversary is **a prompt-injected Claude session on this machine**: same uid, can run `curl` or `python`, and can read the dashboard source. Secondary adversaries are any other tailnet device (through `tailscale serve`) and a malicious web page in the user's browser.

| Threat | Control | Remaining risk |
|---|---|---|
| An agent calls the dashboard's topic routes with a forged `X-Niu-CSRF` and `Origin` | **Paired cookie required on every topic route, GETs included.** The cookie is HttpOnly and `SameSite=Strict`. The server stores only hashes in `web-sessions.json`, which the hook blocks (`block-env.sh:24`). Pairing needs `niu-dash pair` in a TTY (`bin/niu-dash:414-416`). The hook also blocks Bash calls to `/api/topics` (defence in depth only). | An agent that extracts the cookie from the browser profile (Firefox `cookies.sqlite` is plaintext, Chrome's is keyring-encrypted but readable by the same uid). The merge button already carries this risk. It is accepted and listed below. |
| An agent reads the topics token file and calls valkyrie-dispatch directly | Mode 0600, O_NOFOLLOW and uid checks. The hook blocks the file name, `niu-heimar/*` globs, `read_token` and the setter. **The blast radius is limited by the scope:** topics only, no `DELETE`, no feedback, usage, monitor or runs. valkyrie-dispatch rate-limits it to 60 writes an hour and audits by token name. | **The hook is pattern-based and a same-uid process can always read a 0600 file** (for example with an obfuscated `python -c`). This is accepted, because the worst outcome is bounded and reversible (below). Rotation takes one command. |
| The dashboard ends up holding an admin or read token (by mistake or through a swapped file) | `whoami` at setup (the setter deletes a wrong-scope token) and at runtime (cached for 300 s). Anything other than `topics` means nothing is sent. The same check covers the monitor token. | A token swapped within the 300 s cache window could be used for up to 5 minutes. It still only reaches the topic routes, because the dashboard's paths are constants. |
| SSRF or path traversal through topic IDs or filters | The base URL comes from gitignored local config only, already hook-protected (`block-env.sh:24`). Paths are constants plus a lowercase canonical UUID that must fully match. Queries are built from enums and ints with `urlencode`. There are no proxies and no redirects. The method allowlist is GET, POST and PATCH. | None known. |
| XSS through topic text (CLI-created topics can contain `<`) | Rendering uses `textContent` only. The CSP is unchanged (`script-src 'self'`, no inline). The server reshapes with an allowlist and replaces control characters. The smoke test uses hostile fixtures. | None known. |
| Prompt injection through topic text into the research agents | That is outside the dashboard's power to fix. It is the existing control in valkyrie-dispatch: input validation, `prompts/sanitize.py`, and treating topic data as data. The dashboard adds no new way in beyond what `admin` already allowed, and it narrows who can write. | This is the real remaining consequence of a stolen topics token: an attacker can plant steering text in topics within the API limits (120-character names, 80-character terms). Bounded by sanitize, the forbidden characters, the agents' turn and search caps, and the fact that **no endpoint starts a run**. Audit logs show `token=niu-dash-topics`. |
| Mass archive or edit (denial of service on the briefing) | Rate limits: dashboard 10 a minute and 100 a day per device, valkyrie 60 an hour per token. Audit rows go to the dashboard `events` feed (visible on the Events tab) and to valkyrie's logs. Archive is reversible with Restore, and weight and terms can be fixed by hand. | There is no automatic undo. The audit is IDs and field names only, so restoring old values relies on memory or a DB backup (Open question 3). |
| Topic text leaks to the tailnet or to agents through GETs | `GET /api/topics` needs pairing. The `events` audit carries no text. The monitor endpoint stays text-free. | — |
| The token leaks through logs or errors | Fixed messages, the token kept in local variables only (as in `niu_research`), and a test that greps every response and stderr. | — |
| Oversized or malformed bodies | `_body_strict` (16 KiB, 413), allowlist validation, the 16 KiB cap at valkyrie-dispatch, response caps. | — |

**Accepted risks and why:**
1. A same-uid agent can, with effort, read the token file or a browser cookie. A same-uid process can always read the user's own files. The design turns that into a **topics-only, rate-limited, audited, reversible** capability instead of admin.
2. Topic text is a prompt-injection carrier. This is unchanged from today's `admin` and CLI paths, and the pipeline's existing sanitization is the control.

**Optional hardening (not in scope, Open question 4):** an "edit window" that a user arms in a TTY with `niu-dash topics-unlock 15m`, outside of which the dashboard refuses writes.

## Changes

### PR A: kaiserdrake/valkyrie-dispatch (branch `feat/topics-scope`, after PR #1 merges)
| File | Change |
|---|---|
| `src/research_dispatch/core/security.py` | Add `topics` to `Scope` and `SCOPES`. Replace `_RANK` with `_GRANTS`, so `scope_allows` checks capabilities. Update the error message. |
| `src/research_dispatch/api/deps.py` | Add `require_topics_read`, `require_topics_write` and `TopicsWriter`. Keep `require_read`, `require_admin` and `Monitor` unchanged. |
| `src/research_dispatch/api/routers/topics.py` | GETs use `require_topics_read`. POST and PATCH use `TopicsWriter`, a principal used for the audit and the rate limit. PATCH reads the optional `If-Match` and sets `ETag`. DELETE and feedback are unchanged. |
| `src/research_dispatch/api/routers/auth.py` | **New.** `GET /api/v1/auth/whoami` returns `{name, scope}`. |
| `src/research_dispatch/api/app.py` | Include the `auth` router. Map `TopicChangedError` to 412 `topic_changed` and `RateLimitedError` to 429 `rate_limited` (with `Retry-After`). Add `412: "precondition_failed"` and `429: "rate_limited"` to `_HTTP_CODES`. Set `app.state.topic_write_limiter`. |
| `src/research_dispatch/api/schemas.py` | `TopicOut.updated_at: datetime`, serialized as UTC `Z`. |
| `src/research_dispatch/services/topics.py` | `update(..., expected_updated_at: datetime | None = None)` compares inside the lock and raises `TopicChangedError`. Emit the `topic_changed` audit log (field names only). |
| `src/research_dispatch/services/ratelimit.py` | **New.** `SlidingWindowLimiter(limit, window_s, clock)` with `hit(key) -> retry_after | None`. |
| `src/research_dispatch/core/settings.py` | `topics_scope_writes_per_hour: int = 60` |
| `src/research_dispatch/cli.py` | `gen-token --scope` help and check list `topics`. |
| `.env.example` | `TOPICS_SCOPE_WRITES_PER_HOUR=60`, plus a comment on the `topics` scope for the dashboard token. |
| `README.md`, `CLAUDE.md` | A "Scopes" table (monitor, read, topics, admin and what each reaches), `whoami`, `If-Match`. Plus a CLAUDE.md rule: "the `topics` scope must never be granted a non-topic route; the route-scope table test enforces it". |
| `tests/test_security.py`, `tests/api/conftest.py`, `tests/api/apiutil.py`, `tests/api/test_topics_api.py`, `tests/api/test_auth_api.py` (new), `tests/api/test_route_scopes.py` (new), `tests/test_ratelimit.py` (new) | See Test plan. |
| OpenAPI snapshot (if committed) | Regenerate: `updated_at`, `whoami`, 412 and 429. |

### PR B: kaiserdrake/niu-heimar (workspace repo, through `/workspace-sync`)
| File | Change |
|---|---|
| `.claude-workspace/lib/niu_topics.py` | **New.** `token_path`, `_call`, `check_scope`, `list_topics`, `shape_topic`, `validate_create`, `validate_patch`, `map_upstream_error`, `audit`, `RateLimiter`, plus the `STATES`/`MESSAGES`/`FIELD_CODES` tables. |
| `.claude-workspace/lib/niu_research.py` | Refactor `read_token`/`write_token` onto `_read_token_at(path)`/`_write_token_at(path, token)` (behaviour unchanged). Export `UUID_RE` and `_opener`. Add the `whoami` scope check for the monitor token (404 tolerated). Fix the `PERSONAL/valkyrie-dispatch` messages. |
| `.claude-workspace/bin/niu-dash` | `GET /api/topics` (paired), `POST /api/topics/{create,update,refresh}` (`_post_ok`, paired, rate limit), `_body_strict`, `research-token --scope monitor|topics` with the `whoami` check after saving. Update the usage docstring. |
| `.claude-workspace/web/index.html` | Status/Topics segmented control, `#tpbody` (toolbar, `#tplist`), `<dialog id="tpdlg">`. Static markup only. |
| `.claude-workspace/web/app.js` | `TP` state, `loadTopics`, `renderTopics`, `tpRow`, `tpDialog`, `tpSave`, `tpToggle`, `tpArchive`, the `TP_MSG`/`TP_FIELD_MSG` tables, client-side mirrors of the validation, `#research/topics` hash handling. All through `h()`. |
| `.claude-workspace/web/style.css` | `.tpbar`, `.tprow`, `.tpchip`, `.tpweight`, the switch, `.tp-archived`, dialog form layout, the narrow-screen stacking. Existing colour variables only. |
| `.claude-workspace/hooks/block-env.sh` | `:24` gets an explicit `research-dispatch\.topics\.token` (already matched, made explicit). `:60` gets `topics`, so Bash calls to `/api/topics…` are blocked. `:28` already blocks `research-token --scope topics`, now covered by a test. |
| `.claude-workspace/tests/topics_test.py`, `tests/topics.test.sh` | **New.** See Test plan. |
| `.claude-workspace/tests/fixtures/topics/{list_ok.json,hostile.json,cli_overlimit.json}` | **New.** |
| `.claude-workspace/tests/research_test.py` | `whoami` scope check on the monitor token (`monitor` ok, `admin` gives `too_powerful`, 404 tolerated). The token-path refactor stays green. |
| `.claude-workspace/tests/web_test.py` | Topic route guards and the smoke pass with the topics fixture. |
| `.claude-workspace/tests/web_smoke.js` | Optional topics fixture argument; render the Topics sub-view and assert. |
| `.claude-workspace/tests/block-env.test.sh` | New cases (Test plan). |
| `README.md` | "Research tab, Topics": setup, pairing requirement, states, what is audited. |
| `.claude-workspace/docs/design/2026-11-07-dashboard-topics-panel.md` | This document. |

## Test plan
All tests use fakes only: placeholder `rd_pat_TEST…` tokens, temp state directories, a fake valkyrie-dispatch `http.server` on a random loopback port. Never port 8080, never real token files, never `.env`.

**valkyrie-dispatch (pytest, ASGI transport)**
1. **`tests/test_security.py`, table-driven `scope_allows`:** every pair of {monitor, read, topics, admin, bogus} × {monitor, read, topics:read, topics:write, admin, bogus} against the expected matrix. Also: `parse_api_tokens` accepts `topics`, and a bogus scope raises with the new message.
2. **`tests/api/test_route_scopes.py` (new):** walk `app.routes`. For each route, call it with a `topics` token and assert 401/403 versus allowed. **Only** `GET /api/v1/topics`, `GET /topics/{id}`, `POST /topics`, `PATCH /topics/{id}` and `GET /auth/whoami` pass the auth layer. `DELETE /topics/{id}`, `/feedback`, `/usage/gate` and `/monitor/summary` return 403. A new route that forgets a dependency fails this test.
3. **`test_topics_api.py`:**
   - Extend `test_auth` (`:224`) with the topics token: list, get, create and patch work. `monitor` gets 403 on all of them, and `read` gets 403 on writes.
   - `updated_at` is present and ends in `Z`.
   - **If-Match:** a matching value gives 200 and a new ETag. A stale value gives 412 `topic_changed`, and the DB is unchanged. A missing header behaves as today. A concurrent pair (two PATCHes with the same `If-Match`, `asyncio.gather`) gives exactly one 200 and one 412.
4. **`tests/test_ratelimit.py` + API:** with a fake clock, a `topics` token gets write 61 → 429 with `Retry-After`. The window slides. An admin token is never limited. GETs are never limited.
5. **`tests/api/test_auth_api.py`:** `whoami` gives the name and scope for each scope. No token gives 401. The response keys are exactly `{name, scope}`.
6. **Audit:** capture structlog. Create and patch a topic named `SECRET-NAME` with keyword `SECRET-KW`. The log lines contain `topic_id`, `fields` and `token`, and **contain neither** string.
7. **No run endpoint:** the existing route-table assertion still passes.

**Dashboard: `tests/topics_test.py`** (the `check()` style, `NIU_TOPICS_TOKEN_FILE`, `NIU_RESEARCH_CONFIG`, `NIU_EVENTS_DB` set)
1. **Table-driven `validate_create`/`validate_patch`** (about 40 rows, `(input, expected_body | {field: code})`):
   - Name: empty, one character, 120 and 121 code points, whitespace collapse, `<`, `` ` ``, `{`, ` `, `\x00`, RTL override (category Cf).
   - Keywords: 0, 1, 20 and 21 items, an 81-character term, duplicate terms differing in case, a blank line, a comma inside a term.
   - Excluded terms: 50 and 51 items.
   - Weight: `0.09`, `0.1`, `5.0`, `5.01`, `NaN`, `inf`, `"1"`, `true`.
   - `is_active`: `"true"`, `1`.
   - Unknown key `notes` or `agent_type` in a patch gives 400. An empty patch gives `empty`. A 16 KiB+ serialized body gives `toolarge`.
2. **`shape_topic`:** `hostile.json` (extra keys `prompt_text`, `token`, an uppercase or `../` id, `agent_type:"admin"`, a 10,000-character name, 500 keywords, a control character) drops or caps per §2, and the output keys are exactly the allowlist. `cli_overlimit.json` (a 180-character name, 30 keywords) is kept and flagged `over_api_limits`.
3. **Paths and SSRF:** the IDs `../usage/gate`, `%2e%2e`, `00000000-0000-0000-0000-00000000000G`, an uppercase UUID and `id?x=1` are rejected before any request, and the fake server records zero hits. Query values come only from enums. Redirect: a 302 to a capture server gives `incompatible`, and the capture server sees nothing. With `http_proxy` set, no traffic reaches the proxy capture.
4. **Scope gate:**
   - `whoami` returning `admin` or `read` gives `too_powerful`, and **no topic request is made** (the fake asserts its counter).
   - `monitor` gives `wrong_scope`. 404 gives `incompatible`.
   - A cache hit within 300 s makes one `whoami` call.
5. **States:** no file gives `not_configured`. Mode 0644 or a symlink gives `error/perms`. Connection refused or a timeout gives `unreachable`, with the last good list `stale:true`. 401 gives `unauthorized`. A 2 MiB list gives `error/toolarge`.
6. **Error mapping** (table): each upstream `(status, body)` maps to a dashboard code. Upstream `message` and `msg` never appear in the output. An `existing_id` that isn't a UUID is dropped.
7. **Rate limit:** with a fake clock, the 11th write in a minute from one device gives 429 without an upstream call. A second device is independent.
8. **Audit:** after a create and a patch, the `events` rows have `event = "TopicChange"`, and the summary matches `^(create|update|activate|deactivate) [0-9a-f]{8} \[[a-z_,]*\] by ` and contains no topic text.
9. **Token never leaks:** run `niu-dash serve` against the fake with 401, 403 and 500 responses. Grep every response body, `/api/state`, `/api/events` and stderr for the token: no match.

**Dashboard: `tests/web_test.py`**
- `GET /api/topics` without a cookie gives 401 `not_paired`.
- POST create without CSRF gives 403. Cross-origin gives 403. With CSRF and the same origin but no cookie gives 401.
- With a cookie (seed `web-sessions.json` in the temp state directory with a known hash, as the existing pairing tests do), create through the fake gives 200.
- The CSP header is unchanged. The index has `id="rsview-topics"` and `id="tpdlg"`.

**Dashboard: `tests/web_smoke.js`** (fake DOM)
- Render the Topics sub-view with `list_ok.json`: rows are grouped per agent, and the counts read "General 2/200".
- With `hostile.json` names such as `<img src=x onerror=alert(1)>`, the fake DOM has **zero** `img` and `script` elements, and the row `textContent` equals the literal string.
- Assert that the Topics code never assigns `innerHTML` (wrap the setter in the fake DOM and fail on any write while rendering the Topics view).
- `state: "not_paired"` shows the pairing message, and all write buttons are disabled.

**Dashboard: `tests/block-env.test.sh`**
- `Read ~/.local/state/niu-heimar/research-dispatch.topics.token` is blocked. `Bash cat …/research-dispatch.topics.token` is blocked.
- `Bash niu-dash research-token --scope topics` is blocked.
- `Bash curl -X POST http://127.0.0.1:8765/api/topics/update` is blocked. `Bash curl http://127.0.0.1:8765/api/topics` is blocked.
- `Bash curl http://127.0.0.1:8765/api/research` is still allowed.

**Manual checks** (after both PRs are merged)
1. Without pairing, the Topics view shows the pairing message, and devtools shows no `/api/topics` data.
2. Store an **admin** token with `--scope topics`. The setter deletes it with a scope message. Force it into the file by hand, and the panel shows `too_powerful` with no requests in valkyrie's logs.
3. Add, edit, toggle, archive and restore a topic. `rd topics list --all` agrees. The Events tab shows `TopicChange` rows with IDs only.
4. Edit a topic in the CLI while the dialog is open. Saving shows "changed elsewhere".
5. Stop the container. The list greys out, writes are disabled, and the Status view still works.
6. Check dark mode and a narrow phone viewport over the tailnet (a paired phone).

## Rollout
1. **PR A in kaiserdrake/valkyrie-dispatch** (branch `feat/topics-scope`): rebase on `main` after PR #1 (M0/M1) merges. Run `/dual-review` with **security-reviewer on Opus** (it changes the scope model). Merge, release, then `docker compose pull && docker compose up -d`. It is additive: existing tokens and duenext behave the same.
2. **PR B in kaiserdrake/niu-heimar** (through `/workspace-sync`, `/dual-review`, security on Opus: hook, credential file, new write proxy). Then `systemctl --user restart niu-dash`.
   - **2a, fallback:** if review capacity is short, PR B ships only `GET /api/topics` (paired), the list UI and the hook and token changes. Create and update go in a follow-up PR. The same token works for both, but a read-only phase still leaves a write-capable token on disk. If that matters, delay the token setup until the write PR.
3. **Order:** A must be deployed before the panel works. If B ships first, `whoami` returns 404, so the Topics view shows `incompatible` and the monitor check tolerates the 404. Nothing breaks.
4. **User setup (one time):**
   1. `cd ~/WORKSPACE/PERSONAL/valkyrie-dispatch && docker compose exec app rd gen-token --name niu-dash-topics --scope topics`
   2. Add the printed `niu-dash-topics:topics:<sha256>` to `API_TOKENS` in the project's env file yourself, then run `docker compose up -d`.
   3. `~/WORKSPACE/.claude-workspace/bin/niu-dash research-token --scope topics` and paste the token (the setter checks the scope).
   4. Pair the browser if it isn't already (`niu-dash pair`), then open Research, then Topics.
5. **Rollback:**
   - Remove the `niu-dash-topics` entry from `API_TOKENS` and restart, which revokes the token at once. Or run `niu-dash research-token --scope topics --remove`.
   - Reverting PR B removes the routes. Reverting PR A is safe once no `topics` tokens are configured. With one still in `API_TOKENS`, startup rejects the unknown scope, so remove the entry first.

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| The scope refactor accidentally widens `read` or `monitor` | Table-driven `scope_allows` plus the route-scope walk test. The existing monitor and topics auth tests stay. |
| The user stores an admin token in the topics file | `whoami` at setup and at runtime, and the panel refuses anything except `topics`. |
| CLI-created topics break the API rules on edit | Display caps, the `over_api_limits` flag, an explanatory notice, and field highlighting. |
| Lost updates from concurrent edits (CLI, duenext, feedback mutes) | `If-Match` checked inside the service write lock gives 412, and the UI offers a reload. |
| More than 200 archived topics | `archived_truncated`, shown as "showing 200 archived topics; use `rd topics list --all` for the rest". |
| Weight semantics change in M3 (decay: `TopicOut.weight` becomes the effective weight) | Open question 2. Until M3, edits set the stored weight directly. |
| The 16 KiB body limit is hit with many long non-ASCII terms | Measured locally before sending, giving the `toolarge` message "Too much text: shorten keywords or excluded terms". |

## Open questions (with defaults)
1. **Topic notes:** should topics get a free-text `notes` field (≤500 characters, the `Notes` type at `schemas.py:50-55`)? **Default: no.** It needs an Alembic migration (`0003_topic_notes`, `notes TEXT NULL`) and a decision on whether notes ever reach prompts. Notes would be one more injection carrier, so they would be **dashboard-only, never prompted**. They could be added later as an additive field.
2. **Weight editing after M3 decay:** should the edit form set the base weight (with the effective weight shown read-only), or be hidden? **Default:** M3 adds `base_weight` to `TopicOut`, and the form edits that. Until then the form edits `weight`.
3. **Undo or history:** should valkyrie-dispatch keep a `topic_revisions` table with full before and after values, so a malicious or mistaken edit can be reverted? **Default: no** for now. The DB volume backup and the ID-only audit are enough for a single user. Revisit if the topics token is ever misused.
4. **Edit window:** should writes need a TTY-armed unlock (`niu-dash topics-unlock 15m`)? **Default: no.** Pairing plus scope, rate limits and audit are proportionate. It can be added without an API change.
5. **Monitor counts:** should `monitor.v1` also carry per-agent active topic counts (numbers only), so the Status view can show "12 topics" without pairing? **Default: yes, as a later additive field** (`service.topics: {general: n, …}`). Not part of these PRs.
