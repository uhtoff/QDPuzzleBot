"""ChannelActivityDb.record against the real schema (LIVE puzzlebot_test).

record() is an upsert that runs on every human message, and its column list is
hand-written SQL, so a column it stops writing must still be insertable without
it (a default, or gone). Rows torn down in finally, as the other *_live_db tests.
"""
import datetime

from bot.store import ChannelActivityDb, mydb

CHANNEL = 990000000000000057
GUILD = 990000000000000058
T1 = datetime.datetime(2026, 11, 7, 12, 0)
T2 = datetime.datetime(2026, 11, 7, 12, 5)


def _row():
    cur = mydb.cursor()
    cur.execute("SELECT guild_id, last_human_message_at, last_author_name "
                "FROM channel_activity WHERE channel_id = %s", (CHANNEL,))
    row = cur.fetchone()
    cur.close()
    return tuple(row) if row is not None else None


def test_record_inserts_then_updates_against_the_real_schema():
    try:
        ChannelActivityDb.record(channel_id=CHANNEL, guild_id=GUILD, at=T1, author_name="alice")
        assert _row() == (GUILD, T1, "alice")
        ChannelActivityDb.record(channel_id=CHANNEL, guild_id=GUILD, at=T2, author_name="bob")
        assert _row() == (GUILD, T2, "bob")
    finally:
        cur = mydb.cursor()
        cur.execute("DELETE FROM channel_activity WHERE channel_id = %s", (CHANNEL,))
        cur.close()
