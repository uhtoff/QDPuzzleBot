from unittest.mock import AsyncMock, MagicMock

from bot.cogs.puzzles import Puzzles


def _cog_and_ctx(hunt):
    cog = MagicMock()
    cog.get_hunt = MagicMock(return_value=hunt)
    cog.service = MagicMock()
    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.send = AsyncMock()
    return cog, ctx


async def test_backfill_calls_service_and_reports():
    hunt = MagicMock()
    cog, ctx = _cog_and_ctx(hunt)
    cog.service.backfill_glyphs = AsyncMock(return_value=3)

    await Puzzles.backfill_glyphs.callback(cog, ctx)

    cog.service.backfill_glyphs.assert_awaited_once_with(ctx.guild, hunt)
    ctx.send.assert_awaited()
    assert "3" in ctx.send.await_args.args[0]


async def test_backfill_refuses_outside_hunt():
    cog, ctx = _cog_and_ctx(None)
    cog.service.backfill_glyphs = AsyncMock()

    await Puzzles.backfill_glyphs.callback(cog, ctx)

    cog.service.backfill_glyphs.assert_not_awaited()
    ctx.send.assert_awaited()
