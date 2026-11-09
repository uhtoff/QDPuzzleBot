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


# --- Task 4: the flag is set at creation; extra sheets -----------------------

from unittest.mock import patch  # noqa: E402

from tests.test_create_service import _service_with_sheets, _svc_hunt  # noqa: E402
import tests.test_reconcile as _rec  # noqa: E402


def _hunt_with_style(style):
    h = _svc_hunt()
    h.header_style = style
    return h


async def _create_one(svc, hunt):
    guild = MagicMock()
    guild.channels = []
    guild.categories = [MagicMock(id=999)]
    notifier = MagicMock()
    notifier.welcome = AsyncMock()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.category_layout.pick_unsolved_category",
               AsyncMock(return_value=MagicMock(id=999, channels=[]))), \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=222, mention="<#222>", name="np"), True))):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        await svc.create_puzzle(guild, hunt, None, "Newpuz", notifier=notifier)


def _record_layout(gsheet):
    seen = []

    async def rec(puzzle, **kw):
        seen.append(puzzle.header_layout)

    gsheet.create_puzzle_spreadsheet = AsyncMock(side_effect=rec)
    return seen


@pytest.mark.asyncio
async def test_create_puzzle_copies_the_hunt_style_before_the_sheet_call():
    svc, gsheet = _service_with_sheets()
    seen = _record_layout(gsheet)
    await _create_one(svc, _hunt_with_style("compact"))
    await _create_one(svc, _hunt_with_style("full"))
    assert seen == ["compact", "full"]


@pytest.mark.asyncio
async def test_create_mini_copies_the_hunt_style():
    svc, gsheet = _service_with_sheets()
    gsheet.mini_insert_index = AsyncMock(return_value=0)
    seen = _record_layout(gsheet)
    svc.make_minimeta = AsyncMock()
    svc._links_to_safe = MagicMock(return_value=[])
    for style in ("compact", "full"):
        minimeta = make_puzzle(id=7, name="MM")
        thread = MagicMock(id=5)
        thread.name = "t"
        thread.send = AsyncMock()
        channel = MagicMock()
        channel.create_thread = AsyncMock(return_value=thread)
        svc.bot.get_cog.return_value = gsheet
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb:
            pdb.get_all_from_hunt.return_value = []
            pdb.commit.side_effect = lambda p: setattr(p, "id", 2)
            svc.refresh_minimeta = AsyncMock()
            svc.refresh_header_links = AsyncMock()
            guild = MagicMock()
            guild.get_channel.return_value = channel
            await svc.create_mini(guild, _hunt_with_style(style), minimeta, "Mini")
    assert seen == ["compact", "full"]


@pytest.mark.asyncio
async def test_unknown_hunt_style_creates_a_full_puzzle():
    svc, gsheet = _service_with_sheets()
    seen = _record_layout(gsheet)
    await _create_one(svc, _hunt_with_style("weird"))
    assert seen == ["full"]


def test_reconcile_recreates_an_old_full_puzzle_full_in_a_compact_hunt():
    gcog = _rec._gcog(page_name=None)

    async def _add(puzzle, index):
        puzzle.google_page_id = "77"
        return "77"

    gcog.add_new_puzzle_sheet.side_effect = _add
    svc = _rec._service_with_gsheet(gcog)
    p = _rec._puzzle(google_page_id="42", tags=[10], header_layout="full")
    hunt = _rec._hunt(header_style="compact")
    with patch.object(_rec.ps.PuzzleJsonDb, "commit"):
        asyncio.run(svc._reconcile_tabs(hunt, p, apply=True))
    gcog.add_new_puzzle_sheet.assert_awaited_once()
    assert gcog.add_new_puzzle_sheet.await_args.args[0] is p
    assert p.header_layout == "full"


def _extra_cog(sheet_id=555):
    cog = _cog()
    cog.get_puzzle_sheet_index = AsyncMock(return_value=4)
    cog.create_blank_tab = AsyncMock(return_value=sheet_id)
    cog.add_new_sheet = AsyncMock(return_value=sheet_id)
    cog.update_puzzle_info = AsyncMock()
    cog.get_unique_tab_name = AsyncMock(side_effect=lambda n, s=None: n)
    return cog


def test_compact_extra_sheet_is_a_blank_tab_with_title_and_status():
    cog = _extra_cog()
    calls = _capture_batch(cog)
    puzzle = _compact(name="Alpha", solved=True, solution="ANSWER")
    puzzle.google_page_id = 100
    sid = asyncio.run(cog.create_additional_spreadsheet(puzzle, "Grid"))
    assert sid == 555
    cog.create_blank_tab.assert_awaited_once_with("SS", "Grid (Alpha)", 5)
    cog.add_new_sheet.assert_not_awaited()
    cog.update_puzzle_info.assert_not_awaited()
    assert len(calls) == 1
    reqs = _reqs(calls)
    assert _cell_at(reqs, 0, 0)["updateCells"]["range"]["sheetId"] == 555
    a1 = _cell_at(reqs, 0, 0)["updateCells"]["rows"][0]["values"][0]
    assert a1["userEnteredValue"] == {"stringValue": "Grid"}
    assert a1["textFormatRuns"] == [{"startIndex": 0,
                                     "format": {"link": {"uri": "https://h/alpha"}}}]
    a2 = _cell_at(reqs, 1, 0)["updateCells"]["rows"][0]["values"][0]
    assert a2["userEnteredValue"] == {"stringValue": "ANSWER"}
    assert a2["userEnteredFormat"]["textFormat"] == {"bold": True}
    assert a2["userEnteredFormat"]["backgroundColor"] == GoogleSheets.SOLUTION_GREEN
    with pytest.raises(AssertionError):
        _cell_at(reqs, 2, 0)
    greys = [r["repeatCell"]["range"] for r in reqs if "repeatCell" in r
             and "backgroundColor" in r["repeatCell"]["cell"].get("userEnteredFormat", {})]
    assert greys == [_rng(0, 2, 0, 1, sid=555)]
    borders = [r["updateBorders"]["range"] for r in reqs if "updateBorders" in r]
    assert borders == [_rng(0, 2, 0, 1, sid=555)]


def test_full_extra_sheet_still_duplicates_the_template():
    cog = _extra_cog()
    calls = _capture_batch(cog)
    puzzle = make_puzzle(name="Alpha")
    asyncio.run(cog.create_additional_spreadsheet(puzzle, "Grid"))
    cog.add_new_sheet.assert_awaited_once()
    cog.update_puzzle_info.assert_awaited_once()
    cog.create_blank_tab.assert_not_awaited()
    assert calls == []
