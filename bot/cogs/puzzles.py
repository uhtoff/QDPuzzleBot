import datetime
import logging
import random
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import tasks, commands
from googleapiclient.errors import HttpError
import pytz
import asyncio
from functools import wraps

from bot.utils import urls
from bot.store import PuzzleData, PuzzleJsonDb, GuildSettingsDb, \
    RoundData, RoundJsonDb, HuntData, HuntJsonDb, SheetsJsonDb, AdditionalSheetData
from bot.utils.chunking import build_note_embeds
from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLES, STATUSES, PRIORITIES

logger = logging.getLogger(__name__)

# Per-puzzle async mutex — shared with the web edge via bot/core/locks so a
# dashboard write serializes against the matching bang-command (see M4).
from bot.core.locks import _PUZZLE_LOCKS
from bot.core.errors import SheetRefreshError
from bot.cogs.ui import ConfirmView


def with_puzzle_mutex(*, wait: bool = True):
    """
    Decorator that serializes commands per puzzle.
    If wait=False, it will error immediately if busy.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(self, ctx: commands.Context, *args, **kwargs):
            env = getattr(ctx, "env", None) or self.environment.get(ctx.message.id)
            puzzle = (env or {}).get("puzzle")

            # If not in a puzzle context, just run
            if not puzzle:
                return await func(self, ctx, *args, **kwargs)

            lock = _PUZZLE_LOCKS[puzzle.id]

            if wait:
                if lock.locked():
                    await ctx.send("⏳ Hold your horses!  Another command is running for this puzzle, I'll get to you in a second.")
                async with lock:
                    return await func(self, ctx, *args, **kwargs)

            try:
                await asyncio.wait_for(lock.acquire(), timeout=0.001)
            except asyncio.TimeoutError:
                await ctx.send("⏳ Woah there, I'm busy running another command on that puzzle. Patience is a virtue, try again later.")
                return

            try:
                return await func(self, ctx, *args, **kwargs)
            finally:
                lock.release()

        return wrapper
    return decorator

class Puzzles(commands.Cog):
    MAX_CHANNELS_PER_CATEGORY = 50
    MAX_CHANNELS_PER_GUILD = 500
    MAX_CATEGORIES = 50
    GENERAL_CHANNEL_NAME = "general"
    ROLE_REASON = "bot-role"
    PUZZLE_REASON = "bot-puzzle"
    DELETE_REASON = "bot-delete"
    HUNT_REASON = "bot-hunt-general"
    ROUND_REASON = "bot-round"
    SOLVE_CATEGORY = False
    SOLVE_DIVIDER = "———solved———"
    SOLVED_PUZZLES_CATEGORY = "solved"
    PUZZLE_GROUPS = ["Round","Metapuzzle","Metaless Round"]
    # module-level constants (from bot.core.constants), re-exposed as class attributes
    STATUSES = STATUSES
    PRIORITIES = PRIORITIES
    REMINDERS = [
            "Welcome to Duck Soup - together we will be stronger as Quarantine Duckrypters - Souped Up!",
            "Don't forget that Mystery Hunt is a marathon, not a sprint.  Stand up, walk around, eat some fruit, grab a drink.  "
            "You could even consider having some sleep...",
            "PuzzleBot thinks you should have a drink and a break, you wouldn't want to disappoint PuzzleBot would you?",
            "Scarier than the Minnesotan Loon, PuzzleBot commands you to take a break.",
            "Much like Santa, PuzzleBot knows when you've been sleeping and frankly you haven't been doing enough, take a break!",
            "Hydration keeps your brain moist and a moist brain does better thinking, it's also delicious.",
            "Surely you should have eaten something other than snacks and pizza by now?  No?  "
            "What would your mother say?",
            "Look into their eyes, PuzzleBot has no soul, but they can see yours and it hasn't been well fed or rested enough.",
            "The answer to your hint request is that you would have already solved the puzzle by now if you had actually taken a break.",
            "PuzzleBot says to stay hydrated and sleep well. You wouldn't dare to disobey, would you?",
            "PuzzleBot is concerned you might develop deep vein thromboses, so stand up, stretch, walk around...jog?"
            "Don't worry there'll still be lots of puzzles left to solve even if you take a break before that meta is done.  Honestly."
        ]

    def __init__(self, bot):
        self.bot = bot
        from bot.core.puzzle_service import PuzzleService
        self.service = PuzzleService(self.bot)
        self.reminder_index=0
        self.position_lock = asyncio.Lock()
        self.environment = {}
        # channel.id -> latest desired name, for renames deferred past the bucket.
        self._pending_renames = {}
        # Strong refs to in-flight deferred-rename tasks so they aren't GC'd.
        self._rename_tasks = set()

    # Discord enforces a strict, per-channel rename bucket (historically ~2 renames
    # per 10 minutes). discord.py normally waits out a 429, but that can block a
    # command for up to ~10 minutes. With max_ratelimit_timeout set on the bot, an
    # over-long wait raises discord.RateLimited instead; we catch it here and apply
    # the rename in the background, coalescing rapid renames to the latest name.
    async def safe_rename(self, channel, name, ctx=None):
        try:
            await channel.edit(name=name)
            return True
        except discord.RateLimited as exc:
            already_pending = channel.id in self._pending_renames
            self._pending_renames[channel.id] = name
            if ctx is not None:
                await ctx.send(
                    f"⏳ Discord is rate-limiting renames on this channel; "
                    f"I'll apply the new name in about {round(exc.retry_after)}s."
                )
            if not already_pending:
                task = asyncio.create_task(self._deferred_rename(channel, exc.retry_after))
                self._rename_tasks.add(task)
                task.add_done_callback(self._rename_tasks.discard)
            return False

    async def _deferred_rename(self, channel, delay):
        """Wait out the rename bucket, then apply the latest requested name,
        coalescing any further renames queued while we were waiting."""
        attempts = 0
        while attempts < 6:
            await asyncio.sleep(delay + 1)
            name = self._pending_renames.get(channel.id)
            if name is None:
                return
            attempts += 1
            try:
                await channel.edit(name=name)
            except discord.RateLimited as exc:
                delay = exc.retry_after
                continue
            except discord.HTTPException:
                logger.exception("Deferred rename of channel %s failed", channel.id)
                self._pending_renames.pop(channel.id, None)
                return
            # Applied. If a newer name was queued meanwhile, loop to apply it too.
            if self._pending_renames.get(channel.id) == name:
                self._pending_renames.pop(channel.id, None)
                return
            delay = 0
        logger.warning("Gave up deferred rename of channel %s after repeated rate limits", channel.id)
        self._pending_renames.pop(channel.id, None)

    async def cog_before_invoke(self, ctx):
        """For separating commands use ctx.message.id"""
        """For logging ctx.author.name and ctx.message.content"""
        """Before command invoked setup puzzle objects and channel type"""
        guild = None
        guild_data = None
        channel_type = None
        hunt = None
        hunt_round = None
        puzzle = None
        gsheet_cog = None
        guild = ctx.guild
        guild_data = GuildSettingsDb.get(ctx.guild.id)
        channel_type = GuildSettingsDb.get_channel_type(ctx.channel.id)
        if channel_type is None:
            channel_type = GuildSettingsDb.get_channel_type(ctx.channel.category.id)
        if channel_type is None:
            channel_type = "Guild"
        if channel_type == 'Hunt':
            hunt = HuntJsonDb.get_by_attr(channel_id=ctx.channel.id,)
        if channel_type == 'Group':
            hunt_round = RoundJsonDb.get_by_attr(category_id=ctx.channel.category.id,)
            if hunt_round:
                channel_type = hunt_round.type
                hunt = HuntJsonDb.get_by_attr(id=hunt_round.hunt_id)
                if hunt_round.meta_id:
                    puzzle = PuzzleJsonDb.get_by_attr(id=hunt_round.meta_id)
        if channel_type == 'Puzzle':
            puzzle = PuzzleJsonDb.get_by_attr(channel_id=ctx.channel.id,)
            # Check if we've actually got the metapuzzle for a round and set the right channel type if so
            # Otherwise grab the group/round from the category
            if puzzle:
                hunt_round = RoundJsonDb.get_by_attr(meta_id=puzzle.id)
                if hunt_round:
                    channel_type = hunt_round.type
                else:
                    hunt_round = RoundJsonDb.get_by_attr(category_id=ctx.channel.category.id, )
                hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        print (f"Channel type: {channel_type}")
        gsheet_cog = self.bot.get_cog("GoogleSheets")
        if gsheet_cog is not None:
            gsheet_cog.set_notify_channel(ctx.channel)
        if hunt is not None:
            gsheet_cog.set_spreadsheet_id(hunt.google_sheet_id)
            gsheet_cog.set_archive_spreadsheet_id(hunt.archive_google_sheet_id)
        self.environment[ctx.message.id] = {'channel_type': channel_type,
                                            'hunt': hunt,
                                            'hunt_round': hunt_round,
                                            'puzzle': puzzle,
                                            'guild': guild,
                                            'guild_data': guild_data,
                                            'gsheet_cog': gsheet_cog}

    async def cog_after_invoke(self, ctx):
        """After command invoked ensure changes committed to database"""
        if self.get_puzzle(ctx):
            PuzzleJsonDb.commit(self.get_puzzle(ctx))
        if self.get_hunt_round(ctx):
            RoundJsonDb.commit(self.get_hunt_round(ctx))
        if self.get_hunt(ctx):
            HuntJsonDb.commit(self.get_hunt(ctx))
        if self.get_guild_data(ctx):
            GuildSettingsDb.commit(self.get_guild_data(ctx))
        self.environment.pop(ctx.message.id, None)

    async def meta_code_autocomplete(
            self,
            interaction: discord.Interaction,
            current: str,
    ) -> list[app_commands.Choice[str]]:
        # Pull valid rounds/metas from DB
        channel = interaction.channel
        hunt_id = RoundJsonDb.get_by_attr(category_id = channel.category.id).hunt_id
        rounds = RoundJsonDb.get_all(hunt_id)
        rounds_sorted = sorted(
            rounds,
            key=lambda r: (
                r.solve_time is not None,  # False (unsolved) first, True (solved) last
                -(r.start_time.timestamp() if r.start_time else 0.0),  # newest first
            )
        )
        current_lc = (current or "").lower()
        choices: list[app_commands.Choice[str]] = []
        for r in rounds_sorted:
            # filter to only rounds that are metas (meta_code is a 6-char string;
            # non-meta rounds keep the default int 0)
            if not isinstance(r.meta_code, str) or len(r.meta_code) != 6:
                continue
            # Build a human label. Keep it short-ish.
            label = f"{r.name} ({r.meta_code})"
            # Allow searching by name OR meta_code OR id
            haystack = f"{r.name} {r.meta_code} {r.id}".lower()
            if current_lc in haystack:
                choices.append(app_commands.Choice(name=label[:100], value=r.meta_code))

            if len(choices) >= 25:  # Discord hard limit
                break

        return choices

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{type(self).__name__} Cog ready.")


    def clean_name(self, name):
        """Cleanup name to be appropriate for discord channel"""
        name = name.strip()
        if (name[0] == name[-1]) and name.startswith(("'", '"')):
            name = name[1:-1]
        return "-".join(name.lower().split())

    def get_channel_type(self, ctx):
        return self.environment[ctx.message.id]['channel_type'] or None

    def set_channel_type(self, ctx, channel_type):
        self.environment[ctx.message.id]['channel_type'] = channel_type

    def get_hunt(self, ctx):
        return self.environment[ctx.message.id]['hunt'] or None

    def set_hunt(self, ctx, hunt):
        self.environment[ctx.message.id]['hunt'] = hunt

    def get_hunt_round(self, ctx):
        return self.environment[ctx.message.id]['hunt_round'] or None

    def set_hunt_round(self, ctx, hunt_round):
        self.environment[ctx.message.id]['hunt_round'] = hunt_round

    def get_puzzle(self, ctx):
        return self.environment[ctx.message.id]['puzzle'] or None

    def set_puzzle(self, ctx, puzzle):
        self.environment[ctx.message.id]['puzzle'] = puzzle

    def get_guild(self, ctx):
        return self.environment[ctx.message.id]['guild'] or None

    def set_guild(self, ctx, guild):
        self.environment[ctx.message.id]['guild'] = guild

    def get_guild_data(self, ctx):
        return self.environment[ctx.message.id]['guild_data'] or None

    def set_guild_data(self, ctx, guild_data):
        self.environment[ctx.message.id]['guild_data'] = guild_data

    def get_gsheet_cog(self, ctx):
        return self.environment[ctx.message.id]['gsheet_cog'] or None

    def set_gsheet_cog(self, ctx, gsheet_cog):
        self.environment[ctx.message.id]['gsheet_cog'] = gsheet_cog

    def get_puzzle_sheet(self, ctx, puzzle: PuzzleData):
        hunt = self.get_hunt(ctx)
        if puzzle.archived:
            return hunt.archive_google_sheet_id
        else:
            return hunt.google_sheet_id

    def get_additional_sheet_spreadsheet(self, ctx, puzzle: PuzzleData):
        hunt = self.get_hunt(ctx)
        if puzzle.solved:
            return hunt.archive_google_sheet_id
        else:
            return hunt.google_sheet_id

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        # Unknown command: stay silent rather than dumping an error at the user
        # (people mistype, and other cogs may own the command).
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.CheckFailure) and str(error) == "PUZZLE_BUSY":
            await ctx.send("⏳ That puzzle is busy running another command. Try again in a moment.")
            return
        # Missing role / permission: 30+ commands are role-gated; show a friendly
        # line instead of a raw MissingAnyRole traceback.
        if isinstance(error, (commands.MissingAnyRole, commands.MissingRole,
                              commands.MissingPermissions, commands.NotOwner)):
            await ctx.send(":no_entry_sign: Sorry, you don't have the role needed to run that command.")
            return
        # Bad / missing arguments: tell the user how the command is meant to be called.
        if isinstance(error, commands.UserInputError):
            usage = f"{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}".strip()
            await ctx.send(f":grey_question: Sorry, I couldn't understand that. Usage: `{usage}`")
            return
        # Any other check failure (e.g. wrong channel): keep it short, no traceback.
        if isinstance(error, commands.CheckFailure):
            await ctx.send(":no_entry_sign: You can't run that command here.")
            return
        # Unwrap nested framework wrappers (HybridCommandError -> app_commands
        # CommandInvokeError -> ...) down to the real exception.
        original = error
        while getattr(original, "original", None) is not None:
            original = original.original
        if isinstance(original, HttpError):
            status = getattr(original.resp, "status", None)
            if status == 429:
                await ctx.send("⚠️ Google Sheets is rate-limiting us right now, so your "
                               "change may not have been saved. Please try again in a moment.")
            else:
                await ctx.send("⚠️ Google Sheets returned an error, so your change may not "
                               "have been saved. Please try again shortly.")
            logger.warning("Google Sheets error in command %s: %s", ctx.command, original)
            return
        if isinstance(original, discord.RateLimited):
            await ctx.send("⏳ Discord is rate-limiting this action; please try again in a moment.")
            return
        # Genuinely unexpected: log the full traceback server-side, show the user a
        # short apology rather than a raw exception dump.
        logger.exception("Unhandled error in command %s", ctx.command, exc_info=original)
        await ctx.send(":exclamation: Something went wrong running that command. The error has been logged.")

    async def check_is_bot_channel(self, ctx) -> bool:
        """Check if command was sent to bot channel configured in settings"""
        settings = GuildSettingsDb.get_cached(ctx.guild.id)
        if not settings.discord_bot_channel:
            # If no channel is designated, then all channels are fine
            # to listen to commands.
            return True

        if ctx.channel.name == settings.discord_bot_channel:
            # Channel name matches setting (note, channel name might not be unique)
            return True

        await ctx.send(f":exclamation: Most bot commands should be sent to #{settings.discord_bot_channel}")
        return False

    async def check_available_space(self, ctx, required_channels = 0, required_categories = 0):
        space_available = True
        guild = ctx.guild
        channels = len(ctx.channel.category.channels)
        categories = len(guild.categories)
        total_channels = len(guild.channels)
        remaining_channels = self.MAX_CHANNELS_PER_GUILD - total_channels
        remaining_categories = self.MAX_CATEGORIES - categories
        remaining_category_channels = self.MAX_CHANNELS_PER_CATEGORY-channels
        if remaining_channels < required_channels or remaining_category_channels < required_channels or remaining_categories < required_categories:
            space_available = False
        bot_channel = discord.utils.get(ctx.guild.channels, name=self.get_guild_data(ctx).discord_bot_channel)
        if space_available is False:
            await bot_channel.send(f"@here A command has just failed due to lack of space on the server, there are currently {categories} categories on the server, {channels} channels in {ctx.channel.category.name} and {total_channels} total channels.")
        else:
            remaining_channels -= required_channels
            remaining_category_channels -= required_channels
            remaining_categories -= required_categories
            if remaining_channels <= 10:
                await bot_channel.send(
                    f"@here Server space is getting tight, remaining channel space is {remaining_channels}."
                )
            if remaining_categories <= 2 and required_categories > 0:
                await bot_channel.send(
                    f"@here Server space is getting tight, remaining category space is {remaining_categories}."
                )
            if remaining_category_channels <= 2:
                await bot_channel.send(
                    f"@here The {ctx.channel.category.name} category is getting full, remaining channel space is {remaining_category_channels}."
                )
        return space_available

    @commands.command(aliases=["h"])
    @commands.has_any_role(*ADMIN_ROLES)
    async def hunt(self, ctx, *, arg):
        """*(admin) Create a new hunt: !hunt hunt-name:hunt-url"""

        if not (await self.check_is_bot_channel(ctx)):
            return

        role = None
        if ", " in arg:
            arg, role = arg.split(", ", 1)
        if ":" in arg:
            hunt_name, hunt_url = arg.split(":", 1)
            if HuntJsonDb.check_duplicates(hunt_name):
                return await ctx.send(f":exclamation: **Hunt {hunt_name}** already exists, please use a different name")
            await self.create_hunt(ctx, hunt_name, hunt_url, role)
            return await ctx.send(
                f":white_check_mark: I've created a new hunt category and channel for {hunt_name}"
            )

        await ctx.send(f"Unable to parse hunt name {arg}, try using `!h hunt-name:hunt-url`")

    async def create_hunt(self, ctx, hunt_name: str, hunt_url: str, role_name: Optional[str] = None):
        guild = ctx.guild

        if await self.check_available_space(ctx,5,2) is False:
            return await ctx.send(":x: There is not enough enough space on the server to create a new hunt, please inform the moderators.")

        category_name = self.clean_name(hunt_name)
        google_drive_id = None
        team_role = None
        if role_name:
            # Reuse an existing role with this name if present, so a hunt's team
            # role id is captured even when the role was made by hand.
            team_role = discord.utils.get(guild.roles, name=role_name)
            if team_role is None:
                team_role = await guild.create_role(
                    name=role_name, colour=discord.Colour.random(), mentionable=True, reason=self.ROLE_REASON
                )

        category = await guild.create_category(category_name, position=max(len(guild.categories) - 2,0))
        text_channel, created_text = await self.get_or_create_channel(
            guild=guild, category=category, channel_name=self.GENERAL_CHANNEL_NAME, channel_type="text", reason=self.HUNT_REASON, position = 0
        )
        settings = self.get_guild_data(ctx)

        if self.SOLVE_CATEGORY:
            await guild.create_category(self.get_solved_puzzle_category(hunt_name), position=max(len(guild.categories) - 2,0))
        else:
            await self.get_or_create_channel(
                guild=guild, category=category, channel_name=self.SOLVE_DIVIDER,
                channel_type="text", reason=self.ROUND_REASON, position=500
            )

        if self.get_gsheet_cog(ctx) is not None:
            google_drive_id = await self.get_gsheet_cog(ctx).create_hunt_spreadsheet(hunt_name)

        uid = HuntJsonDb.generate_uid('uid')

        new_hunt = HuntData(
            name=hunt_name,
            category_id=category.id,
            channel_id=text_channel.id,
            guild_id=settings.id,
            url=hunt_url,
            uid=uid,
        )

        if team_role is not None:
            # Captured so the dashboard can gate active-hunt visibility by team role.
            new_hunt.role_id = team_role.id

        if google_drive_id:
            new_hunt.google_sheet_id=google_drive_id

        HuntJsonDb.commit(new_hunt)

        # add hunt settings
        initial_message = await self.send_initial_hunt_channel_messages(ctx, text_channel, hunt=new_hunt)
        await initial_message.pin()

        return (category, text_channel, True)

    @commands.hybrid_command(description="Set the hunt to use a second spreadsheet to hold solved puzzles (admin only)",)
    @commands.has_any_role(*ADMIN_ROLES)
    async def set_to_archive(self, ctx):
        """*(admin) Set hunt to use a second spreadsheet to hold solved puzzles: !set_to_archive*"""
        if self.get_channel_type(ctx) != "Hunt":
            await ctx.send(":x: This command must be done from the main Hunt channel")
            return

        hunt = self.get_hunt(ctx)

        if len(hunt.archive_google_sheet_id)>0:
            await ctx.send(":x: This hunt is already set to use a second sheet for solved puzzles")
            return

        if self.get_gsheet_cog(ctx) is not None:
            google_drive_id = await self.get_gsheet_cog(ctx).create_hunt_archive_spreadsheet(hunt.name)
            hunt.archive_google_sheet_id = google_drive_id

        HuntJsonDb.commit(hunt)

        await ctx.send(":white_check_mark: The hunt has been set to use a second sheet for solved puzzles")
        await self.info(ctx, update=True)
        return


    @commands.command(aliases=["p"])
    @with_puzzle_mutex(wait=True)
    async def puzzle(self, ctx, *, arg):
        """*Create new puzzle channels: !p puzzle-name*"""

        puzzle_name = arg
        if PuzzleJsonDb.check_duplicates_in_hunt(puzzle_name, self.get_hunt(ctx).id):
            return await ctx.send(f":exclamation: Puzzle **{puzzle_name}** already exists in this hunt or will lead to a duplicate channel name, please use a different name")

        if await self.check_available_space(ctx,1) is False:
            return await ctx.send(":x: There is no free channel to create this puzzle, please inform the moderators.")

        category = ctx.channel.category
        new_puzzle = await self.create_puzzle(ctx, puzzle_name, self.get_tag_from_category(category))
        await self.create_puzzle_channel(ctx, new_puzzle, position='bottom')

    async def create_metapuzzle(self,ctx, puzzle_name, tag = None):
        return await self.create_puzzle(ctx, puzzle_name, tag, metapuzzle = 1)

    async def create_puzzle(self, ctx, puzzle_name, tag = None, **kwargs):
        new_puzzle = PuzzleData(
            name=puzzle_name,
            hunt_id=self.get_hunt(ctx).id,
            start_time=datetime.datetime.now(tz=pytz.UTC),
            status='Unstarted',
            priority='Normal'
        )
        if kwargs.get('metapuzzle'):
            new_puzzle.metapuzzle = kwargs['metapuzzle']
        if self.get_hunt(ctx).url:
            # NOTE: this is a heuristic and may need to be updated!
            # This is based on last year's URLs, where the URL format was
            # https://<site>/puzzle/puzzle_name
            prefix = self.get_hunt(ctx).puzzle_prefix or 'puzzle'
            hunt_url_base = self.get_hunt(ctx).url.rstrip("/")
            p_name = self.clean_name(puzzle_name).replace("-", self.get_hunt(ctx).url_sep)
            new_puzzle.url = f"{hunt_url_base}/{prefix}/{p_name}"

        if tag:
            new_puzzle.tags.append(tag)

        if self.get_gsheet_cog(ctx) is not None:
            # update google sheet ID
            await self.get_gsheet_cog(ctx).create_puzzle_spreadsheet(new_puzzle)

        PuzzleJsonDb.commit(new_puzzle)

        try:
            await self._update_all_metas(ctx, new_puzzle)
        except SheetRefreshError as exc:
            await self._warn_stale_sheets(ctx, exc)

        return new_puzzle

    async def _update_metameta_impl(self, ctx, metameta, hunt_id):
        all_puzzles = PuzzleJsonDb.get_all_from_hunt(hunt_id)
        await self.get_gsheet_cog(ctx).add_metametapuzzle_data(metameta, all_puzzles)

    @commands.command()
    async def update_metameta(self, ctx):
        hunt_id = self.get_hunt(ctx).id
        meta_meta_puzzle = PuzzleJsonDb.get_by_attr(metameta=1)
        if meta_meta_puzzle:
            await self._update_metameta_impl(ctx, meta_meta_puzzle, hunt_id)
        await ctx.send(":white_check_mark: Meta meta updated")

    @commands.hybrid_command(description="Rebuild meta roll-up sheets (organiser only)")
    @commands.has_any_role(*ORGANISER_ROLES)
    async def rebuild(self, ctx):
        """*(Organiser) Rebuild meta roll-up sheets. In a round/meta channel rebuilds
        just that round's meta; elsewhere rebuilds every meta in the hunt: !rebuild*"""
        hunt = self.get_hunt(ctx)
        if hunt is None:
            await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")
            return
        # Targeted: if this channel's category is a meta round, rebuild only that one.
        target = None
        category = getattr(ctx.channel, "category", None)
        if category is not None:
            candidate = RoundJsonDb.get_by_attr(category_id=category.id)
            if candidate is not None and candidate.meta_id:
                target = candidate
        rounds = [target] if target is not None else [
            r for r in RoundJsonDb.get_all(hunt.id) if r.meta_id
        ]
        if not rounds:
            await ctx.send(":information_source: No meta sheets to rebuild in this hunt.")
            return

        ok = 0
        failed = []
        for meta_round in rounds:
            try:
                await self.service.refresh_meta(hunt, meta_round)
                ok += 1
            except Exception:
                logger.exception("!rebuild: refresh failed for round %s", meta_round.id)
                failed.append(meta_round.meta_code or meta_round.name or str(meta_round.id))

        msg = f":white_check_mark: Rebuilt {ok} meta sheet{'s' if ok != 1 else ''}."
        if failed:
            msg += (f"\n:warning: {len(failed)} still failing (left stale): "
                    + ", ".join(str(f) for f in failed))
        await ctx.send(msg)

    async def update_metapuzzle(self, ctx, hunt_round):
        """Refresh a single meta's roll-up sheet (badged on failure via the rollup).
        Raises SheetRefreshError if it failed, so the bang edge surfaces it uniformly
        with _warn_stale_sheets instead of aborting on a raw Sheets error."""
        hunt = self.get_hunt(ctx)
        failed = await self.service.refresh_metas(hunt, [hunt_round])
        if failed:
            raise SheetRefreshError(failed)

    async def web_refresh_meta(self, hunt, hunt_round):
        """Best-effort wrapper over PuzzleService.refresh_meta, retained as the
        swallowing wrapper still used by web_create_puzzle (deferred to M3.5).
        Swallows Sheets errors."""
        try:
            await self.service.refresh_meta(hunt, hunt_round)
        except Exception:
            logger.exception("web_refresh_meta: sheet update failed for round %s",
                             getattr(hunt_round, "id", None))

    def _next_web_ctx_id(self):
        """Unique negative id for a synthetic web context (never collides with a Discord snowflake)."""
        self._web_ctx_seq = getattr(self, "_web_ctx_seq", 0) - 1
        return self._web_ctx_seq

    async def _web_render_info(self, guild, hunt, hunt_round, puzzle, channel, update):
        """(Re)render the pinned intro/info message via the same send_initial_*
        handlers the ! commands use. Returns the Message (or None on update if no
        pin exists). Caller owns try/except.

        The send_initial_* methods read hunt/guild_data from self.environment keyed
        by ctx.message.id, so we build a synthetic ctx and populate that entry.
        """
        from types import SimpleNamespace
        fake_id = self._next_web_ctx_id()
        self.environment[fake_id] = {
            "hunt": hunt,
            "guild_data": GuildSettingsDb.get(guild.id),
            "gsheet_cog": self.bot.get_cog("GoogleSheets"),
            "guild": guild,
            "puzzle": puzzle,
            "hunt_round": hunt_round,
            "channel_type": None,
        }
        fake_ctx = SimpleNamespace(message=SimpleNamespace(id=fake_id), guild=guild, channel=channel)
        try:
            handler = (
                self.send_initial_metapuzzle_channel_messages
                if puzzle.is_metapuzzle()
                else self.send_initial_puzzle_channel_messages
            )
            return await handler(fake_ctx, channel, update=update, hunt=hunt, hunt_round=hunt_round, puzzle=puzzle)
        finally:
            self.environment.pop(fake_id, None)

    async def _web_send_welcome(self, guild, hunt, hunt_round, puzzle, channel):
        """Post and pin the same intro message the ! commands send, from a web create."""
        try:
            message = await self._web_render_info(guild, hunt, hunt_round, puzzle, channel, update=False)
            await message.pin()
        except Exception:
            logger.exception("web create: failed to send/pin welcome message")

    async def web_refresh_info(self, guild, hunt, puzzle):
        """Best-effort refresh of a puzzle's pinned info message after a metadata
        change (status/priority/type/solution/sheets), mirroring `!info update`.
        Never raises — a Discord hiccup must not fail the underlying DB write."""
        if guild is None or not puzzle.channel_id:
            return
        channel = guild.get_channel(puzzle.channel_id)
        if channel is None:
            return
        # A metapuzzle's info embed names its round (meta_code/name); find the
        # round this puzzle is the meta of. Feeder puzzles don't need a round.
        hunt_round = None
        if puzzle.is_metapuzzle():
            for tag in puzzle.tags:
                candidate = RoundJsonDb.get_by_attr(id=tag)
                if candidate and candidate.meta_id == puzzle.id:
                    hunt_round = candidate
                    break
        try:
            await self._web_render_info(guild, hunt, hunt_round, puzzle, channel, update=True)
        except Exception:
            logger.exception("web_refresh_info: failed to refresh pinned info for puzzle %s", puzzle.id)

    async def web_create_puzzle(self, guild, hunt, round_data, puzzle_name, is_meta=False, send_welcome=True):
        """ctx-free puzzle creation for the dashboard.

        Mirrors create_puzzle + create_puzzle_channel but resolves the sheets cog
        and category explicitly and skips the (ctx-bound) welcome message. The
        puzzle is placed in round_data's category and tagged to that round.
        Raises RuntimeError("<code>") on a recoverable problem (e.g. no space).
        """
        category = discord.utils.get(guild.categories, id=round_data.category_id) if round_data else None
        # Space checks before we create any Google sheet (avoids orphan sheets).
        if len(guild.channels) >= self.MAX_CHANNELS_PER_GUILD:
            raise RuntimeError("guild_full")
        if category is not None and len(category.channels) >= self.MAX_CHANNELS_PER_CATEGORY:
            raise RuntimeError("category_full")

        new_puzzle = PuzzleData(
            name=puzzle_name,
            hunt_id=hunt.id,
            start_time=datetime.datetime.now(tz=pytz.UTC),
            status='Unstarted',
            priority='Normal',
        )
        if is_meta:
            new_puzzle.metapuzzle = 1
        if hunt.url:
            prefix = hunt.puzzle_prefix or 'puzzle'
            hunt_url_base = hunt.url.rstrip("/")
            p_name = self.clean_name(puzzle_name).replace("-", hunt.url_sep)
            new_puzzle.url = f"{hunt_url_base}/{prefix}/{p_name}"
        if round_data is not None:
            new_puzzle.tags.append(round_data.id)

        gsheet_cog = self.bot.get_cog("GoogleSheets")
        if gsheet_cog is not None:
            gsheet_cog.set_spreadsheet_id(hunt.google_sheet_id)
            gsheet_cog.set_archive_spreadsheet_id(hunt.archive_google_sheet_id)
            await gsheet_cog.create_puzzle_spreadsheet(new_puzzle)

        PuzzleJsonDb.commit(new_puzzle)

        # No "🧩" name prefix for metas: the web dashboard's META label is the
        # single source of truth, and a prefix-less name avoids ever renaming.
        try:
            channel_name = self.clean_name(new_puzzle.name)
            text_channel, created = await self.get_or_create_channel(
                guild=guild, category=category, channel_name=channel_name,
                channel_type="text", reason=self.PUZZLE_REASON,
            )
            if created:
                if category is not None:
                    divider = self.get_solve_divider_position(None, category)
                    if divider is not False:
                        async with self.position_lock:
                            await text_channel.edit(position=divider)
                new_puzzle.channel_mention = text_channel.mention
                new_puzzle.channel_id = text_channel.id
                new_puzzle.channel_name = text_channel.name
                PuzzleJsonDb.commit(new_puzzle)
                # Metapuzzle messages need the round's meta_code, which isn't set until
                # after web_create_round finishes; that path sends the welcome itself.
                if send_welcome:
                    await self._web_send_welcome(guild, hunt, round_data, new_puzzle, text_channel)
        except Exception:
            # Channel create (or post-create work) failed after the sheet + DB row were
            # committed — clean up both so we don't strand an orphan (row + Google tab).
            await self._cleanup_orphan_puzzle(hunt, new_puzzle)
            raise

        for tag in new_puzzle.tags:
            await self.web_refresh_meta(hunt, RoundJsonDb.get_by_attr(id=tag))
        return new_puzzle

    async def _cleanup_orphan_puzzle(self, hunt, puzzle):
        """Best-effort removal of a puzzle's Google sheet + DB row after a failed
        create. Never raises (we are already handling a create failure)."""
        gsheet_cog = self.bot.get_cog("GoogleSheets")
        if gsheet_cog is not None:
            try:
                gsheet_cog.set_spreadsheet_id(hunt.google_sheet_id)
                gsheet_cog.set_archive_spreadsheet_id(hunt.archive_google_sheet_id)
                await gsheet_cog.delete_puzzle_spreadsheet(puzzle)
            except Exception:
                logger.exception("orphan cleanup: failed to delete sheet for %s", puzzle)
        try:
            if getattr(puzzle, "id", 0):
                PuzzleJsonDb.delete(puzzle.id)
        except Exception:
            logger.exception("orphan cleanup: failed to delete DB row for %s", puzzle)

    async def web_create_round(self, guild, hunt, name):
        """ctx-free metapuzzle/round creation for the dashboard (like !mp).

        Clones the hunt's main category for permissions, creates a new category
        with a metapuzzle channel, and returns (RoundData, PuzzleData).
        Raises RuntimeError("<code>") on a recoverable problem.
        """
        if RoundJsonDb.check_duplicates_in_hunt(name, hunt):
            raise RuntimeError("round_exists")
        if PuzzleJsonDb.check_duplicates_in_hunt(name, hunt.id):
            raise RuntimeError("puzzle_name_clash")
        if len(guild.categories) >= self.MAX_CATEGORIES:
            raise RuntimeError("no_category_space")

        new_category_name = self.clean_name(name)
        existing_category = discord.utils.get(guild.categories, name=new_category_name)
        if existing_category or hunt.parallel_hunt:
            new_category_name = new_category_name + " — " + self.clean_name(hunt.name)
            existing_category = discord.utils.get(guild.categories, name=new_category_name)
        if existing_category:
            raise RuntimeError("category_exists")

        # Clone the hunt's main category so the new round inherits its permissions.
        ref_category = discord.utils.get(guild.categories, id=hunt.category_id)
        if ref_category is None:
            raise RuntimeError("no_template_category")
        new_category = None
        new_round = None
        puzzle = None
        try:
            new_category = await ref_category.clone(name=new_category_name)
            await new_category.edit(position=ref_category.position + 1)
            if self.SOLVE_CATEGORY is False:
                await self.get_or_create_channel(
                    guild=guild, category=new_category, channel_name=self.SOLVE_DIVIDER,
                    channel_type="text", reason=self.ROUND_REASON, position=500,
                )

            new_round = RoundData(name=name, hunt_id=hunt.id, category_id=new_category.id, type="Metapuzzle")
            RoundJsonDb.commit(new_round)

            puzzle = await self.web_create_puzzle(guild, hunt, new_round, name, is_meta=True, send_welcome=False)
            new_round.meta_code = RoundJsonDb.generate_uid('meta_code', 6, name)
            new_round.meta_id = puzzle.id
            RoundJsonDb.commit(new_round)
            # Send the meta welcome now that meta_code/meta_id are populated.
            channel = guild.get_channel(puzzle.channel_id) if puzzle.channel_id else None
            if channel is not None:
                await self._web_send_welcome(guild, hunt, new_round, puzzle, channel)
            return new_round, puzzle
        except Exception:
            # Roll back so a partial (e.g. rate-limited) create leaves neither a
            # meta-less round row NOR an orphan category. Without the category
            # cleanup, each renamed retry clones another category and strands it,
            # which is itself extra channel-create churn. Both deletes are
            # best-effort: during an active rate-limit they may fail too, which is
            # fine — still better than accumulating orphans on every retry.
            if new_round is not None and getattr(new_round, "id", 0):
                try:
                    RoundJsonDb.delete(new_round.id)
                except Exception:
                    logger.exception("web_create_round: rollback of round row failed")
            if puzzle is not None:
                await self._cleanup_orphan_puzzle(hunt, puzzle)
            if new_category is not None:
                try:
                    for ch in list(new_category.channels):
                        await ch.delete(reason="web_create_round rollback")
                    await new_category.delete(reason="web_create_round rollback")
                except Exception:
                    logger.exception("web_create_round: rollback of orphan category failed")
            raise

    async def web_post_solved_embed(self, guild, hunt, puzzle):
        """Best-effort dashboard-side presentation after service.solve: post the
        SOLVED embed in the puzzle channel, reposition it to the solved section,
        and refresh the pinned info. Never raises."""
        channel = guild.get_channel(puzzle.channel_id) if (guild and puzzle.channel_id) else None
        if channel is not None:
            guild_data = GuildSettingsDb.get(guild.id)
            emoji = guild_data.discord_bot_emoji if guild_data else ""
            embed = discord.Embed(
                title="PUZZLE SOLVED!",
                description=f"{emoji} :partying_face: Great work! Marked the solution as `{puzzle.solution}`",
            )
            embed.add_field(name="Follow-up", value="Solved from the dashboard. Tidying up the sheets…")
            try:
                await channel.send(embed=embed)
            except Exception:
                logger.exception("web_post_solved_embed: failed to post solved message")
        if channel is not None and channel.category is not None:
            try:
                divider = self.get_solve_divider_position(None, channel.category)
                if divider is not False:
                    async with self.position_lock:
                        await channel.edit(position=divider + 1)
            except Exception:
                logger.exception("web_post_solved_embed: failed to reposition channel")
        await self.web_refresh_info(guild, hunt, puzzle)

    def get_first_channel(self, ctx, category):
        position = False
        for channel in category.channels:
            if position is False or channel.position < position:
                position = channel.position
        return position

    def get_solve_divider_position(self, ctx, category):
        position = False
        for channel in category.channels:
            if channel.name == self.SOLVE_DIVIDER:
                position = channel.position
                break
        return position

    @commands.hybrid_command(description="Move the channel to the top of the unsolved section of the category (organisers only)",)
    @commands.has_any_role(*ORGANISER_ROLES)
    async def move_to_top(self, ctx):
        channel = ctx.channel
        position = self.get_first_channel(ctx, channel.category)
        await channel.edit(position=position)
        await ctx.send(":white_check_mark: Channel moved to top of category")

    @commands.hybrid_command(description="Move the channel to the bottom of the unsolved section of the category (organisers only)",)
    @commands.has_any_role(*ORGANISER_ROLES)
    async def move_to_bottom(self, ctx):
        channel = ctx.channel
        position = self.get_solve_divider_position(ctx, channel.category)
        await channel.edit(position=position)
        await ctx.send(":white_check_mark: Channel moved to bottom of unsolved")

    @commands.hybrid_command(description="Move the channel to the solved section of the category (organisers only)",)
    @commands.has_any_role(*ORGANISER_ROLES)
    async def move_to_solved(self, ctx):
        channel = ctx.channel
        position = self.get_solve_divider_position(ctx, channel.category)
        await channel.edit(position=position+1)
        await ctx.send(":white_check_mark: Channel moved to solved")


    def get_tag_from_category(self, category):
        hunt_round = RoundJsonDb.get_by_attr(category_id=category.id)
        if hunt_round:
            return hunt_round.id
        else:
            return None

    @commands.hybrid_command(description="Show all tags on the puzzle (admin only)",)
    @commands.has_any_role(*ORGANISER_ROLES)
    async def list_tags(self, ctx):
        """*(admin) For troubleshooting only: !list_tags*"""
        puzzle = self.get_puzzle(ctx)
        tag_info = ""
        tags = puzzle.tags.copy()
        for tag in tags:
            tag_name = RoundJsonDb.get_by_attr(id=tag).name
            tag_info += f"{tag_name} - ID:{tag}\n"
        embed = discord.Embed(
            description=f"""Tag listing for {puzzle.name}"""
        )
        embed.add_field(
            name="Current tags",
            value=tag_info,
            inline=False,
        )
        groups = RoundJsonDb.get_all(self.get_hunt(ctx).id)
        group_info = ""
        for group in groups:
            group_info += f"{group.name} - ID:{group.id}\n"
        embed.add_field(
            name="Available tags",
            value=group_info,
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.command()
    @commands.has_any_role(*ORGANISER_ROLES)
    async def add_tag(self, ctx, *, arg):
        """*(admin) For troubleshooting only: !add_tag tag_id*"""
        try:
            new_tag = int(arg)
        except ValueError:
            await ctx.channel.send(":x: Tag id must be a number.")
            return
        puzzle = self.get_puzzle(ctx)
        puzzle.tags.append(new_tag)
        await ctx.channel.send(f"```json\n{puzzle.to_json(indent=2)}```")

    @commands.command()
    @commands.has_any_role(*ORGANISER_ROLES)
    @with_puzzle_mutex(wait=True)
    async def remove_tag(self, ctx, *, arg):
        """*(admin) For troubleshooting only: !remove_tag tag_id*"""
        remove_tag = int(arg)
        puzzle = self.get_puzzle(ctx)
        if remove_tag in puzzle.tags:
            puzzle.tags.remove(remove_tag)
        await ctx.channel.send(f"```json\n{puzzle.to_json(indent=2)}```")

    @commands.command()
    @with_puzzle_mutex(wait=True)
    async def add_sheet(self, ctx, *args):
        puzzle = self.get_puzzle(ctx)

        if puzzle.solved:
            return await ctx.send(f":x: You can't add additional sheets to solved puzzles!")

        name = " ".join(args)

        sheet = await self.service.add_sheet(self.get_hunt(ctx), puzzle, name)
        await self.info(ctx, update=True)
        return await ctx.send(f":white_check_mark: Added sheet {name} to puzzle.")

    @commands.command(aliases=['delete_sheet'])
    @with_puzzle_mutex(wait=True)
    async def remove_sheet(self, ctx, number: int):
        """*Remove an additional sheet by its number (see !list_sheets): !remove_sheet 2*"""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        if number < 1 or number > len(puzzle.additional_sheets):
            return await ctx.send(f":x: No additional sheet #{number}. Use !list_sheets to see them.")
        sheet = puzzle.additional_sheets[number - 1]
        name = sheet.puzzle_name
        # Call the helper directly: the sheet is already resolved by index here, so
        # service.remove_sheet() (which re-looks-up by id) would just re-scan the list.
        await self.service._delete_additional_sheet(self.get_hunt(ctx), puzzle, sheet)
        await self.info(ctx, update=True)
        return await ctx.send(f":white_check_mark: Removed sheet {name} from puzzle.")

    @commands.command(aliases=['list_sheets'])
    async def list_additional_sheets(self, ctx):
        puzzle = self.get_puzzle(ctx)
        if len(puzzle.additional_sheets) == 0:
            return await ctx.send(':x: No additional sheets found!')

        embed = discord.Embed()

        embed.add_field(
            name="Additional Sheet Links",
            value=self.create_additional_sheet_list(ctx, puzzle),
            inline=False,
        )

        return await ctx.send(embed=embed)

    def create_additional_sheet_list(self, ctx, puzzle):
        sheet_links = ""
        count = 1
        spreadsheet_id = self.get_additional_sheet_spreadsheet(ctx, puzzle)
        for sheet in puzzle.additional_sheets:
            spreadsheet_url = urls.spreadsheet_url(spreadsheet_id,
                                                   sheet.google_page_id)
            sheet_links += f"{count}: [{sheet.puzzle_name}]({spreadsheet_url})\n"
            count+=1
        return sheet_links

    async def create_puzzle_channel(self, ctx, new_puzzle: PuzzleData, category = None, **kwargs):
        """Create new text channel for puzzle, and optionally a voice channel

        Save puzzle metadata to data_dir, send initial messages to channel, and
        create corresponding Google Sheet if GoogleSheets cog is set up.
        """
        send_initial_message = kwargs.get("send_initial_message", True)
        position = kwargs.get("position", 1)
        if category is None:
            category = ctx.channel.category
        # No "🧩" name prefix for metas (dashboard META label supersedes it).
        channel_name = self.clean_name(new_puzzle.name)

        text_channel, created_text = await self.get_or_create_channel(
            guild=self.get_guild(ctx), category=category, channel_name=channel_name,
            channel_type="text", reason=self.PUZZLE_REASON
        )
        if created_text:
            async with self.position_lock:
                if position == 'bottom':
                    await text_channel.edit(position=self.get_solve_divider_position(ctx, category))
                elif position == 'top':
                    await text_channel.edit(position=self.get_first_channel(ctx, category))
                else:
                    await text_channel.edit(position=position)
            new_puzzle.channel_mention=text_channel.mention
            new_puzzle.channel_id=text_channel.id
            new_puzzle.channel_name=text_channel.name
            if send_initial_message:
                initial_message = await self.send_initial_puzzle_channel_messages(ctx, text_channel, puzzle=new_puzzle)
                await initial_message.pin()

        if created_text:
            await ctx.send(
                f":white_check_mark: I've created a new puzzle channel for {category.name}: {text_channel.mention}"
            )
            PuzzleJsonDb.commit(new_puzzle)

        else:
            await ctx.send(
                f"I've found an already existing puzzle channel for {ctx.channel.category.name}: {text_channel.mention}"
            )
            await self.delete_puzzle_data(ctx, new_puzzle)

        return text_channel, created_text

    @commands.hybrid_command(description="Sync all the puzzles in the category to the metapuzzle (organiser only)",aliases=['sync_round'])
    @commands.has_any_role(*ORGANISER_ROLES)
    @with_puzzle_mutex(wait=True)
    async def sync_meta(self, ctx):
        """*(Admin only) Sync all the puzzles in this category to the metapuzzle: !sync_meta*"""
        puzzle = self.get_puzzle(ctx)
        if self.get_channel_type(ctx) not in ["Metaless Round","Metapuzzle","Round"]:
            await ctx.send(":x: This does not appear to be a main group channel")
            return
        meta_category = ctx.channel.category
        meta_round = RoundJsonDb.get_by_attr(category_id=meta_category.id)
        old_tags = []
        if meta_round:
            for channel in meta_category.channels:
                puzzle = PuzzleJsonDb.get_by_attr(channel_id=channel.id)
                if puzzle:
                    if puzzle.tags:
                        old_tags.extend(puzzle.tags.copy())
                    # Remove puzzle from old rounds if a not a metapuzzle
                    if puzzle.is_metapuzzle() is False:
                        puzzle.tags.clear()
                    puzzle.tags.append(meta_round.id)
                    PuzzleJsonDb.commit(puzzle)
            unique_old_tags = list(dict.fromkeys(old_tags))
            affected = [meta_round] + [RoundJsonDb.get_by_attr(id=t) for t in unique_old_tags]
            failed = await self.service.refresh_metas(self.get_hunt(ctx), affected)
            if failed:
                await self._warn_stale_sheets(ctx, SheetRefreshError(failed))

        await ctx.send(f":white_check_mark: All the puzzles in this category have been tagged to the group")

    @commands.hybrid_command(
        name="add_to_meta",
        description="Add the puzzle to a metapuzzle",
        aliases=["add_to_round"],
    )
    @app_commands.autocomplete(meta_code=meta_code_autocomplete)
    @with_puzzle_mutex(wait=True)
    async def add_to_meta(self, ctx, meta_code):
        """*Add the puzzle to a metapuzzle: !add_to_meta <meta_code>*"""
        if self.get_channel_type(ctx) == "Hunt":
            await ctx.send(":x: This does not appear to be a Puzzle channel")
            return
        puzzle = self.get_puzzle(ctx)
        meta_round = RoundJsonDb.get_by_attr(meta_code=meta_code)
        if meta_round:
            await self.service.add_to_meta(puzzle, meta_round)
            await ctx.send(
                f":white_check_mark: This puzzle has been tagged to {meta_round.name} and meta sheets are being updated.")
        else:
            await ctx.send(":x: Please send a valid meta code")

    @commands.hybrid_command(
        name="move_to_meta",
        description="Assign the puzzle to a metapuzzle",
        aliases=["move_to_round"],
    )
    @app_commands.autocomplete(meta_code=meta_code_autocomplete)
    @with_puzzle_mutex(wait=True)
    async def move_to_meta(self, ctx, meta_code):
        """*Assign the puzzle to a metapuzzle, this will additionally remove previous assignments and move it to the category: !move_to_meta <meta_code>*"""
        if self.get_channel_type(ctx) == "Hunt":
            await ctx.send(":x: This does not appear to be a Puzzle channel")
            return
        puzzle = self.get_puzzle(ctx)
        meta_round = RoundJsonDb.get_by_attr(meta_code=meta_code)
        if meta_round:
            await ctx.send(
                f":white_check_mark: This puzzle has been moved to {meta_round.name} and meta sheets are being updated.")
            await self.service.move_to_meta(self.get_guild(ctx), puzzle, meta_round)
        else:
            await ctx.send(":x: Please send a valid meta code")

    @commands.hybrid_command(
        name="remove_from_meta",
        description="Remove the puzzle from a metapuzzle",
        aliases=["remove_from_round"],
    )
    @app_commands.autocomplete(meta_code=meta_code_autocomplete)
    @with_puzzle_mutex(wait=True)
    async def remove_from_meta(self, ctx, meta_code):
        """*Remove the puzzle from a metapuzzle if it has been incorrectly assigned: !remove_from_meta <meta_code>*"""
        if self.get_channel_type(ctx) == "Hunt":
            await ctx.send(":x: This does not appear to be a Puzzle channel")
            return
        puzzle = self.get_puzzle(ctx)
        meta_round = RoundJsonDb.get_by_attr(meta_code=meta_code)
        if not meta_round:
            await ctx.send(":x: Please send a valid meta code")
            return
        if puzzle.id == meta_round.meta_id:
            await ctx.send(":x: You can't remove a metapuzzle from it's own round!")
            return
        if meta_round:
            await ctx.send(
                f":white_check_mark: This puzzle has been removed from {meta_round.name} and meta sheets are being updated.")
            await self.service.remove_from_meta(puzzle, meta_round)
        else:
            await ctx.send(":x: Please send a valid meta code")

    async def _round_impl(self, ctx, round_name, command, make_category = True, make_puzzle = True):

        puzzle_name = self.GENERAL_CHANNEL_NAME
        puzzle = self.get_puzzle(ctx)
        text_channel = ctx.channel

        command_key = command.casefold()

        command_map = {
            "r":("Round",self.send_initial_round_channel_messages, True),
            "round":("Round",self.send_initial_round_channel_messages, True),
            "mp":("Metapuzzle",self.send_initial_metapuzzle_channel_messages, True),
            "metapuzzle":("Metapuzzle",self.send_initial_metapuzzle_channel_messages, True),
            "roundnometa":("Metaless Round",self.send_initial_metaless_round_channel_messages, False),
            "rnm":("Metaless Round",self.send_initial_metaless_round_channel_messages, False),
        }

        group_type, handler, needs_puzzle = command_map[command_key]

        if RoundJsonDb.check_duplicates_in_hunt(round_name, self.get_hunt(ctx)):
            return await ctx.send(f":exclamation: **{group_type} {round_name}** already exists in this hunt or will lead to a duplicate channel name, please use a different name.")

        # If making a puzzle first check that won't cause a clash before making categories

        if make_puzzle:
            puzzle_name = round_name + " - meta" if group_type == "Round" else round_name
            if PuzzleJsonDb.check_duplicates_in_hunt(puzzle_name, self.get_hunt(ctx).id):
                return await ctx.send(f":exclamation: **{group_type} {round_name}** will cause a puzzle name clash within this hunt, please use a different name.")

        if make_category:
            new_category_name = self.clean_name(round_name)
            guild = ctx.guild
            existing_category = discord.utils.get(guild.categories, name=new_category_name)
            if existing_category or self.get_hunt(ctx).parallel_hunt:
                # Try to append the hunt name to the round category name and check if it still exists
                new_category_name = new_category_name + " — " + self.clean_name(self.get_hunt(ctx).name)
                existing_category = discord.utils.get(guild.categories, name=new_category_name)
            if not existing_category:
                logger.info(f"Creating a new channel category for {group_type}: {new_category_name}")
                position = ctx.channel.category.position + 1
                new_category = await ctx.channel.category.clone(name=new_category_name)
                await new_category.edit(position=position)
                if self.SOLVE_CATEGORY is False:
                    await self.get_or_create_channel(
                        guild=guild, category=new_category, channel_name=self.SOLVE_DIVIDER,
                        channel_type="text", reason=self.ROUND_REASON, position=500
                    )
            else:
                return await ctx.send(
                    f":exclamation: Category **{new_category_name}** already present in this server, please name the round differently.")

        else:
            new_category = ctx.channel.category

        new_round = RoundData(
            name = round_name,
            hunt_id = self.get_hunt(ctx).id,
            category_id = new_category.id,
            type = group_type,
        )

        RoundJsonDb.commit(new_round)

        if make_puzzle:
            puzzle = await self.create_metapuzzle(ctx, puzzle_name, self.get_tag_from_category(new_category))
            text_channel, created = await self.create_puzzle_channel(ctx, puzzle, new_category, send_initial_message=False, position=0)
        elif make_category:
            text_channel, created_text = await self.get_or_create_channel(
                guild=self.get_guild(ctx), category=new_category, channel_name=puzzle_name,
                channel_type="text", reason=self.PUZZLE_REASON, position=0
            )

        meta_code = RoundJsonDb.generate_uid('meta_code',6,round_name)
        new_round.meta_code = meta_code
        if puzzle is not None:
            new_round.meta_id = puzzle.id
        RoundJsonDb.commit(new_round)

        kwargs = {
            "hunt_round": new_round,
            "puzzle": puzzle,
        }

        if make_category:
            initial_message = await handler(ctx, text_channel, **kwargs)
            await initial_message.pin()

        return new_round, puzzle

    @commands.command(aliases=["r","mp","metapuzzle", "roundnometa", "rnm"])
    async def round(self, ctx, *, arg):
        """*Create new puzzle round with: !r round-name*
        *Create new metapuzzle category with: !mp metapuzzle-name*
        *Create a new round without a meta with either !roundnometa round-name or !rnm round-name*"""

        if self.get_channel_type(ctx) != "Hunt":
            await ctx.channel.send(":x: This command must be done from the main Hunt channel")
            return

        if await self.check_available_space(ctx,5,1) is False:
            return await ctx.send(":x: There is not enough enough space on the server to create a new round, please inform the moderators.")

        command = ctx.invoked_with

        hunt_round, puzzle = await self._round_impl(ctx, arg, command)

        await ctx.send(
            f":white_check_mark: I've created a new {puzzle.name} category and channel for {self.get_hunt(ctx).name} - {hunt_round.name}"
        )

    @commands.hybrid_command(description="Convert puzzle to metapuzzle", )
    @commands.has_any_role(*ADMIN_ROLES)
    @commands.has_permissions(manage_channels=True)
    async def convert_to_meta(self, ctx):
        if ( puzzle := self.get_puzzle(ctx) ) is None:
            await ctx.channel.send(":x: This doesn't appear to be a puzzle.")
            return
        elif puzzle.is_metapuzzle():
            await ctx.channel.send(":x: This puzzle is already a metapuzzle.")
            return

        # create round without puzzle or category
        hunt_round, puzzle = await self._round_impl(ctx, puzzle.name,"Metapuzzle", False, False)
        self.set_hunt_round(ctx, hunt_round)
        puzzle.add_tag(hunt_round.id)
        puzzle.metapuzzle = 1
        PuzzleJsonDb.commit(puzzle)
        await self.get_gsheet_cog(ctx).convert_puzzle_spreadsheet(puzzle)
        # Channel rename intentionally omitted: the web dashboard's META label
        # supersedes the "🧩" name prefix, and renames hit Discord's strict
        # per-channel rename bucket (2/10min) — toggling convert burned it.
        self.set_channel_type(ctx, "Metapuzzle")
        await self.info(ctx, update=True)
        await ctx.send(
            f":white_check_mark: I've converted this puzzle into a metapuzzle within the category, other puzzles can now be tagged to it using its meta code {hunt_round.meta_code}."
        )

    @commands.hybrid_command(description="Convert puzzle to feeder", )
    @commands.has_any_role(*ADMIN_ROLES)
    @commands.has_permissions(manage_channels=True)
    async def convert_to_feeder(self, ctx):
        if (puzzle := self.get_puzzle(ctx)) is None:
            await ctx.channel.send(":x: This doesn't appear to be a puzzle.")
            return
        elif puzzle.is_metapuzzle() is False:
            await ctx.channel.send(":x: This puzzle is already a feeder.")
            return

        meta_round = RoundJsonDb.get_by_attr(meta_id=puzzle.id)
        if meta_round is not None:
            RoundJsonDb.delete(meta_round.id)
            if meta_round.id in puzzle.tags:
                puzzle.tags.remove(meta_round.id)
        puzzle.metapuzzle = False
        PuzzleJsonDb.commit(puzzle)
        await self.get_gsheet_cog(ctx).convert_puzzle_spreadsheet(puzzle)
        # Channel rename intentionally omitted (see convert_to_meta): the dashboard
        # label supersedes the name prefix and renames hit the strict rename bucket.
        self.set_channel_type(ctx, "Puzzle")
        await self.info(ctx, update=True)
        await ctx.send(
            f":white_check_mark: I've converted this puzzle into a feeder, other puzzles can no longer be tagged to it."
        )

    def get_settings(self, ctx):
        data = None
        if self.get_channel_type(ctx) == "Puzzle":
            data = self.get_puzzle(ctx)
        elif self.get_channel_type(ctx) in self.PUZZLE_GROUPS:
            data = self.get_hunt_round(ctx)
        elif self.get_channel_type(ctx) == "Hunt":
            data = self.get_hunt(ctx)
        else:
            data = self.get_guild_data(ctx)
        return data

    def save_settings(self, ctx, settings):
        if self.get_channel_type(ctx) == "Puzzle":
            PuzzleJsonDb.commit(settings)
        elif self.get_channel_type(ctx) in self.PUZZLE_GROUPS:
            RoundJsonDb.commit(settings)
        elif self.get_channel_type(ctx) == "Hunt":
            HuntJsonDb.commit(settings)
        else:
            GuildSettingsDb.commit(settings)

    @commands.hybrid_command(description="Show channel settings (admin only)",)
    @commands.has_any_role(*ADMIN_ROLES)
    @commands.has_permissions(manage_channels=True)
    async def show_settings(self, ctx):
        """*(admin) Show channel settings for debug*"""
        await ctx.send(f"```json\n{self.get_settings(ctx).to_json(indent=2)}```")

    @commands.hybrid_command(description="Show puzzle settings (admin only)",)
    @commands.has_any_role(*ADMIN_ROLES)
    @commands.has_permissions(manage_channels=True)
    async def show_puzzle_settings(self, ctx):
        """*(admin) Show channel puzzle settings for debug*"""
        if self.get_puzzle(ctx):
            settings = []
            for attr, value in self.get_puzzle(ctx).__dict__.items():
                settings.append(f"{attr} = {value}")

            embeds = build_note_embeds(
                message="",
                notes=settings,
                title="Puzzle Settings",
                note_embed=False,
            )

            # Hybrid-friendly sending:
            # If invoked as slash, defer to avoid "interaction failed" on slow builds.
            if ctx.interaction:
                await ctx.defer()

            for e in embeds:
                await ctx.send(embed=e)

        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

    @commands.command()
    @commands.has_any_role(*ADMIN_ROLES)
    async def set_login(self, ctx, *, arg):
        """*Set username and password for hunt*
        Format username:password"""
        if self.get_channel_type(ctx) == "Guild":
            await ctx.send(":x: This must be sent in a channel related to a hunt")
            return

        if ":" in arg:
            username, password = arg.split(":", 1)
            self.get_hunt(ctx).username = username
            self.get_hunt(ctx).password = password
            return await ctx.send(
                f":white_check_mark: I've updated the login details for hunt {self.get_hunt(ctx).name}"
            )

        await ctx.send(f"Unable to parse details {arg}, try using `!set_login username:password`")

    @commands.command()
    @commands.has_any_role(*ADMIN_ROLES)
    async def set_start(self,ctx,timestamp:int):
        """*Set start time for hunt, only works with a UNIX timestamp*"""
        if self.get_channel_type(ctx) == "Guild":
            await ctx.send(":x: This must be sent in a channel related to a hunt")
            return

        self.get_hunt(ctx).start_timestamp = timestamp
        return await ctx.send(
            f":white_check_mark: I've updated the start time for hunt {self.get_hunt(ctx).name}"
        )

    @commands.hybrid_command(aliases=["ph"])
    @commands.has_any_role(*ADMIN_ROLES)
    async def parallel_hunt(self, ctx):
        """*Mark that hunts are being done in parallel - more than one team doing at the same time
        Acts as a toggle."""
        if self.get_channel_type(ctx) != "Hunt":
            await ctx.send(":x: This must be sent in the main hunt channel")
            return

        self.get_hunt(ctx).parallel_hunt = not self.get_hunt(ctx).parallel_hunt

        if self.get_hunt(ctx).parallel_hunt:
            return await ctx.send(
                f":white_check_mark: I've marked {self.get_hunt(ctx).name} as being run in parallel"
            )
        else:
            return await ctx.send(
                f":white_check_mark: I've marked {self.get_hunt(ctx).name} as not being run in parallel"
            )

    @commands.command(aliases=["update_setting","update_puzzle_setting","update_puzzle_settings"])
    @commands.has_any_role(*ADMIN_ROLES)
    @commands.has_permissions(manage_channels=True)
    async def update_settings(self, ctx, setting_key: str, setting_value: str):
        """*(admin) Update channel/hunt/guild settings: !update_settings key value - can tolerate spaces in value with inverted commas*"""
        command = ctx.invoked_with
        if command not in ("update_puzzle_setting", "update_puzzle_settings"):
            settings = self.get_settings(ctx)
        elif self.get_puzzle(ctx):
            settings = self.get_puzzle(ctx)
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")
            return
        if hasattr(settings, setting_key):
            old_value = getattr(settings, setting_key)
            value: Any
            if type(old_value) == str:
                value = setting_value
                setattr(settings, setting_key, setting_value)
            elif type(old_value) == int:
                try:
                    value = int(setting_value)
                except ValueError:
                    await ctx.send(f":x: Cannot set `{setting_key}={setting_value}`, needs integer input.")
                    return
            elif type(old_value) == bool:
                if setting_value.strip().lower() in ('false', '0'):
                    value = False
                elif setting_value.strip().lower() in ('true', '1'):
                    value = True
                else:
                    await ctx.send(f":x: Cannot set `{setting_key}={setting_value}`, needs boolean input (0, 1, true, false).")
                    return
            else:
                await ctx.send(f":x: `{setting_key}` is type `{type(old_value).__name__}` and cannot be set from this command.")
                return

            setattr(settings, setting_key, value)
            if command not in ("update_puzzle_setting", "update_puzzle_settings"):
                self.save_settings(ctx, settings)
            else:
                PuzzleJsonDb.commit(settings)
            await ctx.send(f":white_check_mark: Updated `{setting_key}={value}` from old value: `{old_value}`")
        else:
            await ctx.send(f":exclamation: Unrecognized setting key: `{setting_key}`. Use `!show_settings` for more info.")


    @commands.hybrid_command(description="List puzzles",aliases=["list"])
    async def list_puzzles(self, ctx):
        """*List puzzles in the current Round if invoked in Puzzle or Round channels, or in the entire Hunt if in the Hunt channel*"""
        all_puzzles = {}
        embed_title = ""
        if self.get_channel_type(ctx) in ["Round","Puzzle","Metaless Round","Metapuzzle"]:
            round_puzzles = PuzzleJsonDb.get_all_from_round(self.get_hunt_round(ctx).id)
            all_puzzles[self.get_hunt_round(ctx).id] = {
                        'name': self.get_hunt_round(ctx).name,
                        'puzzles': round_puzzles
                    }
            embed_title = f"Puzzles in Round {self.get_hunt_round(ctx).name}"
        else:
            await ctx.send(":x: Please got to https://quarantinedecrypters.com for an overview of all puzzles")

        embed = discord.Embed(title=embed_title, colour=discord.Colour.blurple())

        for round_id in all_puzzles:
            round_name = all_puzzles[round_id]['name']
            puzzle_count = 1
            round_part = 1
            message = ""
            # Create a message with a new embed field per round,
            # listing all puzzles in the embed field
            for puzzle in all_puzzles[round_id]['puzzles']:
                if puzzle_count > 15:
                    # Too many puzzles, split the embed
                    round_name_part = f"{round_name} - part {round_part}"
                    embed.add_field(name=round_name_part, value=message, inline=False)
                    message = ""
                    round_part += 1
                    puzzle_count = 1
                message += f"{puzzle.name} - {puzzle.channel_mention}"
                if puzzle.puzzle_type:
                    message += f" type:{puzzle.puzzle_type}"
                if puzzle.solution:
                    message += f" sol:**{puzzle.solution}**"
                elif puzzle.status:
                    message += f" status:{puzzle.status}"
                message += "\n"
                puzzle_count += 1

            # add last round
            if message:
                if round_part > 1:
                    round_name = f"{round_name} - part {round_part}"
                embed.add_field(name=round_name, value=message, inline=False)

        if embed.fields:
            await ctx.send(embed=embed)

    async def get_or_create_channel(
        self, guild, category: discord.CategoryChannel, channel_name: str, channel_type, **kwargs
    ):
        """Retrieve given channel by name/category or create one"""
        if channel_type == "text":
            channel_type = discord.ChannelType.text
        elif channel_type == "voice":
            channel_type = discord.ChannelType.voice
        if not (channel_type is discord.ChannelType.text or channel_type is discord.ChannelType.voice):
            raise ValueError(f"Unrecognized channel_type: {channel_type}")
        channel = discord.utils.get(guild.channels, category=category, type=channel_type, name=channel_name)
        created = False
        if not channel:
            message = f"Creating a new channel: {channel_name} of type {channel_type} for category: {category}"
            logger.info(message)
            create_method = (
                guild.create_text_channel if channel_type is discord.ChannelType.text else guild.create_voice_channel
            )
            channel = await create_method(channel_name, category=category, **kwargs)
            created = True

        return (channel, created)


    async def send_initial_hunt_channel_messages(self, ctx, channel: discord.TextChannel, **kwargs):
        hunt = kwargs.get('hunt',self.get_hunt(ctx))
        embed = discord.Embed(
            description=f"""Welcome to the general channel for {hunt.name}!"""
        )
        embed.add_field(
            name="Overview",
            value="This channel "
            " has info about the hunt itself and it is where you will create new round channels",
            inline=False,
        )
        puzzle_links = (f"""The following are some useful links and information:
                        • [Hunt Website]({hunt.url})
                        • Hunt Start Time: <t:{hunt.start_timestamp}:f>
                        • [Google Sheet](https://docs.google.com/spreadsheets/d/{hunt.google_sheet_id})
                        """)
        if hunt.archive_google_sheet_id:
            puzzle_links += f"""• [Solved Google Sheet Archive](https://docs.google.com/spreadsheets/d/{hunt.archive_google_sheet_id})
            """
        if hunt.username:
            puzzle_links += f"""• Hunt Username: {hunt.username}
            """
        if hunt.password:
            puzzle_links += f"""• Hunt Password: {hunt.password}
            """
        puzzle_links += f"""• [Overview Website]({self._overview_url(ctx, hunt)})"""
        embed.add_field(
            name="Hunt Information",
            value=puzzle_links,
            inline=False,
        )
        embed.add_field(
                    name="Useful commands",
                    value=f"""The following are some useful commands:
        • `!r <round name>` : Create a new round with a metapuzzle channel
        • `!mp <metapuzzle name>` : Create a new metapuzzle
        • `!rnm <round name>` : Create a round without a metapuzzle channel
        • `!info` : Repeat this message
        • `!set_login username:password` : Set the login details for the hunt if shared
        """,
                    inline=False,
                )
        if kwargs.get("update", False):
            channel_pins = await channel.pins()
            return await channel_pins[-1].edit(embed=embed)
        else:
            return await channel.send(embed=embed)

    def _add_puzzle_links_field(self, ctx, embed, puzzle, hunt):
        spreadsheet_url = urls.spreadsheet_url(self.get_puzzle_sheet(ctx, puzzle),
                                               puzzle.google_page_id) if self.get_hunt(ctx).google_sheet_id else "?"
        puzzle_links = (f"""    • [Puzzle link]({puzzle.url or "?"})
                                • [Google Sheet]({spreadsheet_url})
                                """)
        puzzle_links += f"""• [Overview Website]({self._overview_url(ctx, hunt)})"""
        embed.add_field(
            name="Puzzle Links",
            value=puzzle_links,
            inline=False,
        )

    @staticmethod
    def _add_metapuzzle_command_fields(embed):
        """Add the shared Metapuzzle/Puzzle command-help fields to an embed."""
        embed.add_field(
                        name="Metapuzzle commands",
                        value="""
        • `!s SOLUTION` will mark this puzzle as solved and archive this channel
        • `!ps SOLUTION` will mark this puzzle as partially solved, multiple answers will be seperated by slashes, mark as completed with !s SOLUTION with the final solution or just !s if all answers entered 
        • `!mark_as_complete` will mark the puzzle solved with a tick (for interactions or puzzles without a traditional answer)
        • `!add_sheet <name>` will add an additional working sheet to the puzzle, for sub-puzzles or just tidier work (name is optional)
        """,
                        inline=False,
                    )
        embed.add_field(
            name="Puzzle commands",
            value="""
                • `!link <url>` will update the link to the puzzle on the hunt website
                • `!type <puzzle type>` will mark the type of the puzzle
                • `!priority <priority>` will mark the priority of the puzzle
                • `!status <status>` will update the status of the puzzle
                • `!note <note>` can be used to leave a note about ideas/progress
                • `!notes` can be used to see the notes that have been left
                • `!erase_note <note number>` can be used to erase the specified note
                """,
            inline=False,
        )

    def _build_embed_list(self, ctx, embed, puzzle):
        """Wrap the main embed plus an optional additional-sheets embed in a list."""
        embed_list = [embed]
        if len(puzzle.additional_sheets) > 0:
            sheets_embed = discord.Embed()
            sheets_embed.add_field(
                name="Additional Sheet Links",
                value=self.create_additional_sheet_list(ctx, puzzle),
                inline=False,
            )
            embed_list.append(sheets_embed)
        return embed_list

    @staticmethod
    def _add_puzzle_status_fields(embed, puzzle):
        """Add Status/Solution/Type/Priority fields for a puzzle to an embed."""
        try:
            embed.add_field(name="Status", value=puzzle.status or "?", inline=False,)
            if puzzle.solution:
                embed.add_field(name="Solution", value=puzzle.solution, inline=False,)
            embed.add_field(name="Type", value=puzzle.puzzle_type or "?", inline=False,)
            embed.add_field(name="Priority", value=puzzle.priority or "?", inline=False,)
        except Exception:
            logger.exception("Failed to add puzzle status fields to embed")

    async def send_initial_metapuzzle_channel_messages(self, ctx, channel: discord.TextChannel, **kwargs) -> discord.Message:
        """Send intro message on a meta channel"""
        hunt_round = kwargs.get('hunt_round', self.get_hunt_round(ctx))
        hunt = kwargs.get('hunt', self.get_hunt(ctx))
        puzzle = kwargs.get('puzzle', self.get_puzzle(ctx))
        embed = discord.Embed(
            description=f"""Welcome to the meta channel for {hunt_round.name}!"""
        )
        embed.add_field(
            name="Overview",
            value="This channel is for general discussion of the round and working on the meta, please note the first two column of the sheet are reserved for the puzzle titles and answers and will be overwritten by the bot!",
            inline=False,
        )
        embed.add_field(
                        name="General Commands",
                        value=f"""The following may be useful discord commands:
        • `!p <puzzle-name>` will add a puzzle to this meta.
        • `!list` : List all puzzles in this meta.
        • `!info` will re-post this message.
        • `!move_to_meta {hunt_round.meta_code}` when used in another puzzle channel will add it to this meta and move it to this category.
        • `!add_to_meta {hunt_round.meta_code}` when used in another puzzle channel will add it to this meta but not move it (e.g. if a puzzle feeds multiple metas).
        • `!remove_from_meta {hunt_round.meta_code}` when used in another puzzle channel will remove it from this meta.
        • `!sync_meta` (admin_only) when used in this channel will assign all the puzzles in the category to the meta.
        • `!rename_meta <puzzle-name>` (admin only) will rename the metapuzzle.
        """,
                        inline=False,
                    )

        self._add_metapuzzle_command_fields(embed)
        self._add_puzzle_links_field(ctx, embed, puzzle, hunt)
        self._add_puzzle_status_fields(embed, puzzle)
        embed_list = self._build_embed_list(ctx, embed, puzzle)

        if kwargs.get("update", False):
            channel_pins = await channel.pins()
            return await channel_pins[-1].edit(embeds=embed_list)
        else:
            return await channel.send(embeds=embed_list)

    async def send_initial_metaless_round_channel_messages(self, ctx, channel: discord.TextChannel, **kwargs) -> discord.Message:
        """Send intro message on a metaless round channel"""
        hunt_round = kwargs.get('hunt_round', self.get_hunt_round(ctx))
        hunt = kwargs.get('hunt', self.get_hunt(ctx))
        embed = discord.Embed(
            description=f"""Welcome to the round channel for {hunt_round.name}!"""
        )
        embed.add_field(
            name="Overview",
            value="This channel is for general discussion of the round.",
            inline=False,
        )
        embed.add_field(
                        name="General Commands",
                        value=f"""The following may be useful discord commands:
        • `!p <puzzle-name>` will add a puzzle to this round.
        • `!list` : List all puzzles in this round.
        • `!info` will re-post this message.
        • `!move_to_round {hunt_round.meta_code}` when used in another puzzle channel will add it to this round and move it to this category.
        • `!add_to_round {hunt_round.meta_code}` when used in another puzzle channel will add it to this round but not move it (e.g. if a puzzle feeds multiple rounds).
        • `!remove_from_round {hunt_round.meta_code}` when used in another puzzle channel will remove it from this round.
        • `!sync_round` (admin only) when used in this channel will assign all the puzzles in the category to the round.
        """,
                        inline=False,
                    )
        embed.add_field(name="Overview Website", value=self._overview_url(ctx, hunt),
                        inline=False)
        if kwargs.get("update", False):
            channel_pins = await channel.pins()
            return await channel_pins[-1].edit(embed=embed)
        else:
            return await channel.send(embed=embed)

    async def send_initial_round_channel_messages(self, ctx, channel: discord.TextChannel, **kwargs) -> discord.Message:
        """Send intro message on a round channel"""
        hunt_round = kwargs.get('hunt_round', self.get_hunt_round(ctx))
        hunt = kwargs.get('hunt', self.get_hunt(ctx))
        puzzle = kwargs.get('puzzle', self.get_puzzle(ctx))
        embed = discord.Embed(
            description=f"""Welcome to the round channel for {hunt_round.name}!"""
        )
        embed.add_field(
            name="Overview",
            value="This channel is for general discussion of the round and working on the meta, please note the first two column of the sheet are reserved for the puzzle titles and answers and will be overwritten by the bot!",
            inline=False,
        )
        embed.add_field(
                        name="General Commands",
                        value=f"""The following may be useful discord commands:
        • `!p <puzzle-name>` will add a puzzle to this round.
        • `!list` : List all puzzles in this round.
        • `!info` will re-post this message.
        • `!move_to_round {hunt_round.meta_code}` when used in another puzzle channel will add it to this round and move it to this category.
        • `!add_to_round {hunt_round.meta_code}` when used in another puzzle channel will add it to this round but not move it (e.g. if a puzzle feeds multiple rounds).
        • `!remove_from_round {hunt_round.meta_code}` when used in another puzzle channel will remove it from this round.
        • `!sync_round` (admin only) when used in this channel will assign all the puzzles in the category to the round.
        • `!rename_meta <puzzle-name>` (admin only) will rename the metapuzzle.
        """,
                        inline=False,
                    )

        self._add_metapuzzle_command_fields(embed)
        self._add_puzzle_links_field(ctx, embed, puzzle, hunt)
        self._add_puzzle_status_fields(embed, puzzle)

        embed_list = self._build_embed_list(ctx, embed, puzzle)

        if kwargs.get("update", False):
            channel_pins = await channel.pins()
            return await channel_pins[-1].edit(embeds=embed_list)
        else:
            return await channel.send(embeds=embed_list)

    async def send_initial_puzzle_channel_messages(self, ctx, channel: discord.TextChannel, **kwargs) -> discord.Message:
        """Send intro message on a puzzle channel"""
        puzzle = kwargs.get("puzzle", self.get_puzzle(ctx))
        hunt = kwargs.get('hunt', self.get_hunt(ctx))
        embed = discord.Embed(
            description=f"""Welcome to the puzzle channel for {puzzle.name} in Test!"""
        )
        embed.add_field(
            name="Overview",
            value="This channel is a good place to discuss how to tackle this puzzle. Usually you'll want to do most of the puzzle work itself on Google Sheets / Docs.",
            inline=False,
        )
        embed.add_field(
                        name="Commands",
                        value="""
                • `!p <puzzle-name>` will add a puzzle to this round/meta puzzle.
                • `!s SOLUTION` will mark this puzzle as solved and archive this channel
                • `!ps SOLUTION` will mark this puzzle as partially solved, multiple answers will be seperated by slashes, mark as completed with !s SOLUTION with the final solution or just !s if all answers entered 
                • `!mark_as_complete` will mark the puzzle solved with a tick (for interactions or puzzles without a traditional answer)
                • `!add_sheet <name>` will add an additional working sheet to the puzzle, for sub-puzzles or just tidier work (name is optional)
                """,
                        inline=False,
                    )
        embed.add_field(
            name="Puzzle commands",
            value="""
                • `!link <url>` will update the link to the puzzle on the hunt website
                • `!info` will re-post this message
                • `!type <puzzle type>` will mark the type of the puzzle
                • `!priority <priority>` will mark the priority of the puzzle
                • `!status <status>` will update the status of the puzzle
                • `!note <note>` can be used to leave a note about ideas/progress
                • `!notes` can be used to see the notes that have been left
                • `!erase_note <note number>` can be used to erase the specified note
                • `!rename_puzzle <puzzle-name>` (admin only) will rename the puzzle.
                """,
            inline=False,
        )
        self._add_puzzle_links_field(ctx, embed, puzzle, hunt)
        self._add_puzzle_status_fields(embed, puzzle)

        embed_list = self._build_embed_list(ctx, embed, puzzle)

        if kwargs.get("update",False):
            channel_pins = await channel.pins()
            if channel_pins:
                return await channel_pins[-1].edit(embeds=embed_list)
        else:
            return await channel.send(embeds=embed_list)

    async def send_not_puzzle_channel(self, ctx):
        # TODO: Fix this
        if ctx.channel and ctx.channel.category.name == self.get_solved_puzzle_category(""):
            await ctx.send(":x: This puzzle appears to already be solved")
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

    def get_solved_puzzle_category(self, hunt_name):
        return f"{hunt_name}-{self.SOLVED_PUZZLES_CATEGORY}"

    @commands.command()
    async def info(self, ctx, **kwargs):
        """*Show discord command help for a puzzle channel*"""
        update = kwargs.get("update", False)
        if self.get_channel_type(ctx) == "Hunt":
            await self.send_initial_hunt_channel_messages(ctx, ctx.channel, update=update)
        elif self.get_channel_type(ctx) == "Round":
            await self.send_initial_round_channel_messages(ctx, ctx.channel, update=update)
        elif self.get_channel_type(ctx) == "Metapuzzle":
            await self.send_initial_metapuzzle_channel_messages(ctx, ctx.channel, update=update)
        elif self.get_channel_type(ctx) == "Metaless Round":
            await self.send_initial_metaless_round_channel_messages(ctx, ctx.channel, update=update)
        elif self.get_channel_type(ctx) == "Puzzle":
            await self.send_initial_puzzle_channel_messages(ctx, ctx.channel, update=update)
        else:
            await ctx.send(":x: This does not appear to be a hunt channel")

    async def send_state(self, ctx, channel: discord.TextChannel, puzzle_data: PuzzleData, description=None):
        """Send simple embed showing relevant links"""
        embed = discord.Embed(description=description)
        embed.add_field(name="Hunt URL", value=puzzle_data.url or "?")
        spreadsheet_url = urls.spreadsheet_url(self.get_hunt(ctx).google_sheet_id, puzzle_data.google_page_id) if self.get_hunt(ctx).google_sheet_id else "?"
        embed.add_field(name="Google Drive", value=spreadsheet_url)
        embed.add_field(name="Status", value=puzzle_data.status or "?")
        embed.add_field(name="Type", value=puzzle_data.puzzle_type or "?")
        embed.add_field(name="Priority", value=puzzle_data.priority or "?")
        await ctx.send(embed=embed)
        await self.info(ctx, update=True)

    @commands.command(aliases=["rename_meta"])
    @commands.has_any_role(*ORGANISER_ROLES)
    @with_puzzle_mutex(wait=True)
    async def rename_puzzle(self, ctx, *, puzzle_name: str):
        """Rename a puzzle"""
        if self.get_puzzle(ctx):
            if PuzzleJsonDb.check_duplicates_in_hunt(puzzle_name, self.get_hunt(ctx).id):
                return await ctx.send(
                    f":exclamation: Puzzle **{puzzle_name}** already exists in this hunt or will lead to a duplicate channel name, please use a different name")
            await self.safe_rename(ctx.channel, self.clean_name(puzzle_name), ctx)
            self.get_puzzle(ctx).name = puzzle_name
            self.get_puzzle(ctx).channel_name = self.clean_name(puzzle_name)
            PuzzleJsonDb.commit(self.get_puzzle(ctx))
            if self.get_gsheet_cog(ctx) is not None:
                # update google sheet ID
                await self.get_gsheet_cog(ctx).update_puzzle(self.get_puzzle(ctx), True)
                if self.get_hunt_round(ctx):
                    try:
                        await self.update_metapuzzle(ctx, self.get_hunt_round(ctx))
                    except SheetRefreshError as exc:
                        await self._warn_stale_sheets(ctx, exc)
            await ctx.channel.send(":white_check_mark: I've updated the puzzle and channel names")
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

    async def _update_puzzle_field(self, ctx, field, value):
        """Set a puzzle attribute and re-render the channel state, or warn if not a puzzle channel."""
        puzzle = self.get_puzzle(ctx)
        if puzzle:
            setattr(puzzle, field, value)
            await self.send_state(
                ctx, ctx.channel, puzzle, description=":white_check_mark: I've updated:" if value else None
            )
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

    @commands.command()
    @with_puzzle_mutex(wait=True)
    async def link(self, ctx, *, url: str):
        """*Update link to puzzle*"""
        if self.get_puzzle(ctx):
            self.get_puzzle(ctx).url = url
            if self.get_gsheet_cog(ctx) is not None:
                # update google sheet ID
                await self.get_gsheet_cog(ctx).update_puzzle(self.get_puzzle(ctx))
            await self.send_state(
                ctx, ctx.channel, self.get_puzzle(ctx), description=":white_check_mark: I've updated:" if url else None
            )
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

    @commands.hybrid_command(description="Update puzzle status")
    @app_commands.choices(status=[
        app_commands.Choice(name="Unstarted", value="Unstarted"),
        app_commands.Choice(name="In Progress", value="In Progress"),
        app_commands.Choice(name="Stuck", value="Stuck"),
        app_commands.Choice(name="Needs Extraction", value="Needs Extraction"),
        app_commands.Choice(name="Solved", value="Solved"),
        app_commands.Choice(name="Backsolved", value="Backsolved"),
    ])
    @with_puzzle_mutex(wait=True)
    async def status(self, ctx, *, status: str):
        """*Update puzzle status, one of "unstarted", "in progress", "stuck", "needs extraction", "solved", "backsolved"*"""
        if status is not None and status.lower() not in self.STATUSES:
            await ctx.send(f":exclamation: Status should be one of {self.STATUSES}, got \"{status}\"")
            return

        await self._update_puzzle_field(ctx, "status", status)

    @commands.command()
    @with_puzzle_mutex(wait=True)
    async def type(self, ctx, *, puzzle_type: str):
        """*Update puzzle type, e.g. "crossword"*"""
        await self._update_puzzle_field(ctx, "puzzle_type", puzzle_type)

    @commands.hybrid_command(description="Update puzzle priority")
    @app_commands.choices(priority=[
        app_commands.Choice(name="Low", value="low"),
        app_commands.Choice(name="Medium", value="medium"),
        app_commands.Choice(name="High", value="high"),
        app_commands.Choice(name="Very High", value="very high"),
    ])
    @with_puzzle_mutex(wait=True)
    async def priority(self, ctx, *, priority: str):
        """*Update puzzle priority, one of "low", "medium", "high", "very high"*"""
        if priority is not None and priority.lower() not in self.PRIORITIES:
            await ctx.send(f":exclamation: Priority should be one of {self.PRIORITIES}, got \"{priority}\"")
            return

        await self._update_puzzle_field(ctx, "priority", priority)

    @commands.command(aliases=["notes"])
    @with_puzzle_mutex(wait=True)
    async def note(self, ctx, *, note: Optional[str]):
        """*Show or add a note about the puzzle*"""
        if self.get_puzzle(ctx) is None:
            await self.send_not_puzzle_channel(ctx)
            return

        message = "Showing notes left by users!"
        if note:
            self.get_puzzle(ctx).notes.append(f"{note} - {ctx.message.jump_url}")
            PuzzleJsonDb.commit(self.get_puzzle(ctx))
            message = (
                f"Added a new note! Use `!erase_note {len(self.get_puzzle(ctx).notes)}` to remove the note if needed. "
                f"Check `!notes` for the current list of notes."
            )

        if self.get_puzzle(ctx).notes:
            puzzle = self.get_puzzle(ctx)

            embeds = build_note_embeds(
                message=message,
                notes = puzzle.notes,
                title="Puzzle Notes",
            )

            # Hybrid-friendly sending:
            # If invoked as slash, defer to avoid "interaction failed" on slow builds.
            if ctx.interaction:
                await ctx.defer()

            for e in embeds:
                await ctx.send(embed=e)

        else:
            embed = discord.Embed(description="No notes left yet, use `!note my note here` to leave a note")
            await ctx.send(embed=embed)

    @commands.command(aliases=["delete_note"])
    @with_puzzle_mutex(wait=True)
    async def erase_note(self, ctx, note_index: int):
        """*Remove a note by index*"""

        if self.get_puzzle(ctx) is None:
            await self.send_not_puzzle_channel(ctx)
            return

        if 1 <= note_index <= len(self.get_puzzle(ctx).notes):
            del self.get_puzzle(ctx).notes[note_index - 1]
            description = f"Erased note {note_index}"
        else:
            description = f"Unable to find note {note_index}"

        puzzle = self.get_puzzle(ctx)

        embeds = build_note_embeds(
            message=description,
            notes=puzzle.notes,
            title="Puzzle Notes",
        )

        # Hybrid-friendly sending:
        # If invoked as slash, defer to avoid "interaction failed" on slow builds.
        if ctx.interaction:
            await ctx.defer()

        for e in embeds:
            await ctx.send(embed=e)

    @staticmethod
    def _normalize_solution(args):
        return " ".join(args).strip().upper()

    async def _update_all_metas(self, ctx, puzzle):
        """Refresh every metapuzzle this puzzle is tagged to, on the SAME isolation
        path as solve/retag: each meta is attempted and badged independently via
        service.refresh_metas, and a SheetRefreshError is raised if any failed so the
        bang edge can warn (via _warn_stale_sheets) instead of aborting mid-loop."""
        hunt = self.get_hunt(ctx)
        rounds = [RoundJsonDb.get_by_attr(id=tag) for tag in puzzle.tags]
        failed = await self.service.refresh_metas(hunt, rounds)
        if failed:
            raise SheetRefreshError(failed)

    async def _warn_stale_sheets(self, ctx, exc):
        """Uniform surfacing of a SheetRefreshError from a bang edge: the mutation is
        saved and the affected meta sheet(s) are badged on the dashboard."""
        await ctx.send(f":warning: {exc}")

    def _overview_url(self, ctx, hunt):
        """Overview website URL for a hunt (falls back to the numeric id)."""
        return f"{self.get_guild_data(ctx).website_url}?uid={hunt.uid or hunt.id}"

    async def _partial_impl(self, ctx, *args):
        solution = self._normalize_solution(args)
        self.get_puzzle(ctx).status = "Partially Solved"

        if self.get_puzzle(ctx).solution:
            self.get_puzzle(ctx).solution += "/" + solution
        else:
            self.get_puzzle(ctx).solution = solution

        PuzzleJsonDb.commit(self.get_puzzle(ctx))

        sheet_warning = None
        try:
            await self._update_all_metas(ctx, self.get_puzzle(ctx))
        except SheetRefreshError as exc:
            sheet_warning = exc

        emoji = self.get_guild_data(ctx).discord_bot_emoji
        embed = discord.Embed(title="Partially SOLVED!",
                              description=f"{emoji} :partying_face: Great work! Added the solution `{solution}`")
        embed.add_field(
            name="Follow-up",
            value="If you need to clear the partial solutions then use !unsolve and they will all be wiped. "
        )
        await ctx.send(embed=embed)
        await self.send_initial_puzzle_channel_messages(ctx, ctx.channel, update=True)
        if sheet_warning:
            await self._warn_stale_sheets(ctx, sheet_warning)

    @commands.command(aliases=["ps"])
    @with_puzzle_mutex(wait=False)
    async def partial(self, ctx, *args):
        """*Add a partial solution to the puzzle, will append the answer along with a slash if multiple answers present: !ps PARTIAL SOLUTION*"""

        if not self.get_puzzle(ctx):
            await self.send_not_puzzle_channel(ctx)
            return
        elif self.get_puzzle(ctx).solved:
            await ctx.send(":x: This puzzle appears to already be solved")
            return

        await self._partial_impl(ctx, *args)


    async def _solve_impl(self, ctx, *args):
        solution = self._normalize_solution(args)
        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)

        sheet_warning = None
        try:
            applied = await self.service.solve(self.get_guild(ctx), hunt, puzzle, solution)
        except SheetRefreshError as exc:
            # The solve is fully applied; one or more meta sheets are stale (and
            # badged on the dashboard). Tell the channel rather than tracebacking.
            applied = True
            sheet_warning = exc
        if not applied:
            await ctx.send(":x: Nice try, but you need to give a solution!")
            return

        emoji = self.get_guild_data(ctx).discord_bot_emoji
        embed = discord.Embed(title="PUZZLE SOLVED!",
                              description=f"{emoji} :partying_face: Great work! Marked the solution as `{puzzle.solution}`")
        embed.add_field(
            name="Follow-up",
            value="If the solution was entered incorrectly, please use `!update_solution` to update it, if the puzzle isn't actually solved at all then use `!unsolve`.  \nGive me a sec to tidy up the sheets."
        )
        await ctx.send(embed=embed)
        await self.move_to_solved(ctx)
        await self.info(ctx, update=True)
        if sheet_warning:
            await self._warn_stale_sheets(ctx, sheet_warning)
        else:
            await ctx.send(":white_check_mark: Sheets all tidied away.")

    @commands.command(aliases=["s"])
    @with_puzzle_mutex(wait=False)
    async def solve(self, ctx, *args):
        """*Mark puzzle as fully solved and update the sheet with the solution: !s SOLUTION*"""

        if not self.get_puzzle(ctx):
            await self.send_not_puzzle_channel(ctx)
            return
        elif self.get_puzzle(ctx).solved:
            await ctx.send(":x: This puzzle appears to already be solved")
            return
        await self._solve_impl(ctx, *args)


    @commands.command(aliases=["update_solution","update_answer"])
    @with_puzzle_mutex(wait=False)
    async def change_solution(self, ctx, *args):
        """*Change a previously marked solution, if not solved then solve as usual: !change_solution SOLUTION*"""
        if not self.get_puzzle(ctx):
            await self.send_not_puzzle_channel(ctx)
            return
        elif self.get_puzzle(ctx).solved == 0:
            await self._solve_impl(ctx, *args)
            return

        solution = self._normalize_solution(args)


        puzzle = self.get_puzzle(ctx)

        if solution == "":
            await ctx.send(":x: Nice try, but you need to give a solution!")
            return

        puzzle.solution = solution
        puzzle.solve_time = datetime.datetime.now(tz=pytz.UTC)

        PuzzleJsonDb.commit(puzzle)


        sheet_warning = None
        try:
            await self._update_all_metas(ctx, puzzle)
        except SheetRefreshError as exc:
            sheet_warning = exc

        emoji = self.get_guild_data(ctx).discord_bot_emoji
        embed = discord.Embed(title="PUZZLE SOLUTION UPDATED!",
                              description=f"{emoji} :partying_face: Great work! I've updated the solution to `{puzzle.solution}`")
        embed.add_field(
            name="Follow-up",
            value="If the solution was entered incorrectly, please use `!update_solution` to update it, if the puzzle isn't actually solved at all then use `!unsolve`. "
        )
        await ctx.send(embed=embed)
        await self.get_gsheet_cog(ctx).update_solution(puzzle)
        await self.info(ctx, update=True)
        if sheet_warning:
            await self._warn_stale_sheets(ctx, sheet_warning)

    @commands.command(aliases=["add_to_solution"])
    @with_puzzle_mutex(wait=False)
    async def add_solution(self, ctx, *args):
        """*Add a solution to a previously solved puzzle, for instance if an additional answer is found, if not solved then add a partial solution: !add_solution SOLUTION*"""
        if not self.get_puzzle(ctx):
            await self.send_not_puzzle_channel(ctx)
            return
        elif self.get_puzzle(ctx).solved == 0:
            await self._partial_impl(ctx, *args)
            return

        solution = self._normalize_solution(args)


        puzzle = self.get_puzzle(ctx)

        if solution == "":
            await ctx.send(":x: Nice try, but you need to give a solution!")
            return

        puzzle.solution += "/" + solution
        puzzle.solve_time = datetime.datetime.now(tz=pytz.UTC)

        PuzzleJsonDb.commit(puzzle)


        sheet_warning = None
        try:
            await self._update_all_metas(ctx, puzzle)
        except SheetRefreshError as exc:
            sheet_warning = exc

        emoji = self.get_guild_data(ctx).discord_bot_emoji
        embed = discord.Embed(title="EXTRA PUZZLE SOLUTION ADDED!",
                              description=f"{emoji} :partying_face: Great work! I've updated the solution to `{puzzle.solution}`")
        await ctx.send(embed=embed)
        await self.get_gsheet_cog(ctx).update_solution(puzzle)
        await self.info(ctx, update=True)
        if sheet_warning:
            await self._warn_stale_sheets(ctx, sheet_warning)

    @commands.command(name="mark_as_complete", aliases=["mark_as_solved","complete"])
    @with_puzzle_mutex(wait=False)
    async def mark_as_complete(self, ctx):
        """*Mark a puzzle as completed with a tick*"""
        await self._solve_impl(ctx, "✅")

    @commands.command(aliases=["u"])
    @with_puzzle_mutex(wait=False)
    async def unsolve(self, ctx):
        """*Mark an accidentally solved puzzle as not solved*"""

        if not self.get_puzzle(ctx):
            await self.send_not_puzzle_channel(ctx)
            return
        elif not self.get_puzzle(ctx).solved and not self.get_puzzle(ctx).solution:
            await ctx.send(":x: This puzzle appears to not be solved")
            return

        puzzle = self.get_puzzle(ctx)
        prev_solution = puzzle.solution
        puzzle.status = "Unsolved"
        puzzle.solution = ""

        puzzle.solve_time = None

        puzzle.archive_time = None

        PuzzleJsonDb.commit(self.get_puzzle(ctx))

        emoji = self.get_guild_data(ctx).discord_bot_emoji
        embed = discord.Embed(
            description=f"{emoji} Alright, I've unmarked {prev_solution} as the solution. "
            "You'll get 'em next time!  \nGive me a sec to restore the sheets."
        )
        await ctx.send(embed=embed)

        await self.get_gsheet_cog(ctx).restore_puzzle_spreadsheet(puzzle)
        await self.move_to_bottom(ctx)

        puzzle.solved = False

        if self.get_hunt_round(ctx):
            try:
                await self.update_metapuzzle(ctx, self.get_hunt_round(ctx))
            except SheetRefreshError as exc:
                await self._warn_stale_sheets(ctx, exc)

        await self.info(ctx, update=True)
        for sheet in puzzle.additional_sheets:
            SheetsJsonDb.commit(sheet)
        await ctx.send(":white_check_mark: Sheets all restored.")

    @commands.command()
    @commands.has_any_role(*ADMIN_ROLES)
    async def archive_round(self, ctx):
        """(admin) * Archives round to threads *"""
        hunt_general_channel = self.get_hunt_channel(ctx)
        category = ctx.channel.category
        ignore_channels = []
        if self.get_channel_type(ctx) == 'Hunt':
            ignore_channels = [ctx.channel.name]
        success = await self.archive_category(ctx, hunt_general_channel, ignore_channels = ignore_channels, delete_channels=[self.SOLVE_DIVIDER])
        if success:
            await ctx.channel.category.delete(reason=self.DELETE_REASON)
            await hunt_general_channel.send(f":white_check_mark: Round {category.name} successfully archived.")
            return True
        else:
            return False

    @commands.command()
    @commands.has_any_role(*ADMIN_ROLES)
    async def archive_category(self, ctx, archive_to = None, ignore_channels = None, delete_channels = None):
        """(admin) * Archives category to threads *"""
        if ignore_channels is None:
            ignore_channels = []
        if delete_channels is None:
            delete_channels = []
        # If archive_to not set then archive to the main hunt channel
        if archive_to is None:
            archive_to = self.get_hunt_channel(ctx)
        channels = ctx.channel.category.channels
        for channel in channels:
            if channel.name in ignore_channels:
                continue
            if channel.name not in delete_channels:
                success = await self.archive_channel(ctx, channel, archive_to)
                if success is False:
                    return False
            else:
                await channel.delete(reason=self.DELETE_REASON)
        return True

    @commands.command()
    @commands.has_any_role(*ADMIN_ROLES)
    async def archive_channel(self, ctx, channel = None, archive_to = None):
        """(admin) * Archives channel to threads *"""
        if channel is None:
            channel = ctx.channel
        if archive_to is None:
            archive_to = self.bot.get_channel(self.get_hunt(ctx).channel_id)

        webhook = await archive_to.webhooks()
        if len(webhook) == 0:
            await ctx.channel.send(":x: Can't find a webhook in that channel, please create one before archiving")
            return False
        thread_message = await archive_to.send(content=f'Archive of channel {channel.name}', silent=True)
        puzzle = PuzzleJsonDb.get_by_attr(channel_id=channel.id)
        if puzzle:
            hunt_round = self.get_hunt_round(ctx)
            if hunt_round:
                round_name = hunt_round.name
            else:
                round_name = channel.category
            thread_name = f"{puzzle.name} ({round_name})"
            puzzle.archive_time = datetime.datetime.now()
        else:
            thread_name = f"{channel.name} ({channel.category.name})"
        if len(thread_name) > 100:
            thread_name = thread_name[:100]
        thread = await archive_to.create_thread(name=thread_name, message=thread_message)
        messages = []
        # Get all the messages in the channel
        async for message in channel.history(limit=None, oldest_first=True):
            messages.append(message)
        # Get all active threads and iterate for messages
        active_threads = channel.threads
        for active_thread in active_threads:
            async for message in active_thread.history(limit=None, oldest_first=True):
                messages.append(message)
        # Get all archived threads and iterate for messages
        async for archived_thread in channel.archived_threads():
            async for message in archived_thread.history(limit=None, oldest_first=True):
                messages.append(message)
        # Sort the list of messages
        sorted_messages = sorted(messages, key=lambda x: x.created_at)
        bot_id = ctx.bot.application_id
        for message in sorted_messages:
            if message:
                author = message.author
                if author.id == bot_id:
                    continue
                content = message.content
                if content:
                    if content[0] == '!' and content[:3] != '!s ':
                        continue
                    files = []
                    for a in message.attachments:
                        try:
                            files.append(await a.to_file(use_cached=True))
                        except discord.HTTPException as e:
                            # some attachments/filetypes can't be re-downloaded - skip
                            # the attachment but keep archiving the rest of the message.
                            logger.warning(f"Skipping attachment {a.filename!r} while archiving: {e}")
                    avatar_url = getattr(author.avatar, 'url', None)
                    try:
                        moved_message = await webhook[0].send(content=content, username=author.display_name, avatar_url=avatar_url, files = files,
                                                          embeds=message.embeds, thread=thread, wait=True, silent=True)
                    except discord.HTTPException as e:
                        # e.g. an attachment too large to re-upload, or content the API
                        # rejects - skip this message rather than aborting the archive.
                        logger.warning(f"Skipping message from {author.display_name} while archiving: {e}")
                        continue
                    if message.reactions:
                        for reaction in message.reactions:
                            try:
                                await moved_message.add_reaction(reaction.emoji)
                            except discord.HTTPException as e:
                                # e.g. a custom/external emoji the bot can't apply - skip it
                                # rather than aborting the whole archive.
                                logger.warning(f"Skipping reaction {reaction.emoji!r} while archiving: {e}")
                    await asyncio.sleep(0.1)
        await channel.delete(reason=self.DELETE_REASON)
        return True

    @commands.command()
    @commands.has_any_role(*ADMIN_ROLES)
    async def archive_solved_manually(self, ctx):
        """*(admin) Permanently archive solved puzzles*"""
        guild = ctx.guild
        settings = GuildSettingsDb.get(guild.id)
        hunt_id = ctx.channel.category.id
        hunt_category = discord.utils.get(guild.categories, id=hunt_id)
        hunt_settings = settings.hunt_settings[hunt_id]
        solved_category_name = self.get_solved_puzzle_category(hunt_settings.hunt_name)
        solved_category = discord.utils.get(guild.categories, name=solved_category_name)
        last_position = 0
        for hunt_channel in hunt_category.channels:
            logger.debug(f"{hunt_channel.name} : {hunt_channel.position}")
            if hunt_channel.position > last_position:
                last_position = hunt_channel.position

        # Delete the channels without many messages and move the others to the end of the main category
        # TODO Look at the channel move method
        for channel in solved_category.channels:
            num_messages = len(await channel.history(limit=10).flatten())
            if num_messages > 7:
                last_position += 1
                # Category/position aren't rename-limited; apply them immediately,
                # then defer the (strict-bucket) rename if necessary.
                await channel.edit(category=hunt_category, position=last_position)
                await self.safe_rename(channel, "solved-" + channel.name)
            else:
                await channel.delete(reason=self.DELETE_REASON)
        hunt_general_channel = self.get_hunt_channel(ctx)
        await solved_category.delete(reason=self.DELETE_REASON)
        await hunt_general_channel.send(f":white_check_mark: Solved puzzles successfully archived.")

    @commands.command()
    @commands.has_any_role(*ORGANISER_ROLES)
    @with_puzzle_mutex(wait=False)
    async def delete_puzzle(self, ctx):
        """*(admin) Permanently delete a puzzle, its channel and sheet*"""
        # Wrapper for delete command
        if self.get_channel_type(ctx) != "Puzzle":
            await ctx.send(":x: This does not appear to be a puzzle channel")
            return

        if not await self._confirm_destructive(ctx, f"Delete puzzle **{self.get_puzzle(ctx).name}**, its channel and its sheet?"):
            return

        try:
            await self.delete_puzzle_data(ctx, self.get_puzzle(ctx))
            if self.get_hunt_round(ctx):
                try:
                    await self.update_metapuzzle(ctx, self.get_hunt_round(ctx))
                except SheetRefreshError as exc:
                    await self._warn_stale_sheets(ctx, exc)
            await (self.bot.get_channel(self.get_hunt(ctx).channel_id)
                   .send(f":white_check_mark: Puzzle {self.get_puzzle(ctx).name} successfully deleted and sheet cleaned up."))
            await ctx.channel.delete(reason=self.DELETE_REASON)
        except Exception:
            logger.exception("Puzzle deletion failed")
            await ctx.send(f":x: Channel deletion failed")
        self.set_puzzle(ctx, None)

    async def delete_puzzle_data(self, ctx, puzzle: PuzzleData, delete_sheet=True):
        if delete_sheet is True:
            if self.get_gsheet_cog(ctx) is not None:
                await self.get_gsheet_cog(ctx).delete_puzzle_spreadsheet(puzzle)
        PuzzleJsonDb.delete(puzzle.id)
        return True

    async def _delete_puzzles(self, ctx, puzzles):
        """Delete each puzzle's data and its Discord channel. Returns the names
        of puzzles whose channel could not be deleted (already gone or
        rate-limited), so callers can report what remains."""
        failed = []
        for puzzle in puzzles:
            await self.delete_puzzle_data(ctx, puzzle)
            if not await self._safe_delete_channel(self.get_guild(ctx), puzzle.channel_id):
                failed.append(puzzle.name)
        return failed

    async def _warn_failed_channels(self, channel, failed):
        """Notify `channel` about puzzles whose channels could not be deleted."""
        if failed:
            await channel.send(
                ":warning: Could not delete channels for: " + ", ".join(failed)
                + ". They may already be gone or were rate-limited — re-run to finish."
            )

    async def _delete_round_category(self, ctx, hunt_round):
        """Delete a round's category and its solved-divider channel (if present)."""
        category = discord.utils.get(self.get_guild(ctx).categories, id=hunt_round.category_id)
        if self.SOLVE_CATEGORY is False:
            solved_divider = self.get_solved_channel(category)
            if solved_divider:
                await solved_divider.delete(reason=self.DELETE_REASON)
        await category.delete(reason=self.DELETE_REASON)

    @commands.command(aliases=['delete_metapuzzle','delete_group'])
    @commands.has_any_role(*ORGANISER_ROLES)
    async def delete_round(self, ctx):
        """*(admin) Permanently delete a group of puzzles - round/metapuzzle etc.*"""
        if self.get_channel_type(ctx) not in self.PUZZLE_GROUPS:
            await ctx.send(f":x: This command must be done from the main group channel")
            return

        hunt_round = self.get_hunt_round(ctx)
        n = len(PuzzleJsonDb.get_all_from_round(hunt_round.id))
        if not await self._confirm_destructive(ctx, f"Delete round **{hunt_round.name}** and its {n} puzzle(s), channels and sheets?"):
            return

        hunt_general_channel = self.bot.get_channel(self.get_hunt(ctx).channel_id)

        hunt_round = self.get_hunt_round(ctx)
        round_puzzles = PuzzleJsonDb.get_all_from_round(hunt_round.id)
        failed = await self._delete_puzzles(ctx, round_puzzles)

        await self.delete_round_data(hunt_round)

        await self._delete_round_category(ctx, hunt_round)

        await hunt_general_channel.send(f":white_check_mark: Round {self.get_hunt_round(ctx).name} successfully deleted.  "
                                        f"All puzzle sheets have been deleted.")
        await self._warn_failed_channels(hunt_general_channel, failed)
        self.set_puzzle(ctx, None)
        self.set_hunt_round(ctx, None)

    async def delete_round_data(self, hunt_round: RoundData):
        RoundJsonDb.delete(hunt_round.id)
        return True

    async def _confirm_destructive(self, ctx, description):
        """Send a Confirm/Cancel prompt and return True only if the invoker
        confirms. Returns False on cancel or timeout."""
        view = ConfirmView(ctx.author.id)
        embed = discord.Embed(
            title="⚠️ Confirm deletion",
            description=description + "\n\n**Google sheets will be permanently deleted (not recoverable).**",
            color=discord.Color.red(),
        )
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()
        if view.value is None:
            await ctx.send(":hourglass: Confirmation timed out — nothing was deleted.")
        return view.value is True

    async def _safe_delete_channel(self, guild, channel_id):
        """Delete a channel by id, tolerating an already-missing channel and a
        mid-loop rate-limit. Returns True on delete, False if it was missing or
        the delete could not complete (so callers can report what remains)."""
        channel = discord.utils.get(guild.channels, id=channel_id)
        if channel is None:
            return False
        try:
            await channel.delete(reason=self.DELETE_REASON)
            return True
        except discord.NotFound:
            return False  # already gone between lookup and delete — benign, no log
        except (discord.RateLimited, discord.HTTPException):
            logger.exception("delete: could not delete channel %s", channel_id)
            return False

    @commands.command()
    @commands.has_any_role(*ADMIN_ROLES)
    async def delete_hunt(self, ctx):
        """*(admin) Permanently delete a Hunt*"""
        if self.get_channel_type(ctx) == "Hunt":
            hunt_channel = ctx.channel
        else:
            await ctx.send(f":x: This command must be done from the main hunt channel")
            return
        hunt = self.get_hunt(ctx)
        hunt_puzzles = PuzzleJsonDb.get_all_from_hunt(hunt.id)
        n = len(hunt_puzzles)
        if not await self._confirm_destructive(ctx, f"Delete the ENTIRE hunt **{hunt.name}**: {n} puzzle(s), all rounds, channels and sheets?"):
            return
        failed = await self._delete_puzzles(ctx, hunt_puzzles)
        hunt_rounds = RoundJsonDb.get_all(self.get_hunt(ctx).id)
        for hunt_round in hunt_rounds:
            try:
                await self.delete_round_data(hunt_round)
                await self._delete_round_category(ctx, hunt_round)
            except Exception:
                logger.exception("Failed to delete round %s during hunt deletion", hunt_round.id)
        await self._warn_failed_channels(hunt_channel, failed)
        await hunt_channel.delete(reason=self.DELETE_REASON)
        await hunt_channel.category.delete(reason=self.DELETE_REASON)
        HuntJsonDb.delete(self.get_hunt(ctx).id)
        self.set_puzzle(ctx, None)
        self.set_hunt_round(ctx, None)
        self.set_hunt(ctx, None)
        return True

    @commands.command()
    @commands.has_any_role(*ADMIN_ROLES)
    async def debug_puzzle_channel(self, ctx):
        """*(admin) See puzzle metadata*"""

        if self.get_channel_type(ctx) != "Puzzle":
            await self.send_not_puzzle_channel(ctx)
            return

        await ctx.channel.send(f"```json\n{self.get_puzzle(ctx).to_json()}```")


    @tasks.loop(hours=2)
    async def reminder_loop(self, channel):
        reminders = self.REMINDERS
        if self.reminder_index % 2 == 0:
            await channel.send("@everyone You can get some help on how to use PuzzleBot by referring to [Brillig's guide](https://docs.google.com/document/d/1hY-T2nMwHJLHiR-szsmG2F1ZWGIe88jyQpzKiXE1xLY/edit?usp=sharing) or with the `!help` command.  "
                               "The most useful commands are `!p <puzzle_name>` to make a new puzzle, "
                               "`!s <SOLUTION>` to solve one and "
                               "`!info` to get info on the puzzle including a link to the sheet.  Don't forget to go to the overview website for our full hunt status.")
        await channel.send("@here " + random.choice(reminders))
        self.reminder_index += 1

    @commands.hybrid_command(description="Start the reminder loop (organisers only)",)
    @commands.has_any_role(*ORGANISER_ROLES)
    async def start_reminders(self, ctx):
        """*(admin) Start reminder loop*"""
        channel = self.get_hunt_channel(ctx)
        self.reminder_index = 0
        self.reminder_loop.start(channel)
        await ctx.send(":white_check_mark: Reminder loop started.")

    @commands.hybrid_command(description="Stop the reminder loop (organisers only)",)
    @commands.has_any_role(*ORGANISER_ROLES)
    async def stop_reminders(self, ctx):
        """*(admin) Stop reminder loop*"""
        self.reminder_loop.cancel()
        await ctx.send("@here Okay, I'll leave you alone for a bit, but remember I'm always watching...")

    def get_hunt_channel(self, ctx):
        return self.bot.get_channel(self.get_hunt(ctx).channel_id)

    def get_solved_channel(self, category):
        solved_channel = None
        for channel in category.channels:
            if channel.name == self.SOLVE_DIVIDER:
                solved_channel = channel
        return solved_channel


async def setup(bot):
    await bot.add_cog(Puzzles(bot))
