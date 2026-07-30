"""!media_stats / !media_sweep — the bang edge over bot/core/media_gc.

The service itself is covered by test_media_gc.py; these pin the command
contract: report-only mutates nothing, the sweep is gated behind the shared
destructive confirm, and both are organiser-checked.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from tests.conftest import make_cog, make_ctx, make_hunt


def _orphans(*sizes):
    return [SimpleNamespace(id=i, size=s) for i, s in enumerate(sizes, start=1)]


def _cog_and_ctx():
    return make_cog(None, make_hunt(id=10)), make_ctx()


def _gc(orphans=(), swept=None):
    """A stubbed media_gc as the cog module sees it (it does `from bot.core
    import media_gc`, so the cog's own name is what has to be patched)."""
    gc = MagicMock()
    gc.orphaned_media.return_value = list(orphans)
    gc.sweep.return_value = swept or SimpleNamespace(count=0, bytes_freed=0)
    gc.format_size.side_effect = lambda n: f"{n} bytes"
    return gc


class TestMediaStats:

    @pytest.mark.asyncio
    async def test_reports_count_and_size_without_sweeping(self):
        cog, ctx = _cog_and_ctx()
        gc = _gc(orphans=_orphans(100, 25))

        with patch("bot.cogs.puzzles.media_gc", gc):
            await Puzzles.media_stats.callback(cog, ctx)

        gc.orphaned_media.assert_called_once_with(10)
        gc.sweep.assert_not_called()
        msg = ctx.send.call_args[0][0]
        assert "2 orphaned images" in msg
        # Summed, not just the last row's size: two rows is what makes that
        # distinguishable.
        assert "125 bytes" in msg

    @pytest.mark.asyncio
    async def test_reports_nothing_to_do_when_clean(self):
        cog, ctx = _cog_and_ctx()

        with patch("bot.cogs.puzzles.media_gc", _gc()):
            await Puzzles.media_stats.callback(cog, ctx)

        assert "No orphaned images" in ctx.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_refuses_outside_a_hunt_channel(self):
        cog = make_cog(None, None)
        cog.get_hunt = lambda ctx: None
        ctx = make_ctx()
        gc = _gc(orphans=_orphans(1))

        with patch("bot.cogs.puzzles.media_gc", gc):
            await Puzzles.media_stats.callback(cog, ctx)

        gc.orphaned_media.assert_not_called()
        assert ":x:" in ctx.send.call_args[0][0]


class TestMediaSweep:

    @pytest.mark.asyncio
    async def test_confirmed_sweep_deletes_and_reports(self):
        cog, ctx = _cog_and_ctx()
        cog._confirm_destructive = AsyncMock(return_value=True)
        gc = _gc(orphans=_orphans(100),
                 swept=SimpleNamespace(count=1, bytes_freed=100))

        with patch("bot.cogs.puzzles.media_gc", gc):
            await Puzzles.media_sweep.callback(cog, ctx)

        gc.sweep.assert_called_once_with(10)
        assert "Swept 1 image" in ctx.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cancelled_sweep_deletes_nothing(self):
        cog, ctx = _cog_and_ctx()
        cog._confirm_destructive = AsyncMock(return_value=False)
        gc = _gc(orphans=_orphans(100))

        with patch("bot.cogs.puzzles.media_gc", gc):
            await Puzzles.media_sweep.callback(cog, ctx)

        gc.sweep.assert_not_called()
        assert "cancelled" in ctx.send.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_clean_hunt_is_not_asked_to_confirm(self):
        cog, ctx = _cog_and_ctx()
        cog._confirm_destructive = AsyncMock(return_value=True)
        gc = _gc()

        with patch("bot.cogs.puzzles.media_gc", gc):
            await Puzzles.media_sweep.callback(cog, ctx)

        cog._confirm_destructive.assert_not_awaited()
        gc.sweep.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirm_prompt_names_the_number_at_risk(self):
        """The count is the only thing standing between the organiser and a
        bulk delete, so it has to reach the prompt, not just the report."""
        cog, ctx = _cog_and_ctx()
        cog._confirm_destructive = AsyncMock(return_value=False)

        with patch("bot.cogs.puzzles.media_gc", _gc(orphans=_orphans(1, 2, 3))):
            await Puzzles.media_sweep.callback(cog, ctx)

        assert "3 image(s)" in cog._confirm_destructive.call_args[0][1]

    @pytest.mark.asyncio
    async def test_refuses_outside_a_hunt_channel(self):
        cog = make_cog(None, None)
        cog.get_hunt = lambda ctx: None
        ctx = make_ctx()
        gc = _gc(orphans=_orphans(1))

        with patch("bot.cogs.puzzles.media_gc", gc):
            await Puzzles.media_sweep.callback(cog, ctx)

        gc.sweep.assert_not_called()
        assert ":x:" in ctx.send.call_args[0][0]


class TestGating:

    @pytest.mark.parametrize("command", ["media_stats", "media_sweep"])
    def test_both_commands_are_organiser_gated(self, command):
        """Neither callback checks permissions itself — the decorator is the
        whole gate, so its absence would be silent."""
        checks = getattr(Puzzles, command).checks
        assert checks, f"!{command} has no permission check"


class TestReportWording:

    def test_singular_and_plural(self):
        with patch("bot.cogs.puzzles.media_gc", _gc()):
            assert "1 orphaned image," in Puzzles._format_media_report(1, 10)
            assert "2 orphaned images," in Puzzles._format_media_report(2, 10)

    def test_size_is_reported_as_a_floor(self):
        """bytes_freed omits tile pyramids, so the copy must not read as exact."""
        with patch("bot.cogs.puzzles.media_gc", _gc()):
            assert "at least" in Puzzles._format_media_report(1, 10)
