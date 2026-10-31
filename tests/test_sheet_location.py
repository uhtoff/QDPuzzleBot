from types import SimpleNamespace

from bot.core.sheet_location import (
    SheetLocation,
    additional_sheet_location,
    hunt_sheet_location,
    puzzle_location,
)


def _hunt(live="LIVE", solved="ARCHIVE"):
    return SimpleNamespace(google_sheet_id=live, solved_sheet_id=solved)


def _puzzle(on_solved=False, page="11"):
    return SimpleNamespace(on_solved_sheet=on_solved, google_page_id=page)


def _extra(on_solved=False, page="22"):
    return SimpleNamespace(on_solved_sheet=on_solved, google_page_id=page)


def test_live_puzzle_is_on_the_live_sheet():
    loc = puzzle_location(_hunt(), _puzzle(on_solved=False, page="11"))
    assert loc == SheetLocation("LIVE", "11")


def test_solved_puzzle_is_on_the_solved_sheet():
    loc = puzzle_location(_hunt(), _puzzle(on_solved=True, page="12"))
    assert loc == SheetLocation("ARCHIVE", "12")


def test_solved_flag_with_empty_solved_sheet_falls_back_to_live():
    loc = puzzle_location(_hunt(solved=""), _puzzle(on_solved=True))
    assert loc.spreadsheet_id == "LIVE"
    assert "/d//" not in (loc.url or "")


def test_extra_uses_its_own_flag_not_the_puzzles():
    hunt = _hunt()
    live_puzzle = _puzzle(on_solved=False)
    solved_puzzle = _puzzle(on_solved=True)
    # extra archived while its puzzle is live, and the reverse
    assert additional_sheet_location(hunt, _extra(on_solved=True, page="1")) == SheetLocation("ARCHIVE", "1")
    assert additional_sheet_location(hunt, _extra(on_solved=False, page="2")) == SheetLocation("LIVE", "2")
    assert puzzle_location(hunt, live_puzzle).spreadsheet_id == "LIVE"
    assert puzzle_location(hunt, solved_puzzle).spreadsheet_id == "ARCHIVE"


def test_extra_on_solved_sheet_with_empty_solved_id_falls_back_to_live():
    loc = additional_sheet_location(_hunt(solved=""), _extra(on_solved=True))
    assert loc.spreadsheet_id == "LIVE"


def test_hunt_sheet_is_always_on_the_hunt_sheet():
    assert hunt_sheet_location(_hunt(), "33") == SheetLocation("LIVE", "33")


def test_url_is_full_url_with_gid():
    loc = SheetLocation("abc", 7)
    assert loc.url == "https://docs.google.com/spreadsheets/d/abc/edit#gid=7"


def test_gid_zero_is_a_real_page():
    assert SheetLocation("abc", 0).url == "https://docs.google.com/spreadsheets/d/abc/edit#gid=0"


def test_url_none_without_page_or_spreadsheet():
    assert SheetLocation("abc", None).url is None
    assert SheetLocation("abc", "").url is None
    assert SheetLocation("", "5").url is None


def test_legacy_full_url_sheet_id_passes_through_unchanged():
    legacy = "https://docs.google.com/spreadsheets/d/old/edit#gid=99"
    assert SheetLocation(legacy, "5").url == legacy
