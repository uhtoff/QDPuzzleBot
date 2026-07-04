"""delete_round's keep_puzzles=True (default) path: deletes only the round's
own target puzzle (or, for a target-less Group round, its dedicated chat
channel), relocates every other member puzzle's Discord channel into the
hunt's general category instead of deleting it, and leaves their DB
rows/sheets untouched. Forum-layout rounds ignore keep_puzzles entirely (no
"move to general category" equivalent for threads) and always full-delete.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.discord_channels import MAX_CHANNELS_PER_CATEGORY, SOLVE_DIVIDER
from tests.conftest import make_hunt, make_puzzle, make_round


def _svc():
    from bot.core.puzzle_service import PuzzleService
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


def _general_category(cat_id=500, divider_pos=1):
    div = MagicMock(); div.name = SOLVE_DIVIDER; div.position = divider_pos
    general_ch = MagicMock(); general_ch.name = "general"; general_ch.position = 0
    cat = MagicMock(id=cat_id)
    cat.channels = [general_ch, div]
    return cat


@pytest.mark.asyncio
async def test_keep_puzzles_deletes_target_and_relocates_feeder():
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=1)

    target = make_puzzle(id=1, is_target=True, channel_id=901, tags=[5])
    feeder = make_puzzle(id=2, channel_id=902, tags=[5], solved=False)

    target_ch = MagicMock(id=901); target_ch.delete = AsyncMock()
    feeder_ch = MagicMock(id=902); feeder_ch.category_id = 100; feeder_ch.edit = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [target_ch, feeder_ch]
    round_cat.delete = AsyncMock()
    general_cat = _general_category()

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {901: target_ch, 902: feeder_ch}.get(cid))

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [target, feeder]
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []

        failed = await svc.delete_round(guild, hunt, tag_group)

    pdb.delete.assert_called_once_with(1)                                       # only the target's DB row
    feeder_ch.edit.assert_awaited_once_with(category=general_cat, position=1)    # unsolved: just above divider(1)
    target_ch.delete.assert_awaited_once()
    assert failed == []


@pytest.mark.asyncio
async def test_keep_puzzles_prefers_sibling_group_category_over_general():
    """A kept puzzle also tagged to another group lands in that group's
    category instead of the hunt's general category."""
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)

    feeder = make_puzzle(id=2, name="Feeder", channel_id=902, tags=[5, 7], solved=False)
    feeder_ch = MagicMock(id=902)
    feeder_ch.category_id = 100
    feeder_ch.category = MagicMock(id=100)
    feeder_ch.edit = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [feeder_ch]
    round_cat.text_channels = []
    round_cat.delete = AsyncMock()
    general_cat = MagicMock(id=500)
    general_cat.name = "General"
    general_cat.channels = []
    sibling_cat = MagicMock(id=200)
    sibling_cat.channels = []

    guild = MagicMock()
    guild.categories = [round_cat, general_cat, sibling_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {902: feeder_ch}.get(cid))

    sibling_group = MagicMock(id=7, category_id=200)

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [feeder]
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []
        tgdb.get_by_attr.return_value = sibling_group

        failed = await svc.delete_round(guild, hunt, tag_group)

    tgdb.get_by_attr.assert_called_once_with(id=7)     # tag_group.id (5) excluded
    feeder_ch.edit.assert_awaited_once_with(category=sibling_cat, position=0)
    assert failed == []


@pytest.mark.asyncio
async def test_keep_puzzles_falls_back_to_general_when_sibling_group_has_no_category():
    """If a kept puzzle's other group has no usable category (e.g. forum-home,
    or the group row is otherwise unresolvable), it still falls back to the
    hunt's general category rather than being reported as a failure."""
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)

    feeder = make_puzzle(id=2, name="Feeder", channel_id=902, tags=[5, 7], solved=False)
    feeder_ch = MagicMock(id=902)
    feeder_ch.category_id = 100
    feeder_ch.category = MagicMock(id=100)
    feeder_ch.edit = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [feeder_ch]
    round_cat.text_channels = []
    round_cat.delete = AsyncMock()
    general_cat = _general_category()

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {902: feeder_ch}.get(cid))

    sibling_group = MagicMock(id=7, category_id=None)   # unresolvable home category

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [feeder]
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []
        tgdb.get_by_attr.return_value = sibling_group

        failed = await svc.delete_round(guild, hunt, tag_group)

    feeder_ch.edit.assert_awaited_once_with(category=general_cat, position=1)
    assert failed == []


@pytest.mark.asyncio
async def test_keep_puzzles_relocation_failure_reported():
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)

    feeder = make_puzzle(id=2, name="Feeder", channel_id=902, tags=[5])
    feeder_ch = MagicMock(id=902); feeder_ch.category_id = 100; feeder_ch.edit = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [feeder_ch]
    round_cat.text_channels = []             # no dedicated chat channel to find
    round_cat.delete = AsyncMock()
    general_cat = MagicMock(id=500)
    general_cat.name = "General"
    general_cat.channels = [MagicMock() for _ in range(MAX_CHANNELS_PER_CATEGORY)]  # full -> split attempted
    # Discord-side failure while splitting (e.g. category-create rejected) — genuine failure.
    general_cat.clone = AsyncMock(side_effect=RuntimeError("no space"))

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {902: feeder_ch}.get(cid))

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [feeder]
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []
        tgdb.get_by_attr.return_value = None

        failed = await svc.delete_round(guild, hunt, tag_group)

    feeder_ch.edit.assert_not_awaited()
    feeder_ch.delete.assert_not_called()      # never destroyed — just left where it is
    assert failed == ["Feeder"]
    pdb.delete.assert_not_called()            # feeder's DB row untouched despite the relocation failure


@pytest.mark.asyncio
async def test_keep_puzzles_group_type_deletes_chat_channel_keeps_feeder():
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)
    tag_group.name = "Loose Ends"

    feeder = make_puzzle(id=2, channel_id=902, tags=[5])
    feeder_ch = MagicMock(id=902); feeder_ch.category_id = 100; feeder_ch.edit = AsyncMock()
    chat_ch = MagicMock(id=800); chat_ch.name = "loose-ends"; chat_ch.delete = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [feeder_ch, chat_ch]
    round_cat.text_channels = [chat_ch]
    round_cat.delete = AsyncMock()
    general_cat = _general_category()

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {902: feeder_ch, 800: chat_ch}.get(cid))

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [feeder]
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []

        failed = await svc.delete_round(guild, hunt, tag_group)

    chat_ch.delete.assert_awaited_once()
    feeder_ch.edit.assert_awaited_once_with(category=general_cat, position=1)
    pdb.delete.assert_not_called()
    assert failed == []


@pytest.mark.asyncio
async def test_forum_layout_ignores_keep_puzzles_full_deletes():
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="forum")
    tag_group = make_round(id=5, category_id=777, target_puzzle_id=0)

    p1 = make_puzzle(id=1, channel_id=901, tags=[5])
    p1_ch = MagicMock(id=901); p1_ch.delete = AsyncMock()
    forum_ch = MagicMock(id=777); forum_ch.delete = AsyncMock()

    guild = MagicMock()
    guild.categories = []          # forum channel is NOT a category
    guild.get_channel = MagicMock(side_effect=lambda cid: {901: p1_ch, 777: forum_ch}.get(cid))

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [p1]

        failed = await svc.delete_round(guild, hunt, tag_group, keep_puzzles=True)

    pdb.delete.assert_called_once_with(1)     # keep_puzzles=True ignored for forum — full delete
    forum_ch.delete.assert_awaited_once()
    assert failed == []


@pytest.mark.asyncio
async def test_keep_puzzles_split_state_protects_relocation_failure_from_blanket_delete():
    """Integration coverage for the keep_channel_ids wiring: a puzzle whose
    channel fails to relocate must survive the real (non-mocked)
    delete_group_categories blanket per-category delete, while an
    unprotected channel in the same category is still swept."""
    from bot.store.tag_group_category_data import TagGroupCategoryData

    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)

    feeder = make_puzzle(id=2, name="Feeder", channel_id=902, tags=[5])
    feeder_ch = MagicMock(id=902); feeder_ch.category_id = 100; feeder_ch.edit = AsyncMock()
    feeder_ch.delete = AsyncMock()
    stray_ch = MagicMock(id=903); stray_ch.delete = AsyncMock()   # unprotected leftover channel

    round_cat = MagicMock(id=100)
    round_cat.channels = [feeder_ch, stray_ch]
    round_cat.text_channels = []          # no dedicated chat channel
    round_cat.delete = AsyncMock()
    general_cat = MagicMock(id=500)
    general_cat.name = "General"
    general_cat.channels = [MagicMock() for _ in range(MAX_CHANNELS_PER_CATEGORY)]  # full -> split attempted
    # Discord-side failure while splitting (e.g. category-create rejected) — genuine failure.
    general_cat.clone = AsyncMock(side_effect=RuntimeError("no space"))

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {902: feeder_ch}.get(cid))

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb:
        pdb.get_all_from_tag_group.return_value = [feeder]
        tgcdb.get_for_group.return_value = [
            TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
        ]
        hcdb.get_for_group.return_value = []
        tgdb.get_by_attr.return_value = None

        failed = await svc.delete_round(guild, hunt, tag_group)

    feeder_ch.edit.assert_not_awaited()
    feeder_ch.delete.assert_not_called()      # protected via keep_channel_ids
    stray_ch.delete.assert_awaited_once()     # unprotected channel still swept
    round_cat.delete.assert_awaited_once()    # category itself still torn down
    tgcdb.delete_for_group.assert_called_once_with(5)
    pdb.delete.assert_not_called()            # feeder's DB row untouched
    assert failed == ["Feeder"]


@pytest.mark.asyncio
async def test_deletes_categories_before_deleting_group_row():
    """tag_group_category rows FK-cascade off the tag_group row (migration 022),
    so TagGroupJsonDb.delete(tag_group.id) must run AFTER
    category_layout.delete_group_categories reads them — deleting the row
    first would silently wipe the very rows it needs, making it fall back to
    (and only tear down) the legacy single-category path and orphan the
    solved/overflow categories."""
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)

    round_cat = MagicMock(id=100)
    round_cat.channels = []
    round_cat.text_channels = []
    round_cat.delete = AsyncMock()

    guild = MagicMock()
    guild.categories = [round_cat]
    guild.get_channel = MagicMock(return_value=None)

    manager = MagicMock()
    delete_group_cats = AsyncMock(return_value=True)
    manager.attach_mock(delete_group_cats, "delete_group_categories")

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               delete_group_cats):
        manager.attach_mock(tgdb.delete, "tag_group_delete")
        pdb.get_all_from_tag_group.return_value = []
        tgcdb.get_for_group.return_value = []

        await svc.delete_round(guild, hunt, tag_group)

    call_names = [c[0] for c in manager.mock_calls]
    assert call_names.index("delete_group_categories") < call_names.index("tag_group_delete")


@pytest.mark.asyncio
async def test_full_delete_protects_foreign_target_puzzle():
    """keep_puzzles=False deletes every ordinary member puzzle, but a member
    puzzle that is itself a DIFFERENT tag group's target (a nested meta whose
    target puzzle also feeds this round) must be relocated, not deleted —
    deleting it would orphan the nested group's target_puzzle_id (review #30)."""
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)

    ordinary = make_puzzle(id=2, name="Ordinary", channel_id=902, tags=[5])
    nested_meta_target = make_puzzle(
        id=3, name="Nested Meta", is_target=True, channel_id=903, tags=[5])

    ordinary_ch = MagicMock(id=902); ordinary_ch.delete = AsyncMock()
    nested_ch = MagicMock(id=903); nested_ch.category_id = 100; nested_ch.edit = AsyncMock(); nested_ch.delete = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [ordinary_ch, nested_ch]
    round_cat.text_channels = []
    round_cat.delete = AsyncMock()
    general_cat = _general_category()

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(
        side_effect=lambda cid: {902: ordinary_ch, 903: nested_ch}.get(cid))

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [ordinary, nested_meta_target]
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []

        failed = await svc.delete_round(guild, hunt, tag_group, keep_puzzles=False)

    pdb.delete.assert_called_once_with(2)                                        # only the ordinary puzzle's DB row
    ordinary_ch.delete.assert_awaited_once()                                     # ordinary puzzle: deleted
    nested_ch.edit.assert_awaited_once_with(category=general_cat, position=1)    # nested meta target: relocated
    nested_ch.delete.assert_not_called()
    assert failed == []


@pytest.mark.asyncio
async def test_keep_puzzles_empty_target_less_round_still_deletes_chat_channel():
    """An empty target-less Group round (no member puzzles) in keep_puzzles=True
    mode must still have its dedicated chat channel deleted — this must not
    regress when to_keep is empty (regression guard for #30 fix)."""
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)
    tag_group.name = "Loose Ends"

    chat_ch = MagicMock(id=800); chat_ch.name = "loose-ends"; chat_ch.delete = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [chat_ch]
    round_cat.text_channels = [chat_ch]
    round_cat.delete = AsyncMock()

    guild = MagicMock()
    guild.categories = [round_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {800: chat_ch}.get(cid))

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = []
        tgcdb.get_for_group.return_value = []

        failed = await svc.delete_round(guild, hunt, tag_group, keep_puzzles=True)

    chat_ch.delete.assert_awaited_once()
    assert failed == []


@pytest.mark.asyncio
async def test_full_delete_forum_mode_protects_foreign_target_puzzle():
    """Forum-layout rounds always full-delete regardless of keep_puzzles (no
    relocation story for threads), but must still protect a member puzzle
    that is a DIFFERENT tag group's target — deleting it would orphan that
    nested group's target_puzzle_id, same as the non-forum full-delete path
    (review #30). Since a forum thread has no relocation equivalent, the
    protected puzzle's thread is simply left alone: not deleted, not moved."""
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="forum")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)

    nested_meta_target = make_puzzle(
        id=3, name="Nested Meta", is_target=True, channel_id=903, tags=[5])

    forum_ch = MagicMock(id=100); forum_ch.delete = AsyncMock()
    nested_ch = MagicMock(id=903); nested_ch.edit = AsyncMock(); nested_ch.delete = AsyncMock()
    guild = MagicMock()
    guild.get_channel = MagicMock(side_effect=lambda cid: {100: forum_ch, 903: nested_ch}.get(cid))

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch.object(svc, "_safe_delete_channel", AsyncMock(return_value=True)) as safe_delete:
        pdb.get_all_from_tag_group.return_value = [nested_meta_target]

        failed = await svc.delete_round(guild, hunt, tag_group, keep_puzzles=True)

    pdb.delete.assert_not_called()             # nested meta target: protected, not deleted
    safe_delete.assert_not_awaited()           # its thread's own delete never attempted
    nested_ch.edit.assert_not_awaited()        # no relocation attempted (no equivalent for threads)
    forum_ch.delete.assert_awaited_once()      # forum channel itself still torn down
    assert failed == []


@pytest.mark.asyncio
async def test_full_delete_refreshes_other_fed_targets():
    """keep_puzzles=False deletes a feeder puzzle that also feeds ANOTHER
    round's meta; that other meta's target must be refreshed so its
    contribution table doesn't keep a stale row for the deleted puzzle
    (review #29)."""
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)
    other_group = make_round(id=9, category_id=200, target_puzzle_id=0)

    # Feeder is tagged into both this round (5, being deleted) and another
    # round (9, NOT being deleted) — deleting it must refresh group 9.
    feeder = make_puzzle(id=2, name="Feeder", channel_id=902, tags=[5, 9])
    feeder_ch = MagicMock(id=902); feeder_ch.delete = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [feeder_ch]
    round_cat.text_channels = []
    round_cat.delete = AsyncMock()

    guild = MagicMock()
    guild.categories = [round_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {902: feeder_ch}.get(cid))

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch.object(svc, "refresh_targets", AsyncMock(return_value=[])) as refresh_mock, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [feeder]
        tgcdb.get_for_group.return_value = []
        tgdb.get_by_attr.side_effect = lambda id: other_group if id == 9 else tag_group

        failed = await svc.delete_round(guild, hunt, tag_group, keep_puzzles=False)

    refresh_mock.assert_awaited_once()
    refreshed_ids = {g.id for g in refresh_mock.await_args.args[1]}
    assert refreshed_ids == {9}          # group 5 (being deleted) is excluded
    assert failed == []
