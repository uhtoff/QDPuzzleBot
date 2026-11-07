import asyncio
import logging
import logging.handlers
import os
import re
import signal
import time
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

async def get_prefix(_bot, message):
    return commands.when_mentioned_or(utils.config.prefix)(_bot, message)


class PuzzleBot(commands.AutoShardedBot):
    """AutoShardedBot plus the extra attributes this app staples on at runtime."""
    version: str
    invite: str
    web_task: "asyncio.Task | None"
    _web_shutdown: asyncio.Event


bot = PuzzleBot(command_prefix=get_prefix, intents=discord.Intents.all(), case_insensitive=True)
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
    assert bot.user is not None  # on_ready fires only after login
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

# discord.log is the root logger's sink, so it collects web-route tracebacks and
# the dashboard's per-request access lines as well as gateway chatter. It rotates
# at midnight. backupCount only caps the NUMBER of files (days with no logging
# produce no file), and legacy size-rotated discord.log.1..5 are never touched by
# it, so rotated files are also pruned by AGE: the retention the public privacy
# page promises (frontend/src/pages/Privacy.tsx) is "deleted once 30 days old".
LOG_BACKUP_DAYS = 30
LOG_FILE = 'discord.log'


def prune_old_logs(directory: str | Path = '.', base=LOG_FILE, max_age_days=LOG_BACKUP_DAYS, now=None):
    """Delete rotated logs (``<base>.*``) whose mtime is older than ``max_age_days``.

    Matches both the timed suffix (discord.log.YYYY-MM-DD) and legacy size-rotated
    numbers (discord.log.N). The live ``<base>`` file and unrelated files are never
    touched. Returns the deleted paths.
    """
    cutoff = (time.time() if now is None else now) - max_age_days * 86400
    deleted = []
    for path in Path(directory).glob(base + '.*'):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                deleted.append(path)
        except OSError:
            pass  # best effort: never let log housekeeping break logging
    return deleted


class AgePrunedTimedRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """Midnight rotation that also deletes rotated files older than LOG_BACKUP_DAYS."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._prune()

    def doRollover(self):
        super().doRollover()
        self._prune()

    def _prune(self):
        prune_old_logs(Path(self.baseFilename).parent, Path(self.baseFilename).name)


def setup_logger(log_level=logging.INFO):
    # Set up basic logging as per https://discordpy.readthedocs.io/en/latest/logging.html#logging-setup
    logger = logging.getLogger()  # 'discord')
    logger.setLevel(log_level)
    handler = AgePrunedTimedRotatingFileHandler(
        filename=LOG_FILE,
        when='midnight',
        backupCount=LOG_BACKUP_DAYS,
        encoding='utf-8',
    )
    handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
    logger.addHandler(handler)


def extensions():
    files = Path("bot", "cogs").rglob("*.py")
    for file in files:
        # Only load modules that are actual cog extensions (define a module-level
        # setup()). Helper modules under cogs/ — e.g. ui.py, reusable discord.ui
        # components imported directly by cogs — have no setup() and would
        # otherwise raise NoEntryPointError on every startup.
        try:
            source = file.read_text(encoding="utf-8")
        except OSError:
            continue
        if not re.search(r"^\s*(async\s+)?def\s+setup\b", source, re.MULTILINE):
            continue
        yield file.as_posix()[:-3].replace("/", ".")


async def load_extensions(_bot):
    for ext in extensions():
        try:
            await _bot.load_extension(ext)
        except Exception as ex:
            print(f"Failed to load extension {ext} - exception: {ex}")
            traceback.print_exc()


def install_shutdown_handlers(_bot):
    """Turn SIGTERM/SIGINT into a graceful ``bot.close()``.

    start.sh sends SIGTERM, waits 10s, then SIGKILLs. With no handler the
    default action terminates instantly, aborting in-flight archive/channel/
    sheet work mid-operation and evaporating the in-process locks. Reconcile
    heals the result, but a deploy during a live hunt is a real window, and
    closing the gateway cleanly costs a fraction of that 10s.

    Note hypercorn is deliberately kept away from the signals (see
    bot/web/__init__.py) -- we own them here, and stop the dashboard by setting
    the shutdown event it was given.

    A second signal exits immediately, so a wedged shutdown can be cut short
    without waiting for start.sh's SIGKILL."""
    logger = logging.getLogger(__name__)
    loop = asyncio.get_running_loop()
    closing = False

    def _handle(signame: str) -> None:
        nonlocal closing
        if closing:
            logger.warning("Second %s during shutdown; exiting now.", signame)
            # os._exit skips interpreter cleanup, so flush the log sinks by hand
            # first -- but only ours: logging.shutdown() would close every
            # handler in the process, including any the caller owns.
            for handler in logging.getLogger().handlers:
                handler.flush()
            os._exit(1)
            return  # unreachable in production; keeps the fall-through honest
        closing = True
        logger.info("%s received; shutting down gracefully.", signame)
        # Let the dashboard finish its in-flight requests and stop serving.
        web_shutdown = getattr(_bot, "_web_shutdown", None)
        if web_shutdown is not None:
            web_shutdown.set()
        # close() makes the pending bot.start() return, so main() unwinds normally.
        # Parked on the bot because asyncio holds only a weak reference to a
        # task -- an unreferenced one can be collected before it ever runs.
        _bot._shutdown_task = loop.create_task(_bot.close(), name="graceful-shutdown")

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle, sig.name)
        except NotImplementedError:
            # Non-POSIX loop (Windows): leave the default disposition in place.
            logger.debug("No signal-handler support for %s on this loop.", sig.name)


async def main():
    setup_logger()
    async with bot:
        install_shutdown_handlers(bot)
        await bot.start(utils.config.token)
