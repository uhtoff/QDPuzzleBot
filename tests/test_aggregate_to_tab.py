"""Tests for the M2 generic roll-up engine in bot/cogs/puzzles_gsheet.py.

These cover the pure logic (A1 helpers, solution text, row building) and the
range/values handed to the Sheets values() API — without touching Google. The
suite uses asyncio.run() rather than async test functions because pytest-asyncio
isn't a dependency (see the sibling test_gsheet_concurrency.py)."""
import asyncio

import pytest

import bot.cogs.puzzles_gsheet as gsheet_mod
from bot.cogs.puzzles_gsheet import GoogleSheets
from bot.store.puzzle_data import PuzzleData
from bot.store.tag_group_data import TagGroupData


# Local builders (kept self-contained so this M2 branch doesn't depend on the
# M1 test-seam's conftest fixtures — M1 and M2 are parallel, independent work).
def make_puzzle(id=1, name="Puzzle", solution=None, is_target=False, tags=None):
    p = PuzzleData()
    p.id = id
    p.name = name
    p.solution = solution
    p.is_target = is_target
    p.tags = tags if tags is not None else []
    return p


def make_round(id=1, name="Round", group_code="RND"):
    r = TagGroupData()
    r.id = id
    r.name = name
    r.group_code = group_code
    return r


def _cog():
    # Skip __init__ (it builds a real Sheets service); the methods under test
    # don't touch instance state beyond what each test stubs.
    return GoogleSheets.__new__(GoogleSheets)


# ---------------------------------------------------------------------------
# A1 helpers
# ---------------------------------------------------------------------------

class TestA1Helpers:
    def test_col_letter_single(self):
        assert GoogleSheets._col_letter(0) == "A"
        assert GoogleSheets._col_letter(1) == "B"
        assert GoogleSheets._col_letter(25) == "Z"

    def test_col_letter_double(self):
        assert GoogleSheets._col_letter(26) == "AA"
        assert GoogleSheets._col_letter(27) == "AB"
        assert GoogleSheets._col_letter(51) == "AZ"
        assert GoogleSheets._col_letter(52) == "BA"

    def test_a1_cell(self):
        # 0-based row/col -> A1; the renderers' header lives at row index 4 = A5.
        assert GoogleSheets._a1_cell(0, 0) == "A1"
        assert GoogleSheets._a1_cell(4, 0) == "A5"
        assert GoogleSheets._a1_cell(5, 1) == "B6"
        assert GoogleSheets._a1_cell(4, 2) == "C5"


# ---------------------------------------------------------------------------
# _solution_text
# ---------------------------------------------------------------------------

class TestSolutionText:
    def test_real_answer(self):
        assert GoogleSheets._solution_text(make_puzzle(solution="BANANA")) == "BANANA"

    def test_checkmark_sentinel_shown_as_is(self):
        # mark_as_complete records "✅" for a solved-but-no-answer puzzle; it must
        # be shown (and keep its row), not trigger the old continue-and-drop bug.
        assert GoogleSheets._solution_text(make_puzzle(solution="✅")) == "✅"

    def test_no_solution_is_unsolved(self):
        assert GoogleSheets._solution_text(make_puzzle(solution=None)) == "Unsolved"
        assert GoogleSheets._solution_text(make_puzzle(solution="")) == "Unsolved"


# ---------------------------------------------------------------------------
# add_metapuzzle_data — row building + the ✅ row-shift regression
# ---------------------------------------------------------------------------

def _capture_aggregate(cog):
    """Replace aggregate_to_tab with a recorder; returns the captured-calls list."""
    calls = []

    async def fake_aggregate(spreadsheet_id, tab_page_id, columns, rows, **kw):
        calls.append({
            "spreadsheet_id": spreadsheet_id,
            "tab_page_id": tab_page_id,
            "columns": columns,
            "rows": rows,
        })

    cog.aggregate_to_tab = fake_aggregate
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.get_archive_spreadsheet_id = lambda: "ARCHIVE"
    return calls


class TestAddMetapuzzleData:
    def test_rows_aligned_and_self_skipped(self):
        cog = _cog()
        calls = _capture_aggregate(cog)
        meta = make_puzzle(id=1, name="Meta")
        meta.google_page_id = "900"
        solved = make_puzzle(id=2, name="Alpha", solution="WORD")
        checked = make_puzzle(id=3, name="Beta", solution="✅")
        unsolved = make_puzzle(id=4, name="Gamma", solution=None)

        asyncio.run(cog.add_metapuzzle_data(meta, [meta, solved, checked, unsolved]))

        assert len(calls) == 1
        call = calls[0]
        assert call["spreadsheet_id"] == "LIVE"
        assert call["tab_page_id"] == "900"
        assert call["columns"] == ["Puzzle titles", "Puzzle solutions"]
        # The ✅ puzzle keeps a correctly-paired row (regression: old code dropped
        # it AND shifted every following answer up a row).
        assert call["rows"] == [
            ["Alpha", "WORD"],
            ["Beta", "✅"],
            ["Gamma", "Unsolved"],
        ]

    def test_archived_meta_targets_archive_spreadsheet(self):
        cog = _cog()
        calls = _capture_aggregate(cog)
        meta = make_puzzle(id=1, name="Meta")
        meta.archived = True
        asyncio.run(cog.add_metapuzzle_data(meta, [meta]))
        assert calls[0]["spreadsheet_id"] == "ARCHIVE"
        assert calls[0]["rows"] == []


# ---------------------------------------------------------------------------
# add_metametapuzzle_data — drops the hardcoded RETAWR/LAOFNO skip
# ---------------------------------------------------------------------------

class TestAddMetametapuzzleData:
    def _patch_rounds(self, monkeypatch, rounds_by_id):
        def fake_get_by_attr(id=None, **kw):
            return rounds_by_id[id]
        monkeypatch.setattr(gsheet_mod.TagGroupJsonDb, "get_by_attr", fake_get_by_attr)

    def test_includes_formerly_hardcoded_rounds(self, monkeypatch):
        cog = _cog()
        calls = _capture_aggregate(cog)
        # Two rounds, one with the old hardcoded-skip meta_code.
        rounds = {
            10: make_round(id=10, name="Outer Wilds", group_code="RETAWR"),
            11: make_round(id=11, name="Fresh Round", group_code="ABCDEF"),
        }
        self._patch_rounds(monkeypatch, rounds)

        metameta = make_puzzle(id=1, name="MetaMeta", is_target=True)
        metameta.google_page_id = "abc"
        p_in_old = make_puzzle(id=2, name="OldHunt", solution="X", tags=[10])
        p_in_new = make_puzzle(id=3, name="NewHunt", solution="✅", tags=[11])
        tagless = make_puzzle(id=4, name="Loose", solution=None, tags=[])

        asyncio.run(cog.add_metametapuzzle_data(metameta, [metameta, p_in_old, p_in_new, tagless]))

        call = calls[0]
        assert call["columns"] == ["Puzzle Round", "Puzzle Title", "Puzzle Solution"]
        assert call["rows"] == [
            ["Outer Wilds", "OldHunt", "X"],     # would have been skipped by the old RETAWR guard
            ["Fresh Round", "NewHunt", "✅"],     # ✅ kept + aligned
            ["No Round", "Loose", "Unsolved"],
        ]

    def test_skips_metas_and_multi_tag(self, monkeypatch):
        cog = _cog()
        calls = _capture_aggregate(cog)
        rounds = {10: make_round(id=10, name="R", group_code="ZZZ")}
        self._patch_rounds(monkeypatch, rounds)

        metameta = make_puzzle(id=1, name="MM", is_target=True)
        a_meta = make_puzzle(id=2, name="AMeta", is_target=True, tags=[10])
        multi = make_puzzle(id=3, name="Multi", tags=[10, 11])
        normal = make_puzzle(id=4, name="Normal", solution="Y", tags=[10])

        asyncio.run(cog.add_metametapuzzle_data(metameta, [metameta, a_meta, multi, normal]))

        assert calls[0]["rows"] == [["R", "Normal", "Y"]]


# ---------------------------------------------------------------------------
# aggregate_to_tab — the range/values it hands to the values() API
# ---------------------------------------------------------------------------

class TestAggregateToTab:
    def _capture_values(self, cog, tab_name="My Tab"):
        cog.get_page_name_by_id = lambda page_id, spreadsheet_id=None: tab_name
        recorded = {}

        async def fake_clear(spreadsheet_id, a1_range):
            recorded["clear"] = (spreadsheet_id, a1_range)

        async def fake_update(spreadsheet_id, a1_range, values):
            recorded["update"] = (spreadsheet_id, a1_range, values)

        cog._values_clear = fake_clear
        cog._values_update = fake_update
        return recorded

    def test_clear_then_update_ranges_and_values(self):
        cog = _cog()
        rec = self._capture_values(cog)
        asyncio.run(cog.aggregate_to_tab(
            "SHEET", 777,
            ["Title", "Answer"],
            [["A", "1"], ["B", "2"]],
        ))
        # Clears the whole owned block from the header down (retires 50-row pad).
        assert rec["clear"] == ("SHEET", "'My Tab'!A5:B")
        sid, rng, values = rec["update"]
        assert sid == "SHEET"
        assert rng == "'My Tab'!A5"
        assert values == [["Title", "Answer"], ["A", "1"], ["B", "2"]]

    def test_three_columns_clear_range(self):
        cog = _cog()
        rec = self._capture_values(cog)
        asyncio.run(cog.aggregate_to_tab("S", 1, ["a", "b", "c"], []))
        assert rec["clear"][1] == "'My Tab'!A5:C"
        # Empty roll-up still writes the header (and the clear wipes old rows).
        assert rec["update"][2] == [["a", "b", "c"]]

    def test_tab_name_apostrophe_escaped(self):
        cog = _cog()
        rec = self._capture_values(cog, tab_name="Bob's Tab")
        asyncio.run(cog.aggregate_to_tab("S", 1, ["x"], [["y"]]))
        assert rec["clear"][1] == "'Bob''s Tab'!A5:A"
        assert rec["update"][1] == "'Bob''s Tab'!A5"

    def test_missing_tab_raises(self):
        cog = _cog()
        cog.get_page_name_by_id = lambda page_id, spreadsheet_id=None: None
        with pytest.raises(RuntimeError):
            asyncio.run(cog.aggregate_to_tab("S", 999, ["x"], []))
