#!/usr/bin/env python3
"""Apply the numbered migrations against the legacy `puzzlebot` DB.

`migrate.py` reads the DB name from `config.database` (normally `puzzlebot_test`).
This wrapper points its connection at `puzzlebot` instead, without touching the
shared, gitignored `config.json`. Also runs the one pre-flight check that matters:
migration 005 adds a UNIQUE key on tags(round_id, puzzle_id) and fails on duplicates.

Run step 01_drop_guilds.sql BEFORE this, and 02_post_migrate_cleanup.sql AFTER.

Usage (from repo root):
    pipenv run python docs/legacy-hunt-migration/run_migrate.py
"""
import json
import sys
from pathlib import Path

import mysql.connector

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
import migrate  # noqa: E402  (repo module, needs sys.path first)

TARGET_DB = "puzzlebot"


def _config():
    return json.loads((REPO_ROOT / "config.json").read_text())


def target_connect():
    c = _config()
    return mysql.connector.connect(
        host="localhost", user=c["mysql_username"], password=c["mysql_password"],
        database=TARGET_DB, charset="utf8mb4", autocommit=False,
    )


def main():
    # Pre-flight: duplicate tag pairs would break migration 005's unique key.
    conn = target_connect()
    cur = conn.cursor()
    cur.execute("""SELECT round_id, puzzle_id, COUNT(*) c FROM tags
                   GROUP BY round_id, puzzle_id HAVING c > 1""")
    dups = cur.fetchall()
    cur.close(); conn.close()
    print("duplicate (round_id,puzzle_id) tag pairs:", len(dups))
    if dups:
        print("ABORT: resolve duplicate tags before migrating (005 unique key).")
        return 1

    # Point the runner at the target DB and apply everything pending.
    migrate.connect = target_connect
    conn = migrate.connect()
    try:
        migrate.cmd_status(conn)
        migrate.cmd_migrate(conn)
        migrate.cmd_status(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
