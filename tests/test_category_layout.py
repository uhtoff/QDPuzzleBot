import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.store.tag_group_category_data import (
    TagGroupCategoryData, ROLE_UNSOLVED, ROLE_SOLVED,
)
from bot.core.discord_channels import SOLVE_DIVIDER


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
async def test_pick_unsolved_in_place_group_uses_channel_parent_category():
    # An in-place-converted group stores its target's CHANNEL id in category_id,
    # which isn't in guild.categories. New puzzles must land in that channel's
    # parent category rather than nowhere.
    parent = _cat(100, 5, n_channels=3)
    home_channel = MagicMock(id=555)
    home_channel.category = parent
    guild = MagicMock()
    guild.categories = [parent]                 # 555 is NOT a category
    guild.get_channel.return_value = home_channel
    dao = MagicMock(); dao.get_for_group.return_value = []
    tag_group = MagicMock(id=5, category_id=555, name="Meta")

    result = await layout.pick_unsolved_category(guild, tag_group, dao)
    assert result is parent


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


def test_group_id_for_category_uses_dao_row():
    dao = MagicMock()
    dao.get_by_category.return_value = TagGroupCategoryData(
        group_id=42, category_id=200, role="solved", seq=0)
    assert layout.group_id_for_category(200, dao) == 42


def test_group_id_for_category_none_when_unknown():
    dao = MagicMock()
    dao.get_by_category.return_value = None
    assert layout.group_id_for_category(999, dao) is None


@pytest.mark.asyncio
async def test_move_channel_to_solved_moves_into_solved_category():
    solved_cat = _cat(200, 6, n_channels=0)
    guild = MagicMock(); guild.categories = [solved_cat]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(group_id=5, category_id=200, role="solved", seq=0),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")
    source = MagicMock(id=100)
    channel = MagicMock(); channel.category = source; channel.edit = AsyncMock()

    returned_source = await layout.move_channel_to_solved(guild, tag_group, dao, channel)
    channel.edit.assert_awaited_once_with(category=solved_cat)
    assert returned_source is source


@pytest.mark.asyncio
async def test_move_channel_to_solved_noop_if_already_there():
    solved_cat = _cat(200, 6, n_channels=0)
    guild = MagicMock(); guild.categories = [solved_cat]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=200, role="solved", seq=0),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")
    channel = MagicMock(); channel.category = solved_cat; channel.edit = AsyncMock()

    returned = await layout.move_channel_to_solved(guild, tag_group, dao, channel)
    channel.edit.assert_not_awaited()
    assert returned is None


@pytest.mark.asyncio
async def test_cleanup_deletes_empty_overflow_and_row():
    overflow = _cat(201, 7, n_channels=0)
    overflow.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [overflow]
    row = TagGroupCategoryData(id=9, group_id=5, category_id=201, role="solved", seq=1)
    dao = MagicMock(); dao.get_by_category.return_value = row
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.cleanup_empty_overflow(guild, tag_group, dao, overflow)
    overflow.delete.assert_awaited_once()
    dao.delete.assert_called_once_with(9)


@pytest.mark.asyncio
async def test_cleanup_keeps_primary_seq0():
    primary = _cat(100, 5, n_channels=0)
    primary.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [primary]
    row = TagGroupCategoryData(id=1, group_id=5, category_id=100, role="unsolved", seq=0)
    dao = MagicMock(); dao.get_by_category.return_value = row
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.cleanup_empty_overflow(guild, tag_group, dao, primary)
    primary.delete.assert_not_awaited()
    dao.delete.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_keeps_nonempty_overflow():
    overflow = _cat(201, 7, n_channels=3)
    overflow.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [overflow]
    row = TagGroupCategoryData(id=9, group_id=5, category_id=201, role="solved", seq=1)
    dao = MagicMock(); dao.get_by_category.return_value = row
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.cleanup_empty_overflow(guild, tag_group, dao, overflow)
    overflow.delete.assert_not_awaited()
    dao.delete.assert_not_called()


# ----------------------------------------------------------------- teardown tests

@pytest.mark.asyncio
async def test_delete_group_categories_removes_all_and_rows():
    primary = _cat(100, 5, n_channels=0); primary.delete = AsyncMock()
    solved = _cat(200, 6, n_channels=0); solved.delete = AsyncMock()
    overflow = _cat(201, 7, n_channels=0); overflow.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [primary, solved, overflow]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(group_id=5, category_id=200, role="solved", seq=0),
        TagGroupCategoryData(group_id=5, category_id=201, role="solved", seq=1),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    result = await layout.delete_group_categories(guild, tag_group, dao)

    assert result is True
    primary.delete.assert_awaited_once()
    solved.delete.assert_awaited_once()
    overflow.delete.assert_awaited_once()
    dao.delete_for_group.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_delete_group_categories_single_state_uses_legacy_only():
    # No join rows → returns False so the caller runs its legacy single-category path.
    guild = MagicMock(); guild.categories = []
    dao = MagicMock(); dao.get_for_group.return_value = []
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")
    assert await layout.delete_group_categories(guild, tag_group, dao) is False


@pytest.mark.asyncio
async def test_delete_group_categories_deletes_child_channels():
    primary = _cat(100, 5, n_channels=2); primary.delete = AsyncMock()
    for ch in primary.channels:
        ch.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [primary]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.delete_group_categories(guild, tag_group, dao)
    for ch in primary.channels:
        ch.delete.assert_awaited_once()
    primary.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_allocate_raises_when_no_category_budget():
    from bot.core.discord_channels import MAX_CATEGORIES
    u0 = _cat(100, 5, n_channels=layout.CATEGORY_SOFT_LIMIT)
    guild = MagicMock()
    guild.categories = [u0] * MAX_CATEGORIES   # at the ceiling
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")
    with pytest.raises(RuntimeError, match="no_category_space"):
        await layout.pick_unsolved_category(guild, tag_group, dao)


# --------------------------------------------------------------- primary_category
def test_primary_category_resolves_category_home():
    """A channel-layout group's category_id IS a Discord category -> resolved."""
    cat = MagicMock(id=999)
    guild = MagicMock(categories=[cat])
    tag_group = MagicMock(category_id=999)
    assert layout.primary_category(guild, tag_group) is cat


def test_primary_category_none_for_non_category_home():
    """Forum/group/in-place homes store a non-category channel id, which is not
    among guild.categories -> None (the caller then skips category placement)."""
    cat = MagicMock(id=999)
    guild = MagicMock(categories=[cat])
    tag_group = MagicMock(category_id=12345)  # a forum/text/puzzle channel id
    assert layout.primary_category(guild, tag_group) is None


def test_primary_category_none_for_unset_home():
    """A channel-less group (category_id == 0) -> None."""
    guild = MagicMock(categories=[MagicMock(id=999)])
    assert layout.primary_category(guild, MagicMock(category_id=0)) is None


# ----------------------------------------------------------------- group_chat_channel

def test_group_chat_channel_none_when_has_target():
    tag_group = MagicMock(category_id=100)
    tag_group.has_target = True
    assert layout.group_chat_channel(MagicMock(), tag_group) is None


def test_group_chat_channel_none_when_no_primary_category():
    tag_group = MagicMock(category_id=0)
    tag_group.has_target = False
    guild = MagicMock()
    guild.categories = []
    assert layout.group_chat_channel(guild, tag_group) is None


def test_group_chat_channel_finds_by_name():
    tag_group = MagicMock(category_id=100)
    tag_group.name = "Loose Ends"
    tag_group.has_target = False
    chat_ch = MagicMock()
    chat_ch.name = "loose-ends"
    primary = MagicMock(id=100)
    primary.text_channels = [chat_ch]
    guild = MagicMock()
    guild.categories = [primary]
    assert layout.group_chat_channel(guild, tag_group) is chat_ch


# ----------------------------------------------------------------- move_channel_to_general

@pytest.mark.asyncio
async def test_move_channel_to_general_no_category_returns_false():
    guild = MagicMock()
    guild.categories = []
    channel = MagicMock()
    channel.edit = AsyncMock()
    assert await layout.move_channel_to_general(guild, 999, channel, False) is False
    channel.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_channel_to_general_full_category_overflows_to_new_category():
    from bot.core.discord_channels import MAX_CHANNELS_PER_CATEGORY
    general = MagicMock(id=500, name="General", position=3)
    general.name = "General"
    general.channels = [MagicMock() for _ in range(MAX_CHANNELS_PER_CATEGORY)]
    overflow = MagicMock(id=501)
    overflow.channels = []
    general.clone = AsyncMock(return_value=overflow)
    overflow.edit = AsyncMock()
    guild = MagicMock()
    guild.categories = [general]
    channel = MagicMock()
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_general(guild, 500, channel, False)

    assert result is True
    general.clone.assert_awaited_once_with(name="General (2)")
    overflow.edit.assert_awaited_once_with(position=4)
    channel.edit.assert_awaited_once_with(category=overflow, position=0)


@pytest.mark.asyncio
async def test_move_channel_to_general_reuses_existing_overflow_with_room():
    from bot.core.discord_channels import MAX_CHANNELS_PER_CATEGORY
    general = MagicMock(id=500)
    general.name = "General"
    general.channels = [MagicMock() for _ in range(MAX_CHANNELS_PER_CATEGORY)]
    general.clone = AsyncMock()
    overflow = MagicMock(id=501)
    overflow.name = "General (2)"
    overflow.channels = []
    guild = MagicMock()
    guild.categories = [general, overflow]
    channel = MagicMock()
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_general(guild, 500, channel, False)

    assert result is True
    general.clone.assert_not_awaited()
    channel.edit.assert_awaited_once_with(category=overflow, position=0)


@pytest.mark.asyncio
async def test_move_channel_to_general_no_category_space_returns_false():
    from bot.core.discord_channels import MAX_CHANNELS_PER_CATEGORY, MAX_CATEGORIES
    general = MagicMock(id=500)
    general.name = "General"
    general.channels = [MagicMock() for _ in range(MAX_CHANNELS_PER_CATEGORY)]
    general.clone = AsyncMock()
    others = [MagicMock() for _ in range(MAX_CATEGORIES - 1)]
    guild = MagicMock()
    guild.categories = [general] + others
    channel = MagicMock()
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_general(guild, 500, channel, False)

    assert result is False
    general.clone.assert_not_awaited()
    channel.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_channel_to_general_unsolved_positions_above_divider():
    div = MagicMock(); div.name = SOLVE_DIVIDER; div.position = 5
    other = MagicMock(); other.name = "general"; other.position = 0
    general = MagicMock(id=500)
    general.channels = [div, other]
    guild = MagicMock()
    guild.categories = [general]
    channel = MagicMock()
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_general(guild, 500, channel, False)

    assert result is True
    channel.edit.assert_awaited_once_with(category=general, position=4)


@pytest.mark.asyncio
async def test_move_channel_to_general_solved_positions_after_divider():
    div = MagicMock(); div.name = SOLVE_DIVIDER; div.position = 5
    other = MagicMock(); other.name = "general"; other.position = 0
    general = MagicMock(id=500)
    general.channels = [div, other]
    guild = MagicMock()
    guild.categories = [general]
    channel = MagicMock()
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_general(guild, 500, channel, True)

    assert result is True
    channel.edit.assert_awaited_once_with(category=general, position=6)


@pytest.mark.asyncio
async def test_move_channel_to_general_no_divider_appends_at_end():
    other = MagicMock(); other.name = "general"; other.position = 3
    general = MagicMock(id=500)
    general.channels = [other]           # no SOLVE_DIVIDER channel present
    guild = MagicMock()
    guild.categories = [general]
    channel = MagicMock()
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_general(guild, 500, channel, False)

    assert result is True
    channel.edit.assert_awaited_once_with(category=general, position=4)


# ----------------------------------------------------------------- delete_group_categories keep_channel_ids

@pytest.mark.asyncio
async def test_delete_group_categories_keeps_protected_channel():
    keep = MagicMock(id=42); keep.delete = AsyncMock()
    doomed = MagicMock(id=43); doomed.delete = AsyncMock()
    cat = MagicMock(id=100); cat.channels = [keep, doomed]; cat.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [cat]
    dao = MagicMock()
    dao.get_for_group.return_value = [TagGroupCategoryData(group_id=5, category_id=100)]
    tag_group = MagicMock(id=5)

    result = await layout.delete_group_categories(guild, tag_group, dao, keep_channel_ids={42})

    assert result is True
    keep.delete.assert_not_called()
    doomed.delete.assert_awaited_once()
    cat.delete.assert_awaited_once()
    dao.delete_for_group.assert_called_once_with(5)
