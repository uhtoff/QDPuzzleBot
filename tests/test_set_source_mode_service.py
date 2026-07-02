"""TDD tests for PuzzleService.set_source_mode.

Written before the implementation; these must pass once the service method
is added.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from bot.store.rollup_data import RollupData
from tests.conftest import make_round


def _hunt(id=10):
    h = HuntData()
    h.id = id
    h.google_sheet_id = "SHEET1"
    h.solved_sheet_id = "ARCH1"
    return h


def _svc():
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


@pytest.mark.asyncio
async def test_set_source_mode_creates_rollup_when_missing():
    """When no rollup exists yet, one is created and committed with the new mode."""
    svc = _svc()
    meta_round = make_round(id=5, target_puzzle_id=1)
    with patch("bot.core.puzzle_service.RollupsJsonDb") as mock_rudb, \
         patch.object(svc, "refresh_target", new_callable=AsyncMock) as mock_refresh:
        mock_rudb.get_by_attr.return_value = None  # no existing rollup
        await svc.set_source_mode(_hunt(), meta_round, "subtree")
        # A commit must have been made
        mock_rudb.commit.assert_called_once()
        committed = mock_rudb.commit.call_args.args[0]
        assert committed.source_mode == "subtree"
        assert committed.tag_group_id == 5
        # refresh_meta must be called after commit
        mock_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_source_mode_updates_existing_rollup():
    """When a rollup exists, its source_mode is updated in-place and committed."""
    svc = _svc()
    meta_round = make_round(id=7, target_puzzle_id=2)
    existing = RollupData(id=42, hunt_id=10, tag_group_id=7, source_mode="round")
    with patch("bot.core.puzzle_service.RollupsJsonDb") as mock_rudb, \
         patch.object(svc, "refresh_target", new_callable=AsyncMock):
        mock_rudb.get_by_attr.return_value = existing
        await svc.set_source_mode(_hunt(), meta_round, "explicit")
        mock_rudb.commit.assert_called_once()
        committed = mock_rudb.commit.call_args.args[0]
        assert committed.source_mode == "explicit"
        assert committed.id == 42


@pytest.mark.asyncio
async def test_set_source_mode_calls_refresh_meta():
    """refresh_target is awaited with (hunt, meta_round) after the commit."""
    svc = _svc()
    hunt = _hunt()
    meta_round = make_round(id=3, target_puzzle_id=1)
    with patch("bot.core.puzzle_service.RollupsJsonDb") as mock_rudb, \
         patch.object(svc, "refresh_target", new_callable=AsyncMock) as mock_refresh:
        mock_rudb.get_by_attr.return_value = None
        await svc.set_source_mode(hunt, meta_round, "round")
        mock_refresh.assert_awaited_once_with(hunt, meta_round)  # refresh_target


@pytest.mark.asyncio
async def test_set_source_mode_invalid_mode_raises():
    """An unrecognised mode raises ValueError before touching the DB."""
    svc = _svc()
    meta_round = make_round(id=5, target_puzzle_id=1)
    with patch("bot.core.puzzle_service.RollupsJsonDb") as mock_rudb, \
         patch.object(svc, "refresh_target", new_callable=AsyncMock):
        mock_rudb.get_by_attr.return_value = None
        with pytest.raises((ValueError, RuntimeError)):
            await svc.set_source_mode(_hunt(), meta_round, "banana")
        # No commit or refresh should have happened
        mock_rudb.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["round", "subtree", "explicit"])
async def test_set_source_mode_accepts_all_valid_modes(mode):
    """All three valid modes are accepted without error."""
    svc = _svc()
    meta_round = make_round(id=5, target_puzzle_id=1)
    with patch("bot.core.puzzle_service.RollupsJsonDb") as mock_rudb, \
         patch.object(svc, "refresh_target", new_callable=AsyncMock):
        mock_rudb.get_by_attr.return_value = None
        await svc.set_source_mode(_hunt(), meta_round, mode)
        mock_rudb.commit.assert_called_once()
