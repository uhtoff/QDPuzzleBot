# M6 — Additive typed-group columns on `rounds` + `tags`

Date: 2026-06-22
Milestone: **M6** (deps: M0, M5) — from `docs/PLANS.md`.

## Goal

Lay the typed-group substrate the structural features (M7 meta-meta/aggregation, M8
clusters, M9 capstones, M11 feeder-threads) all build on, **additively and with zero
behaviour change**. Keep the table named `rounds` and the load-bearing `round_id`
membership key (a full rename is ~360 edits across 19 files for no feature payoff);
instead add typed columns that let a `rounds` row be reasoned about as a *TagGroup* of a
given `kind`. This is simultaneously the conservative `parent_round_id`+`puzzle_edges`
proposal and the bold tag-group model, at conservative risk.

Per the resolved decision in PLANS Part 1, M6 only *lands and maintains* the columns —
nothing **reads** them for behaviour yet (the board's solved section stays
Discord-position-driven; channel-less groups are not exercised).

## Design decisions (locked during brainstorming)

1. **kind/status liveness:** backfill **and keep-updated** on existing write paths
   (create sets `kind`; the meta-solve hook sets `status='solved'`), so the columns
   never go stale — but nothing reads them for behaviour in M6. `parent_group_id` /
   `tags.edge_type` stay fully dormant at their defaults.
2. **"A round is solved when its meta is solved"** — `status` tracks the round's meta
   puzzle. Metaless rounds (no meta) stay `active`; they remain a **grouping aid** for
   now (may be retired in a later milestone, so we don't over-invest in their status).
3. **Cycle-guard shipped now** as a unit-tested guarded setter for `parent_group_id`.
   It guards **group nesting only** (the containment tree) and has nothing to do with
   puzzle answer-feeding.
4. Keep `rounds` table + `round_id` SQL; add columns additively.

## Two graphs (do not conflate)

This distinction is the crux of the cycle-guard and was explicitly checked against the
MITMH-2025 "two puzzles feed each other" case:

- **Feeding / membership graph** — `tags` rows carrying `edge_type` (puzzle → group).
  "Puzzle A's answer feeds group B." May be **mutual** (A→B *and* B→A) and bidirectional
  meta-to-meta; this never loops because a roll-up is a **snapshot read** of whatever
  answers exist now (partial or full). **The cycle-guard never touches this.**
- **Containment / nesting graph** — `parent_group_id` (group → *parent* group). "Round B
  is nested inside Round A," a tree for subtree walks. A cycle here is a broken tree.
  **The cycle-guard forbids only this.**

## Components

### 1. Migration `003_typed_groups.sql` (additive; ships before code)

`rounds`:
```sql
ALTER TABLE `rounds`
  ADD COLUMN `kind`            varchar(20) DEFAULT NULL,
  ADD COLUMN `status`          varchar(20) NOT NULL DEFAULT 'active',
  ADD COLUMN `parent_group_id` int(11)     DEFAULT NULL,
  ADD CONSTRAINT `ParentGroupID` FOREIGN KEY (`parent_group_id`)
      REFERENCES `rounds` (`id`) ON DELETE SET NULL ON UPDATE CASCADE;
```
`category_id` is **already** `bigint DEFAULT NULL` — no change needed.

`tags`:
```sql
ALTER TABLE `tags`
  ADD COLUMN `edge_type` varchar(32) NOT NULL DEFAULT 'feeds-meta';
```
Existing `tags` rows pick up `feeds-meta` automatically on ALTER.

**Backfill `kind`** (order matters):
1. `metaless` where `meta_id IS NULL OR meta_id = 0`.
2. `meta` where `meta_id` is set (non-null, non-zero).
3. override to `metameta` where the meta puzzle has `metameta = 1`
   (`UPDATE rounds r JOIN puzzles p ON p.id = r.meta_id SET r.kind='metameta' WHERE p.metameta=1`).

**Backfill `status`**:
`archived` if `archive_time IS NOT NULL`, else `solved` if `solve_time IS NOT NULL`
(the round's `solve_time` is stamped when its meta solves), else `active`.

### 2. `RoundData` + cleanups (`bot/store/round_data.py`, `bot/store/mysqldb.py`)

- Add fields so the generic `commit()` persists them:
  `kind: str = ""`, `status: str = "active"`, `parent_group_id: Optional[int] = None`.
- **Fix `meta_code`**: `int = 0` → `str = "0"` (it already holds the 6-char handle, and
  `meta_code_autocomplete` already guards `isinstance(r.meta_code, str)`).
- **Delete the dead `RoundJsonDb.get`**: `MySQLRoundJsonDb.get(round_id)` queries
  `WHERE channel_id` though `RoundData` has no `channel_id` — broken and unused. Remove
  it and the `_RoundJsonDb.get` interface stub. (Verify no callers first.)

### 3. Keep `kind`/`status` accurate (no behaviour reads)

- **Create paths set `kind`**: `web_create_round` / `!mp` → `meta`; `!rnm` (metaless
  round) → `metaless`. (`metameta` is only ever set by backfill or a manual DB edit, as
  today — no code writes the `metameta` flag.)
- **Solve hook**: in `PuzzleService.solve`, at the existing branch where
  `meta_round.solve_time` is stamped (the solved puzzle *is* the round's meta), also set
  `meta_round.status = 'solved'` before `RoundJsonDb.commit(meta_round)`.
- **Revert hooks where they already exist**: `!unsolve` sets the round back to `active`
  if it was the meta; round-archive sets `archived`. Only wire these where the code
  already touches the round on those events — do not add new traversals.
- Nothing reads `status`/`kind` for behaviour. The solved section stays driven by
  `get_solve_divider_position` + the `———solved———` divider channel.

### 4. Cycle-guarded nesting setter (`PuzzleService.set_parent_group`)

```
async def set_parent_group(self, child_round, parent_id):
    """Nest child_round under parent group `parent_id`, rejecting containment cycles.
    Guards group NESTING only — never affects tags/edge_type answer-feeding."""
```
- `parent_id = None` clears nesting (always allowed).
- Reject if `parent_id == child_round.id` (self-nest).
- Walk the prospective parent's ancestor chain via `parent_group_id`
  (`RoundJsonDb.get_by_attr(id=...)`); reject with `ValueError` if `child_round.id`
  appears (would close a cycle). A depth cap (e.g. 64) backstops a pre-existing broken
  chain.
- On success set `child_round.parent_group_id` and `RoundJsonDb.commit`.
- Not called by any M6 behaviour — substrate for M7. Unit-tested.

### 5. `edge_type` dormant

Lands at default `feeds-meta`. The existing `tags` insert
(`MySQLPuzzleJsonDb.commit`) omits `edge_type`, so the default applies; M7 will set it
explicitly per membership.

## Testing

- **Backfill** (seeded test DB): a meta round → `kind='meta'`; a metameta → `metameta`;
  a metaless round → `metaless`; `status` = `solved`/`archived`/`active` from
  solve/archive times. New `tags` rows default to `edge_type='feeds-meta'`.
- **`RoundData`**: round-trips `kind`/`status`/`parent_group_id`; `meta_code` is a `str`.
- **Create sets `kind`**: a meta create yields `kind='meta'`; `!rnm` yields `metaless`.
- **Solve sets `status`**: solving a round's meta sets that round `status='solved'`
  (mock cog, mirroring the M5 refresh_meta tests).
- **Cycle-guard**: nesting a child under an unrelated parent succeeds; self-nest and
  nesting under a descendant raise `ValueError`; `parent_id=None` clears.

## Constraints / non-goals

- **Migration → deploy → code.** `003` runs before the new code (the generic `commit()`
  writes every dataclass field by name).
- **No behaviour change / no reads.** `kind`/`status`/`parent_group_id`/`edge_type` are
  not read by any board, command, or sheet logic in M6.
- **Channel-less groups not exercised** — `category_id` stays nullable but the ~12
  `get_by_attr(category_id)` lookups still assume a category; left for a later milestone.
- No `rounds`-table rename; no `round_id` → `group_id` rename.
- Leave the unrelated dirty `Pipfile.lock` out of any M6 commit.

## Acceptance criteria

- Migration `003` stands up the columns; backfill yields correct `kind`/`status` for the
  existing rows; new `tags` rows default to `feeds-meta`.
- `RoundData` persists/loads the new fields; `meta_code` is typed `str`.
- Creating a meta/metaless round sets `kind`; solving a round's meta sets the round
  `status='solved'`.
- `set_parent_group` rejects self/descendant cycles and accepts valid nesting; no M6
  behaviour path calls it.
- The dead `RoundJsonDb.get` is gone with no broken callers.
- Full pytest suite green; nothing reads the new columns for behaviour.
