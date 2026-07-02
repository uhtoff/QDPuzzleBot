"""Guard test for sheet_list(): a Sheets get response must carry a 'sheets' field.

A real spreadsheet always reports at least one sheet, so a response missing the
'sheets' key means the read returned bad/empty data (a transient Google glitch),
not an empty spreadsheet. sheet_list() must fail loudly at the source rather than
let callers index ["sheets"] and raise an opaque KeyError five frames deep — or,
worse, treat the missing key as "no tabs" and create duplicate tabs.

Stubs the Google seam (get_sheet / execute_sync) in the style of the sibling
gsheet suites.
"""
import pytest
from unittest.mock import AsyncMock, patch

import bot.cogs.puzzles_gsheet as gs
from bot.cogs.puzzles_gsheet import GoogleSheets


def _cog():
    # Skip __init__ (it builds a real Sheets service); sheet_list only touches the
    # module-level get_sheet / execute_with_retry, which we stub.
    return GoogleSheets.__new__(GoogleSheets)


async def test_sheet_list_raises_when_sheets_key_missing():
    cog = _cog()
    with patch.object(gs, "get_sheet"), \
         patch.object(gs, "execute_with_retry", new=AsyncMock(return_value={})):
        with pytest.raises(RuntimeError):
            await cog.sheet_list("SS")


async def test_sheet_list_returns_response_when_sheets_present():
    cog = _cog()
    payload = {"sheets": [{"properties": {"title": "Tide", "sheetId": 1}}]}
    with patch.object(gs, "get_sheet"), \
         patch.object(gs, "execute_with_retry", new=AsyncMock(return_value=payload)):
        assert await cog.sheet_list("SS") == payload
