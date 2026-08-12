# Feeder Pool — design

Automating the given-feeder list for a **meta matching puzzle**.

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
- A `Feeder Pool` table on the parent's tab and a `Feeder Claims` block on every mini's
  tab, with a solver-tickable claim column read back on an explicit sync.
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

## Sheet surfaces

Both are native Tables reconciled by name, riding `reconcile_table` unchanged.

**Parent tab — `Feeder Pool <page_id>`.** A second table on a tab that already carries
`Contributions <page_id>`; `select_table` resolves them by name.

```
 key    item             claimed by       confirmed  │ note
 ────────── bot-owned, data_col_count=4 ─────────────┼── solver-owned ──
 f:12   ALBATROSS        mini1            mini1      │ fits SEA
 f:13   MARZIPAN         mini1, mini3     —          │ clash?
 f:14   QUAY             —                —          │ homeless
```

**Each mini tab — `Feeder Claims <page_id>`.**

```
 key    item             state     elsewhere      │ claim
 ────────── bot-owned, data_col_count=4 ──────────┼── solver-owned ──
 f:14   QUAY             free      —              │  [x]
 f:13   MARZIPAN         claimed   mini1          │  [ ]
 f:12   ALBATROSS        taken     mini1          │  [ ]
 f:15   TERN             claimed   mini1, mini3   │  [x]
```

`state` and `elsewhere` are split rather than combined so the native Table's filter
dropdown gives exactly three buckets: filter `state=free` for what is genuinely up for
grabs, sort by `state` to sink settled items. Naming the rivals is the point — an
unconfirmed claim by another mini is *useful information*, not noise, even when it turns
out to be wrong.

**Authority is uniform: the bot owns every column except the solver-owned one at the far
right of each table.** `note` and `claim` sit outside the bot-written span, so
`reconcile_table` structurally cannot clobber them. `item` sits *inside* the span, which is
why text is not sheet-editable (see "Editing a feeder's text").

The claim column is a boolean data-validation range. **This is the only genuinely new
Sheets call in the feature**; everything else is an existing method.

## Sync

`sync_feeder_claims(hunt, minimeta)` — the explicit pull:

1. Read the claim column from every mini tab, plus the note column from the parent pool
   tab.
2. Diff ticks against `feeder_claims`; insert and delete **unconfirmed** rows only.
3. Adopt changed notes.
4. **Ignore any key not currently in the pool.** `reconcile_table` blanks pruned rows
   rather than deleting them (`:1166`), so a tick can survive on the blanked row of a
   removed item. Without this rule that stale tick resurrects a dead claim.
5. Re-render both surfaces — one tick changes `state` on every other mini's block.
6. Report: claims added, claims dropped, newly contested items, still-homeless count.

**Confirmed claims are bot-owned.** Unticking a confirmed claim does not undo it; the next
render restores the tick. Unsolving the mini is the way to undo a confirmation.

Reading N mini tabs wants a `_values_batch_get` on the gsheet cog (the existing
`_values_get` at `bot/cogs/puzzles_gsheet.py:846` takes a single range). N separate calls is
an acceptable first cut — each is already retry-wrapped — but the batch form is preferred.

## Confirm on solve

Solving a mini is proof that its claims were right, so confirmation is automatic rather
than a manual tick.

Hooked at `bot/core/puzzle_service.py:1286` (`solve`), firing only when the puzzle is a
`Mini` whose parent has a non-empty pool:

1. Set `confirmed=1` on every claim held by that mini.
2. **Rival claims are left alone.** Confirmation never deletes. A losing claim is
   information worth keeping, and deleting it would make unsolve lossy — you cannot
   resurrect what you deleted. Rivals simply render as `taken` from then on.
3. Re-render both surfaces.

`unsolve` (`:1370`) is the exact inverse: set `confirmed=0` on that mini's claims. The
contested set returns intact.

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

**Decision: share the Minimeta's existing badge, and don't badge the mini claim blocks.**

- A failure of *either* the mini-list render or the pool render marks the Minimeta stale.
  The badge does not say which failed. It doesn't need to: the fix for both is
  `!sync_feeders`, which rewrites every surface and reports what it fixed.
- Mini claim blocks are best-effort and unbadged. They self-heal on the next sync.
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

## Command and route surface

Naming follows the existing `!make_minimeta` / `!create_mini` family. Every command has a
web equivalent, per the project's parity principle.

| Command | Route | Gate |
|---|---|---|
| `!add_feeders <minimeta>` + multi-line paste | `POST /puzzles/<id>/feeders` | ungated |
| `!feeders <minimeta>` (list embed) | pool data in the board payload | ungated |
| `!edit_feeder <key> <text>` | `PATCH /puzzles/<id>/feeders/<item_id>` | ungated |
| `!remove_feeder <key>` | `DELETE /puzzles/<id>/feeders/<item_id>` | organiser |
| `!sync_feeders <minimeta>` | `POST /puzzles/<id>/feeders/sync` | ungated |

Gating follows the 2026-08-11 audit's area-based principle: building a pool is a
Create-area operation (like `!make_minimeta`, ungated); destroying part of one is not (like
`!remove_minimeta`, organiser); syncing is a refresh (ungated).

Re-pasting into `!add_feeders` is additive — new lines append, existing ones are left alone
(the `UNIQUE (minimeta_puzzle_id, text)` constraint makes this a natural upsert), so
pasting the list twice is harmless.

Dashboard: a Feeder Pool panel on the Minimeta, showing the pool with claims, state, notes,
and click-to-assign, live-pushed via the existing board-push Notifier.

## New machinery

| New | Reused unchanged |
|---|---|
| 1 migration, 2 tables | the append/update/prune diff engine (`reconcile_table`) |
| `bot/core/feeder_pool.py` — pure: row shaping, `state` derivation, tick diffing | table-selection-by-name (`select_table`) |
| 2 sheet renderers, thin wrappers modelled on `render_minimeta_contribution` | solver-owned-column span preservation |
| boolean data-validation for the claim column — the only new Sheets call | board push, audit log, locks; the Minimeta's existing staleness badge, shared (see "Staleness") |
| `_values_batch_get` (or N calls as a first cut) | the `refresh_minimeta` lifecycle to hang renders off |
| ~4 service methods + the solve/unsolve hook | both solve entrypoints, via the one seam |
| 5 commands, 5 routes, 1 dashboard panel | |

A medium feature, not a subsystem. The expensive part — a bidirectional sheet sync with
conflict rules — already exists and already has a precedent for a solver-owned column
inside a bot-owned table.

## Testing

**Pure, and the highest value —** `bot/core/feeder_pool.py`:

- `state` derivation across all four values, from more than one viewing target. A single
  item or a single viewer cannot catch a transposed target id, so every case uses at least
  two minis and two items.
- Tick diffing: added, dropped, unchanged, and a tick on a key absent from the pool
  (must be ignored, not adopted).
- Confirmed claims are never dropped by an untick.

**Service level —** confirm-on-solve and its inverse:

- Solving a mini confirms exactly its own claims and no others.
- Rival claims survive confirmation and read as `taken`.
- Unsolve restores the exact pre-solve contested set.
- A Sheets failure during the post-solve render leaves the solve applied and the rollup
  badged, not the solve rolled back.

**Both edges —** the solve hook must be probed through *both* entrypoints
(`bot/cogs/puzzles.py` and `bot/web/actions.py`), not just the service, or the test proves
nothing about the parity that motivated the shared seam. The existing
`tests/test_solve_equivalence.py` is the model.

**Gating —** the route→tier map in `tests/test_web_route_gating.py` and the command probe in
`tests/test_command_gating.py` both assert whole maps, so the five new commands and routes
must be added there or the suite will flag the gap.

**Not gate-verified:** the sheet rendering itself. The Google Sheets calls are mocked in
tests, so column spans, the data-validation checkbox, and the multiple-tables-per-tab
resolution are verified by inspection and by trying it on a real hunt sheet — not by the
suite. Worth one manual pass on staging before this is considered done.

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
    subsequent `!sync_feeders` clears it.

## Constraints

- Do not change existing Minimeta, rollup, or contribution-table behaviour.
- Do not add a second writer to any column. Every column has exactly one owner.
- Do not put a blocking Sheets read on the solve path.
- Follow the existing table-by-name discipline — never resolve the bot's table by position.
