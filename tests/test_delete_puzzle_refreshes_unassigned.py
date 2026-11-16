"""Deleting a puzzle must refresh the Unassigned Feeders sheet tab.

The board drops the puzzle at once (delete_puzzle pushes it, and the board reads
the DB), but the sheet tab is a native Table reconciled only when something
calls refresh_unassigned. Neither delete entry point did, so a deleted
unassigned feeder's row lingered until an unrelated mutation fired a refresh.

The refresh lives in the shared seam (PuzzleService.delete_puzzle) so both the
!delete_puzzle bang and the web DELETE route get it.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt, make_puzzle


def _svc():
    from bot.core.puzzle_service import PuzzleService
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


async def _delete(svc, hunt, puzzle, children=None):
    children = children or {}
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch.object(svc, "_gsheet_cog", return_value=None), \
         patch.object(svc, "refresh_targets", new=AsyncMock(return_value=[])), \
         patch.object(svc, "_safe_delete_channel", new=AsyncMock(return_value=True)), \
         patch.object(svc, "refresh_unassigned_safe", new=AsyncMock()) as refresh:
        pdb.get_children.side_effect = lambda pid: children.get(pid, [])
        await svc.delete_puzzle(None, hunt, puzzle)
        # Let the fire-and-forget task run while the mock is still installed.
        import asyncio
        await asyncio.sleep(0)
    return refresh


@pytest.mark.asyncio
async def test_deleting_a_puzzle_refreshes_the_unassigned_tab_for_its_hunt():
    svc = _svc()
    hunt = make_hunt()
    puzzle = make_puzzle(id=1, tags=[], channel_id=None)

    refresh = await _delete(svc, hunt, puzzle)

    refresh.assert_awaited_once_with(hunt)


@pytest.mark.asyncio
async def test_deleting_a_minimeta_parent_refreshes_once_not_per_mini():
    """Minis are never on the tab (parent_puzzle_id set), and the parent's own
    refresh runs after they are gone -- so TWO minis must still mean ONE refresh."""
    svc = _svc()
    hunt = make_hunt()
    parent = make_puzzle(id=1, tags=[], channel_id=None)
    mini_a = make_puzzle(id=2, tags=[], channel_id=None, parent_puzzle_id=1)
    mini_b = make_puzzle(id=3, tags=[], channel_id=None, parent_puzzle_id=1)

    refresh = await _delete(svc, hunt, parent, children={1: [mini_a, mini_b]})

    refresh.assert_awaited_once_with(hunt)


# ---------------------------------------------------------------------------
# delete_round -- its own teardown loop, so it needs its own refresh
# ---------------------------------------------------------------------------

def _round_guild(round_cat_id=100):
    from bot.core.discord_channels import SOLVE_DIVIDER
    div = MagicMock(); div.name = SOLVE_DIVIDER; div.position = 1
    gen = MagicMock(); gen.name = "general"; gen.position = 0
    general = MagicMock(id=500); general.channels = [gen, div]
    round_cat = MagicMock(id=round_cat_id); round_cat.channels = []
    round_cat.delete = AsyncMock()
    guild = MagicMock()
    guild.categories = [round_cat, general]
    guild.get_channel = MagicMock(return_value=None)
    guild.get_channel_or_thread = guild.get_channel
    return guild


@pytest.mark.asyncio
@pytest.mark.parametrize("keep_puzzles", [True, False])
async def test_deleting_a_round_refreshes_the_unassigned_tab_after_the_group_goes(
        keep_puzzles):
    """Kept members lose their feeds edge only when the tag_group row is deleted
    (the tags rows cascade off it), so the refresh must come AFTER that delete
    or it reconciles against the old membership and adds nothing."""
    from tests.conftest import make_round
    svc = _svc()
    hunt = make_hunt(category_id=500)
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=1)
    target = make_puzzle(id=1, is_target=True, channel_id=None, tags=[5])
    member = make_puzzle(id=2, channel_id=None, tags=[5])
    order = []

    async def refresh(h):
        order.append("refresh")

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch.object(svc, "_gsheet_cog", return_value=None), \
         patch.object(svc, "refresh_header_links", new=AsyncMock(return_value=[])), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)), \
         patch.object(svc, "refresh_unassigned_safe",
                      new=AsyncMock(side_effect=refresh)) as refresh_m:
        pdb.get_all_from_tag_group.return_value = [target, member]
        pdb.get_by_attr.return_value = None
        pdb.get_children.return_value = []
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []
        tgdb.delete.side_effect = lambda gid: order.append("group_deleted")
        await svc.delete_round(_round_guild(), hunt, tag_group,
                               keep_puzzles=keep_puzzles)
        import asyncio
        await asyncio.sleep(0)

    refresh_m.assert_awaited_once_with(hunt)
    assert order == ["group_deleted", "refresh"], order
