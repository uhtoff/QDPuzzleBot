"""Pins for the HeaderGeometry seam (full layout)."""
from unittest.mock import patch

from bot.cogs.puzzles_gsheet import GoogleSheets
from bot.core.sheet_location import Link
from tests.conftest import make_puzzle


def test_full_geometry_reads_the_patched_config_at_call_time():
    cog = GoogleSheets.__new__(GoogleSheets)
    with patch("bot.cogs.puzzles_gsheet.config") as cfg:
        cfg.puzzle_cell_name, cfg.puzzle_cell_solution, cfg.puzzle_cell_progress = "C1", "J1", "C3"
        g = cog._header_geometry(make_puzzle())
    assert (g.layout, g.title_a1, g.status_a1, g.progress_a1) == ("full", "C1", "J1", "C3")
    assert (g.feeds_a1, g.frozen_rows, g.band_rows, g.band_cols, g.grey_whole_rows) \
        == ("I3", 3, 3, 17, True)


def test_link_runs_without_prefix_match_the_feeds_cell():
    cog = GoogleSheets.__new__(GoogleSheets)
    text, runs = cog._link_runs("", [Link("A", "u1"), Link("B", None)])
    assert text == "A, B"
    assert runs == [{"startIndex": 0, "format": {"link": {"uri": "u1"}}},
                    {"startIndex": 1, "format": {}}, {"startIndex": 3, "format": {}}]
