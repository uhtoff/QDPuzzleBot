"""Slice 4 storage against the real schema (LIVE puzzlebot_test; needs migration
048). The joins, filters and the first-run-wins stamp are SQL, so they are
checked against MariaDB rather than a mock. Follows the live-DB convention of
tests/test_hunt_category_cascade.py: raw inserts, torn down in finally."""
from bot.store import HuntInviteDb, HuntJsonDb, mydb

GUILD_SNOWFLAKE = 990000000000000048
ROSTER = 880000000000000048


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


def test_invite_storage_against_the_real_schema():
    guild_row = _exec("INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)",
                      (GUILD_SNOWFLAKE, "slice-4 live test"))
    live = archived = None
    try:
        live = _exec("INSERT INTO hunts (name, guild_id, roster_role_id, archived) "
                     "VALUES (%s, %s, %s, 0)", ("s4 live", guild_row, ROSTER))
        archived = _exec("INSERT INTO hunts (name, guild_id, roster_role_id, archived) "
                         "VALUES (%s, %s, %s, 1)", ("s4 archived", guild_row, ROSTER))

        assert HuntJsonDb.live_team_hunt_for_guild(GUILD_SNOWFLAKE).id == live
        assert HuntJsonDb.live_invited_hunts_for_roster(ROSTER) == []   # nothing sent yet

        HuntJsonDb.mark_invites_sent(live)
        first = _one("SELECT invites_sent_time FROM hunts WHERE id = %s", (live,))[0]
        assert first is not None
        HuntJsonDb.mark_invites_sent(live)                                # first run wins
        assert _one("SELECT invites_sent_time FROM hunts WHERE id = %s", (live,))[0] == first
        HuntJsonDb.mark_invites_sent(archived)

        got = HuntJsonDb.live_invited_hunts_for_roster(ROSTER)
        assert [(h.id, snowflake) for h, snowflake in got] == [(live, GUILD_SNOWFLAKE)]
        assert HuntJsonDb.live_invited_hunts_for_roster(ROSTER + 1) == []

        HuntInviteDb.record(live, 111, "abc")
        HuntInviteDb.record(live, 222, "def")
        assert HuntInviteDb.invited_user_ids(live) == {111, 222}
        HuntInviteDb.mark_joined(live, 111)
        joined = _one("SELECT COUNT(*) FROM hunt_invites WHERE hunt_id = %s AND joined_at IS NOT NULL",
                      (live,))[0]
        assert joined == 1
    finally:
        for hunt_id in (live, archived):
            if hunt_id is not None:
                _exec("DELETE FROM hunts WHERE id = %s", (hunt_id,))     # cascades hunt_invites
        _exec("DELETE FROM guilds WHERE id = %s", (guild_row,))


def test_leaving_storage_against_the_real_schema():
    """What the hub-role-lost path reads and clears: every live team hunt (sent or
    not), one person's unused invite codes, and forgetting them for one hunt."""
    guild_row = _exec("INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)",
                      (GUILD_SNOWFLAKE, "slice-4 leaving test"))
    live = archived = hub_hunt = None
    try:
        live = _exec("INSERT INTO hunts (name, guild_id, roster_role_id, archived) "
                     "VALUES (%s, %s, %s, 0)", ("s4 live", guild_row, ROSTER))
        archived = _exec("INSERT INTO hunts (name, guild_id, roster_role_id, archived) "
                         "VALUES (%s, %s, %s, 1)", ("s4 archived", guild_row, ROSTER))
        hub_hunt = _exec("INSERT INTO hunts (name, guild_id, archived) VALUES (%s, %s, 0)",
                         ("s4 hub-hosted", guild_row))

        got = [(h.id, s) for h, s in HuntJsonDb.live_team_hunts() if s == GUILD_SNOWFLAKE]
        assert got == [(live, GUILD_SNOWFLAKE)]          # not archived, not hub-hosted

        HuntInviteDb.record(live, 111, "used")
        HuntInviteDb.mark_joined(live, 111)
        HuntInviteDb.record(live, 111, "spare")
        HuntInviteDb.record(live, 222, "other")
        assert HuntInviteDb.pending_codes(live, 111) == ["spare"]

        HuntInviteDb.forget(live, 111)
        assert HuntInviteDb.invited_user_ids(live) == {222}
    finally:
        for hunt_id in (live, archived, hub_hunt):
            if hunt_id is not None:
                _exec("DELETE FROM hunts WHERE id = %s", (hunt_id,))
        _exec("DELETE FROM guilds WHERE id = %s", (guild_row,))
