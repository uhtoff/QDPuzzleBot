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
    gsheet.render_contribution = AsyncMock()
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
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.RollupsJsonDb") as rudb, \
         patch("bot.core.puzzle_service.EvidenceJsonDb") as edb:
        rudb.get_by_attr.return_value = rollup_existing
        edb.get_all.return_value = []
        yield pdb, hdb, rdb, rudb


class TestRefreshMetaFires:
    @pytest.mark.asyncio
    async def test_points_cog_at_hunt_spreadsheet(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        with _patched() as (pdb, _h, _r, _ru):
            pdb.get_by_attr.return_value = make_puzzle(id=1, is_target=True)
            pdb.get_all_from_tag_group.return_value = []
            await svc.refresh_target(_hunt(), meta_round)
        gsheet.set_spreadsheet_id.assert_called_with("SHEET1")
        gsheet.set_archive_spreadsheet_id.assert_called_with("ARCH1")

    @pytest.mark.asyncio
    async def test_calls_add_metapuzzle_data_for_regular_meta(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        metapuzzle = make_puzzle(id=1, is_target=True)
        feeders = [make_puzzle(id=2, name="Feeder", solution="ANS")]
        with _patched() as (pdb, _h, _r, _ru):
            pdb.get_by_attr.return_value = metapuzzle
            pdb.get_all_from_tag_group.return_value = feeders
            await svc.refresh_target(_hunt(), meta_round)
        # Task 10: renderers collapsed onto the unified render_contribution.
        gsheet.render_contribution.assert_awaited_once_with(
            metapuzzle, 5, feeders, evidence=[])
        gsheet.add_metapuzzle_data.assert_not_awaited()
        gsheet.add_metametapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_routed_evidence_passed_as_informs_content(self):
        # Evidence routed to the target is fetched, mapped (note -> content) and
        # passed to render_contribution so informs members render content rows.
        from bot.store.evidence_data import EvidenceData
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        metapuzzle = make_puzzle(id=1, is_target=True)
        informer = make_puzzle(id=2, name="Informer")
        informer.tag_edges = {5: {"informs"}}
        ev = EvidenceData()
        ev.id, ev.source_puzzle_id, ev.note = 11, 2, "is blue"
        with _patched() as (pdb, _h, _r, _ru):
            pdb.get_by_attr.return_value = metapuzzle
            pdb.get_all_from_tag_group.return_value = [informer]
            with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb:
                edb.get_all.return_value = [ev]
                await svc.refresh_target(_hunt(), meta_round)
        gsheet.render_contribution.assert_awaited_once_with(
            metapuzzle, 5, [informer],
            evidence=[{"id": 11, "source_puzzle_id": 2, "content": "is blue"}])
        edb.get_all.assert_called_once_with(10, target_puzzle_id=1)

    @pytest.mark.asyncio
    async def test_calls_add_metametapuzzle_data_for_metameta(self):
        # After M7 the metameta flag is replaced by source_mode='subtree'.
        # The backfill (004) set source_mode='subtree' on rollups for
        # metameta puzzles, so this path still calls add_metametapuzzle_data.
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        metameta = make_puzzle(id=1, is_target=True)
        child = make_round(id=6)
        child.parent_group_id = 5
        branch_solved = [make_puzzle(id=2, solved=True), make_puzzle(id=3, solved=True)]
        existing = RollupData(id=9, hunt_id=10, tag_group_id=5, source_mode="subtree")
        with _patched(rollup_existing=existing) as (pdb, _h, rdb, _ru):
            pdb.get_by_attr.return_value = metameta
            rdb.get_all.return_value = [meta_round, child]
            # The subtree walk queries each child round for puzzles.
            pdb.get_all_from_tag_group.side_effect = lambda rid: {5: [], 6: branch_solved}[rid]
            await svc.refresh_target(_hunt(), meta_round)
        # Task 10: subtree membership still branch-walked, but rendered via the
        # unified contribution table (no separate metameta renderer).
        gsheet.render_contribution.assert_awaited_once_with(
            metameta, 5, branch_solved, evidence=[])
        gsheet.add_metametapuzzle_data.assert_not_awaited()
        gsheet.add_metapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tag_with_edge_triggers_rebuild(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        puzzle = make_puzzle(id=2, tags=[])
        puzzle.tag_edges = {}
        with _patched() as (pdb, hdb, _r, _ru):
            hdb.get_by_attr.return_value = _hunt()
            pdb.get_by_attr.return_value = make_puzzle(id=1, is_target=True)
            pdb.get_all_from_tag_group.return_value = [puzzle]
            await svc.tag_with_edge(puzzle, meta_round, "feeds")
        assert gsheet.render_contribution.await_count == 1

    @pytest.mark.asyncio
    async def test_untag_from_rebuilds_affected_groups(self):
        svc, gsheet = _svc_with_sheets()
        # Feeder tagged to two metas; untagging from one must refresh it.
        puzzle = make_puzzle(id=2, tags=[5, 6])
        puzzle.tag_edges = {5: {"feeds"}, 6: {"feeds"}}
        with _patched() as (pdb, hdb, rdb, _ru):
            hdb.get_by_attr.return_value = _hunt()
            rdb.get_by_attr.side_effect = lambda id: make_round(id=id, target_puzzle_id=id)
            pdb.get_by_attr.return_value = make_puzzle(id=1, is_target=True)
            pdb.get_all_from_tag_group.return_value = []
            await svc.untag_from(puzzle, make_round(id=5, target_puzzle_id=5))
        assert 5 not in puzzle.tags
        assert 6 in puzzle.tags
        assert gsheet.render_contribution.await_count == 1


class TestRollupBookkeeping:
    @pytest.mark.asyncio
    async def test_persists_last_ok_on_success(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        with _patched() as (pdb, _h, _r, rudb):
            pdb.get_by_attr.return_value = make_puzzle(id=1, is_target=True)
            pdb.get_all_from_tag_group.return_value = []
            await svc.refresh_target(_hunt(), meta_round)
        committed = rudb.commit.call_args[0][0]
        assert committed.tag_group_id == 5
        assert committed.hunt_id == 10
        assert committed.last_ok == 1
        assert committed.last_error is None
        assert committed.updated_at is not None

    @pytest.mark.asyncio
    async def test_persists_failure_and_reraises(self):
        svc, gsheet = _svc_with_sheets()
        gsheet.render_contribution.side_effect = RuntimeError("sheets down")
        meta_round = make_round(id=5, target_puzzle_id=1)
        with _patched() as (pdb, _h, _r, rudb):
            pdb.get_by_attr.return_value = make_puzzle(id=1, is_target=True)
            pdb.get_all_from_tag_group.return_value = []
            with pytest.raises(RuntimeError, match="sheets down"):
                await svc.refresh_target(_hunt(), meta_round)
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
            pdb.get_by_attr.return_value = make_puzzle(id=1, is_target=True)
            pdb.get_all_from_tag_group.return_value = []
            await svc.refresh_target(_hunt(), meta_round)
        committed = rudb.commit.call_args[0][0]
        assert committed.id == 9          # same row, not a new insert
        assert committed.last_ok == 1
        assert committed.last_error is None

    @pytest.mark.asyncio
    async def test_tab_pointer_uses_google_page_id_and_records_metadata(self):
        svc, gsheet = _svc_with_sheets()
        gsheet.stamp_rollup_metadata = AsyncMock(return_value=(42, 777))
        meta_round = make_round(id=5, target_puzzle_id=1)
        metapuzzle = make_puzzle(id=1, is_target=True)
        metapuzzle.google_page_id = "42"
        with _patched() as (pdb, _h, _r, rudb):
            pdb.get_by_attr.return_value = metapuzzle
            pdb.get_all_from_tag_group.return_value = []
            await svc.refresh_target(_hunt(), meta_round)
        committed = rudb.commit.call_args[0][0]
        assert committed.tab_page_id == 42
        assert committed.metadata_id == 777

    @pytest.mark.asyncio
    async def test_metadata_stamp_failure_is_non_fatal(self):
        svc, gsheet = _svc_with_sheets()
        gsheet.stamp_rollup_metadata = AsyncMock(side_effect=RuntimeError("meta api down"))
        meta_round = make_round(id=5, target_puzzle_id=1)
        metapuzzle = make_puzzle(id=1, is_target=True)
        metapuzzle.google_page_id = "42"
        with _patched() as (pdb, _h, _r, rudb):
            pdb.get_by_attr.return_value = metapuzzle
            pdb.get_all_from_tag_group.return_value = []
            await svc.refresh_target(_hunt(), meta_round)   # must NOT raise
        committed = rudb.commit.call_args[0][0]
        assert committed.last_ok == 1            # render succeeded
        assert committed.tab_page_id == 42       # trusted pointer still recorded
        assert committed.metadata_id is None     # metadata left for a later retry


class TestSolveFailureIsolation:
    @pytest.mark.asyncio
    async def test_one_failing_meta_does_not_skip_others_or_archive(self):
        svc, gsheet = _svc_with_sheets()
        # render_contribution fails for the first meta, succeeds for the second.
        calls = {"n": 0}

        async def flaky(metapuzzle, tag_group_id, members, evidence=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first meta sheet down")

        gsheet.render_contribution.side_effect = flaky

        puzzle = make_puzzle(id=2, tags=[5, 6])
        round5 = make_round(id=5, target_puzzle_id=100)
        round6 = make_round(id=6, target_puzzle_id=101)

        def round_by_id(id):
            return {5: round5, 6: round6}[id]

        def puzzle_by_id(id):
            return {100: make_puzzle(id=100, is_target=True),
                    101: make_puzzle(id=101, is_target=True)}[id]

        with _patched() as (pdb, _h, rdb, rudb):
            rdb.get_by_attr.side_effect = lambda **kw: round_by_id(kw["id"])
            pdb.get_by_attr.side_effect = lambda **kw: puzzle_by_id(kw["id"])
            pdb.get_all_from_tag_group.return_value = []
            with pytest.raises(SheetRefreshError) as exc:
                await svc.solve(None, _hunt(), puzzle, "ANSWER")

        # Both metas attempted; the archive still ran despite the first failure.
        assert gsheet.render_contribution.await_count == 2
        gsheet.archive_puzzle_spreadsheet.assert_awaited_once()
        assert exc.value.failed_round_ids == [5]
        assert puzzle.solved is True
