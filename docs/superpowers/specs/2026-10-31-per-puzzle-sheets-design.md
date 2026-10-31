# Per-puzzle sheets, and links between puzzle sheets

**Date:** 2026-10-31
**Status:** Approved 2026-10-31
**Roadmap:** item 2 of the agreed order (`docs/PLANS.md`, "Agreed order (2026-10-16)"), plus
the "link puzzle names in every roll-up table" item from item 3, pulled forward.

## Intent

Today every hunt is one Google spreadsheet with a tab per puzzle. Over a hunt it gets huge:
slow to open, close to unusable on a phone, and the tab bar — which is how people actually
get around, even though the dashboard is meant for that — becomes a scroll-and-hunt
exercise. Moving solved puzzles to a separate solved spreadsheet trims the size a bit, but
it changes the puzzle's link, so anyone with that tab open or bookmarked is left on a sheet
that's gone (user, 2026-10-31).

This project adds a second way of running a hunt's sheets — **one spreadsheet file per
puzzle**, in a Drive folder per hunt — chosen per hunt. A file keeps its link for life,
including when it's solved. Small files are quick to open on a phone.

Taking the tab bar away means the sheets themselves have to carry the navigation. So the
project also adds **links between puzzle sheets**, and those go into **every** hunt, tab
hunts included (user correction, 2026-10-31):

- **Up:** each puzzle's header links to the sheets of the puzzles it **feeds** — its meta,
  capstone, metameta — so you can get from a feeder to its meta in one click.
- **Down:** in every roll-up table, each puzzle's name links to that puzzle's sheet.

To find any other puzzle, people use the dashboard, or hop through the meta ("click to the
meta and back out" — user).

## Goals

1. **A per-hunt sheet mode:** `tabs` (today's behaviour, the default) or `files` (one file per
   puzzle). Chosen when the hunt is created. The user's framing: it's an option because
   people need convincing, and what will convince them is **navigation that is as easy as or
   easier than tabs**.
2. **In a files hunt:**
   - a Drive **folder per hunt**, with a **Solved** subfolder;
   - one **reference spreadsheet** per hunt holding the template's **REFERENCE** tab,
     **Unassigned Feeders**, **Evidence**, and the hunt's loose working sheets — "the place
     for central work" (user decision). No puzzle tabs. People keep
     it open beside the puzzle they're working on ("easier to use Reference if it's in
     another file" — user);
   - a **link to the reference spreadsheet in the welcome messages** (user decision);
   - **one file per puzzle**, holding only that puzzle: its main tab (with the header the
     bot writes today) plus that puzzle's extra sheets. No REFERENCE tab in puzzle files;
   - a **minimeta's file also holds its minis**, one tab each. Solving a mini colours its tab
     green and moves it to the end of the file (user decision); it doesn't leave the file.
   - **solving moves the file into Solved** (unsolve moves it back) — its link never changes,
     and there is no separate solved spreadsheet in this mode;
   - every link that points at a puzzle's sheet today (Discord channel messages, the
     dashboard board/drawer, the overview website, `!reconcile`) points at its file.
3. **Links, in every hunt:**
   - **Header up-links:** a puzzle's header shows a link to each puzzle it **feeds** (not
     ones it only **informs** — user decision). Kept current by the bot as tags change.
   - **Roll-up down-links:** in the contribution tables on metas, capstones and metametas,
     the minimeta tables, and the Unassigned Feeders tab, each puzzle's name links to its
     sheet.

## Non-goals

- Switching a hunt between modes once it has puzzles or rounds.
- A list/index of every puzzle in the reference spreadsheet. The dashboard is the index
  (user).
- Header links to anything other than the puzzles it feeds (user: "in terms of other sheets,
  just the puzzles that it feeds").
- Keeping blank copies ready in advance so file links appear instantly. A short wait per
  sheet is fine (user).
- Activity indicators, feeder-solve announcements, the feeders-worktable redesign — later
  roadmap items, though files are the groundwork for sheet-edit activity.
- Links in the Evidence tab. (My call; easy to add later.)

## Anti-goals

- **Breaking anything that works today** — the user's one wince. Above all, a tab hunt must
  behave exactly as now apart from the new links: same tabs, same header geometry (Table at
  row 4, label column, target header shift), same solve/unsolve to the solved spreadsheet,
  same reconcile results. Hunts already running when this ships must keep working untouched.
- **A mode that rots.** The forum layout shows what happens to a second, less-used mode.
  Shared paths (create, rename, solve, unsolve, delete, tag, reconcile, roll-up rendering)
  must run through one seam with tests for both modes, not a parallel copy of the code.
- **Formula links.** Names are written as plain text with a rich-text link
  (`textFormatRuns` → `link.uri`), never `=HYPERLINK()`: the reconciler reads cells back as
  text, and a puzzle name starting with `=` can't become a formula (`docs/PLANS.md`).

## Decided

### Choosing the mode

- A hunt-level setting, default `tabs`, set at creation. It **can be switched until the hunt
  has any puzzle or round/group** (user decision, 2026-10-31); after that it is fixed and a
  switch is refused with the reason. Switching leaves the hunt exactly as if it had been
  created in the new mode (folder, Solved subfolder and reference spreadsheet made, or the
  reverse; a solved-sheet setting cleared on the way to files). How the existing hunt
  spreadsheet is reused or replaced is the builder's call. Offered on the dashboard's hunt
  settings panel and as a command.
- Offered everywhere a hunt is created (command-parity principle): the dashboard's
  create-hunt form, the team-server setup page's Advanced section (the 5c spec already
  reserved a place for it there), and `!hunt` / `!setup_server`. Exact command syntax is the
  builder's, but every surface must be able to choose `files`.
- `!use_solved_sheet` and the dashboard's solved-sheet option don't apply to a files hunt:
  refused with a reason, not silently ignored.

### Files mode — what gets made, and where

- **Hunt creation:** create the hunt folder (and its Solved subfolder), then the reference
  spreadsheet: a copy of the master template trimmed to REFERENCE, then the Unassigned
  Feeders and Evidence tabs set up as today. The reference spreadsheet is the hunt's
  `google_sheet_id`, so `evidence_sheet_page_id` / `unassigned_sheet_page_id` keep pointing
  at tabs inside it and the existing Evidence/Unassigned code keeps working.
- **Puzzle creation:** copy the master template into the hunt folder, trim it to the puzzle
  tab (one batchUpdate with the header writes, as the 2026-10-16 spike did), set
  `importFunctionsExternalUrlAccessAllowed` so `IMAGE()`-based named functions don't prompt
  per file, share anyone-with-link as writer (as today's hunt spreadsheet is). The Discord
  channel and dashboard entry appear at once; the sheet link appears when the copy finishes
  (spike: ~2.4–3 s, occasional spikes to ~12 s — acceptable, user).
- **A failed copy:** creation stays fire-and-forget (the channel and dashboard entry already
  exist). If the copy fails after retries, the bot says so in the puzzle's channel and points
  at `!reconcile apply`, which is the recovery path (below). No separate "pending sheet" state
  or re-create command. (My call.)
- **`!reconcile` in a files hunt** lists the hunt folder and repairs: a puzzle with no file
  (creates it), a file in the wrong folder for its solved state (moves it), a file name that
  doesn't match the puzzle (renames it). Stray files in the folder are reported, never
  deleted. The existing roll-up table and Unassigned/Evidence checks run against the files as
  they do against tabs today. (My call.)
- **Rename:** renames the file too. **Delete:** deletes the file. **Hunt delete:** deletes
  the hunt's files and folder, as it deletes the spreadsheet today.
- **Solve / unsolve:** move the file between the hunt folder and Solved. One Drive call; the
  file id and link are unchanged.
- **Loose hunt sheets** (`add_hunt_sheet`, `!list_sheets`) are tabs in the reference
  spreadsheet, as they are tabs in the hunt spreadsheet today (user decision).
- **Minis** are tabs in their minimeta's file, created there when the mini is made (after the
  minimeta's own copy has finished). On solve the tab turns green and moves to the end; on
  unsolve it loses the colour and moves back in front of the solved minis (my call). A mini's
  location resolves through its parent: it is the third case the location seam handles, with
  a puzzle's own file and a tabs-mode tab.
- **Welcome messages:** the hunt-channel welcome's "Google Sheet" link becomes the reference
  spreadsheet ("Reference sheet"); puzzle-channel welcomes link both the puzzle's own file
  and the reference spreadsheet. (My reading of "a link to that in the Welcome messages".)

### Links — how they behave in each mode

- **Files mode:** a link is the file's URL. It never changes, so links are written once and
  only rewritten when tags change or a puzzle is renamed.
- **Tabs mode:** a link is the spreadsheet URL plus `#gid=<tab id>` — always the **full**
  URL, never a bare `#gid`, because a solved feeder's tab lives in the solved spreadsheet
  while its meta stays in the hunt spreadsheet.
  - Up-links from a feeder to its meta stay valid: targets never move to the solved
    spreadsheet.
  - Down-links to a feeder change when the feeder is solved and its tab moves; the bot
    rewrites them then. (Corrected 2026-10-31 from the code map: `reconcile_table` already
    rewrites every present row on each render, so there is no text comparison to extend —
    `docs/PLANS.md` was wrong on that. The real hazard is ordering: `service.solve` renders
    the target tables *before* `move_puzzle_to_solved_sheet`, so the targets must be
    re-rendered after the move.)
- **Minis link up to their minimeta.** A mini isn't tagged, so "the puzzles it feeds" would
  give it no up-link; the point is getting to the meta quickly, so a mini's header links to
  its parent minimeta too. (My call.)
- **Where the up-links go in the header** is left to the builder, within the anti-goal:
  existing header geometry stays as it is, and the links are added, not moved into place
  over something else. A puzzle that feeds nothing shows no links.
- **Existing tab hunts** get the links as their tabs are next written or reconciled; no
  separate migration of old tabs is needed for this to be safe. (My call.)

### Structure change first (surfaced per the house rules)

The bot assumes one spreadsheet per hunt, set per request (`set_spreadsheet_id`) and
addressed by tab id. Counted 2026-10-31: `google_page_id` **90 references in 7 files**,
`solved_sheet_id` **45 in 7**, `set_spreadsheet_id` **3 in 3**. Files mode needs a single
"where does this puzzle's sheet live" seam — spreadsheet id + tab id for any puzzle, in
either mode, including solved tabs-mode puzzles on the solved spreadsheet — that every
caller goes through. The links in both modes need the same seam (a link is "where this
puzzle's sheet lives" as a URL).

Trigger: the structure is wrong for the feature (it would otherwise be bolted onto a
one-spreadsheet assumption in dozens of places) and a parallel files-mode path is what the
"mode that rots" anti-goal rules out. The extraction is its own phase and its own commits,
moving existing behaviour behind the seam unchanged, with existing tests re-pointed
one-for-one, before any files-mode behaviour is added.

Also a prerequisite: the global `_execute_lock` in `bot/utils/gsheets.py` serialises every
Google API call across all hunts, so one ~3 s file copy would stall every hunt's Sheets
calls. Replace it (per-thread `Http`, per `docs/PLANS.md`) first.

### Order

1. Replace `_execute_lock`.
2. The sheet-location seam (no behaviour change).
3. **Links in tab hunts** — useful straight away, and they are what makes files mode
   convincing. Can ship on its own.
4. **Files mode.** Its links come for free from step 3 through the seam.

Each step lands separately on `staging`.

## My calls (for the user to check)

- (Superseded by the user: the mode can be switched until the first puzzle or round.)
- Links in the Evidence tab are left out for now.
- A mini's header links up to its minimeta.
- Unsolving a mini un-greens its tab and moves it back in front of the solved minis.
- A failed file copy is reported in the puzzle's channel and repaired by `!reconcile apply`,
  which in a files hunt also fixes wrong-folder and wrong-name files and reports (never
  deletes) stray files.
- Existing tab hunts pick up links as tabs are rewritten or reconciled, rather than through a
  one-off sweep.
- Puzzle-channel welcomes link both the puzzle's file and the reference spreadsheet.
- Puzzle files are named after the puzzle; the folder after the hunt (exact format left to
  the builder).
- The hunt folder is created in the service account's Drive (the spike confirmed its own
  15 GB quota; 331 MB used), or under the server's `drive_parent_id` if that's set and the
  service account can write there.

## Constraints

- A push to `staging` deploys and runs migrations (`deploy.sh` → `migrate.py`); a mode column
  needs a migration (next free number, currently 051 — check for collisions before merging).
- Every bang command has a dashboard equivalent where practical (project principle).
- Tests for every shared path in both modes; Google API calls are mocked as elsewhere in the
  suite. Frontend tests cover pure helpers only.
- Google quotas: Drive copy is the slow call; a round unlock creating ten puzzles is ~30 s of
  copies, one at a time or a few at once — the builder chooses, within Drive's rate limits.

## Left to the builder

- The seam's shape and names, and where it lives (`bot/core/`).
- The header placement and wording of the up-links.
- Command syntax for choosing the mode.
- Concurrency of file copies; retry behaviour (following `execute_with_retry`).
- How the reference spreadsheet's tabs are trimmed/created from the master template.

## Acceptance

1. A tab hunt created after this ships behaves as today, plus: each feeder's header links to
   the sheets of the puzzles it feeds (not ones it informs), and roll-up tables link each
   puzzle name to its sheet. After a feeder is solved, the links to it point at its tab in
   the solved spreadsheet.
2. A hunt that was running before the deploy keeps working with no manual step.
3. A files hunt: creating it makes a folder, Solved subfolder and a reference spreadsheet
   (REFERENCE, Unassigned Feeders, Evidence); the hunt welcome links the reference
   spreadsheet.
4. Creating a puzzle in a files hunt makes its own file in the folder, header as today, no
   REFERENCE tab, named functions working (including `IMAGE()`-based ones without a
   prompt); its extra sheets are tabs in that file, and a minimeta's minis are tabs in its
   file. Solving a mini turns its tab green and moves it to the end; loose hunt sheets are
   tabs in the reference spreadsheet.
5. Solving moves the file into Solved and every link to it still works; unsolving moves it
   back.
6. Discord, the dashboard and the overview website open each puzzle's own file.
7. `!reconcile` reports no drift on a healthy hunt of either mode.
8. The mode can be chosen from the dashboard create form, the team-server setup page and
   the commands; solved-sheet options refuse with a reason on a files hunt.
9. A hunt with no puzzles or rounds can be switched either way, from the dashboard and by
   command, and then behaves as if created in that mode; once it has a puzzle or round the
   switch is refused with the reason.
