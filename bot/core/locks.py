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


@asynccontextmanager
async def puzzle_lock(puzzle_id):
    """Hold the per-puzzle lock for the duration of the block, waiting if another
    holder (bang-command or web action) currently has it."""
    async with _PUZZLE_LOCKS[puzzle_id]:
        yield
