"""Unit tests for the reusable destructive-action confirmation View (M10)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.cogs.ui import ConfirmView


def _interaction(user_id):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
        ),
    )


@pytest.mark.asyncio
async def test_interaction_check_blocks_non_author():
    view = ConfirmView(author_id=42)
    interaction = _interaction(user_id=99)
    allowed = await view.interaction_check(interaction)
    assert allowed is False
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_interaction_check_allows_author():
    view = ConfirmView(author_id=42)
    assert await view.interaction_check(_interaction(user_id=42)) is True


@pytest.mark.asyncio
async def test_confirm_sets_true_and_stops():
    view = ConfirmView(author_id=42)
    interaction = _interaction(user_id=42)
    await view.confirm.callback(interaction)
    assert view.value is True
    assert all(child.disabled for child in view.children)
    assert view.is_finished()
    interaction.response.edit_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_sets_false_and_stops():
    view = ConfirmView(author_id=42)
    interaction = _interaction(user_id=42)
    await view.cancel.callback(interaction)
    assert view.value is False
    assert view.is_finished()


@pytest.mark.asyncio
async def test_timeout_leaves_value_none():
    view = ConfirmView(author_id=42)
    view.message = SimpleNamespace(edit=AsyncMock())
    await view.on_timeout()
    assert view.value is None
    view.message.edit.assert_awaited_once()
