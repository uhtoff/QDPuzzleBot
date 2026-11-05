"""A tab-mode puzzle create that fails after its tab is added deletes the tab.

The tab is added first and filled in by later batches; a failure there (e.g. a
rate limit that outlasts the retries) used to strand an empty tab in the hunt
spreadsheet, and the user's retry then got "<name> (2)". Files mode already
deletes its half-made copy (create_puzzle_file).
"""
import pytest
from googleapiclient.errors import HttpError

from tests.conftest import make_hunt, make_puzzle
from tests.test_files_mode_create import NEW_PAGE, Recorder, _cog


def _deletes(rec):
    return [(sid, r["deleteSheet"]["sheetId"])
            for _, sid, body in rec.batches for r in body["requests"]
            if "deleteSheet" in r]


def _adds(body):
    return any("addSheet" in r for r in body["requests"])


def test_a_failure_after_the_tab_is_added_deletes_the_tab():
    cog = _cog()
    # The addSheet goes through; the next batch (the header fill) fails.
    rec = Recorder(fail_on=lambda kind, sid, body: kind == "batch" and not _adds(body)
                   and not any("deleteSheet" in r for r in body["requests"]))
    puzzle = make_puzzle(name="Clueless")
    with pytest.raises(HttpError):
        rec.run(cog.create_puzzle_spreadsheet(puzzle, index=2, hunt=make_hunt()))

    assert _deletes(rec) == [("LIVE", NEW_PAGE)]
    assert puzzle.google_page_id == ""


def test_a_failed_add_has_no_tab_to_delete():
    cog = _cog()
    rec = Recorder(fail_on=lambda kind, sid, body: kind == "batch" and _adds(body))
    puzzle = make_puzzle(name="Clueless")
    with pytest.raises(HttpError):
        rec.run(cog.create_puzzle_spreadsheet(puzzle, index=2, hunt=make_hunt()))

    assert _deletes(rec) == []
    assert puzzle.google_page_id == ""
