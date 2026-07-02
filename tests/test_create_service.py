"""M3.5: PuzzleService.create_puzzle/create_round orchestration + cleanup.

These mirror the existing tests/test_create_orphan_cleanup.py wiring but target the
SERVICE, which calls the *module* function bot.core.discord_channels.get_or_create_channel
(not a cog attribute) and the relocated bot.core.puzzle_service.PuzzleJsonDb / TagGroupJsonDb.
Helpers are uniquely named and local to this file (parallel gate shards by file).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_round, make_puzzle


def _svc_hunt():
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "S1"
    h.archive_google_sheet_id = "A1"
    h.url = None
    h.category_id = 1
    h.name = "H"
    return h


def _service_with_sheets():
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    gsheet.compute_insert_index = AsyncMock(return_value=0)
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return PuzzleService(bot), gsheet


@pytest.mark.asyncio
async def test_create_puzzle_commits_row_creates_sheet_and_notifies():
    svc, gsheet = _service_with_sheets()
    guild = MagicMock()
    guild.channels = []
    cat = MagicMock(id=999)
    guild.categories = [cat]
    notifier = MagicMock()
    notifier.welcome = AsyncMock()
    round_data = make_round(id=5, category_id=999)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.category_layout.pick_unsolved_category",
               AsyncMock(return_value=MagicMock(id=999, channels=[]))), \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=222, mention="<#222>", name="newpuz"), True))):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        await svc.create_puzzle(guild, _svc_hunt(), round_data, "Newpuz", is_meta=False, notifier=notifier)
    gsheet.create_puzzle_spreadsheet.assert_awaited_once()
    notifier.welcome.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_puzzle_orphan_cleanup_on_channel_failure():
    # Relocated from test_create_orphan_cleanup.py — now targets the SERVICE.
    svc, gsheet = _service_with_sheets()
    guild = MagicMock()
    guild.channels = []
    guild.categories = [MagicMock(id=999)]
    notifier = MagicMock()
    notifier.welcome = AsyncMock()
    round_data = make_round(id=5, category_id=999)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.category_layout.pick_unsolved_category",
               AsyncMock(return_value=MagicMock(id=999, channels=[]))), \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(side_effect=discord.HTTPException(MagicMock(), "boom"))):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        with pytest.raises(discord.HTTPException):
            await svc.create_puzzle(guild, _svc_hunt(), round_data, "Newpuz", is_meta=False, notifier=notifier)
    gsheet.delete_puzzle_spreadsheet.assert_awaited_once()
    pdb.delete.assert_called_once_with(1)
    notifier.welcome.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_puzzle_uses_layout_picker_for_round():
    svc, gsheet = _service_with_sheets()
    guild = MagicMock()
    guild.channels = []
    target_cat = MagicMock(id=777)
    target_cat.channels = []
    guild.categories = [target_cat]
    notifier = MagicMock()
    notifier.welcome = AsyncMock()
    round_data = make_round(id=5, category_id=999)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.category_layout.pick_unsolved_category",
               AsyncMock(return_value=target_cat)) as pick, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=222, mention="<#222>", name="newpuz"), True))):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        await svc.create_puzzle(guild, _svc_hunt(), round_data, "Newpuz", is_meta=False, notifier=notifier)
    pick.assert_awaited_once()


@pytest.mark.parametrize("group_type, target_puzzle_id, expected_edge", [
    ("Metapuzzle", 9, "feeds"),    # feeds the meta target
    ("Capstone", 9, "informs"),    # informs the capstone target
    ("Runaround", 9, "feeds"),     # runaround feeds like a meta
    ("Group", 0, "none"),          # bare group: no target puzzle to feed/inform
])
@pytest.mark.asyncio
async def test_create_puzzle_sets_edge_by_group_type(group_type, target_puzzle_id, expected_edge):
    """A new puzzle assigned to a group gets the edge that matches the group's
    type — not an unconditional 'feeds'. Regression: create_puzzle used to only
    append the tag, leaving the DB default ('feeds') for every group type."""
    svc, gsheet = _service_with_sheets()
    guild = MagicMock()
    guild.channels = []
    cat = MagicMock(id=999, channels=[])
    guild.categories = [cat]
    notifier = MagicMock()
    notifier.welcome = AsyncMock()
    round_data = make_round(id=5, category_id=999, target_puzzle_id=target_puzzle_id)
    round_data.type = group_type
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.category_layout.pick_unsolved_category",
               AsyncMock(return_value=cat)), \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=222, mention="<#222>", name="newpuz"), True))):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        puzzle = await svc.create_puzzle(
            guild, _svc_hunt(), round_data, "Newpuz", is_meta=False, notifier=notifier)
    pdb.tag_puzzle_with_edge.assert_called_once_with(1, 5, expected_edge)
    assert puzzle.has_edge(5, expected_edge)


@pytest.mark.asyncio
async def test_create_meta_target_gets_target_edge():
    """A group's own target puzzle (is_meta) is a member but neither feeds nor
    informs its group, so it is tagged with a 'none' self-edge — not 'feeds'."""
    svc, gsheet = _service_with_sheets()
    guild = MagicMock()
    guild.channels = []
    cat = MagicMock(id=999, channels=[])
    guild.categories = [cat]
    notifier = MagicMock()
    notifier.welcome = AsyncMock()
    round_data = make_round(id=5, category_id=999, target_puzzle_id=0)
    round_data.type = "Metapuzzle"
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.category_layout.pick_unsolved_category",
               AsyncMock(return_value=cat)), \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=222, mention="<#222>", name="newpuz"), True))):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        puzzle = await svc.create_puzzle(
            guild, _svc_hunt(), round_data, "TheMeta", is_meta=True, notifier=notifier)
    pdb.tag_puzzle_with_edge.assert_called_once_with(1, 5, "target")
    assert puzzle.has_edge(5, "target")


@pytest.mark.asyncio
async def test_create_unassigned_puzzle_sets_no_edge():
    """An unassigned puzzle (no round_data) has no group, so no edge is written."""
    svc, gsheet = _service_with_sheets()
    guild = MagicMock()
    guild.channels = []
    cat = MagicMock(id=1, channels=[])
    guild.categories = [cat]
    notifier = MagicMock()
    notifier.welcome = AsyncMock()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=222, mention="<#222>", name="newpuz"), True))):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        await svc.create_puzzle(
            guild, _svc_hunt(), None, "Newpuz", is_meta=False, notifier=notifier)
    pdb.tag_puzzle_with_edge.assert_not_called()


@pytest.mark.asyncio
async def test_create_group_stores_category_id_not_text_channel():
    """A channel-mode Group stores its cloned CATEGORY id (so new puzzles land
    under it), not the group's text-channel id. Regression: create_tag_group
    stored group_text_channel.id for every Group, breaking category placement."""
    svc, gsheet = _service_with_sheets()
    guild = MagicMock()
    guild.channels = []
    ref_cat = MagicMock(id=1, position=0)
    guild.categories = [ref_cat]
    new_cat = MagicMock(id=2, channels=[])
    ref_cat.clone = AsyncMock(return_value=new_cat)
    new_cat.edit = AsyncMock()
    notifier = MagicMock()
    notifier.welcome = AsyncMock()

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=333), True))):
        rdb.check_duplicates_in_hunt.return_value = False
        pdb.check_duplicates_in_hunt.return_value = False
        rdb.commit.side_effect = lambda r: setattr(r, "id", 5)
        rdb.generate_uid.return_value = "GRP001"
        new_round, puzzle = await svc.create_tag_group(
            guild, _svc_hunt(), "New Group",
            notifier=notifier, meta_puzzle_name=False, round_type="Group")

    assert puzzle is None
    assert new_round.category_id == new_cat.id   # the cloned category, not text channel 333
    notifier.welcome.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_round_creates_round_and_meta_puzzle():
    svc, gsheet = _service_with_sheets()
    guild = MagicMock()
    guild.channels = []
    ref_cat = MagicMock(id=1, position=0)
    guild.categories = [ref_cat]
    new_cat = MagicMock(id=2, channels=[])
    ref_cat.clone = AsyncMock(return_value=new_cat)
    new_cat.edit = AsyncMock()
    notifier = MagicMock()
    notifier.welcome = AsyncMock()

    # The service makes the meta puzzle via its own create_puzzle; stub that seam so
    # this test isolates the round/meta orchestration (mirrors web_create_round :820).
    meta_puzzle = make_puzzle(id=77, name="New Round", is_target=True, channel_id=222)
    guild.get_channel = MagicMock(return_value=MagicMock(id=222))

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=333), True))), \
         patch.object(svc, "create_puzzle", AsyncMock(return_value=meta_puzzle)):
        rdb.check_duplicates_in_hunt.return_value = False
        pdb.check_duplicates_in_hunt.return_value = False
        rdb.commit.side_effect = lambda r: setattr(r, "id", 5)
        rdb.generate_uid.return_value = "META01"
        result = await svc.create_tag_group(guild, _svc_hunt(), "New Round", notifier=notifier)

    new_round, puzzle = result
    assert puzzle is meta_puzzle
    assert new_round.target_puzzle_id == meta_puzzle.id
    assert new_round.group_code == "META01"


@pytest.mark.asyncio
async def test_create_round_rolls_back_on_failure():
    svc, gsheet = _service_with_sheets()
    guild = MagicMock()
    guild.channels = []
    ref_cat = MagicMock(id=1, position=0)
    guild.categories = [ref_cat]
    new_cat = MagicMock(id=2, channels=[])
    ref_cat.clone = AsyncMock(return_value=new_cat)
    new_cat.edit = AsyncMock()
    new_cat.delete = AsyncMock()
    notifier = MagicMock()
    notifier.welcome = AsyncMock()

    meta_puzzle = make_puzzle(id=77, name="New Round", is_target=True, channel_id=222)
    cleanup_mock = AsyncMock()

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=333), True))), \
         patch.object(svc, "create_puzzle", AsyncMock(return_value=meta_puzzle)), \
         patch.object(svc, "_cleanup_orphan_puzzle", cleanup_mock):
        rdb.check_duplicates_in_hunt.return_value = False
        pdb.check_duplicates_in_hunt.return_value = False
        rdb.commit.side_effect = lambda r: setattr(r, "id", 5)
        rdb.generate_uid.side_effect = RuntimeError("late failure")  # fails BEFORE create_puzzle (new order)
        with pytest.raises(RuntimeError):
            await svc.create_tag_group(guild, _svc_hunt(), "New Round", notifier=notifier)

    # generate_uid fails before create_puzzle is reached → puzzle=None.
    # Round row deleted, no orphan to clean, cloned category removed.
    rdb.delete.assert_called_once_with(5)
    cleanup_mock.assert_not_awaited()
    new_cat.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_round_uses_layout_teardown_for_split():
    """A split round routes category teardown through delete_group_categories
    (deleting all its categories + join rows) instead of the legacy single path."""
    svc, gsheet = _service_with_sheets()
    guild = MagicMock(); guild.categories = []
    round_data = make_round(id=5, category_id=999)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=True)) as teardown:
        pdb.get_all_from_tag_group.return_value = []
        await svc.delete_round(guild, _svc_hunt(), round_data)
    teardown.assert_awaited_once()
