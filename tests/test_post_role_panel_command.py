"""!post_role_panel: (re)posts the #lobby Join/Leave panel for a hunt made by
!setup_server, and refuses anything the panel couldn't serve."""
from unittest.mock import AsyncMock, MagicMock, patch

from bot.cogs.puzzles import Puzzles
from bot.core import server_setup
from tests.conftest import make_cog, make_ctx


def _hunt(roster_role_id=70, archived=False):
    h = MagicMock()
    h.id = 42
    h.name = "Big Hunt"
    h.role_id = 71
    h.roster_role_id = roster_role_id
    h.archived = archived
    return h


def _ctx(lobby=True, team_role=True):
    ctx = make_ctx()
    lob = MagicMock()
    lob.name = "lobby"
    lob.mention = "#lobby"
    lob.send = AsyncMock()
    cmds = MagicMock()
    cmds.name = "bot-commands"
    ctx.guild.text_channels = [cmds, lob] if lobby else [cmds]
    role = MagicMock()
    role.name = "Team Rocket"
    ctx.guild.get_role.side_effect = lambda i: role if (team_role and i == 71) else None
    return ctx, lob


async def _call(ctx, hunt, in_bot_channel=True):
    cog = make_cog()
    cog.check_is_bot_channel = AsyncMock(return_value=in_bot_channel)
    with patch("bot.cogs.puzzles.HuntJsonDb") as hdb:
        hdb.get_by_name_for_guild.return_value = hunt
        await Puzzles.post_role_panel.callback(cog, ctx, hunt_name="Big Hunt")
    return hdb


def _sent(ctx):
    return "\n".join(c.args[0] for c in ctx.send.await_args_list)


async def test_posts_the_welcome_and_panel_in_lobby():
    ctx, lob = _ctx()
    hdb = await _call(ctx, _hunt())
    hdb.get_by_name_for_guild.assert_called_once_with("Big Hunt", ctx.guild.id)
    lob.send.assert_awaited_once()
    text = lob.send.await_args.args[0]
    view = lob.send.await_args.kwargs["view"]
    assert server_setup.WELCOME_MARK in text and "Team Rocket" in text
    assert [c.custom_id for c in view.children] == ["hunt_role:join:42", "hunt_role:leave:42"]
    assert "#lobby" in _sent(ctx)


async def test_outside_the_bot_channel_does_nothing():
    ctx, lob = _ctx()
    hdb = await _call(ctx, _hunt(), in_bot_channel=False)
    hdb.get_by_name_for_guild.assert_not_called()
    lob.send.assert_not_awaited()


async def test_an_unknown_hunt_refuses():
    ctx, lob = _ctx()
    await _call(ctx, None)
    lob.send.assert_not_awaited()
    assert ":x:" in _sent(ctx)


async def test_a_hunt_with_no_hub_role_refuses():
    ctx, lob = _ctx()
    await _call(ctx, _hunt(roster_role_id=0))
    lob.send.assert_not_awaited()
    assert "hub role" in _sent(ctx)


async def test_an_archived_hunt_refuses():
    ctx, lob = _ctx()
    await _call(ctx, _hunt(archived=True))
    lob.send.assert_not_awaited()
    assert ":x:" in _sent(ctx)


async def test_a_server_without_lobby_refuses():
    ctx, lob = _ctx(lobby=False)
    await _call(ctx, _hunt())
    lob.send.assert_not_awaited()
    assert "#lobby" in _sent(ctx)


async def test_a_missing_team_role_refuses():
    ctx, lob = _ctx(team_role=False)
    await _call(ctx, _hunt())
    lob.send.assert_not_awaited()
    assert ":x:" in _sent(ctx)
