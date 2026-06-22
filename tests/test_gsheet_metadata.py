"""Tests for the developer-metadata helpers (M5) — stable tab pointers.

Like test_aggregate_to_tab, these stub the Google seam (get_sheet /
execute_with_retry) and use asyncio.run() rather than async test functions
(pytest-asyncio is configured, but these match the sibling gsheet suites' style).
"""
import asyncio
from unittest.mock import AsyncMock, patch

import bot.cogs.puzzles_gsheet as gs
from bot.cogs.puzzles_gsheet import GoogleSheets


def _cog():
    # Skip __init__ (it builds a real Sheets service); the helpers under test only
    # touch the module-level get_sheet / execute_with_retry, which we stub.
    return GoogleSheets.__new__(GoogleSheets)


def test_ensure_creates_metadata_when_absent():
    cog = _cog()
    with patch.object(gs, "get_sheet"), \
         patch.object(gs, "execute_with_retry", new=AsyncMock()) as ewr:
        ewr.side_effect = [
            {"matchedDeveloperMetadata": []},  # search: none yet
            {"replies": [{"createDeveloperMetadata":
                          {"developerMetadata": {"metadataId": 555}}}]},  # create
        ]
        result = asyncio.run(cog.ensure_tab_metadata("SS", 42, "rollup:5"))
    assert result == 555
    assert ewr.await_count == 2  # searched, then created


def test_ensure_returns_existing_without_duplicating():
    cog = _cog()
    with patch.object(gs, "get_sheet"), \
         patch.object(gs, "execute_with_retry", new=AsyncMock()) as ewr:
        ewr.return_value = {"matchedDeveloperMetadata":
                            [{"developerMetadata": {"metadataId": 999}}]}
        result = asyncio.run(cog.ensure_tab_metadata("SS", 42, "rollup:5"))
    assert result == 999
    assert ewr.await_count == 1  # search hit; no create issued


def test_find_tab_by_metadata_returns_sheet_id():
    cog = _cog()
    with patch.object(gs, "get_sheet"), \
         patch.object(gs, "execute_with_retry", new=AsyncMock()) as ewr:
        ewr.return_value = {"location": {"sheetId": 77}}
        result = asyncio.run(cog.find_tab_by_metadata("SS", 555))
    assert result == 77


def test_find_tab_by_metadata_none_when_no_location():
    cog = _cog()
    with patch.object(gs, "get_sheet"), \
         patch.object(gs, "execute_with_retry", new=AsyncMock()) as ewr:
        ewr.return_value = {}
        result = asyncio.run(cog.find_tab_by_metadata("SS", 555))
    assert result is None
