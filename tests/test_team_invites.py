"""team_invites: !send_invites and !invite_player DM private single-use invites to
holders of the hunt's hub role, record who was DM'd, leave nothing behind when a
DM fails, and never put a link anywhere but a DM."""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, call, patch

import discord

from bot.core import team_invites
from bot.core.team_invites import InviteReport

HUNT_ID, ROSTER, OTHER_HUB_ROLE = 42, 70, 99


def _forbidden():
    return discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Cannot send messages to this user")


def _person(uid, name, dms_open=True, bot=False):
    p = MagicMock()
    p.id = uid
    p.display_name = name
    p.bot = bot
    p.send = AsyncMock(side_effect=None if dms_open else _forbidden())
    return p


def _hunt(roster_role_id=ROSTER):
    h = MagicMock()
    h.id = HUNT_ID
    h.name = "Big Hunt"
    h.roster_role_id = roster_role_id
    return h


class World:
    """A team server with #lobby, a hub, and the hunt's hub role."""

    def __init__(self, members=(), here=(), invited=(), lobby=True, invite_fails=False,
                 hub_roles_of=None, roster_found=True):
        self.hub = MagicMock()
        self.hub.name = "The Hub"
        self.roster = MagicMock()
        self.roster.id = ROSTER
        self.roster.name = "Team Rocket"
        self.roster.guild = self.hub
        self.roster.members = list(members)
        self.roster_found = roster_found
        self.invites = []

        async def create_invite(**kwargs):
            if invite_fails:
                raise _forbidden()
            inv = MagicMock()
            n = len(self.invites) + 1
            inv.code = f"code{n}"
            inv.url = f"https://discord.gg/code{n}"
            inv.delete = AsyncMock()
            inv.kwargs = kwargs
            self.invites.append(inv)
            return inv

        self.lobby = MagicMock()
        self.lobby.name = "lobby"
        self.lobby.create_invite = create_invite
        cmds = MagicMock()
        cmds.name = "bot-commands"
        self.guild = MagicMock()
        self.guild.text_channels = [cmds, self.lobby] if lobby else [cmds]
        here_ids = {p.id for p in here}
        self.guild.get_member.side_effect = lambda uid: MagicMock() if uid in here_ids else None
        self.invited = set(invited)
        self.hub_roles_of = hub_roles_of or {}
        self.bot = MagicMock()

    def patched(self, stack):
        idb = stack.enter_context(patch("bot.core.team_invites.HuntInviteDb"))
        idb.invited_user_ids.return_value = self.invited
        hdb = stack.enter_context(patch("bot.core.team_invites.HuntJsonDb"))
        stack.enter_context(patch("bot.core.team_invites.role_panel.find_roster_role",
                                  return_value=self.roster if self.roster_found else None))

        async def hub_member(hub, uid):
            if uid not in self.hub_roles_of:
                return None
            m = MagicMock()
            m.roles = [MagicMock(id=r) for r in self.hub_roles_of[uid]]
            return m

        self.hub_member = stack.enter_context(patch(
            "bot.core.team_invites.hunt_archive.hub_member", new=AsyncMock(side_effect=hub_member)))
        return idb, hdb

    async def send(self, hunt=None):
        with ExitStack() as stack:
            idb, hdb = self.patched(stack)
            report = await team_invites.send_invites(self.bot, self.guild, hunt or _hunt())
        return report, idb, hdb

    async def invite_one(self, user, invoker):
        with ExitStack() as stack:
            idb, _ = self.patched(stack)
            reply = await team_invites.invite_one(self.bot, self.guild, _hunt(), user, invoker)
        return reply, idb


# --- send_invites ----------------------------------------------------------------

async def test_invites_everyone_not_here_and_not_yet_invited():
    ann, bob, cat, dee = (_person(1, "Ann"), _person(2, "Bob"), _person(3, "Cat"), _person(4, "Dee"))
    w = World(members=[ann, bob, cat, dee], here=[bob], invited={3})
    report, idb, hdb = await w.send()
    assert report.sent == ["Ann", "Dee"]
    assert (report.already_here, report.already_invited, report.failed, report.error) == (1, 1, [], "")
    assert "https://discord.gg/code1" in ann.send.await_args.args[0]
    assert "https://discord.gg/code2" in dee.send.await_args.args[0]
    bob.send.assert_not_awaited()
    cat.send.assert_not_awaited()
    assert idb.record.call_args_list == [call(HUNT_ID, 1, "code1"), call(HUNT_ID, 4, "code2")]
    hdb.mark_invites_sent.assert_called_once_with(HUNT_ID)


async def test_each_invite_is_single_use_and_never_expires():
    w = World(members=[_person(1, "Ann")])
    await w.send()
    kwargs = w.invites[0].kwargs
    assert (kwargs["max_uses"], kwargs["max_age"], kwargs["unique"]) == (1, 0, True)


async def test_the_dm_names_the_team_and_the_hunt():
    ann = _person(1, "Ann")
    await World(members=[ann]).send()
    text = ann.send.await_args.args[0]
    assert "Team Rocket" in text and "Big Hunt" in text


async def test_closed_dms_delete_the_invite_and_record_nothing():
    ann = _person(1, "Ann", dms_open=False)
    w = World(members=[ann])
    report, idb, hdb = await w.send()
    assert report.failed == ["Ann"] and report.sent == []
    w.invites[0].delete.assert_awaited_once()
    idb.record.assert_not_called()
    hdb.mark_invites_sent.assert_called_once_with(HUNT_ID)


async def test_bots_with_the_hub_role_are_skipped():
    robot = _person(1, "Robot", bot=True)
    w = World(members=[robot])
    report, idb, _ = await w.send()
    robot.send.assert_not_awaited()
    assert report.sent == [] and w.invites == []


async def test_no_lobby_stops_before_anything_and_does_not_switch_on_late_dms():
    ann = _person(1, "Ann")
    report, idb, hdb = await World(members=[ann], lobby=False).send()
    assert "#lobby" in report.error
    ann.send.assert_not_awaited()
    hdb.mark_invites_sent.assert_not_called()


async def test_an_invite_that_cannot_be_created_stops_the_run():
    ann = _person(1, "Ann")
    report, idb, hdb = await World(members=[ann], invite_fails=True).send()
    assert "couldn't create" in report.error
    ann.send.assert_not_awaited()
    hdb.mark_invites_sent.assert_not_called()


async def test_an_unfindable_hub_role_stops_the_run():
    ann = _person(1, "Ann")
    report, idb, hdb = await World(members=[ann], roster_found=False).send()
    assert report.error
    ann.send.assert_not_awaited()
    hdb.mark_invites_sent.assert_not_called()


# --- report_text -------------------------------------------------------------------

def test_the_report_names_people_and_never_carries_a_link():
    text = team_invites.report_text(InviteReport(
        sent=["Ann", "Dee"], failed=["Eve"], already_here=1, already_invited=2))
    assert "Ann" in text and "Dee" in text and "Eve" in text
    assert "!invite_player" in text
    assert "1 already here" in text and "2 already invited" in text
    assert "discord.gg" not in text
    assert "automatically" in text


def test_a_stopped_run_says_so_and_promises_nothing():
    text = team_invites.report_text(InviteReport(sent=["Ann"], error="this server has no #lobby channel"))
    assert "Stopped" in text and "#lobby" in text
    assert "automatically" not in text


def test_a_run_with_nobody_to_invite_says_so():
    assert "Nobody" in team_invites.report_text(InviteReport())


# --- invite_one (!invite_player) ----------------------------------------------------

async def test_dms_a_hub_role_holder_a_fresh_invite():
    user, me = _person(5, "Fay"), _person(9, "Mod")
    w = World(hub_roles_of={5: {ROSTER}})
    reply, idb = await w.invite_one(user, me)
    assert "https://discord.gg/code1" in user.send.await_args.args[0]
    me.send.assert_not_awaited()
    idb.record.assert_called_once_with(HUNT_ID, 5, "code1")
    assert "Fay" in reply and "discord.gg" not in reply
    w.hub_member.assert_awaited_once_with(w.hub, 5)


async def test_closed_dms_send_the_link_to_the_invoker_instead():
    user, me = _person(5, "Fay", dms_open=False), _person(9, "Mod")
    w = World(hub_roles_of={5: {ROSTER}})
    reply, idb = await w.invite_one(user, me)
    assert "https://discord.gg/code1" in me.send.await_args.args[0]
    assert "Fay" in me.send.await_args.args[0]
    idb.record.assert_called_once_with(HUNT_ID, 5, "code1")
    assert "to you" in reply and "discord.gg" not in reply


async def test_when_nobody_can_be_dmd_the_invite_is_deleted():
    user, me = _person(5, "Fay", dms_open=False), _person(9, "Mod", dms_open=False)
    w = World(hub_roles_of={5: {ROSTER}})
    reply, idb = await w.invite_one(user, me)
    w.invites[0].delete.assert_awaited_once()
    idb.record.assert_not_called()
    assert reply.startswith(":x:")


async def test_refuses_someone_without_the_hub_role():
    user, me = _person(5, "Fay"), _person(9, "Mod")
    w = World(hub_roles_of={5: {OTHER_HUB_ROLE}})
    reply, idb = await w.invite_one(user, me)
    assert w.invites == []
    user.send.assert_not_awaited()
    assert "Team Rocket" in reply


async def test_refuses_someone_not_on_the_hub():
    user, me = _person(5, "Fay"), _person(9, "Mod")
    w = World()
    reply, _ = await w.invite_one(user, me)
    assert w.invites == [] and reply.startswith(":x:")


async def test_someone_already_here_is_not_invited():
    user, me = _person(5, "Fay"), _person(9, "Mod")
    w = World(here=[user], hub_roles_of={5: {ROSTER}})
    reply, _ = await w.invite_one(user, me)
    assert w.invites == []
    assert "already" in reply
