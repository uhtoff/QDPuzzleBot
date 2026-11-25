"""PuzzleService.safe_rename: a channel rename must not hold its caller while
Discord's rename limit (two per channel per ten minutes) is waited out."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import bot.core.puzzle_service as ps
from bot.core.puzzle_service import PuzzleService


def _svc():
    return PuzzleService(MagicMock(), notifier=AsyncMock())


def _channel(edit):
    ch = MagicMock()
    ch.id = 50
    ch.edit = edit
    return ch


async def test_a_prompt_rename_is_applied_and_reported():
    svc, edit = _svc(), AsyncMock()
    assert await svc.safe_rename(_channel(edit), "new") is True
    edit.assert_awaited_once_with(name="new")
    assert svc._pending_renames == {}


async def test_a_rate_limited_rename_returns_and_finishes_in_the_background():
    """discord.py sleeps on a spent bucket instead of raising. The caller gets
    False within the wait, and the name still lands once the bucket frees."""
    svc = _svc()
    release, names = asyncio.Event(), []

    async def edit(*, name):
        await release.wait()
        names.append(name)
    ch = _channel(edit)
    ctx = MagicMock(); ctx.send = AsyncMock()
    with patch.object(ps, "RENAME_WAIT_SECONDS", 0.01):
        assert await svc.safe_rename(ch, "first", ctx=ctx) is False
        assert names == []                       # nothing applied yet
        ctx.send.assert_awaited_once()
        # A second rename while one is waiting only replaces the pending name:
        # it must not start another edit or another background task.
        assert await svc.safe_rename(ch, "second") is False
    assert len(svc._rename_tasks) == 1
    release.set()
    await asyncio.gather(*svc._rename_tasks)
    assert names == ["second"]                   # the latest name, applied once
    assert svc._pending_renames == {}


def test_needs_rename_compares_against_a_queued_name():
    """A rename waiting out Discord's limit is where the channel is headed, so a
    rename back to the name Discord still shows is NOT a no-op."""
    svc = _svc()
    ch = _channel(AsyncMock())
    ch.name = "alpha"
    assert svc.needs_rename(ch, "alpha") is False         # nothing queued: Discord's name
    assert svc.needs_rename(ch, "beta") is True
    svc._pending_renames[ch.id] = "beta"                  # alpha -> beta is queued
    assert svc.needs_rename(ch, "alpha") is True          # back to alpha must replace it
    assert svc.needs_rename(ch, "beta") is False


async def test_renaming_back_while_queued_ends_on_the_latest_name():
    """alpha -> beta is rate-limited and queued; the user renames back to alpha.
    Discord must end on alpha, not on the stale queued beta."""
    svc = _svc()
    release, names = asyncio.Event(), []

    async def edit(*, name):
        await release.wait()
        names.append(name)
    ch = _channel(edit)
    ch.name = "alpha"
    with patch.object(ps, "RENAME_WAIT_SECONDS", 0.01):
        assert await svc.safe_rename(ch, "beta") is False
        if svc.needs_rename(ch, "alpha"):                  # what each caller checks
            await svc.safe_rename(ch, "alpha")
    release.set()
    await asyncio.gather(*svc._rename_tasks)
    assert names[-1] == "alpha"
    assert svc._pending_renames == {}
