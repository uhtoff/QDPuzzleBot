"""delete_round's keep_puzzles=True (default) path: deletes only the round's
own target puzzle (or, for a target-less Group round, its dedicated chat
channel), relocates every other member puzzle's Discord channel into the
hunt's general category instead of deleting it, and leaves their DB
rows/sheets untouched.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.discord_channels import MAX_CHANNELS_PER_CATEGORY, SOLVE_DIVIDER
from tests.conftest import make_hunt, make_puzzle, make_round


@pytest.fixture(autouse=True)
def _no_store_lookups(monkeypatch):
    """category_layout lazily imports bot.store for its claim checks and
    source-category cleanup; stub those lookups to miss so relocation flows
    here never touch MySQL (these tests patch the service's own DAO refs,
    which the lazy import doesn't go through)."""
    import bot.store as store
    for name in ("TagGroupCategoryDb", "HuntCategoryDb"):
        stub = MagicMock()
        stub.get_by_category.return_value = None
        monkeypatch.setattr(store, name, stub)
    for name in ("TagGroupJsonDb", "HuntJsonDb"):
        stub = MagicMock()
        stub.get_by_attr.return_value = None
        monkeypatch.setattr(store, name, stub)


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
    hunt = make_hunt(category_id=500)
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
    guild.get_channel_or_thread = guild.get_channel

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [target, feeder]
        pdb.get_by_attr.return_value = None
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
    hunt = make_hunt(category_id=500)
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
    guild.get_channel_or_thread = guild.get_channel

    sibling_group = MagicMock(id=7, category_id=200)

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [feeder]
        pdb.get_by_attr.return_value = None
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []
        tgdb.get_by_attr.return_value = sibling_group

        failed = await svc.delete_round(guild, hunt, tag_group)

    tgdb.get_by_attr.assert_called_once_with(id=7)     # tag_group.id (5) excluded
    feeder_ch.edit.assert_awaited_once_with(category=sibling_cat, position=0)
    assert failed == []


@pytest.mark.asyncio
async def test_keep_puzzles_falls_back_to_general_when_sibling_group_has_no_category():
    """If a kept puzzle's other group has no usable category (e.g. a channel
    home, or the group row is otherwise unresolvable), it still falls back to the
    hunt's general category rather than being reported as a failure."""
    svc = _svc()
    hunt = make_hunt(category_id=500)
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
    guild.get_channel_or_thread = guild.get_channel

    sibling_group = MagicMock(id=7, category_id=None)   # unresolvable home category

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [feeder]
        pdb.get_by_attr.return_value = None
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []
        tgdb.get_by_attr.return_value = sibling_group

        failed = await svc.delete_round(guild, hunt, tag_group)

    feeder_ch.edit.assert_awaited_once_with(category=general_cat, position=1)
    assert failed == []


@pytest.mark.asyncio
async def test_keep_puzzles_relocation_failure_reported():
    svc = _svc()
    hunt = make_hunt(category_id=500)
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
    guild.get_channel_or_thread = guild.get_channel

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [feeder]
        pdb.get_by_attr.return_value = None
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
    hunt = make_hunt(category_id=500)
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
    guild.get_channel_or_thread = guild.get_channel

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [feeder]
        pdb.get_by_attr.return_value = None
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []

        failed = await svc.delete_round(guild, hunt, tag_group)

    chat_ch.delete.assert_awaited_once()
    feeder_ch.edit.assert_awaited_once_with(category=general_cat, position=1)
    pdb.delete.assert_not_called()
    assert failed == []


@pytest.mark.asyncio
async def test_keep_puzzles_split_state_protects_relocation_failure_from_blanket_delete():
    """Integration coverage for the keep_channel_ids wiring: a puzzle whose
    channel fails to relocate must survive the real (non-mocked)
    delete_group_categories blanket per-category delete, while an
    unprotected channel in the same category is still swept."""
    from bot.store.tag_group_category_data import TagGroupCategoryData

    svc = _svc()
    hunt = make_hunt(category_id=500)
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
    guild.get_channel_or_thread = guild.get_channel

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb:
        pdb.get_all_from_tag_group.return_value = [feeder]
        pdb.get_by_attr.return_value = None    # stray_ch has no puzzle record at all
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
    hunt = make_hunt(category_id=500)
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=0)

    round_cat = MagicMock(id=100)
    round_cat.channels = []
    round_cat.text_channels = []
    round_cat.delete = AsyncMock()

    guild = MagicMock()
    guild.categories = [round_cat]
    guild.get_channel = MagicMock(return_value=None)
    guild.get_channel_or_thread = guild.get_channel

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
    hunt = make_hunt(category_id=500)
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
    guild.get_channel_or_thread = guild.get_channel

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [ordinary, nested_meta_target]
        pdb.get_by_attr.return_value = None
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
    hunt = make_hunt(category_id=500)
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
    guild.get_channel_or_thread = guild.get_channel

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = []
        pdb.get_by_attr.return_value = None
        tgcdb.get_for_group.return_value = []

        failed = await svc.delete_round(guild, hunt, tag_group, keep_puzzles=True)

    chat_ch.delete.assert_awaited_once()
    assert failed == []


@pytest.mark.asyncio
async def test_full_delete_keeps_shared_feeder_deletes_exclusive_feeder():
    """keep_puzzles=False must not delete a member puzzle that is ALSO tagged
    to another group (a shared feeder) — that group still needs it. Only the
    round's own target and members tagged EXCLUSIVELY to this group are fair
    game for full delete; a shared feeder is relocated instead (data-loss
    regression: previously any non-target member was deleted outright)."""
    svc = _svc()
    hunt = make_hunt(category_id=500)
    tag_group = make_round(id=10, category_id=100, target_puzzle_id=1)

    target = make_puzzle(id=1, is_target=True, channel_id=901, tags=[10])
    exclusive_feeder = make_puzzle(id=2, name="Exclusive", channel_id=902, tags=[10])
    shared_feeder = make_puzzle(id=3, name="Shared", channel_id=903, tags=[10, 99])

    target_ch = MagicMock(id=901); target_ch.delete = AsyncMock()
    exclusive_ch = MagicMock(id=902); exclusive_ch.delete = AsyncMock()
    shared_ch = MagicMock(id=903); shared_ch.category_id = 100; shared_ch.edit = AsyncMock()
    shared_ch.delete = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [target_ch, exclusive_ch, shared_ch]
    round_cat.text_channels = []
    round_cat.delete = AsyncMock()
    general_cat = _general_category()

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(
        side_effect=lambda cid: {901: target_ch, 902: exclusive_ch, 903: shared_ch}.get(cid))
    guild.get_channel_or_thread = guild.get_channel

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [target, exclusive_feeder, shared_feeder]
        pdb.get_by_attr.return_value = None
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []
        tgdb.get_by_attr.return_value = None    # group 99 unresolvable -> falls back to general

        failed = await svc.delete_round(guild, hunt, tag_group, keep_puzzles=False)

    deleted_ids = {c.args[0] for c in pdb.delete.call_args_list}
    assert deleted_ids == {1, 2}                                  # target + exclusive feeder only
    target_ch.delete.assert_awaited_once()
    exclusive_ch.delete.assert_awaited_once()
    shared_ch.delete.assert_not_called()                          # shared feeder: never destroyed
    shared_ch.edit.assert_awaited_once_with(category=general_cat, position=1)  # relocated instead
    assert failed == []


@pytest.mark.asyncio
async def test_full_delete_relocates_kept_target_whose_home_is_doomed_to_general():
    """A kept puzzle that is ANOTHER group's target, where that other group is
    channel-home INTO the category being deleted (its target channel was
    consolidated into this round's category), must be relocated to the hunt's
    general category — not left behind. Previously move_channel_to_owner
    resolved the other group's home back to the doomed category, returned a
    false 'already placed' success, and the channel was orphaned to the guild
    root ('unclassified') when the category was torn down."""
    svc = _svc()
    hunt = make_hunt(category_id=500)
    tag_group = make_round(id=10, category_id=100, target_puzzle_id=1)

    own_target = make_puzzle(id=1, is_target=True, channel_id=901, tags=[10])
    # P is group 77's target (is_target) and also feeds group 10 (being deleted).
    # Group 77 is channel-home: category_id points at P's own channel (903),
    # which physically sits inside group 10's doomed category (100).
    foreign_target = make_puzzle(
        id=3, name="Foreign Meta", is_target=True, channel_id=903, tags=[10, 77])

    own_ch = MagicMock(id=901); own_ch.delete = AsyncMock()
    p_ch = MagicMock(id=903); p_ch.category_id = 100
    p_ch.edit = AsyncMock(); p_ch.delete = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [own_ch, p_ch]
    round_cat.text_channels = []
    round_cat.delete = AsyncMock()
    p_ch.category = round_cat          # its parent is the doomed category
    general_cat = _general_category()

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {901: own_ch, 903: p_ch}.get(cid))
    guild.get_channel_or_thread = guild.get_channel

    foreign_group = MagicMock(id=77, category_id=903)   # channel-home into cat 100

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [own_target, foreign_target]
        pdb.get_by_attr.return_value = None
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []
        tgdb.get_by_attr.side_effect = lambda id: foreign_group if id == 77 else None

        failed = await svc.delete_round(guild, hunt, tag_group, keep_puzzles=False)

    pdb.delete.assert_called_once_with(1)                                    # only group 10's own target
    p_ch.delete.assert_not_called()                                          # foreign target never destroyed
    p_ch.edit.assert_awaited_once_with(category=general_cat, position=1)      # relocated to general, not orphaned
    assert failed == []


@pytest.mark.asyncio
async def test_full_delete_relocates_foreign_channel_before_category_teardown():
    """A channel physically inside this round's owned category whose puzzle
    is NOT a member of this group at all (e.g. a channel-home meta that was
    consolidated/promoted into this category, tagged to a different group)
    must be relocated to the hunt's general category BEFORE the category
    teardown sweep — not swept up and deleted along with the round's own
    channels (data-loss regression)."""
    svc = _svc()
    hunt = make_hunt(category_id=500)
    tag_group = make_round(id=10, category_id=100, target_puzzle_id=1)

    target = make_puzzle(id=1, is_target=True, channel_id=901, tags=[10])
    foreign_puzzle = make_puzzle(id=900, name="Foreign", channel_id=555, tags=[77], solved=False)

    target_ch = MagicMock(id=901); target_ch.delete = AsyncMock()
    foreign_ch = MagicMock(id=555); foreign_ch.category_id = 100; foreign_ch.edit = AsyncMock()
    foreign_ch.delete = AsyncMock()
    foreign_ch.name = "foreign-puzzle"

    round_cat = MagicMock(id=100)
    round_cat.channels = [target_ch, foreign_ch]
    round_cat.text_channels = []
    round_cat.delete = AsyncMock()
    general_cat = _general_category()

    guild = MagicMock()
    guild.categories = [round_cat, general_cat]
    guild.get_channel = MagicMock(
        side_effect=lambda cid: {901: target_ch, 555: foreign_ch}.get(cid))
    guild.get_channel_or_thread = guild.get_channel

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [target]
        pdb.get_by_attr.side_effect = lambda channel_id: (
            foreign_puzzle if channel_id == 555 else None)
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []

        failed = await svc.delete_round(guild, hunt, tag_group, keep_puzzles=False)

    pdb.delete.assert_called_once_with(1)              # only the round's own target deleted
    target_ch.delete.assert_awaited_once()
    foreign_ch.delete.assert_not_called()               # foreign channel: never destroyed
    foreign_ch.edit.assert_awaited_once_with(category=general_cat, position=1)  # relocated out
    assert failed == []


@pytest.mark.asyncio
async def test_full_delete_refreshes_other_fed_targets():
    """keep_puzzles=False deletes this round's own target puzzle, which also
    happens to feed ANOTHER round's meta; that other meta's target must be
    refreshed so its contribution table doesn't keep a stale row for the
    deleted puzzle (review #29). (A plain non-target member shared with
    another group is now kept/relocated rather than deleted — see the
    exclusivity fix — so this covers the still-deleted case: the round's own
    target, which is also tagged elsewhere.)"""
    svc = _svc()
    hunt = make_hunt(category_id=500)
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=2)
    other_group = make_round(id=9, category_id=200, target_puzzle_id=0)

    # Target is tagged into both this round (5, being deleted) and another
    # round (9, NOT being deleted) — deleting it must refresh group 9.
    feeder = make_puzzle(id=2, name="Feeder", is_target=True, channel_id=902, tags=[5, 9])
    feeder_ch = MagicMock(id=902); feeder_ch.delete = AsyncMock()

    round_cat = MagicMock(id=100)
    round_cat.channels = [feeder_ch]
    round_cat.text_channels = []
    round_cat.delete = AsyncMock()

    guild = MagicMock()
    guild.categories = [round_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {902: feeder_ch}.get(cid))
    guild.get_channel_or_thread = guild.get_channel

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


@pytest.mark.asyncio
async def test_files_full_delete_removes_each_file_and_skips_a_mini_whose_file_went():
    """Files hunt, full delete: the target's file and the exclusive feeder's
    file are deleted (delete_file, never a tab delete); the feeder's mini lives
    in the feeder's file, so its tab went with that file and gets no call --
    a tab delete on a deleted file only 404s. All three DB rows still go."""
    from bot.cogs.puzzles_gsheet import GoogleSheets
    svc = _svc()
    hunt = make_hunt(google_sheet_id="REF", solved_sheet_id="", sheet_mode="files",
                     drive_folder_id="FOLDER", solved_folder_id="SOLVEDFOLDER")
    tag_group = make_round(id=5, category_id=0, target_puzzle_id=1)
    target = make_puzzle(id=1, is_target=True, channel_id=0, tags=[5])
    target.google_sheet_id, target.google_page_id = "FILE1", 11
    feeder = make_puzzle(id=2, channel_id=0, tags=[5])
    feeder.google_sheet_id, feeder.google_page_id = "FILE2", 21
    mini = make_puzzle(id=3, channel_id=0, parent_puzzle_id=2)
    mini.google_sheet_id, mini.google_page_id = "FILE2", 31

    cog = GoogleSheets.__new__(GoogleSheets)
    cog.get_spreadsheet_id = lambda: "REF"
    cog.get_solved_sheet_id = lambda: ""
    cog.delete_file = AsyncMock()
    cog.delete_sheet = AsyncMock()

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb"), \
         patch.object(svc, "_gsheet_cog", return_value=cog):
        pdb.get_all_from_tag_group.return_value = [target, feeder]
        pdb.get_children.side_effect = lambda pid: [mini] if pid == 2 else []
        await svc.delete_round(None, hunt, tag_group, keep_puzzles=False)

    assert [c.args for c in cog.delete_file.await_args_list] == [("FILE1",), ("FILE2",)]
    cog.delete_sheet.assert_not_awaited()
    assert sorted(c.args[0] for c in pdb.delete.call_args_list) == [1, 2, 3]
