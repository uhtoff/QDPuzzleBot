"""Media captions against the real schema (LIVE puzzlebot_test, migration 068)."""
import datetime

from bot.store import MediaJsonDb, mydb

GUILD_SNOW = 990000000000000068


def _exec(sql, params=()):
    cur = mydb.cursor()
    cur.execute(sql, params)
    last = cur.lastrowid
    cur.close()
    return last


def test_captions_and_the_puzzles_showing_a_picture():
    now = datetime.datetime(2026, 11, 28)
    guild = _exec("INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)", (GUILD_SNOW, "t caption"))
    try:
        hunt = _exec("INSERT INTO hunts (name, guild_id, archived) VALUES (%s,%s,0)", ("t cap", guild))
        m1 = _exec("INSERT INTO media (hunt_id, path, created_at) VALUES (%s,%s,%s)", (hunt, "a", now))
        m2 = _exec("INSERT INTO media (hunt_id, path, created_at) VALUES (%s,%s,%s)", (hunt, "b", now))
        p1 = _exec("INSERT INTO puzzles (name, hunt_id) VALUES (%s,%s)", ("p1", hunt))
        p2 = _exec("INSERT INTO puzzles (name, hunt_id) VALUES (%s,%s)", ("p2", hunt))
        _exec("INSERT INTO puzzle_images (puzzle_id, media_id, position, created_at) "
              "VALUES (%s,%s,0,%s)", (p1, m1, now))
        _exec("INSERT INTO evidence_items (hunt_id, source_puzzle_id, media_id, note, created_at) "
              "VALUES (%s,%s,%s,'clue',%s)", (hunt, p2, m1, now))

        assert MediaJsonDb.captions([m1, m2]) == {}
        MediaJsonDb.update_caption(m1, "**Grid** key")
        assert MediaJsonDb.get_by_id(m1).caption == "**Grid** key"
        assert MediaJsonDb.captions([m1, m2, m1]) == {m1: "**Grid** key"}
        assert MediaJsonDb.captions([]) == {}
        MediaJsonDb.update_caption(m1, None)
        assert MediaJsonDb.get_by_id(m1).caption is None

        # Own image on p1, image evidence logged from p2; m2 is on nothing.
        assert MediaJsonDb.puzzle_ids_using(m1) == sorted([p1, p2])
        assert MediaJsonDb.puzzle_ids_using(m2) == []
    finally:
        _exec("DELETE FROM evidence_items WHERE hunt_id IN (SELECT id FROM hunts WHERE guild_id = %s)", (guild,))
        _exec("DELETE FROM puzzle_images WHERE puzzle_id IN (SELECT p.id FROM puzzles p "
              "JOIN hunts h ON h.id = p.hunt_id WHERE h.guild_id = %s)", (guild,))
        _exec("DELETE FROM puzzles WHERE hunt_id IN (SELECT id FROM hunts WHERE guild_id = %s)", (guild,))
        _exec("DELETE FROM media WHERE hunt_id IN (SELECT id FROM hunts WHERE guild_id = %s)", (guild,))
        _exec("DELETE FROM hunts WHERE guild_id = %s", (guild,))
        _exec("DELETE FROM guilds WHERE id = %s", (guild,))
