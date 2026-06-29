"""
Settings Cog shipped with cookiecutter-discordpy-postgres.

The settings here are unrelated to puzzle_settings.py
"""

from discord.ext import commands

from bot.store import GuildSettings, GuildSettingsDb


class Settings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{type(self).__name__} Cog ready.")
        # on_guild_join only fires at the moment of joining, so guilds the bot
        # was already a member of when this code/DB was deployed have no row in
        # `guilds`. Then GuildSettingsDb.get*() returns None and the first
        # caller to dereference it crashes (e.g. check_is_bot_channel). Backfill
        # any missing rows for the guilds we're currently in.
        for guild in self.bot.guilds:
            self._ensure_guild_settings(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Add new guilds to the database"""
        self._ensure_guild_settings(guild)

    @staticmethod
    def _ensure_guild_settings(guild):
        """Create a default settings row for `guild` if one doesn't exist yet."""
        if GuildSettingsDb.get(guild.id) is not None:
            return
        g = GuildSettings()
        g.guild_id = guild.id
        g.guild_name = guild.name
        GuildSettingsDb.commit(g)


async def setup(bot):
    await bot.add_cog(Settings(bot))
