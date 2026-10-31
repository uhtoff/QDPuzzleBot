"""#15: !sync must also tag puzzles moved into a split round's solved/overflow
categories, not just the puzzles remaining in the group's primary category."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_cog, make_ctx, make_round
from bot.cogs.puzzles import Puzzles
from bot.store.tag_group_category_data import ROLE_SOLVED, ROLE_UNSOLVED, TagGroupCategoryData


def _make_channel(id):
    channel = MagicMock()
    channel.id = id
    return channel


def _make_category(id, channels):
    category = MagicMock()
    category.id = id
    category.channels = channels
    return category


def _make_sync_ctx(primary_category, extra_categories=()):
    cog = make_cog()
    cog.get_channel_type = lambda ctx: "Metapuzzle"
    cog.get_hunt = lambda ctx: None
    cog.service.tag_edge_only = AsyncMock()
    cog.service.refresh_unassigned_safe = AsyncMock()
    cog.service.refresh_header_links = AsyncMock()

    ctx = make_ctx()
    ctx.channel.category = primary_category
    ctx.guild.categories = [primary_category, *extra_categories]
    return cog, ctx


class TestSyncSplitCategories:
    @pytest.mark.asyncio
    async def test_unsplit_round_syncs_only_primary_category(self):
        puzzle = MagicMock(id=1, name="Feeder", tags=[])
        channel = _make_channel(101)
        primary = _make_category(1, [channel])
        target_group = make_round(id=5, category_id=1)
        target_group.type = "Metapuzzle"

        cog, ctx = _make_sync_ctx(primary)

        with patch("bot.cogs.puzzles.TagGroupJsonDb") as tgdb, \
             patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb, \
             patch("bot.cogs.puzzles.TagGroupCategoryDb") as tgcdb:
            tgdb.get_by_attr.return_value = target_group
            pdb.get_by_attr.return_value = puzzle
            tgcdb.get_for_group.return_value = []

            await Puzzles.sync.callback(cog, ctx)

        cog.service.tag_edge_only.assert_awaited_once_with(
            puzzle, target_group, "feeds", guild=None, refresh_headers=False)

    @pytest.mark.asyncio
    async def test_split_round_also_syncs_solved_category(self):
        """A puzzle moved to the solved category is tracked only via
        TagGroupCategoryDb, not TagGroupJsonDb.category_id — the old code
        silently skipped it."""
        primary_puzzle = MagicMock(id=1, name="Feeder A", tags=[])
        solved_puzzle = MagicMock(id=2, name="Feeder B (solved)", tags=[])

        primary_channel = _make_channel(101)
        solved_channel = _make_channel(202)
        primary = _make_category(1, [primary_channel])
        solved = _make_category(2, [solved_channel])

        target_group = make_round(id=5, category_id=1)
        target_group.type = "Metapuzzle"

        cog, ctx = _make_sync_ctx(primary, extra_categories=[solved])

        # !sync now scans the categories, then RE-READS each puzzle by id under
        # the group lock (see Puzzles.sync), so the stub has to answer both
        # lookups. Same two puzzles either way — the assertion below is unchanged.
        def _puzzle_for_channel(channel_id=None, id=None):
            if channel_id is not None:
                return {101: primary_puzzle, 202: solved_puzzle}.get(channel_id)
            return {1: primary_puzzle, 2: solved_puzzle}.get(id)

        with patch("bot.cogs.puzzles.TagGroupJsonDb") as tgdb, \
             patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb, \
             patch("bot.cogs.puzzles.TagGroupCategoryDb") as tgcdb:
            tgdb.get_by_attr.return_value = target_group
            pdb.get_by_attr.side_effect = _puzzle_for_channel
            tgcdb.get_for_group.return_value = [
                TagGroupCategoryData(group_id=5, category_id=1, role=ROLE_UNSOLVED, seq=0),
                TagGroupCategoryData(group_id=5, category_id=2, role=ROLE_SOLVED, seq=0),
            ]

            await Puzzles.sync.callback(cog, ctx)

        synced_puzzles = {c.args[0] for c in cog.service.tag_edge_only.await_args_list}
        assert synced_puzzles == {primary_puzzle, solved_puzzle}


@pytest.mark.asyncio
async def test_sync_writes_feeds_lines_once_for_the_whole_loop():
    """Header writes are capped: three synced puzzles skip their own Feeds-line
    write and the sync makes ONE refresh_header_links call covering all three."""
    puzzles = {i: MagicMock(id=i, name=f"Feeder {i}", tags=[]) for i in (1, 2, 3)}
    channels = [_make_channel(100 + i) for i in (1, 2, 3)]
    primary = _make_category(1, channels)
    target_group = make_round(id=5, category_id=1)
    target_group.type = "Metapuzzle"
    cog, ctx = _make_sync_ctx(primary)

    def _puzzle(channel_id=None, id=None):
        return puzzles.get(channel_id - 100 if channel_id is not None else id)

    with patch("bot.cogs.puzzles.TagGroupJsonDb") as tgdb, \
         patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb, \
         patch("bot.cogs.puzzles.TagGroupCategoryDb") as tgcdb:
        tgdb.get_by_attr.return_value = target_group
        pdb.get_by_attr.side_effect = _puzzle
        tgcdb.get_for_group.return_value = []
        await Puzzles.sync.callback(cog, ctx)

    assert cog.service.tag_edge_only.await_count == 3
    assert all(c.kwargs.get("refresh_headers") is False
               for c in cog.service.tag_edge_only.await_args_list)
    cog.service.refresh_header_links.assert_awaited_once()
    assert set(cog.service.refresh_header_links.await_args.args[1]) == set(puzzles.values())
