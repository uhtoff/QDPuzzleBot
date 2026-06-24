"""Tests for meta-matching service layer + HuntData fields."""
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_puzzle


def _make_hunt(id=10):
    from bot.store.hunt_data import HuntData
    h = HuntData()
    h.id = id
    h.google_sheet_id = "SHEET1"
    h.unassigned_sheet_page_id = None
    h.unassigned_table_id = None
    return h


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
        await svc.refresh_unassigned(_make_hunt())   # must not raise

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_creates_tab_and_table_lazily(self, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = _make_hunt()
        PuzzleDb.get_unassigned.return_value = []
        gsheet = MagicMock()
        gsheet.create_blank_tab = AsyncMock(return_value=42)
        gsheet.ensure_unassigned_table = AsyncMock(return_value=7)
        gsheet.read_unassigned_keys = AsyncMock(return_value=[])
        gsheet.apply_unassigned_changes = AsyncMock()
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
        hunt = _make_hunt(); hunt.unassigned_sheet_page_id = 42; hunt.unassigned_table_id = 7
        PuzzleDb.get_unassigned.return_value = [
            make_puzzle(id=1, name="Solved One", solved=True, solution="cat"),
            make_puzzle(id=2, name="Open Two", solved=False, solution=None),
        ]
        gsheet = MagicMock()
        gsheet.read_unassigned_keys = AsyncMock(return_value=[])     # nothing in sheet yet
        gsheet.apply_unassigned_changes = AsyncMock()
        bot = MagicMock(); bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)
        await svc.refresh_unassigned(hunt)
        # appends carry uppercased answer + length, blank for unsolved
        appends = gsheet.apply_unassigned_changes.call_args.kwargs.get("appends") \
            or gsheet.apply_unassigned_changes.call_args[0][2]
        assert [1, "Solved One", "CAT", 3] in appends
        assert [2, "Open Two", "", ""] in appends

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    async def test_classified_row_is_pruned(self, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = _make_hunt(); hunt.unassigned_sheet_page_id = 42; hunt.unassigned_table_id = 7
        PuzzleDb.get_unassigned.return_value = []          # pool now empty
        gsheet = MagicMock()
        gsheet.read_unassigned_keys = AsyncMock(return_value=[5])    # id 5 still in sheet
        gsheet.apply_unassigned_changes = AsyncMock()
        bot = MagicMock(); bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)
        await svc.refresh_unassigned(hunt)
        prunes = gsheet.apply_unassigned_changes.call_args.kwargs.get("prunes") \
            or gsheet.apply_unassigned_changes.call_args[0][3]
        assert 5 in prunes
