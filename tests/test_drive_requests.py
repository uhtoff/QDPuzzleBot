"""Request-shape tests for the Drive primitives (files-mode hunts).

Patches the cog module's get_drive / execute_with_retry and asserts the exact
request kwargs; no live Google.
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

import bot.cogs.puzzles_gsheet as gsheet_mod
from bot.cogs.puzzles_gsheet import GoogleSheets
from bot.utils import config

FOLDER = "application/vnd.google-apps.folder"


def _cog():
    cog = GoogleSheets.__new__(GoogleSheets)
    cog._notify_retry = None
    return cog


def _http_error(status):
    resp = MagicMock()
    resp.status = status
    resp.reason = "x"
    return HttpError(resp, b"{}")


def _run(coro, responses=None, raises=None):
    """Run coro with get_drive and execute_with_retry patched; return (drive, results)."""
    drive = MagicMock()
    queue = list(responses or [])

    async def fake_exec(req, on_retry=None):
        if raises is not None:
            raise raises
        return queue.pop(0) if queue else {}

    with patch.object(gsheet_mod, "get_drive", return_value=drive), \
            patch.object(gsheet_mod, "execute_with_retry", fake_exec):
        return drive, asyncio.run(coro)


def test_create_folder_with_parent():
    cog = _cog()
    drive, fid = _run(cog.create_folder("Hunt", "PARENT"), [{"id": "F1"}])
    assert fid == "F1"
    kw = drive.files().create.call_args.kwargs
    assert kw["body"] == {"name": "Hunt", "mimeType": FOLDER, "parents": ["PARENT"]}


def test_create_folder_without_parent_omits_parents():
    cog = _cog()
    drive, _ = _run(cog.create_folder("Hunt"), [{"id": "F1"}])
    assert drive.files().create.call_args.kwargs["body"] == {"name": "Hunt", "mimeType": FOLDER}


def test_copy_template_copies_into_folder_then_shares():
    cog = _cog()
    drive, fid = _run(cog.copy_template("Puz", "FOLD"), [{"id": "NEW"}, {}])
    assert fid == "NEW"
    kw = drive.files().copy.call_args.kwargs
    assert kw["fileId"] == config.master_spreadsheet
    assert kw["body"] == {"name": "Puz", "parents": ["FOLD"]}
    pk = drive.permissions().create.call_args.kwargs
    assert pk["fileId"] == "NEW"
    assert pk["body"] == {"type": "anyone", "role": "writer"}


def test_move_file_uses_add_and_remove_parents():
    cog = _cog()
    drive, _ = _run(cog.move_file("FILE", "TO", "FROM"))
    kw = drive.files().update.call_args.kwargs
    assert kw["fileId"] == "FILE"
    assert kw["addParents"] == "TO"
    assert kw["removeParents"] == "FROM"


def test_rename_file_sends_name_body():
    cog = _cog()
    drive, _ = _run(cog.rename_file("FILE", "New"))
    kw = drive.files().update.call_args.kwargs
    assert kw["fileId"] == "FILE"
    assert kw["body"] == {"name": "New"}


def test_delete_file_swallows_404():
    cog = _cog()
    drive, _ = _run(cog.delete_file("FILE"), raises=_http_error(404))
    assert drive.files().delete.call_args.kwargs == {"fileId": "FILE"}


def test_delete_file_raises_other_errors():
    cog = _cog()
    with pytest.raises(HttpError):
        _run(cog.delete_file("FILE"), raises=_http_error(403))


def test_get_file_requests_state_fields_and_returns_the_metadata():
    cog = _cog()
    meta = {"id": "F", "name": "n", "parents": ["P"], "trashed": False}
    drive, got = _run(cog.get_file("F"), [meta])
    assert got == meta
    drive.files().get.assert_called_once_with(fileId="F", fields="id,name,parents,trashed")


def test_get_file_404_is_none_and_other_errors_raise():
    cog = _cog()
    _, got = _run(cog.get_file("F"), raises=_http_error(404))
    assert got is None
    with pytest.raises(HttpError):
        _run(cog.get_file("F"), raises=_http_error(500))


def test_list_folder_follows_pages_and_excludes_trashed():
    cog = _cog()
    a = {"id": "1", "name": "a", "mimeType": "m"}
    b = {"id": "2", "name": "b", "mimeType": "m"}
    drive, items = _run(
        cog.list_folder("FOLD"),
        [{"files": [a], "nextPageToken": "T2"}, {"files": [b]}],
    )
    assert items == [a, b]
    calls = drive.files().list.call_args_list
    assert len(calls) == 2
    assert "pageToken" not in calls[0].kwargs
    assert calls[1].kwargs["pageToken"] == "T2"
    q = calls[0].kwargs["q"]
    assert "'FOLD' in parents" in q and "trashed=false" in q


def test_template_tabs_reads_once_and_caches(monkeypatch):
    monkeypatch.setattr(gsheet_mod, "_TEMPLATE_TABS", None)
    cog = _cog()
    calls = []

    async def fake_sheet_list(spreadsheet_id=None):
        calls.append(spreadsheet_id)
        return {"sheets": [
            {"properties": {"sheetId": 7, "title": "REFERENCE", "hidden": True}},
            {"properties": {"sheetId": 0, "title": "Overview"}},
        ]}

    cog.sheet_list = fake_sheet_list

    async def go():
        return await cog.template_tabs(), await cog.template_tabs()

    first, second = asyncio.run(go())
    assert calls == [config.master_spreadsheet]
    assert first == second == [
        {"sheetId": 7, "title": "REFERENCE", "hidden": True},
        {"sheetId": 0, "title": "Overview", "hidden": False},
    ]


def test_move_file_without_from_folder_looks_up_all_parents():
    cog = _cog()
    drive, _ = _run(cog.move_file("FILE", "TO"), responses=[{"parents": ["P1", "P2"]}, {}])
    assert drive.files().get.call_args.kwargs == {"fileId": "FILE", "fields": "parents"}
    kw = drive.files().update.call_args.kwargs
    assert kw["addParents"] == "TO"
    assert kw["removeParents"] == "P1,P2"


def test_move_file_with_from_folder_makes_no_lookup():
    cog = _cog()
    drive, _ = _run(cog.move_file("FILE", "TO", "FROM"))
    drive.files().get.assert_not_called()
