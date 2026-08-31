"""Creating a hunt's solved-puzzles sheet is one seam, not two copies (C7).

The web route and the bang !use_solved_sheet each hand-rolled check -> create ->
commit, unlocked. Two concurrent invocations (a double-click, or bang and web
together) both passed the exists-check and created two Drive spreadsheets,
orphaning one.

The copies had also already drifted, which is the argument for a seam rather
than just bolting a lock onto each:

  * the bang tested `len(hunt.solved_sheet_id) > 0`, which raises TypeError on a
    NULL column where the web twin's truthiness check does not
  * the bang committed the hunt row even when the Sheets cog was absent -- a
    no-op write; the web twin returned 503

Lock-free by the bot/core convention: hunt_sheet_lock is held at the EDGE (its
own docstring says so), so both callers wrap this and the service never
re-acquires it.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from tests.conftest import make_hunt


def _svc(gsheet_cog):
    bot = MagicMock()
    bot.get_cog.return_value = gsheet_cog
    return PuzzleService(bot)


def _cog(sheet_id="NEW_SHEET"):
    cog = MagicMock()
    cog.create_solved_sheet = AsyncMock(return_value=sheet_id)
    return cog


@pytest.mark.asyncio
async def test_it_creates_records_and_returns_the_sheet_id():
    hunt = make_hunt()
    hunt.solved_sheet_id = ""
    cog = _cog()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        result = await _svc(cog).create_hunt_solved_sheet(hunt)

    assert result == "NEW_SHEET"
    assert hunt.solved_sheet_id == "NEW_SHEET"
    cog.create_solved_sheet.assert_awaited_once_with(hunt.name)
    hdb.commit.assert_called_once_with(hunt)


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", ["ALREADY", "x"])
async def test_a_hunt_that_already_has_one_is_refused(existing):
    """Idempotence, and the reason the lock matters: this check and the create
    below it must be one critical section."""
    hunt = make_hunt()
    hunt.solved_sheet_id = existing
    cog = _cog()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        with pytest.raises(RuntimeError, match="solved_sheet_exists"):
            await _svc(cog).create_hunt_solved_sheet(hunt)

    cog.create_solved_sheet.assert_not_awaited()
    hdb.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("absent", [None, ""])
async def test_a_null_column_counts_as_absent_not_as_a_crash(absent):
    """The bang's drift. `len(None) > 0` raises TypeError; both spellings of
    "no sheet yet" must take the create path. Two values, so a len()-based
    implementation disagrees on one of them.
    """
    hunt = make_hunt()
    hunt.solved_sheet_id = absent
    cog = _cog()
    with patch("bot.core.puzzle_service.HuntJsonDb"):
        result = await _svc(cog).create_hunt_solved_sheet(hunt)

    assert result == "NEW_SHEET"


@pytest.mark.asyncio
async def test_no_sheets_cog_refuses_without_committing():
    """The bang's other drift: it committed the hunt row even with no cog, so a
    hunt could be marked done having created nothing."""
    hunt = make_hunt()
    hunt.solved_sheet_id = ""
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        with pytest.raises(RuntimeError, match="gsheet_unavailable"):
            await _svc(None).create_hunt_solved_sheet(hunt)

    hdb.commit.assert_not_called()
    assert not hunt.solved_sheet_id
