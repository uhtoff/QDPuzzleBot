"""Tests for the shared per-puzzle async mutex (M4).

The same registry is used by the Discord bang-commands (via the with_puzzle_mutex
decorator) and the web dashboard, so a write to a puzzle serializes regardless of
which surface initiated it.
"""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_same_puzzle_serializes_no_interleave():
    """Two tasks taking the same puzzle's lock must not interleave their critical
    sections — the second waits for the first to release."""
    from bot.core.locks import puzzle_lock

    order = []

    async def worker(tag, hold):
        async with puzzle_lock(1):
            order.append(f"{tag}-start")
            await asyncio.sleep(hold)
            order.append(f"{tag}-end")

    await asyncio.gather(worker("A", 0.02), worker("B", 0.0))

    # Whichever ran first, its start and end are adjacent (no interleaving).
    assert order[0].split("-")[0] == order[1].split("-")[0]
    assert order[2].split("-")[0] == order[3].split("-")[0]
    assert order[0].split("-")[0] != order[2].split("-")[0]


@pytest.mark.asyncio
async def test_distinct_puzzles_do_not_block():
    """A held lock on one puzzle does not block acquiring another puzzle's lock."""
    from bot.core.locks import puzzle_lock

    async with puzzle_lock(1):
        # Acquiring a different puzzle's lock must succeed immediately.
        acquired = False
        async with puzzle_lock(2):
            acquired = True
        assert acquired is True


def test_locks_are_distinct_per_id():
    from bot.core.locks import _PUZZLE_LOCKS

    assert _PUZZLE_LOCKS[1] is not _PUZZLE_LOCKS[2]
    assert _PUZZLE_LOCKS[1] is _PUZZLE_LOCKS[1]


def test_bang_decorator_and_core_share_one_registry():
    """The Discord decorator and the web edge MUST use the same lock objects, or
    they wouldn't serialize against each other."""
    import bot.cogs.puzzles as puzzles_mod
    from bot.core import locks

    assert puzzles_mod._PUZZLE_LOCKS is locks._PUZZLE_LOCKS
