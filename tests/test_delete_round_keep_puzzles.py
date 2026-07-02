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
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [target, feeder]
        tgcdb.get_for_group.return_value = []

        failed = await svc.delete_round(guild, hunt, tag_group)

    pdb.delete.assert_called_once_with(1)                                       # only the target's DB row
    feeder_ch.edit.assert_awaited_once_with(category=general_cat, position=0)    # unsolved: divider_pos(1) - 1
    target_ch.delete.assert_awaited_once()
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
    general_cat.channels = [MagicMock() for _ in range(MAX_CHANNELS_PER_CATEGORY)]  # full

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {902: feeder_ch}.get(cid))

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [feeder]
        tgcdb.get_for_group.return_value = []

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
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [feeder]
        tgcdb.get_for_group.return_value = []

        failed = await svc.delete_round(guild, hunt, tag_group)

    chat_ch.delete.assert_awaited_once()
    feeder_ch.edit.assert_awaited_once_with(category=general_cat, position=0)
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
    general_cat.channels = [MagicMock() for _ in range(MAX_CHANNELS_PER_CATEGORY)]  # full -> relocation fails

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {902: feeder_ch}.get(cid))

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb:
        pdb.get_all_from_tag_group.return_value = [feeder]
        tgcdb.get_for_group.return_value = [
            TagGroupCategoryData(group_id=5, category_id=100, role="unsolved", seq=0),
        ]

        failed = await svc.delete_round(guild, hunt, tag_group)

    feeder_ch.edit.assert_not_awaited()
    feeder_ch.delete.assert_not_called()      # protected via keep_channel_ids
    stray_ch.delete.assert_awaited_once()     # unprotected channel still swept
    round_cat.delete.assert_awaited_once()    # category itself still torn down
    tgcdb.delete_for_group.assert_called_once_with(5)
    pdb.delete.assert_not_called()            # feeder's DB row untouched
    assert failed == ["Feeder"]


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
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [ordinary, nested_meta_target]
        tgcdb.get_for_group.return_value = []

        failed = await svc.delete_round(guild, hunt, tag_group, keep_puzzles=False)

    pdb.delete.assert_called_once_with(2)                                        # only the ordinary puzzle's DB row
    ordinary_ch.delete.assert_awaited_once()                                     # ordinary puzzle: deleted
    nested_ch.edit.assert_awaited_once_with(category=general_cat, position=0)    # nested meta target: relocated
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
async def test_full_delete_forum_mode_unaffected_by_foreign_target_protection():
    """Forum-layout rounds always full-delete regardless of keep_puzzles (no
    relocation story for threads) — a forum round containing a foreign
    tag group's target puzzle must still delete it outright, exactly like
    before the #30 fix, not attempt (and silently fail) a relocation."""
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="forum")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)

    nested_meta_target = make_puzzle(
        id=3, name="Nested Meta", is_target=True, channel_id=903, tags=[5])

    forum_ch = MagicMock(id=100); forum_ch.delete = AsyncMock()
    guild = MagicMock()
    guild.get_channel = MagicMock(side_effect=lambda cid: {100: forum_ch}.get(cid))

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch.object(svc, "_safe_delete_channel", AsyncMock(return_value=True)) as safe_delete:
        pdb.get_all_from_tag_group.return_value = [nested_meta_target]

        failed = await svc.delete_round(guild, hunt, tag_group, keep_puzzles=True)

    pdb.delete.assert_called_once_with(3)             # deleted outright, not protected
    safe_delete.assert_awaited_once_with(guild, 903)  # attempted its own channel delete
    forum_ch.delete.assert_awaited_once()              # forum channel itself torn down
    assert failed == []
