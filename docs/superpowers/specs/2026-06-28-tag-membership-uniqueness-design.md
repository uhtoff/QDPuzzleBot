# Tag-membership uniqueness — implications of a `(tag_group, puzzle, edge_type)` key

Date: 2026-06-28
Status: analysis / design (no implementation; decision still open)

## Goal

Decide whether to let a `(puzzle, tag_group)` membership carry more than the single
`edge_type` it carries today, and understand the blast radius. The motivating wants:

1. A target whose target-ness survives a deliberate **self-feed** (today self-feeding
   overwrites the `'target'` self-edge — target-ness only survives via the separate
   scalar `tag_group.target_puzzle_id`).
2. The option of **more than one target per group** (split-meta), retiring the scalar
   `target_puzzle_id` in favour of edge-derived target-ness.

The user proposed widening the uniqueness to `UNIQUE (tag_group_id, puzzle_id, edge_type)`
(call it **C1**). This doc answers "are we leaning on the current uniqueness anywhere?"
and argues a lighter design (**C2**, a per-row role column) gets both wants for a fraction
of the cost.

## Current invariant

- `tags` has `UNIQUE KEY tag_uq (tag_group_id, puzzle_id)` — migration
  `005_tags_unique_key.sql` (it deduped first, then added the key).
- One row per pair, one `edge_type varchar(32)` (migration `003`, default `'feeds'`
  since `012`). Re-tagging the same pair **upserts that one row**.
- In memory: `PuzzleData.tag_edges: Dict[tag_group_id -> edge_type]` — one edge per group.

## Are we leaning on the uniqueness? Yes — in six places

These all assume **exactly one `tags` row per `(puzzle, group)`** and would break or
silently misbehave under C1 (multi-row):

1. **`tag_puzzle_with_edge`** — `bot/store/mysqldb.py:452-464`. `INSERT ... ON DUPLICATE
   KEY UPDATE edge_type`; its docstring already says "Relies on migration 005's UNIQUE
   KEY (tag_group_id, puzzle_id)." Under C1 the key includes `edge_type`, so re-pinning a
   pair from `feeds`→`informs` no longer collides — it **inserts a second row**. The
   puzzle ends up with two edges to the group. This is the single biggest dependence.
2. **`MySQLPuzzleJsonDb.commit`** — `bot/store/mysqldb.py:490-513`. Reconciles `tags`
   against `puzzle.tags` by **set arithmetic on `tag_group_id`** (add/remove whole
   memberships), and inserts added rows **with no `edge_type`** (relies on the column
   default). It is membership-granular, not edge-granular — it cannot represent or
   preserve multiple edges per group.
3. **`bulk_update_edge_type`** — `bot/store/mysqldb.py:478-488`. `UPDATE tags SET
   edge_type=%s WHERE tag_group_id=%s`. Under C1, a pair with two rows (e.g. `feeds` +
   `none`) set to the same value collapses to two identical `(g,p,X)` rows → **duplicate-key
   violation**.
4. **`delete_tag`** — `bot/store/mysqldb.py:466-476`. `DELETE ... WHERE puzzle_id AND
   tag_group_id` removes **all** edges for the pair; it has no edge granularity, so
   removing just one edge of a multi-edge pair is impossible without an `edge_type` arg.
5. **`get_tags` → `tag_edges` dict** — `bot/store/mysqldb.py:417-425`. Builds
   `tag_edges[gid] = edge_type`; a dict keyed by group id **can only hold one edge per
   group** (last row wins). The in-memory model structurally cannot represent multi-edge
   without changing the type and every reader.
6. **`get_all_from_tag_group`** — `bot/store/mysqldb.py:343-347`. `... LEFT JOIN tags ...
   WHERE tags.tag_group_id=%s` with **no `DISTINCT`**. Under C1 a puzzle with two edge
   rows is returned **twice** → double-counted members in every rollup / count / sheet.

Plus the ~10 in-memory readers that test `p.tag_edges.get(gid) == X` and assume a single
edge: `bot/core/puzzle_service.py` lines 114, 125, 138, 608, 621, 778, 929 (readers) and
the writers at 825, 1363, 1608. All would need "does **any** edge match" semantics under C1.

**Not affected:** `get_unassigned` (`mysqldb.py:404-410`, an EXISTS check that still
works), `get_is_target` (`mysqldb.py:439-450`, queries `tag_group`, not `tags`), and the
**separate** `evidence_items.target_puzzle_id` / `rollups.target_puzzle_id` columns (their
own FK + unique key; unrelated to this and must stay).

## The reframing: C1 is the heavy way; C2 gets the wants cheaply

Both motivating wants are **per-membership facts about a role**, not a need for one puzzle
to hold several different contribution edges to one group:

- "target survives self-feed" = this membership is *both* the target *and* contributes via
  `feeds`. Two facts, **one row**.
- "split-meta / many targets" = several **different** puzzles in the group each have a
  membership flagged as target. Still **one row per puzzle**.

Neither needs multiple rows for the *same* pair. So:

### C2 (recommended) — add a role to the single membership row

- Schema: keep `UNIQUE (tag_group_id, puzzle_id)`; add `tags.is_target TINYINT(1) NOT
  NULL DEFAULT 0` (or a `role` enum). One row per pair carries `edge_type` (contribution)
  **and** `is_target` (role).
- `target_puzzle_id` is **derived**, not stored: a group's targets are
  `SELECT puzzle_id FROM tags WHERE tag_group_id=? AND is_target=1`.
- Keeps the upsert, `commit` reconciliation, `bulk_update_edge_type`, `delete_tag`, and
  member queries **all intact** (no duplicate members, no upsert breakage).
- In memory: `tag_edges` stays one-per-group; add a parallel `target_groups: set[int]`
  (or a small `Membership` dataclass) — a contained change.
- Self-feed: a target sets `edge_type='feeds'` while `is_target` stays 1. The `'target'`
  EDGE value becomes redundant (role lives in the column) — keep it as a default contribution
  or retire it; either way the target survives.
- Multi-target: free — two rows in the group with `is_target=1`.
- Migration: backfill `is_target=1` from `tag_group.target_puzzle_id`, then the ~30
  `target_puzzle_id` read sites (see the earlier blast-radius pass) switch to the derived
  query/flag; finally drop the column. Bigger than nothing, far smaller than C1.

### C1 — `UNIQUE (tag_group_id, puzzle_id, edge_type)` (multi-row)

- Only buys something C2 doesn't if a **single** puzzle must hold **multiple distinct
  contribution edges to the same group at once** (e.g. simultaneously `feeds` and
  `informs` one meta). No current requirement asks for this.
- Costs all six dependences above: rework the upsert (delete-then-insert or per-edge
  upsert), make `commit` edge-granular, fix `bulk_update` for collisions, add `edge_type`
  to `delete_tag`, change `tag_edges` to a multimap and update every reader, and add
  `DISTINCT`/dedup to member queries.

### A — status quo

Keep `target_puzzle_id` authoritative; `'target'` edge stays a self-describing convenience
(commit `2e1c9e9`). Zero work; target-ness stored twice; self-feed overwrites the edge but
the scalar carries target-ness. The `T+F` badge already makes the divergence legible.

## Recommendation

If multi-target / edge-survivable target is wanted, do **C2**, not C1 — C1 is strictly more
work for a capability nobody has asked for. If multi-target is not actually wanted, stay on
**A**; the only thing C2 then buys is removing the dual storage of target-ness, which is a
tidy-up, not a feature.

## Open questions

1. Is multiple-targets-per-group (split-meta) a real product goal, or "probably confusing"
   (user's words)? This is the only thing that justifies any schema change.
2. If C2: keep the `'target'` edge value as a target's default contribution, or retire it
   in favour of `is_target` + a normal contribution edge?
3. Does anything ever need one puzzle to carry two contribution edges to the *same* group?
   If genuinely yes → C1; if no (expected) → C2.

## Acceptance criteria (if C2 is chosen)

- A target that self-feeds is still reported as the group's target (no reliance on the
  `'target'` edge value).
- A group can have ≥1 target; rollups/sheets/notifier handle >1 without double-counting.
- `tag_group.target_puzzle_id` is dropped; all former readers derive from `tags.is_target`.
- Upsert, `commit` reconcile, `bulk_re_edge`, remove, and member queries unchanged in
  behaviour for ordinary feeders.
