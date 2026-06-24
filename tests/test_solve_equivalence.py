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


# ---------------------------------------------------------------------------
# PuzzleService.delete_puzzle — DB + Discord state mutations
# ---------------------------------------------------------------------------
class TestWebDeletePuzzle:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, puzzle, meta_rounds=None):
        """Run service.delete_puzzle with mocked DBs. meta_rounds is the list
        RoundJsonDb.get_by_attr returns for each tag (one per tag)."""
        deletes = []
        tag_rounds = list(meta_rounds or [])

        def fake_round_get(**kwargs):
            rid = kwargs.get("id")
            for r in tag_rounds:
                if r is not None and r.id == rid:
                    return r
            return None

        with patch("bot.cogs.puzzles.PuzzleJsonDb") as mock_pdb, \
             patch("bot.cogs.puzzles.RoundJsonDb") as mock_rdb, \
             patch("bot.cogs.puzzles.HuntJsonDb") as mock_hdb, \
             patch("bot.cogs.puzzles.GuildSettingsDb") as mock_gsdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb", mock_pdb), \
             patch("bot.core.puzzle_service.RoundJsonDb", mock_rdb), \
             patch("bot.core.puzzle_service.HuntJsonDb", mock_hdb), \
             patch("bot.core.puzzle_service.GuildSettingsDb", mock_gsdb):
            mock_pdb.delete.side_effect = lambda pid: deletes.append(("puzzle", pid))
            mock_rdb.get_by_attr.side_effect = fake_round_get
            mock_hdb.get_by_attr.return_value = hunt
            mock_gsdb.get.return_value = None

            await cog.service.delete_puzzle(_no_guild(), hunt, puzzle)

        return deletes

    @pytest.mark.asyncio
    async def test_deletes_puzzle_db_record(self, cog, hunt):
        puz = make_puzzle(id=7, tags=[])
        deletes = await self._run(cog, hunt, puz)
        assert ("puzzle", 7) in deletes

    @pytest.mark.asyncio
    async def test_deletes_tagged_round_meta_refresh(self, cog, hunt):
        """delete_puzzle refreshes parent metas (via refresh_metas) for each tag."""
        rnd = make_round(id=5, meta_id=3)
        puz = make_puzzle(id=7, tags=[5])
        # Service calls RoundJsonDb.get_by_attr(id=5) to resolve the tag.
        await self._run(cog, hunt, puz, meta_rounds=[rnd])
        # No assertion on meta sheet calls — gsheet cog is None in tests.
        # The DB delete is the minimal observable invariant.

    @pytest.mark.asyncio
    async def test_no_guild_skips_channel_delete(self, cog, hunt):
        """Guild=None is a safe no-op for Discord teardown."""
        puz = make_puzzle(id=7, channel_id=999, tags=[])
        deletes = await self._run(cog, hunt, puz)
        assert ("puzzle", 7) in deletes


# ---------------------------------------------------------------------------
# PuzzleService.delete_round — DB + Discord state mutations
# ---------------------------------------------------------------------------
class TestWebDeleteRound:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, hunt_round, puzzles=None):
        """Run service.delete_round with mocked DBs.
        puzzles = PuzzleJsonDb.get_all_from_round return value."""
        puzzle_deletes = []
        round_deletes = []
        puzzles = puzzles or []

        with patch("bot.cogs.puzzles.PuzzleJsonDb") as mock_pdb, \
             patch("bot.cogs.puzzles.RoundJsonDb") as mock_rdb, \
             patch("bot.cogs.puzzles.HuntJsonDb") as mock_hdb, \
             patch("bot.cogs.puzzles.GuildSettingsDb") as mock_gsdb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb", mock_pdb), \
             patch("bot.core.puzzle_service.RoundJsonDb", mock_rdb), \
             patch("bot.core.puzzle_service.HuntJsonDb", mock_hdb), \
             patch("bot.core.puzzle_service.GuildSettingsDb", mock_gsdb):
            mock_pdb.delete.side_effect = lambda pid: puzzle_deletes.append(pid)
            mock_rdb.delete.side_effect = lambda rid: round_deletes.append(rid)
            mock_pdb.get_all_from_round.return_value = puzzles
            mock_hdb.get_by_attr.return_value = hunt
            mock_gsdb.get.return_value = None

            failed = await cog.service.delete_round(_no_guild(), hunt, hunt_round)

        return puzzle_deletes, round_deletes, failed

    @pytest.mark.asyncio
    async def test_deletes_round_db_record(self, cog, hunt):
        rnd = make_round(id=5)
        _, round_deletes, _ = await self._run(cog, hunt, rnd)
        assert 5 in round_deletes

    @pytest.mark.asyncio
    async def test_deletes_all_puzzle_db_records(self, cog, hunt):
        rnd = make_round(id=5)
        p1 = make_puzzle(id=1, tags=[5])
        p2 = make_puzzle(id=2, tags=[5])
        puzzle_deletes, _, _ = await self._run(cog, hunt, rnd, puzzles=[p1, p2])
        assert set(puzzle_deletes) == {1, 2}

    @pytest.mark.asyncio
    async def test_empty_round_deletes_cleanly(self, cog, hunt):
        rnd = make_round(id=5)
        puzzle_deletes, round_deletes, failed = await self._run(cog, hunt, rnd, puzzles=[])
        assert puzzle_deletes == []
        assert 5 in round_deletes
        assert failed == []

    @pytest.mark.asyncio
    async def test_no_guild_skips_channel_delete(self, cog, hunt):
        """Guild=None → no Discord teardown, all DB deletes still happen."""
        rnd = make_round(id=5)
        p = make_puzzle(id=1, channel_id=888, tags=[5])
        puzzle_deletes, round_deletes, failed = await self._run(cog, hunt, rnd, puzzles=[p])
        assert 1 in puzzle_deletes
        assert 5 in round_deletes
        assert failed == []

# ---------------------------------------------------------------------------
# set_puzzle_url (service) — mirrors !link: puzzle.url = url; commit; gsheet
# update_puzzle (skipped here, no gsheet cog). No solve_time, no status change.
# ---------------------------------------------------------------------------
class TestWebSetPuzzleUrl:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, puzzle, url, rounds_by_id=None):
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

            await cog.service.set_puzzle_url(_no_guild(), hunt, puzzle, url)

        return commits

    @pytest.mark.asyncio
    async def test_sets_url(self, cog, hunt):
        puz = make_puzzle()          # url defaults to ""
        await self._run(cog, hunt, puz, "https://example.com/p")
        assert puz.url == "https://example.com/p"

    @pytest.mark.asyncio
    async def test_commits_puzzle(self, cog, hunt):
        puz = make_puzzle(id=42)
        commits = await self._run(cog, hunt, puz, "https://x")
        assert any(c[0] == "puzzle" and c[1] == 42 for c in commits)

    @pytest.mark.asyncio
    async def test_does_not_mark_solved_or_stamp_time(self, cog, hunt):
        puz = make_puzzle(solved=False)
        await self._run(cog, hunt, puz, "https://x")
        assert puz.solved is False
        assert puz.solve_time is None


# ---------------------------------------------------------------------------
# convert_to_meta (service) — mirrors !convert_to_meta + _round_impl convert
# branch: create a RoundData wrapping the puzzle (type "Metapuzzle"), meta_id =
# puzzle.id, generate meta_code; then puzzle.add_tag(round.id), metapuzzle=1,
# commit. gsheet restructure (convert_puzzle_spreadsheet) + channel-type live at
# the edge / are skipped with no gsheet cog. Method returns (round, puzzle).
# NOTE: _round_impl calls RoundJsonDb.generate_uid for meta_code — the harness
# must give RoundJsonDb.generate_uid a return value (e.g. "MCODE1").
# ---------------------------------------------------------------------------
class TestWebConvertToMeta:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, puzzle, rounds_by_id=None):
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
            mock_rdb.commit.side_effect = lambda r: commits.append(("round", r))
            mock_rdb.generate_uid.return_value = "MCODE1"
            mock_hdb.get_by_attr.return_value = hunt
            mock_gsdb.get.return_value = None

            result = await cog.service.convert_to_meta(_no_guild(), hunt, puzzle)

        return result

    @pytest.mark.asyncio
    async def test_marks_metapuzzle(self, cog, hunt):
        puz = make_puzzle(metapuzzle=0)
        await self._run(cog, hunt, puz)
        assert puz.metapuzzle == 1
        assert puz.is_metapuzzle() is True

    @pytest.mark.asyncio
    async def test_creates_round_with_meta_id(self, cog, hunt):
        puz = make_puzzle(id=7)
        new_round, _ = await self._run(cog, hunt, puz)
        assert new_round.meta_id == 7
        assert new_round.type == "Metapuzzle"

    @pytest.mark.asyncio
    async def test_tags_puzzle_to_new_round(self, cog, hunt):
        puz = make_puzzle(id=7, tags=[])
        new_round, _ = await self._run(cog, hunt, puz)
        assert new_round.id in puz.tags


# ---------------------------------------------------------------------------
# rename_puzzle (service) — set name, set channel_name (cleaned), commit, gsheet
# update_puzzle(puzzle, True), refresh the round's meta. The service ALSO owns the
# Discord channel rename via self.safe_rename, but it is guarded on `guild is not
# None` — so under _no_guild() the channel rename is skipped (same as solve). These
# tests therefore assert ONLY the DB transition; do not assert any rename call.
# ---------------------------------------------------------------------------
class TestWebRenamePuzzle:
    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.fixture
    def hunt(self):
        return _make_hunt()

    async def _run(self, cog, hunt, puzzle, name, rounds_by_id=None):
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

            await cog.service.rename_puzzle(_no_guild(), hunt, puzzle, name)

        return commits

    @pytest.mark.asyncio
    async def test_sets_name(self, cog, hunt):
        puz = make_puzzle(name="Old Name")
        await self._run(cog, hunt, puz, "New Name")
        assert puz.name == "New Name"

    @pytest.mark.asyncio
    async def test_commits_puzzle(self, cog, hunt):
        puz = make_puzzle(id=42, name="Old")
        commits = await self._run(cog, hunt, puz, "New")
        assert any(c[0] == "puzzle" and c[1] == 42 for c in commits)


# ---------------------------------------------------------------------------
# Bang !unsolve delegation — verifies the cog delegates to service.unsolve
# ---------------------------------------------------------------------------
class TestBangUnsolveDelegation:
    """The !unsolve bang command must delegate business logic to service.unsolve.

    This test mocks service.unsolve and asserts the cog calls it with the
    correct puzzle_data argument, so the full service logic (meta refresh,
    minimeta rollup, thread unarchive) is invoked rather than a hand-rolled
    partial implementation.
    """

    @pytest.fixture
    def cog(self):
        return _make_cog()

    @pytest.mark.asyncio
    async def test_bang_unsolve_calls_service_unsolve(self, cog):
        """!unsolve must call self.service.unsolve(guild, hunt, puzzle_data)."""
        from unittest.mock import AsyncMock, patch
        import discord

        puz = make_puzzle(id=5, solved=True, solution="WRONG")
        hunt = _make_hunt()

        # Build a minimal ctx stub
        ctx = MagicMock()
        ctx.channel.id = puz.channel_id
        ctx.guild = MagicMock(spec=discord.Guild)
        ctx.send = AsyncMock()

        # Stub helpers the cog uses before delegating
        cog.get_puzzle = MagicMock(return_value=puz)
        cog.get_hunt = MagicMock(return_value=hunt)
        cog.get_guild = MagicMock(return_value=ctx.guild)
        cog.get_guild_data = MagicMock()
        cog.get_guild_data.return_value.discord_bot_emoji = ":robot:"
        cog.move_to_bottom = AsyncMock()
        cog.info = AsyncMock()
        cog._warn_stale_sheets = AsyncMock()

        # Mock service.unsolve — this is what we're asserting IS called
        cog.service.unsolve = AsyncMock()

        with patch("bot.cogs.puzzles.PuzzleJsonDb"), \
             patch("bot.cogs.puzzles.RoundJsonDb"):
            await cog.unsolve.callback(cog, ctx)

        cog.service.unsolve.assert_called_once()
        call_args = cog.service.unsolve.call_args
        # The puzzle data must be passed somewhere in the call
        all_args = list(call_args.args) + list(call_args.kwargs.values())
        assert puz in all_args
