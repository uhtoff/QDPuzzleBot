import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core import voice
from bot.core.discord_channels import MAX_CATEGORY_CHANNELS


def _hunt(**kw):
    base = dict(id=1, name="Big Hunt", category_id=500, archived=False,
                voice_channel_id=0)
    base.update(kw)
    return SimpleNamespace(**base)


def _puzzle(**kw):
    base = dict(id=7, name="Cap One", solved=False, voice_channel_id=0)
    base.update(kw)
    return SimpleNamespace(**base)


def _vc(cid, name="vc", members=()):
    ch = MagicMock()
    ch.id = cid
    ch.name = name
    ch.members = list(members)
    ch.mention = f"<#{cid}>"
    ch.send = AsyncMock()
    ch.delete = AsyncMock()
    return ch


def _category(cid, name, n_channels=0):
    cat = MagicMock()
    cat.id = cid
    cat.name = name
    cat.channels = [object()] * n_channels
    cat.delete = AsyncMock()
    cat.clone = AsyncMock()
    return cat


def _guild(channels=(), categories=()):
    by_id = {c.id: c for c in channels}
    g = MagicMock()
    g.id = 99
    g.get_channel = MagicMock(side_effect=lambda i: by_id.get(i))
    g.categories = list(categories)
    g.create_voice_channel = AsyncMock()
    g.create_category = AsyncMock()
    return g


@pytest.fixture(autouse=True)
def _fresh_locks():
    voice._hunt_locks.clear()
    yield
    voice._hunt_locks.clear()


@pytest.fixture
def stores():
    with patch("bot.core.voice.HuntJsonDb") as h, patch("bot.core.voice.PuzzleJsonDb") as p:
        yield h, p


def test_category_names():
    assert voice.voice_category_name("Big Hunt", 1) == "Big Hunt Voice"
    assert voice.voice_category_name("Big Hunt", 2) == "Big Hunt Voice 2"
    assert len(voice.voice_category_name("x" * 200, 1)) == 100
    assert len(voice.voice_category_name("x" * 200, 2)) == 100


def test_voice_url():
    assert voice.voice_url(1, 2) == "https://discord.com/channels/1/2"


async def test_ensure_hunt_voice_reuses_a_live_channel(stores):
    h, _ = stores
    live = _vc(11)
    guild = _guild([live])
    hunt = _hunt(voice_channel_id=11)
    assert await voice.ensure_hunt_voice(guild, hunt) is live
    guild.create_voice_channel.assert_not_awaited()
    h.set_voice_channel.assert_not_called()


async def test_ensure_hunt_voice_recreates_when_deleted_by_hand(stores):
    h, _ = stores
    main = _category(500, "Big Hunt")
    new = _vc(12)
    guild = _guild([main])
    guild.create_voice_channel.return_value = new
    hunt = _hunt(voice_channel_id=11)
    assert await voice.ensure_hunt_voice(guild, hunt) is new
    assert guild.create_voice_channel.await_args.args[0] == "Big Hunt voice"
    assert guild.create_voice_channel.await_args.kwargs["category"] is main
    h.set_voice_channel.assert_called_once_with(1, 12)
    assert hunt.voice_channel_id == 12


async def test_ensure_hunt_voice_none_when_category_missing_or_full(stores):
    h, _ = stores
    guild = _guild([])
    assert await voice.ensure_hunt_voice(guild, _hunt()) is None
    full = _category(500, "Big Hunt", MAX_CATEGORY_CHANNELS)
    guild = _guild([full])
    assert await voice.ensure_hunt_voice(guild, _hunt()) is None
    guild.create_voice_channel.assert_not_awaited()
    h.set_voice_channel.assert_not_called()


async def test_voice_category_is_cloned_from_the_hunt_category():
    main = _category(500, "Big Hunt")
    clone = _category(600, "Big Hunt Voice")
    main.clone.return_value = clone
    guild = _guild([main], categories=[main])
    cat = await voice.ensure_voice_category(guild, _hunt())
    assert cat is clone
    assert main.clone.await_args.kwargs["name"] == "Big Hunt Voice"
    guild.create_category.assert_not_awaited()


async def test_existing_voice_category_with_room_is_reused():
    main = _category(500, "Big Hunt")
    existing = _category(600, "Big Hunt Voice", 3)
    guild = _guild([main], categories=[main, existing])
    assert await voice.ensure_voice_category(guild, _hunt()) is existing
    main.clone.assert_not_awaited()


async def test_full_voice_category_overflows_to_voice_2():
    main = _category(500, "Big Hunt")
    full = _category(600, "Big Hunt Voice", MAX_CATEGORY_CHANNELS)
    two = _category(601, "Big Hunt Voice 2")
    main.clone.return_value = two
    guild = _guild([main], categories=[main, full])
    assert await voice.ensure_voice_category(guild, _hunt()) is two
    assert main.clone.await_args.kwargs["name"] == "Big Hunt Voice 2"

    # and with Voice 2 already existing with room: reused, no clone
    main2 = _category(500, "Big Hunt")
    existing2 = _category(601, "Big Hunt Voice 2", 2)
    guild = _guild([main2], categories=[main2, full, existing2])
    assert await voice.ensure_voice_category(guild, _hunt()) is existing2
    main2.clone.assert_not_awaited()


async def test_missing_hunt_category_refuses():
    guild = _guild([])
    with pytest.raises(voice.VoiceError) as ei:
        await voice.ensure_voice_category(guild, _hunt())
    assert ei.value.code == "no_hunt_category"
    guild.create_category.assert_not_awaited()


@pytest.mark.parametrize("hunt_kw,puzzle_kw,code", [
    ({"archived": True}, {}, "archived"),
    ({}, {"solved": True}, "solved"),
])
async def test_create_puzzle_voice_refuses_solved_and_archived(stores, hunt_kw, puzzle_kw, code):
    guild = _guild([])
    with pytest.raises(voice.VoiceError) as ei:
        await voice.create_puzzle_voice(guild, _hunt(**hunt_kw), _puzzle(**puzzle_kw))
    assert ei.value.code == code
    guild.create_voice_channel.assert_not_awaited()


async def test_create_puzzle_voice_returns_existing(stores):
    _, p = stores
    live = _vc(21)
    guild = _guild([live])
    ch, created = await voice.create_puzzle_voice(guild, _hunt(), _puzzle(voice_channel_id=21))
    assert ch is live and created is False
    guild.create_voice_channel.assert_not_awaited()
    p.set_voice_channel.assert_not_called()


async def test_create_puzzle_voice_creates_hunt_voice_first_if_missing(stores):
    h, p = stores
    main = _category(500, "Big Hunt")
    vcat = _category(600, "Big Hunt Voice")
    hunt_vc, puz_vc = _vc(12), _vc(22)
    guild = _guild([main], categories=[main, vcat])
    guild.create_voice_channel.side_effect = [hunt_vc, puz_vc]
    hunt, puzzle = _hunt(), _puzzle()
    ch, created = await voice.create_puzzle_voice(guild, hunt, puzzle)
    assert ch is puz_vc and created is True
    first, second = guild.create_voice_channel.await_args_list
    assert first.args[0] == "Big Hunt voice"
    assert second.args[0] == "Cap One" and second.kwargs["category"] is vcat
    h.set_voice_channel.assert_called_once_with(1, 12)
    p.set_voice_channel.assert_called_once_with(7, 22)
    assert hunt.voice_channel_id == 12 and puzzle.voice_channel_id == 22


async def test_concurrent_ensure_hunt_voice_creates_once(stores):
    h, _ = stores
    main = _category(500, "Big Hunt")
    guild = _guild([main])

    created = _vc(12)
    by_id = {500: main}
    guild.get_channel = MagicMock(side_effect=lambda i: by_id.get(i))

    async def create_and_register(*a, **k):
        await asyncio.sleep(0)
        by_id[12] = created
        return created

    guild.create_voice_channel.side_effect = create_and_register
    hunt = _hunt()
    r = await asyncio.gather(voice.ensure_hunt_voice(guild, hunt), voice.ensure_hunt_voice(guild, hunt))
    assert guild.create_voice_channel.await_count == 1
    assert r[0] is created and r[1] is created
