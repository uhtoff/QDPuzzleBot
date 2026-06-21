import asyncio
import logging
import re
from pathlib import Path
import traceback

import discord
from discord.ext import commands

from bot import utils
from bot.utils import config

__version__ = "0.1.0"

invite_link = "https://discordapp.com/api/oauth2/authorize?client_id={}&permissions=8&scope=bot%20applications.commands"

TEST_GUILD_IDS = [
    1227020288445779978,
    1328015907737436301,
]

def _install_ratelimit_probe():
    """Diagnostic: when a response leaves a rate-limit bucket exhausted with a long
    reset (>60s), log its X-RateLimit-Scope/Bucket/Reset and URL. That is the very
    response (usually a 200) that seeds the pre-emptive ``RateLimited`` raise caused
    by ``max_ratelimit_timeout`` below — so it tells us, before the next request
    fails, whether a long channel cooldown is per-guild (scope=shared, URL shows the
    guild id) or account/global (scope=user / X-RateLimit-Global). Fires only on a
    >60s reset, so it is silent in normal operation. Remove once the scope is known.
    """
    import discord.http as _dhttp
    if getattr(_dhttp.Ratelimit.update, "_probe_installed", False):
        return
    _probe_log = logging.getLogger("bot.ratelimit_probe")
    _orig_update = _dhttp.Ratelimit.update

    def _update(self, response, *, use_clock=False):
        _orig_update(self, response, use_clock=use_clock)
        try:
            if self.remaining == 0 and (self.reset_after or 0) > 60:
                h = response.headers
                _probe_log.warning(
                    "RATELIMIT-PROBE exhausted: scope=%s global=%s bucket=%s limit=%s "
                    "reset_after=%.0fs url=%s",
                    h.get("X-RateLimit-Scope"), h.get("X-RateLimit-Global"),
                    h.get("X-RateLimit-Bucket"), h.get("X-RateLimit-Limit"),
                    self.reset_after, getattr(response, "url", "?"),
                )
        except Exception:
            pass

    _update._probe_installed = True
    _dhttp.Ratelimit.update = _update


_install_ratelimit_probe()


async def get_prefix(_bot, message):
    return commands.when_mentioned_or(utils.config.prefix)(_bot, message)


bot = commands.AutoShardedBot(command_prefix=get_prefix, intents=discord.Intents.all(), case_insensitive=True, max_ratelimit_timeout=60)
bot.version = __version__

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

@bot.event
async def on_ready():
    bot.invite = invite_link.format(bot.user.id)
    guild_names = [g.name for g in bot.guilds]
    guild_ids = [g.id for g in bot.guilds]

    print(
        f"""Logged in as {bot.user}..
        Serving {len(bot.users)} users in {len(bot.guilds)} guilds: {guild_names} {guild_ids}
        Invite: {invite_link.format(bot.user.id)}
    """
    )

    # for guild in bot.guilds:
    #     try:
    #         guild_obj = discord.Object(id=guild.id)
    #         #bot.tree.clear_commands(guild=guild_obj)
    #         # bot.tree.copy_global_to(guild=guild_obj)
    #         synced = await bot.tree.sync(guild=guild_obj)
    #         print(f"Synced {len(synced)} commands for guild '{guild.name}' ({guild.id})")
    #     except Exception as e:
    #         print(f"Failed to sync commands for {guild.name} ({guild.id}): {e}")
    #
    # print("\nRegistered App Commands:")
    # for app_command in bot.tree.get_commands():
    #     print(f"- {app_command.name} (Type: {type(app_command)})")

# @bot.command(name="sync")
# async def sync_tree(ctx):
#     synced = await bot.tree.sync()
#     print(f"Synced {len(synced)} command(s).")

@bot.event
async def setup_hook():
    await load_extensions(bot)
    # Start the optional embedded web dashboard (no-op unless web.enabled).
    try:
        from bot.web import start_web
        bot.web_task = await start_web(bot)
    except Exception:
        traceback.print_exc()
    if config.development:
        # Only one sync call per guild.
        for gid in TEST_GUILD_IDS:
            guild = discord.Object(id=gid)
            bot.tree.copy_global_to(guild=guild)
            try:
                synced = await bot.tree.sync(guild=guild)
                print(f"[setup_hook] Synced {len(synced)} commands to guild {gid}")
            except asyncio.CancelledError:
                # Ctrl-C during rate limit sleep -> clean exit
                print("[setup_hook] Sync cancelled (shutdown).")
                raise
            except Exception as e:
                print(f"[setup_hook] Failed to sync guild {gid}: {e}")

    else:
        try:
            synced = await bot.tree.sync()
            print(f"[setup_hook] Globally synced {len(synced)} commands.")
        except asyncio.CancelledError:
            print("[setup_hook] Global sync cancelled (shutdown).")
            raise

@bot.command()
@commands.is_owner()
async def wipe_globals(ctx):
    bot.tree.clear_commands(guild=None)
    synced = await bot.tree.sync()
    await ctx.send(f"Wiped global commands. Global now has {len(synced)}.")

@bot.command()
@commands.is_owner()
async def nuke_all_appcmds(ctx):
    # 1) Delete ALL global commands (on Discord)
    bot.tree.clear_commands(guild=None)
    synced = await bot.tree.sync()
    await ctx.send(f"Nuked globals. Global now has {len(synced)} commands.")

    # 2) Delete ALL guild commands (on Discord) for every guild the bot is in
    for g in bot.guilds:
        guild_obj = discord.Object(id=g.id)
        bot.tree.clear_commands(guild=guild_obj)
        synced_g = await bot.tree.sync(guild=guild_obj)
        await ctx.send(f"Nuked guild cmds in {g.name}. Guild now has {len(synced_g)} commands.")

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
