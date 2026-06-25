# Milestone D — Sheet generation design

**Date:** 2026-06-25
**Status:** Design agreed; ready for implementation planning.
**Parent design:** `docs/superpowers/specs/2026-06-24-taggroup-edges-and-commands-design.md`
**Sibling spec:** `docs/superpowers/specs/2026-06-25-milestone-c-commands-design.md`

Milestones A (vocabulary rename), B (edge model: feeds/informs/none, drop kind), and
C (command surface) precede this. Milestone D is the sheet/native-table layer: it makes
the Google Sheet surface reflect the edge model — one generated, unified contribution
table per target, every puzzle tab generated in code, and an Unassigned Feeders tab with
a feeds-only membership test.

**No new DB migration.** The schema from A+B and the commands from C are the substrate.

---

## Scope

**In scope:**
- Generate **every** puzzle tab in code — retire both hidden templates ("Tab template"
  and "Meta Tab template") and the `duplicateSheet` / `copy_puzzle` copy model.
- One **unified contribution table** per target, as a *real* Google Sheets native Table
  (range covers the body) — feeders, informers, and grouped members in a single list,
  replacing the two flat renderers (`add_metapuzzle_data` / `add_metametapuzzle_data`).
- An **incremental reconciler** keyed on a hidden column-A row key (append / update / prune).
- **Promotion-safe** tab transform (regular tab → target tab) that preserves solver data.
- Rename **"Unassigned Puzzles" → "Unassigned Feeders"** with a **feeds-only** membership
  test, retrofitted from today's header-only/cosmetic table to a real native Table that
  shares the same generator + reconciler.

**Out of scope:**
- Frontend / board UI changes (board stays Metapuzzle-only; D is the Sheets surface only).
- Sheet **archive / duplicate** mechanics (unchanged; native-Table interaction with
  `copyTo`/`duplicateSheet` remains a known probe point, but archive is not retouched here).
- N-level nesting rework and metadata-key pointers for per-puzzle tabs (explorer-flagged
  adjacent improvements — explicitly **not** this milestone).
- `!sync` / membership / convert command behaviour (Milestone C).

**Depends on:** Milestone C complete — promote-in-place, `convert_to_type`, and the edge
vocabulary `feeds` / `informs` / `none`. No new migration.

---

## Current state (what exists today)

All sheet code lives in `bot/cogs/puzzles_gsheet.py` (~973 lines).

- **Per-puzzle tabs** are *copied* from a hidden template via `add_new_sheet`
  (`duplicateSheet`): "Tab template" for regular puzzles, "Meta Tab template" (+2 columns)
  for metapuzzles (`add_new_puzzle_sheet`, line ~361). Promote/demote shuffles columns with
  `copy_puzzle`'s `copyPaste` between `FIRST_PUZZLE_COLUMN` and `FIRST_METAPUZZLE_COLUMN`
  (line ~217).
- **Meta roll-up tabs** are rendered by `aggregate_to_tab` (line ~699) via
  `values().clear()` + `values().update()` — a **sized overwrite**, safe only because the
  meta tab is dedicated. Two thin wrappers select membership: `add_metapuzzle_data`
  (~729, meta ← feeders) and `add_metametapuzzle_data` (~742, meta-meta ← metas). Called
  from `PuzzleService._render_rollup` after `select_rollup_members`.
- **Unassigned table** already uses a native `addTable` with a hidden id column and an
  incremental reconcile: `ensure_unassigned_table` (~768), `read_unassigned_keys` (~827),
  `apply_unassigned_changes` (~856). But per the parent design it is effectively
  **header-only / cosmetic** (the table range is the header; the body is hand-managed) and
  its reconcile **overwrites** (`updateCells`) — surviving only because the tab is dedicated.
- **Membership selection** (`select_rollup_members`, `puzzle_service.py`) already speaks the
  post-B edge vocabulary (`feeds` / `none`, with `informs` materialised in `tags`). The
  Sheets cog *reads* selected members; it does not validate edges.

The Sheets cog has **no hard coupling** to the renamed vocabulary — it works through
`puzzle.tags`, `puzzle.tag_edges`, and `is_metapuzzle()`, all of which survived A/B.

---

## Architecture — one tab generator

A single code path builds any puzzle tab:

1. `addSheet` (new blank tab).
2. Write the **header** cells from a **captured layout** (see "Template capture" below).
3. If the puzzle **is a target**, add the **contribution table** (a real native Table).

**Template capture (option 2 — read-once) — DONE.** Captured 2026-06-25 by reading the
master template spreadsheet's hidden "Tab template" (sheetId 1971123506) and "Meta Tab
template" (42737759) tabs through the project's own authed Sheets client (no openpyxl/xlsx
needed) and persisting the result as `bot/cogs/sheet_layout.py`. The captured layout (3
frozen header rows):

- **Static labels:** A1 "Puzzle Title:", H1 "Solution:", A2 "Puzzle Flavor Text:",
  A3 "Progress Summary:" (bold).
- **Merges:** B1:G1 (title), I1:Q1 (solution), B2:Q2 (flavour), B3:Q3 (progress).
- **Bot-dynamic** (written at runtime from config, NOT from the artifact): `B1`
  name/url (`puzzle_cell_name`/`_link`), `I1` solution (`puzzle_cell_solution`),
  `B3` progress (`puzzle_cell_progress`, default "none").
- **User-owned:** `B2` flavour text (not bot-written).
- The Meta tab's old rollup region (A4 "managed by the bot" notice; A5/B5 "Puzzle name"/
  "Puzzle solution" headers, data below) is **replaced** by the generated contribution Table.

**Target-ness signal — has-target, name-agnostic.** The trigger is **"is this puzzle the
target of some tag_group?"** Today that is `puzzle.is_metapuzzle()`
(`bot/store/puzzle_data.py:87` → `self.metapuzzle == 1`); a pending change (see below) may
replace the `metapuzzle` column with a derived `has_target(puzzle)` helper. **D depends on
the has-target *concept*, not the column** — it calls whatever the current accessor is and
does not bake in `metapuzzle`.

The signal means **has-target**, *not* "is-fed". An audit of all readers confirmed
`metapuzzle` is used purely as has-target everywhere; the fed-vs-informed distinction lives
entirely in the **edge layer** (`select_rollup_members` filters `tag_edges == "feeds"`;
membership never gates on `metapuzzle`). So a Capstone's or Runaround's target is equally a
target. The contribution table is correct for all of them — its rows distinguish
`Feeder` / `Information` / `Group` from the **edges**, so it renders the right content
whether the target is fed, informed, or both. D must never read the has-target signal as
"this is a fed meta".

> **Prerequisite: Milestone B2** — `docs/superpowers/specs/2026-06-25-milestone-b2-derive-has-target-design.md`.
> B2 (sequenced **after C, before D**) derives has-target and drops the denormalised
> `metapuzzle` + `metameta` columns, replacing `is_metapuzzle()` with a `has_target(puzzle)`
> accessor. D is written name-agnostic, so it reads `has_target` once B2 lands (and would
> still work via `is_metapuzzle()` if B2 slipped).

This replaces `add_new_sheet`'s `duplicateSheet` and **retires** `copy_puzzle`'s
column-shuffle. **Create and promote become the same routine** — promotion is just "this
tab now also gets a contribution table" (see Promotion below). Both hidden templates
("Tab template", "Meta Tab template") are deleted.

**Header layout comes from the captured artifact** `bot/cogs/sheet_layout.py` (Approach A via
read-once capture): the generator writes the static labels + merges from the artifact and the
dynamic name/url, solution, progress cells from `config.puzzle_cell_*`. The flavour cell (B2)
is user-owned and never written. This reproduces today's appearance without keeping the
templates.

> Why retire "Tab template" too (beyond the parent design's stated boundary): the regular
> and meta tabs differ only by the contribution-table columns — the header is effectively
> the same. Generating both in code collapses `duplicateSheet` + `copy_puzzle` into one
> path and **dissolves the parent design's "promote breaks the copy model" problem** —
> there is no copy model left to break.

---

## The unified contribution table (per target)

One real native Table (`addTable`, range covering the **body**, not just the header) on the
target's own tab — feeders, informers, and grouped members in a single filterable list.

**Placement:** the table occupies the **left columns A-D**, below the header rows, with the
solver grid shifted right (see Data preservation) — generalizing the existing meta layout
(rollup in the left columns, puzzle grid shifted to `FIRST_METAPUZZLE_COLUMN`).

| col | role |
|---|---|
| **A** (hidden) | row key (stable, edge-anchored — see below) |
| **B** `Type` | `Feeder` / `Information` / `Group` — visible, filterable |
| **C** | source puzzle (hyperlink) |
| **D** | answer (Feeder) / content (Information) / — (Group) |

A real Table gives banding, header styling, column types, and genuine interactive filter &
sort on `Type` for free, built in code.

**Key-column placement — column A, hidden.** The captured template puts descriptive labels in
A1:A3, but those are the *only* things in column A — every dynamic cell (`B1`/`I1`/`B3`) is
already ≥ column B. So the generator writes the header **labels starting at column B**, leaving
column A free to be the hidden key column (header rows empty, body rows the key). This matches
the parent design and keeps the bookkeeping key out of sight.

**Row key (column A, hidden)** — anchored on the **edge type** (stable; *not* the volatile
`tag.id`), so the reconcile survives re-tagging:

| key | `Type` | one row per |
|---|---|---|
| `feeds:<puzzle_id>` | Feeder | feeding member (one — `UNIQUE(tag_group_id, puzzle_id)`) |
| `none:<puzzle_id>` | Group | grouped member |
| `informs:<puzzle_id>` | Information | bare informs edge, nothing logged yet |
| `informs:<puzzle_id>:<evidence_id>` | Information | each logged datum |

The `:<evidence_id>` segment **appears only when needed** — a bare `informs` edge yields one
keyless `informs:<pid>` row; logging two facts from one source yields two
`informs:<pid>:<eid>` rows (and the bare row is replaced). Keys are stable surrogates so the
column-A diff survives re-tagging and re-rendering.

---

## Incremental reconcile (column-A diff)

On each refresh of a target's table:

1. **Read** column A (the current key set) from the existing table.
2. **Compute** the desired key set from the group's members, their edges, and any evidence
   rows (a pure function of DB state).
3. **Diff** and apply, table-aware:
   - **append** new keys via `appendCells(tableId)` (fills the first free body row,
     footer-aware, inserts — never overwrites);
   - **update** changed rows in place via `updateCells` on the matched body row;
   - **prune** removed keys via `deleteDimension` (the table range shrinks with the row).

This **generalises** today's `apply_unassigned_changes` into a reusable reconciler keyed on
column A and grown the table-aware way (vs today's overwrite). The diff itself
(`current_keys`, `desired_keys` → append/update/prune lists) is a **pure function**, tested
without any Sheets calls.

---

## Data preservation

The preservation seam is **promotion** — a regular puzzle tab gaining a contribution table.
This **generalizes the existing convert** (`copy_puzzle`), which already shifts a meta's puzzle
content from column 0 to `FIRST_METAPUZZLE_COLUMN = 2` to free the left columns for the rollup.
Confirmed against a live meta tab ("Batman - meta"): the rollup sits in the **left columns
(A-B), below the header**, with the solver grid shifted right — no column hidden today.

D's contribution table is 4 columns (key, Type, Source, Content), so promotion frees the left
**4** columns and hides the key column A. Sequence (all inserts, never sized overwrites):

1. **Insert columns** at the left equal to the contribution-table width
   (`insertDimension COLUMNS` at column A) — the solver grid + header shift right intact, so a
   solver's existing `B5` becomes `F5`, never overwritten.
2. **Add the contribution table** (real native Table) into the freed left columns A-D.
3. **Hide column A** (the key column) — now empty in the header rows and the key in the body.
4. **Regenerate the header**: rewrite the bot-owned cells (name/url, solution, progress) at
   their `config.puzzle_cell_*` positions and **restore the user-owned flavour cell** (it rode
   the shift; read-and-restore it to its proper place — the one piece of user data the
   transform must preserve by hand). Labels are written starting at column B so nothing
   meaningful lives in the hidden column A.

**Create is the degenerate case** — a fresh tab has no solver grid to shift, so the generator
just writes the header (labels at B+, A free) and adds the table in A-D with A hidden.

Belt + suspenders, as in the parent design:
- **Suspenders (the guarantee):** insert-based growth — content is pushed down/right, never
  eaten.
- **Belt (the social contract):** grey-shade the table's columns below the current last row
  ("bot-managed, don't type here"). Not a safety mechanism (a user can still type in a grey
  cell) — the guarantee comes from inserts.
- **Bot/user column split** by range-limiting writes (as the Unassigned `Notes` column
  already is): the bot writes only its own columns and never the user's.

---

## Unassigned Feeders (rename + semantic test + real-table retrofit)

- **Rename** the tab and label **"Unassigned Puzzles" → "Unassigned Feeders"**.
- **Change the qualifying test** in `get_unassigned`: today it excludes any puzzle *tagged
  into a meta-bearing round* (mere membership disqualifies). The new test is **"has no
  `feeds` edge to any meta"** — a puzzle whose *answer* feeds nothing is an unassigned
  **feeder**, regardless of `informs` / `none` membership. This is what makes flipped
  Puzzmon feeders (now informers) resurface here for metamatch while still informing their
  capstone.
- **Retrofit to a real native Table**: today's tab is header-only/cosmetic with an
  overwrite reconcile. Move it onto the **shared generator + reconciler** (hidden id column,
  user-owned `Notes` column preserved, grown via `appendCells(tableId)`), gaining the same
  real filter/sort. The hidden key column is `id:<puzzle_id>` (the table is a flat list of
  puzzles, not edges).

---

## Service-layer wiring

`refresh_meta` / `refresh_minimeta` / `refresh_unassigned` keep their orchestration role —
membership selection stays in `select_rollup_members` (which already speaks the B edge
vocabulary). Only the **render** call changes:

- `aggregate_to_tab` (clear + update overwrite) → the new generated-table **reconciler**.
- `add_metapuzzle_data` / `add_metametapuzzle_data` collapse into the **unified contribution
  renderer** (which emits feeders, informers, and grouped members from one pass).

`refresh_unassigned` is routed through the same reconciler against the Unassigned Feeders
table.

---

## Testing

Mirror the existing approach (`tests/test_aggregate_to_tab.py`): **mock the Sheets client,
assert the `batchUpdate` request shapes** — no live Google calls, no new infrastructure.

- **New `tests/test_contribution_table.py`:**
  - Desired-key computation from members / edges / evidence (feeds → `feeds:<pid>`,
    none → `none:<pid>`, bare informs → `informs:<pid>`, logged datum →
    `informs:<pid>:<eid>`, bare-row replacement when a datum lands).
  - The column-A **diff** (pure function): append / update / prune given `current` vs
    `desired` key sets, including empty-table and all-prune cases.
  - Request shapes: `addTable`, `appendCells(tableId)`, `updateCells`, `deleteDimension`,
    column-hide for A.
  - **Promotion** transform: save flavour/progress → `insertDimension COLUMNS` →
    table write → header reconstruct with restored flavour/progress (assert the inserts
    precede writes and no sized overwrite touches the solver grid).
- **Extend `tests/test_select_rollup_members.py`** (or a focused new test) for the
  **feeds-only** Unassigned Feeders membership test (informs/none membership does **not**
  disqualify; a feeds edge does).

**Acceptance bar:** full suite green (`timeout 180 pipenv run python -m pytest tests/ -q`),
`ruff check` exit 0.

---

## Constraints

- **No migration.** Schema from A+B is the substrate.
- **No new test infrastructure.** Reuse the mocked-Sheets pattern and existing DB fixtures.
- **All growth insert-based** on shared/target tabs — never `updateCells`/`values.update`
  over a range that holds solver data.
- **Both hidden templates retired** — every tab generated in code (Approach A).
- **Archive mechanics untouched** — D does not retouch sheet archive/duplicate.

---

## Open items (resolve at plan time, not here)

- **Capture exact current template cells** — flavour text, progress formula/cells,
  formatting, and the row/column constants (`FIRST_PUZZLE_ROW`, `FIRST_PUZZLE_COLUMN`,
  `FIRST_METAPUZZLE_COLUMN`) — so generated tabs match today's appearance. A planning step
  before coding the generator.
- **Existing copied tabs:** the first refresh after D should generate the table onto
  whatever tab already exists via the insert-based promotion path (no separate migration).
  Confirm this holds for already-copied meta tabs on the test hunt.
- **`refresh_unassigned` liveness:** CONFIRMED live — called via `refresh_unassigned_safe()`
  in `bot/cogs/puzzles.py` and `bot/web/actions.py`; the reconcile-diff already exists as pure
  functions `unassigned_row` / `diff_rows` in `bot/core/unassigned.py`. D generalises those.
- **`metapuzzle` overload — cross-check with C.** `metapuzzle == 1` is now "has-target",
  shared by Metapuzzle / Capstone / Runaround targets. `convert_to_type(..., "Group")`'s
  already-grouped branch (`puzzle_service.py:660-663`) sets only `tag_group.type` — it does
  **not** clear `target_puzzle_id` / `metapuzzle`, so a converted Group may retain a target
  and the flag. Whether that is intended (vs `Group ⟺ target_puzzle_id IS NULL` from the
  parent design) is a **C** question. D's only requirement: trigger on `is_metapuzzle()`
  (has-target) and let the edge rows decide content — do not assume the flag means "fed".
- **Native Table + archive:** `appendCells(tableId)` / table-range behaviour under archive
  is a known undocumented area — confirm the contribution table does not regress the
  existing archive flow (no behaviour change intended).

## Rollback

- The work lands as a unit in dev; revert the milestone's commits to back out.
- No DB migration to reverse.
- Retiring the hidden templates is reversible by restoring the template tabs and the
  `duplicateSheet` path from git history if the generated path must be abandoned.
