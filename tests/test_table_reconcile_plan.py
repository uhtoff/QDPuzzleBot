"""The pure row planner behind reconcile_table: which rows are rewritten in place
(including a member re-keyed by a type convert), which are deleted with the rows
below shifting up, where appends land after compaction, and where the Table ends."""
from bot.core.table_reconcile import member_identity, plan_rows


def test_member_identity():
    assert member_identity("feeds:12") == "12"
    assert member_identity("informs:12:55") == "12"
    assert member_identity("none:12") == "12"
    assert member_identity("informs:free:55") is None
    assert member_identity("7") is None and member_identity("") is None


def test_rekeys_changed_edge_in_place():
    p = plan_rows([(5, ["informs:12", "x"]), (6, ["informs:13", "y"])],
                  [["feeds:12", "One"], ["feeds:13", "Two"]], header_row=4)
    assert p.updates == [(5, ["feeds:12", "One"]), (6, ["feeds:13", "Two"])]
    assert p.deletes == [] and p.appends == []
    assert p.end_row == 7


def test_departed_rows_deleted_bottom_up_and_appends_after_compaction():
    p = plan_rows([(5, ["feeds:1"]), (6, ["feeds:2"]), (7, ["feeds:3"]), (8, ["feeds:4"])],
                  [["feeds:2", "B"], ["feeds:4", "D"], ["feeds:9", "Z"]], header_row=4)
    assert p.deletes == [7, 5]
    assert p.updates == [(6, ["feeds:2", "B"]), (8, ["feeds:4", "D"])]
    assert p.appends == [["feeds:9", "Z"]]
    assert p.append_start == 7 and p.end_row == 8


def test_old_blank_rows_deleted_but_stranded_note_row_is_kept():
    p = plan_rows([(5, ["", "", "", ""]), (6, ["", "", "", "", "my note"]),
                   (7, ["feeds:2"])], [["feeds:2", "B"]], header_row=4)
    assert p.deletes == [5]          # 6 holds a solver note in an in-Table column
    assert p.updates == [(7, ["feeds:2", "B"])]


def test_many_rows_one_member_rekeys_topmost():
    p = plan_rows([(5, ["informs:12:5"]), (6, ["informs:12:6"])],
                  [["feeds:12", "One"]], header_row=4)
    assert p.updates == [(5, ["feeds:12", "One"])] and p.deletes == [6]


def test_non_member_keys_never_rekey():
    p = plan_rows([(1, ["7"]), (2, ["informs:free:55"])],
                  [["8", "x"], ["informs:free:56", "y"]], header_row=0)
    assert p.deletes == [2, 1] and len(p.appends) == 2


def test_stranded_note_row_is_kept():
    p = plan_rows([(5, ["", "", "", "", "note"])], [], header_row=4)
    assert p.deletes == [] and p.end_row == 6
