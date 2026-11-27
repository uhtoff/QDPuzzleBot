"""!tidy_category end to end through the cog: which channels count as puzzles
and which are solved must come from the category's own channel ids.

Regression (2026-11-24, test server): the command used
PuzzleJsonDb.get_all(guild.id), but hunts.guild_id is the guilds-table row id,
not the Discord id, so no puzzle matched — every channel was treated as
non-puzzle and only the divider moved (to the bottom)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.discord_channels import SOLVE_DIVIDER


def _channel(name, cid, pos):
    ch = MagicMock(); ch.name = name; ch.id = cid; ch.position = pos
    return ch


@pytest.mark.asyncio
async def test_tidy_category_sorts_by_db_solved_state_for_this_categorys_channels():
    from bot.cogs.puzzles import Puzzles
    bot = MagicMock(); bot.get_cog.return_value = None
    cog = Puzzles(bot)

    # The reported Singletons layout: solved a and c above the divider,
    # general below the puzzles.
    chans = [_channel("unassigned", 1, 0), _channel("c", 13, 1), _channel("b", 22, 2),
             _channel("a", 11, 3), _channel("general", 2, 4),
             _channel(SOLVE_DIVIDER, 99, 5)]
    category = MagicMock(id=500); category.text_channels = chans; category.channels = chans
    guild = MagicMock(id=777)
    guild._state.http.bulk_channel_update = AsyncMock()
    ctx = MagicMock(); ctx.guild = guild; ctx.send = AsyncMock()
    ctx.channel = chans[1]; ctx.channel.category = category
    cog._tag_group_for_channel = MagicMock(return_value=None)   # hunt-owned category

    with patch("bot.cogs.puzzles.PuzzleJsonDb") as db, \
            patch("bot.cogs.puzzles.HuntJsonDb") as hunts:
        db.get_solved_by_channel_ids.return_value = {13: True, 22: False, 11: True}
        hunts.get_by_attr.return_value = MagicMock(channel_id=2)   # hunt general
        await Puzzles.tidy_category.callback(cog, ctx)

    hunts.get_by_attr.assert_called_once_with(category_id=500)

    asked = set(db.get_solved_by_channel_ids.call_args.args[0])
    assert asked == {1, 13, 22, 11, 2, 99}
    args, _ = guild._state.http.bulk_channel_update.await_args
    # general pinned above unassigned; solved a/c below the divider.
    assert [e["id"] for e in args[1]] == [2, 1, 22, 99, 13, 11]
    ctx.send.assert_awaited_once()
    assert "Tidied" in ctx.send.await_args.args[0]


def test_get_solved_by_channel_ids_queries_channel_ids_and_maps_solved():
    from bot.store.mysqldb import MySQLPuzzleJsonDb
    db = MySQLPuzzleJsonDb.__new__(MySQLPuzzleJsonDb)
    db._fetch_all = MagicMock(return_value=[
        {"channel_id": 13, "solved": 1}, {"channel_id": 22, "solved": 0}])

    assert db.get_solved_by_channel_ids(iter([13, 22, 2])) == {13: True, 22: False}
    sql, params = db._fetch_all.call_args.args
    assert "channel_id IN (%s,%s,%s)" in sql
    assert params == (13, 22, 2)
    assert db.get_solved_by_channel_ids([]) == {}


@pytest.mark.asyncio
async def test_tidy_category_pins_metaless_groups_chat_channel():
    from bot.cogs.puzzles import Puzzles
    bot = MagicMock(); bot.get_cog.return_value = None
    cog = Puzzles(bot)
    chans = [_channel("p", 21, 0), _channel("my-group", 3, 1),
             _channel(SOLVE_DIVIDER, 99, 2)]
    category = MagicMock(id=600); category.text_channels = chans; category.channels = chans
    guild = MagicMock(id=777)
    guild._state.http.bulk_channel_update = AsyncMock()
    ctx = MagicMock(); ctx.guild = guild; ctx.send = AsyncMock()
    ctx.channel = chans[0]; ctx.channel.category = category
    cog._tag_group_for_channel = MagicMock(return_value=MagicMock(has_target=False))

    with patch("bot.cogs.puzzles.PuzzleJsonDb") as db, \
            patch("bot.cogs.puzzles.category_layout.group_chat_channel",
                  return_value=chans[1]):
        db.get_solved_by_channel_ids.return_value = {21: False}
        await Puzzles.tidy_category.callback(cog, ctx)

    args, _ = guild._state.http.bulk_channel_update.await_args
    assert [e["id"] for e in args[1]] == [3, 21, 99]


@pytest.mark.asyncio
async def test_tidy_category_adds_a_missing_divider_then_sorts_around_it():
    """A Group made before groups got dividers: tidy adds the divider, then
    puts the solved puzzle below it, and says so."""
    from bot.cogs.puzzles import Puzzles
    bot = MagicMock(); bot.get_cog.return_value = None
    cog = Puzzles(bot)
    chans = [_channel("s", 31, 0), _channel("my-group", 3, 1), _channel("u", 21, 2)]
    category = MagicMock(id=600); category.text_channels = chans; category.channels = chans
    guild = MagicMock(id=777)
    guild._state.http.bulk_channel_update = AsyncMock()
    ctx = MagicMock(); ctx.guild = guild; ctx.send = AsyncMock()
    ctx.channel = chans[0]; ctx.channel.category = category
    cog._tag_group_for_channel = MagicMock(return_value=MagicMock(has_target=False))

    async def add(g, cat):
        cat.text_channels.append(_channel(SOLVE_DIVIDER, 99, 3))
        return True

    with patch("bot.cogs.puzzles.PuzzleJsonDb") as db, \
            patch("bot.cogs.puzzles.category_layout.group_chat_channel", return_value=chans[1]), \
            patch("bot.cogs.puzzles.category_layout.add_missing_divider",
                  AsyncMock(side_effect=add)) as added:
        db.get_solved_by_channel_ids.return_value = {31: True, 21: False}
        await Puzzles.tidy_category.callback(cog, ctx)

    added.assert_awaited_once_with(guild, category)
    args, _ = guild._state.http.bulk_channel_update.await_args
    assert [e["id"] for e in args[1]] == [3, 21, 99, 31]
    text = ctx.send.await_args.args[0]
    assert "Added the solved divider" in text and "Tidied" in text


@pytest.mark.asyncio
async def test_tidy_category_without_a_divider_it_may_not_add_still_says_so():
    """A split or hand-made category: no divider added, the old message."""
    from bot.cogs.puzzles import Puzzles
    bot = MagicMock(); bot.get_cog.return_value = None
    cog = Puzzles(bot)
    chans = [_channel("u", 21, 0)]
    category = MagicMock(id=600); category.text_channels = chans; category.channels = chans
    ctx = MagicMock(); ctx.guild = MagicMock(id=777); ctx.send = AsyncMock()
    ctx.channel = chans[0]; ctx.channel.category = category
    cog._tag_group_for_channel = MagicMock(return_value=None)

    with patch("bot.cogs.puzzles.PuzzleJsonDb") as db, \
            patch("bot.cogs.puzzles.HuntJsonDb") as hunts, \
            patch("bot.cogs.puzzles.category_layout.add_missing_divider",
                  AsyncMock(return_value=False)):
        db.get_solved_by_channel_ids.return_value = {21: False}
        hunts.get_by_attr.return_value = None
        await Puzzles.tidy_category.callback(cog, ctx)

    assert "no solved divider" in ctx.send.await_args.args[0]
