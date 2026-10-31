"""template_link persistence against the real schema (LIVE puzzlebot_test; needs
migration 050)."""
from bot.store import GuildSettings, GuildSettingsDb, mydb

GUILD_SNOWFLAKE = 990000000000000050


def _exec(sql, params=()):
    cur = mydb.cursor()
    cur.execute(sql, params)
    cur.close()


def test_template_link_round_trips_through_commit():
    try:
        settings = GuildSettings(guild_id=GUILD_SNOWFLAKE, guild_name="slice-5b live test",
                                 template_link="https://discord.new/abc123")
        GuildSettingsDb.commit(settings)  # INSERT path
        assert GuildSettingsDb.get(GUILD_SNOWFLAKE).template_link == "https://discord.new/abc123"

        settings.template_link = "https://discord.new/zzz9"
        GuildSettingsDb.commit(settings)  # UPDATE path
        assert GuildSettingsDb.get(GUILD_SNOWFLAKE).template_link == "https://discord.new/zzz9"

        settings.template_link = ""
        GuildSettingsDb.commit(settings)
        assert GuildSettingsDb.get(GUILD_SNOWFLAKE).template_link == ""
    finally:
        _exec("DELETE FROM guilds WHERE guild_id = %s", (GUILD_SNOWFLAKE,))
