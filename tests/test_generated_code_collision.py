"""Missing UNIQUE constraints (2026-08-07 review): hunts.uid and
tag_group.group_code are minted by generate_uid, which SELECTs for a clash and
then INSERTs. Two concurrent creates (a bang command and a web create) can both
pass the check and both insert the same code, and the second hunt is then
unreachable by uid.

Migration 040 adds the UNIQUE indexes, which turns that race from silent
corruption into a duplicate-key error. These tests cover the other half: the
write re-mints and retries instead of surfacing the error as a 500.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mysql.connector.errors import IntegrityError

from bot.store.mysqldb import MySQLHuntJsonDb, MySQLTagGroupJsonDb


def _dup(index):
    """The error MariaDB actually raises when a UNIQUE index rejects an insert.

    Verified against MariaDB 12.3.2 by inserting a duplicate uid for real:

        1062 (23000): Duplicate entry 'HIPFEW' for key 'uq_hunts_uid'

    Note it names the INDEX, not the column -- so the index names in migration
    040 are load-bearing for the retry, and a mock saying "for key 'uid'" would
    exercise a branch production never takes."""
    return IntegrityError(
        msg=f"1062 (23000): Duplicate entry 'AB12CD' for key '{index}'", errno=1062)


def _db(cls, *, failures):
    """A DAO whose INSERT raises a duplicate-key error `failures` times."""
    mydb, cursor = MagicMock(), MagicMock()
    cursor.lastrowid = 7
    calls = {"n": 0}

    def execute(_stmt, _data=()):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise _dup(f"uq_{cls.TABLE_NAME}_{cls.GENERATED_UNIQUE_FIELDS[0]}")

    cursor.execute.side_effect = execute
    mydb.cursor.return_value = cursor
    db = cls(mydb=mydb)
    db.check_duplicates = lambda value, field="name": False   # no DB for the re-mint
    return db, calls


def test_a_colliding_hunt_uid_is_reminted_and_the_insert_retried():
    db, calls = _db(MySQLHuntJsonDb, failures=1)
    hunt = SimpleNamespace(id=0, uid="AB12CD", name="Hunt")

    db.commit(hunt)

    assert calls["n"] == 2                 # first insert rejected, second accepted
    assert hunt.uid != "AB12CD"            # a fresh code, not the losing one
    assert len(hunt.uid) == 6
    assert hunt.id == 7                    # the retry's row id was picked up


def test_a_colliding_group_code_is_reminted_and_the_insert_retried():
    """The second table with a generated code -- one table passing proves the
    retry works, not that it is wired to both DAOs."""
    db, calls = _db(MySQLTagGroupJsonDb, failures=1)
    group = SimpleNamespace(id=0, group_code="AB12CD", name="Round")

    db.commit(group)

    assert calls["n"] == 2
    assert group.group_code != "AB12CD"


def test_it_gives_up_rather_than_retrying_forever():
    """A duplicate-key error that re-minting cannot fix (a wedged index, or a
    clash on some other unique column) must surface, not spin."""
    db, calls = _db(MySQLHuntJsonDb, failures=99)
    hunt = SimpleNamespace(id=0, uid="AB12CD", name="Hunt")

    with pytest.raises(IntegrityError):
        db.commit(hunt)
    assert calls["n"] <= MySQLHuntJsonDb.GENERATED_CODE_ATTEMPTS


def test_an_unrelated_integrity_error_is_not_retried():
    """Only a duplicate on the generated field is recoverable by re-minting. A
    foreign-key violation must surface immediately, with no wasted retries and
    no code churn on the object."""
    mydb, cursor = MagicMock(), MagicMock()
    cursor.execute.side_effect = IntegrityError(
        msg="1452 (23000): Cannot add or update a child row: a foreign key constraint fails",
        errno=1452)
    mydb.cursor.return_value = cursor
    hunt = SimpleNamespace(id=0, uid="AB12CD", name="Hunt")

    with pytest.raises(IntegrityError):
        MySQLHuntJsonDb(mydb=mydb).commit(hunt)

    assert cursor.execute.call_count == 1
    assert hunt.uid == "AB12CD"


def test_the_index_names_the_migrations_create_are_the_ones_the_retry_matches():
    """The retry identifies the column from the index name in the error text, so
    the two must agree. Reads the migrations rather than restating the names, so
    renaming an index there without updating the DAO fails here.

    The two indexes ship in different migrations: hunts.uid in 040, and
    tag_group.group_code in 042, which had to re-code 41 rows and move the
    column default off the shared '0' sentinel before the index could exist.
    """
    from pathlib import Path
    assert "`uq_hunts_uid`" in Path("migrations/040_unique_generated_codes.sql").read_text()
    assert "`uq_tag_group_group_code`" in Path("migrations/042_unique_group_code.sql").read_text()

    for cls in (MySQLHuntJsonDb, MySQLTagGroupJsonDb):
        for field in cls.GENERATED_UNIQUE_FIELDS:
            db = cls(mydb=MagicMock())
            index = f"uq_{cls.TABLE_NAME}_{field}"
            assert db._duplicate_generated_field(_dup(index)) == field
