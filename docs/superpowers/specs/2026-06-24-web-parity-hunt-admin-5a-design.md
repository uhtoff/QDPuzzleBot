# Design: Web ↔ bang parity — Phase 5a (per-hunt admin pane)

Date: 2026-06-24
Status: approved (brainstormed) — ready for implementation handoff
Part of the **web-dashboard-command-parity** initiative (see
`.ai/archive/2026-06-24-web-parity-puzzle-edits/web-command-parity.md`, Phase 5 / Tier C).

## Goal

Give the web dashboard parity with the per-hunt **settings** bang commands, behind an
admin-only pane folded into the existing **Manage** tab (the one that already hosts
reconcile). Hunt **creation** (5b) and **deletion** (5c) are explicitly out of scope here;
this phase is the safe, reversible, field-edit slice.

## Scope

### In scope (5a)
- Edit per-hunt settings on web: `url`, hunt start time, shared login (`username`/`password`),
  and the advanced URL-building keys `url_sep` / `puzzle_prefix`.
- Archive / unarchive a hunt (the `archived` bool — reversible).
- Create the solved-puzzles archive sheet (`set_to_archive` — one-way action).
- A read-only **hunt-info panel** on the dashboard (website · start · sheet links · login),
  the web equivalent of the Discord welcome message — this is what makes login *display*
  parity work.
- Extract `set_start` / `set_login` into a committing service method and rewire the bang
  commands onto it (fixes a real persistence bug — see Background).

### Out of scope
- 5b hunt creation (HuntList "New hunt" form) — captured as forward notes below.
- 5c `delete_hunt`.
- The generic `!update_settings` for round / guild / puzzle scopes — only the per-hunt keys
  named above are exposed; we do **not** bulk-expose the settings surface.
- Discord-side archiving (`!archive_round/category/channel/solved_manually`) — Tier D, excluded.

## Background findings (why this isn't just "wrap a service")

1. **`set_start` / `set_login` don't persist.** Both mutate `self.get_hunt(ctx)` in place and
   **never call `HuntJsonDb.commit()`** (`puzzles.py:1531-1532`, `:1547`). Contrast
   `set_to_archive` (`:505`) and `update_settings`→`save_settings` (`:1481`), which do commit.
   The in-memory object changes for the running process but is not durably saved. The web path
   must commit, so we extract a committing service method and rewire both bang commands onto it.
2. **Two unsynced start columns.** `!set_start` writes `start_timestamp: int` (read only by the
   Discord info embed, `puzzles.py:1670`). The dashboard reads `hunt.start_time: datetime`
   (`web/data.py:150`), which **nothing in the codebase ever writes** — so the dashboard hunt
   start time is always null today. **Decision (option B):** on every start write, set
   **both** `start_timestamp` *and* mirror it into `start_time`
   (`start_time = datetime.fromtimestamp(ts, tz=pytz.UTC)`). Web uses a datetime picker → unix; the
   Discord embed is left untouched; the dashboard `start_time` finally populates.
3. **"Archive" is two different things.** `set_to_archive` creates a *second Google Sheet for
   solved puzzles* and sets `archive_google_sheet_id` (one-way; errors if already set) — it does
   **not** mark the hunt finished. The `archived: bool` flag (hunt finished → read-only +
   broadened visibility) has **no dedicated bang command**; today it is only reachable via
   `!update_settings archived true`. We treat these as two distinct controls.
4. **Login is already team-visible.** `send_initial_hunt_channel_messages`
   (`puzzles.py:1676-1680`) prints `Hunt Username:` / `Hunt Password:` in plaintext into the
   hunt welcome message, visible to every team member with hunt access. So credentials are not
   secret from the team — only *editing* is admin-restricted.
5. **`parallel_hunt` already retired** (commit `bc60986`) — removed from scope entirely; not
   part of this pane.

## Auth model

- All 5a controls are **admin-gated** (`ADMIN_ROLES` = Moderator/mod/admin), matching the bang
  commands (`set_start`, `set_login`, `set_to_archive`, `update_settings` all use
  `@has_any_role(*ADMIN_ROLES)`). This is **stricter** than the `ORGANISER_ROLES` gate used by
  the rest of the dashboard (and by reconcile, which stays organiser-gated in the same tab).
- `user_can_manage(user_id, guild, roles=...)` and `require_organiser(guild, roles=...)` already
  accept a `roles` arg, so admin gating is `roles=ADMIN_ROLES`. Add a thin `require_admin`
  wrapper for clarity, and an `is_admin` flag in the hunt-detail payload to drive the frontend.
- **Admin endpoints must bypass the archived-lockout.** Puzzle-edit endpoints return 403 when
  `hunt["archived"]`. The settings/archive endpoints must *not* — otherwise an archived hunt
  could never be un-archived or corrected. They gate on `ADMIN_ROLES` only and operate on
  archived hunts too.

## Field-by-field spec

| Item | Bang today | Web treatment | Endpoint family |
|---|---|---|---|
| `url` | `!update_settings url` (no dedicated cmd) | editable text, `http(s)` validation, blank clears | settings |
| start time | `!set_start <unix>` (no commit) | datetime picker → writes `start_timestamp` **+** mirrors `start_time` | settings |
| `username` / `password` | `!set_login user:pass` (no commit) | two fields; **edit** admin-only; **display** to team, password **click-to-reveal** | settings |
| `url_sep` | `!update_settings url_sep` | text input in **Advanced** collapsible | settings |
| `puzzle_prefix` | `!update_settings puzzle_prefix` | text input in **Advanced** collapsible | settings |
| `archived` | `!update_settings archived` | **Archive / Unarchive toggle**, plain (reversible) confirm | action |
| `set_to_archive` | `!set_to_archive` | one-time **"Create archive sheet"** button → sheet link once set | action |

## Backend design

### Service method (settings)
Add `update_hunt_settings(self, hunt, **fields)` to `PuzzleService`:
- Whitelist of accepted keys: `url`, `start_timestamp`, `username`, `password`, `url_sep`,
  `puzzle_prefix`. Only keys explicitly provided are applied (sentinel/`UNSET` pattern, so a
  caller can set just one field).
- When `start_timestamp` is provided, also set
  `hunt.start_time = datetime.datetime.fromtimestamp(ts, tz=pytz.UTC)` (the option-B mirror;
  uses the codebase's `pytz.UTC` convention, cf. `puzzle_service.py:1042`).
- Single `HuntJsonDb.commit(hunt)` at the end.
- Rewire bang commands onto it: `set_start` → `update_hunt_settings(hunt, start_timestamp=ts)`;
  `set_login` → `update_hunt_settings(hunt, username=u, password=p)`. The bang keeps its own
  `username:password` colon parsing and its embeds/messaging; it just delegates the mutate+commit.

### Endpoints (`bot/web/actions.py`, mirroring the `retag_puzzle` pattern)
- `PATCH /api/hunts/<uid>/settings` — body = JSON of whitelisted keys. `@login_required` →
  `require_admin` → load hunt → (no archived-lockout) → `update_hunt_settings(...)` → `_audit`
  → return refreshed hunt detail. Reject any non-whitelisted key with 400.
- `POST /api/hunts/<uid>/archive` — body `{ "archived": true|false }`. Flips `hunt.archived`,
  commits, audits. Reversible; no typed-confirm server-side (the confirm is a frontend dialog).
- `POST /api/hunts/<uid>/archive-sheet` — calls the gsheet cog's
  `create_hunt_archive_spreadsheet(hunt.name)`, sets `archive_google_sheet_id`, commits, audits.
  Refuses (409) if `archive_google_sheet_id` already set (parity with the bang). Fail gracefully
  (clear error) if the gsheet cog is unavailable.
- Audit action names: `hunt_settings`, `hunt_archive`, `hunt_archive_sheet`.
- **Locking:** field edits are single-row, low-contention, infrequent admin ops; last-write-wins
  is acceptable (the bang itself takes no hunt lock). No new lock required. If a hunt-level lock
  is later introduced for create/reconcile, these can adopt it.

### Data layer (`bot/web/data.py`)
- Extend the hunt-**detail** fetch + serializer to include `start_timestamp`, `username`,
  `password`, `url_sep`, `puzzle_prefix`, and computed `is_admin`. (Current `hunt_to_json` at
  `data.py:140` and the detail SELECTs at `:103/:111` do not fetch these.)
- `username`/`password` are returned only to users who can see the hunt (same gate as the rest of
  the hunt detail — i.e. the team `role_id` audience, matching the Discord welcome message).

## Frontend design

- **Manage tab:** keep `ReconcilePanel` (organiser-gated) and add a **HuntAdminPanel** rendered
  only when `is_admin`. The admin panel contains:
  - the settings form (url, start picker, username, password),
  - an **Advanced** collapsible (url_sep, puzzle_prefix),
  - the **Archive / Unarchive** toggle (plain confirm dialog),
  - the **Create archive sheet** button (→ link once `archive_google_sheet_id` is set).
- **Read-only hunt-info panel** (visible to any team member who can see the hunt): website ·
  start time · Google Sheet link · archive sheet link (if any) · username · password with
  **click-to-reveal**. This is the dashboard equivalent of the Discord welcome message.
- **types.ts:** extend `HuntDetail.hunt` with `start_timestamp`, `username`, `password`,
  `url_sep`, `puzzle_prefix`, `is_admin`.

## Implementation phases (ordered, each independently committable)

1. **Service extraction + bang rewire (tests first).** Add `update_hunt_settings` with the
   whitelist + start mirror + commit. Rewire `set_start` / `set_login`. Service-level unit test
   asserting each field persists and that a start write sets both `start_timestamp` and
   `start_time`. (Mirrors the Phase 1 solve-variant extraction.)
2. **Settings endpoint.** `PATCH /api/hunts/<uid>/settings` + `require_admin` + data-layer fields
   + payload `is_admin`. Reject non-whitelisted keys.
3. **Action endpoints.** `POST .../archive` and `POST .../archive-sheet`.
4. **Frontend read-only info panel** (website/start/sheets/login click-to-reveal), gated to
   hunt-visible users.
5. **Frontend admin panel** in the Manage tab (settings form + Advanced + archive toggle +
   create-sheet button), gated on `is_admin`.
6. **Verify:** `tsc --noEmit`, the service/equivalence tests, and a manual dashboard
   click-through (edit each field, archive/unarchive, create archive sheet, reveal password).

## Testing & verification

- New `tests/test_hunt_settings_service.py`: whitelist application, start mirroring, commit,
  non-whitelisted key rejection at the service boundary.
- Bang ↔ web equivalence for `set_start` / `set_login` (both now route through
  `update_hunt_settings`): assert identical DB state, in the style of
  `tests/test_solve_equivalence.py`.
- Endpoint wrappers are low-risk (auth + reuse), matching existing untested wrappers; at minimum
  test the 400-on-bad-key and the archived-lockout bypass.
- Run with `timeout 180 pipenv run python -m pytest tests/ -q`.

## Forward notes — 5b web hunt creation (NOT built here)

When the HuntList "New hunt" form is built:
- `url` / link — **mandatory**.
- start time — **mandatory on web create** (optional on bang create).
- login — optional.
- **create-as-archived** — optional checkbox (to add a past/finished hunt as a record).
- **create separate archive sheet** — optional checkbox (set up the solved-puzzles sheet from
  day one rather than retrofitting via the 5a button).

## Rollback / recovery

- Phases are independent commits; revert any one without touching the others.
- The orphaned `parallel_hunt` DB column (from `bc60986`) remains droppable via a later
  migration if ever desired; not part of this phase.
