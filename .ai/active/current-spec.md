# Current Spec

## Goal

Fix two production bugs and a formatting regression introduced by Milestone D
(code-generated sheet tabs):

1. **Meta formation fails** — web returns 500, bot says "Google Sheets had an
   error". Root cause (confirmed in discord.log): `generate_puzzle_tab` writes the
   header merges (e.g. `B1:G1`) then `_add_contribution_table` inserts a native
   Table at row 1, cols A–D, overlapping the merge. Sheets rejects:
   *"You can't insert a table over a merged range."*
2. **New web hunt has no welcome message** — the web `create_hunt` route never
   posts/pins the initial hunt-channel message; the bang `!newhunt` does it via
   `send_initial_hunt_channel_messages` (`bot/cogs/puzzles.py:441`).
3. **Header formatting lost** — code-generated tabs set no font, background,
   column width, or frozen rows (the old duplicateSheet path inherited these from
   the template tab).
4. **Unassigned Feeders (and the shared reconciler) don't populate** — two bugs in
   `reconcile_table` / `apply_unassigned_changes`:
   a. `reconcile_table._cell` writes every value as `stringValue`; the Unassigned
      **Length** column is number-typed and rejects a string (esp. `""` when
      unsolved), failing the whole atomic batch (see `_unassigned_len_cell`, whose
      numeric handling was dropped in the shared-reconciler retrofit).
   b. Double-diff: `refresh_unassigned` diffs, then `apply_unassigned_changes`
      forwards only `appends+updates` to `reconcile_table`, which diffs AGAIN vs
      the live sheet — pruning every unchanged existing row each refresh.
5. **Rename to "Unassigned Feeders" incomplete** — tab title, native Table name,
   and any "Unassigned Puzzles" label still say the old name (refresh_unassigned
   `tab_name`, `ensure_unassigned_table`).
6. **Rows written outside the native Table range (headers decorative)** —
   `_add_contribution_table` / `ensure_unassigned_table` create a 1-row (header
   only) Table; `reconcile_table` writes data rows below it via raw `updateCells`
   but never resizes the Table, so the rows are not part of the Table and the
   header is purely cosmetic. The reconciler must `updateTable` the range to cover
   header + all data rows after writing (applies to both tables).

## In scope

- Contribution table starts at **row 4** (0-based header row index 3, below the 3
  frozen header rows) **everywhere it renders on a puzzle/target tab**: the
  fresh-generate path (`generate_puzzle_tab`) AND the in-place promote/demote path
  (`promote_tab_to_target` / `demote_tab_to_puzzle`).
- `reconcile_table` made header-row aware so it skips the label rows above the
  table (A1–A3 hold "Puzzle Title:/Flavor/Progress") instead of treating them as
  table keys (which would prune/delete them).
- Web `create_hunt` posts + pins the welcome message (parity with `!newhunt`).
- Header formatting on **all** generated tabs (feeders and targets):
  - Font **JetBrains Mono**.
  - Column A widened to fit the label text.
  - Grey background on the top 3 rows.
  - Top 3 rows frozen (`frozenRowCount=3`).
  - Solution cell (`I1`) bright green **only when the puzzle is solved** (grey
    until then).

## Out of scope

- The **Unassigned Feeders** tab and the **Evidence** tab keep their current
  layout/placement — the row-4 rule does NOT apply to them.
- No change to the feeder→meta edge model, web SPA, or unrelated commands.

## Relevant files

- `bot/cogs/puzzles_gsheet.py` — `generate_puzzle_tab`, `_add_contribution_table`,
  `reconcile_table`, `render_contribution`, `promote_tab_to_target`,
  `demote_tab_to_puzzle`, `update_puzzle_info`, solve/solution writers.
- `bot/cogs/sheet_layout.py` — captured header cells/merges; `FROZEN_ROWS=3`.
- `bot/web/actions.py` — `create_hunt` route (welcome parity).
- `bot/cogs/puzzles.py` — `send_initial_hunt_channel_messages`, `_overview_url`.
- `tests/test_contribution_table_requests.py` — request-shape tests.

## Required behaviour

- Generating a target tab no longer overlaps a merged range (no 500 / Sheets
  error); the contribution table header sits at row 4.
- A target with no members reconciles without deleting the header label rows.
- Promote/demote produce the same row-4 geometry as a freshly generated target.
- Creating a hunt on the web posts and pins the welcome embed in the hunt channel.
- Every generated tab uses JetBrains Mono, wide col A, grey frozen 3-row header;
  the solution cell turns bright green when (and only when) solved.

## Constraints

- Mocked tests can't catch server-side merge/format validation — add geometric
  invariants (table range must not overlap any merge) where feasible.
- Keep changes narrow; preserve existing naming/patterns. Commit per phase.

## Acceptance criteria

- `timeout 120 <pytest> tests/test_contribution_table_requests.py` green, with a
  new test asserting the target table range does not overlap any merge.
- Manual: create a meta on web + bot → succeeds, tab formatted, table at row 4.
- Manual: create a hunt on web → welcome message pinned in hunt channel.

## Phased plan (execution order)

1. **Web hunt welcome** — isolated, low risk. (`actions.py`, maybe a web helper
   on the cog.)
2. **Table at row 4 + reconcile header-awareness** — `generate_puzzle_tab` adds
   table at `header_row=FROZEN_ROWS`; `reconcile_table`/`render_contribution`
   thread a `header_row`; rework promote/demote to the row-4 geometry. Update +
   extend tests.
3. **Header formatting** — font, col-A width, grey 3-row header, freeze 3 rows in
   `generate_puzzle_tab`; solution cell green wired into the solve path.
