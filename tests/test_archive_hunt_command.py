"""!archive_hunt cog command: confirm gate + delegation to service.archive_hunt."""
from unittest.mock import AsyncMock, MagicMock, patch


from bot.cogs.puzzles import Puzzles
from bot.core import hunt_archive
from tests.conftest import make_cog, make_ctx


def _hunt():
    h = MagicMock()
    h.name = "H"
    h.role_id = 0
    h.start_time = None
    h.channel_id = 60
    h.layout_mode = "channel"
    return h


async def test_archive_hunt_runs_when_confirmed():
    hunt = _hunt()
    cog = make_cog(hunt=hunt)
    cog.service.archive_hunt = AsyncMock(return_value=hunt_archive.HuntArchiveReport(
        channels_archived=4, final_name="H - me"))
    ctx = make_ctx()
    ctx.author.display_name = "me"
    with patch.object(cog, "_confirm_destructive", new=AsyncMock(return_value=True)), \
         patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb:
        PuzzleDb.get_all_from_hunt.return_value = []
        await Puzzles.archive_hunt.callback(cog, ctx)
    cog.service.archive_hunt.assert_awaited_once()
    assert cog.service.archive_hunt.await_args.args[0] is ctx.guild
    assert cog.service.archive_hunt.await_args.args[1] is hunt
    assert cog.service.archive_hunt.await_args.args[2] is ctx.author


async def test_archive_hunt_aborts_when_cancelled():
    hunt = _hunt()
    cog = make_cog(hunt=hunt)
    cog.service.archive_hunt = AsyncMock()
    ctx = make_ctx()
    ctx.author.display_name = "me"
    with patch.object(cog, "_confirm_destructive", new=AsyncMock(return_value=False)), \
         patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb:
        PuzzleDb.get_all_from_hunt.return_value = []
        await Puzzles.archive_hunt.callback(cog, ctx)
    cog.service.archive_hunt.assert_not_awaited()


async def test_archive_hunt_locks_member_puzzles():
    """archive_hunt must hold every member puzzle's lock across the archive,
    matching delete_hunt/delete_round (review follow-up). The lock is taken AFTER
    the confirm click, not around it (H5 aggravator) — see
    tests/test_lock_ordering.py."""
    hunt = _hunt()
    cog = make_cog(hunt=hunt)
    cog.service.archive_hunt = AsyncMock(return_value=hunt_archive.HuntArchiveReport(
        channels_archived=4, final_name="H - me"))
    ctx = make_ctx()
    ctx.author.display_name = "me"
    members = [MagicMock(id=3), MagicMock(id=1)]
    with patch.object(cog, "_confirm_destructive", new=AsyncMock(return_value=True)), \
         patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb, \
         patch("bot.cogs.puzzles.puzzles_lock") as lock_mock:
        PuzzleDb.get_all_from_hunt.return_value = members
        lock_mock.return_value.__aenter__ = AsyncMock(return_value=None)
        lock_mock.return_value.__aexit__ = AsyncMock(return_value=None)
        await Puzzles.archive_hunt.callback(cog, ctx)
    lock_mock.assert_called_once_with([3, 1])
