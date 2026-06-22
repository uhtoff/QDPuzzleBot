"""Per-puzzle async mutex shared across all surfaces (M4).

Discord bang-commands (via the with_puzzle_mutex decorator in bot/cogs/puzzles.py)
and the web dashboard (bot/web/actions.py) both serialize per-puzzle writes through
this single registry, so a dashboard solve/retag can no longer interleave with a
bang-command on the same puzzle and lose updates.

NOTE: asyncio.Lock is NOT reentrant. Acquire a given puzzle's lock at most once per
task. Locking lives at the edges (the bang decorator and the web handlers); the
ctx-free PuzzleService deliberately does NOT lock, so an edge that already holds the
lock can call the service without deadlocking.
"""
import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager

# One asyncio.Lock per puzzle id.
_PUZZLE_LOCKS = defaultdict(asyncio.Lock)

# One asyncio.Lock per ROUND id, guarding writes to that round's roll-up sheet (M5).
# Kept in a SEPARATE registry from _PUZZLE_LOCKS: a puzzle id and a round id are
# both plain ints from different tables, so a shared registry would make
# "puzzle 5" and "round 5" collide on one lock.
_ROLLUP_LOCKS = defaultdict(asyncio.Lock)


@asynccontextmanager
async def puzzle_lock(puzzle_id):
    """Hold the per-puzzle lock for the duration of the block, waiting if another
    holder (bang-command or web action) currently has it."""
    async with _PUZZLE_LOCKS[puzzle_id]:
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
