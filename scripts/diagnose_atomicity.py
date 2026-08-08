"""Verify the atomicity fixes against a real database.

The pytest suite proves the code's shape; this proves the DATABASE agrees -- that
migration 037 applied, that the FK genuinely cascades, and that two concurrent
writers under the real locks do not lose an update.

Run from the repo root, with the bot's environment:

    pipenv run python scripts/diagnose_atomicity.py [--keep] [--force]

Every fixture row it creates is named or prefixed DIAG- and deleted in a finally
block, even on failure. --keep skips the cleanup for post-mortem inspection.

REFUSES TO RUN against a database whose name is not the test database unless
--force is given: cases 3 and 5 create and mutate real rows.
"""
import asyncio
import inspect
import logging
import os
import sys
from unittest.mock import MagicMock

# Runnable from anywhere: put the repo root on sys.path (so `import bot` works) and
# chdir there (config.json / google_secrets.json are loaded by relative path).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
os.chdir(_REPO_ROOT)

_KEEP = "--keep" in sys.argv[1:]
_FORCE = "--force" in sys.argv[1:]

_EXPECTED_DATABASE = "puzzlebot_test"

# --------------------------------------------------------------------------- guard
# Check the configured database BEFORE importing bot.store, which opens the real
# connection and would otherwise touch a database we have not cleared yet.
from bot.utils import config  # noqa: E402

if config.database != _EXPECTED_DATABASE and not _FORCE:
    print(
        f"REFUSING TO RUN: configured database is {config.database!r}, expected "
        f"{_EXPECTED_DATABASE!r}. Cases 3 and 5 create and mutate real rows. "
        "Pass --force to override."
    )
    sys.exit(1)

# --------------------------------------------------------------------------- imports
from bot.core.locks import _PUZZLE_LOCKS, puzzle_lock  # noqa: E402
from bot.core.puzzle_service import PuzzleService  # noqa: E402
from bot.store import (  # noqa: E402
    HuntJsonDb,
    MessageDb,
    PuzzleImageJsonDb,
    PuzzleJsonDb,
    mydb,
)

# --------------------------------------------------------------------------- results
_PASS, _FAIL, _SKIP = [], [], []


class Skip(Exception):
    """Raise from a case body to report SKIP instead of PASS/FAIL."""


async def case(name, fn):
    """Run *fn* (sync or async), catch exceptions, and print PASS/FAIL/SKIP."""
    try:
        result = fn()
        if inspect.isawaitable(result):
            await result
        print(f"[PASS] {name}")
        _PASS.append(name)
    except Skip as exc:
        print(f"[SKIP] {name}: {exc}")
        _SKIP.append(name)
    except Exception as exc:  # noqa: BLE001 - we want to report the raw failure
        detail = str(exc) or repr(exc)
        print(f"[FAIL] {name}: {detail}")
        _FAIL.append((name, detail))


# --------------------------------------------------------------------------- helpers
def _live_hunt_id():
    """Return a real hunts.id to FK against, creating a throwaway hunt if needed."""
    cur = mydb.cursor()
    cur.execute("SELECT id FROM hunts ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if row is None:
        cur.execute("INSERT INTO hunts (name) VALUES (%s)", ("DIAG-live-test-hunt",))
        hunt_id = cur.lastrowid
    else:
        hunt_id = row[0]
    cur.close()
    return hunt_id


def _insert_puzzle(name, status=""):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name, status) VALUES (%s, %s, %s)",
        (_live_hunt_id(), name, status),
    )
    pid = cur.lastrowid
    cur.close()
    return pid


def _delete_puzzle(puzzle_id):
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
    cur.close()


# --------------------------------------------------------------------------- case 1
def _case1_schema():
    cur = mydb.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE filename = %s",
            ("037_message_attachment_fk.sql",),
        )
        (applied,) = cur.fetchone()
        assert applied == 1, (
            "migration 037_message_attachment_fk.sql is not recorded in "
            "schema_migrations -- has it been applied to this database?"
        )

        cur.execute(
            "SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = %s AND CONSTRAINT_NAME = %s",
            (config.database, "MessageAttachmentMessageID"),
        )
        row = cur.fetchone()
        assert row is not None, (
            "no MessageAttachmentMessageID FK found on message_attachments"
        )
        assert row[0] == "CASCADE", f"FK DELETE_RULE is {row[0]!r}, expected CASCADE"

        cur.execute(
            "SELECT COUNT(*) FROM message_attachments a "
            "LEFT JOIN messages m ON a.message_id = m.id WHERE m.id IS NULL"
        )
        (orphans,) = cur.fetchone()
        assert orphans == 0, f"{orphans} orphaned message_attachments row(s) found"
    finally:
        cur.close()


# --------------------------------------------------------------------------- case 2
def _case2_fk_cascade():
    message_id = 91000000000000001
    attachment_ids = (91000000000000011, 91000000000000012)
    paths = ("DIAG-a/one.png", "DIAG-a/two.png")
    try:
        cur = mydb.cursor()
        cur.execute(
            "INSERT INTO messages (id, channel_id, guild_id, author_id, author_name, "
            "kind, content, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(3))",
            (message_id, 900001, 900002, 900003, "DIAG-cascade", 0, "hello"),
        )
        for aid, path in zip(attachment_ids, paths):
            cur.execute(
                "INSERT INTO message_attachments (id, message_id, filename, size, "
                "source_url, local_path) VALUES (%s,%s,%s,%s,%s,%s)",
                (aid, message_id, "a.png", 1, "http://x/a.png", path),
            )
        cur.close()

        returned = MessageDb.delete_message(message_id)

        assert sorted(returned) == sorted(paths), (
            f"delete_message returned {sorted(returned)!r}, expected {sorted(paths)!r}"
        )
        cur = mydb.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM message_attachments WHERE message_id = %s",
            (message_id,),
        )
        (remaining,) = cur.fetchone()
        cur.close()
        assert remaining == 0, (
            f"{remaining} attachment row(s) survived delete_message -- FK did not cascade"
        )
    finally:
        if not _KEEP:
            cur = mydb.cursor()
            cur.execute(
                "DELETE FROM message_attachments WHERE message_id = %s", (message_id,)
            )
            cur.execute("DELETE FROM messages WHERE id = %s", (message_id,))
            cur.close()


# --------------------------------------------------------------------------- case 3
def _case3_edge_conversion():
    cur = mydb.cursor()
    cur.execute("INSERT INTO tag_group (hunt_id) VALUES (%s)", (_live_hunt_id(),))
    gid = cur.lastrowid
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)",
        (_live_hunt_id(), "DIAG-BulkFeeder"),
    )
    feeder = cur.lastrowid
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)",
        (_live_hunt_id(), "DIAG-BulkAlready"),
    )
    already = cur.lastrowid
    cur.close()
    try:
        cur = mydb.cursor()
        cur.execute(
            "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s,%s,%s)",
            (gid, feeder, "feeds"),
        )
        # `already` holds the TARGET edge alongside an old one -- the case that
        # violates the UNIQUE constraint under a naive single UPDATE and the one the
        # `edge_type <> new_edge` guard on the DELETE protects.
        cur.execute(
            "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s,%s,%s)",
            (gid, already, "feeds"),
        )
        cur.execute(
            "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s,%s,%s)",
            (gid, already, "informs"),
        )
        cur.close()

        PuzzleJsonDb.bulk_update_edge_type(gid, "informs")

        def _edges(puzzle_id):
            c = mydb.cursor()
            c.execute(
                "SELECT edge_type FROM tags WHERE tag_group_id=%s AND puzzle_id=%s",
                (gid, puzzle_id),
            )
            edges = sorted(r[0] for r in c.fetchall())
            c.close()
            return edges

        assert _edges(feeder) == ["informs"], (
            f"feeder edges were {_edges(feeder)!r}; feeder lost membership"
        )
        assert _edges(already) == ["informs"], (
            f"already-informed member's edges were {_edges(already)!r}; expected "
            "exactly one ['informs'] row"
        )
    finally:
        if not _KEEP:
            cur = mydb.cursor()
            for pid in (feeder, already):
                cur.execute("DELETE FROM puzzles WHERE id = %s", (pid,))
            cur.execute("DELETE FROM tag_group WHERE id = %s", (gid,))
            cur.close()


# --------------------------------------------------------------------------- case 4
def _case4_positions():
    import datetime

    def _insert_media():
        c = mydb.cursor()
        c.execute(
            "INSERT INTO media (hunt_id, path, filename, content_type, size, origin,"
            " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (_live_hunt_id(), "DIAG-uploads/positions/x.png", "x.png", "image/png",
             100, "upload", datetime.datetime(2020, 1, 1)),
        )
        mid = c.lastrowid
        c.close()
        return mid

    def _insert_image(puzzle_id, position):
        media_id = _insert_media()
        c = mydb.cursor()
        c.execute(
            "INSERT INTO puzzle_images (puzzle_id, media_id, position, created_at)"
            " VALUES (%s, %s, %s, %s)",
            (puzzle_id, media_id, position, datetime.datetime(2020, 1, 1)),
        )
        iid = c.lastrowid
        c.close()
        return iid

    pid = _insert_puzzle("DIAG-PositionTest")
    try:
        a = _insert_image(pid, 0)
        b = _insert_image(pid, 1)
        c_id = _insert_image(pid, 2)

        # A genuine permutation of three: neither a no-op nor a simple swap of the
        # first two would satisfy the assertion below.
        PuzzleImageJsonDb.set_positions([c_id, a, b])

        cur = mydb.cursor()
        placeholders = ",".join(["%s"] * 3)
        cur.execute(
            f"SELECT id, position FROM puzzle_images WHERE id IN ({placeholders})",
            (a, b, c_id),
        )
        got = {r[0]: r[1] for r in cur.fetchall()}
        cur.close()

        assert got == {c_id: 0, a: 1, b: 2}, (
            f"readback order was {got!r}, expected {{c:0, a:1, b:2}}"
        )
    finally:
        if not _KEEP:
            cur = mydb.cursor()
            cur.execute("DELETE FROM puzzle_images WHERE puzzle_id = %s", (pid,))
            cur.close()
            _delete_puzzle(pid)


# --------------------------------------------------------------------------- case 5
async def _case5_concurrent_writers():
    hunt = HuntJsonDb.get_by_attr(id=_live_hunt_id())
    pid = _insert_puzzle("DIAG-Concurrent", status="")
    try:
        service = PuzzleService(MagicMock())  # notifier defaults to NullNotifier

        async def _write_note():
            async with puzzle_lock(pid):
                puzzle = PuzzleJsonDb.get_by_attr(id=pid)
                await service.add_note(hunt, puzzle, "DIAG note", "diag-script")

        async def _write_status():
            async with puzzle_lock(pid):
                puzzle = PuzzleJsonDb.get_by_attr(id=pid)
                await service.update_puzzle_field(hunt, puzzle, "status", "stuck")

        await asyncio.gather(_write_note(), _write_status())

        fresh = PuzzleJsonDb.get_by_attr(id=pid)
        assert fresh is not None
        assert any(n.startswith("DIAG note - diag-script") for n in fresh.notes), (
            f"note was lost; puzzle.notes = {fresh.notes!r}"
        )
        assert fresh.status == "stuck", (
            f"status write was lost; puzzle.status = {fresh.status!r}"
        )
    finally:
        _PUZZLE_LOCKS.pop(pid, None)
        if not _KEEP:
            _delete_puzzle(pid)


# --------------------------------------------------------------------------- case 6
class _CollectHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _case6_missing_row():
    pid = _insert_puzzle("DIAG-MissingRowTest")
    logger = logging.getLogger("bot.store.mysqldb")
    handler = _CollectHandler()
    prev_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    try:
        puzzle = PuzzleJsonDb.get_by_attr(id=pid)
        assert puzzle is not None

        # The row goes away underneath the object we are holding -- exactly what a
        # concurrent delete_round does to a stale snapshot.
        _delete_puzzle(pid)

        puzzle.status = "stuck"
        PuzzleJsonDb.commit(puzzle)

        assert any(
            "puzzles" in r.getMessage() and str(pid) in r.getMessage()
            for r in handler.records
        ), (
            "no ERROR log naming the table and id; got "
            f"{[r.getMessage() for r in handler.records]}"
        )
        assert PuzzleJsonDb.get_by_attr(id=pid) is None, (
            "commit() to a deleted row somehow (re)created it"
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)
        if not _KEEP:
            _delete_puzzle(pid)  # idempotent; the row is already gone


# --------------------------------------------------------------------------- main
async def main():
    print(f"=== diagnose_atomicity against database {config.database!r} ===\n")

    await case("case 1: migration 037 applied, FK cascades, no orphans", _case1_schema)
    await case("case 2: delete_message cascades to attachments", _case2_fk_cascade)
    await case("case 3: bulk_update_edge_type keeps every member", _case3_edge_conversion)
    await case("case 4: set_positions writes the exact order", _case4_positions)
    await case("case 5: two concurrent writers under the real lock", _case5_concurrent_writers)
    await case("case 6: commit to a deleted row logs ERROR", _case6_missing_row)

    print(
        f"\n=== summary: {len(_PASS)} passed, {len(_FAIL)} failed, "
        f"{len(_SKIP)} skipped ==="
    )
    if _FAIL:
        for name, detail in _FAIL:
            print(f"  FAILED: {name}: {detail}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
