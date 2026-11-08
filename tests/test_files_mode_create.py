"""Files-mode puzzle, mini and extra sheet creation at the cog (Task 7).

The cog is built with __new__ and the module's get_sheet / execute_with_retry
are patched, so every Sheets call is recorded with its spreadsheet id and body;
Drive primitives (copy_template, delete_file) and template_tabs are AsyncMocks.
No real Google calls.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

import bot.cogs.puzzles_gsheet as gsheet_mod
from bot.cogs.puzzles_gsheet import GoogleSheets
from tests.conftest import make_hunt, make_puzzle

# The live master template's tabs (progress.md, Task 4 read-only check).
TEMPLATE_TABS = [
    {"sheetId": 1050059266, "title": "REFERENCE", "hidden": False},
    {"sheetId": 1971123506, "title": "Tab template", "hidden": True},
    {"sheetId": 42737759, "title": "Meta Tab template", "hidden": True},
    {"sheetId": 1354734589, "title": "OVERVIEW Template", "hidden": True},
    {"sheetId": 1378602762, "title": "Tool- NATO alphabet", "hidden": True},
    {"sheetId": 2034589729, "title": "Tool- Pokémon", "hidden": True},
    {"sheetId": 1625049102, "title": "Template- Word search with bigram finder", "hidden": True},
    {"sheetId": 1945881957, "title": " Tool- Periodic Table", "hidden": True},
]
TAB_TEMPLATE_ID = 1971123506
NEW_PAGE = 555


def _cog():
    cog = GoogleSheets.__new__(GoogleSheets)
    cog._notify_retry = None
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.get_solved_sheet_id = lambda: "SOLVED"
    cog.template_tabs = AsyncMock(return_value=[dict(t) for t in TEMPLATE_TABS])
    cog.copy_template = AsyncMock(return_value="NEWFILE")
    cog.delete_file = AsyncMock()
    return cog


class Recorder:
    """Patches get_sheet + execute_with_retry; records ("get"|"batch", id, body)."""

    def __init__(self, sheets_by_id=None, fail_on=None):
        self.calls = []
        self.sheets_by_id = sheets_by_id or {}
        self.fail_on = fail_on   # predicate(kind, id, body) -> raise HttpError
        api = MagicMock()

        def batch(spreadsheetId, body):
            return ("batch", spreadsheetId, body)

        def get(spreadsheetId, fields=None):
            return ("get", spreadsheetId, None)

        api.batchUpdate.side_effect = batch
        api.get.side_effect = get
        self.api = api

    async def execute(self, req, on_retry=None, retry_ambiguous=True):
        kind, sid, body = req
        self.calls.append(req)
        if self.fail_on and self.fail_on(kind, sid, body):
            raise HttpError(MagicMock(status=500, reason="x"), b"boom")
        if kind == "get":
            return {"sheets": self.sheets_by_id.get(sid, [])}
        replies = []
        for r in body["requests"]:
            if "addSheet" in r:
                replies.append({"addSheet": {"properties": {"sheetId": NEW_PAGE}}})
            elif "duplicateSheet" in r:
                replies.append({"duplicateSheet": {"properties": {"sheetId": 777}}})
            else:
                replies.append({})
        return {"replies": replies}

    def run(self, coro):
        with patch.object(gsheet_mod, "get_sheet", return_value=self.api), \
                patch.object(gsheet_mod, "execute_with_retry", self.execute):
            return asyncio.run(coro)

    @property
    def batches(self):
        return [c for c in self.calls if c[0] == "batch"]

    @property
    def gets(self):
        return [c for c in self.calls if c[0] == "get"]


def _files_hunt():
    return make_hunt(google_sheet_id="REF", solved_sheet_id="",
                     sheet_mode="files", drive_folder_id="FOLDER",
                     solved_folder_id="SOLVEDFOLDER")


def _sheet(sid, title, index, hidden=False):
    return {"properties": {"sheetId": sid, "title": title, "index": index,
                           "hidden": hidden}}


# --------------------------------------------------------- create_puzzle_file

def test_files_puzzle_copies_into_the_hunt_folder_and_stores_ids():
    cog = _cog()
    rec = Recorder()
    puzzle = make_puzzle(name="Clueless")
    rec.run(cog.create_puzzle_spreadsheet(puzzle, index=9, hunt=_files_hunt()))

    cog.copy_template.assert_awaited_once_with("Clueless", "FOLDER")
    assert puzzle.google_sheet_id == "NEWFILE"
    assert puzzle.google_page_id == NEW_PAGE
    assert puzzle.on_solved_sheet is False


def test_files_puzzle_budget_two_batches_no_read_all_in_the_new_file():
    cog = _cog()
    rec = Recorder()
    rec.run(cog.create_puzzle_spreadsheet(make_puzzle(name="Clueless"), index=9,
                                          hunt=_files_hunt()))

    assert rec.gets == []                       # no sheet_list read
    assert len(rec.batches) == 2                # addSheet + one header batch
    assert {c[1] for c in rec.batches} == {"NEWFILE"}
    add = rec.batches[0][2]["requests"]
    assert add == [{"addSheet": {"properties": {"title": "Clueless", "index": 0}}}]


def test_files_puzzle_header_batch_trims_template_and_allows_import_functions():
    cog = _cog()
    rec = Recorder()
    puzzle = make_puzzle(name="Clueless")
    rec.run(cog.create_puzzle_spreadsheet(puzzle, hunt=_files_hunt()))

    header = rec.batches[1][2]["requests"]
    deleted = {r["deleteSheet"]["sheetId"] for r in header if "deleteSheet" in r}
    assert deleted == {t["sheetId"] for t in TEMPLATE_TABS} - {TAB_TEMPLATE_ID}
    props = [r for r in header if "updateSpreadsheetProperties" in r]
    assert props == [{"updateSpreadsheetProperties": {
        "properties": {"importFunctionsExternalUrlAccessAllowed": True},
        "fields": "importFunctionsExternalUrlAccessAllowed"}}]
    # The same batch carries the tab's header (the name hyperlink on the new page).
    formulas = [r["updateCells"]["rows"][0]["values"]["userEnteredValue"].get("formulaValue")
                for r in header if "updateCells" in r
                and r["updateCells"]["range"]["sheetId"] == NEW_PAGE]
    assert any(f and "Clueless" in f for f in formulas)


def test_files_puzzle_tab_name_avoids_a_template_title():
    """The blank tab is added before the trim runs, so a puzzle named like a
    template tab must not collide with it."""
    cog = _cog()
    rec = Recorder()
    rec.run(cog.create_puzzle_spreadsheet(make_puzzle(name="REFERENCE"),
                                          hunt=_files_hunt()))
    add = rec.batches[0][2]["requests"][0]["addSheet"]["properties"]
    assert add["title"] == "REFERENCE (2)"


def test_files_puzzle_header_links_ride_in_the_header_batch():
    from bot.core.sheet_location import Link
    cog = _cog()
    rec = Recorder()
    links = [Link("Meta", "https://docs.google.com/spreadsheets/d/M/edit#gid=1")]
    rec.run(cog.create_puzzle_spreadsheet(make_puzzle(name="P"), header_links=links,
                                          hunt=_files_hunt()))
    header = rec.batches[1][2]["requests"]
    linked = [r for r in header if "updateCells" in r and "textFormatRuns"
              in str(r["updateCells"]["rows"])]
    assert linked, "Feeds line should be in the header batch"
    assert len(rec.batches) == 2


def test_files_puzzle_failed_header_batch_deletes_the_new_file_and_raises():
    cog = _cog()
    rec = Recorder(fail_on=lambda kind, sid, body: kind == "batch" and any(
        "deleteSheet" in r for r in body["requests"]))
    puzzle = make_puzzle(name="Clueless")
    with pytest.raises(HttpError):
        rec.run(cog.create_puzzle_spreadsheet(puzzle, hunt=_files_hunt()))
    cog.delete_file.assert_awaited_once_with("NEWFILE")
    assert puzzle.google_sheet_id == ""
    assert puzzle.google_page_id == ""


def test_files_puzzle_failed_copy_raises_without_deleting_anything():
    cog = _cog()
    cog.copy_template = AsyncMock(side_effect=HttpError(MagicMock(status=500), b"x"))
    rec = Recorder()
    puzzle = make_puzzle(name="Clueless")
    with pytest.raises(HttpError):
        rec.run(cog.create_puzzle_spreadsheet(puzzle, hunt=_files_hunt()))
    cog.delete_file.assert_not_awaited()
    assert rec.calls == []
    assert puzzle.google_sheet_id == ""


def test_copy_template_deletes_the_copy_when_sharing_fails():
    """copy_template is 1 copy + 1 permission; a failed permission must not
    leave an unshared copy behind (the caller never learns its id)."""
    cog = GoogleSheets.__new__(GoogleSheets)
    cog._notify_retry = None
    cog.delete_file = AsyncMock()
    drive = MagicMock()
    calls = []

    async def fake_exec(req, on_retry=None):
        calls.append(req)
        if len(calls) == 1:
            return {"id": "COPY1"}
        raise HttpError(MagicMock(status=500), b"perm")

    with patch.object(gsheet_mod, "get_drive", return_value=drive), \
            patch.object(gsheet_mod, "execute_with_retry", fake_exec):
        with pytest.raises(HttpError):
            asyncio.run(cog.copy_template("P", "FOLDER"))
    cog.delete_file.assert_awaited_once_with("COPY1")


# ---------------------------------------------------------- tabs: unchanged

@pytest.mark.parametrize("hunt", [None, make_hunt(google_sheet_id="LIVE")])
def test_tabs_create_makes_exactly_todays_calls(hunt):
    """hunt=None and a tabs hunt both take today's path: one read (unique tab
    name), addSheet at the given index, the header batch, then the
    update_puzzle_info batch -- all on the live spreadsheet; no Drive call."""
    cog = _cog()
    rec = Recorder(sheets_by_id={"LIVE": [_sheet(1, "Other", 0)]})
    puzzle = make_puzzle(name="Clueless")
    kw = {} if hunt is None else {"hunt": hunt}
    rec.run(cog.create_puzzle_spreadsheet(puzzle, index=9, **kw))

    assert [(c[0], c[1]) for c in rec.calls] == [
        ("get", "LIVE"), ("batch", "LIVE"), ("batch", "LIVE"), ("batch", "LIVE")]
    assert rec.calls[1][2] == {"requests": [{"addSheet": {"properties": {
        "title": "Clueless", "index": 9}}}]}
    assert not any("deleteSheet" in r or "updateSpreadsheetProperties" in r
                   for r in rec.calls[2][2]["requests"])
    last = rec.calls[3][2]["requests"]
    assert len(last) == 1 and "updateCells" in last[0]
    cog.copy_template.assert_not_awaited()
    cog.template_tabs.assert_not_awaited()
    assert puzzle.google_sheet_id == ""


# ------------------------------------------------------------------- minis

def test_files_mini_tab_goes_into_the_parents_file_with_no_drive_call():
    cog = _cog()
    rec = Recorder(sheets_by_id={"PARENTFILE": [_sheet(10, "Mini Meta", 0)]})
    mini = make_puzzle(name="Mini 1", parent_puzzle_id=3, puzzle_type="Mini")
    mini.google_sheet_id = "PARENTFILE"
    rec.run(cog.create_puzzle_spreadsheet(mini, index=1, hunt=_files_hunt()))

    cog.copy_template.assert_not_awaited()
    assert {c[1] for c in rec.calls} == {"PARENTFILE"}
    assert rec.calls[1][2]["requests"][0]["addSheet"]["properties"]["index"] == 1
    assert mini.google_sheet_id == "PARENTFILE"


def test_files_mini_without_a_parent_file_is_refused_before_any_call():
    cog = _cog()
    rec = Recorder()
    mini = make_puzzle(name="Mini 1", parent_puzzle_id=3, puzzle_type="Mini")
    with pytest.raises(RuntimeError, match="parent_sheet_missing"):
        rec.run(cog.create_puzzle_spreadsheet(mini, index=1, hunt=_files_hunt()))
    assert rec.calls == []
    cog.copy_template.assert_not_awaited()


def _mini(id, page, solved=False):
    m = make_puzzle(id=id, name=f"m{id}", parent_puzzle_id=3, solved=solved,
                    puzzle_type="Mini")
    m.google_page_id = page
    m.google_sheet_id = "PARENTFILE"
    return m


def _minimeta():
    mm = make_puzzle(id=3, name="Mini Meta", puzzle_type="Minimeta")
    mm.google_page_id = 100
    mm.google_sheet_id = "PARENTFILE"
    return mm


def test_mini_index_lands_before_the_first_solved_mini():
    """main, m1, m2, solved m3, solved m4 -> 3 (appending would give 5)."""
    cog = _cog()
    sheets = [_sheet(100, "Mini Meta", 0), _sheet(101, "m1", 1), _sheet(102, "m2", 2),
              _sheet(103, "m3", 3), _sheet(104, "m4", 4)]
    rec = Recorder(sheets_by_id={"PARENTFILE": sheets})
    minis = [_mini(1, 101), _mini(2, 102), _mini(4, 103, solved=True),
             _mini(5, 104, solved=True)]
    idx = rec.run(cog.mini_insert_index(_minimeta(), minis))
    assert idx == 3
    assert rec.gets == [("get", "PARENTFILE", None)]


def test_mini_index_without_a_solved_mini_follows_the_last_unsolved_one():
    """main, m1, m2, then the hidden Tab template -> 3 (appending gives 4,
    compute_insert_index's empty fallback gives 7)."""
    cog = _cog()
    sheets = [_sheet(100, "Mini Meta", 0), _sheet(101, "m1", 1), _sheet(102, "m2", 2),
              _sheet(TAB_TEMPLATE_ID, "Tab template", 3, hidden=True)]
    rec = Recorder(sheets_by_id={"PARENTFILE": sheets})
    idx = rec.run(cog.mini_insert_index(_minimeta(), [_mini(1, 101), _mini(2, 102)]))
    assert idx == 3


def test_first_mini_goes_right_after_the_minimetas_tab():
    cog = _cog()
    sheets = [_sheet(100, "Mini Meta", 0),
              _sheet(TAB_TEMPLATE_ID, "Tab template", 1, hidden=True)]
    rec = Recorder(sheets_by_id={"PARENTFILE": sheets})
    assert rec.run(cog.mini_insert_index(_minimeta(), [])) == 1


# ------------------------------------------------------------------ delete

def test_delete_own_file_deletes_the_file_not_a_tab():
    cog = _cog()
    cog.delete_sheet = AsyncMock()
    p = make_puzzle(name="P")
    p.google_sheet_id = "PFILE"
    p.google_page_id = 5
    asyncio.run(cog.delete_puzzle_spreadsheet(p))
    cog.delete_file.assert_awaited_once_with("PFILE")
    cog.delete_sheet.assert_not_awaited()


def test_delete_mini_deletes_its_tab_never_the_minimetas_file():
    cog = _cog()
    cog.delete_sheet = AsyncMock()
    mini = _mini(1, 101)
    asyncio.run(cog.delete_puzzle_spreadsheet(mini))
    cog.delete_sheet.assert_awaited_once_with(101, "PARENTFILE")
    cog.delete_file.assert_not_awaited()


@pytest.mark.parametrize("on_solved,expected", [(False, "LIVE"), (True, "SOLVED")])
def test_delete_tab_puzzle_is_unchanged(on_solved, expected):
    cog = _cog()
    cog.delete_sheet = AsyncMock()
    p = make_puzzle(name="P")
    p.google_page_id = 5
    p.on_solved_sheet = on_solved
    asyncio.run(cog.delete_puzzle_spreadsheet(p))
    cog.delete_sheet.assert_awaited_once_with(5, expected)
    cog.delete_file.assert_not_awaited()


# ------------------------------------------------------------------ extras

def test_extra_in_a_files_puzzle_duplicates_that_files_own_template():
    cog = _cog()
    pfile = [_sheet(NEW_PAGE, "Clueless", 0),
             _sheet(TAB_TEMPLATE_ID, "Tab template", 1, hidden=True)]
    live = [_sheet(999, "Tab template", 3, hidden=True)]
    rec = Recorder(sheets_by_id={"PFILE": pfile, "LIVE": live})
    p = make_puzzle(name="Clueless")
    p.google_sheet_id = "PFILE"
    p.google_page_id = NEW_PAGE
    page = rec.run(cog.create_additional_spreadsheet(p, "Grid"))

    assert page == 777
    assert {c[1] for c in rec.calls} == {"PFILE"}
    dup = next(r["duplicateSheet"] for c in rec.batches for r in c[2]["requests"]
               if "duplicateSheet" in r)
    assert dup["sourceSheetId"] == TAB_TEMPLATE_ID
    assert dup["insertSheetIndex"] == 1


# ----------------------------------------------------------- service sanity

def test_hunt_without_sheet_mode_attribute_takes_the_tabs_path():
    """Some callers hand the cog a SimpleNamespace hunt; no sheet_mode = tabs."""
    cog = _cog()
    rec = Recorder(sheets_by_id={"LIVE": []})
    rec.run(cog.create_puzzle_spreadsheet(make_puzzle(name="P"), index=7,
                                          hunt=SimpleNamespace(google_sheet_id="LIVE")))
    cog.copy_template.assert_not_awaited()


# ------------------------------------------------- edges: bang and web errors

def _http_error():
    return HttpError(MagicMock(status=500, reason="x"), b"copy failed")


@pytest.mark.asyncio
async def test_bang_create_reports_a_sheet_failure_and_leaves_nothing_behind():
    """!p in a files hunt whose copy fails: a clear "couldn't create the sheet"
    reply (not the server-full one), and no row and no channel."""
    from bot.cogs.puzzles import Puzzles
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = AsyncMock(side_effect=_http_error())
    gsheet.compute_insert_index = AsyncMock(return_value=0)
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    cog = Puzzles(bot)
    cog.check_available_space = AsyncMock(return_value=True)
    cog.service._maybe_relieve_overflow = AsyncMock()
    hunt = _files_hunt()
    guild = MagicMock()
    guild.channels = []
    ctx = MagicMock()
    ctx.message.id = 770001
    ctx.send = AsyncMock()
    ctx.guild = guild
    ctx.env = None
    cog.environment[ctx.message.id] = {
        "channel_type": "Hunt", "hunt": hunt, "tag_group": None, "puzzle": None,
        "guild": guild, "guild_data": None, "gsheet_cog": None}
    goc = AsyncMock()
    with patch("bot.cogs.puzzles.PuzzleJsonDb") as cog_pdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.category_layout.pick_unsolved_category",
               AsyncMock(return_value=MagicMock(id=999, channels=[]))), \
         patch("bot.core.puzzle_service.get_or_create_channel", goc):
        cog_pdb.check_duplicates_in_hunt.return_value = False
        await Puzzles.puzzle.callback(cog, ctx, arg="Clueless")

    msg = ctx.send.await_args.args[0]
    assert "couldn't create" in msg.lower() and "sheet" in msg.lower()
    assert "server full" not in msg
    pdb.commit.assert_not_called()
    goc.assert_not_awaited()


@pytest.fixture
def web():
    from tests.conftest import (make_hunt_ctx, make_web_app, start_patches,
                                stop_patches)
    cog = MagicMock()
    cog.service.create_puzzle = AsyncMock()
    cog.service.create_mini = AsyncMock()
    cog.service.create_tag_group = AsyncMock()
    cog.service.refresh_unassigned_safe = AsyncMock()
    cog.web_refresh_info = AsyncMock()
    app = make_web_app("actions", cog=cog)
    ctx = make_hunt_ctx()
    minimeta = make_puzzle(id=3, puzzle_type="Minimeta")
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._visible_hunt_for_puzzle",
              AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.HuntJsonDb"),
        patch("bot.store.PuzzleJsonDb"),
        patch("bot.store.TagGroupJsonDb"),
    ]
    started = start_patches(patches)
    hdb, pdb = started[4], started[5]
    hdb.get_by_attr.return_value = _files_hunt()
    pdb.check_duplicates_in_hunt.return_value = False
    pdb.get_by_attr.return_value = minimeta
    try:
        yield SimpleNamespace(client=app.test_client(), svc=cog.service)
    finally:
        stop_patches(patches)


@pytest.mark.asyncio
async def test_web_create_puzzle_sheet_failure_is_a_clean_error(web):
    web.svc.create_puzzle.side_effect = _http_error()
    resp = await web.client.post("/api/hunts/U1/puzzles", json={"name": "P"})
    assert resp.status_code == 502
    assert (await resp.get_json())["error"] == "sheet_create_failed"


@pytest.mark.asyncio
async def test_web_create_mini_sheet_failure_is_a_clean_error(web):
    web.svc.create_mini.side_effect = _http_error()
    resp = await web.client.post("/api/puzzles/3/minis", json={"name": "m"})
    assert resp.status_code == 502
    assert (await resp.get_json())["error"] == "sheet_create_failed"


@pytest.mark.asyncio
async def test_web_create_mini_without_a_parent_file_returns_the_slug(web):
    web.svc.create_mini.side_effect = RuntimeError("parent_sheet_missing")
    resp = await web.client.post("/api/puzzles/3/minis", json={"name": "m"})
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "parent_sheet_missing"


@pytest.mark.asyncio
async def test_bang_add_mini_without_a_parent_file_points_at_reconcile():
    from bot.cogs.puzzles import Puzzles
    cog = Puzzles(MagicMock())
    minimeta = make_puzzle(id=3, puzzle_type="Minimeta")
    cog.get_puzzle = MagicMock(return_value=minimeta)
    cog.get_hunt = MagicMock(return_value=_files_hunt())
    cog.service.create_mini = AsyncMock(side_effect=RuntimeError("parent_sheet_missing"))
    ctx = MagicMock()
    ctx.message.id = 770002
    ctx.send = AsyncMock()
    ctx.env = None
    await Puzzles.add_mini.callback(cog, ctx, "Tiny")
    msg = ctx.send.await_args.args[0]
    assert "!reconcile apply" in msg


# ------------------------------------- reconcile recreation (files mode index)

def _reconcile_gcog():
    from bot.core.sheet_location import spreadsheet_for
    g = MagicMock()
    g.spreadsheet_of = lambda obj: spreadsheet_for(obj, "REF", "")
    g.get_page_name_by_id = AsyncMock(return_value=None)   # tab gone
    g.add_new_puzzle_sheet = AsyncMock()
    g.update_puzzle_info = AsyncMock()
    g.compute_insert_index = AsyncMock(return_value=999)
    g.mini_insert_index = AsyncMock(return_value=2)
    return g


def _reconcile_svc(gcog):
    from bot.core.puzzle_service import PuzzleService
    bot = MagicMock()
    bot.get_cog.return_value = gcog
    return PuzzleService(bot, notifier=AsyncMock())


def test_reconcile_recreates_a_files_mini_tab_by_the_mini_rule():
    gcog = _reconcile_gcog()
    minimeta = _minimeta()
    lost = _mini(1, 101)
    sibling = _mini(2, 102)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb:
        pdb.get_all_from_hunt.return_value = [minimeta, lost, sibling]
        asyncio.run(_reconcile_svc(gcog)._reconcile_tabs(_files_hunt(), lost, apply=True))
    gcog.mini_insert_index.assert_awaited_once_with(minimeta, [sibling])
    gcog.compute_insert_index.assert_not_awaited()
    gcog.add_new_puzzle_sheet.assert_awaited_once_with(lost, 2)


def test_reconcile_recreates_an_own_file_tab_first_in_its_file():
    gcog = _reconcile_gcog()
    p = make_puzzle(id=8, name="P")
    p.google_sheet_id = "PFILE"
    p.google_page_id = 5
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb:
        pdb.get_all_from_hunt.return_value = [p]
        asyncio.run(_reconcile_svc(gcog)._reconcile_tabs(_files_hunt(), p, apply=True))
    gcog.get_page_name_by_id.assert_awaited_once_with(5, "PFILE")
    gcog.compute_insert_index.assert_not_awaited()
    gcog.add_new_puzzle_sheet.assert_awaited_once_with(p, 0)


def test_mini_index_keeps_an_unsolved_minis_extra_beside_it():
    """main, m1, m2, m2's extra, solved m3 -> 4: before the first solved mini,
    not straight after m2 (3), which would split m2 from its extra."""
    cog = _cog()
    sheets = [_sheet(100, "Mini Meta", 0), _sheet(101, "m1", 1), _sheet(102, "m2", 2),
              _sheet(150, "Grid (m2)", 3), _sheet(103, "m3", 4)]
    rec = Recorder(sheets_by_id={"PARENTFILE": sheets})
    minis = [_mini(1, 101), _mini(2, 102), _mini(4, 103, solved=True)]
    assert rec.run(cog.mini_insert_index(_minimeta(), minis)) == 4


# ------------------------- fix round 1: stale template cache, hunt-sheet guard

def _real_template_cog(monkeypatch, cached, master_now):
    """A cog whose template_tabs is the real cached reader: the process cache
    holds `cached`; a re-read of the master returns `master_now`."""
    from bot.utils import config
    # Pin the id: CI's config.json has no master_spreadsheet, and sheet_list(None)
    # reads the hunt's own spreadsheet instead of the fake master below.
    monkeypatch.setattr(config, "master_spreadsheet", "MASTER")
    cog = _cog()
    del cog.template_tabs                 # use the real method
    monkeypatch.setattr(gsheet_mod, "_TEMPLATE_TABS", [dict(t) for t in cached])
    master = [_sheet(t["sheetId"], t["title"], i, t["hidden"])
              for i, t in enumerate(master_now)]
    return cog, {config.master_spreadsheet: master}


def _header_batches(rec):
    return [c for c in rec.batches
            if any("updateSpreadsheetProperties" in r for r in c[2]["requests"])]


def test_stale_template_cache_is_reread_and_the_header_batch_retried_once(monkeypatch):
    """A tab removed from the master mid-run: the cached trim deletes an id the
    copy lacks, so Google rejects the (atomic) batch. Re-read and retry once."""
    gone = 2034589729   # "Tool- Pokémon", removed from the master
    now = [t for t in TEMPLATE_TABS if t["sheetId"] != gone]
    cog, master = _real_template_cog(monkeypatch, TEMPLATE_TABS, now)
    rec = Recorder(sheets_by_id=master, fail_on=lambda kind, sid, body: kind == "batch"
                   and any(r.get("deleteSheet", {}).get("sheetId") == gone
                           for r in body["requests"]))
    puzzle = make_puzzle(name="Clueless")
    rec.run(cog.create_puzzle_spreadsheet(puzzle, hunt=_files_hunt()))

    headers = _header_batches(rec)
    assert len(headers) == 2                                   # exactly one retry
    retried = {r["deleteSheet"]["sheetId"] for r in headers[1][2]["requests"]
               if "deleteSheet" in r}
    assert retried == {t["sheetId"] for t in now} - {TAB_TEMPLATE_ID}
    assert [c for c in rec.batches if any("addSheet" in r for r in c[2]["requests"])] \
        == [rec.batches[0]]                                    # no second tab
    assert all(c[1] == "NEWFILE" for c in rec.batches)
    cog.delete_file.assert_not_awaited()                       # file kept
    assert puzzle.google_sheet_id == "NEWFILE"
    assert puzzle.google_page_id == NEW_PAGE
    assert [t["sheetId"] for t in gsheet_mod._TEMPLATE_TABS] == [t["sheetId"] for t in now]


def test_a_retry_that_also_fails_deletes_the_copy_and_drops_the_cache(monkeypatch):
    cog, master = _real_template_cog(monkeypatch, TEMPLATE_TABS, TEMPLATE_TABS)
    rec = Recorder(sheets_by_id=master, fail_on=lambda kind, sid, body: kind == "batch"
                   and any("deleteSheet" in r for r in body["requests"]))
    puzzle = make_puzzle(name="Clueless")
    with pytest.raises(HttpError):
        rec.run(cog.create_puzzle_spreadsheet(puzzle, hunt=_files_hunt()))

    assert len(_header_batches(rec)) == 2                      # one retry, no more
    cog.delete_file.assert_awaited_once_with("NEWFILE")
    assert puzzle.google_sheet_id == "" and puzzle.google_page_id == ""
    assert gsheet_mod._TEMPLATE_TABS is None                   # next create re-reads


def test_a_failed_addsheet_is_not_retried(monkeypatch):
    cog, master = _real_template_cog(monkeypatch, TEMPLATE_TABS, TEMPLATE_TABS)
    rec = Recorder(sheets_by_id=master, fail_on=lambda kind, sid, body: kind == "batch"
                   and any("addSheet" in r for r in body["requests"]))
    with pytest.raises(HttpError):
        rec.run(cog.create_puzzle_spreadsheet(make_puzzle(name="P"), hunt=_files_hunt()))
    assert len(rec.batches) == 1
    assert rec.gets == []                                      # no template re-read
    cog.delete_file.assert_awaited_once_with("NEWFILE")


@pytest.mark.parametrize("hunt_sheet", ["LIVE", "SOLVED"])
def test_delete_never_takes_a_hunt_level_spreadsheet(hunt_sheet):
    """A row whose own-file id is the hunt's reference (live) or solved
    spreadsheet is corrupt; deleting it would destroy the hunt's sheet."""
    cog = _cog()
    cog.delete_sheet = AsyncMock()
    p = make_puzzle(name="P")
    p.google_sheet_id = hunt_sheet
    p.google_page_id = 5
    asyncio.run(cog.delete_puzzle_spreadsheet(p))
    cog.delete_file.assert_not_awaited()
    cog.delete_sheet.assert_not_awaited()
