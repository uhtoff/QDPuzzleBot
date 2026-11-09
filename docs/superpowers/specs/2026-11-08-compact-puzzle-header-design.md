# Compact puzzle-sheet header

Approved mockup: https://claude.ai/artifact/5MPVyTeLtkTwM4FCzhw5bX (version 4).

## Intent

Today every puzzle tab opens with a three-row, grey, frozen header that spans
columns A–Q: labels ("Puzzle Title:", "Solution:", "Puzzle Flavor Text:",
"Progress Summary:", "Feeds:") with merged value cells beside them. It takes a
lot of the sheet, solvers can't use those columns in rows 1–3, and the
flavour-text and progress cells go unused. The user wants the header reduced to
three cells in one column, leaving the rest of the sheet free for solving.

## The new ("compact") header

Three cells in the header column (column A on a puzzle tab):

| Cell | Content |
|---|---|
| A1 | The puzzle's name, a rich-text link to the puzzle's page on the hunt site (the URL B1 links to today). |
| A2 | `Unsolved`. On solve: the answer itself (no "Solved:" prefix), bold, bright green background. On unsolve: back to `Unsolved`, grey. |
| A3 | `Feeds: ` followed by the metas it feeds, each a rich-text link, separated by `, ` (only the names are linked). With none: `No links`. |

Formatting: the three cells are grey (`HEADER_GREY`) with the thin black border
on all edges, JetBrains Mono like the rest of the tab, and the header column
keeps the 220 px width. Nothing else in rows 1–3: no labels, no merges, no grey
or borders beyond the three cells. **No frozen rows.** The A2 answer is bold;
`Unsolved` is not.

**Meta tabs** (targets and minimetas): column A is the roll-up Table's hidden
id column, so the same three cells sit in **column B**, directly above the
roll-up Table, which keeps its current position (header row at row 4) and its
current columns. The roll-up Table is otherwise unchanged; the mockup's
"Length"/"Notes" columns are not part of this work.

**Additional sheets** (extra tabs a puzzle gets via `!add_sheet` / the drawer):
same as the puzzle's main tab minus A3: A1 title link, A2 status. (Today they
carry only the B1 name and receive the I1/B3 solve writes.) *Builder's call,
not discussed with the user — flag if it looks wrong.*

## Which puzzles get it: a per-hunt setting

The header style is a **hunt setting**, `header_style`, `'full'` (today's
header) or `'compact'`. A puzzle gets the style its hunt has **at the moment the
puzzle is created**, and keeps it for its whole life. Changing the setting
mid-hunt affects only puzzles created afterwards (like `voice_per_puzzle`), so a
hunt can contain both.

- **New hunts default to `compact`** ("for the moment": the default is one
  constant, easy to flip later).
- **Hunts that exist when this ships are set to `full`** by the migration, so
  nothing changes under a running hunt. *(Builder's call; the user asked only
  that the default be compact.)*
- **Where it's set** (the same surfaces as the hunt's other options):
  - hunt creation: `!hunt` accepts a trailing `, full` token (like `, files` /
    `, voice`; omitted = compact), and the dashboard's create-hunt form has a
    "Header style" choice defaulting to Compact;
  - mid-hunt: `!update_settings header_style full|compact` in the hunt, and the
    dashboard's hunt settings panel (same permission as the other hunt
    settings there), with readable labels ("Full header" / "Compact header").

Storage, migration **060** (058 is reserved by the `drop-dead-columns` branch):

- `hunts.header_style` VARCHAR, existing rows `'full'`, default for new rows
  `'compact'`.
- `puzzles.header_layout` VARCHAR, existing rows `'full'`, column default
  `'full'`, so code that predates this (e.g. in the migrate→restart window)
  still creates correctly-labelled full puzzles. The create path writes the
  hunt's current `header_style` onto the new puzzle explicitly, before the tab
  is generated. One per-puzzle value governs all of the puzzle's tabs (main and
  additional).



The layout is read from the flag, never inferred from sheet contents: solvers
edit these cells, and the existing `_tab_layout` probe (which looks for the
"Puzzle Title:" label) can't see a compact header.

Minis: a mini has no tab of its own today; nothing changes.

## Behaviour per operation (compact puzzles)

Full puzzles keep exactly today's behaviour on every row below.

- **Create** (tabs and files mode, including the files-mode retry and the
  reconcile re-creates): write the three cells and their formatting; no frozen
  rows, no labels, no merges. A3 gets the feeds links or `No links`.
- **Solve**: A2 = answer, bold, green. Additional sheets: their A2 likewise.
- **Change/add answer** (`update_solution` callers): A2 = new answer.
- **Unsolve**: A2 = `Unsolved`, not bold, grey; additional sheets likewise.
- **Rename / URL change**: rewrite A1's linked name.
- **Feeds change** (every `refresh_header_links` caller): rewrite A3. No
  "re-stamp row 3 into the Feeds layout" upgrade step (that exists only for old
  full tabs).
- **Promote to meta / minimeta in place**: the header moves from column A to
  column B (because column A becomes the hidden id column) and the roll-up
  Table is added at row 4 as today. Values survive: A2's status/answer (and its
  green), A1, A3. There is no flavour or progress to carry.
- **Demote** (remove minimeta, convert back to puzzle): the reverse, B → A.
- **Solved-folder / solved-sheet moves, archive**: unchanged (they move the
  whole tab or file).

## Structure

Today the cell addresses come from three global config strings
(`config.puzzle_cell_name` B1, `puzzle_cell_solution` I1,
`puzzle_cell_progress` B3), the feeds cell from `sheet_layout.FEEDS_LINKS_A1`,
and the column shift from `GoogleSheets._header_shift`. Two layouts means those
can no longer be global: introduce one per-layout geometry (cell refs for
title/status/feeds, whether there is a progress cell, frozen rows, header
extent, labels, merges) chosen by the puzzle's `header_layout`, and route the
existing write sites through it. `_header_shift` already funnels most of them
and is the natural place to grow into this. The full geometry must reproduce
today's requests exactly, so the existing tests keep passing unchanged against
full puzzles.

The complete inventory of header write sites (53 rows, with file:line) is in the
worktree at `.ai/active/header-inventory.md`; the plan must cover every site it
lists.

## Builder's calls (settled after the builder check; not discussed with the user)

1. **Where the flag is set.** `PuzzleService`'s create path writes
   `header_layout` (copied from the hunt's `header_style`) on the new puzzle row before calling the sheets
   cog; `generate_puzzle_tab` and every other writer read it from the puzzle
   object. Reconcile re-creates read the existing row, so an old puzzle's
   re-created tab stays full. Minis get no tab and no flag of their own;
   additional sheets follow their puzzle's flag.
2. **Promote/demote detection.** For compact puzzles the "Puzzle Title:" probe
   is bypassed: the flag plus the puzzle's existing DB state (`is_target` /
   `is_minimeta`, Table present or not) decide what to do. Promote must be
   idempotent: a compact tab that already has its contribution Table is not
   promoted again. Full tabs keep the probe and its "unknown layout" guard.
3. **What promote/demote move.** Only the three header cells (values and
   formatting) move between column A and column B. Nothing else in rows 1–3 is
   cleared: solvers may use that space now. Columns are inserted and deleted
   exactly as today (the Table's columns), so solver content shifts with them
   as it does today.
4. **A3 overflow.** No wrap, no merge. A long feeds list spills into B when B is
   empty and is clipped otherwise.
5. **Solve/unsolve** overwrite A2 unconditionally, as solve overwrites I1 today.
6. **Shift timing.** Which column the header is in is decided as today
   (`is_target` / `is_minimeta` on the puzzle and whether it's the main tab), so
   a compact puzzle that is flagged as a minimeta but not yet lazily promoted
   behaves as a full one does today.
7. **Call budgets.** Compact puzzles may use a different number of Sheets
   requests than full ones. Full counts in `test_sheet_call_volume.py`
   stay exactly as they are; compact gets its own pinned counts.

## Out of scope

- Converting existing puzzles' headers, including when a hunt's setting
  changes.
- Any change to the roll-up / contribution Table, the Unassigned tables, or
  Table placement.
- Flavour text and progress summary: they simply don't exist in the compact
  layout. (The dashboard doesn't read them; check during planning that nothing
  else does.)
- The `config.json` `puzzle_cell_*` keys stay, describing the full layout.

## Acceptance

- A new puzzle in a compact tabs hunt and in a compact files hunt shows exactly the mockup's
  header; an old puzzle in the same hunt keeps the full header through
  solve, unsolve, rename and feeds changes.
- Solve/unsolve/rename/feeds changes on a compact puzzle touch only A1–A3 (B1–B3
  on a meta tab) and never write the full cells (I1, B3, I3, H-column labels).
- Promoting a compact puzzle to a meta and demoting it back keeps its status,
  answer and feeds, with the header in column B while it's a meta.
- Existing tests pass unchanged; new tests cover each operation above for a
  compact puzzle.
- A new hunt is `compact` unless `, full` (or the dashboard choice) says
  otherwise; existing hunts read `full` after the migration; changing
  `header_style` mid-hunt (bang command or dashboard) changes only puzzles
  created afterwards.
