from unittest.mock import AsyncMock, MagicMock

from bot.cogs.puzzles import Puzzles
from bot.store.tag_group_data import TagGroupData


def _cog_and_ctx(meta, hunt, puzzle, dest):
    cog = MagicMock()
    cog.get_hunt = MagicMock(return_value=hunt)
    cog.get_tag_group = MagicMock(return_value=meta)
    cog.get_puzzle = MagicMock(return_value=puzzle)
    cog.resolve_target_group = AsyncMock(return_value=dest)
    cog.service = MagicMock()
    ctx = MagicMock(); ctx.guild = MagicMock(); ctx.send = AsyncMock()
    return cog, ctx


def _meta():
    m = TagGroupData(name="spooky-meta"); m.id = 10; m.target_puzzle_id = 1
    return m


async def test_consolidate_calls_service_and_acks():
    meta = _meta(); hunt = MagicMock()
    target = MagicMock(); target.id = 1
    dest = TagGroupData(name="round-a"); dest.id = 20
    cog, ctx = _cog_and_ctx(meta, hunt, target, dest)
    cog.service.consolidate_to = AsyncMock(return_value=dest)

    await Puzzles.consolidate_to.callback(cog, ctx, "ROUNDA")

    cog.service.consolidate_to.assert_awaited_once_with(ctx.guild, hunt, meta, dest, force=False)
    ctx.send.assert_awaited()


async def test_consolidate_refuses_outside_target_channel():
    meta = _meta(); hunt = MagicMock()
    feeder = MagicMock(); feeder.id = 999
    dest = TagGroupData(name="round-a"); dest.id = 20
    cog, ctx = _cog_and_ctx(meta, hunt, feeder, dest)
    cog.service.consolidate_to = AsyncMock()

    await Puzzles.consolidate_to.callback(cog, ctx, "ROUNDA")

    cog.service.consolidate_to.assert_not_awaited()
    ctx.send.assert_awaited()


async def test_consolidate_refuses_self_destination():
    meta = _meta(); hunt = MagicMock()
    target = MagicMock(); target.id = 1
    cog, ctx = _cog_and_ctx(meta, hunt, target, meta)  # dest resolves to the same meta
    cog.service.consolidate_to = AsyncMock()

    await Puzzles.consolidate_to.callback(cog, ctx, "SELF")

    cog.service.consolidate_to.assert_not_awaited()
    ctx.send.assert_awaited()


async def test_consolidate_confirms_on_would_overflow_then_forces():
    meta = _meta(); hunt = MagicMock()
    target = MagicMock(); target.id = 1
    dest = TagGroupData(name="round-a"); dest.id = 20
    cog, ctx = _cog_and_ctx(meta, hunt, target, dest)
    cog.service.consolidate_to = AsyncMock(side_effect=[RuntimeError("would_overflow"), dest])
    cog._confirm_destructive = AsyncMock(return_value=True)

    await Puzzles.consolidate_to.callback(cog, ctx, "ROUNDA")

    assert cog.service.consolidate_to.await_count == 2
    assert cog.service.consolidate_to.await_args_list[1].kwargs.get("force") is True
    # The overflow confirm button must read "consolidate", not the default "delete".
    assert cog._confirm_destructive.await_args.kwargs.get("confirm_label") == "Confirm consolidate"


async def test_consolidate_overflow_cancelled_does_not_force():
    meta = _meta(); hunt = MagicMock()
    target = MagicMock(); target.id = 1
    dest = TagGroupData(name="round-a"); dest.id = 20
    cog, ctx = _cog_and_ctx(meta, hunt, target, dest)
    cog.service.consolidate_to = AsyncMock(side_effect=RuntimeError("would_overflow"))
    cog._confirm_destructive = AsyncMock(return_value=False)

    await Puzzles.consolidate_to.callback(cog, ctx, "ROUNDA")

    assert cog.service.consolidate_to.await_count == 1
    ctx.send.assert_awaited()
