# Multi-edge tag membership + evidence-driven informs

Date: 2026-06-28
Status: design — approved direction, pending spec review

## Goal

Let a single puzzle relate to one target/group in more than one way at the same time —
specifically, **feed it (answer) and inform it (info) simultaneously**. Today a
`(puzzle, tag_group)` membership carries exactly one `edge_type`, forcing an XOR between
`feeds` and `informs`: setting one evicts the other, so an informing feeder's answer
disappears from the contribution table.

Rationale (user's framing): a typed many-to-many join only earns its keep if the
relationship's *type* is part of its identity. If each pair could only ever hold one edge,
`edge_type` would be a mere attribute and a column on the puzzle would do as well. Putting
`edge_type` in the key is what makes multiple typed relationships possible — which is the
point of the join table.

Two concrete wins:
1. A feeder can both feed and inform its target; both render.
2. Linking two puzzles in the **evidence board** auto-sets an `informs` relationship
   (a "hint") that surfaces the info with no manual tagging and **never evicts** an
   existing `feeds`.

## Decision (locked)

- **C1, multi-edge tags.** Widen `tags` uniqueness to `(tag_group_id, puzzle_id, edge_type)`.
- Evidence-board link auto-adds an `informs` edge (coexists with `feeds`).
- `target_puzzle_id` and the target model are **out of scope** — C1 leaves them untouched
  (a self-feeding target simply keeps both its `target` row and a `feeds` row). The
  earlier `is_target`-flag idea (C2/C3) is shelved.

### Accepted sub-decisions
- **`bulk_re_edge` / convert-to-capstone**: keep current *replace* semantics (flip
  feeders' `feeds` → `informs`), not additive — it is a deliberate type conversion.
- **Pooled exclusivity**: `none` and `feeds`/`informs` are mutually exclusive. Setting
  `none` clears `feeds`/`informs`; adding `feeds`/`informs` clears `none`.
- **Unlink lifecycle**: clearing an evidence link auto-removes the informs edge only if the
  source has no remaining evidence routed to that target (a manually-set informs is never
  silently removed).

## Legal edge combinations (app-enforced)

Per `(puzzle, group)` pair, the edge set is one of:
- a non-empty subset of `{feeds, informs}` (either or both), or
- exactly `{none}` (pooled), or
- `{target}` optionally plus `feeds`/`informs` (a target that self-feeds/-informs).

`none` never coexists with any other edge.

## Data model

### Schema — migration `020_multi_edge_tags.sql`
```sql
ALTER TABLE `tags` DROP KEY `tag_uq`,
  ADD UNIQUE KEY `tag_uq` (`tag_group_id`, `puzzle_id`, `edge_type`);
```
Existing rows are already unique under the wider key (they were unique under the narrower
one and `edge_type` is `NOT NULL DEFAULT 'feeds'` since `003`/`012`), so **no dedup or
backfill** is needed. (Migration `019` is the current head → next is `020`.)

### In-memory — `bot/store/puzzle_data.py:48`
`tag_edges: Dict[int, str]` → `Dict[int, set[str]]` (one entry per group, holding the set
of that pair's edges). All readers updated (below).

## Component changes

### Store (`bot/store/mysqldb.py`)
- **`get_tags` (417)** — build `tag_edges[gid]` as a `set`, adding each row's `edge_type`.
- **`tag_puzzle_with_edge` (452)** — now an **add**: `INSERT ... ON DUPLICATE KEY UPDATE
  edge_type = edge_type` (no-op on dup; the wider key makes each `(g,p,edge)` its own row).
  Update docstring (it currently claims reliance on the 2-col key).
- **New `delete_tag_edge(puzzle_id, tag_group_id, edge_type)`** — `DELETE ... AND
  edge_type = %s` (remove one edge). Keep **`delete_tag` (466)** for full removal (all
  edges of a pair) — used by `clear`/whole-membership removal.
- **`bulk_update_edge_type` (478)** — unsafe as-is under the wider key (two rows for a pair
  set to the same value collide). Reimplement as: delete rows of the source edge that would
  collide, then update the rest — or, simplest, `DELETE FROM tags WHERE tag_group_id=%s AND
  edge_type=<old>` then re-insert `<new>` for affected puzzles. Scope it to the
  feeds→informs conversion `bulk_re_edge` actually needs.
- **`get_all_from_tag_group` (343)** — dedupe: `WHERE puzzles.id IN (SELECT puzzle_id FROM
  tags WHERE tag_group_id=%s)` (avoids returning a feeds+informs puzzle twice).
- **`MySQLPuzzleJsonDb.commit` (490)** — the membership reconcile is group-id-granular and
  inserts bare rows (default edge). **Risk area. Resolved:** make `tag_edges` the single
  source of truth and rewrite the reconcile to operate at `(tag_group_id, edge_type)`
  granularity against it — delete DB rows absent from `tag_edges`, insert rows present in
  it (always with an explicit `edge_type`), and derive `puzzle.tags` from
  `tag_edges.keys()`. Audit every direct `puzzle.tags` mutation (e.g. `convert_to_type`
  `add_tag` at 820) so they round-trip through `tag_edges`. Must never insert a default
  `feeds` row.

### Service (`bot/core/puzzle_service.py`)
- Single-edge readers → set membership tests (`== "x"` → `"x" in edges`): `is_round_feeder`
  (114), `is_capstone` (125), `is_metameta` (138), `select_rollup_members` (608, 621),
  `bulk_re_edge` counter (778), feeder collection (929), `_sole_informs_target` (1829),
  `refresh_informs_targets` (1906).
- Edge writers operate on the set: self-`target` assigns (825, 1363, 1608).
- **`tag_with_edge` (679)** — add semantics (add this edge, keep others). The contribution
  refresh stays.
- **`tag_edge_only` (695)** — still sweeps the same edge on *other* groups; now per-edge.
- **`untag_from` (738)** — already takes `edge=None`. `edge=None` → remove whole membership
  (all edges); `edge=X` → remove just that edge row. Keep the meta last-tag guard.
- `default_edge_for_group` (157) — unchanged.

### Web (`bot/web/`)
- **`retag_puzzle` (`actions.py:794`)** — `mode:"add"` + `edge` now **adds** that edge
  (not replace). `mode:"remove"` takes an **optional `edge`**: omitted → remove the whole
  membership (all edges, as today); with `edge` → remove just that one edge row. Pooled
  exclusivity enforced server-side.
- **`update_evidence` (`actions.py:1641`)** — after `set_target`, also `add_edge(source,
  target_group, "informs")`. On clear (`target_puzzle_id` null/changed), remove the
  auto-informs edge per the unlink lifecycle. Mirror the same in the bang path
  (`log_clues` / `_sole_informs_target`, `puzzle_service.py:1836`).
- **Board API (`data.py:158`)** — the LEFT JOIN now yields multiple tag rows per pair.
  Aggregate per `tag_group_id` into one `rounds[]` entry with `edges: string[]` (replacing
  scalar `edge_type`). Keep `is_target_of`.

### Contribution render (`bot/core/contribution.py:17`, `puzzles_gsheet.py:1429`)
`contribution_rows` iterates per *edge* of each member instead of the single value, so a
puzzle with `{feeds, informs}` emits a `feeds:<pid>` Feeder row **and** `informs:<pid>:<eid>`
Information rows. Keys are already edge-anchored, so no key collisions. A bare `informs`
edge with no evidence routed yet still renders an `informs:<pid>` Information / blank row,
as today. `render_contribution` self-edge guard (1447) updated for the set.

### Frontend
- **Types (`types.ts`)** — `PuzzleTagGroup.edge_type: string` → `edges: string[]`.
- **`PuzzleTable.tsx`** — `edgeCode`/`edgeColor`/`edgeTitle`/`EdgeBadge` render the set
  (e.g. `F+I`). Edit menu becomes per-edge **toggles** (feeds on/off, informs on/off; `none`
  and `target` as before) plus the existing "Remove from group" (whole membership).
- **`api.ts`** — retag payload supports add/remove of a single edge.
- **`DragBoard.tsx`** — uses `is_target_of` only; unaffected.

## Phasing
1. **Model + primitives + render + migration** — schema, `tag_edges` set, store primitives,
   service readers, `contribution_rows`, board API aggregation, frontend badge rendering.
   Self-feeding-target coexistence falls out here.
2. **Evidence link → informs hint + menu** — `update_evidence`/`log_clues` auto-informs and
   unlink lifecycle; per-edge toggle menu.

## Risks
- **`commit` reconcile** (store) — easiest place to silently corrupt edges; needs the most
  care / tests.
- **`bulk_update_edge_type`** — duplicate-key collisions under the wider key.
- **Board API shape change** — `rounds[].edge_type` → `edges[]` touches every reader of
  that field; grep before changing.
- Any code assuming `tag_edges[gid]` is a string will break loudly (good) — sweep all
  `.tag_edges` accesses, not only the ones listed.

## Acceptance criteria
- A puzzle can hold `feeds` + `informs` to one group; both a Feeder row and Information
  row(s) render on the target's tab.
- Adding `informs` does **not** remove an existing `feeds` (and vice-versa); setting `none`
  clears both; `feeds`/`informs` clears `none`.
- Linking source→target in the evidence board surfaces the info on the target's tab with no
  manual tag; clearing the link removes the auto-informs only when no evidence remains.
- A self-feeding target keeps its `target` row and renders its self-feed.
- `bulk_re_edge` (convert-to-capstone) still flips feeders feeds→informs with no duplicate
  rows or duplicate members.
- Existing single-edge puzzles behave exactly as before (migration is non-destructive).

## Resolved decisions (were open questions)
1. **`commit` reconcile** — make `tag_edges` the source of truth; reconcile the `tags`
   table edge-by-edge against it; derive `puzzle.tags` from it. (See the store bullet.)
2. **Per-edge remove API** — extend `mode:"remove"` with an optional `edge` (omitted =
   whole membership; with `edge` = that one edge).
3. **Bare `informs` rendering** — keep current behaviour: render an Information / blank row
   even with no evidence yet.
