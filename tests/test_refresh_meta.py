"""Regression tests that the meta-sheet rebuild actually FIRES.

The bang-vs-web equivalence suite (test_solve_equivalence.py) runs with no
GoogleSheets cog (bot.get_cog -> None), so it verifies DB-state transitions but
NOT that PuzzleService.refresh_meta reaches add_metapuzzle_data /
add_metametapuzzle_data. These tests close that gap with a present (mocked) cog,
so an extraction that silently stops rebuilding meta sheets is caught.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_puzzle, make_round


def _hunt():
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "SHEET1"
    h.archive_google_sheet_id = "ARCH1"
    return h


def _svc_with_sheets():
    gsheet = MagicMock()
    gsheet.add_metapuzzle_data = AsyncMock()
    gsheet.add_metametapuzzle_data = AsyncMock()
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return PuzzleService(bot), gsheet


class TestRefreshMetaFires:
    @pytest.mark.asyncio
    async def test_points_cog_at_hunt_spreadsheet(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, meta_id=1)
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb:
            pdb.get_by_attr.return_value = make_puzzle(id=1, metapuzzle=1)
            pdb.get_all_from_round.return_value = []
            await svc.refresh_meta(_hunt(), meta_round)
        gsheet.set_spreadsheet_id.assert_called_with("SHEET1")
        gsheet.set_archive_spreadsheet_id.assert_called_with("ARCH1")

    @pytest.mark.asyncio
    async def test_calls_add_metapuzzle_data_for_regular_meta(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, meta_id=1)
        metapuzzle = make_puzzle(id=1, metapuzzle=1)
        feeders = [make_puzzle(id=2, name="Feeder", solution="ANS")]
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb:
            pdb.get_by_attr.return_value = metapuzzle
            pdb.get_all_from_round.return_value = feeders
            await svc.refresh_meta(_hunt(), meta_round)
        gsheet.add_metapuzzle_data.assert_awaited_once_with(metapuzzle, feeders)
        gsheet.add_metametapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_calls_add_metametapuzzle_data_for_metameta(self):
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, meta_id=1)
        metameta = make_puzzle(id=1, metapuzzle=1, metameta=1)
        all_puzzles = [make_puzzle(id=2), make_puzzle(id=3)]
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb:
            pdb.get_by_attr.return_value = metameta
            pdb.get_all_from_hunt.return_value = all_puzzles
            await svc.refresh_meta(_hunt(), meta_round)
        gsheet.add_metametapuzzle_data.assert_awaited_once_with(metameta, all_puzzles)
        gsheet.add_metapuzzle_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_add_to_meta_triggers_rebuild(self):
        """End-to-end: tagging a puzzle into a meta rebuilds that meta's sheet."""
        svc, gsheet = _svc_with_sheets()
        meta_round = make_round(id=5, meta_id=1)
        puzzle = make_puzzle(id=2, tags=[])
        with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
             patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
            hdb.get_by_attr.return_value = _hunt()
            pdb.get_by_attr.return_value = make_puzzle(id=1, metapuzzle=1)
            pdb.get_all_from_round.return_value = [puzzle]
            await svc.add_to_meta(puzzle, meta_round)
        assert gsheet.add_metapuzzle_data.await_count == 1
