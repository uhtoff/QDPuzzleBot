# Meta-matching — "Unassigned Puzzles" worktable design

> Part of M9b. Date: 2026-06-23. Branch: `working-1`.
> Companion feature (capstones / progressive reveal) is a **separate** spec, designed next.

## Goal

Give solvers a single Google-Sheet working space where every puzzle that hasn't yet been tied
to a meta sits together — so they can theorise, annotate, rearrange, and work out which meta each
one feeds. The bot keeps the list current (adds new unassigned puzzles, fills in answers as they
get solved, removes ones that get classified); the humans do the thinking. Classification itself
still happens the normal way (dashboard drag / bang command) — the sheet is a **thinking space, not
an input**.

## Background — the data model this rests on

- A puzzle is tagged into its round at creation (`PuzzleService.create_puzzle` does
  `new_puzzle.tags.append(round_data.id)` when `round_data is not None`,
  `bot/core/puzzle_service.py`). Puzzles created at **hunt level** (no round) get **no tag**.
- A "meta" is a **round** with `meta_id` set (`RoundData.meta_id`). A puzzle "feeds a meta" when it
  is tagged into a round that has a `meta_id`.
- Therefore **"unassigned" = a puzzle that feeds no meta** — it is not tagged into any
  meta-bearing round. In practice today that is: hunt-category puzzles (no round tag) and any
  puzzles in a metaless round. (When capstones land, a capstone is **not** counted as a "real meta"
  for this purpose, so converting a meta to a capstone makes its feeders unassigned again — see
  Open items.)
- The orphaned pool is already derivable: `bot/web/data.py:get_board` returns each puzzle with a
  `rounds` array; a puzzle feeding no meta has no meta-bearing round in it.

## In scope

- A per-hunt **`Unassigned Puzzles`** Google-Sheet tab holding a **native Sheets Table**
  (`addTable`), reconciled incrementally (append / update / prune) — NOT clear-and-overwrite.
- The pool: **non-meta puzzles, solved or unsolved, that feed no meta.**
- Schema: `hunts.unassigned_sheet_page_id` (+ table id) so the tab/table is created lazily once.
- Service method `refresh_unassigned(hunt)` + store query for the pool.
- Triggers: puzzle create (hunt-level), solve, retag/assign, manual `!rebuild`-style command —
  fire-and-forget, mirroring `refresh_evidence`.
- **Web-create of unassigned (hunt-level) puzzles** — a dashboard path to create a puzzle with no
  round, so the worktable can be fed from the dashboard, not only via bang. (Aligns with the project
  principle that **every bang command should have a web-dashboard equivalent**.)

## Out of scope (flagged, not built here)

- **Reading the sheet back.** The bot never parses the user's arrangement; assignment is via the
  existing retag (`PuzzleService.move/add_to_meta`, dashboard drag / `!move_to_meta`).
- **Capstones** (making capstone puzzles, dossier, never-auto-solve) — separate M9b spec.
  **Carry forward (decided):** *making a meta into a capstone orphans its feeders* — a capstone is
  not a "real meta" for classification, so its feeders feed no meta and flow back into this
  worktable for re-classification. The capstone spec must honour this.
- Auto-suggesting a meta from title/answer. (Sheet is a thinking space only.)

## The tab

One tab per hunt, **`Unassigned Puzzles`**, created lazily on first refresh (reuse the
`create_blank_tab` pattern from evidence, `bot/cogs/puzzles_gsheet.py:376`). It holds a **native
Google Sheets Table** so the built-in filter/sort works (sorting by `Length` while matching is a
primary use). `addTable`/`updateTable` are new gsheet-cog methods (no current support).

> The PLANS caveat about native Tables (behaviour under `copyTo`/`duplicateSheet`) does **not**
> apply — this tab is never archived or duplicated.

### Columns

Bot owns a fixed **left block**; everything to the right is the user's and is never touched.

| Col | Owner | Notes |
|---|---|---|
| `id` | bot | Stable key = puzzle id. **Hidden.** Used to identify a row regardless of user reordering. |
| `Puzzle` | bot | Puzzle name (title) — a first-class matching signal ("assign by title"). |
| `Answer` | bot | Solution, **UPPERCASE**. Blank while unsolved (blank = the unsolved signal). |
| `Length` | bot | Letters in the answer **excluding spaces** (`RED HERRING` → 10), numeric. Blank while unsolved. |
| `Round` | bot | **Hidden by default**; column reserved even when hidden so toggling it on never shifts user columns. Opt-in via the refresh command flag / a per-tab setting. |
| `Notes` | seeded by bot, then user-owned | Header written once on creation; the bot never writes its cells. First of the user's columns. |
| … | user | User adds more columns to the right (theories, "meta guess", colour tags). Bot stays out. |

`Length` strips spaces only (hyphens/punctuation counted) — **decided**. Solved/unsolved is shown by
a blank `Answer`, no extra marker column — **decided**. `Round` is hidden by default and turned on
via a **command flag** on the refresh/rebuild command — **decided**.

### Reconcile (append / update / prune — never overwrite)

On each refresh the bot reads the table's hidden `id` column and diffs against the current pool:

1. **Append** — any pool puzzle not already in the table → new row at the **bottom**, writing only
   the bot's left-block cells. Existing rows never move, so arrangement and notes are preserved.
2. **Update** — a row whose puzzle has since been **solved** → fill in `Answer` + `Length` in place
   (bot-owned cells only).
3. **Prune** — a row whose puzzle is **no longer in the pool** (classified to a meta, deleted, or
   re-tagged) → delete the **whole row**. Whole-row deletion shifts rows below up together, so their
   adjacent user notes stay aligned. The matched row's own notes go with it (correct — it's done).
4. Rows that remain stay exactly where the user put them.

Keying on puzzle id means the user can **reorder/sort freely** and append/prune still hit the right
rows. The only unpreservable case is the user deleting a row's `id` cell — guarded by keeping the
`id` column + header row bot-managed (and hidden).

### Triggers

`refresh_unassigned(hunt)` fired fire-and-forget (like `refresh_evidence`) after:
- a puzzle is **created** at hunt level (new unassigned),
- a puzzle is **solved** (fill answer/length),
- a **retag/assign** (so a just-classified puzzle is pruned; a just-unassigned one appears),
- a manual `!rebuild`-style command (and a dashboard rebuild button, optional).

## Data model / schema

- `hunts.unassigned_sheet_page_id INT NULL` (+ `unassigned_table_id INT NULL` for the native
  table) — mirrors `hunts.evidence_sheet_page_id` (`bot/store/hunt_data.py:37`).
- New migration **`010_unassigned.sql`** (next free number — **check for collision** with the
  parallel `!reconcile` session before merging; see memory `migration-number-collisions`).
- No change to `tags` / `puzzles` / `rounds`. Classification reuses the existing tag graph.

## Service / store / gsheet seams

- **Store:** `PuzzleJsonDb.get_unassigned(hunt_id) -> list[PuzzleData]` — non-meta puzzles
  (`metapuzzle = 0`) with no tag into a meta-bearing round.
- **Service:** `PuzzleService.refresh_unassigned(hunt)` — resolve gsheet cog, lazily create the
  tab+table, read current table keys, diff, apply append/update/prune.
- **gsheet cog:** new `add_table` / `update_table` / row append+delete helpers via `batchUpdate`
  (`addTable`, `appendCells`/`insertDimension`, `deleteDimension`). Read current keys via
  `values().get` on the hidden id column.

## Architecture notes

- Incremental reconcile (read-diff-mutate) is the one genuinely new mechanic — distinct from
  `aggregate_to_tab` (`bot/cogs/puzzles_gsheet.py:679`), which clears+rewrites and would wipe the
  user's work. This tab is **human-owned**; the bot only touches its own columns and whole rows.
- Refresh is best-effort and must never fail the triggering action (solve/create/retag), matching
  the evidence path.

## Relevant files

| File | Change |
|---|---|
| `migrations/010_unassigned.sql` | new — `hunts.unassigned_sheet_page_id` + `unassigned_table_id` |
| `bot/store/hunt_data.py` | add the two fields |
| `bot/store/mysqldb.py` | `get_unassigned(hunt_id)` query |
| `bot/core/puzzle_service.py` | `refresh_unassigned`; fire it from create/solve/retag |
| `bot/cogs/puzzles_gsheet.py` | `add_table`/`update_table` + row append/update/delete; read keys |
| `bot/cogs/puzzles.py` | `!rebuild`-style trigger with a `Round` column flag (+ wire create/solve/retag bang paths) |
| `bot/web/actions.py` | fire `refresh_unassigned` from web create/solve/retag edges; **new web endpoint to create a hunt-level (unassigned) puzzle** |
| `frontend/src/…` | dashboard control to create an unassigned puzzle |

## Acceptance criteria

- A solved-or-unsolved puzzle that feeds no meta appears as a row in the `Unassigned Puzzles`
  table (Answer/Length blank until solved; Answer uppercase; Length excludes spaces).
- Solving such a puzzle fills its Answer + Length **in place**, preserving its row position and the
  user's adjacent notes.
- Assigning a puzzle to a meta prunes its row on the next refresh; other rows' notes stay aligned.
- Newly-created hunt-level puzzles append to the bottom without disturbing existing rows.
- User columns to the right of `Notes` (and `Notes` itself) are never overwritten by a refresh.
- The bot never reads the user's arrangement back; classification only happens via the retag paths.

## Decisions (resolved)

- `Length` = letters excluding spaces only (hyphens/punctuation counted).
- Solved/unsolved shown by a blank `Answer` — no marker column.
- `Round` column hidden by default, enabled via a **command flag**.
- **Web parity:** web-create of unassigned (hunt-level) puzzles is **in scope** (project
  principle: every bang command has a dashboard equivalent).
- **Capstone carry-forward:** making a meta a capstone **orphans its feeders** — the capstone spec
  must treat a capstone as not-a-meta for classification.

## Open items (validate during build)

1. **Native Table column-add UX** — confirm a user can add their own columns to a native table
   cleanly (vs the table absorbing/blocking them). Fallback: a plain managed range with a styled
   header row. ("Let's try it as a table" — validate, don't assume.)
2. **Migration number** — `010_*` is the next free slot today; re-check for collision with the
   parallel `!reconcile` work before merging (memory `migration-number-collisions`).
