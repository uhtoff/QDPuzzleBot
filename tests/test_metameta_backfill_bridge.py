"""M7: the puzzles.metameta flag gives way to rollup.source_mode.

After migration 004 a metameta round carries source_mode='subtree'. refresh_meta
must branch on source_mode (NOT on metapuzzle.metameta), walking the 'feeds' chain
(get_all + get_all_from_tag_group) rather than the old hunt-wide scan
(get_all_from_hunt).
"""
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from bot.store.rollup_data import RollupData
from tests.conftest import make_puzzle, make_round


def _hunt():
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "SHEET1"
    h.solved_sheet_id = "ARCH1"
    return h


def _svc_with_sheets():
    gsheet = MagicMock()
    gsheet.add_metapuzzle_data = AsyncMock()
    gsheet.add_metametapuzzle_data = AsyncMock()
    gsheet.render_contribution = AsyncMock()
    gsheet.move_puzzle_to_solved_sheet = AsyncMock()
    gsheet.stamp_rollup_metadata = AsyncMock(return_value=(None, None))
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return PuzzleService(bot), gsheet


@contextmanager
def _patched(rollup_existing=None):
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.RollupsJsonDb") as rudb:
        rudb.get_by_attr.return_value = rollup_existing
        yield pdb, hdb, rdb, rudb


class TestMetametaBridge:
    @pytest.mark.asyncio
    async def test_metameta_round_with_subtree_mode_uses_branch_not_hunt_scan(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, target_puzzle_id=1)
        metameta = make_puzzle(id=1, is_target=True)
        existing = RollupData(id=9, hunt_id=10, tag_group_id=5, source_mode="subtree")
        with _patched(rollup_existing=existing) as (pdb, _h, rdb, _ru):
            pdb.get_by_attr.return_value = metameta
            rdb.get_all.return_value = [meta_round]
            pdb.get_all_from_tag_group.return_value = []
            await svc.refresh_target(_hunt(), meta_round)
        pdb.get_all_from_hunt.assert_not_called()   # no hunt-wide scan post-M7
        gsheet.render_contribution.assert_awaited_once()
