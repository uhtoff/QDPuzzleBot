"""M8 Phase 3: puzzle-keyed roll-up — a minimeta's mini-puzzles render onto the
minimeta's OWN sheet via the rollup engine, tracked on a puzzle-keyed rollups row.

Mirrors test_refresh_meta.py's mocking style (present-but-mocked GoogleSheets cog).
"""
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_puzzle


def _hunt():
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "SHEET1"
    h.archive_google_sheet_id = "ARCH1"
    return h


def _svc_with_sheets():
    gsheet = MagicMock()
    gsheet.add_metapuzzle_data = AsyncMock()
    gsheet.archive_puzzle_spreadsheet = AsyncMock()
    gsheet.restore_puzzle_spreadsheet = AsyncMock()
    gsheet.stamp_rollup_metadata = AsyncMock(return_value=(None, None))
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return PuzzleService(bot), gsheet


@contextmanager
def _patched(rollup_existing=None):
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.RollupsJsonDb") as rudb:
        rudb.get_by_attr.return_value = rollup_existing
        yield pdb, hdb, rdb, rudb


class TestRefreshMinimeta:
    @pytest.mark.asyncio
    async def test_renders_minis_onto_minimeta_sheet(self):
        svc, gsheet = _svc_with_sheets()
        minimeta = make_puzzle(id=3, puzzle_type="Minimeta")
        minis = [
            make_puzzle(id=10, name="Mini A", parent_puzzle_id=3),
            make_puzzle(id=11, name="Mini B", parent_puzzle_id=3, solved=True, solution="X"),
        ]
        with _patched() as (pdb, _h, _r, _ru):
            pdb.get_children.return_value = minis
            await svc.refresh_minimeta(_hunt(), minimeta)
        gsheet.add_metapuzzle_data.assert_awaited_once_with(minimeta, minis)

    @pytest.mark.asyncio
    async def test_creates_puzzle_keyed_rollup_record(self):
        svc, _gsheet = _svc_with_sheets()
        minimeta = make_puzzle(id=3, puzzle_type="Minimeta")
        with _patched() as (pdb, _h, _r, rudb):
            pdb.get_children.return_value = []
            await svc.refresh_minimeta(_hunt(), minimeta)
        committed = rudb.commit.call_args[0][0]
        assert committed.target_puzzle_id == 3
        assert committed.tag_group_id is None        # puzzle-keyed, not round-keyed
        assert committed.hunt_id == 10
        assert committed.last_ok == 1
        assert committed.last_error is None

    @pytest.mark.asyncio
    async def test_badges_failure_and_reraises(self):
        svc, gsheet = _svc_with_sheets()
        gsheet.add_metapuzzle_data.side_effect = RuntimeError("sheets down")
        minimeta = make_puzzle(id=3, puzzle_type="Minimeta")
        with _patched() as (pdb, _h, _r, rudb):
            pdb.get_children.return_value = []
            with pytest.raises(RuntimeError, match="sheets down"):
                await svc.refresh_minimeta(_hunt(), minimeta)
        committed = rudb.commit.call_args[0][0]
        assert committed.last_ok == 0
        assert "sheets down" in committed.last_error

    @pytest.mark.asyncio
    async def test_noop_without_sheets_cog(self):
        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)
        minimeta = make_puzzle(id=3, puzzle_type="Minimeta")
        with _patched() as (pdb, _h, _r, rudb):
            await svc.refresh_minimeta(_hunt(), minimeta)
        rudb.commit.assert_not_called()


class TestSolveUnsolveTriggerMinimetaRollup:
    @pytest.mark.asyncio
    async def test_mini_solve_refreshes_parent(self):
        svc, _gsheet = _svc_with_sheets()
        mini = make_puzzle(id=10, parent_puzzle_id=3, tags=[])
        parent = make_puzzle(id=3, puzzle_type="Minimeta")
        svc.refresh_minimeta = AsyncMock()
        with _patched() as (pdb, _h, _r, _ru):
            pdb.get_by_attr.return_value = parent
            await svc.solve(None, _hunt(), mini, "ANS")
        svc.refresh_minimeta.assert_awaited_once()
        assert svc.refresh_minimeta.call_args[0][1] is parent

    @pytest.mark.asyncio
    async def test_regular_solve_does_not_refresh_minimeta(self):
        svc, _gsheet = _svc_with_sheets()
        puzzle = make_puzzle(id=1, tags=[])      # no parent_puzzle_id
        svc.refresh_minimeta = AsyncMock()
        with _patched() as (pdb, _h, _r, _ru):
            await svc.solve(None, _hunt(), puzzle, "ANS")
        svc.refresh_minimeta.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mini_unsolve_refreshes_parent(self):
        svc, _gsheet = _svc_with_sheets()
        mini = make_puzzle(id=10, parent_puzzle_id=3, tags=[], solved=True, solution="X")
        parent = make_puzzle(id=3, puzzle_type="Minimeta")
        svc.refresh_minimeta = AsyncMock()
        with _patched() as (pdb, _h, _r, _ru):
            pdb.get_by_attr.return_value = parent
            await svc.unsolve(None, _hunt(), mini)
        svc.refresh_minimeta.assert_awaited_once()
        assert svc.refresh_minimeta.call_args[0][1] is parent
