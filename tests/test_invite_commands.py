"""!send_invites and !invite_player: admin commands on a team server that route to
team_invites, refusing anything that has no hub role to invite from."""
from unittest.mock import AsyncMock, MagicMock, patch

from bot.cogs.puzzles import Puzzles
from bot.core.team_invites import InviteReport
from tests.conftest import make_cog, make_ctx


def _hunt(roster_role_id=70, archived=False):
    h = MagicMock()
    h.id = 42
    h.name = "Big Hunt"
    h.roster_role_id = roster_role_id
    h.archived = archived
    return h


def _cog(in_bot_channel=True):
    cog = make_cog()
    cog.bot = MagicMock()
    cog.check_is_bot_channel = AsyncMock(return_value=in_bot_channel)
    return cog


def _sent(ctx):
    return "\n".join(c.args[0] for c in ctx.send.await_args_list)


# --- !send_invites ----------------------------------------------------------------

async def _send_invites(cog, ctx, hunt, report=None):
    with patch("bot.cogs.puzzles.HuntJsonDb") as hdb, \
         patch("bot.cogs.puzzles.team_invites.send_invites",
               new=AsyncMock(return_value=report or InviteReport(sent=["Ann"]))) as run:
        hdb.get_by_name_for_guild.return_value = hunt
        await Puzzles.send_invites.callback(cog, ctx, hunt_name="Big Hunt")
    return hdb, run


async def test_send_invites_runs_and_reports():
    cog, ctx, hunt = _cog(), make_ctx(), _hunt()
    hdb, run = await _send_invites(cog, ctx, hunt)
    hdb.get_by_name_for_guild.assert_called_once_with("Big Hunt", ctx.guild.id)
    run.assert_awaited_once_with(cog.bot, ctx.guild, hunt)
    assert "Ann" in _sent(ctx)


async def test_send_invites_outside_the_bot_channel_does_nothing():
    cog, ctx = _cog(in_bot_channel=False), make_ctx()
    hdb, run = await _send_invites(cog, ctx, _hunt())
    hdb.get_by_name_for_guild.assert_not_called()
    run.assert_not_awaited()


async def test_send_invites_refuses_an_unknown_hunt():
    cog, ctx = _cog(), make_ctx()
    _, run = await _send_invites(cog, ctx, None)
    run.assert_not_awaited()
    assert ":x:" in _sent(ctx)


async def test_send_invites_refuses_an_archived_hunt():
    cog, ctx = _cog(), make_ctx()
    _, run = await _send_invites(cog, ctx, _hunt(archived=True))
    run.assert_not_awaited()
    assert ":x:" in _sent(ctx)


async def test_send_invites_refuses_a_hunt_with_no_hub_role():
    cog, ctx = _cog(), make_ctx()
    _, run = await _send_invites(cog, ctx, _hunt(roster_role_id=0))
    run.assert_not_awaited()
    assert "hub role" in _sent(ctx)


# --- !invite_player ---------------------------------------------------------------

async def _invite_player(cog, ctx, hunt, user):
    with patch("bot.cogs.puzzles.HuntJsonDb") as hdb, \
         patch("bot.cogs.puzzles.team_invites.invite_one",
               new=AsyncMock(return_value="DM'd it")) as one:
        hdb.live_team_hunt_for_guild.return_value = hunt
        await Puzzles.invite_player.callback(cog, ctx, user)
    return hdb, one


async def test_invite_player_routes_with_the_invoker():
    cog, ctx, hunt, user = _cog(), make_ctx(), _hunt(), MagicMock()
    hdb, one = await _invite_player(cog, ctx, hunt, user)
    hdb.live_team_hunt_for_guild.assert_called_once_with(ctx.guild.id)
    one.assert_awaited_once_with(cog.bot, ctx.guild, hunt, user, ctx.author)
    assert "DM'd it" in _sent(ctx)


async def test_invite_player_refuses_a_server_with_no_team_hunt():
    cog, ctx = _cog(), make_ctx()
    _, one = await _invite_player(cog, ctx, None, MagicMock())
    one.assert_not_awaited()
    assert ":x:" in _sent(ctx)


async def test_invite_player_outside_the_bot_channel_does_nothing():
    cog, ctx = _cog(in_bot_channel=False), make_ctx()
    hdb, one = await _invite_player(cog, ctx, _hunt(), MagicMock())
    hdb.live_team_hunt_for_guild.assert_not_called()
    one.assert_not_awaited()
