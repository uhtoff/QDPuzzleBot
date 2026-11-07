"""!archive_round on a SPLIT owner must archive EVERY category the owner owns
(unsolved / '— solved' / '(N)' overflow), delete them all, and drop the join
rows — not just the category the command happened to run in, which stranded
the sibling categories and left every join row dangling."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.store.tag_group_category_data import TagGroupCategoryData
from tests.conftest import make_cog, make_hunt, make_round


def _cat(cid):
    cat = MagicMock(id=cid)
    cat.name = f"cat{cid}"
    cat.channels = []
    cat.delete = AsyncMock()
    return cat


def _ctx(categories, current):
    ctx = MagicMock()
    ctx.guild.categories = categories
    ctx.channel.category = current
    return ctx


@pytest.mark.asyncio
async def test_archive_round_split_owner_archives_all_categories():
    hunt = make_hunt(id=10)
    group = make_round(id=5, category_id=100)
    cog = make_cog(hunt=hunt)
    unsolved, solved, overflow = _cat(100), _cat(200), _cat(201)
    ctx = _ctx([unsolved, solved, overflow], unsolved)
    general = MagicMock(); general.send = AsyncMock()
    cog.get_hunt_channel = MagicMock(return_value=general)
    cog.get_channel_type = MagicMock(return_value="Round")
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(group_id=5, category_id=200, role="solved", seq=0),
        TagGroupCategoryData(group_id=5, category_id=201, role="solved", seq=1),
    ]
    cog._owner_for_channel = MagicMock(return_value=(group, dao))
    cog.archive_channel = AsyncMock(return_value=True)

    result = await cog.archive_round.callback(cog, ctx)

    assert result is True
    unsolved.delete.assert_awaited_once()
    solved.delete.assert_awaited_once()
    overflow.delete.assert_awaited_once()
    dao.delete_for_group.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_archive_round_single_category_keeps_legacy_path():
    """An unsplit round archives exactly the invoking channel's category, as
    before."""
    hunt = make_hunt(id=10)
    cog = make_cog(hunt=hunt)
    cat = _cat(100)
    ctx = _ctx([cat], cat)
    general = MagicMock(); general.send = AsyncMock()
    cog.get_hunt_channel = MagicMock(return_value=general)
    cog.get_channel_type = MagicMock(return_value="Round")
    cog._owner_for_channel = MagicMock(return_value=(None, None))
    cog.archive_category = AsyncMock(return_value=True)

    result = await cog.archive_round.callback(cog, ctx)

    assert result is True
    cog.archive_category.assert_awaited_once()
    cat.delete.assert_awaited_once()
