"""Captured header layout for code-generated puzzle tabs (Milestone D).

Replaces the hidden "Tab template" / "Meta Tab template" tabs: the static header
cells they carry are captured here as data (read once from the master template
spreadsheet via the Sheets API — see scripts/capture_template_layout.py), so
every puzzle tab is generated in code.

Captured 2026-06-25 from master spreadsheet "Tab template" (sheetId 1971123506)
and "Meta Tab template" (sheetId 42737759); both 3 frozen header rows.

Cell roles in the template (do NOT re-emit the dynamic ones from here — the bot
writes them at runtime from config.puzzle_cell_*):
  * B1  name/url hyperlink   -> config.puzzle_cell_name / _link  (DYNAMIC)
  * I1  solution             -> config.puzzle_cell_solution      (DYNAMIC)
  * B3  progress ("none")    -> config.puzzle_cell_progress      (DYNAMIC)
  * B2  flavour text         -> user-owned (NOT bot-written); preserved on promote
  * A1/H1/A2/A3 labels       -> STATIC (this artifact)
The Meta tab's old rollup region (A4 notice, A5/B5 "Puzzle name"/"Puzzle solution")
is intentionally NOT reproduced — the generated contribution Table replaces it.

A cell spec is {"a1": str, "value": str, "type": "stringValue"|"formulaValue",
"bold": bool}. A merge spec is (start_row, end_row, start_col, end_col), 0-based
half-open (Sheets GridRange form).
"""

FROZEN_ROWS = 3

# Static header labels shared by every puzzle tab.
PUZZLE_TAB_CELLS = [
    {"a1": "A1", "value": "Puzzle Title:", "type": "stringValue", "bold": True},
    {"a1": "H1", "value": "Solution:", "type": "stringValue", "bold": True},
    {"a1": "A2", "value": "Puzzle Flavor Text:", "type": "stringValue", "bold": True},
    {"a1": "A3", "value": "Progress Summary:", "type": "stringValue", "bold": True},
]

# Header merges shared by every puzzle tab (title, solution, flavour, progress).
PUZZLE_TAB_MERGES = [
    (0, 1, 1, 7),    # B1:G1  title (bot writes the hyperlink here)
    (0, 1, 8, 17),   # I1:Q1  solution (bot writes here)
    (1, 2, 1, 17),   # B2:Q2  flavour text (user-owned)
    (2, 3, 1, 17),   # B3:Q3  progress (bot writes "none"/"Solved" here)
]

# A target tab adds nothing static beyond the shared header — the contribution
# Table (added in code, below the frozen header) replaces the old A4/A5 rollup
# region. Kept as an explicit empty list so the generator's branch is obvious.
META_TAB_EXTRA_CELLS: list[dict] = []
META_TAB_EXTRA_MERGES: list[tuple] = []


def puzzle_tab_layout(is_target):
    """Return (cells, merges) for a puzzle tab. is_target adds the meta-only
    static cells (currently none — the contribution Table is added separately)."""
    cells = list(PUZZLE_TAB_CELLS)
    merges = list(PUZZLE_TAB_MERGES)
    if is_target:
        cells += META_TAB_EXTRA_CELLS
        merges += META_TAB_EXTRA_MERGES
    return cells, merges
