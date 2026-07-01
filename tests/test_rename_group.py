from types import SimpleNamespace

import pytest

from bot.core import category_layout
from tests.conftest import make_round


class _FakeCat:
    def __init__(self, id, name, text_channels=()):
        self.id = id
        self.name = name
        self.text_channels = list(text_channels)


class _FakeChan:
    def __init__(self, id, name):
        self.id = id
        self.name = name


class _FakeGuild:
    def __init__(self, categories=(), channels=()):
        self.categories = list(categories)
        self._channels = {c.id: c for c in channels}

    def get_channel(self, cid):
        return self._channels.get(cid)


class _FakeDao:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def get_for_group(self, group_id):
        return self._rows


def test_plan_meta_single_category_renames_only_category():
    cat = _FakeCat(id=999, name="old-round")
    guild = _FakeGuild(categories=[cat])
    group = make_round(id=1, name="old round", target_puzzle_id=7, category_id=999)
    plan = category_layout.group_rename_plan(guild, group, "New Round", _FakeDao())
    assert plan == [(cat, "New Round")]


def test_plan_metaless_renames_category_and_text_channel():
    chan = _FakeChan(id=500, name="old-group")
    cat = _FakeCat(id=999, name="old-group", text_channels=[chan])
    guild = _FakeGuild(categories=[cat])
    group = make_round(id=2, name="old group", target_puzzle_id=0, category_id=999)
    plan = category_layout.group_rename_plan(guild, group, "New Group", _FakeDao())
    assert plan == [(cat, "New Group"), (chan, "new-group")]


def test_plan_metaless_matches_text_channel_when_category_name_unnormalized():
    # Create-time collision path: the category keeps its verbatim, space-padded
    # name while the text channel is Discord-normalized. The planner must still
    # find the text channel (matched via clean_channel_name of the category's name).
    chan = _FakeChan(id=500, name="old-—-other")
    cat = _FakeCat(id=999, name="old — other", text_channels=[chan])
    guild = _FakeGuild(categories=[cat])
    group = make_round(id=20, name="old — other", target_puzzle_id=0, category_id=999)
    plan = category_layout.group_rename_plan(guild, group, "New Group", _FakeDao())
    assert plan == [(cat, "New Group"), (chan, "new-group")]


def test_plan_metaless_matches_text_channel_after_create_time_category_collision():
    # Create-time collision appends " — <hunt>" to the CATEGORY name, but the
    # text channel was created from clean_channel_name(raw group name) with no
    # suffix. The planner must match the channel via the group's stored name,
    # not the (suffixed) category name, or the channel keeps its stale name.
    chan = _FakeChan(id=500, name="group")
    cat = _FakeCat(id=999, name="Group — MyHunt", text_channels=[chan])
    guild = _FakeGuild(categories=[cat])
    group = make_round(id=21, name="Group", target_puzzle_id=0, category_id=999)
    plan = category_layout.group_rename_plan(guild, group, "New Group", _FakeDao())
    assert plan == [(cat, "New Group"), (chan, "new-group")]


def test_plan_split_round_renames_all_categories():
    from bot.store.tag_group_category_data import ROLE_SOLVED, ROLE_UNSOLVED
    unsolved = _FakeCat(id=10, name="old-round")
    solved = _FakeCat(id=11, name="old-round — solved")
    guild = _FakeGuild(categories=[unsolved, solved])
    group = make_round(id=3, name="old round", target_puzzle_id=7, category_id=10)
    rows = [
        SimpleNamespace(category_id=10, role=ROLE_UNSOLVED, seq=0),
        SimpleNamespace(category_id=11, role=ROLE_SOLVED, seq=0),
    ]
    plan = category_layout.group_rename_plan(guild, group, "New Round", _FakeDao(rows))
    assert (unsolved, "New Round") in plan
    assert (solved, "New Round — solved") in plan
    assert len(plan) == 2


from unittest.mock import AsyncMock, patch

from tests.conftest import make_cog, make_ctx, make_hunt, make_puzzle


def _rename_cog(channel_type, tag_group, puzzle=None):
    from bot.cogs.puzzles import Puzzles
    cog = make_cog()
    cog.get_channel_type = lambda ctx: channel_type
    cog.get_tag_group = lambda ctx: tag_group
    cog.get_puzzle = lambda ctx: puzzle
    cog.get_hunt = lambda ctx: make_hunt()
    cog.service.rename_group = AsyncMock()
    return cog, Puzzles


@pytest.mark.asyncio
async def test_rename_group_from_target_puzzle_channel():
    grp = make_round(id=7, name="Old", target_puzzle_id=70)
    puz = make_puzzle(id=70)
    cog, Puzzles = _rename_cog("Metapuzzle", grp, puz)
    ctx = make_ctx()
    ctx.channel.send = AsyncMock()  # success path awaits ctx.channel.send
    with patch("bot.cogs.puzzles.TagGroupJsonDb") as rdb:
        rdb.check_duplicates_in_hunt.return_value = False
        await Puzzles.rename_group.callback(cog, ctx, group_name="New Name")
    cog.service.rename_group.assert_awaited_once()
    assert cog.service.rename_group.await_args.args[3] == "New Name"


@pytest.mark.asyncio
async def test_rename_group_from_regular_group_channel():
    grp = make_round(id=8, name="Old", target_puzzle_id=0)
    cog, Puzzles = _rename_cog("Group", grp, None)
    ctx = make_ctx()
    ctx.channel.send = AsyncMock()  # success path awaits ctx.channel.send
    with patch("bot.cogs.puzzles.TagGroupJsonDb") as rdb:
        rdb.check_duplicates_in_hunt.return_value = False
        await Puzzles.rename_group.callback(cog, ctx, group_name="New Name")
    cog.service.rename_group.assert_awaited_once()


@pytest.mark.asyncio
async def test_rename_group_excludes_self_from_duplicate_check():
    # Renaming a group must exclude its own row from the hunt duplicate check,
    # so an idempotent/punctuation-only rename isn't rejected as a self-clash.
    grp = make_round(id=7, name="Old", target_puzzle_id=70)
    puz = make_puzzle(id=70)
    cog, Puzzles = _rename_cog("Metapuzzle", grp, puz)
    ctx = make_ctx()
    ctx.channel.send = AsyncMock()
    with patch("bot.cogs.puzzles.TagGroupJsonDb") as rdb:
        rdb.check_duplicates_in_hunt.return_value = False
        await Puzzles.rename_group.callback(cog, ctx, group_name="Old!")
    rdb.check_duplicates_in_hunt.assert_called_once()
    assert rdb.check_duplicates_in_hunt.call_args.kwargs.get("exclude_group_id") == 7
    cog.service.rename_group.assert_awaited_once()


@pytest.mark.asyncio
async def test_rename_group_rejects_non_group_channel():
    grp = make_round(id=9, name="Old", target_puzzle_id=70)
    puz = make_puzzle(id=99)  # not the target
    cog, Puzzles = _rename_cog("Puzzle", grp, puz)
    ctx = make_ctx()
    await Puzzles.rename_group.callback(cog, ctx, group_name="New Name")
    cog.service.rename_group.assert_not_awaited()
    assert ctx.send.await_count == 1


@pytest.mark.asyncio
async def test_rename_group_rejects_duplicate_name():
    grp = make_round(id=10, name="Old", target_puzzle_id=70)
    puz = make_puzzle(id=70)
    cog, Puzzles = _rename_cog("Metapuzzle", grp, puz)
    ctx = make_ctx()
    with patch("bot.cogs.puzzles.TagGroupJsonDb") as rdb:
        rdb.check_duplicates_in_hunt.return_value = True
        await Puzzles.rename_group.callback(cog, ctx, group_name="Dupe")
    cog.service.rename_group.assert_not_awaited()
    assert ctx.send.await_count == 1
