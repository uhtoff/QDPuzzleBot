import datetime
import logging
import random
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import tasks, commands
from googleapiclient.errors import HttpError
import asyncio
from functools import wraps

from bot.utils import urls
from bot.store import PuzzleData, PuzzleJsonDb, GuildSettingsDb, \
    TagGroupJsonDb, HuntData, HuntJsonDb, TagGroupCategoryDb, resolve_group
from bot.utils.chunking import build_note_embeds
from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLES, STATUSES, PRIORITIES

# Alias for the ctx-free channel adapter (M3.5).
import bot.core.discord_channels as _channels
from bot.core import category_layout

logger = logging.getLogger(__name__)

# Per-puzzle async mutex — shared with the web edge via bot/core/locks so a
# dashboard write serializes against the matching bang-command (see M4).
from bot.core.locks import _PUZZLE_LOCKS
from bot.core.errors import SheetRefreshError
from bot.core.puzzle_service import TYPE_DEFAULTS, GROUP_TYPES
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
        from bot.core.notifier import CompositeNotifier, DiscordNotifier
        self.service = PuzzleService(self.bot, notifier=CompositeNotifier([DiscordNotifier(self.bot)]))
        self.reminder_index=0
        self.position_lock = asyncio.Lock()
        self.environment = {}

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{type(self).__name__} Cog ready.")

    async def cog_before_invoke(self, ctx):
        """For separating commands use ctx.message.id"""
        """For logging ctx.author.name and ctx.message.content"""
        """Before command invoked setup puzzle objects and channel type"""
        guild = None
        guild_data = None
        channel_type = None
        hunt = None
        tag_group = None
        puzzle = None
        gsheet_cog = None
        guild = ctx.guild
        guild_data = GuildSettingsDb.get(ctx.guild.id)

        if isinstance(ctx.channel, discord.Thread):
            # Mini-puzzle thread: resolve directly by thread snowflake, no category needed.
            puzzle = PuzzleJsonDb.get_by_attr(channel_id=ctx.channel.id)
            if puzzle:
                channel_type = 'Puzzle'
                hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
                # A forum thread can be a group's target (meta/round/capstone): upgrade
                # the type so !info treats it as a meta, mirroring the category path below.
                target_group = TagGroupJsonDb.get_by_attr(target_puzzle_id=puzzle.id)
                if target_group:
                    channel_type = target_group.type
                    tag_group = target_group
                else:
                    for tag_id in puzzle.tags:
                        r = TagGroupJsonDb.get_by_attr(id=tag_id)
                        if r:
                            tag_group = r
                            break
            else:
                channel_type = 'Guild'
        else:
            channel_type = GuildSettingsDb.get_channel_type(ctx.channel.id)
            if channel_type is None:
                channel_type = GuildSettingsDb.get_channel_type(ctx.channel.category.id)
            if channel_type is None:
                channel_type = "Guild"
            if channel_type == 'Hunt':
                hunt = HuntJsonDb.get_by_attr(channel_id=ctx.channel.id,)
            if channel_type == 'Group':
                # Forum-mode Group: the text channel's OWN id is the group's category_id.
                # Channel-mode Group: fallback to category lookup below.
                tag_group = TagGroupJsonDb.get_by_attr(category_id=ctx.channel.id)
                if tag_group is None:
                    tag_group = TagGroupJsonDb.get_by_attr(category_id=ctx.channel.category.id,)
                if tag_group:
                    channel_type = tag_group.type
                if tag_group:
                    channel_type = tag_group.type
                    hunt = HuntJsonDb.get_by_attr(id=tag_group.hunt_id)
                    if tag_group.target_puzzle_id:
                        puzzle = PuzzleJsonDb.get_by_attr(id=tag_group.target_puzzle_id)
            if channel_type == 'Puzzle':
                puzzle = PuzzleJsonDb.get_by_attr(channel_id=ctx.channel.id,)
                # Check if we've actually got the metapuzzle for a group and set the right channel type if so
                # Otherwise grab the group from the category
                if puzzle:
                    tag_group = TagGroupJsonDb.get_by_attr(target_puzzle_id=puzzle.id)
                    if tag_group:
                        channel_type = tag_group.type
                    else:
                        tag_group = TagGroupJsonDb.get_by_attr(category_id=ctx.channel.category.id, )
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
                                            'tag_group': tag_group,
                                            'puzzle': puzzle,
                                            'guild': guild,
                                            'guild_data': guild_data,
                                            'gsheet_cog': gsheet_cog}

    async def cog_after_invoke(self, ctx):
        """After command invoked ensure changes committed to database"""
        if self.get_puzzle(ctx):
            PuzzleJsonDb.commit(self.get_puzzle(ctx))
        if self.get_tag_group(ctx):
            TagGroupJsonDb.commit(self.get_tag_group(ctx))
        if self.get_hunt(ctx):
            HuntJsonDb.commit(self.get_hunt(ctx))
        if self.get_guild_data(ctx):
            GuildSettingsDb.commit(self.get_guild_data(ctx))
        self.environment.pop(ctx.message.id, None)

    async def group_code_autocomplete(
            self,
            interaction: discord.Interaction,
            current: str,
    ) -> list[app_commands.Choice[str]]:
        # Pull valid rounds/metas from DB. Resolve the owning group through the
        # shared fallback (_tag_group_for_channel handles overflow/solved categories
        # via the join table, forum threads, and null-category contexts); return []
        # when the context can't be resolved so the autocomplete never raises.
        channel = interaction.channel
        tag_group = self._tag_group_for_channel(channel)
        if tag_group is None:
            return []
        rounds = TagGroupJsonDb.get_all(tag_group.hunt_id)
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
            if not isinstance(r.group_code, str) or len(r.group_code) != 6:
                continue
            # Build a human label. Keep it short-ish.
            label = f"{r.name} ({r.group_code})"
            # Allow searching by name OR meta_code OR id
            haystack = f"{r.name} {r.group_code} {r.id}".lower()
            if current_lc in haystack:
                choices.append(app_commands.Choice(name=label[:100], value=r.group_code))

            if len(choices) >= 25:  # Discord hard limit
                break

        return choices

    async def puzzle_type_autocomplete(
            self,
            interaction: discord.Interaction,
            current: str,
    ) -> list[app_commands.Choice[str]]:
        # puzzle_type is free-form (no fixed vocabulary), so this only *suggests* — the
        # user can still submit any text. Offer the types already used in this hunt first
        # (the team's real vocabulary), then common defaults not already present.
        defaults = ["Crossword", "Logic", "Cryptic", "Wordplay", "Meta",
                    "Word Search", "Cipher", "Image", "Audio", "Physical"]
        suggestions = defaults
        try:
            category = interaction.channel.category
            hunt_id = TagGroupJsonDb.get_by_attr(category_id=category.id).hunt_id
            used = sorted({p.puzzle_type for p in PuzzleJsonDb.get_all_from_hunt(hunt_id) if p.puzzle_type})
            seen = {t.lower() for t in used}
            suggestions = used + [d for d in defaults if d.lower() not in seen]
        except Exception:
            # Autocomplete must never raise (it just shows nothing useful); fall back to defaults.
            pass

        current_lc = (current or "").lower()
        return [
            app_commands.Choice(name=t[:100], value=t)
            for t in suggestions
            if current_lc in t.lower()
        ][:25]

    async def resolve_target_group(self, ctx, meta_code):
        """Resolve `meta_code` to a TagGroupData within the current hunt, or send help and
        return None.

        Forgiving counterpart to the slash autocomplete for the bang path: trims/normalises
        input, accepts a group *name* as well as its 6-char code, scopes to the current hunt,
        and replies with an actionable message (disambiguation list / available groups)
        instead of a bare ":x: invalid". Used by feeds / informs / groups_with / remove_from."""
        hunt = self.get_hunt(ctx)
        groups = TagGroupJsonDb.get_all(hunt.id) if hunt else []
        match = resolve_group(meta_code, groups)
        if match.group:
            return match.group

        def _listing(items):
            return "\n".join(f"• {g.name} (`{g.group_code}`)" for g in items[:10])

        if match.status == "ambiguous":
            await ctx.send(
                f":grey_question: `{meta_code}` matches several groups — be more specific:\n"
                f"{_listing(match.candidates)}")
        elif match.status == "empty":
            await ctx.send(":x: Please supply a group code or name, e.g. `!feeds AB12CD`.")
        else:  # none
            metas = [g for g in groups if isinstance(g.group_code, str) and len(g.group_code) == 6]
            if metas:
                await ctx.send(
                    f":x: No group matches `{meta_code}`. Available groups:\n{_listing(metas)}")
            else:
                await ctx.send(":x: This hunt has no groups to assign to yet.")
        return None

    def clean_name(self, name):
        """Cleanup name to be appropriate for discord channel."""
        return _channels.clean_name(name)

    def get_channel_type(self, ctx):
        return self.environment[ctx.message.id]['channel_type'] or None

    def set_channel_type(self, ctx, channel_type):
        self.environment[ctx.message.id]['channel_type'] = channel_type

    def get_hunt(self, ctx):
        if ctx is None:
            return None
        return self.environment[ctx.message.id]['hunt'] or None

    def set_hunt(self, ctx, hunt):
        self.environment[ctx.message.id]['hunt'] = hunt

    def get_tag_group(self, ctx):
        if ctx is None:
            return None
        return self.environment[ctx.message.id]['tag_group'] or None

    def set_tag_group(self, ctx, tag_group):
        self.environment[ctx.message.id]['tag_group'] = tag_group

    def get_puzzle(self, ctx):
        if ctx is None:
            return None
        return self.environment[ctx.message.id]['puzzle'] or None

    def set_puzzle(self, ctx, puzzle):
        self.environment[ctx.message.id]['puzzle'] = puzzle

    def _resolve_group_for_rename(self, ctx):
        """Resolve the group to rename from the current channel, or None.

        Two valid contexts: a regular group's main text channel (channel_type
        'Group'), or a group's TARGET puzzle channel (the resolved tag_group's
        target_puzzle_id matches the current puzzle). Feeder/other channels -> None.
        """
        tag_group = self.get_tag_group(ctx)
        if tag_group is None:
            return None
        if self.get_channel_type(ctx) == "Group":
            return tag_group
        puzzle = self.get_puzzle(ctx)
        if puzzle is not None and tag_group.target_puzzle_id == puzzle.id:
            return tag_group
        return None

    def get_guild(self, ctx):
        if ctx is None:
            return None
        return self.environment[ctx.message.id]['guild'] or None

    def get_guild_data(self, ctx):
        if ctx is None:
            return None
        return self.environment[ctx.message.id]['guild_data'] or None

    def get_gsheet_cog(self, ctx):
        if ctx is None:
            return None
        return self.environment[ctx.message.id]['gsheet_cog'] or None

    def get_puzzle_sheet(self, ctx=None, puzzle: PuzzleData = None, hunt: HuntData = None):
        """Return the active or archive spreadsheet id for a puzzle.

        When *hunt* is not provided (bang path), falls back to environment lookup.
        """
        if hunt is None:
            hunt = self.get_hunt(ctx)
        if puzzle.archived:
            return hunt.archive_google_sheet_id
        else:
            return hunt.google_sheet_id

    def get_additional_sheet_spreadsheet(self, ctx=None, puzzle: PuzzleData = None, hunt: HuntData = None):
        """Return the spreadsheet id for additional sheets (archive when solved)."""
        if hunt is None:
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
        # Threads are uncapped (forum mode), always have space
        if isinstance(ctx.channel, discord.Thread):
            return True
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
        """*Create a new hunt: its category, general channel and Google sheet.*

        Usage: `!hunt hunt-name:hunt-url[, role-name][, forum]`
        Example: `!hunt mystery-2026:https://hunt.example.com`
        Add `, Team A` to gate the hunt behind a role. Append `, forum` to use a
        forum-backed layout (a forum channel per round). Run in the bot channel.
        Admins/mods only. Alias: `!h`"""

        if not (await self.check_is_bot_channel(ctx)):
            return

        # Optional trailing ", forum" token selects the forum-backed layout. Strip it
        # first so the existing name:url[, role] parsing below is unaffected.
        layout_mode = "channel"
        stripped = arg.rstrip()
        if stripped.lower().endswith(", forum"):
            arg = stripped[: -len(", forum")]
            layout_mode = "forum"

        role = None
        if ", " in arg:
            arg, role = arg.split(", ", 1)
        if ":" in arg:
            hunt_name, hunt_url = arg.split(":", 1)
            if HuntJsonDb.check_duplicates(hunt_name):
                return await ctx.send(f":exclamation: **Hunt {hunt_name}** already exists, please use a different name")
            await self.create_hunt(ctx, hunt_name, hunt_url, role, layout_mode=layout_mode)
            return await ctx.send(
                f":white_check_mark: I've created a new hunt category and channel for {hunt_name}"
            )

        await ctx.send(f"Unable to parse hunt name {arg}, try using `!h hunt-name:hunt-url`")

    async def create_hunt(self, ctx, hunt_name: str, hunt_url: str, role_name: Optional[str] = None,
                          layout_mode: str = "channel"):
        if await self.check_available_space(ctx, 5, 2) is False:
            return await ctx.send(":x: There is not enough enough space on the server to create a new hunt, please inform the moderators.")

        settings = self.get_guild_data(ctx)
        new_hunt = await self.service.create_hunt(
            ctx.guild,
            name=hunt_name,
            url=hunt_url,
            start_timestamp=0,
            role_name=role_name,
            layout_mode=layout_mode,
            guild_id=settings.id if settings else 0,
        )

        text_channel = self.bot.get_channel(new_hunt.channel_id)
        initial_message = await self.send_initial_hunt_channel_messages(ctx, text_channel, hunt=new_hunt)
        await initial_message.pin()

        return (ctx.guild.get_channel(new_hunt.category_id), text_channel, True)

    @commands.hybrid_command(description="Set the hunt to use a second spreadsheet to hold solved puzzles (admin only)",)
    @commands.has_any_role(*ADMIN_ROLES)
    async def set_to_archive(self, ctx):
        """*Set this hunt to use a second spreadsheet to hold solved puzzles.*

        Usage: `!set_to_archive`
        Example: `!set_to_archive`
        Run in the main Hunt channel. Admins/mods only."""
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
        """*Create a new puzzle channel and Google sheet in the current round/hunt.*

        Usage: `!puzzle puzzle-name`
        Example: `!puzzle clueless-crossword`
        Run in the hunt or round you want the puzzle to belong to. Alias: `!p`

        Delegates to PuzzleService.create_puzzle (Phase 5 strangle).
        Bang-edge duplicate check + ctx.send ack stay at this level.
        """
        from bot.core.notifier import DiscordNotifier

        puzzle_name = arg
        hunt = self.get_hunt(ctx)
        guild = ctx.guild

        if PuzzleJsonDb.check_duplicates_in_hunt(puzzle_name, hunt.id):
            return await ctx.send(
                f":exclamation: Puzzle **{puzzle_name}** already exists in this hunt or "
                "will lead to a duplicate channel name, please use a different name"
            )

        if await self.check_available_space(ctx, 1) is False:
            return await ctx.send(
                ":x: There is no free channel to create this puzzle, please inform "
                "the moderators."
            )

        env = self.environment.get(ctx.message.id)
        round_data = env.get("tag_group") if env else None

        try:
            await self.service.create_puzzle(
                guild, hunt, round_data, puzzle_name,
                is_meta=False, notifier=DiscordNotifier(self.bot),
            )
        except RuntimeError:
            return await ctx.send(":x: Failed to create puzzle (server full?).")
        asyncio.create_task(self.service.refresh_unassigned_safe(hunt))

    # --- legacy create twins removed (Phase 5 strangle) ---

    @commands.hybrid_command(description="Rebuild meta roll-up sheets (organiser only)")
    @commands.has_any_role(*ORGANISER_ROLES)
    async def rebuild(self, ctx):
        """*Rebuild meta roll-up sheets from the current puzzle data.*

        In a round/meta channel rebuilds just that round's meta; run anywhere else
        in the hunt it rebuilds every meta.
        Usage: `!rebuild`
        Example: `!rebuild`
        Organisers/admins only."""
        hunt = self.get_hunt(ctx)
        if hunt is None:
            await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")
            return
        # Targeted: if this channel's category is a meta round, rebuild only that one.
        target = None
        category = getattr(ctx.channel, "category", None)
        if category is not None:
            candidate = TagGroupJsonDb.get_by_attr(category_id=category.id)
            if candidate is not None and candidate.target_puzzle_id:
                target = candidate
        rounds = [target] if target is not None else [
            r for r in TagGroupJsonDb.get_all(hunt.id) if r.target_puzzle_id
        ]
        if not rounds:
            await ctx.send(":information_source: No meta sheets to rebuild in this hunt.")
            return

        ok = 0
        failed = []
        for target_group in rounds:
            try:
                await self.service.refresh_target(hunt, target_group)
                ok += 1
            except Exception:
                logger.exception("!rebuild: refresh failed for group %s", target_group.id)
                failed.append(target_group.group_code or target_group.name or str(target_group.id))

        msg = f":white_check_mark: Rebuilt {ok} meta sheet{'s' if ok != 1 else ''}."
        if failed:
            msg += (f"\n:warning: {len(failed)} still failing (left stale): "
                    + ", ".join(str(f) for f in failed))
        await ctx.send(msg)

    @commands.hybrid_command(name="rebuild_unassigned", description="Rebuild the Unassigned Feeders tab (organiser only)")
    @commands.has_any_role(*ORGANISER_ROLES)
    async def rebuild_unassigned(self, ctx, *, show_round: bool = False):
        """*Reconcile the hunt's "Unassigned Feeders" sheet tab with current data.*

        Usage: `!rebuild_unassigned [show_round]`
        Example: `!rebuild_unassigned true`
        Pass a truthy value (e.g. `true`) to include the round column. Organisers/admins only."""
        hunt = self.get_hunt(ctx)
        if hunt is None:
            await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")
            return
        try:
            await self.service.refresh_unassigned(hunt, show_round=show_round)
            await ctx.send(":white_check_mark: Unassigned Feeders tab reconciled.")
        except Exception as exc:
            logger.exception("rebuild_unassigned failed for hunt %s", hunt.id if hunt else "?")
            await ctx.send(f":warning: Failed to reconcile unassigned tab: `{type(exc).__name__}: {exc}`")

    @staticmethod
    def _orphan_name_lines(report, *, limit=20):
        """Render named, truncated sections for the orphan channels/tabs in
        `report`. Each list is capped at `limit` entries (with an "…and N more"
        line) to stay within Discord's message limits. Shared by the reconcile
        report and the destructive-confirmation prompt."""
        lines = []

        def _section(label, items, render):
            if not items:
                return
            shown = items[:limit]
            names = ", ".join(render(item) for item in shown)
            lines.append(f"**{label}:** {names}")
            remaining = len(items) - len(shown)
            if remaining:
                lines.append(f"…and {remaining} more")

        _section("Channels", report.orphan_channels, lambda c: f"#{c[1]}")
        _section("Tabs", report.orphan_tabs, lambda t: f'"{t[1]}"')
        return lines

    @staticmethod
    def _format_reconcile_report(report, *, applied, limit=20):
        verb = "Applied" if applied else "Would apply"
        lines = [f"**Reconcile — {verb.lower()}:**"]
        counts = report.counts()
        if counts:
            lines += [f"- {kind}: {n}" for kind, n in sorted(counts.items())]
        else:
            lines.append("- no channel/tab changes needed")
        if report.orphan_channels:
            lines.append(f":wastebasket: {len(report.orphan_channels)} orphan channel"
                         f"{'s' if len(report.orphan_channels) != 1 else ''} "
                         "(not deleted unless you run `!reconcile apply orphans`)")
        if report.orphan_tabs:
            lines.append(f":wastebasket: {len(report.orphan_tabs)} orphan tab"
                         f"{'s' if len(report.orphan_tabs) != 1 else ''} "
                         "(not deleted unless you run `!reconcile apply orphans`)")
        lines += Puzzles._orphan_name_lines(report, limit=limit)
        if report.failures:
            lines.append(":warning: failures: "
                         + ", ".join(f"{t} ({e})" for t, e in report.failures))
        return "\n".join(lines)

    @staticmethod
    def _format_orphan_confirmation(report, *, limit=20):
        """Build the destructive-confirmation prompt, naming the orphan channels
        and tabs that would be deleted. Truncates each list to `limit` entries
        (with an "…and N more" line) to stay within Discord's embed limits."""
        lines = [f"Delete {len(report.orphan_channels)} channel(s) and "
                 f"{len(report.orphan_tabs)} tab(s) with no matching puzzle?"]
        for line in Puzzles._orphan_name_lines(report, limit=limit):
            # Blank-line separator before each section header for readability.
            lines.append(f"\n{line}" if line.startswith("**") else line)
        return "\n".join(lines)

    @commands.hybrid_command(description="Reconcile Discord + Sheets against the DB (organiser only)")
    @commands.has_any_role(*ORGANISER_ROLES)
    async def reconcile(self, ctx, mode: str = "", scope: str = ""):
        """*Heal Discord channels and Sheets tabs to match the database.*

        Usage: `!reconcile [apply [orphans]]`
        Example: `!reconcile` (report only)
        `!reconcile apply` fixes mismatches; `!reconcile apply orphans` also deletes
        orphaned channels/tabs (with confirmation). Run in a hunt channel. Organisers/admins only."""
        hunt = self.get_hunt(ctx)
        if hunt is None:
            await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")
            return
        apply = mode.lower() == "apply"
        want_orphans = scope.lower() == "orphans"

        report = await self.service.reconcile(ctx.guild, hunt, apply=apply)
        await ctx.send(self._format_reconcile_report(report, applied=apply))

        if apply and want_orphans and report.has_orphans():
            desc = self._format_orphan_confirmation(report)
            if await self._confirm_destructive(ctx, desc):
                deleted, failed = await self.service.delete_orphans(ctx.guild, hunt, report)
                msg = f":white_check_mark: Deleted {deleted} orphan(s)."
                if failed:
                    msg += f" :warning: {failed} could not be deleted."
                await ctx.send(msg)

    async def update_metapuzzle(self, ctx, tag_group):
        """Refresh a single meta's roll-up sheet (badged on failure via the rollup).
        Raises SheetRefreshError if it failed, so the bang edge surfaces it uniformly
        with _warn_stale_sheets instead of aborting on a raw Sheets error."""
        hunt = self.get_hunt(ctx)
        failed = await self.service.refresh_targets(hunt, [tag_group])
        if failed:
            raise SheetRefreshError(failed)

    async def web_refresh_meta(self, hunt, tag_group):
        """Best-effort wrapper over PuzzleService.refresh_target.  Swallows Sheets errors."""
        try:
            await self.service.refresh_target(hunt, tag_group)
        except Exception:
            logger.exception("web_refresh_meta: sheet update failed for group %s",
                             getattr(tag_group, "id", None))


    async def _web_send_welcome(self, guild, hunt, tag_group, puzzle, channel):
        """Post and pin the welcome message via DiscordNotifier."""
        from bot.core.notifier import DiscordNotifier
        notifier = DiscordNotifier(self.bot)
        try:
            await notifier.welcome(guild, hunt, tag_group, puzzle, channel)
        except Exception:
            logger.exception("web create: failed to send/pin welcome message")

    async def web_refresh_info(self, guild, hunt, puzzle):
        """Best-effort refresh of a puzzle's pinned info message after a metadata
        change (status/priority/type/solution/sheets), mirroring `!info update`.
        Never raises — a Discord hiccup must not fail the underlying DB write."""
        from bot.core.notifier import DiscordNotifier
        notifier = DiscordNotifier(self.bot)
        try:
            await notifier.refresh_info(guild, hunt, puzzle)
        except Exception:
            logger.exception("web_refresh_info: failed to refresh pinned info for puzzle %s", puzzle.id)

    async def web_post_solved_embed(self, guild, hunt, puzzle):
        """Best-effort dashboard-side presentation after service.solve: post the
        SOLVED embed in the puzzle channel, reposition it to the solved section,
        and refresh the pinned info. Never raises."""
        # get_channel_or_thread (not get_channel) so a mini-puzzle, whose channel_id
        # is a thread snowflake, also resolves — otherwise the SOLVED embed is silently
        # skipped for minis.
        channel = guild.get_channel_or_thread(puzzle.channel_id) if (guild and puzzle.channel_id) else None
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
        if isinstance(channel, discord.Thread):
            # A mini-puzzle thread can't be repositioned, and the solve service has
            # already archived it on solve — nothing to do here beyond the embed above.
            pass
        elif channel is not None and channel.category is not None:
            try:
                await self._apply_solved_move(guild, channel)
            except Exception:
                logger.exception("web_post_solved_embed: failed to reposition channel")
        await self.web_refresh_info(guild, hunt, puzzle)

    async def web_post_unsolved(self, guild, hunt, puzzle, prev_solution=""):
        """Best-effort dashboard-side presentation after service.unsolve: post the
        UNSOLVED embed in the puzzle channel, move it back to an unsolved category
        (split state) or above the divider (single state), and refresh the pinned
        info. Never raises. Mirrors web_post_solved_embed (and the bang !unsolve
        channel message), since service.unsolve has already cleared the solution
        the caller passes prev_solution for the message."""
        # get_channel_or_thread (not get_channel) so a mini-puzzle, whose channel_id
        # is a thread snowflake, also resolves — otherwise the embed is skipped for minis.
        channel = guild.get_channel_or_thread(puzzle.channel_id) if (guild and puzzle.channel_id) else None
        if channel is not None:
            guild_data = GuildSettingsDb.get(guild.id)
            emoji = guild_data.discord_bot_emoji if guild_data else ""
            solution_txt = f" `{prev_solution}`" if prev_solution else ""
            embed = discord.Embed(
                title="PUZZLE UNSOLVED",
                description=f"{emoji} I've unmarked{solution_txt} as the solution. You'll get 'em next time!",
            )
            embed.add_field(name="Follow-up", value="Unsolved from the dashboard. Restoring the sheets…")
            try:
                await channel.send(embed=embed)
            except Exception:
                logger.exception("web_post_unsolved: failed to post unsolved message")
        if channel is not None and not isinstance(channel, discord.Thread) and channel.category is not None:
            try:
                await self._apply_unsolved_move(guild, channel)
            except Exception:
                logger.exception("web_post_unsolved: failed to reposition channel")
        await self.web_refresh_info(guild, hunt, puzzle)

    def _tag_group_for_channel(self, channel):
        """Resolve the tag_group owning a channel's category — primary category
        first, then the join table for solved/overflow categories."""
        if isinstance(channel, discord.Thread):
            # Forum puzzle: parent is the forum, whose id == tag_group.category_id
            parent = channel.parent
            if parent is None:
                return None
            tg = TagGroupJsonDb.get_by_attr(category_id=parent.id)
            return tg
        if channel.category is None:
            return None
        tg = TagGroupJsonDb.get_by_attr(category_id=channel.category.id)
        if tg is not None:
            return tg
        gid = category_layout.group_id_for_category(channel.category.id, TagGroupCategoryDb)
        return TagGroupJsonDb.get_by_attr(id=gid) if gid else None

    async def _apply_solved_move(self, guild, channel):
        """Category-aware reposition of a just-solved channel: SPLIT → move into a
        solved category (+ clean an emptied overflow source); SINGLE → below the
        divider. Caller guards thread/None. Never raises on its own."""
        tag_group = self._tag_group_for_channel(channel)
        if tag_group is not None and category_layout.is_split(tag_group, TagGroupCategoryDb):
            source = await category_layout.move_channel_to_solved(
                guild, tag_group, TagGroupCategoryDb, channel,
            )
            if source is not None:
                await category_layout.cleanup_empty_overflow(
                    guild, tag_group, TagGroupCategoryDb, source,
                )
        else:
            divider = self.get_solve_divider_position(None, channel.category)
            if divider is not False:
                async with self.position_lock:
                    await channel.edit(position=divider + 1)

    async def _apply_unsolved_move(self, guild, channel):
        """Category-aware reposition of a just-unsolved channel: SPLIT → move back to
        an unsolved category (+ clean an emptied overflow source); SINGLE → above the
        divider. Caller guards thread/None. Never raises on its own."""
        tag_group = self._tag_group_for_channel(channel)
        if tag_group is not None and category_layout.is_split(tag_group, TagGroupCategoryDb):
            source = await category_layout.move_channel_to_unsolved(
                guild, tag_group, TagGroupCategoryDb, channel,
            )
            if source is not None:
                await category_layout.cleanup_empty_overflow(
                    guild, tag_group, TagGroupCategoryDb, source,
                )
        else:
            divider = self.get_solve_divider_position(None, channel.category)
            if divider is not False:
                async with self.position_lock:
                    await channel.edit(position=divider)

    def get_first_channel(self, ctx, category):
        return _channels.first_channel_position(category)

    def get_solve_divider_position(self, ctx, category):
        return _channels.solve_divider_position(category)

    @commands.hybrid_command(description="Move the channel to the top of the unsolved section of the category (organisers only)",)
    @commands.has_any_role(*ORGANISER_ROLES)
    async def move_to_top(self, ctx):
        """*Move this channel to the top of the category's unsolved section.*

        Usage: `!move_to_top`
        Example: `!move_to_top`
        Run in the puzzle channel you want to move. Organisers/admins only."""
        channel = ctx.channel
        if isinstance(channel, discord.Thread):
            # Threads live inside a channel and can't be repositioned within a category.
            await ctx.send(":information_source: This is a thread, which can't be repositioned.")
            return
        position = self.get_first_channel(ctx, channel.category)
        await channel.edit(position=position)
        await ctx.send(":white_check_mark: Channel moved to top of category")

    @commands.hybrid_command(description="Move the channel to the bottom of the unsolved section of the category (organisers only)",)
    @commands.has_any_role(*ORGANISER_ROLES)
    async def move_to_bottom(self, ctx):
        """*Move this channel to the bottom of the category's unsolved section.*

        Usage: `!move_to_bottom`
        Example: `!move_to_bottom`
        Run in the puzzle channel you want to move. Organisers/admins only."""
        channel = ctx.channel
        if isinstance(channel, discord.Thread):
            await ctx.send(":information_source: This is a thread, which can't be repositioned.")
            return
        await self._apply_unsolved_move(self.get_guild(ctx), channel)
        await ctx.send(":white_check_mark: Channel moved to bottom of unsolved")

    @commands.hybrid_command(description="Move the channel to the solved section of the category (organisers only)",)
    @commands.has_any_role(*ORGANISER_ROLES)
    async def move_to_solved(self, ctx):
        """*Move this channel into the category's solved section (mini-puzzle threads are archived).*

        Usage: `!move_to_solved`
        Example: `!move_to_solved`
        Run in the puzzle channel you want to move. Organisers/admins only."""
        channel = ctx.channel
        if isinstance(channel, discord.Thread):
            # A mini-puzzle thread can't be repositioned into the solved section;
            # archiving is its "moved to solved" equivalent. (On the !s path the
            # solve service has already archived it; this also covers a standalone
            # !move_to_solved.) Confirm first, then archive, so the confirmation
            # message doesn't re-open the thread we just archived.
            await ctx.send(":white_check_mark: Mini-puzzle thread archived")
            await channel.edit(archived=True)
            return
        await self._apply_solved_move(self.get_guild(ctx), channel)
        await ctx.send(":white_check_mark: Channel moved to solved")


    @commands.hybrid_command(description="Show all tags on the puzzle (admin only)",)
    @commands.has_any_role(*ORGANISER_ROLES)
    async def list_tags(self, ctx):
        """*List the tag IDs on this puzzle plus all available group tags (troubleshooting).*

        Usage: `!list_tags`
        Example: `!list_tags`
        Run in a puzzle channel. Organisers/admins only."""
        puzzle = self.get_puzzle(ctx)
        tag_info = ""
        tags = puzzle.tags.copy()
        for tag in tags:
            tag_name = TagGroupJsonDb.get_by_attr(id=tag).name
            tag_info += f"{tag_name} - ID:{tag}\n"
        embed = discord.Embed(
            description=f"""Tag listing for {puzzle.name}"""
        )
        embed.add_field(
            name="Current tags",
            value=tag_info,
            inline=False,
        )
        groups = TagGroupJsonDb.get_all(self.get_hunt(ctx).id)
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
        """*Manually add a group tag ID to this puzzle (troubleshooting — prefer !feeds/!informs).*

        Usage: `!add_tag tag_id`
        Example: `!add_tag 42`
        Find IDs with `!list_tags`. Run in a puzzle channel. Organisers/admins only."""
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
        """*Manually remove a group tag ID from this puzzle (troubleshooting — prefer !remove_from).*

        Usage: `!remove_tag tag_id`
        Example: `!remove_tag 42`
        Find IDs with `!list_tags`. Run in a puzzle channel. Organisers/admins only."""
        remove_tag = int(arg)
        puzzle = self.get_puzzle(ctx)
        if remove_tag in puzzle.tags:
            puzzle.tags.remove(remove_tag)
        await ctx.channel.send(f"```json\n{puzzle.to_json(indent=2)}```")

    @commands.command()
    @with_puzzle_mutex(wait=True)
    async def add_sheet(self, ctx, *args):
        """*Add an extra working sheet (tab) to this puzzle.*

        Usage: `!add_sheet sheet-name`
        Example: `!add_sheet grid attempt 2`
        Run in a puzzle channel that isn't solved yet. List them with `!list_sheets`."""
        puzzle = self.get_puzzle(ctx)

        if puzzle.solved:
            return await ctx.send(":x: You can't add additional sheets to solved puzzles!")

        name = " ".join(args)

        await self.service.add_sheet(self.get_hunt(ctx), puzzle, name)
        await self.info(ctx, update=True)
        return await ctx.send(f":white_check_mark: Added sheet {name} to puzzle.")

    @commands.command(aliases=['delete_sheet'])
    @with_puzzle_mutex(wait=True)
    async def remove_sheet(self, ctx, number: int):
        """*Remove an additional sheet from this puzzle by its number.*

        Usage: `!remove_sheet number`
        Example: `!remove_sheet 2`
        Get the number from `!list_sheets`. Run in a puzzle channel. Alias: `!delete_sheet`."""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        if number < 1 or number > len(puzzle.additional_sheets):
            return await ctx.send(f":x: No additional sheet #{number}. Use !list_sheets to see them.")
        name = puzzle.additional_sheets[number - 1].puzzle_name
        await self.service.remove_sheet_by_index(self.get_hunt(ctx), puzzle, number)
        await self.info(ctx, update=True)
        return await ctx.send(f":white_check_mark: Removed sheet {name} from puzzle.")

    @commands.command(aliases=['list_sheets'])
    async def list_additional_sheets(self, ctx):
        """*List this puzzle's additional sheets with links and their numbers.*

        Usage: `!list_sheets`
        Example: `!list_sheets`
        Run in a puzzle channel. Alias: `!list_additional_sheets`. Numbers feed `!remove_sheet`."""
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

    @commands.command()
    async def make_minimeta(self, ctx):
        """*Convert this puzzle into a minimeta cluster that holds mini-puzzles.*

        Usage: `!make_minimeta`
        Example: `!make_minimeta`
        Run in a puzzle channel, then add minis with `!add_mini`."""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        await self.service.make_minimeta(puzzle)
        await ctx.send(f":white_check_mark: **{puzzle.name}** is now a minimeta cluster. Use `!add_mini` to add mini-puzzles.")

    @commands.command()
    async def remove_minimeta(self, ctx):
        """*Revert this minimeta back to an ordinary puzzle, deleting its mini-puzzles.*

        Usage: `!remove_minimeta`
        Example: `!remove_minimeta`
        Run in the minimeta's channel. Deletes every mini-puzzle (thread + sheet) and
        the rollup table, then clears the Minimeta type."""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        try:
            await self.service.remove_minimeta(ctx.guild, self.get_hunt(ctx), puzzle)
        except RuntimeError:
            return await ctx.send(":x: This puzzle is not a minimeta.")
        await ctx.send(
            f":white_check_mark: **{puzzle.name}** is no longer a minimeta; its "
            f"mini-puzzles were removed."
        )

    @commands.command()
    @with_puzzle_mutex(wait=True)
    async def add_mini(self, ctx, *args):
        """*Add a mini-puzzle (its own thread + sheet) to this minimeta.*

        Usage: `!add_mini name`
        Example: `!add_mini hidden word search`
        Run in the minimeta channel (see `!make_minimeta`)."""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        name = " ".join(args).strip()
        if not name:
            return await ctx.send(":x: Provide a name: `!add_mini Puzzle Name`")
        try:
            mini = await self.service.create_mini(
                ctx.guild, self.get_hunt(ctx), puzzle, name
            )
        except RuntimeError as exc:
            return await ctx.send(f":x: Could not create mini-puzzle: {exc}")
        await self.info(ctx, update=True)  # refresh the minimeta's pinned mini list
        await ctx.send(f":white_check_mark: Created mini-puzzle **{mini.name}**. Thread: <#{mini.channel_id}>")

    @commands.command()
    async def list_minis(self, ctx):
        """*List the mini-puzzles in this minimeta with their numbers and solve state.*

        Usage: `!list_minis`
        Example: `!list_minis`
        Run in the minimeta channel. Numbers feed `!solve_mini` / `!unsolve_mini`."""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        minis = self.service.get_minis(puzzle.id)
        if not minis:
            return await ctx.send(":x: No mini-puzzles found. Use `!add_mini` to add one.")
        embed = discord.Embed(title=f"Mini-puzzles for {puzzle.name}")
        embed.description = self.create_mini_list(puzzle, minis, self.get_hunt(ctx))
        await ctx.send(embed=embed)

    def create_mini_list(self, puzzle: PuzzleData, minis=None, hunt: HuntData = None):
        """Render the numbered mini-puzzle list (solved/unsolved). Shared by
        !list_minis and the pinned-info embed so both stay in sync. Each row
        links to the mini's thread (a channel mention jumps to it) and, when
        *hunt* is supplied, its Google sheet tab."""
        if minis is None:
            minis = self.service.get_minis(puzzle.id)
        lines = []
        for i, mini in enumerate(minis, 1):
            links = []
            if mini.channel_id:
                links.append(f"<#{mini.channel_id}>")
            if hunt is not None and hunt.google_sheet_id and mini.google_page_id:
                sheet_url = urls.spreadsheet_url(
                    self.get_puzzle_sheet(None, mini, hunt), mini.google_page_id
                )
                links.append(f"[sheet]({sheet_url})")
            suffix = f" ({' · '.join(links)})" if links else ""
            if mini.solved:
                lines.append(f"{i}. ✅ **{mini.name}**{suffix} — {mini.solution}")
            else:
                lines.append(f"{i}. ⬜ **{mini.name}**{suffix} — unsolved")
        return "\n".join(lines)

    @commands.command()
    @with_puzzle_mutex(wait=True)
    async def solve_mini(self, ctx, number: int, *args):
        """*Record the answer for a numbered mini-puzzle in this minimeta.*

        Usage: `!solve_mini number answer`
        Example: `!solve_mini 2 BLUE WHALE`
        Run in the minimeta channel; get the number from `!list_minis`."""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        solution = " ".join(args).strip()
        if not solution:
            return await ctx.send(":x: Provide a solution: `!solve_mini 2 ANSWER`")
        try:
            await self.service.solve_mini_by_number(
                ctx.guild, self.get_hunt(ctx), puzzle.id, number, solution
            )
        except ValueError as exc:
            return await ctx.send(f":x: {exc}")
        await self.info(ctx, update=True)  # refresh the minimeta's pinned mini list
        await ctx.send(f":white_check_mark: Solved mini #{number}: **{solution}**")

    @commands.command()
    @with_puzzle_mutex(wait=True)
    async def unsolve_mini(self, ctx, number: int):
        """*Clear the recorded answer for a numbered mini-puzzle in this minimeta.*

        Usage: `!unsolve_mini number`
        Example: `!unsolve_mini 2`
        Run in the minimeta channel; get the number from `!list_minis`."""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        try:
            await self.service.unsolve_mini_by_number(
                ctx.guild, self.get_hunt(ctx), puzzle.id, number
            )
        except ValueError as exc:
            return await ctx.send(f":x: {exc}")
        await self.info(ctx, update=True)  # refresh the minimeta's pinned mini list
        await ctx.send(f":white_check_mark: Mini #{number} unmarked as solved.")

    @commands.hybrid_command(name="clue", description="Log a freeform clue/observation to the evidence sheet")
    async def clue(self, ctx, *, note: str):
        """*Log a freeform clue/observation from this puzzle to the evidence sheet.*

        Usage: `!clue text`
        Example: `!clue every answer starts with a chemical element`
        Run in a puzzle channel. For several at once use `!clues`."""
        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)
        if not note.strip():
            await ctx.send(":warning: No clue text provided.")
            return
        self.service.log_clues(hunt, puzzle, [note.strip()])
        asyncio.create_task(self.service.refresh_evidence(hunt))
        asyncio.create_task(self.service.refresh_informs_targets(hunt, puzzle))
        await ctx.send(f":pencil: Clue logged from **{puzzle.name}**.")

    @commands.hybrid_command(name="clues", description="Log several clues at once (quote any clue with spaces)")
    async def clues(self, ctx, *, items: str):
        """*Log several clues at once; quote any clue that contains spaces.*

        Usage: `!clues item1 item2 "multi word item" ...`
        Example: `!clues APPLE BANANA "CHERRY BOMB" DURIAN`
        Run in a puzzle channel. For a single freeform clue use `!clue`."""
        import shlex
        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)
        if not items.strip():
            await ctx.send(":warning: No items provided.")
            return
        notes = shlex.split(items)
        if not notes:
            await ctx.send(":warning: No items provided.")
            return
        self.service.log_clues(hunt, puzzle, notes)
        asyncio.create_task(self.service.refresh_evidence(hunt))
        asyncio.create_task(self.service.refresh_informs_targets(hunt, puzzle))
        await ctx.send(f":pencil: {len(notes)} clue{'s' if len(notes) != 1 else ''} logged from **{puzzle.name}**.")

    def create_additional_sheet_list(self, ctx=None, puzzle: PuzzleData = None, hunt: HuntData = None):
        sheet_links = ""
        count = 1
        spreadsheet_id = self.get_additional_sheet_spreadsheet(ctx, puzzle, hunt)
        for sheet in puzzle.additional_sheets:
            spreadsheet_url = urls.spreadsheet_url(spreadsheet_id,
                                                   sheet.google_page_id)
            sheet_links += f"{count}: [{sheet.puzzle_name}]({spreadsheet_url})\n"
            count+=1
        return sheet_links

    @commands.command(
        name="sync",
        description="Sync all puzzles in the category to the group (organiser only)",
        aliases=["sync_meta", "sync_feeders", "sync_round"],
    )
    @commands.has_any_role(*ORGANISER_ROLES)
    @with_puzzle_mutex(wait=True)
    async def sync(self, ctx):
        """*Tag every puzzle in this category to the category's group with its default edge.*

        Usage: `!sync`
        Example: `!sync`
        Run in a round/meta/group's main channel. Organisers/admins only.
        Aliases: `!sync_meta`, `!sync_feeders`, `!sync_round`."""
        if self.get_channel_type(ctx) not in GROUP_TYPES:
            await ctx.send(":x: This does not appear to be a main group channel")
            return
        hunt = self.get_hunt(ctx)
        if hunt and getattr(hunt, 'layout_mode', '') == 'forum':
            await ctx.send(":x: !sync is not available for forum hunts yet")
            return
        meta_category = ctx.channel.category
        target_group = TagGroupJsonDb.get_by_attr(category_id=meta_category.id)
        if not target_group:
            await ctx.send(":x: Could not find the group for this category.")
            return

        default_edge = TYPE_DEFAULTS.get(target_group.type, {}).get("default_edge", "feeds")

        updated = 0
        errors = []
        for channel in meta_category.channels:
            puz = PuzzleJsonDb.get_by_attr(channel_id=channel.id)
            if puz is None:
                continue
            if puz.id == target_group.target_puzzle_id:
                continue
            try:
                await self.service.tag_edge_only(puz, target_group, default_edge, guild=None)
                updated += 1
            except Exception as exc:
                errors.append((puz.name, str(exc)))

        hunt = self.get_hunt(ctx)
        if hunt is not None:
            asyncio.create_task(self.service.refresh_unassigned_safe(hunt))

        msg = f":white_check_mark: Synced {updated} puzzle(s) to {target_group.name}"
        if errors:
            msg += f" ({len(errors)} failure(s))"
        await ctx.send(msg)

    @commands.hybrid_command(
        name="feeds",
        description="Tag this puzzle as a feeder to a group",
        aliases=["add_to_meta", "add_to_round"],
    )
    @app_commands.autocomplete(meta_code=group_code_autocomplete)
    @with_puzzle_mutex(wait=True)
    async def feeds(self, ctx, meta_code):
        """*Tag this puzzle as a feeder of a group (adds a "feeds" edge), without moving it.*

        Usage: `!feeds meta_code`
        Example: `!feeds AB12CD`
        Run in the feeder puzzle's channel; `meta_code` is the target group's 6-char code.
        Use `!feeds_only` to also move the channel. Aliases: `!add_to_meta`, `!add_to_round`."""
        if self.get_channel_type(ctx) == "Hunt":
            await self.send_not_puzzle_channel(ctx)
            return
        puzzle = self.get_puzzle(ctx)
        target_group = await self.resolve_target_group(ctx, meta_code)
        if not target_group:
            return
        await self.service.tag_with_edge(puzzle, target_group, "feeds")
        asyncio.create_task(self.service.refresh_unassigned_safe(self.get_hunt(ctx)))
        await ctx.send(
            f":white_check_mark: This puzzle now feeds {target_group.name}. Meta sheets are being updated.")

    @commands.hybrid_command(
        name="feeds_only",
        description="Assign this puzzle as a feeder, removing other feeder assignments",
        aliases=["move_to_meta", "move_to_round"],
    )
    @app_commands.autocomplete(meta_code=group_code_autocomplete)
    @with_puzzle_mutex(wait=True)
    async def feeds_only(self, ctx, meta_code):
        """*Assign this puzzle as a feeder of a group, removing other feeder tags and moving its channel.*

        Usage: `!feeds_only meta_code`
        Example: `!feeds_only AB12CD`
        Run in the feeder puzzle's channel; `meta_code` is the target group's 6-char code.
        Use `!feeds` to add without moving. Aliases: `!move_to_meta`, `!move_to_round`."""
        if self.get_channel_type(ctx) == "Hunt":
            await self.send_not_puzzle_channel(ctx)
            return
        puzzle = self.get_puzzle(ctx)
        target_group = await self.resolve_target_group(ctx, meta_code)
        if not target_group:
            return
        await ctx.send(
            f":white_check_mark: This puzzle is now assigned to {target_group.name}. Meta sheets are being updated.")
        await self.service.tag_edge_only(puzzle, target_group, "feeds", self.get_guild(ctx))
        asyncio.create_task(self.service.refresh_unassigned_safe(self.get_hunt(ctx)))

    @commands.hybrid_command(
        name="remove_from",
        description="Remove the puzzle from a group",
        aliases=["remove_from_meta", "remove_from_round"],
    )
    @app_commands.autocomplete(meta_code=group_code_autocomplete)
    @with_puzzle_mutex(wait=True)
    async def remove_from(self, ctx, meta_code):
        """*Remove this puzzle from a group, deleting whatever edge tied it there.*

        Usage: `!remove_from meta_code`
        Example: `!remove_from AB12CD`
        Run in the puzzle's channel; `meta_code` is the group's 6-char code.
        Aliases: `!remove_from_meta`, `!remove_from_round`."""
        if self.get_channel_type(ctx) == "Hunt":
            await self.send_not_puzzle_channel(ctx)
            return
        puzzle = self.get_puzzle(ctx)
        target_group = await self.resolve_target_group(ctx, meta_code)
        if not target_group:
            return
        if puzzle.id == target_group.target_puzzle_id:
            await ctx.send(":x: You can't remove the target puzzle from its own group!")
            return
        await ctx.send(
            f":white_check_mark: This puzzle has been removed from {target_group.name}.")
        await self.service.untag_from(puzzle, target_group)
        asyncio.create_task(self.service.refresh_unassigned_safe(self.get_hunt(ctx)))

    @commands.hybrid_command(
        name="informs",
        description="Tag this puzzle as informing a group (capstone)",
    )
    @app_commands.autocomplete(meta_code=group_code_autocomplete)
    @with_puzzle_mutex(wait=True)
    async def informs(self, ctx, meta_code):
        """*Tag this puzzle as informing a group (adds an "informs" edge), without moving it.*

        Usage: `!informs meta_code`
        Example: `!informs AB12CD`
        Run in the puzzle's channel; `meta_code` is the target group's 6-char code.
        An informer contributes to the meta without being a standard feeder.
        Use `!informs_only` to also clear other informer tags."""
        if self.get_channel_type(ctx) == "Hunt":
            await self.send_not_puzzle_channel(ctx)
            return
        puzzle = self.get_puzzle(ctx)
        target_group = await self.resolve_target_group(ctx, meta_code)
        if not target_group:
            return
        await self.service.tag_with_edge(puzzle, target_group, "informs")
        asyncio.create_task(self.service.refresh_unassigned_safe(self.get_hunt(ctx)))
        await ctx.send(
            f":white_check_mark: This puzzle now informs {target_group.name}. Meta sheets are being updated.")

    @commands.hybrid_command(
        name="informs_only",
        description="Assign this puzzle as an informer, removing other informer assignments",
    )
    @app_commands.autocomplete(meta_code=group_code_autocomplete)
    @with_puzzle_mutex(wait=True)
    async def informs_only(self, ctx, meta_code):
        """*Assign this puzzle as an informer of a group, removing its other informer tags.*

        Usage: `!informs_only meta_code`
        Example: `!informs_only AB12CD`
        Run in the puzzle's channel; `meta_code` is the target group's 6-char code.
        Use `!informs` to add without clearing others."""
        if self.get_channel_type(ctx) == "Hunt":
            await self.send_not_puzzle_channel(ctx)
            return
        puzzle = self.get_puzzle(ctx)
        target_group = await self.resolve_target_group(ctx, meta_code)
        if not target_group:
            return
        await ctx.send(
            f":white_check_mark: This puzzle is now assigned to {target_group.name}. Meta sheets are being updated.")
        await self.service.tag_edge_only(puzzle, target_group, "informs", guild=None)
        asyncio.create_task(self.service.refresh_unassigned_safe(self.get_hunt(ctx)))

    @commands.hybrid_command(
        name="groups_with",
        description="Tag this puzzle into a group with 'none' edge (non-contributing)",
    )
    @app_commands.autocomplete(meta_code=group_code_autocomplete)
    @with_puzzle_mutex(wait=True)
    async def groups_with(self, ctx, meta_code):
        """*Add this puzzle to a group without contributing to its meta (a "none" edge).*

        Usage: `!groups_with meta_code`
        Example: `!groups_with AB12CD`
        Run in the puzzle's channel; `meta_code` is the target group's 6-char code.
        Use `!groups_only_with` to also clear other "none" tags."""
        if self.get_channel_type(ctx) == "Hunt":
            await self.send_not_puzzle_channel(ctx)
            return
        puzzle = self.get_puzzle(ctx)
        target_group = await self.resolve_target_group(ctx, meta_code)
        if not target_group:
            return
        await self.service.tag_with_edge(puzzle, target_group, "none")
        asyncio.create_task(self.service.refresh_unassigned_safe(self.get_hunt(ctx)))
        await ctx.send(
            f":white_check_mark: This puzzle is now in {target_group.name}.")

    @commands.hybrid_command(
        name="groups_only_with",
        description="Assign this puzzle to a group with 'none' edge, removing other none assignments",
    )
    @app_commands.autocomplete(meta_code=group_code_autocomplete)
    @with_puzzle_mutex(wait=True)
    async def groups_only_with(self, ctx, meta_code):
        """*Assign this puzzle to a group with a non-contributing "none" edge, removing its other "none" tags.*

        Usage: `!groups_only_with meta_code`
        Example: `!groups_only_with AB12CD`
        Run in the puzzle's channel; `meta_code` is the target group's 6-char code.
        Use `!groups_with` to add without clearing others."""
        if self.get_channel_type(ctx) == "Hunt":
            await self.send_not_puzzle_channel(ctx)
            return
        puzzle = self.get_puzzle(ctx)
        target_group = await self.resolve_target_group(ctx, meta_code)
        if not target_group:
            return
        await ctx.send(
            f":white_check_mark: This puzzle is now assigned to {target_group.name}.")
        await self.service.tag_edge_only(puzzle, target_group, "none", guild=None)
        asyncio.create_task(self.service.refresh_unassigned_safe(self.get_hunt(ctx)))

    async def _round_impl(self, ctx, round_name, command):
        """Round creation handler — delegates to PuzzleService.create_round.

        The command string determines round type:
          !r / !round       → Metapuzzle (creates meta puzzle named "name - meta")
          !mp / !metapuzzle → Metapuzzle (creates meta puzzle named "name")
          !capstone / !cs   → Capstone (creates meta puzzle)
          !runaround / !ra  → Runaround (creates meta puzzle, source_mode=subtree)
          !group            → Group (no meta puzzle)
        """
        from bot.core.notifier import DiscordNotifier

        command_key = command.casefold()

        # command_map: key → (group_type, create_meta_puzzle, meta_name_suffix)
        command_map = {
            "r": ("Metapuzzle", True, " - meta"),
            "round": ("Metapuzzle", True, " - meta"),
            "mp": ("Metapuzzle", True, None),
            "metapuzzle": ("Metapuzzle", True, None),
            "capstone": ("Capstone", True, None),
            "cs": ("Capstone", True, None),
            "runaround": ("Runaround", True, None),
            "ra": ("Runaround", True, None),
            "group": ("Group", False, None),
        }

        group_type, create_meta, suffix = command_map[command_key]
        hunt = self.get_hunt(ctx)

        # --- Delegate to service ---
        meta_puzzle_name = (
            round_name + suffix if suffix else round_name
        ) if create_meta else False

        try:
            notifier = DiscordNotifier(self.bot)
            new_round, puzzle = await self.service.create_tag_group(
                ctx.guild, hunt, round_name,
                notifier=notifier,
                meta_puzzle_name=meta_puzzle_name,
                round_type=group_type,
            )
            # Runaround post-create: set source_mode to subtree
            if group_type == "Runaround":
                await self.service.set_source_mode(hunt, new_round, "subtree")
            return new_round, puzzle
        except RuntimeError as exc:
            err_code = str(exc)
            if err_code == "round_exists":
                await ctx.send(
                    f":exclamation: **{group_type} {round_name}** already exists in this "
                    "hunt or will lead to a duplicate channel name, please use a "
                    "different name."
                )
            elif err_code == "puzzle_name_clash":
                await ctx.send(
                    f":exclamation: **{group_type} {round_name}** will cause a puzzle "
                    "name clash within this hunt, please use a different name."
                )
            elif err_code in ("no_category_space", "category_exists"):
                await ctx.send(
                    ":exclamation: Category creation failed — server may be full or "
                    "the category already exists."
                )
            return  # error already sent, stop further processing

    @commands.command(aliases=["r","mp","metapuzzle",
                               "capstone", "cs", "runaround", "ra", "group"])
    async def round(self, ctx, *, arg):
        """*Create a new round/group category; the alias you use picks the type.*

        Usage: `!round name` (or one of the type aliases below)
        Example: `!round the-bog`
        Run in the main Hunt channel. The created type depends on the alias:
        • `!r` / `!round name` — a round (also makes a "name - meta" meta puzzle)
        • `!mp` / `!metapuzzle name` — a metapuzzle
        • `!cs` / `!capstone name` — a capstone
        • `!ra` / `!runaround name` — a runaround
        • `!group name` — a group (no meta puzzle)"""

        if self.get_channel_type(ctx) != "Hunt":
            await ctx.channel.send(":x: This command must be done from the main Hunt channel")
            return

        if await self.check_available_space(ctx,5,1) is False:
            return await ctx.send(":x: There is not enough enough space on the server to create a new round, please inform the moderators.")

        command = ctx.invoked_with

        tag_group, puzzle = await self._round_impl(ctx, arg, command)

        if puzzle is None:
            await ctx.send(
                f":white_check_mark: I've created a new category for {self.get_hunt(ctx).name} - "
                f"{tag_group.name}"
            )
        else:
            await ctx.send(
                f":white_check_mark: I've created a new {puzzle.name} category and channel "
                f"for {self.get_hunt(ctx).name} - {tag_group.name}"
            )

    @commands.hybrid_command(description="Convert puzzle to metapuzzle", )
    @commands.has_any_role(*ADMIN_ROLES)
    @commands.has_permissions(manage_channels=True)
    async def convert_to_meta(self, ctx):
        """*Turn this feeder puzzle into a metapuzzle that others can be tagged to.*

        Usage: `!convert_to_meta`
        Example: `!convert_to_meta`
        Run in the puzzle's channel. Admins/mods only (needs Manage Channels)."""
        if ( puzzle := self.get_puzzle(ctx) ) is None:
            await ctx.channel.send(":x: This doesn't appear to be a puzzle.")
            return
        elif puzzle.is_target:
            await ctx.channel.send(":x: This puzzle is already a metapuzzle.")
            return

        try:
            tag_group, summary = await self.service.convert_to_type(
                ctx.guild, self.get_hunt(ctx), puzzle, "Metapuzzle"
            )
        except RuntimeError as e:
            await ctx.send(f":x: {e}")
            return
        self.set_tag_group(ctx, tag_group)
        self.set_channel_type(ctx, "Metapuzzle")
        await self.info(ctx, update=True)
        await ctx.send(
            f":white_check_mark: I've converted this puzzle into a metapuzzle within the category, other puzzles can now be tagged to it using its meta code {tag_group.group_code}."
        )

    @commands.hybrid_command(description="Convert puzzle to capstone", )
    @commands.has_any_role(*ADMIN_ROLES)
    @commands.has_permissions(manage_channels=True)
    async def convert_to_capstone(self, ctx):
        """*Convert this meta puzzle into a capstone (final meta).*

        Usage: `!convert_to_capstone`
        Example: `!convert_to_capstone`
        Run in a meta puzzle's channel. Admins/mods only (needs Manage Channels)."""
        if (puzzle := self.get_puzzle(ctx)) is None:
            await ctx.channel.send(":x: This doesn't appear to be a puzzle.")
            return
        elif not puzzle.is_target:
            await ctx.channel.send(":x: This puzzle is not a meta and cannot be converted to a capstone.")
            return

        try:
            tag_group, summary = await self.service.convert_to_type(
                ctx.guild, self.get_hunt(ctx), puzzle, "Capstone"
            )
        except RuntimeError as e:
            await ctx.send(f":x: {e}")
            return
        self.set_tag_group(ctx, tag_group)
        self.set_channel_type(ctx, "Capstone")
        await self.info(ctx, update=True)
        await ctx.send(
            f":white_check_mark: I've converted this puzzle into a capstone. {summary}"
        )

    @commands.hybrid_command(description="Convert puzzle to runaround", )
    @commands.has_any_role(*ADMIN_ROLES)
    @commands.has_permissions(manage_channels=True)
    async def convert_to_runaround(self, ctx):
        """*Convert this meta puzzle into a runaround.*

        Usage: `!convert_to_runaround`
        Example: `!convert_to_runaround`
        Run in a meta puzzle's channel. Admins/mods only (needs Manage Channels)."""
        if (puzzle := self.get_puzzle(ctx)) is None:
            await ctx.channel.send(":x: This doesn't appear to be a puzzle.")
            return
        elif not puzzle.is_target:
            await ctx.channel.send(":x: This puzzle is not a meta and cannot be converted to a runaround.")
            return

        try:
            tag_group, summary = await self.service.convert_to_type(
                ctx.guild, self.get_hunt(ctx), puzzle, "Runaround"
            )
        except RuntimeError as e:
            await ctx.send(f":x: {e}")
            return
        self.set_tag_group(ctx, tag_group)
        self.set_channel_type(ctx, "Runaround")
        await self.info(ctx, update=True)
        await ctx.send(
            f":white_check_mark: I've converted this puzzle into a runaround. {summary}"
        )

    @commands.hybrid_command(description="Convert meta puzzle to group", )
    @commands.has_any_role(*ADMIN_ROLES)
    @commands.has_permissions(manage_channels=True)
    async def convert_to_group(self, ctx):
        """*Convert this meta puzzle into a group (a category with no meta puzzle).*

        Usage: `!convert_to_group`
        Example: `!convert_to_group`
        Run in a meta puzzle's channel. Admins/mods only (needs Manage Channels)."""
        if (puzzle := self.get_puzzle(ctx)) is None:
            await ctx.channel.send(":x: This doesn't appear to be a puzzle.")
            return
        elif not puzzle.is_target:
            await ctx.channel.send(":x: This puzzle is not a meta and cannot be converted to a group.")
            return

        try:
            tag_group, summary = await self.service.convert_to_type(
                ctx.guild, self.get_hunt(ctx), puzzle, "Group"
            )
        except RuntimeError as e:
            await ctx.send(f":x: {e}")
            return
        self.set_tag_group(ctx, tag_group)
        self.set_channel_type(ctx, "Group")
        await self.info(ctx, update=True)
        await ctx.send(
            f":white_check_mark: I've converted this puzzle into a group. {summary}"
        )

    @commands.hybrid_command(description="Convert meta to a plain metapuzzle", )
    @commands.has_any_role(*ADMIN_ROLES)
    @commands.has_permissions(manage_channels=True)
    async def convert_to_metapuzzle(self, ctx):
        """*Convert this meta (capstone/runaround/group) back to a plain metapuzzle.*

        Usage: `!convert_to_metapuzzle`
        Example: `!convert_to_metapuzzle`
        Run in a meta puzzle's channel; feeders revert to the 'feeds' edge.
        Admins/mods only (needs Manage Channels)."""
        if (puzzle := self.get_puzzle(ctx)) is None:
            await ctx.channel.send(":x: This doesn't appear to be a puzzle.")
            return
        elif not puzzle.is_target:
            await ctx.channel.send(":x: This puzzle is not a meta. Use `!convert_to_meta` to promote a feeder.")
            return

        try:
            tag_group, summary = await self.service.convert_to_type(
                ctx.guild, self.get_hunt(ctx), puzzle, "Metapuzzle"
            )
        except RuntimeError as e:
            await ctx.send(f":x: {e}")
            return
        self.set_tag_group(ctx, tag_group)
        self.set_channel_type(ctx, "Metapuzzle")
        await self.info(ctx, update=True)
        await ctx.send(
            f":white_check_mark: I've converted this puzzle into a metapuzzle. {summary}"
        )

    @commands.hybrid_command(
        description="Convert metapuzzle back to feeder",
        aliases=["convert_to_feeder"],
    )
    @commands.has_any_role(*ADMIN_ROLES)
    @commands.has_permissions(manage_channels=True)
    async def convert_to_puzzle(self, ctx):
        """*Convert this meta puzzle back into an ordinary feeder puzzle.*

        Usage: `!convert_to_puzzle`
        Example: `!convert_to_puzzle`
        Run in a meta puzzle's channel; other puzzles can no longer be tagged to it.
        Admins/mods only (needs Manage Channels). Alias: `!convert_to_feeder`."""
        if (puzzle := self.get_puzzle(ctx)) is None:
            await ctx.channel.send(":x: This doesn't appear to be a puzzle.")
            return
        elif puzzle.is_target is False:
            await ctx.channel.send(":x: This puzzle is already a feeder.")
            return

        try:
            await self.service.convert_to_puzzle(ctx.guild, self.get_hunt(ctx), puzzle)
        except RuntimeError as e:
            await ctx.send(f":x: {e}")
            return
        # Channel rename intentionally omitted (see convert_to_meta): the dashboard
        # label supersedes the name prefix and renames hit the strict rename bucket.
        self.set_channel_type(ctx, "Puzzle")
        await self.info(ctx, update=True)
        await ctx.send(
            ":white_check_mark: I've converted this puzzle into a feeder, other puzzles can no longer be tagged to it."
        )

    def get_settings(self, ctx):
        data = None
        if self.get_channel_type(ctx) == "Puzzle":
            data = self.get_puzzle(ctx)
        elif self.get_channel_type(ctx) in GROUP_TYPES:
            data = self.get_tag_group(ctx)
        elif self.get_channel_type(ctx) == "Hunt":
            data = self.get_hunt(ctx)
        else:
            data = self.get_guild_data(ctx)
        return data

    def save_settings(self, ctx, settings):
        if self.get_channel_type(ctx) == "Puzzle":
            PuzzleJsonDb.commit(settings)
        elif self.get_channel_type(ctx) in GROUP_TYPES:
            TagGroupJsonDb.commit(settings)
        elif self.get_channel_type(ctx) == "Hunt":
            HuntJsonDb.commit(settings)
        else:
            GuildSettingsDb.commit(settings)

    @commands.hybrid_command(description="Show channel settings (admin only)",)
    @commands.has_any_role(*ADMIN_ROLES)
    @commands.has_permissions(manage_channels=True)
    async def show_settings(self, ctx):
        """*Dump the raw settings for this channel's object (puzzle/round/hunt/guild) as JSON.*

        Usage: `!show_settings`
        Example: `!show_settings`
        For debugging. Admins/mods only (needs Manage Channels)."""
        await ctx.send(f"```json\n{self.get_settings(ctx).to_json(indent=2)}```")

    @commands.hybrid_command(description="Show puzzle settings (admin only)",)
    @commands.has_any_role(*ADMIN_ROLES)
    @commands.has_permissions(manage_channels=True)
    async def show_puzzle_settings(self, ctx):
        """*Dump this puzzle's stored fields and values for debugging.*

        Usage: `!show_puzzle_settings`
        Example: `!show_puzzle_settings`
        Run in a puzzle channel. Admins/mods only (needs Manage Channels)."""
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
            await self.send_not_puzzle_channel(ctx)

    @commands.command()
    @commands.has_any_role(*ADMIN_ROLES)
    async def set_login(self, ctx, *, arg):
        """*Store the hunt's login username and password (shown on hunt info).*

        Usage: `!set_login username:password`
        Example: `!set_login team42:hunter2`
        Run in any channel belonging to the hunt. Admins/mods only."""
        if self.get_channel_type(ctx) == "Guild":
            await ctx.send(":x: This must be sent in a channel related to a hunt")
            return

        if ":" in arg:
            username, password = arg.split(":", 1)
            self.service.update_hunt_settings(self.get_hunt(ctx), username=username, password=password)
            return await ctx.send(
                f":white_check_mark: I've updated the login details for hunt {self.get_hunt(ctx).name}"
            )

        await ctx.send(f"Unable to parse details {arg}, try using `!set_login username:password`")

    @commands.command()
    @commands.has_any_role(*ADMIN_ROLES)
    async def set_start(self,ctx,timestamp:int):
        """*Set the hunt's start time from a UNIX timestamp (seconds since epoch).*

        Usage: `!set_start timestamp`
        Example: `!set_start 1767225600`
        Run in any channel belonging to the hunt. Admins/mods only."""
        if self.get_channel_type(ctx) == "Guild":
            await ctx.send(":x: This must be sent in a channel related to a hunt")
            return

        self.service.update_hunt_settings(self.get_hunt(ctx), start_timestamp=timestamp)
        return await ctx.send(
            f":white_check_mark: I've updated the start time for hunt {self.get_hunt(ctx).name}"
        )

    @commands.command(aliases=["update_setting","update_puzzle_setting","update_puzzle_settings"])
    @commands.has_any_role(*ADMIN_ROLES)
    @commands.has_permissions(manage_channels=True)
    async def update_settings(self, ctx, setting_key: str, setting_value: str):
        """*Set a single setting field on this channel's object (puzzle/round/hunt/guild).*

        Usage: `!update_settings key value`
        Example: `!update_settings discord_bot_emoji ":dog:"`
        Quote a value containing spaces. Inspect available keys with `!show_settings`.
        Use the `!update_puzzle_settings` alias to target the puzzle in this channel.
        Admins/mods only (needs Manage Channels)."""
        command = ctx.invoked_with
        if command not in ("update_puzzle_setting", "update_puzzle_settings"):
            settings = self.get_settings(ctx)
        elif self.get_puzzle(ctx):
            settings = self.get_puzzle(ctx)
        else:
            await self.send_not_puzzle_channel(ctx)
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
        """*List the puzzles in the current round with their type, status and solution.*

        Usage: `!list_puzzles`
        Example: `!list_puzzles`
        Run in a puzzle or round/meta channel. Alias: `!list`.
        For a whole-hunt overview, use the dashboard website."""
        all_puzzles = {}
        embed_title = ""
        if self.get_channel_type(ctx) in (*GROUP_TYPES, "Puzzle"):
            round_puzzles = PuzzleJsonDb.get_all_from_tag_group(self.get_tag_group(ctx).id)
            all_puzzles[self.get_tag_group(ctx).id] = {
                        'name': self.get_tag_group(ctx).name,
                        'puzzles': round_puzzles
                    }
            embed_title = f"Puzzles in Round {self.get_tag_group(ctx).name}"
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
        """Retrieve given channel by name/category or create one."""
        return await _channels.get_or_create_channel(guild, category, channel_name, channel_type, **kwargs)


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
        puzzle_links += f"""• [Overview Website]({self._overview_url(ctx, hunt, kwargs.get('guild_data'))})"""
        embed.add_field(
            name="Hunt Information",
            value=puzzle_links,
            inline=False,
        )
        embed.add_field(
                    name="Useful commands",
                    value="""The following are some useful commands:
        • `!r <round name>` : Create a new round with a metapuzzle channel
        • `!mp <metapuzzle name>` : Create a new metapuzzle
        • `!group <group name>` : Create a group without a metapuzzle channel
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

    def _add_puzzle_links_field(self, ctx, embed, puzzle, hunt, guild_data=None):
        spreadsheet_url = urls.spreadsheet_url(self.get_puzzle_sheet(ctx, puzzle, hunt),
                                               puzzle.google_page_id) if hunt.google_sheet_id else "?"
        puzzle_links = (f"""    • [Puzzle link]({puzzle.url or "?"})
                                • [Google Sheet]({spreadsheet_url})
                                """)
        puzzle_links += f"""• [Overview Website]({self._overview_url(ctx, hunt, guild_data)})"""
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
        • `!ps SOLUTION` will mark this puzzle as partially solved, multiple answers will be separated by slashes, mark as completed with !s SOLUTION with the final solution or just !s if all answers entered 
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
                • `!clue <text>` logs a clue/observation from this puzzle to the hunt's evidence sheet
                • `!clues item1 item2 "multi word item"` logs several clues at once (quote any with spaces)
                """,
            inline=False,
        )

    def _build_embed_list(self, ctx=None, embed: discord.Embed = None, puzzle: PuzzleData = None, hunt: HuntData = None):
        """Wrap the main embed plus an optional additional-sheets embed in a list."""
        embed_list = [embed]
        if len(puzzle.additional_sheets) > 0:
            sheets_embed = discord.Embed()
            sheets_embed.add_field(
                name="Additional Sheet Links",
                value=self.create_additional_sheet_list(ctx, puzzle, hunt),
                inline=False,
            )
            embed_list.append(sheets_embed)
        minis = self.service.get_minis(puzzle.id)
        if minis:
            minis_embed = discord.Embed()
            minis_embed.add_field(
                name="Mini-puzzles",
                value=self.create_mini_list(puzzle, minis, hunt),
                inline=False,
            )
            embed_list.append(minis_embed)
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
        tag_group = kwargs.get('tag_group', self.get_tag_group(ctx))
        hunt = kwargs.get('hunt', self.get_hunt(ctx))
        puzzle = kwargs.get('puzzle', self.get_puzzle(ctx))
        embed = discord.Embed(
            description=f"""Welcome to the meta channel for {tag_group.name}!"""
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
        • `!feeds_only {tag_group.group_code}` when used in another puzzle channel will add it to this meta as a feeder and move it to this category.
        • `!feeds {tag_group.group_code}` when used in another puzzle channel will add it to this meta as a feeder but not move it (e.g. if a puzzle feeds multiple metas).
        • `!informs {tag_group.group_code}` when used in another puzzle channel will tag it as informing this meta — it contributes to the meta without being a standard feeder (e.g. capstones / cross-references).
        • `!remove_from {tag_group.group_code}` when used in another puzzle channel will remove it from this meta.
        • `!sync` (admin only) when used in this channel will assign all the puzzles in the category to the meta.
        • `!rename_meta <puzzle-name>` (admin only) will rename the metapuzzle.
        """,
                        inline=False,
                    )

        self._add_metapuzzle_command_fields(embed)
        self._add_puzzle_links_field(ctx, embed, puzzle, hunt, kwargs.get('guild_data'))
        self._add_puzzle_status_fields(embed, puzzle)
        embed_list = self._build_embed_list(ctx=ctx, embed=embed, puzzle=puzzle, hunt=hunt)

        if kwargs.get("update", False):
            channel_pins = await channel.pins()
            return await channel_pins[-1].edit(embeds=embed_list)
        else:
            return await channel.send(embeds=embed_list)

    async def send_initial_group_channel_messages(self, ctx, channel: discord.TextChannel, **kwargs) -> discord.Message:
        """Send intro message on a group/round channel"""
        tag_group = kwargs.get('tag_group', self.get_tag_group(ctx))
        hunt = kwargs.get('hunt', self.get_hunt(ctx))
        embed = discord.Embed(
            description=f"""Welcome to the round channel for {tag_group.name}!"""
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
        • `!feeds_only {tag_group.group_code}` when used in another puzzle channel will add it to this round as a feeder and move it to this category.
        • `!feeds {tag_group.group_code}` when used in another puzzle channel will add it to this round as a feeder but not move it (e.g. if a puzzle feeds multiple rounds).
        • `!informs {tag_group.group_code}` when used in another puzzle channel will tag it as informing this round — it contributes to the round without being a standard feeder.
        • `!remove_from {tag_group.group_code}` when used in another puzzle channel will remove it from this round.
        • `!sync` (admin only) when used in this channel will assign all the puzzles in the category to the round.
        """,
                        inline=False,
                    )
        embed.add_field(name="Overview Website", value=self._overview_url(ctx, hunt, kwargs.get('guild_data')),
                        inline=False)
        if kwargs.get("update", False):
            channel_pins = await channel.pins()
            return await channel_pins[-1].edit(embed=embed)
        else:
            return await channel.send(embed=embed)

    async def send_initial_puzzle_channel_messages(self, ctx, channel: discord.TextChannel, **kwargs) -> discord.Message:
        """Send intro message on a puzzle channel"""
        puzzle = kwargs.get("puzzle", self.get_puzzle(ctx))
        hunt = kwargs.get('hunt', self.get_hunt(ctx))
        embed = discord.Embed(
            description=f"""Welcome to the puzzle channel for {puzzle.name} in {hunt.name}!"""
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
                • `!ps SOLUTION` will mark this puzzle as partially solved, multiple answers will be separated by slashes, mark as completed with !s SOLUTION with the final solution or just !s if all answers entered 
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
                • `!clue <text>` logs a clue/observation to the hunt's evidence sheet (`!clues` for several at once)
                • `!feeds <meta_code>` tags this puzzle as a feeder of a meta/round (code is in the meta's pinned message); `!informs <meta_code>` tags it as informing one without being a standard feeder
                • `!rename_puzzle <puzzle-name>` (admin only) will rename the puzzle.
                """,
            inline=False,
        )
        self._add_puzzle_links_field(ctx, embed, puzzle, hunt, kwargs.get('guild_data'))
        self._add_puzzle_status_fields(embed, puzzle)

        embed_list = self._build_embed_list(ctx=ctx, embed=embed, puzzle=puzzle, hunt=hunt)

        if kwargs.get("update",False):
            channel_pins = await channel.pins()
            if channel_pins:
                return await channel_pins[-1].edit(embeds=embed_list)
        else:
            return await channel.send(embeds=embed_list)

    async def send_not_puzzle_channel(self, ctx):
        category = ctx.channel.category if ctx.channel else None
        if category is not None and category.name.endswith(f"-{self.SOLVED_PUZZLES_CATEGORY}"):
            await ctx.send(":x: This puzzle appears to already be solved")
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

    def get_solved_puzzle_category(self, hunt_name):
        return f"{hunt_name}-{self.SOLVED_PUZZLES_CATEGORY}"

    @commands.command()
    async def info(self, ctx, **kwargs):
        """*Re-post (or refresh) this channel's pinned info message with links and command tips.*

        Usage: `!info`
        Example: `!info`
        Works in hunt, round, meta and puzzle channels; content adapts to the channel type."""
        update = kwargs.get("update", False)
        ch_type = self.get_channel_type(ctx)
        if ch_type == "Hunt":
            await self.send_initial_hunt_channel_messages(ctx, ctx.channel, update=update)
        elif ch_type in ("Metapuzzle", "Capstone", "Runaround"):
            await self.send_initial_metapuzzle_channel_messages(ctx, ctx.channel, update=update)
        elif ch_type == "Group":
            await self.send_initial_group_channel_messages(ctx, ctx.channel, update=update)
        elif ch_type == "Puzzle":
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

    @commands.hybrid_command(aliases=["rename_meta"], description="Rename this puzzle, its channel and its sheet tab")
    @commands.has_any_role(*ORGANISER_ROLES)
    @with_puzzle_mutex(wait=True)
    async def rename_puzzle(self, ctx, *, puzzle_name: str):
        """*Rename this puzzle, its channel and its sheet tab.*

        Usage: `!rename_puzzle new name`
        Example: `!rename_puzzle the actual clueless crossword`
        Run in the puzzle's channel. Organisers/admins only. Alias: `!rename_meta`."""
        puzzle = self.get_puzzle(ctx)
        if puzzle:
            if PuzzleJsonDb.check_duplicates_in_hunt(puzzle_name, self.get_hunt(ctx).id, exclude_puzzle_id=puzzle.id):
                return await ctx.send(
                    f":exclamation: Puzzle **{puzzle_name}** already exists in this hunt or will lead to a duplicate channel name, please use a different name")
            try:
                await self.service.rename_puzzle(
                    ctx.guild, self.get_hunt(ctx), puzzle, puzzle_name
                )
            except SheetRefreshError as exc:
                await self._warn_stale_sheets(ctx, exc)
                return
            await ctx.channel.send(":white_check_mark: I've updated the puzzle and channel names")
        else:
            await self.send_not_puzzle_channel(ctx)

    @commands.hybrid_command(aliases=["rename_round", "rename_metapuzzle"],
                             description="Rename this group, its category and channel")
    @commands.has_any_role(*ORGANISER_ROLES)
    async def rename_group(self, ctx, *, group_name: str):
        """*Rename this group and its Discord category/channel.*

        Usage: `!rename_group new name`
        Example: `!rename_group the wonderland round`
        Run from the group's target-puzzle channel, or from a regular group's
        channel. Organisers/admins only. Aliases: `!rename_round`,
        `!rename_metapuzzle`. Does NOT rename the target puzzle."""
        tag_group = self._resolve_group_for_rename(ctx)
        if tag_group is None:
            await ctx.send(
                ":x: Run this from a group's target-puzzle channel, or from a "
                "regular group's channel.")
            return
        if TagGroupJsonDb.check_duplicates_in_hunt(group_name, self.get_hunt(ctx)):
            await ctx.send(
                f":exclamation: A group called **{group_name}** already exists in "
                f"this hunt, please use a different name")
            return
        await self.service.rename_group(
            ctx.guild, self.get_hunt(ctx), tag_group, group_name)
        await ctx.channel.send(":white_check_mark: I've renamed the group.")

    @commands.hybrid_command(description="Set or update the puzzle's URL on the hunt site")
    @with_puzzle_mutex(wait=True)
    async def link(self, ctx, *, url: str):
        """*Set or update the puzzle's URL (the link to the puzzle on the hunt site).*

        Usage: `!link url`
        Example: `!link https://hunt.example.com/puzzles/crossword`
        Run in the puzzle's channel."""
        if self.get_puzzle(ctx):
            puzzle = self.get_puzzle(ctx)
            hunt = self.get_hunt(ctx)
            await self.service.set_puzzle_url(ctx.guild, hunt, puzzle, url)
            await self.send_state(
                ctx, ctx.channel, puzzle, description=":white_check_mark: I've updated:" if url else None
            )
        else:
            await self.send_not_puzzle_channel(ctx)

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
        """*Set this puzzle's working status.*

        Usage: `!status value`
        Example: `!status stuck`
        One of: Unstarted, In Progress, Stuck, Needs Extraction, Solved, Backsolved.
        Run in the puzzle's channel."""
        puzzle = self.get_puzzle(ctx)
        if puzzle is None:
            await self.send_not_puzzle_channel(ctx)
            return
        hunt = self.get_hunt(ctx)
        try:
            await self.service.update_puzzle_field(hunt, puzzle, "status", status)
        except ValueError:
            await ctx.send(f":exclamation: Status should be one of {self.STATUSES}, got \"{status}\"")
            return
        await self.send_state(ctx, ctx.channel, puzzle, description=":white_check_mark: I've updated:" if status else None)

    @commands.hybrid_command(description="Set this puzzle's type label (e.g. crossword, logic, cryptic)")
    @app_commands.autocomplete(puzzle_type=puzzle_type_autocomplete)
    @with_puzzle_mutex(wait=True)
    async def type(self, ctx, *, puzzle_type: str):
        """*Set this puzzle's type label (freeform, e.g. crossword, logic, cryptic).*

        Usage: `!type puzzle_type`
        Example: `!type crossword`
        Run in the puzzle's channel."""
        puzzle = self.get_puzzle(ctx)
        if puzzle is None:
            await self.send_not_puzzle_channel(ctx)
            return
        hunt = self.get_hunt(ctx)
        await self.service.update_puzzle_field(hunt, puzzle, "puzzle_type", puzzle_type)
        await self.send_state(ctx, ctx.channel, puzzle, description=":white_check_mark: I've updated:" if puzzle_type else None)

    @commands.hybrid_command(description="Update puzzle priority")
    @app_commands.choices(priority=[
        app_commands.Choice(name="Low", value="low"),
        app_commands.Choice(name="Medium", value="medium"),
        app_commands.Choice(name="High", value="high"),
        app_commands.Choice(name="Very High", value="very high"),
    ])
    @with_puzzle_mutex(wait=True)
    async def priority(self, ctx, *, priority: str):
        """*Set this puzzle's priority.*

        Usage: `!priority value`
        Example: `!priority high`
        One of: low, medium, high, very high. Run in the puzzle's channel."""
        puzzle = self.get_puzzle(ctx)
        if puzzle is None:
            await self.send_not_puzzle_channel(ctx)
            return
        hunt = self.get_hunt(ctx)
        try:
            await self.service.update_puzzle_field(hunt, puzzle, "priority", priority)
        except ValueError:
            await ctx.send(f":exclamation: Priority should be one of {self.PRIORITIES}, got \"{priority}\"")
            return
        await self.send_state(ctx, ctx.channel, puzzle, description=":white_check_mark: I've updated:" if priority else None)

    @commands.hybrid_command(aliases=["notes"], description="Show this puzzle's notes, or add a note if you pass text")
    @with_puzzle_mutex(wait=True)
    async def note(self, ctx, *, note: Optional[str]):
        """*Show this puzzle's notes, or add a note if you pass text.*

        Usage: `!note [text]`
        Example: `!note try anagramming the first letters`
        With no text, lists existing notes. Run in the puzzle's channel.
        Alias: `!notes`. Remove a note with `!erase_note`."""
        if self.get_puzzle(ctx) is None:
            await self.send_not_puzzle_channel(ctx)
            return

        message = "Showing notes left by users!"
        if note:
            hunt = self.get_hunt(ctx)
            # Slash invocations have no ctx.message (so no jump_url); attribute to the
            # author's display name instead, matching the web surface's attribution style.
            attribution = ctx.message.jump_url if ctx.message else ctx.author.display_name
            await self.service.add_note(hunt, self.get_puzzle(ctx), note, attribution)
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

    @commands.hybrid_command(aliases=["delete_note"], description="Remove one of this puzzle's notes by its number")
    @with_puzzle_mutex(wait=True)
    async def erase_note(self, ctx, note_index: int):
        """*Remove one of this puzzle's notes by its number.*

        Usage: `!erase_note note_index`
        Example: `!erase_note 1`
        Get the number from `!notes`. Run in the puzzle's channel. Alias: `!delete_note`."""

        if self.get_puzzle(ctx) is None:
            await self.send_not_puzzle_channel(ctx)
            return

        hunt = self.get_hunt(ctx)
        erased = await self.service.erase_note(hunt, self.get_puzzle(ctx), note_index)
        description = f"Erased note {note_index}" if erased else f"Unable to find note {note_index}"

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
        # Joins args and strips whitespace; the service normalizes case to uppercase.
        return " ".join(args).strip()

    async def _update_all_metas(self, ctx, puzzle):
        """Refresh every metapuzzle this puzzle is tagged to, on the SAME isolation
        path as solve/retag: each meta is attempted and badged independently via
        service.refresh_metas, and a SheetRefreshError is raised if any failed so the
        bang edge can warn (via _warn_stale_sheets) instead of aborting mid-loop."""
        hunt = self.get_hunt(ctx)
        tag_groups = [TagGroupJsonDb.get_by_attr(id=tag) for tag in puzzle.tags]
        failed = await self.service.refresh_targets(hunt, tag_groups)
        if failed:
            raise SheetRefreshError(failed)

    async def _warn_stale_sheets(self, ctx, exc):
        """Uniform surfacing of a SheetRefreshError from a bang edge: the mutation is
        saved and the affected meta sheet(s) are badged on the dashboard."""
        await ctx.send(f":warning: {exc}")

    def _overview_url(self, ctx=None, hunt: HuntData = None, guild_data: GuildSettingsDb = None):
        """Overview website URL for a hunt (falls back to the numeric id)."""
        if guild_data is None:
            guild_data = self.get_guild_data(ctx)
        base = (getattr(guild_data, "website_url", "") or "").rstrip("/")
        return f"{base}/hunts/{hunt.uid or hunt.id}"

    async def _partial_impl(self, ctx, *args):
        solution = self._normalize_solution(args)
        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)

        sheet_warning = None
        try:
            await self.service.add_partial(self.get_guild(ctx), hunt, puzzle, solution)
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

    @commands.hybrid_command(aliases=["ps"], description="Record a partial solution without marking the puzzle solved")
    @with_puzzle_mutex(wait=False)
    async def partial(self, ctx, *, answer: str = ""):
        """*Record a partial solution without marking the puzzle solved.*

        Usage: `!partial partial answer`
        Example: `!partial BLUE`
        Repeated use appends, joining answers with a slash. Run in an unsolved puzzle channel.
        Alias: `!ps`. Clear partials with `!unsolve`."""

        if not self.get_puzzle(ctx):
            await self.send_not_puzzle_channel(ctx)
            return
        elif self.get_puzzle(ctx).solved:
            await ctx.send(":x: This puzzle appears to already be solved")
            return

        await self._partial_impl(ctx, *answer.split())


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
        asyncio.create_task(self.service.refresh_unassigned_safe(hunt))

    @commands.hybrid_command(aliases=["s"], description="Mark this puzzle solved, record the answer and move it to solved")
    @with_puzzle_mutex(wait=False)
    async def solve(self, ctx, *, solution: str = ""):
        """*Mark this puzzle fully solved, record the answer and move it to the solved section.*

        Usage: `!solve answer`
        Example: `!solve BLUE WHALE`
        Run in an unsolved puzzle channel. Alias: `!s`.
        Fix a typo with `!change_solution`; undo with `!unsolve`."""

        if not self.get_puzzle(ctx):
            await self.send_not_puzzle_channel(ctx)
            return
        elif self.get_puzzle(ctx).solved:
            await ctx.send(":x: This puzzle appears to already be solved")
            return
        await self._solve_impl(ctx, *solution.split())


    @commands.hybrid_command(aliases=["update_solution","update_answer"], description="Replace this puzzle's recorded solution (e.g. fix a typo)")
    @with_puzzle_mutex(wait=False)
    async def change_solution(self, ctx, *, answer: str = ""):
        """*Replace this puzzle's recorded solution (e.g. to fix a typo).*

        Usage: `!change_solution answer`
        Example: `!change_solution BLUE WHALE`
        If the puzzle wasn't solved yet it solves it. Run in the puzzle's channel.
        Aliases: `!update_solution`, `!update_answer`."""
        if not self.get_puzzle(ctx):
            await self.send_not_puzzle_channel(ctx)
            return
        elif not self.get_puzzle(ctx).solved:
            await self._solve_impl(ctx, *answer.split())
            return

        solution = self._normalize_solution(answer.split())

        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)

        if solution == "":
            await ctx.send(":x: Nice try, but you need to give a solution!")
            return

        sheet_warning = None
        try:
            await self.service.change_solution(self.get_guild(ctx), hunt, puzzle, solution)
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
        await self.info(ctx, update=True)
        if sheet_warning:
            await self._warn_stale_sheets(ctx, sheet_warning)

    @commands.hybrid_command(aliases=["add_to_solution"], description="Add an extra solution to an already-solved puzzle")
    @with_puzzle_mutex(wait=False)
    async def add_solution(self, ctx, *, answer: str = ""):
        """*Add an extra solution to an already-solved puzzle (e.g. a second valid answer).*

        Usage: `!add_solution answer`
        Example: `!add_solution GREY WHALE`
        If the puzzle isn't solved yet this records a partial instead. Run in the puzzle's channel.
        Alias: `!add_to_solution`."""
        if not self.get_puzzle(ctx):
            await self.send_not_puzzle_channel(ctx)
            return
        elif not self.get_puzzle(ctx).solved:
            await self._partial_impl(ctx, *answer.split())
            return

        solution = self._normalize_solution(answer.split())


        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)

        if solution == "":
            await ctx.send(":x: Nice try, but you need to give a solution!")
            return

        sheet_warning = None
        try:
            await self.service.add_solution(self.get_guild(ctx), hunt, puzzle, solution)
        except SheetRefreshError as exc:
            sheet_warning = exc

        emoji = self.get_guild_data(ctx).discord_bot_emoji
        embed = discord.Embed(title="EXTRA PUZZLE SOLUTION ADDED!",
                              description=f"{emoji} :partying_face: Great work! I've updated the solution to `{puzzle.solution}`")
        await ctx.send(embed=embed)
        await self.info(ctx, update=True)
        if sheet_warning:
            await self._warn_stale_sheets(ctx, sheet_warning)

    @commands.hybrid_command(name="mark_as_complete", aliases=["mark_as_solved","complete"], description="Mark this puzzle solved with a tick when there's no text answer")
    @with_puzzle_mutex(wait=False)
    async def mark_as_complete(self, ctx):
        """*Mark this puzzle solved with a tick (✅) when there's no text answer to record.*

        Usage: `!mark_as_complete`
        Example: `!mark_as_complete`
        Run in an unsolved puzzle channel. Aliases: `!mark_as_solved`, `!complete`."""
        await self._solve_impl(ctx, "✅")

    @commands.hybrid_command(aliases=["u"], description="Undo a solve: clear the solution and move back to unsolved")
    @with_puzzle_mutex(wait=False)
    async def unsolve(self, ctx):
        """*Undo a solve: clear the solution and move the channel back to unsolved.*

        Usage: `!unsolve`
        Example: `!unsolve`
        Run in a solved puzzle's channel. Also clears partial solutions. Alias: `!u`."""

        if not self.get_puzzle(ctx):
            await self.send_not_puzzle_channel(ctx)
            return
        elif not self.get_puzzle(ctx).solved and not self.get_puzzle(ctx).solution:
            await ctx.send(":x: This puzzle appears to not be solved")
            return

        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)
        prev_solution = puzzle.solution

        emoji = self.get_guild_data(ctx).discord_bot_emoji
        embed = discord.Embed(
            description=f"{emoji} Alright, I've unmarked {prev_solution} as the solution. "
            "You'll get 'em next time!  \nGive me a sec to restore the sheets."
        )
        await ctx.send(embed=embed)

        sheet_warning = None
        try:
            await self.service.unsolve(self.get_guild(ctx), hunt, puzzle)
        except SheetRefreshError as exc:
            sheet_warning = exc

        await self.move_to_bottom(ctx)
        await self.info(ctx, update=True)
        if sheet_warning:
            await self._warn_stale_sheets(ctx, sheet_warning)
        else:
            await ctx.send(":white_check_mark: Sheets all restored.")

    @commands.command()
    @commands.has_any_role(*ADMIN_ROLES)
    async def archive_round(self, ctx):
        """*Archive this round: copy every channel in the category into threads, then delete the category.*

        Usage: `!archive_round`
        Example: `!archive_round`
        Run in a channel of the round to archive. Admins/mods only."""
        hunt = self.get_hunt(ctx)
        if hunt and getattr(hunt, 'layout_mode', '') == 'forum':
            await ctx.send(":x: !archive_round is not available for forum hunts yet")
            return False
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
        """*Archive every channel in this category into threads on the hunt's general channel.*

        Usage: `!archive_category`
        Example: `!archive_category`
        Run in a channel of the category to archive; defaults to archiving into the hunt
        general channel. Admins/mods only. Usually called via `!archive_round`."""
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
        """*Archive this channel's messages into a thread, then delete the channel.*

        Usage: `!archive_channel`
        Example: `!archive_channel`
        Run in the channel to archive. The destination needs a webhook. Admins/mods only.
        Usually called via `!archive_round` / `!archive_category`."""
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
            tag_group = self.get_tag_group(ctx)
            if tag_group:
                round_name = tag_group.name
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
        """*Tidy the hunt's "solved" category: move busy solved channels back (prefixed "solved-") and delete quiet ones.*

        Usage: `!archive_solved_manually`
        Example: `!archive_solved_manually`
        Run in a channel of the hunt. Admins/mods only."""
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
                await self.service.safe_rename(channel, "solved-" + channel.name)
            else:
                await channel.delete(reason=self.DELETE_REASON)
        hunt_general_channel = self.get_hunt_channel(ctx)
        await solved_category.delete(reason=self.DELETE_REASON)
        await hunt_general_channel.send(":white_check_mark: Solved puzzles successfully archived.")

    @commands.command()
    @commands.has_any_role(*ORGANISER_ROLES)
    @with_puzzle_mutex(wait=False)
    async def delete_puzzle(self, ctx):
        """*Permanently delete this puzzle: its channel and its Google sheet (asks to confirm).*

        Usage: `!delete_puzzle`
        Example: `!delete_puzzle`
        Run in the puzzle's channel. Organisers/admins only. Not recoverable."""
        # Wrapper for delete command
        if self.get_channel_type(ctx) != "Puzzle":
            await self.send_not_puzzle_channel(ctx)
            return

        if not await self._confirm_destructive(ctx, f"Delete puzzle **{self.get_puzzle(ctx).name}**, its channel and its sheet?"):
            return

        puzzle = self.get_puzzle(ctx)
        hunt_general_channel = self.bot.get_channel(self.get_hunt(ctx).channel_id)
        try:
            await self.service.delete_puzzle(ctx.guild, self.get_hunt(ctx), puzzle)
            await hunt_general_channel.send(
                f":white_check_mark: Puzzle {puzzle.name} successfully deleted and sheet cleaned up.")
        except ValueError as exc:
            # Target puzzles can't be deleted directly (would orphan the tag group).
            await ctx.send(f":x: {exc}")
        except SheetRefreshError as exc:
            await hunt_general_channel.send(f":warning: Puzzle {puzzle.name} deleted. {exc}")
        except Exception:
            logger.exception("Puzzle deletion failed")
            await ctx.send(":x: Channel deletion failed")
        self.set_puzzle(ctx, None)

    async def delete_puzzle_data(self, ctx, puzzle: PuzzleData, delete_sheet=True):
        if delete_sheet is True:
            if self.get_gsheet_cog(ctx) is not None:
                await self.get_gsheet_cog(ctx).delete_puzzle_spreadsheet(puzzle)
        PuzzleJsonDb.delete(puzzle.id)
        return True

    async def _warn_failed_channels(self, channel, failed):
        """Notify `channel` about puzzles whose channels could not be deleted."""
        if failed:
            await channel.send(
                ":warning: Could not delete channels for: " + ", ".join(failed)
                + ". They may already be gone or were rate-limited — re-run to finish."
            )

    @commands.command(aliases=['delete_metapuzzle','delete_group'])
    @commands.has_any_role(*ORGANISER_ROLES)
    async def delete_round(self, ctx):
        """*Permanently delete this round/group: all its puzzles, channels and sheets (asks to confirm).*

        Usage: `!delete_round`
        Example: `!delete_round`
        Run in the round/group's main channel. Organisers/admins only. Not recoverable.
        Aliases: `!delete_metapuzzle`, `!delete_group`."""
        if self.get_channel_type(ctx) not in GROUP_TYPES:
            await ctx.send(":x: This command must be done from the main group channel")
            return

        tag_group = self.get_tag_group(ctx)
        n = len(PuzzleJsonDb.get_all_from_tag_group(tag_group.id))
        if not await self._confirm_destructive(ctx, f"Delete round **{tag_group.name}** and its {n} puzzle(s), channels and sheets?"):
            return

        hunt_general_channel = self.bot.get_channel(self.get_hunt(ctx).channel_id)

        failed = await self.service.delete_round(ctx.guild, self.get_hunt(ctx), tag_group)

        await hunt_general_channel.send(
            f":white_check_mark: Round {tag_group.name} successfully deleted.  "
            f"All puzzle sheets have been deleted.")
        await self._warn_failed_channels(hunt_general_channel, failed)
        self.set_puzzle(ctx, None)
        self.set_tag_group(ctx, None)

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
        """*Permanently delete the ENTIRE hunt: every round, puzzle, channel and sheet (asks to confirm).*

        Usage: `!delete_hunt`
        Example: `!delete_hunt`
        Run in the main Hunt channel. Admins/mods only. Not recoverable."""
        if self.get_channel_type(ctx) == "Hunt":
            hunt_channel = ctx.channel
        else:
            await ctx.send(":x: This command must be done from the main hunt channel")
            return
        hunt = self.get_hunt(ctx)
        n = len(PuzzleJsonDb.get_all_from_hunt(hunt.id))
        if not await self._confirm_destructive(ctx, f"Delete the ENTIRE hunt **{hunt.name}**: {n} puzzle(s), all rounds, channels and sheets?"):
            return
        failed = await self.service.delete_hunt(ctx.guild, hunt)
        await self._warn_failed_channels(hunt_channel, failed)
        self.set_puzzle(ctx, None)
        self.set_tag_group(ctx, None)
        self.set_hunt(ctx, None)
        return True

    @commands.command()
    @commands.has_any_role(*ADMIN_ROLES)
    async def debug_puzzle_channel(self, ctx):
        """*Dump this puzzle's full stored record as JSON (debugging).*

        Usage: `!debug_puzzle_channel`
        Example: `!debug_puzzle_channel`
        Run in a puzzle channel. Admins/mods only."""

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
        """*Start the periodic reminder loop that posts tips in the hunt channel.*

        Usage: `!start_reminders`
        Example: `!start_reminders`
        Run in a channel of the hunt. Organisers/admins only. Stop it with `!stop_reminders`."""
        channel = self.get_hunt_channel(ctx)
        self.reminder_index = 0
        self.reminder_loop.start(channel)
        await ctx.send(":white_check_mark: Reminder loop started.")

    @commands.hybrid_command(description="Stop the reminder loop (organisers only)",)
    @commands.has_any_role(*ORGANISER_ROLES)
    async def stop_reminders(self, ctx):
        """*Stop the periodic reminder loop.*

        Usage: `!stop_reminders`
        Example: `!stop_reminders`
        Organisers/admins only. Start it with `!start_reminders`."""
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
