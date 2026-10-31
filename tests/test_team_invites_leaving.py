"""Leaving a team: whoever loses a hunt's hub role is removed from that hunt's team
server (an unused invite of theirs is deleted instead, if they never arrived), and
whoever loses the hub's Admin tier loses `mod` there. Someone still entitled by the
other route -- hub role or hub admin -- stays. Mods hear about it in #bot-commands."""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.cogs.team_servers import TeamServers
from bot.core import team_invites

HUNT_ID, TEAM_ROLE, ROSTER, HUB_ADMIN, MOD, OTHER = 42, 71, 70, 80, 81, 99
TEAM_GUILD, PERSON = 2000, 5


def _forbidden():
    return discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Missing Permissions")


def _role(role_id, name):
    r = MagicMock()
    r.id = role_id
    r.name = name
    return r


def _hunt():
    h = MagicMock()
    h.id = HUNT_ID
    h.name = "Big Hunt"
    h.role_id = TEAM_ROLE
    h.roster_role_id = ROSTER
    return h


class World:
    def __init__(self, still_held=(), here=True, owner=False, kick_fails=False,
                 pending=(), hub_has_roster=True, is_bot=False):
        self.hub = MagicMock()
        self.hub.name = "The Hub"
        self.roster = _role(ROSTER, "Team Rocket")
        self.hub.get_role.side_effect = (
            lambda i: self.roster if (i == ROSTER and hub_has_roster) else None)

        self.team_role = _role(TEAM_ROLE, "Team Rocket")
        self.mod = _role(MOD, "mod")
        self.bot_commands = MagicMock()
        self.bot_commands.name = "bot-commands"
        self.bot_commands.send = AsyncMock()
        self.team_guild = MagicMock()
        self.team_guild.id = TEAM_GUILD
        self.team_guild.owner_id = PERSON if owner else 1
        self.team_guild.text_channels = [self.bot_commands]
        self.team_guild.get_role.side_effect = (
            lambda i: {TEAM_ROLE: self.team_role, MOD: self.mod}.get(i))

        self.on_team = MagicMock()
        self.on_team.id = PERSON
        self.on_team.display_name = "Fay"
        self.on_team.roles = [self.team_role, self.mod]
        self.on_team.kick = AsyncMock(side_effect=_forbidden() if kick_fails else None)
        self.on_team.remove_roles = AsyncMock()
        self.team_guild.get_member.side_effect = lambda uid: self.on_team if here else None

        self.invites = []
        for code in ("spare", "someone-else"):
            inv = MagicMock()
            inv.code = code
            inv.delete = AsyncMock()
            self.invites.append(inv)
        self.team_guild.invites = AsyncMock(return_value=self.invites)
        self.pending = list(pending)

        self.bot = MagicMock()
        self.bot.get_guild.side_effect = lambda gid: self.team_guild if gid == TEAM_GUILD else None

        self.person = MagicMock()
        self.person.id = PERSON
        self.person.bot = is_bot
        self.person.display_name = "Fay"
        self.person.guild = self.hub
        self.person.roles = [_role(i, f"role {i}") for i in still_held]

    async def lose(self, *role_ids):
        tiers = {id(self.hub): {HUB_ADMIN}, id(self.team_guild): {MOD}}
        with ExitStack() as stack:
            idb = stack.enter_context(patch("bot.core.team_invites.HuntInviteDb"))
            idb.pending_codes.return_value = self.pending
            hdb = stack.enter_context(patch("bot.core.team_invites.HuntJsonDb"))
            hdb.live_team_hunts.return_value = [(_hunt(), TEAM_GUILD)]
            stack.enter_context(patch("bot.core.role_gate.tier_role_ids",
                                      side_effect=lambda g, names: tiers.get(id(g), set())))
            await team_invites.on_hub_roles_removed(self.bot, self.person, set(role_ids))
        self.idb = idb
        return idb

    def told(self):
        return " ".join(c.args[0] for c in self.bot_commands.send.await_args_list)


# --- losing the hub role ------------------------------------------------------------

async def test_losing_the_hub_role_removes_them_from_the_team_server():
    w = World()
    idb = await w.lose(ROSTER)
    w.on_team.kick.assert_awaited_once()
    reason = w.on_team.kick.await_args.kwargs["reason"]
    assert "Team Rocket" in reason and "Big Hunt" in reason
    idb.forget.assert_called_once_with(HUNT_ID, PERSON)
    assert "Fay" in w.told() and "Team Rocket" in w.told()


async def test_a_hub_admin_who_loses_the_hub_role_stays():
    w = World(still_held=(HUB_ADMIN,))
    idb = await w.lose(ROSTER)
    w.on_team.kick.assert_not_awaited()
    w.on_team.remove_roles.assert_not_awaited()
    idb.forget.assert_not_called()


async def test_losing_an_unrelated_role_does_nothing():
    w = World()
    idb = await w.lose(OTHER)
    w.on_team.kick.assert_not_awaited()
    w.on_team.remove_roles.assert_not_awaited()
    idb.forget.assert_not_called()


async def test_someone_who_never_arrived_has_their_unused_invite_deleted():
    w = World(here=False, pending=["spare"])
    idb = await w.lose(ROSTER)
    spare, other = w.invites
    spare.delete.assert_awaited_once()
    other.delete.assert_not_awaited()
    idb.forget.assert_called_once_with(HUNT_ID, PERSON)


async def test_someone_never_invited_and_not_here_costs_no_invite_lookup():
    w = World(here=False, pending=[])
    await w.lose(ROSTER)
    w.team_guild.invites.assert_not_awaited()


async def test_the_server_owner_cannot_be_removed_so_loses_the_roles_and_mods_are_told():
    w = World(owner=True)
    await w.lose(ROSTER)
    w.on_team.kick.assert_not_awaited()
    removed = set(w.on_team.remove_roles.await_args.args)
    assert removed == {w.team_role, w.mod}
    assert "owner" in w.told().lower()


async def test_a_failed_removal_is_reported_to_the_mods():
    w = World(kick_fails=True)
    await w.lose(ROSTER)
    assert "couldn't" in w.told().lower() and "Fay" in w.told()


async def test_a_bot_losing_a_role_is_ignored():
    w = World(is_bot=True)
    await w.lose(ROSTER)
    w.on_team.kick.assert_not_awaited()


# --- losing hub admin -------------------------------------------------------------

async def test_losing_hub_admin_but_keeping_the_hub_role_loses_mod_only():
    w = World(still_held=(ROSTER,))
    idb = await w.lose(HUB_ADMIN)
    w.on_team.kick.assert_not_awaited()
    assert w.on_team.remove_roles.await_args.args == (w.mod,)
    idb.forget.assert_not_called()


async def test_losing_hub_admin_without_the_hub_role_removes_them():
    w = World()
    await w.lose(HUB_ADMIN)
    w.on_team.kick.assert_awaited_once()


async def test_losing_admin_on_a_server_that_is_not_this_hunts_hub_does_nothing():
    w = World(hub_has_roster=False)
    await w.lose(HUB_ADMIN)
    w.on_team.kick.assert_not_awaited()
    w.on_team.remove_roles.assert_not_awaited()


async def test_losing_both_at_once_removes_them():
    w = World()
    await w.lose(ROSTER, HUB_ADMIN)
    w.on_team.kick.assert_awaited_once()


# --- the listener ------------------------------------------------------------------

def _member(role_ids):
    m = MagicMock()
    m.id = PERSON
    m.roles = [_role(i, f"role {i}") for i in role_ids]
    return m


async def test_the_cog_routes_removed_roles_and_swallows_errors():
    cog = TeamServers(MagicMock())
    before, after = _member([ROSTER, OTHER]), _member([OTHER])
    with patch("bot.cogs.team_servers.team_invites.on_hub_roles_removed",
               new=AsyncMock(side_effect=RuntimeError("boom"))) as removed, \
         patch("bot.cogs.team_servers.team_invites.on_hub_roles_added", new=AsyncMock()) as added:
        await cog.on_member_update(before, after)
    removed.assert_awaited_once_with(cog.bot, after, {ROSTER})
    added.assert_not_awaited()


@pytest.mark.parametrize("before_ids, after_ids", [([OTHER], [OTHER, ROSTER])])
async def test_the_cog_does_not_call_removal_when_nothing_was_removed(before_ids, after_ids):
    cog = TeamServers(MagicMock())
    with patch("bot.cogs.team_servers.team_invites.on_hub_roles_removed", new=AsyncMock()) as removed, \
         patch("bot.cogs.team_servers.team_invites.on_hub_roles_added", new=AsyncMock()):
        await cog.on_member_update(_member(before_ids), _member(after_ids))
    removed.assert_not_awaited()
