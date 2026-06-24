# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Started: 2026-06-24
Status: ready for handoff — Phase 1 (tests) not yet started. Design @ 7a51235; 5a (predecessor) merged to main.

## Goal

Web↔bang parity phases 5b + 5c: create a hunt from the web (HuntList "New hunt" form) and delete a
hunt from the web (admin pane danger-zone), both backed by new ctx-free `PuzzleService.create_hunt`
/ `delete_hunt` methods that the bang `!hunt` / `!delete_hunt` rewire onto.

## Context the executing agent needs (not obvious from files)

- **The create path is more cog-coupled than the solve variants were.** Today's `create_hunt`
  (`puzzles.py:426`) leans on cog helpers/constants: `check_available_space`, `get_or_create_channel`,
  `clean_name`, `SOLVE_CATEGORY`, `SOLVE_DIVIDER`, `GENERAL_CHANNEL_NAME`, `get_solved_puzzle_category`,
  `get_guild_data`. The closest existing **service** precedent is `create_mini` (`puzzle_service.py:1029`)
  — a ctx-free create that takes `guild`, creates Discord objects directly (`guild.create_*`), resolves
  gsheet via `self._gsheet_cog`, commits, and posts a best-effort welcome. Follow that shape; lift
  module-level `clean_name`; keep `check_available_space` and the welcome embed in the cog.
- **Whole-spreadsheet delete does not exist yet.** The gsheet cog only deletes *tabs*
  (`delete_puzzle_spreadsheet` → `delete_sheet`). Spreadsheets are Drive files (`get_drive().files()
  .copy/create`). Phase 3 adds `delete_hunt_spreadsheet(file_id)` using `get_drive().files().delete`.
  This is a deliberate behaviour change: the bang `!delete_hunt` gains whole-file deletion via the
  shared seam (user-approved).
- **`delete_hunt` deletes BOTH sheet files** — `google_sheet_id` and `archive_google_sheet_id` (if set).
- **Admin gating, per guild.** Create/delete gate on `ADMIN_ROLES` (Moderator/mod/admin), same as the
  bang commands and the 5a admin pane — NOT the looser `ORGANISER_ROLES`. `require_admin` already
  exists in `actions.py` (added in 5a). The New-hunt guild dropdown lists only guilds where the user
  holds an `ADMIN_ROLE`.
- **No archived-lockout on delete.** `DELETE /api/hunts/<uid>` must not inherit the `if archived: 403`
  guard the puzzle-edit endpoints use — an archived hunt must stay deletable. (The Manage tab is
  already reachable for archived hunts to admins, per the 5a fix `d51c109`.)
- **`start_timestamp` mirrors into `start_time`** (the 5a idiom). Start time is mandatory on web create.
- **No `create_as_archived`** (user dropped it). Hunts are created live; archive afterward via the 5a
  toggle. The separate **create-archive-sheet** checkbox stays (provisions the solved-puzzles sheet).
- 5a is on main: `update_hunt_settings`, `PATCH /api/hunts/<uid>/settings`, archive + archive-sheet
  endpoints, `AdminSettingsPanel`, `HuntInfoPanel`. Reuse `TypedConfirmModal` (used by delete_puzzle /
  delete_round) for the delete confirm.

## What NOT to touch

- No `create_as_archived`; no generic `update_settings` surface; no Discord welcome embed / start
  rendering changes; do not revive `parallel_hunt`.
- Don't widen scope beyond the six create fields + the two delete behaviours named in the spec.
- Keep `ReconcilePanel` and the 5a panels as-is; the delete danger-zone is an addition to
  `AdminSettingsPanel`.

## If you get stuck

Diagnose with `superpowers:systematic-debugging` first. If an acceptance command fails twice without a
clear, understood fix (two-strikes rule), write `.ai/active/blocker.md` (what you tried + exact output)
and STOP — do not add scaffolding or invent harness machinery to route around a failing assumption.
The create test asserts the **stable contract** (committed HuntData fields, gsheet calls, duplicate
guard) — do NOT assert the exact Discord call sequence; that is decided in Phase 2.

## Next step

Start Phase 1 (Opus 4.8): write `tests/test_create_hunt_service.py` and
`tests/test_delete_hunt_service.py` from the skeletons in `current-spec.md`, confirm they fail only on
the missing `create_hunt` / `delete_hunt` methods, commit the tests alone.
