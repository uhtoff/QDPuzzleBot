"""M3.5: bang and web create paths produce identical core effects.

Both edges must funnel through PuzzleService.create_puzzle with the same core args
(name + is_meta). We spy on the service seam and drive each edge once.

Phase 4 completes the **web** delegation; Phase 5 will fold the bang callers onto
the same service path. Until then only the web assertion fires against the spy.

Helpers are uniquely named and local to this file (parallel gate shards by file).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_round, make_puzzle


def _equiv_hunt():
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "S1"
    h.archive_google_sheet_id = "A1"
    h.url = None
    h.name = "H"
    return h


def _equiv_cog():
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return Puzzles(bot)


def _equiv_created_puzzle():
    """Return a PuzzleData that mirrors what create_puzzle returns."""
    p = make_puzzle(id=99, name="Newpuz")
    p.tags.append(5)  # same round as the test
    return p


@pytest.mark.asyncio
async def test_bang_and_web_create_share_service_call():
    cog = _equiv_cog()
    hunt = _equiv_hunt()
    guild = MagicMock()
    guild.channels = []
    cat = MagicMock(id=999, name="round-cat")
    guild.categories = [cat]
    round_data = make_round(id=5, category_id=999)

    created_channel = MagicMock(id=222, mention="<#222>", name="newpuz")

    # Recording spy on the shared service seam, reused across both edges.
    spy = AsyncMock(return_value=_equiv_created_puzzle())

    with patch.object(PuzzleService, "create_puzzle", spy), \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(created_channel, True))):

        # Web edge — calls service directly (Phase 2e: web_create_puzzle deleted).
        await cog.service.create_puzzle(guild, hunt, round_data, "Newpuz", is_meta=False)

        # Bang edge — Phase 5 folds !p onto the SAME service path. The bang command
        # is a discord.py Command; drive its mutex-wrapped callback directly. The
        # cog-level dup check + space check stay at the edge, so stub them out.
        cog.environment[7] = {
            "channel_type": "Group", "hunt": hunt, "tag_group": round_data,
            "puzzle": None, "guild": guild, "guild_data": None, "gsheet_cog": None,
        }
        bang_ctx = MagicMock()
        bang_ctx.env = None                 # force the mutex to read cog.environment
        bang_ctx.guild = guild
        bang_ctx.message.id = 7
        bang_ctx.send = AsyncMock()
        with patch("bot.cogs.puzzles.PuzzleJsonDb") as cog_pdb, \
             patch.object(cog, "check_available_space", AsyncMock(return_value=True)):
            cog_pdb.check_duplicates_in_hunt.return_value = False
            await cog.puzzle.callback(cog, bang_ctx, arg="Newpuz")

    # Both edges funnelled through the one service seam.
    assert spy.await_count == 2
    # Both calls pass is_meta=False (positional index 4 or keyword).
    metas = {call.kwargs.get("is_meta", call.args[4] if len(call.args) > 4 else None)
             for call in spy.await_args_list}
    assert metas == {False}
