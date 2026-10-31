"""Pure helpers for the Unassigned Puzzles worktable reconcile."""
from bot.core.sheet_location import Link


def unassigned_row(puzzle, link=None):
    """Shape a puzzle into a sheet row: [id, name, ANSWER_UPPER, length].

    Unsolved puzzles get blank Answer and blank Length.
    Length = letter count excluding spaces (punctuation kept).
    link: optional callable puzzle -> url | None; when given the name cell is a Link.
    """
    if puzzle.solved and puzzle.solution:
        answer = puzzle.solution.upper()
        length = len(answer.replace(" ", ""))
    else:
        answer = ""
        length = ""

    name = Link(puzzle.name, link(puzzle)) if link is not None else puzzle.name
    return [puzzle.id, name, answer, length]


def diff_rows(current_ids, desired_rows):
    """Compare current sheet keys against desired rows.

    Args:
        current_ids: list of puzzle ids currently in the sheet (from read_unassigned_keys).
        desired_rows: list of [id, name, answer, length] rows from the DB.

    Keys may be ints (Unassigned puzzle ids) or strings (contribution row keys);
    the diff only requires row[0] be hashable and comparable. Shared by both tables.

    Returns:
        (appends, updates, prunes) where:
            appends: desired rows whose id is NOT in current_ids.
            updates: desired rows whose id IS in current_ids.
            prunes: current_ids NOT found in any desired row.
    """
    desired_ids = [row[0] for row in desired_rows]

    appends = [row for row in desired_rows if row[0] not in current_ids]
    updates = [row for row in desired_rows if row[0] in current_ids]
    prunes = [cid for cid in current_ids if cid not in desired_ids]

    return appends, updates, prunes
