"""M3.5: bang and web create paths produce identical core effects.

Both edges must funnel through PuzzleService.create_puzzle with the same core args
(name + is_meta). We spy on the service seam and drive each edge once. Until Phase 4/5
strangle both callers onto the service, patching PuzzleService.create_puzzle raises
AttributeError (the attribute doesn't exist yet) — the right Phase-1 red.

Helpers are uniquely named and local to this file (parallel gate shards by file).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_round


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

    # Recording spy on the shared service seam. Raises AttributeError now because
    # PuzzleService.create_puzzle does not exist yet → right reason for Phase 1.
    spy = AsyncMock(return_value=make_round(id=99))  # return value unused by the edges
    with patch.object(PuzzleService, "create_puzzle", spy), \
         patch("bot.core.discord_channels.get_or_create_channel",
               AsyncMock(return_value=(created_channel, True))), \
         patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch("bot.cogs.puzzles.PuzzleJsonDb"):

        # Web edge.
        await cog.web_create_puzzle(guild, hunt, round_data, "Newpuz", is_meta=False)

        # Bang edge.
        ctx = MagicMock()
        ctx.channel.category = cat
        ctx.guild = guild
        cog.get_guild = MagicMock(return_value=guild)
        cog.get_hunt = MagicMock(return_value=hunt)
        ctx.send = AsyncMock()
        await cog.create_puzzle_channel(ctx, MagicMock(name="Newpuz", tags=[5]), category=cat)

    assert spy.await_count == 2
    # Both edges pass the same is_meta flag for an equivalent create.
    metas = {call.kwargs.get("is_meta", call.args[4] if len(call.args) > 4 else None)
             for call in spy.await_args_list}
    assert metas == {False}
