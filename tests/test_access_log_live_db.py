"""Slice 5 storage against the real schema (LIVE puzzlebot_test; needs migration
049). Raw inserts, torn down in finally, as tests/test_team_invites_live_db.py."""
from bot.store import HuntAccessLogDb, HuntJsonDb, mydb

GUILD_SNOWFLAKE = 990000000000000049


def _exec(sql, params=()):
    cur = mydb.cursor()
    cur.execute(sql, params)
    last = cur.lastrowid
    cur.close()
    return last


def _one(sql, params=()):
    cur = mydb.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    cur.close()
    return row


def test_access_log_and_mod_invite_storage_against_the_real_schema():
    guild_row = _exec("INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)",
                      (GUILD_SNOWFLAKE, "slice-5 live test"))
    hunt = None
    try:
        hunt = _exec("INSERT INTO hunts (name, guild_id, archived) VALUES (%s, %s, 0)",
                     ("s5 live", guild_row))

        HuntAccessLogDb.record(hunt, 111, "alice", "grant", "arrival")
        HuntAccessLogDb.record(hunt, 222, "bob", "elevate", "hand", actor_id=777, detail="by hand")
        rows = HuntAccessLogDb.for_hunt(hunt)
        assert [(r["user_id"], r["username"], r["action"], r["via"], r["actor_id"], r["detail"])
                for r in rows] == [
            (111, "alice", "grant", "arrival", None, None),
            (222, "bob", "elevate", "hand", 777, "by hand"),
        ]
        assert HuntAccessLogDb.for_hunt(hunt + 1000000) == []

        # mod invite code: None -> "abc" -> None, and commit() leaves it alone
        assert HuntJsonDb.mod_invite_code(hunt) is None
        HuntJsonDb.set_mod_invite_code(hunt, "abc")
        assert HuntJsonDb.mod_invite_code(hunt) == "abc"
        HuntJsonDb.commit(HuntJsonDb.get_by_attr(id=hunt))
        assert HuntJsonDb.mod_invite_code(hunt) == "abc"
        HuntJsonDb.set_mod_invite_code(hunt, None)
        assert HuntJsonDb.mod_invite_code(hunt) is None

        # the log outlives its hunt
        _exec("DELETE FROM hunts WHERE id = %s", (hunt,))
        left = _one("SELECT COUNT(*) FROM hunt_access_log WHERE hunt_id = %s", (hunt,))[0]
        assert left == 2
    finally:
        if hunt is not None:
            _exec("DELETE FROM hunts WHERE id = %s", (hunt,))
            _exec("DELETE FROM hunt_access_log WHERE hunt_id = %s", (hunt,))
        _exec("DELETE FROM guilds WHERE id = %s", (guild_row,))
