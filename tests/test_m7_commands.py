"""Thin tests for M7 Discord setter commands (!nest, !source_mode, !pool).

Mocks the service layer; verifies round resolution, role gating, and calls to
the right PuzzleService method. Does not test the service itself (covered by
test_refresh_meta.py / test_rollup_source_mode.py / test_subtree_walk.py)."""
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from tests.conftest import make_round


def _cog(hunt, service):
    cog = Puzzles.__new__(Puzzles)
    cog.get_hunt = lambda ctx: hunt
    cog.get_channel_type = lambda ctx: "Puzzle"
    cog.service = service
    return cog


def _ctx(channel_type="Puzzle", category=None):
    return types.SimpleNamespace(
        channel=types.SimpleNamespace(category=category),
        send=AsyncMock(),
    )


class TestNestCommand:
    @pytest.mark.asyncio
    async def test_nest_calls_set_parent_group(self):
        hunt = types.SimpleNamespace(id=10)
        service = MagicMock(set_parent_group=AsyncMock())
        cog = _cog(hunt, service)
        ctx = _ctx()
        child = make_round(id=5, meta_code="CHILD")
        parent = make_round(id=3, meta_code="PARENT")
        with patch("bot.cogs.puzzles.RoundJsonDb") as rdb:
            def get_by_meta(code):
                if code == "CHILD":
                    return child
                if code == "PARENT":
                    return parent
                return None
            rdb.get_by_attr.side_effect = lambda **kw: get_by_meta(kw.get("meta_code"))
            await Puzzles.nest.callback(cog, ctx, "CHILD", "PARENT")
        service.set_parent_group.assert_awaited_once_with(child, 3)
        assert "nested under" in ctx.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_nest_clear_when_none(self):
        hunt = types.SimpleNamespace(id=10)
        service = MagicMock(set_parent_group=AsyncMock())
        cog = _cog(hunt, service)
        ctx = _ctx()
        child = make_round(id=5, meta_code="CHILD")
        with patch("bot.cogs.puzzles.RoundJsonDb") as rdb:
            rdb.get_by_attr.return_value = child
            await Puzzles.nest.callback(cog, ctx, "CHILD", "none")
        service.set_parent_group.assert_awaited_once_with(child, None)
        assert "cleared" in ctx.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_nest_rejects_invalid_child(self):
        hunt = types.SimpleNamespace(id=10)
        service = MagicMock(set_parent_group=AsyncMock())
        cog = _cog(hunt, service)
        ctx = _ctx()
        with patch("bot.cogs.puzzles.RoundJsonDb") as rdb:
            rdb.get_by_attr.return_value = None
            await Puzzles.nest.callback(cog, ctx, "BADCODE", "PARENT")
        service.set_parent_group.assert_not_awaited()
        assert "Invalid child" in ctx.send.call_args[0][0]


class TestSourceModeCommand:
    @pytest.mark.asyncio
    async def test_source_mode_sets_and_refreshes(self):
        hunt = types.SimpleNamespace(id=10)
        service = MagicMock(refresh_meta=AsyncMock())
        cog = _cog(hunt, service)
        ctx = _ctx()
        meta_round = make_round(id=5, meta_code="META", meta_id=99)
        rollup = MagicMock(round_id=5, source_mode="round")
        with patch("bot.cogs.puzzles.RoundJsonDb") as rdb, \
             patch("bot.cogs.puzzles.RollupsJsonDb") as rudb:
            rdb.get_by_attr.return_value = meta_round
            rudb.get_by_attr.return_value = rollup
            await Puzzles.source_mode.callback(cog, ctx, "META", "subtree")
        assert rollup.source_mode == "subtree"
        rudb.commit.assert_called_once_with(rollup)
        service.refresh_meta.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_source_mode_rejects_invalid_mode(self):
        hunt = types.SimpleNamespace(id=10)
        service = MagicMock(refresh_meta=AsyncMock())
        cog = _cog(hunt, service)
        ctx = _ctx()
        await Puzzles.source_mode.callback(cog, ctx, "META", "banana")
        assert "one of" in ctx.send.call_args[0][0].lower()


class TestPoolCommand:
    @pytest.mark.asyncio
    async def test_pool_tags_with_aggregates_into(self):
        hunt = types.SimpleNamespace(id=10)
        service = MagicMock(refresh_metas=AsyncMock())
        cog = _cog(hunt, service)
        ctx = _ctx()
        puzzle = MagicMock(
            id=42,
            name="Pooled",
            tags=[5],
            hunt_id=10,
            channel_id=111,
        )
        meta_round = make_round(id=5, meta_id=99)
        cog.get_puzzle = lambda _ctx: puzzle
        with patch("bot.cogs.puzzles.RoundJsonDb") as rdb, \
             patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb:
            # get_by_attr called for both channel lookup and meta round resolution
            def get_round(**kw):
                kid = kw.get("id")
                if kid == 5:
                    return meta_round
                if kw.get("meta_code"):
                    return None
                return None
            rdb.get_by_attr.side_effect = get_round

            await Puzzles.pool.callback(cog, ctx)
        pdb.tag_puzzle_with_edge.assert_called_once_with(42, 5, "aggregates-into")

    @pytest.mark.asyncio
    async def test_pool_rejects_hunt_channel(self):
        hunt = types.SimpleNamespace(id=10)
        service = MagicMock(refresh_metas=AsyncMock())
        cog = _cog(hunt, service)
        cog.get_channel_type = lambda ctx: "Hunt"
        ctx = _ctx(channel_type="Hunt")
        await Puzzles.pool.callback(cog, ctx)
        assert "Puzzle channel" in ctx.send.call_args[0][0]
