"""M6: the parent_group_id nesting setter rejects containment cycles.

This guards group NESTING only — it never touches the tags/edge_type answer-feeding
graph, so mutual A<->B feeding stays legal. These tests cover the guard, not feeding.
"""
from unittest.mock import MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from tests.conftest import make_round


def _svc():
    return PuzzleService(MagicMock())


@pytest.mark.asyncio
async def test_valid_nesting_sets_parent_and_commits():
    svc = _svc()
    child, parent = make_round(id=1), make_round(id=2)
    with patch("bot.core.puzzle_service.RoundJsonDb") as rdb:
        rdb.get_by_attr.side_effect = lambda **kw: {2: parent}.get(kw["id"])
        await svc.set_parent_group(child, 2)
    assert child.parent_group_id == 2
    rdb.commit.assert_called_once_with(child)


@pytest.mark.asyncio
async def test_self_nest_rejected():
    svc = _svc()
    child = make_round(id=1)
    with patch("bot.core.puzzle_service.RoundJsonDb"):
        with pytest.raises(ValueError):
            await svc.set_parent_group(child, 1)
    assert child.parent_group_id is None


@pytest.mark.asyncio
async def test_descendant_cycle_rejected():
    svc = _svc()
    child, parent = make_round(id=1), make_round(id=2)
    parent.parent_group_id = 1  # round 2 is already nested under round 1
    with patch("bot.core.puzzle_service.RoundJsonDb") as rdb:
        rdb.get_by_attr.side_effect = lambda **kw: {1: child, 2: parent}.get(kw["id"])
        with pytest.raises(ValueError):
            await svc.set_parent_group(child, 2)  # nesting 1 under 2 would cycle
    assert child.parent_group_id is None  # left unchanged
    rdb.commit.assert_not_called()


@pytest.mark.asyncio
async def test_clear_parent_always_allowed():
    svc = _svc()
    child = make_round(id=1)
    child.parent_group_id = 9
    with patch("bot.core.puzzle_service.RoundJsonDb") as rdb:
        await svc.set_parent_group(child, None)
    assert child.parent_group_id is None
    rdb.commit.assert_called_once_with(child)
