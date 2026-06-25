"""Regression tests that the meta-sheet rebuild FIRES and is TRACKED (M5).

The bang-vs-web equivalence suite (test_solve_equivalence.py) runs with no
GoogleSheets cog (bot.get_cog -> None), so it verifies DB-state transitions but
NOT that PuzzleService.refresh_meta reaches add_metapuzzle_data /
add_metametapuzzle_data. These tests close that gap with a present (mocked) cog,
and additionally assert M5's rollup bookkeeping: last_ok/last_error persistence,
re-raise on failure, the google_page_id tab pointer, and solve-time failure
isolation across multiple metas.
"""
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.core.errors import SheetRefreshError
from bot.store.hunt_data import HuntData
from bot.store.rollup_data import RollupData
from tests.conftest import make_puzzle, make_round


def _hunt():
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "SHEET1"
    h.archive_google_sheet_id = "ARCH1"
    return h


def _svc_with_sheets():
    gsheet = MagicMock()
    gsheet.add_metapuzzle_data = AsyncMock()
    gsheet.add_metametapuzzle_data = AsyncMock()
    gsheet.archive_puzzle_spreadsheet = AsyncMock()
    gsheet.stamp_rollup_metadata = AsyncMock(return_value=(None, None))
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return PuzzleService(bot), gsheet


@contextmanager
def _patched(rollup_existing=None):
    """Patch the store layer refresh_meta touches. RollupsJsonDb.get_by_attr returns
    `rollup_existing` (None -> a fresh row is created); commit is a no-op mock whose
    call_args the test inspects."""
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.RoundJsonDb") as rdb, \
         patch("bot.core.puzzle_service.RollupsJsonDb") as rudb:
        rudb.get_by_attr.return_value = rollup_existing
        yield pdb, hdb, rdb, rudb


class TestRefreshMetaFires:
    @pytest.mark.asyncio
    async def test_points_cog_at_hunt_spreadsheet(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        with _patched() as (pdb, _h, _r, _ru):
            pdb.get_by_attr.return_value = make_puzzle(id=1, metapuzzle=1)
            pdb.get_all_from_round.return_value = []
            await svc.refresh_meta(_hunt(), meta_round)
        gsheet.set_spreadsheet_id.assert_called_with("SHEET1")
        gsheet.set_archive_spreadsheet_id.assert_called_with("ARCH1")

    @pytest.mark.asyncio
    async def test_calls_add_metapuzzle_data_for_regular_meta(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        metapuzzle = make_puzzle(id=1, metapuzzle=1)
        feeders = [make_puzzle(id=2, name="Feeder", solution="ANS")]
        with _patched() as (pdb, _h, _r, _ru):
            pdb.get_by_attr.return_value = metapuzzle
            pdb.get_all_from_round.return_value = feeders
            await svc.refresh_meta(_hunt(), meta_round)
        gsheet.add_metapuzzle_data.assert_awaited_once_with(metapuzzle, feeders)
        gsheet.add_metametapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_calls_add_metametapuzzle_data_for_metameta(self):
        # After M7 the metameta flag is replaced by source_mode='subtree'.
        # The backfill (004) set source_mode='subtree' on rollups for
        # metameta puzzles, so this path still calls add_metametapuzzle_data.
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        metameta = make_puzzle(id=1, metapuzzle=1, metameta=1)
        child = make_round(id=6)
        child.parent_group_id = 5
        branch_solved = [make_puzzle(id=2, solved=True), make_puzzle(id=3, solved=True)]
        existing = RollupData(id=9, hunt_id=10, tag_group_id=5, source_mode="subtree")
        with _patched(rollup_existing=existing) as (pdb, _h, rdb, _ru):
            pdb.get_by_attr.return_value = metameta
            rdb.get_all.return_value = [meta_round, child]
            # The subtree walk queries each child round for puzzles.
            pdb.get_all_from_round.side_effect = lambda rid: {5: [], 6: branch_solved}[rid]
            await svc.refresh_meta(_hunt(), meta_round)
        gsheet.add_metametapuzzle_data.assert_awaited_once_with(metameta, branch_solved)
        gsheet.add_metapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_add_to_meta_triggers_rebuild(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        puzzle = make_puzzle(id=2, tags=[])
        with _patched() as (pdb, hdb, _r, _ru):
            hdb.get_by_attr.return_value = _hunt()
            pdb.get_by_attr.return_value = make_puzzle(id=1, metapuzzle=1)
            pdb.get_all_from_round.return_value = [puzzle]
            await svc.add_to_meta(puzzle, meta_round)
        assert gsheet.add_metapuzzle_data.await_count == 1

    @pytest.mark.asyncio
    async def test_clear_metas_untags_all_and_rebuilds_each(self):
        svc, gsheet = _svc_with_sheets()
        # Feeder tagged to two metas; clearing must empty its tags and refresh both.
        puzzle = make_puzzle(id=2, tags=[5, 6])
        with _patched() as (pdb, hdb, rdb, _ru):
            hdb.get_by_attr.return_value = _hunt()
            rdb.get_by_attr.side_effect = lambda id: make_round(id=id, target_puzzle_id=id)
            pdb.get_by_attr.return_value = make_puzzle(id=1, metapuzzle=1)
            pdb.get_all_from_round.return_value = []
            await svc.clear_metas(puzzle)
        assert puzzle.tags == []
        assert gsheet.add_metapuzzle_data.await_count == 2


class TestRollupBookkeeping:
    @pytest.mark.asyncio
    async def test_persists_last_ok_on_success(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        with _patched() as (pdb, _h, _r, rudb):
            pdb.get_by_attr.return_value = make_puzzle(id=1, metapuzzle=1)
            pdb.get_all_from_round.return_value = []
            await svc.refresh_meta(_hunt(), meta_round)
        committed = rudb.commit.call_args[0][0]
        assert committed.tag_group_id == 5
        assert committed.hunt_id == 10
        assert committed.last_ok == 1
        assert committed.last_error is None
        assert committed.updated_at is not None

    @pytest.mark.asyncio
    async def test_persists_failure_and_reraises(self):
        svc, gsheet = _svc_with_sheets()
        gsheet.add_metapuzzle_data.side_effect = RuntimeError("sheets down")
        meta_round = make_round(id=5, target_puzzle_id=1)
        with _patched() as (pdb, _h, _r, rudb):
            pdb.get_by_attr.return_value = make_puzzle(id=1, metapuzzle=1)
            pdb.get_all_from_round.return_value = []
            with pytest.raises(RuntimeError, match="sheets down"):
                await svc.refresh_meta(_hunt(), meta_round)
        committed = rudb.commit.call_args[0][0]
        assert committed.last_ok == 0
        assert "sheets down" in committed.last_error

    @pytest.mark.asyncio
    async def test_updates_existing_rollup_in_place(self):
        """find-or-create reuses the existing row rather than duplicating it."""
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        existing = RollupData(id=9, hunt_id=10, tag_group_id=5, last_ok=0, last_error="old")
        with _patched(rollup_existing=existing) as (pdb, _h, _r, rudb):
            pdb.get_by_attr.return_value = make_puzzle(id=1, metapuzzle=1)
            pdb.get_all_from_round.return_value = []
            await svc.refresh_meta(_hunt(), meta_round)
        committed = rudb.commit.call_args[0][0]
        assert committed.id == 9          # same row, not a new insert
        assert committed.last_ok == 1
        assert committed.last_error is None

    @pytest.mark.asyncio
    async def test_tab_pointer_uses_google_page_id_and_records_metadata(self):
        svc, gsheet = _svc_with_sheets()
        gsheet.stamp_rollup_metadata = AsyncMock(return_value=(42, 777))
        meta_round = make_round(id=5, target_puzzle_id=1)
        metapuzzle = make_puzzle(id=1, metapuzzle=1)
        metapuzzle.google_page_id = "42"
        with _patched() as (pdb, _h, _r, rudb):
            pdb.get_by_attr.return_value = metapuzzle
            pdb.get_all_from_round.return_value = []
            await svc.refresh_meta(_hunt(), meta_round)
        committed = rudb.commit.call_args[0][0]
        assert committed.tab_page_id == 42
        assert committed.metadata_id == 777

    @pytest.mark.asyncio
    async def test_metadata_stamp_failure_is_non_fatal(self):
        svc, gsheet = _svc_with_sheets()
        gsheet.stamp_rollup_metadata = AsyncMock(side_effect=RuntimeError("meta api down"))
        meta_round = make_round(id=5, target_puzzle_id=1)
        metapuzzle = make_puzzle(id=1, metapuzzle=1)
        metapuzzle.google_page_id = "42"
        with _patched() as (pdb, _h, _r, rudb):
            pdb.get_by_attr.return_value = metapuzzle
            pdb.get_all_from_round.return_value = []
            await svc.refresh_meta(_hunt(), meta_round)   # must NOT raise
        committed = rudb.commit.call_args[0][0]
        assert committed.last_ok == 1            # render succeeded
        assert committed.tab_page_id == 42       # trusted pointer still recorded
        assert committed.metadata_id is None     # metadata left for a later retry


class TestSolveFailureIsolation:
    @pytest.mark.asyncio
    async def test_one_failing_meta_does_not_skip_others_or_archive(self):
        svc, gsheet = _svc_with_sheets()
        # add_metapuzzle_data fails for the first meta, succeeds for the second.
        calls = {"n": 0}

        async def flaky(metapuzzle, feeders):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first meta sheet down")

        gsheet.add_metapuzzle_data.side_effect = flaky

        puzzle = make_puzzle(id=2, tags=[5, 6])
        round5 = make_round(id=5, target_puzzle_id=100)
        round6 = make_round(id=6, target_puzzle_id=101)

        def round_by_id(id):
            return {5: round5, 6: round6}[id]

        def puzzle_by_id(id):
            return {100: make_puzzle(id=100, metapuzzle=1),
                    101: make_puzzle(id=101, metapuzzle=1)}[id]

        with _patched() as (pdb, _h, rdb, rudb):
            rdb.get_by_attr.side_effect = lambda **kw: round_by_id(kw["id"])
            pdb.get_by_attr.side_effect = lambda **kw: puzzle_by_id(kw["id"])
            pdb.get_all_from_round.return_value = []
            with pytest.raises(SheetRefreshError) as exc:
                await svc.solve(None, _hunt(), puzzle, "ANSWER")

        # Both metas attempted; the archive still ran despite the first failure.
        assert gsheet.add_metapuzzle_data.await_count == 2
        gsheet.archive_puzzle_spreadsheet.assert_awaited_once()
        assert exc.value.failed_round_ids == [5]
        assert puzzle.solved is True
