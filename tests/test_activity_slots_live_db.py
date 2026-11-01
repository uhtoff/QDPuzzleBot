"""Activity-slot storage against the real schema (LIVE puzzlebot_test; needs
migration 052). Raw inserts, torn down in finally, as tests/test_access_log_live_db.py."""
import datetime

from bot.store import PuzzleActivityDb, SheetActivityDb, mydb

GUILD_SNOWFLAKE = 990000000000000052
T = datetime.datetime(2026, 11, 1, 12, 0)


def _exec(sql, params=()):
    cur = mydb.cursor()
    cur.execute(sql, params)
    last = cur.lastrowid
    cur.close()
    return last


def _rows(puzzle_ids):
    cur = mydb.cursor()
    marks = ", ".join(["%s"] * len(puzzle_ids))
    cur.execute(
        "SELECT puzzle_id, slot_start, chat_count, sheet_edited FROM puzzle_activity_slots "
        f"WHERE puzzle_id IN ({marks}) ORDER BY puzzle_id, slot_start", tuple(puzzle_ids))
    rows = [tuple(r) for r in cur.fetchall()]
    cur.close()
    return rows


def test_puzzle_activity_slots_against_the_real_schema():
    guild_row = _exec("INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)",
                      (GUILD_SNOWFLAKE, "activity slots live test"))
    hunts = []
    try:
        def hunt(name, mode, folder, archived):
            h = _exec("INSERT INTO hunts (name, guild_id, archived, sheet_mode, drive_folder_id) "
                      "VALUES (%s, %s, %s, %s, %s)", (name, guild_row, archived, mode, folder))
            hunts.append(h)
            return h

        h1 = hunt("files live", "files", "FOLD052", 0)
        hunt("files archived", "files", "OLD052", 1)
        hunt("tabs", "tabs", "", 0)
        p1 = _exec("INSERT INTO puzzles (name, hunt_id, channel_id, google_sheet_id) "
                   "VALUES ('p1', %s, 1001052, 'S1052')", (h1,))
        p2 = _exec("INSERT INTO puzzles (name, hunt_id, channel_id, google_sheet_id) "
                   "VALUES ('p2', %s, 1002052, '')", (h1,))

        PuzzleActivityDb.add_chat(1001052, T + datetime.timedelta(minutes=1))
        PuzzleActivityDb.add_chat(1001052, T + datetime.timedelta(minutes=1, seconds=30))
        PuzzleActivityDb.add_chat(1001052, T + datetime.timedelta(minutes=6))
        PuzzleActivityDb.add_chat(424242052, T)  # not a puzzle channel
        assert _rows([p1, p2]) == [
            (p1, T, 2, 0),
            (p1, T + datetime.timedelta(minutes=5), 1, 0),
        ]

        SheetActivityDb.mark_sheet_edited("S1052", T + datetime.timedelta(minutes=2))
        SheetActivityDb.mark_sheet_edited("S1052", T + datetime.timedelta(minutes=3))
        SheetActivityDb.mark_sheet_edited("", T + datetime.timedelta(minutes=2))
        assert _rows([p1, p2]) == [
            (p1, T, 2, 1),
            (p1, T + datetime.timedelta(minutes=5), 1, 0),
        ]

        # only the live files hunt -- not the archived files hunt, not the tabs hunt
        mine = {(h, f) for h, f in SheetActivityDb.files_hunt_folders() if h in hunts}
        assert mine == {(h1, "FOLD052")}

        _exec("DELETE FROM puzzles WHERE id = %s", (p1,))
        assert _rows([p1]) == []
    finally:
        for h in hunts:
            _exec("DELETE FROM puzzles WHERE hunt_id = %s", (h,))
            _exec("DELETE FROM hunts WHERE id = %s", (h,))
        _exec("DELETE FROM guilds WHERE id = %s", (guild_row,))
