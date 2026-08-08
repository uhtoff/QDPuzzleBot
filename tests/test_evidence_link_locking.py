"""M5: the background evidence link/unlink paths hold the source puzzle's lock.

They are launched as bare create_task from the web layer, so without the lock they
commit concurrently with a locked bang command, and the bidirectional tag-sync in
PuzzleJsonDb.commit can silently revert the informs edge that command just wrote.

The stores are patched to return None so the methods return as soon as they get
past the lock: the behaviour under test is purely "does it wait", and resolving a
real group/source would only add ways for the test to fail for other reasons.
Patch targets are the names as looked up in bot.core.puzzle_service, not where they
are defined -- patching the source module would leave the service's own reference
untouched and the mock would never engage.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from bot.core.locks import _PUZZLE_LOCKS
from bot.core.puzzle_service import PuzzleService

SOURCE_ID = 424242
TARGET_ID = 424243


def _service():
    return PuzzleService(MagicMock(), notifier=MagicMock())


async def _assert_blocks_until_released(coro_factory):
    """Launch the coroutine while the source lock is held; it must not progress."""
    async with _PUZZLE_LOCKS[SOURCE_ID]:
        service_call = asyncio.create_task(coro_factory())
        # Two yields, not one: a single yield may not be enough for the task to
        # reach its first real await, which would make `not done` true for the
        # wrong reason.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not service_call.done(), (
            "the background link ran while the source puzzle's lock was held"
        )

    await asyncio.wait_for(service_call, timeout=1)


@pytest.mark.asyncio
async def test_link_informs_waits_for_the_source_puzzle_lock():
    service = _service()
    hunt = MagicMock()
    try:
        with patch("bot.core.puzzle_service.TagGroupJsonDb") as GroupDb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb") as PuzzleDb:
            GroupDb.get_by_attr.return_value = None
            PuzzleDb.get_by_attr.return_value = None
            await _assert_blocks_until_released(
                lambda: service.link_informs_for_evidence(hunt, SOURCE_ID, TARGET_ID)
            )
    finally:
        _PUZZLE_LOCKS.pop(SOURCE_ID, None)


@pytest.mark.asyncio
async def test_unlink_informs_waits_for_the_source_puzzle_lock():
    service = _service()
    hunt = MagicMock()
    try:
        with patch("bot.core.puzzle_service.TagGroupJsonDb") as GroupDb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb") as PuzzleDb, \
             patch("bot.core.puzzle_service.EvidenceJsonDb") as EvidenceDb:
            EvidenceDb.get_all.return_value = []
            GroupDb.get_by_attr.return_value = None
            PuzzleDb.get_by_attr.return_value = None
            await _assert_blocks_until_released(
                lambda: service.unlink_informs_for_evidence(hunt, SOURCE_ID, TARGET_ID)
            )
    finally:
        _PUZZLE_LOCKS.pop(SOURCE_ID, None)


@pytest.mark.asyncio
async def test_a_sourceless_evidence_item_returns_without_locking():
    """source_puzzle_id is None for hunt-level evidence -- there is no puzzle to
    lock on, so the early return must stay OUTSIDE the lock."""
    service = _service()
    await asyncio.wait_for(
        service.link_informs_for_evidence(MagicMock(), None, TARGET_ID), timeout=1
    )
    assert None not in _PUZZLE_LOCKS, "a lock was created for a sourceless item"
