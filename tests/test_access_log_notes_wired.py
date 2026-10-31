"""The bot's own role changes leave access_notes, so the access log files them under
the right `via` (and posts only the quiet-list ones). Each test drives the real
function, then feeds the matching before/after pair to the access-log listener."""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.core import access_log, access_notes, role_panel, team_invites

HUNT_ID, TEAM_ROLE, ROSTER, MOD, USER, TEAM_GUILD = 42, 71, 70, 81, 9, 2000


def _clear():
    access_notes._notes.clear()


def _role(role_id, name):
    r = MagicMock()
    r.id = role_id
    r.name = name
    return r


class World:
    def __init__(self):
        _clear()
        self.hub = MagicMock()
        self.hub.name = "The Hub"
        self.roster = _role(ROSTER, "Team Rocket")
        self.roster.guild = self.hub
        self.team_role = _role(TEAM_ROLE, "Team Rocket")
        self.mod = _role(MOD, "mod")
        self.guild = MagicMock()
        self.guild.id = TEAM_GUILD
        self.guild.name = "Rocket HQ"
        roles = {TEAM_ROLE: self.team_role, MOD: self.mod}
        self.guild.get_role.side_effect = roles.get
        self.guild.owner_id = 1
        self.member = MagicMock()
        self.member.id = USER
        self.member.bot = False
        self.member.display_name = "Fay"
        self.member.guild = self.guild
        self.member.roles = []
        self.member.add_roles = AsyncMock()
        self.member.remove_roles = AsyncMock()
        self.member.kick = AsyncMock()
        self.hunt = MagicMock()
        self.hunt.id, self.hunt.name = HUNT_ID, "Big Hunt"
        self.hunt.role_id, self.hunt.roster_role_id = TEAM_ROLE, ROSTER
        self.hunt.archived = False
        self.hunt.guild_id = 7
        self.bot = MagicMock()
        self.bot.guilds = [MagicMock(get_role=lambda i: self.roster if i == ROSTER else None)]
        self.bot.get_guild.return_value = self.guild

    def log(self):
        """Run the listener against the patched world; returns (rows, post mock)."""
        self.stack = ExitStack()
        s = self.stack
        self.db = s.enter_context(patch("bot.core.access_log.HuntAccessLogDb"))
        hdb = s.enter_context(patch("bot.core.access_log.HuntJsonDb"))
        hdb.live_team_hunt_for_guild.return_value = self.hunt
        self.post = s.enter_context(
            patch("bot.core.access_log.hub_channel.post", new=AsyncMock(return_value=True)))
        s.enter_context(patch("bot.core.role_panel.admin_roles", return_value=[self.mod]))
        return self

    def rows(self):
        return [c.args for c in self.db.record.call_args_list]

    def pair(self, before_roles, after_roles):
        before, after = MagicMock(), MagicMock()
        before.roles, after.roles = list(before_roles), list(after_roles)
        after.id, after.bot, after.display_name, after.guild = USER, False, "Fay", self.guild
        return before, after


async def test_hub_admin_join_writes_grant_and_elevate_quietly():
    w = World()
    hub_member = MagicMock()
    hub_member.roles = [_role(1, "x")]
    with ExitStack() as s:
        s.enter_context(patch("bot.core.role_panel.HuntJsonDb")).get_by_attr.return_value = w.hunt
        s.enter_context(patch("bot.core.role_panel.GuildSettingsDb")).get.return_value = MagicMock(id=7)
        s.enter_context(patch("bot.core.role_panel.role_gate.tier_role_ids", return_value={MOD}))
        s.enter_context(patch("bot.core.role_panel.role_gate.member_holds_tier", return_value=True))
        s.enter_context(patch("bot.core.role_panel.hunt_archive.hub_member",
                              new=AsyncMock(return_value=hub_member)))
        await role_panel.join(w.bot, w.guild, w.member, HUNT_ID)
    w.log()
    with w.stack:
        await access_log.on_roles_changed(w.bot, *w.pair([], [w.team_role, w.mod]))
    assert sorted(r[3:5] for r in w.rows()) == [("elevate", "button"), ("grant", "button")]
    w.post.assert_not_awaited()


async def test_leave_is_quiet():
    w = World()
    w.member.roles = [w.team_role, w.mod]
    with ExitStack() as s:
        s.enter_context(patch("bot.core.role_panel.HuntJsonDb")).get_by_attr.return_value = w.hunt
        s.enter_context(patch("bot.core.role_panel.GuildSettingsDb")).get.return_value = MagicMock(id=7)
        s.enter_context(patch("bot.core.role_panel.role_gate.tier_role_ids", return_value={MOD}))
        await role_panel.leave(w.guild, w.member, HUNT_ID)
    w.log()
    with w.stack:
        await access_log.on_roles_changed(w.bot, *w.pair([w.team_role, w.mod], []))
    assert sorted(r[3:5] for r in w.rows()) == [("demote", "button"), ("revoke", "button")]
    w.post.assert_not_awaited()


async def test_arrival_grant_is_quiet():
    w = World()
    hub_member = MagicMock()
    hub_member.roles = [w.roster]
    with ExitStack() as s:
        s.enter_context(patch("bot.core.team_invites.HuntJsonDb")).live_team_hunt_for_guild.return_value = w.hunt
        s.enter_context(patch("bot.core.team_invites.HuntInviteDb"))
        s.enter_context(patch("bot.core.team_invites.hunt_archive.hub_member",
                              new=AsyncMock(return_value=hub_member)))
        await team_invites.on_join(w.bot, w.member)
    w.log()
    with w.stack:
        await access_log.on_roles_changed(w.bot, *w.pair([], [w.team_role]))
    assert [r[3:5] for r in w.rows()] == [("grant", "arrival")]
    w.post.assert_not_awaited()


async def test_drop_mod_is_logged_as_hub_admin_lost_and_posted():
    w = World()
    w.member.roles = [w.mod]
    w.guild.get_member.return_value = w.member
    w.log()
    with w.stack, patch("bot.core.team_invites._tell_mods", new=AsyncMock()):
        await team_invites._drop_mod(w.guild, w.member, w.roster, w.hunt)
        await access_log.on_roles_changed(w.bot, *w.pair([w.mod], []))
    assert [r[3:5] for r in w.rows()] == [("demote", "hub_admin_lost")]
    w.post.assert_awaited_once()


async def test_kick_note_makes_departure_hub_role_lost():
    w = World()
    w.member.roles = [w.team_role]
    w.guild.get_member.return_value = w.member
    w.log()
    with w.stack, patch("bot.core.team_invites._tell_mods", new=AsyncMock()), \
            patch("bot.core.team_invites.HuntInviteDb"):
        await team_invites._remove_from_team(w.guild, w.member, w.roster, w.hunt)
        await access_log.on_departed(w.bot, w.member)
    assert ("depart", "hub_role_lost") in [r[3:5] for r in w.rows()]
    w.post.assert_awaited_once()


async def test_failures_also_reach_the_hub():
    w = World()
    w.guild.get_member.return_value = None
    channel = MagicMock()
    channel.name = "bot-commands"
    channel.send = AsyncMock()
    w.guild.text_channels = [channel]
    role = _role(ROSTER, "Team Rocket")
    w.hub.get_role.return_value = role
    w.member.guild = w.hub
    with ExitStack() as s:
        s.enter_context(patch("bot.core.team_invites.HuntJsonDb")).live_invited_hunts_for_roster.return_value = [(w.hunt, 5)]
        inv = s.enter_context(patch("bot.core.team_invites.HuntInviteDb"))
        inv.invited_user_ids.return_value = set()
        s.enter_context(patch("bot.core.team_invites.deliver_once", new=AsyncMock(return_value=False)))
        post = s.enter_context(patch("bot.core.team_invites.hub_channel.post", new=AsyncMock()))
        await team_invites.on_hub_roles_added(w.bot, w.member, [ROSTER])
    post.assert_awaited_once()
    assert post.await_args.args[0] is w.hub
    assert "couldn't DM" in post.await_args.args[1]
    channel.send.assert_awaited_once()
    assert "couldn't DM" in channel.send.await_args.args[0]


async def test_failed_kick_reaches_the_hub_too():
    w = World()
    w.guild.get_member.return_value = w.member
    resp = MagicMock()
    resp.status = 403
    w.member.kick = AsyncMock(side_effect=discord.Forbidden(resp, "nope"))
    with patch("bot.core.team_invites._tell_mods", new=AsyncMock()) as tell, \
            patch("bot.core.team_invites.HuntInviteDb"):
        await team_invites._remove_from_team(w.guild, w.member, w.roster, w.hunt)
    assert tell.await_args.kwargs.get("hub") is w.hub


async def test_failed_join_leaves_no_note_to_mislabel_a_later_hand_grant():
    w = World()
    w.member.add_roles = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    hub_member = MagicMock()
    hub_member.roles = [_role(1, "x")]
    with ExitStack() as s:
        s.enter_context(patch("bot.core.role_panel.HuntJsonDb")).get_by_attr.return_value = w.hunt
        s.enter_context(patch("bot.core.role_panel.GuildSettingsDb")).get.return_value = MagicMock(id=7)
        s.enter_context(patch("bot.core.role_panel.role_gate.tier_role_ids", return_value={MOD}))
        s.enter_context(patch("bot.core.role_panel.role_gate.member_holds_tier", return_value=True))
        s.enter_context(patch("bot.core.role_panel.hunt_archive.hub_member",
                              new=AsyncMock(return_value=hub_member)))
        await role_panel.join(w.bot, w.guild, w.member, HUNT_ID)
    assert access_notes._notes == {}
    w.log()
    with w.stack, patch("bot.core.access_log._hand_actor", new=AsyncMock(return_value=None)):
        await access_log.on_roles_changed(w.bot, *w.pair([], [w.mod]))
    assert [r[3:5] for r in w.rows()] == [("elevate", "hand")]
    w.post.assert_awaited_once()
