# Design — "solved puzzles sheet" rename + drop the dead `archive_time` trio

Date: 2026-07-02
Status: approved (design) — pending implementation plan

## Goal

The word **archive** is overloaded across three unrelated features in the codebase, which
makes the model hard to read and invites mistakes (e.g. treating `puzzle.archived` and
`hunt.archived` as the same thing). This change fixes the vocabulary for one of those
features and deletes a second, dead one.

The three "archive" concepts today:

1. **Discord channel archiving** — `!archive_round` / `!archive_category` /
   `!archive_channel` / `!archive_solved_manually` (`bot/cogs/puzzles.py`). Copies a
   channel's history into a thread, then deletes the channel. **Bang-only, by design** —
   unchanged by this work (only its docs note is corrected).
2. **The "archive sheet" = a second Google spreadsheet that holds solved puzzles' tabs.**
   Only solved **feeders** move there; **target/meta puzzles stay on the main sheet**
   (`archive_puzzle_spreadsheet` moves a tab only when `puzzle.is_target is False`,
   `bot/cogs/puzzles_gsheet.py`). This is the feature we **rename** to "solved puzzles
   sheet".
3. **The hunt `archived` flag** — `hunts.archived` boolean, toggled by the web "Archive
   hunt" action; moves a hunt into the dashboard's "archived" list and makes it read-only.
   **Unrelated and unchanged.**

Separately, a **fourth, dead** feature exists: an "auto-archive solved channels" path built
around `*.archive_time` columns and `get_solved_*_to_archive()` methods that **nothing ever
calls**. We delete it.

## In scope

- **Part A — rename** feature #2 from `archive*` to `solved_sheet` / `on_solved_sheet`
  across DB, backend, command, web API, and frontend; user-facing prose becomes
  "solved puzzles sheet".
- **Part B — drop** the dead `archive_time` trio (`hunts`, `tag_group`, `puzzles`) and the
  uncalled `get_solved_*_to_archive()` methods plus their now-orphaned writes and tests.
- Correct the `docs/commands.md` §Archiving note and the `web-dashboard-command-parity`
  memory.

## Out of scope

- The Discord channel-archiving commands (#1) — behaviour unchanged; docs note corrected only.
- The `hunts.archived` dashboard flag (#3) — untouched.
- The `status` enum — its `"archived"` backing value simply becomes unreachable after Part
  B; we do **not** change enum values (per AGENTS.md).
- `puzzles.archive_time` was set by both the solved-sheet move and channel archiving, but is
  **read nowhere live**, so removing it loses no behaviour.

## Constraints

- The generic `commit()` writes each dataclass field by its column name, so a renamed field
  and its DB column **cannot diverge** — the migration and the backend rename must land in
  the same commit.
- Follow **migration → deploy → code**: for renames/drops the old code breaks the moment the
  migration runs, so migration + renamed code deploy together (a brief bot restart). Zero-
  downtime add/dual-write/drop is unnecessary for this bot on its test DB.
- Migration number **024** is currently free in all worktrees; re-check for collisions
  immediately before merge.
- Do not touch unrelated dirty files; keep the `hunts.archived` flag and channel-archiving
  paths intact.

## Part A — rename map

### Migration 024 (renames)

| Table | Old column | New column | Type (restated for `CHANGE COLUMN`) |
|---|---|---|---|
| `hunts` | `archive_google_sheet_id` | `solved_sheet_id` | `varchar(50) DEFAULT NULL` |
| `puzzles` | `archived` | `on_solved_sheet` | `tinyint(1) DEFAULT 0` |

(Verify the live column definitions at implementation time before restating types.)

### Backend

- `HuntData.archive_google_sheet_id` → `solved_sheet_id` (`bot/store/hunt_data.py`)
- `PuzzleData.archived` → `on_solved_sheet` (`bot/store/puzzle_data.py`)
- `bot/web/data.py`: the `archive_google_sheet_id` SELECT columns → `solved_sheet_id`;
  `puzzles.archived` → `puzzles.on_solved_sheet`; JSON output key `"archived"` →
  `"on_solved_sheet"` (puzzle payload only — the hunt `"archived"` key stays).
- `bot/cogs/puzzles_gsheet.py`:
  - `get_archive_spreadsheet_id` / `set_archive_spreadsheet_id` → `get_solved_sheet_id` /
    `set_solved_sheet_id`; internal `archive_spreadsheet_id` attr → `solved_sheet_id`
  - `create_hunt_archive_spreadsheet` → `create_solved_sheet`
  - `archive_puzzle_spreadsheet` → `move_puzzle_to_solved_sheet`
  - `restore_puzzle_spreadsheet` → `restore_puzzle_from_solved_sheet`
  - `move_puzzle_spreadsheet(to_archive, …)` → `move_puzzle_sheet(to_solved, …)`
  - all `puzzle.archived` reads → `puzzle.on_solved_sheet`
- `bot/core/puzzle_service.py`: matching call-site + field renames.

### Command

- `!set_to_archive` → **`!use_solved_sheet`**, with `aliases=["set_to_archive"]` so the old
  name keeps working. Docstring/`description=` reworded to "solved puzzles sheet" (≤100 chars
  for the slash surface).

### Web + API

- Route `POST /hunts/<uid>/archive-sheet` → `/hunts/<uid>/solved-sheet`
- Handler `post_hunt_archive_sheet` → `post_hunt_solved_sheet`
- Create-hunt optional key `create_archive_sheet` → `create_solved_sheet`
  (`bot/web/actions.py` `_CREATE_HUNT_OPTIONAL`; `bot/core/puzzle_service.py` create path)
- JSON field `archive_sheet_base_url` → `solved_sheet_base_url` (`bot/web/api.py`)
- Error key `archive_sheet_exists` → `solved_sheet_exists`
- Audit event `hunt_archive_sheet` → `hunt_solved_sheet`

### Frontend (`frontend/src`)

- `types.ts`: **puzzle** `archived` → `on_solved_sheet` (line ~106); `archive_sheet_base_url`
  → `solved_sheet_base_url`. Leave the **hunt** `archived` fields (~20/29/72) untouched.
- `components/PuzzleLinks.tsx`: `puzzle.archived` → `puzzle.on_solved_sheet`; prop
  `archiveSheetBase` → `solvedSheetBase`.
- `components/HuntInfoPanel.tsx`: `archive_sheet_base_url` → `solved_sheet_base_url`; link
  label → "Solved puzzles sheet".
- `pages/HuntBoard.tsx`: `archive_sheet_base_url` → `solved_sheet_base_url`;
  `hasArchiveSheet` → `hasSolvedSheet`.
- `pages/HuntList.tsx`: create-hunt state `createArchiveSheet` → `createSolvedSheet`; payload
  `create_archive_sheet` → `create_solved_sheet`; checkbox label →
  "Move solved feeders to their own sheet".
- `api.ts`: the archive-sheet endpoint call → solved-sheet.

### Docs

- `docs/commands.md`: rewrite the §Archiving note to separate the three "archive" concepts
  (keeping the bang-only channel-archiving point); update the `!set_to_archive` row to
  `!use_solved_sheet` + "solved puzzles sheet" wording.
- Update the `web-dashboard-command-parity` memory to reflect the new command name.

## Part B — drop the dead `archive_time` trio

### Migration 024 (drops)

- `ALTER TABLE hunts DROP COLUMN archive_time;`
- `ALTER TABLE tag_group DROP COLUMN archive_time;`  *(current name of the former `rounds` table)*
- `ALTER TABLE puzzles DROP COLUMN archive_time;`

### Remove

- Fields: `HuntData.archive_time`, `TagGroupData.archive_time`, `PuzzleData.archive_time`.
- Dead methods: `get_solved_puzzles_to_archive` (`bot/store/mysqldb.py`, `puzzle_data.py`
  stub) and `get_solved_rounds_to_archive` (`hunt_data.py`, `tag_group_data.py`) — verified
  zero callers repo-wide.
- Orphaned writes: `bot/core/puzzle_service.py:1024` (set), `:1049` (clear), and
  `bot/cogs/puzzles.py:3035` (channel-archive set).

### Tests

- `tests/test_archive_channel.py`: delete the obsolete `#22`
  `test_sets_and_commits_archive_time_for_a_tracked_puzzle` (the rest of the file, exercising
  channel archiving, stays).
- `tests/test_minimeta_service.py`: drop `archive_time` from the fixture and the
  unsolve-reset assertions.
- `tests/test_backfill_consistency.py`: drop `r.archive_time` from the SELECT and the
  `archived`-status branch (that branch is already unreachable since nothing writes the
  column).

## Staging (reviewable commits)

1. **Backend + migration 024** — both renames *and* all three drops, plus every backend
   read/write site and all affected backend tests, in one green commit (the field↔column
   coupling forbids splitting the rename).
2. **Frontend** — identifier renames + the two label changes.
3. **Docs + memory** — `commands.md` note rewrite, `!use_solved_sheet` row, parity memory.

The app is only end-to-end consistent once stages 1 and 2 are both deployed (the route +
payload rename spans backend and frontend).

## Verification

- After stage 1: run migration 024 against the test DB, then the affected pytest files under
  `timeout` in the project's compact mode: `test_archive_channel`, `test_minimeta_service`,
  `test_backfill_consistency`, `test_create_hunt_service`, `test_sheet_service`,
  `test_reconcile`, and the web-parity create/delete tests. Full suite at the phase boundary.
- After stage 2: frontend typecheck/build.
- Manual smoke (optional): `!use_solved_sheet` in a hunt channel; solve a feeder and confirm
  its tab moves to the second sheet while a meta stays on the main sheet; the dashboard link
  reads "Solved puzzles sheet".

## Rollback / recovery notes

- Migration 024 is a rename + drops; it is **not** trivially reversible (dropped
  `archive_time` data is discarded — acceptable, as it was write-only/dead). To roll back,
  revert the code commits and restore the columns from backup/base DDL.
- Because deploy is atomic (migration + code together), a failed deploy is recovered by
  reverting to the previous release and its schema.

## Acceptance criteria

- No `archive_google_sheet_id`, `archive_spreadsheet_id`, `archive_puzzle_spreadsheet`,
  `create_archive_sheet`, `archive_sheet_base_url`, or puzzle-level `archived` identifiers
  remain for feature #2 (the `hunts.archived` flag and channel-archiving names are untouched).
- No `archive_time` column, field, method, write, or test assertion remains.
- `!use_solved_sheet` works; `!set_to_archive` still works as an alias.
- Web "create solved sheet" (create-hunt checkbox + existing-hunt button) works via the new
  `/solved-sheet` route and `create_solved_sheet` payload; dashboard links render.
- All targeted tests pass; `docs/commands.md` and the parity memory are corrected.
