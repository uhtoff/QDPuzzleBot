"""Behavioural coverage for get_hunt_puzzles, the SPA's whole puzzle payload (M33).

This ~130-line SQL-join-plus-row-folding function had exactly three tests, and
all three did:

    src = inspect.getsource(data.get_hunt_puzzles)
    assert "<some substring>" in src

A substring assert on source text passes if the string appears in a comment, in
dead code, or inverted. Nothing executed the function, so every fold below --
the multi-round collapse, the mini attachment, the sheet attachment, the rollup
badge -- could regress silently while the suite stayed green.

Faking the DB: the function issues FOUR separate `fetch_all` calls, so a single
patched return value cannot drive it. An ordered `side_effect` list would work
until someone adds a fifth query, then silently mis-feed every fold. This
dispatches on a distinctive fragment of each query instead, which survives
reordering and fails loudly on an unrecognised query rather than quietly
returning the wrong rows.

One test does still assert on SQL text -- but on the query actually PASSED to
fetch_all, not on the function's source. That is the difference between "the
query the code issues contains this filter" and "this string appears somewhere
in the file".
"""
from unittest.mock import patch

import pytest

from bot.web import data


def _row(**kw):
    """A main-query row with every column the fold reads, overridable."""
    base = {
        "id": 1, "name": "Alpha", "url": None, "channel_id": 900,
        "google_page_id": "P1", "google_sheet_id": "", "is_target": 0, "status": "Unstarted",
        "solved": 0, "on_solved_sheet": 0, "solution": None, "priority": None,
        "puzzle_type": None, "notes": None, "start_time": None, "solve_time": None,
        "tag_group_id": None, "tag_group_name": None,
        "tag_group_target_puzzle_id": None, "tag_group_group_code": None,
        "tag_group_type": None, "tag_edge_type": None,
        "last_human_message_at": None, "last_author_name": None,
    }
    base.update(kw)
    return base


class _Dispatcher:
    """Answers each of get_hunt_puzzles' four queries by matching its SQL."""

    def __init__(self, main=(), sheets=(), minis=(), rollups=()):
        self.main, self.sheets, self.minis, self.rollups = main, sheets, minis, rollups
        self.queries = []

    async def __call__(self, sql, params=()):
        self.queries.append(sql)
        if "FROM additional_sheets" in sql:
            return list(self.sheets)
        if "FROM rollups" in sql:
            return list(self.rollups)
        if "parent_puzzle_id IS NOT NULL" in sql:
            return list(self.minis)
        if "FROM puzzles " in sql:
            return list(self.main)
        raise AssertionError(f"unrecognised query, fake needs updating:\n{sql}")


HUNT = {"id": 10, "google_sheet_id": "LIVE", "solved_sheet_id": "SOLVED"}


async def _run(hunt=HUNT, **kw):
    fake = _Dispatcher(**kw)
    with patch.object(data, "fetch_all", fake):
        return await data.get_hunt_puzzles(hunt, "123"), fake


# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_puzzle_in_two_rounds_appears_once_carrying_both():
    """The join returns one ROW PER ROUND. Two rounds, so a fold that overwrites
    instead of appending disagrees -- with one round it cannot."""
    out, _ = await _run(main=[
        _row(tag_group_id=5, tag_group_name="Round A", tag_edge_type="feeds"),
        _row(tag_group_id=6, tag_group_name="Round B", tag_edge_type="feeds"),
    ])

    assert len(out) == 1, out
    assert [r["name"] for r in out[0]["rounds"]] == ["Round A", "Round B"]


@pytest.mark.asyncio
async def test_minis_attach_to_their_parent_and_are_not_top_level():
    out, _ = await _run(
        main=[_row(id=1, name="Minimeta")],
        minis=[{"id": 2, "name": "Mini one", "solved": 1, "solution": "AAA",
                "channel_id": 901, "parent_puzzle_id": 1},
               {"id": 3, "name": "Mini two", "solved": 0, "solution": None,
                "channel_id": 902, "parent_puzzle_id": 1}],
    )

    assert [p["id"] for p in out] == [1], "minis must not be top-level puzzles"
    assert [m["name"] for m in out[0]["mini_puzzles"]] == ["Mini one", "Mini two"]
    assert out[0]["mini_puzzles"][0]["solved"] is True
    assert out[0]["mini_puzzles"][1]["solved"] is False


@pytest.mark.asyncio
async def test_additional_sheets_attach_to_their_own_puzzle():
    """Two puzzles, one sheet each: a fold that attaches to the wrong puzzle,
    or to all of them, disagrees."""
    out, _ = await _run(
        main=[_row(id=1, name="Alpha"), _row(id=2, name="Beta")],
        sheets=[{"id": 7, "puzzle_id": 2, "puzzle_name": "grid", "google_page_id": "P9",
                "google_sheet_id": "", "on_solved_sheet": 0}],
    )

    by_id = {p["id"]: p for p in out}
    assert by_id[1]["sheets"] == []
    assert [s["name"] for s in by_id[2]["sheets"]] == ["grid"]


@pytest.mark.asyncio
async def test_a_failed_rollup_marks_only_its_own_minimeta_stale():
    """Two rollups with opposite last_ok, so "always stale" and "stale on
    failure" disagree."""
    out, _ = await _run(
        main=[_row(id=1), _row(id=2)],
        rollups=[{"target_puzzle_id": 1, "last_ok": 0, "last_error": "boom"},
                 {"target_puzzle_id": 2, "last_ok": 1, "last_error": None}],
    )

    by_id = {p["id"]: p for p in out}
    assert by_id[1]["rollup_stale"] is True
    assert by_id[1]["rollup_error"] == "boom"
    assert by_id[2]["rollup_stale"] is False


@pytest.mark.asyncio
async def test_a_row_referencing_an_absent_puzzle_is_ignored():
    """Every fold guards on `is not None`. A mini or sheet whose parent is not
    in the payload must be skipped, not crash the whole dashboard."""
    out, _ = await _run(
        main=[_row(id=1)],
        minis=[{"id": 2, "name": "Orphan", "solved": 0, "solution": None,
                "channel_id": None, "parent_puzzle_id": 999}],
        sheets=[{"id": 7, "puzzle_id": 999, "puzzle_name": "x", "google_page_id": "P",
                "google_sheet_id": "", "on_solved_sheet": 0}],
        rollups=[{"target_puzzle_id": 999, "last_ok": 0, "last_error": "e"}],
    )

    assert len(out) == 1
    assert out[0]["mini_puzzles"] == []
    assert out[0]["sheets"] == []


@pytest.mark.asyncio
async def test_the_queries_issued_carry_the_filters_the_payload_depends_on():
    """The one text assertion left, and it is on the SQL actually PASSED to
    fetch_all rather than on the function's source. A source grep matches
    comments and dead code; this cannot.
    """
    _, fake = await _run(main=[_row()])
    joined = "\n".join(fake.queries)

    # Minis are excluded from the main list...
    assert "parent_puzzle_id IS NULL" in joined
    # ...and fetched separately.
    assert "parent_puzzle_id IS NOT NULL" in joined
    # Puzzle-keyed rollups only: group badges come from get_rollups_by_tag_group.
    assert "target_puzzle_id IS NOT NULL" in joined
    assert len(fake.queries) == 4, fake.queries


# --- server-built sheet URLs (files mode task 3) ----------------------------

def _gid(sheet_id, page):
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={page}"


@pytest.mark.asyncio
async def test_each_puzzle_carries_the_url_its_location_rule_gives():
    """Three puzzles, three different spreadsheets: live, solved, own file. A
    builder that ignores on_solved_sheet or google_sheet_id disagrees on one."""
    out, _ = await _run(main=[
        _row(id=1, google_page_id="P1"),
        _row(id=2, google_page_id="P2", on_solved_sheet=1),
        _row(id=3, google_page_id="P3", google_sheet_id="OWN"),
    ])
    by_id = {p["id"]: p for p in out}
    assert by_id[1]["sheet_url"] == _gid("LIVE", "P1")
    assert by_id[2]["sheet_url"] == _gid("SOLVED", "P2")
    assert by_id[3]["sheet_url"] == _gid("OWN", "P3")


@pytest.mark.asyncio
async def test_an_extra_sheet_uses_its_own_solved_flag_not_its_puzzles():
    """Fixes the drawer mismatch: the puzzle is live, the extra is on the solved
    sheet (and a second extra is live on a solved puzzle)."""
    out, _ = await _run(
        main=[_row(id=1), _row(id=2, on_solved_sheet=1)],
        sheets=[
            {"id": 7, "puzzle_id": 1, "puzzle_name": "a", "google_page_id": "X1",
             "google_sheet_id": "", "on_solved_sheet": 1},
            {"id": 8, "puzzle_id": 2, "puzzle_name": "b", "google_page_id": "X2",
             "google_sheet_id": "", "on_solved_sheet": 0},
        ],
    )
    by_id = {p["id"]: p for p in out}
    assert by_id[1]["sheets"][0]["url"] == _gid("SOLVED", "X1")
    assert by_id[2]["sheets"][0]["url"] == _gid("LIVE", "X2")
    assert by_id[1]["sheets"][0]["page_id"] == "X1"
