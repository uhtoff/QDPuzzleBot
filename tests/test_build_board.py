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
        assert grp["name"] == "Unassigned Feeders"
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
        # The meta itself is not a feeder — the X/Y count is feeders only.
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
# build_board — Unassigned Feeders (feeds nothing)
# ---------------------------------------------------------------------------

def _unassigned(board):
    return next((g for g in board if g["tag_group_id"] == 0), None)


class TestBuildBoardUnassignedFeeders:
    def test_informs_only_puzzle_in_both_group_and_unassigned(self):
        # A puzzle that only informs a capstone feeds nothing -> it appears under
        # the capstone group AND in Unassigned Feeders.
        r = make_round_ref(id=5, name="Cap", group_code="C5", edges=("informs",))
        puz = make_puzzle_row(id=2, name="Informer", rounds=[r])
        board = build_board([puz])
        cap = next(g for g in board if g["tag_group_id"] == 5)
        un = _unassigned(board)
        assert puz in cap["puzzles"]
        assert un is not None and puz in un["puzzles"]

    def test_none_only_puzzle_is_unassigned(self):
        r = make_round_ref(id=5, name="Pool", group_code="P5", edges=("none",))
        puz = make_puzzle_row(id=2, name="Pooled", rounds=[r])
        board = build_board([puz])
        assert _unassigned(board) is not None

    def test_feeds_puzzle_not_unassigned(self):
        r = make_round_ref(id=5, name="Round", group_code="R5", edges=("feeds",))
        puz = make_puzzle_row(id=2, name="Feeder", rounds=[r])
        board = build_board([puz])
        assert _unassigned(board) is None

    def test_meta_target_not_unassigned(self):
        # A target puzzle is excluded from Unassigned even though it feeds nothing.
        r = make_round_ref(id=5, name="Round", is_target_of=True)
        meta = make_puzzle_row(id=3, name="Meta", is_meta=True, rounds=[r])
        board = build_board([meta])
        assert _unassigned(board) is None

    def test_feeds_and_informs_puzzle_not_unassigned(self):
        # Feeds at least one meta -> assigned, even if it also informs another.
        rf = make_round_ref(id=5, name="Meta", group_code="M5", edges=("feeds",))
        ri = make_round_ref(id=6, name="Cap", group_code="C6", edges=("informs",))
        puz = make_puzzle_row(id=2, name="Mixed", rounds=[rf, ri])
        board = build_board([puz])
        assert _unassigned(board) is None

    def test_column_order_unassigned_meta_capstone_plain(self):
        # Columns sort: Unassigned Feeders, then unsolved metas, then unsolved
        # capstones, then plain groups — classified from all_groups + edges.
        m = make_round_ref(id=1, name="Meta", group_code="M", edges=("feeds",))
        c = make_round_ref(id=2, name="Cap", group_code="C", edges=("informs",))
        feeder = make_puzzle_row(id=10, name="F", rounds=[m])
        informer = make_puzzle_row(id=11, name="I", rounds=[c])
        orphan = make_puzzle_row(id=12, name="O")  # feeds nothing -> Unassigned
        all_groups = [
            {"id": 1, "name": "Meta", "group_code": "M", "type": "Metapuzzle", "target_puzzle_id": 99},
            {"id": 2, "name": "Cap", "group_code": "C", "type": "Capstone", "target_puzzle_id": 98},
            {"id": 3, "name": "Plain", "group_code": "G", "type": "Group", "target_puzzle_id": 0},
        ]
        board = build_board([feeder, informer, orphan], None, all_groups)
        assert [g["name"] for g in board] == ["Unassigned Feeders", "Meta", "Cap", "Plain"]

    def test_column_order_solved_targets_pushed_right(self):
        # Full order: Unassigned, unsolved meta, unsolved capstone, plain,
        # solved meta, solved capstone. meta/capstone derived from feeder edges;
        # target solved-state from the target puzzle.
        def meta_group(gid, name, solved, edge):
            target = make_puzzle_row(
                id=100 + gid, name=f"{name}-T", is_meta=True, solved=solved,
                rounds=[make_round_ref(id=gid, name=name, group_code=name, is_target_of=True)],
            )
            feeder = make_puzzle_row(
                id=200 + gid, name=f"{name}-f",
                rounds=[make_round_ref(id=gid, name=name, group_code=name, edges=(edge,))],
            )
            return [target, feeder]

        puzzles = []
        puzzles += meta_group(1, "UMeta", solved=False, edge="feeds")
        puzzles += meta_group(2, "UCap", solved=False, edge="informs")
        puzzles += meta_group(4, "SMeta", solved=True, edge="feeds")
        puzzles += meta_group(5, "SCap", solved=True, edge="informs")
        # A plain group (no target) via a pooled 'none' edge.
        puzzles.append(make_puzzle_row(
            id=300, name="pooled",
            rounds=[make_round_ref(id=3, name="Plain", group_code="Plain", edges=("none",))],
        ))
        names = [g["name"] for g in build_board(puzzles)]
        # Unassigned Feeders holds the informers/pooled (feed nothing); then the
        # ordered targets and the plain group.
        names = [n for n in names if n != "Unassigned Feeders"]
        assert names == ["UMeta", "UCap", "Plain", "SMeta", "SCap"]


# ---------------------------------------------------------------------------
# build_board — bare (empty) groups
# ---------------------------------------------------------------------------

class TestBuildBoardBareGroups:
    def test_empty_group_surfaced_as_drop_target(self):
        # A group with no tagged puzzles still appears (with an empty puzzle list).
        all_groups = [{"id": 7, "name": "Bare", "group_code": "B7", "type": "Group"}]
        board = build_board([], None, all_groups)
        assert len(board) == 1
        grp = board[0]
        assert grp["tag_group_id"] == 7
        assert grp["name"] == "Bare"
        assert grp["group_code"] == "B7"
        assert grp["puzzles"] == []
        assert grp["num_feeders"] == 0

    def test_empty_group_appended_after_populated_groups(self):
        r1 = make_round_ref(id=1, name="Alpha", group_code="A1")
        puz = make_puzzle_row(id=1, name="P1", rounds=[r1])
        all_groups = [
            {"id": 1, "name": "Alpha", "group_code": "A1", "type": "Round"},
            {"id": 9, "name": "Empty", "group_code": "E9", "type": "Group"},
        ]
        board = build_board([puz], None, all_groups)
        # Populated group keeps its (single) puzzle; the bare one is appended.
        assert [g["name"] for g in board] == ["Alpha", "Empty"]
        assert board[0]["puzzles"][0]["name"] == "P1"
        assert board[1]["puzzles"] == []

    def test_codeless_group_skipped(self):
        # A group with no group_code cannot be a drop target, so it is not added.
        all_groups = [{"id": 3, "name": "NoCode", "group_code": None, "type": "Group"}]
        assert build_board([], None, all_groups) == []

    def test_populated_group_not_duplicated_by_all_groups(self):
        r1 = make_round_ref(id=1, name="Alpha", group_code="A1")
        puz = make_puzzle_row(id=1, name="P1", rounds=[r1])
        all_groups = [{"id": 1, "name": "Alpha", "group_code": "A1", "type": "Round"}]
        board = build_board([puz], None, all_groups)
        assert len(board) == 1
        assert board[0]["puzzles"][0]["name"] == "P1"


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


# ---------------------------------------------------------------------------
# build_board — sub-kind sort order within meta tier
# ---------------------------------------------------------------------------

class TestBuildBoardSubKindOrder:
    """build_board should order groups: runaround < metameta < meta within the
    unsolved-meta and solved-meta tiers (before and after capstones/plain)."""

    def _grp(self, gid, name, edge, feeder_is_meta=False, solved=False, gtype="Metapuzzle"):
        """Return (puzzle_list, all_groups_entry) for a group with one target + one feeder."""
        target = make_puzzle_row(
            id=100 + gid, name=f"{name}-T", is_meta=True, solved=solved,
            rounds=[make_round_ref(id=gid, name=name, group_code=name, is_target_of=True)],
        )
        feeder = make_puzzle_row(
            id=200 + gid, name=f"{name}-F", is_meta=feeder_is_meta,
            rounds=[make_round_ref(id=gid, name=name, group_code=name, edges=(edge,))],
        )
        ag = {"id": gid, "name": name, "group_code": name, "type": gtype,
              "target_puzzle_id": 100 + gid}
        return [target, feeder], ag

    def test_runaround_before_metameta_before_meta_unsolved(self):
        puzzles, ags = [], []
        for gid, name, edge, fim, gtype in [
            (3, "Meta",      "feeds", False, "Metapuzzle"),
            (2, "Metameta",  "feeds", True,  "Metapuzzle"),
            (1, "Runaround", "feeds", False, "Runaround"),
        ]:
            p, g = self._grp(gid, name, edge, fim, solved=False, gtype=gtype)
            puzzles.extend(p); ags.append(g)
        board = build_board(puzzles, None, ags)
        names = [g["name"] for g in board if g["tag_group_id"] != 0]
        assert names == ["Runaround", "Metameta", "Meta"]

    def test_runaround_before_metameta_before_meta_solved(self):
        puzzles, ags = [], []
        for gid, name, edge, fim, gtype in [
            (3, "Meta",      "feeds", False, "Metapuzzle"),
            (2, "Metameta",  "feeds", True,  "Metapuzzle"),
            (1, "Runaround", "feeds", False, "Runaround"),
        ]:
            p, g = self._grp(gid, name, edge, fim, solved=True, gtype=gtype)
            puzzles.extend(p); ags.append(g)
        board = build_board(puzzles, None, ags)
        names = [g["name"] for g in board if g["tag_group_id"] != 0]
        assert names == ["Runaround", "Metameta", "Meta"]

    def test_unsolved_sub_kinds_before_solved_sub_kinds(self):
        puzzles, ags = [], []
        for gid, name, edge, fim, gtype, solved in [
            (6, "SMeta",      "feeds", False, "Metapuzzle", True),
            (5, "SMetameta",  "feeds", True,  "Metapuzzle", True),
            (4, "SRunaround", "feeds", False, "Runaround",  True),
            (1, "UMeta",      "feeds", False, "Metapuzzle", False),
            (2, "UMetameta",  "feeds", True,  "Metapuzzle", False),
            (3, "URunaround", "feeds", False, "Runaround",  False),
        ]:
            p, g = self._grp(gid, name, edge, fim, solved=solved, gtype=gtype)
            puzzles.extend(p); ags.append(g)
        board = build_board(puzzles, None, ags)
        names = [g["name"] for g in board if g["tag_group_id"] != 0]
        assert names == ["URunaround", "UMetameta", "UMeta",
                         "SRunaround", "SMetameta", "SMeta"]
