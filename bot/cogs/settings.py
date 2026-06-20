"""
Settings Cog shipped with cookiecutter-discordpy-postgres.

The settings here are unrelated to puzzle_settings.py
"""

import discord
from discord.ext import commands

from bot.store import GuildSettings, GuildSettingsDb


class Settings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{type(self).__name__} Cog ready.")

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Add new guilds to the database"""
        g = GuildSettings()
        g.guild_id = guild.id
        g.guild_name = guild.name
        _ = GuildSettingsDb.commit(g)


async def setup(bot):
    await bot.add_cog(Settings(bot))
