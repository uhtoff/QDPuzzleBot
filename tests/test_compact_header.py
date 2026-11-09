"""Compact three-cell header at tab/file creation (tabs, files, files retry, meta)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.cogs.puzzles_gsheet import GoogleSheets
from bot.core.sheet_location import Link
from tests.conftest import make_puzzle
from tests.test_files_mode_create import (
    Recorder, _cog as _files_cog, _files_hunt,
)

P = 900


def _cog():
    cog = GoogleSheets.__new__(GoogleSheets)
    cog.spreadsheet_of = lambda p: "SS"
    cog.get_unique_tab_name = AsyncMock(return_value="Alpha")
    cog.create_blank_tab = AsyncMock(return_value=P)
    cog._add_contribution_table = AsyncMock()
    return cog


def _capture_batch(cog):
    calls = []

    async def fake(body, spreadsheet_id=None):
        calls.append(body)
        return {"replies": []}

    cog.batch_update = fake
    return calls


def _compact(**kw):
    p = make_puzzle(**kw)
    p.header_layout = "compact"
    p.url = "https://h/alpha"
    return p


def _rng(r0, r1, c0, c1, sid=P):
    return {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1,
            "startColumnIndex": c0, "endColumnIndex": c1}


def _reqs(calls):
    return [r for body in calls for r in body["requests"]]


def _cell_at(reqs, row, col):
    for r in reqs:
        uc = r.get("updateCells")
        if uc and uc["range"].get("startRowIndex") == row \
                and uc["range"].get("startColumnIndex") == col \
                and "rows" in uc and len(uc["rows"][0]["values"]) == 1:
            return r
    raise AssertionError(f"no updateCells at r{row} c{col}")


def _title(name, url, row_col=(0, 0)):
    runs = [{"startIndex": 0, "format": {"link": {"uri": url}}}]
    return {"updateCells": {
        "rows": [{"values": [{"userEnteredValue": {"stringValue": name},
                              "textFormatRuns": runs}]}],
        "fields": "userEnteredValue,textFormatRuns",
        "range": _rng(row_col[0], row_col[0] + 1, row_col[1], row_col[1] + 1)}}


def test_compact_feeder_tab_is_three_cells_and_nothing_else():
    cog = _cog()
    calls = _capture_batch(cog)
    puzzle = _compact(name="Alpha")
    asyncio.run(cog.generate_puzzle_tab(
        puzzle, is_target=False, header_links=[Link("Alpha Meta", "U1")]))
    reqs = _reqs(calls)

    assert _cell_at(reqs, 0, 0) == _title("Alpha", "https://h/alpha")
    a2 = _cell_at(reqs, 1, 0)["updateCells"]
    assert a2["rows"][0]["values"][0]["userEnteredValue"] == {"stringValue": "Unsolved"}
    fmt = a2["rows"][0]["values"][0]["userEnteredFormat"]
    assert fmt["backgroundColor"] == GoogleSheets.HEADER_GREY
    assert fmt["textFormat"] == {"bold": False}
    a3 = _cell_at(reqs, 2, 0)["updateCells"]
    assert a3["rows"][0]["values"][0] == {
        "userEnteredValue": {"stringValue": "Feeds: Alpha Meta"},
        "textFormatRuns": [{"startIndex": 0, "format": {}},
                           {"startIndex": 7, "format": {"link": {"uri": "U1"}}}]}
    assert a3["fields"] == "userEnteredValue,textFormatRuns"

    text = repr(reqs)
    assert "mergeCells" not in text
    assert "frozenRowCount" not in text
    assert "formulaValue" not in text

    greys = [r["repeatCell"]["range"] for r in reqs if "repeatCell" in r
             and "backgroundColor" in r["repeatCell"]["cell"].get("userEnteredFormat", {})]
    assert greys == [_rng(0, 3, 0, 1)]
    borders = [r["updateBorders"]["range"] for r in reqs if "updateBorders" in r]
    assert borders == [_rng(0, 3, 0, 1)]
    widths = [r["updateDimensionProperties"] for r in reqs if "updateDimensionProperties" in r]
    assert len(widths) == 1
    assert widths[0]["range"]["startIndex"] == 0 and widths[0]["range"]["endIndex"] == 1
    assert widths[0]["properties"]["pixelSize"] == 220

    for r in reqs:
        uc = r.get("updateCells")
        if uc and "startColumnIndex" in uc["range"]:
            assert uc["range"]["startColumnIndex"] == 0
            assert uc["range"]["endColumnIndex"] == 1
            assert uc["range"]["endRowIndex"] <= 3
        rc = r.get("repeatCell")
        if rc and "startColumnIndex" in rc["range"]:
            assert rc["range"]["endColumnIndex"] == 1
            assert rc["range"]["endRowIndex"] <= 3


def test_compact_tab_without_links_says_no_links():
    cog = _cog()
    calls = _capture_batch(cog)
    asyncio.run(cog.generate_puzzle_tab(_compact(name="Alpha"), is_target=False,
                                        header_links=None))
    a3 = _cell_at(_reqs(calls), 2, 0)["updateCells"]
    assert a3["rows"][0]["values"][0] == {"userEnteredValue": {"stringValue": "No links"}}
    assert a3["fields"] == "userEnteredValue,textFormatRuns"


def test_compact_feeds_runs_count_utf16_units():
    cog = _cog()
    calls = _capture_batch(cog)
    asyncio.run(cog.generate_puzzle_tab(
        _compact(name="Alpha"), is_target=False,
        header_links=[Link("🧩 Meta", "U1"), Link("B", "U2")]))
    a3 = _cell_at(_reqs(calls), 2, 0)["updateCells"]["rows"][0]["values"][0]
    assert [r["startIndex"] for r in a3["textFormatRuns"]] == [0, 7, 14, 16]


def test_compact_target_has_header_in_b_and_table_in_the_same_batch():
    cog = _cog()
    calls = _capture_batch(cog)
    puzzle = _compact(name="Meta", is_target=True)
    asyncio.run(cog.generate_puzzle_tab(puzzle, is_target=True))
    assert len(calls) == 1
    reqs = _reqs(calls)
    assert _cell_at(reqs, 0, 1) == _title("Meta", "https://h/alpha", (0, 1))
    add = next(r["addTable"]["table"] for r in reqs if "addTable" in r)
    assert add["range"]["startRowIndex"] == 3
    hide = [r for r in reqs if "updateDimensionProperties" in r
            and r["updateDimensionProperties"]["properties"].get("hiddenByUser")]
    assert len(hide) == 1
    assert hide[0]["updateDimensionProperties"]["range"]["startIndex"] == 0
    # grey / border sit on the shifted column B only
    borders = [r["updateBorders"]["range"] for r in reqs if "updateBorders" in r]
    assert borders == [_rng(0, 3, 1, 2)]
    cog._add_contribution_table.assert_not_awaited()


def test_compact_target_create_failure_propagates_with_nothing_deferred():
    cog = _cog()

    async def boom(body, spreadsheet_id=None):
        raise RuntimeError("500")

    cog.batch_update = boom
    puzzle = _compact(name="Meta", is_target=True)
    with pytest.raises(RuntimeError):
        asyncio.run(cog.generate_puzzle_tab(puzzle, is_target=True))
    cog._add_contribution_table.assert_not_awaited()


def test_compact_files_create_and_its_retry_both_stamp_the_compact_header():
    cog = _files_cog()
    state = {"n": 0}

    def fail_first_header(kind, sid, body):
        if kind != "batch" or any("addSheet" in r for r in body["requests"]):
            return False
        state["n"] += 1
        return state["n"] == 1

    rec = Recorder(fail_on=fail_first_header)
    puzzle = _compact(name="Clueless")
    rec.run(cog.create_puzzle_spreadsheet(
        puzzle, index=9, hunt=_files_hunt(),
        header_links=[Link("Alpha Meta", "U1")]))

    headers = [b for (_, _, b) in rec.batches
               if not any("addSheet" in r for r in b["requests"])
               and any("updateBorders" in r for r in b["requests"])]
    assert len(headers) == 2          # the failed attempt and the retry
    retry = headers[1]["requests"]
    assert _cell_at(retry, 0, 0)["updateCells"]["rows"][0]["values"][0][
        "userEnteredValue"] == {"stringValue": "Clueless"}
    a2 = _cell_at(retry, 1, 0)["updateCells"]["rows"][0]["values"][0]
    assert a2["userEnteredValue"] == {"stringValue": "Unsolved"}
    a3 = _cell_at(retry, 2, 0)["updateCells"]["rows"][0]["values"][0]
    assert a3["userEnteredValue"] == {"stringValue": "Feeds: Alpha Meta"}
    assert any("deleteSheet" in r for r in retry)       # template trim rode along
    for b in headers:
        assert "mergeCells" not in repr(b)


def test_full_puzzle_with_magicmock_layout_gets_the_full_header():
    cog = _cog()
    calls = _capture_batch(cog)
    puzzle = make_puzzle(name="Alpha")
    puzzle.header_layout = MagicMock()
    puzzle.url = "https://h/alpha"
    asyncio.run(cog.generate_puzzle_tab(puzzle, is_target=False))
    reqs = _reqs(calls)
    assert any("mergeCells" in r for r in reqs)
    assert "Puzzle Title:" in repr(reqs)
