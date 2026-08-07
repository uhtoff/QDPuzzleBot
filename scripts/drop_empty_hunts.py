#!/usr/bin/env python3
"""Drop empty legacy hunts (0 puzzles) from the local ``puzzlebot`` database.

Context: the legacy-DB migration left a batch of old hunts with no puzzles — their
puzzles were pure orphans (no hunt_id / no tag / no meta) and were swept in the
cleanup, so they can't be rebuilt into anything more than a flat, ungrouped list.
Rather than recover that, we drop the empty hunt shells. The chat archives remain
the record.

Deletion is clean: ``tag_group``, ``rollups`` and ``evidence_items`` all cascade
``ON DELETE CASCADE`` from ``hunts`` (and ``tags`` cascade from ``tag_group``), and
these hunts have zero ``puzzles`` — so a single ``DELETE FROM hunts`` removes the
shell and all its children. ``web_audit_log`` rows (no FK) are intentionally left
as historical audit trail.

Safety:
  * Targets ``puzzlebot`` explicitly (NOT config.database / puzzlebot_test).
  * ``--apply`` takes a full ``mysqldump`` of the DB to a timestamped file first.
  * The drop set is computed live (hunts with 0 puzzles) minus KEEP, so a hunt that
    has gained puzzles since is never dropped.

    pipenv run python drop_empty_hunts.py            # DRY RUN: list what would go
    pipenv run python drop_empty_hunts.py --apply    # dump, then delete + commit
"""
import os
import subprocess
import sys

import mysql.connector

from bot.utils import config

DB = "puzzlebot"                       # local cleaned DB — deliberately not config.database
KEEP_HUNT_IDS = {153, 154, 155, 156}   # Paradox Puzzlehunt — future hunt, keep


def connect():
    return mysql.connector.connect(
        host="localhost", user=config.mysql_username, password=config.mysql_password,
        database=DB, charset="utf8mb4", autocommit=False,
    )


def resolve_drop_set(cur):
    """Empty hunts (0 puzzles) minus the keep list, with cascade counts."""
    cur.execute(
        "SELECT h.id, h.name, h.guild_id, "
        "(SELECT COUNT(*) FROM tag_group g WHERE g.hunt_id=h.id) AS tag_groups, "
        "(SELECT COUNT(*) FROM tags t JOIN tag_group g ON g.id=t.tag_group_id "
        " WHERE g.hunt_id=h.id) AS tags, "
        "(SELECT COUNT(*) FROM rollups r WHERE r.hunt_id=h.id) AS rollups, "
        "(SELECT COUNT(*) FROM evidence_items e WHERE e.hunt_id=h.id) AS evidence "
        "FROM hunts h "
        "WHERE (SELECT COUNT(*) FROM puzzles p WHERE p.hunt_id=h.id) = 0 "
        "ORDER BY h.id"
    )
    return [r for r in cur.fetchall() if r["id"] not in KEEP_HUNT_IDS]


def dump_database(ts):
    out = f"puzzlebot_predrop_{ts}.sql"
    env = dict(os.environ, MYSQL_PWD=config.mysql_password)
    with open(out, "wb") as fh:
        subprocess.run(
            ["mysqldump", "--single-transaction", "--routines", "--triggers",
             "-h", "localhost", "-u", config.mysql_username, DB],
            stdout=fh, env=env, check=True,
        )
    return out


def main(argv):
    apply = "--apply" in argv[1:]
    ts = argv[argv.index("--ts") + 1] if "--ts" in argv else None
    if apply and not ts:
        print("ERROR: --apply requires --ts <timestamp> (avoids nondeterministic dump names).")
        print("       e.g.  --apply --ts 20260703-1200")
        return 2

    conn = connect()
    cur = conn.cursor(dictionary=True)
    try:
        drops = resolve_drop_set(cur)
        if not drops:
            print("No empty hunts to drop.")
            return 0

        print(f"{len(drops)} empty hunt(s) to drop from `{DB}` "
              f"(keeping {sorted(KEEP_HUNT_IDS)}):\n")
        tot = {"tag_groups": 0, "tags": 0, "rollups": 0, "evidence": 0}
        for r in drops:
            for k in tot:
                tot[k] += r[k]
            print(f"  id={r['id']:<4} g{r['guild_id']}  "
                  f"tg={r['tag_groups']:<3} tags={r['tags']:<3} "
                  f"roll={r['rollups']:<2} ev={r['evidence']:<2}  {r['name']!r}")
        print(f"\n  cascades: {tot['tag_groups']} tag_group, {tot['tags']} tags, "
              f"{tot['rollups']} rollups, {tot['evidence']} evidence rows.")
        print("  (web_audit_log rows are left in place — no FK, historical audit.)")

        ids = [r["id"] for r in drops]
        if not apply:
            print(f"\nDRY RUN — nothing deleted. Re-run with --apply --ts <stamp> "
                  f"to dump + delete these {len(ids)} hunts.")
            return 0

        dump = dump_database(ts)
        print(f"\nBacked up `{DB}` to {dump}")
        fmt = ",".join(["%s"] * len(ids))
        cur.execute(f"DELETE FROM hunts WHERE id IN ({fmt})", ids)
        deleted = cur.rowcount
        conn.commit()
        print(f"Deleted {deleted} hunt rows (+ cascaded children). Committed.")
        return 0
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
