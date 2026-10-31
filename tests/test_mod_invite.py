"""The reusable mod invite: ensure/post/delete, and the !mod_invite command."""
import discord
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from bot.core import mod_invite
from bot.core.team_invites import InviteUnavailable
from tests.conftest import make_cog, make_ctx


def _hunt(id=7, name="Big Hunt", role_id=5, roster=70):
    h = MagicMock(); h.id = id; h.name = name; h.role_id = role_id; h.roster_role_id = roster
    return h


def _invite(code, url=None):
    i = MagicMock(); i.code = code; i.url = url or f"https://discord.gg/{code}"; i.delete = AsyncMock()
    return i


def _guild(invites=(), lobby=True):
    g = MagicMock(); g.id = 2000; g.name = "Team Server"
    g.invites = AsyncMock(return_value=list(invites))
    if lobby:
        ch = MagicMock(); ch.name = "lobby"
        ch.create_invite = AsyncMock(return_value=_invite("NEW"))
        g.text_channels = [ch]
        g.lobby = ch
    else:
        g.text_channels = []
    role = MagicMock(); role.name = "Team Rocket"
    g.get_role.return_value = role
    return g


@pytest.fixture
def hdb():
    with patch("bot.core.mod_invite.HuntJsonDb") as m:
        m.mod_invite_code.return_value = None
        yield m


async def test_ensure_makes_a_reusable_lobby_invite_and_stores_it(hdb):
    g, hunt = _guild(), _hunt()
    inv = await mod_invite.ensure(g, hunt)
    kw = g.lobby.create_invite.await_args.kwargs
    assert kw["max_uses"] == 0 and kw["max_age"] == 0 and kw["unique"] is True
    assert kw["reason"] == "mod invite: Big Hunt"
    hdb.set_mod_invite_code.assert_called_once_with(7, "NEW")
    assert inv.code == "NEW"


async def test_ensure_reuses_a_live_stored_invite(hdb):
    stored = _invite("OLD")
    g = _guild(invites=[_invite("other"), stored])
    hdb.mod_invite_code.return_value = "OLD"
    assert await mod_invite.ensure(g, _hunt()) is stored
    g.lobby.create_invite.assert_not_awaited()
    hdb.set_mod_invite_code.assert_not_called()


async def test_a_vanished_invite_is_replaced_and_stored(hdb):
    g = _guild(invites=[_invite("other")])
    hdb.mod_invite_code.return_value = "GONE"
    inv = await mod_invite.ensure(g, _hunt())
    assert inv.code == "NEW"
    hdb.set_mod_invite_code.assert_called_once_with(7, "NEW")


async def test_no_lobby_raises_invite_unavailable(hdb):
    with pytest.raises(InviteUnavailable, match="#lobby"):
        await mod_invite.ensure(_guild(lobby=False), _hunt())


async def test_a_failed_create_raises_invite_unavailable(hdb):
    g = _guild()
    g.lobby.create_invite.side_effect = discord.HTTPException(MagicMock(status=500), "boom")
    with pytest.raises(InviteUnavailable):
        await mod_invite.ensure(g, _hunt())


def _hub():
    hub = MagicMock(); hub.name = "The Hub"
    return hub


async def test_post_refuses_a_visible_channel_without_making_an_invite(hdb):
    g, hub = _guild(), _hub()
    bot = MagicMock()
    with patch("bot.core.mod_invite.role_panel.find_roster_role", return_value=SimpleNamespace(guild=hub)), \
         patch("bot.core.mod_invite.hub_channel.find", return_value=(None, "#admin is visible to everyone")), \
         patch("bot.core.mod_invite.hub_channel.post", new=AsyncMock()) as post:
        reply = await mod_invite.post(bot, g, _hunt())
    g.lobby.create_invite.assert_not_awaited()
    post.assert_not_awaited()
    assert "#admin is visible to everyone" in reply


async def test_post_puts_the_link_in_the_admin_channel(hdb):
    g, hub = _guild(), _hub()
    chan = MagicMock(); chan.name = "admin"
    with patch("bot.core.mod_invite.role_panel.find_roster_role", return_value=SimpleNamespace(guild=hub)), \
         patch("bot.core.mod_invite.hub_channel.find", return_value=(chan, "")), \
         patch("bot.core.mod_invite.hub_channel.post", new=AsyncMock(return_value=True)) as post:
        reply = await mod_invite.post(MagicMock(), g, _hunt())
    hub_arg, text = post.await_args.args
    assert hub_arg is hub
    assert "https://discord.gg/NEW" in text and "Big Hunt" in text and "Join" in text
    assert "Team Server" in text and "Team Rocket" in text
    assert "discord.gg" not in reply


async def test_delete_removes_and_clears(hdb):
    stored = _invite("OLD")
    g = _guild(invites=[stored])
    hdb.mod_invite_code.return_value = "OLD"
    await mod_invite.delete(g, _hunt())
    stored.delete.assert_awaited_once()
    hdb.set_mod_invite_code.assert_called_once_with(7, None)


async def test_delete_with_nothing_stored_is_a_no_op(hdb):
    g = _guild()
    await mod_invite.delete(g, _hunt())
    g.invites.assert_not_awaited()
    hdb.set_mod_invite_code.assert_not_called()


async def test_delete_never_raises(hdb):
    g = _guild()
    g.invites.side_effect = RuntimeError("boom")
    hdb.mod_invite_code.return_value = "OLD"
    await mod_invite.delete(g, _hunt())


# --- !mod_invite -------------------------------------------------------------------

def _cog(guilds):
    cog = make_cog()
    cog.bot = MagicMock()
    cog.bot.get_guild.side_effect = guilds.get
    cog.check_is_bot_channel = AsyncMock(return_value=True)
    return cog


async def _run(cog, ctx, live, name="big hunt"):
    with patch("bot.cogs.puzzles.HuntJsonDb") as hdb, \
         patch("bot.cogs.puzzles.mod_invite.post", new=AsyncMock(return_value="ok")) as post:
        hdb.live_team_hunts.return_value = live
        await Puzzles.mod_invite.callback(cog, ctx, hunt_name=name)
    return post


async def test_mod_invite_command_finds_the_hunt_on_this_hub_only():
    ours, theirs = _hunt(id=1, roster=70), _hunt(id=2, roster=80)
    ctx = make_ctx()
    ctx.guild.get_role.side_effect = lambda rid: object() if rid == 70 else None
    team = MagicMock()
    cog = _cog({2000: team, 3000: MagicMock()})
    post = await _run(cog, ctx, [(theirs, 3000), (ours, 2000)])
    post.assert_awaited_once_with(cog.bot, team, ours)
    ctx.send.assert_awaited_once_with("ok")


async def test_mod_invite_command_refuses_an_ambiguous_name():
    a, b = _hunt(id=1), _hunt(id=2)
    ctx = make_ctx()
    ga, gb = MagicMock(), MagicMock(); ga.name = "Alpha"; gb.name = "Beta"
    cog = _cog({2000: ga, 3000: gb})
    post = await _run(cog, ctx, [(a, 2000), (b, 3000)])
    post.assert_not_awaited()
    msg = ctx.send.await_args.args[0]
    assert "Alpha" in msg and "Beta" in msg


async def test_mod_invite_command_with_no_match_says_so():
    ctx = make_ctx()
    cog = _cog({})
    post = await _run(cog, ctx, [], name="Nope")
    post.assert_not_awaited()
    assert "No running team hunt named **Nope** on this hub" in ctx.send.await_args.args[0]


async def test_mod_invite_command_when_bot_not_on_team_server():
    ctx = make_ctx()
    cog = _cog({})
    post = await _run(cog, ctx, [(_hunt(), 2000)])
    post.assert_not_awaited()
    assert "not on" in ctx.send.await_args.args[0]
