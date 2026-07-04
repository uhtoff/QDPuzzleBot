# Current Spec — auto-consolidation for category split/overflow

## Goal

The inverse of the split/overflow engine in `bot/core/category_layout.py`: when counts
drop, fold overflow categories back into lower-seq ones, and ultimately reverse the
unsolved/solved split (single category + divider) — without churn or Discord rename
rate limits.

## In scope

Phase A — `consolidate(guild, owner_id, dao)` core (category_layout, under `layout_lock`):
- Per role, unsolved first then solved:
  1. Reap dangling rows (row whose category no longer resolves) — the missing reaper.
  2. Delete empty seq>=1 overflow categories unconditionally (old cleanup semantics).
  3. Fold highest-seq overflow into lower-seq categories of the same role when
     `movers <= aggregate_free_vs_SOFT_LIMIT - CONSOLIDATE_MARGIN` (margin 4, fixed).
     Fill targets in seq order up to CATEGORY_SOFT_LIMIT; explicit positions; NEVER
     rename anything (loose seq — renames hit Discord's 2/10min bucket).

Phase B — un-split (`_maybe_unsplit`, same lock hold):
- Precondition: exactly one row per role (seq=0 each) and
  `count(unsolved) + count(solved) + 1 <= CATEGORY_SOFT_LIMIT - CONSOLIDATE_MARGIN`.
- Order (resumable, rows deleted LAST): create divider at bottom of the unsolved
  category (`get_or_create_channel(..., channel_name=SOLVE_DIVIDER, channel_type="text",
  position=500)`) → bounded wait for the divider to appear in the guild cache (moves
  compute positions from cache; without it a bulk renumber would land solved channels
  above the divider) → move solved text channels below it with explicit positions →
  delete the solved category → `dao.delete_for_group(owner_id)`.
- On cache-wait timeout: abort, leave split state (a divider in a split category is
  inert — puzzle_count excludes it, split movers ignore it). Next trigger retries.

Phase C — triggers:
- `maybe_consolidate(guild, owner_id, dao)`: cheap unlocked candidate pre-check, then
  `asyncio.create_task(consolidate(...))` (eager detection, background execution).
- `maybe_consolidate_category(guild, category)`: resolve owning table across BOTH DAOs
  (like cleanup_source_category), then schedule for that owner. Supersedes the
  cleanup_source_category call sites.
- Hook sites: cog `_apply_solved_move`/`_apply_unsolved_move` (after a move with a
  source), both `move_channel_to_owner` branches, `delete_puzzle`.

## Out of scope

- Periodic sweep for out-of-band Discord deletions (deferred).
- seq renumbering / category renames (explicitly rejected — rate limits).
- create-path capacity race, divider-two-locks, gateway-lag races (deferred minors).

## Relevant files

- bot/core/category_layout.py (engine + all new code)
- bot/core/discord_channels.py (get_or_create_channel, SOLVE_DIVIDER)
- bot/cogs/puzzles.py (_apply_*_move triggers)
- bot/core/puzzle_service.py (delete_puzzle trigger)
- tests/test_category_layout.py, tests/test_create_service.py, tests/test_solve_equivalence.py

## Constraints

- TDD per fix (this repo runs `pipenv run python -m pytest`, tests patch DAOs; module
  layout_lock needs a fresh asyncio.Lock per test — autouse fixture already in
  test_category_layout.py, plus autouse bot.store stubs `_no_store_claims`).
- All state re-read INSIDE layout_lock (established pattern).
- seq=0 rows die only in un-split/teardown; folds touch seq>=1 only.
- `dao.delete(row.id)` after the Discord delete (crash leaves an orphan row, which is
  tolerated + now reaped).
- Empty overflow reclaim stays unconditional (no margin gate) — matches shipped
  cleanup_empty_overflow behaviour.

## Acceptance criteria

- Fold moves channels with explicit positions, deletes category + row, no renames.
- Fold blocked when margin insufficient; empty categories always reclaimed.
- Unsolved folds processed before solved.
- Un-split produces single category with divider above the solved block; rows gone.
- Un-split refuses when overflow rows remain or total exceeds SOFT-4.
- Triggers schedule background consolidation; solve/web paths not blocked.
- Full suite green (`timeout 300 pipenv run python -m pytest tests/ -q`, ~950 tests).
