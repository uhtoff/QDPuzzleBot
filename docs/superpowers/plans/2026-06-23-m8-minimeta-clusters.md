# M8 Minimeta Clusters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add minimeta cluster support — a parent puzzle ("minimeta") with N mini-puzzles, each in its own Discord thread and Google Sheet tab, with answers rolling up to the minimeta sheet via the existing `refresh_meta` mechanism.

**Architecture:** Mini-puzzles are first-class `PuzzleData` rows linked to their parent via a new `parent_puzzle_id` FK. Discord threads (not channels) host mini-puzzle commands — thread-awareness is added to `cog_before_invoke`. New service methods in `bot/core/puzzle_service.py` drive both bang commands and web API endpoints. The dashboard gains an expand panel on minimeta rows for viewing, creating, solving, and unsolving minis.

**Tech Stack:** Python/discord.py 2.7.1, MySQL, Quart web API, React/TypeScript/Mantine frontend.

**Spec:** `docs/superpowers/specs/2026-06-23-m8-minimeta-clusters-design.md`

**Test runner:**
```bash
timeout 120 pipenv run python -m pytest tests/<file>.py -q > /tmp/test.log 2>&1
```
Read `/tmp/test.log` for results. Exit 124 = timeout.

---

## File Map

| Action | File | Change |
|---|---|---|
| Create | `migrations/007_minimeta.sql` | `parent_puzzle_id` column + FK |
| Modify | `bot/store/puzzle_data.py` | Add `parent_puzzle_id` field |
| Modify | `bot/store/mysqldb.py` | Add `get_children()` to `MySQLPuzzleJsonDb` |
| Modify | `tests/conftest.py` | Add `parent_puzzle_id` param to `make_puzzle` |
| Modify | `bot/cogs/puzzles.py` | Thread-aware `cog_before_invoke` + 5 new commands |
| Modify | `bot/core/puzzle_service.py` | 6 new/extended service methods |
| Create | `tests/test_minimeta_service.py` | Service-layer unit tests |
| Create | `tests/test_minimeta_commands.py` | Bang-command unit tests |
| Modify | `bot/web/data.py` | `parent_puzzle_id` in payload, `mini_puzzles` array, filter minis |
| Modify | `bot/web/actions.py` | `POST /api/puzzles/<id>/minis`, `POST /api/puzzles/<id>/unsolve` |
| Modify | `frontend/src/types.ts` | `MiniPuzzle` interface, `Puzzle.mini_puzzles` |
| Modify | `frontend/src/api.ts` | `useCreateMini`, `useUnsolvePuzzle` |
| Create | `frontend/src/components/MiniPuzzlePanel.tsx` | Expand panel component |
| Modify | `frontend/src/components/PuzzleTable.tsx` | Expand chevron + N/M badge on minimeta rows |

---

## Task 1: Migration — add `parent_puzzle_id`

**Files:**
- Create: `migrations/007_minimeta.sql`

- [ ] **Step 1: Write the migration**

```sql
-- M8: link mini-puzzles to their minimeta parent
ALTER TABLE puzzles ADD COLUMN parent_puzzle_id INT DEFAULT NULL;
ALTER TABLE puzzles ADD CONSTRAINT fk_puzzle_parent
    FOREIGN KEY (parent_puzzle_id) REFERENCES puzzles(id) ON DELETE SET NULL;
```

- [ ] **Step 2: Apply the migration**

```bash
pipenv run python migrate.py migrate
```

Expected: `Applied 007_minimeta.sql` (or similar success message).

- [ ] **Step 3: Verify**

```bash
pipenv run python -c "from bot.store import PuzzleJsonDb; print('ok')"
```

Expected: `ok` (no import errors).

- [ ] **Step 4: Commit**

```bash
git add migrations/007_minimeta.sql
git commit -m "migration: M8 add parent_puzzle_id to puzzles"
```

---

## Task 2: Data model — `PuzzleData` field + `get_children`

**Files:**
- Modify: `bot/store/puzzle_data.py:38` (after `additional_sheets` line)
- Modify: `bot/store/mysqldb.py` (after `get_all_from_round`)
- Modify: `tests/conftest.py:7` (`make_puzzle` signature)

- [ ] **Step 1: Write failing tests**

Create `tests/test_minimeta_service.py`:

```python
"""Tests for M8 minimeta cluster service methods."""
from unittest.mock import MagicMock, patch
import pytest

from bot.store.puzzle_data import PuzzleData
from bot.store.hunt_data import HuntData
from tests.conftest import make_puzzle, make_round


def _make_cog():
    from bot.cogs.puzzles import Puzzles
    bot = MagicMock()
    bot.get_cog.return_value = None
    return Puzzles(bot)


def _make_hunt(id=10):
    h = HuntData()
    h.id = id
    h.google_sheet_id = "SHEET1"
    h.archive_google_sheet_id = "ARCHIVE1"
    return h


class TestPuzzleDataParentField:
    def test_parent_puzzle_id_defaults_none(self):
        p = PuzzleData()
        assert p.parent_puzzle_id is None

    def test_make_puzzle_accepts_parent_puzzle_id(self):
        p = make_puzzle(id=2, parent_puzzle_id=1)
        assert p.parent_puzzle_id == 1

    def test_import_dict_reads_parent_puzzle_id(self):
        row = {
            "id": 5, "name": "Mini", "hunt_id": 10,
            "parent_puzzle_id": 3,
            "channel_id": 999, "solved": False, "solution": None,
            "metapuzzle": 0, "metameta": 0, "status": "", "priority": "",
            "puzzle_type": "Mini", "notes": None, "url": "", "google_page_id": "",
            "archived": False, "voice_channel_id": 0, "channel_mention": "",
            "channel_name": "", "start_time": None, "solve_time": None,
            "archive_time": None, "tag_edges": None,
        }
        p = PuzzleData.import_dict(row)
        assert p.parent_puzzle_id == 3
```

- [ ] **Step 2: Run — expect failures**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_service.py::TestPuzzleDataParentField -q > /tmp/test.log 2>&1
```

Expected: FAIL (AttributeError: 'PuzzleData' has no 'parent_puzzle_id').

- [ ] **Step 3: Add field to PuzzleData**

In `bot/store/puzzle_data.py`, after the `tags` field (line ~49), add:

```python
    parent_puzzle_id: Optional[int] = None
```

- [ ] **Step 4: Update `make_puzzle` in conftest**

In `tests/conftest.py`, update `make_puzzle`:

```python
def make_puzzle(
    id=1,
    name="Test Puzzle",
    hunt_id=10,
    solved=False,
    solution=None,
    metapuzzle=0,
    metameta=0,
    tags=None,
    status="",
    channel_id=111,
    parent_puzzle_id=None,
    puzzle_type="",
):
    p = PuzzleData()
    p.id = id
    p.name = name
    p.hunt_id = hunt_id
    p.channel_id = channel_id
    p.solved = solved
    p.solution = solution
    p.metapuzzle = metapuzzle
    p.metameta = metameta
    p.tags = tags if tags is not None else []
    p.status = status
    p.parent_puzzle_id = parent_puzzle_id
    p.puzzle_type = puzzle_type
    return p
```

- [ ] **Step 5: Add `get_children` to `MySQLPuzzleJsonDb`**

In `bot/store/mysqldb.py`, after `get_all_from_round`:

```python
    def get_children(self, parent_puzzle_id: int):
        """Return all mini-puzzles whose parent_puzzle_id matches."""
        cursor = self.mydb.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM puzzles WHERE parent_puzzle_id = %s ORDER BY id ASC",
            (parent_puzzle_id,),
        )
        rows = cursor.fetchall()
        cursor.close()
        return [self.construct_puzzle(r) for r in rows]
```

- [ ] **Step 6: Run tests — expect pass**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_service.py::TestPuzzleDataParentField -q > /tmp/test.log 2>&1
```

Expected: 3 passed.

- [ ] **Step 7: Run full suite to check no regressions**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```

Expected: all previously passing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add bot/store/puzzle_data.py bot/store/mysqldb.py tests/conftest.py tests/test_minimeta_service.py
git commit -m "M8: PuzzleData.parent_puzzle_id field + PuzzleJsonDb.get_children"
```

---

## Task 3: Thread-aware `cog_before_invoke`

**Files:**
- Modify: `bot/cogs/puzzles.py:165` (`cog_before_invoke`)

`cog_before_invoke` crashes when called from a Discord thread because it accesses `ctx.channel.category.id` — threads have no `.category` attribute. This task adds a thread branch that resolves the mini-puzzle by thread snowflake instead.

- [ ] **Step 1: Add tests to `tests/test_minimeta_service.py`**

Append to the file:

```python
class TestCogBeforeInvokeThread:
    """cog_before_invoke must route thread messages to the mini-puzzle row."""

    def _make_thread_ctx(self, channel_id):
        import discord
        thread = MagicMock(spec=discord.Thread)
        thread.id = channel_id
        ctx = MagicMock()
        ctx.channel = thread
        ctx.guild.id = 1
        ctx.message.id = 999
        return ctx

    @patch("bot.cogs.puzzles.GuildSettingsDb")
    @patch("bot.cogs.puzzles.PuzzleJsonDb")
    @patch("bot.cogs.puzzles.HuntJsonDb")
    @patch("bot.cogs.puzzles.RoundJsonDb")
    def test_thread_routes_to_mini_puzzle(self, RoundDb, HuntDb, PuzzleDb, GuildDb):
        from bot.cogs.puzzles import Puzzles
        import asyncio, discord

        mini = make_puzzle(id=5, hunt_id=10, channel_id=777, parent_puzzle_id=3, tags=[1])
        hunt = _make_hunt(id=10)
        round_ = make_round(id=1)

        GuildDb.get.return_value = MagicMock()
        GuildDb.get_channel_type.return_value = None  # thread not in GuildSettings
        PuzzleDb.get_by_attr.return_value = mini
        HuntDb.get_by_attr.return_value = hunt
        RoundDb.get_by_attr.return_value = round_

        bot = MagicMock()
        bot.get_cog.return_value = None
        cog = Puzzles(bot)

        ctx = self._make_thread_ctx(777)

        asyncio.get_event_loop().run_until_complete(cog.cog_before_invoke(ctx))

        env = cog.environment[999]
        assert env["channel_type"] == "Puzzle"
        assert env["puzzle"] is mini
        assert env["hunt"] is hunt

    @patch("bot.cogs.puzzles.GuildSettingsDb")
    @patch("bot.cogs.puzzles.PuzzleJsonDb")
    @patch("bot.cogs.puzzles.HuntJsonDb")
    @patch("bot.cogs.puzzles.RoundJsonDb")
    def test_unknown_thread_becomes_guild(self, RoundDb, HuntDb, PuzzleDb, GuildDb):
        from bot.cogs.puzzles import Puzzles
        import asyncio, discord

        GuildDb.get.return_value = MagicMock()
        GuildDb.get_channel_type.return_value = None
        PuzzleDb.get_by_attr.return_value = None  # no mini found

        bot = MagicMock()
        bot.get_cog.return_value = None
        cog = Puzzles(bot)

        ctx = self._make_thread_ctx(888)
        asyncio.get_event_loop().run_until_complete(cog.cog_before_invoke(ctx))

        assert cog.environment[999]["channel_type"] == "Guild"
```

- [ ] **Step 2: Run — expect failures**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_service.py::TestCogBeforeInvokeThread -q > /tmp/test.log 2>&1
```

Expected: FAIL (AttributeError on `ctx.channel.category`).

- [ ] **Step 3: Fix `cog_before_invoke` in `bot/cogs/puzzles.py`**

Replace the block from `guild = ctx.guild` down to (but not including) the `print` statement with:

```python
        guild = ctx.guild
        guild_data = GuildSettingsDb.get(ctx.guild.id)

        if isinstance(ctx.channel, discord.Thread):
            # Mini-puzzle thread: resolve directly by thread snowflake, no category needed.
            puzzle = PuzzleJsonDb.get_by_attr(channel_id=ctx.channel.id)
            if puzzle:
                channel_type = 'Puzzle'
                hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
                for tag_id in puzzle.tags:
                    r = RoundJsonDb.get_by_attr(id=tag_id)
                    if r:
                        hunt_round = r
                        break
            else:
                channel_type = 'Guild'
        else:
            channel_type = GuildSettingsDb.get_channel_type(ctx.channel.id)
            if channel_type is None:
                channel_type = GuildSettingsDb.get_channel_type(ctx.channel.category.id)
            if channel_type is None:
                channel_type = "Guild"
            if channel_type == 'Hunt':
                hunt = HuntJsonDb.get_by_attr(channel_id=ctx.channel.id,)
            if channel_type == 'Group':
                hunt_round = RoundJsonDb.get_by_attr(category_id=ctx.channel.category.id,)
                if hunt_round:
                    channel_type = hunt_round.type
                    hunt = HuntJsonDb.get_by_attr(id=hunt_round.hunt_id)
                    if hunt_round.meta_id:
                        puzzle = PuzzleJsonDb.get_by_attr(id=hunt_round.meta_id)
            if channel_type == 'Puzzle':
                puzzle = PuzzleJsonDb.get_by_attr(channel_id=ctx.channel.id,)
                if puzzle:
                    hunt_round = RoundJsonDb.get_by_attr(meta_id=puzzle.id)
                    if hunt_round:
                        channel_type = hunt_round.type
                    else:
                        hunt_round = RoundJsonDb.get_by_attr(category_id=ctx.channel.category.id,)
                    hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_service.py::TestCogBeforeInvokeThread -q > /tmp/test.log 2>&1
```

Expected: 2 passed.

- [ ] **Step 5: Full suite**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_minimeta_service.py
git commit -m "M8: thread-aware cog_before_invoke for mini-puzzle threads"
```

---

## Task 4: Service — `make_minimeta` + `get_minis`

**Files:**
- Modify: `bot/core/puzzle_service.py`
- Modify: `tests/test_minimeta_service.py`

- [ ] **Step 1: Add tests**

Append to `tests/test_minimeta_service.py`:

```python
class TestMakeMinimeta:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    def test_sets_puzzle_type(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        puzzle = make_puzzle(id=1)
        assert puzzle.puzzle_type == ""
        svc.make_minimeta(puzzle)
        assert puzzle.puzzle_type == "Minimeta"
        PuzzleDb.commit.assert_called_once_with(puzzle)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    def test_idempotent(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        puzzle = make_puzzle(id=1, puzzle_type="Minimeta")
        svc.make_minimeta(puzzle)
        assert puzzle.puzzle_type == "Minimeta"
        PuzzleDb.commit.assert_called_once()


class TestGetMinis:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    def test_returns_children(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        children = [make_puzzle(id=2), make_puzzle(id=3)]
        PuzzleDb.get_children.return_value = children
        result = svc.get_minis(1)
        PuzzleDb.get_children.assert_called_once_with(1)
        assert result == children

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    def test_empty(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        PuzzleDb.get_children.return_value = []
        assert svc.get_minis(1) == []
```

- [ ] **Step 2: Run — expect failures**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_service.py::TestMakeMinimeta tests/test_minimeta_service.py::TestGetMinis -q > /tmp/test.log 2>&1
```

Expected: FAIL (no such methods).

- [ ] **Step 3: Implement in `bot/core/puzzle_service.py`**

After the `unsolve` area or before `add_sheet`, add:

```python
    def make_minimeta(self, puzzle):
        """Mark a puzzle as a minimeta cluster parent. Idempotent."""
        puzzle.puzzle_type = "Minimeta"
        PuzzleJsonDb.commit(puzzle)

    def get_minis(self, minimeta_id):
        """Return ordered list of mini-puzzle PuzzleData for a minimeta."""
        return PuzzleJsonDb.get_children(minimeta_id)
```

- [ ] **Step 4: Run — expect pass**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_service.py::TestMakeMinimeta tests/test_minimeta_service.py::TestGetMinis -q > /tmp/test.log 2>&1
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_minimeta_service.py
git commit -m "M8: PuzzleService.make_minimeta + get_minis"
```

---

## Task 5: Service — `create_mini`

**Files:**
- Modify: `bot/core/puzzle_service.py`
- Modify: `tests/test_minimeta_service.py`

Creates a thread in the minimeta's channel, a Google Sheet tab, and a `PuzzleData` row with `parent_puzzle_id` set. Tags the mini into the minimeta's round so `refresh_meta` picks up its answer.

- [ ] **Step 1: Add tests**

Append to `tests/test_minimeta_service.py`:

```python
class TestCreateMini:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.RoundJsonDb")
    def test_creates_puzzle_row_with_parent(self, RoundDb, PuzzleDb):
        """create_mini sets parent_puzzle_id, puzzle_type=Mini, tags into minimeta's round."""
        import asyncio, discord
        from bot.core.puzzle_service import PuzzleService

        minimeta = make_puzzle(id=3, hunt_id=10, channel_id=555, tags=[1])
        hunt = _make_hunt(id=10)
        round_ = make_round(id=1)
        RoundDb.get_by_attr.return_value = round_

        # Discord stubs
        guild = MagicMock()
        channel = MagicMock(spec=discord.TextChannel)
        thread = MagicMock(spec=discord.Thread)
        thread.id = 9001
        channel.create_thread = MagicMock(return_value=asyncio.coroutine(lambda: thread)())
        guild.get_channel.return_value = channel

        # Sheet stub
        gsheet = MagicMock()
        gsheet.create_puzzle_spreadsheet = MagicMock(
            return_value=asyncio.coroutine(lambda p: None)()
        )
        bot = MagicMock()
        bot.get_cog.return_value = gsheet

        svc = PuzzleService(bot)
        mini = asyncio.get_event_loop().run_until_complete(
            svc.create_mini(guild, hunt, minimeta, "Tiny Puzzle")
        )

        assert mini.parent_puzzle_id == 3
        assert mini.puzzle_type == "Mini"
        assert mini.channel_id == 9001
        assert 1 in mini.tags
        PuzzleDb.commit.assert_called()

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.RoundJsonDb")
    def test_auto_makes_parent_minimeta(self, RoundDb, PuzzleDb):
        """create_mini auto-sets puzzle_type=Minimeta on parent if not already set."""
        import asyncio, discord
        from bot.core.puzzle_service import PuzzleService

        minimeta = make_puzzle(id=3, hunt_id=10, channel_id=555, tags=[1], puzzle_type="")
        hunt = _make_hunt()
        RoundDb.get_by_attr.return_value = make_round(id=1)

        guild = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        thread.id = 9002
        channel = MagicMock(spec=discord.TextChannel)
        channel.create_thread = MagicMock(return_value=asyncio.coroutine(lambda: thread)())
        guild.get_channel.return_value = channel

        gsheet = MagicMock()
        gsheet.create_puzzle_spreadsheet = MagicMock(
            return_value=asyncio.coroutine(lambda p: None)()
        )
        bot = MagicMock()
        bot.get_cog.return_value = gsheet

        svc = PuzzleService(bot)
        asyncio.get_event_loop().run_until_complete(
            svc.create_mini(guild, hunt, minimeta, "Another Mini")
        )

        assert minimeta.puzzle_type == "Minimeta"
```

- [ ] **Step 2: Run — expect failures**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_service.py::TestCreateMini -q > /tmp/test.log 2>&1
```

Expected: FAIL.

- [ ] **Step 3: Implement `create_mini` in `bot/core/puzzle_service.py`**

Add after `make_minimeta`:

```python
    async def create_mini(self, guild, hunt, minimeta, name, notifier=None):
        """Create a mini-puzzle: thread in minimeta's channel + Google tab + PuzzleData row.

        Tags the mini into the minimeta's round (first tag) so refresh_meta
        rolls up its answer to the minimeta sheet on solve.
        Auto-sets the minimeta's puzzle_type to 'Minimeta' if not already set.
        """
        import discord as _discord
        from bot.cogs.discord_channels import clean_name

        self.make_minimeta(minimeta)

        mini = PuzzleData(
            name=name,
            hunt_id=hunt.id,
            start_time=datetime.datetime.now(tz=pytz.UTC),
            status='Unstarted',
            priority='Normal',
            puzzle_type='Mini',
            parent_puzzle_id=minimeta.id,
        )

        # Tag into the minimeta's first round so refresh_meta fires on solve.
        if minimeta.tags:
            mini.tags.append(minimeta.tags[0])

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.create_puzzle_spreadsheet(mini)

        PuzzleJsonDb.commit(mini)

        try:
            channel = guild.get_channel(minimeta.channel_id)
            thread = await channel.create_thread(
                name=clean_name(name),
                type=_discord.ChannelType.public_thread,
            )
            mini.channel_id = thread.id
            mini.channel_name = thread.name
            PuzzleJsonDb.commit(mini)
        except Exception:
            await self._cleanup_orphan_puzzle(hunt, mini)
            raise

        return mini
```

- [ ] **Step 4: Run — expect pass**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_service.py::TestCreateMini -q > /tmp/test.log 2>&1
```

Expected: 2 passed.

- [ ] **Step 5: Full suite**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```

- [ ] **Step 6: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_minimeta_service.py
git commit -m "M8: PuzzleService.create_mini — thread + sheet + puzzle row"
```

---

## Task 6: Service — `unsolve` + thread archive on mini solve

**Files:**
- Modify: `bot/core/puzzle_service.py`
- Modify: `tests/test_minimeta_service.py`

Extracts `unsolve` logic into the service layer (needed for the web endpoint). Extends `solve` to archive the mini's thread when a mini-puzzle is solved, and extends `unsolve` to unarchive it.

- [ ] **Step 1: Add tests**

Append to `tests/test_minimeta_service.py`:

```python
class TestSolveArchivesThread:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.RoundJsonDb")
    def test_mini_solve_archives_thread(self, RoundDb, PuzzleDb):
        """Solving a mini-puzzle archives its Discord thread."""
        import asyncio, discord
        from bot.core.puzzle_service import PuzzleService

        mini = make_puzzle(id=5, hunt_id=10, channel_id=9001, tags=[], parent_puzzle_id=3)
        hunt = _make_hunt()
        RoundDb.get_by_attr.return_value = None

        thread = MagicMock(spec=discord.Thread)
        thread.edit = MagicMock(return_value=asyncio.coroutine(lambda **kw: None)())
        guild = MagicMock()
        guild.get_channel_or_thread.return_value = thread

        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)

        asyncio.get_event_loop().run_until_complete(
            svc.solve(guild, hunt, mini, "ANSWER")
        )

        thread.edit.assert_called_once_with(archived=True)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.RoundJsonDb")
    def test_regular_puzzle_solve_no_thread_archive(self, RoundDb, PuzzleDb):
        """Solving a regular puzzle does not attempt thread archive."""
        import asyncio
        from bot.core.puzzle_service import PuzzleService

        puzzle = make_puzzle(id=1, hunt_id=10, tags=[])
        hunt = _make_hunt()
        RoundDb.get_by_attr.return_value = None

        guild = MagicMock()
        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)

        asyncio.get_event_loop().run_until_complete(
            svc.solve(guild, hunt, puzzle, "ANSWER")
        )

        guild.get_channel_or_thread.assert_not_called()


class TestUnsolveService:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.RoundJsonDb")
    def test_unsolve_clears_fields(self, RoundDb, PuzzleDb):
        """unsolve resets solved/solution/solve_time/archive_time."""
        import asyncio
        from bot.core.puzzle_service import PuzzleService

        puzzle = make_puzzle(id=1, hunt_id=10, solved=True, solution="ANS", tags=[])
        puzzle.solve_time = "sometime"
        puzzle.archive_time = "sometime"
        hunt = _make_hunt()
        RoundDb.get_by_attr.return_value = None

        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)

        asyncio.get_event_loop().run_until_complete(
            svc.unsolve(None, hunt, puzzle)
        )

        assert puzzle.solved is False
        assert puzzle.solution == ""
        assert puzzle.solve_time is None
        assert puzzle.archive_time is None
        assert puzzle.status == "Unsolved"
        PuzzleDb.commit.assert_called()

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.RoundJsonDb")
    def test_mini_unsolve_unarchives_thread(self, RoundDb, PuzzleDb):
        """unsolve on a mini-puzzle unarchives its thread."""
        import asyncio, discord
        from bot.core.puzzle_service import PuzzleService

        mini = make_puzzle(id=5, solved=True, solution="X", tags=[], parent_puzzle_id=3, channel_id=9001)
        mini.solve_time = "t"
        mini.archive_time = "t"
        hunt = _make_hunt()
        RoundDb.get_by_attr.return_value = None

        thread = MagicMock(spec=discord.Thread)
        thread.edit = MagicMock(return_value=asyncio.coroutine(lambda **kw: None)())
        guild = MagicMock()
        guild.get_channel_or_thread.return_value = thread

        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)

        asyncio.get_event_loop().run_until_complete(
            svc.unsolve(guild, hunt, mini)
        )

        thread.edit.assert_called_once_with(archived=False)
```

- [ ] **Step 2: Run — expect failures**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_service.py::TestSolveArchivesThread tests/test_minimeta_service.py::TestUnsolveService -q > /tmp/test.log 2>&1
```

Expected: FAIL.

- [ ] **Step 3: Extend `PuzzleService.solve` to archive thread**

In `bot/core/puzzle_service.py`, in the `solve` method, after `PuzzleJsonDb.commit(puzzle)` (the first one, before `for tag in puzzle.tags`) add:

```python
        # M8: archive the mini-puzzle thread on solve.
        if puzzle.parent_puzzle_id is not None and guild is not None:
            thread = guild.get_channel_or_thread(puzzle.channel_id)
            if thread is not None:
                try:
                    await thread.edit(archived=True)
                except Exception:
                    logger.exception("Failed to archive mini-puzzle thread %s on solve", puzzle.channel_id)
```

- [ ] **Step 4: Add `PuzzleService.unsolve`**

In `bot/core/puzzle_service.py`, after the `solve` method, add:

```python
    async def unsolve(self, guild, hunt, puzzle):
        """Clear a puzzle's solve state and restore its sheet.

        For mini-puzzles (parent_puzzle_id set), also unarchives the thread.
        """
        prev_solution = puzzle.solution
        puzzle.status = "Unsolved"
        puzzle.solution = ""
        puzzle.solved = False
        puzzle.solve_time = None
        puzzle.archive_time = None
        PuzzleJsonDb.commit(puzzle)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.restore_puzzle_spreadsheet(puzzle)

        # Refresh metas so the unsolved answer disappears from the meta sheet.
        for tag in puzzle.tags:
            try:
                meta_round = RoundJsonDb.get_by_attr(id=tag)
                if meta_round is not None:
                    await self.refresh_meta(hunt, meta_round)
            except Exception:
                logger.exception("unsolve: meta refresh failed for round %s", tag)

        # M8: unarchive the mini-puzzle thread.
        if puzzle.parent_puzzle_id is not None and guild is not None:
            thread = guild.get_channel_or_thread(puzzle.channel_id)
            if thread is not None:
                try:
                    await thread.edit(archived=False)
                except Exception:
                    logger.exception("Failed to unarchive mini-puzzle thread %s", puzzle.channel_id)

        # Mirror round-solve undo if this puzzle was a round meta.
        for tag in puzzle.tags:
            round_ = RoundJsonDb.get_by_attr(id=tag)
            if round_ and round_.meta_id == puzzle.id:
                round_.solve_time = None
                round_.status = "active"
                RoundJsonDb.commit(round_)
```

- [ ] **Step 5: Run — expect pass**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_service.py::TestSolveArchivesThread tests/test_minimeta_service.py::TestUnsolveService -q > /tmp/test.log 2>&1
```

Expected: 4 passed.

- [ ] **Step 6: Full suite**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```

- [ ] **Step 7: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_minimeta_service.py
git commit -m "M8: PuzzleService.unsolve + thread archive/unarchive on mini solve"
```

---

## Task 7: Service — `solve_mini_by_number` + `unsolve_mini_by_number`

**Files:**
- Modify: `bot/core/puzzle_service.py`
- Modify: `tests/test_minimeta_service.py`

- [ ] **Step 1: Add tests**

Append to `tests/test_minimeta_service.py`:

```python
class TestSolveMiniByNumber:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.RoundJsonDb")
    def test_resolves_by_number_and_delegates(self, RoundDb, PuzzleDb):
        import asyncio
        from bot.core.puzzle_service import PuzzleService
        from unittest.mock import AsyncMock

        mini1 = make_puzzle(id=10, parent_puzzle_id=3, tags=[])
        mini2 = make_puzzle(id=11, parent_puzzle_id=3, tags=[])
        PuzzleDb.get_children.return_value = [mini1, mini2]
        RoundDb.get_by_attr.return_value = None

        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)
        svc.solve = AsyncMock(return_value=True)

        asyncio.get_event_loop().run_until_complete(
            svc.solve_mini_by_number(None, _make_hunt(), 3, 2, "ANSWER")
        )

        svc.solve.assert_called_once()
        called_puzzle = svc.solve.call_args[0][2]
        assert called_puzzle.id == 11  # 1-indexed: number 2 → index 1

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    def test_raises_on_bad_number(self, PuzzleDb):
        import asyncio
        from bot.core.puzzle_service import PuzzleService

        PuzzleDb.get_children.return_value = [make_puzzle(id=10, parent_puzzle_id=3)]
        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)

        with pytest.raises(ValueError, match="No mini #5"):
            asyncio.get_event_loop().run_until_complete(
                svc.solve_mini_by_number(None, _make_hunt(), 3, 5, "X")
            )


class TestUnsolveMiniByNumber:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.RoundJsonDb")
    def test_resolves_and_delegates(self, RoundDb, PuzzleDb):
        import asyncio
        from bot.core.puzzle_service import PuzzleService
        from unittest.mock import AsyncMock

        mini = make_puzzle(id=10, parent_puzzle_id=3, solved=True, solution="ANS", tags=[])
        PuzzleDb.get_children.return_value = [mini]
        RoundDb.get_by_attr.return_value = None

        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)
        svc.unsolve = AsyncMock()

        asyncio.get_event_loop().run_until_complete(
            svc.unsolve_mini_by_number(None, _make_hunt(), 3, 1)
        )

        svc.unsolve.assert_called_once()
        assert svc.unsolve.call_args[0][2].id == 10
```

- [ ] **Step 2: Run — expect failures**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_service.py::TestSolveMiniByNumber tests/test_minimeta_service.py::TestUnsolveMiniByNumber -q > /tmp/test.log 2>&1
```

Expected: FAIL.

- [ ] **Step 3: Implement in `bot/core/puzzle_service.py`**

After `unsolve`, add:

```python
    async def solve_mini_by_number(self, guild, hunt, minimeta_id, number, solution):
        """Solve the Nth mini-puzzle (1-indexed) under the given minimeta puzzle id."""
        minis = self.get_minis(minimeta_id)
        if number < 1 or number > len(minis):
            raise ValueError(f"No mini #{number} — {len(minis)} mini(s) exist")
        return await self.solve(guild, hunt, minis[number - 1], solution)

    async def unsolve_mini_by_number(self, guild, hunt, minimeta_id, number):
        """Unsolve the Nth mini-puzzle (1-indexed) under the given minimeta puzzle id."""
        minis = self.get_minis(minimeta_id)
        if number < 1 or number > len(minis):
            raise ValueError(f"No mini #{number} — {len(minis)} mini(s) exist")
        await self.unsolve(guild, hunt, minis[number - 1])
```

- [ ] **Step 4: Run — expect pass**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_service.py::TestSolveMiniByNumber tests/test_minimeta_service.py::TestUnsolveMiniByNumber -q > /tmp/test.log 2>&1
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_minimeta_service.py
git commit -m "M8: solve_mini_by_number + unsolve_mini_by_number"
```

---

## Task 8: Bang commands

**Files:**
- Modify: `bot/cogs/puzzles.py`
- Create: `tests/test_minimeta_commands.py`

Add five commands. All live in the `Puzzles` cog after the existing `list_additional_sheets` command.

- [ ] **Step 1: Write failing tests**

Create `tests/test_minimeta_commands.py`:

```python
"""Tests for M8 minimeta bang commands."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from tests.conftest import make_puzzle, make_round


def _make_ctx(puzzle, channel_id=111, message_id=1):
    ctx = MagicMock()
    ctx.channel.id = channel_id
    ctx.message.id = message_id
    ctx.guild = MagicMock()
    return ctx


def _make_cog(puzzle, hunt=None, hunt_round=None):
    from bot.cogs.puzzles import Puzzles
    bot = MagicMock()
    bot.get_cog.return_value = None
    cog = Puzzles(bot)
    cog.environment[1] = {
        "puzzle": puzzle,
        "hunt": hunt or MagicMock(),
        "hunt_round": hunt_round,
        "guild": MagicMock(),
        "guild_data": MagicMock(),
        "channel_type": "Puzzle",
        "gsheet_cog": None,
    }
    return cog


class TestMakeMiniMeta:
    def test_sets_puzzle_type_and_confirms(self):
        puzzle = make_puzzle(id=1)
        cog = _make_cog(puzzle)
        ctx = _make_ctx(puzzle)
        ctx.send = AsyncMock()
        cog.service = MagicMock()
        cog.service.make_minimeta = MagicMock()

        asyncio.get_event_loop().run_until_complete(cog.make_minimeta(ctx))

        cog.service.make_minimeta.assert_called_once_with(puzzle)
        ctx.send.assert_called_once()
        assert "minimeta" in ctx.send.call_args[0][0].lower()


class TestListMinis:
    def test_empty_list(self):
        puzzle = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = _make_cog(puzzle)
        ctx = _make_ctx(puzzle)
        ctx.send = AsyncMock()
        cog.service = MagicMock()
        cog.service.get_minis = MagicMock(return_value=[])

        asyncio.get_event_loop().run_until_complete(cog.list_minis(ctx))

        ctx.send.assert_called_once()
        assert "no mini" in ctx.send.call_args[0][0].lower()

    def test_shows_minis_with_solve_state(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        mini1 = make_puzzle(id=2, name="Mini A", solved=False)
        mini2 = make_puzzle(id=3, name="Mini B", solved=True, solution="ANS")
        cog = _make_cog(parent)
        ctx = _make_ctx(parent)
        ctx.send = AsyncMock()
        cog.service = MagicMock()
        cog.service.get_minis = MagicMock(return_value=[mini1, mini2])

        asyncio.get_event_loop().run_until_complete(cog.list_minis(ctx))

        ctx.send.assert_called_once()
        # The embed or message should mention both mini names
        sent = str(ctx.send.call_args)
        assert "Mini A" in sent
        assert "Mini B" in sent


class TestSolveMiniCommand:
    def test_delegates_to_service(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = _make_cog(parent)
        ctx = _make_ctx(parent)
        ctx.send = AsyncMock()
        cog.service = MagicMock()
        cog.service.solve_mini_by_number = AsyncMock(return_value=True)

        asyncio.get_event_loop().run_until_complete(cog.solve_mini(ctx, 2, "ANSWER"))

        cog.service.solve_mini_by_number.assert_called_once()
        args = cog.service.solve_mini_by_number.call_args[0]
        assert args[3] == 2
        assert args[4] == "ANSWER"
        ctx.send.assert_called_once()
        assert "#2" in ctx.send.call_args[0][0]

    def test_bad_number_sends_error(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = _make_cog(parent)
        ctx = _make_ctx(parent)
        ctx.send = AsyncMock()
        cog.service = MagicMock()
        cog.service.solve_mini_by_number = AsyncMock(side_effect=ValueError("No mini #9"))

        asyncio.get_event_loop().run_until_complete(cog.solve_mini(ctx, 9, "X"))

        ctx.send.assert_called_once()
        assert ":x:" in ctx.send.call_args[0][0]


class TestUnsolveMiniCommand:
    def test_delegates_to_service(self):
        parent = make_puzzle(id=1, puzzle_type="Minimeta")
        cog = _make_cog(parent)
        ctx = _make_ctx(parent)
        ctx.send = AsyncMock()
        cog.service = MagicMock()
        cog.service.unsolve_mini_by_number = AsyncMock()

        asyncio.get_event_loop().run_until_complete(cog.unsolve_mini(ctx, 1))

        cog.service.unsolve_mini_by_number.assert_called_once()
        ctx.send.assert_called_once()
```

- [ ] **Step 2: Run — expect failures**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_commands.py -q > /tmp/test.log 2>&1
```

Expected: FAIL (commands don't exist).

- [ ] **Step 3: Add commands to `bot/cogs/puzzles.py`**

After `list_additional_sheets`, add:

```python
    @commands.command()
    async def make_minimeta(self, ctx):
        """*Convert this puzzle into a minimeta cluster: !make_minimeta*"""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        self.service.make_minimeta(puzzle)
        await ctx.send(f":white_check_mark: **{puzzle.name}** is now a minimeta cluster. Use `!add_mini` to add mini-puzzles.")

    @commands.command()
    @with_puzzle_mutex(wait=True)
    async def add_mini(self, ctx, *args):
        """*Add a mini-puzzle (thread + sheet) to this minimeta: !add_mini Name*"""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        name = " ".join(args).strip()
        if not name:
            return await ctx.send(":x: Provide a name: `!add_mini Puzzle Name`")
        try:
            mini = await self.service.create_mini(
                ctx.guild, self.get_hunt(ctx), puzzle, name
            )
        except RuntimeError as exc:
            return await ctx.send(f":x: Could not create mini-puzzle: {exc}")
        await ctx.send(f":white_check_mark: Created mini-puzzle **{mini.name}**. Thread: <#{mini.channel_id}>")

    @commands.command()
    async def list_minis(self, ctx):
        """*List mini-puzzles for this minimeta: !list_minis*"""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        minis = self.service.get_minis(puzzle.id)
        if not minis:
            return await ctx.send(":x: No mini-puzzles found. Use `!add_mini` to add one.")
        embed = discord.Embed(title=f"Mini-puzzles for {puzzle.name}")
        lines = []
        for i, mini in enumerate(minis, 1):
            if mini.solved:
                lines.append(f"{i}. ✅ **{mini.name}** — {mini.solution}")
            else:
                lines.append(f"{i}. ⬜ **{mini.name}** — unsolved")
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @commands.command()
    @with_puzzle_mutex(wait=True)
    async def solve_mini(self, ctx, number: int, *args):
        """*Solve mini-puzzle #N from the minimeta channel: !solve_mini 2 ANSWER*"""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        solution = " ".join(args).strip()
        if not solution:
            return await ctx.send(":x: Provide a solution: `!solve_mini 2 ANSWER`")
        try:
            await self.service.solve_mini_by_number(
                ctx.guild, self.get_hunt(ctx), puzzle.id, number, solution
            )
        except ValueError as exc:
            return await ctx.send(f":x: {exc}")
        await ctx.send(f":white_check_mark: Solved mini #{number}: **{solution}**")

    @commands.command()
    @with_puzzle_mutex(wait=True)
    async def unsolve_mini(self, ctx, number: int):
        """*Unsolve mini-puzzle #N from the minimeta channel: !unsolve_mini 2*"""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        try:
            await self.service.unsolve_mini_by_number(
                ctx.guild, self.get_hunt(ctx), puzzle.id, number
            )
        except ValueError as exc:
            return await ctx.send(f":x: {exc}")
        await ctx.send(f":white_check_mark: Mini #{number} unmarked as solved.")
```

- [ ] **Step 4: Run tests — expect pass**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_commands.py -q > /tmp/test.log 2>&1
```

Expected: all passed.

- [ ] **Step 5: Full suite**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```

- [ ] **Step 6: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_minimeta_commands.py
git commit -m "M8: bang commands — make_minimeta, add_mini, list_minis, solve_mini, unsolve_mini"
```

---

## Task 9: Web API — mini_puzzles payload + create + unsolve endpoints

**Files:**
- Modify: `bot/web/data.py`
- Modify: `bot/web/actions.py`
- Modify: `tests/test_minimeta_service.py` (add web API tests)

- [ ] **Step 1: Add tests**

Append to `tests/test_minimeta_service.py`:

```python
class TestWebDataMinisPayload:
    def test_mini_puzzles_excluded_from_main_list(self):
        """Puzzles with parent_puzzle_id must not appear in the main puzzle list."""
        # This is enforced by a WHERE clause; verify the query string exists.
        import inspect
        from bot.web import data
        src = inspect.getsource(data.get_puzzles_for_hunt)
        assert "parent_puzzle_id IS NULL" in src

    def test_minimeta_payload_shape(self):
        """Minimeta puzzle payload must include a mini_puzzles key."""
        # Tested via the query structure
        import inspect
        from bot.web import data
        src = inspect.getsource(data.get_puzzles_for_hunt)
        assert "mini_puzzles" in src
```

- [ ] **Step 2: Run — expect failures**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_service.py::TestWebDataMinisPayload -q > /tmp/test.log 2>&1
```

Expected: FAIL.

- [ ] **Step 3: Update `bot/web/data.py`**

In `get_puzzles_for_hunt`, locate the main puzzle query and add `AND puzzles.parent_puzzle_id IS NULL` to the WHERE clause:

```python
        "WHERE puzzles.hunt_id = %s AND puzzles.parent_puzzle_id IS NULL "
```

Add `parent_puzzle_id` to the puzzle dict (so the frontend knows the field exists even though it's always NULL for these rows):

```python
            puz = {
                ...
                "parent_puzzle_id": row.get("parent_puzzle_id"),
                "mini_puzzles": [],
                ...
            }
```

After the `sheet_rows` join block (end of the function, before `return`), add a mini-puzzles fetch:

```python
    mini_rows = await fetch_all(
        "SELECT id, name, solved, solution, channel_id, parent_puzzle_id "
        "FROM puzzles WHERE hunt_id = %s AND parent_puzzle_id IS NOT NULL "
        "ORDER BY id ASC",
        (hunt_id,),
    )
    for m in mini_rows:
        parent = puzzles.get(m["parent_puzzle_id"])
        if parent is not None:
            parent["mini_puzzles"].append({
                "id": m["id"],
                "name": m["name"],
                "solved": bool(m["solved"]),
                "solution": m["solution"],
                "channel_id": str(m["channel_id"]) if m["channel_id"] else None,
            })
```

- [ ] **Step 4: Add `POST /api/puzzles/<id>/minis` to `bot/web/actions.py`**

After the `solve_puzzle` route, add:

```python
@actions_bp.route("/puzzles/<int:puzzle_id>/minis", methods=["POST"])
@login_required
async def create_mini(puzzle_id):
    """Create a mini-puzzle (thread + sheet) under an existing minimeta."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403

    body = await request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "empty_name"}), 400

    from bot.store import HuntJsonDb, PuzzleJsonDb
    from bot.web.auth import ORGANISER_ROLES
    err = await require_organiser(ctx.get("guild_snowflake"), ORGANISER_ROLES)
    if err:
        return err

    async with puzzle_lock(puzzle_id):
        minimeta = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if minimeta is None:
            return jsonify({"error": "puzzle_not_found"}), 404

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        bot = current_app.bot
        cog = bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503
        guild = bot.get_guild(int(ctx["guild_snowflake"])) if ctx.get("guild_snowflake") else None

        try:
            mini = await cog.service.create_mini(guild, hunt_obj, minimeta, name)
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 400

    await _audit("create_mini", puzzle_id=mini.id, hunt_uid=ctx["hunt"]["uid"], detail=name)
    _notify_board(ctx["hunt"]["uid"], ctx.get("guild_snowflake"))
    return jsonify({"ok": True, "mini_id": mini.id, "name": mini.name, "channel_id": str(mini.channel_id)})
```

- [ ] **Step 5: Add `POST /api/puzzles/<id>/unsolve` to `bot/web/actions.py`**

After the `create_mini` route, add:

```python
@actions_bp.route("/puzzles/<int:puzzle_id>/unsolve", methods=["POST"])
@login_required
async def unsolve_puzzle(puzzle_id):
    """Unsolve a puzzle (or mini-puzzle) via the dashboard."""
    ctx, err = await _visible_hunt_for_puzzle(puzzle_id)
    if err:
        return err
    if ctx["hunt"]["archived"]:
        return jsonify({"error": "hunt_archived"}), 403

    from bot.store import HuntJsonDb, PuzzleJsonDb

    async with puzzle_lock(puzzle_id):
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            return jsonify({"error": "puzzle_not_found"}), 404
        if not puzzle.solved and not puzzle.solution:
            return jsonify({"error": "not_solved"}), 400

        hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
        bot = current_app.bot
        cog = bot.get_cog("Puzzles")
        if cog is None:
            return jsonify({"error": "bot_unavailable"}), 503
        snowflake = ctx.get("guild_snowflake")
        guild = bot.get_guild(int(snowflake)) if snowflake else None

        try:
            await cog.service.unsolve(guild, hunt_obj, puzzle)
        except (discord.RateLimited, discord.HTTPException) as exc:
            return _discord_error_response(exc)

    await _audit("unsolve", puzzle_id=puzzle.id, hunt_uid=ctx["hunt"]["uid"])
    _notify_board(ctx["hunt"]["uid"], ctx.get("guild_snowflake"))
    return jsonify({"ok": True})
```

- [ ] **Step 6: Run tests**

```bash
timeout 60 pipenv run python -m pytest tests/test_minimeta_service.py::TestWebDataMinisPayload -q > /tmp/test.log 2>&1
```

Expected: 2 passed.

- [ ] **Step 7: Full suite**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```

- [ ] **Step 8: Commit**

```bash
git add bot/web/data.py bot/web/actions.py tests/test_minimeta_service.py
git commit -m "M8: web API — mini_puzzles in payload, POST /minis, POST /unsolve"
```

---

## Task 10: Frontend — minimeta expand panel

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Create: `frontend/src/components/MiniPuzzlePanel.tsx`
- Modify: `frontend/src/components/PuzzleTable.tsx`

- [ ] **Step 1: Update `frontend/src/types.ts`**

After `AdditionalSheet`, add:

```typescript
export interface MiniPuzzle {
  id: number;
  name: string;
  solved: boolean;
  solution: string | null;
  channel_id: string | null;
}
```

In `Puzzle`, add after `sheets`:

```typescript
  mini_puzzles: MiniPuzzle[];
```

- [ ] **Step 2: Add hooks to `frontend/src/api.ts`**

After `useSolvePuzzle`, add:

```typescript
export function useUnsolvePuzzle(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId }: { puzzleId: number }) =>
      apiSend<{ ok: boolean }>(`/api/puzzles/${puzzleId}/unsolve`, "POST"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hunt", uid] });
    },
  });
}

export function useCreateMini(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId, name }: { puzzleId: number; name: string }) =>
      apiSend<{ ok: boolean; mini_id: number; name: string; channel_id: string }>(
        `/api/puzzles/${puzzleId}/minis`,
        "POST",
        { name }
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hunt", uid] });
    },
  });
}
```

- [ ] **Step 3: Create `frontend/src/components/MiniPuzzlePanel.tsx`**

```typescript
import { useState } from "react";
import { ActionIcon, Badge, Box, Button, Group, Stack, Text, TextInput, Tooltip } from "@mantine/core";
import { IconCheck, IconX } from "@tabler/icons-react";
import type { MiniPuzzle } from "../types";
import { useCreateMini, useSolvePuzzle, useUnsolvePuzzle } from "../api";

interface Props {
  parentId: number;
  minis: MiniPuzzle[];
  uid: string;
  huntSheetId?: string | null;
}

export function MiniPuzzlePanel({ parentId, minis, uid, huntSheetId }: Props) {
  const [newName, setNewName] = useState("");
  const [solveInputs, setSolveInputs] = useState<Record<number, string>>({});
  const createMini = useCreateMini(uid);
  const solvePuzzle = useSolvePuzzle(uid);
  const unsolvePuzzle = useUnsolvePuzzle(uid);

  function setSolveInput(id: number, val: string) {
    setSolveInputs((prev) => ({ ...prev, [id]: val }));
  }

  return (
    <Box p="xs" bg="var(--mantine-color-gray-0)" style={{ borderTop: "1px solid var(--mantine-color-gray-3)" }}>
      <Stack gap="xs">
        {minis.length === 0 && (
          <Text size="sm" c="dimmed">No mini-puzzles yet.</Text>
        )}
        {minis.map((mini, i) => (
          <Group key={mini.id} gap="xs" wrap="nowrap">
            <Text size="sm" fw={500} style={{ minWidth: 24 }}>{i + 1}.</Text>
            <Text size="sm" style={{ flex: 1 }}>{mini.name}</Text>
            {mini.solved ? (
              <>
                <Badge color="green" variant="light">{mini.solution}</Badge>
                <Tooltip label="Unsolve">
                  <ActionIcon
                    size="sm"
                    variant="subtle"
                    color="red"
                    onClick={() => unsolvePuzzle.mutate({ puzzleId: mini.id })}
                    loading={unsolvePuzzle.isPending}
                  >
                    <IconX size={14} />
                  </ActionIcon>
                </Tooltip>
              </>
            ) : (
              <>
                <TextInput
                  size="xs"
                  placeholder="Answer…"
                  value={solveInputs[mini.id] ?? ""}
                  onChange={(e) => setSolveInput(mini.id, e.currentTarget.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && solveInputs[mini.id]) {
                      solvePuzzle.mutate({ puzzleId: mini.id, solution: solveInputs[mini.id] });
                      setSolveInput(mini.id, "");
                    }
                  }}
                  style={{ width: 160 }}
                />
                <Tooltip label="Mark solved">
                  <ActionIcon
                    size="sm"
                    variant="subtle"
                    color="green"
                    disabled={!solveInputs[mini.id]}
                    onClick={() => {
                      solvePuzzle.mutate({ puzzleId: mini.id, solution: solveInputs[mini.id] });
                      setSolveInput(mini.id, "");
                    }}
                  >
                    <IconCheck size={14} />
                  </ActionIcon>
                </Tooltip>
              </>
            )}
          </Group>
        ))}

        <Group gap="xs">
          <TextInput
            size="xs"
            placeholder="New mini-puzzle name…"
            value={newName}
            onChange={(e) => setNewName(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && newName.trim()) {
                createMini.mutate({ puzzleId: parentId, name: newName.trim() });
                setNewName("");
              }
            }}
            style={{ flex: 1 }}
          />
          <Button
            size="xs"
            disabled={!newName.trim()}
            loading={createMini.isPending}
            onClick={() => {
              createMini.mutate({ puzzleId: parentId, name: newName.trim() });
              setNewName("");
            }}
          >
            Add Mini
          </Button>
        </Group>
      </Stack>
    </Box>
  );
}
```

- [ ] **Step 4: Update `frontend/src/components/PuzzleTable.tsx`**

Add expand state near the top of the component (after the existing `solvePuzzle` state):

```typescript
  const [expandedMinis, setExpandedMinis] = useState<Set<number>>(new Set());
  function toggleMinis(id: number) {
    setExpandedMinis((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
```

Import `MiniPuzzlePanel`:

```typescript
import { MiniPuzzlePanel } from "./MiniPuzzlePanel";
```

In the table row for each puzzle `p`, locate where `sheets` badges are rendered. After (or replacing) the additional-sheets badge for minimeta-type puzzles, add:

```typescript
{p.puzzle_type === "Minimeta" && (
  <Tooltip label={`${p.mini_puzzles.filter(m => m.solved).length}/${p.mini_puzzles.length} minis solved`}>
    <ActionIcon
      size="sm"
      variant="subtle"
      onClick={() => toggleMinis(p.id)}
    >
      <Badge color="violet" variant="light">
        {p.mini_puzzles.filter(m => m.solved).length}/{p.mini_puzzles.length}
      </Badge>
    </ActionIcon>
  </Tooltip>
)}
```

After the `<Table.Tr>` closing tag for the puzzle row, add a conditional expand row:

```typescript
{p.puzzle_type === "Minimeta" && expandedMinis.has(p.id) && (
  <Table.Tr key={`minis-${p.id}`}>
    <Table.Td colSpan={99} p={0}>
      <MiniPuzzlePanel
        parentId={p.id}
        minis={p.mini_puzzles}
        uid={detail.hunt.uid}
      />
    </Table.Td>
  </Table.Tr>
)}
```

- [ ] **Step 5: Build frontend and check for TypeScript errors**

```bash
cd frontend && npm run build 2>&1 | tail -30
```

Expected: build succeeds with no type errors. Fix any type errors before continuing.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/components/MiniPuzzlePanel.tsx frontend/src/components/PuzzleTable.tsx
git commit -m "M8: dashboard — minimeta expand panel with mini list, solve, add"
```

---

## Verification

- [ ] Run full test suite one final time:

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```

Expected: all tests pass.

- [ ] End-to-end smoke test (requires running bot + DB):
  1. Create a puzzle in a hunt
  2. `!make_minimeta` → confirms minimeta type
  3. `!add_mini Tiny Puzzle 1` → thread created in channel, confirm in Discord
  4. `!add_mini Tiny Puzzle 2` → second thread
  5. `!list_minis` → shows 2 unsolved minis
  6. `!solve_mini 1 ANSWER` → mini solved, thread archived, minimeta sheet updated
  7. `!solve ANSWER2` from Tiny Puzzle 2's thread → same result
  8. Dashboard: minimeta row shows `1/2` badge, expand shows both minis
  9. Add mini from dashboard → new thread appears
  10. Solve from dashboard → mini marked solved

- [ ] Update PLANS.md to mark M8 done (add ✅ + commit hash after the tasks commit)
