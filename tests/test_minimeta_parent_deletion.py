"""Deleting a minimeta parent must take its minis with it (M1).

Minis are children keyed by parent_puzzle_id, and the FK is ON DELETE SET NULL,
so deleting the parent left the mini ROWS behind with parent_puzzle_id NULL.
get_unassigned filters on exactly `parent_puzzle_id IS NULL` and no feeds edge,
so the orphans then surfaced on the Unassigned Feeders tab and on the dashboard
board -- each carrying a thread channel_id whose parent channel had just been
deleted.

Nothing routed a plain delete through the tidy paths that exist (delete_mini,
remove_minimeta), and no test covered deleting a minimeta parent at all.

Two entry points need it, and they are NOT the same code path:
  * delete_puzzle, which tears a puzzle down itself
  * delete_round, which does its own per-puzzle teardown over `to_delete`
    rather than calling delete_puzzle

delete_round's narrow branch RELOCATES its members instead of deleting them, so
their minis must survive. That distinction is the whole risk in this fix.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.discord_channels import SOLVE_DIVIDER
from tests.conftest import make_hunt, make_puzzle, make_round


def _svc():
    from bot.core.puzzle_service import PuzzleService
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


# ---------------------------------------------------------------------------
# delete_puzzle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deleting_a_minimeta_parent_deletes_its_minis():
    """TWO minis, so "delete one" and "delete them all" disagree."""
    svc = _svc()
    hunt = make_hunt()
    parent = make_puzzle(id=1, tags=[], channel_id=None)
    mini_a = make_puzzle(id=2, tags=[], channel_id=None)
    mini_b = make_puzzle(id=3, tags=[], channel_id=None)
    mini_a.parent_puzzle_id = 1
    mini_b.parent_puzzle_id = 1

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch.object(svc, "_gsheet_cog", return_value=None), \
         patch.object(svc, "refresh_targets", new=AsyncMock(return_value=[])), \
         patch.object(svc, "_safe_delete_channel", new=AsyncMock(return_value=True)):
        pdb.get_children.side_effect = lambda pid: [mini_a, mini_b] if pid == 1 else []
        await svc.delete_puzzle(None, hunt, parent)

    deleted = [c.args[0] for c in pdb.delete.call_args_list]
    assert sorted(deleted) == [1, 2, 3], deleted


@pytest.mark.asyncio
async def test_deleting_an_ordinary_puzzle_deletes_only_itself():
    """The keeper: without it, "delete everything reachable" would pass above."""
    svc = _svc()
    hunt = make_hunt()
    puzzle = make_puzzle(id=1, tags=[], channel_id=None)

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch.object(svc, "_gsheet_cog", return_value=None), \
         patch.object(svc, "refresh_targets", new=AsyncMock(return_value=[])), \
         patch.object(svc, "_safe_delete_channel", new=AsyncMock(return_value=True)):
        pdb.get_children.return_value = []
        await svc.delete_puzzle(None, hunt, puzzle)

    assert [c.args[0] for c in pdb.delete.call_args_list] == [1]


# ---------------------------------------------------------------------------
# delete_round -- the two branches must differ
# ---------------------------------------------------------------------------

def _general_category(cat_id=500):
    div = MagicMock(); div.name = SOLVE_DIVIDER; div.position = 1
    gen = MagicMock(); gen.name = "general"; gen.position = 0
    cat = MagicMock(id=cat_id)
    cat.channels = [gen, div]
    return cat


def _round_guild(round_cat_id=100):
    round_cat = MagicMock(id=round_cat_id)
    round_cat.channels = []
    round_cat.delete = AsyncMock()
    guild = MagicMock()
    guild.categories = [round_cat, _general_category()]
    guild.get_channel = MagicMock(return_value=None)
    guild.get_channel_or_thread = guild.get_channel
    return guild


@pytest.mark.asyncio
async def test_a_full_round_delete_sweeps_the_minis_of_deleted_members():
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=1)
    target = make_puzzle(id=1, is_target=True, channel_id=None, tags=[5])
    member = make_puzzle(id=2, channel_id=None, tags=[5])
    mini = make_puzzle(id=3, channel_id=None, tags=[])
    mini.parent_puzzle_id = 2

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [target, member]
        pdb.get_by_attr.return_value = None
        pdb.get_children.side_effect = lambda pid: [mini] if pid == 2 else []
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []
        await svc.delete_round(_round_guild(), hunt, tag_group, keep_puzzles=False)

    deleted = sorted(c.args[0] for c in pdb.delete.call_args_list)
    assert 3 in deleted, f"the member's mini must go with it: {deleted}"
    assert deleted == [1, 2, 3], deleted


@pytest.mark.asyncio
async def test_a_narrow_round_delete_leaves_a_relocated_members_minis_alone():
    """The guard that makes the fix safe. keep_puzzles=True RELOCATES members
    rather than deleting them, so their minis must survive with them -- only
    the round's own target is torn down.

    Paired with the test above: a sweep that ignores the branch passes that one
    and fails this one.
    """
    svc = _svc()
    hunt = make_hunt(category_id=500, layout_mode="channel")
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=1)
    target = make_puzzle(id=1, is_target=True, channel_id=None, tags=[5])
    member = make_puzzle(id=2, channel_id=None, tags=[5])
    mini = make_puzzle(id=3, channel_id=None, tags=[])
    mini.parent_puzzle_id = 2

    gsheet = MagicMock(); gsheet.delete_puzzle_spreadsheet = AsyncMock()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [target, member]
        pdb.get_by_attr.return_value = None
        pdb.get_children.side_effect = lambda pid: [mini] if pid == 2 else []
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []
        await svc.delete_round(_round_guild(), hunt, tag_group)

    deleted = [c.args[0] for c in pdb.delete.call_args_list]
    assert 3 not in deleted, f"a relocated member's mini must survive: {deleted}"
    assert 2 not in deleted, f"the member itself is relocated, not deleted: {deleted}"
    assert deleted == [1], deleted
