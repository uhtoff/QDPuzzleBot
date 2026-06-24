# Design — Web↔bang parity 5b (hunt creation) + 5c (hunt deletion)

Date: 2026-06-24
Scope: Phases **5b + 5c** of the web↔bang parity roadmap
(`.ai/archive/2026-06-24-web-parity-puzzle-edits/web-command-parity.md`, Tier C).
Predecessor: 5a hunt admin pane (`docs/superpowers/specs/2026-06-24-web-parity-hunt-admin-5a-design.md`,
shipped). This design supersedes the "Forward notes — 5b" block in that 5a doc.

## Goal

Give the web dashboard parity with the bang `!hunt` (create) and `!delete_hunt` commands:

- **5b** — create a live hunt from the all-hunts (HuntList) screen, provisioning everything the
  bang does (Discord category + channels, Google Sheet, optional team role).
- **5c** — delete a hunt from the per-hunt admin pane, tearing down all its puzzles, rounds,
  Discord channels/categories, and Google spreadsheets.

Both are **admin-gated** (`ADMIN_ROLES`), per guild — the same gate the bang commands and the 5a
admin pane use.

## Decisions (from brainstorming)

- **Full provisioning parity** for web create — extract the bang's `ctx`-coupled logic into a
  ctx-free `PuzzleService.create_hunt(guild, ...)`. (Same approach as the solve-variant extraction.)
- **Guild picker, admin-gated per guild** — the New-hunt form has a guild dropdown listing only
  guilds where the user holds an `ADMIN_ROLE`; create and delete both gate on `ADMIN_ROLES` in the
  target guild. The New-hunt button is hidden if the user is admin in zero guilds.
- **No `create_as_archived`** — a hunt is always created live. Archiving a past hunt is done
  afterward via the existing 5a archive toggle. (The separate **create-archive-sheet** option stays —
  that provisions the solved-puzzles sheet from day one; it is a different thing from `archived`.)
- **`delete_hunt` deletes the entire Google spreadsheet(s)** — the whole Drive file(s), not just
  tabs — for both `google_sheet_id` and `archive_google_sheet_id`. This behaviour lives in the
  shared service method so the bang and the web endpoint behave identically. If the bang does not
  currently delete the file, this is a deliberate behaviour change applied via the shared seam.

## Out of scope — DO NOT BUILD

- Generic `!update_settings` surface (shipped separately as part of 5a-era work).
- Editing settings during creation beyond the named fields (use the 5a admin pane post-create).
- `create_as_archived` (dropped, see above).
- Any change to the Discord welcome embed or `start_timestamp` Discord rendering.
- Reviving the retired `parallel_hunt` feature.

## Core architecture — two ctx-free service extractions

Both bang commands today are coupled to `ctx`. Web has no `ctx`. Extract the mutation/provisioning
logic into `PuzzleService`, then rewire the bang commands onto it (keeping their `ctx`-derived
guild, confirm prompt, and welcome embed). This is the riskiest part and is guarded by service
tests written first.

### `create_hunt`

```python
async def create_hunt(self, guild, *, name, url, start_timestamp,
                      role_name=None, username=None, password=None,
                      create_archive_sheet=False) -> HuntData:
    ...
```

Owns, in order:
1. Duplicate-name check (`HuntJsonDb.check_duplicates(name)`) — caller maps to 409.
2. Space check (Discord category/channel headroom).
3. Discord provisioning: hunt category, welcome/general channel, solved category-or-divider
   (per the `SOLVE_CATEGORY` setting).
4. Google Sheet (`create_hunt_spreadsheet(name)`); if `create_archive_sheet`, also
   `create_hunt_archive_spreadsheet(name)`.
5. Optional team role: reuse an existing Discord role by name or create one.
6. Build `HuntData` (generated `uid`, `category_id`, `channel_id`, `guild_id`, `url`,
   `google_sheet_id`, `archive_google_sheet_id`, `role_id`), set `start_timestamp` **and mirror
   into `start_time`** (the 5a idiom), set `username`/`password` if provided.
7. `HuntJsonDb.commit(new_hunt)`; return it.

The bang `!hunt` keeps its `"name:url[, role]"` parsing, derives `guild` from `ctx`, calls the
service, then sends its pinned welcome embed (embed stays in the cog).

### `delete_hunt`

```python
async def delete_hunt(self, guild, hunt) -> list[str]:  # returns failed-channel names
    ...
```

Owns the teardown the bang does, mirroring `delete_round`'s isolation contract:
1. Delete every puzzle (sheet tab + DB row + Discord channel), isolating per-puzzle channel
   failures into the returned list.
2. Delete every round (Discord category + solved divider + DB row).
3. **Delete the hunt's Google spreadsheet file(s)** — `google_sheet_id` and, if set,
   `archive_google_sheet_id` — as whole Drive files. Needs a gsheet-cog "delete spreadsheet file"
   method; the cog currently only exposes `delete_puzzle_spreadsheet` (a tab delete), so add the
   file-level delete if absent.
4. Delete the hunt's Discord welcome channel + category.
5. `HuntJsonDb.delete(hunt.id)`.

The bang `!delete_hunt` keeps its `ConfirmView` prompt + impact summary, derives `guild`/`hunt`
from `ctx`, calls the service, and warns on failed channels.

## 5b — create endpoint, payload, frontend

### `POST /api/hunts`

- `@login_required` → resolve target guild from body `guild_id` → `require_admin(guild_snowflake)`.
- Body: `{guild_id, name, url, start_timestamp, username?, password?, role_name?,
  create_archive_sheet?}`. **Mandatory:** `guild_id`, `name`, `url`, `start_timestamp`.
- Validation: missing mandatory field → 400; duplicate name → 409; bot/guild unavailable → 503;
  Discord failure → reuse `_discord_error_response`; non-fatal sheet failure → `sheet_warning`.
- Resolve the guild object via `current_app.bot.get_guild(snowflake)`, call
  `cog.service.create_hunt(guild, **fields)`, `_audit("create_hunt", hunt_uid=...)`, return the new
  hunt's `uid` (+ detail).

### Admin-guild source for the picker

Provide an admin-filtered guild list for the dropdown — extend the existing guilds data with a
per-guild `is_admin` flag, or a small `GET /api/guilds?admin=1`. The dropdown lists only guilds
where the user holds an `ADMIN_ROLE`.

### Frontend

- A **"New hunt"** button on HuntList, hidden when the user is admin in zero guilds.
- Modal form: guild dropdown (admin guilds only), **name**, **url** (required), **start** datetime
  (required), an **Advanced** collapsible (login `username`/`password`, team-role name), and a
  **create-archive-sheet** checkbox.
- `useCreateHunt` mutation → `POST /api/hunts`; on success invalidate `["hunts"]` and navigate to
  the new hunt board.

## 5c — delete endpoint, frontend

### `DELETE /api/hunts/<uid>`

- Mirrors `delete_round`'s thin controller: resolve hunt → `require_admin` → call
  `cog.service.delete_hunt(guild, hunt)` → `_audit("delete_hunt", hunt_uid=...)` →
  `{ok: true, failed_channels?: [...]}`.
- **No archived-lockout** — an archived hunt must still be deletable.

### Frontend

- A **"Delete hunt"** danger-zone in the existing `AdminSettingsPanel` (Manage tab — admin-gated
  and reachable even when archived, per the 5a fix).
- Reuse `TypedConfirmModal` with `confirmPhrase = hunt.name` and an impact summary (N puzzles, all
  rounds, Discord channels, **Google spreadsheets — permanently deleted, irreversible**).
- `useDeleteHunt` mutation; on success invalidate `["hunts"]` and navigate back to HuntList.

## Testing

- `tests/test_create_hunt_service.py` (NEW) — mock `guild` + gsheet cog; assert `HuntData` fields,
  `start_time` mirror, duplicate guard, role reuse-vs-create, `create_archive_sheet` flag,
  provisioning call order.
- `tests/test_delete_hunt_service.py` (NEW) — assert puzzles + rounds + hunt torn down, spreadsheet
  file delete(s) called for both sheet ids, failed-channel isolation, DB rows deleted.
- Endpoint wrappers untested per project convention. `cd frontend && npx tsc --noEmit` clean.
- Helpers uniquely named and local to each test file (the parallel gate shards by file).

## Phases (tests-first, each independently shippable)

1. **Failing service tests** for `create_hunt` + `delete_hunt` (Opus).
2. **Extract `create_hunt`** service method + rewire bang `!hunt`.
3. **Extract `delete_hunt`** service method (incl. whole-spreadsheet delete; add the gsheet
   file-delete method if absent) + rewire bang `!delete_hunt`.
4. **`POST /api/hunts`** + admin-guild source + payload.
5. **`DELETE /api/hunts/<uid>`**.
6. **Frontend New-hunt form** on HuntList.
7. **Frontend Delete danger-zone** in `AdminSettingsPanel`.
8. **Verification** — full `pytest`, `tsc --noEmit`, `ruff`, manual create→delete click-through.

## Verification & rollback

- Service extractions guarded by the new service tests (run after each extraction).
- Each phase is an independent commit; revert a phase without touching others.
- Manual: create a hunt from the web (confirm Discord category/channels + sheet provisioned, lands
  on the board), then delete it (confirm channels, categories, and spreadsheet files all removed).

## Constraints

- Reuse existing seams (`require_admin`, `_audit`, `_visible_hunt_by_uid`, `_discord_error_response`,
  `TypedConfirmModal`, `HuntJsonDb`). Invent none.
- `start_timestamp` writes mirror into `start_time` (the 5a idiom). Do not touch the Discord embed.
- Keep bang behaviour identical apart from the shared-seam changes (the now-correct spreadsheet
  delete in `delete_hunt`).
- Do not refactor unrelated code; stay within the files these phases touch.
