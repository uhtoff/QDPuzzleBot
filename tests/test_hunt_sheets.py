"""Plan C: working sheets that belong to a hunt rather than to a puzzle."""
from unittest.mock import AsyncMock, MagicMock, patch


class TestAdditionalSheetData:
    def test_hunt_id_defaults_to_none(self):
        from bot.store.puzzle_data import AdditionalSheetData
        assert AdditionalSheetData().hunt_id is None

    def test_puzzle_id_defaults_to_none(self):
        """A sheet can belong to the hunt rather than to a puzzle, so the default
        is 'no puzzle', not the sentinel 0. 0 is not merely wrong-looking: the
        AdditionalSheetPuzzleID FK from 022 has no puzzles row with id 0, so a
        commit carrying it would fail on the constraint."""
        from bot.store.puzzle_data import AdditionalSheetData
        assert AdditionalSheetData().puzzle_id is None

    def test_a_hunt_level_sheet_commits_a_null_puzzle_id(self):
        """The generic commit() serialises every field by name. Assert on the SQL
        it actually built, not on the dataclass -- a default that silently became
        0 again would be invisible from the object side."""
        from bot.store.mysqldb import MySQLAdditionalSheetsDb
        from bot.store.puzzle_data import AdditionalSheetData
        mydb = MagicMock()
        cursor = MagicMock()
        cursor.lastrowid = 7
        mydb.cursor.return_value = cursor
        db = MySQLAdditionalSheetsDb(mydb=mydb)
        db.commit(AdditionalSheetData(hunt_id=10, puzzle_name="scratch"))
        sql, params = cursor.execute.call_args[0]
        fields = [f.strip(" `") for f in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
        assert dict(zip(fields, params))["puzzle_id"] is None
        assert dict(zip(fields, params))["hunt_id"] == 10


class TestGetByHunt:
    def _db(self):
        from bot.store.mysqldb import MySQLAdditionalSheetsDb
        mydb = MagicMock()
        cursor = MagicMock()
        mydb.dict_cursor.return_value = cursor
        return MySQLAdditionalSheetsDb(mydb=mydb), cursor

    def _row(self, id, name, page_id="P1"):
        return {"id": id, "google_page_id": page_id, "puzzle_id": None,
                "hunt_id": 10, "puzzle": 0, "puzzle_name": name,
                "solution": "", "solved": 0}

    def test_returns_sheet_objects(self):
        db, cursor = self._db()
        cursor.fetchall.return_value = [self._row(3, "alt text")]
        sheets = db.get_by_hunt(10)
        assert [s.id for s in sheets] == [3]
        assert sheets[0].puzzle_name == "alt text"
        assert sheets[0].hunt_id == 10
        assert sheets[0].puzzle_id is None

    def test_excludes_puzzle_owned_sheets(self):
        """A puzzle's sheet also belongs to a hunt transitively; only sheets with
        NO puzzle are hunt-level, and the query -- not the caller -- must say so."""
        db, cursor = self._db()
        cursor.fetchall.return_value = []
        db.get_by_hunt(10)
        sql, params = cursor.execute.call_args[0]
        assert "puzzle_id IS NULL" in sql
        assert params == (10,)

    def test_orders_by_id_so_the_list_is_stable(self):
        """!remove_sheet takes a 1-based index off !list_sheets, so two calls that
        disagree about order would delete the wrong sheet. Two rows, because one
        cannot have a wrong order."""
        db, cursor = self._db()
        cursor.fetchall.return_value = [self._row(3, "first"), self._row(9, "second")]
        sheets = db.get_by_hunt(10)
        assert [s.puzzle_name for s in sheets] == ["first", "second"]
        sql, _ = cursor.execute.call_args[0]
        assert "ORDER BY" in sql and "id" in sql.split("ORDER BY", 1)[1]

    def test_returns_empty_list_when_there_are_none(self):
        db, cursor = self._db()
        cursor.fetchall.return_value = None
        assert db.get_by_hunt(10) == []


class TestHuntSheetLock:
    async def test_second_holder_waits_for_the_first(self):
        import asyncio
        from bot.core.locks import hunt_sheet_lock
        order = []

        async def hold(tag, delay):
            async with hunt_sheet_lock(10):
                order.append(f"{tag}-in")
                await asyncio.sleep(delay)
                order.append(f"{tag}-out")

        await asyncio.gather(hold("a", 0.02), hold("b", 0))
        # Whichever ran first, its critical section is not interleaved with the
        # other's -- that is the whole point of the lock.
        assert order in (["a-in", "a-out", "b-in", "b-out"],
                         ["b-in", "b-out", "a-in", "a-out"])

    async def test_different_hunts_do_not_block_each_other(self):
        import asyncio
        from bot.core.locks import hunt_sheet_lock
        async with hunt_sheet_lock(10):
            # A second hunt's lock must be free while hunt 10's is held; a shared
            # registry keyed on a bare int would deadlock this.
            await asyncio.wait_for(
                asyncio.shield(_take_and_release(hunt_sheet_lock, 11)), timeout=1)

    async def test_registry_is_separate_from_the_puzzle_locks(self):
        """A hunt id and a puzzle id are different int spaces. Sharing one
        registry would make hunt 5 and puzzle 5 serialise against each other."""
        from bot.core.locks import _HUNT_SHEET_LOCKS, _PUZZLE_LOCKS
        assert _HUNT_SHEET_LOCKS is not _PUZZLE_LOCKS
        async with _PUZZLE_LOCKS[5]:
            assert not _HUNT_SHEET_LOCKS[5].locked()


async def _take_and_release(lock_cm, key):
    async with lock_cm(key):
        return True


class TestAddHuntSheet:
    def _svc(self, gsheet):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService.__new__(PuzzleService)
        svc.notifier = MagicMock(board_changed=AsyncMock())
        svc._gsheet_cog = MagicMock(return_value=gsheet)
        return svc

    def _gsheet(self):
        g = MagicMock()
        g.get_unique_tab_name = AsyncMock(side_effect=lambda name, sid=None: name)
        g.create_blank_tab = AsyncMock(return_value=555)
        return g

    async def test_creates_a_blank_tab_on_the_hunt_sheet_and_commits_a_row(self):
        from tests.conftest import make_hunt
        hunt = make_hunt(id=10, google_sheet_id="SHEET1", solved_sheet_id="ARCHIVE1")
        gsheet = self._gsheet()
        svc = self._svc(gsheet)
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sheet = await svc.add_hunt_sheet(hunt, "alt text")
        gsheet.create_blank_tab.assert_awaited_once()
        args = gsheet.create_blank_tab.await_args[0]
        # The hunt's MAIN spreadsheet, never the solved one -- a hunt sheet is
        # never solved, and the solved sheet is a different spreadsheet id.
        assert args[0] == "SHEET1"
        assert args[1] == "alt text"
        assert sheet.hunt_id == 10
        assert sheet.puzzle_id is None
        assert sheet.puzzle_name == "alt text"
        assert sheet.google_page_id == 555
        sdb.commit.assert_called_once_with(sheet)

    async def test_dedupes_the_tab_name(self):
        """create_blank_tab does not dedupe (add_new_sheet does), and Sheets
        rejects a duplicate tab title, so a second 'scratch' would blow up."""
        from tests.conftest import make_hunt
        gsheet = self._gsheet()
        gsheet.get_unique_tab_name = AsyncMock(return_value="scratch (2)")
        svc = self._svc(gsheet)
        with patch("bot.core.puzzle_service.SheetsJsonDb"):
            sheet = await svc.add_hunt_sheet(make_hunt(id=10), "scratch")
        assert gsheet.create_blank_tab.await_args[0][1] == "scratch (2)"
        assert sheet.puzzle_name == "scratch (2)"

    async def test_blank_name_falls_back_to_a_default(self):
        from tests.conftest import make_hunt
        gsheet = self._gsheet()
        svc = self._svc(gsheet)
        with patch("bot.core.puzzle_service.SheetsJsonDb"):
            sheet = await svc.add_hunt_sheet(make_hunt(id=10, name="Edric 2026"), "")
        assert sheet.puzzle_name == "Sheet for Edric 2026"

    async def test_no_gsheet_cog_still_records_the_sheet(self):
        """Mirrors add_sheet: a hunt with no Google integration still gets its
        row, just with no tab behind it."""
        from tests.conftest import make_hunt
        svc = self._svc(None)
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sheet = await svc.add_hunt_sheet(make_hunt(id=10), "alt text")
        assert sheet.google_page_id == ""
        sdb.commit.assert_called_once()


class TestRemoveHuntSheet:
    def _svc(self, gsheet):
        from bot.core.puzzle_service import PuzzleService
        svc = PuzzleService.__new__(PuzzleService)
        svc.notifier = MagicMock(board_changed=AsyncMock())
        svc._gsheet_cog = MagicMock(return_value=gsheet)
        return svc

    def _sheet(self, id, name, page_id):
        from bot.store.puzzle_data import AdditionalSheetData
        return AdditionalSheetData(id=id, hunt_id=10, puzzle_name=name,
                                   google_page_id=page_id)

    async def test_get_hunt_sheets_delegates_to_the_dao(self):
        from tests.conftest import make_hunt
        svc = self._svc(None)
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sdb.get_by_hunt.return_value = [self._sheet(1, "a", "P1")]
            sheets = svc.get_hunt_sheets(make_hunt(id=10))
        sdb.get_by_hunt.assert_called_once_with(10)
        assert [s.puzzle_name for s in sheets] == ["a"]

    async def test_remove_deletes_the_tab_and_the_row(self):
        from tests.conftest import make_hunt
        gsheet = MagicMock()
        gsheet.delete_sheet = AsyncMock()
        svc = self._svc(gsheet)
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sdb.get_by_hunt.return_value = [self._sheet(4, "alt text", "P9")]
            await svc.remove_hunt_sheet(make_hunt(id=10), 4)
        # archive=False: a hunt sheet is never on the solved spreadsheet.
        gsheet.delete_sheet.assert_awaited_once_with("P9", False)
        sdb.delete.assert_called_once_with(4)

    async def test_remove_by_index_picks_the_right_sheet(self):
        """Two sheets, because with one the index cannot be wrong. The ids are
        NOT in index order, so an implementation that passed the index straight
        through as an id would fail rather than accidentally agree.
        """
        from tests.conftest import make_hunt
        gsheet = MagicMock()
        gsheet.delete_sheet = AsyncMock()
        svc = self._svc(gsheet)
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sdb.get_by_hunt.return_value = [self._sheet(7, "first", "P7"),
                                            self._sheet(2, "second", "P2")]
            await svc.remove_hunt_sheet_by_index(make_hunt(id=10), 2)
        gsheet.delete_sheet.assert_awaited_once_with("P2", False)
        sdb.delete.assert_called_once_with(2)

    async def test_remove_by_index_rejects_out_of_range(self):
        import pytest
        from tests.conftest import make_hunt
        svc = self._svc(MagicMock())
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sdb.get_by_hunt.return_value = [self._sheet(7, "first", "P7")]
            with pytest.raises(IndexError):
                await svc.remove_hunt_sheet_by_index(make_hunt(id=10), 2)
            with pytest.raises(IndexError):
                await svc.remove_hunt_sheet_by_index(make_hunt(id=10), 0)

    async def test_remove_unknown_id_raises(self):
        import pytest
        from tests.conftest import make_hunt
        svc = self._svc(MagicMock())
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sdb.get_by_hunt.return_value = []
            with pytest.raises(RuntimeError, match="sheet_not_found"):
                await svc.remove_hunt_sheet(make_hunt(id=10), 4)

    async def test_a_missing_tab_does_not_stop_the_row_being_deleted(self):
        """Idempotent, exactly as _delete_additional_sheet is: a tab someone
        already deleted by hand must not leave an unremovable row behind."""
        from tests.conftest import make_hunt
        gsheet = MagicMock()
        gsheet.delete_sheet = AsyncMock(side_effect=Exception("boom"))
        svc = self._svc(gsheet)
        with patch("bot.core.puzzle_service.SheetsJsonDb") as sdb:
            sdb.get_by_hunt.return_value = [self._sheet(4, "alt text", "P9")]
            await svc.remove_hunt_sheet(make_hunt(id=10), 4)
        sdb.delete.assert_called_once_with(4)


class TestWebHuntSheets:
    async def test_returns_the_hunts_own_sheets(self):
        from bot.web import data
        rows = [
            {"id": 7, "puzzle_name": "alt text", "google_page_id": "P7"},
            {"id": 2, "puzzle_name": "site source", "google_page_id": "P2"},
        ]
        with patch("bot.web.data.fetch_all", AsyncMock(return_value=rows)) as fa:
            out = await data.get_hunt_sheets(10)
        # Two rows, in query order: the frontend numbers them and !remove_sheet
        # indexes the same list, so a reordering here is a real bug.
        assert out == [
            {"id": 7, "name": "alt text", "page_id": "P7"},
            {"id": 2, "name": "site source", "page_id": "P2"},
        ]
        sql, params = fa.await_args[0]
        assert "puzzle_id IS NULL" in sql
        assert params == (10,)

    async def test_empty_when_the_hunt_has_none(self):
        from bot.web import data
        with patch("bot.web.data.fetch_all", AsyncMock(return_value=[])):
            assert await data.get_hunt_sheets(10) == []
