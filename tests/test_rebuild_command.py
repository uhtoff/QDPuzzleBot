"""Tests for the !rebuild bang command's orchestration (M5).

The command is thin (the real work is PuzzleService.refresh_meta, tested in
test_refresh_meta.py); these lock its branching: targeted-in-a-meta-channel vs
hunt-wide, meta-only selection, and ok/fail aggregation in the summary.
"""
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from tests.conftest import make_round


def _cog(hunt, service):
    cog = Puzzles.__new__(Puzzles)
    cog.get_hunt = lambda ctx: hunt
    cog.service = service
    return cog


def _ctx(category=None):
    return types.SimpleNamespace(
        channel=types.SimpleNamespace(category=category),
        send=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_hunt_wide_rebuilds_only_metas():
    hunt = types.SimpleNamespace(id=10)
    service = MagicMock(refresh_target=AsyncMock())
    cog = _cog(hunt, service)
    ctx = _ctx(category=None)
    metas = [make_round(id=1, target_puzzle_id=100), make_round(id=2, target_puzzle_id=101)]
    non_meta = make_round(id=3, target_puzzle_id=0)
    with patch("bot.cogs.puzzles.TagGroupJsonDb") as rdb:
        rdb.get_all.return_value = metas + [non_meta]
        await Puzzles.rebuild.callback(cog, ctx)
    assert service.refresh_target.await_count == 2  # the non-meta round is skipped
    assert "Rebuilt 2" in ctx.send.call_args[0][0]


@pytest.mark.asyncio
async def test_targeted_when_in_a_meta_category():
    hunt = types.SimpleNamespace(id=10)
    service = MagicMock(refresh_target=AsyncMock())
    cog = _cog(hunt, service)
    ctx = _ctx(category=types.SimpleNamespace(id=999))
    target = make_round(id=5, target_puzzle_id=50, category_id=999)
    with patch("bot.cogs.puzzles.TagGroupJsonDb") as rdb:
        rdb.get_by_attr.return_value = target
        await Puzzles.rebuild.callback(cog, ctx)
    rdb.get_all.assert_not_called()
    assert service.refresh_target.await_count == 1


@pytest.mark.asyncio
async def test_summary_reports_failures():
    hunt = types.SimpleNamespace(id=10)
    service = MagicMock(refresh_target=AsyncMock(side_effect=[None, RuntimeError("down")]))
    cog = _cog(hunt, service)
    ctx = _ctx(category=None)
    metas = [make_round(id=1, target_puzzle_id=100, group_code="AAA"),
             make_round(id=2, target_puzzle_id=101, group_code="BBB")]
    with patch("bot.cogs.puzzles.TagGroupJsonDb") as rdb:
        rdb.get_all.return_value = metas
        await Puzzles.rebuild.callback(cog, ctx)
    msg = ctx.send.call_args[0][0]
    assert "Rebuilt 1" in msg
    assert "1 still failing" in msg
    assert "BBB" in msg
