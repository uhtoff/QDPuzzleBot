"""Tests for M9 evidence board — service layer and data model."""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_hunt, make_puzzle


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

    def test_import_dict_reattaches_utc_tzinfo_to_created_at(self):
        """#33: created_at is stored naive UTC; import_dict must reattach tzinfo
        like PuzzleData.import_dict does for every *_time field, or evidence
        timestamps serialize/compare inconsistently with the rest of the app."""
        from bot.store.evidence_data import EvidenceData
        row = {
            "id": 7, "hunt_id": 10, "source_puzzle_id": 5,
            "target_puzzle_id": None, "note": "n",
            "created_at": datetime.datetime(2026, 6, 23, 14, 0, 0),
        }
        e = EvidenceData.import_dict(row)
        assert e.created_at.tzinfo == datetime.timezone.utc
        assert e.created_at.isoformat() == "2026-06-23T14:00:00+00:00"

    def test_import_dict_handles_null_created_at(self):
        from bot.store.evidence_data import EvidenceData
        row = {"id": 1, "hunt_id": 10, "source_puzzle_id": 5, "note": "n", "created_at": None}
        e = EvidenceData.import_dict(row)
        assert e.created_at is None


class TestLogClues:
    """PuzzleService.log_clues — inserts one item per note."""

    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_inserts_one_item_per_note(self, EvidenceDb):
        from bot.core.puzzle_service import PuzzleService
        item1, item2 = MagicMock(), MagicMock()
        EvidenceDb.log.side_effect = [item1, item2]

        svc = PuzzleService(MagicMock())
        hunt = make_hunt()
        puzzle = make_puzzle(id=5, hunt_id=10)

        result = await svc.log_clues(hunt, puzzle, ["note A", "note B"])

        assert EvidenceDb.log.call_count == 2
        EvidenceDb.log.assert_any_call(10, 5, "note A")
        EvidenceDb.log.assert_any_call(10, 5, "note B")
        assert result == [item1, item2]

    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_single_note(self, EvidenceDb):
        from bot.core.puzzle_service import PuzzleService
        item = MagicMock()
        EvidenceDb.log.return_value = item

        svc = PuzzleService(MagicMock())
        result = await svc.log_clues(make_hunt(), make_puzzle(id=3, hunt_id=10), ["solo note"])

        EvidenceDb.log.assert_called_once_with(10, 3, "solo note")
        assert result == [item]

    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_empty_list_returns_empty(self, EvidenceDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        result = await svc.log_clues(make_hunt(), make_puzzle(id=3, hunt_id=10), [])
        EvidenceDb.log.assert_not_called()
        assert result == []

    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_auto_routes_to_sole_informs_target(self, EvidenceDb, TagGroupDb):
        from bot.core.puzzle_service import PuzzleService
        item = MagicMock(); item.id = 77
        EvidenceDb.log.return_value = item
        group = MagicMock(); group.target_puzzle_id = 99
        TagGroupDb.get_by_attr.return_value = group

        svc = PuzzleService(MagicMock())
        puzzle = make_puzzle(id=5, hunt_id=10, tags=[3])
        puzzle.tag_edges = {3: {"informs"}}
        await svc.log_clues(make_hunt(), puzzle, ["clue"])

        EvidenceDb.set_target.assert_called_once_with(77, 99)

    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_no_route_when_multiple_informs(self, EvidenceDb, TagGroupDb):
        from bot.core.puzzle_service import PuzzleService
        EvidenceDb.log.return_value = MagicMock()
        g1 = MagicMock(); g1.target_puzzle_id = 99
        g2 = MagicMock(); g2.target_puzzle_id = 100
        TagGroupDb.get_by_attr.side_effect = lambda id: {3: g1, 4: g2}[id]

        svc = PuzzleService(MagicMock())
        puzzle = make_puzzle(id=5, hunt_id=10, tags=[3, 4])
        puzzle.tag_edges = {3: {"informs"}, 4: {"informs"}}
        await svc.log_clues(make_hunt(), puzzle, ["clue"])

        EvidenceDb.set_target.assert_not_called()

    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_no_route_when_feeds_only(self, EvidenceDb, TagGroupDb):
        from bot.core.puzzle_service import PuzzleService
        EvidenceDb.log.return_value = MagicMock()

        svc = PuzzleService(MagicMock())
        puzzle = make_puzzle(id=5, hunt_id=10, tags=[3])
        puzzle.tag_edges = {3: {"feeds"}}
        await svc.log_clues(make_hunt(), puzzle, ["clue"])

        EvidenceDb.set_target.assert_not_called()


class TestRefreshEvidence:
    """PuzzleService.refresh_evidence — native Table via ensure_evidence_table +
    reconcile_table, lazy tab/table create, hidden id key column."""

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_creates_tab_and_table_lazily(self, EvidenceDb, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = make_hunt()
        hunt.evidence_sheet_page_id = None
        hunt.evidence_table_id = None

        EvidenceDb.get_all.return_value = []

        gsheet = MagicMock()
        gsheet.create_blank_tab = AsyncMock(return_value=42)
        gsheet.ensure_evidence_table = AsyncMock(return_value=7)
        gsheet.reconcile_table = AsyncMock()

        bot = MagicMock()
        bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)

        await svc.refresh_evidence(hunt)

        assert hunt.evidence_sheet_page_id == 42
        assert hunt.evidence_table_id == 7
        gsheet.ensure_evidence_table.assert_awaited_once_with(42)
        HuntDb.commit.assert_called_once_with(hunt)
        gsheet.reconcile_table.assert_awaited_once()

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_skips_create_when_already_set(self, EvidenceDb, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = make_hunt()
        hunt.evidence_sheet_page_id = 99  # tab already exists
        hunt.evidence_table_id = 7        # table already exists

        EvidenceDb.get_all.return_value = []

        gsheet = MagicMock()
        gsheet.create_blank_tab = AsyncMock()
        gsheet.ensure_evidence_table = AsyncMock()
        gsheet.reconcile_table = AsyncMock()

        bot = MagicMock()
        bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)

        await svc.refresh_evidence(hunt)

        gsheet.create_blank_tab.assert_not_called()
        gsheet.ensure_evidence_table.assert_not_called()
        HuntDb.commit.assert_not_called()
        gsheet.reconcile_table.assert_awaited_once()

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_reconcile_rows_lead_with_id_and_resolved_names(self, EvidenceDb, PuzzleDb, HuntDb):
        from bot.core.puzzle_service import PuzzleService
        hunt = make_hunt()
        hunt.evidence_sheet_page_id = 99
        hunt.evidence_table_id = 7

        item = MagicMock()
        item.id = 5
        item.source_puzzle_id = 1
        item.target_puzzle_id = 2
        item.note = "a clue"
        item.created_at = datetime.datetime(2026, 6, 23, 12, 0)
        EvidenceDb.get_all.return_value = [item]
        PuzzleDb.get_by_attr.side_effect = lambda id: {
            1: make_puzzle(id=1, name="Feeder One"),
            2: make_puzzle(id=2, name="Meta Two"),
        }[id]

        gsheet = MagicMock()
        gsheet.reconcile_table = AsyncMock()
        bot = MagicMock()
        bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)

        await svc.refresh_evidence(hunt)

        kwargs = gsheet.reconcile_table.call_args.kwargs
        assert kwargs["page_id"] == 99
        # 5 bot-owned columns: id, Source Puzzle, Note, Target Puzzle, Logged At.
        assert kwargs["data_col_count"] == 5
        # Row leads with the hidden evidence id (the unique reconcile key) then the
        # resolved source/target puzzle names.
        assert kwargs["desired_rows"] == [
            [5, "Feeder One", "a clue", "Meta Two", "2026-06-23 12:00"]
        ]

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_no_gsheet_cog_is_noop(self, EvidenceDb, PuzzleDb, HuntDb):
        """refresh_evidence is a no-op when the gsheet cog is unavailable."""
        from bot.core.puzzle_service import PuzzleService
        hunt = make_hunt()
        hunt.evidence_sheet_page_id = 99

        EvidenceDb.get_all.return_value = []

        bot = MagicMock()
        bot.get_cog.return_value = None  # no gsheet cog
        svc = PuzzleService(bot)

        # Must not raise
        await svc.refresh_evidence(hunt)


class TestRefreshInformsTargets:
    """PuzzleService.refresh_informs_targets / refresh_target_for_puzzle —
    re-render the target roll-up after a clue is logged/routed."""

    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    async def test_refreshes_informed_target(self, TagGroupDb):
        from bot.core.puzzle_service import PuzzleService
        group = MagicMock(); group.target_puzzle_id = 99
        TagGroupDb.get_by_attr.return_value = group
        svc = PuzzleService(MagicMock())
        svc.refresh_target = AsyncMock()
        puzzle = make_puzzle(id=5, hunt_id=10, tags=[3])
        puzzle.tag_edges = {3: {"informs"}}
        hunt = make_hunt()

        await svc.refresh_informs_targets(hunt, puzzle)

        svc.refresh_target.assert_awaited_once_with(hunt, group)

    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    async def test_skips_when_feeds_only(self, TagGroupDb):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        svc.refresh_target = AsyncMock()
        puzzle = make_puzzle(id=5, hunt_id=10, tags=[3])
        puzzle.tag_edges = {3: {"feeds"}}

        await svc.refresh_informs_targets(make_hunt(), puzzle)

        svc.refresh_target.assert_not_awaited()

    @patch("bot.core.puzzle_service.TagGroupJsonDb")
    async def test_refresh_target_for_puzzle(self, TagGroupDb):
        from bot.core.puzzle_service import PuzzleService
        group = MagicMock()
        TagGroupDb.get_by_attr.return_value = group
        svc = PuzzleService(MagicMock())
        svc.refresh_target = AsyncMock()
        hunt = make_hunt()

        await svc.refresh_target_for_puzzle(hunt, 99)

        TagGroupDb.get_by_attr.assert_called_once_with(target_puzzle_id=99)
        svc.refresh_target.assert_awaited_once_with(hunt, group)

    async def test_refresh_target_for_puzzle_none_is_noop(self):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService(MagicMock())
        svc.refresh_target = AsyncMock()
        await svc.refresh_target_for_puzzle(make_hunt(), None)
        svc.refresh_target.assert_not_awaited()


class TestMySQLEvidenceDbMethods:
    """MySQLEvidenceDb.delete and update_note — SQL execution and commit."""

    def _make_db(self):
        from bot.store.mysqldb import MySQLEvidenceDb
        from unittest.mock import MagicMock
        mock_mydb = MagicMock()
        mock_cursor = MagicMock()
        # delete() uses the plain cursor; update_note() reads back via dict_cursor.
        mock_mydb.cursor.return_value = mock_cursor
        mock_mydb.dict_cursor.return_value = mock_cursor
        db = MySQLEvidenceDb(mydb=mock_mydb)
        return db, mock_mydb, mock_cursor

    def test_delete_executes_delete_sql_and_commits(self):
        db, mock_mydb, mock_cursor = self._make_db()
        db.delete(42)
        mock_cursor.execute.assert_called_once_with(
            "DELETE FROM evidence_items WHERE id = %s", (42,)
        )
        mock_mydb.commit.assert_called_once()
        mock_cursor.close.assert_called()

    def test_delete_nonexistent_id_does_not_raise(self):
        db, mock_mydb, mock_cursor = self._make_db()
        # Should not raise even if no rows matched
        db.delete(999)
        mock_cursor.execute.assert_called_once()

    def test_update_note_executes_update_then_select(self):
        import datetime
        from bot.store.evidence_data import EvidenceData
        db, mock_mydb, mock_cursor = self._make_db()
        row = {
            "id": 7, "hunt_id": 10, "source_puzzle_id": 5,
            "target_puzzle_id": None, "note": "new text",
            "created_at": datetime.datetime(2026, 6, 28, 12, 0, 0),
        }
        mock_cursor.fetchone.return_value = row

        result = db.update_note(7, "new text")

        calls = mock_cursor.execute.call_args_list
        assert any("UPDATE evidence_items SET note" in str(c) for c in calls)
        assert any("SELECT * FROM evidence_items WHERE id" in str(c) for c in calls)
        mock_mydb.commit.assert_called_once()
        assert isinstance(result, EvidenceData)
        assert result.note == "new text"

    def test_update_note_returns_none_for_missing_id(self):
        db, mock_mydb, mock_cursor = self._make_db()
        mock_cursor.fetchone.return_value = None

        result = db.update_note(999, "text")

        assert result is None

    def test_log_returns_tz_aware_created_at(self):
        """#33: the in-memory item log() returns must match what a re-fetch via
        import_dict would produce, so it serializes consistently either way."""
        import datetime
        db, mock_mydb, mock_cursor = self._make_db()
        mock_cursor.lastrowid = 42

        result = db.log(10, 5, "a clue")

        assert result.created_at.tzinfo == datetime.timezone.utc
