"""Milestone C — Phase 1 failing tests for the edge-aware service primitives.

These exercise the new ctx-free PuzzleService methods that replace the old
tag-based ``add_to_meta`` / ``move_to_meta`` / ``remove_from_meta`` / ``clear_metas``:

    tag_with_edge(puzzle, group, edge)
    tag_edge_only(puzzle, group, edge, guild=None)
    untag_from(puzzle, group, edge=None)
    bulk_re_edge(tag_group, new_edge, hunt) -> str

Pattern mirrors the mock-based service tests in ``tests/test_solve_equivalence.py``:
the three JsonDb classes are patched, sheet/board side-effects are stubbed on the
service, and we assert the in-memory PuzzleData mutations (``tags`` list +
``tag_edges`` dict) the rest of the codebase reads.

Phase 1 expectation: every test errors with ``AttributeError`` because the methods
do not exist yet. Phase 2 implements them and turns these green. Helpers are local
to this file (the parallel runner shards by file).
"""
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt, make_puzzle, make_round


# ---------------------------------------------------------------------------
# Helpers (local — do not import from sibling test files)
# ---------------------------------------------------------------------------

def _make_cog_c():
    from bot.cogs.puzzles import Puzzles

    bot = MagicMock()
    bot.get_cog.return_value = None  # no GoogleSheets cog
    return Puzzles(bot)


@contextlib.contextmanager
def _service_mocks(cog, groups_by_id=None):
    """Patch the JsonDb seams and stub the service's sheet/board side-effects.

    ``groups_by_id`` feeds ``RoundJsonDb.get_by_attr(id=...)`` so the methods can
    resolve the "other" groups they touch. Yields the (PuzzleJsonDb, RoundJsonDb,
    HuntJsonDb) mocks so a test can configure/inspect DB calls.
    """
    groups_by_id = groups_by_id or {}

    cog.service.refresh_target = AsyncMock()
    cog.service.refresh_targets = AsyncMock(return_value=[])
    cog.service.refresh_unassigned_safe = AsyncMock()
    cog.service.notifier = MagicMock()
    cog.service.notifier.board_changed = AsyncMock()

    def round_get(**kwargs):
        if "id" in kwargs:
            return groups_by_id.get(kwargs["id"])
        return None

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.RoundJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as mhdb:
        mpdb.commit.side_effect = lambda p: None
        mpdb.tag_puzzle_with_edge.return_value = None
        mpdb.delete_tag.return_value = None
        mpdb.bulk_update_edge_type.return_value = None
        mrdb.get_by_attr.side_effect = round_get
        mhdb.get_by_attr.return_value = make_hunt()
        yield mpdb, mrdb, mhdb


# ---------------------------------------------------------------------------
# tag_with_edge — upsert an edge, keep tags + tag_edges in sync
# ---------------------------------------------------------------------------

class TestTagWithEdge:
    @pytest.fixture
    def cog(self):
        return _make_cog_c()

    @pytest.mark.asyncio
    async def test_adds_group_to_tags(self, cog):
        puz = make_puzzle(tags=[])
        puz.tag_edges = {}
        grp = make_round(id=5)
        with _service_mocks(cog):
            await cog.service.tag_with_edge(puz, grp, "feeds")
        assert 5 in puz.tags

    @pytest.mark.asyncio
    async def test_sets_tag_edges_dict(self, cog):
        puz = make_puzzle(tags=[])
        puz.tag_edges = {}
        grp = make_round(id=5)
        with _service_mocks(cog):
            await cog.service.tag_with_edge(puz, grp, "feeds")
        assert puz.tag_edges[5] == "feeds"

    @pytest.mark.asyncio
    async def test_idempotent_edge_update(self, cog):
        puz = make_puzzle(tags=[])
        puz.tag_edges = {}
        grp = make_round(id=5)
        with _service_mocks(cog):
            await cog.service.tag_with_edge(puz, grp, "feeds")
            await cog.service.tag_with_edge(puz, grp, "informs")
        # Second call updates the edge in place; the group appears once in tags.
        assert puz.tag_edges[5] == "informs"
        assert puz.tags.count(5) == 1

    @pytest.mark.asyncio
    async def test_informs_edge(self, cog):
        puz = make_puzzle(tags=[])
        puz.tag_edges = {}
        grp = make_round(id=5)
        with _service_mocks(cog):
            await cog.service.tag_with_edge(puz, grp, "informs")
        assert puz.tag_edges[5] == "informs"
        assert 5 in puz.tags


# ---------------------------------------------------------------------------
# tag_edge_only — set the edge and clear other edges OF THE SAME TYPE
# ---------------------------------------------------------------------------

class TestTagEdgeOnly:
    @pytest.fixture
    def cog(self):
        return _make_cog_c()

    @pytest.mark.asyncio
    async def test_sets_target_edge(self, cog):
        puz = make_puzzle(tags=[])
        puz.tag_edges = {}
        grp = make_round(id=5)
        with _service_mocks(cog):
            await cog.service.tag_edge_only(puz, grp, "feeds", guild=None)
        assert puz.tag_edges[5] == "feeds"
        assert 5 in puz.tags

    @pytest.mark.asyncio
    async def test_removes_other_same_type_edges(self, cog):
        puz = make_puzzle(tags=[3, 5])
        puz.tag_edges = {3: "feeds", 5: "feeds"}
        grp = make_round(id=5)
        groups = {3: make_round(id=3), 5: grp}
        with _service_mocks(cog, groups_by_id=groups):
            await cog.service.tag_edge_only(puz, grp, "feeds", guild=None)
        assert 3 not in puz.tags      # the other feeds edge was swept
        assert 5 in puz.tags          # the target stays

    @pytest.mark.asyncio
    async def test_leaves_different_type_edges(self, cog):
        puz = make_puzzle(tags=[3, 4])
        puz.tag_edges = {3: "feeds", 4: "informs"}
        grp = make_round(id=5)
        groups = {3: make_round(id=3), 4: make_round(id=4), 5: grp}
        with _service_mocks(cog, groups_by_id=groups):
            await cog.service.tag_edge_only(puz, grp, "feeds", guild=None)
        assert 4 in puz.tags          # the informs edge is untouched

    @pytest.mark.asyncio
    async def test_no_channel_move_without_guild(self, cog):
        puz = make_puzzle(tags=[], channel_id=222)
        puz.tag_edges = {}
        grp = make_round(id=5)
        with _service_mocks(cog):
            # guild=None must not attempt any Discord channel relocation.
            await cog.service.tag_edge_only(puz, grp, "feeds", guild=None)
        assert puz.tag_edges[5] == "feeds"


# ---------------------------------------------------------------------------
# untag_from — delete the (puzzle, group) edge; optional type guard
# ---------------------------------------------------------------------------

class TestUntagFrom:
    @pytest.fixture
    def cog(self):
        return _make_cog_c()

    @pytest.mark.asyncio
    async def test_unconditional_remove(self, cog):
        puz = make_puzzle(tags=[5])
        puz.tag_edges = {5: "feeds"}
        grp = make_round(id=5)
        with _service_mocks(cog):
            await cog.service.untag_from(puz, grp)
        assert 5 not in puz.tags

    @pytest.mark.asyncio
    async def test_type_guarded_removes_matching(self, cog):
        puz = make_puzzle(tags=[5])
        puz.tag_edges = {5: "feeds"}
        grp = make_round(id=5)
        with _service_mocks(cog):
            await cog.service.untag_from(puz, grp, edge="feeds")
        assert 5 not in puz.tags

    @pytest.mark.asyncio
    async def test_type_guarded_noop_on_mismatch(self, cog):
        puz = make_puzzle(tags=[5])
        puz.tag_edges = {5: "informs"}
        grp = make_round(id=5)
        with _service_mocks(cog):
            await cog.service.untag_from(puz, grp, edge="feeds")
        assert 5 in puz.tags          # edge mismatch -> no-op

    @pytest.mark.asyncio
    async def test_leaves_other_tags(self, cog):
        puz = make_puzzle(tags=[3, 5])
        puz.tag_edges = {3: "feeds", 5: "feeds"}
        grp = make_round(id=5)
        with _service_mocks(cog):
            await cog.service.untag_from(puz, grp)
        assert 3 in puz.tags
        assert 5 not in puz.tags


# ---------------------------------------------------------------------------
# bulk_re_edge — flip every member's edge_type, return a sweep summary
# ---------------------------------------------------------------------------

class TestBulkReEdge:
    @pytest.fixture
    def cog(self):
        return _make_cog_c()

    def _members(self, group_id, feeds=0, none=0):
        """Members ordered feeds-then-none so the summary key order is stable."""
        members = []
        for _ in range(feeds):
            p = make_puzzle(tags=[group_id])
            p.tag_edges = {group_id: "feeds"}
            members.append(p)
        for _ in range(none):
            p = make_puzzle(tags=[group_id])
            p.tag_edges = {group_id: "none"}
            members.append(p)
        return members

    @pytest.mark.asyncio
    async def test_returns_summary_string_mixed(self, cog):
        grp = make_round(id=5)
        hunt = make_hunt()
        with _service_mocks(cog) as (mpdb, _mrdb, _mhdb):
            mpdb.get_all_from_tag_group.return_value = self._members(5, feeds=3, none=2)
            summary = await cog.service.bulk_re_edge(grp, "informs", hunt)
        assert summary == "flipped 3 feeds + 2 none → informs"

    @pytest.mark.asyncio
    async def test_returns_summary_single_type(self, cog):
        grp = make_round(id=5)
        hunt = make_hunt()
        with _service_mocks(cog) as (mpdb, _mrdb, _mhdb):
            mpdb.get_all_from_tag_group.return_value = self._members(5, feeds=7)
            summary = await cog.service.bulk_re_edge(grp, "informs", hunt)
        assert summary == "flipped 7 feeds → informs"

    @pytest.mark.asyncio
    async def test_empty_group(self, cog):
        grp = make_round(id=5)
        hunt = make_hunt()
        with _service_mocks(cog) as (mpdb, _mrdb, _mhdb):
            mpdb.get_all_from_tag_group.return_value = []
            summary = await cog.service.bulk_re_edge(grp, "informs", hunt)
        assert summary == "0 members"

    @pytest.mark.asyncio
    async def test_calls_bulk_update(self, cog):
        grp = make_round(id=5)
        hunt = make_hunt()
        with _service_mocks(cog) as (mpdb, _mrdb, _mhdb):
            mpdb.get_all_from_tag_group.return_value = self._members(5, feeds=2)
            await cog.service.bulk_re_edge(grp, "informs", hunt)
            mpdb.bulk_update_edge_type.assert_called_once_with(5, "informs")
