"""The bang meta-refresh helpers follow the same isolation path as solve/retag (M5).

_update_all_metas and update_metapuzzle both route through PuzzleService.refresh_metas
(failure-isolated, each meta badged) and raise SheetRefreshError if any meta failed, so
every bang edge surfaces a stale sheet via _warn_stale_sheets rather than aborting.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from bot.core.errors import SheetRefreshError
from tests.conftest import make_puzzle, make_round


def _cog(failed):
    cog = Puzzles.__new__(Puzzles)
    cog.get_hunt = lambda ctx: MagicMock(id=10)
    cog.service = MagicMock(refresh_metas=AsyncMock(return_value=failed))
    return cog


@pytest.mark.asyncio
async def test_update_all_metas_attempts_all_then_raises():
    cog = _cog(failed=[5])
    puzzle = make_puzzle(id=2, tags=[5, 6])
    with patch("bot.cogs.puzzles.RoundJsonDb") as rdb:
        rdb.get_by_attr.side_effect = lambda **kw: make_round(id=kw["id"], meta_id=kw["id"] * 10)
        with pytest.raises(SheetRefreshError) as exc:
            await cog._update_all_metas(None, puzzle)
    rounds_arg = cog.service.refresh_metas.await_args[0][1]
    assert [r.id for r in rounds_arg] == [5, 6]  # every tag handed to the isolated path
    assert exc.value.failed_round_ids == [5]


@pytest.mark.asyncio
async def test_update_all_metas_no_failure_does_not_raise():
    cog = _cog(failed=[])
    puzzle = make_puzzle(id=2, tags=[5])
    with patch("bot.cogs.puzzles.RoundJsonDb") as rdb:
        rdb.get_by_attr.side_effect = lambda **kw: make_round(id=kw["id"], meta_id=50)
        await cog._update_all_metas(None, puzzle)  # must not raise


@pytest.mark.asyncio
async def test_update_metapuzzle_raises_on_failure():
    cog = _cog(failed=[7])
    with pytest.raises(SheetRefreshError):
        await cog.update_metapuzzle(None, make_round(id=7, meta_id=70))


@pytest.mark.asyncio
async def test_update_metapuzzle_ok_does_not_raise():
    cog = _cog(failed=[])
    await cog.update_metapuzzle(None, make_round(id=7, meta_id=70))  # must not raise
