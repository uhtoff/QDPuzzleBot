"""Tests for M9 evidence board — service layer and data model."""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_puzzle


def _make_hunt(id=10):
    from bot.store.hunt_data import HuntData
    h = HuntData()
    h.id = id
    h.google_sheet_id = "SHEET1"
    h.archive_google_sheet_id = "ARCHIVE1"
    h.evidence_sheet_page_id = None
    return h


class TestEvidenceData:
    """EvidenceData dataclass — fields and import_dict."""

    def test_default_fields(self):
        from bot.store.evidence_data import EvidenceData
        e = EvidenceData()
        assert e.id == 0
        assert e.hunt_id == 0
        assert e.source_puzzle_id == 0
        assert e.target_puzzle_id is None
        assert e.note == ""
        assert e.created_at is None

    def test_import_dict_reads_all_fields(self):
        from bot.store.evidence_data import EvidenceData
        row = {
            "id": 7,
            "hunt_id": 10,
            "source_puzzle_id": 5,
            "target_puzzle_id": 12,
            "note": "Suspect 3 likes Bourbon biscuits",
            "created_at": datetime.datetime(2026, 6, 23, 14, 0, 0),
        }
        e = EvidenceData.import_dict(row)
        assert e.id == 7
        assert e.hunt_id == 10
        assert e.source_puzzle_id == 5
        assert e.target_puzzle_id == 12
        assert e.note == "Suspect 3 likes Bourbon biscuits"

    def test_import_dict_handles_null_target(self):
        from bot.store.evidence_data import EvidenceData
        row = {
            "id": 1, "hunt_id": 10, "source_puzzle_id": 5,
            "target_puzzle_id": None,
            "note": "some note",
            "created_at": datetime.datetime(2026, 6, 23, 12, 0, 0),
        }
        e = EvidenceData.import_dict(row)
        assert e.target_puzzle_id is None


class TestLogClues:
    """PuzzleService.log_clues — inserts one item per note."""

    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    def test_inserts_one_item_per_note(self, EvidenceDb):
        from bot.core.puzzle_service import PuzzleService
        item1, item2 = MagicMock(), MagicMock()
        EvidenceDb.log.side_effect = [item1, item2]

        svc = PuzzleService(MagicMock())
        hunt = _make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)

        result = svc.log_clues(hunt, puzzle, ["note A", "note B"])

        assert EvidenceDb.log.call_count == 2
        EvidenceDb.log.assert_any_call(10, 5, "note A")
        EvidenceDb.log.assert_any_call(10, 5, "note B")
        assert result == [item1, item2]

    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    def test_single_note(self, EvidenceDb):
        from bot.core.puzzle_service import PuzzleService
        item = MagicMock()
        EvidenceDb.log.return_value = item

        svc = PuzzleService(MagicMock())
        result = svc.log_clues(_make_hunt(), make_puzzle(id=3, hunt_id=10), ["solo note"])

        EvidenceDb.log.assert_called_once_with(10, 3, "solo note")
        assert result == [item]

    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    def test_empty_list_returns_empty(self, EvidenceDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        result = svc.log_clues(_make_hunt(), make_puzzle(id=3, hunt_id=10), [])
        EvidenceDb.log.assert_not_called()
        assert result == []


class TestRefreshEvidence:
    """PuzzleService.refresh_evidence — sheet tab, lazy create, aggregate_to_tab."""

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_creates_tab_lazily_when_none(self, EvidenceDb, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = _make_hunt()
        hunt.evidence_sheet_page_id = None

        item = MagicMock()
        item.source_puzzle_id = 5
        item.target_puzzle_id = None
        item.note = "a clue"
        item.created_at = datetime.datetime(2026, 6, 23, 12, 0)
        EvidenceDb.get_all.return_value = [item]
        PuzzleDb.get_by_attr.return_value = make_puzzle(id=5, name="Feeder Five")

        gsheet = MagicMock()
        async def fake_create_blank_tab(spreadsheet_id, name):
            return 42
        gsheet.create_blank_tab = fake_create_blank_tab
        gsheet.aggregate_to_tab = AsyncMock()

        bot = MagicMock()
        bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)

        await svc.refresh_evidence(hunt)

        assert hunt.evidence_sheet_page_id == 42
        HuntDb.commit.assert_called_once_with(hunt)
        gsheet.aggregate_to_tab.assert_called_once()

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_skips_tab_create_when_already_set(self, EvidenceDb, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = _make_hunt()
        hunt.evidence_sheet_page_id = 99  # already exists

        EvidenceDb.get_all.return_value = []

        gsheet = MagicMock()
        gsheet.create_blank_tab = AsyncMock()
        gsheet.aggregate_to_tab = AsyncMock()

        bot = MagicMock()
        bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)

        await svc.refresh_evidence(hunt)

        gsheet.create_blank_tab.assert_not_called()
        HuntDb.commit.assert_not_called()
        gsheet.aggregate_to_tab.assert_called_once()

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_aggregate_to_tab_receives_correct_columns(self, EvidenceDb, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = _make_hunt()
        hunt.evidence_sheet_page_id = 99

        EvidenceDb.get_all.return_value = []

        gsheet = MagicMock()
        gsheet.aggregate_to_tab = AsyncMock()

        bot = MagicMock()
        bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)

        await svc.refresh_evidence(hunt)

        cols = gsheet.aggregate_to_tab.call_args[0][2]
        assert "Source Puzzle" in cols
        assert "Note" in cols
        assert "Target Puzzle" in cols
        assert "Logged At" in cols

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_no_gsheet_cog_is_noop(self, EvidenceDb, PuzzleDb, HuntDb):
        """refresh_evidence is a no-op when the gsheet cog is unavailable."""
        from bot.core.puzzle_service import PuzzleService
        hunt = _make_hunt()
        hunt.evidence_sheet_page_id = 99

        EvidenceDb.get_all.return_value = []

        bot = MagicMock()
        bot.get_cog.return_value = None  # no gsheet cog
        svc = PuzzleService(bot)

        # Must not raise
        await svc.refresh_evidence(hunt)
