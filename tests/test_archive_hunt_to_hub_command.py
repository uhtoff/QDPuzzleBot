"""!archive_hunt_to_hub: hub validated before confirming, summary to the hub."""
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.cogs.puzzles import Puzzles
from bot.core import hunt_archive
from tests.conftest import make_cog, make_ctx


def _hunt():
    h = MagicMock()
    h.name = "H"; h.role_id = 0; h.start_time = None
    h.channel_id = 60; h.layout_mode = "channel"
    return h


def _hub():
    hub = MagicMock(); hub.name = "The Hub"
    hub_channel = MagicMock(); hub_channel.send = AsyncMock()
    hub.get_channel.return_value = hub_channel
    return hub, hub_channel


def _ctx():
    ctx = make_ctx(); ctx.author.display_name = "me"
    return ctx


async def test_refuses_a_bad_hub_without_asking_to_confirm():
    cog = make_cog(hunt=_hunt()); cog.bot = MagicMock()
    cog.service.archive_hunt_to_hub = AsyncMock()
    ctx = _ctx()
    confirm = AsyncMock(return_value=True)
    with patch.object(cog, "_confirm_destructive", new=confirm), \
         patch("bot.cogs.puzzles.hunt_archive.resolve_hub",
               new=AsyncMock(side_effect=hunt_archive.HubUnavailable("you aren't an admin on The Hub"))):
        await Puzzles.archive_hunt_to_hub.callback(cog, ctx, 1000)
    ctx.send.assert_awaited_once_with(":x: you aren't an admin on The Hub")
    confirm.assert_not_awaited()
    cog.service.archive_hunt_to_hub.assert_not_awaited()


async def test_names_the_hub_then_reports_there_even_if_ctx_channel_is_gone():
    hunt = _hunt()
    cog = make_cog(hunt=hunt); cog.bot = MagicMock()
    hub, hub_channel = _hub()
    hub_row = MagicMock()
    cog.service.archive_hunt_to_hub = AsyncMock(return_value=hunt_archive.HuntArchiveReport(
        channels_archived=4, final_name="H - me", hub_channel_id=3000, left_guild=True))
    ctx = _ctx()
    gone = discord.HTTPException(MagicMock(status=404, reason="Not Found"), "Unknown Channel")
    ctx.send = AsyncMock(side_effect=[MagicMock(edit=AsyncMock()), gone])  # status msg, then summary
    confirm = AsyncMock(return_value=True)
    with patch.object(cog, "_confirm_destructive", new=confirm), \
         patch("bot.cogs.puzzles.hunt_archive.resolve_hub",
               new=AsyncMock(return_value=(hub, hub_row))) as resolve, \
         patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb:
        PuzzleDb.get_all_from_hunt.return_value = []
        await Puzzles.archive_hunt_to_hub.callback(cog, ctx, 1000)
    resolve.assert_awaited_once_with(cog.bot, 1000, ctx.guild, ctx.author)
    assert "The Hub" in confirm.await_args.args[1]
    args = cog.service.archive_hunt_to_hub.await_args.args
    assert args[:5] == (ctx.guild, hunt, ctx.author, hub, hub_row)
    hub.get_channel.assert_called_once_with(3000)
    summary = hub_channel.send.await_args.args[0]
    assert "H - me" in summary
    assert "left this server" in summary
