# Files mode (one spreadsheet file per puzzle) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Step 4 of the per-puzzle sheets design: a per-hunt `files` mode in which each
puzzle is its own spreadsheet file in a Drive folder per hunt, alongside today's `tabs` mode,
running through one location seam rather than a parallel copy of the sheet code.

**Architecture:** A puzzle (and an extra sheet) gains its own `google_sheet_id`; empty means
"derive from the hunt" (tabs mode, unchanged), set means "this file" (files mode; a mini
carries its minimeta's file id). One pure rule in `bot/core/sheet_location.py` turns
`(object, live id, solved id)` into a spreadsheet id; the service uses it with the hunt, the
cog with its two ContextVars. Every cog operation on a puzzle's or extra's tab routes
through that rule, and every page-level cog method takes an explicit `spreadsheet_id`
(defaulting to the hunt's live spreadsheet, so hunt-level tabs and today's tests are
unchanged). Files-mode behaviour (folders, copies, moves) then lives in a few
mode-dispatching cog methods plus Drive primitives; everything else — roll-ups, header
links, solution writes, extras, promote/demote — works in either mode by routing alone.

**Tech Stack:** Python 3.12/3.13, discord.py, Quart, google-api-python-client (Sheets v4 /
Drive v3), MariaDB migrations, React/TypeScript dashboard, pytest.

**Spec:** `docs/superpowers/specs/2026-10-31-per-puzzle-sheets-design.md` (approved
2026-10-31). Code map used to write this plan (scratch, facts restated where a task needs
them): `/tmp/claude-1000/-home-russ-projects-puzzbot/3b43a6eb-90aa-4b4d-bf76-f8235ef86eed/scratchpad/files-mode-code-map.md`
(staging @ 38ef37a). Steps 1–3 shipped as `docs/superpowers/plans/2026-10-31-sheet-links.md`.

**Spec correction (needs the user's ruling — see "Open decision" below):** the spec says
puzzle creation "stays fire-and-forget (the channel and dashboard entry already exist)". The
code map shows the opposite: `svc.create_puzzle` (svc:3013) awaits the sheet **before** the
channel exists, inside `hunt_create_lock`, and on a later failure `_cleanup_orphan_puzzle`
deletes the sheet and row. This plan is written for **inline creation** (today's shape; the
copy's ~3 s becomes part of the create command). The alternative is in "Open decision".

## Global Constraints

- **Nothing that works today breaks** (the user's one wince). A tab hunt behaves exactly as
  now: same tabs, same header geometry, same solve/unsolve to the solved spreadsheet, same
  reconcile results, same number of Sheets calls (`tests/test_sheet_call_volume.py` passes
  unchanged). Running hunts keep working with no manual step.
- **No parallel files-mode path** ("a mode that rots"). Shared operations (create, rename,
  solve, unsolve, delete, tag, reconcile, roll-up rendering, header links) run through the
  location rule; mode checks appear only in the methods this plan names as dispatch points:
  hunt creation, mode switch, puzzle/mini sheet creation, solve/unsolve placement, rename of
  the file, delete of the file, hunt delete, reconcile's file checks, welcome text, and the
  solved-sheet refusals.
- **Mode values:** `hunts.sheet_mode` is exactly `"tabs"` (default) or `"files"`. Anything
  else is refused with slug `bad_sheet_mode`.
- **Switching** is allowed only while the hunt has no puzzle and no tag group (round/group/
  meta): `PuzzleJsonDb.get_all_from_hunt(hunt.id) == []` and `TagGroupJsonDb.get_all(hunt.id)
  == []`; otherwise refused with slug `sheet_mode_locked`.
- **Files-mode hunts have no solved spreadsheet.** `create_solved_sheet=True` with
  `sheet_mode="files"` on create, `!use_solved_sheet`, and `POST /api/hunts/<uid>/solved-sheet`
  on a files hunt are refused with slug `files_mode_no_solved_sheet` and a reason.
- **Location rule (the one rule):** an object with a non-empty `google_sheet_id` lives in that
  spreadsheet; otherwise in the hunt's solved spreadsheet iff its `on_solved_sheet` is set and
  the solved id is non-empty; otherwise the hunt's live spreadsheet. In files mode
  `on_solved_sheet` is never set (a solved file keeps its id and link).
- **Names in Drive:** hunt folder = hunt name; subfolder = `Solved`; reference spreadsheet =
  `<hunt name> Reference`; puzzle file = puzzle name. Renaming a puzzle renames its file.
- **Never delete a Drive file reconcile did not create.** Stray files in a hunt's folders are
  reported, never deleted, including by `apply orphans`.
- **Links stay rich text, never formulas;** the URL form is still built only by
  `sheet_location` (`https://docs.google.com/spreadsheets/d/<id>/edit#gid=<page>`).
- **Test seams stay importable:** `bot.cogs.puzzles_gsheet.get_sheet`, `get_drive`,
  `execute_with_retry`; `cog.get_spreadsheet_id` / `get_solved_sheet_id` stay
  monkeypatchable and keep meaning "the hunt's live / solved spreadsheet".
- **Migration 051** is the only schema change; check `ls migrations/` in every worktree for a
  collision before merging. Every `HuntData`/`PuzzleData`/`AdditionalSheetData` attribute is
  written as a column by `_commit_once` (mysqldb.py:78), so the migration and the dataclass
  fields land in the same commit, and the push that ships them runs `migrate.py` first.
- Run tests as `timeout 600 .venv/bin/python -m pytest tests/ -q > <file> 2>&1`; before any
  push also `.venv/bin/ruff check .` and `~/.local/bin/basedpyright --pythonpath
  .venv/bin/python bot/`. A worktree needs `config.json` + `google_secrets.json` copied in.
  Frontend: `cd frontend && npx tsc --noEmit && npx vitest run` (pure helpers only).
- Every changed pre-existing test needs a stated reason in its commit message and a
  one-for-one coverage mapping (no weakened assertions).
- Any change to Drive/Sheets request code that only mocks can test gets a **read-only** live
  smoke check where one is possible (memory: credentials and request shapes are invisible to
  the suite); writes are checked in the manual acceptance on the test instance.

## Review Focus

1. **A mini created right after its minimeta** (files mode): the mini's tab must land in the
   minimeta's file, and its `google_sheet_id` must equal the minimeta's — including when the
   minimeta's file was recreated by reconcile.
2. **A puzzle whose file copy fails** (files mode, inline): nothing half-made is left — no
   channel, no row, no orphan file — and the user sees a clear error, on both the bang and
   web paths (the bang path today catches only `RuntimeError`; an `HttpError` escapes).
3. **Reconcile on a files hunt with loose hunt sheets** in the reference spreadsheet: they
   must not be reported as orphan tabs (today they are, in tab hunts too — `apply orphans`
   would delete them).
4. **A target solved in files mode:** its file moves to Solved, its feeders' Feeds links and
   every roll-up link to it still open it (file id unchanged), and its own table still
   refreshes after the move.
5. **Switching a hunt from files back to tabs:** the reference spreadsheet becomes the hunt
   spreadsheet and extras/loose sheets still work there (it must have kept the hidden
   `Tab template`).

## Open decision (ask the user before Task 7)

Puzzle file creation **inline** (recommended: today's shape, ~3 s added to each create in a
files hunt, occasional ~12 s; a failed copy fails the create cleanly as today) or **in the
background** (spec's original wording: channel first, sheet link filled in later via
`notifier.refresh_info`; needs a "file pending" guard so solve/rename/mini-create on a
puzzle whose copy is still running wait or skip, and a channel message on failure). Tasks
1–6 are the same either way; Task 7 is written for inline.

---

### Task 1: Schema and the one location rule

**Files:**
- Create: `migrations/051_files_mode.sql`
- Modify: `bot/store/hunt_data.py`, `bot/store/puzzle_data.py` (PuzzleData, AdditionalSheetData), `bot/core/sheet_location.py`
- Test: `tests/test_sheet_location.py`; a store round-trip test beside the nearest existing one (`rg -l "HuntData.import_dict|construct_puzzle" tests`)

**Interfaces:**
- Produces columns/fields (all `NOT NULL DEFAULT ''` / `'tabs'`):
  `hunts.sheet_mode VARCHAR(10) DEFAULT 'tabs'`, `hunts.drive_folder_id VARCHAR(100)`,
  `hunts.solved_folder_id VARCHAR(100)`, `puzzles.google_sheet_id VARCHAR(100)`,
  `additional_sheets.google_sheet_id VARCHAR(100)` → `HuntData.sheet_mode: str = "tabs"`,
  `.drive_folder_id: str = ""`, `.solved_folder_id: str = ""`,
  `PuzzleData.google_sheet_id: str = ""`, `AdditionalSheetData.google_sheet_id: str = ""`.
- Produces in `sheet_location.py`:
  ```python
  def spreadsheet_for(obj, live_id: str, solved_id: str) -> str   # the Location rule
  ```
  `puzzle_location` / `additional_sheet_location` call it with the hunt's ids. Hunt sheets
  are unchanged (always `hunt.google_sheet_id`).

- [ ] **Step 1: failing tests** — `spreadsheet_for`: own id wins over a set solved flag; empty
  own id + solved flag + solved id → solved; solved flag + empty solved id → live; plain →
  live. `puzzle_location` and `additional_sheet_location` with an own id (files) and without
  (every existing case still passes). Store: a hunt row and a puzzle row without the new keys
  import with the defaults; with them, round-trip.
- [ ] **Step 2:** run, expect failures.
- [ ] **Step 3:** write `051_files_mode.sql` in the house format (header comment, PRE-FLIGHT
  note, re-runnable `ADD COLUMN IF NOT EXISTS` as MariaDB allows — see `050_template_link.sql`
  and `045` for a flag column); add the fields; implement `spreadsheet_for` and re-point the
  two resolvers to it.
- [ ] **Step 4:** focused tests, then the full suite. Pre-flight the migration against the
  local test DB (`.venv/bin/python migrate.py` against the dev config) and confirm the five
  columns exist with their defaults.
- [ ] **Step 5: commit** `feat(sheets): per-puzzle spreadsheet id and hunt sheet mode (migration 051)`.

### Task 2: Route every cog sheet operation through the location rule (no behaviour change)

The structure change surfaced in the spec. Today ~14 cog methods use `self.get_spreadsheet_id()`
implicitly and `batch_update(body, archive=False)` picks live/solved by flag (code map §A).

**Files:**
- Modify: `bot/cogs/puzzles_gsheet.py`; service callers in `bot/core/puzzle_service.py` that pass `archive=` or rely on the implicit live id for a puzzle's tab (`_has_contribution_table` svc:335, `_reconcile_tabs` svc:618)
- Test: re-point existing tests (map below); add `tests/test_sheet_routing.py`

**Interfaces:**
- Produces on the cog:
  ```python
  def spreadsheet_of(self, obj) -> str        # spreadsheet_for(obj, live, solved) with the ContextVar ids
  async def batch_update(self, body, spreadsheet_id: str | None = None)  # None → live; `archive=` removed
  ```
  and an explicit `spreadsheet_id: str | None = None` (None → live) on every page-level
  method: `reconcile_table`, `_ensure_contribution_table`, `_add_contribution_table`,
  `_tab_layout`, `ensure_unassigned_table`, `ensure_evidence_table`, `delete_sheet`,
  `move_sheet_to_end`, `get_page_id_by_name`, `get_puzzle_sheet_index`, `add_new_sheet`.
- Rule: a method that acts on a **puzzle's or extra's tab** gets its spreadsheet from
  `self.spreadsheet_of(puzzle_or_sheet)` (replacing `_target_spreadsheet_id`, which goes);
  this covers `update_puzzle_info`, `update_solution`, `generate_puzzle_tab` (new puzzle →
  `spreadsheet_of(puzzle)`), `promote_tab_to_target`, `demote_tab_to_puzzle`,
  `render_contribution`, `render_minimeta_contribution`, `stamp_rollup_metadata`,
  `delete_puzzle_spreadsheet`, `create_additional_spreadsheet`, `compute_insert_index`'s
  sheet read. `write_header_links` groups by `loc.spreadsheet_id` and writes to it directly
  (drop the "live or solved only" guard; still skip an empty id or page id).
  `move_puzzle_sheet` / `move_puzzle_to_solved_sheet` / `restore_puzzle_from_solved_sheet`
  keep their tabs-mode logic, now expressed with explicit ids.
- Known consequence, accepted (ruling): a **solved minimeta in a tab hunt** now has its table
  rendered on the solved spreadsheet where its tab is (today the render silently misses —
  code map complication 9). This is a fix, not a regression; pin it with a test.

- [ ] **Step 1: failing tests** in `test_sheet_routing.py`: a puzzle with `google_sheet_id="F"`
  routes `update_solution`, `update_puzzle_info`, `render_contribution` (via `reconcile_table`)
  and `delete_puzzle_spreadsheet` to `"F"`; an extra with its own id routes to it; a tabs
  puzzle on the solved sheet routes to `"SOLVED"`; `write_header_links` with locations in
  three different spreadsheets sends three batches, one to each; a solved tab-hunt minimeta's
  table render targets `"SOLVED"`.
- [ ] **Step 2:** run, expect failures.
- [ ] **Step 3:** implement. Re-point existing tests mechanically: recorders written as
  `fake(body, archive=False)` become `fake(body, spreadsheet_id=None)` asserting the same
  spreadsheet (`archive=True` ↔ `"SOLVED"`, `False` ↔ live). List every changed test file in
  the commit message with the mapping; no assertion may be dropped or loosened.
- [ ] **Step 4:** full suite; `tests/test_sheet_call_volume.py` must pass **unchanged**.
- [ ] **Step 5: commit** `refactor(sheets): route cog sheet ops through the location rule`.

### Task 3: Dashboard links built on the server

The frontend assembles sheet URLs from `sheet_base_url`/`solved_sheet_base_url` +
`sheet_page` (code map §G); extras even use the main puzzle's solved flag. Files mode needs
per-puzzle URLs, so the server sends them.

**Files:**
- Modify: `bot/web/data.py` (`get_hunt_puzzles` ~308-348, extras ~384-396, hunt sheets ~444-449), `bot/web/api.py` (hunt detail ~151), `bot/web/actions.py` (new-sheet responses ~360, ~423); frontend `types.ts`, `PuzzleLinks.tsx`, `PuzzleDrawer.tsx`, `BoardGroup.tsx`, `PuzzleTable.tsx`, `HuntInfoPanel.tsx`, `HuntBoard.tsx`
- Test: `tests/test_hunt_puzzles_payload.py`, `tests/test_hunt_detail_team_flag.py` (extend), `tests/test_hunt_sheets_web.py`

**Interfaces:**
- Payload: each puzzle gains `sheet_url: str | None` (`puzzle_location(hunt, p).url`), each
  extra `url` (`additional_sheet_location`), each hunt sheet `url` (`hunt_sheet_location`);
  hunt detail gains `sheet_url` (the hunt/reference spreadsheet), `solved_sheet_url`
  (or None) and `sheet_mode`. `sheet_base_url`, `solved_sheet_base_url`, `sheet_page`,
  `page_id` stay in the payload this task (removed in a later tidy only if nothing reads
  them); the frontend stops reading them for links.
- `hasSolvedSheet` derives from `solved_sheet_url`.

- [ ] **Step 1: failing tests** — payload has `sheet_url` for a live puzzle, a solved puzzle
  on the solved sheet, and a puzzle with its own file; an extra on the solved sheet whose
  puzzle is live gets the solved URL (fixes the drawer mismatch); hunt sheets carry `url`.
- [ ] **Step 2:** run, expect failures. **Step 3:** implement server, then frontend consumers.
- [ ] **Step 4:** pytest + `npx tsc --noEmit` + vitest; `rg "sheetBase|sheet_base_url|sheet_page" frontend/src` shows no remaining link assembly.
- [ ] **Step 5: commit** `feat(web): send each sheet's URL instead of building it in the browser`.

### Task 4: Drive primitives

No Drive code handles folders today; `drive_parent_id` is stored but never read (code map §I).

**Files:**
- Modify: `bot/cogs/puzzles_gsheet.py`
- Test: `tests/test_drive_requests.py` (new — the first tests of Drive request shapes; patch `bot.cogs.puzzles_gsheet.get_drive` and `execute_with_retry`)

**Interfaces (all async, all via `execute_with_retry`):**
```python
async def create_folder(self, name: str, parent_id: str | None = None) -> str
async def copy_template(self, name: str, folder_id: str) -> str          # copy of config.master_spreadsheet into folder; shares anyone-with-link writer
async def move_file(self, file_id: str, to_folder: str, from_folder: str) -> None   # files().update addParents/removeParents
async def rename_file(self, file_id: str, name: str) -> None
async def delete_file(self, file_id: str) -> None                          # tolerant of 404, like delete_hunt_spreadsheet
async def list_folder(self, folder_id: str) -> list[dict]                  # [{id, name, mimeType}], follows nextPageToken, trashed=false
async def template_tabs(self) -> list[dict]                                # master's sheets (sheetId, title, hidden); cached per process (a Drive copy keeps sheet ids)
```
`create_folder` puts the folder under `parent_id` when given (the guild's `drive_parent_id`),
else the service account's root.

- [ ] **Step 1: failing tests** — request bodies: folder mimeType
  `application/vnd.google-apps.folder` with `parents` only when a parent is given; copy body
  `{"name", "parents": [folder]}` then a permission `{"type": "anyone", "role": "writer"}`;
  move uses `addParents`/`removeParents`; list paginates two pages and filters trashed;
  delete swallows 404 but raises other errors; `template_tabs` calls the API once across two
  calls.
- [ ] **Step 2–4:** run red, implement, run green + full suite. Then a **read-only** live
  check: `list_folder` on a folder the service account owns and `template_tabs()` (record the
  master's tab titles/hidden flags in the ledger — Task 5/7 trim rules depend on them).
- [ ] **Step 5: commit** `feat(drive): folder, copy, move, rename, list primitives`.

### Task 5: Files-mode hunt creation, mode switch, solved-sheet refusals (service)

**Files:**
- Modify: `bot/core/puzzle_service.py` (`create_hunt` svc:3902, `create_hunt_solved_sheet` svc:2896, `delete_hunt` svc:4001), `bot/cogs/puzzles_gsheet.py`
- Test: `tests/test_create_hunt_service.py`, `tests/test_solved_sheet_seam.py`, `tests/test_delete_hunt_service.py`, new `tests/test_sheet_mode_switch.py`

**Interfaces:**
```python
# cog
async def create_reference_spreadsheet(self, hunt_name: str, folder_id: str) -> str
    # copy_template into folder named "<hunt> Reference", then ONE batchUpdate deleting every
    # tab except REFERENCE and the hidden tabs (Tab template etc. stay — extras and a later
    # switch back to tabs need them)
# service
async def create_hunt(..., sheet_mode: str = "tabs", ...)       # new kwarg beside layout_mode
async def set_sheet_mode(self, hunt, mode: str, *, guild_parent_id: str | None) -> None
```
- `create_hunt(sheet_mode="files")`: `create_folder(name, drive_parent_id)` →
  `create_folder("Solved", folder)` → `create_reference_spreadsheet` → store
  `drive_folder_id`, `solved_folder_id`, `google_sheet_id` (= reference), `sheet_mode`.
  Unassigned/Evidence tabs stay lazy, as today (they land in the reference spreadsheet).
- `set_sheet_mode`: refuse per Global Constraints; tabs→files: create both folders, **move**
  the existing hunt spreadsheet into the hunt folder and rename it `<hunt> Reference` (it
  keeps REFERENCE, loose sheets, Unassigned/Evidence); if a solved spreadsheet exists, delete
  it (it can hold no puzzles) and clear `solved_sheet_id`. files→tabs: move the reference
  spreadsheet to the hunt folder's parent (root when none), rename it to the hunt name,
  delete both (empty) folders, clear the folder ids. Same mode → no-op.
- `delete_hunt` on a files hunt deletes the hunt folder (`delete_file(drive_folder_id)`;
  Drive deletes an owned folder's descendants) instead of the two spreadsheets.
- Solved-sheet refusals: `create_hunt(create_solved_sheet=True, sheet_mode="files")` and
  `create_hunt_solved_sheet` on a files hunt raise `RuntimeError("files_mode_no_solved_sheet")`.

- [ ] **Step 1: failing tests** — files create makes folder (under a given parent), Solved
  subfolder and reference spreadsheet in that order and stores all ids; tabs create is
  byte-for-byte today's calls; refusal slugs; switch refused with a puzzle, refused with a
  group, tabs→files moves (not copies) the existing spreadsheet and drops an empty solved
  sheet, files→tabs reverses it; delete_hunt deletes the folder for files and the two
  spreadsheets for tabs.
- [ ] **Step 2–4:** red, implement, green, full suite.
- [ ] **Step 5: commit** `feat(sheets): files-mode hunts — folder, reference spreadsheet, mode switch`.

### Task 6: Choosing the mode on every surface

**Files:**
- Modify: `bot/cogs/puzzles.py` (`!hunt` pz:689, `!setup_server` pz:4154, `!use_solved_sheet` pz:820, new `!sheet_mode`), `bot/web/actions.py` (`create_hunt` act:763, `post_guild_setup` act:1829, `post_hunt_solved_sheet` act:1071, new `PATCH /api/hunts/<uid>/sheet-mode`), `bot/core/server_setup.py` (thread `sheet_mode` beside `create_solved_sheet` through `run_setup`/`perform_setup`/`_perform_setup`); frontend `HuntList.tsx` + `api.ts` `CreateHuntPayload`, `ServerSetup.tsx` + `lib/setupForm.ts`, `AdminSettingsPanel.tsx`; docs `docs/commands.md`, `docs/user-guide.md`, `docs/dashboard.md`
- Test: `tests/test_hunt_layout_option.py` (pattern to copy), `tests/test_setup_web.py`, `tests/test_setup_server_command.py`, `tests/test_server_setup_perform.py`, `tests/test_solved_sheet_seam.py`, `frontend/src/lib/setupForm.test.ts`

**Interfaces / syntax (decided here):**
- `!hunt name:url[, role][, forum][, files]` — trailing flags in any order, same stripping
  as `, forum` today.
- `!setup_server <hub> <roster> <url> <hunt name>[, files]` — a trailing `, files` on the
  name sets the mode (the name is rest-of-line).
- `!sheet_mode [tabs|files]` — hunt channel, admins, under `with_hunt_sheet_mutex`; no
  argument reports the current mode; refusals print the reason.
- Web: `sheet_mode` added to `_CREATE_HUNT_OPTIONAL` and `_SETUP_OPTIONAL`, validated against
  the two values (`bad_sheet_mode`); `PATCH /api/hunts/<uid>/sheet-mode` `{"sheet_mode"}`
  under `hunt_sheet_lock`, organiser tier as the solved-sheet route; 409 for
  `sheet_mode_locked` / `files_mode_no_solved_sheet`.
- Frontend: create form and setup page Advanced sections get a switch "One spreadsheet per
  puzzle"; when on, the solved-sheet option is hidden/disabled. AdminSettingsPanel shows the
  mode, offers the switch only while the hunt is empty, and hides the solved-sheet button on
  a files hunt.

- [ ] **Step 1: failing tests** for each parser/route/payload above (bang parsing incl.
  `, forum, files` and `, files, forum`; refusals reach the user as text/409; setupForm
  payload carries `sheet_mode`).
- [ ] **Step 2–4:** red, implement, green; pytest + tsc + vitest.
- [ ] **Step 5:** docs: the commands, the dashboard options, and a short "Files mode" section
  in the user guide (what's where: folder, Solved, Reference; solved-sheet options don't
  apply).
- [ ] **Step 6: commit** `feat: choose files mode from every hunt-creation surface; !sheet_mode`.

### Task 7: Puzzle, mini and extra sheets in a files hunt (inline creation)

**Files:**
- Modify: `bot/cogs/puzzles_gsheet.py` (`create_puzzle_spreadsheet` cog:701 becomes the dispatch point), `bot/core/puzzle_service.py` (`create_puzzle` svc:3013, `create_mini` svc:2796, `_reconcile_tabs` svc:618 recreation), `bot/cogs/puzzles.py` (bang create error handling pz:896-903; welcome text pz:3025-3078)
- Test: `tests/test_create_service.py`, `tests/test_create_orphan_cleanup.py`, `tests/test_minimeta_service.py`, `tests/test_hunt_sheets.py`, `tests/test_welcome_message_indentation.py`, new `tests/test_files_mode_create.py`

**Interfaces:**
```python
async def create_puzzle_spreadsheet(self, puzzle, index=None, header_links=None, *, hunt=None)
    # tabs (hunt None or sheet_mode "tabs"): exactly today's path
    # files: create_puzzle_file(...) unless puzzle.parent_puzzle_id (mini) → tab in parent's file
async def create_puzzle_file(self, puzzle, folder_id: str, header_links=None) -> None
    # copy_template(puzzle.name, folder) → set puzzle.google_sheet_id →
    # add the puzzle tab (generate_puzzle_tab, which now targets spreadsheet_of(puzzle)) →
    # in the SAME header batch: delete every template tab except the hidden "Tab template",
    # and updateSpreadsheetProperties importFunctionsExternalUrlAccessAllowed=true
    # Budget: 1 Drive copy + 1 permission + at most 2 Sheets batchUpdates (no sheet_list read:
    # template_tabs() supplies the ids to delete)
```
- **Minis (files):** `mini.google_sheet_id = minimeta.google_sheet_id`; the tab goes after the
  minimeta's main tab and the unsolved minis, before the first solved (green) mini — e.g.
  main, m1, m2, solved m3 → new mini at index 3. A minimeta without a file id (copy failed
  earlier) → the mini create is refused with `parent_sheet_missing` and a pointer to
  `!reconcile apply`.
- **Extras (files):** `extra.google_sheet_id = puzzle.google_sheet_id`; `add_new_sheet`
  duplicates that file's hidden `Tab template` (kept by the trim). Loose hunt sheets are
  unchanged (reference spreadsheet = `hunt.google_sheet_id`).
- **Failure:** a copy/trim failure raises out of `create_puzzle` before the channel exists,
  as today; `_cleanup_orphan_puzzle` deletes the **file** in files mode (via
  `delete_puzzle_spreadsheet` routing, Task 8). The bang create handler also catches
  `HttpError` and says the sheet couldn't be created (today only `RuntimeError` is caught).
- **Welcome text (files):** hunt welcome's "Google Sheet" becomes "Reference sheet" (no
  solved-sheet line); puzzle and meta welcomes keep the puzzle's link (now its file) and add
  a "Reference sheet" bullet.

- [ ] **Step 1: failing tests** — files puzzle create: copy into `hunt.drive_folder_id`, ids
  stored, one header batch containing the deletes and the property; tabs create unchanged
  (assert the exact existing calls); mini goes to the parent's file at the index rule above
  (two instances: with and without a solved mini); missing parent file → `parent_sheet_missing`;
  extra in a files puzzle duplicates the file's own template; a failed copy leaves no row, no
  channel, no file, on bang (HttpError message) and web; welcome text both modes.
- [ ] **Step 2–4:** red, implement, green, full suite.
- [ ] **Step 5: commit** `feat(sheets): puzzle files, mini tabs and extras in files mode`.

### Task 8: Solve, unsolve, rename and delete in a files hunt

**Files:**
- Modify: `bot/cogs/puzzles_gsheet.py` (`move_puzzle_to_solved_sheet` cog:617, `restore_puzzle_from_solved_sheet` cog:567, `update_puzzle_info` rename branch, `delete_puzzle_spreadsheet` cog:518), `bot/core/puzzle_service.py` (solve svc:1497, unsolve svc:1606, `rename_puzzle` svc:2684, `delete_puzzle` svc:1872, `delete_round` svc:1919, `_teardown_group_target` svc:1424, `_delete_additional_sheet` svc:2279)
- Test: `tests/test_solve_link_order.py`, `tests/test_sheet_failure_isolation.py`, `tests/test_delete_round_keep_puzzles.py`, `tests/test_minimeta_service.py`, new `tests/test_files_mode_lifecycle.py`

**Rules:**
- **Solve (files, own file):** today's header writes (solution, "Solved", green background,
  tab colour) in the puzzle's file, then `move_file(file, solved_folder, drive_folder)` — for
  every puzzle with its own file, **targets and minimetas included** (ruling: the link never
  changes, so nothing pins a target in place). `on_solved_sheet` stays False; extras ride
  along inside the file. **Unsolve:** clear those writes, move the file back.
- **Solve (files, mini):** tab turns green and moves to the end of the parent's file (today's
  no-solved-sheet path, in the parent's file). **Unsolve:** un-green and move back before the
  first solved mini (Task 7 index rule).
- The T6 ordering (targets rendered after the move) is kept; in files mode the links don't
  change, so it is merely harmless.
- **Rename (files, own file):** `rename_file` plus today's tab rename. A mini renames its tab
  only.
- **Delete (files):** own file → `delete_file`; mini → delete its tab in the parent's file
  (today's `delete_sheet`, routed); extras inside a deleted file need no separate call.
  `delete_round` and `_teardown_group_target` go through the same routed delete.

- [ ] **Step 1: failing tests** — files solve: header batch to the puzzle's file, then one
  move call with the right folders, no `copyTo`; target solve moves too and its feeders'
  links are unchanged; mini solve: green + move-to-end in the parent's file, no Drive call;
  unsolve reverses each; rename calls `rename_file` (own file) / not (mini); delete calls
  `delete_file` (own file) / `delete_sheet` in the parent's file (mini); every tabs-mode test
  in the listed files passes unchanged.
- [ ] **Step 2–4:** red, implement, green, full suite.
- [ ] **Step 5: commit** `feat(sheets): solve/unsolve move files, rename and delete files in files mode`.

### Task 9: Reconcile in a files hunt (and loose sheets are not orphans)

**Files:**
- Modify: `bot/core/puzzle_service.py` (`reconcile` svc:653, `_fetch_present_tab_ids` svc:589, `_reconcile_tabs` svc:618, `_collect_orphan_tabs` svc:767, `delete_orphans` svc:801), `bot/core/reconcile.py` (report kinds), `bot/cogs/puzzles.py` (report text ~987-1023), `frontend/src/components/ReconcilePanel.tsx` (show stray files), docs `docs/commands.md`
- Test: `tests/test_reconcile.py`

**Rules:**
- Files hunt: list `drive_folder_id` and `solved_folder_id` once each (no per-file reads).
  For each puzzle with no `parent_puzzle_id`: no file id, or its id not found in either
  folder → `file_create` (apply: Task 7's `create_puzzle_file`, then its minis' tabs are
  recreated in the new file and each mini's `google_sheet_id` updated); found in the wrong
  folder for `puzzle.solved` → `file_move`; name ≠ puzzle name → `file_rename`. Files in the
  folders matching no puzzle (and not the reference spreadsheet) → `report.stray_files`,
  never deleted (`delete_orphans` ignores them).
- Mini tab presence is checked by reading each minimeta's file that has minis (one read per
  such minimeta), recreating a missing mini tab on apply.
- `_collect_orphan_tabs` runs against the hunt/reference spreadsheet in both modes and counts
  the hunt's loose sheets (`SheetsJsonDb.get_by_hunt`) as valid — fixes the existing bug
  where `apply orphans` would delete them (tab hunts too; ruling: a data-loss fix).
- Tab hunts: every existing reconcile test passes unchanged.

- [ ] **Step 1: failing tests** — healthy files hunt reports nothing (spec acceptance 7);
  each of file_create / file_move / file_rename detected and applied; a stray file reported
  and survives `apply orphans`; minis recreated with updated ids when their minimeta's file
  is recreated; a loose hunt sheet is not an orphan tab (both modes).
- [ ] **Step 2–4:** red, implement, green, full suite.
- [ ] **Step 5: commit** `feat(reconcile): files-mode drift checks; loose hunt sheets are not orphans`.

---

## Quota notes (from the user's question, 2026-11-01)

Sheets writes are the capped resource (~60/min for the bot's one service account); Drive
calls have a separate, much larger quota.

| Action | Tab hunt today | Files hunt |
|---|---|---|
| Create a puzzle | 1 read + 3 writes | 1 Drive copy + 1 Drive permission + ≤2 writes |
| Solve a feeder | copy, rename, delete + 1 batch (≈4 writes, reads) | 1 write + 1 Drive move |
| Feeds lines for N puzzles (`!sync`, `!reconcile apply`) | 1 write per spreadsheet (1–2) | **N writes** (one per file) |
| Roll-up render | 1 write per meta | same |

Files mode is cheaper per puzzle and per solve, and dearer for bulk header refreshes: a
`!sync` across 20 puzzles is 20 writes instead of 1. That is within the limit with the
existing back-off, but `!reconcile apply` on a large files hunt will show "retrying"
notices; run it at a quiet moment.

## Manual acceptance (test instance, after merge)

The spec's acceptance 3–9, plus: create a files hunt from each surface; check the folder,
Solved subfolder and Reference spreadsheet in Drive; create a puzzle, a meta, a minimeta
with two minis and an extra sheet; confirm `IMAGE()`-based named functions work in a puzzle
file without a prompt; solve and unsolve a feeder, a mini and the meta; rename and delete a
puzzle; run `!reconcile` (no drift) then delete a puzzle file by hand and `!reconcile apply`;
switch an empty hunt both ways; delete the files hunt and check the folder is gone.
