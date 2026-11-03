"""Hub helpers for !archive_hunt_to_hub: validating the named hub, and finding a
half-built hub archive channel again on a re-run."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core import hunt_archive
from bot.store.hunt_data import HuntData

HUB, TEAM, USER = 1000, 2000, 42



@pytest.fixture(autouse=True)
def _no_archive_category_lookup():
    """Archive-category placement reads the server's settings row; it has its
    own tests (test_archive_category.py), so keep these off the database."""
    with patch("bot.core.hunt_archive.place_in_archive_category", new=AsyncMock()):
        yield

def _team():
    g = MagicMock(); g.id = TEAM
    return g


def _invoker():
    m = MagicMock(); m.id = USER
    return m


def _hub(member):
    hub = MagicMock(); hub.id = HUB; hub.name = "The Hub"
    hub.get_member.return_value = member
    hub.chunked = True
    return hub


def _member(*role_ids):
    m = MagicMock(); m.roles = [MagicMock(id=r) for r in role_ids]
    return m


def _bot(hub):
    bot = MagicMock(); bot.get_guild.return_value = hub
    return bot


async def test_resolve_hub_returns_hub_and_its_guilds_row_for_a_hub_admin():
    member = _member(7, 8)
    hub = _hub(member)
    row = MagicMock(); row.id = 9
    with patch("bot.core.hunt_archive.role_gate") as rg, \
         patch("bot.core.hunt_archive.GuildSettingsDb") as gsdb:
        rg.member_holds_tier.return_value = True
        gsdb.get.return_value = row
        got = await hunt_archive.resolve_hub(_bot(hub), HUB, _team(), _invoker())
    assert got == (hub, row)
    hub.get_member.assert_called_once_with(USER)
    assert rg.member_holds_tier.call_args.args[:2] == ({7, 8}, hub)
    gsdb.get.assert_called_once_with(HUB)


async def test_resolve_hub_refuses_the_current_server():
    with pytest.raises(hunt_archive.HubUnavailable, match="this server"):
        await hunt_archive.resolve_hub(_bot(_hub(_member())), TEAM, _team(), _invoker())


async def test_resolve_hub_refuses_a_server_the_bot_is_not_in():
    with pytest.raises(hunt_archive.HubUnavailable, match="1000"):
        await hunt_archive.resolve_hub(_bot(None), HUB, _team(), _invoker())


async def test_resolve_hub_refuses_a_non_member():
    with pytest.raises(hunt_archive.HubUnavailable, match="aren't a member of The Hub"):
        await hunt_archive.resolve_hub(_bot(_hub(None)), HUB, _team(), _invoker())


async def test_resolve_hub_fetches_the_member_while_the_hub_is_unchunked():
    hub = _hub(None); hub.chunked = False
    hub.fetch_member = AsyncMock(return_value=_member(7))
    with patch("bot.core.hunt_archive.role_gate") as rg, \
         patch("bot.core.hunt_archive.GuildSettingsDb") as gsdb:
        rg.member_holds_tier.return_value = True
        gsdb.get.return_value = MagicMock()
        await hunt_archive.resolve_hub(_bot(hub), HUB, _team(), _invoker())
    hub.fetch_member.assert_awaited_once_with(USER)


async def test_resolve_hub_refuses_a_member_who_is_not_a_hub_admin():
    with patch("bot.core.hunt_archive.role_gate") as rg, \
         patch("bot.core.hunt_archive.GuildSettingsDb") as gsdb:
        rg.member_holds_tier.return_value = False
        with pytest.raises(hunt_archive.HubUnavailable, match="aren't an admin on The Hub"):
            await hunt_archive.resolve_hub(_bot(_hub(_member(7))), HUB, _team(), _invoker())
    gsdb.get.assert_not_called()


async def test_resolve_hub_refuses_a_hub_with_no_guilds_row():
    with patch("bot.core.hunt_archive.role_gate") as rg, \
         patch("bot.core.hunt_archive.GuildSettingsDb") as gsdb:
        rg.member_holds_tier.return_value = True
        gsdb.get.return_value = None
        with pytest.raises(hunt_archive.HubUnavailable, match="guilds row"):
            await hunt_archive.resolve_hub(_bot(_hub(_member(7))), HUB, _team(), _invoker())


def _hunt(uid):
    h = HuntData(); h.uid = uid
    return h


def _chan(topic):
    c = MagicMock(); c.topic = topic
    return c


async def test_ensure_hub_archive_channel_reuses_the_one_tagged_for_this_hunt():
    other, mine = _chan("puzzbot-archive:zzz"), _chan("puzzbot-archive:abc")
    hub = MagicMock(); hub.text_channels = [other, mine]
    hub.create_text_channel = AsyncMock()
    got = await hunt_archive.ensure_hub_archive_channel(hub, _hunt("abc"), "H - team")
    assert got is mine
    hub.create_text_channel.assert_not_awaited()


async def test_ensure_hub_archive_channel_creates_an_uncategorised_tagged_channel():
    hub = MagicMock(); hub.text_channels = [_chan("puzzbot-archive:zzz"), _chan(None)]
    created = MagicMock()
    hub.create_text_channel = AsyncMock(return_value=created)
    got = await hunt_archive.ensure_hub_archive_channel(hub, _hunt("abc"), "H - team")
    assert got is created
    hub.create_text_channel.assert_awaited_once_with(
        "H - team", topic="puzzbot-archive:abc")


async def test_resolve_hub_refuses_when_the_bot_lacks_hub_permissions():
    hub = _hub(_member(7))
    hub.me.guild_permissions.manage_webhooks = False
    with patch("bot.core.hunt_archive.role_gate") as rg, \
         patch("bot.core.hunt_archive.GuildSettingsDb") as gsdb:
        rg.member_holds_tier.return_value = True
        gsdb.get.return_value = MagicMock()
        with pytest.raises(hunt_archive.HubUnavailable, match="Manage Webhooks"):
            await hunt_archive.resolve_hub(_bot(hub), HUB, _team(), _invoker())
    hub.get_member.assert_not_called()


async def test_ensure_hub_archive_channel_truncates_the_name_to_100_chars():
    hub = MagicMock(); hub.text_channels = []
    hub.create_text_channel = AsyncMock(return_value=MagicMock())
    await hunt_archive.ensure_hub_archive_channel(hub, _hunt("abc"), "x" * 150)
    hub.create_text_channel.assert_awaited_once_with(
        "x" * 100, topic="puzzbot-archive:abc")
