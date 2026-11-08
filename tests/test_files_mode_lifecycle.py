"""Files-mode solve, unsolve, rename and delete (Task 8).

A files hunt has one spreadsheet per puzzle in a Drive folder with a "Solved"
subfolder. Solving a puzzle with its own file writes today's header (solution,
"Solved", green cell, green tab) IN THAT FILE and then moves the file into the
Solved folder -- no copyTo, no new page id, on_solved_sheet stays False, so
every link to it holds. A mini's tab lives in its minimeta's file: it is
greened and moved to the end of that file, with no Drive call. Unsolve reverses
each. Rename renames the file too (own file only); delete removes the file (own
file) or the mini's tab in the parent's file.

Cog-level tests use the real cog with get_sheet / execute_with_retry patched
(tests.test_files_mode_create.Recorder); Drive primitives are AsyncMocks that
append to the same call log, so order is readable. No real Google calls.
"""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.sheet_location import puzzle_location
from bot.store.puzzle_data import AdditionalSheetData
from tests.conftest import make_hunt, make_puzzle, make_round
from tests.test_files_mode_create import Recorder, _cog, _files_hunt, _sheet

GREEN = {"red": 0.0, "green": 0.85, "blue": 0.0}
GREY = {"red": 0.851, "green": 0.851, "blue": 0.851}


# ------------------------------------------------------------------ helpers

def _own(id=1, name="Clueless", page=11, solved=False, solution="ANSWER", **kw):
    p = make_puzzle(id=id, name=name, solved=solved, solution=solution, **kw)
    p.google_sheet_id = f"FILE{id}"
    p.google_page_id = page
    return p


def _extra(id, page, file_id):
    return AdditionalSheetData(id=id, google_page_id=str(page), puzzle_id=1,
                               google_sheet_id=file_id)


def _mini(id=41, page=410, parent=40, file_id="MMFILE", solved=False, solution="X"):
    m = make_puzzle(id=id, name=f"Mini {id}", parent_puzzle_id=parent,
                    puzzle_type="Mini", solved=solved, solution=solution)
    m.google_sheet_id = file_id
    m.google_page_id = page
    return m


def _logged_cog(rec):
    """The real cog, with Drive primitives logging into the Recorder's calls."""
    cog = _cog()

    async def move_file(file_id, to_folder, from_folder=None):
        rec.calls.append(("move_file", file_id, (to_folder, from_folder)))
        return [from_folder]
    cog.move_file = AsyncMock(side_effect=move_file)

    async def rename_file(file_id, name):
        rec.calls.append(("rename_file", file_id, name))
    cog.rename_file = AsyncMock(side_effect=rename_file)
    return cog


def _values(body):
    """{(sheetId, column): value} for every updateCells in a batch body."""
    out = {}
    for r in body["requests"]:
        if "updateCells" in r:
            rng = r["updateCells"]["range"]
            val = r["updateCells"]["rows"][0]["values"]["userEnteredValue"]
            out[(str(rng["sheetId"]), rng["startColumnIndex"])] = next(iter(val.values()))
    return out


def _props(body, key):
    """[(sheetId, value)] for every updateSheetProperties setting `key`."""
    return [(str(r["updateSheetProperties"]["properties"]["sheetId"]),
             r["updateSheetProperties"]["properties"][key])
            for r in body["requests"]
            if "updateSheetProperties" in r
            and r["updateSheetProperties"]["fields"] == key]


def _bgs(body):
    return [(str(r["repeatCell"]["range"]["sheetId"]),
             r["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"])
            for r in body["requests"] if "repeatCell" in r]


# solution is I1 (col 8), progress is B3 (col 1); a target's own tab shifts by 1.
SOL, PROG = 8, 1


# ------------------------------------------------------- solve: own file

def test_files_solve_writes_the_header_in_its_file_then_moves_the_file_once():
    rec = Recorder()
    cog = _logged_cog(rec)
    puzzle = _own(solution="ANSWER")
    rec.run(cog.move_puzzle_to_solved_sheet(puzzle, hunt=_files_hunt()))

    kinds = [c[0] for c in rec.calls]
    assert kinds == ["batch", "move_file"]          # header first, then ONE move
    _, sid, body = rec.calls[0]
    assert sid == "FILE1"                           # the puzzle's own file
    vals = _values(body)
    assert vals[("11", SOL)] == "ANSWER"
    assert vals[("11", PROG)] == "Solved"
    assert _bgs(body) == [("11", GREEN)]
    assert _props(body, "tabColor") == [("11", {"green": 1})]
    assert _props(body, "index") == []              # the file moves, not its tab
    assert rec.calls[1] == ("move_file", "FILE1", ("SOLVEDFOLDER", "FOLDER"))
    rec.api.sheets.return_value.copyTo.assert_not_called()
    # Same file, same page, no solved-spreadsheet flag: every link holds.
    assert (puzzle.google_sheet_id, puzzle.google_page_id) == ("FILE1", 11)
    assert puzzle.on_solved_sheet is False


def test_files_solve_extras_ride_along_inside_the_file():
    rec = Recorder()
    cog = _logged_cog(rec)
    puzzle = _own()
    puzzle.additional_sheets = [_extra(7, 12, "FILE1"), _extra(8, 13, "FILE1")]
    rec.run(cog.move_puzzle_to_solved_sheet(puzzle, hunt=_files_hunt()))

    batches = rec.batches
    assert len(batches) == 1 and batches[0][1] == "FILE1"
    vals = _values(batches[0][2])
    for page in ("12", "13"):
        assert vals[(page, SOL)] == "ANSWER" and vals[(page, PROG)] == "Solved"
    assert sorted(_props(batches[0][2], "tabColor")) == [
        ("11", {"green": 1}), ("12", {"green": 1}), ("13", {"green": 1})]
    # One Drive move for the whole file; the extras keep their ids and flags.
    assert [c for c in rec.calls if c[0] == "move_file"] == [
        ("move_file", "FILE1", ("SOLVEDFOLDER", "FOLDER"))]
    assert [(s.google_page_id, s.on_solved_sheet) for s in puzzle.additional_sheets] == [
        ("12", False), ("13", False)]


def test_files_target_solve_moves_too_with_the_shifted_header():
    rec = Recorder()
    cog = _logged_cog(rec)
    target = _own(id=2, name="Meta", page=21, is_target=True, solution="META")
    rec.run(cog.move_puzzle_to_solved_sheet(target, hunt=_files_hunt()))

    vals = _values(rec.batches[0][2])
    assert vals[("21", SOL + 1)] == "META" and vals[("21", PROG + 1)] == "Solved"
    assert [c for c in rec.calls if c[0] == "move_file"] == [
        ("move_file", "FILE2", ("SOLVEDFOLDER", "FOLDER"))]
    assert target.on_solved_sheet is False


def test_files_solve_with_a_failed_folder_move_keeps_the_header_and_does_not_raise(caplog):
    """Chosen behaviour: the header (the visible solve) is written; a Drive
    move failure is logged, not raised, so the solve is not reported as a sheet
    failure. !reconcile apply's file_move (Task 9) puts the file in Solved."""
    rec = Recorder()
    cog = _logged_cog(rec)
    cog.move_file = AsyncMock(side_effect=RuntimeError("Drive 500"))
    puzzle = _own()
    with caplog.at_level(logging.ERROR):
        rec.run(cog.move_puzzle_to_solved_sheet(puzzle, hunt=_files_hunt()))
    assert len(rec.batches) == 1
    cog.move_file.assert_awaited_once()
    assert "FILE1" in caplog.text


def test_files_solve_header_failure_still_raises_before_any_move():
    rec = Recorder(fail_on=lambda kind, sid, body: kind == "batch")
    cog = _logged_cog(rec)
    with pytest.raises(Exception):
        rec.run(cog.move_puzzle_to_solved_sheet(_own(), hunt=_files_hunt()))
    cog.move_file.assert_not_awaited()


# ------------------------------------------------------------ solve: mini

def test_files_mini_solve_greens_and_moves_to_the_end_of_the_parents_file_no_drive():
    rec = Recorder(sheets_by_id={"MMFILE": [
        _sheet(400, "Mini Meta", 0), _sheet(410, "Mini 41", 1),
        _sheet(420, "Mini 42", 2), _sheet(99, "Tab template", 3, hidden=True)]})
    cog = _logged_cog(rec)
    mini = _mini(solution="X")
    rec.run(cog.move_puzzle_to_solved_sheet(mini, hunt=_files_hunt()))

    assert [c[0] for c in rec.calls] == ["get", "batch"]
    assert rec.gets[0][1] == "MMFILE" and rec.batches[0][1] == "MMFILE"
    body = rec.batches[0][2]
    vals = _values(body)
    assert vals[("410", SOL)] == "X" and vals[("410", PROG)] == "Solved"
    assert _bgs(body) == [("410", GREEN)]
    assert _props(body, "index") == [("410", 4)]      # len(sheets): truly last
    assert _props(body, "tabColor") == [("410", {"green": 1})]
    cog.move_file.assert_not_awaited()
    rec.api.sheets.return_value.copyTo.assert_not_called()
    assert (mini.google_sheet_id, mini.google_page_id, mini.on_solved_sheet) == (
        "MMFILE", 410, False)


def test_files_minimeta_solve_moves_its_file_and_leaves_the_minis_tabs_alone():
    rec = Recorder()
    cog = _logged_cog(rec)
    minimeta = _own(id=40, name="Mini Meta", page=400, puzzle_type="Minimeta")
    minimeta.is_minimeta = True
    rec.run(cog.move_puzzle_to_solved_sheet(minimeta, hunt=_files_hunt()))
    body = rec.batches[0][2]
    assert {k[0] for k in _values(body)} == {"400"}     # only the minimeta's tab
    assert _props(body, "index") == []                  # not moved after its minis
    assert [c for c in rec.calls if c[0] == "move_file"] == [
        ("move_file", "FILE40", ("SOLVEDFOLDER", "FOLDER"))]


# ------------------------------------------------------- unsolve: own file

def test_files_unsolve_clears_the_header_in_its_file_then_moves_the_file_back():
    rec = Recorder()
    cog = _logged_cog(rec)
    puzzle = _own(solved=False, solution="")
    puzzle.additional_sheets = [_extra(7, 12, "FILE1")]
    rec.run(cog.restore_puzzle_from_solved_sheet(puzzle, hunt=_files_hunt()))

    assert [c[0] for c in rec.calls] == ["batch", "move_file"]
    _, sid, body = rec.calls[0]
    assert sid == "FILE1"
    vals = _values(body)
    assert vals[("11", SOL)] == "" and vals[("11", PROG)] == ""
    assert vals[("12", SOL)] == "" and vals[("12", PROG)] == ""
    assert _bgs(body) == [("11", GREY)]
    assert sorted(_props(body, "tabColor")) == [("11", None), ("12", None)]
    assert _props(body, "index") == []
    assert rec.calls[1] == ("move_file", "FILE1", ("FOLDER", "SOLVEDFOLDER"))
    rec.api.sheets.return_value.copyTo.assert_not_called()
    assert (puzzle.google_sheet_id, puzzle.google_page_id, puzzle.on_solved_sheet) == (
        "FILE1", 11, False)


def test_files_unsolve_with_a_failed_folder_move_does_not_raise(caplog):
    rec = Recorder()
    cog = _logged_cog(rec)
    cog.move_file = AsyncMock(side_effect=RuntimeError("Drive 500"))
    with caplog.at_level(logging.ERROR):
        rec.run(cog.restore_puzzle_from_solved_sheet(_own(), hunt=_files_hunt()))
    assert len(rec.batches) == 1
    assert "FILE1" in caplog.text


# ---------------------------------------------------------- unsolve: mini

def test_files_mini_unsolve_ungreens_and_moves_to_the_given_index_no_drive():
    rec = Recorder()
    cog = _logged_cog(rec)
    mini = _mini(solution="")
    rec.run(cog.restore_puzzle_from_solved_sheet(mini, restore_index=3,
                                                 hunt=_files_hunt()))
    assert [c[0] for c in rec.calls] == ["batch"]
    body = rec.batches[0][2]
    assert rec.batches[0][1] == "MMFILE"
    assert _bgs(body) == [("410", GREY)]
    assert _props(body, "index") == [("410", 3)]
    assert _props(body, "tabColor") == [("410", None)]
    cog.move_file.assert_not_awaited()


# -------------------------------------------------------- service: solve/unsolve

def _svc_with(cog):
    from bot.core.puzzle_service import PuzzleService
    bot = MagicMock()
    bot.get_cog.return_value = cog
    svc = PuzzleService(bot)
    svc.notifier = MagicMock(board_changed=AsyncMock())
    svc.refresh_target = AsyncMock()
    svc.refresh_minimeta = AsyncMock()
    svc.refresh_header_links = AsyncMock()
    return svc


@patch("bot.core.puzzle_service.SheetsJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_target_solve_through_the_service_moves_its_file_and_feeders_links_hold(
        PuzzleDb, TagGroupDb, SheetsDb):
    """A target with two feeders: solving it moves its file, writes nothing to
    either feeder's tab, and the link both feeders' Feeds lines carry is the
    same string before and after."""
    rec = Recorder()
    cog = _logged_cog(rec)
    hunt = _files_hunt()
    target = _own(id=2, name="Meta", page=21, is_target=True, tags=[5])
    feeders = [_own(id=3, name="F1", page=31, tags=[5]),
               _own(id=4, name="F2", page=41, tags=[5])]
    for f in feeders:
        f.tag_edges = {5: {"feeds"}}
    group = SimpleNamespace(id=5, target_puzzle_id=2, solve_time=None, status="")
    TagGroupDb.get_by_attr.side_effect = lambda **kw: group
    PuzzleDb.get_children.return_value = []
    svc = _svc_with(cog)
    from bot.core.puzzle_service import feeds_targets
    # Each feeder's own Feeds-line links, computed from that feeder.
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as GroupDb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as PDb:
        GroupDb.get_by_attr.side_effect = lambda **kw: group
        PDb.get_by_attr.side_effect = lambda **kw: target
        before = {f.name: svc._links_to(hunt, feeds_targets(f)) for f in feeders}

        assert rec.run(svc.solve(None, hunt, target, "META")) is True

        after = {f.name: svc._links_to(hunt, feeds_targets(f)) for f in feeders}
    url = puzzle_location(hunt, target).url
    assert "FILE2" in url
    for f in feeders:
        assert [l.uri for l in before[f.name]] == [url] == [l.uri for l in after[f.name]]
    assert [c for c in rec.calls if c[0] == "move_file"] == [
        ("move_file", "FILE2", ("SOLVEDFOLDER", "FOLDER"))]
    assert {c[1] for c in rec.batches} == {"FILE2"}     # no write to FILE3/FILE4


@patch("bot.core.puzzle_service.SheetsJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_service_solve_with_a_failed_folder_move_is_not_a_sheet_failure(
        PuzzleDb, TagGroupDb, SheetsDb):
    rec = Recorder()
    cog = _logged_cog(rec)
    cog.move_file = AsyncMock(side_effect=RuntimeError("Drive 500"))
    PuzzleDb.get_children.return_value = []
    puzzle = _own()
    svc = _svc_with(cog)
    assert rec.run(svc.solve(None, _files_hunt(), puzzle, "ANSWER")) is True  # no raise
    assert puzzle.solved is True
    PuzzleDb.commit.assert_called_with(puzzle)


@patch("bot.core.puzzle_service.SheetsJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_service_unsolve_of_a_mini_restores_before_the_first_solved_mini(
        PuzzleDb, TagGroupDb, SheetsDb):
    """Two other minis -- 42 unsolved, 43 solved -- and the unsolving 41 at the
    end: 41 lands at 43's index (before the first solved mini), by the Task 7
    rule, with no read of the hunt's reference spreadsheet."""
    rec = Recorder(sheets_by_id={"MMFILE": [
        _sheet(400, "Mini Meta", 0), _sheet(420, "Mini 42", 1),
        _sheet(430, "Mini 43", 2), _sheet(410, "Mini 41", 3)]})
    cog = _logged_cog(rec)
    minimeta = _own(id=40, name="Mini Meta", page=400, puzzle_type="Minimeta")
    minimeta.google_sheet_id = "MMFILE"
    mini = _mini(id=41, page=410, solved=True, solution="X")
    others = [_mini(id=42, page=420), _mini(id=43, page=430, solved=True)]
    PuzzleDb.get_by_attr.side_effect = lambda id: minimeta if id == 40 else None
    PuzzleDb.get_children.side_effect = lambda pid: [mini, *others] if pid == 40 else []
    svc = _svc_with(cog)

    rec.run(svc.unsolve(None, _files_hunt(), mini))

    assert [c[1] for c in rec.gets] == ["MMFILE"]           # never "REF"
    assert _props(rec.batches[-1][2], "index") == [("410", 2)]
    cog.move_file.assert_not_awaited()
    assert mini.solved is False


@patch("bot.core.puzzle_service.SheetsJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_service_unsolve_of_an_own_file_reads_no_spreadsheet_and_moves_it_back(
        PuzzleDb, TagGroupDb, SheetsDb):
    rec = Recorder()
    cog = _logged_cog(rec)
    PuzzleDb.get_children.return_value = []
    puzzle = _own(solved=True)
    rec.run(_svc_with(cog).unsolve(None, _files_hunt(), puzzle))
    assert rec.gets == []
    assert [c for c in rec.calls if c[0] == "move_file"] == [
        ("move_file", "FILE1", ("FOLDER", "SOLVEDFOLDER"))]


@patch("bot.core.puzzle_service.SheetsJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_service_unsolve_of_a_puzzle_with_no_file_completes_and_badges_it(
        PuzzleDb, TagGroupDb, SheetsDb):
    """Like solve in the same state: the sheet step fails (nothing to address),
    the unsolve itself still completes and is committed, and the puzzle is
    reported through SheetRefreshError.failed_puzzle_ids."""
    from bot.core.errors import SheetRefreshError
    rec = Recorder()
    cog = _logged_cog(rec)
    PuzzleDb.get_children.return_value = []
    puzzle = _own(solved=True)
    puzzle.google_sheet_id = ""
    svc = _svc_with(cog)
    with pytest.raises(SheetRefreshError) as exc:
        rec.run(svc.unsolve(None, _files_hunt(), puzzle))
    assert exc.value.failed_puzzle_ids == [puzzle.id] and exc.value.failed_group_ids == []
    assert puzzle.solved is False and puzzle.solution == ""
    PuzzleDb.commit.assert_called_with(puzzle)
    assert rec.calls == []          # nothing was addressed to the wrong spreadsheet


# ----------------------------------------------------------------- rename

@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_rename_own_file_renames_the_file_and_its_tab(PuzzleDb, TagGroupDb):
    rec = Recorder(sheets_by_id={"FILE1": [_sheet(11, "Clueless", 0)]})
    cog = _logged_cog(rec)
    puzzle = _own()
    TagGroupDb.get_by_attr.return_value = None
    PuzzleDb.get_children.return_value = []
    rec.run(_svc_with(cog).rename_puzzle(None, _files_hunt(), puzzle, "Clueful"))

    assert [c for c in rec.calls if c[0] == "rename_file"] == [
        ("rename_file", "FILE1", "Clueful")]
    assert [c[1] for c in rec.batches] == ["FILE1"]
    assert _props(rec.batches[0][2], "title") == [("11", "Clueful")]


@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_rename_mini_renames_its_tab_only(PuzzleDb, TagGroupDb):
    rec = Recorder(sheets_by_id={"MMFILE": [_sheet(400, "Mini Meta", 0),
                                            _sheet(410, "Mini 41", 1)]})
    cog = _logged_cog(rec)
    mini = _mini()
    TagGroupDb.get_by_attr.return_value = None
    PuzzleDb.get_children.return_value = []
    rec.run(_svc_with(cog).rename_puzzle(None, _files_hunt(), mini, "Tiny"))

    cog.rename_file.assert_not_awaited()
    assert [c[1] for c in rec.batches] == ["MMFILE"]
    assert _props(rec.batches[0][2], "title") == [("410", "Tiny")]


@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_rename_file_failure_is_logged_and_the_rename_still_completes(
        PuzzleDb, TagGroupDb, caplog):
    rec = Recorder(sheets_by_id={"FILE1": [_sheet(11, "Clueless", 0)]})
    cog = _logged_cog(rec)
    cog.rename_file = AsyncMock(side_effect=RuntimeError("Drive 500"))
    TagGroupDb.get_by_attr.return_value = None
    PuzzleDb.get_children.return_value = []
    puzzle = _own()
    svc = _svc_with(cog)
    with caplog.at_level(logging.ERROR):
        rec.run(svc.rename_puzzle(None, _files_hunt(), puzzle, "Clueful"))
    assert puzzle.name == "Clueful"
    svc.refresh_header_links.assert_awaited_once()
    assert "FILE1" in caplog.text


@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_rename_in_a_tabs_hunt_never_touches_drive(PuzzleDb, TagGroupDb):
    rec = Recorder(sheets_by_id={"LIVE": [_sheet(11, "Clueless", 7)]})
    cog = _logged_cog(rec)
    puzzle = make_puzzle(name="Clueless")
    puzzle.google_page_id = 11
    TagGroupDb.get_by_attr.return_value = None
    PuzzleDb.get_children.return_value = []
    rec.run(_svc_with(cog).rename_puzzle(None, make_hunt(google_sheet_id="LIVE"),
                                         puzzle, "Clueful"))
    cog.rename_file.assert_not_awaited()
    assert [c[1] for c in rec.batches] == ["LIVE"]


# ----------------------------------------------------------------- delete

def _delete_cog():
    """Real delete_puzzle_spreadsheet; its two leaves are mocks."""
    cog = _cog()
    cog.delete_sheet = AsyncMock()
    return cog


@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_delete_puzzle_deletes_its_file_and_never_a_tab(PuzzleDb, TagGroupDb):
    cog = _delete_cog()
    PuzzleDb.get_children.return_value = []
    svc = _svc_with(cog)
    svc.refresh_targets = AsyncMock(return_value=[])
    asyncio.run(svc.delete_puzzle(None, _files_hunt(), _own()))
    cog.delete_file.assert_awaited_once_with("FILE1")
    cog.delete_sheet.assert_not_awaited()


@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_delete_mini_deletes_its_tab_in_the_parents_file(PuzzleDb, TagGroupDb):
    cog = _delete_cog()
    minimeta = _own(id=40, page=400)
    minimeta.google_sheet_id = "MMFILE"
    PuzzleDb.get_children.return_value = []
    PuzzleDb.get_by_attr.return_value = minimeta
    svc = _svc_with(cog)
    svc.refresh_targets = AsyncMock(return_value=[])
    asyncio.run(svc.delete_mini(None, _files_hunt(), _mini()))
    cog.delete_sheet.assert_awaited_once_with(410, "MMFILE")
    cog.delete_file.assert_not_awaited()


@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_teardown_group_target_deletes_the_targets_file(PuzzleDb, TagGroupDb):
    cog = _delete_cog()
    svc = _svc_with(cog)
    target = _own(id=2, page=21, is_target=True, tags=[5])
    group = make_round(id=5, target_puzzle_id=2)
    asyncio.run(svc._teardown_group_target(None, _files_hunt(), group, target))
    cog.delete_file.assert_awaited_once_with("FILE2")
    cog.delete_sheet.assert_not_awaited()
    PuzzleDb.delete.assert_called_once_with(2)


@patch("bot.core.puzzle_service.SheetsJsonDb")
def test_delete_additional_sheet_deletes_its_tab_in_the_puzzles_file(SheetsDb):
    cog = _delete_cog()
    svc = _svc_with(cog)
    puzzle = _own()
    puzzle.additional_sheets = [_extra(7, 12, "FILE1"), _extra(8, 13, "FILE1")]
    asyncio.run(svc._delete_additional_sheet(_files_hunt(), puzzle,
                                             puzzle.additional_sheets[0]))
    cog.delete_sheet.assert_awaited_once_with("12", "FILE1")
    cog.delete_file.assert_not_awaited()
    SheetsDb.delete.assert_called_once_with(7)
    assert [s.id for s in puzzle.additional_sheets] == [8]


@patch("bot.core.puzzle_service.EvidenceJsonDb")
@patch("bot.core.puzzle_service.RollupsJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_a_solved_targets_own_table_renders_in_its_own_file(
        PuzzleDb, TagGroupDb, RollupsDb, EvidenceDb):
    """Review focus 4: after the solve moved the target's file, the target's
    own roll-up table is written to that same file (same id), not to the
    reference spreadsheet or a feeder's file."""
    cog = _cog()
    hunt = _files_hunt()
    target = _own(id=2, name="Meta", page=21, is_target=True, tags=[5], solved=True)
    group = SimpleNamespace(id=5, hunt_id=1, target_puzzle_id=2)
    PuzzleDb.get_by_attr.return_value = target
    RollupsDb.get_by_attr.return_value = SimpleNamespace(
        hunt_id=1, tag_group_id=5, target_puzzle_id=None, metadata_id=7, tab_page_id=21)
    EvidenceDb.get_all.return_value = []
    svc = _svc_with(cog)
    del svc.refresh_target          # the real one, not the stub
    svc.select_rollup_members = AsyncMock(return_value=[])
    cog.stamp_rollup_metadata = AsyncMock(return_value=(21, 7))
    cog._ensure_contribution_table = AsyncMock(return_value={})
    cog.reconcile_table = AsyncMock()

    asyncio.run(svc.refresh_target(hunt, group))

    cog._ensure_contribution_table.assert_awaited_once()
    assert cog._ensure_contribution_table.await_args.kwargs["spreadsheet_id"] == "FILE2"
    cog.reconcile_table.assert_awaited_once()
    kw = cog.reconcile_table.await_args.kwargs
    assert (kw["spreadsheet_id"], kw["page_id"]) == ("FILE2", 21)
