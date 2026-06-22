#!/usr/bin/env python3
"""Schema migration runner for QDPuzzleBot.

Applies the ``NNN_*.sql`` files in ``migrations/`` in filename order, tracking
which have run in a ``schema_migrations`` table so each applies exactly once.

This is the bring-up tool for the bot's MySQL database: against an empty schema
it stands the whole thing up from ``000_base_schema.sql`` onward; against a
populated one it applies only what is new.

Usage::

    python migrate.py                 # apply all pending migrations
    python migrate.py status          # show applied vs pending, then exit
    python migrate.py baseline        # mark ALL present migrations as applied
                                      # WITHOUT running them (for a DB that was
                                      # built/migrated by hand before this tool)

Connection settings (host, user, password, database name) are read from the
same ``config.json`` the bot uses, via ``bot.utils.config``.

The golden rule (see docs/PLANS.md M0): **migration -> deploy -> code.** The
bot's generic ``commit()`` writes every dataclass field by name, so a column
must exist in the database before code that references it ships. Always run
pending migrations before deploying code that depends on them.

Migrations are plain ``.sql``. Use full-line ``--`` comments only (inline
``--`` after a statement is not stripped). Statements are separated by ``;``.
Prefer ``CREATE TABLE IF NOT EXISTS`` so a re-run after a partial failure is
safe; note that MySQL auto-commits DDL, so a multi-statement file that fails
midway cannot be rolled back -- fix forward or baseline.
"""
import sys
from pathlib import Path

import mysql.connector

from bot.utils import config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def discover_migrations(migrations_dir=MIGRATIONS_DIR):
    """Return the migration .sql files sorted by filename (i.e. NNN_ order)."""
    return sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())


def split_statements(sql_text):
    """Split a migration file into individual SQL statements.

    Strips full-line ``--`` comments and blank lines, then splits the remainder
    on ``;``. Returns a list of non-empty statements (without trailing ``;``).
    """
    lines = []
    for line in sql_text.splitlines():
        if line.strip().startswith("--"):
            continue
        lines.append(line)
    body = "\n".join(lines)
    return [stmt.strip() for stmt in body.split(";") if stmt.strip()]


def connect():
    """Open a MySQL connection from the bot's config (autocommit off)."""
    return mysql.connector.connect(
        host="localhost",
        user=config.mysql_username,
        password=config.mysql_password,
        database=config.database,
        charset="utf8mb4",
        autocommit=False,
    )


def ensure_migrations_table(conn):
    cursor = conn.cursor()
    try:
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS `schema_migrations` ("
            "  `filename`   VARCHAR(255) NOT NULL,"
            "  `applied_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            "  PRIMARY KEY (`filename`)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
        conn.commit()
    finally:
        cursor.close()


def applied_set(conn):
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT filename FROM `schema_migrations`")
        return {row[0] for row in cursor.fetchall()}
    finally:
        cursor.close()


def record_applied(conn, filename):
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO `schema_migrations` (`filename`) VALUES (%s)", (filename,)
        )
        conn.commit()
    finally:
        cursor.close()


def apply_migration(conn, path):
    """Run every statement in one migration file, then record it as applied."""
    statements = split_statements(path.read_text())
    cursor = conn.cursor()
    try:
        for stmt in statements:
            cursor.execute(stmt)
        conn.commit()
    except Exception:
        conn.rollback()
        cursor.close()
        raise
    cursor.close()
    record_applied(conn, path.name)


def cmd_status(conn):
    ensure_migrations_table(conn)
    applied = applied_set(conn)
    migrations = discover_migrations()
    if not migrations:
        print(f"No migrations found in {MIGRATIONS_DIR}")
        return
    print(f"Migrations in {MIGRATIONS_DIR}:")
    for path in migrations:
        mark = "applied" if path.name in applied else "PENDING"
        print(f"  [{mark}] {path.name}")
    orphans = applied - {p.name for p in migrations}
    for name in sorted(orphans):
        print(f"  [applied, file missing] {name}")


def cmd_migrate(conn):
    ensure_migrations_table(conn)
    applied = applied_set(conn)
    migrations = discover_migrations()
    pending = [p for p in migrations if p.name not in applied]
    if not pending:
        print("Database is up to date; nothing to apply.")
        return
    for path in pending:
        print(f"Applying {path.name} ...")
        apply_migration(conn, path)
        print(f"  done.")
    print(f"Applied {len(pending)} migration(s).")


def cmd_baseline(conn):
    """Record every present migration as applied without executing any SQL.

    For an existing database that was created/migrated by hand before this
    runner existed: its schema is already current, so we only need to seed the
    bookkeeping table so future migrations layer on correctly.
    """
    ensure_migrations_table(conn)
    applied = applied_set(conn)
    migrations = discover_migrations()
    newly = [p for p in migrations if p.name not in applied]
    if not newly:
        print("All present migrations are already recorded; nothing to baseline.")
        return
    for path in newly:
        record_applied(conn, path.name)
        print(f"Marked {path.name} as applied (not executed).")
    print(f"Baselined {len(newly)} migration(s).")


COMMANDS = {
    "migrate": cmd_migrate,
    "status": cmd_status,
    "baseline": cmd_baseline,
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else "migrate"
    if command not in COMMANDS:
        print(f"Unknown command: {command!r}")
        print(f"Usage: python migrate.py [{'|'.join(COMMANDS)}]")
        return 2
    conn = connect()
    try:
        COMMANDS[command](conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
