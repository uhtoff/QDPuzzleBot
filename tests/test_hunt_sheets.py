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
