"""get_unread_counts / get_own_last_message_ids against the real schema (LIVE puzzlebot_test).

The count query filters on `kind` and groups by channel with a generated OR of
(channel, marker) pairs; a mistake in either only shows against real rows.
Rows torn down in finally, as the other *_live_db tests.
"""
import asyncio
import datetime

from bot.store import mydb
from bot.web import data

CH_A = 990000000000000061
CH_B = 990000000000000062
GUILD = 990000000000000063
BASE = 990000000000000100   # message ids: BASE + small offsets, well clear of real snowflakes
ME = 4242
OTHER = 4343
T = datetime.datetime(2026, 11, 24, 12, 0)


def _ulive_insert(msg_id, channel, author, kind):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO messages (id, channel_id, guild_id, author_id, author_name, kind, content, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (msg_id, channel, GUILD, author, "t", kind, "x", T))
    cur.close()


def _ulive_cleanup():
    cur = mydb.cursor()
    cur.execute("DELETE FROM messages WHERE channel_id IN (%s, %s)", (CH_A, CH_B))
    cur.close()


def test_unread_counts_and_own_last_against_the_real_schema():
    _ulive_cleanup()
    try:
        _ulive_insert(BASE + 10, CH_A, OTHER, 0)
        _ulive_insert(BASE + 20, CH_A, ME, 0)
        _ulive_insert(BASE + 30, CH_A, OTHER, 0)
        _ulive_insert(BASE + 40, CH_A, OTHER, 3)   # a !command: never unread
        _ulive_insert(BASE + 50, CH_A, OTHER, 1)   # this bot: never unread
        _ulive_insert(BASE + 15, CH_B, OTHER, 0)
        _ulive_insert(BASE + 25, CH_B, OTHER, 0)

        counts = asyncio.run(data.get_unread_counts({CH_A: BASE + 10, CH_B: BASE + 15}))
        assert counts == {CH_A: {"unread": 2, "last_id": BASE + 30},
                          CH_B: {"unread": 1, "last_id": BASE + 25}}

        own = asyncio.run(data.get_own_last_message_ids([CH_A, CH_B], ME, BASE))
        assert own == {CH_A: BASE + 20}
        # An own message at or below the baseline is not reported.
        assert asyncio.run(data.get_own_last_message_ids([CH_A, CH_B], ME, BASE + 20)) == {}
    finally:
        _ulive_cleanup()
