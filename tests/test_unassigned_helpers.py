"""Tests for meta-matching pure helpers: row shaping + reconcile diff."""
from tests.conftest import make_puzzle


class TestUnassignedRow:
    """unassigned_row(puzzle) -> [id, name, ANSWER_UPPER, length] ; blanks when unsolved."""

    def test_solved_row_uppercases_answer_and_counts_letters_excluding_spaces(self):
        from bot.core.unassigned import unassigned_row
        p = make_puzzle(id=1024, name="Crossword Caper", solved=True, solution="red herring")
        row = unassigned_row(p)
        assert row == [1024, "Crossword Caper", "RED HERRING", 10]   # length excludes the space

    def test_unsolved_row_has_blank_answer_and_blank_length(self):
        from bot.core.unassigned import unassigned_row
        p = make_puzzle(id=1025, name="Mystery Box", solved=False, solution=None)
        row = unassigned_row(p)
        assert row == [1025, "Mystery Box", "", ""]

    def test_length_strips_spaces_only_not_punctuation(self):
        from bot.core.unassigned import unassigned_row
        p = make_puzzle(id=2, name="P", solved=True, solution="A-B C")
        # strip spaces only: "A-B C" -> "A-BC" -> 4 chars
        assert unassigned_row(p)[3] == 4


class TestUnassignedRowLink:
    def test_link_wraps_name_with_callable_result(self):
        from bot.core.sheet_location import Link
        from bot.core.unassigned import unassigned_row
        a = make_puzzle(id=1, name="A")
        b = make_puzzle(id=2, name="B")
        urls = {1: "u/1", 2: None}
        assert unassigned_row(a, lambda p: urls[p.id])[1] == Link("A", "u/1")
        assert unassigned_row(b, lambda p: urls[p.id])[1] == Link("B", None)


class TestDiffRows:
    """diff_rows(current_ids, desired_rows) -> (appends, updates, prunes).
    desired_rows are [id, name, answer, length] lists (id is element 0)."""

    def test_new_desired_id_is_appended(self):
        from bot.core.unassigned import diff_rows
        desired = [[1, "A", "", ""], [2, "B", "ANS", 3]]
        appends, updates, prunes = diff_rows(current_ids=[1], desired_rows=desired)
        assert [r for r in appends] == [[2, "B", "ANS", 3]]
        assert prunes == []

    def test_missing_current_id_is_pruned(self):
        from bot.core.unassigned import diff_rows
        desired = [[1, "A", "", ""]]
        appends, updates, prunes = diff_rows(current_ids=[1, 9], desired_rows=desired)
        assert prunes == [9]
        assert appends == []

    def test_id_in_both_is_an_update(self):
        from bot.core.unassigned import diff_rows
        desired = [[1, "A", "ANS", 3]]
        appends, updates, prunes = diff_rows(current_ids=[1], desired_rows=desired)
        assert updates == [[1, "A", "ANS", 3]]
        assert appends == [] and prunes == []
