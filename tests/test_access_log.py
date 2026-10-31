"""The access log: team-server member events become hunt_access_log rows; only the
quiet list (hand changes, hub-driven removals, non-button elevate/demote) is posted."""
import datetime
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.cogs.team_servers import TeamServers
from bot.core import access_log, access_notes

HUNT_ID, TEAM_ROLE, ROSTER, MOD, OTHER = 42, 71, 70, 81, 99
TEAM_GUILD, PERSON, ACTOR = 2000, 5, 777


@pytest.fixture(autouse=True)
def _clear_notes():
    access_notes._notes.clear()
    yield
    access_notes._notes.clear()


def _role(role_id, name):
    r = MagicMock()
    r.id = role_id
    r.name = name
    return r


def _entry(target_id=PERSON, role_ids=(), actor=ACTOR, added=True, age=1):
    e = MagicMock()
    e.target.id = target_id
    e.user.id = actor
    e.created_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age)
    side = MagicMock()
    side.roles = [_role(i, "r") for i in role_ids]
    empty = MagicMock()
    empty.roles = []
    e.after, e.before = (side, empty) if added else (empty, side)
    return e


def _gen(entries):
    async def g(*a, **k):
        for e in entries:
            yield e
    return g


class World:
    def __init__(self, live=True, is_bot=False):
        self.hub = MagicMock()
        self.hub.name = "The Hub"
        self.team_role = _role(TEAM_ROLE, "Team Rocket")
        self.mod = _role(MOD, "mod")
        self.other = _role(OTHER, "snacks")
        self.guild = MagicMock()
        self.guild.id = TEAM_GUILD
        self.guild.name = "Rocket HQ"
        self.guild.get_role.side_effect = lambda i: {TEAM_ROLE: self.team_role, MOD: self.mod}.get(i)
        self.guild.audit_logs = MagicMock(side_effect=lambda **k: _gen([])())
        self.live = live
        self.member = MagicMock()
        self.member.id = PERSON
        self.member.bot = is_bot
        self.member.display_name = "Fay"
        self.member.guild = self.guild
        self.member.roles = []
        self.bot = MagicMock()
        roster = MagicMock()
        roster.guild = self.hub
        self.bot.guilds = [MagicMock(get_role=lambda i: roster if i == ROSTER else None)]
        self.sleep = AsyncMock()

    def audit(self, *per_call):
        """Each audit_logs() call yields the next list of entries."""
        calls = iter(per_call)
        self.guild.audit_logs = MagicMock(side_effect=lambda **k: _gen(next(calls, []))())

    async def run(self, fn, *args):
        hunt = MagicMock()
        hunt.id, hunt.role_id, hunt.roster_role_id = HUNT_ID, TEAM_ROLE, ROSTER
        with ExitStack() as stack:
            self.db = stack.enter_context(patch("bot.core.access_log.HuntAccessLogDb"))
            hdb = stack.enter_context(patch("bot.core.access_log.HuntJsonDb"))
            hdb.live_team_hunt_for_guild.return_value = hunt if self.live else None
            self.post = stack.enter_context(
                patch("bot.core.access_log.hub_channel.post", new=AsyncMock(return_value=True)))
            stack.enter_context(patch("bot.core.role_gate.tier_role_ids", return_value={MOD}))
            stack.enter_context(patch("bot.core.access_log.asyncio.sleep", new=self.sleep))
            await fn(self.bot, *args)

    async def change(self, gained=(), lost=()):
        before = MagicMock()
        before.roles = list(lost)
        after = MagicMock()
        after.roles = list(gained)
        after.id, after.bot, after.display_name, after.guild = (
            PERSON, self.member.bot, "Fay", self.guild)
        await self.run(access_log.on_roles_changed, before, after)

    def rows(self):
        return [c.args for c in self.db.record.call_args_list]


def test_exact_value_sets():
    assert access_log.ACTIONS == ("grant", "revoke", "elevate", "demote", "arrive", "depart")
    assert access_log.VIAS == ("button", "arrival", "hub_role_lost", "hub_admin_lost", "hand", "self")


async def test_bot_noted_grant_is_logged_quietly():
    w = World()
    access_notes.expect(TEAM_GUILD, PERSON, TEAM_ROLE, "arrival")
    await w.change(gained=[w.team_role])
    assert w.rows() == [(HUNT_ID, PERSON, "Fay", "grant", "arrival", None, "Team Rocket")]
    w.post.assert_not_awaited()
    w.guild.audit_logs.assert_not_called()


async def test_hand_grant_of_mod_is_logged_with_actor_and_posted():
    w = World()
    w.audit([_entry(role_ids=[MOD])])
    await w.change(gained=[w.mod])
    assert w.rows() == [(HUNT_ID, PERSON, "Fay", "elevate", "hand", ACTOR, "mod")]
    w.post.assert_awaited_once()
    text = w.post.await_args.args[1]
    assert "Fay" in text and "mod" in text and f"<@{ACTOR}>" in text


async def test_audit_entry_for_another_role_or_person_or_stale_is_not_the_actor():
    w = World()
    w.audit([_entry(role_ids=[OTHER]), _entry(target_id=6, role_ids=[MOD]),
             _entry(role_ids=[MOD], age=120)], [])
    await w.change(gained=[w.mod])
    assert w.rows()[0][5] is None


async def test_hand_removal_matches_the_removed_side_of_the_entry():
    w = World()
    w.audit([_entry(role_ids=[TEAM_ROLE], added=True), _entry(role_ids=[TEAM_ROLE], added=False, actor=888)])
    await w.change(lost=[w.team_role])
    assert w.rows() == [(HUNT_ID, PERSON, "Fay", "revoke", "hand", 888, "Team Rocket")]


async def test_hand_change_with_unreadable_audit_log_still_writes_row():
    w = World()
    forbidden = discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "no")
    w.guild.audit_logs = MagicMock(side_effect=forbidden)
    await w.change(gained=[w.mod])
    assert w.rows() == [(HUNT_ID, PERSON, "Fay", "elevate", "hand", None, "mod")]
    w.post.assert_awaited_once()
    assert "someone" in w.post.await_args.args[1]


async def test_missing_audit_entry_is_retried_once():
    w = World()
    w.audit([], [_entry(role_ids=[MOD])])
    await w.change(gained=[w.mod])
    assert w.rows()[0][5] == ACTOR
    w.sleep.assert_awaited_once()
    assert w.guild.audit_logs.call_count == 2


async def test_update_on_a_non_team_guild_is_ignored():
    w = World(live=False)
    await w.change(gained=[w.mod])
    w.db.record.assert_not_called()
    w.post.assert_not_awaited()


async def test_unrelated_role_changes_are_ignored():
    w = World()
    await w.change(gained=[w.other])
    w.db.record.assert_not_called()
    w.post.assert_not_awaited()
    w.guild.audit_logs.assert_not_called()


async def test_bots_are_ignored():
    w = World(is_bot=True)
    await w.change(gained=[w.mod])
    w.db.record.assert_not_called()
    w.post.assert_not_awaited()


async def test_two_watched_roles_in_one_update_write_one_row_each():
    w = World()
    access_notes.expect(TEAM_GUILD, PERSON, TEAM_ROLE, "button")
    access_notes.expect(TEAM_GUILD, PERSON, MOD, "button")
    await w.change(gained=[w.team_role, w.mod])
    assert sorted((r[3], r[4]) for r in w.rows()) == [("elevate", "button"), ("grant", "button")]
    w.post.assert_not_awaited()


async def test_bot_noted_elevate_not_by_button_is_posted():
    w = World()
    access_notes.expect(TEAM_GUILD, PERSON, MOD, "hub_admin_lost")
    await w.change(lost=[w.mod])
    assert w.rows() == [(HUNT_ID, PERSON, "Fay", "demote", "hub_admin_lost", None, "mod")]
    w.post.assert_awaited_once()


async def test_hub_role_lost_removal_is_posted():
    w = World()
    w.member.roles = [w.team_role, w.mod]
    access_notes.expect(TEAM_GUILD, PERSON, "kick", "hub_role_lost")
    await w.run(access_log.on_departed, w.member)
    assert [(r[3], r[4]) for r in w.rows()] == [
        ("depart", "hub_role_lost"), ("revoke", "hub_role_lost"), ("demote", "hub_role_lost")]
    w.post.assert_awaited_once()
    assert "Fay" in w.post.await_args.args[1]


async def test_leaving_by_themselves_is_quiet():
    w = World()
    w.member.roles = [w.team_role]
    await w.run(access_log.on_departed, w.member)
    assert [(r[3], r[4]) for r in w.rows()] == [("depart", "self"), ("revoke", "self")]
    w.post.assert_not_awaited()


async def test_kick_by_hand_is_posted_with_actor():
    w = World()
    w.audit([_entry(target_id=6), _entry()])
    await w.run(access_log.on_departed, w.member)
    assert w.rows() == [(HUNT_ID, PERSON, "Fay", "depart", "hand", ACTOR, None)]
    w.post.assert_awaited_once()
    assert f"<@{ACTOR}>" in w.post.await_args.args[1]


async def test_arrival_is_logged_quietly():
    w = World()
    await w.run(access_log.on_arrived, w.member)
    assert w.rows() == [(HUNT_ID, PERSON, "Fay", "arrive", "arrival")]
    w.post.assert_not_awaited()


async def test_the_cog_routes_member_events_and_swallows_errors():
    cog = TeamServers(MagicMock())
    member = MagicMock()
    member.roles = []
    gained = MagicMock()
    gained.roles = [_role(1, "x")]
    with patch("bot.cogs.team_servers.access_log.on_roles_changed",
               new=AsyncMock(side_effect=RuntimeError("boom"))) as roles, \
            patch("bot.cogs.team_servers.access_log.on_arrived",
                  new=AsyncMock(side_effect=RuntimeError("boom"))) as arr, \
            patch("bot.cogs.team_servers.access_log.on_departed",
                  new=AsyncMock(side_effect=RuntimeError("boom"))) as dep, \
            patch("bot.cogs.team_servers.team_invites.on_join", new=AsyncMock()) as join, \
            patch("bot.cogs.team_servers.team_invites.on_hub_roles_added", new=AsyncMock()) as added:
        await cog.on_member_update(member, gained)
        await cog.on_member_join(member)
        await cog.on_member_remove(member)
    roles.assert_awaited_once()
    arr.assert_awaited_once()
    dep.assert_awaited_once()
    join.assert_awaited_once()
    added.assert_awaited_once()


async def test_elevate_is_posted_whatever_the_via_except_button():
    w = World()
    access_notes.expect(TEAM_GUILD, PERSON, MOD, "arrival")
    await w.change(gained=[w.mod])
    w.post.assert_awaited_once()
