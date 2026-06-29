"""M7: refresh_meta selects membership by rollup.source_mode (round / subtree / explicit).

Mirrors the seam in test_refresh_meta.py (present, mocked GoogleSheets cog). Post
Task 10 the two renderers are collapsed onto the unified render_contribution, so
source_mode no longer picks a renderer — it picks MEMBERSHIP. These tests assert
the member set handed to render_contribution per mode. The helpers are defined
module-local (the parallel gate shards by file; no cross-test-file imports).
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
    gsheet.render_contribution = AsyncMock()
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
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.RollupsJsonDb") as rudb:
        rudb.get_by_attr.return_value = rollup_existing
        yield pdb, hdb, rdb, rudb


class TestSourceModeRouting:
    @pytest.mark.asyncio
    async def test_round_mode_renders_all_group_members(self):
        # rollup.source_mode == "round" (the default): all feeds members rendered.
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        metapuzzle = make_puzzle(id=1, is_target=True)
        feeders = [make_puzzle(id=2, name="Feeder", solution="ANS")]
        with _patched() as (pdb, _h, _r, _ru):
            pdb.get_by_attr.return_value = metapuzzle
            pdb.get_all_from_tag_group.return_value = feeders
            await svc.refresh_target(_hunt(), meta_round)
        gsheet.render_contribution.assert_awaited_once()
        args = gsheet.render_contribution.await_args
        assert args.args[0] is metapuzzle
        assert args.args[1] == 5
        assert [p.id for p in args.args[2]] == [2]
        assert args.kwargs["evidence"] == []

    @pytest.mark.asyncio
    async def test_subtree_mode_aggregates_branch_solved_puzzles(self):
        # rollup.source_mode == "subtree": walk the 'feeds' chain and render via the
        # metameta (3-col) path. A nested meta (id=7) feeds the runaround; its own
        # solved feeder (id=2) surfaces.
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        metapuzzle = make_puzzle(id=1, is_target=True)
        metapuzzle.add_edge(5, "target")
        existing = RollupData(id=9, hunt_id=10, tag_group_id=5, source_mode="subtree")
        child = make_round(id=6, target_puzzle_id=7)
        nested_meta = make_puzzle(id=7, solved=False)
        nested_meta.add_edge(5, "feeds")
        nested_meta.add_edge(6, "target")
        solved = make_puzzle(id=2, name="Branch", solved=True, solution="X")
        solved.add_edge(6, "feeds")
        with _patched(rollup_existing=existing) as (pdb, _h, rdb, _ru):
            pdb.get_by_attr.return_value = metapuzzle
            rdb.get_all.return_value = [meta_round, child]   # TagGroupJsonDb.get_all(hunt_id)
            pdb.get_all_from_tag_group.side_effect = lambda rid: {
                5: [metapuzzle, nested_meta], 6: [nested_meta, solved]}.get(rid, [])
            await svc.refresh_target(_hunt(), meta_round)
        # render_contribution received exactly the branch's solved puzzles
        gsheet.render_contribution.assert_awaited_once()
        args = gsheet.render_contribution.await_args
        assert args.args[0] is metapuzzle
        assert args.args[1] == 5
        assert [p.id for p in args.args[2]] == [2]

    @pytest.mark.asyncio
    async def test_explicit_mode_uses_aggregates_into_members(self):
        # rollup.source_mode == "explicit": only puzzles whose edge into THIS round
        # is 'none' are aggregated, rendered via the 2-col pool shape.
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        metapuzzle = make_puzzle(id=1, is_target=True)
        existing = RollupData(id=9, hunt_id=10, tag_group_id=5, source_mode="explicit")
        pooled = make_puzzle(id=2, name="Pooled", solution="P")
        pooled.tag_edges = {5: "none"}
        other = make_puzzle(id=3, name="Feeder", solution="F")
        other.tag_edges = {5: "feeds"}
        with _patched(rollup_existing=existing) as (pdb, _h, _r, _ru):
            pdb.get_by_attr.return_value = metapuzzle
            pdb.get_all_from_tag_group.return_value = [pooled, other]
            await svc.refresh_target(_hunt(), meta_round)
        # Only the 'none' edge member is rendered (pool = name+solution).
        gsheet.render_contribution.assert_awaited_once()
        args = gsheet.render_contribution.await_args
        assert args.args[0] is metapuzzle
        assert args.args[1] == 5
        assert [p.id for p in args.args[2]] == [2]
