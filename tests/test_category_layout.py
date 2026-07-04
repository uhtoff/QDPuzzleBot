import asyncio

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


@pytest.fixture(autouse=True)
def _fresh_layout_lock(monkeypatch):
    """layout_lock is module-level, and an asyncio.Lock binds to the event loop
    of its first *contended* acquire — but pytest-asyncio gives every test its
    own loop, so a lock contended in one test raises 'bound to a different
    event loop' in the next. Give each test a fresh lock."""
    monkeypatch.setattr(layout, "layout_lock", asyncio.Lock())


@pytest.fixture(autouse=True)
def _no_store_claims(monkeypatch):
    """Stub the lazily-imported bot.store lookups (claim checks, source-category
    resolution) to miss by default so no unit test here touches MySQL; tests
    that care override via _patch_no_claims / their own monkeypatching."""
    import bot.store as store
    for name in ("TagGroupCategoryDb", "HuntCategoryDb"):
        stub = MagicMock()
        stub.get_by_category.return_value = None
        monkeypatch.setattr(store, name, stub)
    for name in ("TagGroupJsonDb", "HuntJsonDb"):
        stub = MagicMock()
        stub.get_by_attr.return_value = None
        monkeypatch.setattr(store, name, stub)


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


def test_soft_limit_is_cap_minus_small_fixed_headroom():
    """The headroom is a small fixed number (2: room to delete the solve
    divider and land the new channel), not a large or proportional buffer —
    a hunt server already tends to have a lot of categories, so splitting
    earlier than necessary just means more categories than needed."""
    from bot.core.discord_channels import MAX_CHANNELS_PER_CATEGORY
    assert layout.CATEGORY_SOFT_LIMIT == max(1, MAX_CHANNELS_PER_CATEGORY - 2)


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
    # Same objects: everything in this fixture is a text channel, and split's
    # mover selection reads the text-only view (voice positions live in a
    # separate Discord sorting bucket and must not be compared).
    primary.text_channels = primary.channels
    solved_cat = MagicMock(id=200)
    solved_cat.channels = []
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
    dao.make_row.side_effect = lambda owner_id, category_id, role, seq: TagGroupCategoryData(
        group_id=owner_id, category_id=category_id, role=role, seq=seq)
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.split_group(guild, tag_group, dao)

    # Solved channel moved into the new solved category WITH an explicit
    # position (discord.py only re-sorts when `position` is given; without it
    # the channel keeps its old absolute position number); unsolved untouched.
    solved[0].edit.assert_awaited_once_with(category=solved_cat, position=0)
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
async def test_split_group_leaves_voice_channels_alone():
    """CategoryChannel.channels includes voice channels, but Discord numbers
    voice positions in a SEPARATE sorting bucket from text — comparing a voice
    position against the divider's text position is meaningless and could drag
    a lobby/voice channel into the '— solved' category. Only text channels are
    mover candidates."""
    guild, primary, solved_cat, div, unsolved, solved = _split_fixtures()
    voice = MagicMock(); voice.name = "lobby"; voice.position = 30   # voice bucket
    voice.edit = AsyncMock()
    primary.channels = primary.channels + [voice]    # in .channels ...
    # ... but NOT in .text_channels (discord.py's text-only view)
    dao = MagicMock()
    dao.get_for_group.return_value = []
    dao.make_row.side_effect = lambda owner_id, category_id, role, seq: TagGroupCategoryData(
        group_id=owner_id, category_id=category_id, role=role, seq=seq)
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.split_group(guild, tag_group, dao)

    voice.edit.assert_not_awaited()          # stays in the unsolved category
    solved[0].edit.assert_awaited_once()     # real solved text channel still moves


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
    dao.make_row.side_effect = lambda owner_id, category_id, role, seq: TagGroupCategoryData(
        group_id=owner_id, category_id=category_id, role=role, seq=seq)
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    result = await layout.pick_unsolved_category(guild, tag_group, dao)
    assert result is new_cat
    # New row written for unsolved seq=1.
    row = dao.commit.call_args.args[0]
    assert (row.role, row.seq, row.category_id) == ("unsolved", 1, 101)


def _patch_no_claims(monkeypatch):
    """Make the lazy bot.store claim lookups all miss (nothing claims any
    category). Returns the four mocks for per-test overrides."""
    import bot.store as store
    tgc = MagicMock(); tgc.get_by_category.return_value = None
    hc = MagicMock(); hc.get_by_category.return_value = None
    tgj = MagicMock(); tgj.get_by_attr.return_value = None
    hj = MagicMock(); hj.get_by_attr.return_value = None
    monkeypatch.setattr(store, "TagGroupCategoryDb", tgc)
    monkeypatch.setattr(store, "HuntCategoryDb", hc)
    monkeypatch.setattr(store, "TagGroupJsonDb", tgj)
    monkeypatch.setattr(store, "HuntJsonDb", hj)
    return tgc, hc, tgj, hj


@pytest.mark.asyncio
async def test_allocate_does_not_adopt_foreign_category_by_name(monkeypatch):
    """A guild category that merely SHARES the computed overflow name (e.g. a
    round literally named 'round-1 (2)' created before round 'round-1' ever
    overflowed) must not be adopted: adoption commits a join row claiming it,
    and delete_round would later delete the foreign round's channels. A fresh
    clone is created instead — Discord permits duplicate category names."""
    _, _, tgj, _ = _patch_no_claims(monkeypatch)
    foreign_round = MagicMock(id=77)
    tgj.get_by_attr.return_value = foreign_round      # 'round-1 (2)' is another round's home

    u0 = _cat(100, 5, n_channels=layout.CATEGORY_SOFT_LIMIT)   # full
    foreign = _cat(300, 9, n_channels=2, name="round-1 (2)")
    new_cat = _cat(101, 6, n_channels=0)
    u0.clone = AsyncMock(return_value=new_cat)
    guild = MagicMock(); guild.categories = [u0, foreign]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
    ]
    dao.make_row.side_effect = lambda owner_id, category_id, role, seq: TagGroupCategoryData(
        group_id=owner_id, category_id=category_id, role=role, seq=seq)
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    result = await layout.pick_unsolved_category(guild, tag_group, dao)

    assert result is new_cat
    u0.clone.assert_awaited_once()
    row = dao.commit.call_args.args[0]
    assert row.category_id == 101                     # never the foreign 300


@pytest.mark.asyncio
async def test_allocate_still_adopts_unclaimed_same_name_category(monkeypatch):
    """Crash-resume: a same-name category claimed by NOBODY (our own clone from
    a run that died before committing the row) is still adopted, not re-cloned."""
    _patch_no_claims(monkeypatch)

    u0 = _cat(100, 5, n_channels=layout.CATEGORY_SOFT_LIMIT)   # full
    orphan = _cat(300, 9, n_channels=0, name="round-1 (2)")
    u0.clone = AsyncMock()
    guild = MagicMock(); guild.categories = [u0, orphan]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
    ]
    dao.make_row.side_effect = lambda owner_id, category_id, role, seq: TagGroupCategoryData(
        group_id=owner_id, category_id=category_id, role=role, seq=seq)
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    result = await layout.pick_unsolved_category(guild, tag_group, dao)

    assert result is orphan
    u0.clone.assert_not_awaited()
    row = dao.commit.call_args.args[0]
    assert row.category_id == 300


@pytest.mark.asyncio
async def test_split_group_does_not_adopt_claimed_solved_category(monkeypatch):
    """Same ownership rule at split time: an existing 'round-1 — solved'
    category owned by another group's join row is not reused as this group's
    solved category."""
    tgc, _, _, _ = _patch_no_claims(monkeypatch)
    guild, primary, solved_cat, div, unsolved, solved = _split_fixtures()
    foreign_solved = MagicMock(id=999)
    foreign_solved.name = layout.solved_category_name("round-1")
    guild.categories = [primary, foreign_solved]
    tgc.get_by_category.side_effect = lambda cid: (
        TagGroupCategoryData(group_id=88, category_id=999, role="solved", seq=0)
        if cid == 999 else None)

    dao = MagicMock()
    dao.get_for_group.return_value = []
    dao.make_row.side_effect = lambda owner_id, category_id, role, seq: TagGroupCategoryData(
        group_id=owner_id, category_id=category_id, role=role, seq=seq)
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    await layout.split_group(guild, tag_group, dao)

    primary.clone.assert_awaited_once()               # fresh clone, no adoption
    written = {(r.role, r.category_id) for r in
               (c.args[0] for c in dao.commit.call_args_list)}
    assert ("solved", 200) in written                 # the clone (id 200)
    assert ("solved", 999) not in written             # never the foreign one


@pytest.mark.asyncio
async def test_pick_unsolved_reuses_primary_when_split_alone_frees_enough_room(monkeypatch):
    """A single (not yet split) category crossing the soft limit splits into
    unsolved/solved — it must NOT also allocate a numbered overflow category
    if moving the solved channels out already leaves the primary with room."""
    monkeypatch.setattr(layout, "CATEGORY_SOFT_LIMIT", 2)
    div = MagicMock(); div.name = SOLVE_DIVIDER; div.position = 3
    u1 = MagicMock(); u1.name = "u1"; u1.position = 0
    s1 = MagicMock(); s1.name = "s1"; s1.position = 4
    s2 = MagicMock(); s2.name = "s2"; s2.position = 5
    primary = MagicMock(id=100, position=5)
    primary.name = "round-1"
    primary.channels = [u1, div, s1, s2]   # 3 puzzles total -> crosses limit of 2
    primary.text_channels = primary.channels   # same list: all text, stays in sync
    solved_cat = MagicMock(id=200)
    solved_cat.channels = []
    solved_cat.edit = AsyncMock()
    primary.clone = AsyncMock(return_value=solved_cat)

    # MagicMocks don't simulate Discord's own effect of a category move/delete
    # on the category's live .channels list, so reproduce it: moving/deleting
    # a channel removes it from primary (and, for a move, appends it to its
    # new home) exactly as the real guild cache would after a successful edit.
    def _move_to_solved(ch):
        async def _do(*, category=None, **kw):
            primary.channels.remove(ch)
            solved_cat.channels.append(ch)
        return _do
    s1.edit = AsyncMock(side_effect=_move_to_solved(s1))
    s2.edit = AsyncMock(side_effect=_move_to_solved(s2))
    div.delete = AsyncMock(side_effect=lambda **kw: primary.channels.remove(div))

    guild = MagicMock(); guild.categories = [primary]
    dao = MagicMock()
    dao.get_for_group.side_effect = [
        [],  # pick_unsolved_category's initial not-split check
        [],  # split_group's fast-path already-split check
        [],  # split_group's TOCTOU re-check under the lock
        [TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0)],  # post-split
    ]
    dao.make_row.side_effect = lambda owner_id, category_id, role, seq: TagGroupCategoryData(
        group_id=owner_id, category_id=category_id, role=role, seq=seq)
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    result = await layout.pick_unsolved_category(guild, tag_group, dao)

    assert result is primary                      # reused, not a numbered overflow
    assert primary.channels == [u1]                # solved + divider left; unsolved untouched
    primary.clone.assert_awaited_once()            # only the solved-category clone from splitting


def test_group_id_for_category_uses_dao_row():
    dao = MagicMock()
    dao.get_by_category.return_value = TagGroupCategoryData(
        group_id=42, category_id=200, role="solved", seq=0)
    assert layout.group_id_for_category(200, dao) == 42


def test_group_id_for_category_none_when_unknown():
    dao = MagicMock()
    dao.get_by_category.return_value = None
    assert layout.group_id_for_category(999, dao) is None


def test_group_id_for_category_works_with_hunt_rows():
    """The duck-typed DAO contract must hold for BOTH join tables:
    HuntCategoryData stores its owner as hunt_id, but generic code reads
    row.group_id — the hunt row has to answer to it too."""
    from bot.store.hunt_category_data import HuntCategoryData
    dao = MagicMock()
    dao.get_by_category.return_value = HuntCategoryData(
        hunt_id=7, category_id=300, role="solved", seq=0)
    assert layout.group_id_for_category(300, dao) == 7


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
    channel.edit.assert_awaited_once_with(category=solved_cat, position=0)
    assert returned_source is source


@pytest.mark.asyncio
async def test_move_channel_to_solved_appends_after_existing_channels():
    """Regression: channel.edit(category=X) with no `position` keeps the
    channel's OLD absolute position number (discord.py only re-sorts when
    `position` is given) — it must always be requested explicitly, or a
    moved-in channel can land above an existing one (e.g. the round's own
    target puzzle) instead of after it."""
    target_puzzle = MagicMock(); target_puzzle.name = "target"; target_puzzle.position = 0
    other = MagicMock(); other.name = "other"; other.position = 1
    solved_cat = MagicMock(id=200, position=6)
    solved_cat.name = "round-1 — solved"
    solved_cat.channels = [target_puzzle, other]   # already there before the move
    solved_cat.edit = AsyncMock()
    guild = MagicMock(); guild.categories = [solved_cat]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(group_id=5, category_id=200, role="solved", seq=0),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")
    source = MagicMock(id=100)
    # A stale, unrelated LOW position from the channel's previous (unsolved)
    # category — if this leaked through unchanged, it would land above
    # target_puzzle (position 0) instead of after it.
    channel = MagicMock(position=0); channel.category = source; channel.edit = AsyncMock()

    await layout.move_channel_to_solved(guild, tag_group, dao, channel)

    channel.edit.assert_awaited_once_with(category=solved_cat, position=2)


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
async def test_move_channel_to_unsolved_appends_after_existing_channels():
    """Same explicit-position requirement as move_channel_to_solved: a stale
    absolute position from the channel's old category must not land it above
    an existing channel (e.g. the round's own target puzzle) in the new one."""
    target_puzzle = MagicMock(); target_puzzle.name = "target"; target_puzzle.position = 0
    unsolved_cat = MagicMock(id=100, position=5)
    unsolved_cat.name = "round-1"
    unsolved_cat.channels = [target_puzzle]
    unsolved_cat.edit = AsyncMock()
    guild = MagicMock(); guild.categories = [unsolved_cat]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(group_id=5, category_id=200, role="solved", seq=0),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")
    source = MagicMock(id=200)
    channel = MagicMock(position=0); channel.category = source; channel.edit = AsyncMock()

    await layout.move_channel_to_unsolved(guild, tag_group, dao, channel)

    channel.edit.assert_awaited_once_with(category=unsolved_cat, position=1)


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


@pytest.mark.asyncio
async def test_cleanup_rechecks_emptiness_under_the_lock():
    """A concurrent move can legally land a channel in an 'empty' overflow
    category between cleanup's emptiness check and its lock acquisition
    (_pick_or_allocate returns any row with room). Deleting then would drop
    that channel to the guild root — the emptiness check must be re-run
    inside layout_lock."""
    overflow = _cat(201, 7, n_channels=0)
    overflow.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [overflow]
    row = TagGroupCategoryData(id=9, group_id=5, category_id=201, role="solved", seq=1)
    dao = MagicMock(); dao.get_by_category.return_value = row
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    async with layout.layout_lock:
        task = asyncio.ensure_future(
            layout.cleanup_empty_overflow(guild, tag_group, dao, overflow))
        await asyncio.sleep(0)          # run cleanup up to the lock
        late = MagicMock(); late.name = "late-arrival"
        overflow.channels.append(late)  # concurrent move lands a channel
    await task

    overflow.delete.assert_not_awaited()
    dao.delete.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_rechecks_row_under_the_lock():
    """Symmetric TOCTOU on the row: a concurrent cleanup can delete the join
    row while this one waits for the lock — the row must be re-fetched inside
    layout_lock, and a vanished row means nothing to do."""
    overflow = _cat(201, 7, n_channels=0)
    overflow.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [overflow]
    row = TagGroupCategoryData(id=9, group_id=5, category_id=201, role="solved", seq=1)
    dao = MagicMock()
    dao.get_by_category.side_effect = [row, None]   # pre-lock hit, under-lock miss
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    async with layout.layout_lock:
        task = asyncio.ensure_future(
            layout.cleanup_empty_overflow(guild, tag_group, dao, overflow))
        await asyncio.sleep(0)
    await task

    overflow.delete.assert_not_awaited()
    dao.delete.assert_not_called()


@pytest.mark.asyncio
async def test_allocate_rechecks_category_ceiling_under_the_lock():
    """Two concurrent allocations at 49 guild categories must not both pass the
    MAX_CATEGORIES gate: the check has to run inside layout_lock so the loser
    raises the structured no_category_space error callers map to a friendly
    message, not a raw HTTPException from Discord's cap."""
    from bot.core.discord_channels import MAX_CATEGORIES
    u0 = _cat(100, 5, n_channels=layout.CATEGORY_SOFT_LIMIT)   # full
    u0.clone = AsyncMock()
    guild = MagicMock(); guild.categories = [u0]
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
    ]
    tag_group = MagicMock(id=5, category_id=100, name="Round 1")

    async with layout.layout_lock:
        task = asyncio.ensure_future(
            layout.pick_unsolved_category(guild, tag_group, dao))
        await asyncio.sleep(0)                      # run allocation up to the lock
        guild.categories = [u0] * MAX_CATEGORIES    # a rival filled the guild
    with pytest.raises(RuntimeError, match="no_category_space"):
        await task
    u0.clone.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_source_category_resolves_owning_table(monkeypatch):
    """A vacated source category can belong to EITHER join table (an unassigned
    puzzle leaving hunt overflow for a round category, or vice versa) — the
    cleanup must resolve the owning table itself rather than trust the
    destination's dao, which would silently never match."""
    import bot.store as store
    from bot.store.hunt_category_data import HuntCategoryData
    overflow = _cat(201, 7, n_channels=0)
    overflow.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [overflow]
    hunt_row = HuntCategoryData(id=9, hunt_id=7, category_id=201, role="solved", seq=1)
    tgc = MagicMock(); tgc.get_by_category.return_value = None
    hc = MagicMock(); hc.get_by_category.return_value = hunt_row
    monkeypatch.setattr(store, "TagGroupCategoryDb", tgc)
    monkeypatch.setattr(store, "HuntCategoryDb", hc)

    await layout.cleanup_source_category(guild, overflow)

    overflow.delete.assert_awaited_once()
    hc.delete.assert_called_once_with(9)


@pytest.mark.asyncio
async def test_cleanup_source_category_noop_when_untracked(monkeypatch):
    import bot.store as store
    plain = _cat(300, 9, n_channels=0)
    plain.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [plain]
    tgc = MagicMock(); tgc.get_by_category.return_value = None
    hc = MagicMock(); hc.get_by_category.return_value = None
    monkeypatch.setattr(store, "TagGroupCategoryDb", tgc)
    monkeypatch.setattr(store, "HuntCategoryDb", hc)

    await layout.cleanup_source_category(guild, plain)

    plain.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_channel_to_owner_legacy_destination_cleans_vacated_source(monkeypatch):
    """Moving a channel into a NOT-split owner (legacy divider layout) still
    vacates its previous category — if that source was an overflow category
    (of either table), it must be reclaimed just like the split-destination
    branch does."""
    other = MagicMock(); other.name = "general"; other.position = 3
    primary = MagicMock(id=500)
    primary.channels = [other]
    guild = MagicMock(); guild.categories = [primary]
    owner = MagicMock(id=5, category_id=500, name="General")
    dao = MagicMock(); dao.get_for_group.return_value = []
    source_cat = MagicMock(id=201)
    channel = MagicMock(); channel.category_id = 999; channel.category = source_cat
    channel.edit = AsyncMock()
    cleanup = AsyncMock()
    monkeypatch.setattr(layout, "cleanup_source_category", cleanup)

    result = await layout.move_channel_to_owner(guild, owner, dao, channel, False)

    assert result is True
    cleanup.assert_awaited_once_with(guild, source_cat)


# ----------------------------------------------------------------- consolidation

def _filled_cat(cid, pos, n, name="round-1"):
    """A category whose channels carry real positions and async edit/delete."""
    cat = _cat(cid, pos, n_channels=n, name=name)
    for i, ch in enumerate(cat.channels):
        ch.position = i
        ch.edit = AsyncMock()
    cat.text_channels = cat.channels
    cat.delete = AsyncMock()
    return cat


def _dao_with_rows(rows):
    """A dao over a mutable row list: get_for_group re-reads it, delete removes
    by row id — so consolidate's re-read-after-acting loop sees real state."""
    store = list(rows)
    dao = MagicMock()
    dao.get_for_group.side_effect = lambda gid: list(store)
    dao.delete.side_effect = lambda rid: store.remove(
        next(r for r in store if r.id == rid))
    return dao, store


@pytest.mark.asyncio
async def test_consolidate_folds_top_overflow_into_lower(monkeypatch):
    """Enough aggregate free space (with margin): the highest-seq overflow's
    channels move into the lower category with explicit positions, and the
    emptied category + its row are deleted. Nothing is ever renamed (loose
    seq — Discord's name bucket is 2 edits / 10 min per channel)."""
    monkeypatch.setattr(layout, "CATEGORY_SOFT_LIMIT", 8)
    u0 = _filled_cat(100, 5, n=2)
    u1 = _filled_cat(101, 6, n=2)          # movers=2 <= free(6) - margin(4)
    guild = MagicMock(); guild.categories = [u0, u1]
    dao, store = _dao_with_rows([
        TagGroupCategoryData(id=1, group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(id=2, group_id=5, category_id=101, role="unsolved", seq=1),
    ])

    await layout.consolidate(guild, 5, dao)

    m1, m2 = u1.channels
    m1.edit.assert_awaited_once_with(category=u0, position=2)
    m2.edit.assert_awaited_once_with(category=u0, position=3)
    u1.delete.assert_awaited_once()
    assert [r.id for r in store] == [1]    # only the seq=0 row survives
    u0.edit.assert_not_called()            # no rename, no reposition of survivors


@pytest.mark.asyncio
async def test_consolidate_respects_margin(monkeypatch):
    """movers > free - CONSOLIDATE_MARGIN: folding now would sit too close to
    the split threshold and oscillate — leave the overflow alone."""
    monkeypatch.setattr(layout, "CATEGORY_SOFT_LIMIT", 8)
    u0 = _filled_cat(100, 5, n=2)          # free = 6
    u1 = _filled_cat(101, 6, n=3)          # movers=3 > 6 - 4
    guild = MagicMock(); guild.categories = [u0, u1]
    dao, store = _dao_with_rows([
        TagGroupCategoryData(id=1, group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(id=2, group_id=5, category_id=101, role="unsolved", seq=1),
    ])

    await layout.consolidate(guild, 5, dao)

    for ch in u1.channels:
        ch.edit.assert_not_awaited()
    u1.delete.assert_not_awaited()
    assert len(store) == 2


@pytest.mark.asyncio
async def test_consolidate_always_reclaims_empty_overflow(monkeypatch):
    """An EMPTY seq>=1 category is reclaimed unconditionally — no margin gate —
    matching cleanup_empty_overflow's shipped semantics."""
    monkeypatch.setattr(layout, "CATEGORY_SOFT_LIMIT", 8)
    u0 = _filled_cat(100, 5, n=7)          # nearly full: free(1) < margin
    u1 = _filled_cat(101, 6, n=0)          # empty overflow
    guild = MagicMock(); guild.categories = [u0, u1]
    dao, store = _dao_with_rows([
        TagGroupCategoryData(id=1, group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(id=2, group_id=5, category_id=101, role="unsolved", seq=1),
    ])

    await layout.consolidate(guild, 5, dao)

    u1.delete.assert_awaited_once()
    assert [r.id for r in store] == [1]


@pytest.mark.asyncio
async def test_consolidate_reaps_dangling_rows():
    """A row whose category no longer resolves (out-of-band delete, crashed
    cleanup) is dropped — previously these accumulated forever."""
    u0 = _filled_cat(100, 5, n=2)
    guild = MagicMock(); guild.categories = [u0]      # 101 is GONE
    dao, store = _dao_with_rows([
        TagGroupCategoryData(id=1, group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(id=2, group_id=5, category_id=101, role="unsolved", seq=1),
    ])

    await layout.consolidate(guild, 5, dao)

    assert [r.id for r in store] == [1]


@pytest.mark.asyncio
async def test_consolidate_never_touches_seq0(monkeypatch):
    """seq=0 rows are structural (category_id home, clone template, unsuffixed
    name) — consolidation must only ever delete seq>=1 rows/categories."""
    monkeypatch.setattr(layout, "CATEGORY_SOFT_LIMIT", 8)
    u0 = _filled_cat(100, 5, n=0)          # empty, but PRIMARY
    s0 = _filled_cat(200, 6, n=0)          # empty, but solved seq=0
    guild = MagicMock(); guild.categories = [u0, s0]
    dao, store = _dao_with_rows([
        TagGroupCategoryData(id=1, group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(id=2, group_id=5, category_id=200, role="solved", seq=0),
    ])
    monkeypatch.setattr(layout, "_maybe_unsplit", AsyncMock(), raising=False)

    await layout.consolidate(guild, 5, dao)

    u0.delete.assert_not_awaited()
    s0.delete.assert_not_awaited()
    assert len(store) == 2


@pytest.mark.asyncio
async def test_consolidate_unsolved_before_solved(monkeypatch):
    """Unsolved-side folds run before solved-side ones (unsolved is the side
    normal play keeps changing; do it first so solved work isn't wasted)."""
    monkeypatch.setattr(layout, "CATEGORY_SOFT_LIMIT", 8)
    order = []
    u0 = _filled_cat(100, 5, n=0); u1 = _filled_cat(101, 6, n=0)
    s0 = _filled_cat(200, 7, n=0); s1 = _filled_cat(201, 8, n=0)
    u1.delete = AsyncMock(side_effect=lambda **kw: order.append("unsolved"))
    s1.delete = AsyncMock(side_effect=lambda **kw: order.append("solved"))
    guild = MagicMock(); guild.categories = [u0, u1, s0, s1]
    dao, store = _dao_with_rows([
        TagGroupCategoryData(id=1, group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(id=2, group_id=5, category_id=101, role="unsolved", seq=1),
        TagGroupCategoryData(id=3, group_id=5, category_id=200, role="solved", seq=0),
        TagGroupCategoryData(id=4, group_id=5, category_id=201, role="solved", seq=1),
    ])
    # This test asserts fold ordering only; the post-fold state is a valid
    # un-split candidate, which has its own tests below.
    monkeypatch.setattr(layout, "_maybe_unsplit", AsyncMock(), raising=False)

    await layout.consolidate(guild, 5, dao)

    assert order == ["unsolved", "solved"]


@pytest.mark.asyncio
async def test_consolidate_tracks_capacity_across_moves_in_one_run(monkeypatch):
    """The guild cache does NOT reflect this run's own moves (it updates on
    gateway events) — consolidate must track what it already added to each
    target, or two successive folds could overfill one category."""
    monkeypatch.setattr(layout, "CATEGORY_SOFT_LIMIT", 8)
    u0 = _filled_cat(100, 5, n=2)          # cached free = 6
    u1 = _filled_cat(101, 6, n=2)
    u2 = _filled_cat(102, 7, n=2)
    guild = MagicMock(); guild.categories = [u0, u1, u2]
    dao, store = _dao_with_rows([
        TagGroupCategoryData(id=1, group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(id=2, group_id=5, category_id=101, role="unsolved", seq=1),
        TagGroupCategoryData(id=3, group_id=5, category_id=102, role="unsolved", seq=2),
    ])

    await layout.consolidate(guild, 5, dao)

    # Fold #1 (seq=2 -> lowers) fits: movers=2 <= free(6+6=12 vs 8-cap... per
    # target) - margin. After it, u0 effectively holds 4: fold #2 (seq=1) has
    # free = (8-4)+(gone) = 4, movers=2 <= 4-4 is FALSE -> must NOT fold.
    u2.delete.assert_awaited_once()
    u1.delete.assert_not_awaited()
    assert {r.id for r in store} == {1, 2}
    # And the fold-#1 movers landed after u0's cached channels at 2,3 —
    # the second mover's position accounts for the first (cache won't).
    m1, m2 = u2.channels
    m1.edit.assert_awaited_once_with(category=u0, position=2)
    m2.edit.assert_awaited_once_with(category=u0, position=3)


# ----------------------------------------------------------------- un-split

def _unsplit_fixture(monkeypatch, n_unsolved=3, n_solved=2, divider_reaches_cache=True):
    """Owner split into exactly u0/s0 (seq=0 each). get_or_create_channel is
    patched: it 'creates' the divider and (by default) makes it appear in
    u0's cached channel list, the way the gateway event would."""
    monkeypatch.setattr(layout, "CATEGORY_SOFT_LIMIT", 10)
    u0 = _filled_cat(100, 5, n=n_unsolved)
    s0 = _filled_cat(200, 6, n=n_solved, name="round-1 — solved")
    guild = MagicMock(); guild.categories = [u0, s0]
    dao, store = _dao_with_rows([
        TagGroupCategoryData(id=1, group_id=5, category_id=100, role="unsolved", seq=0),
        TagGroupCategoryData(id=2, group_id=5, category_id=200, role="solved", seq=0),
    ])
    dao.delete_for_group.side_effect = lambda gid: store.clear()

    divider = MagicMock(); divider.name = SOLVE_DIVIDER
    divider.position = n_unsolved          # lands after the unsolved block

    async def _fake_get_or_create(guild, category, channel_name, channel_type, **kw):
        if divider_reaches_cache:
            category.channels.append(divider)
        return divider, True

    goc = AsyncMock(side_effect=_fake_get_or_create)
    monkeypatch.setattr(layout, "get_or_create_channel", goc)
    # Don't really sleep while polling for the cache in the timeout test.
    monkeypatch.setattr(layout, "_DIVIDER_WAIT_TRIES", 3)
    monkeypatch.setattr(layout, "_DIVIDER_WAIT_DELAY", 0)
    return guild, u0, s0, dao, store, divider, goc


@pytest.mark.asyncio
async def test_unsplit_merges_solved_back_under_divider(monkeypatch):
    """Both sides at seq=0 and small enough: recreate the divider at the bottom
    of the unsolved category, move the solved channels below it with explicit
    positions, delete the solved category, and delete the rows LAST."""
    guild, u0, s0, dao, store, divider, goc = _unsplit_fixture(monkeypatch)
    solved_channels = list(s0.channels)

    await layout.consolidate(guild, 5, dao)

    goc.assert_awaited_once_with(
        guild=guild, category=u0, channel_name=SOLVE_DIVIDER,
        channel_type="text", position=500)
    # Movers land after the divider (position 3), in their original order.
    solved_channels[0].edit.assert_awaited_once_with(category=u0, position=4)
    solved_channels[1].edit.assert_awaited_once_with(category=u0, position=5)
    s0.delete.assert_awaited_once()
    dao.delete_for_group.assert_called_once_with(5)
    assert store == []                     # owner is single-category again


@pytest.mark.asyncio
async def test_unsplit_blocked_when_total_exceeds_margin(monkeypatch):
    """unsolved + solved + divider must fit under SOFT_LIMIT - margin, or the
    next few creates would just re-split — leave the split alone."""
    guild, u0, s0, dao, store, divider, goc = _unsplit_fixture(
        monkeypatch, n_unsolved=4, n_solved=2)   # 4+2+1=7 > 10-4

    await layout.consolidate(guild, 5, dao)

    goc.assert_not_awaited()
    s0.delete.assert_not_awaited()
    assert len(store) == 2


@pytest.mark.asyncio
async def test_unsplit_blocked_while_overflow_rows_remain(monkeypatch):
    """Un-split only from the fully-folded position: one row per role."""
    guild, u0, s0, dao, store, divider, goc = _unsplit_fixture(
        monkeypatch, n_unsolved=1, n_solved=1)
    u1 = _filled_cat(101, 7, n=6)          # margin-blocked: 6 > free(9) - 4
    guild.categories.append(u1)
    store.append(TagGroupCategoryData(
        id=3, group_id=5, category_id=101, role="unsolved", seq=1))

    await layout.consolidate(guild, 5, dao)

    goc.assert_not_awaited()
    s0.delete.assert_not_awaited()
    assert len(store) == 3


@pytest.mark.asyncio
async def test_unsplit_aborts_when_divider_never_reaches_cache(monkeypatch):
    """Moves compute positions from the guild cache; if the created divider
    never shows up there, a bulk renumber could land solved channels ABOVE it.
    Abort instead — split state is kept (an extra divider in a split category
    is inert) and the next trigger retries."""
    guild, u0, s0, dao, store, divider, goc = _unsplit_fixture(
        monkeypatch, divider_reaches_cache=False)

    await layout.consolidate(guild, 5, dao)

    goc.assert_awaited_once()
    for ch in s0.channels:
        ch.edit.assert_not_awaited()
    s0.delete.assert_not_awaited()
    assert len(store) == 2                 # rows survive -> resumable


@pytest.mark.asyncio
async def test_unsplit_completes_when_solved_category_gone(monkeypatch):
    """A dangling solved seq=0 row (category deleted out-of-band) still
    finishes the un-split: divider recreated, rows dropped, nothing to move."""
    guild, u0, s0, dao, store, divider, goc = _unsplit_fixture(monkeypatch)
    guild.categories.remove(s0)            # solved category vanished

    await layout.consolidate(guild, 5, dao)

    goc.assert_awaited_once()
    s0.delete.assert_not_awaited()
    dao.delete_for_group.assert_called_once_with(5)
    assert store == []


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


# ----------------------------------------------------------------- move_channel_to_owner
#
# move_channel_to_owner(guild, owner, dao, channel, solved) is generic over any
# owner (tag_group OR hunt) — used when a puzzle's channel must move to a
# DIFFERENT owner entirely (e.g. delete_round's keep_puzzles relocation, either
# into a sibling group or the hunt's own general container), as opposed to
# move_channel_to_solved/_unsolved's same-owner solve/unsolve reposition.

@pytest.mark.asyncio
async def test_move_channel_to_owner_no_category_returns_false():
    guild = MagicMock()
    guild.categories = []
    owner = MagicMock(id=5, category_id=0, name="General")
    dao = MagicMock()
    dao.get_for_group.return_value = []
    channel = MagicMock()
    channel.edit = AsyncMock()

    assert await layout.move_channel_to_owner(guild, owner, dao, channel, False) is False
    channel.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_channel_to_owner_already_in_place_returns_true_without_edit():
    primary = MagicMock(id=100)
    primary.channels = []
    guild = MagicMock(); guild.categories = [primary]
    owner = MagicMock(id=5, category_id=100, name="General")
    dao = MagicMock(); dao.get_for_group.return_value = []
    channel = MagicMock(); channel.category_id = 100
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_owner(guild, owner, dao, channel, False)

    assert result is True
    channel.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_channel_to_owner_not_split_unsolved_positions_above_divider():
    div = MagicMock(); div.name = SOLVE_DIVIDER; div.position = 5
    other = MagicMock(); other.name = "general"; other.position = 0
    primary = MagicMock(id=500)
    primary.channels = [div, other]
    guild = MagicMock(); guild.categories = [primary]
    owner = MagicMock(id=5, category_id=500, name="General")
    dao = MagicMock(); dao.get_for_group.return_value = []
    channel = MagicMock(); channel.category_id = 999
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_owner(guild, owner, dao, channel, False)

    assert result is True
    channel.edit.assert_awaited_once_with(category=primary, position=5)


@pytest.mark.asyncio
async def test_move_channel_to_owner_not_split_solved_positions_after_divider():
    div = MagicMock(); div.name = SOLVE_DIVIDER; div.position = 5
    other = MagicMock(); other.name = "general"; other.position = 0
    primary = MagicMock(id=500)
    primary.channels = [div, other]
    guild = MagicMock(); guild.categories = [primary]
    owner = MagicMock(id=5, category_id=500, name="General")
    dao = MagicMock(); dao.get_for_group.return_value = []
    channel = MagicMock(); channel.category_id = 999
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_owner(guild, owner, dao, channel, True)

    assert result is True
    channel.edit.assert_awaited_once_with(category=primary, position=6)


@pytest.mark.asyncio
async def test_move_channel_to_owner_not_split_no_divider_appends_at_end():
    other = MagicMock(); other.name = "general"; other.position = 3
    primary = MagicMock(id=500)
    primary.channels = [other]           # no SOLVE_DIVIDER channel present
    guild = MagicMock(); guild.categories = [primary]
    owner = MagicMock(id=5, category_id=500, name="General")
    dao = MagicMock(); dao.get_for_group.return_value = []
    channel = MagicMock(); channel.category_id = 999
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_owner(guild, owner, dao, channel, False)

    assert result is True
    channel.edit.assert_awaited_once_with(category=primary, position=4)


@pytest.mark.asyncio
async def test_move_channel_to_owner_not_split_edit_failure_returns_false():
    primary = MagicMock(id=500)
    primary.channels = []
    guild = MagicMock(); guild.categories = [primary]
    owner = MagicMock(id=5, category_id=500, name="General")
    dao = MagicMock(); dao.get_for_group.return_value = []
    channel = MagicMock(); channel.category_id = 999
    channel.edit = AsyncMock(side_effect=RuntimeError("boom"))

    result = await layout.move_channel_to_owner(guild, owner, dao, channel, False)

    assert result is False


@pytest.mark.asyncio
async def test_move_channel_to_owner_over_soft_limit_splits_then_places(monkeypatch):
    """A not-yet-split owner whose primary is already at the soft limit gets
    proactively split (mirroring pick_unsolved_category's own proactive
    split) instead of being force-appended past the limit."""
    primary = MagicMock(id=100)
    primary.channels = [MagicMock() for _ in range(layout.CATEGORY_SOFT_LIMIT)]  # full
    guild = MagicMock(); guild.categories = [primary]
    owner = MagicMock(id=5, category_id=100, name="General")
    dao = MagicMock()
    split_rows = [TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0)]
    dao.get_for_group.side_effect = [
        [],           # not split (initial check)
        split_rows,   # post-split progress check
        split_rows,   # the retry's own is_split check
    ]
    split_mock = AsyncMock()
    monkeypatch.setattr(layout, "split_group", split_mock)
    unsolved_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(layout, "move_channel_to_unsolved", unsolved_mock)
    channel = MagicMock(); channel.category_id = 999

    result = await layout.move_channel_to_owner(guild, owner, dao, channel, False)

    split_mock.assert_awaited_once_with(guild, owner, dao)
    unsolved_mock.assert_awaited_once_with(guild, owner, dao, channel)
    assert result is True


@pytest.mark.asyncio
async def test_move_channel_to_owner_in_place_home_full_skips_without_recursion():
    """An in-place-promoted meta's category_id is a TEXT CHANNEL; _home_category
    falls back to its parent category, but that host category is NOT ours to
    split (split_group resolves via _category_by_id, silently no-ops, and the
    old code recursed forever on identical state). When the host is full the
    move must simply be skipped."""
    parent = _cat(100, 5, n_channels=layout.CATEGORY_SOFT_LIMIT)   # full host
    home_channel = MagicMock(id=555)
    home_channel.category = parent
    guild = MagicMock()
    guild.categories = [parent]                  # 555 is NOT a category
    guild.get_channel.return_value = home_channel
    owner = MagicMock(id=5, category_id=555, name="Meta")
    dao = MagicMock(); dao.get_for_group.return_value = []
    channel = MagicMock(); channel.category_id = 999
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_owner(guild, owner, dao, channel, False)

    assert result is False
    channel.edit.assert_not_awaited()
    dao.commit.assert_not_called()               # never tried to split the host


@pytest.mark.asyncio
async def test_move_channel_to_owner_in_place_home_with_room_still_places():
    """Below the limit the in-place fallback keeps working: the feeder lands in
    the meta's host category."""
    parent = _cat(100, 5, n_channels=3)
    for i, ch in enumerate(parent.channels):
        ch.position = i
    home_channel = MagicMock(id=555)
    home_channel.category = parent
    guild = MagicMock()
    guild.categories = [parent]
    guild.get_channel.return_value = home_channel
    owner = MagicMock(id=5, category_id=555, name="Meta")
    dao = MagicMock(); dao.get_for_group.return_value = []
    channel = MagicMock(); channel.category_id = 999
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_owner(guild, owner, dao, channel, False)

    assert result is True
    channel.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_move_channel_to_owner_returns_false_when_split_makes_no_progress(monkeypatch):
    """If split_group runs but the owner still is not split afterwards, the
    retry recursion would re-enter with byte-identical state forever — it must
    bail out with False instead."""
    primary = MagicMock(id=100)
    primary.channels = [MagicMock() for _ in range(layout.CATEGORY_SOFT_LIMIT)]  # full
    guild = MagicMock(); guild.categories = [primary]
    owner = MagicMock(id=5, category_id=100, name="General")
    dao = MagicMock(); dao.get_for_group.return_value = []   # never becomes split
    monkeypatch.setattr(layout, "split_group", AsyncMock())  # silently no-ops
    channel = MagicMock(); channel.category_id = 999
    channel.edit = AsyncMock()

    result = await layout.move_channel_to_owner(guild, owner, dao, channel, False)

    assert result is False
    channel.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_channel_to_owner_split_failure_returns_false(monkeypatch):
    """A Discord-side failure while proactively splitting (e.g. clone raises)
    must be isolated as a per-puzzle failure, not propagate out of
    delete_round and abort the whole run."""
    primary = MagicMock(id=100)
    primary.channels = [MagicMock() for _ in range(layout.CATEGORY_SOFT_LIMIT)]  # full
    guild = MagicMock(); guild.categories = [primary]
    owner = MagicMock(id=5, category_id=100, name="General")
    dao = MagicMock(); dao.get_for_group.return_value = []
    monkeypatch.setattr(layout, "split_group", AsyncMock(side_effect=RuntimeError("boom")))
    channel = MagicMock(); channel.category_id = 999

    result = await layout.move_channel_to_owner(guild, owner, dao, channel, False)

    assert result is False


@pytest.mark.asyncio
async def test_move_channel_to_owner_split_state_delegates_and_cleans_overflow(monkeypatch):
    guild = MagicMock()
    owner = MagicMock(id=5, category_id=100, name="General")
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
    ]
    channel = MagicMock()
    source_cat = MagicMock()
    solved_mock = AsyncMock(return_value=source_cat)
    monkeypatch.setattr(layout, "move_channel_to_solved", solved_mock)
    # Source cleanup goes through the table-agnostic resolver: the vacated
    # category may belong to the OTHER join table (cross-owner relocation).
    cleanup_mock = AsyncMock()
    monkeypatch.setattr(layout, "cleanup_source_category", cleanup_mock)

    result = await layout.move_channel_to_owner(guild, owner, dao, channel, True)

    solved_mock.assert_awaited_once_with(guild, owner, dao, channel)
    cleanup_mock.assert_awaited_once_with(guild, source_cat)
    assert result is True


@pytest.mark.asyncio
async def test_move_channel_to_owner_split_state_exception_returns_false(monkeypatch):
    guild = MagicMock()
    owner = MagicMock(id=5, category_id=100, name="General")
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
    ]
    channel = MagicMock()
    monkeypatch.setattr(
        layout, "move_channel_to_unsolved", AsyncMock(side_effect=RuntimeError("boom")))

    result = await layout.move_channel_to_owner(guild, owner, dao, channel, False)

    assert result is False


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
