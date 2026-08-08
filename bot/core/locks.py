"""Per-puzzle async mutex shared across all surfaces (M4).

Discord bang-commands (via the with_puzzle_mutex decorator in bot/cogs/puzzles.py)
and the web dashboard (bot/web/actions.py) both serialize per-puzzle writes through
this single registry, so a dashboard solve/retag can no longer interleave with a
bang-command on the same puzzle and lose updates.

NOTE: asyncio.Lock is NOT reentrant, and never times out. Acquire a given puzzle's
lock at most once per task. Locking lives at the edges (the bang decorator and the
web handlers); the ctx-free PuzzleService deliberately does NOT lock, so an edge that
already holds the lock can call the service without deadlocking.

There are three DELIBERATE exceptions inside PuzzleService, each safe only because of
who calls it. Adding a caller that already holds the relevant lock deadlocks the bot
permanently -- no error, no timeout, just a wedged event loop until restart:

  - refresh_meta takes rollup_lock, on the disjoint round-id key space (see below).
  - bulk_re_edge takes its member puzzles' locks internally; its callers must lock
    only the target, never a member.
  - link_informs_for_evidence / unlink_informs_for_evidence take
    puzzle_lock(source_puzzle_id) (M5). Safe ONLY because every caller is a bare
    asyncio.create_task from a lock-free web handler (bot/web/actions.py). Note the
    adjacent trap: !clue/!clues already call refresh_informs_targets from INSIDE
    with_puzzle_mutex, so routing a bang command through the link path instead would
    hang on the source puzzle's own lock.

The bang edge also re-reads under the lock (with_puzzle_mutex ->
Puzzles._refresh_environment), which takes no lock of its own.
"""
import asyncio
from collections import defaultdict
from contextlib import AsyncExitStack, asynccontextmanager

# One asyncio.Lock per puzzle id.
_PUZZLE_LOCKS = defaultdict(asyncio.Lock)

# One asyncio.Lock per ROUND id, guarding writes to that round's roll-up sheet (M5).
# Kept in a SEPARATE registry from _PUZZLE_LOCKS: a puzzle id and a round id are
# both plain ints from different tables, so a shared registry would make
# "puzzle 5" and "round 5" collide on one lock.
_ROLLUP_LOCKS = defaultdict(asyncio.Lock)

# One asyncio.Lock per HUNT id, serializing puzzle/group CREATION within a hunt so
# the duplicate-name check and the create it guards run as one critical section.
# Separate registry (hunt id is yet another int space) for the same reason as above.
_HUNT_CREATE_LOCKS = defaultdict(asyncio.Lock)

# One asyncio.Lock per HUNT id, serializing writes to that hunt's OWN working
# sheets. Needed because with_puzzle_mutex no-ops when there is no puzzle in
# context, so a hunt-level !add_sheet would otherwise race another on the Sheets
# API. Separate registry again (hunt id is a different int space from puzzle id),
# and separate from _HUNT_CREATE_LOCKS: that one guards puzzle/group creation, and
# reusing it would make adding a scratch sheet block someone creating a puzzle.
_HUNT_SHEET_LOCKS = defaultdict(asyncio.Lock)


@asynccontextmanager
async def puzzle_lock(puzzle_id):
    """Hold the per-puzzle lock for the duration of the block, waiting if another
    holder (bang-command or web action) currently has it."""
    async with _PUZZLE_LOCKS[puzzle_id]:
        yield


@asynccontextmanager
async def puzzles_lock(puzzle_ids):
    """Hold EVERY listed puzzle's lock for the duration of the block (M10, #45):
    round/hunt teardown needs to serialize against a concurrent solve/retag on ANY
    of its member puzzles, not just one. Acquired in sorted order so two
    overlapping multi-puzzle holders (e.g. two round deletions sharing a puzzle)
    can't deadlock each other; a single-puzzle holder (puzzle_lock/
    with_puzzle_mutex) never blocks waiting on a second lock, so it can't join a
    cycle either. Waits if any lock is currently held; de-dupes ids so a puzzle
    listed twice is only locked once (asyncio.Lock is not reentrant)."""
    async with AsyncExitStack() as stack:
        for puzzle_id in sorted(set(puzzle_ids)):
            await stack.enter_async_context(_PUZZLE_LOCKS[puzzle_id])
        yield


@asynccontextmanager
async def hunt_create_lock(hunt_id):
    """Serialize puzzle/group creation within a hunt so the duplicate-name check
    and the create it guards form one atomic critical section — a double-submit
    (or two concurrent creators) can't both pass the check and each create a row.

    Held at the edges (web handlers) around check+create; the lock-free
    PuzzleService it calls never re-acquires it. create_puzzle/create_tag_group
    take deeper locks (category_layout.layout_lock, rollup_lock) only AFTER this
    one and never the reverse, so there is no lock-ordering cycle."""
    async with _HUNT_CREATE_LOCKS[hunt_id]:
        yield


@asynccontextmanager
async def hunt_sheet_lock(hunt_id):
    """Serialize writes to a hunt's own working sheets (add/remove), so two
    people running !add_sheet in the hunt channel at once cannot race on the
    Sheets API.

    Held at the edge (the cog decorator), like the puzzle lock, so the lock-free
    PuzzleService it calls never re-acquires it. Never held at the same time as a
    puzzle lock: a sheet command has either a puzzle or a hunt in context, never
    both, so there is no lock-ordering cycle to reason about.
    """
    async with _HUNT_SHEET_LOCKS[hunt_id]:
        yield


@asynccontextmanager
async def rollup_lock(round_id):
    """Hold the per-round roll-up lock so only one writer touches a given meta
    sheet at a time, waiting if another holder has it.

    Unlike the per-puzzle lock (held at the edges so the ctx-free PuzzleService can
    stay lock-free and non-reentrant), this lock is taken INSIDE
    PuzzleService.refresh_meta — the single choke point every meta-sheet write
    (solve, retag, !rebuild, create) routes through. That is deadlock-safe because
    the fan-out callers refresh metas one at a time, so at most one rollup lock is
    held per task, on a key space (round id) disjoint from the puzzle lock's. The
    lock wraps the DB read as well as the render, so each holder renders
    post-commit state (no lost update against a concurrent rebuild)."""
    async with _ROLLUP_LOCKS[round_id]:
        yield
