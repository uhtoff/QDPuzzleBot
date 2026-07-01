"""Self-clash fix: renaming a puzzle must exclude itself from the hunt
duplicate-name check, so adding punctuation/emoji to a puzzle's own name
(e.g. "Monster Mash" -> "Monster Mash!") is not rejected as a duplicate of
itself."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_cog, make_ctx, make_hunt, make_puzzle


def _rename_puzzle_cog(puzzle):
    from bot.cogs.puzzles import Puzzles
    cog = make_cog(puzzle=puzzle, hunt=make_hunt())
    cog.service.rename_puzzle = AsyncMock()
    return cog, Puzzles


def _ctx_for(puzzle):
    ctx = make_ctx()
    ctx.env = {"puzzle": puzzle}  # satisfy with_puzzle_mutex lock path
    ctx.channel.send = AsyncMock()  # success path awaits ctx.channel.send
    return ctx


@pytest.mark.asyncio
async def test_rename_puzzle_excludes_self_from_duplicate_check():
    puz = make_puzzle(id=70, name="Monster Mash")
    cog, Puzzles = _rename_puzzle_cog(puz)
    ctx = _ctx_for(puz)
    with patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb:
        pdb.check_duplicates_in_hunt.return_value = False
        await Puzzles.rename_puzzle.callback(cog, ctx, puzzle_name="Monster Mash!")
    pdb.check_duplicates_in_hunt.assert_called_once()
    call = pdb.check_duplicates_in_hunt.call_args
    assert call.kwargs.get("exclude_puzzle_id") == 70
    cog.service.rename_puzzle.assert_awaited_once()


@pytest.mark.asyncio
async def test_rename_puzzle_still_rejects_real_duplicate():
    puz = make_puzzle(id=70, name="Monster Mash")
    cog, Puzzles = _rename_puzzle_cog(puz)
    ctx = _ctx_for(puz)
    with patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb:
        pdb.check_duplicates_in_hunt.return_value = True  # a *different* puzzle clashes
        await Puzzles.rename_puzzle.callback(cog, ctx, puzzle_name="Some Other Puzzle")
    cog.service.rename_puzzle.assert_not_awaited()
    assert ctx.send.await_count == 1


# --- Service layer: guard the Discord channel edit on a real name change ------

class _FakeChannel:
    def __init__(self, name):
        self.name = name


class _FakeGuild:
    def __init__(self, channel):
        self._channel = channel

    def get_channel(self, _cid):
        return self._channel


def _svc_no_sheets():
    from bot.core.puzzle_service import PuzzleService
    bot = MagicMock()
    bot.get_cog.return_value = None  # no GoogleSheets cog -> skip sheet update
    return PuzzleService(bot)


@pytest.mark.asyncio
async def test_rename_puzzle_skips_discord_edit_when_channel_name_unchanged():
    # "Monster Mash" -> "Monster Mash!" both clean to "monster-mash": no Discord
    # edit should fire (it would waste a rename-bucket token on a no-op).
    puz = make_puzzle(id=70, name="Monster Mash", channel_id=111)
    channel = _FakeChannel("monster-mash")
    svc = _svc_no_sheets()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch.object(svc, "safe_rename", new_callable=AsyncMock) as safe_rename:
        await svc.rename_puzzle(_FakeGuild(channel), make_hunt(), puz, "Monster Mash!")
    safe_rename.assert_not_awaited()
    assert puz.name == "Monster Mash!"          # name + sheet tab still updated
    assert puz.channel_name == "monster-mash"
    pdb.commit.assert_called_once_with(puz)


@pytest.mark.asyncio
async def test_rename_puzzle_edits_discord_when_channel_name_changes():
    puz = make_puzzle(id=70, name="Old Name", channel_id=111)
    channel = _FakeChannel("old-name")
    svc = _svc_no_sheets()
    with patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch.object(svc, "safe_rename", new_callable=AsyncMock) as safe_rename:
        await svc.rename_puzzle(_FakeGuild(channel), make_hunt(), puz, "New Name")
    safe_rename.assert_awaited_once_with(channel, "new-name")
