"""M7.5 Phase 1 — failing tests for the pure membership selector.

`PuzzleService.select_rollup_members` extracts the membership-selection half of
`_render_rollup` so a read path (the aggregated-answers endpoint) can reuse the
SAME selection the sheet render uses, WITHOUT the Google-Sheet side-effect. These
tests pin the puzzle-id set returned per `source_mode` and assert that neither
renderer is awaited (the selector is pure — no sheet write).

The seam is copied module-local from tests/test_refresh_meta.py (NOT imported from
the sibling file, so this file is self-contained under the parallel gate).
"""
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.rollup_data import RollupData
from tests.conftest import make_puzzle, make_round


def _svc_with_sheets():
    """A PuzzleService whose (mocked) GoogleSheets cog exposes the two renderers,
    so a test can assert the selector NEVER awaits them."""
    gsheet = MagicMock()
    gsheet.add_metapuzzle_data = AsyncMock()
    gsheet.add_metametapuzzle_data = AsyncMock()
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return PuzzleService(bot), gsheet


@contextmanager
def _patched():
    """Patch the store layer the selector reads (PuzzleJsonDb for round members /
    subtree puzzles; RoundJsonDb for the subtree walk)."""
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.RoundJsonDb") as rdb:
        yield pdb, rdb


class TestSelectRollupMembers:
    @pytest.mark.asyncio
    async def test_round_mode_returns_feeds_meta_members_without_rendering(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        rollup = RollupData(id=9, hunt_id=10, tag_group_id=5, source_mode="round")
        feeder = make_puzzle(id=2, name="Feeder", solution="ANS")
        feeder.tag_edges = {5: "feeds"}
        with _patched() as (pdb, _rdb):
            pdb.get_all_from_tag_group.return_value = [feeder]
            members = await svc.select_rollup_members(meta_round, rollup)
        assert [p.id for p in members] == [2]
        gsheet.add_metapuzzle_data.assert_not_awaited()
        gsheet.add_metametapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_round_mode_excludes_non_matching_edge_type(self):
        # A 'none' edge is NOT a feeds feeder; round mode drops it.
        svc, _gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        rollup = RollupData(id=9, hunt_id=10, tag_group_id=5, source_mode="round")
        feeder = make_puzzle(id=2, name="Feeder", solution="ANS")
        feeder.tag_edges = {5: "feeds"}
        pooled = make_puzzle(id=3, name="Pooled", solution="P")
        pooled.tag_edges = {5: "none"}
        with _patched() as (pdb, _rdb):
            pdb.get_all_from_tag_group.return_value = [feeder, pooled]
            members = await svc.select_rollup_members(meta_round, rollup)
        assert [p.id for p in members] == [2]

    @pytest.mark.asyncio
    async def test_subtree_mode_returns_branch_solved_puzzles_without_rendering(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        rollup = RollupData(id=9, hunt_id=10, tag_group_id=5, source_mode="subtree")
        child = make_round(id=6)
        child.parent_group_id = 5
        solved = make_puzzle(id=2, name="Branch", solved=True, solution="X")
        solved.tag_edges = {6: "feeds"}
        with _patched() as (pdb, rdb):
            rdb.get_all.return_value = [meta_round, child]
            pdb.get_all_from_tag_group.side_effect = lambda rid: {5: [], 6: [solved]}[rid]
            members = await svc.select_rollup_members(meta_round, rollup)
        assert [p.id for p in members] == [2]
        gsheet.add_metapuzzle_data.assert_not_awaited()
        gsheet.add_metametapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_explicit_mode_returns_only_none_edge_members(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        rollup = RollupData(id=9, hunt_id=10, tag_group_id=5, source_mode="explicit")
        pooled = make_puzzle(id=2, name="Pooled", solution="P")
        pooled.tag_edges = {5: "none"}
        other = make_puzzle(id=3, name="Feeder", solution="F")
        other.tag_edges = {5: "feeds"}
        with _patched() as (pdb, _rdb):
            pdb.get_all_from_tag_group.return_value = [pooled, other]
            members = await svc.select_rollup_members(meta_round, rollup)
        assert [p.id for p in members] == [2]
        gsheet.add_metapuzzle_data.assert_not_awaited()
        gsheet.add_metametapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falsy_source_mode_defaults_to_round(self):
        # A rollup whose source_mode is empty/falsy is treated as 'round'.
        svc, _gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        rollup = RollupData(id=9, hunt_id=10, tag_group_id=5, source_mode="")
        feeder = make_puzzle(id=2, name="Feeder", solution="ANS")
        feeder.tag_edges = {5: "feeds"}
        with _patched() as (pdb, _rdb):
            pdb.get_all_from_tag_group.return_value = [feeder]
            members = await svc.select_rollup_members(meta_round, rollup)
        assert [p.id for p in members] == [2]
