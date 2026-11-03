"""Multi-team hunts: hub channels with explicit overwrites, and find_or_create."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core import multi_team

HUB_ID = 1000
MT_ID = 7


def _role(rid, name="r"):
    r = MagicMock()
    r.id = rid
    r.name = name
    return r


class FakeMtDb:
    """In-memory stand-in for MultiTeamDb."""

    def __init__(self, members=(), name="Big Hunt"):
        self.rows = {}
        self.members_list = list(members)
        self.name = name
        self.create_calls = 0
        self.channels = None
        self.next_id = 1

    def get(self, mt_id):
        return {"id": mt_id, "name": self.name, "hub_guild_id": HUB_ID,
                "no_spoiler_channel_id": None, "finishers_channel_id": None}

    def members(self, mt_id):
        return self.members_list

    def set_channels(self, mt_id, ns, fin):
        self.channels = (ns, fin)

    def running_by_name(self, hub_guild_id, name):
        for row in self.rows.values():
            if row["name"].lower() == name.lower():
                return row
        return None

    def create(self, hub_guild_id, name):
        self.create_calls += 1
        mt_id = self.next_id
        self.next_id += 1
        self.rows[mt_id] = {"id": mt_id, "name": name}
        return mt_id


def _member(hunt_id, role_id, completed=None):
    return {"hunt_id": hunt_id, "completed_time": completed,
            "roster_role_id": role_id, "archived": 0}


def _hub(roles, existing=()):
    hub = MagicMock()
    hub.id = HUB_ID
    hub.default_role = _role(1, "@everyone")
    hub.get_role.side_effect = lambda rid: roles.get(rid)
    hub.text_channels = list(existing)
    hub.get_channel.side_effect = lambda cid: None
    created = []

    async def create(name, **kw):
        ch = MagicMock()
        ch.id = 500 + len(created)
        ch.name = name
        ch.topic = kw.get("topic")
        ch.edit = AsyncMock()
        created.append((ch, kw))
        hub.text_channels.append(ch)
        return ch

    hub.create_text_channel = AsyncMock(side_effect=create)
    hub.created = created
    return hub


ALLOW = dict(read_messages=True, send_messages=True, read_message_history=True)


# PermissionOverwrite stores view_channel under its legacy key read_messages.
def _perm_values(ow):
    return {k: v for k, v in ow._values.items()}


def _expect(hub, role_ids):
    out = {hub.default_role: {"read_messages": False}}
    for rid in role_ids:
        out[hub.get_role(rid)] = ALLOW
    return out


def _actual(overwrites):
    return {role: _perm_values(ow) for role, ow in overwrites.items()}


@pytest.fixture
def roles():
    return {10: _role(10, "Team A"), 11: _role(11, "Team B"), 90: _role(90, "Admin")}


@pytest.fixture
def db():
    d = FakeMtDb(members=[_member(1, 10), _member(2, 11)])
    with patch("bot.core.multi_team.MultiTeamDb", d), \
         patch("bot.core.multi_team.role_gate.tier_role_ids", return_value=frozenset({90})):
        yield d


def test_markers():
    assert multi_team.no_spoiler_marker(7) == "puzzbot-multi-team:7:no-spoilers"
    assert multi_team.finishers_marker(7) == "puzzbot-multi-team:7:finishers"
    assert multi_team.archive_marker(7) == "puzzbot-multi-team-archive:7"


async def test_first_call_creates_both_with_exact_overwrites(db, roles):
    hub = _hub(roles)
    ns, fin = await multi_team.ensure_channels(hub, MT_ID)
    (c1, kw1), (c2, kw2) = hub.created
    assert c1.name == "big-hunt-no-spoilers" and c2.name == "big-hunt-finishers"
    assert kw1["topic"] == "puzzbot-multi-team:7:no-spoilers"
    assert kw2["topic"] == "puzzbot-multi-team:7:finishers"
    assert "category" not in kw1 and "category" not in kw2
    assert _actual(kw1["overwrites"]) == _expect(hub, [10, 11, 90])
    assert _actual(kw2["overwrites"]) == _expect(hub, [])
    assert db.channels == (c1.id, c2.id)
    assert (ns, fin) == (c1, c2)


async def test_second_call_finds_by_marker_and_creates_nothing(db, roles):
    hub = _hub(roles)
    await multi_team.ensure_channels(hub, MT_ID)
    hub.create_text_channel.reset_mock()
    ns, fin = await multi_team.ensure_channels(hub, MT_ID)
    hub.create_text_channel.assert_not_awaited()
    assert ns.topic.endswith(":no-spoilers") and fin.topic.endswith(":finishers")
    for ch in (ns, fin):
        ch.edit.assert_awaited_once()


async def test_completed_member_gains_exactly_that_role_on_finishers(db, roles):
    hub = _hub(roles)
    await multi_team.ensure_channels(hub, MT_ID)
    db.members_list = [_member(1, 10), _member(2, 11, completed="2026-01-01")]
    ns, fin = await multi_team.ensure_channels(hub, MT_ID)
    assert _actual(fin.edit.await_args.kwargs["overwrites"]) == _expect(hub, [11])
    assert _actual(ns.edit.await_args.kwargs["overwrites"]) == _expect(hub, [10, 11, 90])


async def test_removed_role_is_dropped_from_existing_channel(db, roles):
    hub = _hub(roles)
    await multi_team.ensure_channels(hub, MT_ID)
    db.members_list = [_member(1, 10)]
    ns, _ = await multi_team.ensure_channels(hub, MT_ID)
    assert _actual(ns.edit.await_args.kwargs["overwrites"]) == _expect(hub, [10, 90])


async def test_deleted_finishers_channel_is_recreated(db, roles):
    hub = _hub(roles)
    ns, fin = await multi_team.ensure_channels(hub, MT_ID)
    hub.text_channels.remove(fin)
    hub.create_text_channel.reset_mock()
    ns2, fin2 = await multi_team.ensure_channels(hub, MT_ID)
    assert ns2 is ns and fin2 is not fin
    hub.create_text_channel.assert_awaited_once()
    kw = hub.create_text_channel.await_args.kwargs
    assert kw["topic"] == "puzzbot-multi-team:7:finishers"
    assert _actual(kw["overwrites"]) == _expect(hub, [])
    assert db.channels == (ns.id, fin2.id)


async def test_missing_hub_role_is_skipped(db, roles):
    del roles[11]
    hub = _hub(roles)
    await multi_team.ensure_channels(hub, MT_ID)
    assert _actual(hub.created[0][1]["overwrites"]) == _expect(hub, [10, 90])


async def test_stored_id_is_used_when_topic_matches(db, roles):
    hub = _hub(roles)
    ns, fin = await multi_team.ensure_channels(hub, MT_ID)
    hub.text_channels.clear()  # scan would find nothing; stored id must
    hub.get_channel.side_effect = lambda cid: {ns.id: ns, fin.id: fin}.get(cid)
    db.get = lambda mt_id: {"id": mt_id, "name": "Big Hunt", "hub_guild_id": HUB_ID,
                            "no_spoiler_channel_id": ns.id, "finishers_channel_id": fin.id}
    hub.create_text_channel.reset_mock()
    ns2, fin2 = await multi_team.ensure_channels(hub, MT_ID)
    assert (ns2, fin2) == (ns, fin)
    hub.create_text_channel.assert_not_awaited()


async def test_concurrent_find_or_create_creates_once():
    d = FakeMtDb()
    real_create = d.create

    def slow_create(*a):
        return real_create(*a)

    d.create = slow_create
    hub = MagicMock()
    hub.id = HUB_ID
    with patch("bot.core.multi_team.MultiTeamDb", d):
        a, b = await asyncio.gather(multi_team.find_or_create(hub, "Big Hunt"),
                                    multi_team.find_or_create(hub, "Big Hunt"))
    assert d.create_calls == 1
    assert a[0] == b[0]
    assert sorted([a[1], b[1]]) == [False, True]


async def test_find_or_create_is_case_insensitive():
    d = FakeMtDb()
    hub = MagicMock()
    hub.id = HUB_ID
    with patch("bot.core.multi_team.MultiTeamDb", d):
        first, created1 = await multi_team.find_or_create(hub, "Big Hunt")
        again, created2 = await multi_team.find_or_create(hub, "big hunt")
    assert created1 is True and created2 is False
    assert again == first
    assert d.create_calls == 1
