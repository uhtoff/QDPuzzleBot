"""Arrival and late joiners: whoever joins a team server holding the hunt's hub
role gets the team role (however they got in); whoever is given the hub role once
invites have gone out is DM'd one; failures are reported by name, never by link;
and the listener cog never lets an error escape."""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.cogs.team_servers import TeamServers
from bot.core import team_invites

HUNT_ID, TEAM_ROLE, ROSTER, OTHER_HUB_ROLE, TEAM_GUILD = 42, 71, 70, 99, 2000


def _forbidden():
    return discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Cannot send messages to this user")


def _hunt():
    h = MagicMock()
    h.id = HUNT_ID
    h.name = "Big Hunt"
    h.role_id = TEAM_ROLE
    h.roster_role_id = ROSTER
    return h


def _role(role_id, name, guild=None):
    r = MagicMock()
    r.id = role_id
    r.name = name
    r.guild = guild
    return r


class World:
    def __init__(self, hub_roles=(ROSTER,), on_hub=True, held=(), team_hunt=True,
                 here=False, invited=(), dms_open=True, bot_in_team_guild=True, is_bot=False):
        self.hub = MagicMock()
        self.hub.name = "The Hub"
        self.roster = _role(ROSTER, "Team Rocket", self.hub)
        self.hub.get_role.side_effect = lambda i: self.roster if i == ROSTER else None

        self.team_role = _role(TEAM_ROLE, "Team Rocket")
        self.invites = []

        async def create_invite(**kwargs):
            inv = MagicMock()
            inv.code = f"code{len(self.invites) + 1}"
            inv.url = f"https://discord.gg/{inv.code}"
            inv.delete = AsyncMock()
            self.invites.append(inv)
            return inv

        lobby = MagicMock()
        lobby.name = "lobby"
        lobby.create_invite = create_invite
        self.bot_commands = MagicMock()
        self.bot_commands.name = "bot-commands"
        self.bot_commands.send = AsyncMock()
        self.team_guild = MagicMock()
        self.team_guild.id = TEAM_GUILD
        self.team_guild.text_channels = [self.bot_commands, lobby]
        self.team_guild.get_role.side_effect = lambda i: self.team_role if i == TEAM_ROLE else None
        self.team_guild.get_member.side_effect = lambda uid: MagicMock() if here else None

        self.bot = MagicMock()
        self.bot.get_guild.side_effect = (
            lambda gid: self.team_guild if (gid == TEAM_GUILD and bot_in_team_guild) else None)

        # The person, as a member of the team server (arrival) ...
        self.joiner = MagicMock()
        self.joiner.id = 5
        self.joiner.bot = is_bot
        self.joiner.guild = self.team_guild
        self.joiner.roles = [self.team_role] if TEAM_ROLE in held else []
        self.joiner.add_roles = AsyncMock()
        # ... and as a member of the hub (role added there).
        self.hub_person = MagicMock()
        self.hub_person.id = 5
        self.hub_person.bot = is_bot
        self.hub_person.display_name = "Fay"
        self.hub_person.guild = self.hub
        self.hub_person.roles = [_role(i, f"role {i}") for i in hub_roles]
        self.hub_person.send = AsyncMock(side_effect=None if dms_open else _forbidden())

        self.on_hub = on_hub
        self.team_hunt = team_hunt
        self.invited = set(invited)

    def patched(self, stack):
        idb = stack.enter_context(patch("bot.core.team_invites.HuntInviteDb"))
        idb.invited_user_ids.return_value = self.invited
        hdb = stack.enter_context(patch("bot.core.team_invites.HuntJsonDb"))
        hdb.live_team_hunt_for_guild.return_value = _hunt() if self.team_hunt else None
        hdb.live_invited_hunts_for_roster.side_effect = (
            lambda rid: [(_hunt(), TEAM_GUILD)] if rid == ROSTER else [])
        stack.enter_context(patch("bot.core.team_invites.role_panel.find_roster_role",
                                  return_value=self.roster))
        self.hub_member = stack.enter_context(patch(
            "bot.core.team_invites.hunt_archive.hub_member",
            new=AsyncMock(return_value=self.hub_person if self.on_hub else None)))
        return idb, hdb

    async def arrive(self):
        with ExitStack() as stack:
            idb, hdb = self.patched(stack)
            await team_invites.on_join(self.bot, self.joiner)
        return idb, hdb

    async def given(self, *role_ids):
        with ExitStack() as stack:
            idb, hdb = self.patched(stack)
            await team_invites.on_hub_roles_added(self.bot, self.hub_person, set(role_ids))
        return idb, hdb


# --- arrival ---------------------------------------------------------------------

async def test_a_hub_role_holder_gets_the_team_role_on_arrival():
    w = World()
    idb, hdb = await w.arrive()
    hdb.live_team_hunt_for_guild.assert_called_once_with(TEAM_GUILD)
    w.hub_member.assert_awaited_once_with(w.hub, 5)
    assert w.joiner.add_roles.await_args.args == (w.team_role,)
    assert "Big Hunt" in w.joiner.add_roles.await_args.kwargs["reason"]
    idb.mark_joined.assert_called_once_with(HUNT_ID, 5)


async def test_someone_without_the_hub_role_gets_nothing_on_arrival():
    w = World(hub_roles=(OTHER_HUB_ROLE,))
    idb, _ = await w.arrive()
    w.joiner.add_roles.assert_not_awaited()
    idb.mark_joined.assert_not_called()


async def test_someone_not_on_the_hub_gets_nothing_on_arrival():
    w = World(on_hub=False)
    await w.arrive()
    w.joiner.add_roles.assert_not_awaited()


async def test_a_server_without_a_team_hunt_is_ignored():
    w = World(team_hunt=False)
    await w.arrive()
    w.hub_member.assert_not_awaited()
    w.joiner.add_roles.assert_not_awaited()


async def test_a_bot_joining_is_ignored():
    w = World(is_bot=True)
    await w.arrive()
    w.joiner.add_roles.assert_not_awaited()


async def test_arriving_with_the_team_role_already_is_noted_but_not_re_granted():
    w = World(held=(TEAM_ROLE,))
    idb, _ = await w.arrive()
    w.joiner.add_roles.assert_not_awaited()
    idb.mark_joined.assert_called_once_with(HUNT_ID, 5)


# --- given the hub role later ------------------------------------------------------

async def test_someone_given_the_hub_role_is_dmd_an_invite():
    w = World()
    idb, hdb = await w.given(ROSTER)
    hdb.live_invited_hunts_for_roster.assert_called_once_with(ROSTER)
    text = w.hub_person.send.await_args.args[0]
    assert "https://discord.gg/code1" in text and "Team Rocket" in text
    idb.record.assert_called_once_with(HUNT_ID, 5, "code1")


async def test_an_unrelated_role_triggers_nothing():
    w = World()
    await w.given(OTHER_HUB_ROLE)
    w.hub_person.send.assert_not_awaited()
    assert w.invites == []


async def test_someone_already_on_the_team_server_is_not_dmd():
    w = World(here=True)
    await w.given(ROSTER)
    w.hub_person.send.assert_not_awaited()


async def test_someone_already_invited_is_not_dmd_twice():
    w = World(invited={5})
    await w.given(ROSTER)
    w.hub_person.send.assert_not_awaited()


async def test_a_team_server_the_bot_is_not_in_is_skipped():
    w = World(bot_in_team_guild=False)
    await w.given(ROSTER)
    w.hub_person.send.assert_not_awaited()


async def test_a_failed_dm_is_reported_to_the_team_server_by_name_not_link():
    w = World(dms_open=False)
    idb, _ = await w.given(ROSTER)
    w.invites[0].delete.assert_awaited_once()
    idb.record.assert_not_called()
    text = w.bot_commands.send.await_args.args[0]
    assert "Fay" in text and "!invite_player 5" in text
    assert "discord.gg" not in text


async def test_an_uncreatable_invite_says_so_with_the_reason_and_no_link():
    w = World()
    w.team_guild.text_channels = [w.bot_commands]  # no #lobby
    await w.given(ROSTER)
    text = w.bot_commands.send.await_args.args[0]
    assert "couldn't create an invite" in text and "#lobby" in text
    assert "!invite_player 5" in text
    assert "discord.gg" not in text
    assert "couldn't DM" not in text


# --- the listener cog --------------------------------------------------------------

async def test_a_join_is_routed_to_team_invites():
    bot, member = MagicMock(), MagicMock()
    with patch("bot.cogs.team_servers.team_invites.on_join", new=AsyncMock()) as on_join:
        await TeamServers(bot).on_member_join(member)
    on_join.assert_awaited_once_with(bot, member)


async def test_a_join_error_never_escapes():
    with patch("bot.cogs.team_servers.team_invites.on_join",
               new=AsyncMock(side_effect=RuntimeError("boom"))):
        await TeamServers(MagicMock()).on_member_join(MagicMock())


async def test_a_role_update_routes_only_the_added_roles():
    bot = MagicMock()
    kept, added = _role(1, "kept"), _role(2, "added")
    before, after = MagicMock(), MagicMock()
    before.roles, after.roles = [kept], [kept, added]
    with patch("bot.cogs.team_servers.team_invites.on_hub_roles_added", new=AsyncMock()) as handler:
        await TeamServers(bot).on_member_update(before, after)
    handler.assert_awaited_once_with(bot, after, {2})


async def test_a_role_removal_does_nothing():
    before, after = MagicMock(), MagicMock()
    before.roles, after.roles = [_role(1, "a"), _role(2, "b")], [_role(1, "a")]
    with patch("bot.cogs.team_servers.team_invites.on_hub_roles_added", new=AsyncMock()) as handler:
        await TeamServers(MagicMock()).on_member_update(before, after)
    handler.assert_not_awaited()


async def test_a_role_update_error_never_escapes():
    before, after = MagicMock(), MagicMock()
    before.roles, after.roles = [], [_role(2, "b")]
    with patch("bot.cogs.team_servers.team_invites.on_hub_roles_added",
               new=AsyncMock(side_effect=RuntimeError("boom"))):
        await TeamServers(MagicMock()).on_member_update(before, after)
