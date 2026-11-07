"""Tests for the A1-notation helpers in bot/cogs/puzzles_gsheet.py (pure logic,
no Google calls)."""
from bot.cogs.puzzles_gsheet import GoogleSheets


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
