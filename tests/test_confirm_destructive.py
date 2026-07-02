"""_confirm_destructive: archive callers get accurate (non-'sheets deleted') copy."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from tests.conftest import make_ctx


async def test_archive_confirm_omits_sheets_warning():
    cog = Puzzles.__new__(Puzzles)
    ctx = make_ctx()
    with patch("bot.cogs.puzzles.ConfirmView") as CV:
        view = CV.return_value
        view.value = True
        view.wait = AsyncMock()
        result = await cog._confirm_destructive(ctx, "Archive hunt H?", sheets_deleted=False)
    assert result is True
    embed = ctx.send.await_args.kwargs["embed"]
    assert embed.title == "⚠️ Confirm archive"
    assert "Google sheets will be permanently deleted" not in embed.description


async def test_delete_confirm_keeps_sheets_warning_by_default():
    cog = Puzzles.__new__(Puzzles)
    ctx = make_ctx()
    with patch("bot.cogs.puzzles.ConfirmView") as CV:
        view = CV.return_value
        view.value = False
        view.wait = AsyncMock()
        result = await cog._confirm_destructive(ctx, "Delete puzzle X?")
    assert result is False
    embed = ctx.send.await_args.kwargs["embed"]
    assert embed.title == "⚠️ Confirm deletion"
    assert "Google sheets will be permanently deleted" in embed.description
