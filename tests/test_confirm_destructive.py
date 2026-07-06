"""_confirm_destructive: archive callers get accurate (non-'sheets deleted') copy."""
from unittest.mock import AsyncMock, patch


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


async def test_confirm_destructive_passes_confirm_label_to_view():
    cog = Puzzles.__new__(Puzzles)
    ctx = make_ctx()
    with patch("bot.cogs.puzzles.ConfirmView") as CV:
        view = CV.return_value
        view.value = True
        view.wait = AsyncMock()
        await cog._confirm_destructive(
            ctx, "Consolidate meta into round?",
            sheets_deleted=False, confirm_label="Confirm consolidate")
    assert CV.call_args.kwargs.get("confirm_label") == "Confirm consolidate"


async def test_confirm_destructive_defaults_confirm_label_to_delete():
    cog = Puzzles.__new__(Puzzles)
    ctx = make_ctx()
    with patch("bot.cogs.puzzles.ConfirmView") as CV:
        view = CV.return_value
        view.value = True
        view.wait = AsyncMock()
        await cog._confirm_destructive(ctx, "Delete puzzle X?")
    assert CV.call_args.kwargs.get("confirm_label") == "Confirm delete"


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
