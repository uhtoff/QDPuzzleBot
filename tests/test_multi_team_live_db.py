"""Multi-team hunt storage against the real schema (LIVE puzzlebot_test; needs
migration 053). Raw inserts, torn down in finally, as tests/test_access_log_live_db.py."""
import pytest

from bot.store import MultiTeamDb, mydb

GUILD_SNOWFLAKE = 990000000000000053
HUB = 990000000000000153


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


def test_multi_team_storage_against_the_real_schema():
    guild_row = _exec("INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)",
                      (GUILD_SNOWFLAKE, "multi-team live test"))
    hunts = []
    mts = []
    try:
        h1 = _exec("INSERT INTO hunts (name, guild_id, archived, roster_role_id) VALUES (%s, %s, 0, %s)",
                   ("mt hunt one", guild_row, 5001))
        h2 = _exec("INSERT INTO hunts (name, guild_id, archived, roster_role_id) VALUES (%s, %s, 0, %s)",
                   ("mt hunt two", guild_row, 5002))
        hunts = [h1, h2]

        # create / get; two on one hub, created out of name order
        zeta = MultiTeamDb.create(HUB, "Zeta Hunt")
        alpha = MultiTeamDb.create(HUB, "Alpha Hunt")
        mts = [zeta, alpha]
        assert zeta != alpha
        row = MultiTeamDb.get(alpha)
        assert row["name"] == "Alpha Hunt" and row["hub_guild_id"] == HUB
        assert row["no_spoiler_channel_id"] is None and row["finishers_channel_id"] is None
        assert row["archived_time"] is None and row["created_at"] is not None
        assert MultiTeamDb.get(alpha + 1000000) is None

        # link: h1, h2 -> alpha; none -> zeta
        MultiTeamDb.link(h1, alpha)
        MultiTeamDb.link(h1, alpha)  # same id: no-op
        with pytest.raises(ValueError):
            MultiTeamDb.link(h1, zeta)
        assert MultiTeamDb.for_hunt(h1)["multi_team_id"] == alpha
        MultiTeamDb.link(h2, alpha)

        # running_for_hub: ordered by name, team_count excludes archived hunts
        running = MultiTeamDb.running_for_hub(HUB)
        assert [(r["name"], r["team_count"]) for r in running] == [("Alpha Hunt", 2), ("Zeta Hunt", 0)]
        _exec("UPDATE hunts SET archived = 1 WHERE id = %s", (h2,))
        running = MultiTeamDb.running_for_hub(HUB)
        assert [(r["name"], r["team_count"]) for r in running] == [("Alpha Hunt", 1), ("Zeta Hunt", 0)]
        assert MultiTeamDb.running_for_hub(HUB + 1) == []

        # running_by_name: case-insensitive, running only
        assert MultiTeamDb.running_by_name(HUB, "aLPHA hUNT")["id"] == alpha
        assert MultiTeamDb.running_by_name(HUB, "Zeta Hunt")["id"] == zeta
        assert MultiTeamDb.running_by_name(HUB, "nope") is None
        assert MultiTeamDb.running_by_name(HUB + 1, "Alpha Hunt") is None

        # set_channels
        MultiTeamDb.set_channels(alpha, 111, 222)
        got = MultiTeamDb.get(alpha)
        assert (got["no_spoiler_channel_id"], got["finishers_channel_id"]) == (111, 222)
        assert MultiTeamDb.get(zeta)["no_spoiler_channel_id"] is None

        # for_hunt fields
        fh = MultiTeamDb.for_hunt(h1)
        assert fh["name"] == "Alpha Hunt" and fh["hub_guild_id"] == HUB
        assert (fh["no_spoiler_channel_id"], fh["finishers_channel_id"]) == (111, 222)
        assert fh["completed_time"] is None and fh["archived_time"] is None
        assert MultiTeamDb.for_hunt(h1 + 1000000) is None

        # members
        members = {m["hunt_id"]: m for m in MultiTeamDb.members(alpha)}
        assert set(members) == {h1, h2}
        assert members[h1]["roster_role_id"] == 5001 and members[h2]["roster_role_id"] == 5002
        assert not members[h1]["archived"] and members[h2]["archived"]
        assert MultiTeamDb.members(zeta) == []

        # set_completed: True on change, False on repeat, True on undo
        assert MultiTeamDb.set_completed(h1, True) is True
        assert MultiTeamDb.set_completed(h1, True) is False
        assert MultiTeamDb.for_hunt(h1)["completed_time"] is not None
        assert MultiTeamDb.for_hunt(h2)["completed_time"] is None
        assert MultiTeamDb.set_completed(h1, False) is True
        assert MultiTeamDb.set_completed(h1, False) is False
        assert MultiTeamDb.for_hunt(h1)["completed_time"] is None

        # mark_archived: drops out of the running views
        MultiTeamDb.mark_archived(zeta)
        assert MultiTeamDb.get(zeta)["archived_time"] is not None
        assert [r["name"] for r in MultiTeamDb.running_for_hub(HUB)] == ["Alpha Hunt"]
        assert MultiTeamDb.running_by_name(HUB, "Zeta Hunt") is None

        # deleting a hunt cascades its member row
        _exec("DELETE FROM hunts WHERE id = %s", (h1,))
        assert _one("SELECT COUNT(*) FROM multi_team_members WHERE hunt_id = %s", (h1,))[0] == 0
        assert MultiTeamDb.for_hunt(h1) is None
        assert [m["hunt_id"] for m in MultiTeamDb.members(alpha)] == [h2]
    finally:
        for h in hunts:
            _exec("DELETE FROM hunts WHERE id = %s", (h,))
        for m in mts:
            _exec("DELETE FROM multi_team_hunts WHERE id = %s", (m,))
        _exec("DELETE FROM guilds WHERE id = %s", (guild_row,))
