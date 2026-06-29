"""Runaround feeds-walk (collect_subtree_puzzles).

source_mode='subtree' collects every solved puzzle reachable by an unbroken chain
of 'feeds' edges up to the runaround group. Pure tests with TagGroupJsonDb /
PuzzleJsonDb mocked: TagGroupJsonDb.get_all yields the hunt's groups (for the
target->group map); PuzzleJsonDb.get_all_from_tag_group yields each group's members.

Only 'feeds' is followed — 'informs' (capstone evidence), 'none' (pooled grab-bag),
minimeta sub-answers (parent_puzzle_id children) and the group's own 'target'
self-edge puzzle never surface. A visited-groups set makes mutual meta-to-meta
feeding cycle-safe with no depth cap.
"""
from unittest.mock import MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from tests.conftest import make_puzzle, make_round


async def _walk(root, groups, members_by_group):
    """Run collect_subtree_puzzles with the DAOs mocked. `groups` is the hunt's
    group list; `members_by_group` maps group id -> [PuzzleData]."""
    svc = PuzzleService(MagicMock())
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb:
        rdb.get_all.return_value = groups
        pdb.get_all_from_tag_group.side_effect = lambda gid: members_by_group.get(gid, [])
        return await svc.collect_subtree_puzzles(root)


class TestFeedsWalk:
    @pytest.mark.asyncio
    async def test_includes_root_groups_own_feeders_not_its_target(self):
        # A feeder tagged 'feeds' directly INTO the runaround group surfaces; the
        # group's own 'target' self-edge puzzle does not.
        root = make_round(id=1, target_puzzle_id=10)
        target = make_puzzle(id=10, solved=True, solution="RUN")
        target.add_edge(1, "target")
        feeder = make_puzzle(id=60, solved=True, solution="META")
        feeder.add_edge(1, "feeds")
        puzzles = await _walk(root, [root], {1: [target, feeder]})
        assert sorted(p.id for p in puzzles) == [60]

    @pytest.mark.asyncio
    async def test_descends_feeds_chain_into_nested_metas_solved_only(self):
        # Feed a meta-meta into the runaround -> its answer AND "the ones below"
        # (its own feeders) come along, transitively, solved-only.
        root = make_round(id=1, target_puzzle_id=10)
        group2 = make_round(id=2, target_puzzle_id=60)   # the meta-meta's group
        target = make_puzzle(id=10, solved=True)
        target.add_edge(1, "target")
        mm = make_puzzle(id=60, solved=True, solution="MM")
        mm.add_edge(1, "feeds")                           # feeds the runaround
        mm.add_edge(2, "target")                          # is its own group's target
        below_ok = make_puzzle(id=70, solved=True, solution="A")
        below_ok.add_edge(2, "feeds")
        below_unsolved = make_puzzle(id=71, solved=False)
        below_unsolved.add_edge(2, "feeds")
        puzzles = await _walk(
            root, [root, group2],
            {1: [target, mm], 2: [mm, below_ok, below_unsolved]})
        assert sorted(p.id for p in puzzles) == [60, 70]  # mm + its solved feeder

    @pytest.mark.asyncio
    async def test_capstone_answer_surfaces_but_informs_evidence_and_pools_do_not(self):
        # A capstone fed into the runaround surfaces its own answer, but the walk
        # never descends its 'informs' evidence; a 'none' pooled member is excluded.
        root = make_round(id=1, target_puzzle_id=10)
        group3 = make_round(id=3, target_puzzle_id=80)   # the capstone's group
        target = make_puzzle(id=10, solved=True)
        target.add_edge(1, "target")
        capstone = make_puzzle(id=80, solved=True, solution="CAP")
        capstone.add_edge(1, "feeds")                    # fed up as an answer
        capstone.add_edge(3, "target")
        evidence = make_puzzle(id=90, solved=True, solution="CLUE")
        evidence.add_edge(3, "informs")                  # evidence, never followed
        pooled = make_puzzle(id=81, solved=True, solution="POOL")
        pooled.add_edge(1, "none")                       # grab-bag member
        puzzles = await _walk(
            root, [root, group3],
            {1: [target, capstone, pooled], 3: [capstone, evidence]})
        assert sorted(p.id for p in puzzles) == [80]

    @pytest.mark.asyncio
    async def test_minimeta_sub_answers_never_surface_even_if_mistagged(self):
        # The minimeta's own answer can feed the runaround, but its sub-answers
        # (parent_puzzle_id children) are skipped even if mistagged with 'feeds'.
        root = make_round(id=1, target_puzzle_id=10)
        target = make_puzzle(id=10, solved=True)
        target.add_edge(1, "target")
        minimeta = make_puzzle(id=100, solved=True, solution="MINI", puzzle_type="Minimeta")
        minimeta.add_edge(1, "feeds")
        mini = make_puzzle(id=101, solved=True, solution="SUB", parent_puzzle_id=100)
        mini.add_edge(1, "feeds")                         # mistagged — must still be skipped
        puzzles = await _walk(root, [root], {1: [target, minimeta, mini]})
        assert sorted(p.id for p in puzzles) == [100]

    @pytest.mark.asyncio
    async def test_mutual_meta_feeding_terminates_each_answer_once(self):
        # A 'feeds' cycle (A's group feeds B's, B's feeds A's) must not hang and
        # must collect each answer exactly once.
        root = make_round(id=1, target_puzzle_id=10)
        group2 = make_round(id=2, target_puzzle_id=20)
        group3 = make_round(id=3, target_puzzle_id=30)
        target = make_puzzle(id=10, solved=True)
        target.add_edge(1, "target")
        a = make_puzzle(id=20, solved=True, solution="A")
        a.add_edge(1, "feeds")
        a.add_edge(2, "target")
        a.add_edge(3, "feeds")                            # A also feeds B's group
        b = make_puzzle(id=30, solved=True, solution="B")
        b.add_edge(2, "feeds")                            # B feeds A's group
        b.add_edge(3, "target")
        puzzles = await _walk(
            root, [root, group2, group3],
            {1: [target, a], 2: [a, b], 3: [b, a]})
        assert sorted(p.id for p in puzzles) == [20, 30]
