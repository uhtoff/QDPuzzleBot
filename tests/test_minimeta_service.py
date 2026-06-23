"""Tests for M8 minimeta cluster service methods."""
from unittest.mock import MagicMock, patch
import pytest

from bot.store.puzzle_data import PuzzleData
from bot.store.hunt_data import HuntData
from tests.conftest import make_puzzle, make_round


def _make_cog():
    from bot.cogs.puzzles import Puzzles
    bot = MagicMock()
    bot.get_cog.return_value = None
    return Puzzles(bot)


def _make_hunt(id=10):
    h = HuntData()
    h.id = id
    h.google_sheet_id = "SHEET1"
    h.archive_google_sheet_id = "ARCHIVE1"
    return h


class TestPuzzleDataParentField:
    def test_parent_puzzle_id_defaults_none(self):
        p = PuzzleData()
        assert p.parent_puzzle_id is None

    def test_make_puzzle_accepts_parent_puzzle_id(self):
        p = make_puzzle(id=2, parent_puzzle_id=1)
        assert p.parent_puzzle_id == 1

    def test_import_dict_reads_parent_puzzle_id(self):
        row = {
            "id": 5, "name": "Mini", "hunt_id": 10,
            "parent_puzzle_id": 3,
            "channel_id": 999, "solved": False, "solution": None,
            "metapuzzle": 0, "metameta": 0, "status": "", "priority": "",
            "puzzle_type": "Mini", "notes": None, "url": "", "google_page_id": "",
            "archived": False, "voice_channel_id": 0, "channel_mention": "",
            "channel_name": "", "start_time": None, "solve_time": None,
            "archive_time": None, "tag_edges": None,
        }
        p = PuzzleData.import_dict(row)
        assert p.parent_puzzle_id == 3
