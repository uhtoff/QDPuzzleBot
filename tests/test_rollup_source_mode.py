"""M7: refresh_meta routes by rollup.source_mode (round / subtree / explicit).

Mirrors the seam in test_refresh_meta.py (present, mocked GoogleSheets cog) but
asserts WHICH renderer the source_mode branch awaits — and that the wrong one is
NOT awaited. The helpers are defined module-local (the parallel gate shards by
file; no cross-test-file imports).
"""
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
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
    `rollup_existing` (None -> a fresh round-mode row is created)."""
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.RoundJsonDb") as rdb, \
         patch("bot.core.puzzle_service.RollupsJsonDb") as rudb:
        rudb.get_by_attr.return_value = rollup_existing
        yield pdb, hdb, rdb, rudb


class TestSourceModeRouting:
    @pytest.mark.asyncio
    async def test_round_mode_calls_add_metapuzzle_data(self):
        # rollup.source_mode == "round" (the default) -> today's path, unchanged.
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        metapuzzle = make_puzzle(id=1, metapuzzle=1)
        feeders = [make_puzzle(id=2, name="Feeder", solution="ANS")]
        with _patched() as (pdb, _h, _r, _ru):
            pdb.get_by_attr.return_value = metapuzzle
            pdb.get_all_from_tag_group.return_value = feeders
            await svc.refresh_target(_hunt(), meta_round)
        gsheet.add_metapuzzle_data.assert_awaited_once_with(metapuzzle, feeders)
        gsheet.add_metametapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_subtree_mode_aggregates_branch_solved_puzzles(self):
        # rollup.source_mode == "subtree": collect solved puzzles across the
        # parent_group_id branch and render via the metameta (3-col) path.
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        metapuzzle = make_puzzle(id=1, metapuzzle=1)
        existing = RollupData(id=9, hunt_id=10, tag_group_id=5, source_mode="subtree")
        # child round 6 nested under round 5; its solved feeder is id=2.
        child = make_round(id=6, target_puzzle_id=0)
        child.parent_group_id = 5
        solved = make_puzzle(id=2, name="Branch", solved=True, solution="X")
        with _patched(rollup_existing=existing) as (pdb, _h, rdb, _ru):
            pdb.get_by_attr.return_value = metapuzzle
            rdb.get_all.return_value = [meta_round, child]   # RoundJsonDb.get_all(hunt_id)
            pdb.get_all_from_tag_group.side_effect = lambda rid: {5: [], 6: [solved]}[rid]
            await svc.refresh_target(_hunt(), meta_round)
        # the metameta renderer received exactly the branch's solved puzzles
        args = gsheet.add_metametapuzzle_data.await_args
        assert args.args[0] is metapuzzle
        assert [p.id for p in args.args[1]] == [2]
        gsheet.add_metapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_explicit_mode_uses_aggregates_into_members(self):
        # rollup.source_mode == "explicit": only puzzles whose edge into THIS round
        # is 'aggregates-into' are aggregated, rendered via the 2-col pool shape.
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        metapuzzle = make_puzzle(id=1, metapuzzle=1)
        existing = RollupData(id=9, hunt_id=10, tag_group_id=5, source_mode="explicit")
        pooled = make_puzzle(id=2, name="Pooled", solution="P")
        pooled.tag_edges = {5: "aggregates-into"}
        other = make_puzzle(id=3, name="Feeder", solution="F")
        other.tag_edges = {5: "feeds-meta"}
        with _patched(rollup_existing=existing) as (pdb, _h, _r, _ru):
            pdb.get_by_attr.return_value = metapuzzle
            pdb.get_all_from_tag_group.return_value = [pooled, other]
            await svc.refresh_target(_hunt(), meta_round)
        # Only the aggregates-into member is rendered (pool = name+solution).
        gsheet.add_metapuzzle_data.assert_awaited_once()
        args = gsheet.add_metapuzzle_data.await_args
        assert args.args[0] is metapuzzle
        assert [p.id for p in args.args[1]] == [2]
        gsheet.add_metametapuzzle_data.assert_not_awaited()
