"""Tests for PuzzleService.add_note / erase_note."""
from unittest.mock import patch

from tests.conftest import make_hunt, make_puzzle


class TestAddNote:
    """PuzzleService.add_note — appends formatted note and commits."""

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_appends_note_with_attribution(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)
        puzzle.notes = []

        await svc.add_note(hunt, puzzle, "This is a clue", "https://discord.com/msg/123")

        assert len(puzzle.notes) == 1
        assert "This is a clue" in puzzle.notes[0]
        assert "https://discord.com/msg/123" in puzzle.notes[0]
        PuzzleDb.commit.assert_called_once_with(puzzle)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_appends_to_existing_notes(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)
        puzzle.notes = ["first note - https://discord.com/msg/1"]

        await svc.add_note(hunt, puzzle, "second note", "https://discord.com/msg/2")

        assert len(puzzle.notes) == 2
        assert "second note" in puzzle.notes[1]
        PuzzleDb.commit.assert_called_once_with(puzzle)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_web_attribution_format(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)
        puzzle.notes = []

        await svc.add_note(hunt, puzzle, "web clue", "Alice (web)")

        assert "web clue" in puzzle.notes[0]
        assert "Alice (web)" in puzzle.notes[0]
        PuzzleDb.commit.assert_called_once_with(puzzle)


class TestEraseNote:
    """PuzzleService.erase_note — removes note by 1-based index and commits."""

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_removes_note_by_index(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)
        puzzle.notes = ["note A - url1", "note B - url2", "note C - url3"]

        result = await svc.erase_note(hunt, puzzle, 2)

        assert result is True
        assert puzzle.notes == ["note A - url1", "note C - url3"]
        PuzzleDb.commit.assert_called_once_with(puzzle)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_removes_first_note(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)
        puzzle.notes = ["note A - url1", "note B - url2"]

        result = await svc.erase_note(hunt, puzzle, 1)

        assert result is True
        assert puzzle.notes == ["note B - url2"]
        PuzzleDb.commit.assert_called_once_with(puzzle)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_bad_index_returns_false_no_commit(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)
        puzzle.notes = ["note A - url1"]

        result = await svc.erase_note(hunt, puzzle, 5)

        assert result is False
        assert puzzle.notes == ["note A - url1"]
        PuzzleDb.commit.assert_not_called()

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_zero_index_returns_false(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)
        puzzle.notes = ["note A - url1"]

        result = await svc.erase_note(hunt, puzzle, 0)

        assert result is False
        PuzzleDb.commit.assert_not_called()
