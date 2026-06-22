"""Tests for the per-round roll-up mutex (M5).

Only one writer may touch a given meta round's roll-up sheet at a time, but
distinct rounds refresh in parallel. The registry is separate from the per-puzzle
locks so a puzzle id and a round id with the same integer value don't collide.
"""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_same_round_serializes_no_interleave():
    from bot.core.locks import rollup_lock

    order = []

    async def worker(tag, hold):
        async with rollup_lock(5):
            order.append(f"{tag}-start")
            await asyncio.sleep(hold)
            order.append(f"{tag}-end")

    await asyncio.gather(worker("A", 0.02), worker("B", 0.0))

    assert order[0].split("-")[0] == order[1].split("-")[0]
    assert order[2].split("-")[0] == order[3].split("-")[0]
    assert order[0].split("-")[0] != order[2].split("-")[0]


@pytest.mark.asyncio
async def test_distinct_rounds_do_not_block():
    from bot.core.locks import rollup_lock

    async with rollup_lock(1):
        acquired = False
        async with rollup_lock(2):
            acquired = True
        assert acquired is True


def test_rollup_and_puzzle_registries_are_separate():
    """A round id and a puzzle id with the same integer must NOT share a lock."""
    from bot.core.locks import _PUZZLE_LOCKS, _ROLLUP_LOCKS

    assert _ROLLUP_LOCKS[5] is not _PUZZLE_LOCKS[5]
