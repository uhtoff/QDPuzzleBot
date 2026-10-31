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
    h.archived = False
    h.roster_role_id = 0
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


async def test_refuses_an_archived_hunt_before_resolving_the_hub():
    hunt = _hunt(); hunt.archived = True
    cog = make_cog(hunt=hunt); cog.bot = MagicMock()
    cog.service.archive_hunt_to_hub = AsyncMock()
    ctx = _ctx()
    with patch("bot.cogs.puzzles.hunt_archive.resolve_hub", new=AsyncMock()) as resolve:
        await Puzzles.archive_hunt_to_hub.callback(cog, ctx, 1000)
    ctx.send.assert_awaited_once_with(
        ":x: This hunt is already archived. Moving an in-place archive onto a hub isn't supported yet.")
    resolve.assert_not_awaited()
    cog.service.archive_hunt_to_hub.assert_not_awaited()


async def test_a_failing_hub_send_still_replies_in_ctx():
    hunt = _hunt()
    cog = make_cog(hunt=hunt); cog.bot = MagicMock()
    hub, hub_channel = _hub()
    hub_channel.send = AsyncMock(side_effect=discord.HTTPException(
        MagicMock(status=403, reason="Forbidden"), "Missing Access"))
    cog.service.archive_hunt_to_hub = AsyncMock(return_value=hunt_archive.HuntArchiveReport(
        channels_archived=4, final_name="H - me", hub_channel_id=3000, left_guild=False))
    ctx = _ctx()
    status = MagicMock(edit=AsyncMock())
    ctx.send = AsyncMock(side_effect=[status, MagicMock()])
    with patch.object(cog, "_confirm_destructive", new=AsyncMock(return_value=True)), \
         patch("bot.cogs.puzzles.hunt_archive.resolve_hub",
               new=AsyncMock(return_value=(hub, MagicMock()))), \
         patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb:
        PuzzleDb.get_all_from_hunt.return_value = []
        await Puzzles.archive_hunt_to_hub.callback(cog, ctx, 1000)
    hub_channel.send.assert_awaited_once()
    assert ctx.send.await_count == 2
    assert "H - me" in ctx.send.await_args.args[0]


async def test_a_partial_run_tells_the_admin_to_rerun_from_general():
    hunt = _hunt()
    cog = make_cog(hunt=hunt); cog.bot = MagicMock()
    hub, hub_channel = _hub()
    cog.service.archive_hunt_to_hub = AsyncMock(return_value=hunt_archive.HuntArchiveReport(
        final_name="H - me", hub_channel_id=3000, failures=["x"]))
    ctx = _ctx()
    ctx.send = AsyncMock(side_effect=[MagicMock(edit=AsyncMock()), MagicMock()])
    with patch.object(cog, "_confirm_destructive", new=AsyncMock(return_value=True)), \
         patch("bot.cogs.puzzles.hunt_archive.resolve_hub",
               new=AsyncMock(return_value=(hub, MagicMock()))), \
         patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb:
        PuzzleDb.get_all_from_hunt.return_value = []
        await Puzzles.archive_hunt_to_hub.callback(cog, ctx, 1000)
    summary = ctx.send.await_args.args[0]
    assert "re-run !archive_hunt_to_hub" in summary
    assert "general channel" in summary


async def test_with_no_id_it_archives_to_the_hunt_s_hub():
    hunt = _hunt(); hunt.roster_role_id = 70
    cog = make_cog(hunt=hunt); cog.bot = MagicMock()
    cog.service.archive_hunt_to_hub = AsyncMock()
    roster = MagicMock(); roster.guild.id = 1000
    ctx = _ctx()
    with patch("bot.cogs.puzzles.role_panel.find_roster_role", return_value=roster) as find, \
         patch("bot.cogs.puzzles.hunt_archive.resolve_hub",
               new=AsyncMock(side_effect=hunt_archive.HubUnavailable("stop here"))) as resolve:
        await Puzzles.archive_hunt_to_hub.callback(cog, ctx)
    find.assert_called_once_with(cog.bot, 70)
    resolve.assert_awaited_once_with(cog.bot, 1000, ctx.guild, ctx.author)


async def test_an_id_disagreeing_with_the_hunt_s_hub_is_refused_before_resolving():
    hunt = _hunt(); hunt.roster_role_id = 70
    cog = make_cog(hunt=hunt); cog.bot = MagicMock()
    cog.service.archive_hunt_to_hub = AsyncMock()
    roster = MagicMock(); roster.guild.id = 1000
    ctx = _ctx()
    with patch("bot.cogs.puzzles.role_panel.find_roster_role", return_value=roster), \
         patch("bot.cogs.puzzles.hunt_archive.resolve_hub", new=AsyncMock()) as resolve:
        await Puzzles.archive_hunt_to_hub.callback(cog, ctx, 2000)
    resolve.assert_not_awaited()
    cog.service.archive_hunt_to_hub.assert_not_awaited()
    reply = ctx.send.await_args.args[0]
    assert reply.startswith(":x:") and "1000" in reply


async def test_no_id_on_a_hunt_without_a_hub_role_asks_for_one():
    cog = make_cog(hunt=_hunt()); cog.bot = MagicMock()
    cog.service.archive_hunt_to_hub = AsyncMock()
    ctx = _ctx()
    with patch("bot.cogs.puzzles.hunt_archive.resolve_hub", new=AsyncMock()) as resolve:
        await Puzzles.archive_hunt_to_hub.callback(cog, ctx)
    resolve.assert_not_awaited()
    assert "server id" in ctx.send.await_args.args[0]
