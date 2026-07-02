"""Service-layer tests for additional-sheet add/remove (M3 Task 4)."""
from unittest.mock import MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_puzzle
from bot.store.puzzle_data import AdditionalSheetData


def _hunt():
    h = HuntData(); h.id = 10; h.google_sheet_id = "S1"; h.solved_sheet_id = "A1"
    return h


def _svc_no_cog():
    bot = MagicMock(); bot.get_cog.return_value = None
    return PuzzleService(bot)


class TestAddSheet:
    @pytest.mark.asyncio
    async def test_raises_on_solved(self):
        svc = _svc_no_cog()
        with pytest.raises(RuntimeError, match="solved_no_sheets"):
            await svc.add_sheet(_hunt(), make_puzzle(solved=True), "x")

    @pytest.mark.asyncio
    async def test_appends_and_commits_when_unsolved(self):
        svc = _svc_no_cog()
        puz = make_puzzle(solved=False)
        puz.additional_sheets = []
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sheet = await svc.add_sheet(_hunt(), puz, "MySheet")
        assert sheet in puz.additional_sheets
        assert sheet.puzzle_name == "MySheet"
        assert sdb.commit.called


class TestRemoveSheetByIndex:
    @pytest.mark.asyncio
    async def test_raises_on_out_of_bounds_zero(self):
        svc = _svc_no_cog()
        puz = make_puzzle()
        sheet = AdditionalSheetData(puzzle_id=puz.id, puzzle_name="S")
        sheet.id = 1
        puz.additional_sheets = [sheet]
        with pytest.raises(IndexError):
            await svc.remove_sheet_by_index(_hunt(), puz, 0)

    @pytest.mark.asyncio
    async def test_raises_on_out_of_bounds_too_large(self):
        svc = _svc_no_cog()
        puz = make_puzzle()
        sheet = AdditionalSheetData(puzzle_id=puz.id, puzzle_name="S")
        sheet.id = 1
        puz.additional_sheets = [sheet]
        with pytest.raises(IndexError):
            await svc.remove_sheet_by_index(_hunt(), puz, 2)

    @pytest.mark.asyncio
    async def test_removes_correct_sheet_by_index(self):
        svc = _svc_no_cog()
        puz = make_puzzle()
        s1 = AdditionalSheetData(puzzle_id=puz.id, puzzle_name="First")
        s1.id = 11
        s2 = AdditionalSheetData(puzzle_id=puz.id, puzzle_name="Second")
        s2.id = 22
        puz.additional_sheets = [s1, s2]
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            await svc.remove_sheet_by_index(_hunt(), puz, 2)
        assert all(s.id != 22 for s in puz.additional_sheets)
        assert any(s.id == 11 for s in puz.additional_sheets)
        assert sdb.delete.called

    @pytest.mark.asyncio
    async def test_delegates_to_remove_sheet_guard(self):
        """remove_sheet_by_index goes through remove_sheet, so sheet_not_found is still honoured."""
        svc = _svc_no_cog()
        puz = make_puzzle()
        puz.additional_sheets = []
        with pytest.raises(IndexError):
            await svc.remove_sheet_by_index(_hunt(), puz, 1)


class TestRemoveSheet:
    @pytest.mark.asyncio
    async def test_raises_when_missing(self):
        svc = _svc_no_cog()
        puz = make_puzzle(); puz.additional_sheets = []
        with pytest.raises(RuntimeError, match="sheet_not_found"):
            await svc.remove_sheet(_hunt(), puz, 999)

    @pytest.mark.asyncio
    async def test_removes_existing(self):
        svc = _svc_no_cog()
        puz = make_puzzle()
        sheet = AdditionalSheetData(puzzle_id=puz.id, puzzle_name="S")
        sheet.id = 42
        puz.additional_sheets = [sheet]
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            await svc.remove_sheet(_hunt(), puz, 42)
        assert all(s.id != 42 for s in puz.additional_sheets)
        assert sdb.delete.called
