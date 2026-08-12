# Feeder Pool — design

Automating the given-feeder list for a **meta matching puzzle**.

> **Revised 2026-08-12 (after Plan 1 shipped).** The original sheet design put a `Feeder
> Pool` table on the Minimeta's own tab and a `Feeder Claims` block on each Mini's tab.
> **That design was unbuildable** and is superseded by the matrix tab below — see
> "Why not two tables on the existing tabs". The data model, the claim/confirm rules and
> the provided rows are unchanged; only the sheet surfaces and the sync command name are.

## The puzzle shape

A meta matching puzzle hands solvers a flat list of finished answers — the *given
feeders* — and a set of mini-puzzles that are each shaped like a metapuzzle rather than
an ordinary puzzle. No feeder needs solving; the entire work is deciding **which feeder
belongs to which mini**. Each mini, once fed the right subset, yields a mini answer, and
the main puzzle is a meta over those mini answers — sometimes also consuming a given
feeder directly.

Today this is buildable with the existing Minimeta model, but the feeder list has to be
tracked by hand somewhere outside the bot. This design gives that list a home, a place to
leave notes against it, and a mass-assignment path.

## Goal

A **feeder pool** owned by a Minimeta puzzle: a list of given feeder strings, claimable by
its Minis (and by the Minimeta itself), annotated with notes, visible and sortable from
every mini's own sheet tab, and confirmed automatically when a mini is solved.

## In scope

- Pool of feeder strings owned by a Minimeta.
- Claims: any Mini (or the parent Minimeta) may claim any feeder; overlaps are legal and
  visible.
- Automatic confirmation of a mini's claims when that mini is solved; automatic demotion
  on unsolve.
- A **matrix tab** per Minimeta: feeders down the side, claim targets across the top,
  solver-tickable checkbox cells read back on an explicit sync.
- **Provided rows**: feeders claimed for the main meta rendered into the parent's existing
  contribution table alongside the mini answers.
- Dashboard panel mirroring the pool, per the web-parity principle.
- Bang commands and web routes for add / list / edit / remove / sync.

## Out of scope

- Pools on TagGroups (round/metapuzzle groups with real puzzle feeders). Minimeta-owned
  only; generalising later stays open but is deliberately not designed for now.
- Feeders that themselves need solving. A feeder is finished text — it never gets a
  channel, a thread, or a sheet tab.
- Timed/background polling of the sheets. Sync is explicit.
- Any change to existing Minimeta behaviour. An empty pool renders nothing.

## Relevant files

Existing seams this rides on:

- `bot/cogs/puzzles_gsheet.py:1142` — `reconcile_table()`. Reads the named native Table,
  diffs the full desired set, appends/updates/prunes. **Writes only `data_col_count`
  columns from the key column and deliberately preserves a wider Table span for
  solver-owned columns** (`:1204`, which names the Unassigned Notes column as exactly this
  case). This is the precedent the claim checkbox relies on.
- `bot/cogs/puzzles_gsheet.py:1210` — rebuilds its row map by reading the key column, so
  rows are matched **by value, not position**. A solver may freely sort or filter a table
  and the next reconcile still lands correctly.
- `bot/core/sheet_tables.py` — `select_table()`; picks the bot's table by name and refuses
  to write when it cannot identify one. Built for the multiple-tables-per-tab case this
  design creates.
- `bot/core/unassigned.py` — `diff_rows()`; the append/update/prune diff. The model for the
  new pure module.
- `bot/core/contribution.py` / `CONTRIBUTION_HEADER` (`bot/cogs/puzzles_gsheet.py:921`) —
  `["key", "Source", "Type", "Content"]`, a `Type` vocabulary (`Feeder`/`Group`/
  `Information`), namespaced keys, and `informs:free:<eid>` — an existing row with no puzzle
  behind it. Provided rows follow that precedent.
- `bot/cogs/puzzles_gsheet.py` `render_minimeta_contribution()` — the model for the two new
  renderers: synthesises rows, calls `reconcile_table` with a named table, lazily promotes
  the tab if the table is absent.
- `bot/core/puzzle_service.py:1286` / `:1370` — `solve()` / `unsolve()`. **The shared
  seam**: called by both `bot/web/actions.py:523` and `bot/cogs/puzzles.py:3488`, so
  hooking here covers both edges by construction.
- `bot/core/puzzle_service.py:721` — `refresh_minimeta()`; the lifecycle and rollup-badging
  pattern the new renders follow.
- `migrations/008_rollup_puzzle_target.sql` — `UNIQUE KEY uq_rollups_target_puzzle
  (target_puzzle_id)`. One staleness record per puzzle; see "Staleness".

New files:

- `migrations/043_feeder_pool.sql`
- `bot/core/feeder_pool.py` — pure module, no Discord/Sheets imports.

## Data model

```sql
CREATE TABLE feeder_pool_items (
  id                  INT PRIMARY KEY AUTO_INCREMENT,
  minimeta_puzzle_id  INT NOT NULL,          -- FK puzzles(id) ON DELETE CASCADE
  text                VARCHAR(255) NOT NULL, -- the given feeder string
  note                TEXT,                  -- solver freeform, sheet-editable
  position            INT NOT NULL,          -- paste order
  UNIQUE (minimeta_puzzle_id, text)
);

CREATE TABLE feeder_claims (
  id         INT PRIMARY KEY AUTO_INCREMENT,
  item_id    INT NOT NULL,   -- FK feeder_pool_items(id) ON DELETE CASCADE
  target_id  INT NOT NULL,   -- FK puzzles(id) ON DELETE CASCADE — a Mini, or the Minimeta
  confirmed  BOOLEAN NOT NULL DEFAULT 0,
  UNIQUE (item_id, target_id)
);
```

Decisions embedded here:

- **A claim is a row.** Unclaimed = no rows. Contested = several rows. Confirmed = one row
  with `confirmed=1`.
- **The Minimeta itself is a valid `target_id`.** That is how "the main puzzle sometimes
  uses a given feeder directly" is expressed — no special case, no extra column.
- **`note` lives on the item, not the claim.** "I think it's for mini 1" is a fact about
  the feeder, not about one particular claim.
- **`position` preserves paste order**, so the pool reads in the order the puzzle presented
  it. Sheet-side sorting is a view concern and does not write back.
- **Empty pool = today's behaviour exactly.** No existing Minimeta changes; nothing renders
  until a pool has items.

### Derived claim state

`state` is derived per (item, viewing target) — never stored:

| value     | meaning from the viewing mini's point of view       |
|-----------|-----------------------------------------------------|
| `free`    | no other target claims it                            |
| `claimed` | ≥1 unconfirmed claim by another target               |
| `taken`   | confirmed to another target                          |
| `mine`    | confirmed to this target                             |

Derivation lives in `bot/core/feeder_pool.py` and is pure — the highest-value unit tests in
the feature sit here.

Since the revision to a matrix tab, `state` and `rival_names` are consumed by the **Discord
`!feeders` embed** rather than by a sheet column: the matrix shows every claim at a glance,
so it needs no derived state column. Both remain correct and load-bearing.

## Sheet surfaces

**One dedicated tab per Minimeta, laid out as a matrix.** Feeders down the side, claim
targets across the top, a checkbox in each cell — which is how a matching puzzle is
actually solved.

Tab name: `Feeders — <minimeta name>`, created through `get_unique_tab_name`. Its
`sheetId` is stored in a new nullable column `puzzles.feeder_tab_page_id` (migration 044),
because the Minimeta's existing `google_page_id` names its own puzzle tab and `rollups`
already spends its one `tab_page_id` on the mini-list.

```
 A(hidden)  B            C         D      E       F       G
 key        item         note      main   mini1   mini2   mini3
 f:12       ALBATROSS    fits SEA  [ ]    [x]     [ ]     [ ]
 f:13       MARZIPAN     clash?    [ ]    [x]     [x]     [ ]
 f:14       QUAY                   [ ]    [ ]     [ ]     [ ]
 f:15       TERN         for main? [x]    [ ]     [ ]     [ ]
 └── bot-owned ──┘       └──────── solver-owned ─────────────┘
     data_col_count=2
```

- **Columns A–B are bot-owned** and reconciled by `reconcile_table` with
  `data_col_count=2`. Everything from C rightward is preserved untouched, by the same
  column-span rule the Unassigned table's Notes column already relies on
  (`bot/cogs/puzzles_gsheet.py:1204`).
- **`note` sits at C, immediately after `item`**, so that adding a mini appends a column at
  the far right and *nothing moves*. Putting note last would make every new mini shift it.
- **One tick column per claim target**, `main` first (the Minimeta itself), then the Minis in
  creation order. The header row is bot-written; the cells below are the solver's.
- **The bot does write into the tick columns, but only to restore a confirmed tick.**
  Confirmed claims are bot-owned: unticking one does not undo it, and the next render puts
  it back. Unsolving the mini is how it is undone. `diff_ticks`
  (`bot/core/feeder_pool.py`) already encodes exactly this and is the only place the rule
  lives.
- Tick columns are declared `columnType: "BOOLEAN"` in the `addTable` request's
  `columnProperties`, so the checkbox applies to rows as the Table grows.
  `reconcile_table`'s resize sends `"fields": "range"` only, so a column type set at
  creation is never clobbered.

### Why not two tables on the existing tabs

The original design is not merely awkward, it is **destructive**, and the reason is worth
recording so it is not re-proposed.

`reconcile_table` builds its row map by reading the **entire** key column of the tab —
`'Tab'!A:A` (`bot/cogs/puzzles_gsheet.py:1210`) — and treats every non-empty key below the
header as a live row. `diff_rows` then classifies any key not in the caller's desired set
as a **prune**, and prunes are blanked. Two bot-owned tables sharing a tab's key column
therefore blank each other's rows on every render, forever.

Three further obstacles, all verified in the code:

- **`select_table` falls back to a lone unnamed table regardless of the name asked for**
  (`bot/core/sheet_tables.py:44`). With a second bot table present, a transient failure
  during creation could route contribution rows into the pool table — reintroducing the
  exact bug that module was written to prevent.
- **A Mini's tab has no contribution area and no promote path.** Minis are created as
  ordinary puzzles, so row 3 onward is solver grid; a claims block at A4 lands on their work.
- **The Minimeta's tab has no free column band.** `promote_tab_to_target` already pushed the
  solver grid to E+, and nothing bounds how far right it extends.

The matrix tab sidesteps all four: its own tab, its own key column, no pre-existing solver
grid, and no second table anywhere.

## Sync

`!sync_claims` — the explicit pull. (`!sync_claims` is **not available**: it is already an
alias of `!sync`, and discord.py raises `CommandRegistrationError` at cog load on a
duplicate, so reusing it would fail the bot at startup rather than shadow anything.)

1. **One** `values().get` over the whole feeder tab. Because the matrix is a single tab
   there is no N-tab read — which matters more than quota arithmetic suggests, since
   `_execute_lock` (`bot/utils/gsheets.py:37`) serialises every Sheets call process-wide, so
   N reads would block every other hunt's Sheets traffic for the duration.
2. Map the header row to claim targets, then run `diff_ticks` **per tick column**, inserting
   and deleting unconfirmed claims only.
3. Adopt changed notes from column C via the already-built, currently-unused
   `FeederPoolDb.set_note`.
4. **Ignore any key not currently in the pool.** `reconcile_table` blanks pruned rows rather
   than deleting them, so a tick can outlive its item. `diff_ticks` already implements this;
   the sync must route through it rather than re-deriving it.
5. Re-render the matrix and the parent's contribution table.
6. Report: claims added, claims dropped, newly contested items, still-homeless count.

Values come back as **FORMATTED_VALUE strings**, so a ticked box reads as `"TRUE"`, not a
bool — and an all-unticked column may omit `values` entirely rather than returning blanks.

**Do not take `rollup_lock` around a call to `refresh_minimeta`.** `asyncio.Lock` is not
reentrant and never times out, so re-entry wedges the event loop permanently — no error, no
timeout, until restart (`bot/core/locks.py:24`).

## Confirm on solve

Solving a mini is proof that its claims were right, so confirmation is automatic rather
than a manual tick.

Hooked at `bot/core/puzzle_service.py:1286` (`solve`), firing only when the puzzle is a
`Mini` whose parent has a non-empty pool:

1. Set `confirmed=1` on every claim held by that mini.
2. **Rival claims are left alone.** Confirmation never deletes. A losing claim is
   information worth keeping, and deleting it would make unsolve lossy — you cannot
   resurrect what you deleted. Rivals simply render as `taken` from then on.
3. Any provided row for a feeder just confirmed to this mini drops out of the parent's
   contribution table, superseded (see "Provided rows").
4. Re-render all three surfaces.

`unsolve` (`:1370`) is the exact inverse: set `confirmed=0` on that mini's claims. The
contested set returns intact, and any provided row it had superseded returns with it.

The DB confirmation is part of the solve. **The sheet re-render is best-effort**, badged on
the Minimeta where a badge exists (see "Staleness"), matching how `create_mini` already treats `refresh_minimeta`
(`bot/core/puzzle_service.py:2552`) and how `solve` already defers `SheetRefreshError` to
the end — a Sheets hiccup must never fail a solve.

Reporting is explicit rather than silent. The solve response names what changed:

```
✓ Mini 1 solved — SEABIRDS

Feeder pool updated:
  confirmed → mini1: ALBATROSS, MARZIPAN, QUAY, TERN
  MARZIPAN was also claimed by mini3 — now shown as taken there
  3 feeders still homeless
```

A sloppy claim set (5 ticked, 4 actually used) is therefore visible immediately and fixed
by unticking and re-syncing, rather than being locked in unnoticed.

## Staleness

When a sheet write fails the bot records the failure rather than crashing, and the
dashboard shows a **"sheet stale"** badge (`frontend/src/components/BoardGroup.tsx:158`,
`frontend/src/components/PuzzleRowActions.tsx:52`) driven by `rollup_stale`. That flag is
stored **once per puzzle** — `migrations/008` puts `UNIQUE KEY uq_rollups_target_puzzle
(target_puzzle_id)` on the `rollups` table — and a Minimeta already spends its one slot on
its mini-list.

This feature adds more writes than there are slots: the pool table is a second write to the
parent's tab, and each mini's claim block is a write to a different tab again.

**Decision: share the Minimeta's existing badge.** The matrix tab has no badge of its own.

- A failure of *either* the mini-list render or the matrix render marks the Minimeta stale.
  The badge does not say which failed. It doesn't need to: the fix for both is
  `!sync_claims`, which rewrites every surface and reports what it fixed.
- The DB is never wrong, only the sheet drifts, so the exposure is bounded: a write that
  fails during confirm-on-solve stays invisible until someone syncs.

**Rejected:** adding a `kind` column and relaxing the unique to `(target_puzzle_id, kind)`
so every write gets its own badge. More precise, but it alters an existing table's
constraint on live data to distinguish failures a user would respond to identically.

## Editing a feeder's text

`!remove_feeder` is for a **wrong** item — a pasted header line, page furniture, one line
too many. It cascades away the item's claims and note, which is why it is the one
organiser-gated command in the set.

It is **not** the way to fix a typo, because that would discard the work attached to the
item. Typos are fixed by `!edit_feeder <key> <new text>` or inline in the dashboard panel.
The key (`f:<id>`) is independent of the text, so editing a keyed row is unambiguous — the
obstacle to editing it in the sheet is purely that `item` sits inside the bot-written span,
not any identity problem.

## Provided rows

The main puzzle is a meta over its mini answers and *sometimes over a given feeder
directly*. Those two kinds of input need to sit together somewhere for the meta to be
solvable, and the natural home is the parent's existing contribution table rather than a
third table.

That table is already shaped for it. `CONTRIBUTION_HEADER = ["key", "Source", "Type",
"Content"]` (`bot/cogs/puzzles_gsheet.py:921`); `Type` is already a vocabulary (`Feeder`,
`Group`, `Information`); keys are already namespaced; and `bot/core/contribution.py`
already renders a row with **no puzzle behind it** — `informs:free:<eid>`, sourceless
evidence belonging to the hunt, with `—` in the Source column. A given feeder is that same
shape:

```
 key            Source     Type      Content
 feeds:101      Mini 1     Feeder    SEABIRDS
 feeds:102      Mini 2     Feeder    NIGHTFALL
 provided:13    —          Provided  MARZIPAN
```

`Content` then holds exactly what the meta operates on, and `Type` distinguishes a mini
answer from a given feeder; one filter separates them.

### Rendered on the claim, not on confirmation

Confirmation happens when a **mini** is solved. The Minimeta is not a mini: the confirming
event for a claim on the main meta would be solving the main meta, which is the last thing
that happens in the whole puzzle. A provided row that waited for confirmation would
therefore never appear while it was useful.

So a provided row renders as soon as the feeder is claimed for the Minimeta. The
contribution table is a working surface — it already carries unproven content, since an
unsolved feeder's row sits there with a blank `Content`. The uncertainty is visible in the
pool table, which is where it belongs.

### Superseded claims drop out

A claim **confirmed to a mini** is proof that the feeder belongs to that mini. Any lingering
main-meta claim on the same feeder is therefore known wrong, and its provided row is **not
rendered**, keeping the meta's answer list clean.

Nothing is deleted: the claim survives in the pool rendered as `taken`, so the solver can
see the guess was wrong. Unsolving the mini un-supersedes it and the provided row returns —
no special handling, it falls out of the same derivation.

### Constraints

- Only feeders claimed to the Minimeta itself (`target_id = minimeta.id`) appear — driven by
  the `main` column of the matrix. Never the whole pool: that would tip every loose string
  into the meta's answer table.
- **`render_minimeta_contribution(minimeta, members)` has no claims argument**
  (`bot/cogs/puzzles_gsheet.py:1518`), and both `bot/core/puzzle_service.py:741` and
  `tests/test_refresh_minimeta.py`'s `assert_awaited_once_with(minimeta, minis)` pin the
  current shape. Adding provided rows changes that signature and both call sites.
- `render_minimeta_contribution` builds its rows inline rather than via
  `contribution_rows`, so the provided rows are appended there.
- `provided:<item_id>` is a fresh key namespace, so the column-A diff is unaffected.
- The contribution table is therefore a **third surface that re-renders on feeder changes**
  — on sync, and on confirm-on-solve (confirming to a mini can supersede a main claim and
  remove a row).

## Command and route surface

Naming follows the existing `!make_minimeta` / `!create_mini` family. Every command has a
web equivalent, per the project's parity principle.

| Command | Route | Gate |
|---|---|---|
| `!add_feeders <minimeta>` + multi-line paste | `POST /puzzles/<id>/feeders` | ungated |
| `!feeders <minimeta>` (list embed) | pool data in the board payload | ungated |
| `!edit_feeder <key> <text>` | `PATCH /puzzles/<id>/feeders/<item_id>` | ungated |
| `!remove_feeder <key>` | `DELETE /puzzles/<id>/feeders/<item_id>` | organiser |
| `!sync_claims <minimeta>` | `POST /puzzles/<id>/feeders/sync` | ungated |

Gating follows the 2026-08-11 audit's area-based principle: building a pool is a
Create-area operation (like `!make_minimeta`, ungated); destroying part of one is not (like
`!remove_minimeta`, organiser); syncing is a refresh (ungated).

Re-pasting into `!add_feeders` is additive — new lines append, existing ones are left alone
(the `UNIQUE (minimeta_puzzle_id, text)` constraint makes this a natural upsert), so
pasting the list twice is harmless.

Dashboard: a Feeder Pool panel on the Minimeta, showing the pool with claims, state, notes,
and click-to-assign, live-pushed via the existing board-push Notifier.

## New machinery

Revised after the matrix decision. Plan 1 (core) has shipped; this is what Plans 2–3 add.

| New | Reused unchanged |
|---|---|
| migration 044 — `puzzles.feeder_tab_page_id` | `reconcile_table`'s append/update/prune engine, and its solver-owned column-span preservation |
| the matrix tab: create, name via `get_unique_tab_name`, header row, one tick column per target | `select_table` (single bot table per tab — the case it was designed for) |
| `columnType: "BOOLEAN"` in `addTable`'s `columnProperties` — **no checkbox or data-validation code exists anywhere in this repo today** | `execute_with_retry` backoff; callers participate by routing through existing helpers |
| column add/remove as Minis come and go | the Minimeta's existing staleness badge |
| a row-shaping function for the matrix (no equivalent of `contribution_rows` exists for it) | `claim_state` / `rival_names` / `provided_item_ids` / `diff_ticks` — all built and tested in Plan 1 |
| reading a non-key column and mapping it back to keys | `values().get` + the `key_to_row` pattern |
| `!sync_claims` + its web route | board push, audit log, locks |
| provided rows appended in `render_minimeta_contribution` (signature change) | the contribution table's `Type` vocabulary and `informs:free:` sourceless-row precedent |

**No `_values_batch_get` is needed** — a single tab means a single read. That was only
required by the superseded multi-tab design.

## Testing

**Pure, and still the highest value —** `bot/core/feeder_pool.py` (shipped in Plan 1):
`claim_state` across all four values from two viewing targets; `provided_item_ids` including
the supersede rule; `diff_ticks` including a stale tick on a pruned row and the
never-drop-a-confirmed-claim rule. Every case uses at least two items and two minis, so a
transposed id cannot pass.

**Service level —** confirm-on-solve and its inverse, exercised through **both** entrypoints
(`bot/cogs/puzzles.py` and `bot/web/actions.py`), not just the service.

**Gating —** `tests/test_web_route_gating.py` and `tests/test_command_gating.py` assert whole
maps, so every new command and route must be added there or the suite flags the gap.

### What the suite cannot verify — read this before trusting a green run

**Every Sheets test in this repo asserts request shape against mocks.**
`tests/test_contribution_table_requests.py` says so in its own header: it patches the cog's
batch/low-level seams and asserts the `batchUpdate` request bodies. That verifies column
indices, ranges, request ordering and which table name was targeted. It verifies **nothing**
about what Google does with those requests.

Concretely, a plan can gate on *"the `addTable` request carries `columnType: BOOLEAN` at
column index 3"*. It **cannot** gate on:

- the checkbox actually rendering,
- what a ticked box reads back as through `values().get` (formatted strings, not bools — and
  an all-unticked column may omit `values` entirely),
- whether `addTable` accepts `columnType` on a header-only range,
- how the tab behaves once a solver sorts or filters it.

Those need a **live probe against a real spreadsheet**. The project's established form for
this is a one-off script under `scripts/` (`diagnose_addtable.py`, `diagnose_generate.py`)
plus a recorded manual session (`.ai/archive/2026-06-26-milestone-d-live-test/`). Plan 2 is
not done until that probe has been run and its result recorded — a green suite is not
evidence here, and saying otherwise would misrepresent the coverage.

One live finding already on record, so it is not re-derived: **a native Table sharing columns
with header merges above it is fine** — the column-overlap hypothesis was tested and
disproven. A Table must still not *overlap* a merged range.

## Acceptance criteria

Criteria 1–8 are **delivered by Plan 1** (verified: suite 1981 green at `288c9a5`).

1. A Minimeta with an empty pool behaves exactly as it does today.
2. Pasting 30 feeders creates 30 items in paste order; re-pasting the same list adds nothing —
   including when the re-paste differs only in case or accents.
3. An over-length line is rejected without inserting any of the paste.
4. Solving a mini confirms its claims and leaves rival claims intact.
5. Unsolving restores the exact contested set from before the solve.
6. Editing a feeder's text preserves its claims and note; removing one cascades both away.
7. A confirmed claim cannot be dropped by `!unclaim_feeder` — only by unsolving.
8. Everything keys on `parent_puzzle_id`, so retyping a mini with `!type` changes nothing.

Criteria 9–17 are **Plan 2**:

9. A Minimeta with a non-empty pool gets one `Feeders — <name>` tab, recorded in
   `puzzles.feeder_tab_page_id`, and creating it a second time is a no-op.
10. The matrix carries one tick column per claim target, `main` first, then Minis in creation
    order; adding a Mini appends a column at the far right and moves nothing.
11. Ticking a cell and running `!sync_claims` creates that claim; unticking removes it.
12. A tick on a row whose feeder was removed is ignored, not adopted.
13. Unticking a **confirmed** claim does not remove it, and the next render restores the tick.
14. A note typed in column C survives every render and is adopted on sync.
15. Solving a mini ticks and locks its claims; unsolving releases them.
16. A feeder ticked in the `main` column appears as a `provided:<id>` row on the parent's
    contribution table with `Type = Provided`; a confirmed claim elsewhere removes it.
17. A failed matrix render marks the Minimeta "sheet stale", and `!sync_claims` clears it.

**Not gate-verifiable** (see "What the suite cannot verify"): 9's real tab creation, the
checkbox rendering in 10, and the read-back format underlying 11–13. Those require the live
probe.

## Constraints

- Only feeders claimed to the Minimeta itself (`target_id = minimeta.id`) appear — driven by
  the `main` column of the matrix. Never the whole pool: that would tip every loose string
  into the meta's answer table.
- **`render_minimeta_contribution(minimeta, members)` has no claims argument**
  (`bot/cogs/puzzles_gsheet.py:1518`), and both `bot/core/puzzle_service.py:741` and
  `tests/test_refresh_minimeta.py`'s `assert_awaited_once_with(minimeta, minis)` pin the
  current shape. Adding provided rows changes that signature and both call sites.
- `render_minimeta_contribution` builds its rows inline rather than via
  `contribution_rows`, so the provided rows are appended there.
- `provided:<item_id>` is a fresh key namespace, so the column-A diff is unaffected.
- The contribution table is therefore a **third surface that re-renders on feeder changes**
  — on sync, and on confirm-on-solve (confirming to a mini can supersede a main claim and
  remove a row).

## Command and route surface

Naming follows the existing `!make_minimeta` / `!create_mini` family. Every command has a
web equivalent, per the project's parity principle.

| Command | Route | Gate |
|---|---|---|
| `!add_feeders <minimeta>` + multi-line paste | `POST /puzzles/<id>/feeders` | ungated |
| `!feeders <minimeta>` (list embed) | pool data in the board payload | ungated |
| `!edit_feeder <key> <text>` | `PATCH /puzzles/<id>/feeders/<item_id>` | ungated |
| `!remove_feeder <key>` | `DELETE /puzzles/<id>/feeders/<item_id>` | organiser |
| `!sync_claims <minimeta>` | `POST /puzzles/<id>/feeders/sync` | ungated |

Gating follows the 2026-08-11 audit's area-based principle: building a pool is a
Create-area operation (like `!make_minimeta`, ungated); destroying part of one is not (like
`!remove_minimeta`, organiser); syncing is a refresh (ungated).

Re-pasting into `!add_feeders` is additive — new lines append, existing ones are left alone
(the `UNIQUE (minimeta_puzzle_id, text)` constraint makes this a natural upsert), so
pasting the list twice is harmless.

Dashboard: a Feeder Pool panel on the Minimeta, showing the pool with claims, state, notes,
and click-to-assign, live-pushed via the existing board-push Notifier.

## New machinery

Revised after the matrix decision. Plan 1 (core) has shipped; this is what Plans 2–3 add.

| New | Reused unchanged |
|---|---|
| migration 044 — `puzzles.feeder_tab_page_id` | `reconcile_table`'s append/update/prune engine, and its solver-owned column-span preservation |
| the matrix tab: create, name via `get_unique_tab_name`, header row, one tick column per target | `select_table` (single bot table per tab — the case it was designed for) |
| `columnType: "BOOLEAN"` in `addTable`'s `columnProperties` — **no checkbox or data-validation code exists anywhere in this repo today** | `execute_with_retry` backoff; callers participate by routing through existing helpers |
| column add/remove as Minis come and go | the Minimeta's existing staleness badge |
| a row-shaping function for the matrix (no equivalent of `contribution_rows` exists for it) | `claim_state` / `rival_names` / `provided_item_ids` / `diff_ticks` — all built and tested in Plan 1 |
| reading a non-key column and mapping it back to keys | `values().get` + the `key_to_row` pattern |
| `!sync_claims` + its web route | board push, audit log, locks |
| provided rows appended in `render_minimeta_contribution` (signature change) | the contribution table's `Type` vocabulary and `informs:free:` sourceless-row precedent |

**No `_values_batch_get` is needed** — a single tab means a single read. That was only
required by the superseded multi-tab design.

## Testing

**Pure, and still the highest value —** `bot/core/feeder_pool.py` (shipped in Plan 1):
`claim_state` across all four values from two viewing targets; `provided_item_ids` including
the supersede rule; `diff_ticks` including a stale tick on a pruned row and the
never-drop-a-confirmed-claim rule. Every case uses at least two items and two minis, so a
transposed id cannot pass.

**Service level —** confirm-on-solve and its inverse, exercised through **both** entrypoints
(`bot/cogs/puzzles.py` and `bot/web/actions.py`), not just the service.

**Gating —** `tests/test_web_route_gating.py` and `tests/test_command_gating.py` assert whole
maps, so every new command and route must be added there or the suite flags the gap.

### What the suite cannot verify — read this before trusting a green run

**Every Sheets test in this repo asserts request shape against mocks.**
`tests/test_contribution_table_requests.py` says so in its own header: it patches the cog's
batch/low-level seams and asserts the `batchUpdate` request bodies. That verifies column
indices, ranges, request ordering and which table name was targeted. It verifies **nothing**
about what Google does with those requests.

Concretely, a plan can gate on *"the `addTable` request carries `columnType: BOOLEAN` at
column index 3"*. It **cannot** gate on:

- the checkbox actually rendering,
- what a ticked box reads back as through `values().get` (formatted strings, not bools — and
  an all-unticked column may omit `values` entirely),
- whether `addTable` accepts `columnType` on a header-only range,
- how the tab behaves once a solver sorts or filters it.

Those need a **live probe against a real spreadsheet**. The project's established form for
this is a one-off script under `scripts/` (`diagnose_addtable.py`, `diagnose_generate.py`)
plus a recorded manual session (`.ai/archive/2026-06-26-milestone-d-live-test/`). Plan 2 is
not done until that probe has been run and its result recorded — a green suite is not
evidence here, and saying otherwise would misrepresent the coverage.

One live finding already on record, so it is not re-derived: **a native Table sharing columns
with header merges above it is fine** — the column-overlap hypothesis was tested and
disproven. A Table must still not *overlap* a merged range.

## Acceptance criteria

1. A Minimeta with an empty pool behaves exactly as it does today — no new tables render,
   no new columns appear.
2. Pasting 30 feeders creates 30 items in paste order; re-pasting the same list adds
   nothing.
3. Ticking a feeder on mini 1's tab and syncing shows it as `claimed`/`mini1` on mini 2's
   tab and in the parent pool table.
4. The same feeder ticked on two mini tabs shows as contested in both places and in the
   dashboard, without error.
5. Solving mini 1 confirms its claims, leaves mini 3's rival claims intact but rendered as
   `taken`, and reports what it did.
6. Unsolving mini 1 restores the exact contested set from before the solve.
7. A solver's note and claim tick survive an unrelated re-render of the table.
8. Editing a feeder's text preserves its claims and note; removing a feeder cascades both
   away.
9. Filtering a mini's block by `state=free` lists exactly the feeders no other target
   claims.
10. Sorting a mini's block by hand does not break the next sync.
11. A failed pool render marks the Minimeta "sheet stale" in the dashboard, and a
    subsequent `!sync_claims` clears it.
12. Ticking a feeder's `main` column and syncing adds a `provided:<id>` row to the parent's
    contribution table, with `Type = Provided` and the feeder text in `Content`, sitting
    alongside the `Feeder` rows for the mini answers.
13. That provided row appears while the claim is unconfirmed — it does not wait for
    anything to be solved.
14. Solving a mini that had also claimed that feeder removes the provided row, while the
    main-meta claim survives in the pool rendered as `taken`.
15. Unsolving that mini brings the provided row back.
16. The whole pool never appears in the contribution table — only feeders claimed to the
    Minimeta itself.

## Constraints

- Do not change existing Minimeta, rollup, or contribution-table behaviour.
- Do not add a second writer to any column. Every column has exactly one owner.
- Do not put a blocking Sheets read on the solve path.
- Follow the existing table-by-name discipline — never resolve the bot's table by position.
