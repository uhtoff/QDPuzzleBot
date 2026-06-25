"""Tests for build_board and _load_notes in bot/web/data.py."""

from bot.web.data import build_board, _load_notes
from tests.conftest import make_puzzle_row, make_round_ref


# ---------------------------------------------------------------------------
# _load_notes
# ---------------------------------------------------------------------------

class TestLoadNotes:
    def test_none_returns_empty(self):
        assert _load_notes(None) == []

    def test_empty_string_returns_empty(self):
        assert _load_notes("") == []

    def test_valid_list(self):
        assert _load_notes('["a", "b"]') == ["a", "b"]

    def test_non_list_json_returns_empty(self):
        # A JSON object (not a list) is rejected
        assert _load_notes('{"key": "val"}') == []

    def test_invalid_json_returns_empty(self):
        assert _load_notes("not json at all") == []


# ---------------------------------------------------------------------------
# build_board — basic structure
# ---------------------------------------------------------------------------

class TestBuildBoardBasic:
    def test_empty_input(self):
        assert build_board([]) == []

    def test_unassigned_bucket_for_tagless_puzzle(self):
        puz = make_puzzle_row(id=1, name="Orphan")
        board = build_board([puz])
        assert len(board) == 1
        grp = board[0]
        assert grp["tag_group_id"] == 0
        assert grp["name"] == "Unassigned"
        assert grp["puzzles"] == [puz]
        assert grp["num_feeders"] == 1
        assert grp["num_solved_feeders"] == 0

    def test_unassigned_solved_feeder_counts(self):
        puz = make_puzzle_row(id=1, name="Solved Orphan", solved=True)
        board = build_board([puz])
        grp = board[0]
        assert grp["num_feeders"] == 1
        assert grp["num_solved_feeders"] == 1

    def test_single_round_single_feeder(self):
        rnd = make_round_ref(id=5, name="Round One", group_code="R1")
        puz = make_puzzle_row(id=2, name="Feeder", rounds=[rnd])
        board = build_board([puz])
        assert len(board) == 1
        grp = board[0]
        assert grp["tag_group_id"] == 5
        assert grp["name"] == "Round One"
        assert grp["num_feeders"] == 1
        assert grp["num_solved_feeders"] == 0
        assert grp["target_solution"] is None
        assert grp["target_solved"] is False

    def test_meta_puzzle_not_counted_as_feeder(self):
        rnd = make_round_ref(id=5, name="Round One", is_target_of=True)
        meta = make_puzzle_row(id=3, name="Meta", is_meta=True, solved=True,
                               solution="ANSWER", rounds=[rnd])
        board = build_board([meta])
        grp = board[0]
        # The meta itself is not a feeder
        assert grp["num_feeders"] == 0
        assert grp["num_solved_feeders"] == 0
        assert grp["target_solved"] is True
        assert grp["target_solution"] == "ANSWER"


class TestBuildBoardRollupBadge:
    def test_no_rollup_map_is_not_stale(self):
        rnd = make_round_ref(id=5, name="Round One")
        puz = make_puzzle_row(id=2, name="Feeder", rounds=[rnd])
        grp = build_board([puz])[0]
        assert grp["rollup_stale"] is False
        assert grp["rollup_error"] is None

    def test_stale_rollup_flags_the_group(self):
        rnd = make_round_ref(id=5, name="Round One")
        puz = make_puzzle_row(id=2, name="Feeder", rounds=[rnd])
        rollups = {5: {"stale": True, "error": "sheets down"}}
        grp = build_board([puz], rollups)[0]
        assert grp["rollup_stale"] is True
        assert grp["rollup_error"] == "sheets down"

    def test_healthy_rollup_not_stale(self):
        rnd = make_round_ref(id=5, name="Round One")
        puz = make_puzzle_row(id=2, name="Feeder", rounds=[rnd])
        rollups = {5: {"stale": False, "error": None}}
        grp = build_board([puz], rollups)[0]
        assert grp["rollup_stale"] is False

    def test_source_mode_propagated_from_rollup(self):
        rnd = make_round_ref(id=5, name="Round One")
        puz = make_puzzle_row(id=2, name="Feeder", rounds=[rnd])
        rollups = {5: {"stale": False, "error": None, "source_mode": "subtree"}}
        grp = build_board([puz], rollups)[0]
        assert grp["source_mode"] == "subtree"

    def test_source_mode_defaults_to_round_when_missing(self):
        rnd = make_round_ref(id=5, name="Round One")
        puz = make_puzzle_row(id=2, name="Feeder", rounds=[rnd])
        # no rollup map at all
        grp = build_board([puz])[0]
        assert grp["source_mode"] == "round"


# ---------------------------------------------------------------------------
# build_board — ordering
# ---------------------------------------------------------------------------

class TestBuildBoardOrdering:
    def test_groups_appear_in_first_seen_order(self):
        r1 = make_round_ref(id=1, name="Alpha")
        r2 = make_round_ref(id=2, name="Beta")
        puz1 = make_puzzle_row(id=1, name="P1", rounds=[r1])
        puz2 = make_puzzle_row(id=2, name="P2", rounds=[r2])
        puz3 = make_puzzle_row(id=3, name="P3", rounds=[r1])
        board = build_board([puz1, puz2, puz3])
        assert [g["name"] for g in board] == ["Alpha", "Beta"]

    def test_unassigned_appears_at_first_tagless_position(self):
        r1 = make_round_ref(id=1, name="Round A")
        puz_round = make_puzzle_row(id=1, name="In Round", rounds=[r1])
        puz_orphan = make_puzzle_row(id=2, name="Orphan")
        # Orphan comes first → Unassigned first
        board = build_board([puz_orphan, puz_round])
        assert board[0]["tag_group_id"] == 0
        assert board[1]["name"] == "Round A"


# ---------------------------------------------------------------------------
# build_board — multi-round puzzles
# ---------------------------------------------------------------------------

class TestBuildBoardMultiRound:
    def test_puzzle_in_two_rounds_appears_in_both(self):
        r1 = make_round_ref(id=1, name="Round A")
        r2 = make_round_ref(id=2, name="Round B")
        puz = make_puzzle_row(id=1, name="Multi", rounds=[r1, r2])
        board = build_board([puz])
        assert len(board) == 2
        for grp in board:
            assert grp["puzzles"][0]["name"] == "Multi"

    def test_multi_round_puzzle_feeder_counts_in_each(self):
        r1 = make_round_ref(id=1, name="Round A")
        r2 = make_round_ref(id=2, name="Round B")
        puz = make_puzzle_row(id=1, name="Multi", solved=True, rounds=[r1, r2])
        board = build_board([puz])
        for grp in board:
            assert grp["num_feeders"] == 1
            assert grp["num_solved_feeders"] == 1


# ---------------------------------------------------------------------------
# build_board — round-level aggregates
# ---------------------------------------------------------------------------

class TestBuildBoardAggregates:
    def test_feeder_solve_count(self):
        r = make_round_ref(id=1, name="R")
        p1 = make_puzzle_row(id=1, name="A", solved=True, rounds=[r])
        p2 = make_puzzle_row(id=2, name="B", solved=False, rounds=[r])
        p3 = make_puzzle_row(id=3, name="C", solved=True, rounds=[r])
        board = build_board([p1, p2, p3])
        grp = board[0]
        assert grp["num_feeders"] == 3
        assert grp["num_solved_feeders"] == 2

    def test_meta_solution_and_solved_flag(self):
        r = make_round_ref(id=1, name="R", is_target_of=True)
        meta = make_puzzle_row(id=10, name="Meta", solved=True, solution="METASOL",
                               is_meta=True, rounds=[r])
        feeder = make_puzzle_row(id=11, name="Feeder", rounds=[make_round_ref(id=1, name="R")])
        board = build_board([meta, feeder])
        grp = board[0]
        assert grp["target_solution"] == "METASOL"
        assert grp["target_solved"] is True
        assert grp["num_feeders"] == 1

    def test_unsolved_meta(self):
        r = make_round_ref(id=1, name="R", is_target_of=True)
        meta = make_puzzle_row(id=10, name="Meta", solved=False, rounds=[r])
        board = build_board([meta])
        grp = board[0]
        assert grp["target_solved"] is False
        assert grp["target_solution"] is None

    def test_group_code_carried_through(self):
        r = make_round_ref(id=1, name="R", group_code="XYZABC")
        puz = make_puzzle_row(id=1, name="P", rounds=[r])
        board = build_board([puz])
        assert board[0]["group_code"] == "XYZABC"
