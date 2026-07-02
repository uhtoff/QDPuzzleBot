# Solved-Puzzles-Sheet Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the "archive sheet" feature (a second Google spreadsheet that holds solved *feeders*) to "solved puzzles sheet" across DB, backend, command, web API, and frontend; and delete the never-wired auto-archive feature (the `archive_time` trio + `get_solved_*_to_archive` methods).

**Architecture:** This is a rename + dead-code removal, not new behaviour. One DB migration (024) renames two columns and drops three; the backend rename and migration must land in a single commit because the generic `commit()` writes each dataclass field by its column name (field and column cannot diverge). Frontend and docs follow in their own commits. The unrelated `hunts.archived` dashboard flag and the Discord channel-archiving commands are **left untouched**.

**Tech Stack:** Python 3 (discord.py, Quart web), MySQL (raw SQL via `bot/store`), React + TypeScript (Vite) frontend, pytest, `migrate.py` migration runner.

**Spec:** `docs/superpowers/specs/2026-07-02-solved-sheet-rename-design.md` — read it first.

---

## Conventions for this plan

- **Run tests:** `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` (targeted:
  `timeout 120 pipenv run python -m pytest tests/test_x.py -q > /tmp/test.log 2>&1`). `pipenv run` is
  required (`python`/`pytest` aren't on PATH). On a killed run (exit 124) read `/tmp/test.log`.
- **Migrations:** `pipenv run python migrate.py status` (list pending), `pipenv run python migrate.py`
  (apply). Applied to the local `puzzlebot_test` DB from `config.json`. Migrations "midway cannot be
  rolled back — fix forward or baseline."
- **Frontend typecheck:** `cd frontend && npx tsc --noEmit`.
- **This is a rename:** the reliable technique is *grep for the old identifier → replace at each hit →
  grep again to confirm zero remain*. Steps below give the exact old→new maps and the verification
  greps. Do **not** blanket sed `archived` — the **hunt** `archived` flag must survive; only the
  **puzzle**-context `archived` changes.

---

## Task 0: Pre-flight

**Files:** none (verification only).

- [ ] **Step 1: Confirm a clean, green baseline**

Run: `git status --short` (expect only this plan/spec as untracked or committed; no stray edits).
Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: PASS. If anything fails on a clean tree, stop and report — do not start the rename on red.

- [ ] **Step 2: Confirm migration 024 is free**

Run: `ls migrations/ | grep -E "^024" || echo FREE`
Expected: `FREE`. Also re-check sibling worktrees before merge:
`for d in /home/russ/projects/puzzbot /home/russ/projects/puzzbot/.claude/worktrees/working-2 /home/russ/projects/puzzbot/.claude/worktrees/working-3; do ls "$d"/migrations 2>/dev/null | grep -E "^024"; done` → expect no output.

- [ ] **Step 3: Capture the live column definitions (for exact `CHANGE COLUMN` types)**

Run: `pipenv run python migrate.py status`
Run (via a quick python one-liner or mysql client) `SHOW COLUMNS FROM hunts LIKE 'archive_google_sheet_id'` and `SHOW COLUMNS FROM puzzles LIKE 'archived'`.
Expected: `hunts.archive_google_sheet_id` = `varchar(50)` nullable; `puzzles.archived` = `tinyint(1)` default 0. If they differ from the base schema, use the actual definitions in the migration's `CHANGE COLUMN` clauses in Task 1.

---

## Task 1: Backend rename + migration 024 + drop `archive_time` (atomic — one commit)

This whole task is **one commit**: the migration renames/drops columns and the code must match at all
times, so tests only pass once every backend edit and the migration are both in place. Make all edits,
apply the migration, run tests, then commit.

**Files:**
- Create: `migrations/024_solved_sheet_rename_drop_archive_time.sql`
- Modify: `bot/store/hunt_data.py`, `bot/store/puzzle_data.py`, `bot/store/tag_group_data.py`, `bot/store/mysqldb.py`
- Modify: `bot/cogs/puzzles_gsheet.py`, `bot/cogs/puzzles.py`, `bot/core/puzzle_service.py`
- Modify: `bot/web/data.py`, `bot/web/api.py`, `bot/web/actions.py`
- Test: `tests/test_archive_channel.py`, `tests/test_minimeta_service.py`, `tests/test_backfill_consistency.py`, and any test referencing the renamed identifiers (grep in Step 10).

- [ ] **Step 1: Write migration 024**

Create `migrations/024_solved_sheet_rename_drop_archive_time.sql`:

```sql
-- 024: Rename the "archive sheet" (a second spreadsheet holding solved feeders) to the
-- "solved puzzles sheet", and drop the never-wired auto-archive feature's archive_time
-- columns. See docs/superpowers/specs/2026-07-02-solved-sheet-rename-design.md.

-- Part A — renames (CHANGE COLUMN restates the type; confirm types via Task 0 Step 3)
ALTER TABLE `hunts`
  CHANGE COLUMN `archive_google_sheet_id` `solved_sheet_id` varchar(50) DEFAULT NULL;

ALTER TABLE `puzzles`
  CHANGE COLUMN `archived` `on_solved_sheet` tinyint(1) DEFAULT 0;

-- Part B — drop the dead archive_time trio (never read in any live path)
ALTER TABLE `hunts`     DROP COLUMN `archive_time`;
ALTER TABLE `tag_group` DROP COLUMN `archive_time`;
ALTER TABLE `puzzles`   DROP COLUMN `archive_time`;
```

- [ ] **Step 2: Rename the hunt sheet-id field + drop archive_time fields in the store dataclasses**

- `bot/store/hunt_data.py`: rename field `archive_google_sheet_id` → `solved_sheet_id`; delete the
  `archive_time: Optional[datetime.datetime] = None` field; delete the `get_solved_rounds_to_archive`
  method.
- `bot/store/puzzle_data.py`: rename field `archived` → `on_solved_sheet`; delete the `archive_time`
  field; delete the `get_solved_puzzles_to_archive` method stub.
- `bot/store/tag_group_data.py`: delete the `archive_time` field; delete the
  `get_solved_rounds_to_archive` method.

- [ ] **Step 3: Remove the dead reader + drop archive_time write-path in `mysqldb.py`**

- `bot/store/mysqldb.py`: delete the `get_solved_puzzles_to_archive` method (the `if
  puzzle.archive_time is not None` block and its enclosing method). Grep the file for `archive_time`
  and `get_solved_` afterwards to confirm none remain.

- [ ] **Step 4: Rename gsheet-cog methods, attrs, and puzzle flag reads**

In `bot/cogs/puzzles_gsheet.py`, apply this map (rename definitions **and** every call site in the repo):

| Old | New |
|---|---|
| `get_archive_spreadsheet_id` | `get_solved_sheet_id` |
| `set_archive_spreadsheet_id` | `set_solved_sheet_id` |
| `archive_spreadsheet_id` (internal attr) | `solved_sheet_id` |
| `create_hunt_archive_spreadsheet` | `create_solved_sheet` |
| `archive_puzzle_spreadsheet` | `move_puzzle_to_solved_sheet` |
| `restore_puzzle_spreadsheet` | `restore_puzzle_from_solved_sheet` |
| `move_puzzle_spreadsheet` | `move_puzzle_sheet` |
| param `to_archive` (in that method) | `to_solved` |
| `puzzle.archived` / `puzzle_data.archived` | `.on_solved_sheet` |

- [ ] **Step 5: Update `puzzle_service.py` call sites + remove the archive_time writes**

- `bot/core/puzzle_service.py`: rename all calls to the methods in Step 4's map; rename `.archived`
  reads on puzzles to `.on_solved_sheet` (e.g. `not p.archived` → `not p.on_solved_sheet`); the
  create-hunt path `create_archive_sheet` param/kwarg → `create_solved_sheet`.
- Delete the two archive_time writes: the `puzzle.archive_time = datetime...now()` set and the
  `puzzle.archive_time = None` clear (around lines 1024 and 1049). Leave the surrounding
  `on_solved_sheet`/`google_page_id` logic intact.

- [ ] **Step 6: Update the cog (`bot/cogs/puzzles.py`) — command rename, method calls, flag read, archive_time write**

- Rename the command. Replace the `set_to_archive` definition with (keep the old name as an alias):

```python
    @commands.hybrid_command(
        aliases=["set_to_archive"],
        description="Use a separate 'solved puzzles sheet' for solved feeders (admin only)",
    )
    @commands.has_any_role(*ADMIN_ROLES)
    async def use_solved_sheet(self, ctx):
        """*Use a separate 'solved puzzles sheet' to hold solved feeders (metas stay on the main sheet).*

        Usage: `!use_solved_sheet`
        Example: `!use_solved_sheet`
        Run in the main Hunt channel. Admins/mods only."""
```

  Update the body's messages ("...set to use a second sheet for solved puzzles" → "...set to use a
  solved puzzles sheet"), and rename the internal calls (`create_hunt_archive_spreadsheet` →
  `create_solved_sheet`, `hunt.archive_google_sheet_id` → `hunt.solved_sheet_id`).
- Rename other gsheet-method calls per Step 4's map throughout the file.
- Rename the puzzle flag read at ~line 383 (`if puzzle.archived:` → `if puzzle.on_solved_sheet:`).
- Delete the `puzzle.archive_time = datetime...now()` write in `archive_channel` (~line 3035). (Channel
  archiving keeps working; it just no longer stamps a dead column.)

- [ ] **Step 7: Update store SQL in `bot/web/data.py`**

- Every SELECT column list containing `archive_google_sheet_id` → `solved_sheet_id` (several hunt
  queries).
- The puzzle query: `puzzles.archived` → `puzzles.on_solved_sheet`; the JSON output key
  `"archived": bool(row["archived"])` → `"on_solved_sheet": bool(row["on_solved_sheet"])`.
  **Do not** touch the hunt-level `"archived"` output keys (those are `hunts.archived`).

- [ ] **Step 8: Update web `api.py` + `actions.py`**

- `bot/web/api.py`: JSON field `archive_sheet_base_url` → `solved_sheet_base_url` (built from
  `hunt["solved_sheet_id"]`); rename the helper var accordingly.
- `bot/web/actions.py`:
  - `_CREATE_HUNT_OPTIONAL` set: `"create_archive_sheet"` → `"create_solved_sheet"`.
  - Route decorator `/hunts/<uid>/archive-sheet` → `/hunts/<uid>/solved-sheet`.
  - Handler `post_hunt_archive_sheet` → `post_hunt_solved_sheet`.
  - Inside it: `hunt_obj.archive_google_sheet_id` → `hunt_obj.solved_sheet_id`; error key
    `"archive_sheet_exists"` → `"solved_sheet_exists"`; the gsheet call
    `create_hunt_archive_spreadsheet`/`set_archive_spreadsheet_id` → new names; audit event
    `"hunt_archive_sheet"` → `"hunt_solved_sheet"`; response key `archive_google_sheet_id` →
    `solved_sheet_id`.

- [ ] **Step 9: Update backend tests**

- `tests/test_archive_channel.py`: **delete** the `#22` test
  `test_sets_and_commits_archive_time_for_a_tracked_puzzle` entirely (it exists only to prove a now-
  removed column is committed). Keep the rest of the file. Update any `create_hunt_archive_spreadsheet`
  / `.archived` / method-name references elsewhere in the file per the maps.
- `tests/test_minimeta_service.py`: remove the `"archive_time"` key from the dict fixture (~line 34)
  and delete the archive_time set/assert lines in the unsolve-reset test (`puzzle.archive_time = ...`,
  `assert puzzle.archive_time is None`, `mini.archive_time = ...`). Rename any `.archived` →
  `.on_solved_sheet`.
- `tests/test_backfill_consistency.py`: drop `archive_time` from the SELECT and its branch:

```python
    cur.execute(
        "SELECT r.id, r.target_puzzle_id, r.status, r.solve_time "
        "FROM tag_group r"
    )
    ...
    for r in rows:
        if r["solve_time"] is not None:
            expected = "solved"
        else:
            expected = "active"
        assert r["status"] == expected, r
```

- Grep the whole `tests/` tree and fix every remaining hit of the renamed identifiers:
  `grep -rnE "archive_google_sheet_id|archive_spreadsheet_id|create_hunt_archive_spreadsheet|archive_puzzle_spreadsheet|restore_puzzle_spreadsheet|move_puzzle_spreadsheet|create_archive_sheet|archive_sheet_base_url|\.archive_time" tests/`
  Apply the old→new maps (e.g. `archive_google_sheet_id="ARC"` kwargs in hunt factories →
  `solved_sheet_id="ARC"`). For puzzle `.archived` uses, replace with `.on_solved_sheet`.

- [ ] **Step 10: Apply the migration to the test DB**

Run: `pipenv run python migrate.py status` (expect 024 pending)
Run: `pipenv run python migrate.py` (applies 024)
Run: `pipenv run python migrate.py status` (expect 024 applied, none pending)

- [ ] **Step 11: Grep-verify no stale backend identifiers remain**

Run:
```
grep -rnE "archive_google_sheet_id|archive_spreadsheet_id|create_hunt_archive_spreadsheet|archive_puzzle_spreadsheet|restore_puzzle_spreadsheet|move_puzzle_spreadsheet|post_hunt_archive_sheet|archive_sheet_base_url|create_archive_sheet|archive_sheet_exists|hunt_archive_sheet|get_solved_puzzles_to_archive|get_solved_rounds_to_archive|\.archive_time" bot/ tests/
```
Expected: **no output** (except, acceptably, the string `"set_to_archive"` inside the command's
`aliases=[...]`, which this grep does not match). Then two `archived` spot-checks — review each hit by
eye, because the **hunt** flag legitimately stays and only the **puzzle** flag changes:
- `grep -rnE "\.archived\b" bot/ | grep -iv hunt` → should be empty (puzzle attribute access all renamed).
- `grep -rnE "\barchived\b" bot/ | grep -iv "hunt|archived_thread"` → review: any remaining hit must be a
  hunt-context reference or a comment, never a puzzle column/attr (catches a hardcoded `archived` SQL
  string in an INSERT/UPDATE, if one exists).

- [ ] **Step 12: Run the tests**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: PASS. On failure, read `/tmp/test.log` (tail it), fix, re-run. Common causes: a missed
`.archived`→`.on_solved_sheet`, a missed method-call rename, or a test hunt factory still passing
`archive_google_sheet_id=`.

- [ ] **Step 13: Commit**

```bash
git add migrations/024_solved_sheet_rename_drop_archive_time.sql bot/ tests/
git commit -m "refactor(sheet): rename archive sheet -> solved puzzles sheet; drop dead archive_time (024)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Frontend rename + labels (one commit)

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/api.ts`
- Modify: `frontend/src/components/PuzzleLinks.tsx`, `frontend/src/components/HuntInfoPanel.tsx`
- Modify: `frontend/src/pages/HuntBoard.tsx`, `frontend/src/pages/HuntList.tsx`

- [ ] **Step 1: Apply the frontend identifier map**

| Old | New | Where |
|---|---|---|
| Puzzle `archived` | `on_solved_sheet` | `types.ts` (the **Puzzle** interface only), `PuzzleLinks.tsx` |
| `archive_sheet_base_url` | `solved_sheet_base_url` | `types.ts`, `HuntInfoPanel.tsx`, `HuntBoard.tsx` |
| prop `archiveSheetBase` | `solvedSheetBase` | `PuzzleLinks.tsx` (+ caller) |
| prop `hasArchiveSheet` | `hasSolvedSheet` | `HuntBoard.tsx` (+ admin panel usage) |
| state `createArchiveSheet` / setter | `createSolvedSheet` | `HuntList.tsx` |
| payload key `create_archive_sheet` | `create_solved_sheet` | `HuntList.tsx` |
| endpoint path `/archive-sheet` | `/solved-sheet` | `api.ts` |

**Leave the hunt `archived` fields untouched** — `types.ts` lines for `HuntSummary`/hunt detail
`archived`, `HuntList.tsx` "Archived" badge, `HuntBoard.tsx` `data.hunt.archived`, `EvidencePanel.tsx`
`detail.hunt.archived`.

- [ ] **Step 2: Update the two user-facing labels**

- `HuntList.tsx`: the create-hunt checkbox label → **"Move solved feeders to their own sheet"**.
- `HuntInfoPanel.tsx`: the sheet link text → **"Solved puzzles sheet"**. (Also update the
  create/existing-hunt setup button copy, if present, to "Move solved feeders to their own sheet".)

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors. Fix any missed rename (a leftover `archived`/`archive_sheet_base_url` reference on
a puzzle/sheet object will surface as a type error).

- [ ] **Step 4: Grep-verify**

Run: `grep -rnE "archive_sheet_base_url|archiveSheetBase|hasArchiveSheet|createArchiveSheet|create_archive_sheet|archive-sheet" frontend/src`
Expected: no output. Then `grep -rn "\.archived" frontend/src` should show **only** hunt-level uses.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "refactor(web): rename archive sheet -> solved puzzles sheet in the dashboard

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Docs + memory (one commit)

**Files:**
- Modify: `docs/commands.md`
- Modify: `/home/russ/.claude/projects/-home-russ-projects-puzzbot/memory/web-dashboard-command-parity.md` (user memory — commit separately/not at all; it is outside the repo)

- [ ] **Step 1: Fix the `!set_to_archive` row in `docs/commands.md`**

In the "Hunt management" table, replace the `!set_to_archive` row with:

```
| `!use_solved_sheet` *(slash)* | `!set_to_archive` | — | Hunt channel | admins/mods | Use a separate "solved puzzles sheet": solved feeders move there, metas stay on the main sheet. |
```

- [ ] **Step 2: Rewrite the `## Archiving` note in `docs/commands.md`**

Replace the intro under `## Archiving` with a note that separates the three "archive" concepts and
states the bang-only fact accurately:

```
## Archiving

Admins/mods. **Channel archiving is bang-only** — copying a Discord channel's history into a
thread and deleting the channel has **no web-dashboard equivalent, by design**. Don't confuse it
with two unrelated features that also say "archive": the **solved puzzles sheet** (see
`!use_solved_sheet` — a second spreadsheet solved feeders move to) and the **hunt archived flag**
(the dashboard "Archive hunt" action that makes a hunt read-only). Neither of those touches Discord
channels.
```

- [ ] **Step 3: Update the parity memory**

In `web-dashboard-command-parity.md`, change the documented-exception bullet that names
`!set_to_archive` behaviour to reference `!use_solved_sheet`, and note the sheet is now called the
"solved puzzles sheet". (This file lives outside the repo under the user's memory dir; update it but do
not `git add` it.)

- [ ] **Step 4: Commit the docs**

```bash
git add docs/commands.md
git commit -m "docs(commands): rename to !use_solved_sheet; clarify the three 'archive' concepts

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Full suite green: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
- [ ] Frontend typecheck clean: `cd frontend && npx tsc --noEmit`
- [ ] `pipenv run python migrate.py status` shows 024 applied, nothing pending.
- [ ] Repo-wide stale-identifier grep (Task 1 Step 11 + Task 2 Step 4) returns nothing.
- [ ] Optional manual smoke: in a hunt channel run `!use_solved_sheet` (and confirm `!set_to_archive`
      still works as an alias); solve a feeder → its tab moves to the solved sheet; a meta stays on the
      main sheet; the dashboard shows a "Solved puzzles sheet" link.

## Rollback / recovery

- Migration 024 is a rename + drops and is **not** auto-reversible (dropped `archive_time` data is
  discarded — acceptable, it was write-only). To back out: `git revert` the three commits and restore
  the columns from the base DDL / a DB backup, then re-baseline `migrate.py`.
- Because deploy is atomic (migration + code together), recover a bad deploy by returning to the prior
  release and its schema.

## Notes for the executing agent

- **Migration-number collision:** if another worktree lands a `024_*` before this merges, renumber this
  file to the next free number and re-run `migrate.py status`.
- **Do not** rename the `hunts.archived` flag or any Discord channel-archiving command — they are out of
  scope and share only the English word "archive".
- Keep the `status` enum's `"archived"` value as-is (unreachable but harmless); changing enum backing
  values is out of scope.
