# Hunt-level Working Sheets (Plan C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a working sheet belong to the hunt rather than to a puzzle, and let an evidence item name such a sheet as its source.

**Architecture:** `additional_sheets` gains a nullable `hunt_id`, so a row with `puzzle_id IS NULL AND hunt_id = <h>` is a hunt-level sheet. A new `get_by_hunt()` makes those rows reachable (every read path today goes through a puzzle). The three sheet bang-commands fall back to the hunt when there is no puzzle in context, serialised by a new hunt-keyed lock — `with_puzzle_mutex` explicitly no-ops without a puzzle, so today a hunt-level `!add_sheet` would run unserialised. `evidence_items` gains `source_sheet_id`, rendered through the `evidence_source_label()` seam Plan A introduced for exactly this.

**Tech Stack:** Python 3 / discord.py 2.7.1, Quart, MySQL (raw SQL DAOs, no ORM), Google Sheets API, React + Mantine + react-query, pytest with `asyncio_mode = auto`.

---

## Spec

`docs/superpowers/specs/2026-07-26-canvas-image-evidence-design.md` is authoritative. This plan implements **Plan C only**. Plan A (image evidence) has shipped; Plan B (the Canvas) is a separate plan and is **not** a dependency of this one.

Acceptance criteria covered: **11, 12, 13** (the spec's numbering).

### Decisions taken since the spec was written

Three points the spec left open, settled with the user before this plan was written:

1. **A hunt-level sheet's Google tab is a plain blank tab** — `create_blank_tab` on the
   hunt's main spreadsheet, as the Evidence tab already uses. Not the `PUZZLE_SHEET`
   template: `create_additional_spreadsheet` is puzzle-shaped throughout (it derives the
   insert index from the puzzle's own tab via `get_puzzle_sheet_index`, and writes a header
   hyperlink built from `puzzle.url` / `puzzle.name` via `update_puzzle_info`), and none of
   that has a meaning without a puzzle.
2. **The dashboard shows hunt-level sheets in `HuntInfoPanel`**, on the board header,
   beside the existing "Google Sheet" link. The payload gains a top-level `hunt_sheets`.
3. **A hunt-level sheet is a pickable evidence source** in the Evidence panel — not
   backend-only. The panel's source picker offers sheets alongside puzzles, and the sheet
   name renders as the source in the panel, in the Evidence sheet tab, and in contribution
   tables.

### One deliberate deviation from the spec's wording

The spec says `bot/web/data.py:266` should have its "inner join widened to a left join".
**Do not do that.** That query is

```sql
SELECT s.id, s.puzzle_id, s.puzzle_name, s.google_page_id
FROM additional_sheets s JOIN puzzles ON puzzles.id = s.puzzle_id
WHERE puzzles.hunt_id = %s
```

A `LEFT JOIN` changes nothing here: a hunt-level row has `puzzle_id IS NULL`, so the joined
`puzzles.hunt_id` is NULL and the `WHERE` discards the row anyway. The join is also doing
the right job — its output is attached to a puzzle dict (`puzzles.get(s["puzzle_id"])`), and
a hunt-level sheet has no puzzle to attach to. Hunt-level sheets therefore get **their own
query**, keyed on `s.hunt_id`, feeding a new top-level `hunt_sheets` array. Task 10.

**Deliberately deferred:** creating and deleting hunt-level sheets from the dashboard. The
project's command-parity principle wants it eventually; this plan ships the read side
(AC 12) and leaves write to Discord, matching how `!add_sheet` works for puzzles today.

## Where this work happens

**Work directly on `staging` in the main checkout (`/home/russ/projects/puzzbot`). Do not
create a worktree** — the user wants this branch live on the test site so it can be
exercised against real Discord and real Sheets as it lands. `staging` is the integration
branch (`main` is intentionally behind).

This overrides the repo's usual persistent-worktree convention (`AGENTS.md` → Worktrees) and
any default your execution skill has toward isolating work. Commit each task straight to
`staging`.

## Before you start

Read these first — they are house rules this plan assumes:

- `AGENTS.md` — the domain vocabulary, the test command, the **migration → deploy → code** rule.
- `docs/agents/python.md` — pytest and `unittest.mock` gotchas, especially *patch where the name is looked up*.

Run the suite once to confirm a green baseline:

```bash
timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```

Read `/tmp/test.log`. A timeout-killed run exits 124. Do not start until this is green.

## Key conventions you must follow

- **Patch DAOs at the service import site.** `bot/core/puzzle_service.py` does
  `from bot.store import SheetsJsonDb`, so tests patch
  `bot.core.puzzle_service.SheetsJsonDb`, **never** `bot.store.SheetsJsonDb`. Patching the
  source module leaves the service's own reference untouched and the mock silently never
  engages.
- **Factories, not fixtures.** `tests/conftest.py` exports plain functions — `make_hunt()`,
  `make_puzzle()`, `make_ctx()`, `make_cog()`. Import and call them.
- **`asyncio_mode = auto`.** `async def test_*` needs no decorator.
- **Anything awaited is an `AsyncMock`**; sync attributes stay `MagicMock`.
- **`MagicMock().name` is a Mock, not a string.** Several tasks below build sheet stubs
  whose `puzzle_name` is read as data — set it explicitly after construction.
- **`PuzzleService.__new__(PuzzleService)` needs a notifier.** Several tasks build the
  service without its constructor to avoid needing a DB or a bot. Any method decorated
  `@pushes_board()` dereferences `self.notifier` on success, so a bare `__new__` instance
  raises `AttributeError` — which surfaces as a crash rather than a clean assertion failure.
  Set `svc.notifier = MagicMock(board_changed=AsyncMock())`, as
  `tests/test_sheet_failure_isolation.py` already does.
- **A test with one instance of a thing usually proves nothing about that thing.** One
  sheet in a list cannot have a wrong order; one field read back off a returned object whose
  values came from the call's own parameters cannot catch a transposed SQL tuple. Where a
  test guards ordering, mapping, or what actually reached the database, use two instances
  and assert on what was sent, not on what was passed in.
- Commit after every task.

## File structure

| File | | Responsibility |
| --- | --- | --- |
| `migrations/031_hunt_level_sheets.sql` | new | `additional_sheets.hunt_id`; `evidence_items.source_sheet_id` |
| `bot/store/puzzle_data.py` | modify | `AdditionalSheetData.hunt_id`; `puzzle_id` becomes nullable |
| `bot/store/mysqldb.py` | modify | `MySQLAdditionalSheetsDb.get_by_hunt`; `MySQLEvidenceDb.log` widened |
| `bot/store/evidence_data.py` | modify | `source_sheet_id` field |
| `bot/core/locks.py` | modify | `_HUNT_SHEET_LOCKS` + `hunt_sheet_lock()` |
| `bot/core/puzzle_service.py` | modify | Hunt-sheet create/remove/list; sheet-name resolution; `evidence_source_label`; `refresh_evidence`; `log_clues` |
| `bot/cogs/puzzles.py` | modify | `with_hunt_sheet_mutex`; the three sheet commands run in a hunt channel |
| `bot/web/data.py` | modify | `get_hunt_sheets`; evidence serialisation gains the sheet source |
| `bot/web/api.py` | modify | `hunt_sheets` in the hunt payload; `POST /api/evidence` accepts `source_sheet_id` |
| `frontend/src/types.ts` | modify | `HuntSheet`; `HuntDetail.hunt_sheets`; `EvidenceItem` sheet source |
| `frontend/src/api.ts` | modify | `useAddEvidence` carries a sheet source |
| `frontend/src/components/HuntInfoPanel.tsx` | modify | Hunt sheet links |
| `frontend/src/pages/HuntBoard.tsx` | modify | Pass `hunt_sheets` through |
| `frontend/src/components/EvidencePanel.tsx` | modify | Combined source picker, filter, and display |
| `docs/commands.md` | modify | Updated Sheets section |
| `tests/test_hunt_sheets.py` | new | DAO, lock, and service tests |
| `tests/test_hunt_sheet_commands.py` | new | Cog command tests |

---

## Task 1: Migration — `hunt_id` on sheets, `source_sheet_id` on evidence

**Files:**
- Create: `migrations/031_hunt_level_sheets.sql`

Ships **before** any code that reads or writes the new columns. The generic `commit()` in
`mysqldb.py` serialises every dataclass field by name, so adding a field to
`AdditionalSheetData` before its column exists breaks every existing sheet write.

- [ ] **Step 1: Check the migration number is free**

Run: `ls migrations | tail -3`

Expected: highest is `030_media_and_sourceless_evidence.sql`. If a parallel worktree has
already claimed `031`, use the next free number — git merges colliding numbers cleanly
without noticing, so this must be checked by eye.

- [ ] **Step 2: Write the migration**

```sql
-- 031_hunt_level_sheets.sql
-- Plan C (hunt-level working sheets). Two changes:
--
-- 1. additional_sheets gains a nullable hunt_id. puzzle_id is ALREADY nullable
--    (022's cleanup guards `WHERE a.puzzle_id IS NOT NULL`), but with no hunt_id
--    a puzzle-less row was unreachable -- every read path went through a puzzle.
--    A hunt-level sheet is exactly `puzzle_id IS NULL AND hunt_id = <h>`.
--
-- 2. evidence_items gains source_sheet_id: a fact can come from a hunt-level
--    working sheet rather than from a puzzle. At most one of source_puzzle_id /
--    source_sheet_id is set; that invariant is owned by the service methods that
--    write each kind, not by a check constraint (this schema uses none).
--
-- Deleting a sheet must not delete the notes written about it, so
-- source_sheet_id is ON DELETE SET NULL -- the same choice made for
-- evidence_items.media_id in 030. additional_sheets.hunt_id is ON DELETE CASCADE:
-- a hunt's sheets are part of the hunt and go with it.

ALTER TABLE `additional_sheets`
  ADD COLUMN `hunt_id` INT(11) DEFAULT NULL,
  ADD KEY `idx_additional_sheets_hunt` (`hunt_id`),
  ADD CONSTRAINT `AdditionalSheetHuntID` FOREIGN KEY (`hunt_id`)
      REFERENCES `hunts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE `evidence_items`
  ADD COLUMN `source_sheet_id` INT(11) DEFAULT NULL,
  ADD CONSTRAINT `EvidenceSourceSheetID` FOREIGN KEY (`source_sheet_id`)
      REFERENCES `additional_sheets` (`id`) ON DELETE SET NULL ON UPDATE CASCADE;
```

- [ ] **Step 3: Apply it**

Run: `pipenv run python migrate.py migrate`
Expected: reports `031_hunt_level_sheets.sql` applied, exit 0.

- [ ] **Step 4: Verify the schema changed**

Run: `pipenv run python migrate.py status`
Expected: `031` listed as applied, nothing pending.

- [ ] **Step 5: Commit**

```bash
git add migrations/031_hunt_level_sheets.sql
git commit -m "feat(db): allow a working sheet to belong to a hunt, and evidence to cite one"
```

---

## Task 2: `AdditionalSheetData` carries a hunt and an optional puzzle

**Files:**
- Modify: `bot/store/puzzle_data.py:15-22`
- Test: `tests/test_hunt_sheets.py` (new)

`puzzle_id` must become nullable **as well as** `hunt_id` being added. It defaults to `0`
today, and `additional_sheets.puzzle_id` carries the `AdditionalSheetPuzzleID` FK from
migration 022 — so committing a hunt-level sheet that left `puzzle_id` at its default would
try to write `puzzle_id = 0`, which no `puzzles` row has, and the insert would fail on the
FK. `None` is the only value that is legal for a sheet with no puzzle.

- [ ] **Step 1: Write the failing test**

Create `tests/test_hunt_sheets.py`:

```python
"""Plan C: working sheets that belong to a hunt rather than to a puzzle."""
from unittest.mock import AsyncMock, MagicMock, patch


class TestAdditionalSheetData:
    def test_hunt_id_defaults_to_none(self):
        from bot.store.puzzle_data import AdditionalSheetData
        assert AdditionalSheetData().hunt_id is None

    def test_puzzle_id_defaults_to_none(self):
        """A sheet can belong to the hunt rather than to a puzzle, so the default
        is 'no puzzle', not the sentinel 0. 0 is not merely wrong-looking: the
        AdditionalSheetPuzzleID FK from 022 has no puzzles row with id 0, so a
        commit carrying it would fail on the constraint."""
        from bot.store.puzzle_data import AdditionalSheetData
        assert AdditionalSheetData().puzzle_id is None

    def test_a_hunt_level_sheet_commits_a_null_puzzle_id(self):
        """The generic commit() serialises every field by name. Assert on the SQL
        it actually built, not on the dataclass -- a default that silently became
        0 again would be invisible from the object side."""
        from bot.store.mysqldb import MySQLAdditionalSheetsDb
        from bot.store.puzzle_data import AdditionalSheetData
        mydb = MagicMock()
        cursor = MagicMock()
        cursor.lastrowid = 7
        mydb.cursor.return_value = cursor
        db = MySQLAdditionalSheetsDb(mydb=mydb)
        db.commit(AdditionalSheetData(hunt_id=10, puzzle_name="scratch"))
        sql, params = cursor.execute.call_args[0]
        fields = [f.strip(" `") for f in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
        assert dict(zip(fields, params))["puzzle_id"] is None
        assert dict(zip(fields, params))["hunt_id"] == 10
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheets.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: 'AdditionalSheetData' object has no attribute 'hunt_id'`.
Read `/tmp/test.log`.

- [ ] **Step 3: Update the dataclass**

In `bot/store/puzzle_data.py`, replace the `AdditionalSheetData` body:

```python
@dataclass
class AdditionalSheetData:
    id: int = 0
    google_page_id: str = ""
    # None when the sheet belongs to the hunt rather than to a puzzle. NOT 0:
    # the AdditionalSheetPuzzleID FK (migration 022) would reject that.
    puzzle_id: Optional[int] = None
    # Set only for hunt-level sheets; a puzzle's own sheets reach their hunt
    # through the puzzle.
    hunt_id: Optional[int] = None
    puzzle: bool = False
    puzzle_name: str = ""
    solution: str = ""
    solved: bool = False
```

`Optional` is already imported at the top of that file (`PuzzleData.solution` uses it). If
it is not, add `from typing import Optional`.

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheets.py -q > /tmp/test.log 2>&1`
Expected: 3 passed.

- [ ] **Step 5: Check nothing else broke**

Run: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: all pass. `MySQLPuzzleJsonDb.get_additional_sheets` copies every field off the row
by name, so the new `hunt_id` is populated for puzzle sheets too (as NULL).

- [ ] **Step 6: Commit**

```bash
git add bot/store/puzzle_data.py tests/test_hunt_sheets.py
git commit -m "feat(store): AdditionalSheetData carries a hunt and an optional puzzle"
```

---

## Task 3: `get_by_hunt()` makes puzzle-less sheets reachable

**Files:**
- Modify: `bot/store/mysqldb.py:304-312` (`MySQLAdditionalSheetsDb`)
- Test: `tests/test_hunt_sheets.py` (append)

`get_by_puzzle` returns raw rows. `get_by_hunt` returns `AdditionalSheetData` objects
instead, because every caller of it (the service, the cog, the web layer) wants the object —
the raw-row shape exists only because `construct_puzzle` copies fields onto a puzzle.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hunt_sheets.py`:

```python
class TestGetByHunt:
    def _db(self):
        from bot.store.mysqldb import MySQLAdditionalSheetsDb
        mydb = MagicMock()
        cursor = MagicMock()
        mydb.dict_cursor.return_value = cursor
        return MySQLAdditionalSheetsDb(mydb=mydb), cursor

    def _row(self, id, name, page_id="P1"):
        return {"id": id, "google_page_id": page_id, "puzzle_id": None,
                "hunt_id": 10, "puzzle": 0, "puzzle_name": name,
                "solution": "", "solved": 0}

    def test_returns_sheet_objects(self):
        db, cursor = self._db()
        cursor.fetchall.return_value = [self._row(3, "alt text")]
        sheets = db.get_by_hunt(10)
        assert [s.id for s in sheets] == [3]
        assert sheets[0].puzzle_name == "alt text"
        assert sheets[0].hunt_id == 10
        assert sheets[0].puzzle_id is None

    def test_excludes_puzzle_owned_sheets(self):
        """A puzzle's sheet also belongs to a hunt transitively; only sheets with
        NO puzzle are hunt-level, and the query -- not the caller -- must say so."""
        db, cursor = self._db()
        cursor.fetchall.return_value = []
        db.get_by_hunt(10)
        sql, params = cursor.execute.call_args[0]
        assert "puzzle_id IS NULL" in sql
        assert params == (10,)

    def test_orders_by_id_so_the_list_is_stable(self):
        """!remove_sheet takes a 1-based index off !list_sheets, so two calls that
        disagree about order would delete the wrong sheet. Two rows, because one
        cannot have a wrong order."""
        db, cursor = self._db()
        cursor.fetchall.return_value = [self._row(3, "first"), self._row(9, "second")]
        sheets = db.get_by_hunt(10)
        assert [s.puzzle_name for s in sheets] == ["first", "second"]
        sql, _ = cursor.execute.call_args[0]
        assert "ORDER BY" in sql and "id" in sql.split("ORDER BY", 1)[1]

    def test_returns_empty_list_when_there_are_none(self):
        db, cursor = self._db()
        cursor.fetchall.return_value = None
        assert db.get_by_hunt(10) == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheets.py -q -k GetByHunt > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: 'MySQLAdditionalSheetsDb' object has no attribute 'get_by_hunt'`.

- [ ] **Step 3: Implement**

In `bot/store/mysqldb.py`, add to `MySQLAdditionalSheetsDb` beneath `get_by_puzzle`:

```python
    def get_by_hunt(self, hunt_id):
        """Return the hunt's OWN working sheets -- those with no puzzle.

        A puzzle's sheets belong to the hunt transitively and are loaded through
        the puzzle (construct_puzzle -> get_additional_sheets); they are
        deliberately excluded here. Ordered by id so !list_sheets and the index
        !remove_sheet takes from it always agree.
        """
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "SELECT * FROM additional_sheets"
                " WHERE hunt_id = %s AND puzzle_id IS NULL ORDER BY id ASC",
                (hunt_id,),
            )
            rows = cursor.fetchall() or []
        finally:
            cursor.close()
        sheets = []
        for row in rows:
            sheet = AdditionalSheetData()
            for attr in sheet.__dict__:
                if attr in row:
                    setattr(sheet, attr, row[attr])
            sheets.append(sheet)
        return sheets
```

`AdditionalSheetData` is already imported at the top of `bot/store/mysqldb.py` (it is used by
`get_additional_sheets`). Confirm with
`grep -n "AdditionalSheetData" bot/store/mysqldb.py | head -2` and add the import beside the
other store-data imports if it is missing.

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheets.py -q > /tmp/test.log 2>&1`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/store/mysqldb.py tests/test_hunt_sheets.py
git commit -m "feat(store): add get_by_hunt so a hunt's own sheets are reachable"
```

---

## Task 4: A hunt-keyed lock for sheet commands

**Files:**
- Modify: `bot/core/locks.py`
- Test: `tests/test_hunt_sheets.py` (append)

`with_puzzle_mutex` serialises on `_PUZZLE_LOCKS[puzzle.id]` but explicitly no-ops when there
is no puzzle in context (`bot/cogs/puzzles.py:61` — *"If not in a puzzle context, just
run"*). A hunt-level `!add_sheet` would therefore run unserialised, and two people adding one
at once would race on the Sheets API. A **separate registry** is required: a puzzle id and a
hunt id are both plain ints from different tables, so sharing one would make "puzzle 5" and
"hunt 5" collide on a single lock — the same reasoning the file already records for
`_ROLLUP_LOCKS` and `_HUNT_CREATE_LOCKS`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hunt_sheets.py`:

```python
class TestHuntSheetLock:
    async def test_second_holder_waits_for_the_first(self):
        import asyncio
        from bot.core.locks import hunt_sheet_lock
        order = []

        async def hold(tag, delay):
            async with hunt_sheet_lock(10):
                order.append(f"{tag}-in")
                await asyncio.sleep(delay)
                order.append(f"{tag}-out")

        await asyncio.gather(hold("a", 0.02), hold("b", 0))
        # Whichever ran first, its critical section is not interleaved with the
        # other's -- that is the whole point of the lock.
        assert order in (["a-in", "a-out", "b-in", "b-out"],
                         ["b-in", "b-out", "a-in", "a-out"])

    async def test_different_hunts_do_not_block_each_other(self):
        import asyncio
        from bot.core.locks import hunt_sheet_lock
        async with hunt_sheet_lock(10):
            # A second hunt's lock must be free while hunt 10's is held; a shared
            # registry keyed on a bare int would deadlock this.
            await asyncio.wait_for(
                asyncio.shield(_take_and_release(hunt_sheet_lock, 11)), timeout=1)

    async def test_registry_is_separate_from_the_puzzle_locks(self):
        """A hunt id and a puzzle id are different int spaces. Sharing one
        registry would make hunt 5 and puzzle 5 serialise against each other."""
        from bot.core.locks import _HUNT_SHEET_LOCKS, _PUZZLE_LOCKS
        assert _HUNT_SHEET_LOCKS is not _PUZZLE_LOCKS
        async with _PUZZLE_LOCKS[5]:
            assert not _HUNT_SHEET_LOCKS[5].locked()


async def _take_and_release(lock_cm, key):
    async with lock_cm(key):
        return True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheets.py -q -k HuntSheetLock > /tmp/test.log 2>&1`
Expected: FAIL — `ImportError: cannot import name 'hunt_sheet_lock' from 'bot.core.locks'`.

- [ ] **Step 3: Implement**

In `bot/core/locks.py`, beside `_HUNT_CREATE_LOCKS`:

```python
# One asyncio.Lock per HUNT id, serializing writes to that hunt's OWN working
# sheets. Needed because with_puzzle_mutex no-ops when there is no puzzle in
# context, so a hunt-level !add_sheet would otherwise race another on the Sheets
# API. Separate registry again (hunt id is a different int space from puzzle id),
# and separate from _HUNT_CREATE_LOCKS: that one guards puzzle/group creation, and
# reusing it would make adding a scratch sheet block someone creating a puzzle.
_HUNT_SHEET_LOCKS = defaultdict(asyncio.Lock)
```

and, beside `hunt_create_lock`:

```python
@asynccontextmanager
async def hunt_sheet_lock(hunt_id):
    """Serialize writes to a hunt's own working sheets (add/remove), so two
    people running !add_sheet in the hunt channel at once cannot race on the
    Sheets API.

    Held at the edge (the cog decorator), like the puzzle lock, so the lock-free
    PuzzleService it calls never re-acquires it. Never held at the same time as a
    puzzle lock: a sheet command has either a puzzle or a hunt in context, never
    both, so there is no lock-ordering cycle to reason about.
    """
    async with _HUNT_SHEET_LOCKS[hunt_id]:
        yield
```

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheets.py -q > /tmp/test.log 2>&1`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/core/locks.py tests/test_hunt_sheets.py
git commit -m "feat(core): add a hunt-keyed lock for hunt-level sheet writes"
```

---

## Task 5: Service — create a hunt-level sheet

**Files:**
- Modify: `bot/core/puzzle_service.py` (beside `add_sheet`, ~line 2394)
- Test: `tests/test_hunt_sheets.py` (append)

A plain blank tab on the hunt's **main** spreadsheet — never the solved sheet; a hunt sheet
is never solved. The name goes through `get_unique_tab_name` first, because `create_blank_tab`
(unlike `add_new_sheet`) does not dedupe, and Sheets rejects a duplicate tab title outright.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hunt_sheets.py`:

```python
class TestAddHuntSheet:
    def _svc(self, gsheet):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService.__new__(PuzzleService)
        svc.notifier = MagicMock(board_changed=AsyncMock())
        svc._gsheet_cog = MagicMock(return_value=gsheet)
        return svc

    def _gsheet(self):
        g = MagicMock()
        g.get_unique_tab_name = AsyncMock(side_effect=lambda name, sid=None: name)
        g.create_blank_tab = AsyncMock(return_value=555)
        return g

    async def test_creates_a_blank_tab_on_the_hunt_sheet_and_commits_a_row(self):
        from tests.conftest import make_hunt
        hunt = make_hunt(id=10, google_sheet_id="SHEET1", solved_sheet_id="ARCHIVE1")
        gsheet = self._gsheet()
        svc = self._svc(gsheet)
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sheet = await svc.add_hunt_sheet(hunt, "alt text")
        gsheet.create_blank_tab.assert_awaited_once()
        args = gsheet.create_blank_tab.await_args[0]
        # The hunt's MAIN spreadsheet, never the solved one -- a hunt sheet is
        # never solved, and the solved sheet is a different spreadsheet id.
        assert args[0] == "SHEET1"
        assert args[1] == "alt text"
        assert sheet.hunt_id == 10
        assert sheet.puzzle_id is None
        assert sheet.puzzle_name == "alt text"
        assert sheet.google_page_id == 555
        sdb.commit.assert_called_once_with(sheet)

    async def test_dedupes_the_tab_name(self):
        """create_blank_tab does not dedupe (add_new_sheet does), and Sheets
        rejects a duplicate tab title, so a second 'scratch' would blow up."""
        from tests.conftest import make_hunt
        gsheet = self._gsheet()
        gsheet.get_unique_tab_name = AsyncMock(return_value="scratch (2)")
        svc = self._svc(gsheet)
        with patch("bot.core.puzzle_service.SheetsJsonDb"):
            sheet = await svc.add_hunt_sheet(make_hunt(id=10), "scratch")
        assert gsheet.create_blank_tab.await_args[0][1] == "scratch (2)"
        assert sheet.puzzle_name == "scratch (2)"

    async def test_blank_name_falls_back_to_a_default(self):
        from tests.conftest import make_hunt
        gsheet = self._gsheet()
        svc = self._svc(gsheet)
        with patch("bot.core.puzzle_service.SheetsJsonDb"):
            sheet = await svc.add_hunt_sheet(make_hunt(id=10, name="Edric 2026"), "")
        assert sheet.puzzle_name == "Sheet for Edric 2026"

    async def test_no_gsheet_cog_still_records_the_sheet(self):
        """Mirrors add_sheet: a hunt with no Google integration still gets its
        row, just with no tab behind it."""
        from tests.conftest import make_hunt
        svc = self._svc(None)
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sheet = await svc.add_hunt_sheet(make_hunt(id=10), "alt text")
        assert sheet.google_page_id == ""
        sdb.commit.assert_called_once()
```

You must also add the new method to the board-push inventory, or
`tests/test_board_push_service.py::test_every_mutation_carries_the_push_decorator` fails —
it asserts an **exact** set. In `tests/test_board_push_service.py`, add `"add_hunt_sheet"`
to `EXPECTED_PUSH_METHODS` (the set is alphabetical; it goes before `"add_note"`).

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheets.py -q -k AddHuntSheet > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: 'PuzzleService' object has no attribute 'add_hunt_sheet'`.

- [ ] **Step 3: Implement**

In `bot/core/puzzle_service.py`, immediately after `remove_sheet_by_index` (~line 2427):

```python
    @pushes_board()
    async def add_hunt_sheet(self, hunt, name):
        """Create a working sheet that belongs to the HUNT, not to a puzzle.

        Somewhere to pick at something found in a page's alt text, or loose on the
        hunt site, before anyone knows which puzzle it belongs to. Returns the
        AdditionalSheetData.

        A plain blank tab, not the PUZZLE_SHEET template: create_additional_
        spreadsheet derives its insert index from the puzzle's own tab and writes
        a header hyperlink from the puzzle's url/name, none of which means
        anything here. Always on the hunt's MAIN spreadsheet -- a hunt sheet is
        never solved, so the solved spreadsheet is never the target.
        """
        gsheet_cog = self._gsheet_cog(hunt)
        sheet = AdditionalSheetData(
            hunt_id=hunt.id,
            puzzle_name=name or f"Sheet for {hunt.name}",
        )
        if gsheet_cog is not None:
            # create_blank_tab does NOT dedupe the way add_new_sheet does, and the
            # Sheets API rejects a duplicate tab title, so uniquify first and keep
            # the name we actually used.
            unique = await gsheet_cog.get_unique_tab_name(
                sheet.puzzle_name, hunt.google_sheet_id)
            sheet.puzzle_name = unique
            sheet.google_page_id = await gsheet_cog.create_blank_tab(
                hunt.google_sheet_id, unique)
        SheetsJsonDb.commit(sheet)
        return sheet
```

`AdditionalSheetData` and `SheetsJsonDb` are both already imported in this module
(`bot/core/puzzle_service.py:25` and the `from bot.store import ...` block). Confirm with
`grep -n "SheetsJsonDb" bot/core/puzzle_service.py | head -2`.

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheets.py tests/test_board_push_service.py -q > /tmp/test.log 2>&1`
Expected: 14 passed in `test_hunt_sheets.py`, and `test_board_push_service.py` still green
(it will fail if you skipped the `EXPECTED_PUSH_METHODS` addition in Step 3).

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_hunt_sheets.py tests/test_board_push_service.py
git commit -m "feat(core): create a working sheet that belongs to the hunt"
```

---

## Task 6: Service — list and remove hunt-level sheets

**Files:**
- Modify: `bot/core/puzzle_service.py` (beside `add_hunt_sheet`)
- Test: `tests/test_hunt_sheets.py` (append)

`_delete_additional_sheet` cannot be reused: it reads `puzzle.solved` to decide which
spreadsheet the tab lives on and mutates `puzzle.additional_sheets`. A hunt sheet has neither.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hunt_sheets.py`:

```python
class TestRemoveHuntSheet:
    def _svc(self, gsheet):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService.__new__(PuzzleService)
        svc.notifier = MagicMock(board_changed=AsyncMock())
        svc._gsheet_cog = MagicMock(return_value=gsheet)
        return svc

    def _sheet(self, id, name, page_id):
        from bot.store.puzzle_data import AdditionalSheetData
        return AdditionalSheetData(id=id, hunt_id=10, puzzle_name=name,
                                   google_page_id=page_id)

    async def test_get_hunt_sheets_delegates_to_the_dao(self):
        from tests.conftest import make_hunt
        svc = self._svc(None)
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sdb.get_by_hunt.return_value = [self._sheet(1, "a", "P1")]
            sheets = svc.get_hunt_sheets(make_hunt(id=10))
        sdb.get_by_hunt.assert_called_once_with(10)
        assert [s.puzzle_name for s in sheets] == ["a"]

    async def test_remove_deletes_the_tab_and_the_row(self):
        from tests.conftest import make_hunt
        gsheet = MagicMock()
        gsheet.delete_sheet = AsyncMock()
        svc = self._svc(gsheet)
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sdb.get_by_hunt.return_value = [self._sheet(4, "alt text", "P9")]
            await svc.remove_hunt_sheet(make_hunt(id=10), 4)
        # archive=False: a hunt sheet is never on the solved spreadsheet.
        gsheet.delete_sheet.assert_awaited_once_with("P9", False)
        sdb.delete.assert_called_once_with(4)

    async def test_remove_by_index_picks_the_right_sheet(self):
        """Two sheets, because with one the index cannot be wrong. The ids are
        NOT in index order, so an implementation that passed the index straight
        through as an id would fail rather than accidentally agree.
        """
        from tests.conftest import make_hunt
        gsheet = MagicMock()
        gsheet.delete_sheet = AsyncMock()
        svc = self._svc(gsheet)
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sdb.get_by_hunt.return_value = [self._sheet(7, "first", "P7"),
                                            self._sheet(2, "second", "P2")]
            await svc.remove_hunt_sheet_by_index(make_hunt(id=10), 2)
        gsheet.delete_sheet.assert_awaited_once_with("P2", False)
        sdb.delete.assert_called_once_with(2)

    async def test_remove_by_index_rejects_out_of_range(self):
        import pytest
        from tests.conftest import make_hunt
        svc = self._svc(MagicMock())
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sdb.get_by_hunt.return_value = [self._sheet(7, "first", "P7")]
            with pytest.raises(IndexError):
                await svc.remove_hunt_sheet_by_index(make_hunt(id=10), 2)
            with pytest.raises(IndexError):
                await svc.remove_hunt_sheet_by_index(make_hunt(id=10), 0)

    async def test_remove_unknown_id_raises(self):
        import pytest
        from tests.conftest import make_hunt
        svc = self._svc(MagicMock())
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sdb.get_by_hunt.return_value = []
            with pytest.raises(RuntimeError, match="sheet_not_found"):
                await svc.remove_hunt_sheet(make_hunt(id=10), 4)

    async def test_a_missing_tab_does_not_stop_the_row_being_deleted(self):
        """Idempotent, exactly as _delete_additional_sheet is: a tab someone
        already deleted by hand must not leave an unremovable row behind."""
        from tests.conftest import make_hunt
        gsheet = MagicMock()
        gsheet.delete_sheet = AsyncMock(side_effect=Exception("boom"))
        svc = self._svc(gsheet)
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sdb.get_by_hunt.return_value = [self._sheet(4, "alt text", "P9")]
            await svc.remove_hunt_sheet(make_hunt(id=10), 4)
        sdb.delete.assert_called_once_with(4)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheets.py -q -k RemoveHuntSheet > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: 'PuzzleService' object has no attribute 'get_hunt_sheets'`.

- [ ] **Step 3: Implement**

In `bot/core/puzzle_service.py`, after `add_hunt_sheet`:

```python
    def get_hunt_sheets(self, hunt):
        """The hunt's own working sheets, ordered as !list_sheets numbers them."""
        return SheetsJsonDb.get_by_hunt(hunt.id)

    @pushes_board()
    async def remove_hunt_sheet(self, hunt, sheet_id):
        """Remove one of the hunt's own working sheets by id. Raises
        RuntimeError("sheet_not_found") if absent.

        Not _delete_additional_sheet: that reads puzzle.solved to pick the
        spreadsheet and mutates puzzle.additional_sheets, and a hunt sheet has
        neither a puzzle nor a solved state.
        """
        sheet = next((s for s in SheetsJsonDb.get_by_hunt(hunt.id)
                      if s.id == sheet_id), None)
        if sheet is None:
            raise RuntimeError("sheet_not_found")
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            try:
                # archive=False: a hunt sheet is never on the solved spreadsheet.
                await gsheet_cog.delete_sheet(sheet.google_page_id, False)
            except Exception:
                # Idempotent, as _delete_additional_sheet is: a tab someone
                # already removed by hand must not leave an undeletable row.
                logger.exception(
                    "Failed to delete Google tab for hunt sheet %s", sheet.id)
        SheetsJsonDb.delete(sheet.id)

    async def remove_hunt_sheet_by_index(self, hunt, index):
        """Remove one of the hunt's own sheets by its 1-based !list_sheets number.
        Raises IndexError if out of bounds."""
        sheets = SheetsJsonDb.get_by_hunt(hunt.id)
        if index < 1 or index > len(sheets):
            raise IndexError(f"Sheet index {index} out of range (1–{len(sheets)})")
        await self.remove_hunt_sheet(hunt, sheets[index - 1].id)
```

`remove_hunt_sheet` carries `@pushes_board()`, so add `"remove_hunt_sheet"` to
`EXPECTED_PUSH_METHODS` in `tests/test_board_push_service.py` (before `"remove_minimeta"`).
`remove_hunt_sheet_by_index` does **not** carry it — it delegates, and the re-entrancy guard
would collapse the pushes anyway, exactly as `remove_sheet_by_index` is left undecorated
today.

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheets.py tests/test_board_push_service.py -q > /tmp/test.log 2>&1`
Expected: 20 passed in `test_hunt_sheets.py`, `test_board_push_service.py` still green.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_hunt_sheets.py tests/test_board_push_service.py
git commit -m "feat(core): list and remove a hunt's own working sheets"
```

---

## Task 7: `with_hunt_sheet_mutex` decorator

**Files:**
- Modify: `bot/cogs/puzzles.py` (beside `with_puzzle_mutex`, ~line 50)
- Test: `tests/test_hunt_sheet_commands.py` (new)

Deliberately a **second** decorator rather than a change to `with_puzzle_mutex`: that one is
applied to ~30 commands, and giving it a hunt fallback would start taking a hunt lock for
every hunt-channel command in the cog. Stacking the two on the three sheet commands takes
exactly one lock in either case — the puzzle lock when there is a puzzle, the hunt lock when
there is not.

- [ ] **Step 1: Write the failing test**

Create `tests/test_hunt_sheet_commands.py`:

```python
"""Plan C: the three sheet commands running in a hunt channel."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_ctx, make_cog, make_hunt, make_puzzle


class TestHuntSheetMutex:
    def _decorated(self, cog_puzzle, calls):
        from bot.cogs.puzzles import with_hunt_sheet_mutex

        @with_hunt_sheet_mutex()
        async def command(self, ctx):
            calls.append("ran")
            return "done"

        cog = make_cog(puzzle=cog_puzzle, hunt=make_hunt(id=10))
        return command, cog

    async def test_takes_the_hunt_lock_when_there_is_no_puzzle(self):
        from bot.core.locks import _HUNT_SHEET_LOCKS
        calls = []

        async def command_body(self, ctx):
            # Observed from INSIDE the critical section: asserting afterwards
            # would pass even if the lock were never taken at all.
            calls.append(_HUNT_SHEET_LOCKS[10].locked())

        from bot.cogs.puzzles import with_hunt_sheet_mutex
        command = with_hunt_sheet_mutex()(command_body)
        cog = make_cog(puzzle=None, hunt=make_hunt(id=10))
        await command(cog, make_ctx())
        assert calls == [True]
        assert not _HUNT_SHEET_LOCKS[10].locked()

    async def test_does_not_take_the_hunt_lock_when_there_is_a_puzzle(self):
        """with_puzzle_mutex already covers that case; taking both would be two
        locks for one command and a fresh deadlock surface."""
        from bot.core.locks import _HUNT_SHEET_LOCKS
        calls = []

        async def command_body(self, ctx):
            calls.append(_HUNT_SHEET_LOCKS[10].locked())

        from bot.cogs.puzzles import with_hunt_sheet_mutex
        command = with_hunt_sheet_mutex()(command_body)
        cog = make_cog(puzzle=make_puzzle(id=5, hunt_id=10), hunt=make_hunt(id=10))
        await command(cog, make_ctx())
        assert calls == [False]

    async def test_runs_unlocked_when_there_is_no_hunt_either(self):
        calls = []

        async def command_body(self, ctx):
            calls.append("ran")

        from bot.cogs.puzzles import with_hunt_sheet_mutex
        command = with_hunt_sheet_mutex()(command_body)
        cog = make_cog(puzzle=None, hunt=None)
        cog.get_hunt = lambda ctx: None
        await command(cog, make_ctx())
        assert calls == ["ran"]

    async def test_passes_arguments_and_the_return_value_through(self):
        from bot.cogs.puzzles import with_hunt_sheet_mutex

        @with_hunt_sheet_mutex()
        async def command(self, ctx, number: int):
            return number * 2

        cog = make_cog(puzzle=None, hunt=make_hunt(id=10))
        assert await command(cog, make_ctx(), 3) == 6
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheet_commands.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `ImportError: cannot import name 'with_hunt_sheet_mutex'`.

- [ ] **Step 3: Implement**

In `bot/cogs/puzzles.py`, immediately after `with_puzzle_mutex`'s `decorator` returns:

```python
def with_hunt_sheet_mutex():
    """Serialize a sheet command that has NO puzzle in context, on its hunt.

    with_puzzle_mutex no-ops without a puzzle ("If not in a puzzle context, just
    run"), so a hunt-level !add_sheet would otherwise run unserialised and two
    people running it at once would race on the Sheets API.

    Stacked BENEATH with_puzzle_mutex on the sheet commands, and deliberately
    inert when there IS a puzzle: exactly one lock is taken either way, so the
    two decorators never both hold and there is no lock-ordering cycle.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(self, ctx: commands.Context, *args, **kwargs):
            # get_puzzle/get_hunt rather than with_puzzle_mutex's
            # `getattr(ctx, "env", ...)`: nothing in the bot ever sets ctx.env, so
            # that branch is vestigial there, and the cog's own accessors read the
            # same environment cog_before_invoke populated.
            if self.get_puzzle(ctx) is not None:
                return await func(self, ctx, *args, **kwargs)
            hunt = self.get_hunt(ctx)
            if hunt is None:
                # No hunt resolved -- the command will refuse anyway; nothing to
                # serialise on.
                return await func(self, ctx, *args, **kwargs)
            async with hunt_sheet_lock(hunt.id):
                return await func(self, ctx, *args, **kwargs)
        return wrapper
    return decorator
```

Widen the locks import on `bot/cogs/puzzles.py:28`:

```python
from bot.core.locks import _PUZZLE_LOCKS, puzzles_lock, hunt_sheet_lock
```

The decorator deliberately does **not** copy `with_puzzle_mutex`'s
`getattr(ctx, "env", None)` fallback. `grep -rn "ctx.env" bot/` finds nothing — nothing in
the bot ever sets it, so on a real `commands.Context` that expression is always `None` and
the branch is dead. It is worse than dead in tests: `make_ctx()` returns a `MagicMock`, whose
`.env` auto-vivifies to a truthy Mock, so `(env or {}).get("puzzle")` is truthy and the
decorator concludes there is a puzzle when there is none. That is the house
"a MagicMock collection attribute is truthy" gotcha, and it silently disables the lock.

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheet_commands.py -q > /tmp/test.log 2>&1`
Expected: 4 passed.

If `test_takes_the_hunt_lock_when_there_is_no_puzzle` fails with a `KeyError` on
`self.environment`, `make_cog` does not set that attribute — add
`cog.environment = {}` after building the cog in each test here.

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_hunt_sheet_commands.py
git commit -m "feat(bot): add a hunt-keyed mutex for sheet commands with no puzzle"
```

---

## Task 8: `!add_sheet` and `!remove_sheet` run in a hunt channel

**Files:**
- Modify: `bot/cogs/puzzles.py:1277-1313` (`add_sheet`, `remove_sheet`)
- Test: `tests/test_hunt_sheet_commands.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hunt_sheet_commands.py`:

```python
class TestAddSheetCommand:
    async def test_hunt_channel_creates_a_hunt_level_sheet(self):
        from bot.cogs.puzzles import Puzzles
        hunt = make_hunt(id=10)
        cog = make_cog(puzzle=None, hunt=hunt)
        cog.environment = {}
        cog.service.add_hunt_sheet = AsyncMock(
            return_value=SimpleNamespace(id=1, puzzle_name="alt text"))
        ctx = make_ctx()
        await Puzzles.add_sheet(cog, ctx, "alt", "text")
        cog.service.add_hunt_sheet.assert_awaited_once_with(hunt, "alt text")
        assert ":white_check_mark:" in ctx.send.await_args[0][0]

    async def test_puzzle_channel_is_unchanged(self):
        from bot.cogs.puzzles import Puzzles
        hunt = make_hunt(id=10)
        puzzle = make_puzzle(id=5, hunt_id=10, name="Alpha")
        cog = make_cog(puzzle=puzzle, hunt=hunt)
        cog.environment = {}
        cog.service.add_sheet = AsyncMock()
        cog.service.add_hunt_sheet = AsyncMock()
        cog.info = AsyncMock()
        await Puzzles.add_sheet(cog, make_ctx(), "grid", "2")
        cog.service.add_sheet.assert_awaited_once_with(hunt, puzzle, "grid 2")
        cog.service.add_hunt_sheet.assert_not_awaited()

    async def test_no_hunt_at_all_refuses(self):
        """Outside any hunt category there is nothing to attach a sheet to."""
        from bot.cogs.puzzles import Puzzles
        cog = make_cog(puzzle=None, hunt=None)
        cog.get_hunt = lambda ctx: None
        cog.environment = {}
        cog.service.add_hunt_sheet = AsyncMock()
        ctx = make_ctx()
        await Puzzles.add_sheet(cog, ctx, "alt")
        cog.service.add_hunt_sheet.assert_not_awaited()
        assert ":x:" in ctx.send.await_args[0][0]


class TestRemoveSheetCommand:
    async def test_hunt_channel_removes_by_index(self):
        from bot.cogs.puzzles import Puzzles
        hunt = make_hunt(id=10)
        cog = make_cog(puzzle=None, hunt=hunt)
        cog.environment = {}
        sheet = SimpleNamespace(id=4, puzzle_name="alt text")
        cog.service.get_hunt_sheets = MagicMock(return_value=[sheet])
        cog.service.remove_hunt_sheet_by_index = AsyncMock()
        ctx = make_ctx()
        await Puzzles.remove_sheet(cog, ctx, 1)
        cog.service.remove_hunt_sheet_by_index.assert_awaited_once_with(hunt, 1)
        assert "alt text" in ctx.send.await_args[0][0]

    async def test_hunt_channel_rejects_a_bad_index(self):
        from bot.cogs.puzzles import Puzzles
        cog = make_cog(puzzle=None, hunt=make_hunt(id=10))
        cog.environment = {}
        cog.service.get_hunt_sheets = MagicMock(return_value=[])
        cog.service.remove_hunt_sheet_by_index = AsyncMock()
        ctx = make_ctx()
        await Puzzles.remove_sheet(cog, ctx, 1)
        cog.service.remove_hunt_sheet_by_index.assert_not_awaited()
        assert ":x:" in ctx.send.await_args[0][0]

    async def test_names_the_sheet_the_index_actually_points_at(self):
        """Two sheets: with one, a message that always named sheets[0] would pass.
        """
        from bot.cogs.puzzles import Puzzles
        cog = make_cog(puzzle=None, hunt=make_hunt(id=10))
        cog.environment = {}
        cog.service.get_hunt_sheets = MagicMock(return_value=[
            SimpleNamespace(id=7, puzzle_name="first"),
            SimpleNamespace(id=2, puzzle_name="second"),
        ])
        cog.service.remove_hunt_sheet_by_index = AsyncMock()
        ctx = make_ctx()
        await Puzzles.remove_sheet(cog, ctx, 2)
        assert "second" in ctx.send.await_args[0][0]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheet_commands.py -q -k "AddSheetCommand or RemoveSheetCommand" > /tmp/test.log 2>&1`
Expected: FAIL — the hunt-channel cases hit `send_not_puzzle_channel`, so
`add_hunt_sheet` is never awaited.

- [ ] **Step 3: Implement**

Replace `add_sheet` in `bot/cogs/puzzles.py`:

```python
    @commands.command()
    @with_puzzle_mutex(wait=True)
    @with_hunt_sheet_mutex()
    async def add_sheet(self, ctx, *args):
        """*Add an extra working sheet (tab) to this puzzle, or to the hunt.*

        Usage: `!add_sheet sheet-name`
        Example: `!add_sheet grid attempt 2`
        Run in a puzzle channel that isn't solved yet to add a sheet to that
        puzzle; run anywhere else in the hunt to add one that belongs to the hunt
        itself — somewhere to pick at something found in the hunt's alt text
        before you know which puzzle it belongs to. List them with `!list_sheets`."""
        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)
        name = " ".join(args)

        if puzzle is None:
            if hunt is None:
                return await ctx.send(
                    ":x: This channel isn't part of a hunt, so there's nothing to "
                    "add a sheet to.")
            sheet = await self.service.add_hunt_sheet(hunt, name)
            return await ctx.send(
                f":white_check_mark: Added sheet {sheet.puzzle_name} to the hunt.")

        if puzzle.solved:
            return await ctx.send(":x: You can't add additional sheets to solved puzzles!")

        await self.service.add_sheet(hunt, puzzle, name)
        await self.info(ctx, update=True)
        return await ctx.send(f":white_check_mark: Added sheet {name} to puzzle.")
```

Replace `remove_sheet`:

```python
    @commands.command(aliases=['delete_sheet'])
    @with_puzzle_mutex(wait=True)
    @with_hunt_sheet_mutex()
    async def remove_sheet(self, ctx, number: int):
        """*Remove an additional sheet by its number, from this puzzle or the hunt.*

        Usage: `!remove_sheet number`
        Example: `!remove_sheet 2`
        Get the number from `!list_sheets`. Run in a puzzle channel to remove one
        of that puzzle's sheets, or anywhere else in the hunt to remove one of the
        hunt's own. Alias: `!delete_sheet`."""
        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)

        if puzzle is None:
            if hunt is None:
                return await ctx.send(
                    ":x: This channel isn't part of a hunt, so there are no sheets "
                    "to remove.")
            sheets = self.service.get_hunt_sheets(hunt)
            if number < 1 or number > len(sheets):
                return await ctx.send(
                    f":x: No hunt sheet #{number}. Use !list_sheets to see them.")
            name = sheets[number - 1].puzzle_name
            await self.service.remove_hunt_sheet_by_index(hunt, number)
            return await ctx.send(
                f":white_check_mark: Removed sheet {name} from the hunt.")

        if number < 1 or number > len(puzzle.additional_sheets):
            return await ctx.send(f":x: No additional sheet #{number}. Use !list_sheets to see them.")
        name = puzzle.additional_sheets[number - 1].puzzle_name
        await self.service.remove_sheet_by_index(hunt, puzzle, number)
        await self.info(ctx, update=True)
        return await ctx.send(f":white_check_mark: Removed sheet {name} from puzzle.")
```

Note `self.info(ctx, update=True)` is deliberately **not** called on the hunt path: `info`
refreshes a puzzle's pinned message, and there is no puzzle.

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheet_commands.py -q > /tmp/test.log 2>&1`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_hunt_sheet_commands.py
git commit -m "feat(bot): !add_sheet and !remove_sheet work on a hunt's own sheets"
```

---

## Task 9: `!list_sheets` lists the hunt's own sheets

**Files:**
- Modify: `bot/cogs/puzzles.py:1315-1336` (`list_additional_sheets`), `:1564-1574`
  (`create_additional_sheet_list`)
- Test: `tests/test_hunt_sheet_commands.py` (append)

`create_additional_sheet_list` asserts `puzzle is not None` and derives the spreadsheet id
from `puzzle.solved`. A hunt sheet is always on the hunt's main spreadsheet, so it needs a
sibling builder rather than a widened one — keeping the assert intact for the puzzle path.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hunt_sheet_commands.py`:

```python
class TestListSheetsCommand:
    async def test_hunt_channel_lists_the_hunt_sheets(self):
        from bot.cogs.puzzles import Puzzles
        hunt = make_hunt(id=10, google_sheet_id="SHEET1")
        cog = make_cog(puzzle=None, hunt=hunt)
        cog.environment = {}
        cog.service.get_hunt_sheets = MagicMock(return_value=[
            SimpleNamespace(id=7, puzzle_name="alt text", google_page_id="P7"),
            SimpleNamespace(id=2, puzzle_name="site source", google_page_id="P2"),
        ])
        ctx = make_ctx()
        await Puzzles.list_additional_sheets(cog, ctx)
        embed = ctx.send.await_args.kwargs["embed"]
        value = embed.fields[0].value
        # Two sheets, numbered in the order !remove_sheet will index them.
        assert "1: [alt text]" in value
        assert "2: [site source]" in value
        assert value.index("1: [alt text]") < value.index("2: [site source]")

    async def test_hunt_channel_with_no_sheets_says_so(self):
        from bot.cogs.puzzles import Puzzles
        cog = make_cog(puzzle=None, hunt=make_hunt(id=10))
        cog.environment = {}
        cog.service.get_hunt_sheets = MagicMock(return_value=[])
        ctx = make_ctx()
        await Puzzles.list_additional_sheets(cog, ctx)
        assert ":x:" in ctx.send.await_args[0][0]

    async def test_links_point_at_the_hunt_spreadsheet(self):
        from bot.cogs.puzzles import Puzzles
        cog = make_cog(puzzle=None, hunt=make_hunt(id=10, google_sheet_id="SHEET1"))
        cog.environment = {}
        cog.service.get_hunt_sheets = MagicMock(return_value=[
            SimpleNamespace(id=7, puzzle_name="alt text", google_page_id="P7"),
        ])
        ctx = make_ctx()
        await Puzzles.list_additional_sheets(cog, ctx)
        value = ctx.send.await_args.kwargs["embed"].fields[0].value
        assert "SHEET1" in value
        assert "P7" in value

    async def test_puzzle_channel_is_unchanged(self):
        from bot.cogs.puzzles import Puzzles
        puzzle = make_puzzle(id=5, hunt_id=10, name="Alpha")
        puzzle.additional_sheets = [
            SimpleNamespace(id=1, puzzle_name="grid 2", google_page_id="P1")]
        cog = make_cog(puzzle=puzzle, hunt=make_hunt(id=10))
        cog.environment = {}
        cog.service.get_hunt_sheets = MagicMock()
        ctx = make_ctx()
        await Puzzles.list_additional_sheets(cog, ctx)
        cog.service.get_hunt_sheets.assert_not_called()
        assert "grid 2" in ctx.send.await_args.kwargs["embed"].fields[0].value
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheet_commands.py -q -k ListSheets > /tmp/test.log 2>&1`
Expected: FAIL — the hunt-channel cases hit `send_not_puzzle_channel`, so no embed is sent
and `ctx.send.await_args.kwargs` has no `embed` key.

- [ ] **Step 3: Implement**

Add beside `create_additional_sheet_list` in `bot/cogs/puzzles.py`:

```python
    def create_hunt_sheet_list(self, hunt, sheets):
        """Numbered markdown links for a hunt's OWN working sheets.

        Sibling of create_additional_sheet_list rather than a widening of it: that
        one picks the spreadsheet from puzzle.solved, and a hunt sheet is never
        solved -- it always lives on the hunt's main spreadsheet.
        """
        sheet_links = ""
        for count, sheet in enumerate(sheets, start=1):
            spreadsheet_url = urls.spreadsheet_url(hunt.google_sheet_id,
                                                   sheet.google_page_id)
            sheet_links += f"{count}: [{sheet.puzzle_name}]({spreadsheet_url})\n"
        return sheet_links
```

Replace `list_additional_sheets`:

```python
    @commands.command(aliases=['list_sheets'])
    async def list_additional_sheets(self, ctx):
        """*List additional sheets with links and their numbers.*

        Usage: `!list_sheets`
        Example: `!list_sheets`
        Run in a puzzle channel for that puzzle's sheets, or anywhere else in the
        hunt for the hunt's own. Alias: `!list_additional_sheets`. Numbers feed
        `!remove_sheet`."""
        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)

        if puzzle is None:
            if hunt is None:
                return await ctx.send(
                    ":x: This channel isn't part of a hunt, so there are no sheets "
                    "to list.")
            sheets = self.service.get_hunt_sheets(hunt)
            if not sheets:
                return await ctx.send(':x: No hunt sheets found!')
            embed = discord.Embed()
            embed.add_field(
                name="Hunt Sheet Links",
                value=self.create_hunt_sheet_list(hunt, sheets),
                inline=False,
            )
            return await ctx.send(embed=embed)

        if len(puzzle.additional_sheets) == 0:
            return await ctx.send(':x: No additional sheets found!')

        embed = discord.Embed()

        embed.add_field(
            name="Additional Sheet Links",
            value=self.create_additional_sheet_list(ctx, puzzle),
            inline=False,
        )

        return await ctx.send(embed=embed)
```

`!list_sheets` takes no lock: it is a read, and reading a stale list is no worse than reading
one a moment before someone else's add.

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheet_commands.py -q > /tmp/test.log 2>&1`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_hunt_sheet_commands.py
git commit -m "feat(bot): !list_sheets lists a hunt's own working sheets"
```

---

## Task 10: The dashboard payload carries the hunt's sheets

**Files:**
- Modify: `bot/web/data.py` (beside `get_hunt_puzzles`), `bot/web/api.py:128-151`
- Test: `tests/test_hunt_sheets.py` (append)

See "One deliberate deviation from the spec's wording" above: this is a **new query**, not a
widened join.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hunt_sheets.py`:

```python
class TestWebHuntSheets:
    async def test_returns_the_hunts_own_sheets(self):
        from bot.web import data
        rows = [
            {"id": 7, "puzzle_name": "alt text", "google_page_id": "P7"},
            {"id": 2, "puzzle_name": "site source", "google_page_id": "P2"},
        ]
        with patch("bot.web.data.fetch_all", AsyncMock(return_value=rows)) as fa:
            out = await data.get_hunt_sheets(10)
        # Two rows, in query order: the frontend numbers them and !remove_sheet
        # indexes the same list, so a reordering here is a real bug.
        assert out == [
            {"id": 7, "name": "alt text", "page_id": "P7"},
            {"id": 2, "name": "site source", "page_id": "P2"},
        ]
        sql, params = fa.await_args[0]
        assert "puzzle_id IS NULL" in sql
        assert params == (10,)

    async def test_empty_when_the_hunt_has_none(self):
        from bot.web import data
        with patch("bot.web.data.fetch_all", AsyncMock(return_value=[])):
            assert await data.get_hunt_sheets(10) == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheets.py -q -k WebHuntSheets > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: module 'bot.web.data' has no attribute 'get_hunt_sheets'`.

- [ ] **Step 3: Implement**

In `bot/web/data.py`, immediately after `get_hunt_puzzles` (before `derive_meta_kind`):

```python
async def get_hunt_sheets(hunt_id):
    """The hunt's OWN working sheets -- those belonging to no puzzle.

    A separate query rather than a widening of the puzzle-sheet join above: that
    join's output is attached to a puzzle dict, and these rows have no puzzle.
    (Turning it into a LEFT JOIN would change nothing either -- its
    `WHERE puzzles.hunt_id = %s` discards the null side.)

    Ordered by id so the dashboard numbers them the same way !list_sheets does.
    """
    rows = await fetch_all(
        "SELECT id, puzzle_name, google_page_id FROM additional_sheets "
        "WHERE hunt_id = %s AND puzzle_id IS NULL ORDER BY id ASC",
        (hunt_id,),
    )
    return [
        {"id": r["id"], "name": r["puzzle_name"], "page_id": r["google_page_id"]}
        for r in rows
    ]
```

In `bot/web/api.py`, in the hunt-detail route: add the fetch beside the other loads
(after `all_groups = await data.get_all_tag_groups(hunt["id"])`):

```python
    hunt_sheets = await data.get_hunt_sheets(hunt["id"])
```

and add the key to the returned dict, beside `"board": board`:

```python
        "hunt_sheets": hunt_sheets,
```

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_hunt_sheets.py -q > /tmp/test.log 2>&1`
Expected: 22 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/web/data.py bot/web/api.py tests/test_hunt_sheets.py
git commit -m "feat(web): expose a hunt's own working sheets in the hunt payload"
```

---

## Task 11: `EvidenceData` and the DAO carry a source sheet

**Files:**
- Modify: `bot/store/evidence_data.py`, `bot/store/mysqldb.py` (`MySQLEvidenceDb.log`)
- Test: `tests/test_evidence_service.py` (append)

- [ ] **Step 1: Write the failing test**

Append to the `TestEvidenceData` class in `tests/test_evidence_service.py`:

```python
    def test_source_sheet_id_defaults_to_none(self):
        from bot.store.evidence_data import EvidenceData
        assert EvidenceData().source_sheet_id is None

    def test_import_dict_reads_the_source_sheet(self):
        from bot.store.evidence_data import EvidenceData
        e = EvidenceData.import_dict({
            "id": 1, "hunt_id": 10, "source_puzzle_id": None,
            "source_sheet_id": 4, "target_puzzle_id": 9,
            "note": "found in the alt text", "media_id": None,
            "created_at": None,
        })
        assert e.source_sheet_id == 4
        assert e.source_puzzle_id is None
```

and append a new class to the same file:

```python
class TestEvidenceDbLogSourceSheet:
    def _db(self):
        from bot.store.mysqldb import MySQLEvidenceDb
        mydb = MagicMock()
        cursor = MagicMock()
        cursor.lastrowid = 11
        mydb.dict_cursor.return_value = cursor
        return MySQLEvidenceDb(mydb=mydb), cursor

    def test_log_with_a_source_sheet(self):
        db, _ = self._db()
        e = db.log(hunt_id=10, source_puzzle_id=None, note="alt text",
                   source_sheet_id=4)
        assert e.source_sheet_id == 4
        assert e.source_puzzle_id is None

    def test_log_writes_the_source_sheet_in_column_order(self):
        """Every other assertion here reads fields off the RETURNED object, built
        from log()'s own parameters -- so a SQL tuple that transposed two adjacent
        int columns (media_id and source_sheet_id are both nullable ints) would
        corrupt every row and stay green. Assert on what reached execute."""
        db, cursor = self._db()
        db.log(hunt_id=10, source_puzzle_id=None, note="n", media_id=7,
               source_sheet_id=4)
        sql, params = cursor.execute.call_args[0]
        columns = [c.strip() for c in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
        assert dict(zip(columns, params))["media_id"] == 7
        assert dict(zip(columns, params))["source_sheet_id"] == 4

    def test_source_sheet_defaults_to_none(self):
        db, cursor = self._db()
        db.log(hunt_id=10, source_puzzle_id=5, note="n")
        sql, params = cursor.execute.call_args[0]
        columns = [c.strip() for c in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
        assert dict(zip(columns, params))["source_sheet_id"] is None
```

`MagicMock` is already imported at the top of `tests/test_evidence_service.py` (Plan A's
`TestEvidenceDbLog` uses it).

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_service.py -q -k "EvidenceData or SourceSheet" > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: 'EvidenceData' object has no attribute 'source_sheet_id'`.

- [ ] **Step 3: Implement**

In `bot/store/evidence_data.py`, add one field after `source_puzzle_id`:

```python
    # None unless the evidence came from a hunt-level working sheet. At most one
    # of source_puzzle_id / source_sheet_id is set; the service methods that write
    # each kind own that invariant.
    source_sheet_id: Optional[int] = None
```

In `bot/store/mysqldb.py`, widen `MySQLEvidenceDb.log`:

```python
    def log(self, hunt_id: int, source_puzzle_id, note: str,
            media_id=None, source_sheet_id=None) -> 'EvidenceData':
        """Insert one evidence item and return it.

        source_puzzle_id may be None: evidence can belong to the hunt rather
        than to any one puzzle. media_id may be None: evidence can be text only.
        source_sheet_id names a hunt-level working sheet as the provenance.
        """
        now = datetime.datetime.utcnow()
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                "INSERT INTO evidence_items"
                " (hunt_id, source_puzzle_id, note, media_id, source_sheet_id, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (hunt_id, source_puzzle_id, note, media_id, source_sheet_id, now),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        self.mydb.commit()
        e = EvidenceData()
        e.id = new_id or 0
        e.hunt_id = hunt_id
        e.source_puzzle_id = source_puzzle_id
        e.note = note
        e.media_id = media_id
        e.source_sheet_id = source_sheet_id
        # Match import_dict's tzinfo-reattachment so a freshly-logged item and one
        # read back from the DB serialize identically (#33).
        e.created_at = now.replace(tzinfo=datetime.timezone.utc)
        return e
```

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: all pass. The whole suite, not just this file: `EvidenceData` gained a field, and
the generic `commit()` writes every field by name.

- [ ] **Step 5: Commit**

```bash
git add bot/store/evidence_data.py bot/store/mysqldb.py tests/test_evidence_service.py
git commit -m "feat(store): evidence can name a hunt-level sheet as its source"
```

---

## Task 12: `log_clues` accepts a source sheet

**Files:**
- Modify: `bot/core/puzzle_service.py:2772-2793` (`log_clues`)
- Test: `tests/test_evidence_service.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_evidence_service.py`:

```python
class TestLogCluesSourceSheet:
    def _svc(self):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService.__new__(PuzzleService)
        svc.notifier = MagicMock(board_changed=AsyncMock())
        return svc

    async def test_passes_the_source_sheet_through(self):
        from unittest.mock import patch
        from tests.conftest import make_hunt
        svc = self._svc()
        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb:
            edb.log.return_value = SimpleNamespace(id=1, note="n")
            await svc.log_clues(make_hunt(id=10), None, ["n"], source_sheet_id=4)
        assert edb.log.call_args.kwargs["source_sheet_id"] == 4

    async def test_a_sheet_source_creates_no_informs_edge(self):
        """An informs edge joins two puzzles, and a working sheet is not one. The
        auto-routing derived from a source puzzle's sole informs target must not
        fire for a sheet-sourced item."""
        from unittest.mock import patch
        from tests.conftest import make_hunt
        svc = self._svc()
        svc._sole_informs_target = MagicMock(return_value=99)
        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb:
            edb.log.return_value = SimpleNamespace(id=1, note="n")
            await svc.log_clues(make_hunt(id=10), None, ["n"], source_sheet_id=4)
        svc._sole_informs_target.assert_not_called()
        edb.set_target.assert_not_called()

    async def test_default_is_no_source_sheet(self):
        from unittest.mock import patch
        from tests.conftest import make_hunt, make_puzzle
        svc = self._svc()
        svc._sole_informs_target = MagicMock(return_value=None)
        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb:
            edb.log.return_value = SimpleNamespace(id=1, note="n")
            await svc.log_clues(make_hunt(id=10), make_puzzle(id=5, hunt_id=10), ["n"])
        assert edb.log.call_args.kwargs["source_sheet_id"] is None
```

`SimpleNamespace` is imported at the top of `tests/test_evidence_service.py`; if it is not,
add `from types import SimpleNamespace`.

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_service.py -q -k LogCluesSourceSheet > /tmp/test.log 2>&1`
Expected: FAIL — `TypeError: log_clues() got an unexpected keyword argument 'source_sheet_id'`.

- [ ] **Step 3: Implement**

Replace `log_clues` in `bot/core/puzzle_service.py`. **Keep the `@pushes_board()` line above
it** — `log_clues` is in `EXPECTED_PUSH_METHODS`, and dropping the decorator while replacing
the method is the easy mistake here:

```python
    @pushes_board()
    async def log_clues(self, hunt, puzzle, notes: list, media_id=None,
                        source_sheet_id=None) -> list:
        """Insert one evidence_item per note. Returns list of EvidenceData.

        `puzzle` may be None: evidence can belong to the hunt rather than to any
        one puzzle (e.g. something found in the hunt's alt text). With no source
        there is no informs edge to follow, so such items are created untargeted
        for manual routing via the Evidence panel.

        `source_sheet_id` names a hunt-level working sheet as the provenance. Like
        a sourceless item it creates no informs edge -- an edge joins two puzzles,
        and a working sheet is not one.

        If the source puzzle informs exactly one target, each clue is auto-routed
        to that informed target (target_puzzle_id) so it surfaces in the target's
        roll-up contribution table. Zero or several informs targets -> left
        untargeted for manual routing via the Evidence panel.
        """
        target_id = self._sole_informs_target(puzzle) if puzzle is not None else None
        items = []
        for note in notes:
            item = EvidenceJsonDb.log(hunt.id, puzzle.id if puzzle else None, note,
                                      media_id=media_id,
                                      source_sheet_id=source_sheet_id)
            if target_id is not None:
                item = EvidenceJsonDb.set_target(item.id, target_id) or item
            items.append(item)
        return items
```

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_service.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_evidence_service.py
git commit -m "feat(core): log_clues accepts a hunt-level sheet as the source"
```

---

## Task 13: `evidence_source_label` names the sheet

**Files:**
- Modify: `bot/core/puzzle_service.py:120-127` (`evidence_source_label`), `_render_rollup`
- Test: `tests/test_contribution_table_requests.py` (append)

Plan A left `evidence_source_label(item)` returning `""` as the seam for exactly this. It
gains a resolved-names map rather than doing its own lookup: `_render_rollup` calls it once
per item, and a per-item query would be an N+1.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_contribution_table_requests.py`:

```python
class TestEvidenceSourceLabelSheet:
    def test_puzzle_sourced_items_still_get_an_empty_label(self):
        """contribution_rows uses the member puzzle's own name for those."""
        from types import SimpleNamespace
        from bot.core.puzzle_service import evidence_source_label
        item = SimpleNamespace(source_puzzle_id=5, source_sheet_id=None)
        assert evidence_source_label(item, {}) == ""

    def test_sheet_sourced_items_get_the_sheet_name(self):
        from types import SimpleNamespace
        from bot.core.puzzle_service import evidence_source_label
        item = SimpleNamespace(source_puzzle_id=None, source_sheet_id=4)
        assert evidence_source_label(item, {4: "alt text"}) == "alt text"

    def test_picks_the_right_name_out_of_several(self):
        """One entry in the map cannot be the wrong entry."""
        from types import SimpleNamespace
        from bot.core.puzzle_service import evidence_source_label
        names = {4: "alt text", 9: "site source"}
        a = SimpleNamespace(source_puzzle_id=None, source_sheet_id=9)
        assert evidence_source_label(a, names) == "site source"

    def test_a_deleted_sheet_falls_back_rather_than_blanking(self):
        """source_sheet_id is ON DELETE SET NULL, but a row read between the
        delete and the null would otherwise render an empty Source cell."""
        from types import SimpleNamespace
        from bot.core.puzzle_service import evidence_source_label
        item = SimpleNamespace(source_puzzle_id=None, source_sheet_id=4)
        assert evidence_source_label(item, {}) == "Sheet 4"

    def test_sourceless_items_get_an_empty_label(self):
        from types import SimpleNamespace
        from bot.core.puzzle_service import evidence_source_label
        item = SimpleNamespace(source_puzzle_id=None, source_sheet_id=None)
        assert evidence_source_label(item, {}) == ""


class TestRollupSheetSource:
    async def test_rollup_passes_the_sheet_name_as_the_source_label(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        from bot.core.puzzle_service import PuzzleService
        from tests.conftest import make_puzzle, make_round

        target = make_puzzle(id=4, name="Capstone", hunt_id=10)
        group = make_round(id=1, target_puzzle_id=4)
        rollup = SimpleNamespace(hunt_id=10, source_mode="round",
                                 edge_type_filter=None)
        item = SimpleNamespace(id=9, source_puzzle_id=None, source_sheet_id=4,
                               note="three anchors", media_id=None)

        gsheet = MagicMock()
        gsheet.render_contribution = AsyncMock()
        svc = PuzzleService.__new__(PuzzleService)
        svc.notifier = MagicMock(board_changed=AsyncMock())
        svc.select_rollup_members = AsyncMock(return_value=[])

        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            edb.get_all.return_value = [item]
            sdb.get_names_by_ids.return_value = {4: "alt text"}
            await svc._render_rollup(gsheet, group, rollup, target)

        evidence = gsheet.render_contribution.await_args.kwargs["evidence"]
        assert evidence[0]["source_label"] == "alt text"

    async def test_the_lookup_is_batched_not_per_item(self):
        """A per-item query would be an N+1 across a rendered table. Two items so
        a single call cannot be mistaken for one-call-per-item."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        from bot.core.puzzle_service import PuzzleService
        from tests.conftest import make_puzzle, make_round

        target = make_puzzle(id=4, name="Capstone", hunt_id=10)
        group = make_round(id=1, target_puzzle_id=4)
        rollup = SimpleNamespace(hunt_id=10, source_mode="round",
                                 edge_type_filter=None)
        items = [
            SimpleNamespace(id=9, source_puzzle_id=None, source_sheet_id=4,
                            note="a", media_id=None),
            SimpleNamespace(id=8, source_puzzle_id=None, source_sheet_id=5,
                            note="b", media_id=None),
        ]

        gsheet = MagicMock()
        gsheet.render_contribution = AsyncMock()
        svc = PuzzleService.__new__(PuzzleService)
        svc.notifier = MagicMock(board_changed=AsyncMock())
        svc.select_rollup_members = AsyncMock(return_value=[])

        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            edb.get_all.return_value = items
            sdb.get_names_by_ids.return_value = {4: "alt text", 5: "site source"}
            await svc._render_rollup(gsheet, group, rollup, target)

        sdb.get_names_by_ids.assert_called_once()
        evidence = gsheet.render_contribution.await_args.kwargs["evidence"]
        assert [e["source_label"] for e in evidence] == ["alt text", "site source"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_contribution_table_requests.py -q -k "SourceLabelSheet or RollupSheetSource" > /tmp/test.log 2>&1`
Expected: FAIL — `TypeError: evidence_source_label() takes 1 positional argument but 2 were given`.

- [ ] **Step 3: Implement**

The lookup needs a batched resolver. Add a DAO method to `MySQLAdditionalSheetsDb` in
`bot/store/mysqldb.py`, beneath `get_by_hunt`:

```python
    def get_names_by_ids(self, sheet_ids):
        """Return a {sheet_id: puzzle_name} map for the given ids, in one query.

        Mirrors MySQLPuzzleJsonDb.get_names_by_ids -- rendering a table calls this
        once for the whole batch rather than once per row.
        """
        ids = [int(i) for i in sheet_ids if i]
        if not ids:
            return {}
        placeholders = ", ".join(["%s"] * len(ids))
        cursor = self.mydb.dict_cursor()
        try:
            cursor.execute(
                f"SELECT id, puzzle_name FROM additional_sheets WHERE id IN ({placeholders})",
                tuple(ids),
            )
            rows = cursor.fetchall() or []
        finally:
            cursor.close()
        return {r["id"]: r["puzzle_name"] for r in rows}
```

Replace `evidence_source_label` in `bot/core/puzzle_service.py`:

```python
def evidence_source_label(item, sheet_names) -> str:
    """Display name for an evidence item's source in a rendered table.

    Empty for puzzle-sourced items — contribution_rows uses the member puzzle's
    own name for those — and empty for items with no source at all, which fall
    back to an em dash there. A hunt-level working sheet renders as its name.

    `sheet_names` is a {sheet_id: name} map resolved once for the whole batch;
    looking each one up here would be an N+1 across a rendered table. Plan B
    fills this in for canvas regions.
    """
    sheet_id = getattr(item, "source_sheet_id", None)
    if not sheet_id:
        return ""
    # ON DELETE SET NULL means a missing sheet should be impossible, but a row
    # read in the gap would otherwise render a blank Source cell.
    return sheet_names.get(sheet_id) or f"Sheet {sheet_id}"


def resolve_sheet_names(sheet_ids):
    """Return a {sheet_id: name} map for hunt-level working sheets, in one query."""
    ids = {i for i in sheet_ids if i}
    if not ids:
        return {}
    return SheetsJsonDb.get_names_by_ids(ids)
```

In `_render_rollup`, resolve the names before the comprehension and pass them in:

```python
        raw_evidence = list(
            EvidenceJsonDb.get_all(rollup.hunt_id, target_puzzle_id=metapuzzle.id))
        sheet_names = resolve_sheet_names(
            getattr(e, "source_sheet_id", None) for e in raw_evidence)
        evidence = [
            {
                "id": e.id,
                "source_puzzle_id": e.source_puzzle_id,
                "source_label": evidence_source_label(e, sheet_names),
                "content": evidence_cell_content(e),
            }
            for e in raw_evidence
        ]
```

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_contribution_table_requests.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Run the whole suite**

Run: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: all pass. `evidence_source_label` changed arity, so any other caller would break
here. `grep -rn "evidence_source_label" bot/ tests/` should show only `_render_rollup` and
these tests.

- [ ] **Step 6: Commit**

```bash
git add bot/store/mysqldb.py bot/core/puzzle_service.py tests/test_contribution_table_requests.py
git commit -m "feat(core): render a hunt-level sheet as an evidence source label"
```

---

## Task 14: The Evidence sheet tab shows the sheet as the source

**Files:**
- Modify: `bot/core/puzzle_service.py:2825-2845` (`refresh_evidence`)
- Test: `tests/test_evidence_service.py` (append)

`refresh_evidence` renders the hunt-wide Evidence tab. Plan A made a null source render as an
em dash; a sheet-sourced item should render the sheet's name instead.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_evidence_service.py`:

```python
class TestRefreshEvidenceSheetSource:
    def _svc(self, gsheet):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService.__new__(PuzzleService)
        svc.notifier = MagicMock(board_changed=AsyncMock())
        svc._gsheet_cog = MagicMock(return_value=gsheet)
        return svc

    async def test_source_column_shows_the_sheet_name(self):
        from unittest.mock import patch
        from tests.conftest import make_hunt
        gsheet = MagicMock()
        gsheet.reconcile_evidence_table = AsyncMock()
        svc = self._svc(gsheet)
        hunt = make_hunt(id=10, evidence_sheet_page_id=1, evidence_table_id=2)
        item = SimpleNamespace(id=9, source_puzzle_id=None, source_sheet_id=4,
                               target_puzzle_id=None, note="n", created_at=None)
        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.SheetsJsonDb") as sdb, \
             patch("bot.core.puzzle_service.resolve_puzzle_names", return_value={}):
            edb.get_all.return_value = [item]
            sdb.get_names_by_ids.return_value = {4: "alt text"}
            await svc.refresh_evidence(hunt)
        rows = gsheet.reconcile_evidence_table.await_args[0][-1]
        assert rows[0][1] == "alt text"

    async def test_a_sourceless_item_still_renders_an_em_dash(self):
        """Plan A's behaviour must survive: no source at all is not a sheet."""
        from unittest.mock import patch
        from tests.conftest import make_hunt
        gsheet = MagicMock()
        gsheet.reconcile_evidence_table = AsyncMock()
        svc = self._svc(gsheet)
        hunt = make_hunt(id=10, evidence_sheet_page_id=1, evidence_table_id=2)
        item = SimpleNamespace(id=9, source_puzzle_id=None, source_sheet_id=None,
                               target_puzzle_id=None, note="n", created_at=None)
        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.SheetsJsonDb") as sdb, \
             patch("bot.core.puzzle_service.resolve_puzzle_names", return_value={}):
            edb.get_all.return_value = [item]
            sdb.get_names_by_ids.return_value = {}
            await svc.refresh_evidence(hunt)
        rows = gsheet.reconcile_evidence_table.await_args[0][-1]
        assert rows[0][1] == "—"
```

**Before running:** open `bot/core/puzzle_service.py` at `refresh_evidence` and check what
the rows are actually handed to — the method name and argument position used above
(`gsheet.reconcile_evidence_table(..., rows)`) must match the real call. If it differs,
fix the two assertions to read the rows from the real call site. Do not change the
implementation to fit the test.

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_service.py -q -k RefreshEvidenceSheetSource > /tmp/test.log 2>&1`
Expected: FAIL — the source cell is `"—"` where `"alt text"` is expected.

- [ ] **Step 3: Implement**

In `refresh_evidence`, resolve the sheet names alongside the puzzle names and branch:

```python
        items = EvidenceJsonDb.get_all(hunt.id)
        puzzle_ids = ({i.source_puzzle_id for i in items if i.source_puzzle_id}
                      | {i.target_puzzle_id for i in items if i.target_puzzle_id})
        names = resolve_puzzle_names(puzzle_ids)
        sheet_names = resolve_sheet_names(
            getattr(i, "source_sheet_id", None) for i in items)
        rows = [
            [
                i.id,
                # A null source is evidence belonging to the hunt rather than to
                # a puzzle; the old fallback rendered it as "Puzzle None". A
                # hunt-level working sheet names itself.
                (names.get(i.source_puzzle_id, f"Puzzle {i.source_puzzle_id}")
                 if i.source_puzzle_id
                 else (evidence_source_label(i, sheet_names) or "—")),
```

Leave the remaining cells of the row untouched.

- [ ] **Step 4: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_service.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_evidence_service.py
git commit -m "feat(core): the Evidence tab names a hunt-level sheet as the source"
```

---

## Task 15: Web serialisation and `POST /api/evidence`

**Files:**
- Modify: `bot/web/data.py:558-584` (`get_evidence`), `bot/web/api.py:353-409` (`add_evidence`)
- Test: `tests/test_add_evidence_web.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_add_evidence_web.py`:

```python
class TestEvidenceSheetSourceSerialisation:
    def test_serialises_the_sheet_source(self):
        from bot.web import data
        items = [SimpleNamespace(id=1, source_puzzle_id=None, source_sheet_id=4,
                                 target_puzzle_id=None, note="n", media_id=None,
                                 created_at=None)]
        edb = MagicMock()
        edb.get_all.return_value = items
        with patch("bot.store.EvidenceJsonDb", edb), \
             patch("bot.core.puzzle_service.resolve_puzzle_names", return_value={}), \
             patch("bot.core.puzzle_service.resolve_sheet_names",
                   return_value={4: "alt text"}):
            out = data.get_evidence(10)
        assert out[0]["source_sheet_id"] == 4
        assert out[0]["source_sheet_name"] == "alt text"

    def test_a_puzzle_sourced_item_has_no_sheet_source(self):
        from bot.web import data
        items = [SimpleNamespace(id=1, source_puzzle_id=5, source_sheet_id=None,
                                 target_puzzle_id=None, note="n", media_id=None,
                                 created_at=None)]
        edb = MagicMock()
        edb.get_all.return_value = items
        with patch("bot.store.EvidenceJsonDb", edb), \
             patch("bot.core.puzzle_service.resolve_puzzle_names",
                   return_value={5: "Alpha"}), \
             patch("bot.core.puzzle_service.resolve_sheet_names", return_value={}):
            out = data.get_evidence(10)
        assert out[0]["source_sheet_id"] is None
        assert out[0]["source_sheet_name"] is None
        assert out[0]["source_puzzle_name"] == "Alpha"


@pytest.mark.asyncio
class TestAddEvidenceSheetSource:
    async def test_accepts_a_source_sheet(self, web):
        sdb = MagicMock()
        sdb.get_by_hunt.return_value = [SimpleNamespace(id=4, puzzle_name="alt text")]
        with patch("bot.store.SheetsJsonDb", sdb):
            res = await web.client.post("/api/evidence", json={
                "hunt_uid": "U1", "notes": ["found in the alt text"],
                "source_sheet_id": 4,
            })
        assert res.status_code == 201
        assert web.cog.service.log_clues.await_args.kwargs["source_sheet_id"] == 4

    async def test_rejects_a_sheet_from_another_hunt(self, web):
        """The sheet id comes from the client. Without this check anyone could
        attribute their evidence to another team's sheet."""
        sdb = MagicMock()
        sdb.get_by_hunt.return_value = []
        with patch("bot.store.SheetsJsonDb", sdb):
            res = await web.client.post("/api/evidence", json={
                "hunt_uid": "U1", "notes": ["x"], "source_sheet_id": 999,
            })
        assert res.status_code == 404
        web.cog.service.log_clues.assert_not_awaited()

    async def test_a_puzzle_source_and_a_sheet_source_are_mutually_exclusive(self, web):
        """At most one source. Sending both is a client bug; silently preferring
        one would attribute the evidence to something nobody picked."""
        res = await web.client.post("/api/evidence", json={
            "hunt_uid": "U1", "notes": ["x"],
            "source_puzzle_id": 9, "source_sheet_id": 4,
        })
        assert res.status_code == 400
        web.cog.service.log_clues.assert_not_awaited()

    async def test_a_puzzle_source_alone_still_works(self, web):
        """The mutual-exclusion guard must not break the common case."""
        res = await web.client.post("/api/evidence", json={
            "hunt_uid": "U1", "notes": ["x"], "source_puzzle_id": 9,
        })
        assert res.status_code == 201
        assert web.cog.service.log_clues.await_args.kwargs["source_sheet_id"] is None
```

The `web` fixture's `log_clues` stub takes `media_id` only; widen it in the fixture at
`tests/test_add_evidence_web.py:28-32`:

```python
    cog.service.log_clues = AsyncMock(
        side_effect=lambda hunt, puzzle, notes, media_id=None, source_sheet_id=None: [
            SimpleNamespace(id=1, note=n) for n in notes
        ]
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_add_evidence_web.py -q -k SheetSource > /tmp/test.log 2>&1`
Expected: FAIL — `KeyError: 'source_sheet_id'` on the serialisation tests, and the route
ignores the field so `log_clues` is called without it.

- [ ] **Step 3: Implement the serialisation**

Replace `get_evidence` in `bot/web/data.py`:

```python
def get_evidence(hunt_id, source_puzzle_id=None, target_puzzle_id=None):
    """Return evidence items for a hunt with resolved source and target names.

    source_puzzle_id/source_puzzle_name are None for evidence that belongs to
    the hunt rather than to any one puzzle; source_sheet_id/source_sheet_name
    name a hunt-level working sheet instead. media_url is relative so it works
    on whichever origin is serving the SPA.
    """
    from bot.store import EvidenceJsonDb
    from bot.core.puzzle_service import resolve_puzzle_names, resolve_sheet_names
    items = EvidenceJsonDb.get_all(hunt_id, source_puzzle_id=source_puzzle_id, target_puzzle_id=target_puzzle_id)
    puzzle_ids = ({i.source_puzzle_id for i in items if i.source_puzzle_id}
                  | {i.target_puzzle_id for i in items if i.target_puzzle_id})
    names = resolve_puzzle_names(puzzle_ids)
    sheet_names = resolve_sheet_names(
        getattr(i, "source_sheet_id", None) for i in items)
    return [
        {
            "id": i.id,
            "source_puzzle_id": i.source_puzzle_id,
            "source_puzzle_name": names.get(i.source_puzzle_id, "") if i.source_puzzle_id else None,
            "source_sheet_id": i.source_sheet_id,
            "source_sheet_name": (sheet_names.get(i.source_sheet_id) or f"Sheet {i.source_sheet_id}")
                                 if i.source_sheet_id else None,
            "target_puzzle_id": i.target_puzzle_id,
            "target_puzzle_name": names.get(i.target_puzzle_id, "") if i.target_puzzle_id else None,
            "note": i.note,
            "media_id": i.media_id,
            "media_url": f"/api/media/{i.media_id}" if i.media_id else None,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in items
    ]
```

- [ ] **Step 4: Implement the route**

In `bot/web/api.py`'s `add_evidence`, after `media_id = body.get("media_id")`:

```python
    source_sheet_id = body.get("source_sheet_id")
```

and after the `hunt["archived"]` guard, replacing the existing source-puzzle block:

```python
    # At most one source. Both set is a client bug -- silently preferring one
    # would attribute the evidence to something the user did not pick.
    if source_puzzle_id and source_sheet_id:
        return jsonify({"error": "only one source may be given"}), 400
    # source_puzzle_id is optional: evidence can belong to the hunt rather
    # than to any one puzzle.
    puzzle = None
    if source_puzzle_id:
        puzzle = PuzzleJsonDb.get_by_attr(id=source_puzzle_id)
        if puzzle is None or puzzle.hunt_id != hunt["id"]:
            return jsonify({"error": "puzzle not found in hunt"}), 404
    if source_sheet_id:
        # The id comes from the client; confirm it is one of THIS hunt's sheets
        # before attributing anything to it.
        from bot.store import SheetsJsonDb
        if not any(s.id == int(source_sheet_id)
                   for s in SheetsJsonDb.get_by_hunt(hunt["id"])):
            return jsonify({"error": "sheet not found in hunt"}), 404
```

and widen the `log_clues` call:

```python
    items = await cog.service.log_clues(hunt_obj, puzzle, clean_notes or [""],
                                        media_id=media_id,
                                        source_sheet_id=source_sheet_id)
```

- [ ] **Step 5: Run it to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_add_evidence_web.py tests/test_evidence_web.py -q > /tmp/test.log 2>&1`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add bot/web/data.py bot/web/api.py tests/test_add_evidence_web.py
git commit -m "feat(web): evidence can be logged against, and shows, a hunt-level sheet"
```

---

## Task 16: Frontend types and hooks

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/api.ts`

There is no JS test runner in this repo. `tsc` is the only gate for this task and the two
after it — it verifies shapes, not behaviour, so Task 19's manual check is what actually
exercises them. Say so when reporting.

- [ ] **Step 1: Add the types**

In `frontend/src/types.ts`, add beside `AdditionalSheet`:

```ts
export interface HuntSheet {
  id: number;
  name: string;
  page_id: string | null;
}
```

Add to `HuntDetail`, beside `summary`:

```ts
  hunt_sheets: HuntSheet[];
```

Add to `EvidenceItem`, after `source_puzzle_name`:

```ts
  source_sheet_id: number | null;
  source_sheet_name: string | null;
```

- [ ] **Step 2: Widen the add-evidence hook**

In `frontend/src/api.ts`, replace `useAddEvidence`:

```ts
export function useAddEvidence(huntUid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId, sheetId, note, mediaId }:
      { puzzleId: number | null; sheetId: number | null; note: string; mediaId: number | null }) =>
      apiSend<{ id: number; note: string }[]>("/api/evidence", "POST", {
        hunt_uid: huntUid,
        source_puzzle_id: puzzleId,
        source_sheet_id: sheetId,
        notes: note ? [note] : [],
        media_id: mediaId,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hunt", huntUid] });
      qc.invalidateQueries({ queryKey: ["evidence", huntUid] });
    },
  });
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && timeout 180 npx tsc --noEmit > /tmp/tsc.log 2>&1`
Expected: FAIL — `EvidencePanel.tsx` calls `addEvidence.mutateAsync` without `sheetId`.
That is the next task; read `/tmp/tsc.log` and confirm the only errors are in
`EvidencePanel.tsx`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat(web): frontend types and hook for hunt-level sheets as evidence sources"
```

This commit leaves the frontend not typechecking; Task 17 closes it. That is deliberate —
splitting them keeps each diff reviewable — but do not stop here.

---

## Task 17: The Evidence panel picks and shows a sheet source

**Files:**
- Modify: `frontend/src/components/EvidencePanel.tsx`

The panel's source state currently holds a puzzle id (`cluePuzzleId`, and `filterSource`
holding `""` / `"none"` / `"<puzzle id>"`). A source can now be a puzzle *or* a sheet, so the
values become namespaced (`p:<id>` / `s:<id>`) and `cluePuzzleId` is renamed `clueSource` —
the old name would be false. Both are component-local state; nothing outside this file reads
them.

- [ ] **Step 1: Add a source-label helper**

At the top of the component file, below the imports:

```tsx
/** The one thing a row's Source column shows: a puzzle, a hunt sheet, or nothing. */
function sourceLabel(item: EvidenceItem): string {
  return item.source_puzzle_name ?? item.source_sheet_name ?? "—";
}
```

`EvidenceItem` is already imported in this file; if it is not, add it to the existing
`import type { ... } from "../types"`.

- [ ] **Step 2: Take the hunt's sheets from the detail**

The component already receives `detail`. **Replace** the existing `allPuzzles` memo with:

```tsx
  const sourceChoices = useMemo(() => {
    const puzzles = new Map<number, string>();
    detail.board.forEach((g) => g.puzzles.forEach((p) => puzzles.set(p.id, p.name)));
    return [
      ...Array.from(puzzles.entries()).map(([id, name]) => ({
        value: `p:${id}`, label: name,
      })),
      ...detail.hunt_sheets.map((s) => ({
        value: `s:${s.id}`, label: `${s.name} (hunt sheet)`,
      })),
    ];
  }, [detail]);
```

Two things this preserves from `allPuzzles`, both easy to lose: the `Map` de-duplicates a
puzzle that appears in several groups, and `detail` (not `items`) is the source — the picker
must offer every puzzle, not only ones that already have evidence.

The `(hunt sheet)` suffix is deliberate: a sheet and a puzzle can share a name, and the
picker is the only place the difference is visible before the item is logged.

- [ ] **Step 3: Rename the state and submit both source kinds**

Replace `const [cluePuzzleId, setCluePuzzleId] = useState<string | null>(null);` with:

```tsx
  // "p:<puzzle id>" | "s:<hunt sheet id>" | null. Namespaced because a source is
  // now one of two kinds and a bare id could mean either.
  const [clueSource, setClueSource] = useState<string | null>(null);
```

Replace the `addEvidence.mutateAsync` call inside `submitClue`:

```tsx
      await addEvidence.mutateAsync({
        puzzleId: clueSource?.startsWith("p:") ? Number(clueSource.slice(2)) : null,
        sheetId: clueSource?.startsWith("s:") ? Number(clueSource.slice(2)) : null,
        note,
        mediaId: media ? media.id : null,
      });
```

Replace the log-form `Select` (the one with `placeholder="Source puzzle …"`):

```tsx
          <Select
            size="xs"
            placeholder="Source …"
            data={sourceChoices}
            value={clueSource ?? ""}
            onChange={(v) => setClueSource(v || null)}
            searchable
            clearable
            w={240}
          />
```

- [ ] **Step 4: Namespace the filter**

Replace `sourceOptions`:

```tsx
  const sourceOptions = useMemo(() => {
    const puzzles = new Map<number, string>();
    const sheets = new Map<number, string>();
    let hasSourceless = false;
    items.forEach((i) => {
      if (i.source_puzzle_id != null) puzzles.set(i.source_puzzle_id, i.source_puzzle_name ?? "");
      else if (i.source_sheet_id != null) sheets.set(i.source_sheet_id, i.source_sheet_name ?? "");
      else hasSourceless = true;
    });
    return [
      { value: "", label: "All sources" },
      // The filter is how someone would go looking for hunt-level evidence, so
      // give it an explicit label rather than an empty one.
      ...(hasSourceless ? [{ value: "none", label: "No source" }] : []),
      ...Array.from(puzzles.entries()).map(([id, name]) => ({ value: `p:${id}`, label: name })),
      ...Array.from(sheets.entries()).map(([id, name]) => ({
        value: `s:${id}`, label: `${name} (hunt sheet)`,
      })),
    ];
  }, [items]);
```

and the source branch of `filtered`:

```tsx
      .filter((i) => {
        if (!filterSource) return true;
        if (filterSource === "none") return i.source_puzzle_id == null && i.source_sheet_id == null;
        if (filterSource.startsWith("p:")) return String(i.source_puzzle_id) === filterSource.slice(2);
        return String(i.source_sheet_id) === filterSource.slice(2);
      })
```

- [ ] **Step 5: Show the sheet name in the table, and sort on it**

Replace the source cell (`<Table.Td>{item.source_puzzle_name ?? "—"}</Table.Td>`):

```tsx
      <Table.Td>{sourceLabel(item)}</Table.Td>
```

The `source_puzzle_name` sort key now sorts a column that can hold a sheet name, so it must
compare the same string the cell shows. In the `sorted` memo, replace the value reads:

```tsx
      const av = key === "source_puzzle_name" ? sourceLabel(a) : (a[key] ?? "");
      const bv = key === "source_puzzle_name" ? sourceLabel(b) : (b[key] ?? "");
```

Rename the column header text from `Source Puzzle` to `Source` in the same `SortableTh`
(the `sortKey` stays `source_puzzle_name` — it is the API field the other branch still
reads, and renaming it would ripple into `SortKey` for no gain).

- [ ] **Step 6: Typecheck**

Run: `cd frontend && timeout 180 npx tsc --noEmit > /tmp/tsc.log 2>&1`
Expected: clean, exit 0. Read `/tmp/tsc.log` if not.

- [ ] **Step 7: Build**

Run: `cd frontend && timeout 300 npm run build > /tmp/build.log 2>&1`
Expected: exit 0.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/EvidencePanel.tsx
git commit -m "feat(web): pick and show a hunt-level sheet as an evidence source"
```

---

## Task 18: The board header lists the hunt's sheets

**Files:**
- Modify: `frontend/src/components/HuntInfoPanel.tsx`, `frontend/src/pages/HuntBoard.tsx`

- [ ] **Step 1: Add the prop and render it**

In `frontend/src/components/HuntInfoPanel.tsx`, add to the imports:

```tsx
import type { HuntSheet } from "../types";
```

Add to `HuntInfoPanelProps` and the destructured parameter list:

```tsx
  hunt_sheets: HuntSheet[];
```

Render after the `solved_sheet_base_url` block, before `username`:

```tsx
      {hunt_sheets.length > 0 && sheet_base_url && (
        <Group gap={6} align="flex-start">
          <IconExternalLink size={14} />
          <Text size="sm">
            Hunt working sheets:{" "}
            {hunt_sheets.map((s, i) => (
              <span key={s.id}>
                {i > 0 && ", "}
                <Anchor href={`${sheet_base_url}${s.page_id ?? ""}`} target="_blank" inherit>
                  {s.name}
                </Anchor>
              </span>
            ))}
          </Text>
        </Group>
      )}
```

`sheet_base_url` gates the block because a link needs it — `PuzzleLinks.tsx` builds puzzle
sheet URLs the same way (`${base}${page_id}`).

Add `hunt_sheets.length > 0` to the "nothing configured" fallback condition so a hunt with
only sheets does not claim to have nothing:

```tsx
      {!url && !startTimeStr && !sheet_base_url && !solved_sheet_base_url && !username && !password
        && hunt_sheets.length === 0
        && <Text size="sm" c="dimmed">No hunt settings configured yet.</Text>
      }
```

- [ ] **Step 2: Pass it through**

In `frontend/src/pages/HuntBoard.tsx`, add to the `<HuntInfoPanel ... />` props:

```tsx
        hunt_sheets={data.hunt_sheets}
```

- [ ] **Step 3: Typecheck and build**

Run: `cd frontend && timeout 180 npx tsc --noEmit > /tmp/tsc.log 2>&1`
Expected: clean, exit 0.

Run: `cd frontend && timeout 300 npm run build > /tmp/build.log 2>&1`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/HuntInfoPanel.tsx frontend/src/pages/HuntBoard.tsx
git commit -m "feat(web): show a hunt's own working sheets on the board header"
```

---

## Task 19: Documentation

**Files:**
- Modify: `docs/commands.md:128-136`

The house rule is that a command's docstring and its `docs/commands.md` row change together.
Tasks 8 and 9 changed the docstrings; this closes the pair.

- [ ] **Step 1: Update the Sheets section**

Replace lines 128-136 of `docs/commands.md`:

```markdown
## Sheets

Run in a puzzle channel for that puzzle's sheets, or anywhere else in the hunt for the
hunt's own — somewhere to work on something found in the hunt's alt text before you know
which puzzle it belongs to.

| Command | Aliases | Arguments | Does |
|---|---|---|---|
| `!add_sheet` | | `sheet-name` | Add an extra working sheet. In a puzzle channel it belongs to that puzzle (which must be unsolved); anywhere else in the hunt it belongs to the hunt. |
| `!list_sheets` | `!list_additional_sheets` | — | List the puzzle's sheets, or the hunt's own, with links and numbers. |
| `!remove_sheet` | `!delete_sheet` | `number` | Remove a sheet by its number, from this puzzle or from the hunt. |
```

- [ ] **Step 2: Check the docstrings agree**

Run: `grep -n "hunt" bot/cogs/puzzles.py | grep -i "sheet" | head -20`

Confirm the three command docstrings written in Tasks 8 and 9 describe the same behaviour as
the table. Fix whichever is wrong.

- [ ] **Step 3: Commit**

```bash
git add docs/commands.md
git commit -m "docs: sheet commands work on a hunt's own sheets too"
```

---

## Task 20: Full verification

**Files:** none

- [ ] **Step 1: Whole Python suite**

Run: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: all pass, exit 0. A timeout-killed run exits 124 — read `/tmp/test.log`.

- [ ] **Step 2: Lint the touched Python**

Run: `pipenv run ruff check bot/ tests/ > /tmp/ruff.log 2>&1`
Expected: clean, or only pre-existing findings. **Do not blind-apply `--fix` for F401** —
imports that look unused can be package re-exports or test patch targets.

- [ ] **Step 3: Frontend typecheck and build**

Run: `cd frontend && timeout 180 npx tsc --noEmit > /tmp/tsc.log 2>&1`
Expected: clean, exit 0.

Run: `cd frontend && timeout 300 npm run build > /tmp/build.log 2>&1`
Expected: exit 0.

- [ ] **Step 4: Migration state**

Run: `pipenv run python migrate.py status`
Expected: `031` applied, nothing pending.

- [ ] **Step 5: Confirm no stale name survived the arity change**

Run: `grep -rn "evidence_source_label\|cluePuzzleId\|get_by_hunt" bot/ frontend/src/ tests/`

Expected: `evidence_source_label` appears only in `puzzle_service.py` and
`tests/test_contribution_table_requests.py`, always with two arguments; `cluePuzzleId`
appears nowhere; `get_by_hunt` appears in the DAO, the service, and the API route.

- [ ] **Step 6: Manual check against the running app**

The frontend has no test runner, so these three paths are inspection-only until exercised.
On the test site:

1. In a hunt's general channel, `!add_sheet alt text` → a new tab appears on the hunt's
   spreadsheet and the reply names it.
2. `!list_sheets` in the same channel lists it with a working link; `!list_sheets` in a
   puzzle channel still lists that puzzle's sheets and not the hunt's.
3. The dashboard board header shows "Hunt working sheets: alt text"; the Evidence panel's
   source picker offers "alt text (hunt sheet)", and an item logged against it shows that
   name in the Source column and in the hunt's Evidence tab.
4. `!remove_sheet 1` in the hunt channel removes it.

Report which of these you ran and what happened. Do not claim the frontend is verified by
`tsc`.

- [ ] **Step 7: Final state**

Run: `git status --short && git log --oneline -20`
Expected: clean tree; the tasks above as separate commits on `staging`.

---

## Self-review notes

**Spec coverage.** AC 11 → Tasks 1, 4, 7, 8 (the lock is what makes "two people running it at
once do not race" true). AC 12 → Tasks 9, 10, 18. AC 13 → Tasks 11–15, 17. The spec's
"`get_by_hunt()`" → Task 3; "`evidence_items.source_sheet_id` with its branch in
`evidence_source_label()`" → Tasks 11 and 13. The spec's "widen the inner join to a left
join" is deliberately **not** implemented as written; see the deviation note at the top.

**Not covered, deliberately.** Creating or deleting a hunt-level sheet from the dashboard
(the project's command-parity principle wants it; this plan ships read-only, as noted above).
`get_additional_sheet_spreadsheet(ctx, puzzle, hunt)` is left alone — the spec observed it
already accepts a hunt, but hunt-level sheets always live on `hunt.google_sheet_id`, so the
service reads that directly and the helper keeps its `assert puzzle is not None`.
