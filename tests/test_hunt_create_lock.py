"""Tests for the per-hunt creation mutex.

Puzzle/group creation within a hunt must serialize so a double-submit (or two
concurrent creators) can't both pass the duplicate-name check and each create a
same-named row. The registry is separate from the per-puzzle/per-round locks so a
hunt id sharing an integer value with a puzzle or round id doesn't collide.
"""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_check_then_create_is_atomic_under_concurrency():
    """The whole point: without the lock, two concurrent check-then-create runs
    both see "not present" and both create. With it, exactly one creates."""
    from bot.core.locks import hunt_create_lock

    created: list[str] = []

    async def create_fast():
        async with hunt_create_lock(7):
            if "Fast" in created:        # the duplicate-name check
                return "duplicate"
            await asyncio.sleep(0.01)     # the slow create window (sheet/channel)
            created.append("Fast")
            return "ok"

    r1, r2 = await asyncio.gather(create_fast(), create_fast())

    assert sorted([r1, r2]) == ["duplicate", "ok"]  # exactly one won
    assert created == ["Fast"]                       # only one row created


@pytest.mark.asyncio
async def test_same_hunt_serializes_no_interleave():
    from bot.core.locks import hunt_create_lock

    order = []

    async def worker(tag, hold):
        async with hunt_create_lock(5):
            order.append(f"{tag}-start")
            await asyncio.sleep(hold)
            order.append(f"{tag}-end")

    await asyncio.gather(worker("A", 0.02), worker("B", 0.0))

    assert order[0].split("-")[0] == order[1].split("-")[0]  # A fully before B (or vice-versa)
    assert order[2].split("-")[0] == order[3].split("-")[0]
    assert order[0].split("-")[0] != order[2].split("-")[0]


@pytest.mark.asyncio
async def test_distinct_hunts_do_not_block():
    from bot.core.locks import hunt_create_lock

    async with hunt_create_lock(1):
        acquired = False
        async with hunt_create_lock(2):
            acquired = True
        assert acquired is True


def test_hunt_create_registry_is_separate():
    """A hunt id must not share a lock with a puzzle or round id of equal value."""
    from bot.core.locks import _HUNT_CREATE_LOCKS, _PUZZLE_LOCKS, _ROLLUP_LOCKS

    assert _HUNT_CREATE_LOCKS[5] is not _PUZZLE_LOCKS[5]
    assert _HUNT_CREATE_LOCKS[5] is not _ROLLUP_LOCKS[5]
