"""Pure row planning for GoogleSheets.reconcile_table — no Sheets calls, no I/O.

Given the data rows currently inside a bot-managed Table and the full desired row
set, decide which rows are rewritten in place, which are deleted (the caller
removes them with deleteRange across the Table's full width, so rows below shift
up together with any solver columns inside the Table), and where appends land.

A member whose key changes but who is still the same puzzle — a capstone -> meta
convert turns ``informs:12`` into ``feeds:12`` — keeps its row (re-keyed in place),
so notes solvers keep in the Table's own columns stay beside it.
"""
from dataclasses import dataclass, field


def member_identity(key: str) -> str | None:
    """The puzzle a contribution-row key belongs to, or None when the key is not a
    per-member key (hunt-level evidence ``informs:free:N``, the Evidence and
    Unassigned tables' bare ids, an empty cell). Keys are ``<edge>:<puzzle_id>``
    with an optional ``:<evidence_id>`` suffix."""
    parts = str(key).split(":")
    if len(parts) >= 2 and parts[1].isdigit():
        return parts[1]
    return None


@dataclass
class RowPlan:
    updates: list = field(default_factory=list)    # (grid_row, desired_row), top-down
    deletes: list = field(default_factory=list)    # grid rows, descending (bottom-up)
    appends: list = field(default_factory=list)    # desired rows to add below the table
    append_start: int = 0                          # grid row of the first append, post-delete
    end_row: int = 0                               # exclusive end of the Table after the batch


def plan_rows(rows, desired_rows, header_row: int) -> RowPlan:
    """Plan a reconcile.

    rows         : every data row inside the Table's current extent, as
                   ``(grid_row, cells)``; ``cells[0]`` is the key cell and the rest
                   are the other in-Table cells as read back (possibly short).
    desired_rows : the FULL desired set; ``row[0]`` is the key.
    header_row   : grid row of the Table header.

    Keyed rows whose key is desired are updated in place. A keyed row whose key is
    gone first tries to take a not-yet-placed desired row of the same member
    (top-down, first match in desired order) and is re-keyed in place; otherwise it
    is deleted. Keyless rows are deleted when every cell is empty and kept when any
    cell holds something (a solver note stranded by the old blank-in-place rule).
    """
    rows = sorted(rows, key=lambda r: r[0])
    current_keys = {str(cells[0]) for _, cells in rows if cells and str(cells[0])}
    pending = [r for r in desired_rows if str(r[0]) not in current_keys]
    desired_by_key = {str(r[0]): r for r in desired_rows}

    plan = RowPlan()
    deletes = []
    for grid_row, cells in rows:
        key = str(cells[0]) if cells else ""
        if not key:
            if not any(str(c).strip() for c in cells):
                deletes.append(grid_row)
            continue
        if key in desired_by_key:
            plan.updates.append((grid_row, desired_by_key[key]))
            continue
        ident = member_identity(key)
        match = None
        if ident is not None:
            match = next((r for r in pending if member_identity(str(r[0])) == ident), None)
        if match is not None:
            pending.remove(match)
            plan.updates.append((grid_row, match))
        else:
            deletes.append(grid_row)

    plan.deletes = sorted(deletes, reverse=True)
    plan.appends = pending
    plan.append_start = header_row + 1 + len(rows) - len(plan.deletes)
    plan.end_row = plan.append_start + len(plan.appends)
    return plan
