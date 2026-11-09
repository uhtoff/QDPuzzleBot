"""Captured header layout for code-generated puzzle tabs (Milestone D).

Replaces the hidden "Tab template" / "Meta Tab template" tabs: the static header
cells they carry are captured here as data (read once from the master template
spreadsheet via the Sheets API — see scripts/capture_template_layout.py), so
every puzzle tab is generated in code.

Captured 2026-06-25 from master spreadsheet "Tab template" (sheetId 1971123506)
and "Meta Tab template" (sheetId 42737759); both 3 frozen header rows.

Cell roles in the template (these describe the FULL layout; the compact layout
is COMPACT_GEOMETRY below) (do NOT re-emit the dynamic ones from here — the bot
writes them at runtime from config.puzzle_cell_*):
  * B1  name/url hyperlink   -> config.puzzle_cell_name / _link  (DYNAMIC)
  * I1  solution             -> config.puzzle_cell_solution      (DYNAMIC)
  * B3  progress ("none")    -> config.puzzle_cell_progress      (DYNAMIC)
  * I3  feeds links          -> GoogleSheets.header_links_request (DYNAMIC)
  * B2  flavour text         -> user-owned (NOT bot-written); preserved on promote
  * A1/H1/A2/A3/H3 labels    -> STATIC (this artifact)
The Meta tab's old rollup region (A4 notice, A5/B5 "Puzzle name"/"Puzzle solution")
is intentionally NOT reproduced — the generated contribution Table replaces it.

A cell spec is {"a1": str, "value": str, "type": "stringValue"|"formulaValue",
"bold": bool}. A merge spec is (start_row, end_row, start_col, end_col), 0-based
half-open (Sheets GridRange form).

The full layout's dynamic cell addresses and header extents are described by
``HeaderGeometry``; GoogleSheets builds one per write instead of reading
config.puzzle_cell_* / FROZEN_ROWS directly, so another layout can plug in.
"""

import dataclasses
from dataclasses import dataclass
from typing import Any



@dataclass(frozen=True)
class HeaderGeometry:
    """Where a puzzle tab's dynamic header cells sit and how much of the tab the
    header band occupies. Built per write by GoogleSheets._header_geometry."""
    layout: str            # HEADER_FULL | HEADER_COMPACT
    title_a1: Any          # name/title cell (full: config.puzzle_cell_name)
    status_a1: Any         # solution/status cell (full: config.puzzle_cell_solution)
    progress_a1: Any       # full: config.puzzle_cell_progress; compact: None
    feeds_a1: str | None   # full: FEEDS_LINKS_A1; compact main: "A3"; compact extra: None
    frozen_rows: int       # full: FROZEN_ROWS; compact: 0
    band_rows: int         # rows the header occupies (grey/border): full FROZEN_ROWS
    band_cols: int         # columns bordered from the header column: full 17
    grey_whole_rows: bool  # full: True (grey spans the whole rows); compact: False


FROZEN_ROWS = 3

# Compact layout: title / status / feeds in A1:A3 of the header column, no labels,
# merges or frozen rows. An additional sheet has no feeds line, so its band is A1:A2.
COMPACT_GEOMETRY = HeaderGeometry("compact", "A1", "A2", None, "A3", 0, 3, 1, False)
COMPACT_EXTRA_GEOMETRY = dataclasses.replace(COMPACT_GEOMETRY, feeds_a1=None, band_rows=2)

# Static header labels shared by every puzzle tab.
PUZZLE_TAB_CELLS = [
    {"a1": "A1", "value": "Puzzle Title:", "type": "stringValue", "bold": True},
    {"a1": "H1", "value": "Solution:", "type": "stringValue", "bold": True},
    {"a1": "A2", "value": "Puzzle Flavor Text:", "type": "stringValue", "bold": True},
    {"a1": "A3", "value": "Progress Summary:", "type": "stringValue", "bold": True},
    # Under "Solution:"; the bot writes the linked meta names into I3.
    {"a1": "H3", "value": "Feeds:", "type": "stringValue", "bold": True},
]

# Header merges shared by every puzzle tab (title, solution, flavour, progress,
# feeds). Row 3 was one B3:Q3 progress merge until the Feeds line (2026-10-31).
PUZZLE_TAB_MERGES = [
    (0, 1, 1, 7),    # B1:G1  title (bot writes the hyperlink here)
    (0, 1, 8, 17),   # I1:Q1  solution (bot writes here)
    (1, 2, 1, 17),   # B2:Q2  flavour text (user-owned)
    (2, 3, 1, 7),    # B3:G3  progress (bot writes "none"/"Solved" here)
    (2, 3, 8, 17),   # I3:Q3  feeds links (bot writes here)
]

# The Feeds line: label cell (in PUZZLE_TAB_CELLS) and the cell the linked meta
# names go in -- the top-left of the I3:Q3 merge. Both shift with the header.
FEEDS_LABEL_A1 = "H3"
FEEDS_LINKS_A1 = "I3"

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
