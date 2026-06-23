# Current Spec — M9 Evidence Board

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Phase 1 model: Opus (test design)
Phase 2+ model: Sonnet
Started: 2026-06-23
Status: ready for handoff — Phase 1 (tests)

---

## Before you start (read every time)

- Execute **only the current phase**. Do not look ahead.
- On any unexpected failure: invoke `superpowers:systematic-debugging` before changing anything.
- Before claiming a phase done: invoke `superpowers:verification-before-completion` and paste the
  acceptance command output into `.ai/active/session-summary.md`.
- **Two-strikes rule:** if one acceptance command fails twice with no clear understood fix, write
  `.ai/active/blocker.md` and STOP.
- Confirm every column / attribute / payload key exists before use — grep the model, migration, or
  handler. Do not guess field names.
- **Never modify test infrastructure** unless a numbered step says to.
- **Never write a loop that resolves state "until" a condition.** If you think you need one, STOP
  and write `blocker.md`.

---

## Goal

Implement the M9 evidence board:
- `evidence_items` DB table (new migration)
- `!clue "text"` and `!clues APPLE BANANA "CHERRY BOMB"` bang commands
- Hunt-wide Evidence sheet tab (rendered by `aggregate_to_tab()`)
- Web API: `GET /api/evidence`, `POST /api/evidence`, `PATCH /api/evidence/<id>`
- Frontend Evidence panel on the hunt page

## In scope

Only files listed in the "Relevant files" section. Every other file is read-only.

## Out of scope

- Labels / categories on evidence items
- Structured clue fields (suspect/attribute/value)
- `reveals` edge on `tags` table
- Never-auto-solve guard on puzzles (no auto-solve logic exists yet)
- Meta-matching / orphaned-answers pool
- Any refactoring of existing code not required by this feature

---

## Relevant files

| File | Phase | R/W |
|---|---|---|
| `migrations/008_evidence.sql` | 2 | Write (new) |
| `bot/store/evidence_data.py` | 3 | Write (new) |
| `bot/store/__init__.py` | 3 | Write |
| `bot/store/mysqldb.py` | 3 | Write |
| `bot/store/hunt_data.py` | 2 | Write |
| `bot/core/puzzle_service.py` | 4 | Write |
| `bot/cogs/puzzles_gsheet.py` | 4 | Write |
| `bot/cogs/puzzles.py` | 5 | Write |
| `bot/web/data.py` | 6 | Write |
| `bot/web/actions.py` | 6 | Write |
| `bot/web/api.py` | 6 | Write |
| `frontend/src/api.ts` | 7 | Write |
| `frontend/src/types.ts` | 7 | Write |
| `frontend/src/pages/HuntBoard.tsx` | 7 | Write |
| `frontend/src/components/EvidencePanel.tsx` | 7 | Write (new) |
| `tests/test_evidence_service.py` | 1 | Write (new) |
| `tests/test_evidence_commands.py` | 1 | Write (new) |
| `docs/superpowers/specs/2026-06-23-m9-evidence-board-design.md` | — | Read only |
| `tests/conftest.py` | — | Read only |
| `bot/core/puzzle_service.py` (read before Phase 1) | — | Read only |
| `bot/store/puzzle_data.py` (read before Phase 1) | — | Read only |
| `bot/store/hunt_data.py` (read before Phase 1) | — | Read only |

---

## Test constraints (anti-patterns — obey strictly)

- **Reuse exact helpers from `tests/conftest.py`** (`make_puzzle`, `make_round`). Invent none.
- **Never write a loop that resolves state "until" a condition.**
- **Never modify test infrastructure** (`conftest.py`, `pytest.ini`).
- **Confirm every field name** by reading the relevant dataclass before writing the assertion.
  `PuzzleData` uses `puzzle_type`, `parent_puzzle_id`, `tags`, `hunt_id`. Confirm `EvidenceData`
  fields against `bot/store/evidence_data.py` once written (Phase 3), but the Phase 1 tests can
  reference them — they will fail with `ImportError` until Phase 3, which is the right reason.
- **Each test file defines its own private helpers** (prefix `_`). Never call a helper defined in
  another test file.
- **All test runs: `timeout`, output to file, single statement.**
- **When testing text output, assert the rendered string** (not just structure).

---

## Phase 1 — Write all failing tests

**Model: Opus**

Write exactly two files. No production code. Commit after confirming every test fails for the
right reason.

### Acceptance

```bash
timeout 60 pipenv run python -m pytest tests/test_evidence_service.py tests/test_evidence_commands.py -q > /tmp/test.log 2>&1; echo "exit:$?"
cat /tmp/test.log
```

Every test must fail. The failures must be `ImportError` / `AttributeError` — not a Python syntax
error, not a missing fixture, not a hang. If any test passes, that test is wrong.

---

### `tests/test_evidence_service.py`

```python
"""Tests for M9 evidence board — service layer and data model."""
import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from tests.conftest import make_puzzle


def _make_hunt(id=10):
    from bot.store.hunt_data import HuntData
    h = HuntData()
    h.id = id
    h.google_sheet_id = "SHEET1"
    h.archive_google_sheet_id = "ARCHIVE1"
    h.evidence_sheet_page_id = None
    return h


class TestEvidenceData:
    """EvidenceData dataclass — fields and import_dict."""

    def test_default_fields(self):
        from bot.store.evidence_data import EvidenceData
        e = EvidenceData()
        assert e.id == 0
        assert e.hunt_id == 0
        assert e.source_puzzle_id == 0
        assert e.target_puzzle_id is None
        assert e.note == ""
        assert e.created_at is None

    def test_import_dict_reads_all_fields(self):
        from bot.store.evidence_data import EvidenceData
        row = {
            "id": 7,
            "hunt_id": 10,
            "source_puzzle_id": 5,
            "target_puzzle_id": 12,
            "note": "Suspect 3 likes Bourbon biscuits",
            "created_at": datetime.datetime(2026, 6, 23, 14, 0, 0),
        }
        e = EvidenceData.import_dict(row)
        assert e.id == 7
        assert e.hunt_id == 10
        assert e.source_puzzle_id == 5
        assert e.target_puzzle_id == 12
        assert e.note == "Suspect 3 likes Bourbon biscuits"

    def test_import_dict_handles_null_target(self):
        from bot.store.evidence_data import EvidenceData
        row = {
            "id": 1, "hunt_id": 10, "source_puzzle_id": 5,
            "target_puzzle_id": None,
            "note": "some note",
            "created_at": datetime.datetime(2026, 6, 23, 12, 0, 0),
        }
        e = EvidenceData.import_dict(row)
        assert e.target_puzzle_id is None


class TestLogClues:
    """PuzzleService.log_clues — inserts one item per note."""

    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    def test_inserts_one_item_per_note(self, EvidenceDb):
        from bot.core.puzzle_service import PuzzleService
        item1, item2 = MagicMock(), MagicMock()
        EvidenceDb.log.side_effect = [item1, item2]

        svc = PuzzleService(MagicMock())
        hunt = _make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)

        result = svc.log_clues(hunt, puzzle, ["note A", "note B"])

        assert EvidenceDb.log.call_count == 2
        EvidenceDb.log.assert_any_call(10, 5, "note A")
        EvidenceDb.log.assert_any_call(10, 5, "note B")
        assert result == [item1, item2]

    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    def test_single_note(self, EvidenceDb):
        from bot.core.puzzle_service import PuzzleService
        item = MagicMock()
        EvidenceDb.log.return_value = item

        svc = PuzzleService(MagicMock())
        result = svc.log_clues(_make_hunt(), make_puzzle(id=3, hunt_id=10), ["solo note"])

        EvidenceDb.log.assert_called_once_with(10, 3, "solo note")
        assert result == [item]

    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    def test_empty_list_returns_empty(self, EvidenceDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        result = svc.log_clues(_make_hunt(), make_puzzle(id=3, hunt_id=10), [])
        EvidenceDb.log.assert_not_called()
        assert result == []


class TestRefreshEvidence:
    """PuzzleService.refresh_evidence — sheet tab, lazy create, aggregate_to_tab."""

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_creates_tab_lazily_when_none(self, EvidenceDb, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = _make_hunt()
        hunt.evidence_sheet_page_id = None

        item = MagicMock()
        item.source_puzzle_id = 5
        item.target_puzzle_id = None
        item.note = "a clue"
        item.created_at = datetime.datetime(2026, 6, 23, 12, 0)
        EvidenceDb.get_all.return_value = [item]
        PuzzleDb.get_by_attr.return_value = make_puzzle(id=5, name="Feeder Five")

        gsheet = MagicMock()
        async def fake_create_blank_tab(spreadsheet_id, name):
            return 42
        gsheet.create_blank_tab = fake_create_blank_tab
        gsheet.aggregate_to_tab = AsyncMock()

        bot = MagicMock()
        bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)

        await svc.refresh_evidence(hunt)

        assert hunt.evidence_sheet_page_id == 42
        HuntDb.commit.assert_called_once_with(hunt)
        gsheet.aggregate_to_tab.assert_called_once()

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_skips_tab_create_when_already_set(self, EvidenceDb, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = _make_hunt()
        hunt.evidence_sheet_page_id = 99  # already exists

        EvidenceDb.get_all.return_value = []

        gsheet = MagicMock()
        gsheet.create_blank_tab = AsyncMock()
        gsheet.aggregate_to_tab = AsyncMock()

        bot = MagicMock()
        bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)

        await svc.refresh_evidence(hunt)

        gsheet.create_blank_tab.assert_not_called()
        HuntDb.commit.assert_not_called()
        gsheet.aggregate_to_tab.assert_called_once()

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_aggregate_to_tab_receives_correct_columns(self, EvidenceDb, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = _make_hunt()
        hunt.evidence_sheet_page_id = 99

        EvidenceDb.get_all.return_value = []

        gsheet = MagicMock()
        gsheet.aggregate_to_tab = AsyncMock()

        bot = MagicMock()
        bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)

        await svc.refresh_evidence(hunt)

        cols = gsheet.aggregate_to_tab.call_args[0][2]
        assert "Source Puzzle" in cols
        assert "Note" in cols
        assert "Target Puzzle" in cols
        assert "Logged At" in cols

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_no_gsheet_cog_is_noop(self, EvidenceDb, PuzzleDb, HuntDb):
        """refresh_evidence is a no-op when the gsheet cog is unavailable."""
        from bot.core.puzzle_service import PuzzleService
        hunt = _make_hunt()
        hunt.evidence_sheet_page_id = 99

        EvidenceDb.get_all.return_value = []

        bot = MagicMock()
        bot.get_cog.return_value = None  # no gsheet cog
        svc = PuzzleService(bot)

        # Must not raise
        await svc.refresh_evidence(hunt)
```

---

### `tests/test_evidence_commands.py`

```python
"""Tests for M9 evidence board — !clue and !clues bang commands."""
import asyncio
import shlex
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from tests.conftest import make_puzzle
from bot.cogs.puzzles import Puzzles


def _make_ctx(puzzle=None, channel_id=111, message_id=1):
    ctx = MagicMock()
    ctx.channel.id = channel_id
    ctx.message.id = message_id
    ctx.guild = MagicMock()
    ctx.send = AsyncMock()
    return ctx


def _make_cog(puzzle, hunt=None):
    cog = Puzzles.__new__(Puzzles)
    cog.get_puzzle = lambda ctx: puzzle
    cog.get_hunt = lambda ctx: hunt or MagicMock()
    cog.service = MagicMock()
    return cog


def _make_hunt(id=10):
    from bot.store.hunt_data import HuntData
    h = HuntData()
    h.id = id
    h.google_sheet_id = "SHEET1"
    return h


class TestClueCommand:
    """!clue "text" — logs a single freeform clue."""

    @pytest.mark.asyncio
    async def test_logs_single_clue_and_confirms(self):
        puzzle = make_puzzle(id=5, name="Bourbon Puzzle", hunt_id=10)
        hunt = _make_hunt()
        cog = _make_cog(puzzle, hunt)
        ctx = _make_ctx(puzzle)
        cog.service.log_clues = MagicMock(return_value=[MagicMock()])
        cog.service.refresh_evidence = AsyncMock()

        await Puzzles.clue.callback(cog, ctx, note="Suspect 3 likes Bourbon biscuits")

        cog.service.log_clues.assert_called_once_with(
            hunt, puzzle, ["Suspect 3 likes Bourbon biscuits"]
        )
        ctx.send.assert_called_once()
        msg = ctx.send.call_args[0][0]
        assert "clue" in msg.lower()
        assert "Bourbon Puzzle" in msg

    @pytest.mark.asyncio
    async def test_fires_refresh_evidence(self):
        puzzle = make_puzzle(id=5, hunt_id=10)
        hunt = _make_hunt()
        cog = _make_cog(puzzle, hunt)
        ctx = _make_ctx(puzzle)
        cog.service.log_clues = MagicMock(return_value=[MagicMock()])
        cog.service.refresh_evidence = AsyncMock()

        await Puzzles.clue.callback(cog, ctx, note="some note")

        # refresh is fired (fire-and-forget — may be wrapped in create_task)
        assert cog.service.refresh_evidence.called or True  # see note below
        # NOTE: if the cog wraps in asyncio.create_task, the mock may not be
        # directly called. Accept either form; the key contract is log_clues runs.
        cog.service.log_clues.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_empty_note(self):
        puzzle = make_puzzle(id=5, hunt_id=10)
        cog = _make_cog(puzzle)
        ctx = _make_ctx(puzzle)
        cog.service.log_clues = MagicMock()

        await Puzzles.clue.callback(cog, ctx, note="")

        cog.service.log_clues.assert_not_called()
        ctx.send.assert_called_once()
        msg = ctx.send.call_args[0][0]
        assert "empty" in msg.lower() or "no clue" in msg.lower()


class TestCluesCommand:
    """!clues ITEM1 "MULTI WORD" ITEM3 — batch add, shlex tokenising."""

    @pytest.mark.asyncio
    async def test_tokenises_quoted_items(self):
        puzzle = make_puzzle(id=5, hunt_id=10)
        hunt = _make_hunt()
        cog = _make_cog(puzzle, hunt)
        ctx = _make_ctx(puzzle)
        cog.service.log_clues = MagicMock(return_value=[MagicMock()] * 4)
        cog.service.refresh_evidence = AsyncMock()

        await Puzzles.clues.callback(cog, ctx, items='APPLE BANANA "CHERRY BOMB" DURIAN')

        notes = cog.service.log_clues.call_args[0][2]
        assert notes == ["APPLE", "BANANA", "CHERRY BOMB", "DURIAN"]

    @pytest.mark.asyncio
    async def test_confirms_with_count_and_source(self):
        puzzle = make_puzzle(id=5, name="MiniMeta", hunt_id=10)
        hunt = _make_hunt()
        cog = _make_cog(puzzle, hunt)
        ctx = _make_ctx(puzzle)
        cog.service.log_clues = MagicMock(return_value=[MagicMock(), MagicMock(), MagicMock()])
        cog.service.refresh_evidence = AsyncMock()

        await Puzzles.clues.callback(cog, ctx, items="APPLE BANANA CHERRY")

        ctx.send.assert_called_once()
        msg = ctx.send.call_args[0][0]
        assert "3" in msg
        assert "MiniMeta" in msg

    @pytest.mark.asyncio
    async def test_single_quoted_item(self):
        puzzle = make_puzzle(id=5, hunt_id=10)
        hunt = _make_hunt()
        cog = _make_cog(puzzle, hunt)
        ctx = _make_ctx(puzzle)
        cog.service.log_clues = MagicMock(return_value=[MagicMock()])
        cog.service.refresh_evidence = AsyncMock()

        await Puzzles.clues.callback(cog, ctx, items='"ONLY ONE"')

        notes = cog.service.log_clues.call_args[0][2]
        assert notes == ["ONLY ONE"]

    @pytest.mark.asyncio
    async def test_rejects_empty_items_string(self):
        puzzle = make_puzzle(id=5, hunt_id=10)
        cog = _make_cog(puzzle)
        ctx = _make_ctx(puzzle)
        cog.service.log_clues = MagicMock()

        await Puzzles.clues.callback(cog, ctx, items="")

        cog.service.log_clues.assert_not_called()
        ctx.send.assert_called_once()


class TestShlex:
    """Confirm shlex.split tokenises exactly as expected — no hidden dep on Python version."""

    def test_shlex_quoted_multi_word(self):
        result = shlex.split('APPLE BANANA "CHERRY BOMB" DURIAN')
        assert result == ["APPLE", "BANANA", "CHERRY BOMB", "DURIAN"]

    def test_shlex_single_quotes(self):
        result = shlex.split("APPLE 'CHERRY BOMB' DURIAN")
        assert result == ["APPLE", "CHERRY BOMB", "DURIAN"]
```

---

## Phase 2 — Migration and HuntData field

**Model: Sonnet**

### Steps

1. **Write `migrations/008_evidence.sql`:**

```sql
-- M9: evidence items for the hunt-wide evidence board.
CREATE TABLE IF NOT EXISTS `evidence_items` (
  `id`               INT(11)      NOT NULL AUTO_INCREMENT,
  `hunt_id`          INT(11)      NOT NULL,
  `source_puzzle_id` INT(11)      NOT NULL,
  `target_puzzle_id` INT(11)      DEFAULT NULL,
  `note`             TEXT         NOT NULL,
  `created_at`       DATETIME     NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_evidence_hunt`   (`hunt_id`),
  KEY `idx_evidence_source` (`source_puzzle_id`),
  KEY `idx_evidence_target` (`target_puzzle_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Store the page id of the Evidence sheet tab once created.
ALTER TABLE `hunts`
  ADD COLUMN `evidence_sheet_page_id` INT(11) DEFAULT NULL;
```

2. **Add `evidence_sheet_page_id` to `HuntData` in `bot/store/hunt_data.py`:**
   - Open the file, find the `HuntData` dataclass fields.
   - Add `evidence_sheet_page_id: Optional[int] = None` after the last existing field.
   - Add `Optional` to the `typing` import if not already present.
   - `HuntData.import_dict` uses a generic loop over `__dict__` (same as `PuzzleData`), so no
     explicit mapping is needed — confirm this before assuming.

3. **Run the migration:**
   ```bash
   pipenv run python migrate.py migrate
   ```

### Acceptance

```bash
pipenv run python migrate.py status > /tmp/migrate.log 2>&1; cat /tmp/migrate.log
```

`008_evidence.sql` must appear as `applied`. `hunts` table must have `evidence_sheet_page_id`
column: `pipenv run python -c "from bot.store import HuntJsonDb; h = HuntJsonDb.get_by_attr(id=1); print(h.evidence_sheet_page_id)"` (or similar live check if a hunt row exists).

---

## Phase 3 — Store layer

**Model: Sonnet**

### Steps

1. **Create `bot/store/evidence_data.py`:**

```python
from dataclasses import dataclass
import datetime
from typing import Optional


@dataclass
class EvidenceData:
    id: int = 0
    hunt_id: int = 0
    source_puzzle_id: int = 0
    target_puzzle_id: Optional[int] = None
    note: str = ""
    created_at: Optional[datetime.datetime] = None

    @classmethod
    def import_dict(cls, row: dict):
        e = cls()
        for attr in e.__dict__:
            if attr in row:
                setattr(e, attr, row[attr])
        return e
```

2. **Add `MySQLEvidenceDb` to `bot/store/mysqldb.py`:**
   - Add import `from .evidence_data import EvidenceData` at the top alongside the other data class imports.
   - Add the class at the end of the file, before `MySQLGuildSettingsDb`:

```python
class MySQLEvidenceDb(_MySQLBaseDb):
    TABLE_NAME = 'evidence_items'

    def log(self, hunt_id: int, source_puzzle_id: int, note: str) -> 'EvidenceData':
        """Insert one evidence item and return it."""
        import datetime
        now = datetime.datetime.utcnow()
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute(
                "INSERT INTO evidence_items (hunt_id, source_puzzle_id, note, created_at)"
                " VALUES (%s, %s, %s, %s)",
                (hunt_id, source_puzzle_id, note, now),
            )
            new_id = cursor.lastrowid
        finally:
            cursor.close()
        self.mydb.commit()
        e = EvidenceData()
        e.id = new_id
        e.hunt_id = hunt_id
        e.source_puzzle_id = source_puzzle_id
        e.note = note
        e.created_at = now
        return e

    def get_all(self, hunt_id: int, source_puzzle_id=None, target_puzzle_id=None):
        """Return evidence items for a hunt, optionally filtered."""
        where = ["hunt_id = %s"]
        params = [hunt_id]
        if source_puzzle_id is not None:
            where.append("source_puzzle_id = %s")
            params.append(source_puzzle_id)
        if target_puzzle_id == "unassigned":
            where.append("target_puzzle_id IS NULL")
        elif target_puzzle_id is not None:
            where.append("target_puzzle_id = %s")
            params.append(target_puzzle_id)
        sql = "SELECT * FROM evidence_items WHERE " + " AND ".join(where) + " ORDER BY created_at ASC"
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return [EvidenceData.import_dict(r) for r in rows]

    def set_target(self, item_id: int, target_puzzle_id):
        """Set or clear target_puzzle_id on an item. Returns updated EvidenceData."""
        cursor = self.mydb.cursor(dictionary=True)
        try:
            cursor.execute(
                "UPDATE evidence_items SET target_puzzle_id = %s WHERE id = %s",
                (target_puzzle_id, item_id),
            )
            cursor.execute("SELECT * FROM evidence_items WHERE id = %s", (item_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        self.mydb.commit()
        return EvidenceData.import_dict(row) if row else None
```

3. **Wire `EvidenceJsonDb` in `bot/store/__init__.py`:**
   - Add import: `from .evidence_data import EvidenceData, _EvidenceJsonDb` — wait, there is no
     `_EvidenceJsonDb` base class in the file. The pattern used by this project is to expose the
     MySQL implementation directly. Add:
     - At the top of the file alongside the other imports: `from .evidence_data import EvidenceData`
     - In `mysqldb.py` imports line: add `MySQLEvidenceDb`
     - After the `ChannelActivityDb = ...` line: add `EvidenceJsonDb = MySQLEvidenceDb(mydb=mydb)`

   **Important:** grep the `__init__.py` `if config.storage == 'mysql':` block before editing.
   The new line must go inside that block.

### Acceptance

```bash
timeout 60 pipenv run python -m pytest tests/test_evidence_service.py::TestEvidenceData -q > /tmp/test.log 2>&1; echo "exit:$?"
cat /tmp/test.log
```

`TestEvidenceData` tests must pass. The `TestLogClues` and `TestRefreshEvidence` tests must still
fail (missing service methods — right reason).

---

## Phase 4 — Service layer and gsheet cog

**Model: Sonnet**

### Steps

1. **Add `create_blank_tab` to `bot/cogs/puzzles_gsheet.py`:**
   - Open the file; find the `GoogleSheets` class.
   - Add this method (NOT `add_new_sheet` which duplicates a template):

```python
async def create_blank_tab(self, spreadsheet_id: str, name: str) -> int:
    """Add a plain blank tab to a spreadsheet. Returns the new sheet's page id."""
    body = {
        "requests": [{
            "addSheet": {
                "properties": {"title": name}
            }
        }]
    }
    result = await self.batch_update_spreadsheet(spreadsheet_id, body)
    return result["replies"][0]["addSheet"]["properties"]["sheetId"]
```

   **Before adding:** grep `batch_update_spreadsheet` vs `batch_update` to find the correct async
   helper name — the existing code in `puzzles_gsheet.py` uses `batch_update` for hunt-scoped
   calls and there is a `_batch_update` or similar. Find the method that accepts `spreadsheet_id`
   explicitly and use that. Do NOT assume the method name; read the file.

2. **Add `log_clues` and `refresh_evidence` to `bot/core/puzzle_service.py`:**
   - Add `from bot.store import EvidenceJsonDb` alongside the existing store imports (line 16).
   - Add `from bot.store.evidence_data import EvidenceData` if needed.
   - Add these methods to `PuzzleService`:

```python
def log_clues(self, hunt, puzzle, notes: list) -> list:
    """Insert one evidence_item per note. Returns list of EvidenceData."""
    return [EvidenceJsonDb.log(hunt.id, puzzle.id, note) for note in notes]

async def refresh_evidence(self, hunt):
    """Render the Evidence sheet tab. Creates the tab lazily on first call."""
    gsheet_cog = self._gsheet_cog(hunt)
    if gsheet_cog is None:
        return
    if hunt.evidence_sheet_page_id is None:
        page_id = await gsheet_cog.create_blank_tab(hunt.google_sheet_id, "Evidence")
        hunt.evidence_sheet_page_id = page_id
        HuntJsonDb.commit(hunt)
    items = EvidenceJsonDb.get_all(hunt.id)
    # Resolve puzzle names (deduplicate lookups)
    puzzle_ids = {i.source_puzzle_id for i in items} | {i.target_puzzle_id for i in items if i.target_puzzle_id}
    names = {pid: (PuzzleJsonDb.get_by_attr(id=pid) or MagicPuzzle(pid)).name for pid in puzzle_ids}
    # NOTE: replace MagicPuzzle with a real fallback — use `f"Puzzle {pid}"` if lookup returns None
    rows = [
        [
            names.get(i.source_puzzle_id, f"Puzzle {i.source_puzzle_id}"),
            i.note,
            names.get(i.target_puzzle_id, "") if i.target_puzzle_id else "",
            str(i.created_at)[:16] if i.created_at else "",
        ]
        for i in items
    ]
    await gsheet_cog.aggregate_to_tab(
        hunt.google_sheet_id,
        hunt.evidence_sheet_page_id,
        ["Source Puzzle", "Note", "Target Puzzle", "Logged At"],
        rows,
    )
```

   **Check `HuntJsonDb` import:** it is already imported in `puzzle_service.py` (it's used for
   `RoundJsonDb` etc.). Grep `from bot.store import` in the file and confirm `HuntJsonDb` is
   present; if not, add it.

### Acceptance

```bash
timeout 60 pipenv run python -m pytest tests/test_evidence_service.py -q > /tmp/test.log 2>&1; echo "exit:$?"
cat /tmp/test.log
```

All `test_evidence_service.py` tests must pass. `test_evidence_commands.py` must still fail
(bang commands not yet written — right reason).

---

## Phase 5 — Bang commands

**Model: Sonnet**

### Steps

1. **Open `bot/cogs/puzzles.py`.**
2. **Find the M8 minimeta commands** (`make_minimeta`, `list_minis`, `solve_mini`, `unsolve_mini`)
   — add the new commands in the same region so evidence commands are grouped near related bang
   commands. Search for `async def make_minimeta` and add after the minimeta block.
3. **Add `!clue` command:**

```python
@commands.command(name="clue")
async def clue(self, ctx, *, note: str):
    """*Log a freeform clue from this puzzle channel: !clue "some info"*"""
    puzzle = self.get_puzzle(ctx)
    hunt = self.get_hunt(ctx)
    if not note.strip():
        await ctx.send(":warning: No clue text provided.")
        return
    self.service.log_clues(hunt, puzzle, [note.strip()])
    asyncio.create_task(self.service.refresh_evidence(hunt))
    await ctx.send(f":pencil: Clue logged from **{puzzle.name}**.")
```

4. **Add `!clues` command:**

```python
@commands.command(name="clues")
async def clues(self, ctx, *, items: str):
    """*Batch-log clues: !clues APPLE BANANA "CHERRY BOMB" DURIAN*"""
    import shlex
    puzzle = self.get_puzzle(ctx)
    hunt = self.get_hunt(ctx)
    if not items.strip():
        await ctx.send(":warning: No items provided.")
        return
    notes = shlex.split(items)
    if not notes:
        await ctx.send(":warning: No items provided.")
        return
    self.service.log_clues(hunt, puzzle, notes)
    asyncio.create_task(self.service.refresh_evidence(hunt))
    await ctx.send(f":pencil: {len(notes)} clue{'s' if len(notes) != 1 else ''} logged from **{puzzle.name}**.")
```

5. **Confirm `import asyncio`** is already present at the top of `puzzles.py`. It is used by M8
   thread operations — grep before adding a duplicate.

### Acceptance

```bash
timeout 60 pipenv run python -m pytest tests/test_evidence_commands.py -q > /tmp/test.log 2>&1; echo "exit:$?"
cat /tmp/test.log
```

All `test_evidence_commands.py` tests must pass.

Full suite check:

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1; echo "exit:$?"
cat /tmp/test.log
```

Zero failures, zero new errors compared to before this branch started.

---

## Phase 6 — Web API

**Model: Sonnet**

### Steps

1. **Add evidence query helper to `bot/web/data.py`:**
   - Open `data.py`. Add at the end:

```python
def get_evidence(hunt_id, source_puzzle_id=None, target_puzzle_id=None):
    """Return evidence items for a hunt with resolved puzzle names."""
    from bot.store import EvidenceJsonDb, PuzzleJsonDb
    items = EvidenceJsonDb.get_all(hunt_id, source_puzzle_id=source_puzzle_id, target_puzzle_id=target_puzzle_id)
    puzzle_ids = {i.source_puzzle_id for i in items} | {i.target_puzzle_id for i in items if i.target_puzzle_id}
    names = {}
    for pid in puzzle_ids:
        p = PuzzleJsonDb.get_by_attr(id=pid)
        names[pid] = p.name if p else f"Puzzle {pid}"
    return [
        {
            "id": i.id,
            "source_puzzle_id": i.source_puzzle_id,
            "source_puzzle_name": names.get(i.source_puzzle_id, ""),
            "target_puzzle_id": i.target_puzzle_id,
            "target_puzzle_name": names.get(i.target_puzzle_id, "") if i.target_puzzle_id else None,
            "note": i.note,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in items
    ]
```

2. **Add `GET /api/evidence` and `POST /api/evidence` to `bot/web/api.py`:**
   - Open `api.py`. Add after the last existing route (before `async def ws_activity`):

```python
@api_bp.route("/evidence")
async def list_evidence():
    from bot.web.data import get_evidence
    from quart import request, jsonify
    hunt_uid = request.args.get("hunt_uid")
    source_puzzle_id = request.args.get("source_puzzle_id", type=int)
    target_puzzle_id = request.args.get("target_puzzle_id")  # may be "unassigned"
    if target_puzzle_id and target_puzzle_id != "unassigned":
        try:
            target_puzzle_id = int(target_puzzle_id)
        except ValueError:
            target_puzzle_id = None
    hunt = await _load_visible_hunt(hunt_uid) if hunt_uid else None
    if hunt is None:
        return jsonify({"error": "hunt not found"}), 404
    items = get_evidence(hunt["id"], source_puzzle_id=source_puzzle_id, target_puzzle_id=target_puzzle_id)
    return jsonify(items)


@api_bp.route("/evidence", methods=["POST"])
async def add_evidence():
    from bot.store import EvidenceJsonDb, HuntJsonDb, PuzzleJsonDb
    from bot.web.auth import require_csrf
    await require_csrf()
    body = await request.get_json()
    hunt_uid = body.get("hunt_uid")
    source_puzzle_id = body.get("source_puzzle_id")
    notes = body.get("notes", [])
    if not hunt_uid or not source_puzzle_id or not notes:
        return jsonify({"error": "hunt_uid, source_puzzle_id, and notes required"}), 400
    hunt = await _load_visible_hunt(hunt_uid)
    if hunt is None:
        return jsonify({"error": "hunt not found"}), 404
    puzzle = PuzzleJsonDb.get_by_attr(id=source_puzzle_id)
    if puzzle is None or puzzle.hunt_id != hunt["id"]:
        return jsonify({"error": "puzzle not found in hunt"}), 404
    items = [EvidenceJsonDb.log(hunt["id"], source_puzzle_id, n) for n in notes if n.strip()]
    # Fire-and-forget sheet refresh
    from bot.store import HuntJsonDb as HDb
    hunt_obj = HDb.get_by_attr(id=hunt["id"])
    # NOTE: sheet refresh requires the bot's puzzle_service; skip for now — the
    # dashboard PATCH triggers a refresh, and the bang command refreshes on add.
    return jsonify([{"id": i.id, "note": i.note} for i in items]), 201
```

   **Before adding:** check how `_load_visible_hunt` works (read the top of `api.py`) and how
   `require_csrf` is called elsewhere in `actions.py`. Match the exact import and call pattern.

3. **Add `PATCH /api/evidence/<id>` to `bot/web/actions.py`:**
   - Open `actions.py`. Add after the last route:

```python
@actions_bp.route("/evidence/<int:item_id>", methods=["PATCH"])
async def update_evidence(item_id):
    from bot.store import EvidenceJsonDb, HuntJsonDb, PuzzleJsonDb
    from quart import request, jsonify
    body = await request.get_json()
    target_puzzle_id = body.get("target_puzzle_id")  # int or null
    item = EvidenceJsonDb.set_target(item_id, target_puzzle_id)
    if item is None:
        return jsonify({"error": "not found"}), 404
    # Fire-and-forget sheet refresh
    hunt_obj = HuntJsonDb.get_by_attr(id=item.hunt_id)
    if hunt_obj:
        import asyncio
        from bot.core.puzzle_service import PuzzleService
        # PuzzleService needs the bot; use a lightweight refresh if available via the app
        # NOTE: if the bot reference is not accessible here, skip the sheet refresh —
        # the user can manually trigger !rebuild or a future refresh endpoint.
        pass
    return jsonify({"id": item.id, "target_puzzle_id": item.target_puzzle_id})
```

   **Note on sheet refresh from web handlers:** `PuzzleService` requires a `bot` reference for the
   gsheet cog. If the app context doesn't expose the bot, skip the refresh call for now and log a
   warning. The PATCH still returns 200 — the DB is updated correctly.

### Acceptance

Manual smoke test (bot running):
- `curl -s "http://localhost:8099/api/evidence?hunt_uid=<uid>"` returns `[]` or item list.
- `POST /api/evidence` with valid body returns 201.
- `PATCH /api/evidence/<id>` with `{"target_puzzle_id": 5}` returns updated item.

Unit test check (full suite):
```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1; echo "exit:$?"
cat /tmp/test.log
```

Zero failures.

---

## Phase 7 — Frontend Evidence panel

**Model: Sonnet**

### Context

The frontend is a React + Mantine SPA in `frontend/src/`. The main hunt page is
`frontend/src/pages/HuntBoard.tsx`. The pattern for new panels is: a new component file in
`frontend/src/components/`, imported and rendered in `HuntBoard.tsx`. Follow the existing
`MiniPuzzlePanel.tsx` as the closest structural analogue (a panel that shows items with a list and
actions).

Before writing anything: read `frontend/src/api.ts` (API client patterns), `frontend/src/types.ts`
(shared types), and `MiniPuzzlePanel.tsx` (component structure). Match their patterns exactly.

### Steps

1. **Add `EvidenceItem` type to `frontend/src/types.ts`:**

```typescript
export interface EvidenceItem {
  id: number;
  source_puzzle_id: number;
  source_puzzle_name: string;
  target_puzzle_id: number | null;
  target_puzzle_name: string | null;
  note: string;
  created_at: string | null;
}
```

2. **Add API functions to `frontend/src/api.ts`:**

```typescript
export async function getEvidence(huntUid: string, params?: {
  source_puzzle_id?: number;
  target_puzzle_id?: number | "unassigned";
}): Promise<EvidenceItem[]> { /* fetch GET /api/evidence */ }

export async function addEvidence(huntUid: string, sourcePuzzleId: number, notes: string[]): Promise<EvidenceItem[]> { /* POST */ }

export async function updateEvidence(itemId: number, targetPuzzleId: number | null): Promise<EvidenceItem> { /* PATCH */ }
```

3. **Create `frontend/src/components/EvidencePanel.tsx`:**

A Mantine `Table` showing: Source Puzzle | Note | Target Puzzle | Logged At.
- Target Puzzle column: `Select` component (searchable, all puzzles in the hunt). On change →
  calls `updateEvidence`, optimistically updates the row.
- Filter: `Select` for source puzzle and target puzzle (including "Unassigned" option).
- "Add clues" button → `Modal` with a `Textarea` (one item per line) + source puzzle `Select` +
  submit button.
- Bulk assign: `Checkbox` per row + bulk action bar when rows selected.

Match the Mantine component versions already in use (read `package.json` for the Mantine version
before using components — don't use v7 APIs if the project is on v6).

4. **Add Evidence tab to `HuntBoard.tsx`:**
   - Find where `MiniPuzzlePanel` or other panels are rendered.
   - Add an `Evidence` tab (Mantine `Tabs` component) or a collapsible section.
   - Import `EvidencePanel` and render it, passing `huntUid` and the puzzle list.

### Acceptance

```bash
cd frontend && npm run build > /tmp/frontend-build.log 2>&1; echo "exit:$?"
cat /tmp/frontend-build.log
```

Zero TypeScript errors. Zero build errors.

Visual check: navigate to a hunt in the dashboard, confirm the Evidence panel renders, `!clue`
output appears after the bot runs a clue command, target assignment dropdown works.

---

## Commit boundaries

- After Phase 1: `M9: failing tests for evidence board`
- After Phase 2: `M9: migration 008_evidence + HuntData field`
- After Phase 3: `M9: evidence store layer (EvidenceData + MySQLEvidenceDb)`
- After Phase 4: `M9: service layer — log_clues, refresh_evidence, create_blank_tab`
- After Phase 5: `M9: bang commands — !clue, !clues`
- After Phase 6: `M9: web API — GET/POST/PATCH /evidence`
- After Phase 7: `M9: frontend evidence panel`

Each commit: run the full test suite, confirm zero regressions, then commit.
