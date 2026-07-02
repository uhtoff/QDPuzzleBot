"""Tests for PuzzleService.update_puzzle_field (TDD — written before implementation)."""
from unittest.mock import patch

import pytest

from tests.conftest import make_hunt, make_puzzle


class TestUpdatePuzzleFieldStatus:
    """update_puzzle_field with field='status'."""

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_sets_valid_status_and_commits(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)

        await svc.update_puzzle_field(hunt, puzzle, "status", "stuck")

        assert puzzle.status == "stuck"
        PuzzleDb.commit.assert_called_once_with(puzzle)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_solved_status_rejected_when_not_solved(self, PuzzleDb):
        """"solved"/"backsolved" are owned by the solve machinery, not !status."""
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10, solved=False)

        with pytest.raises(ValueError, match="solve"):
            await svc.update_puzzle_field(hunt, puzzle, "status", "solved")

        PuzzleDb.commit.assert_not_called()

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_backsolved_status_rejected_when_not_solved(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10, solved=False)

        with pytest.raises(ValueError):
            await svc.update_puzzle_field(hunt, puzzle, "status", "backsolved")

        PuzzleDb.commit.assert_not_called()

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_can_toggle_between_solved_and_backsolved_when_solved(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10, solved=True, status="solved")

        await svc.update_puzzle_field(hunt, puzzle, "status", "backsolved")

        assert puzzle.status == "backsolved"
        PuzzleDb.commit.assert_called_once_with(puzzle)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_cannot_change_away_from_solved_status_manually(self, PuzzleDb):
        """Once solved, only the unsolve machinery may change the status."""
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10, solved=True, status="solved")

        with pytest.raises(ValueError, match="unsolve"):
            await svc.update_puzzle_field(hunt, puzzle, "status", "in progress")

        PuzzleDb.commit.assert_not_called()

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_status_case_insensitive_validation(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)

        await svc.update_puzzle_field(hunt, puzzle, "status", "In Progress")

        assert puzzle.status == "In Progress"
        PuzzleDb.commit.assert_called_once_with(puzzle)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_invalid_status_raises_value_error(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)

        with pytest.raises(ValueError, match="status"):
            await svc.update_puzzle_field(hunt, puzzle, "status", "flying")

        PuzzleDb.commit.assert_not_called()

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_puzzle_unchanged_on_invalid_status(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10, status="unstarted")

        with pytest.raises(ValueError):
            await svc.update_puzzle_field(hunt, puzzle, "status", "badvalue")

        assert puzzle.status == "unstarted"


class TestUpdatePuzzleFieldPriority:
    """update_puzzle_field with field='priority'."""

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_sets_valid_priority_and_commits(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)

        await svc.update_puzzle_field(hunt, puzzle, "priority", "high")

        assert puzzle.priority == "high"
        PuzzleDb.commit.assert_called_once_with(puzzle)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_sets_very_high_priority(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)

        await svc.update_puzzle_field(hunt, puzzle, "priority", "very high")

        assert puzzle.priority == "very high"
        PuzzleDb.commit.assert_called_once_with(puzzle)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_invalid_priority_raises_value_error(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)

        with pytest.raises(ValueError, match="priority"):
            await svc.update_puzzle_field(hunt, puzzle, "priority", "urgent")

        PuzzleDb.commit.assert_not_called()


class TestUpdatePuzzleFieldPuzzleType:
    """update_puzzle_field with field='puzzle_type'."""

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_sets_puzzle_type_and_commits(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)

        await svc.update_puzzle_field(hunt, puzzle, "puzzle_type", "crossword")

        assert puzzle.puzzle_type == "crossword"
        PuzzleDb.commit.assert_called_once_with(puzzle)

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_puzzle_type_accepts_any_string(self, PuzzleDb):
        """puzzle_type is free-form — no validation of value."""
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)

        await svc.update_puzzle_field(hunt, puzzle, "puzzle_type", "some-custom-type")

        assert puzzle.puzzle_type == "some-custom-type"
        PuzzleDb.commit.assert_called_once_with(puzzle)


class TestUpdatePuzzleFieldUnknownField:
    """update_puzzle_field rejects unknown field names."""

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_unknown_field_raises_value_error(self, PuzzleDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)

        with pytest.raises(ValueError, match="field"):
            await svc.update_puzzle_field(hunt, puzzle, "bogus_field", "anything")

        PuzzleDb.commit.assert_not_called()

    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_name_field_rejected(self, PuzzleDb):
        """'name' is not a supported field for this method."""
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(None)
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)

        with pytest.raises(ValueError):
            await svc.update_puzzle_field(hunt, puzzle, "name", "new name")

        PuzzleDb.commit.assert_not_called()
