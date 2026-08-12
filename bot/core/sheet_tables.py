"""Locating the bot's own native Table on a spreadsheet tab.

A tab may hold more than one native Table: the bot writes one, and a solver is
free to add their own beside it. The Sheets API returns `Sheet.tables` in an
order it does not document — and reordering is not even user-visible, since
moving a table around the tab is enough to change it. Picking `tables[0]` was
therefore a coin flip, and when it came up wrong the reconciler wrote a target's
feeder rows into a solver's table (and resized that table to cover them).

Every table the bot creates is given a name it chooses, so the name is the
deterministic handle: match on it, fall back to the sole table on a tab (older
tabs whose table was created before the naming settled), and otherwise return
nothing so the caller can refuse rather than guess.
"""

UNASSIGNED_TABLE_NAME = "Unassigned Feeders"
EVIDENCE_TABLE_NAME = "Evidence"


def contribution_table_name(page_id):
    """The name of a target tab's contribution Table.

    Derived from page_id because Table names must be unique within the whole
    spreadsheet and Google rejects '-' and apostrophes — a tab-derived name
    breaks addTable for a "X - meta" round tab. Creation and lookup both come
    through here so the two cannot drift.
    """
    return f"Contributions {page_id}"


def feeder_matrix_table_name(page_id):
    """The name of a feeder matrix tab's Table.

    Page-derived for the same reason as contribution_table_name: Table names must
    be unique across the whole spreadsheet, and Google rejects '-' and
    apostrophes. A matrix tab holds exactly one Table, so this name is also what
    keeps it clear of select_table's lone-unnamed-table fallback trap.
    """
    return f"Feeder Matrix {page_id}"


def select_table(tables, name=None):
    """The bot's Table among `tables` (a Sheet's `tables` list), or None.

    None means "do not write": either the tab has no Table at all, or it has
    several and none of them is ours, in which case any choice would be a guess
    at a solver's table.
    """
    tables = tables or []
    if name is not None:
        for t in tables:
            if t.get("name") == name:
                return t
    # A single Table predating the naming above is unambiguously the bot's: the
    # bot is what put a Table on a target/unassigned/evidence tab in the first
    # place. With two or more and no name match, it is not.
    if len(tables) == 1:
        return tables[0]
    return None
