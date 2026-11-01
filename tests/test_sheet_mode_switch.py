"""Switching a hunt between tabs and files sheet mode (service), plus the
solved-sheet refusals and the cog's reference-spreadsheet trim."""
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from bot.cogs.puzzles_gsheet import GoogleSheets
from bot.core.puzzle_service import PuzzleService
from tests.conftest import make_hunt


def _cog():
    cog = MagicMock()
    cog.create_folder = AsyncMock(side_effect=["FOLDER", "SOLVEDF"])
    cog.move_file = AsyncMock(return_value=["P1"])
    cog.rename_file = AsyncMock()
    cog.delete_file = AsyncMock()
    return cog


def _svc(cog):
    bot = MagicMock()
    bot.get_cog.return_value = cog
    return PuzzleService(bot)


def _hunt(mode="tabs"):
    h = make_hunt()
    h.name = "H"
    h.google_sheet_id = "SS"
    h.solved_sheet_id = ""
    h.sheet_mode = mode
    h.drive_folder_id = "FOLDER" if mode == "files" else ""
    h.solved_folder_id = "SOLVEDF" if mode == "files" else ""
    return h



async def _switch(hunt, mode, cog, parent="PARENT", puzzles=(), groups=()):
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as gdb:
        pdb.get_all_from_hunt.return_value = list(puzzles)
        gdb.get_all.return_value = list(groups)
        await _svc(cog).set_sheet_mode(hunt, mode, guild_parent_id=parent)
    return hdb


@pytest.mark.asyncio
async def test_tabs_to_files_moves_and_renames_existing_spreadsheet():
    hunt, cog = _hunt("tabs"), _cog()
    hdb = await _switch(hunt, "files", cog)
    assert cog.create_folder.await_args_list == [call("H", "PARENT"), call("Solved", "FOLDER")]
    cog.move_file.assert_awaited_once_with("SS", "FOLDER")
    cog.rename_file.assert_awaited_once_with("SS", "H Reference")
    cog.delete_file.assert_not_awaited()
    assert (hunt.sheet_mode, hunt.drive_folder_id, hunt.solved_folder_id, hunt.google_sheet_id) \
        == ("files", "FOLDER", "SOLVEDF", "SS")
    hdb.commit.assert_called_once_with(hunt)


@pytest.mark.asyncio
async def test_tabs_to_files_deletes_the_empty_solved_spreadsheet():
    hunt, cog = _hunt("tabs"), _cog()
    hunt.solved_sheet_id = "ARCH"
    await _switch(hunt, "files", cog)
    cog.delete_file.assert_awaited_once_with("ARCH")
    assert hunt.solved_sheet_id == ""


@pytest.mark.asyncio
async def test_files_to_tabs_reverses_it():
    hunt, cog = _hunt("files"), _cog()
    hdb = await _switch(hunt, "tabs", cog)
    cog.move_file.assert_awaited_once_with("SS", "PARENT", "FOLDER")
    cog.rename_file.assert_awaited_once_with("SS", "H")
    assert cog.delete_file.await_args_list == [call("SOLVEDF"), call("FOLDER")]
    assert (hunt.sheet_mode, hunt.drive_folder_id, hunt.solved_folder_id) == ("tabs", "", "")
    hdb.commit.assert_called_once_with(hunt)


@pytest.mark.asyncio
async def test_files_to_tabs_with_no_parent_moves_to_root():
    hunt, cog = _hunt("files"), _cog()
    await _switch(hunt, "tabs", cog, parent=None)
    cog.move_file.assert_awaited_once_with("SS", "root", "FOLDER")


@pytest.mark.asyncio
async def test_same_mode_is_a_noop():
    hunt, cog = _hunt("tabs"), _cog()
    hdb = await _switch(hunt, "tabs", cog, puzzles=[object()])  # even with a puzzle
    cog.create_folder.assert_not_awaited()
    hdb.commit.assert_not_called()


@pytest.mark.asyncio
async def test_bad_mode_refused():
    with pytest.raises(RuntimeError, match="bad_sheet_mode"):
        await _switch(_hunt(), "folders", _cog())


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [{"puzzles": [object()]}, {"groups": [object()]}])
async def test_switch_refused_with_puzzle_or_group(kw):
    hunt, cog = _hunt("tabs"), _cog()
    with pytest.raises(RuntimeError, match="sheet_mode_locked"):
        await _switch(hunt, "files", cog, **kw)
    cog.create_folder.assert_not_awaited()
    assert hunt.sheet_mode == "tabs"


@pytest.mark.asyncio
async def test_create_solved_sheet_refused_on_files_hunt():
    hunt, cog = _hunt("files"), MagicMock()
    cog.create_solved_sheet = AsyncMock()
    with pytest.raises(RuntimeError, match="files_mode_no_solved_sheet"):
        await _svc(cog).create_hunt_solved_sheet(hunt)
    cog.create_solved_sheet.assert_not_awaited()


@pytest.mark.asyncio
async def test_reference_spreadsheet_trims_visible_non_reference_tabs_once():
    cog = GoogleSheets.__new__(GoogleSheets)
    cog.copy_template = AsyncMock(return_value="NEW")
    cog.template_tabs = AsyncMock(return_value=[
        {"sheetId": 1, "title": "REFERENCE", "hidden": False},
        {"sheetId": 2, "title": "Old visible", "hidden": False},
        {"sheetId": 3, "title": "Another", "hidden": False},
        {"sheetId": 4, "title": "Tab template", "hidden": True},
    ])
    cog.batch_update = AsyncMock()
    assert await cog.create_reference_spreadsheet("H", "F") == "NEW"
    cog.copy_template.assert_awaited_once_with("H Reference", "F")
    cog.batch_update.assert_awaited_once_with(
        {"requests": [{"deleteSheet": {"sheetId": 2}}, {"deleteSheet": {"sheetId": 3}}]},
        spreadsheet_id="NEW")


@pytest.mark.asyncio
async def test_reference_spreadsheet_sends_no_empty_batch():
    cog = GoogleSheets.__new__(GoogleSheets)
    cog.copy_template = AsyncMock(return_value="NEW")
    cog.template_tabs = AsyncMock(return_value=[
        {"sheetId": 1, "title": "REFERENCE", "hidden": False},
        {"sheetId": 4, "title": "Tab template", "hidden": True}])
    cog.batch_update = AsyncMock()
    await cog.create_reference_spreadsheet("H", "F")
    cog.batch_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_tabs_to_files_failure_before_commit_undoes_everything():
    hunt, cog = _hunt("tabs"), _cog()
    hunt.solved_sheet_id = "ARCH"
    cog.rename_file = AsyncMock(side_effect=[RuntimeError("boom"), None])
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as gdb:
        pdb.get_all_from_hunt.return_value = []
        gdb.get_all.return_value = []
        with pytest.raises(RuntimeError, match="boom"):
            await _svc(cog).set_sheet_mode(hunt, "files", guild_parent_id="PARENT")
    hdb.commit.assert_not_called()
    assert (hunt.sheet_mode, hunt.drive_folder_id, hunt.solved_sheet_id) == ("tabs", "", "ARCH")
    # moved back to its looked-up parent, and both created folders deleted
    cog.move_file.assert_any_await("SS", "P1", "FOLDER")
    deleted = [c.args[0] for c in cog.delete_file.await_args_list]
    assert sorted(deleted) == ["FOLDER", "SOLVEDF"]
    assert "ARCH" not in deleted


@pytest.mark.asyncio
async def test_tabs_to_files_commit_failure_renames_back():
    hunt, cog = _hunt("tabs"), _cog()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as gdb:
        pdb.get_all_from_hunt.return_value = []
        gdb.get_all.return_value = []
        hdb.commit.side_effect = RuntimeError("db")
        with pytest.raises(RuntimeError, match="db"):
            await _svc(cog).set_sheet_mode(hunt, "files", guild_parent_id=None)
    cog.rename_file.assert_any_await("SS", "H")
    assert hunt.sheet_mode == "tabs" and hunt.drive_folder_id == ""


@pytest.mark.asyncio
async def test_tabs_to_files_solved_delete_failure_after_commit_keeps_files():
    hunt, cog = _hunt("tabs"), _cog()
    hunt.solved_sheet_id = "ARCH"
    cog.delete_file = AsyncMock(side_effect=RuntimeError("drive"))
    hdb = await _switch(hunt, "files", cog)
    hdb.commit.assert_called_once_with(hunt)
    assert hunt.sheet_mode == "files" and hunt.solved_sheet_id == ""


@pytest.mark.asyncio
async def test_files_to_tabs_failure_before_commit_moves_back():
    hunt, cog = _hunt("files"), _cog()
    cog.rename_file = AsyncMock(side_effect=[RuntimeError("boom"), None])
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as gdb:
        pdb.get_all_from_hunt.return_value = []
        gdb.get_all.return_value = []
        with pytest.raises(RuntimeError, match="boom"):
            await _svc(cog).set_sheet_mode(hunt, "tabs", guild_parent_id="PARENT")
    hdb.commit.assert_not_called()
    assert (hunt.sheet_mode, hunt.drive_folder_id, hunt.solved_folder_id) \
        == ("files", "FOLDER", "SOLVEDF")
    cog.move_file.assert_any_await("SS", "FOLDER", "PARENT")
    cog.delete_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_files_to_tabs_folder_delete_failure_still_committed_as_tabs():
    hunt, cog = _hunt("files"), _cog()
    cog.delete_file = AsyncMock(side_effect=RuntimeError("drive"))
    hdb = await _switch(hunt, "tabs", cog)
    hdb.commit.assert_called_once_with(hunt)
    assert hunt.sheet_mode == "tabs" and hunt.drive_folder_id == ""
    assert cog.delete_file.await_count == 2   # both attempted despite the first failing


@pytest.mark.asyncio
@pytest.mark.parametrize("prev", [["P1"], []])
async def test_tabs_to_files_never_deletes_folder_when_move_back_fails(prev):
    hunt, cog = _hunt("tabs"), _cog()
    cog.rename_file = AsyncMock(side_effect=RuntimeError("boom"))
    if prev:
        cog.move_file = AsyncMock(side_effect=[prev, RuntimeError("move back failed")])
    else:
        cog.move_file = AsyncMock(return_value=[])
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as gdb:
        pdb.get_all_from_hunt.return_value = []
        gdb.get_all.return_value = []
        with pytest.raises(RuntimeError, match="boom"):
            await _svc(cog).set_sheet_mode(hunt, "files", guild_parent_id="PARENT")
    hdb.commit.assert_not_called()
    cog.delete_file.assert_not_awaited()   # the spreadsheet may still be in FOLDER
    if not prev:
        assert cog.move_file.await_count == 1   # no addParents="" move-back


def _http_error(status):
    from googleapiclient.errors import HttpError
    resp = MagicMock()
    resp.status = status
    resp.reason = "x"
    return HttpError(resp, b"{}")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404])
async def test_switch_falls_back_to_drive_root_when_the_parent_is_unusable(status, caplog):
    hunt, cog = _hunt("tabs"), _cog()
    cog.create_folder = AsyncMock(side_effect=[_http_error(status), "FOLDER", "SOLVEDF"])
    with caplog.at_level("WARNING"):
        await _switch(hunt, "files", cog)
    assert cog.create_folder.await_args_list == [
        call("H", "PARENT"), call("H", None), call("Solved", "FOLDER")]
    assert hunt.drive_folder_id == "FOLDER"
    assert "PARENT" in caplog.text


@pytest.mark.asyncio
async def test_switch_propagates_a_500_from_the_parent_folder_create():
    hunt, cog = _hunt("tabs"), _cog()
    cog.create_folder = AsyncMock(side_effect=_http_error(500))
    with pytest.raises(Exception):
        await _switch(hunt, "files", cog)
    assert cog.create_folder.await_count == 1
    assert hunt.sheet_mode == "tabs"
