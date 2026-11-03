"""The hub's multi_team_category setting against the real schema (LIVE
puzzlebot_test; needs migration 055)."""
from bot.store import GuildSettingsDb, mydb

GUILD_SNOWFLAKE = 990000000000000055


def _exec(sql, params=()):
    cur = mydb.cursor()
    cur.execute(sql, params)
    cur.close()


def test_multi_team_category_defaults_to_general_and_round_trips():
    try:
        # A row made before the setting existed (column omitted) reads "general".
        _exec("INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)",
              (GUILD_SNOWFLAKE, "hub category live test"))
        settings = GuildSettingsDb.get(GUILD_SNOWFLAKE)
        assert settings.multi_team_category == "general"

        settings.multi_team_category = "Hunts"
        GuildSettingsDb.commit(settings)  # UPDATE path
        assert GuildSettingsDb.get(GUILD_SNOWFLAKE).multi_team_category == "Hunts"

        settings.multi_team_category = ""  # top level, kept distinct from the default
        GuildSettingsDb.commit(settings)
        assert GuildSettingsDb.get(GUILD_SNOWFLAKE).multi_team_category == ""
    finally:
        _exec("DELETE FROM guilds WHERE guild_id = %s", (GUILD_SNOWFLAKE,))
