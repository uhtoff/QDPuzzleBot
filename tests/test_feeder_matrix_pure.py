"""Unit tests for the matrix shaping helpers. No DB, no Discord, no Sheets."""
from dataclasses import dataclass

from bot.core.feeder_pool import (
    feeder_length,
    matrix_header,
    matrix_rows,
    parse_ticks,
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
    """values().get returns FORMATTED_VALUE strings, so a tick is the string 'TRUE'."""
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
