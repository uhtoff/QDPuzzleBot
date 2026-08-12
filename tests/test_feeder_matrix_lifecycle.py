"""Lifecycle wiring for the feeder matrix (Plan 2, Task 8): refresh_feeder_matrix
must follow every pool-mutating action, and a Sheets hiccup there must never turn
a successful mutation into a failure.

Mocks svc.refresh_feeder_matrix directly on the service instance (mirrors the
existing svc.refresh_minimeta mocking style throughout
tests/test_minimeta_service.py) -- refresh_feeder_matrix's OWN orchestration
(guards, tab-id persistence, badge bookkeeping) is covered by
tests/test_feeder_matrix_service.py; this file only proves the call sites wire
it in, sequentially with refresh_minimeta, never nested inside it.
"""
import discord
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from tests.conftest import make_hunt, make_puzzle


def _mini(pid=101, parent_id=100):
    return make_puzzle(
        id=pid, name=f"Mini {pid}", puzzle_type="Mini", parent_puzzle_id=parent_id, tags=[])


def _create_mini_scenario():
    """A minimeta + hunt + guild/thread/gsheet stub sufficient to run
    create_mini end to end, matching
    tests/test_minimeta_service.py::TestCreateMini's fixture."""
    minimeta = make_puzzle(id=3, hunt_id=10, channel_id=555, tags=[])
    hunt = make_hunt(id=10)

    guild = MagicMock()
    thread = MagicMock(spec=discord.Thread)
    thread.id = 9001
    thread.name = "tiny-puzzle"
    channel = MagicMock(spec=discord.TextChannel)

    async def fake_create_thread(**kwargs):
        return thread
    channel.create_thread = fake_create_thread
    guild.get_channel.return_value = channel

    gsheet = MagicMock()

    async def fake_create_sheet(p, **kwargs):
        return None
    gsheet.create_puzzle_spreadsheet = fake_create_sheet
    gsheet.compute_insert_index = AsyncMock(return_value=0)
    bot = MagicMock()
    bot.get_cog.return_value = gsheet

    svc = PuzzleService(bot)
    return svc, minimeta, hunt, guild


class TestCreateMiniWiring:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_create_mini_refreshes_the_matrix_after_the_mini_list(self, _PuzzleDb):
        svc, minimeta, hunt, guild = _create_mini_scenario()
        order = []
        svc.refresh_minimeta = AsyncMock(side_effect=lambda *a: order.append("minimeta"))
        svc.refresh_feeder_matrix = AsyncMock(side_effect=lambda *a: order.append("matrix"))

        await svc.create_mini(guild, hunt, minimeta, "Tiny Puzzle")

        svc.refresh_feeder_matrix.assert_awaited_once_with(hunt, minimeta)
        # Sequential, not nested: refresh_minimeta's own lock is released before
        # refresh_feeder_matrix (same lock key) is even entered.
        assert order == ["minimeta", "matrix"]

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_a_matrix_render_failure_does_not_fail_the_create(self, _PuzzleDb):
        svc, minimeta, hunt, guild = _create_mini_scenario()
        svc.refresh_minimeta = AsyncMock()
        svc.refresh_feeder_matrix = AsyncMock(side_effect=RuntimeError("sheets down"))

        mini = await svc.create_mini(guild, hunt, minimeta, "Tiny Puzzle")   # must not raise

        assert mini.parent_puzzle_id == 3
        svc.refresh_feeder_matrix.assert_awaited_once()


class TestDeleteMiniWiring:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_delete_mini_refreshes_the_matrix_after_the_mini_list(self, PuzzleDb):
        minimeta = make_puzzle(id=1, puzzle_type="Minimeta")
        mini = make_puzzle(id=2, parent_puzzle_id=1, tags=[])
        PuzzleDb.get_by_attr.return_value = minimeta
        svc = PuzzleService(MagicMock())
        svc.delete_puzzle = AsyncMock()
        order = []
        svc.refresh_minimeta = AsyncMock(side_effect=lambda *a: order.append("minimeta"))
        svc.refresh_feeder_matrix = AsyncMock(side_effect=lambda *a: order.append("matrix"))
        hunt = make_hunt(id=10)

        await svc.delete_mini(MagicMock(), hunt, mini)

        svc.refresh_feeder_matrix.assert_awaited_once_with(hunt, minimeta)
        assert order == ["minimeta", "matrix"]

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_a_matrix_render_failure_does_not_fail_the_delete(self, PuzzleDb):
        minimeta = make_puzzle(id=1, puzzle_type="Minimeta")
        mini = make_puzzle(id=2, parent_puzzle_id=1, tags=[])
        PuzzleDb.get_by_attr.return_value = minimeta
        svc = PuzzleService(MagicMock())
        svc.delete_puzzle = AsyncMock()
        svc.refresh_minimeta = AsyncMock()
        svc.refresh_feeder_matrix = AsyncMock(side_effect=RuntimeError("sheets down"))

        returned = await svc.delete_mini(MagicMock(), make_hunt(), mini)   # must not raise

        assert returned is minimeta


class TestSolveUnsolveMatrixWiring:
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    async def test_mini_solve_refreshes_the_matrix_for_the_parent(self, _RoundDb, PuzzleDb):
        svc = PuzzleService(MagicMock())
        svc.bot.get_cog.return_value = None   # skip the sheet-archive branch
        mini = _mini()
        parent = make_puzzle(id=100, puzzle_type="Minimeta")
        PuzzleDb.get_by_attr.return_value = parent
        svc.refresh_minimeta = AsyncMock()
        svc.refresh_feeder_matrix = AsyncMock()

        await svc.solve(None, make_hunt(), mini, "ANS")

        svc.refresh_feeder_matrix.assert_awaited_once_with(make_hunt(), parent)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    async def test_a_matrix_render_failure_does_not_fail_the_solve(self, _RoundDb, PuzzleDb):
        svc = PuzzleService(MagicMock())
        svc.bot.get_cog.return_value = None
        mini = _mini()
        parent = make_puzzle(id=100, puzzle_type="Minimeta")
        PuzzleDb.get_by_attr.return_value = parent
        svc.refresh_minimeta = AsyncMock()
        svc.refresh_feeder_matrix = AsyncMock(side_effect=RuntimeError("sheets down"))

        applied = await svc.solve(None, make_hunt(), mini, "ANS")   # must not raise

        assert applied is True

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    async def test_regular_solve_does_not_touch_the_matrix(self, _RoundDb, PuzzleDb):
        svc = PuzzleService(MagicMock())
        svc.bot.get_cog.return_value = None
        puzzle = make_puzzle(id=1, tags=[])   # no parent_puzzle_id
        svc.refresh_minimeta = AsyncMock()
        svc.refresh_feeder_matrix = AsyncMock()

        await svc.solve(None, make_hunt(), puzzle, "ANS")

        svc.refresh_feeder_matrix.assert_not_awaited()

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    async def test_mini_unsolve_refreshes_the_matrix_for_the_parent(self, _RoundDb, PuzzleDb):
        svc = PuzzleService(MagicMock())
        svc.bot.get_cog.return_value = None
        mini = _mini()
        mini.solved = True
        mini.solution = "ANS"
        parent = make_puzzle(id=100, puzzle_type="Minimeta")
        PuzzleDb.get_by_attr.return_value = parent
        svc.refresh_minimeta = AsyncMock()
        svc.refresh_feeder_matrix = AsyncMock()

        await svc.unsolve(None, make_hunt(), mini)

        svc.refresh_feeder_matrix.assert_awaited_once_with(make_hunt(), parent)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    async def test_a_matrix_render_failure_does_not_fail_the_unsolve(self, _RoundDb, PuzzleDb):
        svc = PuzzleService(MagicMock())
        svc.bot.get_cog.return_value = None
        mini = _mini()
        mini.solved = True
        mini.solution = "ANS"
        parent = make_puzzle(id=100, puzzle_type="Minimeta")
        PuzzleDb.get_by_attr.return_value = parent
        svc.refresh_minimeta = AsyncMock()
        svc.refresh_feeder_matrix = AsyncMock(side_effect=RuntimeError("sheets down"))

        await svc.unsolve(None, make_hunt(), mini)   # must not raise


class TestSyncClaimsCommandFiresTheSafeWrapper:
    """The five CRUD commands fire refresh_feeder_matrix_safe via
    asyncio.create_task, never awaited directly -- mirrors refresh_unassigned_safe's
    established fire-and-forget pattern (bot/cogs/puzzles.py). Only claim_feeder is
    exercised here; the other four (add_feeders/unclaim_feeder/edit_feeder/
    remove_feeder) follow the identical one-line pattern."""

    @pytest.mark.asyncio
    async def test_claim_feeder_fires_the_safe_refresh(self):
        from bot.cogs.puzzles import Puzzles
        from tests.conftest import make_cog, make_ctx

        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        hunt = make_hunt(id=10)
        cog = make_cog(parent, hunt=hunt)
        ctx = make_ctx()
        item = MagicMock()
        item.id = 13
        item.text = "ALBATROSS"
        cog.service.get_feeder_item = MagicMock(return_value=item)

        await Puzzles.claim_feeder.callback(cog, ctx, 13)
        import asyncio
        await asyncio.sleep(0)   # let the create_task'd coroutine run

        cog.service.refresh_feeder_matrix_safe.assert_awaited_once_with(hunt, parent)
