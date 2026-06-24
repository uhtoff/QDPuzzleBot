"""Bang-vs-web equivalence tests for the solve and tag-mutation operations.

M3 will extract a ctx-free service layer; these tests document the expected
DB-state transitions so the extracted service can be verified to preserve them.

Strategy: call the web_* methods (already ctx-free) on a minimal Puzzles
instance with all DB writes mocked.  Assert the mutations to PuzzleData /
RoundData that a service layer must replicate.

Known divergences from _solve_impl (annotated below) are documented so they
are visible, not silently inherited into the service layer.
"""
import datetime
from unittest.mock import MagicMock, patch

import pytest

from bot.store.hunt_data import HuntData
from tests.conftest import make_puzzle, make_round


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cog():
    """Return a Puzzles cog with a minimal bot stub."""
    from bot.cogs.puzzles import Puzzles

    bot = MagicMock()
    bot.get_cog.return_value = None  # no GoogleSheets cog
    cog = Puzzles(bot)
    return cog


def _make_hunt(id=10, google_sheet_id="SHEET1", archive_google_sheet_id="ARCHIVE1"):
    h = HuntData()
    h.id = id
    h.google_sheet_id = google_sheet_id
    h.archive_google_sheet_id = archive_google_sheet_id
    return h


def _no_guild():
    """guild=None path — skips all Discord channel operations."""
    return None


# ---------------------------------------------------------------------------
# PuzzleService.solve — DB state mutations
# ---------------------------------------------------------------------------

class TestWebSolvePuzzleState:
    """These are the invariants a service-layer solve must preserve."""

    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run_solve(self, cog, hunt, puzzle, solution, rounds_by_id=None):
        rounds_by_id = rounds_by_id or {}
        commits = []

        def fake_round_get(**kwargs):
            return rounds_by_id.get(list(kwargs.values())[0])

        with patch("bot.cogs.puzzles.PuzzleJsonDb") as mock_pdb, \
             patch("bot.cogs.puzzles.RoundJsonDb") as mock_rdb, \
             patch("bot.cogs.puzzles.HuntJsonDb") as mock_hdb, \
             patch("bot.cogs.puzzles.GuildSettingsDb") as mock_gsdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb", mock_pdb), \
             patch("bot.core.puzzle_service.RoundJsonDb", mock_rdb), \
             patch("bot.core.puzzle_service.HuntJsonDb", mock_hdb), \
             patch("bot.core.puzzle_service.GuildSettingsDb", mock_gsdb):
            mock_pdb.commit.side_effect = lambda p: commits.append(("puzzle", p.id))
            mock_rdb.get_by_attr.side_effect = fake_round_get
            mock_rdb.commit.side_effect = lambda r: commits.append(("round", r.id))
            mock_hdb.get_by_attr.return_value = hunt
            mock_gsdb.get.return_value = None

            await cog.service.solve(_no_guild(), hunt, puzzle, solution)

        return commits

    @pytest.mark.asyncio
    async def test_sets_solved_true(self, cog, hunt):
        puz = make_puzzle(solved=False)
        await self._run_solve(cog, hunt, puz, "ANSWER")
        assert puz.solved is True

    @pytest.mark.asyncio
    async def test_sets_status_solved(self, cog, hunt):
        puz = make_puzzle(status="in progress")
        await self._run_solve(cog, hunt, puz, "ANSWER")
        assert puz.status == "Solved"

    @pytest.mark.asyncio
    async def test_sets_solution(self, cog, hunt):
        puz = make_puzzle(solution=None)
        await self._run_solve(cog, hunt, puz, "MYSOLUTION")
        assert puz.solution == "MYSOLUTION"

    @pytest.mark.asyncio
    async def test_appends_to_existing_solution(self, cog, hunt):
        puz = make_puzzle(solution="FIRST")
        await self._run_solve(cog, hunt, puz, "SECOND")
        assert puz.solution == "FIRST/SECOND"

    @pytest.mark.asyncio
    async def test_empty_solution_no_existing_is_rejected(self, cog, hunt):
        # Canonical M3 behaviour: empty solution with no existing solution is a
        # no-op (adopts _solve_impl's guard); the puzzle is NOT marked solved.
        puz = make_puzzle(solution=None, solved=False)
        await self._run_solve(cog, hunt, puz, "")
        assert puz.solution is None
        assert puz.solved is False

    @pytest.mark.asyncio
    async def test_solve_time_set(self, cog, hunt):
        before = datetime.datetime.now(tz=datetime.timezone.utc)
        puz = make_puzzle()
        await self._run_solve(cog, hunt, puz, "X")
        assert puz.solve_time is not None
        assert puz.solve_time >= before

    @pytest.mark.asyncio
    async def test_puzzle_committed(self, cog, hunt):
        puz = make_puzzle(id=42)
        commits = await self._run_solve(cog, hunt, puz, "X")
        puzzle_commits = [c for c in commits if c[0] == "puzzle" and c[1] == 42]
        assert len(puzzle_commits) >= 1

    @pytest.mark.asyncio
    async def test_round_solve_time_set_when_puzzle_is_meta(self, cog, hunt):
        """When the solved puzzle is the meta of its round, round.solve_time is stamped."""
        rnd = make_round(id=5, meta_id=1)  # puzzle 1 is the meta of this round
        puz = make_puzzle(id=1, tags=[5])

        commits = await self._run_solve(cog, hunt, puz, "META", rounds_by_id={5: rnd})

        assert rnd.solve_time is not None
        round_commits = [c for c in commits if c[0] == "round" and c[1] == 5]
        assert len(round_commits) >= 1

    @pytest.mark.asyncio
    async def test_round_solve_time_not_set_for_feeder(self, cog, hunt):
        """A feeder puzzle's round does NOT get a solve_time."""
        rnd = make_round(id=5, meta_id=99)  # meta_id ≠ puzzle id
        puz = make_puzzle(id=1, tags=[5])

        await self._run_solve(cog, hunt, puz, "FEEDER", rounds_by_id={5: rnd})

        assert rnd.solve_time is None

    @pytest.mark.asyncio
    async def test_no_tags_no_round_interaction(self, cog, hunt):
        puz = make_puzzle(id=1, tags=[])
        commits = await self._run_solve(cog, hunt, puz, "SOLO")
        round_commits = [c for c in commits if c[0] == "round"]
        assert round_commits == []


# ---------------------------------------------------------------------------
# add_to_meta (service) — tag mutation
# ---------------------------------------------------------------------------

class TestWebAddToMeta:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    async def _run(self, cog, puzzle, round_data):
        hunt = _make_hunt()
        with patch("bot.cogs.puzzles.PuzzleJsonDb") as mock_pdb, \
             patch("bot.cogs.puzzles.RoundJsonDb") as mock_rdb, \
             patch("bot.cogs.puzzles.HuntJsonDb") as mock_hdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb", mock_pdb), \
             patch("bot.core.puzzle_service.RoundJsonDb", mock_rdb), \
             patch("bot.core.puzzle_service.HuntJsonDb", mock_hdb):
            mock_pdb.commit.side_effect = lambda p: None
            mock_rdb.get_by_attr.return_value = round_data
            mock_hdb.get_by_attr.return_value = hunt
            await cog.service.add_to_meta(puzzle, round_data)

    @pytest.mark.asyncio
    async def test_adds_round_to_tags(self, cog):
        puz = make_puzzle(tags=[])
        rnd = make_round(id=5)
        await self._run(cog, puz, rnd)
        assert 5 in puz.tags

    @pytest.mark.asyncio
    async def test_idempotent_duplicate_tag(self, cog):
        puz = make_puzzle(tags=[5])
        rnd = make_round(id=5)
        await self._run(cog, puz, rnd)
        assert puz.tags.count(5) == 1

    @pytest.mark.asyncio
    async def test_does_not_disturb_other_tags(self, cog):
        puz = make_puzzle(tags=[3, 4])
        rnd = make_round(id=5)
        await self._run(cog, puz, rnd)
        assert 3 in puz.tags
        assert 4 in puz.tags
        assert 5 in puz.tags


# ---------------------------------------------------------------------------
# remove_from_meta (service) — tag mutation
# ---------------------------------------------------------------------------

class TestWebRemoveFromMeta:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    async def _run(self, cog, puzzle, round_data):
        hunt = _make_hunt()
        with patch("bot.cogs.puzzles.PuzzleJsonDb") as mock_pdb, \
             patch("bot.cogs.puzzles.RoundJsonDb") as mock_rdb, \
             patch("bot.cogs.puzzles.HuntJsonDb") as mock_hdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb", mock_pdb), \
             patch("bot.core.puzzle_service.RoundJsonDb", mock_rdb), \
             patch("bot.core.puzzle_service.HuntJsonDb", mock_hdb):
            mock_pdb.commit.side_effect = lambda p: None
            mock_rdb.get_by_attr.return_value = round_data
            mock_hdb.get_by_attr.return_value = hunt
            await cog.service.remove_from_meta(puzzle, round_data)

    @pytest.mark.asyncio
    async def test_removes_round_from_tags(self, cog):
        puz = make_puzzle(tags=[5])
        rnd = make_round(id=5)
        await self._run(cog, puz, rnd)
        assert 5 not in puz.tags

    @pytest.mark.asyncio
    async def test_idempotent_missing_tag(self, cog):
        puz = make_puzzle(tags=[3])
        rnd = make_round(id=5)
        await self._run(cog, puz, rnd)  # should not raise
        assert 5 not in puz.tags
        assert 3 in puz.tags

    @pytest.mark.asyncio
    async def test_does_not_disturb_other_tags(self, cog):
        puz = make_puzzle(tags=[3, 4, 5])
        rnd = make_round(id=5)
        await self._run(cog, puz, rnd)
        assert 3 in puz.tags
        assert 4 in puz.tags
        assert 5 not in puz.tags


# ---------------------------------------------------------------------------
# move_to_meta (service) — tag replacement (non-metapuzzle)
# ---------------------------------------------------------------------------

class TestWebMoveToMeta:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    async def _run(self, cog, puzzle, new_round, old_rounds_by_id=None):
        hunt = _make_hunt()
        old_rounds_by_id = old_rounds_by_id or {}

        def fake_round_get(**kwargs):
            return old_rounds_by_id.get(list(kwargs.values())[0])

        with patch("bot.cogs.puzzles.PuzzleJsonDb") as mock_pdb, \
             patch("bot.cogs.puzzles.RoundJsonDb") as mock_rdb, \
             patch("bot.cogs.puzzles.HuntJsonDb") as mock_hdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb", mock_pdb), \
             patch("bot.core.puzzle_service.RoundJsonDb", mock_rdb), \
             patch("bot.core.puzzle_service.HuntJsonDb", mock_hdb):
            mock_pdb.commit.side_effect = lambda p: None
            mock_rdb.get_by_attr.side_effect = fake_round_get
            mock_hdb.get_by_attr.return_value = hunt
            await cog.service.move_to_meta(None, puzzle, new_round)  # guild=None

    @pytest.mark.asyncio
    async def test_feeder_replaces_all_tags_with_new_round(self, cog):
        puz = make_puzzle(tags=[3, 4], metapuzzle=0)
        new_rnd = make_round(id=7)
        await self._run(cog, puz, new_rnd, old_rounds_by_id={3: make_round(id=3), 4: make_round(id=4)})
        assert puz.tags == [7]

    @pytest.mark.asyncio
    async def test_metapuzzle_keeps_own_tags(self, cog):
        # A metapuzzle's own tags are NOT cleared (it would orphan the round).
        puz = make_puzzle(id=10, tags=[3, 5], metapuzzle=1)
        new_rnd = make_round(id=7)
        await self._run(cog, puz, new_rnd, old_rounds_by_id={3: make_round(id=3), 5: make_round(id=5)})
        # tags are NOT cleared for metas; new round is appended
        assert 7 in puz.tags
        assert 3 in puz.tags or 5 in puz.tags  # at least some old tags remain


# ---------------------------------------------------------------------------
# add_partial (service) — partial-solution mutation. Mirrors _partial_impl:
# status -> "Partially Solved", append "/"+solution if a solution exists else set;
# commit; refresh tagged metas. Does NOT set solve_time (divergence from solve).
# ---------------------------------------------------------------------------
class TestWebAddPartial:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, puzzle, solution, rounds_by_id=None):
        rounds_by_id = rounds_by_id or {}
        commits = []

        def fake_round_get(**kwargs):
            return rounds_by_id.get(list(kwargs.values())[0])

        with patch("bot.cogs.puzzles.PuzzleJsonDb") as mock_pdb, \
             patch("bot.cogs.puzzles.RoundJsonDb") as mock_rdb, \
             patch("bot.cogs.puzzles.HuntJsonDb") as mock_hdb, \
             patch("bot.cogs.puzzles.GuildSettingsDb") as mock_gsdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb", mock_pdb), \
             patch("bot.core.puzzle_service.RoundJsonDb", mock_rdb), \
             patch("bot.core.puzzle_service.HuntJsonDb", mock_hdb), \
             patch("bot.core.puzzle_service.GuildSettingsDb", mock_gsdb):
            mock_pdb.commit.side_effect = lambda p: commits.append(("puzzle", p.id))
            mock_rdb.get_by_attr.side_effect = fake_round_get
            mock_rdb.commit.side_effect = lambda r: commits.append(("round", r.id))
            mock_hdb.get_by_attr.return_value = hunt
            mock_gsdb.get.return_value = None

            await cog.service.add_partial(_no_guild(), hunt, puzzle, solution)

        return commits

    @pytest.mark.asyncio
    async def test_sets_status_partially_solved(self, cog, hunt):
        puz = make_puzzle(status="in progress", solution=None)
        await self._run(cog, hunt, puz, "PART")
        assert puz.status == "Partially Solved"

    @pytest.mark.asyncio
    async def test_sets_solution_when_empty(self, cog, hunt):
        puz = make_puzzle(solution=None)
        await self._run(cog, hunt, puz, "PART")
        assert puz.solution == "PART"

    @pytest.mark.asyncio
    async def test_appends_with_slash_when_existing(self, cog, hunt):
        puz = make_puzzle(solution="FIRST")
        await self._run(cog, hunt, puz, "SECOND")
        assert puz.solution == "FIRST/SECOND"

    @pytest.mark.asyncio
    async def test_does_not_set_solve_time(self, cog, hunt):
        # Divergence from solve: partial leaves solve_time untouched.
        puz = make_puzzle(solution=None)
        await self._run(cog, hunt, puz, "PART")
        assert puz.solve_time is None

    @pytest.mark.asyncio
    async def test_does_not_mark_solved(self, cog, hunt):
        puz = make_puzzle(solved=False, solution=None)
        await self._run(cog, hunt, puz, "PART")
        assert puz.solved is False


# ---------------------------------------------------------------------------
# change_solution (service) — already-solved branch of cog change_solution:
# set solution = new (replace), stamp solve_time, commit, refresh metas.
# (The unsolved fallback stays in the cog and is NOT this method's job.)
# ---------------------------------------------------------------------------
class TestWebChangeSolution:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, puzzle, solution, rounds_by_id=None):
        rounds_by_id = rounds_by_id or {}
        commits = []

        def fake_round_get(**kwargs):
            return rounds_by_id.get(list(kwargs.values())[0])

        with patch("bot.cogs.puzzles.PuzzleJsonDb") as mock_pdb, \
             patch("bot.cogs.puzzles.RoundJsonDb") as mock_rdb, \
             patch("bot.cogs.puzzles.HuntJsonDb") as mock_hdb, \
             patch("bot.cogs.puzzles.GuildSettingsDb") as mock_gsdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb", mock_pdb), \
             patch("bot.core.puzzle_service.RoundJsonDb", mock_rdb), \
             patch("bot.core.puzzle_service.HuntJsonDb", mock_hdb), \
             patch("bot.core.puzzle_service.GuildSettingsDb", mock_gsdb):
            mock_pdb.commit.side_effect = lambda p: commits.append(("puzzle", p.id))
            mock_rdb.get_by_attr.side_effect = fake_round_get
            mock_rdb.commit.side_effect = lambda r: commits.append(("round", r.id))
            mock_hdb.get_by_attr.return_value = hunt
            mock_gsdb.get.return_value = None

            await cog.service.change_solution(_no_guild(), hunt, puzzle, solution)

        return commits

    @pytest.mark.asyncio
    async def test_replaces_solution(self, cog, hunt):
        puz = make_puzzle(solved=True, solution="OLD")
        await self._run(cog, hunt, puz, "NEW")
        assert puz.solution == "NEW"

    @pytest.mark.asyncio
    async def test_stamps_solve_time(self, cog, hunt):
        before = datetime.datetime.now(tz=datetime.timezone.utc)
        puz = make_puzzle(solved=True, solution="OLD")
        await self._run(cog, hunt, puz, "NEW")
        assert puz.solve_time is not None and puz.solve_time >= before

    @pytest.mark.asyncio
    async def test_commits_puzzle(self, cog, hunt):
        puz = make_puzzle(id=42, solved=True, solution="OLD")
        commits = await self._run(cog, hunt, puz, "NEW")
        assert any(c[0] == "puzzle" and c[1] == 42 for c in commits)


# ---------------------------------------------------------------------------
# add_solution (service) — already-solved branch of cog add_solution:
# append "/"+solution, stamp solve_time, commit, refresh metas.
# ---------------------------------------------------------------------------
class TestWebAddSolution:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, puzzle, solution, rounds_by_id=None):
        rounds_by_id = rounds_by_id or {}
        commits = []

        def fake_round_get(**kwargs):
            return rounds_by_id.get(list(kwargs.values())[0])

        with patch("bot.cogs.puzzles.PuzzleJsonDb") as mock_pdb, \
             patch("bot.cogs.puzzles.RoundJsonDb") as mock_rdb, \
             patch("bot.cogs.puzzles.HuntJsonDb") as mock_hdb, \
             patch("bot.cogs.puzzles.GuildSettingsDb") as mock_gsdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb", mock_pdb), \
             patch("bot.core.puzzle_service.RoundJsonDb", mock_rdb), \
             patch("bot.core.puzzle_service.HuntJsonDb", mock_hdb), \
             patch("bot.core.puzzle_service.GuildSettingsDb", mock_gsdb):
            mock_pdb.commit.side_effect = lambda p: commits.append(("puzzle", p.id))
            mock_rdb.get_by_attr.side_effect = fake_round_get
            mock_rdb.commit.side_effect = lambda r: commits.append(("round", r.id))
            mock_hdb.get_by_attr.return_value = hunt
            mock_gsdb.get.return_value = None

            await cog.service.add_solution(_no_guild(), hunt, puzzle, solution)

        return commits

    @pytest.mark.asyncio
    async def test_appends_with_slash(self, cog, hunt):
        puz = make_puzzle(solved=True, solution="FIRST")
        await self._run(cog, hunt, puz, "SECOND")
        assert puz.solution == "FIRST/SECOND"

    @pytest.mark.asyncio
    async def test_stamps_solve_time(self, cog, hunt):
        before = datetime.datetime.now(tz=datetime.timezone.utc)
        puz = make_puzzle(solved=True, solution="FIRST")
        await self._run(cog, hunt, puz, "SECOND")
        assert puz.solve_time is not None and puz.solve_time >= before


# ---------------------------------------------------------------------------
# convert_to_feeder (service) — demote a metapuzzle back to a feeder. Mirrors
# the cog convert_to_feeder body: find the round whose meta_id is this puzzle,
# delete it (orphaning its feeders), drop the round from tags, clear metapuzzle,
# commit, revert the spreadsheet. Returns the count of feeders orphaned by the
# round deletion (every puzzle tagged to the round, excluding the meta itself).
# ---------------------------------------------------------------------------
class TestWebConvertToFeeder:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, puzzle, meta_round=None, round_members=None):
        """round_members = the puzzles get_all_from_round returns for the meta's
        round (feeders + the meta itself)."""
        commits = []
        deletes = []

        def fake_round_get(**kwargs):
            # convert_to_feeder looks the round up by meta_id=puzzle.id
            return meta_round

        with patch("bot.cogs.puzzles.PuzzleJsonDb") as mock_pdb, \
             patch("bot.cogs.puzzles.RoundJsonDb") as mock_rdb, \
             patch("bot.cogs.puzzles.HuntJsonDb") as mock_hdb, \
             patch("bot.cogs.puzzles.GuildSettingsDb") as mock_gsdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb", mock_pdb), \
             patch("bot.core.puzzle_service.RoundJsonDb", mock_rdb), \
             patch("bot.core.puzzle_service.HuntJsonDb", mock_hdb), \
             patch("bot.core.puzzle_service.GuildSettingsDb", mock_gsdb):
            mock_pdb.commit.side_effect = lambda p: commits.append(("puzzle", p.id))
            mock_pdb.get_all_from_round.return_value = round_members or []
            mock_rdb.get_by_attr.side_effect = fake_round_get
            mock_rdb.delete.side_effect = lambda rid: deletes.append(rid)
            mock_hdb.get_by_attr.return_value = hunt
            mock_gsdb.get.return_value = None

            orphaned = await cog.service.convert_to_feeder(_no_guild(), hunt, puzzle)

        return orphaned, commits, deletes

    @pytest.mark.asyncio
    async def test_clears_metapuzzle_flag(self, cog, hunt):
        puz = make_puzzle(id=1, metapuzzle=1, tags=[5])
        rnd = make_round(id=5, meta_id=1)
        await self._run(cog, hunt, puz, meta_round=rnd, round_members=[puz])
        assert not puz.metapuzzle

    @pytest.mark.asyncio
    async def test_deletes_meta_round(self, cog, hunt):
        puz = make_puzzle(id=1, metapuzzle=1, tags=[5])
        rnd = make_round(id=5, meta_id=1)
        _, _, deletes = await self._run(cog, hunt, puz, meta_round=rnd, round_members=[puz])
        assert deletes == [5]

    @pytest.mark.asyncio
    async def test_removes_round_from_tags(self, cog, hunt):
        puz = make_puzzle(id=1, metapuzzle=1, tags=[5])
        rnd = make_round(id=5, meta_id=1)
        await self._run(cog, hunt, puz, meta_round=rnd, round_members=[puz])
        assert 5 not in puz.tags

    @pytest.mark.asyncio
    async def test_commits_puzzle(self, cog, hunt):
        puz = make_puzzle(id=42, metapuzzle=1, tags=[5])
        rnd = make_round(id=5, meta_id=42)
        _, commits, _ = await self._run(cog, hunt, puz, meta_round=rnd, round_members=[puz])
        assert any(c[0] == "puzzle" and c[1] == 42 for c in commits)

    @pytest.mark.asyncio
    async def test_returns_orphan_count_excluding_meta(self, cog, hunt):
        # Round 5 holds the meta (id 1) plus two feeders -> 2 orphaned feeders.
        puz = make_puzzle(id=1, metapuzzle=1, tags=[5])
        feeder_a = make_puzzle(id=2, tags=[5])
        feeder_b = make_puzzle(id=3, tags=[5])
        rnd = make_round(id=5, meta_id=1)
        orphaned, _, _ = await self._run(
            cog, hunt, puz, meta_round=rnd, round_members=[puz, feeder_a, feeder_b]
        )
        assert orphaned == 2

    @pytest.mark.asyncio
    async def test_no_round_clears_flag_without_delete(self, cog, hunt):
        # Defensive: a metapuzzle with no backing round still demotes cleanly.
        puz = make_puzzle(id=1, metapuzzle=1, tags=[])
        orphaned, _, deletes = await self._run(cog, hunt, puz, meta_round=None)
        assert not puz.metapuzzle
        assert deletes == []
        assert orphaned == 0

    def test_preview_lists_orphan_feeders(self, cog):
        # Read-only helper that powers the confirm dialog: feeders minus the meta.
        puz = make_puzzle(id=1, metapuzzle=1, tags=[5])
        feeder_a = make_puzzle(id=2, tags=[5])
        feeder_b = make_puzzle(id=3, tags=[5])
        rnd = make_round(id=5, meta_id=1)
        with patch("bot.core.puzzle_service.RoundJsonDb") as mock_rdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb") as mock_pdb:
            mock_rdb.get_by_attr.return_value = rnd
            mock_pdb.get_all_from_round.return_value = [puz, feeder_a, feeder_b]
            orphans = cog.service.feeders_orphaned_by_convert(puz)
        assert [p.id for p in orphans] == [2, 3]

    def test_preview_empty_when_no_round(self, cog):
        puz = make_puzzle(id=1, metapuzzle=1, tags=[])
        with patch("bot.core.puzzle_service.RoundJsonDb") as mock_rdb:
            mock_rdb.get_by_attr.return_value = None
            assert cog.service.feeders_orphaned_by_convert(puz) == []
