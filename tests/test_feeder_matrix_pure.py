"""Unit tests for the matrix shaping helpers. No DB, no Discord, no Sheets."""
from dataclasses import dataclass

from bot.core.feeder_pool import (
    feeder_length,
    matrix_header,
    matrix_rows,
    parse_ticks,
    stable_matrix_header,
    target_id_from_label,
)


@dataclass
class Item:
    id: int
    text: str
    note: str = ""


@dataclass
class Target:
    id: int
    name: str


@dataclass
class Claim:
    item_id: int
    target_id: int
    confirmed: bool = False


MINIMETA = Target(100, "Matching Meta")
MINI_1, MINI_2 = Target(101, "Mini 1"), Target(102, "Mini 2")
ALBATROSS = Item(12, "ALBATROSS", "fits SEA")
MARZIPAN = Item(13, "MARZIPAN")


def test_feeder_length_excludes_spaces_but_keeps_punctuation():
    """Matches bot/core/unassigned.py's rule, which the Unassigned table already uses."""
    assert feeder_length("SEVENTH HEAVEN") == 13
    assert feeder_length("O'CLOCK") == 7
    assert feeder_length("") == ""


def test_matrix_header_puts_main_first_then_minis_in_order():
    header = matrix_header(MINIMETA, [MINI_1, MINI_2])
    assert header == ["key", "item", "Len", "note",
                      "main #100", "Mini 1 #101", "Mini 2 #102"]


def test_matrix_header_with_no_minis_still_has_main():
    assert matrix_header(MINIMETA, []) == ["key", "item", "Len", "note", "main #100"]


def test_every_tick_column_carries_its_target_id():
    """Two minis sharing a NAME must still be distinguishable — create_mini enforces
    no name uniqueness, so this is reachable, not hypothetical."""
    twin_a, twin_b = Target(101, "Mini"), Target(102, "Mini")
    header = matrix_header(MINIMETA, [twin_a, twin_b])
    ids = [target_id_from_label(label) for label in header]
    assert ids == [None, None, None, None, 100, 101, 102]


def test_target_id_from_label_rejects_a_solvers_own_column():
    assert target_id_from_label("scratch") is None
    assert target_id_from_label("") is None
    assert target_id_from_label("Mini #not-a-number") is None


def test_matrix_rows_are_bot_owned_columns_only():
    """Only key/item/Len — note and the tick cells are the solver's and must never
    appear in the desired rows, or reconcile_table would overwrite them."""
    rows = matrix_rows([ALBATROSS, MARZIPAN])
    assert rows == [["f:12", "ALBATROSS", 9], ["f:13", "MARZIPAN", 8]]


def test_matrix_rows_preserve_pool_order():
    rows = matrix_rows([MARZIPAN, ALBATROSS])
    assert [r[0] for r in rows] == ["f:13", "f:12"]


def test_parse_ticks_maps_each_column_to_its_target():
    """Two targets and two items, arranged so a transposed column or row fails."""
    header = ["key", "item", "Len", "note", "main #100", "Mini 1 #101", "Mini 2 #102"]
    grid = [
        ["f:12", "ALBATROSS", "9", "fits SEA", "", "TRUE", ""],
        ["f:13", "MARZIPAN", "8", "", "TRUE", "", "TRUE"],
    ]
    ticks = parse_ticks(header, grid, [100, 101, 102])
    assert ticks == {100: [13], 101: [12], 102: [13]}


def test_parse_ticks_survives_a_renamed_mini():
    """The sheet header goes stale the moment a mini is renamed. Matching on the name
    would drop this column's ticks silently — losing claims on the next sync."""
    header = ["key", "item", "Len", "note", "main #100", "OLD NAME #101"]
    grid = [["f:12", "A", "1", "", "", "TRUE"]]
    assert parse_ticks(header, grid, [100, 101]) == {100: [], 101: [12]}


def test_parse_ticks_keeps_two_same_named_minis_apart():
    header = ["key", "item", "Len", "note", "main #100", "Mini #101", "Mini #102"]
    grid = [["f:12", "A", "1", "", "", "TRUE", ""]]
    assert parse_ticks(header, grid, [100, 101, 102]) == {100: [], 101: [12], 102: []}


def test_parse_ticks_ignores_a_column_whose_mini_was_deleted():
    """A stale column outlives its mini until the next header rewrite; its ticks must
    not resurrect a claim against a target that no longer exists."""
    header = ["key", "item", "Len", "note", "main #100", "Gone #999"]
    grid = [["f:12", "A", "1", "", "", "TRUE"]]
    assert parse_ticks(header, grid, [100]) == {100: []}


def test_parse_ticks_treats_only_TRUE_as_ticked():
    """values().get returns FORMATTED_VALUE strings, so a tick is the string 'TRUE'.

    Both unticked forms are pinned because a live probe (2026-08-12) showed a BOOLEAN
    table column reads back as an explicit 'FALSE', not the blank this plan assumed.
    The implementation was already correct for both; this test stops that being luck.
    """
    header = ["key", "item", "Len", "note", "main #100"]
    grid = [["f:12", "A", "1", "", "FALSE"], ["f:13", "B", "1", "", ""]]
    assert parse_ticks(header, grid, [100]) == {100: []}


def test_parse_ticks_tolerates_short_rows():
    """A row is right-trimmed by the API when its trailing cells are empty, so a row
    can be shorter than the header."""
    header = ["key", "item", "Len", "note", "main #100", "Mini 1 #101"]
    grid = [["f:12", "A", "1"]]
    assert parse_ticks(header, grid, [100, 101]) == {100: [], 101: []}


def test_parse_ticks_ignores_a_solvers_own_column():
    """A solver may add their own column; with no id in its label it is not ours."""
    header = ["key", "item", "Len", "note", "main #100", "scratch"]
    grid = [["f:12", "A", "1", "", "TRUE", "TRUE"]]
    assert parse_ticks(header, grid, [100]) == {100: [12]}


def test_parse_ticks_skips_rows_with_an_unparsable_key():
    """Blanked prune rows survive on the sheet; they must not become claims."""
    header = ["key", "item", "Len", "note", "main #100"]
    grid = [["", "", "", "", "TRUE"], ["f:13", "B", "1", "", "TRUE"]]
    assert parse_ticks(header, grid, [100]) == {100: [13]}


# --- stable_matrix_header: a column's index belongs to the SHEET ------------
#
# Every case uses at least two minis, arranged so that a re-packing
# implementation (matrix_header's own list order) disagrees with a stable one.

HEAD_3 = ["key", "item", "Len", "note", "main #100", "Mini 1 #101", "Mini 2 #102"]


def test_deleting_the_first_mini_leaves_the_second_where_it_is():
    header, blanked = stable_matrix_header(HEAD_3, MINIMETA, [MINI_2])
    assert header == ["key", "item", "Len", "note", "main #100", "", "Mini 2 #102"]
    assert blanked == [5]
    # The naive rebuild would have put Mini 2 at index 5, over Mini 1's ticks.
    assert matrix_header(MINIMETA, [MINI_2]).index("Mini 2 #102") == 5


def test_deleting_a_middle_mini_moves_neither_of_its_neighbours():
    mini_3 = Target(103, "Mini 3")
    existing = HEAD_3 + ["Mini 3 #103"]
    header, blanked = stable_matrix_header(existing, MINIMETA, [MINI_1, mini_3])
    assert header == ["key", "item", "Len", "note",
                      "main #100", "Mini 1 #101", "", "Mini 3 #103"]
    assert blanked == [6]


def test_a_new_mini_is_appended_and_never_reuses_a_blanked_gap():
    """A gap is only safe once its clearing write has landed; appending costs a
    column and cannot inherit anyone's ticks."""
    existing = ["key", "item", "Len", "note", "main #100", "", "Mini 2 #102"]
    mini_4 = Target(104, "Mini 4")
    header, blanked = stable_matrix_header(existing, MINIMETA, [MINI_2, mini_4])
    assert header == ["key", "item", "Len", "note",
                      "main #100", "", "Mini 2 #102", "Mini 4 #104"]
    assert blanked == []


def test_a_renamed_mini_keeps_its_column_and_gets_the_new_name():
    renamed = Target(101, "Renamed")
    header, _blanked = stable_matrix_header(HEAD_3, MINIMETA, [renamed, MINI_2])
    assert header[5] == "Renamed #101"
    assert header[6] == "Mini 2 #102"


def test_a_solvers_own_column_is_left_alone():
    existing = HEAD_3 + ["my working"]
    header, blanked = stable_matrix_header(existing, MINIMETA, [MINI_1, MINI_2])
    assert header == existing
    assert blanked == []


def test_a_duplicated_id_left_by_the_repacking_bug_is_blanked():
    """Two columns claiming the same target is exactly what the old rebuild left
    behind; parse_ticks would union them, handing one mini another's claims."""
    existing = ["key", "item", "Len", "note", "main #100", "Mini 2 #102", "Mini 2 #102"]
    header, blanked = stable_matrix_header(existing, MINIMETA, [MINI_2])
    assert header == ["key", "item", "Len", "note", "main #100", "Mini 2 #102", ""]
    assert blanked == [6]


def test_an_unrecognisable_row_is_replaced_by_the_canonical_header():
    """A blank tab, or a header row a solver deleted (leaving a DATA row in row
    1), records nothing -- rebuild rather than preserve its cells."""
    assert stable_matrix_header([], MINIMETA, [MINI_1]) == \
        (matrix_header(MINIMETA, [MINI_1]), [])
    data_row = ["f:12", "ALBATROSS", "9", "", "TRUE", ""]
    assert stable_matrix_header(data_row, MINIMETA, [MINI_1]) == \
        (matrix_header(MINIMETA, [MINI_1]), [])


def test_the_bot_owned_prefix_is_always_canonical():
    existing = ["", "", "", "", "main #100", "Mini 1 #101", "Mini 2 #102"]
    header, _blanked = stable_matrix_header(existing, MINIMETA, [MINI_1, MINI_2])
    assert header[:4] == ["key", "item", "Len", "note"]


def test_ticks_are_not_inherited_by_the_surviving_mini():
    """The end-to-end point of all of the above: read the resulting header back
    over the UNMOVED tick data and nothing of Mini 1's lands on Mini 2."""
    header, _blanked = stable_matrix_header(HEAD_3, MINIMETA, [MINI_2])
    grid = [
        ["f:12", "ALBATROSS", "9", "", "", "TRUE", ""],    # was Mini 1's tick
        ["f:13", "MARZIPAN", "8", "", "", "", "TRUE"],     # is Mini 2's tick
    ]
    ticks = parse_ticks(header, grid, {100, 102})
    assert ticks == {100: [], 102: [13]}
