"""Web edge must refresh the parent minimeta's pinned mini-list on mini
create/solve/unsolve — mirroring the bang !solve_mini path's info(update=True).
Unit-tests the shared _refresh_parent_minimeta_pin helper in isolation
(full-route invocation needs app context; the helper holds the new branching)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.web import actions


@pytest.mark.asyncio
async def test_refreshes_parent_pin_for_a_mini():
    cog = MagicMock()
    cog.web_refresh_info = AsyncMock()
    guild, hunt = object(), object()
    parent = SimpleNamespace(id=5, name="Cluster")
    mini = SimpleNamespace(parent_puzzle_id=5)

    with patch("bot.store.PuzzleJsonDb") as PJ:
        PJ.get_by_attr.return_value = parent
        await actions._refresh_parent_minimeta_pin(cog, guild, hunt, mini)

    PJ.get_by_attr.assert_called_once_with(id=5)
    cog.web_refresh_info.assert_awaited_once_with(guild, hunt, parent)


@pytest.mark.asyncio
async def test_noop_for_non_mini():
    cog = MagicMock()
    cog.web_refresh_info = AsyncMock()
    puzzle = SimpleNamespace(parent_puzzle_id=None)

    await actions._refresh_parent_minimeta_pin(cog, object(), object(), puzzle)

    cog.web_refresh_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_noop_when_cog_unavailable():
    puzzle = SimpleNamespace(parent_puzzle_id=5)
    # Must not raise when the bot cog isn't loaded.
    await actions._refresh_parent_minimeta_pin(None, object(), object(), puzzle)


@pytest.mark.asyncio
async def test_noop_when_parent_missing():
    cog = MagicMock()
    cog.web_refresh_info = AsyncMock()
    mini = SimpleNamespace(parent_puzzle_id=5)

    with patch("bot.store.PuzzleJsonDb") as PJ:
        PJ.get_by_attr.return_value = None
        await actions._refresh_parent_minimeta_pin(cog, object(), object(), mini)

    cog.web_refresh_info.assert_not_awaited()
