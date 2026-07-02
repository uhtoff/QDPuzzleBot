# Delete round: fix count, add "keep puzzles" scope

## Context

Reviewed in `docs/reviews/2026-07-02-staging-full-review.md`. The web dashboard's
delete-round flow (`BoardGroup.tsx` → `DELETE /api/tag_groups/<id>` →
`puzzle_service.delete_round`) has three issues to address:

1. The confirm-modal warning undercounts by one when the round has a target
   puzzle (`num_feeders` excludes the target — `puzzle_service.py:791`,
   `bot/web/data.py:451`).
2. `delete_round` is all-or-nothing: it always deletes every member puzzle.
   There's no way to tear down just the round's Discord footprint (category,
   optionally a target puzzle) while keeping the feeder puzzles' DB rows,
   sheets, and channels intact.
3. When a round IS deleted, any puzzle whose Discord channel physically lives
   in that round's category needs somewhere to go if it isn't itself being
   deleted — currently there's no such path since delete always removes
   every member.

Scope: channel/category-layout rounds only. Forum-layout rounds keep today's
behavior (delete the forum channel, cascading its posts) — forum layout is
already being left to go stale in this project (see project memory), and
"move a channel above/below the solved divider" has no forum equivalent.

## Behavior change

### 1. Count fix (frontend only)

`BoardGroup.tsx`'s warning currently reads `group.num_feeders`. Switch to
`group.puzzles.length`, which `build_board` already populates with every
puzzle tagged to the group (target included, per `bot/web/data.py:443-451`).
No backend change needed for this part.

### 2. New delete scope: `keep_puzzles` (default `true`)

Two modes, chosen via a radio in the confirm modal:

- **Round only** (default): delete the tag_group row, its category (+
  solved/overflow categories and divider, same teardown as today), and:
  - if the round has a target puzzle (`tag_group.target_puzzle_id`), delete
    that one puzzle (sheet + DB row + channel) — same treatment
    `delete_puzzle` gives an ordinary puzzle, but bypassing its
    "can't delete a target" guard since this is the sanctioned path for it;
  - if the round has no target (a `Group`-type round, whose category holds a
    plain chat channel instead of a target puzzle), delete that chat channel
    only — no DB row exists for it.
  - Every other member puzzle keeps its DB row, sheet, and Discord channel.
    The tag_group delete already strips their `tags` rows
    (`MySQLTagGroupJsonDb.delete`), so they cleanly fall out of the round.
- **Round + all puzzles**: today's existing behavior, unchanged — deletes
  every member puzzle (sheet + DB + channel) plus the category and group row.

### 3. Relocating kept puzzles' channels

In "Round only" mode, before the category (or categories, for a split round)
is deleted, find every channel among them that belongs to a puzzle being
kept (i.e. every channel that isn't the target/general channel already
scheduled for deletion), and move it into `hunt.category_id` (the general
hunt category), positioned relative to that category's solved divider using
the same rule as `tag_edge_only`'s channel-move
(`puzzle_service.py:730-748`): unsolved puzzles go just above the divider,
solved puzzles go after it. If the general category has no divider (legacy
single-category hunt layout) or is full (`MAX_CHANNELS_PER_CATEGORY`), the
move is skipped for that puzzle and it's reported back the same way
`failed_channels` is today, so the caller can see what still needs manual
attention — the puzzle's DB row is untouched either way, only its channel
placement may be left stale (orphaned top-level channel, since Discord does
not cascade-delete children when a category is deleted).

For a split round (`tag_group_category` rows present), gather member
channels across ALL of the round's categories (unsolved + solved +
overflow), not just the primary one.

## API / data flow changes

- `DELETE /api/tag_groups/<id>` (`bot/web/actions.py`) gains a JSON body
  param `keep_puzzles: bool`, defaulting to `true` server-side if omitted
  (matches the new default), passed through to `service.delete_round`.
- `puzzle_service.delete_round(guild, hunt, tag_group, keep_puzzles=True)`
  gains the `keep_puzzles` parameter and the branching described above.
  Return shape stays `failed_channels: list[str]` (now covering both
  "could not delete this channel" and "could not relocate this channel"
  cases — same tolerance model as today, just a superset of causes).
- The bang command (`!delete_round`, `bot/cogs/puzzles.py:3054`) is NOT
  changed by this work — it keeps calling `delete_round` with its current
  implicit "delete everything" intent, so it must pass
  `keep_puzzles=False` explicitly to preserve today's bang behavior.

## Frontend changes

- `useDeleteRound` (`frontend/src/api.ts`) takes a `keepPuzzles: boolean`
  arg and sends it in the DELETE body.
- `BoardGroup.tsx`'s confirm modal:
  - Fixes the count (`group.puzzles.length`).
  - Adds a `Radio.Group` with two options, "Round only (keep puzzles)"
    (checked by default) and "Round + all N puzzles". Warning text updates
    to describe the selected mode's actual effect (target/general-channel
    deletion + relocation for the first, full deletion for the second).
  - `runDeleteRound` passes the selected mode through to the mutation.

## Out of scope

- No changes to sheets/rollups beyond what already happens when a tag_group
  row is deleted.
- No changes to forum-layout round deletion.
- No changes to `!delete_round` bang UX (only its call site's explicit
  `keep_puzzles=False` argument, to keep behavior identical).
- No change to `delete_hunt` or `delete_puzzle`.
