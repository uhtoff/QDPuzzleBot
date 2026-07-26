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
