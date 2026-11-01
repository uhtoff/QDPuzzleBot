"""The files-hunt sheet-edit poller: one Drive listing per hunt folder, human
(and anonymous) edits marked, the bot's own edits ignored, failures contained."""
import asyncio
import datetime
from unittest.mock import MagicMock, patch

from bot.cogs.sheet_activity import SheetActivity

SHEET = "application/vnd.google-apps.spreadsheet"
NOW = datetime.datetime(2026, 11, 1, 12, 30, tzinfo=datetime.timezone.utc)


class _FakeSheets:
    def __init__(self, by_folder, fail=()):
        self.by_folder, self.fail, self.calls = by_folder, set(fail), []

    async def list_modified_since(self, folder_id, since):
        self.calls.append((folder_id, since))
        if folder_id in self.fail:
            raise RuntimeError("drive down")
        return self.by_folder.get(folder_id, [])


def _poll(folders, sheets):
    bot = MagicMock()
    bot.get_cog.return_value = sheets
    db = MagicMock()
    db.files_hunt_folders.return_value = folders
    cog = SheetActivity(bot)
    with patch("bot.cogs.sheet_activity.SheetActivityDb", db):
        asyncio.run(cog.poll_once(NOW))
    bot.get_cog.assert_called_with("GoogleSheets")
    return db


def test_marks_human_and_anonymous_edits_not_bot_edits():
    files = [
        {"id": "HUMAN", "mimeType": SHEET, "modifiedTime": "2026-11-01T12:21:05.123Z",
         "lastModifyingUser": {"me": False}},
        {"id": "ANON", "mimeType": SHEET, "modifiedTime": "2026-11-01T12:26:00.000Z"},
        {"id": "BOT", "mimeType": SHEET, "modifiedTime": "2026-11-01T12:27:00.000Z",
         "lastModifyingUser": {"me": True}},
        {"id": "DOC", "mimeType": "application/pdf", "modifiedTime": "2026-11-01T12:27:00.000Z"},
    ]
    db = _poll([(1, "F1")], _FakeSheets({"F1": files}))
    marked = [c.args for c in db.mark_sheet_edited.call_args_list]
    utc = datetime.timezone.utc
    assert marked == [
        ("HUMAN", datetime.datetime(2026, 11, 1, 12, 21, 5, 123000, tzinfo=utc)),
        ("ANON", datetime.datetime(2026, 11, 1, 12, 26, tzinfo=utc)),
    ]


def test_looks_back_ten_minutes():
    sheets = _FakeSheets({})
    _poll([(1, "F1")], sheets)
    assert sheets.calls == [("F1", NOW - datetime.timedelta(minutes=10))]


def test_one_hunt_failing_does_not_stop_the_next():
    files = [{"id": "S2", "mimeType": SHEET, "modifiedTime": "2026-11-01T12:25:00Z"}]
    sheets = _FakeSheets({"F2": files}, fail={"F1"})
    db = _poll([(1, "F1"), (2, "F2")], sheets)
    assert [c.args[0] for c in db.mark_sheet_edited.call_args_list] == ["S2"]
    assert [f for f, _ in sheets.calls] == ["F1", "F2"]


def test_no_files_hunts_makes_no_drive_calls():
    sheets = _FakeSheets({})
    db = _poll([], sheets)
    assert sheets.calls == []
    db.mark_sheet_edited.assert_not_called()


def test_missing_gsheet_cog_skips_the_tick():
    bot = MagicMock()
    bot.get_cog.return_value = None
    db = MagicMock()
    db.files_hunt_folders.return_value = [(1, "F1")]
    with patch("bot.cogs.sheet_activity.SheetActivityDb", db):
        asyncio.run(SheetActivity(bot).poll_once(NOW))
    db.mark_sheet_edited.assert_not_called()
