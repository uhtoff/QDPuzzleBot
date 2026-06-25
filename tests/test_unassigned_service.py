"""Tests for meta-matching service layer + HuntData fields."""
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_hunt, make_puzzle


class TestHuntDataFields:
    def test_defaults(self):
        from bot.store.hunt_data import HuntData
        h = HuntData()
        assert h.unassigned_sheet_page_id is None
        assert h.unassigned_table_id is None


class TestRefreshUnassigned:
    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_no_gsheet_cog_is_noop(self, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        bot = MagicMock()
        bot.get_cog.return_value = None
        svc = PuzzleService(bot)
        await svc.refresh_unassigned(make_hunt(unassigned_sheet_page_id=None, unassigned_table_id=None))   # must not raise

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_creates_tab_and_table_lazily(self, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = make_hunt(unassigned_sheet_page_id=None, unassigned_table_id=None)
        PuzzleDb.get_unassigned.return_value = []
        gsheet = MagicMock()
        gsheet.create_blank_tab = AsyncMock(return_value=42)
        gsheet.ensure_unassigned_table = AsyncMock(return_value=7)
        gsheet.reconcile_table = AsyncMock()
        bot = MagicMock(); bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)
        await svc.refresh_unassigned(hunt)
        assert hunt.unassigned_sheet_page_id == 42
        assert hunt.unassigned_table_id == 7
        HuntDb.commit.assert_called_once_with(hunt)

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_reconcile_shapes_rows_uppercase_and_blank_unsolved(self, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = make_hunt(unassigned_sheet_page_id=None, unassigned_table_id=None); hunt.unassigned_sheet_page_id = 42; hunt.unassigned_table_id = 7
        PuzzleDb.get_unassigned.return_value = [
            make_puzzle(id=1, name="Solved One", solved=True, solution="cat"),
            make_puzzle(id=2, name="Open Two", solved=False, solution=None),
        ]
        gsheet = MagicMock()
        gsheet.reconcile_table = AsyncMock()
        bot = MagicMock(); bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)
        await svc.refresh_unassigned(hunt)
        # The full desired set is handed to reconcile_table (which does the single
        # diff against the live sheet): rows carry uppercased answer + length,
        # blank for unsolved. Length col is flagged numeric.
        kwargs = gsheet.reconcile_table.call_args.kwargs
        desired = kwargs["desired_rows"]
        assert [1, "Solved One", "CAT", 3] in desired
        assert [2, "Open Two", "", ""] in desired
        assert kwargs["numeric_cols"] == (3,)

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_empty_pool_reconciles_empty_desired(self, PuzzleDb, HuntDb):
        # Pruning of classified rows now happens INSIDE reconcile_table (it diffs the
        # full desired set against the live sheet). At the service level we only
        # assert the empty pool is forwarded as an empty desired set.
        from bot.core.puzzle_service import PuzzleService
        hunt = make_hunt(unassigned_sheet_page_id=None, unassigned_table_id=None); hunt.unassigned_sheet_page_id = 42; hunt.unassigned_table_id = 7
        PuzzleDb.get_unassigned.return_value = []          # pool now empty
        gsheet = MagicMock()
        gsheet.reconcile_table = AsyncMock()
        bot = MagicMock(); bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)
        await svc.refresh_unassigned(hunt)
        assert gsheet.reconcile_table.call_args.kwargs["desired_rows"] == []
