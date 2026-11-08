"""Multi-team hunts: hub channels with explicit overwrites, and find_or_create."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core import multi_team
from bot.core.discord_channels import MAX_CATEGORY_CHANNELS

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
        self.category_id = None

    def get(self, mt_id):
        ns, fin = self.channels or (None, None)
        return {"id": mt_id, "name": self.name, "hub_guild_id": HUB_ID,
                "no_spoiler_channel_id": ns, "finishers_channel_id": fin,
                "hub_category_id": self.category_id}

    def members(self, mt_id):
        return self.members_list

    def set_channels(self, mt_id, ns, fin):
        self.channels = (ns, fin)

    def running_by_name(self, hub_guild_id, name):
        for row in self.rows.values():
            if row["name"].lower() == name.lower():
                return row
        return None

    def create(self, hub_guild_id, name, hub_category_id=None):
        self.create_calls += 1
        mt_id = self.next_id
        self.next_id += 1
        self.rows[mt_id] = {"id": mt_id, "name": name, "hub_category_id": hub_category_id}
        return mt_id


def _member(hunt_id, role_id, completed=None):
    return {"hunt_id": hunt_id, "completed_time": completed,
            "roster_role_id": role_id, "archived": 0}


def _raw_http(ch, flags=0):
    """The raw HTTP calls mark_spoiler_channel makes, on the channel's state."""
    ch._state.http.get_channel = AsyncMock(return_value={"id": ch.id, "flags": flags})
    ch._state.http.edit_channel = AsyncMock()
    return ch._state.http


SPOILER = 1 << 21


def _hub(roles, existing=()):
    hub = MagicMock()
    hub.id = HUB_ID
    hub.default_role = _role(1, "@everyone")
    hub.me = _role(2, "bot")
    hub.get_role.side_effect = lambda rid: roles.get(rid)
    hub.text_channels = list(existing)
    # Like the real cache: a channel is found by id until it is deleted.
    hub.get_channel.side_effect = lambda cid: next(
        (c for c in hub.text_channels if c.id == cid), None)
    created = []

    async def create(name, **kw):
        await asyncio.sleep(0)  # a real Discord call yields here
        ch = MagicMock()
        ch.id = 500 + len(created)
        ch.name = name
        ch.topic = kw.get("topic")
        ch.edit = AsyncMock()
        _raw_http(ch)
        created.append((ch, kw))
        hub.text_channels.append(ch)
        return ch

    hub.create_text_channel = AsyncMock(side_effect=create)
    hub.created = created
    return hub


ALLOW = dict(read_messages=True, send_messages=True, read_message_history=True)


# PermissionOverwrite stores view_channel under its legacy key read_messages.
BOT = dict(read_messages=True, send_messages=True, read_message_history=True,
           manage_channels=True, manage_roles=True)


def _perm_values(ow):
    return {k: v for k, v in ow._values.items()}


def _expect(hub, role_ids):
    out = {hub.default_role: {"read_messages": False}, hub.me: BOT}
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
    assert kw1["topic"] == "Chat for every team playing Big Hunt. No spoilers, please!"
    assert kw2["topic"] == ("Spoilers for Big Hunt. Only teams that have finished "
                            "can see this channel.")
    assert "category" not in kw1 and "category" not in kw2
    assert _actual(kw1["overwrites"]) == _expect(hub, [10, 11, 90])
    assert _actual(kw2["overwrites"]) == _expect(hub, [])
    assert db.channels == (c1.id, c2.id)
    assert (ns, fin) == (c1, c2)


async def test_second_call_finds_by_stored_id_and_creates_nothing(db, roles):
    hub = _hub(roles)
    first = await multi_team.ensure_channels(hub, MT_ID)
    hub.create_text_channel.reset_mock()
    ns, fin = await multi_team.ensure_channels(hub, MT_ID)
    hub.create_text_channel.assert_not_awaited()
    assert (ns, fin) == first
    for ch in (ns, fin):
        ch.edit.assert_awaited_once()
        assert "topic" not in ch.edit.await_args.kwargs  # readable already: left alone


def _legacy(cid, topic):
    ch = MagicMock()
    ch.id = cid
    ch.name = f"old-{cid}"
    ch.topic = topic
    ch.edit = AsyncMock()
    _raw_http(ch)
    return ch


async def test_legacy_marker_channels_are_found_and_get_readable_topics(db, roles):
    """Channels made before readable topics, never stored: found by the marker
    and their topics replaced, nothing created."""
    ns = _legacy(301, "puzzbot-multi-team:7:no-spoilers")
    fin = _legacy(302, "puzzbot-multi-team:7:finishers")
    hub = _hub(roles, existing=[fin, ns])
    got = await multi_team.ensure_channels(hub, MT_ID)
    assert got == (ns, fin)
    hub.create_text_channel.assert_not_awaited()
    assert ns.edit.await_args.kwargs["topic"] == (
        "Chat for every team playing Big Hunt. No spoilers, please!")
    assert fin.edit.await_args.kwargs["topic"] == (
        "Spoilers for Big Hunt. Only teams that have finished can see this channel.")
    assert db.channels == (301, 302)


async def test_an_admins_own_topic_is_kept(db, roles):
    hub = _hub(roles)
    ns, _ = await multi_team.ensure_channels(hub, MT_ID)
    ns.topic = "Our own words"
    await multi_team.ensure_channels(hub, MT_ID)
    assert "topic" not in ns.edit.await_args.kwargs


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
    assert kw["topic"] == multi_team.finishers_topic("Big Hunt")
    assert _actual(kw["overwrites"]) == _expect(hub, [])
    assert db.channels == (ns.id, fin2.id)


async def test_missing_hub_role_is_skipped(db, roles):
    del roles[11]
    hub = _hub(roles)
    await multi_team.ensure_channels(hub, MT_ID)
    assert _actual(hub.created[0][1]["overwrites"]) == _expect(hub, [10, 90])


async def test_stored_id_is_used_without_a_topic_scan(db, roles):
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


async def test_concurrent_ensure_channels_creates_one_pair(db, roles):
    hub = _hub(roles)
    (a_ns, a_fin), (b_ns, b_fin) = await asyncio.gather(
        multi_team.ensure_channels(hub, MT_ID), multi_team.ensure_channels(hub, MT_ID))
    assert hub.create_text_channel.await_count == 2
    assert [kw["topic"] for _, kw in hub.created] == [
        multi_team.no_spoiler_topic("Big Hunt"), multi_team.finishers_topic("Big Hunt")]
    assert (a_ns, a_fin) == (b_ns, b_fin)


async def test_role_without_id_is_skipped_silently(db, roles, caplog):
    db.members_list = [_member(1, 10), _member(2, None), _member(3, 0)]
    hub = _hub(roles)
    await multi_team.ensure_channels(hub, MT_ID)
    assert _actual(hub.created[0][1]["overwrites"]) == _expect(hub, [10, 90])
    assert "not found" not in caplog.text


async def test_find_or_create_twice_concurrently_is_idempotent():
    d = FakeMtDb()
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


# ------------------------------------------------------------ hub category

def _category(cid, name, n_channels=0):
    c = MagicMock()
    c.id = cid
    c.name = name
    c.channels = [MagicMock() for _ in range(n_channels)]
    return c


async def test_channels_are_created_in_the_stored_hub_category(db, roles):
    general = _category(300, "general", n_channels=3)
    db.category_id = 300
    hub = _hub(roles)
    hub.get_channel.side_effect = lambda cid: general if cid == 300 else None
    await multi_team.ensure_channels(hub, MT_ID)
    (_, kw1), (_, kw2) = hub.created
    assert kw1["category"] is general and kw2["category"] is general
    # explicit overwrites still set on the channel itself (no category sync)
    assert _actual(kw2["overwrites"]) == _expect(hub, [])


@pytest.mark.parametrize("stored,present", [(300, False), (300, "full")])
async def test_a_gone_or_full_category_falls_back_to_uncategorised(db, roles, stored, present):
    full = _category(300, "general", n_channels=MAX_CATEGORY_CHANNELS)
    db.category_id = stored
    hub = _hub(roles)
    hub.get_channel.side_effect = lambda cid: full if (present and cid == 300) else None
    await multi_team.ensure_channels(hub, MT_ID)
    assert all("category" not in kw for _, kw in hub.created)


def _cat_hub(categories):
    hub = MagicMock()
    hub.id = HUB_ID
    hub.categories = list(categories)

    async def create_category(name, **kw):
        c = _category(400, name)
        hub.categories.append(c)
        return c
    hub.create_category = AsyncMock(side_effect=create_category)
    return hub


async def test_find_or_create_uses_an_existing_category_case_insensitively():
    d = FakeMtDb()
    hub = _cat_hub([_category(300, "General"), _category(301, "Voice")])
    with patch("bot.core.multi_team.MultiTeamDb", d):
        mt_id, created = await multi_team.find_or_create(hub, "Big Hunt", "general")
    assert created is True
    assert d.rows[mt_id]["hub_category_id"] == 300
    hub.create_category.assert_not_awaited()


async def test_find_or_create_makes_a_missing_category():
    d = FakeMtDb()
    hub = _cat_hub([_category(300, "general")])
    with patch("bot.core.multi_team.MultiTeamDb", d):
        mt_id, _ = await multi_team.find_or_create(hub, "Big Hunt", "  Hunts  ")
    hub.create_category.assert_awaited_once_with("Hunts")
    assert d.rows[mt_id]["hub_category_id"] == 400


async def test_find_or_create_blank_category_is_uncategorised_and_joining_ignores_it():
    d = FakeMtDb()
    hub = _cat_hub([])
    with patch("bot.core.multi_team.MultiTeamDb", d):
        mt_id, _ = await multi_team.find_or_create(hub, "Big Hunt", " ")
        again, created = await multi_team.find_or_create(hub, "big hunt", "Elsewhere")
    assert d.rows[mt_id]["hub_category_id"] is None
    assert (again, created) == (mt_id, False)
    hub.create_category.assert_not_awaited()


# ------------------------------------------------------------ spoiler flag
async def test_new_finishers_channel_is_marked_spoiler_and_no_spoilers_is_not(db, roles):
    hub = _hub(roles)
    ns, fin = await multi_team.ensure_channels(hub, MT_ID)
    fin._state.http.edit_channel.assert_awaited_once()
    args, kw = fin._state.http.edit_channel.await_args
    assert args == (fin.id,) and kw["flags"] == SPOILER
    ns._state.http.edit_channel.assert_not_awaited()


async def test_spoiler_mark_keeps_the_channels_other_flags():
    ch = MagicMock()
    ch.id = 42
    http = _raw_http(ch, flags=0b1010)
    await multi_team.mark_spoiler_channel(ch)
    assert http.edit_channel.await_args.kwargs["flags"] == 0b1010 | SPOILER


async def test_spoiler_mark_skips_a_channel_already_marked():
    ch = MagicMock()
    ch.id = 42
    http = _raw_http(ch, flags=SPOILER | 1)
    await multi_team.mark_spoiler_channel(ch)
    http.edit_channel.assert_not_awaited()


async def test_spoiler_mark_failure_is_logged_not_raised(caplog):
    ch = MagicMock()
    ch.id = 42
    http = _raw_http(ch)
    http.edit_channel.side_effect = RuntimeError("Discord said no")
    await multi_team.mark_spoiler_channel(ch)
    assert "could not mark channel 42 as a spoiler channel" in caplog.text


async def test_later_updates_leave_the_spoiler_setting_alone(db, roles):
    """Marked once at creation; an admin who unticks it is not overridden."""
    hub = _hub(roles)
    _, fin = await multi_team.ensure_channels(hub, MT_ID)
    fin._state.http.edit_channel.reset_mock()
    db.members_list = [_member(1, 10, completed="2026-01-01")]
    await multi_team.ensure_channels(hub, MT_ID)
    fin._state.http.edit_channel.assert_not_awaited()


async def test_legacy_finishers_channel_is_marked_when_its_topic_is_upgraded(db, roles):
    ns = _legacy(301, "puzzbot-multi-team:7:no-spoilers")
    fin = _legacy(302, "puzzbot-multi-team:7:finishers")
    hub = _hub(roles, existing=[ns, fin])
    await multi_team.ensure_channels(hub, MT_ID)
    assert fin._state.http.edit_channel.await_args.kwargs["flags"] == SPOILER
    ns._state.http.edit_channel.assert_not_awaited()
