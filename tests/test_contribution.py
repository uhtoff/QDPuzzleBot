"""Pure tests for the contribution table row model (Milestone D).

Tasks 3 + 4 of docs/superpowers/plans/2026-06-25-milestone-d-sheet-generation.md.
No Google, no DB — contribution_rows is a pure function and diff_rows is reused
generically. Authored test-first (Opus); implementation lands separately.
"""
from bot.core.contribution import contribution_rows
from bot.core.unassigned import diff_rows
from bot.store.puzzle_data import PuzzleData


def _p(id, name, solution=None, tag_edges=None):
    p = PuzzleData()
    p.id = id
    p.name = name
    p.solution = solution
    p.tag_edges = tag_edges or {}
    return p


# --- Task 3: contribution_rows (feeds / informs / none + evidence) ---------

def test_feeds_member_makes_feeder_row():
    feeder = _p(2, "Alpha", solution="WORD", tag_edges={5: {"feeds"}})
    rows = contribution_rows(tag_group_id=5, members=[feeder], evidence=[])
    assert rows == [["feeds:2", "Alpha", "Feeder", "WORD"]]


def test_none_member_makes_group_row_no_answer():
    grp = _p(3, "Beta", solution="X", tag_edges={5: {"none"}})
    rows = contribution_rows(tag_group_id=5, members=[grp], evidence=[])
    assert rows == [["none:3", "Beta", "Group", ""]]


def test_bare_informs_edge_makes_one_keyless_information_row():
    inf = _p(4, "Gamma", tag_edges={5: {"informs"}})
    rows = contribution_rows(tag_group_id=5, members=[inf], evidence=[])
    assert rows == [["informs:4", "Gamma", "Information", ""]]


def test_informs_with_evidence_makes_one_row_per_datum_and_drops_bare():
    inf = _p(4, "Gamma", tag_edges={5: {"informs"}})
    ev = [{"id": 11, "source_puzzle_id": 4, "content": "is blue"},
          {"id": 12, "source_puzzle_id": 4, "content": "near water"}]
    rows = contribution_rows(tag_group_id=5, members=[inf], evidence=ev)
    assert rows == [
        ["informs:4:11", "Gamma", "Information", "is blue"],
        ["informs:4:12", "Gamma", "Information", "near water"],
    ]


def test_unsolved_feeder_has_blank_answer():
    feeder = _p(2, "Alpha", solution=None, tag_edges={5: {"feeds"}})
    rows = contribution_rows(tag_group_id=5, members=[feeder], evidence=[])
    assert rows == [["feeds:2", "Alpha", "Feeder", ""]]


def test_transitive_subtree_member_with_no_edge_renders_as_feeder():
    # A runaround ('subtree') member reached via a 'feeds' chain through sub-metas
    # carries NO edge to THIS group (its feeds edge points at the sub-meta). It is
    # still a feeder answer and must render as one — otherwise the runaround sheet
    # shows only its direct feeders.
    deep = _p(2, "OldTestament", solution="FABRICATION", tag_edges={1396: {"feeds"}})
    rows = contribution_rows(tag_group_id=1400, members=[deep], evidence=[])
    assert rows == [["feeds:2", "OldTestament", "Feeder", "FABRICATION"]]


def test_transitive_unsolved_subtree_member_renders_blank_feeder():
    deep = _p(7, "Paul", solution=None, tag_edges={1397: {"feeds"}})
    rows = contribution_rows(tag_group_id=1400, members=[deep], evidence=[])
    assert rows == [["feeds:7", "Paul", "Feeder", ""]]


def test_feeds_and_informs_member_emits_both_rows():
    p = _p(8, "Octo", solution="ANSWER", tag_edges={5: {"feeds", "informs"}})
    evidence = [{"id": 1, "source_puzzle_id": 8, "content": "a clue"}]
    rows = contribution_rows(tag_group_id=5, members=[p], evidence=evidence)
    keys = {r[0] for r in rows}
    assert "feeds:8" in keys
    assert "informs:8:1" in keys


def test_bare_informs_with_no_evidence_still_renders():
    p = _p(9, "Nine", tag_edges={5: {"informs"}})
    rows = contribution_rows(tag_group_id=5, members=[p], evidence=[])
    assert rows == [["informs:9", "Nine", "Information", ""]]


# --- Task 4: diff_rows is key-generic (string contribution keys) -----------

def test_diff_rows_handles_string_contribution_keys():
    current = ["feeds:2", "informs:4"]                      # already on the sheet
    desired = [["feeds:2", "Alpha", "Feeder", "WORD"],      # update (key present)
               ["none:3", "Beta", "Group", ""]]             # append (key new)
    appends, updates, prunes = diff_rows(current, desired)
    assert appends == [["none:3", "Beta", "Group", ""]]
    assert updates == [["feeds:2", "Alpha", "Feeder", "WORD"]]
    assert prunes == ["informs:4"]                          # gone from desired


# --- Sourceless evidence (Plan A) -----------------------------------------
# Evidence with no source puzzle has no member row to hang from, so it is
# appended under its own key namespace after the member loop.

def test_sourceless_evidence_gets_its_own_row():
    rows = contribution_rows(
        tag_group_id=5, members=[],
        evidence=[{"id": 9, "source_puzzle_id": None,
                   "source_label": "Harbour map", "content": "three anchors"}],
    )
    assert rows == [["informs:free:9", "Harbour map", "Information", "three anchors"]]


def test_sourceless_rows_come_after_member_rows():
    feeder = _p(2, "Alpha", solution="WORD", tag_edges={5: {"feeds"}})
    rows = contribution_rows(
        tag_group_id=5, members=[feeder],
        evidence=[{"id": 9, "source_puzzle_id": None,
                   "source_label": "Harbour map", "content": "three anchors"}],
    )
    assert rows[0][0] == "feeds:2"
    assert rows[-1][0] == "informs:free:9"


def test_multiple_sourceless_items_keep_their_given_order():
    """The docstring promises "sourceless evidence last, in the given order".
    With a single item that clause is unfalsifiable — reversing the list changes
    nothing observable — so it takes two to pin it. The ids descend so that a
    stray sort by id would fail rather than accidentally agree.
    """
    rows = contribution_rows(
        tag_group_id=5, members=[],
        evidence=[
            {"id": 9, "source_puzzle_id": None, "content": "first"},
            {"id": 3, "source_puzzle_id": None, "content": "second"},
        ],
    )
    assert [r[0] for r in rows] == ["informs:free:9", "informs:free:3"]
    assert [r[3] for r in rows] == ["first", "second"]


def test_sourceless_label_falls_back_to_an_em_dash():
    """Evidence with neither a source puzzle nor a label still renders."""
    rows = contribution_rows(
        tag_group_id=5, members=[],
        evidence=[{"id": 9, "source_puzzle_id": None, "content": "a fact"}],
    )
    assert rows[0][1] == "—"


def test_sourced_evidence_is_unaffected():
    """Rows with a source_puzzle_id keep their existing key and grouping."""
    inf = _p(4, "Gamma", tag_edges={5: {"informs"}})
    rows = contribution_rows(
        tag_group_id=5, members=[inf],
        evidence=[{"id": 4, "source_puzzle_id": 4, "content": "a datum"}],
    )
    assert rows == [["informs:4:4", "Gamma", "Information", "a datum"]]


def test_sourceless_evidence_does_not_leak_into_member_grouping():
    """A sourceless item must not be grouped under a member with no evidence,
    which would blank that member's own bare-edge row."""
    inf = _p(4, "Gamma", tag_edges={5: {"informs"}})
    rows = contribution_rows(
        tag_group_id=5, members=[inf],
        evidence=[{"id": 9, "source_puzzle_id": None, "content": "a fact"}],
    )
    assert rows[0] == ["informs:4", "Gamma", "Information", ""]
    assert rows[1][0] == "informs:free:9"
