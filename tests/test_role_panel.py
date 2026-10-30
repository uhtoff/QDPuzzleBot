"""role_panel.join / leave: Join is for the hunt's roster role on the hub, or a
hub admin (who also gets this server's Admin-tier role); Leave takes both away.
Nobody else is granted anything."""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.core import role_panel

HUNT_ID, GUILD_ROW, TEAM_ROLE, MOD, ROSTER, OTHER_HUB_ROLE, USER = 42, 7, 71, 50, 70, 99, 9


def _role(role_id, name, guild=None):
    r = MagicMock()
    r.id = role_id
    r.name = name
    r.guild = guild
    return r


def _hunt(archived=False, roster_role_id=ROSTER, guild_id=GUILD_ROW):
    h = MagicMock()
    h.id = HUNT_ID
    h.name = "Big Hunt"
    h.role_id = TEAM_ROLE
    h.roster_role_id = roster_role_id
    h.guild_id = guild_id
    h.archived = archived
    return h


class World:
    """A team server and a hub, with one person on each. By default, an ordinary
    roster player who holds nothing on the team server yet."""

    def __init__(self, hub_roles=(ROSTER,), held=(), on_hub=True, hub_admin=False,
                 hunt=None, roster_found=True):
        self.hub = MagicMock()
        self.hub.name = "The Hub"
        self.roster = _role(ROSTER, "Team Rocket", self.hub)
        self.hub.get_role.side_effect = (
            lambda i: self.roster if (i == ROSTER and roster_found) else None)
        unrelated = MagicMock()
        unrelated.get_role.return_value = None
        self.bot = MagicMock()
        self.bot.guilds = [unrelated, self.hub]

        self.team_role = _role(TEAM_ROLE, "Team Rocket")
        self.mod = _role(MOD, "mod")
        roles = {TEAM_ROLE: self.team_role, MOD: self.mod}
        self.guild = MagicMock()
        self.guild.id = 2000
        self.guild.get_role.side_effect = roles.get

        self.member = MagicMock()
        self.member.id = USER
        self.member.roles = [roles[i] for i in held]
        self.member.add_roles = AsyncMock()
        self.member.remove_roles = AsyncMock()

        self.hub_member = None
        if on_hub:
            self.hub_member = MagicMock()
            self.hub_member.roles = [_role(i, f"role {i}") for i in hub_roles]
        self.hub_admin = hub_admin
        self.hunt = hunt or _hunt()

    def _patched(self, stack):
        hdb = stack.enter_context(patch("bot.core.role_panel.HuntJsonDb"))
        hdb.get_by_attr.return_value = self.hunt
        gdb = stack.enter_context(patch("bot.core.role_panel.GuildSettingsDb"))
        gdb.get.return_value = MagicMock(id=GUILD_ROW)
        stack.enter_context(patch(
            "bot.core.role_panel.role_gate.tier_role_ids",
            side_effect=lambda g, names: frozenset({MOD}) if g is self.guild else frozenset()))
        stack.enter_context(patch(
            "bot.core.role_panel.role_gate.member_holds_tier",
            side_effect=lambda ids, g, names: g is self.hub and self.hub_admin))
        stack.enter_context(patch(
            "bot.core.role_panel.hunt_archive.hub_member",
            new=AsyncMock(return_value=self.hub_member)))
        return hdb

    async def join(self):
        with ExitStack() as stack:
            hdb = self._patched(stack)
            reply = await role_panel.join(self.bot, self.guild, self.member, HUNT_ID)
        hdb.get_by_attr.assert_called_once_with(id=HUNT_ID)
        return reply

    async def leave(self):
        with ExitStack() as stack:
            self._patched(stack)
            return await role_panel.leave(self.guild, self.member, HUNT_ID)


# --- find_roster_role ---------------------------------------------------------

def test_find_roster_role_looks_in_every_server_the_bot_is_in():
    w = World()
    assert role_panel.find_roster_role(w.bot, ROSTER) is w.roster


def test_find_roster_role_is_none_when_no_server_has_it():
    w = World(roster_found=False)
    assert role_panel.find_roster_role(w.bot, ROSTER) is None


# --- join: who gets in ---------------------------------------------------------

async def test_a_roster_player_gets_the_team_role_only():
    w = World()
    reply = await w.join()
    w.member.add_roles.assert_awaited_once()
    assert w.member.add_roles.await_args.args == (w.team_role,)
    assert "Big Hunt" in w.member.add_roles.await_args.kwargs["reason"]
    assert "Team Rocket" in reply


async def test_a_hub_admin_gets_the_team_role_and_mod():
    w = World(hub_roles=(OTHER_HUB_ROLE,), hub_admin=True)
    await w.join()
    assert w.member.add_roles.await_args.args == (w.team_role, w.mod)


async def test_a_hub_admin_who_already_plays_still_gets_mod():
    w = World(hub_admin=True, held=(TEAM_ROLE,))
    await w.join()
    assert w.member.add_roles.await_args.args == (w.mod,)


async def test_a_hub_member_without_the_roster_role_is_refused():
    w = World(hub_roles=(OTHER_HUB_ROLE,))
    reply = await w.join()
    w.member.add_roles.assert_not_awaited()
    assert "Team Rocket" in reply and "The Hub" in reply


async def test_someone_not_on_the_hub_is_refused():
    w = World(on_hub=False)
    reply = await w.join()
    w.member.add_roles.assert_not_awaited()
    assert "The Hub" in reply


async def test_a_roster_role_no_server_has_refuses():
    w = World(roster_found=False)
    await w.join()
    w.member.add_roles.assert_not_awaited()


async def test_a_hunt_with_no_roster_role_refuses():
    w = World(hunt=_hunt(roster_role_id=0))
    await w.join()
    w.member.add_roles.assert_not_awaited()


async def test_an_archived_hunt_refuses_join():
    w = World(hunt=_hunt(archived=True))
    reply = await w.join()
    w.member.add_roles.assert_not_awaited()
    assert "over" in reply


async def test_a_hunt_on_another_server_refuses_join():
    w = World(hunt=_hunt(guild_id=GUILD_ROW + 1))
    await w.join()
    w.member.add_roles.assert_not_awaited()


async def test_already_holding_the_team_role_is_a_no_op():
    w = World(held=(TEAM_ROLE,))
    reply = await w.join()
    w.member.add_roles.assert_not_awaited()
    assert "already" in reply


async def test_a_role_the_bot_cannot_grant_names_the_fix():
    w = World()
    w.member.add_roles.side_effect = discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "no")
    reply = await w.join()
    assert "drag my role" in reply


# --- leave ---------------------------------------------------------------------

async def test_leave_removes_the_team_role_and_mod():
    w = World(held=(TEAM_ROLE, MOD))
    await w.leave()
    assert w.member.remove_roles.await_args.args == (w.team_role, w.mod)
    assert "Big Hunt" in w.member.remove_roles.await_args.kwargs["reason"]


async def test_leave_removes_only_what_is_held():
    w = World(held=(TEAM_ROLE,))
    await w.leave()
    assert w.member.remove_roles.await_args.args == (w.team_role,)


async def test_leave_with_nothing_held_is_a_no_op():
    w = World()
    reply = await w.leave()
    w.member.remove_roles.assert_not_awaited()
    assert "nothing to leave" in reply


async def test_leave_is_not_gated_on_the_hub():
    w = World(on_hub=False, held=(TEAM_ROLE,))
    await w.leave()
    w.member.remove_roles.assert_awaited_once()


async def test_a_hunt_on_another_server_refuses_leave():
    w = World(hunt=_hunt(guild_id=GUILD_ROW + 1), held=(TEAM_ROLE,))
    await w.leave()
    w.member.remove_roles.assert_not_awaited()
