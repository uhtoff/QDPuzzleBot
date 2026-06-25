"""M7: parent_group_id subtree walk (collect_subtree_puzzles).

Pure tests with RoundJsonDb / PuzzleJsonDb mocked. The walk gathers descendants
that chain up to the root via parent_group_id, unions their puzzles deduped by
id, keeps solved only, and is cycle-safe (depth cap).
"""
from unittest.mock import MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from tests.conftest import make_puzzle, make_round


class TestSubtreeWalk:
    @pytest.mark.asyncio
    async def test_collects_descendants_deduped_and_solved_only(self):
        svc = PuzzleService(MagicMock())
        root = make_round(id=1, target_puzzle_id=10)
        a = make_round(id=2)
        a.parent_group_id = 1
        b = make_round(id=3)
        b.parent_group_id = 2          # grandchild
        unrelated = make_round(id=99)
        unrelated.parent_group_id = None
        p_solved = make_puzzle(id=50, solved=True, solution="S")
        p_unsolved = make_puzzle(id=51, solved=False)
        with patch("bot.core.puzzle_service.RoundJsonDb") as rdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb:
            rdb.get_all.return_value = [root, a, b, unrelated]
            # p_solved appears in BOTH a and b -> must be deduped to one row.
            pdb.get_all_from_round.side_effect = lambda rid: {
                1: [], 2: [p_solved, p_unsolved], 3: [p_solved]}.get(rid, [])
            puzzles = await svc.collect_subtree_puzzles(root)
        assert sorted(p.id for p in puzzles) == [50]   # solved-only, deduped

    @pytest.mark.asyncio
    async def test_depth_cap_guards_a_broken_chain(self):
        # A pre-existing cycle in parent_group_id must not hang; cap at 64.
        svc = PuzzleService(MagicMock())
        root = make_round(id=1)
        loop = make_round(id=2)
        loop.parent_group_id = 1
        root.parent_group_id = 2                       # 1<->2 cycle
        with patch("bot.core.puzzle_service.RoundJsonDb") as rdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb:
            rdb.get_all.return_value = [root, loop]
            pdb.get_all_from_round.return_value = []
            await svc.collect_subtree_puzzles(root)     # must return, not hang
