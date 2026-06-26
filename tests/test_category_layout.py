import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.store.tag_group_category_data import (
    TagGroupCategoryData, ROLE_UNSOLVED, ROLE_SOLVED,
)


def test_tag_group_category_defaults():
    row = TagGroupCategoryData(group_id=5, category_id=999)
    assert row.role == ROLE_UNSOLVED
    assert row.seq == 0
    assert row.id == 0


def test_role_constants():
    assert ROLE_UNSOLVED == "unsolved"
    assert ROLE_SOLVED == "solved"


from bot.core import category_layout as layout


def test_solved_category_name():
    assert layout.solved_category_name("round-1") == "round-1 — solved"


def test_overflow_category_name_unsolved():
    assert layout.overflow_category_name("round-1", "unsolved", 0) == "round-1"
    assert layout.overflow_category_name("round-1", "unsolved", 1) == "round-1 (2)"
    assert layout.overflow_category_name("round-1", "unsolved", 2) == "round-1 (3)"


def test_overflow_category_name_solved():
    assert layout.overflow_category_name("round-1", "solved", 0) == "round-1 — solved"
    assert layout.overflow_category_name("round-1", "solved", 1) == "round-1 — solved (2)"


def test_puzzle_count_excludes_divider():
    from bot.core.discord_channels import SOLVE_DIVIDER
    div = MagicMock(); div.name = SOLVE_DIVIDER
    a = MagicMock(); a.name = "puz-a"
    b = MagicMock(); b.name = "puz-b"
    cat = MagicMock(); cat.channels = [div, a, b]
    assert layout.puzzle_count(cat) == 2


def test_is_split_false_when_no_rows():
    dao = MagicMock()
    dao.get_for_group.return_value = []
    tag_group = MagicMock(id=5)
    assert layout.is_split(tag_group, dao) is False


def test_is_split_true_when_rows_present():
    dao = MagicMock()
    dao.get_for_group.return_value = [TagGroupCategoryData(group_id=5, category_id=9)]
    tag_group = MagicMock(id=5)
    assert layout.is_split(tag_group, dao) is True


def test_soft_limit_below_hard_cap():
    from bot.core.discord_channels import MAX_CHANNELS_PER_CATEGORY
    assert layout.CATEGORY_SOFT_LIMIT < MAX_CHANNELS_PER_CATEGORY


def _split_fixtures(divider_pos=2):
    """Build a guild + a primary category with 1 divider, 2 unsolved (pos<divider)
    and 1 solved (pos>divider) channel."""
    from bot.core.discord_channels import SOLVE_DIVIDER
    div = MagicMock(); div.name = SOLVE_DIVIDER; div.position = divider_pos
    div.delete = AsyncMock()
    u1 = MagicMock(); u1.name = "u1"; u1.position = 0; u1.edit = AsyncMock()
    u2 = MagicMock(); u2.name = "u2"; u2.position = 1; u2.edit = AsyncMock()
    s1 = MagicMock(); s1.name = "s1"; s1.position = 3; s1.edit = AsyncMock()
    primary = MagicMock(id=100, position=5)
    primary.name = "round-1"
    primary.channels = [u1, div, u2, s1]
    solved_cat = MagicMock(id=200)
    solved_cat.edit = AsyncMock()
    primary.clone = AsyncMock(return_value=solved_cat)
    guild = MagicMock()
    guild.categories = [primary]
    return guild, primary, solved_cat, div, [u1, u2], [s1]


@pytest.mark.asyncio
async def test_split_group_moves_solved_deletes_divider_writes_rows():
    guild, primary, solved_cat, div, unsolved, solved = _split_fixtures()
    dao = MagicMock()
    dao.get_for_group.return_value = []           # not yet split
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.split_group(guild, tag_group, dao)

    # Solved channel moved into the new solved category; unsolved untouched.
    solved[0].edit.assert_awaited_once_with(category=solved_cat)
    unsolved[0].edit.assert_not_awaited()
    # Divider deleted.
    div.delete.assert_awaited_once()
    # Two rows written: unsolved seq0 -> primary, solved seq0 -> solved_cat.
    assert dao.commit.call_count == 2
    written = [c.args[0] for c in dao.commit.call_args_list]
    roles = {(r.role, r.seq, r.category_id) for r in written}
    assert ("unsolved", 0, 100) in roles
    assert ("solved", 0, 200) in roles


@pytest.mark.asyncio
async def test_split_group_idempotent_when_already_split():
    guild, primary, solved_cat, div, unsolved, solved = _split_fixtures()
    dao = MagicMock()
    dao.get_for_group.return_value = [TagGroupCategoryData(group_id=5, category_id=100)]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.split_group(guild, tag_group, dao)

    primary.clone.assert_not_called()
    dao.commit.assert_not_called()


# ----------------------------------------------------------------- picker/allocation tests

def _cat(cid, position, n_channels, name="round-1"):
    chans = []
    for i in range(n_channels):
        ch = MagicMock(); ch.name = f"p{cid}-{i}"; chans.append(ch)
    cat = MagicMock(id=cid, position=position)
    cat.name = name
    cat.channels = chans
    cat.edit = AsyncMock()
    return cat


@pytest.mark.asyncio
async def test_pick_unsolved_single_with_room_returns_primary():
    primary = _cat(100, 5, n_channels=3)
    guild = MagicMock(); guild.categories = [primary]
    dao = MagicMock(); dao.get_for_group.return_value = []
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    result = await layout.pick_unsolved_category(guild, tag_group, dao)
    assert result is primary
    dao.commit.assert_not_called()


@pytest.mark.asyncio
async def test_pick_unsolved_split_picks_lowest_seq_with_room():
    u0 = _cat(100, 5, n_channels=layout.CATEGORY_SOFT_LIMIT)   # full
    u1 = _cat(101, 6, n_channels=3)                            # has room
    guild = MagicMock(); guild.categories = [u0, u1]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(group_id=5, category_id=101, role="unsolved", seq=1),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    result = await layout.pick_unsolved_category(guild, tag_group, dao)
    assert result is u1


@pytest.mark.asyncio
async def test_pick_unsolved_allocates_when_all_full():
    u0 = _cat(100, 5, n_channels=layout.CATEGORY_SOFT_LIMIT)   # full
    new_cat = _cat(101, 6, n_channels=0)
    u0.clone = AsyncMock(return_value=new_cat)
    guild = MagicMock(); guild.categories = [u0]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    result = await layout.pick_unsolved_category(guild, tag_group, dao)
    assert result is new_cat
    # New row written for unsolved seq=1.
    row = dao.commit.call_args.args[0]
    assert (row.role, row.seq, row.category_id) == ("unsolved", 1, 101)
