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
        assert e.source_puzzle_id is None
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

    def test_media_id_defaults_to_none(self):
        from bot.store.evidence_data import EvidenceData
        assert EvidenceData().media_id is None

    def test_source_puzzle_id_defaults_to_none(self):
        """Evidence can belong to the hunt rather than to any one puzzle, so the
        default source is 'none', not the sentinel 0."""
        from bot.store.evidence_data import EvidenceData
        assert EvidenceData().source_puzzle_id is None

    def test_import_dict_reads_null_source_and_media(self):
        from bot.store.evidence_data import EvidenceData
        e = EvidenceData.import_dict({
            "id": 1, "hunt_id": 10,
            "source_puzzle_id": None,
            "target_puzzle_id": 4,
            "note": "found on the poster",
            "media_id": 7,
            "created_at": None,
        })
        assert e.source_puzzle_id is None
        assert e.media_id == 7

    def test_source_sheet_id_defaults_to_none(self):
        from bot.store.evidence_data import EvidenceData
        assert EvidenceData().source_sheet_id is None

    def test_import_dict_reads_the_source_sheet(self):
        from bot.store.evidence_data import EvidenceData
        e = EvidenceData.import_dict({
            "id": 1, "hunt_id": 10, "source_puzzle_id": None,
            "source_sheet_id": 4, "target_puzzle_id": 9,
            "note": "found in the alt text", "media_id": None,
            "created_at": None,
        })
        assert e.source_sheet_id == 4
        assert e.source_puzzle_id is None


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
        EvidenceDb.log.assert_any_call(10, 5, "note A", media_id=None,
                                       source_sheet_id=None)
        EvidenceDb.log.assert_any_call(10, 5, "note B", media_id=None,
                                       source_sheet_id=None)
        assert result == [item1, item2]

    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_single_note(self, EvidenceDb):
        from bot.core.puzzle_service import PuzzleService
        item = MagicMock()
        EvidenceDb.log.return_value = item

        svc = PuzzleService(MagicMock())
        result = await svc.log_clues(make_hunt(), make_puzzle(id=3, hunt_id=10), ["solo note"])

        EvidenceDb.log.assert_called_once_with(10, 3, "solo note", media_id=None,
                                               source_sheet_id=None)
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
        # Text-only evidence: the Note cell now goes through evidence_cell_content
        # (Task 10), which only appends a media URL when media_id is truthy.
        item.media_id = None
        item.created_at = datetime.datetime(2026, 6, 23, 12, 0)
        EvidenceDb.get_all.return_value = [item]
        PuzzleDb.get_names_by_ids.return_value = {1: "Feeder One", 2: "Meta Two"}

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


class TestRefreshEvidenceNullSource:
    """A null source_puzzle_id must not render as the literal 'Puzzle None'
    (the old fallback interpolated it blindly), and the Note column should be
    backed by evidence_cell_content (Task 9) so the media URL surfaces here too."""

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_null_source_is_an_em_dash_but_a_real_source_still_resolves(
        self, EvidenceDb, PuzzleDb, HuntDb
    ):
        """Two items, not one: a fix that renders EVERY row's source cell as
        '—' -- real source included -- would still pass a single-item test.
        Pinning a resolved name alongside the null case rules that out.
        """
        from bot.core.puzzle_service import PuzzleService
        hunt = make_hunt()
        hunt.evidence_sheet_page_id = 99
        hunt.evidence_table_id = 7

        sourced = MagicMock()
        sourced.id = 5
        sourced.source_puzzle_id = 1
        sourced.target_puzzle_id = 2
        sourced.note = "a clue"
        sourced.media_id = None
        # Explicit: an unset MagicMock attribute auto-vivifies TRUTHY, so
        # leaving it off makes refresh_evidence treat this as sheet-sourced.
        sourced.source_sheet_id = None
        sourced.source_region_id = None
        sourced.created_at = datetime.datetime(2026, 6, 23, 12, 0)

        sourceless = MagicMock()
        sourceless.id = 9
        sourceless.source_puzzle_id = None
        sourceless.target_puzzle_id = None
        sourceless.note = "a fact"
        sourceless.media_id = None
        sourceless.source_sheet_id = None
        sourceless.source_region_id = None
        sourceless.created_at = None

        EvidenceDb.get_all.return_value = [sourced, sourceless]
        PuzzleDb.get_names_by_ids.return_value = {1: "Feeder One", 2: "Meta Two"}

        gsheet = MagicMock()
        gsheet.reconcile_table = AsyncMock()
        bot = MagicMock()
        bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)

        await svc.refresh_evidence(hunt)

        rows = gsheet.reconcile_table.call_args.kwargs["desired_rows"]
        by_id = {r[0]: r for r in rows}
        assert by_id[5][1] == "Feeder One"   # real source still resolves
        assert by_id[9][1] == "—"            # null source, not "Puzzle None"

        # None must never reach the batched name-lookup query.
        queried_ids = PuzzleDb.get_names_by_ids.call_args.args[0]
        assert None not in queried_ids

    @patch("bot.core.puzzle_service.HuntJsonDb")
    @patch("bot.core.puzzle_service.PuzzleJsonDb")
    @patch("bot.core.puzzle_service.EvidenceJsonDb")
    async def test_note_column_uses_evidence_cell_content(self, EvidenceDb, PuzzleDb, HuntDb):
        """Task 10 also swaps the Note cell for evidence_cell_content (Task 9),
        so the media URL surfaces on the hunt-wide Evidence tab too."""
        from bot.core.puzzle_service import PuzzleService
        hunt = make_hunt()
        hunt.evidence_sheet_page_id = 99
        hunt.evidence_table_id = 7

        item = MagicMock()
        item.id = 9
        item.source_puzzle_id = None
        item.target_puzzle_id = None
        item.note = "a fact"
        item.media_id = 7
        item.created_at = None

        EvidenceDb.get_all.return_value = [item]
        PuzzleDb.get_names_by_ids.return_value = {}

        gsheet = MagicMock()
        gsheet.reconcile_table = AsyncMock()
        bot = MagicMock()
        bot.get_cog.return_value = gsheet
        svc = PuzzleService(bot)

        with patch("bot.core.puzzle_service.config") as cfg:
            cfg.web_frontend_url = "https://puzzweb.example"
            await svc.refresh_evidence(hunt)

        rows = gsheet.reconcile_table.call_args.kwargs["desired_rows"]
        assert "https://puzzweb.example/api/media/7" in rows[0][2]


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


class TestEvidenceDbLog:
    """MySQLEvidenceDb.log widened to accept a null source puzzle and an
    optional media id (Plan A: evidence can belong to the hunt rather than
    to any one puzzle, and can carry an image)."""

    def _db(self):
        from bot.store.mysqldb import MySQLEvidenceDb
        mydb = MagicMock()
        cursor = MagicMock()
        cursor.lastrowid = 11
        mydb.dict_cursor.return_value = cursor
        return MySQLEvidenceDb(mydb=mydb), cursor

    def test_log_with_a_source_puzzle(self):
        db, _ = self._db()
        e = db.log(hunt_id=10, source_puzzle_id=5, note="a clue")
        assert e.id == 11
        assert e.source_puzzle_id == 5
        assert e.note == "a clue"
        assert e.media_id is None

    def test_log_without_a_source_puzzle(self):
        db, _ = self._db()
        e = db.log(hunt_id=10, source_puzzle_id=None, note="seen on the poster")
        assert e.source_puzzle_id is None
        assert e.note == "seen on the poster"

    def test_log_with_a_media_id(self):
        db, _ = self._db()
        e = db.log(hunt_id=10, source_puzzle_id=None, note="", media_id=7)
        assert e.media_id == 7
        assert e.note == ""

    def test_log_sends_parameters_in_column_order(self):
        """Every other assertion in this class reads fields off the RETURNED
        EvidenceData, and those are built from log's own local parameters --
        never read back from the cursor. So a SQL tuple that transposes two
        adjacent columns (note/media_id, both nullable) would corrupt every
        written row while leaving all of them green. Assert on what was
        actually sent to execute, in the order the columns are declared,
        the same standard applied to MySQLMediaDb.create in test_media_store.py.
        """
        db, cursor = self._db()
        # source_sheet_id and source_region_id are given DIFFERENT values: they
        # are adjacent, both nullable ints, and so are exactly the pair a
        # transposition would swap unnoticed. Passing None for either would hide
        # that -- and passing both is legal here only because this test drives the
        # DAO directly; the service methods above enforce at-most-one-source.
        db.log(hunt_id=10, source_puzzle_id=None, note="seen on the poster",
               media_id=7, source_sheet_id=3, source_region_id=5)
        sql, params = cursor.execute.call_args[0]
        columns = [c.strip() for c in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
        # source_sheet_id (Plan C) is sixth and source_region_id (Plan B2a)
        # seventh. Still an EXACT match, not a subset -- the point of this test is
        # that the tuple's order is pinned.
        assert columns == ["hunt_id", "source_puzzle_id", "note", "media_id",
                           "source_sheet_id", "source_region_id", "created_at"]
        assert params[:6] == (10, None, "seen on the poster", 7, 3, 5)


class TestLogCluesSourceless:
    """PuzzleService.log_clues(hunt, puzzle=None, ...) — evidence that belongs
    to the hunt rather than to any one puzzle. No source means no informs edge
    to auto-route from, so the item is created untargeted."""

    async def test_logs_with_a_null_source_and_no_auto_routing(self):
        from unittest.mock import MagicMock, patch
        from bot.core.puzzle_service import PuzzleService
        from tests.conftest import make_hunt

        hunt = make_hunt(id=10)
        svc = PuzzleService.__new__(PuzzleService)
        svc.notifier = MagicMock(board_changed=AsyncMock())

        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb:
            edb.log.side_effect = lambda hunt_id, source_puzzle_id, note, media_id=None, \
                source_sheet_id=None: MagicMock(id=1, note=note)
            items = await svc.log_clues(hunt, None, ["seen on the poster"])

        assert len(items) == 1
        edb.log.assert_called_once()
        assert edb.log.call_args.args[1] is None or \
            edb.log.call_args.kwargs.get("source_puzzle_id") is None
        edb.set_target.assert_not_called()

    async def test_passes_media_id_through(self):
        from unittest.mock import MagicMock, patch
        from bot.core.puzzle_service import PuzzleService
        from tests.conftest import make_hunt

        svc = PuzzleService.__new__(PuzzleService)
        svc.notifier = MagicMock(board_changed=AsyncMock())
        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb:
            edb.log.side_effect = lambda hunt_id, source_puzzle_id, note, media_id=None, \
                source_sheet_id=None: MagicMock(id=1, note=note, media_id=media_id)
            items = await svc.log_clues(make_hunt(id=10), None, [""], media_id=7)

        assert items[0].media_id == 7
        # Guard against the call silently losing media_id on the way to the DAO
        # (e.g. never forwarding the kwarg at all): read back what was actually
        # sent, not just what the side_effect happened to echo.
        assert edb.log.call_args.kwargs.get("media_id") == 7

    async def test_auto_routing_still_happens_for_a_sourced_clue(self):
        """Regression guard: the None-source early-exit in log_clues must not
        also swallow the existing sole-informs-target auto-routing for clues
        that DO have a source puzzle."""
        from unittest.mock import MagicMock, patch
        from bot.core.puzzle_service import PuzzleService
        from tests.conftest import make_hunt

        svc = PuzzleService.__new__(PuzzleService)
        svc.notifier = MagicMock(board_changed=AsyncMock())
        item = MagicMock(); item.id = 77
        puzzle = make_puzzle(id=5, hunt_id=10, tags=[3])
        puzzle.tag_edges = {3: {"informs"}}
        group = MagicMock(); group.target_puzzle_id = 99

        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb:
            edb.log.return_value = item
            tgdb.get_by_attr.return_value = group
            await svc.log_clues(make_hunt(id=10), puzzle, ["clue"])

        edb.set_target.assert_called_once_with(77, 99)

    async def test_link_informs_is_skipped_without_a_source(self):
        """An informs edge joins two puzzles; a bare observation is not one."""
        from unittest.mock import patch
        from bot.core.puzzle_service import PuzzleService
        from tests.conftest import make_hunt

        svc = PuzzleService.__new__(PuzzleService)
        with patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb:
            await svc.link_informs_for_evidence(make_hunt(id=10), None, 4)
        tgdb.get_by_attr.assert_not_called()

    async def test_unlink_informs_is_skipped_without_a_source(self):
        """Same guard on the removal path — nothing to look up or untag."""
        from unittest.mock import patch
        from bot.core.puzzle_service import PuzzleService
        from tests.conftest import make_hunt

        svc = PuzzleService.__new__(PuzzleService)
        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb:
            await svc.unlink_informs_for_evidence(make_hunt(id=10), None, 4)
        edb.get_all.assert_not_called()
        tgdb.get_by_attr.assert_not_called()


class TestEvidenceDbLogSourceSheet:
    def _db(self):
        from bot.store.mysqldb import MySQLEvidenceDb
        mydb = MagicMock()
        cursor = MagicMock()
        cursor.lastrowid = 11
        mydb.dict_cursor.return_value = cursor
        return MySQLEvidenceDb(mydb=mydb), cursor

    def test_log_with_a_source_sheet(self):
        db, _ = self._db()
        e = db.log(hunt_id=10, source_puzzle_id=None, note="alt text",
                   source_sheet_id=4)
        assert e.source_sheet_id == 4
        assert e.source_puzzle_id is None

    def test_log_writes_the_source_sheet_in_column_order(self):
        """Every other assertion here reads fields off the RETURNED object, built
        from log()'s own parameters -- so a SQL tuple that transposed two adjacent
        int columns (media_id and source_sheet_id are both nullable ints) would
        corrupt every row and stay green. Assert on what reached execute."""
        db, cursor = self._db()
        db.log(hunt_id=10, source_puzzle_id=None, note="n", media_id=7,
               source_sheet_id=4)
        sql, params = cursor.execute.call_args[0]
        columns = [c.strip() for c in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
        assert dict(zip(columns, params))["media_id"] == 7
        assert dict(zip(columns, params))["source_sheet_id"] == 4

    def test_source_sheet_defaults_to_none(self):
        db, cursor = self._db()
        db.log(hunt_id=10, source_puzzle_id=5, note="n")
        sql, params = cursor.execute.call_args[0]
        columns = [c.strip() for c in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
        assert dict(zip(columns, params))["source_sheet_id"] is None


class TestLogCluesSourceSheet:
    def _svc(self):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService.__new__(PuzzleService)
        svc.notifier = MagicMock(board_changed=AsyncMock())
        return svc

    async def test_passes_the_source_sheet_through(self):
        from types import SimpleNamespace
        svc = self._svc()
        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb:
            edb.log.return_value = SimpleNamespace(id=1, note="n")
            await svc.log_clues(make_hunt(id=10), None, ["n"], source_sheet_id=4)
        assert edb.log.call_args.kwargs["source_sheet_id"] == 4

    async def test_a_sheet_source_creates_no_informs_edge(self):
        """An informs edge joins two puzzles, and a working sheet is not one. The
        auto-routing derived from a source puzzle's sole informs target must not
        fire for a sheet-sourced item."""
        from types import SimpleNamespace
        svc = self._svc()
        svc._sole_informs_target = MagicMock(return_value=99)
        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb:
            edb.log.return_value = SimpleNamespace(id=1, note="n")
            await svc.log_clues(make_hunt(id=10), None, ["n"], source_sheet_id=4)
        svc._sole_informs_target.assert_not_called()
        edb.set_target.assert_not_called()

    async def test_default_is_no_source_sheet(self):
        from types import SimpleNamespace
        svc = self._svc()
        svc._sole_informs_target = MagicMock(return_value=None)
        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb:
            edb.log.return_value = SimpleNamespace(id=1, note="n")
            await svc.log_clues(make_hunt(id=10), make_puzzle(id=5, hunt_id=10), ["n"])
        assert edb.log.call_args.kwargs["source_sheet_id"] is None


class TestRefreshEvidenceSheetSource:
    """A sheet-sourced item names the sheet in the Source column; a sourceless
    one still renders the em dash Plan A introduced."""

    def _svc(self, gsheet):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService.__new__(PuzzleService)
        svc.notifier = MagicMock(board_changed=AsyncMock())
        svc._gsheet_cog = MagicMock(return_value=gsheet)
        return svc

    def _item(self, id, source_sheet_id):
        from types import SimpleNamespace
        return SimpleNamespace(id=id, source_puzzle_id=None,
                               source_sheet_id=source_sheet_id,
                               target_puzzle_id=None, note="n", media_id=None,
                               created_at=None)

    async def test_source_column_shows_the_sheet_name_and_the_em_dash(self):
        """Both rows in one render: a fix that blanked every source cell, or one
        that showed the sheet name for all of them, would pass a single-item
        test of either case alone."""
        gsheet = MagicMock()
        gsheet.reconcile_table = AsyncMock()
        svc = self._svc(gsheet)
        hunt = make_hunt(id=10, evidence_sheet_page_id=1, evidence_table_id=2)
        with patch("bot.core.puzzle_service.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.SheetsJsonDb") as sdb, \
             patch("bot.core.puzzle_service.resolve_puzzle_names", return_value={}):
            edb.get_all.return_value = [self._item(9, 4), self._item(8, None)]
            sdb.get_names_by_ids.return_value = {4: "alt text"}
            await svc.refresh_evidence(hunt)
        rows = gsheet.reconcile_table.await_args.kwargs["desired_rows"]
        assert [r[1] for r in rows] == ["alt text", "—"]
