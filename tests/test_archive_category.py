"""The archive_category server setting: archive channels go to the TOP of that
category (made if missing). Hub archives (one team's, and a multi-team hunt's
"all teams") are placed on the hub when made; an in-place !archive_hunt places
its renamed channel on its own server. Empty = top level, as before."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core import hunt_archive
from bot.core.discord_channels import MAX_CATEGORY_CHANNELS


def _category(cid, name, n_channels=0):
    c = MagicMock()
    c.id, c.name = cid, name
    c.channels = [MagicMock() for _ in range(n_channels)]
    return c


def _guild(categories=()):
    g = MagicMock()
    g.id = 1000
    g.categories = list(categories)

    async def create_category(name, **kw):
        c = _category(400, name)
        g.categories.append(c)
        return c
    g.create_category = AsyncMock(side_effect=create_category)
    return g


def _channel():
    ch = MagicMock()
    ch.move = AsyncMock()
    return ch


async def _place(guild, channel, setting):
    with patch("bot.core.hunt_archive.GuildSettingsDb") as gdb:
        gdb.get.return_value = SimpleNamespace(archive_category=setting)
        await hunt_archive.place_in_archive_category(guild, channel)
    gdb.get.assert_called_once_with(guild.id)


async def test_moves_to_the_top_of_the_existing_category_without_syncing():
    archive = _category(300, "Archive")
    guild, ch = _guild([_category(301, "general"), archive]), _channel()
    await _place(guild, ch, " archive ")
    ch.move.assert_awaited_once_with(beginning=True, category=archive, sync_permissions=False)
    guild.create_category.assert_not_awaited()


async def test_makes_a_missing_category_first():
    guild, ch = _guild([_category(301, "general")]), _channel()
    await _place(guild, ch, "Old hunts")
    guild.create_category.assert_awaited_once_with("Old hunts")
    assert ch.move.await_args.kwargs["category"].id == 400


async def test_empty_setting_leaves_the_channel_at_top_level():
    guild, ch = _guild(), _channel()
    await _place(guild, ch, "")
    ch.move.assert_not_awaited()
    guild.create_category.assert_not_awaited()


async def test_a_full_category_leaves_the_channel_where_it_is():
    full = _category(300, "Archive", n_channels=MAX_CATEGORY_CHANNELS)
    guild, ch = _guild([full]), _channel()
    await _place(guild, ch, "Archive")
    ch.move.assert_not_awaited()


async def test_a_discord_failure_is_swallowed():
    guild, ch = _guild([_category(300, "Archive")]), _channel()
    ch.move.side_effect = RuntimeError("missing perms")
    await _place(guild, ch, "Archive")          # no raise


async def test_a_new_tagged_archive_channel_is_placed_but_a_found_one_is_not():
    hub = MagicMock()
    found = MagicMock(); found.topic = "puzzbot-archive:old"
    hub.text_channels = [found]
    made = MagicMock()
    hub.create_text_channel = AsyncMock(return_value=made)
    with patch("bot.core.hunt_archive.place_in_archive_category", new=AsyncMock()) as place:
        assert await hunt_archive.ensure_tagged_channel(hub, "puzzbot-archive:old", "x") is found
        place.assert_not_awaited()
        assert await hunt_archive.ensure_tagged_channel(hub, "puzzbot-archive:new", "y") is made
    place.assert_awaited_once_with(hub, made)
