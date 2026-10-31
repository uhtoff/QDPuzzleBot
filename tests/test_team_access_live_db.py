"""team_hunts / HuntInviteDb.for_hunt against the real schema (LIVE puzzlebot_test)."""
from bot.store import HuntInviteDb, HuntJsonDb, mydb

HUB_SNOW = 990000000000000051
TEAM_SNOW = 990000000000000052


def _exec(sql, params=()):
    cur = mydb.cursor()
    cur.execute(sql, params)
    last = cur.lastrowid
    cur.close()
    return last


def test_team_hunts_and_invite_rows_against_the_real_schema():
    hub = _exec("INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)", (HUB_SNOW, "hub t3"))
    team = _exec("INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)", (TEAM_SNOW, "team t3"))
    ids = []
    try:
        live = _exec("INSERT INTO hunts (name, guild_id, archived, roster_role_id) VALUES (%s,%s,0,%s)",
                     ("t3 live", team, 5551))
        old = _exec("INSERT INTO hunts (name, guild_id, archived, roster_role_id) VALUES (%s,%s,1,%s)",
                    ("t3 old", hub, 5552))
        plain = _exec("INSERT INTO hunts (name, guild_id, archived) VALUES (%s,%s,0)", ("t3 hub", hub))
        ids = [live, old, plain]

        mine = [(h.id, s) for h, s in HuntJsonDb.team_hunts() if h.id in ids]
        assert mine == [(live, TEAM_SNOW), (old, HUB_SNOW)]  # running first; no hub-hosted plain hunt

        HuntInviteDb.record(live, 111, "SECRETCODE")
        HuntInviteDb.mark_joined(live, 111)
        HuntInviteDb.record(live, 222, "SECRETCODE2")
        rows = HuntInviteDb.for_hunt(live)
        assert [r["user_id"] for r in rows] == [111, 222]
        assert all(set(r) == {"user_id", "created_at", "joined_at"} for r in rows)
        assert rows[0]["joined_at"] is not None and rows[1]["joined_at"] is None

        HuntInviteDb.record(old, 333, "SECRETCODE3")
        grouped = HuntInviteDb.for_hunts([live, old, plain])
        assert {k: [r["user_id"] for r in v] for k, v in grouped.items()} == {
            live: [111, 222], old: [333]}
        assert all(set(r) == {"user_id", "created_at", "joined_at"}
                   for v in grouped.values() for r in v)
        assert HuntInviteDb.for_hunts([]) == {}

        assert HuntJsonDb.guild_snowflake_for_hunt(live) == TEAM_SNOW
        assert HuntJsonDb.guild_snowflake_for_hunt(old) == HUB_SNOW
        assert HuntJsonDb.guild_snowflake_for_hunt(plain + 10**6) is None
    finally:
        for h in ids:
            _exec("DELETE FROM hunt_invites WHERE hunt_id = %s", (h,))
            _exec("DELETE FROM hunts WHERE id = %s", (h,))
        _exec("DELETE FROM guilds WHERE id IN (%s, %s)", (hub, team))
