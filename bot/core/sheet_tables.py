"""Locating the bot's own native Table on a spreadsheet tab.

A tab may hold more than one native Table: the bot writes one, and a solver is
free to add their own beside it. The Sheets API returns `Sheet.tables` in an
order it does not document — and reordering is not even user-visible, since
moving a table around the tab is enough to change it. Picking `tables[0]` was
therefore a coin flip, and when it came up wrong the reconciler wrote a target's
feeder rows into a solver's table (and resized that table to cover them).

Every Table the bot creates is given a name it chooses, so the name is the
deterministic handle: match on it, and otherwise return nothing so the caller
can create its own Table (or refuse to write) rather than guess.

Matching is STRICT — there is deliberately no "lone table on the tab must be
ours" fallback. That fallback existed for tabs whose Table predated the naming,
and it silently re-created the bug it was meant to fix: a tab whose own Table
creation was deferred (generate_puzzle_tab tolerates a transient Sheets 500)
holds no Table of ours, so a solver's table is the lone one and was handed the
target's rollup. It also made "any Table is ours" the demote guard in
remove_minimeta/convert_to_puzzle, which deletes columns A-D of an unpromoted
tab. A name miss now means "we have no Table here", which is what lets
_ensure_contribution_table actually create one.

Legacy names, for the record: contribution Tables were created unnamed
(095faaa), then "Contributions" (4cdd7e7), then named after the tab itself
(7532c15), and have been `f"Contributions {page_id}"` since 8c69ba7. The owner
confirmed no live spreadsheet still carries one of the older forms. If a rollup
ever does come back blank on an old sheet, the one-line remedy is an allowlist
of those legacy names here — not a return of the positional fallback.
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


def select_table(tables, name=None):
    """The bot's Table among `tables` (a Sheet's `tables` list), or None.

    None means "the bot has no Table on this tab": either there is no Table at
    all, or none of the ones present carries the name the bot gave its own. Any
    other pick would be a guess at a solver's table — see the module docstring
    for why the lone-table fallback is gone.
    """
    for t in tables or []:
        if name is not None and t.get("name") == name:
            return t
    return None
