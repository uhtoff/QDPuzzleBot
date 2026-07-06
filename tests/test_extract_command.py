from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.cogs.puzzles import Puzzles
from bot.store.tag_group_data import TagGroupData


def _cog_and_ctx(tag_group, hunt):
    cog = MagicMock()  # not spec=Puzzles: `service` is an instance attr, not on the class
    cog.get_hunt = MagicMock(return_value=hunt)
    cog.get_tag_group = MagicMock(return_value=tag_group)
    cog.service = MagicMock()
    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.send = AsyncMock()
    return cog, ctx


async def test_extract_command_calls_service_and_acks():
    meta = TagGroupData(name="spooky-meta")
    meta.id = 10
    meta.target_puzzle_id = 1
    hunt = MagicMock()
    cog, ctx = _cog_and_ctx(meta, hunt)
    new_cat = MagicMock(); new_cat.name = "spooky-meta"
    cog.service.extract_target_to_category = AsyncMock(return_value=new_cat)

    await Puzzles.extract_target_to_category.callback(cog, ctx)

    cog.service.extract_target_to_category.assert_awaited_once_with(ctx.guild, hunt, meta)
    ctx.send.assert_awaited()
    assert "spooky-meta" in ctx.send.await_args.args[0]


async def test_extract_command_refuses_outside_a_meta():
    hunt = MagicMock()
    cog, ctx = _cog_and_ctx(None, hunt)  # no tag_group in context
    cog.service.extract_target_to_category = AsyncMock()

    await Puzzles.extract_target_to_category.callback(cog, ctx)

    cog.service.extract_target_to_category.assert_not_awaited()
    ctx.send.assert_awaited()


async def test_extract_command_maps_runtime_error():
    meta = TagGroupData(name="m"); meta.id = 10; meta.target_puzzle_id = 1
    hunt = MagicMock()
    cog, ctx = _cog_and_ctx(meta, hunt)
    cog.service.extract_target_to_category = AsyncMock(
        side_effect=RuntimeError("no_category_space"))

    await Puzzles.extract_target_to_category.callback(cog, ctx)

    ctx.send.assert_awaited()
    assert "category" in ctx.send.await_args.args[0].lower()
