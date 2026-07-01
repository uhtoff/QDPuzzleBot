"""Self-clash fix: renaming a puzzle must exclude itself from the hunt
duplicate-name check, so adding punctuation/emoji to a puzzle's own name
(e.g. "Monster Mash" -> "Monster Mash!") is not rejected as a duplicate of
itself."""
from unittest.mock import AsyncMock, patch

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
