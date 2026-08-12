"""Service-level tests for refresh_feeder_matrix (Plan 2, Task 5).

Mirrors tests/test_refresh_minimeta.py's mocking style: the GoogleSheets cog is
a MagicMock, so these assert the SERVICE's orchestration -- guards, tab-id
persistence, badge bookkeeping, lock discipline -- not what Sheets does with the
requests the cog would send (see tests/test_feeder_matrix_requests.py for that).
"""
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.feeder_pool_data import FeederClaimData, FeederPoolItemData
from bot.store.hunt_data import HuntData
from tests.conftest import make_puzzle


def _hunt():
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "SHEET1"
    h.solved_sheet_id = "ARCH1"
    return h


def _minimeta(feeder_tab_page_id=None):
    p = make_puzzle(id=3, puzzle_type="Minimeta")
    p.feeder_tab_page_id = feeder_tab_page_id
    return p


def _svc_with_sheets():
    gsheet = MagicMock()
    gsheet.ensure_feeder_matrix_tab = AsyncMock(return_value=500)
    gsheet.render_feeder_matrix = AsyncMock()
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return PuzzleService(bot), gsheet


@contextmanager
def _patched(rollup_existing=None):
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.RollupsJsonDb") as rudb:
        rudb.get_by_attr.return_value = rollup_existing
        yield pdb, rudb


class TestRefreshFeederMatrix:
    @pytest.mark.asyncio
    async def test_renders_the_matrix_when_the_pool_is_non_empty(self):
        svc, gsheet = _svc_with_sheets()
        minimeta = _minimeta(feeder_tab_page_id=500)
        items = [FeederPoolItemData(id=12, text="ALBATROSS")]
        claims = [FeederClaimData(id=1, item_id=12, target_id=3, confirmed=True)]
        svc.get_feeder_pool = MagicMock(return_value=(items, claims))
        minis = [make_puzzle(id=10, name="Mini A", parent_puzzle_id=3)]
        with _patched() as (pdb, _ru):
            pdb.get_children.return_value = minis
            await svc.refresh_feeder_matrix(_hunt(), minimeta)
        gsheet.ensure_feeder_matrix_tab.assert_awaited_once_with(minimeta, minis)
        gsheet.render_feeder_matrix.assert_awaited_once_with(minimeta, items, minis, claims)

    @pytest.mark.asyncio
    async def test_skips_entirely_when_the_pool_is_empty(self):
        svc, gsheet = _svc_with_sheets()
        minimeta = _minimeta()
        svc.get_feeder_pool = MagicMock(return_value=([], []))
        with _patched() as (_pdb, rudb):
            await svc.refresh_feeder_matrix(_hunt(), minimeta)
        gsheet.ensure_feeder_matrix_tab.assert_not_awaited()
        gsheet.render_feeder_matrix.assert_not_awaited()
        rudb.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_persists_the_tab_page_id_returned_by_ensure(self):
        svc, _gsheet = _svc_with_sheets()
        minimeta = _minimeta(feeder_tab_page_id=None)
        items = [FeederPoolItemData(id=12, text="ALBATROSS")]
        svc.get_feeder_pool = MagicMock(return_value=(items, []))
        with _patched() as (pdb, _ru):
            pdb.get_children.return_value = []
            await svc.refresh_feeder_matrix(_hunt(), minimeta)
        assert minimeta.feeder_tab_page_id == 500
        pdb.commit.assert_any_call(minimeta)

    @pytest.mark.asyncio
    async def test_does_not_re_persist_when_the_tab_id_is_unchanged(self):
        svc, _gsheet = _svc_with_sheets()
        minimeta = _minimeta(feeder_tab_page_id=500)   # matches ensure's return value
        items = [FeederPoolItemData(id=12, text="ALBATROSS")]
        svc.get_feeder_pool = MagicMock(return_value=(items, []))
        with _patched() as (pdb, _ru):
            pdb.get_children.return_value = []
            await svc.refresh_feeder_matrix(_hunt(), minimeta)
        pdb.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_badges_success_on_the_shared_rollup_row(self):
        svc, _gsheet = _svc_with_sheets()
        minimeta = _minimeta(feeder_tab_page_id=500)
        items = [FeederPoolItemData(id=12, text="ALBATROSS")]
        svc.get_feeder_pool = MagicMock(return_value=(items, []))
        with _patched() as (pdb, rudb):
            pdb.get_children.return_value = []
            await svc.refresh_feeder_matrix(_hunt(), minimeta)
        committed = rudb.commit.call_args[0][0]
        # Puzzle-keyed, not round-keyed -- the SAME row refresh_minimeta badges.
        assert committed.target_puzzle_id == 3
        assert committed.tag_group_id is None
        assert committed.last_ok == 1
        assert committed.last_error is None

    @pytest.mark.asyncio
    async def test_badges_failure_and_reraises(self):
        svc, gsheet = _svc_with_sheets()
        gsheet.render_feeder_matrix.side_effect = RuntimeError("sheets down")
        minimeta = _minimeta(feeder_tab_page_id=500)
        items = [FeederPoolItemData(id=12, text="ALBATROSS")]
        svc.get_feeder_pool = MagicMock(return_value=(items, []))
        with _patched() as (pdb, rudb):
            pdb.get_children.return_value = []
            with pytest.raises(RuntimeError, match="sheets down"):
                await svc.refresh_feeder_matrix(_hunt(), minimeta)
        committed = rudb.commit.call_args[0][0]
        assert committed.last_ok == 0
        assert "sheets down" in committed.last_error

    @pytest.mark.asyncio
    async def test_noop_without_sheets_cog(self):
        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)
        minimeta = _minimeta(feeder_tab_page_id=500)
        items = [FeederPoolItemData(id=12, text="ALBATROSS")]
        svc.get_feeder_pool = MagicMock(return_value=(items, []))
        with _patched() as (_pdb, rudb):
            await svc.refresh_feeder_matrix(_hunt(), minimeta)
        rudb.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_without_hunt_or_minimeta(self):
        svc, gsheet = _svc_with_sheets()
        await svc.refresh_feeder_matrix(None, _minimeta())
        await svc.refresh_feeder_matrix(_hunt(), None)
        gsheet.ensure_feeder_matrix_tab.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_call_refresh_minimeta(self):
        """The XOR-lock rule (see refresh_feeder_matrix's docstring): both
        functions take the identical non-reentrant rollup_lock key
        ("minimeta", id), so calling refresh_minimeta from inside this one
        would wedge the event loop forever. That hang isn't directly
        testable, so this pins the proxy that would catch a regression
        structurally -- refresh_minimeta is never invoked as part of this
        call at all."""
        svc, _gsheet = _svc_with_sheets()
        svc.refresh_minimeta = AsyncMock()
        minimeta = _minimeta(feeder_tab_page_id=500)
        items = [FeederPoolItemData(id=12, text="ALBATROSS")]
        svc.get_feeder_pool = MagicMock(return_value=(items, []))
        with _patched() as (pdb, _ru):
            pdb.get_children.return_value = []
            await svc.refresh_feeder_matrix(_hunt(), minimeta)
        svc.refresh_minimeta.assert_not_awaited()
