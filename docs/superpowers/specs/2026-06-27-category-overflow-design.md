# Category Overflow — graceful handling of Discord's 50-channels-per-category limit

**Date:** 2026-06-27
**Status:** Design agreed; ready for implementation planning.

## Why

Discord caps a category at **50 channels**. Today a group (`tag_group`) owns exactly one
category (`TagGroupData.category_id`, `bot/store/tag_group_data.py:23`) and, when that
category fills, `create_puzzle()` simply **fails** with `RuntimeError("category_full")`
(`bot/core/puzzle_service.py:1374`). Large rounds therefore hit a hard wall.

This design lets a group **span multiple categories automatically**: as a round grows past a
soft limit it first splits its single divider-organised category into separate
**unsolved** and **solved** categories, then spills into **numbered overflow** categories on
either side as needed — with no manual intervention and no create failures.

## Current state (verified)

- **One group = one category.** `TagGroupData.category_id` is a single column.
- **Divider organisation.** Within a category a `———solved———` divider channel
  (`SOLVE_DIVIDER`, `bot/core/discord_channels.py:16`, created at position 500) separates
  unsolved (above) from solved (below). On solve, the channel moves to `divider+1`
  (`web_post_solved_embed`, `bot/cogs/puzzles.py:714`), serialised by `position_lock`
  (`bot/cogs/puzzles.py:109`).
- **Dormant solved-category path.** `SOLVE_CATEGORY` (`discord_channels.py:15`, currently
  `False`) and hunt-level `{name}-solved` category creation
  (`puzzle_service.py:1708-1715`) already exist but are unused. This design effectively turns
  that mode on **per-group, dynamically, once a group gets large** — small rounds keep the
  divider.
- **Limit checks today.** `MAX_CHANNELS_PER_CATEGORY = 50` in both
  `discord_channels.py:18` and `puzzles.py:70`; space check at
  `puzzle_service.py:1374` and `puzzles.py:380`.
- **Round deletion** deletes one category + divider (`puzzles.py:2563`).
- **Web board** column order (`bot/web/data.py:431`) is group-derived and independent of
  Discord categories — **unaffected** by this work.

## Decisions (agreed during brainstorming — do not re-litigate)

- **Data model:** a normalised **join table** (not a list column, not name-derived).
- **Trigger:** **proactive** at a soft limit below 50, so creates never fail mid-flight.
- **First split is semantic:** drop the divider and split into a **solved** and an
  **unsolved** category; only *then* use **numbered suffixes** for further overflow.
- **Migration of existing channels at split:** **rebalance** (move solved channels into the
  new solved category).
- **No auto-consolidation:** never reshuffle channels to merge sparse categories, and never
  revert split → single. **But** an *overflow* category that becomes genuinely **empty** is
  deleted (see Empty-overflow cleanup).
- **Solved-category name format:** `<name> — solved`.

## Data model

New join table (**migration 014** — next free; re-verify at plan time, parallel worktrees
can collide on the number):

```
tag_group_category
  group_id     bigint  -- FK -> tag_group.id
  category_id  bigint  -- Discord category snowflake
  role         enum/text  -- 'unsolved' | 'solved'
  seq          int        -- 0,1,2…  ordering within a role
  PRIMARY KEY (group_id, category_id)
```

- `TagGroupData.category_id` **stays** and continues to equal the `unsolved seq=0` ("primary")
  category — backward-compatible; existing readers keep working.
- The join table is the **source of truth** for the full category set of a group.
- A group's puzzles' actual placement is still read live from Discord (`channel.category`);
  the table records *which categories belong to the group and in what role*, for placement,
  space-checks, cleanup, and deletion.

## States

A group progresses (and never regresses):

1. **Single (default — today's behaviour).** One category, `unsolved seq=0`, with the
   divider. Solved channels live below the divider. The overwhelming majority of rounds stay
   here. *No backfill of the join table is required for existing groups* — a group with no
   `tag_group_category` rows is treated as Single with its `category_id`.
2. **Split.** Triggered when the Single category crosses the soft limit. Create the
   `solved seq=0` category (`<name> — solved`), **move all solved channels into it**, **delete
   the divider**, and write the two join rows (`unsolved seq=0`, `solved seq=0`).
3. **Overflow.** When the newest category of a role crosses the soft limit, allocate the next
   numbered category for that role (`seq=1,2…`, named `<name> (2)` / `<name> — solved (2)`).

Because the transitions cascade, a split that leaves the unsolved side still over the soft
limit (e.g. almost nothing was solved yet) simply proceeds to allocate `unsolved seq=1`
next — no special case.

## Soft limit

New constant `CATEGORY_SOFT_LIMIT = 45` (hard cap stays `MAX_CHANNELS_PER_CATEGORY = 50`).
Allocation/split triggers when a category **would cross** the soft limit, leaving headroom so
a create never fails. (Value chosen to leave ~5 slots; confirm at plan time.)

## Placement logic

A single seam — "which category should this puzzle's channel be in?" — drives create, solve,
and unsolve.

- **Create** (`create_puzzle`, `puzzle_service.py:1358`): target the **unsolved** side. Pick
  the unsolved category with room (lowest `seq` with space). If placing would cross the soft
  limit:
  - In **Single** state → run the **split transition** first, then place.
  - In **Split/Overflow** state → allocate the next `unsolved seq` then place.
- **Solve** (`web_post_solved_embed`, `puzzles.py:714`): becomes category-aware.
  - **Single** → divider reposition (unchanged).
  - **Split/Overflow** → **move** the channel into the **solved** category with room
    (allocating a numbered solved overflow if the active one is at the soft limit).
- **Unsolve** (`unsolve`, `puzzle_service.py:857`): mirror — move the channel back to an
  **unsolved** category with room.

All channel moves go through the existing `position_lock` and are **best-effort/tolerant** —
they must never raise into the solve/unsolve flow.

## Split / rebalance transition

A serialised, rate-limit-tolerant routine:

1. Create the `<name> — solved` category; write its join row.
2. Under `position_lock`, move solved channels into it **incrementally**.
3. Delete the divider channel from the unsolved category.
4. Ensure the `unsolved seq=0` join row exists.

Given prior rate-limit pain (the `max_ratelimit_timeout` incident — channel ops pre-empting
on bucket reset), moves are incremental and the routine is **idempotent/resumable**: a partial
run leaves the group consistent, and the next trigger finishes it. The divider is deleted
**only after** solved channels have moved, so an interrupted run never strands solved channels
above a missing divider.

## Empty-overflow cleanup

Triggered after any operation that can empty a category (solve-move, unsolve-move, puzzle
delete):

- **Delete** an *overflow* category (`seq ≥ 1`, either role) the moment it holds **zero puzzle
  channels**, and drop its join row.
- **Never** delete a **primary** category (`seq = 0`, unsolved or solved).
- **Never** revert Split → Single (no re-adding a divider) — that would be consolidation.

This removes the clutter of stale numbered categories without any channel reshuffling, staying
within the "no auto-consolidation" rule.

## Touch points to update

- **Round deletion** (`puzzles.py:2563`): delete **all** categories in the group's join table
  (plus the legacy `category_id`), not just one + divider; clear the join rows.
- **Space checks** (`puzzle_service.py:1374`, `puzzles.py:380`): consult the group's category
  **set** via the placement seam, not a single category. `create_puzzle` no longer raises
  `category_full` under normal growth (the soft-limit path allocates); keep a hard guard for
  the true ceiling (guild channel cap / max categories).
- **Guild-level ceilings** (`MAX_CHANNELS_PER_GUILD = 500`, `MAX_CATEGORIES = 50`,
  `puzzles.py:70-72`): unchanged, but overflow categories now consume from the
  `MAX_CATEGORIES` budget — surface a clear error if a hunt is near that ceiling.
- **Data access:** add a `tag_group_category` DAO alongside the existing
  `MySQLTagGroupJsonDb` (`bot/store/mysqldb.py:144`) following the same pattern.

## Scope

**In:**
- Migration 014: `tag_group_category` table.
- DAO + a placement/allocation module ("which category for create/solve/unsolve", soft-limit
  allocation, split transition, empty-overflow cleanup).
- Wire create/solve/unsolve, space-checks, and round-deletion through the seam.
- `CATEGORY_SOFT_LIMIT` constant.
- Tests (below).

**Out:**
- Backfilling existing groups into the join table (a group with no rows = Single; rows appear
  the first time it splits).
- Reverting split→single or any channel rebalancing beyond the one-time split move.
- Web board / dashboard changes (board is category-independent).
- Any change to hunt-level categories (only `tag_group` rounds are affected).

## Constraints

- **Behaviour-preserving for small rounds:** a group under the soft limit must look and behave
  exactly as today (single category + divider). No backfill, no new categories.
- **Tolerant Discord ops:** all moves/creates serialised via `position_lock`, best-effort,
  incremental, resumable; never raise into solve/unsolve.
- **Idempotent transitions:** re-running the split or cleanup on an already-correct group is a
  no-op.
- Keep `TagGroupData.category_id` valid as the primary unsolved category throughout.

## Testing

Discord API mocked as in existing tests.

- **State transitions:** Single → Split (divider removed, solved channels moved, rows
  written) → Overflow (numbered category allocated on each side).
- **Placement selection:** create targets lowest unsolved seq with room; solve targets solved
  side; unsolve returns to unsolved side; allocation fires exactly at the soft-limit boundary.
- **Split rebalance:** correct channels moved; resumable after a simulated mid-run failure
  (divider still present, solved channels not yet moved → next run completes).
- **Empty-overflow cleanup:** emptying a `seq ≥ 1` category deletes it + its row; emptying a
  `seq = 0` category does **not**; no split→single revert.
- **Deletion:** round delete removes all categories + all join rows.
- **Acceptance:** full suite green (`timeout 180 pipenv run python -m pytest tests/ -q`),
  `ruff check` exit 0.

## Verification

- A round grown past the soft limit shows separate `<name>` and `<name> — solved` categories,
  no divider, all channels reachable; further growth adds `<name> (2)` / `<name> — solved (2)`.
- Solving/unsolving moves channels between the correct categories; emptying an overflow
  category removes it.
- A small round is byte-for-byte unchanged (single category + divider).
- `SHOW TABLES LIKE 'tag_group_category'` exists; deleting a multi-category round leaves no
  orphan categories or rows.

## Rollback

- Revert the milestone's commits.
- Reverse migration 014 (drop `tag_group_category`). Groups that had split will retain their
  extra Discord categories, but with the new code gone they fall back to single-`category_id`
  behaviour; any stranded categories can be cleaned up manually. (Note this one-way risk at
  plan time.)
