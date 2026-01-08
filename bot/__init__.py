import asyncio
import logging
import random
import re
from pathlib import Path
import traceback

import discord
from discord.ext import commands

from bot import utils, database
from bot.database.models import Guild

__version__ = "0.1.0"

invite_link = "https://discordapp.com/api/oauth2/authorize?client_id={}&permissions=8&scope=bot%20applications.commands"


async def get_prefix(_bot, message):
    prefix = utils.config.prefix
    if not isinstance(message.channel, discord.DMChannel):
        prefix = utils.get_guild_prefix(_bot, message.guild.id)
    return commands.when_mentioned_or(prefix)(_bot, message)


bot = commands.AutoShardedBot(command_prefix=get_prefix, intents=discord.Intents.all(), case_insensitive=True)
bot.version = __version__
bot.guild_data = {}

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Only care about messages starting with two or more !
    if re.match(r"^!!+", message.content):
        # Try to process commands normally
        ctx = await bot.get_context(message)
        if ctx.valid:                 # <-- command exists and can run
            await bot.invoke(ctx)
        else:
            await message.reply("I know, I'm excited too, so many tasty puzzles.")
        return

    await bot.process_commands(message)

async def preload_guild_data():
    guilds = await Guild.query.gino.all()
    d = dict()
    for guild in guilds:
        d[guild.id] = {"prefix": guild.prefix}
    return d


@bot.event
async def on_ready():
    bot.invite = invite_link.format(bot.user.id)
    # await database.setup()
    # bot.guild_data = await preload_guild_data()
    guild_names = [g.name for g in bot.guilds]
    guild_ids = [g.id for g in bot.guilds]

    print(
        f"""Logged in as {bot.user}..
        Serving {len(bot.users)} users in {len(bot.guilds)} guilds: {guild_names} {guild_ids}
        Invite: {invite_link.format(bot.user.id)}
    """
    )

    for guild in bot.guilds:
        try:
            synced = await bot.tree.sync(guild=discord.Object(id=guild.id))
            print(f"Synced {len(synced)} commands for guild '{guild.name}' ({guild.id})")
        except Exception as e:
            print(f"Failed to sync commands for {guild.name} ({guild.id}): {e}")

    print("\nRegistered App Commands:")
    for app_command in bot.tree.get_commands():
        print(f"- {app_command.name} (Type: {type(app_command)})")

@bot.command(name="sync")
async def sync_tree(ctx):
    synced = await bot.tree.sync()
    print(f"Synced {len(synced)} command(s).")

@bot.event
async def setup_hook():
    await load_extensions(bot)
    await bot.tree.sync()

    print(f"Successfully synced {len(bot.tree.get_commands())} commands.")

def setup_logger(log_level=logging.INFO):
    # Set up basic logging as per https://discordpy.readthedocs.io/en/latest/logging.html#logging-setup
    logger = logging.getLogger()  # 'discord')
    logger.setLevel(log_level)
    handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='a')
    handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
    logger.addHandler(handler)


def extensions():
    files = Path("bot", "cogs").rglob("*.py")
    for file in files:
        yield file.as_posix()[:-3].replace("/", ".")


async def load_extensions(_bot):
    for ext in extensions():
        try:
            await _bot.load_extension(ext)
        except Exception as ex:
            print(f"Failed to load extension {ext} - exception: {ex}")
            traceback.print_exc()


async def main():
    setup_logger()
    async with bot:
        await bot.start(utils.config.token)
