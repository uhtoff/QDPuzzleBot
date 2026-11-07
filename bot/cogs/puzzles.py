import functools
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
from bot.utils import config
from bot.store import PuzzleData, PuzzleJsonDb, GuildSettings, GuildSettingsDb, \
    TagGroupJsonDb, HuntData, HuntJsonDb, TagGroupCategoryDb, HuntCategoryDb, \
    resolve_group, MediaJsonDb, CanvasJsonDb, MultiTeamDb
from bot.store.puzzle_data import AdditionalSheetData
from bot.utils.chunking import (
    FIELD_NAME_LIMIT,
    MAX_FIELDS,
    build_note_embeds,
    split_lines_for_embed,
)
from bot.utils.components import (
    build_plain_messages, build_sectioned_views, send_plain, send_views,
)
from bot.utils.text import flush_left
from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLES, ORGANISER_ROLE_NAMES, STATUSES, PRIORITIES, format_status, format_priority
from bot.core import hub_channel, role_gate, template_link
from bot.core.media_ingest import ingest_image_bytes
from bot.core.media_store import MediaRejected
from bot.core.media_tiling import schedule_tiling
from bot.core.sheet_location import (additional_sheet_location, drive_folder_link,
                                      hunt_sheet_location, puzzle_location, spreadsheet_link)

# Alias for the ctx-free channel adapter (M3.5).
import bot.core.discord_channels as _channels
from bot.core.hub_archive_run import archive_summary as _archive_summary, run_archive_to_hub
from bot.core import category_layout, hunt_archive, media_gc, mod_invite, multi_team, role_panel, server_setup, team_invites

logger = logging.getLogger(__name__)

# Per-puzzle async mutex — shared with the web edge via bot/core/locks so a
# dashboard write serializes against the matching bang-command (see M4).
from bot.core.locks import (_PUZZLE_LOCKS, puzzles_lock, convert_lock_ids,
                            hunt_create_lock, hunt_sheet_lock)
from bot.core.errors import SheetRefreshError
from bot.core.puzzle_service import TYPE_DEFAULTS, GROUP_TYPES
from bot.cogs.ui import ConfirmView

# Settings columns !update_settings refuses to write, mapped to what to do
# instead (M4). This command is a raw escape hatch: it setattrs whatever string
# passes its type gate and commits, so it would otherwise be a third write path
# to these columns with none of the checks the two dedicated edges carry.
#   - The auth columns are validated against the guild's own roles, and guarded
#     against pinning ids the caller does not hold, by !set_admin_roles /
#     !set_organiser_roles and by the dashboard's guild-settings PATCH. Stored
#     ids are the ONLY grant for a tier (bot/core/role_gate.py — no fallback to
#     role names once configured), so junk written here gates every admin out of
#     both surfaces, including out of the commands that would undo it.
#   - The identity columns key the guild row the whole auth model resolves
#     through; rewriting them orphans the settings from the guild.
# Recovery from either is direct SQL, so this path refuses them outright.
PROTECTED_SETTING_KEYS = {
    "admin_role_ids":
        "Use `!set_admin_roles`, which checks the roles exist in this guild and "
        "refuses a change that would lock you out.",
    "organiser_role_ids":
        "Use `!set_organiser_roles`, which checks the roles exist in this guild.",
    "setup_complete_time":
        "This is !setup_server's run-once lock; it is set when setup completes.",
    "guild_id":
        "This is the key every setting for this guild is stored under; changing "
        "it would orphan them.",
    "guild_name":
        "This is filled in from Discord when the guild's settings row is created.",
    "template_link":
        "Use !set_template_link, which validates it.",
}


def _holds(ctx, tier):
    """True if the command author holds *tier* in this guild, per role_gate:
    the guild's configured role ids where it has opted in, the ids of the
    name-matched roles where it has not (M8). Never a name comparison here — a
    member with Manage Roles can mint a role called `admin`."""
    return role_gate.member_holds_tier(
        {r.id for r in ctx.author.roles}, ctx.guild, tier)


def is_admin():
    """Like commands.has_any_role(*ADMIN_ROLES) but resolved through role_gate.
    The Admin tier is never bypassed. Mirrors has_any_role's NoPrivateMessage/
    MissingAnyRole behaviour for parity outside a guild / when denied."""
    def predicate(ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if _holds(ctx, ADMIN_ROLES):
            return True
        raise commands.MissingAnyRole(list(ADMIN_ROLES))
    return commands.check(predicate)


def is_organiser():
    """Like is_admin() for the wider Organiser tier, except the check passes
    unconditionally when the guild has nothing standing for that tier — see
    role_gate.organiser_gate_active."""
    def predicate(ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not role_gate.organiser_gate_active(ctx.guild):
            return True
        if _holds(ctx, ORGANISER_ROLES):
            return True
        raise commands.MissingAnyRole(list(ORGANISER_ROLES))
    return commands.check(predicate)


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
                    self._refresh_environment(ctx, puzzle.id)
                    return await func(self, ctx, *args, **kwargs)

            try:
                await asyncio.wait_for(lock.acquire(), timeout=0.001)
            except asyncio.TimeoutError:
                await ctx.send("⏳ Woah there, I'm busy running another command on that puzzle. Patience is a virtue, try again later.")
                return

            try:
                self._refresh_environment(ctx, puzzle.id)
                return await func(self, ctx, *args, **kwargs)
            finally:
                lock.release()

        return wrapper
    return decorator


SHEET_MODE_REFUSALS = {
    "sheet_mode_locked": "The sheet mode can only be changed while the hunt has no puzzles or groups",
    "files_mode_no_solved_sheet": "This hunt uses one spreadsheet per puzzle, so it has no solved puzzles sheet",
    "gsheet_unavailable": "Google Sheets is not available right now",
    "bad_sheet_mode": "Sheet mode must be `tabs` or `files`",
}


def with_hunt_sheet_mutex():
    """Serialize a sheet command that has NO puzzle in context, on its hunt.

    with_puzzle_mutex no-ops without a puzzle ("If not in a puzzle context, just
    run"), so a hunt-level !add_sheet would otherwise run unserialised and two
    people running it at once would race on the Sheets API.

    Stacked BENEATH with_puzzle_mutex on the sheet commands, and deliberately
    inert when there IS a puzzle: exactly one lock is taken either way, so the
    two decorators never both hold and there is no lock-ordering cycle.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(self, ctx: commands.Context, *args, **kwargs):
            # get_puzzle/get_hunt rather than with_puzzle_mutex's
            # `getattr(ctx, "env", ...)`: nothing in the bot ever sets ctx.env, so
            # that branch is vestigial there, and the cog's own accessors read the
            # same environment cog_before_invoke populated.
            if self.get_puzzle(ctx) is not None:
                return await func(self, ctx, *args, **kwargs)
            hunt = self.get_hunt(ctx)
            if hunt is None:
                # No hunt resolved -- the command will refuse anyway; nothing to
                # serialise on.
                return await func(self, ctx, *args, **kwargs)
            async with hunt_sheet_lock(hunt.id):
                return await func(self, ctx, *args, **kwargs)
        return wrapper
    return decorator


class Puzzles(commands.Cog):
    MAX_CHANNELS_PER_CATEGORY = _channels.MAX_CHANNELS_PER_CATEGORY
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
        self.environment = {}

    def _refresh_environment(self, ctx, puzzle_id):
        """Re-read the command's puzzle/group/hunt from the database, now that the
        per-puzzle lock is held.

        cog_before_invoke resolves which puzzle a channel maps to -- which is how we
        know which lock to take -- but its snapshot is read BEFORE the lock. Services
        commit the object they are given, field by field, so acting on that snapshot
        clobbers whatever a concurrent bang or web write changed while we were waiting
        (H4). Re-reading here gives the bang edge the same lock-then-read shape the web
        handlers already have (bot/web/actions.py).

        Deliberately scoped to the three DB-backed objects. guild_data is not
        refreshed, and neither are the sheet ids cog_before_invoke pushed into the
        sheets cog from the pre-lock hunt -- a concurrent !use_solved_sheet could
        leave those stale. That command is hunt-level and takes no puzzle lock, so
        this lock could not serialise it anyway.
        """
        env = self.environment.get(ctx.message.id)
        if env is None:
            return
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        if puzzle is None:
            # Deleted while we waited for the lock. Leave the stale object in place;
            # the command's own not-found handling deals with it, and T2's rowcount
            # log will record any write that lands on the missing row.
            return
        env["puzzle"] = puzzle
        if env.get("tag_group") is not None:
            refreshed_group = TagGroupJsonDb.get_by_attr(id=env["tag_group"].id)
            if refreshed_group is not None:
                env["tag_group"] = refreshed_group
        if env.get("hunt") is not None:
            refreshed_hunt = HuntJsonDb.get_by_attr(id=env["hunt"].id)
            if refreshed_hunt is not None:
                env["hunt"] = refreshed_hunt

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{type(self).__name__} Cog ready.")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        """Out-of-band deletions (a user hand-deleting a puzzle channel or a
        whole bot-managed category) hit none of the service-side consolidation
        triggers — catch them here. A deleted CATEGORY leaves a dangling join
        row, so trigger on the category itself; a deleted channel shrinks its
        parent category. Bot-initiated deletions land here too, but duplicate
        triggers are cheap no-ops (idempotent, serialised on layout_lock).
        Threads don't fire this event."""
        try:
            if isinstance(channel, discord.CategoryChannel):
                category_layout.maybe_consolidate_category(channel.guild, channel)
            elif getattr(channel, "category", None) is not None:
                category_layout.maybe_consolidate_category(
                    channel.guild, channel.category)
        except Exception:
            logger.exception(
                "on_guild_channel_delete: consolidation trigger failed for %s",
                getattr(channel, "id", "?"))

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
        assert ctx.guild is not None  # hunt commands run inside guilds; DMs have no hunt context
        guild = ctx.guild
        guild_data = GuildSettingsDb.get(ctx.guild.id)

        if isinstance(ctx.channel, discord.Thread):
            # Mini-puzzle thread: resolve directly by thread snowflake, no category needed.
            puzzle = PuzzleJsonDb.get_by_attr(channel_id=ctx.channel.id)
            if puzzle:
                channel_type = 'Puzzle'
                hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
                # Upgrade the type when the thread's puzzle is a group's target so !info
                # treats it as a meta, mirroring the category path below.
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
            # Guild channels only past here (threads handled above); DM-ish channel
            # classes have no .category. Top-level channels have category None.
            channel_category: Optional[discord.CategoryChannel] = getattr(ctx.channel, "category", None)
            channel_type = GuildSettingsDb.get_channel_type(ctx.channel.id)
            if channel_type is None and channel_category is not None:
                channel_type = GuildSettingsDb.get_channel_type(channel_category.id)
            if channel_type is None:
                channel_type = "Guild"
            if channel_type == 'Hunt':
                hunt = HuntJsonDb.get_by_attr(channel_id=ctx.channel.id,)
            if channel_type == 'Group':
                tag_group = None
                if channel_category is not None:
                    tag_group = TagGroupJsonDb.get_by_attr(category_id=channel_category.id,)
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
                        # Direct category_id lookup misses solved/overflow
                        # categories (join-table-owned, not the group's
                        # primary category) — use the split-layout-aware
                        # resolver so a puzzle moved there still finds its group.
                        tag_group = self._tag_group_for_channel(ctx.channel)
                    hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        print (f"Channel type: {channel_type}")
        gsheet_cog = self.bot.get_cog("GoogleSheets")
        if gsheet_cog is not None:
            gsheet_cog.set_notify_channel(ctx.channel)
            if hunt is not None:
                gsheet_cog.set_spreadsheet_id(hunt.google_sheet_id)
                gsheet_cog.set_solved_sheet_id(hunt.solved_sheet_id)
        self.environment[ctx.message.id] = {'channel_type': channel_type,
                                            'hunt': hunt,
                                            'tag_group': tag_group,
                                            'puzzle': puzzle,
                                            'guild': guild,
                                            'guild_data': guild_data,
                                            'gsheet_cog': gsheet_cog}

    async def cog_after_invoke(self, ctx):
        """Drop the command's cached environment.

        Deliberately does NOT commit. It used to write puzzle, tag_group, hunt and
        guild_data after every command, whether or not anything changed -- which
        re-wrote every field of a possibly stale snapshot (H4), and, because
        discord.py calls this from a `finally`, persisted the partial mutation of a
        command that raised partway through. Every mutating path commits its own
        write: the service does it for command flows, and the one in-cog mutation
        (hunt.solved_sheet_id) commits explicitly at its own call site.
        """
        self.environment.pop(ctx.message.id, None)

    async def group_code_autocomplete(
            self,
            interaction: discord.Interaction,
            current: str,
    ) -> list[app_commands.Choice[str]]:
        # Pull valid rounds/metas from DB. Resolve the owning group through the
        # shared fallback (_tag_group_for_channel handles overflow/solved categories
        # via the join table, threads, and null-category contexts); return []
        # when the context can't be resolved so the autocomplete never raises.
        channel = interaction.channel
        tag_group = self._tag_group_for_channel(channel)
        if tag_group is None:
            return []
        # hunt_id param's `str` type comes from its vestigial "*" default; real ids are ints.
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
            channel = interaction.channel
            # Non-text/category-less contexts fall through to the except → defaults.
            assert isinstance(channel, discord.TextChannel)
            category = channel.category
            assert category is not None
            tag_group = TagGroupJsonDb.get_by_attr(category_id=category.id)
            assert tag_group is not None
            hunt_id = tag_group.hunt_id
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

    def get_puzzle_sheet(self, ctx=None, puzzle: Optional[PuzzleData] = None, hunt: Optional[HuntData] = None):
        """Return the active or solved-puzzles spreadsheet id for a puzzle.

        When *hunt* is not provided (bang path), falls back to environment lookup.
        """
        assert puzzle is not None  # callers always pass it; default is keyword-style only
        if hunt is None:
            hunt = self.get_hunt(ctx)
        assert hunt is not None  # puzzle contexts always resolve their hunt
        return puzzle_location(hunt, puzzle).spreadsheet_id

    def get_additional_sheet_spreadsheet(self, ctx=None, sheet: Optional[AdditionalSheetData] = None, hunt: Optional[HuntData] = None):
        """Return the spreadsheet id THIS additional sheet actually lives on.

        Takes the sheet, not the puzzle. Keyed on puzzle.solved it returned the
        archive whenever the puzzle was solved -- but extras only move there if
        the hunt HAS a solved sheet, and solved_sheet_id defaults to "", so a
        default hunt built 'spreadsheets/d//edit#gid=...' dead links (M15).
        That fallback rule now lives in bot.core.sheet_location. Since 045 the location is recorded per sheet, as get_puzzle_sheet has
        always done for the main tab.
        """
        assert sheet is not None  # callers always pass it; default is keyword-style only
        if hunt is None:
            hunt = self.get_hunt(ctx)
        assert hunt is not None  # puzzle contexts always resolve their hunt
        return additional_sheet_location(hunt, sheet).spreadsheet_id

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
        if settings is None or not settings.discord_bot_channel:
            # If no channel is designated, then all channels are fine
            # to listen to commands.
            return True

        if ctx.channel.name == settings.discord_bot_channel:
            # Channel name matches setting (note, channel name might not be unique)
            return True

        await ctx.send(f":exclamation: Most bot commands should be sent to #{settings.discord_bot_channel}")
        return False

    async def check_available_space(self, ctx, required_channels = 0, required_categories = 0):
        # Threads (mini-puzzles) don't count toward channel caps, always have space
        if isinstance(ctx.channel, discord.Thread):
            return True
        # No per-category check here: round and hunt-general categories now
        # auto-split/overflow (category_layout.pick_unsolved_category /
        # move_channel_to_owner) before a single category can actually run out
        # of room, so gating on "the current channel's category is full" would
        # incorrectly block a command auto-split would have handled fine.
        space_available = True
        guild = ctx.guild
        categories = len(guild.categories)
        total_channels = len(guild.channels)
        remaining_channels = self.MAX_CHANNELS_PER_GUILD - total_channels
        remaining_categories = self.MAX_CATEGORIES - categories
        if remaining_channels < required_channels or remaining_categories < required_categories:
            space_available = False
        guild_data = self.get_guild_data(ctx)
        bot_channel = discord.utils.get(ctx.guild.channels, name=guild_data.discord_bot_channel) if guild_data else None
        if bot_channel is None:
            return space_available
        if space_available is False:
            await bot_channel.send(f"@here A command has just failed due to lack of space on the server, there are currently {categories} categories on the server and {total_channels} total channels.")
        else:
            remaining_channels -= required_channels
            remaining_categories -= required_categories
            if remaining_channels <= 10:
                await bot_channel.send(
                    f"@here Server space is getting tight, remaining channel space is {remaining_channels}."
                )
            if remaining_categories <= 2 and required_categories > 0:
                await bot_channel.send(
                    f"@here Server space is getting tight, remaining category space is {remaining_categories}."
                )
        return space_available

    @commands.command(aliases=["h"])
    @is_admin()
    async def hunt(self, ctx, *, arg):
        """*Create a new hunt: its category, general channel and Google sheet.*

        Usage: `!hunt hunt-name:hunt-url[, role-name][, files]`
        Example: `!hunt mystery-2026:https://hunt.example.com`
        Add `, Team A` to gate the hunt behind a role. Append `, files` for one
        Google spreadsheet per puzzle in a Drive folder. Run in the bot channel.
        Admins/mods only. Alias: `!h`"""

        if not (await self.check_is_bot_channel(ctx)):
            return

        # An optional trailing ", files" token selects one-spreadsheet-per-puzzle
        # mode. Strip it first so the name:url[, role] parsing below is unaffected.
        sheet_mode = "tabs"
        arg = arg.rstrip()
        if arg.lower().endswith(", files"):
            arg, sheet_mode = arg[: -len(", files")], "files"

        role = None
        if ", " in arg:
            arg, role = arg.split(", ", 1)
        if ":" in arg:
            hunt_name, hunt_url = arg.split(":", 1)
            if HuntJsonDb.check_duplicates(hunt_name, guild_id=ctx.guild.id):
                return await ctx.send(f":exclamation: **Hunt {hunt_name}** already exists, please use a different name")
            new_hunt = await self.create_hunt(ctx, hunt_name, hunt_url, role,
                                              sheet_mode=sheet_mode)
            if new_hunt is None:
                return  # wrapper already messaged (cancelled / no space)
            return await ctx.send(
                f":white_check_mark: I've created a new hunt category and channel for {hunt_name}"
            )

        await ctx.send(f"Unable to parse hunt name {arg}, try using `!h hunt-name:hunt-url`")

    async def create_hunt(self, ctx, hunt_name: str, hunt_url: str, role_name: Optional[str] = None,
                          sheet_mode: str = "tabs"):
        if await self.check_available_space(ctx, 5, 2) is False:
            await ctx.send(":x: There is not enough enough space on the server to create a new hunt, please inform the moderators.")
            return None

        settings = self.get_guild_data(ctx)
        confirm_existing = False
        while True:
            try:
                new_hunt = await self.service.create_hunt(
                    ctx.guild,
                    name=hunt_name,
                    url=hunt_url,
                    start_timestamp=0,
                    role_name=role_name,
                    sheet_mode=sheet_mode,
                    guild_id=settings.id if settings else 0,
                    creator=ctx.author,
                    confirm_existing_role=confirm_existing,
                )
                break
            except RuntimeError as exc:
                if "existing_role_confirm" in str(exc) and not confirm_existing:
                    if not await self._confirm_reuse_role(ctx, role_name):
                        await ctx.send(":x: Cancelled — no hunt created.")
                        return None
                    confirm_existing = True
                    continue
                raise

        text_channel = self.bot.get_channel(new_hunt.channel_id)
        from bot.core.notifier import DiscordNotifier
        await DiscordNotifier(self.bot).welcome(ctx.guild, new_hunt, channel=text_channel)

        if role_name and not new_hunt.role_id:
            await ctx.send(
                f":warning: Created the hunt, but couldn't gate it to **{role_name}** "
                f"— it's open (ungated). Fix the role in Discord, then re-gate."
            )
        return new_hunt

    @commands.command()
    @is_admin()
    async def set_hunt_role(self, ctx, *, arg):
        """*Gate an existing OPEN hunt behind a team role (web + Discord).*

        Usage: `!set_hunt_role hunt-name, role-name`. Admins/mods only; run in
        the bot channel."""
        if not (await self.check_is_bot_channel(ctx)):
            return
        if ", " not in arg:
            return await ctx.send("Usage: `!set_hunt_role hunt-name, role-name`")
        hunt_name, role_name = (part.strip() for part in arg.split(", ", 1))
        if not hunt_name or not role_name:
            return await ctx.send("Usage: `!set_hunt_role hunt-name, role-name`")
        hunt = HuntJsonDb.get_by_name_for_guild(hunt_name, ctx.guild.id)
        if hunt is None:
            return await ctx.send(f":x: No hunt named **{hunt_name}** in this server.")

        confirm_existing = False
        while True:
            try:
                failed = await self.service.set_hunt_role(
                    ctx.guild, hunt, role_name=role_name,
                    requester=ctx.author, confirm_existing_role=confirm_existing,
                )
                break
            except RuntimeError as exc:
                s = str(exc)
                if "already_gated" in s:
                    return await ctx.send(f":x: **{hunt.name}** already has a role.")
                if "existing_role_confirm" in s and not confirm_existing:
                    if not await self._confirm_reuse_role(ctx, role_name):
                        return await ctx.send(":x: Cancelled.")
                    confirm_existing = True
                    continue
                if "role_not_applied" in s:
                    return await ctx.send(
                        f":x: Couldn't gate to **{role_name}** — the role couldn't "
                        f"be made visible (are you addable to it?)."
                    )
                raise

        msg = f":white_check_mark: Gated **{hunt.name}** behind **{role_name}**."
        if failed:
            msg += f"\n:warning: These categories weren't updated: {', '.join(failed)}"
        await ctx.send(msg)

    @commands.hybrid_command(
        aliases=["set_to_archive"],
        description="Use a separate 'solved puzzles sheet' for solved feeders (admin only)",
    )
    @is_admin()
    @with_hunt_sheet_mutex()
    async def use_solved_sheet(self, ctx):
        """*Use a separate 'solved puzzles sheet' to hold solved feeders (metas stay on the main sheet).*

        Usage: `!use_solved_sheet`
        Example: `!use_solved_sheet`
        Run in the main Hunt channel. Admins/mods only."""
        if self.get_channel_type(ctx) != "Hunt":
            await ctx.send(":x: This command must be done from the main Hunt channel")
            return

        hunt = self.get_hunt(ctx)
        assert hunt is not None  # Hunt channel checked above

        # One seam with the web route (C7). The decorator above holds
        # hunt_sheet_lock, which is what makes the exists-check and the create
        # atomic; the service stays lock-free per the bot/core convention.
        try:
            await self.service.create_hunt_solved_sheet(hunt)
        except RuntimeError as exc:
            if str(exc) == "solved_sheet_exists":
                await ctx.send(":x: This hunt is already using a solved puzzles sheet")
            elif str(exc) == "files_mode_no_solved_sheet":
                await ctx.send(f":x: {SHEET_MODE_REFUSALS['files_mode_no_solved_sheet']}")
            else:
                await ctx.send(":x: Could not create the solved puzzles sheet")
            return

        await ctx.send(":white_check_mark: The hunt has been set to use a solved puzzles sheet")
        await self.info(ctx, update=True)
        return


    @commands.command()
    @is_admin()
    @with_hunt_sheet_mutex()
    async def sheet_mode(self, ctx, mode: Optional[str] = None):
        """*Show or switch how the hunt's Google sheets are laid out (admin only).*

        Usage: `!sheet_mode [tabs|files]`
        Example: `!sheet_mode files`
        `tabs` keeps one spreadsheet with a tab per puzzle; `files` gives each puzzle its
        own spreadsheet in a Drive folder. With no argument, shows the current mode.
        Switching works only while the hunt has no puzzles or groups.
        Run in the main Hunt channel. Admins/mods only."""
        if self.get_channel_type(ctx) != "Hunt":
            await ctx.send(":x: This command must be done from the main Hunt channel")
            return
        hunt = self.get_hunt(ctx)
        assert hunt is not None  # Hunt channel checked above
        if mode is None:
            await ctx.send(f"This hunt uses sheet mode **{hunt.sheet_mode}**.")
            return
        mode = mode.lower()
        if mode not in ("tabs", "files"):
            await ctx.send(":x: Sheet mode must be `tabs` or `files`")
            return
        settings = GuildSettingsDb.get(ctx.guild.id)
        parent_id = (getattr(settings, "drive_parent_id", "") or None)
        try:
            await self.service.set_sheet_mode(hunt, mode, guild_parent_id=parent_id)
        except RuntimeError as exc:
            await ctx.send(f":x: {SHEET_MODE_REFUSALS.get(str(exc), 'Could not change the sheet mode')}")
            return
        await ctx.send(f":white_check_mark: This hunt now uses sheet mode **{mode}**")
        # Re-pin the welcome: files mode adds the Drive folder link, tabs drops it.
        await self.service.notifier.refresh_info(ctx.guild, hunt)

    async def _set_hunt_complete(self, ctx, complete: bool):
        if self.get_channel_type(ctx) != "Hunt":
            await ctx.send(":x: This command must be done from the main Hunt channel",
                           allowed_mentions=discord.AllowedMentions.none())
            return
        hunt = self.get_hunt(ctx)
        assert hunt is not None  # Hunt channel checked above
        try:
            outcome = await multi_team.set_complete(self.bot, hunt, complete)
        except multi_team.MultiTeamRefused as exc:
            await ctx.send(f":x: {exc}", allowed_mentions=discord.AllowedMentions.none())
            return
        except Exception:
            logger.exception("set_complete(%s) failed for hunt %s", complete, hunt.id)
            await ctx.send(":x: Couldn't update the hub channels \u2014 the error has been logged.",
                           allowed_mentions=discord.AllowedMentions.none())
            return
        await ctx.send(outcome.message, allowed_mentions=discord.AllowedMentions.none())

    @commands.command()
    @is_admin()
    async def hunt_complete(self, ctx):
        """*Mark this team as finished in its multi-team hunt (admin only).*

        Usage: `!hunt_complete`
        Example: `!hunt_complete`
        Gives this team's hub role access to the finishers channel on the hub and
        announces the finish there. Does nothing if the team is already marked.
        Run in the main Hunt channel of a team server. Admins/mods only."""
        await self._set_hunt_complete(ctx, True)

    @commands.command()
    @is_admin()
    async def hunt_not_complete(self, ctx):
        """*Undo marking this team as finished in its multi-team hunt (admin only).*

        Usage: `!hunt_not_complete`
        Example: `!hunt_not_complete`
        Removes this team's hub role from the finishers channel on the hub.
        Does nothing if the team isn't marked finished.
        Run in the main Hunt channel of a team server. Admins/mods only."""
        await self._set_hunt_complete(ctx, False)

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

        if hunt is None:
            return await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")

        env = self.environment.get(ctx.message.id)
        round_data = env.get("tag_group") if env else None

        # Serialize check+create per hunt, exactly as POST /hunts/<uid>/puzzles
        # does: create_puzzle takes no lock of its own (bot/core is lock-free by
        # convention) and awaits Sheets and Discord for seconds between the check
        # and the INSERT, so two near-simultaneous !p's would otherwise both pass
        # the check and each create a same-named puzzle.
        #
        # Nested under with_puzzle_mutex when this runs in a round channel (the
        # group's target is in context). That is a strict one-way order —
        # puzzle lock -> hunt_create_lock -> layout_lock/rollup_lock — with no
        # path taking a puzzle lock while holding hunt_create_lock, so it adds
        # no cycle (see hunt_create_lock's docstring in bot/core/locks.py).
        async with hunt_create_lock(hunt.id):
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
            try:
                await self.service.create_puzzle(
                    guild, hunt, round_data, puzzle_name,
                    is_meta=False, notifier=DiscordNotifier(self.bot),
                )
            except RuntimeError:
                return await ctx.send(":x: Failed to create puzzle (server full?).")
            except HttpError:
                # The sheet is made before the channel, so nothing was created.
                logger.exception("!puzzle: sheet creation failed for %s", puzzle_name)
                return await ctx.send(
                    ":x: Couldn't create the puzzle's Google sheet, so the puzzle was "
                    "not created. Please try again shortly.")
        asyncio.create_task(self.service.refresh_unassigned_safe(hunt))

    # --- legacy create twins removed (Phase 5 strangle) ---

    @commands.hybrid_command(description="Rebuild meta roll-up sheets (organiser only)")
    @is_organiser()
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
        candidate = self._tag_group_for_channel(ctx.channel)
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
    @is_organiser()
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
        strays = report.stray_files
        if strays:
            # Not orphans: kept out of _orphan_name_lines so the delete
            # confirmation never lists them.
            lines.append(f":file_folder: {len(strays)} stray file"
                         f"{'s' if len(strays) != 1 else ''} in the hunt's Drive folders "
                         "(never deleted — tidy them by hand)")
            shown = strays[:limit]
            lines.append("**Stray files:** " + ", ".join(f'"{name}"' for _, name in shown))
            if len(strays) > len(shown):
                lines.append(f"…and {len(strays) - len(shown)} more")
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
    @is_organiser()
    async def reconcile(self, ctx, mode: str = "", scope: str = ""):
        """*Heal Discord channels and Sheets tabs to match the database.*

        Usage: `!reconcile [apply [orphans]]`
        Example: `!reconcile` (report only)
        `!reconcile apply` fixes mismatches; `!reconcile apply orphans` also deletes
        orphaned channels/tabs (with confirmation). In a files hunt it also checks each
        puzzle's file (missing, wrong folder, wrong name) and lists stray files, which
        are never deleted. Run in a hunt channel. Organisers/admins only."""
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

    @commands.hybrid_command(
        description="Add/refresh emoji glyphs on every target channel in this hunt")
    @is_organiser()
    async def backfill_glyphs(self, ctx):
        """*One-off: prefix every meta/capstone/runaround channel with its glyph.*

        Usage: `!backfill_glyphs`
        Example: `!backfill_glyphs`
        Run in any channel of the hunt. Organisers/admins only. Idempotent."""
        hunt = self.get_hunt(ctx)
        if hunt is None:
            return await ctx.send(":x: Run this in a hunt channel.")
        renamed = await self.service.backfill_glyphs(ctx.guild, hunt)
        await ctx.send(
            f":white_check_mark: Glyph backfill complete — {renamed} channel(s) renamed.")

    @staticmethod
    def _format_media_report(count, total_bytes):
        """The one-line orphan summary both media commands report.

        Static and string-in/string-out so the wording is testable without a
        Discord context, like _format_reconcile_report."""
        if not count:
            return ":sparkles: No orphaned images — nothing to sweep."
        plural = "s" if count != 1 else ""
        return (f":wastebasket: {count} orphaned image{plural}, "
                f"at least {media_gc.format_size(total_bytes)}.")

    @commands.hybrid_command(
        description="Report images nothing references any more (organiser only)")
    @is_organiser()
    async def media_stats(self, ctx):
        """*Count this hunt's orphaned images and the disk they hold.*

        Usage: `!media_stats`
        Example: `!media_stats`
        Report only — nothing is deleted; `!media_sweep` does that. Images newer
        than 24 hours are never counted. Run in a hunt channel. Organisers/admins only."""
        hunt = self.get_hunt(ctx)
        if hunt is None:
            return await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")
        orphans = media_gc.orphaned_media(hunt.id)
        await ctx.send(self._format_media_report(
            len(orphans), sum(m.size or 0 for m in orphans)))

    @commands.hybrid_command(
        description="Delete images nothing references any more (organiser only)")
    @is_organiser()
    async def media_sweep(self, ctx):
        """*Reclaim disk by deleting this hunt's orphaned images.*

        Usage: `!media_sweep`
        Example: `!media_sweep`
        Deletes only images nothing points at (no evidence item, canvas, puzzle
        image or region crop) and older than 24 hours — including pictures you
        detached from a puzzle or replaced on a clue. Confirms first. Run in a
        hunt channel. Organisers/admins only."""
        hunt = self.get_hunt(ctx)
        if hunt is None:
            return await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")
        orphans = media_gc.orphaned_media(hunt.id)
        await ctx.send(self._format_media_report(
            len(orphans), sum(m.size or 0 for m in orphans)))
        if not orphans:
            return
        confirmed = await self._confirm_destructive(
            ctx,
            f"Permanently delete {len(orphans)} image(s) and their files? "
            f"Nothing currently shown on a puzzle, canvas or evidence item is affected.",
            sheets_deleted=False,
            confirm_label="Confirm sweep",
            title="⚠️ Confirm image sweep",
            timeout_msg=":hourglass: Confirmation timed out — nothing was deleted.")
        if not confirmed:
            return await ctx.send(":x: Sweep cancelled.")
        # Re-derived inside sweep(), so anything attached since the report above
        # simply survives to the next run rather than being deleted from under it.
        # Off the loop: sweep() is an rmtree per row (a single pyramid is ~940
        # files), and blocking here stalls the whole gateway.
        stats = await asyncio.to_thread(media_gc.sweep, hunt.id)
        await ctx.send(
            f":white_check_mark: Swept {stats.count} image(s), freeing at least "
            f"{media_gc.format_size(stats.bytes_freed)}.")

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
        from bot.core.notifier import build_solved_embed
        channel = guild.get_channel_or_thread(puzzle.channel_id) if (guild and puzzle.channel_id) else None
        if channel is not None:
            guild_data = GuildSettingsDb.get(guild.id)
            emoji = guild_data.discord_bot_emoji if guild_data else ""
            embed = build_solved_embed(emoji, puzzle, "Solved from the dashboard. Tidying up the sheets…")
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

    async def web_post_solution_update_embed(self, guild, hunt, puzzle, mode):
        """Best-effort dashboard-side presentation after service.add_partial/
        change_solution/add_solution (mode in "partial"/"change"/"add"): post the
        mode-appropriate embed in the puzzle channel, mirroring the bang
        !partial/!change_solution/!add_solution messages, and refresh the pinned
        info. Never raises. Unlike web_post_solved_embed/web_post_unsolved, these
        modes don't change solved state, so the channel is never repositioned."""
        # get_channel_or_thread (not get_channel) so a mini-puzzle, whose channel_id
        # is a thread snowflake, also resolves — otherwise the embed is skipped for minis.
        channel = guild.get_channel_or_thread(puzzle.channel_id) if (guild and puzzle.channel_id) else None
        if channel is not None:
            guild_data = GuildSettingsDb.get(guild.id)
            emoji = guild_data.discord_bot_emoji if guild_data else ""
            from bot.core.notifier import build_solution_update_embed
            embed = build_solution_update_embed(
                mode, emoji, puzzle.solution, "Updated from the dashboard.")
            try:
                await channel.send(embed=embed)
            except Exception:
                logger.exception("web_post_solution_update_embed: failed to post %s message", mode)
        await self.web_refresh_info(guild, hunt, puzzle)

    async def web_post_unsolved(self, guild, hunt, puzzle, prev_solution=""):
        """Best-effort dashboard-side presentation after service.unsolve: post the
        UNSOLVED embed in the puzzle channel, move it back to an unsolved category
        (split state) or above the divider (single state), and refresh the pinned
        info. Never raises. Mirrors web_post_solved_embed, and now SHARES its embed
        with the bang !unsolve via build_unsolved_embed — the two copies had
        drifted while each was independently maintained. Only the Follow-up text
        differs. service.unsolve has already cleared the solution by this point,
        so the caller passes prev_solution for the message."""
        # get_channel_or_thread (not get_channel) so a mini-puzzle, whose channel_id
        # is a thread snowflake, also resolves — otherwise the embed is skipped for minis.
        channel = guild.get_channel_or_thread(puzzle.channel_id) if (guild and puzzle.channel_id) else None
        if channel is not None:
            guild_data = GuildSettingsDb.get(guild.id)
            emoji = guild_data.discord_bot_emoji if guild_data else ""
            from bot.core.notifier import build_unsolved_embed
            embed = build_unsolved_embed(
                emoji, prev_solution, "Unsolved from the dashboard. Restoring the sheets…")
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
            # A mini-puzzle's thread: its parent is a text channel, not a group's
            # category, so there is no owning group to resolve here.
            return None
        if channel.category is None:
            return None
        tg = TagGroupJsonDb.get_by_attr(category_id=channel.category.id)
        if tg is not None:
            return tg
        gid = category_layout.group_id_for_category(channel.category.id, TagGroupCategoryDb)
        return TagGroupJsonDb.get_by_attr(id=gid) if gid else None

    def _owner_for_channel(self, channel):
        """Resolve the split-layout owner of a channel's category as an
        ``(owner, dao)`` pair: a tag_group + TagGroupCategoryDb (primary
        category or join table), else the hunt itself + HuntCategoryDb for a
        channel in a hunt-owned split/overflow category (unassigned puzzles
        after the hunt's general category splits). ``(None, None)`` when
        nothing owns the category — legacy divider handling applies."""
        tag_group = self._tag_group_for_channel(channel)
        if tag_group is not None:
            return tag_group, TagGroupCategoryDb
        if isinstance(channel, discord.Thread) or channel.category is None:
            return None, None
        hunt_id = category_layout.group_id_for_category(
            channel.category.id, HuntCategoryDb)
        if hunt_id:
            hunt = HuntJsonDb.get_by_attr(id=hunt_id)
            if hunt is not None:
                return hunt, HuntCategoryDb
        return None, None

    async def _apply_solved_move(self, guild, channel):
        """Category-aware reposition of a just-solved channel: SPLIT (round- or
        hunt-owned) → move into a solved category (+ clean an emptied overflow
        source); SINGLE → below the divider. Caller guards thread/None. Never
        raises on its own."""
        owner, dao = self._owner_for_channel(channel)
        if owner is not None and category_layout.is_split(owner, dao):
            source = await category_layout.move_channel_to_solved(
                guild, owner, dao, channel,
            )
            if source is not None:
                # A count just dropped somewhere: reclaim/fold/un-split in the
                # background if the owner's layout can shrink.
                category_layout.maybe_consolidate(guild, owner.id, dao)
        else:
            # Same lock split_group holds while it moves channels and deletes
            # the divider — and the divider position read INSIDE it, or a
            # concurrent first split can strand the channel against a stale
            # divider. (A separate cog-level lock serialized nothing.)
            async with category_layout.layout_lock:
                divider = self.get_solve_divider_position(None, channel.category)
                if divider is not False:
                    await channel.edit(position=divider + 1)

    async def _apply_unsolved_move(self, guild, channel):
        """Category-aware reposition of a just-unsolved channel: SPLIT (round- or
        hunt-owned) → move back to an unsolved category (+ clean an emptied
        overflow source); SINGLE → above the divider. Caller guards thread/None.
        Never raises on its own."""
        owner, dao = self._owner_for_channel(channel)
        if owner is not None and category_layout.is_split(owner, dao):
            source = await category_layout.move_channel_to_unsolved(
                guild, owner, dao, channel,
            )
            if source is not None:
                # See _apply_solved_move: background shrink check.
                category_layout.maybe_consolidate(guild, owner.id, dao)
        else:
            # See _apply_solved_move: layout_lock, divider read inside it.
            async with category_layout.layout_lock:
                divider = self.get_solve_divider_position(None, channel.category)
                if divider is not False:
                    await channel.edit(position=divider)

    def get_first_channel(self, ctx, category):
        return _channels.first_channel_position(category)

    def get_solve_divider_position(self, ctx, category):
        return _channels.solve_divider_position(category)

    @commands.hybrid_command(description="Move the channel to the top of the unsolved section of the category (organisers only)",)
    @is_organiser()
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
    @is_organiser()
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
    @is_organiser()
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


    @commands.hybrid_command(description="Show all tags on the puzzle (organisers/admins)",)
    @is_organiser()
    async def list_tags(self, ctx):
        """*List the tag IDs on this puzzle plus all available group tags (troubleshooting).*

        Usage: `!list_tags`
        Example: `!list_tags`
        Run in a puzzle channel. Organisers/admins only."""
        puzzle = self.get_puzzle(ctx)
        if puzzle is None:
            return await self.send_not_puzzle_channel(ctx)
        tag_info = ""
        tags = puzzle.tags.copy()
        for tag in tags:
            tag_group = TagGroupJsonDb.get_by_attr(id=tag)
            tag_name = tag_group.name if tag_group is not None else "*(deleted group)*"
            tag_info += f"{tag_name} - ID:{tag}\n"
        embed = discord.Embed(
            description=f"""Tag listing for {puzzle.name}"""
        )
        embed.add_field(
            name="Current tags",
            value=tag_info or "*(none)*",
            inline=False,
        )
        hunt = self.get_hunt(ctx)
        assert hunt is not None  # puzzle channels always resolve their hunt
        groups = TagGroupJsonDb.get_all(hunt.id)
        group_info = ""
        for group in groups:
            group_info += f"{group.name} - ID:{group.id}\n"
        embed.add_field(
            name="Available tags",
            value=group_info or "*(none)*",
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.command()
    @with_puzzle_mutex(wait=True)
    @with_hunt_sheet_mutex()
    async def add_sheet(self, ctx, *args):
        """*Add an extra working sheet (tab) to this puzzle, or to the hunt.*

        Usage: `!add_sheet sheet-name`
        Example: `!add_sheet grid attempt 2`
        Run in a puzzle channel that isn't solved yet to add a sheet to that
        puzzle; run anywhere else in the hunt to add one that belongs to the hunt
        itself — somewhere to pick at something found in the hunt's alt text
        before you know which puzzle it belongs to. List them with `!list_sheets`."""
        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)
        name = " ".join(args)

        if puzzle is None:
            if hunt is None:
                return await ctx.send(
                    ":x: This channel isn't part of a hunt, so there's nothing to "
                    "add a sheet to.")
            sheet = await self.service.add_hunt_sheet(hunt, name)
            return await ctx.send(
                f":white_check_mark: Added sheet {sheet.puzzle_name} to the hunt.")

        if puzzle.solved:
            return await ctx.send(":x: You can't add additional sheets to solved puzzles!")

        await self.service.add_sheet(hunt, puzzle, name)
        await self.info(ctx, update=True)
        return await ctx.send(f":white_check_mark: Added sheet {name} to puzzle.")

    @commands.command(aliases=['delete_sheet'])
    @is_organiser()
    @with_puzzle_mutex(wait=True)
    @with_hunt_sheet_mutex()
    async def remove_sheet(self, ctx, number: int):
        """*Remove an additional sheet by its number, from this puzzle or the hunt.*

        Usage: `!remove_sheet number`
        Example: `!remove_sheet 2`
        Get the number from `!list_sheets`. Run in a puzzle channel to remove one
        of that puzzle's sheets, or anywhere else in the hunt to remove one of the
        hunt's own. Organisers/admins only — it deletes the Google tab, so it is
        gated like the other deletes; `!add_sheet` stays open. Alias:
        `!delete_sheet`."""
        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)

        if puzzle is None:
            if hunt is None:
                return await ctx.send(
                    ":x: This channel isn't part of a hunt, so there are no sheets "
                    "to remove.")
            sheets = self.service.get_hunt_sheets(hunt)
            if number < 1 or number > len(sheets):
                return await ctx.send(
                    f":x: No hunt sheet #{number}. Use !list_sheets to see them.")
            name = sheets[number - 1].puzzle_name
            await self.service.remove_hunt_sheet_by_index(hunt, number)
            return await ctx.send(
                f":white_check_mark: Removed sheet {name} from the hunt.")

        if number < 1 or number > len(puzzle.additional_sheets):
            return await ctx.send(f":x: No additional sheet #{number}. Use !list_sheets to see them.")
        name = puzzle.additional_sheets[number - 1].puzzle_name
        await self.service.remove_sheet_by_index(hunt, puzzle, number)
        await self.info(ctx, update=True)
        return await ctx.send(f":white_check_mark: Removed sheet {name} from puzzle.")

    @commands.command(aliases=['canvases'])
    async def canvas(self, ctx):
        """*Link to this hunt's marked-up images.*

        Usage: `!canvas`
        Example: `!canvas`
        Posts a link to the hunt's Canvas view, where a big image can be marked
        up with a box round each puzzle. Drawing itself is web-only. Alias:
        `!canvases`."""
        hunt = self.get_hunt(ctx)
        if hunt is None:
            return await ctx.send(
                ":x: This channel isn't part of a hunt, so there are no canvases.")
        base = (config.web_frontend_url or "").rstrip("/")
        if not base:
            return await ctx.send(
                ":x: The dashboard URL isn't configured, so I can't link to it "
                "(set web.frontend_url in config.json).")
        url = f"{base}/hunts/{hunt.uid}"
        canvases = CanvasJsonDb.get_by_hunt(hunt.id)
        if not canvases:
            return await ctx.send(
                f":x: No canvases yet for **{hunt.name}**. An organiser can "
                f"upload the hunt's big image on the Canvas tab: {url}")
        names = ", ".join(c.name or f"Canvas {c.id}" for c in canvases)
        return await ctx.send(
            f":frame_photo: **{hunt.name}** has {len(canvases)} "
            f"canvas{'es' if len(canvases) != 1 else ''} ({names}). "
            f"Mark them up on the Canvas tab: {url}")

    @commands.command(aliases=['list_sheets'])
    async def list_additional_sheets(self, ctx):
        """*List additional sheets with links and their numbers.*

        Usage: `!list_sheets`
        Example: `!list_sheets`
        Run in a puzzle channel for that puzzle's sheets, or anywhere else in the
        hunt for the hunt's own. Alias: `!list_additional_sheets`. Numbers feed
        `!remove_sheet`."""
        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)

        if puzzle is None:
            if hunt is None:
                return await ctx.send(
                    ":x: This channel isn't part of a hunt, so there are no sheets "
                    "to list.")
            sheets = self.service.get_hunt_sheets(hunt)
            if not sheets:
                return await ctx.send(':x: No hunt sheets found!')
            embed = discord.Embed()
            embed.add_field(
                name="Hunt Sheet Links",
                value=self.create_hunt_sheet_list(hunt, sheets),
                inline=False,
            )
            return await ctx.send(embed=embed)

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
        await self.info(ctx, update=True)

    @commands.command()
    @is_organiser()
    async def remove_minimeta(self, ctx):
        """*Revert this minimeta back to an ordinary puzzle, deleting its mini-puzzles.*

        Usage: `!remove_minimeta`
        Example: `!remove_minimeta`
        Run in the minimeta's channel. Deletes every mini-puzzle (thread + sheet) and
        the rollup table, then clears the Minimeta type. Organisers/admins only;
        asks to confirm. Not recoverable."""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return await self.send_not_puzzle_channel(ctx)
        # Same bar as !delete_mini, which asks before deleting ONE mini: this
        # deletes every one of them plus the rollup, and none of it comes back.
        minis = self.service.get_minis(puzzle.id)
        count = f"{len(minis)} mini-puzzle(s)" if minis else "every mini-puzzle"
        if not await self._confirm_destructive(
                ctx, f"Remove minimeta **{puzzle.name}**, deleting {count} "
                     f"(threads + sheet tabs) and its rollup table?"):
            return
        try:
            await self.service.remove_minimeta(ctx.guild, self.get_hunt(ctx), puzzle)
        except RuntimeError:
            return await ctx.send(":x: This puzzle is not a minimeta.")
        await ctx.send(
            f":white_check_mark: **{puzzle.name}** is no longer a minimeta; its "
            f"mini-puzzles were removed."
        )
        await self.info(ctx, update=True)

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
            if str(exc) == "parent_sheet_missing":
                return await ctx.send(
                    ":x: This minimeta has no spreadsheet of its own, so a mini's tab has "
                    "nowhere to go. Run `!reconcile apply` to recreate it, then try again.")
            return await ctx.send(f":x: Could not create mini-puzzle: {exc}")
        except HttpError:
            logger.exception("!add_mini: sheet creation failed for %s", name)
            return await ctx.send(
                ":x: Couldn't create the mini-puzzle's Google sheet tab, so the mini was "
                "not created. Please try again shortly.")
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
        embed = self._build_mini_list_embed(
            puzzle, minis, self.get_hunt(ctx),
            title=f"Mini-puzzles for {puzzle.name}",
            overflow_hint="see the dashboard for the full list",
        )
        await ctx.send(embed=embed)

    def create_mini_list(self, puzzle: PuzzleData, minis=None, hunt: Optional[HuntData] = None):
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
        # A stale meta sheet is a warning, not a failure: the solve is already
        # committed and the board push has already fired, so reporting it as an
        # error tells the operator the opposite of what happened. Mirrors
        # !solve / !unsolve. ValueError stays an early return -- that one really
        # is a user error.
        sheet_warning = None
        try:
            await self.service.solve_mini_by_number(
                ctx.guild, self.get_hunt(ctx), puzzle.id, number, solution
            )
        except ValueError as exc:
            return await ctx.send(f":x: {exc}")
        except SheetRefreshError as exc:
            sheet_warning = exc
        await self.info(ctx, update=True)  # refresh the minimeta's pinned mini list
        await ctx.send(f":white_check_mark: Solved mini #{number}: **{solution}**")
        if sheet_warning:
            await self._warn_stale_sheets(ctx, sheet_warning)

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
        # As !solve_mini: the unsolve is committed before the meta refresh runs,
        # so a stale sheet is a warning. service.unsolve only began reporting
        # these with M27 -- before that they were swallowed and this handler had
        # nothing to catch.
        sheet_warning = None
        try:
            await self.service.unsolve_mini_by_number(
                ctx.guild, self.get_hunt(ctx), puzzle.id, number
            )
        except ValueError as exc:
            return await ctx.send(f":x: {exc}")
        except SheetRefreshError as exc:
            sheet_warning = exc
        await self.info(ctx, update=True)  # refresh the minimeta's pinned mini list
        await ctx.send(f":white_check_mark: Mini #{number} unmarked as solved.")
        if sheet_warning:
            await self._warn_stale_sheets(ctx, sheet_warning)

    def _resolve_mini_to_delete(self, ctx, number):
        """Resolve which mini `!delete_mini` targets, from either context.

        Returns (mini, error_message): in the mini's own thread the current puzzle
        IS the mini and no number is wanted; in the minimeta's channel the number
        indexes its children (1-based, as `!list_minis` prints them). Exactly one
        of the pair is None.
        """
        puzzle = self.get_puzzle(ctx)
        if puzzle is None:
            return None, ":x: Run this in a mini-puzzle's thread or its minimeta's channel."
        if puzzle.parent_puzzle_id:
            if number is not None:
                return None, (":x: You're in a mini's own thread — `!delete_mini` with no "
                              "number deletes this one. Use a number from the minimeta's channel.")
            return puzzle, None
        if number is None:
            return None, (":x: Provide a number: `!delete_mini 2` (see `!list_minis`), or run "
                          "`!delete_mini` in the mini's own thread.")
        minis = self.service.get_minis(puzzle.id)
        if number < 1 or number > len(minis):
            return None, f":x: No mini #{number} — {len(minis)} mini(s) exist"
        return minis[number - 1], None

    @commands.command()
    @is_organiser()
    @with_puzzle_mutex(wait=False)
    async def delete_mini(self, ctx, number: Optional[int] = None):
        """*Permanently delete one mini-puzzle: its thread and its sheet tab (asks to confirm).*

        Usage: `!delete_mini [number]`
        Example: `!delete_mini 2`
        Run in the minimeta's channel with a number from `!list_minis`, or in the
        mini's own thread with no number. Organisers/admins only. Not recoverable."""
        mini, error = self._resolve_mini_to_delete(ctx, number)
        if error:
            return await ctx.send(error)
        assert mini is not None  # _resolve_mini_to_delete returns exactly one of the pair

        if not await self._confirm_destructive(
                ctx, f"Delete mini-puzzle **{mini.name}**, its thread and its sheet tab?"):
            return

        # Running in the mini's own thread deletes the channel we're talking in, so
        # the report goes to its parent — a later ctx.send would hit a dead thread.
        if mini.channel_id == ctx.channel.id:
            parent_channel = getattr(ctx.channel, "parent", None)
            report = parent_channel.send if parent_channel is not None else ctx.send
        else:
            report = ctx.send

        hunt = self.get_hunt(ctx)
        try:
            minimeta = await self.service.delete_mini(ctx.guild, hunt, mini)
        except Exception:
            logger.exception("Mini-puzzle deletion failed")
            return await ctx.send(":x: Mini-puzzle deletion failed")
        # ctx-free pin refresh: !info would resolve the deleted mini in thread context.
        await self.web_refresh_info(ctx.guild, hunt, minimeta)
        await report(f":white_check_mark: Deleted mini-puzzle **{mini.name}**.")

    async def _store_clue_attachment(self, ctx, hunt):
        """Store the first image attached to ctx.message, if any.

        Returns (media_id, error_message). media_id is None when there is no
        attachment; error_message is None unless the attachment was refused.
        """
        attachments = getattr(ctx.message, "attachments", None) or []
        if not attachments:
            return None, None
        att = attachments[0]
        raw = await att.read()
        try:
            media = ingest_image_bytes(
                hunt_id=hunt.id,
                filename=att.filename or "image",
                raw=raw,
                origin="discord",
                db=MediaJsonDb,
            )
        except MediaRejected as exc:
            return None, str(exc)
        if media.tile_status == "pending":
            schedule_tiling(media, MediaJsonDb,
                             after=functools.partial(self.service.notifier.board_changed, hunt))
        return media.id, None

    @commands.hybrid_command(name="clue", description="Log a freeform clue/observation, optionally with an image")
    async def clue(self, ctx, *, note: str = ""):
        """*Log a freeform clue/observation to the evidence sheet.*

        Usage: `!clue text` (attach an image to log a picture too)
        Example: `!clue every answer starts with a chemical element`
        Run in a puzzle channel to log the clue from that puzzle; run anywhere
        else in the hunt to log it with no source puzzle. The text is optional
        when you attach an image. For several at once use `!clues`."""
        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)
        if hunt is None:
            return await ctx.send(
                ":x: This channel isn't part of a hunt, so clues can't be logged here.")
        media_id, error = await self._store_clue_attachment(ctx, hunt)
        if error:
            return await ctx.send(f":warning: {error}")
        if not note.strip() and media_id is None:
            await ctx.send(":warning: No clue text or image provided.")
            return
        await self.service.log_clues(hunt, puzzle, [note.strip()], media_id=media_id)
        asyncio.create_task(self.service.refresh_evidence_safe(hunt))
        if puzzle is not None:
            asyncio.create_task(self.service.refresh_informs_targets(hunt, puzzle))
        where = f" from **{puzzle.name}**" if puzzle is not None else ""
        what = "Clue" if media_id is None else "Clue with image"
        await ctx.send(f":pencil: {what} logged{where}.")

    @commands.hybrid_command(name="clues", description="Log several clues at once (quote any clue with spaces)")
    async def clues(self, ctx, *, items: str):
        """*Log several clues at once; quote any clue that contains spaces.*

        Usage: `!clues item1 item2 "multi word item" ...`
        Example: `!clues APPLE BANANA "CHERRY BOMB" DURIAN`
        Run in a puzzle channel. For a single freeform clue use `!clue`."""
        import shlex
        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)
        if puzzle is None:
            return await self.send_not_puzzle_channel(ctx)
        if not items.strip():
            await ctx.send(":warning: No items provided.")
            return
        try:
            notes = shlex.split(items)
        except ValueError:
            await ctx.send(
                ":warning: Couldn't parse that — check your quotes are balanced "
                "(a stray `'` or `\"` will confuse it)."
            )
            return
        if not notes:
            await ctx.send(":warning: No items provided.")
            return
        await self.service.log_clues(hunt, puzzle, notes)
        asyncio.create_task(self.service.refresh_evidence_safe(hunt))
        asyncio.create_task(self.service.refresh_informs_targets(hunt, puzzle))
        await ctx.send(f":pencil: {len(notes)} clue{'s' if len(notes) != 1 else ''} logged from **{puzzle.name}**.")

    def create_additional_sheet_list(self, ctx=None, puzzle: Optional[PuzzleData] = None, hunt: Optional[HuntData] = None):
        assert puzzle is not None  # callers always pass it; default is keyword-style only
        sheet_links = ""
        count = 1
        for sheet in puzzle.additional_sheets:
            # Resolved per sheet: two extras on one puzzle can be in different
            # places once a target has been solved (M15/H3).
            spreadsheet_url = urls.spreadsheet_url(
                self.get_additional_sheet_spreadsheet(ctx, sheet, hunt),
                sheet.google_page_id)
            sheet_links += f"{count}: [{sheet.puzzle_name}]({spreadsheet_url})\n"
            count+=1
        return sheet_links

    def create_hunt_sheet_list(self, hunt, sheets):
        """Numbered markdown links for a hunt's OWN working sheets.

        Sibling of create_additional_sheet_list rather than a widening of it: that
        one picks the spreadsheet from puzzle.solved, and a hunt sheet is never
        solved -- it always lives on the hunt's main spreadsheet.
        """
        sheet_links = ""
        for count, sheet in enumerate(sheets, start=1):
            spreadsheet_url = urls.spreadsheet_url(
                hunt_sheet_location(hunt, sheet.google_page_id).spreadsheet_id,
                sheet.google_page_id)
            sheet_links += f"{count}: [{sheet.puzzle_name}]({spreadsheet_url})\n"
        return sheet_links

    @commands.command(
        name="sync",
        description="Sync all puzzles in the category to the group (organiser only)",
        aliases=["sync_meta", "sync_feeders", "sync_round"],
    )
    @is_organiser()
    # Deliberately no with_puzzle_mutex: that locks only the puzzle in context
    # (the group's target), and !sync writes EVERY member's edges. Like the
    # convert commands, it takes the whole group in one sorted puzzles_lock
    # below instead — a second, nested acquisition is what creates a lock-order
    # cycle (H5, see bot/core/locks.py).
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
        meta_category = ctx.channel.category
        target_group = TagGroupJsonDb.get_by_attr(category_id=meta_category.id)
        if not target_group:
            await ctx.send(":x: Could not find the group for this category.")
            return

        default_edge = TYPE_DEFAULTS.get(target_group.type, {}).get("default_edge", "feeds")

        # In a split round, puzzles moved into the solved/overflow categories are
        # tracked only via TagGroupCategoryDb (not TagGroupJsonDb.category_id), so
        # a plain meta_category.channels walk silently misses them (#15).
        sync_categories = [meta_category]
        seen_category_ids = {meta_category.id}
        for row in category_layout.group_categories(target_group, TagGroupCategoryDb):
            if row.category_id in seen_category_ids:
                continue
            extra_category = discord.utils.get(ctx.guild.categories, id=row.category_id)
            if extra_category is not None:
                sync_categories.append(extra_category)
                seen_category_ids.add(extra_category.id)

        # Which puzzles this sync will write. Resolved BEFORE the lock (the same
        # pre-existing window convert_lock_ids and the delete edges have: a puzzle
        # whose channel appears after this walk is not covered), because we need
        # the id set to take the lock.
        member_ids = []
        for category in sync_categories:
            for channel in category.channels:
                puz = PuzzleJsonDb.get_by_attr(channel_id=channel.id)
                if puz is None:
                    continue
                if puz.id == target_group.target_puzzle_id:
                    continue
                member_ids.append(puz.id)

        updated = 0
        errors = []
        touched = []
        # ONE sorted acquisition (H5) over every puzzle this writes — the puzzles
        # being tagged in, plus the target this used to hold via with_puzzle_mutex.
        # Without the members' own locks, a command already in flight on one of
        # them commits its pre-sync snapshot afterwards and its tag-edge diff
        # silently deletes the edge we just wrote (#27).
        #
        # Spelled out rather than reusing convert_lock_ids: sync's set is the
        # puzzles found in the CATEGORIES (which may not be members yet), not the
        # group's current membership. Sorted acquisition over a subset of the same
        # global key space is still cycle-free.
        lock_ids = set(member_ids)
        if target_group.target_puzzle_id:
            lock_ids.add(target_group.target_puzzle_id)
        async with puzzles_lock(lock_ids):
            for puzzle_id in member_ids:
                # Re-read under the lock rather than trusting the pre-lock scan:
                # commit() writes the object it is handed field by field, so a
                # stale snapshot would clobber a concurrent write (H4).
                puz = PuzzleJsonDb.get_by_attr(id=puzzle_id)
                if puz is None:
                    continue
                try:
                    # Feeds lines are written once for the whole sync, below,
                    # not once per puzzle.
                    await self.service.tag_edge_only(puz, target_group, default_edge,
                                                     guild=None, refresh_headers=False)
                    updated += 1
                except Exception as exc:
                    errors.append((puz.name, str(exc)))
                touched.append(puz)

        hunt = self.get_hunt(ctx)
        if touched:
            # One write per spreadsheet; never raises.
            await self.service.refresh_header_links(hunt, touched, reload=True)
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
        if puzzle is None:
            return await self.send_not_puzzle_channel(ctx)
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
        if puzzle is None:
            return await self.send_not_puzzle_channel(ctx)
        target_group = await self.resolve_target_group(ctx, meta_code)
        if not target_group:
            return
        await self.service.tag_edge_only(puzzle, target_group, "feeds", self.get_guild(ctx))
        asyncio.create_task(self.service.refresh_unassigned_safe(self.get_hunt(ctx)))
        await ctx.send(
            f":white_check_mark: This puzzle is now assigned to {target_group.name}. Meta sheets are being updated.")

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
        if puzzle is None:
            return await self.send_not_puzzle_channel(ctx)
        target_group = await self.resolve_target_group(ctx, meta_code)
        if not target_group:
            return
        if puzzle.id == target_group.target_puzzle_id:
            await ctx.send(":x: You can't remove the target puzzle from its own group!")
            return
        await self.service.untag_from(puzzle, target_group)
        asyncio.create_task(self.service.refresh_unassigned_safe(self.get_hunt(ctx)))
        await ctx.send(
            f":white_check_mark: This puzzle has been removed from {target_group.name}.")

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
        if puzzle is None:
            return await self.send_not_puzzle_channel(ctx)
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
        if puzzle is None:
            return await self.send_not_puzzle_channel(ctx)
        target_group = await self.resolve_target_group(ctx, meta_code)
        if not target_group:
            return
        await self.service.tag_edge_only(puzzle, target_group, "informs", guild=None)
        asyncio.create_task(self.service.refresh_unassigned_safe(self.get_hunt(ctx)))
        await ctx.send(
            f":white_check_mark: This puzzle is now assigned to {target_group.name}. Meta sheets are being updated.")

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
        if puzzle is None:
            return await self.send_not_puzzle_channel(ctx)
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
        if puzzle is None:
            return await self.send_not_puzzle_channel(ctx)
        target_group = await self.resolve_target_group(ctx, meta_code)
        if not target_group:
            return
        await self.service.tag_edge_only(puzzle, target_group, "none", guild=None)
        asyncio.create_task(self.service.refresh_unassigned_safe(self.get_hunt(ctx)))
        await ctx.send(
            f":white_check_mark: This puzzle is now assigned to {target_group.name}.")

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
        from bot.core.puzzle_service import (
            GROUP_TYPE_INPUT_ALIASES,
            default_source_mode_for,
            meta_puzzle_name_for,
        )

        command_key = command.casefold()

        # command_map: key → input label (the type as offered to the user; see
        # GROUP_TYPE_INPUT_ALIASES for how it maps to the stored type).
        command_map = {
            "r": "Round",
            "round": "Round",
            "mp": "Metapuzzle",
            "metapuzzle": "Metapuzzle",
            "capstone": "Capstone",
            "cs": "Capstone",
            "runaround": "Runaround",
            "ra": "Runaround",
            "group": "Group",
        }

        input_label = command_map[command_key]
        group_type = GROUP_TYPE_INPUT_ALIASES.get(input_label, input_label)
        hunt = self.get_hunt(ctx)
        # The sole caller (!round) refuses outside a Hunt channel, where
        # cog_before_invoke always resolves the hunt. Named because the create
        # lock below is keyed on hunt.id.
        assert hunt is not None

        # --- Delegate to service ---
        meta_puzzle_name = meta_puzzle_name_for(input_label, round_name)

        try:
            notifier = DiscordNotifier(self.bot)
            # Same per-hunt create lock as the web /metas route: create_tag_group
            # runs its OWN duplicate check internally and then awaits Discord for
            # seconds before committing the row, so the whole call has to be the
            # critical section or two concurrent !round's both pass the check.
            async with hunt_create_lock(hunt.id):
                new_round, puzzle = await self.service.create_tag_group(
                    ctx.guild, hunt, round_name,
                    notifier=notifier,
                    meta_puzzle_name=meta_puzzle_name,
                    round_type=group_type,
                )
            # Post-create: some types override the column's source_mode default.
            source_mode = default_source_mode_for(group_type)
            if source_mode:
                await self.service.set_source_mode(hunt, new_round, source_mode)
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
            return None, None  # error already sent, stop further processing

    @commands.command(aliases=["r","mp","metapuzzle",
                               "capstone", "cs", "runaround", "ra", "group"])
    @is_organiser()
    async def round(self, ctx, *, arg):
        """*Create a new round/group category; the alias you use picks the type.*

        Usage: `!round name` (or one of the type aliases below)
        Example: `!round the-bog`
        Run in the main Hunt channel. The created type depends on the alias:
        • `!r` / `!round name` — a round (also makes a "name - meta" meta puzzle)
        • `!mp` / `!metapuzzle name` — a metapuzzle
        • `!cs` / `!capstone name` — a capstone
        • `!ra` / `!runaround name` — a runaround
        • `!group name` — a group (no meta puzzle)
        Organisers/admins only — creating a group is gated like renaming, deleting
        and converting one. Adding a puzzle (`!p`) stays open to everyone."""

        if self.get_channel_type(ctx) != "Hunt":
            await ctx.channel.send(":x: This command must be done from the main Hunt channel")
            return

        if await self.check_available_space(ctx,5,1) is False:
            return await ctx.send(":x: There is not enough enough space on the server to create a new round, please inform the moderators.")

        command = ctx.invoked_with

        tag_group, puzzle = await self._round_impl(ctx, arg, command)
        if tag_group is None:
            return  # creation failed; _round_impl already reported the error

        hunt = self.get_hunt(ctx)
        assert hunt is not None  # Hunt channel checked above
        if puzzle is None:
            await ctx.send(
                f":white_check_mark: I've created a new category for {hunt.name} - "
                f"{tag_group.name}"
            )
        else:
            await ctx.send(
                f":white_check_mark: I've created a new {puzzle.name} category and channel "
                f"for {hunt.name} - {tag_group.name}"
            )

    async def _run_convert_to_type(
        self, ctx, new_type, channel_type, *, error_if_not_meta, wrong_state_msg, success_msg,
    ):
        """Shared body for the convert_to_meta/capstone/runaround/group/metapuzzle
        bang commands: they differ only in the target group type, the resulting
        channel type, which is_target state is valid to convert from
        (error_if_not_meta: True = metas only, False = non-metas only,
        None = any — convert_to_type promotes a bare puzzle in place), and the
        wrong-state/success message text. Mirrors the web equivalent,
        _convert_to_type_handler (bot/web/actions.py)."""
        if (puzzle := self.get_puzzle(ctx)) is None:
            await ctx.channel.send(":x: This doesn't appear to be a puzzle.")
            return
        if error_if_not_meta is None:
            is_wrong_state = False
        else:
            is_wrong_state = puzzle.is_target if not error_if_not_meta else not puzzle.is_target
        if is_wrong_state:
            await ctx.channel.send(f":x: {wrong_state_msg}")
            return

        # Target + every member, in ONE sorted acquisition (H5) — the same set the
        # web convert routes and the delete/archive edges take. These commands
        # deliberately carry no with_puzzle_mutex (the lock covers the whole group,
        # not just the puzzle in context), so this is their only acquisition; the
        # puzzle is re-read under it rather than trusting the pre-lock snapshot.
        try:
            async with puzzles_lock(convert_lock_ids(puzzle_id=puzzle.id)):
                puzzle = PuzzleJsonDb.get_by_attr(id=puzzle.id) or puzzle
                tag_group, summary = await self.service.convert_to_type(
                    ctx.guild, self.get_hunt(ctx), puzzle, new_type
                )
        except RuntimeError as e:
            await ctx.send(f":x: {e}")
            return
        self.set_tag_group(ctx, tag_group)
        self.set_channel_type(ctx, channel_type)
        await self.info(ctx, update=True)
        await ctx.send(success_msg(tag_group, summary))

    @commands.hybrid_command(description="Convert puzzle to metapuzzle", )
    @is_organiser()
    async def convert_to_meta(self, ctx):
        """*Turn this feeder puzzle into a metapuzzle that others can be tagged to.*

        Usage: `!convert_to_meta`
        Example: `!convert_to_meta`
        Run in the puzzle's channel. Organisers/admins only."""
        await self._run_convert_to_type(
            ctx, "Metapuzzle", "Metapuzzle",
            error_if_not_meta=False,
            wrong_state_msg="This puzzle is already a metapuzzle.",
            success_msg=lambda tag_group, summary: (
                ":white_check_mark: I've converted this puzzle into a metapuzzle within the "
                f"category, other puzzles can now be tagged to it using its meta code {tag_group.group_code}."
            ),
        )

    @commands.hybrid_command(description="Convert puzzle to capstone", )
    @is_organiser()
    async def convert_to_capstone(self, ctx):
        """*Convert this puzzle into a capstone (final meta).*

        Usage: `!convert_to_capstone`
        Example: `!convert_to_capstone`
        Run in the puzzle's channel. A regular puzzle is promoted in place
        (like !convert_to_meta, but straight to capstone); a meta's feeders
        flip feeds → informs. Organisers/admins only."""
        await self._run_convert_to_type(
            ctx, "Capstone", "Capstone",
            error_if_not_meta=None,
            wrong_state_msg="",
            success_msg=lambda tag_group, summary: (
                f":white_check_mark: I've converted this puzzle into a capstone. "
                f"Other puzzles can inform it using its code {tag_group.group_code}. {summary}"
            ),
        )

    @commands.hybrid_command(description="Convert puzzle to runaround", )
    @is_organiser()
    async def convert_to_runaround(self, ctx):
        """*Convert this puzzle into a runaround.*

        Usage: `!convert_to_runaround`
        Example: `!convert_to_runaround`
        Run in the puzzle's channel. A regular puzzle is promoted in place;
        a meta keeps its membership edges. Organisers/admins only."""
        await self._run_convert_to_type(
            ctx, "Runaround", "Runaround",
            error_if_not_meta=None,
            wrong_state_msg="",
            success_msg=lambda tag_group, summary: (
                f":white_check_mark: I've converted this puzzle into a runaround. {summary}"
            ),
        )

    @commands.hybrid_command(description="Convert meta puzzle to group", )
    @is_organiser()
    async def convert_to_group(self, ctx):
        """*Convert this meta puzzle into a group (a category with no meta puzzle).*

        Usage: `!convert_to_group`
        Example: `!convert_to_group`
        Run in a meta puzzle's channel. Organisers/admins only."""
        await self._run_convert_to_type(
            ctx, "Group", "Group",
            error_if_not_meta=True,
            wrong_state_msg="This puzzle is not a meta and cannot be converted to a group.",
            success_msg=lambda tag_group, summary: (
                f":white_check_mark: I've converted this puzzle into a group. {summary}"
            ),
        )

    @commands.hybrid_command(description="Convert meta to a plain metapuzzle", )
    @is_organiser()
    async def convert_to_metapuzzle(self, ctx):
        """*Convert this meta (capstone/runaround/group) back to a plain metapuzzle.*

        Usage: `!convert_to_metapuzzle`
        Example: `!convert_to_metapuzzle`
        Run in a meta puzzle's channel; feeders revert to the 'feeds' edge.
        Organisers/admins only."""
        await self._run_convert_to_type(
            ctx, "Metapuzzle", "Metapuzzle",
            error_if_not_meta=True,
            wrong_state_msg="This puzzle is not a meta. Use `!convert_to_meta` to promote a feeder.",
            success_msg=lambda tag_group, summary: (
                f":white_check_mark: I've converted this puzzle into a metapuzzle. {summary}"
            ),
        )

    @commands.hybrid_command(
        description="Give this meta its own category (moves its target + exclusive feeders)")
    @is_organiser()
    async def extract_target_to_category(self, ctx):
        """*Move this meta's target puzzle and its exclusive feeders into their own category.*

        Usage: `!extract_target_to_category`
        Run in the meta's own target-puzzle channel. Organisers/admins only.
        Bang-only: no web-dashboard equivalent, by design (Discord plumbing)."""
        hunt = self.get_hunt(ctx)
        meta = self.get_tag_group(ctx)
        puzzle = self.get_puzzle(ctx)
        if (hunt is None or meta is None or not meta.has_target
                or puzzle is None or meta.target_puzzle_id != puzzle.id):
            return await ctx.send(
                ":x: Run this in a metapuzzle's own target-puzzle channel "
                "(not a feeder or group channel).")
        try:
            new_category = await self.service.extract_target_to_category(
                ctx.guild, hunt, meta)
        except RuntimeError as exc:
            messages = {
                "already_own_category":
                    ":information_source: This meta already has its own category.",
                "no_category_space":
                    ":x: No free category slot on this server.",
                "no_template_category":
                    ":x: Couldn't find the hunt's template category.",
                "no_target_puzzle":
                    ":x: Couldn't find this meta's target puzzle.",
            }
            return await ctx.send(messages.get(str(exc), f":x: Couldn't extract ({exc})."))
        await ctx.send(
            f":white_check_mark: Extracted **{meta.name}** into its own category "
            f"**{new_category.name}**.")

    @commands.hybrid_command(
        description="Fold this meta back into another category (frees a category slot)")
    @app_commands.autocomplete(destination=group_code_autocomplete)
    @is_organiser()
    async def consolidate_to(self, ctx, destination: str):
        """*Fold this meta into <destination>'s category, freeing this meta's category.*

        Usage: `!consolidate_to <destination-code>`
        Run in the meta's own target-puzzle channel. Organisers/admins only.
        Not done lightly: it deletes this meta's category and moves its puzzles."""
        hunt = self.get_hunt(ctx)
        meta = self.get_tag_group(ctx)
        puzzle = self.get_puzzle(ctx)
        if (hunt is None or meta is None or not meta.has_target
                or puzzle is None or meta.target_puzzle_id != puzzle.id):
            return await ctx.send(
                ":x: Run this in a metapuzzle's own target-puzzle channel.")
        dest = await self.resolve_target_group(ctx, destination)
        if dest is None:
            return  # resolve_target_group already replied
        if dest.id == meta.id:
            return await ctx.send(":x: Can't consolidate a meta into itself.")
        try:
            await self.service.consolidate_to(ctx.guild, hunt, meta, dest, force=False)
        except RuntimeError as exc:
            if str(exc) == "would_overflow":
                if not await self._confirm_destructive(
                        ctx,
                        f"Consolidating **{meta.name}** into **{dest.name}** will overflow "
                        f"it (it'll split into an extra category, reducing the saving). "
                        f"Proceed?",
                        sheets_deleted=False,
                        confirm_label="Confirm consolidate"):
                    return await ctx.send(":x: Consolidation cancelled.")
                try:
                    await self.service.consolidate_to(ctx.guild, hunt, meta, dest, force=True)
                except RuntimeError as exc2:
                    return await ctx.send(f":x: Couldn't consolidate ({exc2}).")
            else:
                return await ctx.send(f":x: Couldn't consolidate ({exc}).")
        await ctx.send(
            f":white_check_mark: Consolidated **{meta.name}** into **{dest.name}**.")

    @commands.hybrid_command(
        description="Convert metapuzzle back to feeder",
        aliases=["convert_to_feeder"],
    )
    @is_organiser()
    async def convert_to_puzzle(self, ctx):
        """*Convert this meta puzzle back into an ordinary feeder puzzle.*

        Usage: `!convert_to_puzzle`
        Example: `!convert_to_puzzle`
        Run in a meta puzzle's channel; other puzzles can no longer be tagged to it.
        Organisers/admins only. Alias: `!convert_to_feeder`."""
        if (puzzle := self.get_puzzle(ctx)) is None:
            await ctx.channel.send(":x: This doesn't appear to be a puzzle.")
            return
        elif puzzle.is_target is False:
            await ctx.channel.send(":x: This puzzle is already a feeder.")
            return

        # Target + every member, in ONE sorted acquisition (H5) — the same set the
        # convert siblings and the delete/archive edges take. Demoting deletes the
        # group, cascade-deleting every member's edge rows, so a member command
        # holding a pre-demote snapshot would otherwise commit afterwards and try
        # to re-INSERT its edge against the deleted group. Like the other convert
        # commands this carries no with_puzzle_mutex, so this is its only
        # acquisition; the puzzle is re-read under it rather than trusting the
        # pre-lock snapshot.
        try:
            async with puzzles_lock(convert_lock_ids(puzzle_id=puzzle.id)):
                puzzle = PuzzleJsonDb.get_by_attr(id=puzzle.id) or puzzle
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

    # --- Authorisation role ids (M8) ---------------------------------------

    def _guild_settings(self, ctx):
        """This guild's settings row, created if the backfill has not run yet."""
        settings = GuildSettingsDb.get(ctx.guild.id)
        if settings is None:
            settings = GuildSettings()
            settings.guild_id = ctx.guild.id
            settings.guild_name = ctx.guild.name
        return settings

    async def _set_tier_roles(self, ctx, column, roles, label):
        settings = self._guild_settings(ctx)
        setattr(settings, column, ",".join(str(r.id) for r in roles))
        GuildSettingsDb.commit(settings)
        if not roles:
            await ctx.send(
                f":white_check_mark: Cleared the {label} role list. This guild is back to "
                f"matching {label} by role NAME — anyone who can create a role can grant "
                f"themselves the tier, so set it again when you can.")
            return
        named = ", ".join(r.mention for r in roles)
        await ctx.send(
            f":white_check_mark: {label.capitalize()} tier is now exactly: {named}. "
            f"A role merely *named* like one of the old defaults no longer grants it.")

    # Prefix-only: a variadic role list is not expressible as a slash-command
    # parameter (discord.py rejects VAR_POSITIONAL in a hybrid callback).
    @commands.command()
    @is_admin()
    async def set_admin_roles(self, ctx, *roles: discord.Role):
        """*Pin the admin tier to specific roles, by id.*

        Usage: `!set_admin_roles @Role [@Role ...]`
        Example: `!set_admin_roles @Hunt Runners`

        Without this, the tier matches role NAMES (Moderator/mod/admin), so anyone
        who can create a role can grant themselves dashboard admin. Run with no
        roles to clear the list and go back to name matching. Admins/mods only."""
        # Storing ids the caller does not hold would lock them out of this very
        # command — the only one that could undo it.
        if roles and not ({r.id for r in ctx.author.roles} & {r.id for r in roles}):
            await ctx.send(
                ":x: You don't hold any of those roles, and setting them would lock "
                "you out of `!set_admin_roles` itself. Include one of your own roles.")
            return
        await self._set_tier_roles(ctx, "admin_role_ids", roles, "admin")

    @commands.command()
    @is_admin()
    async def set_organiser_roles(self, ctx, *roles: discord.Role):
        """*Pin the organiser tier to specific roles, by id.*

        Usage: `!set_organiser_roles @Role [@Role ...]`
        Example: `!set_organiser_roles @Hunt Helpers`

        Run with no roles to clear the list and go back to name matching
        (Organiser/Organisers/Organizer/Organizers). Admins/mods only."""
        await self._set_tier_roles(ctx, "organiser_role_ids", roles, "organiser")

    @commands.hybrid_command(description="Show which roles grant each tier (admin only)",)
    @is_admin()
    async def show_auth_roles(self, ctx):
        """*Report which roles currently grant the admin and organiser tiers.*

        Usage: `!show_auth_roles`
        Says whether each tier is pinned to role ids or still matching by name.
        Admins/mods only."""
        lines = []
        for label, tier in (("Admin", ADMIN_ROLES),
                            ("Organiser", ORGANISER_ROLE_NAMES)):
            configured = role_gate.tier_is_pinned(ctx.guild, tier)
            ids = role_gate.tier_role_ids(ctx.guild, tier)
            named = ", ".join(
                sorted(r.name for r in ctx.guild.roles if r.id in ids)) or "(none)"
            how = "pinned by id" if configured else "matched by NAME (not pinned)"
            lines.append(f"**{label}** — {how}: {named}")
        lines.append("")
        lines.append("Pin a tier with `!set_admin_roles` / `!set_organiser_roles`. "
                     "While a tier is matched by name, anyone who can create a role "
                     "can grant themselves that tier.")
        await ctx.send("\n".join(lines))

    @commands.hybrid_command(description="Set or clear the hub's team-server template link (admin only)")
    @commands.guild_only()
    @is_admin()
    async def set_template_link(self, ctx, link: str = ""):
        """*Set (or, with no argument, clear) this hub's team-server template link.*

        Usage: `!set_template_link [link]`
        Example: `!set_template_link https://discord.new/abc123`
        Accepts `https://discord.new/<code>`, `discord.new/<code>` or
        `https://discord.com/template/<code>`; stored as the discord.new form.
        Run in the bot channel. Admins/mods only."""
        if not (await self.check_is_bot_channel(ctx)):
            return
        canonical = ""
        if link.strip():
            canonical = template_link.normalise(link)
            if canonical is None:
                await ctx.send(f":x: That isn't a template link. Accepted forms: {template_link.ACCEPTED_FORMS}")
                return
        settings = GuildSettingsDb.get(ctx.guild.id)
        if settings is None:
            await ctx.send(":x: This server has no guild settings yet.")
            return
        settings.template_link = canonical
        GuildSettingsDb.commit(settings)  # also refreshes cached_settings
        if canonical:
            await ctx.send(f":white_check_mark: Template link set: {canonical}")
        else:
            await ctx.send(":white_check_mark: Template link cleared.")

    @commands.hybrid_command(description="Post the hub's team-server template link to the admin channel (admin only)")
    @commands.guild_only()
    @is_admin()
    async def show_template_link(self, ctx):
        """*Post the team-server template link in the hub's admin channel.*

        Usage: `!show_template_link`
        The link is posted only in the admin channel (never in the channel you
        ran this in). Admins/mods only."""
        settings = GuildSettingsDb.get(ctx.guild.id)
        link = settings.template_link if settings else ""
        if not link:
            await ctx.send(":information_source: No template link is set. Set one with `!set_template_link <link>`.")
            return
        channel, reason = hub_channel.find(ctx.guild)
        if channel is None:
            await ctx.send(f":x: Not posted: {reason}.")
            return
        sent = await hub_channel.post(
            ctx.guild,
            f":hammer_and_wrench: Team-server template: {link}\n"
            f"If the Discord app ignores it, open {template_link.browser_url(link)} in a browser.")
        if sent:
            await ctx.send(f":white_check_mark: Posted in #{channel.name}.")
        else:
            await ctx.send(f":x: Could not post in #{channel.name}.")

    @commands.hybrid_command(description="Show channel settings (admin only)",)
    @is_admin()
    async def show_settings(self, ctx):
        """*Dump the raw settings for this channel's object (puzzle/round/hunt/guild) as JSON.*

        Usage: `!show_settings`
        Example: `!show_settings`
        For debugging. Admins/mods only."""
        settings = self.get_settings(ctx)
        assert settings is not None  # every channel type resolves a settings object
        await ctx.send(f"```json\n{settings.to_json(indent=2)}```")

    @commands.hybrid_command(description="Show puzzle settings (admin only)",)
    @is_admin()
    async def show_puzzle_settings(self, ctx):
        """*Dump this puzzle's stored fields and values for debugging.*

        Usage: `!show_puzzle_settings`
        Example: `!show_puzzle_settings`
        Run in a puzzle channel. Admins/mods only."""
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
    @is_admin()
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
            hunt = self.get_hunt(ctx)
            assert hunt is not None  # non-Guild channels always resolve their hunt
            await self.service.update_hunt_settings(hunt, username=username, password=password)
            await self.service.notifier.refresh_info(ctx.guild, hunt)
            return await ctx.send(
                f":white_check_mark: I've updated the login details for hunt {hunt.name}"
            )

        await ctx.send(f"Unable to parse details {arg}, try using `!set_login username:password`")

    @commands.command()
    @is_admin()
    async def set_start(self,ctx,timestamp:int):
        """*Set the hunt's start time from a UNIX timestamp (seconds since epoch).*

        Usage: `!set_start timestamp`
        Example: `!set_start 1767225600`
        Run in any channel belonging to the hunt. Admins/mods only."""
        if self.get_channel_type(ctx) == "Guild":
            await ctx.send(":x: This must be sent in a channel related to a hunt")
            return

        hunt = self.get_hunt(ctx)
        assert hunt is not None  # non-Guild channels always resolve their hunt
        await self.service.update_hunt_settings(hunt, start_timestamp=timestamp)
        await self.service.notifier.refresh_info(ctx.guild, hunt)
        return await ctx.send(
            f":white_check_mark: I've updated the start time for hunt {hunt.name}"
        )

    @commands.command(aliases=["update_setting","update_puzzle_setting","update_puzzle_settings"])
    @is_admin()
    async def update_settings(self, ctx, setting_key: str, setting_value: str):
        """*Set a single setting field on this channel's object (puzzle/round/hunt/guild).*

        Usage: `!update_settings key value`
        Example: `!update_settings discord_bot_emoji ":dog:"`
        Quote a value containing spaces. Inspect available keys with `!show_settings`.
        Use the `!update_puzzle_settings` alias to target the puzzle in this channel.
        The authorisation and guild-identity keys (`admin_role_ids`,
        `organiser_role_ids`, `guild_id`, `guild_name`) are refused here — set the
        role ids with `!set_admin_roles` / `!set_organiser_roles`, which validate
        them and won't lock you out. Admins/mods only."""
        # Refused before anything is resolved or written, so a protected key can
        # never reach a commit (see PROTECTED_SETTING_KEYS).
        if setting_key in PROTECTED_SETTING_KEYS:
            await ctx.send(
                f":x: `{setting_key}` cannot be set from this command — it would "
                f"skip the checks that keep this guild's settings recoverable. "
                f"{PROTECTED_SETTING_KEYS[setting_key]}")
            return
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

            # This escape-hatch command bypasses puzzle_service, so it must do its
            # own pin refresh + board push (the proper !status/!type/!priority
            # paths get both for free via service.update_puzzle_field).
            ch_type = self.get_channel_type(ctx)
            if ch_type in ("Hunt", "Metapuzzle", "Capstone", "Runaround", "Group", "Puzzle"):
                # Best-effort, mirroring the web-side web_refresh_info seam: a
                # hand-broken channel with no pinned info message would otherwise
                # raise IndexError here (channel_pins[-1]) after the settings
                # already committed above, leaving the user with an error and no ack.
                try:
                    await self.info(ctx, update=True)
                except Exception:
                    logger.exception(
                        "update_settings: failed to refresh pinned info after setting %s",
                        setting_key,
                    )
            hunt = self.get_hunt(ctx)
            if hunt is not None:
                await self.service.notifier.board_changed(hunt)

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
        tag_group = self.get_tag_group(ctx)
        hunt = self.get_hunt(ctx)
        if self.get_channel_type(ctx) in (*GROUP_TYPES, "Puzzle") and tag_group is not None:
            round_puzzles = PuzzleJsonDb.get_all_from_tag_group(tag_group.id)
            all_puzzles[tag_group.id] = {
                        'name': tag_group.name,
                        'puzzles': round_puzzles
                    }
            embed_title = f"Puzzles in Round {tag_group.name}"
        elif hunt is not None:
            # Outside a round but still inside a hunt (e.g. the hunt's own
            # channel): list every round. This used to point at the website
            # because an embed could not hold a whole hunt; Components V2
            # spills across messages instead, so the listing is viable here.
            for group in TagGroupJsonDb.get_all(hunt.id):
                all_puzzles[group.id] = {
                    'name': group.name,
                    'puzzles': PuzzleJsonDb.get_all_from_tag_group(group.id),
                }
            embed_title = f"Puzzles in {hunt.name}"
        else:
            # No hunt context at all - there is genuinely nothing to list.
            await ctx.send(":x: Please go to https://quarantinedecrypters.com for an overview of all puzzles")

        # Components V2: one section per round, rendered full width. The embed
        # version chopped each round at 15 puzzles into "part N" fields purely
        # to stay under the 1024-character embed field limit - a TextDisplay
        # has no per-field limit, so a round stays a single list.
        sections = []
        for round_id in all_puzzles:
            lines = []
            for puzzle in all_puzzles[round_id]['puzzles']:
                line = f"{puzzle.name} - {puzzle.channel_mention}"
                if puzzle.puzzle_type:
                    line += f" type:{puzzle.puzzle_type}"
                if puzzle.solution:
                    line += f" sol:**{puzzle.solution}**"
                elif puzzle.status:
                    line += f" status:{format_status(puzzle.status)}"
                lines.append(line)
            if lines:
                sections.append((all_puzzles[round_id]['name'], "\n".join(lines)))

        # Three renderings, one switch, so they can be compared in Discord:
        #   "plain"   message content - the only one that uses the FULL window
        #             width, but capped at 2000 chars so more messages
        #   "text"    Components V2, no Container
        #   "card"    Components V2 in a Container (accent bar)
        # Measured on a ~2000px window, "text" and "card" are both stuck in the
        # same narrow column as the embed this replaced - components do not
        # escape it. Only "plain" is actually wide.
        style = "plain"
        if style == "plain":
            await send_plain(ctx, build_plain_messages(
                title=embed_title, sections=sections,
            ))
        else:
            await send_views(ctx, build_sectioned_views(
                title=embed_title,
                sections=sections,
                boxed=(style == "card"),
                accent_colour=discord.Colour.blurple(),
            ))

    async def get_or_create_channel(
        self, guild, category: discord.CategoryChannel, channel_name: str, channel_type, **kwargs
    ):
        """Retrieve given channel by name/category or create one."""
        return await _channels.get_or_create_channel(guild, category, channel_name, channel_type, **kwargs)


    async def _refresh_pinned_info(self, channel: discord.TextChannel, *, embed=None, embeds=None):
        """Edit the bot's own pinned info message in ``channel``, recreating it if missing.

        #20: bang commands used to assume ``(await channel.pins())[-1]`` was always
        our info message and edited it unconditionally. If a mod unpinned/deleted it
        (or the pin isn't the bot's own — e.g. some other message got pinned after
        it, or the channel has >50 pins so [-1] isn't the oldest), that raised
        IndexError/Forbidden after the DB mutation had already committed. Find our
        pin by author instead, and re-send + re-pin rather than crash if it's gone.
        """
        try:
            pins = await channel.pins()
        except discord.Forbidden:
            logger.exception("_refresh_pinned_info: cannot fetch pins in channel %s", channel.id)
            pins = []
        bot_pin = next((m for m in reversed(pins) if m.author.id == self.bot.user.id), None)
        if bot_pin is not None:
            try:
                return await (bot_pin.edit(embeds=embeds) if embeds is not None else bot_pin.edit(embed=embed))
            except discord.Forbidden:
                logger.exception(
                    "_refresh_pinned_info: cannot edit pin %s in channel %s", bot_pin.id, channel.id
                )
        try:
            if embeds is not None:
                message = await channel.send(embeds=embeds)
            else:
                assert embed is not None  # callers pass exactly one of embed/embeds
                message = await channel.send(embed=embed)
            await message.pin()
            return message
        except discord.Forbidden:
            logger.exception("_refresh_pinned_info: cannot send/pin replacement in channel %s", channel.id)
            return None

    async def send_initial_hunt_channel_messages(self, ctx, channel: discord.TextChannel, **kwargs):
        hunt = kwargs.get('hunt',self.get_hunt(ctx))
        assert hunt is not None  # hunt channel welcomes always have their hunt row
        embed = discord.Embed(
            description=f"""Welcome to the general channel for {hunt.name}!"""
        )
        embed.add_field(
            name="Overview",
            value="This channel "
            " has info about the hunt itself and it is where you will create new round channels",
            inline=False,
        )
        files_mode = hunt.sheet_mode == "files"
        sheet_label = "Reference sheet" if files_mode else "Google Sheet"
        puzzle_links = (f"""The following are some useful links and information:
                        • [Hunt Website]({urls.normalize_external_url(hunt.url)})
                        • Hunt Start Time: <t:{hunt.start_timestamp}:f>
                        • [{sheet_label}](https://docs.google.com/spreadsheets/d/{hunt.google_sheet_id})
                        """)
        folder = drive_folder_link(hunt.drive_folder_id) if files_mode else None
        if folder:
            puzzle_links += f"""• [Drive folder]({folder})
            """
        if hunt.solved_sheet_id and not files_mode:
            puzzle_links += f"""• [Solved Puzzles Sheet](https://docs.google.com/spreadsheets/d/{hunt.solved_sheet_id})
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
            value=flush_left(puzzle_links),
            inline=False,
        )
        embed.add_field(
                    name="Useful commands",
                    value=flush_left("""The following are some useful commands:
        • `!r <round name>` : Create a new round with a metapuzzle channel
        • `!mp <metapuzzle name>` : Create a new metapuzzle
        • `!group <group name>` : Create a group without a metapuzzle channel
        • `!info` : Repeat this message
        • `!set_login username:password` : Set the login details for the hunt if shared
        """),
                    inline=False,
                )
        if kwargs.get("update", False):
            return await self._refresh_pinned_info(channel, embed=embed)
        else:
            return await channel.send(embed=embed)

    def _add_puzzle_links_field(self, ctx, embed, puzzle, hunt, guild_data=None):
        spreadsheet_url = urls.spreadsheet_url(self.get_puzzle_sheet(ctx, puzzle, hunt),
                                               puzzle.google_page_id) if hunt.google_sheet_id else "?"
        puzzle_url = urls.normalize_external_url(puzzle.url) if puzzle.url else "?"
        puzzle_links = (f"""    • [Puzzle link]({puzzle_url})
                                • [Google Sheet]({spreadsheet_url})
                                """)
        if hunt.sheet_mode == "files":
            # The puzzle's link above is its own file; the hunt-wide one is here.
            puzzle_links += f"""• [Reference sheet]({spreadsheet_link(hunt.google_sheet_id)})
                                """
        puzzle_links += f"""• [Overview Website]({self._overview_url(ctx, hunt, guild_data)})"""
        embed.add_field(
            name="Puzzle Links",
            value=flush_left(puzzle_links),
            inline=False,
        )

    # Bullets shared verbatim between the meta and puzzle channel pins' solve-related
    # command field (the puzzle pin also gets a leading !p line — see
    # _solve_commands_field_value). Single source so the two pins can't drift.
    _SOLVE_COMMAND_BULLETS = (
        "`!s SOLUTION` will mark this puzzle as solved and archive this channel",
        "`!ps SOLUTION` will mark this puzzle as partially solved, multiple answers will be "
        "separated by slashes, mark as completed with !s SOLUTION with the final solution or "
        "just !s if all answers entered",
        "`!mark_as_complete` will mark the puzzle solved with a tick (for interactions or "
        "puzzles without a traditional answer)",
        "`!add_sheet <name>` will add an additional working sheet to the puzzle, for "
        "sub-puzzles or just tidier work (name is optional)",
    )

    @classmethod
    def _solve_commands_field_value(cls, *, prefix=()):
        bullets = [*prefix, *cls._SOLVE_COMMAND_BULLETS]
        return "\n".join(f"• {b}" for b in bullets)

    # Bullets shared between the meta and puzzle channel pins' "Puzzle commands" field.
    # The puzzle pin additionally gets !info right after !link (redundant on the meta
    # pin, which already lists it under General Commands) and !feeds/!informs/
    # !rename_puzzle appended at the end (meta-only commands live elsewhere). Single
    # source so the two pins can't drift the way they had (#78).
    _PUZZLE_COMMAND_COMMON_BULLETS = (
        "`!link <url>` will update the link to the puzzle on the hunt website",
        "`!type <puzzle type>` will mark the type of the puzzle",
        "`!priority <priority>` will mark the priority of the puzzle",
        "`!status <status>` will update the status of the puzzle",
        "`!note <note>` can be used to leave a note about ideas/progress",
        "`!notes` can be used to see the notes that have been left",
        "`!erase_note <note number>` can be used to erase the specified note",
        "`!clue <text>` logs a clue/observation to the hunt's evidence sheet "
        "(`!clues` for several at once)",
    )

    @classmethod
    def _puzzle_commands_field_value(cls, *, insert_after_first=(), append=()):
        common = cls._PUZZLE_COMMAND_COMMON_BULLETS
        bullets = [common[0], *insert_after_first, *common[1:], *append]
        return "\n".join(f"• {b}" for b in bullets)

    @classmethod
    def _add_metapuzzle_command_fields(cls, embed):
        """Add the shared Metapuzzle/Puzzle command-help fields to an embed."""
        embed.add_field(
                        name="Metapuzzle commands",
                        value=cls._solve_commands_field_value(),
                        inline=False,
                    )
        embed.add_field(
            name="Puzzle commands",
            value=cls._puzzle_commands_field_value(),
            inline=False,
        )

    def _build_embed_list(self, ctx=None, embed: Optional[discord.Embed] = None, puzzle: Optional[PuzzleData] = None, hunt: Optional[HuntData] = None):
        """Wrap the main embed plus an optional additional-sheets embed in a list."""
        assert embed is not None and puzzle is not None  # callers always pass both; defaults are keyword-style only
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
            embed_list.append(self._build_mini_list_embed(puzzle, minis, hunt))
        return embed_list

    def _build_mini_list_embed(
        self,
        puzzle: PuzzleData,
        minis,
        hunt: Optional[HuntData] = None,
        *,
        title: str = "Mini-puzzles",
        overflow_hint: str = "use `!list_minis`",
    ) -> discord.Embed:
        """Render the mini list into one embed that respects Discord's limits.

        A single 1024-char field used to hold the whole list, and a minimeta with
        ~8 minis overflowed it - Discord then rejected the *entire* pinned-info
        edit (50035), so `!add_mini`, `!info` and `!solve` all failed on that
        channel. The list goes in the description (4096) and spills into
        "(cont.)" fields, with anything past the per-embed budget counted rather
        than silently dropped.
        """
        continuation_name = f"{title} (cont.)"[:FIELD_NAME_LIMIT]
        overflow_name = "More"
        # The title, the field names and the overflow note all spend from the
        # same 6000-char embed budget as the values. Reserve the fixed costs
        # here; per-field names are charged as they are actually emitted.
        reserved = (
            len(title)
            + len(overflow_name)
            + len(f"…and 999 more — {overflow_hint}")
        )

        fit = split_lines_for_embed(
            self.create_mini_list(puzzle, minis, hunt),
            reserved=reserved,
            field_name_cost=len(continuation_name),
            max_fields=MAX_FIELDS - 1,  # leave one slot for the overflow note
        )

        embed = discord.Embed(title=title, description=fit.description)
        for value in fit.fields:
            embed.add_field(name=continuation_name, value=value, inline=False)
        if fit.dropped:
            embed.add_field(
                name=overflow_name,
                value=f"…and {fit.dropped} more — {overflow_hint}",
                inline=False,
            )
        return embed

    @staticmethod
    def _add_puzzle_status_fields(embed, puzzle):
        """Add Status/Solution/Type/Priority fields for a puzzle to an embed."""
        try:
            embed.add_field(name="Status", value=format_status(puzzle.status) or "?", inline=False,)
            if puzzle.solution:
                embed.add_field(name="Solution", value=puzzle.solution, inline=False,)
            embed.add_field(name="Type", value=puzzle.puzzle_type or "?", inline=False,)
            embed.add_field(name="Priority", value=format_priority(puzzle.priority) or "?", inline=False,)
        except Exception:
            logger.exception("Failed to add puzzle status fields to embed")

    async def send_initial_metapuzzle_channel_messages(self, ctx, channel: discord.TextChannel, **kwargs) -> Optional[discord.Message]:
        """Send intro message on a meta channel"""
        tag_group = kwargs.get('tag_group', self.get_tag_group(ctx))
        hunt = kwargs.get('hunt', self.get_hunt(ctx))
        puzzle = kwargs.get('puzzle', self.get_puzzle(ctx))
        # Meta channel welcomes always resolve their group, hunt and target puzzle.
        assert tag_group is not None and hunt is not None and puzzle is not None

        # Capstone/runaround share this welcome with plain metas, but their default
        # tagging edge differs (TYPE_DEFAULTS): a capstone's members should use
        # !informs, not !feeds_only/!feeds, or tagging one silently shifts the
        # group toward meta semantics (#70). Word the channel kind and the
        # recommended tagging command to match the group's actual type.
        kind = {"Capstone": "capstone", "Runaround": "runaround"}.get(tag_group.type, "meta")
        rename_target = "metapuzzle" if kind == "meta" else kind
        if tag_group.type == "Capstone":
            tagging_hints = (
                f"        • `!informs {tag_group.group_code}` when used in another puzzle channel will tag it as informing this capstone — this is the edge capstone members should use.\n"
                f"        • `!feeds_only {tag_group.group_code}` / `!feeds {tag_group.group_code}` tag a puzzle as a standard feeder instead; do not use these for capstone members.\n"
            )
        else:
            tagging_hints = (
                f"        • `!feeds_only {tag_group.group_code}` when used in another puzzle channel will add it to this {kind} as a feeder and move it to this category.\n"
                f"        • `!feeds {tag_group.group_code}` when used in another puzzle channel will add it to this {kind} as a feeder but not move it (e.g. if a puzzle feeds multiple metas).\n"
                f"        • `!informs {tag_group.group_code}` when used in another puzzle channel will tag it as informing this {kind} — it contributes without being a standard feeder (e.g. capstones / cross-references).\n"
            )

        embed = discord.Embed(
            description=f"""Welcome to the {kind} channel for {tag_group.name}!"""
        )
        embed.add_field(
            name="Overview",
            value=f"This channel is for general discussion of the round and working on the {kind}, please note the first two column of the sheet are reserved for the puzzle titles and answers and will be overwritten by the bot!",
            inline=False,
        )
        embed.add_field(
                        name="General Commands",
                        value=flush_left(f"""The following may be useful discord commands:
        • `!p <puzzle-name>` will add a puzzle to this {kind}.
        • `!list` : List all puzzles in this {kind}.
        • `!info` will re-post this message.
{tagging_hints}        • `!remove_from {tag_group.group_code}` when used in another puzzle channel will remove it from this {kind}.
        • `!sync` (organisers/admins) when used in this channel will assign all the puzzles in the category to the {kind}.
        • `!rename_meta <puzzle-name>` (organisers/admins) will rename the {rename_target}.
        """),
                        inline=False,
                    )

        self._add_metapuzzle_command_fields(embed)
        self._add_puzzle_links_field(ctx, embed, puzzle, hunt, kwargs.get('guild_data'))
        self._add_puzzle_status_fields(embed, puzzle)
        embed_list = self._build_embed_list(ctx=ctx, embed=embed, puzzle=puzzle, hunt=hunt)

        if kwargs.get("update", False):
            return await self._refresh_pinned_info(channel, embeds=embed_list)
        else:
            return await channel.send(embeds=embed_list)

    async def send_initial_group_channel_messages(self, ctx, channel: discord.TextChannel, **kwargs) -> Optional[discord.Message]:
        """Send intro message on a group/round channel"""
        tag_group = kwargs.get('tag_group', self.get_tag_group(ctx))
        hunt = kwargs.get('hunt', self.get_hunt(ctx))
        assert tag_group is not None  # group channel welcomes always have their group row
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
                        value=flush_left(f"""The following may be useful discord commands:
        • `!p <puzzle-name>` will add a puzzle to this round.
        • `!list` : List all puzzles in this round.
        • `!info` will re-post this message.
        • `!feeds_only {tag_group.group_code}` when used in another puzzle channel will add it to this round as a feeder and move it to this category.
        • `!feeds {tag_group.group_code}` when used in another puzzle channel will add it to this round as a feeder but not move it (e.g. if a puzzle feeds multiple rounds).
        • `!informs {tag_group.group_code}` when used in another puzzle channel will tag it as informing this round — it contributes to the round without being a standard feeder.
        • `!remove_from {tag_group.group_code}` when used in another puzzle channel will remove it from this round.
        • `!sync` (organisers/admins) when used in this channel will assign all the puzzles in the category to the round.
        """),
                        inline=False,
                    )
        embed.add_field(name="Overview Website", value=self._overview_url(ctx, hunt, kwargs.get('guild_data')),
                        inline=False)
        if kwargs.get("update", False):
            return await self._refresh_pinned_info(channel, embed=embed)
        else:
            return await channel.send(embed=embed)

    async def send_initial_puzzle_channel_messages(self, ctx, channel: discord.TextChannel, **kwargs) -> Optional[discord.Message]:
        """Send intro message on a puzzle channel"""
        puzzle = kwargs.get("puzzle", self.get_puzzle(ctx))
        hunt = kwargs.get('hunt', self.get_hunt(ctx))
        assert puzzle is not None and hunt is not None  # puzzle channel welcomes always have both
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
                        value=self._solve_commands_field_value(
                            prefix=("`!p <puzzle-name>` will add a puzzle to this round/meta puzzle.",)
                        ),
                        inline=False,
                    )
        embed.add_field(
            name="Puzzle commands",
            value=self._puzzle_commands_field_value(
                insert_after_first=("`!info` will re-post this message",),
                append=(
                    "`!feeds <meta_code>` tags this puzzle as a feeder of a meta/round (code is "
                    "in the meta's pinned message); `!informs <meta_code>` tags it as informing "
                    "one without being a standard feeder",
                    "`!rename_puzzle <puzzle-name>` (organisers/admins) will rename the puzzle.",
                ),
            ),
            inline=False,
        )
        self._add_puzzle_links_field(ctx, embed, puzzle, hunt, kwargs.get('guild_data'))
        self._add_puzzle_status_fields(embed, puzzle)

        embed_list = self._build_embed_list(ctx=ctx, embed=embed, puzzle=puzzle, hunt=hunt)

        if kwargs.get("update",False):
            return await self._refresh_pinned_info(channel, embeds=embed_list)
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
        hunt = self.get_hunt(ctx)
        assert hunt is not None  # puzzle channels always resolve their hunt
        spreadsheet_url = puzzle_location(hunt, puzzle_data).url or "?"
        embed.add_field(name="Google Drive", value=spreadsheet_url)
        embed.add_field(name="Status", value=format_status(puzzle_data.status) or "?")
        embed.add_field(name="Type", value=puzzle_data.puzzle_type or "?")
        embed.add_field(name="Priority", value=format_priority(puzzle_data.priority) or "?")
        await ctx.send(embed=embed)
        await self.info(ctx, update=True)

    @commands.hybrid_command(aliases=["rename_meta"], description="Rename this puzzle, its channel and its sheet tab")
    @is_organiser()
    @with_puzzle_mutex(wait=True)
    async def rename_puzzle(self, ctx, *, puzzle_name: str):
        """*Rename this puzzle, its channel and its sheet tab.*

        Usage: `!rename_puzzle new name`
        Example: `!rename_puzzle the actual clueless crossword`
        Run in the puzzle's channel. Organisers/admins only. Alias: `!rename_meta`."""
        puzzle = self.get_puzzle(ctx)
        if puzzle:
            hunt = self.get_hunt(ctx)
            assert hunt is not None  # puzzle channels always resolve their hunt
            if PuzzleJsonDb.check_duplicates_in_hunt(puzzle_name, hunt.id, exclude_puzzle_id=puzzle.id):
                return await ctx.send(
                    f":exclamation: Puzzle **{puzzle_name}** already exists in this hunt or will lead to a duplicate channel name, please use a different name")
            try:
                await self.service.rename_puzzle(
                    ctx.guild, hunt, puzzle, puzzle_name
                )
            except SheetRefreshError as exc:
                await self._warn_stale_sheets(ctx, exc)
                return
            await ctx.channel.send(":white_check_mark: I've updated the puzzle and channel names")
        else:
            await self.send_not_puzzle_channel(ctx)

    @commands.hybrid_command(aliases=["rename_round", "rename_metapuzzle"],
                             description="Rename this group, its category and channel")
    @is_organiser()
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
        hunt = self.get_hunt(ctx)
        assert hunt is not None  # group channels always resolve their hunt
        if TagGroupJsonDb.check_duplicates_in_hunt(
                group_name, hunt, exclude_group_id=tag_group.id):
            await ctx.send(
                f":exclamation: A group called **{group_name}** already exists in "
                f"this hunt, please use a different name")
            return
        await self.service.rename_group(
            ctx.guild, hunt, tag_group, group_name)
        await ctx.channel.send(":white_check_mark: I've renamed the group.")

    @commands.hybrid_command(description="Set or update the puzzle's URL on the hunt site")
    @with_puzzle_mutex(wait=True)
    async def link(self, ctx, *, url: str):
        """*Set or update the puzzle's URL (the link to the puzzle on the hunt site).*

        Usage: `!link url`
        Example: `!link https://hunt.example.com/puzzles/crossword`
        Run in the puzzle's channel."""
        puzzle = self.get_puzzle(ctx)
        if puzzle:
            hunt = self.get_hunt(ctx)
            await self.service.set_puzzle_url(ctx.guild, hunt, puzzle, url)
            await self.send_state(
                ctx, ctx.channel, puzzle, description=":white_check_mark: I've updated:" if url else None
            )
        else:
            await self.send_not_puzzle_channel(ctx)

    @commands.hybrid_command(description="Update puzzle status")
    # Built from STATUSES so a status can never again be offered here but missing
    # from the canonical list, or vice versa ("needs submission" was missing).
    # Choice name is the Title Case label; value is the stored lowercase spelling.
    @app_commands.choices(status=[
        app_commands.Choice(name=format_status(_s), value=_s) for _s in STATUSES
    ])
    @with_puzzle_mutex(wait=True)
    async def status(self, ctx, *, status: str):
        """*Set this puzzle's working status.*

        Usage: `!status value`
        Example: `!status stuck`
        One of: Unstarted, In Progress, Stuck, Needs Extraction, Needs Submission,
        Solved, Backsolved.
        Run in the puzzle's channel."""
        puzzle = self.get_puzzle(ctx)
        if puzzle is None:
            await self.send_not_puzzle_channel(ctx)
            return
        hunt = self.get_hunt(ctx)
        try:
            await self.service.update_puzzle_field(hunt, puzzle, "status", status)
        except ValueError as exc:
            await ctx.send(f":exclamation: {exc}")
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
        puzzle = self.get_puzzle(ctx)
        if puzzle is None:
            await self.send_not_puzzle_channel(ctx)
            return

        message = "Showing notes left by users!"
        if note:
            hunt = self.get_hunt(ctx)
            # Slash invocations have no ctx.message (so no jump_url); attribute to the
            # author's display name instead, matching the web surface's attribution style.
            attribution = ctx.message.jump_url if ctx.message else ctx.author.display_name
            await self.service.add_note(hunt, puzzle, note, attribution)
            message = (
                f"Added a new note! Use `!erase_note {len(puzzle.notes)}` to remove the note if needed. "
                f"Check `!notes` for the current list of notes."
            )

        if puzzle.notes:
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

        puzzle = self.get_puzzle(ctx)
        if puzzle is None:
            await self.send_not_puzzle_channel(ctx)
            return

        hunt = self.get_hunt(ctx)
        erased = await self.service.erase_note(hunt, puzzle, note_index)
        description = f"Erased note {note_index}" if erased else f"Unable to find note {note_index}"

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

    async def _warn_stale_sheets(self, ctx, exc):
        """Uniform surfacing of a SheetRefreshError from a bang edge: the mutation is
        saved and the affected meta sheet(s) are badged on the dashboard."""
        await ctx.send(f":warning: {exc}")

    def _overview_url(self, ctx=None, hunt: Optional[HuntData] = None, guild_data: Optional[GuildSettings] = None):
        """Overview website URL for a hunt (falls back to the numeric id)."""
        assert hunt is not None  # callers always pass it; default is keyword-style only
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

        guild_data = self.get_guild_data(ctx)
        assert guild_data is not None  # guilds running hunt commands always have a settings row
        emoji = guild_data.discord_bot_emoji
        from bot.core.notifier import build_solution_update_embed
        embed = build_solution_update_embed(
            "partial", emoji, solution,
            "If you need to clear the partial solutions then use !unsolve and they will all be wiped. ",
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

        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            await self.send_not_puzzle_channel(ctx)
            return
        elif puzzle.solved:
            await ctx.send(":x: This puzzle appears to already be solved")
            return

        await self._partial_impl(ctx, *answer.split())


    async def _solve_impl(self, ctx, *args):
        from bot.core.notifier import build_solved_embed
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

        guild_data = self.get_guild_data(ctx)
        assert guild_data is not None  # guilds running hunt commands always have a settings row
        emoji = guild_data.discord_bot_emoji
        embed = build_solved_embed(
            emoji, puzzle,
            "If the solution was entered incorrectly, please use `!update_solution` to update it, if the puzzle isn't actually solved at all then use `!unsolve`.  \nGive me a sec to tidy up the sheets."
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

        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            await self.send_not_puzzle_channel(ctx)
            return
        elif puzzle.solved:
            await ctx.send(":x: This puzzle appears to already be solved")
            return
        await self._solve_impl(ctx, *solution.split())


    async def _change_or_add_solution_impl(self, ctx, answer, *, mode):
        """Shared tail for !change_solution and !add_solution: not-a-puzzle guard,
        unsolved fallback (to _solve_impl/_partial_impl), normalize, empty-solution
        guard, service call, SheetRefreshError capture, celebration embed, pin
        refresh. `mode` is "change" or "add" — the two commands differ only in the
        service method, the embed title, and the unsolved fallback (#80: these had
        drifted into two ~30-line near-copies, missing the Follow-up field on
        !add_solution that the other three variants all include)."""
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            await self.send_not_puzzle_channel(ctx)
            return
        elif not puzzle.solved:
            if mode == "change":
                await self._solve_impl(ctx, *answer.split())
            else:
                await self._partial_impl(ctx, *answer.split())
            return

        solution = self._normalize_solution(answer.split())

        hunt = self.get_hunt(ctx)

        if solution == "":
            await ctx.send(":x: Nice try, but you need to give a solution!")
            return

        sheet_warning = None
        try:
            if mode == "change":
                await self.service.change_solution(self.get_guild(ctx), hunt, puzzle, solution)
            else:
                await self.service.add_solution(self.get_guild(ctx), hunt, puzzle, solution)
        except SheetRefreshError as exc:
            sheet_warning = exc

        guild_data = self.get_guild_data(ctx)
        assert guild_data is not None  # guilds running hunt commands always have a settings row
        emoji = guild_data.discord_bot_emoji
        from bot.core.notifier import build_solution_update_embed
        embed = build_solution_update_embed(
            mode, emoji, puzzle.solution,
            "If the solution was entered incorrectly, please use `!update_solution` to update it, "
            "if the puzzle isn't actually solved at all then use `!unsolve`. ",
        )
        await ctx.send(embed=embed)
        await self.info(ctx, update=True)
        if sheet_warning:
            await self._warn_stale_sheets(ctx, sheet_warning)

    @commands.hybrid_command(aliases=["update_solution","update_answer"], description="Replace this puzzle's recorded solution (e.g. fix a typo)")
    @with_puzzle_mutex(wait=False)
    async def change_solution(self, ctx, *, answer: str = ""):
        """*Replace this puzzle's recorded solution (e.g. to fix a typo).*

        Usage: `!change_solution answer`
        Example: `!change_solution BLUE WHALE`
        If the puzzle wasn't solved yet it solves it. Run in the puzzle's channel.
        Aliases: `!update_solution`, `!update_answer`."""
        await self._change_or_add_solution_impl(ctx, answer, mode="change")

    @commands.hybrid_command(aliases=["add_to_solution"], description="Add an extra solution to an already-solved puzzle")
    @with_puzzle_mutex(wait=False)
    async def add_solution(self, ctx, *, answer: str = ""):
        """*Add an extra solution to an already-solved puzzle (e.g. a second valid answer).*

        Usage: `!add_solution answer`
        Example: `!add_solution GREY WHALE`
        If the puzzle isn't solved yet this records a partial instead. Run in the puzzle's channel.
        Alias: `!add_to_solution`."""
        await self._change_or_add_solution_impl(ctx, answer, mode="add")

    @commands.hybrid_command(name="mark_as_complete", aliases=["mark_as_solved","complete"], description="Mark this puzzle solved with a tick when there's no text answer")
    @with_puzzle_mutex(wait=False)
    async def mark_as_complete(self, ctx):
        """*Mark this puzzle solved with a tick (✅) when there's no text answer to record.*

        Usage: `!mark_as_complete`
        Example: `!mark_as_complete`
        Run in an unsolved puzzle channel. Aliases: `!mark_as_solved`, `!complete`."""

        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            await self.send_not_puzzle_channel(ctx)
            return
        elif puzzle.solved:
            await ctx.send(":x: This puzzle appears to already be solved")
            return
        await self._solve_impl(ctx, "✅")

    @commands.hybrid_command(aliases=["u"], description="Undo a solve: clear the solution and move back to unsolved")
    @with_puzzle_mutex(wait=False)
    async def unsolve(self, ctx):
        """*Undo a solve: clear the solution and move the channel back to unsolved.*

        Usage: `!unsolve`
        Example: `!unsolve`
        Run in a solved puzzle's channel. Also clears partial solutions. Alias: `!u`."""

        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            await self.send_not_puzzle_channel(ctx)
            return
        elif not puzzle.solved and not puzzle.solution:
            await ctx.send(":x: This puzzle appears to not be solved")
            return

        hunt = self.get_hunt(ctx)
        prev_solution = puzzle.solution

        guild_data = self.get_guild_data(ctx)
        assert guild_data is not None  # guilds running hunt commands always have a settings row
        emoji = guild_data.discord_bot_emoji
        from bot.core.notifier import build_unsolved_embed
        embed = build_unsolved_embed(
            emoji, prev_solution, "Give me a sec to restore the sheets.")
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
    @is_organiser()
    async def absorb_category(self, ctx):
        """*Move every puzzle channel in this hand-made category into the hunt's general layout (auto-overflow), then delete the emptied category.*

        Usage: `!absorb_category`
        Example: `!absorb_category`
        Run in a puzzle channel of the hand-made category to absorb. Cleanup
        for categories split by hand before the overflow engine existed —
        solved puzzles land on the general solved side, and general splits or
        allocates overflow categories as needed. Refuses categories the bot
        already manages. Channels that aren't this hunt's puzzles stay put
        (and keep the category alive). Organisers/admins only.
        Bang-only: no web-dashboard equivalent, by design."""
        hunt = self.get_hunt(ctx)
        if hunt is None:
            await ctx.send(":x: I can't tell which hunt this is — run this in a puzzle channel of the category.")
            return False
        category = ctx.channel.category
        if category is None:
            await ctx.send(":x: This channel isn't in a category.")
            return False
        try:
            report = await self.service.absorb_category(self.get_guild(ctx), hunt, category)
        except ValueError:
            await ctx.send(":x: That category is already managed by the bot — nothing to absorb.")
            return False
        lines = [f":white_check_mark: Absorbed {report['moved']} channel(s) into the hunt's general layout."]
        if report["skipped"]:
            lines.append(f":grey_question: Left in place (not this hunt's puzzles): {', '.join(report['skipped'])}")
        if report["failed"]:
            lines.append(f":x: Could not move: {', '.join(report['failed'])}")
        lines.append(":broom: Emptied category deleted." if report["deleted"]
                     else ":information_source: Category kept (channels remain in it).")
        await ctx.send("\n".join(lines))
        return not report["failed"]

    @commands.command()
    @is_admin()
    async def archive_round(self, ctx):
        """*Archive this round: copy every channel in the category into threads, then delete the category.*

        Usage: `!archive_round`
        Example: `!archive_round`
        Run in a channel of the round to archive. Admins/mods only.
        Bang-only: no web-dashboard equivalent, by design."""
        hunt_general_channel = self.get_hunt_channel(ctx)
        category = ctx.channel.category
        ignore_channels = []
        if self.get_channel_type(ctx) == 'Hunt':
            ignore_channels = [ctx.channel.name]

        owner, dao = self._owner_for_channel(ctx.channel)
        if owner is not None and category_layout.is_split(owner, dao):
            # A split owner owns several categories (unsolved / '— solved' /
            # '(N)' overflow); archive them ALL — not just the one this
            # command ran in — then delete them and drop the join rows, which
            # nothing else would clean up (archive deletes no DB rows, so no
            # FK cascade fires).
            categories = [c for c in
                          (discord.utils.get(ctx.guild.categories, id=r.category_id)
                           for r in category_layout.group_categories(owner, dao))
                          if c is not None]
            for cat in categories:
                if not await self._archive_category_channels(
                        ctx, cat, hunt_general_channel, ignore_channels,
                        [self.SOLVE_DIVIDER]):
                    return False
            for cat in categories:
                await cat.delete(reason=self.DELETE_REASON)
            assert dao is not None  # _owner_for_channel returns owner and dao together
            dao.delete_for_group(owner.id)
            await hunt_general_channel.send(
                f":white_check_mark: Round {category.name} successfully archived.")
            return True

        success = await self.archive_category(ctx, hunt_general_channel, ignore_channels = ignore_channels, delete_channels=[self.SOLVE_DIVIDER])
        if success:
            await ctx.channel.category.delete(reason=self.DELETE_REASON)
            await hunt_general_channel.send(f":white_check_mark: Round {category.name} successfully archived.")
            return True
        else:
            return False

    @commands.command()
    @is_admin()
    async def archive_category(self, ctx, archive_to = None, ignore_channels = None, delete_channels = None):
        """*Archive every channel in this category into threads on the hunt's general channel.*

        Usage: `!archive_category`
        Example: `!archive_category`
        Run in a channel of the category to archive; defaults to archiving into the hunt
        general channel. Admins/mods only. Usually called via `!archive_round`.
        Bang-only: no web-dashboard equivalent, by design."""
        if ignore_channels is None:
            ignore_channels = []
        if delete_channels is None:
            delete_channels = []
        # If archive_to not set then archive to the main hunt channel
        if archive_to is None:
            archive_to = self.get_hunt_channel(ctx)
        return await self._archive_category_channels(
            ctx, ctx.channel.category, archive_to, ignore_channels, delete_channels)

    async def _archive_category_channels(self, ctx, category, archive_to,
                                         ignore_channels, delete_channels):
        """archive_category's body for an explicit *category* — archive_round
        needs it per owned category for split owners, not just ctx's own."""
        for channel in list(category.channels):
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
    @is_admin()
    async def archive_channel(self, ctx, channel = None, archive_to = None):
        """*Archive this channel's messages into a thread, then delete the channel.*

        Usage: `!archive_channel`
        Example: `!archive_channel`
        Run in the channel to archive (creates the destination webhook if missing). Admins/mods only.
        Usually called via `!archive_round` / `!archive_category`.
        Bang-only: no web-dashboard equivalent, by design."""
        if channel is None:
            channel = ctx.channel
        if archive_to is None:
            hunt = self.get_hunt(ctx)
            assert hunt is not None  # hunt channels always resolve their hunt row
            archive_to = self.bot.get_channel(hunt.channel_id)
        try:
            webhook = await hunt_archive.ensure_webhook(archive_to)
        except discord.HTTPException as e:
            await ctx.send(f":x: Couldn't create/find a webhook on {archive_to.mention}: {e}")
            return False
        thread_name = hunt_archive.thread_name_for_channel(channel)
        await hunt_archive.archive_and_delete_channel(
            ctx.bot, archive_to, webhook, channel, thread_name=thread_name)
        return True

    @commands.command()
    @is_admin()
    async def archive_solved_manually(self, ctx):
        """*Tidy the hunt's "solved" category: move busy solved channels back (prefixed "solved-") and delete quiet ones.*

        Usage: `!archive_solved_manually`
        Example: `!archive_solved_manually`
        Run in a channel of the hunt. Admins/mods only.
        Bang-only: no web-dashboard equivalent, by design."""
        guild = ctx.guild
        hunt = self.get_hunt(ctx)
        if hunt is None:
            await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")
            return
        hunt_category = discord.utils.get(guild.categories, id=hunt.category_id)
        solved_category_name = self.get_solved_puzzle_category(hunt.name)
        solved_category = discord.utils.get(guild.categories, name=solved_category_name)
        if hunt_category is None or solved_category is None:
            await ctx.send(":x: Couldn't find this hunt's category or its solved category.")
            return
        last_position = 0
        for hunt_channel in hunt_category.channels:
            logger.debug(f"{hunt_channel.name} : {hunt_channel.position}")
            if hunt_channel.position > last_position:
                last_position = hunt_channel.position

        # Delete the channels without many messages and move the others to the end of the main category
        # TODO Look at the channel move method
        for channel in solved_category.channels:
            num_messages = len([m async for m in channel.history(limit=10)])
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
    @is_admin()
    async def archive_hunt(self, ctx):
        """*Archive the whole hunt: condense every channel into threads here, delete all its categories/channels, rename this channel, mark it archived.*

        Usage: `!archive_hunt`
        Example: `!archive_hunt`
        Run in any channel of the hunt. Admins/mods only. Asks to confirm, then runs
        unattended. Google sheets are kept. On a team server made by !setup_server
        this is !archive_hunt_to_hub: the hunt is archived onto its hub.
        Bang-only: no web-dashboard equivalent, by design."""
        hunt = self.get_hunt(ctx)
        if hunt is None:
            await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")
            return
        if getattr(hunt, "roster_role_id", None):
            # Only !setup_server records a hub role, so this is a team server.
            await self._archive_to_hub(ctx, None)
            return
        team = hunt_archive.resolve_team_name(ctx.guild, hunt, ctx.author)
        final = hunt_archive.archived_channel_name(hunt, team)
        desc = (f"Archive hunt **{hunt.name}**? Condense every channel into threads on this "
                f"channel, delete all its categories and channels (including voice), rename "
                f"this channel to **{final}**, and mark the hunt archived. Google sheets are "
                f"kept. **Not reversible.**")
        # Confirm BEFORE locking: holding every puzzle's lock across the human
        # click blocks all solves/retags in the hunt for the confirm window, and
        # the id set is only meaningful once we know we're proceeding.
        if not await self._confirm_destructive(ctx, desc, sheets_deleted=False):
            return
        member_puzzle_ids = [p.id for p in PuzzleJsonDb.get_all_from_hunt(hunt.id)]
        async with puzzles_lock(member_puzzle_ids):
            status = await ctx.send("⏳ Archiving…")

            async def on_progress(done, total, name):
                try:
                    await status.edit(content=f"⏳ Archived {done}/{total} — {name}")
                except discord.HTTPException:
                    pass

            report = await self.service.archive_hunt(ctx.guild, hunt, ctx.author, on_progress=on_progress)
            summary = _archive_summary(report)
            await ctx.send(summary)

    @commands.command()
    @is_admin()
    async def archive_hunt_to_hub(self, ctx, hub_id: Optional[int] = None):
        """*Archive the whole hunt onto another server (the hub), then leave this one.*

        Usage: `!archive_hunt_to_hub [hub server id]`
        Example: `!archive_hunt_to_hub 123456789012345678`
        On a team server made by !setup_server, leave the id out: the hub is the
        server holding the hunt's hub role. Give it only for hunts without one.
        Run in any channel of the hunt. Admins/mods only, and you must be an admin
        on the hub too. Condenses every channel (the general one included) into
        threads on a new channel on the hub, deletes all its categories/channels,
        moves the hunt to the hub on the dashboard, and removes the bot from this
        server if no other hunt uses it. Asks to confirm. Google sheets are kept.
        The dashboard has the same action on the hunt's admin panel ("Archive to hub")."""
        await self._archive_to_hub(ctx, hub_id)

    async def _archive_to_hub(self, ctx, hub_id: Optional[int]):
        """!archive_hunt_to_hub's body; !archive_hunt runs it on a team server."""
        hunt = self.get_hunt(ctx)
        if hunt is None:
            await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")
            return
        if getattr(hunt, "archived", False):
            await ctx.send(":x: This hunt is already archived. Moving an in-place archive onto a hub isn't supported yet.")
            return
        roster = (role_panel.find_roster_role(self.bot, hunt.roster_role_id)
                  if hunt.roster_role_id else None)
        if hunt.roster_role_id and roster is None and hub_id is None:
            await ctx.send(f":x: I can't see this hunt's hub role (id {hunt.roster_role_id}) — "
                           "am I still on the hub? Give the hub's server id: "
                           "`!archive_hunt_to_hub <hub server id>`")
            return
        try:
            hub_id = hunt_archive.choose_hub_id(hub_id, roster.guild.id if roster else None)
            hub, hub_row = await hunt_archive.resolve_hub(self.bot, hub_id, ctx.guild, ctx.author)
        except hunt_archive.HubUnavailable as e:
            await ctx.send(f":x: {e}")
            return
        team = hunt_archive.resolve_team_name(ctx.guild, hunt, ctx.author)
        final = hunt_archive.archived_channel_name(hunt, team)
        desc = (f"Archive hunt **{hunt.name}** to **{hub.name}**? Condense every channel "
                f"(this one included) into threads on a new channel **{final}** there, "
                f"delete all its categories and channels (including voice), move the hunt "
                f"to {hub.name} on the dashboard, and remove the bot from this server if no "
                f"other hunt uses it. Google sheets are kept. **Not reversible.**")
        # Confirm BEFORE locking — same reasoning as archive_hunt.
        if not await self._confirm_destructive(ctx, desc, sheets_deleted=False):
            return
        await run_archive_to_hub(self.bot, self.service, ctx.guild, hunt, ctx.author,
                                 hub, hub_row, status_channel=ctx)

    @commands.command()
    @commands.guild_only()
    @is_admin()
    async def archive_multi_team(self, ctx, *, name: str):
        """*Condense a multi-team hunt's hub channels into one archive channel now.*

        Usage: `!archive_multi_team <hunt name>`
        Example: `!archive_multi_team Mystery Hunt 2027`
        Run on the hub, in the bot channel. Moves the no-spoiler and finishers
        channels into threads on one "all teams" archive channel and deletes them.
        This happens by itself when the last team archives; use this to do it
        sooner, or to retry after a failure. Admins/mods only. The dashboard has
        the same action on the Team servers page."""
        if not (await self.check_is_bot_channel(ctx)):
            return
        row = MultiTeamDb.running_by_name(ctx.guild.id, name)
        if row is None:
            await ctx.send(f":x: No running multi-team hunt called **{name}** on this server.",
                           allowed_mentions=discord.AllowedMentions.none())
            return
        try:
            multi_team.ensure_no_teams_playing(row["id"])
        except multi_team.MultiTeamRefused as exc:
            await ctx.send(f":x: {exc}", allowed_mentions=discord.AllowedMentions.none())
            return
        await ctx.send("⏳ Archiving…", allowed_mentions=discord.AllowedMentions.none())
        await ctx.send(await multi_team.archive_multi_team(self.bot, ctx.guild, row["id"]),
                       allowed_mentions=discord.AllowedMentions.none())

    @commands.command()
    @commands.guild_only()
    async def setup_server(self, ctx, hub_id: int, roster_role_id: int, hunt_url: str, *, hunt_name: str):
        """*Set up a server freshly made from the team-server template (runs once).*

        Usage: `!setup_server <hub server id> <hub role id> <hunt url> <hunt name>[, files][, multi-team]`
        Example: `!setup_server 123456789012345678 234567890123456789 https://hunt.example Big Hunt`
        Add `, files` after the hunt name for one Google spreadsheet per puzzle.
        Add `, multi-team` if several teams are fielding this hunt: it joins (or starts)
        the multi-team hunt of that name on the hub, with its no-spoiler and finishers channels.
        You must be this server's owner (or an admin here) and an admin on the hub.
        Checks the server matches the template, copies the
        hub's settings, pins `mod` as the admin role, creates the hunt gated to a team
        role named after the hub role, and records that hub role as the hunt's roster.
        If it stops partway, re-run the same command to finish; once complete it is
        locked on this server. Bang-only: the bot must already be here."""
        hunt_name, sheet_mode, multi_team = server_setup.parse_setup_tail(hunt_name)

        async def announce(team_name):
            await ctx.send(f"⏳ Setting up **{hunt_name}** for **{team_name}**…")

        outcome = await server_setup.perform_setup(
            self.bot, self.service, ctx.guild, ctx.author, hub_id, roster_role_id,
            hunt_name, hunt_url, on_start=announce, sheet_mode=sheet_mode,
            multi_team=multi_team)
        await ctx.send("\n".join(outcome.lines))

    @commands.command()
    @commands.guild_only()
    @is_admin()
    async def post_role_panel(self, ctx, *, hunt_name: str):
        """*Post the #lobby Join/Leave panel for a team server's hunt.*

        Usage: `!post_role_panel <hunt name>`
        Example: `!post_role_panel Big Hunt`
        !setup_server posts it for you; use this if it was deleted, or on a server
        set up before the panel existed. Join is for people with the hunt's hub
        role, and hub admins (who also get `mod`). Run in the bot channel.
        Admins/mods only."""
        if not (await self.check_is_bot_channel(ctx)):
            return
        hunt = HuntJsonDb.get_by_name_for_guild(hunt_name, ctx.guild.id)
        if hunt is None:
            await ctx.send(f":x: No hunt named **{hunt_name}** on this server.")
            return
        if hunt.archived:
            await ctx.send(f":x: **{hunt.name}** is archived.")
            return
        if not hunt.roster_role_id:
            await ctx.send(f":x: **{hunt.name}** isn't tied to a hub role (only hunts made by "
                           "!setup_server are), so Join would have nobody to check against.")
            return
        lobby = discord.utils.get(ctx.guild.text_channels, name=server_setup.LOBBY_CHANNEL)
        if lobby is None:
            await ctx.send(f":x: This server has no #{server_setup.LOBBY_CHANNEL} channel.")
            return
        team_role = ctx.guild.get_role(hunt.role_id)
        if team_role is None:
            await ctx.send(f":x: **{hunt.name}**'s team role is missing on this server.")
            return
        await lobby.send(server_setup.welcome_text(hunt, team_role.name),
                         view=role_panel.panel_view(hunt.id))
        await ctx.send(f":white_check_mark: Posted the Join/Leave panel in {lobby.mention}.")

    @commands.command()
    @commands.guild_only()
    @is_admin()
    async def mod_invite(self, ctx, *, hunt_name: str):
        """*Post a reusable mod invite to a team server in the hub's admin channel.*

        Usage: `!mod_invite <hunt name>`
        Example: `!mod_invite Big Hunt`
        Run on the hub. Finds the running team hunt of that name whose hub role is
        on this server, and posts a reusable invite to its #lobby in the hub's
        admin channel (never here). Re-running reuses the same invite. Run in the
        bot channel. Admins/mods only."""
        if not (await self.check_is_bot_channel(ctx)):
            return
        wanted = hunt_name.strip().casefold()
        matches = [(h, snowflake) for h, snowflake in HuntJsonDb.live_team_hunts()
                   if h.name.casefold() == wanted and ctx.guild.get_role(h.roster_role_id) is not None]
        if not matches:
            await ctx.send(f":x: No running team hunt named **{hunt_name}** on this hub.")
            return
        if len(matches) > 1:
            names = ", ".join(
                f"**{g.name}**" if (g := self.bot.get_guild(sf)) else f"server {sf}"
                for _, sf in matches)
            await ctx.send(f":x: More than one team server runs **{hunt_name}** ({names}); "
                           "I won't guess which one.")
            return
        hunt, snowflake = matches[0]
        guild = self.bot.get_guild(snowflake)
        if guild is None:
            await ctx.send(f":x: I'm not on **{hunt.name}**'s team server (id {snowflake}).")
            return
        reply, _ = await mod_invite.post(self.bot, guild, hunt)
        await ctx.send(reply)

    @commands.command()
    @commands.guild_only()
    @is_admin()
    async def send_invites(self, ctx, *, hunt_name: str):
        """*DM a private invite to everyone with the hunt's hub role.*

        Usage: `!send_invites <hunt name>`
        Example: `!send_invites Big Hunt`
        Each person gets a single-use invite to this server by DM, and the team
        role as soon as they arrive. Anyone already here or already invited is
        skipped, so re-running is safe; after the first run, anyone given the hub
        role is DM'd automatically. Lists anyone whose DMs are closed; use
        `!invite_player` for them. Run in the bot channel. Admins/mods only."""
        if not (await self.check_is_bot_channel(ctx)):
            return
        hunt = HuntJsonDb.get_by_name_for_guild(hunt_name, ctx.guild.id)
        if hunt is None:
            await ctx.send(f":x: No hunt named **{hunt_name}** on this server.")
            return
        if hunt.archived:
            await ctx.send(f":x: **{hunt.name}** is archived.")
            return
        if not hunt.roster_role_id:
            await ctx.send(f":x: **{hunt.name}** isn't tied to a hub role (only hunts made by "
                           "!setup_server are), so there's nobody to invite.")
            return
        await ctx.send(f"⏳ Sending invites for **{hunt.name}**…")
        report = await team_invites.send_invites(self.bot, ctx.guild, hunt)
        await ctx.send(team_invites.report_text(report))

    @commands.command()
    @commands.guild_only()
    @is_admin()
    async def invite_player(self, ctx, user: discord.User):
        """*DM one person a fresh private invite to this team server.*

        Usage: `!invite_player <user id or name>`
        Example: `!invite_player 123456789012345678`
        For someone with the hunt's hub role whose invite DM failed or got lost.
        If their DMs are closed, the invite is DM'd to you to pass on privately.
        Run in the bot channel. Admins/mods only."""
        if not (await self.check_is_bot_channel(ctx)):
            return
        hunt = HuntJsonDb.live_team_hunt_for_guild(ctx.guild.id)
        if hunt is None:
            await ctx.send(":x: This server has no live hunt with a hub role (only !setup_server "
                           "makes those).")
            return
        await ctx.send(await team_invites.invite_one(self.bot, ctx.guild, hunt, user, ctx.author))

    @commands.command()
    @is_organiser()
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

        puzzle = self.get_puzzle(ctx)
        assert puzzle is not None  # Puzzle channel checked above
        if not await self._confirm_destructive(ctx, f"Delete puzzle **{puzzle.name}**, its channel and its sheet?"):
            return

        hunt = self.get_hunt(ctx)
        assert hunt is not None  # puzzle channels always resolve their hunt
        hunt_general_channel = self.bot.get_channel(hunt.channel_id)
        try:
            await self.service.delete_puzzle(ctx.guild, hunt, puzzle)
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
            gsheet_cog = self.get_gsheet_cog(ctx)
            if gsheet_cog is not None:
                await gsheet_cog.delete_puzzle_spreadsheet(puzzle)
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
    @is_organiser()
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
        assert tag_group is not None  # group channel checked above
        n = len(PuzzleJsonDb.get_all_from_tag_group(tag_group.id))
        # Confirm BEFORE locking: holding every member's lock across the human
        # click blocks all solves/retags in the round for the confirm window. The
        # id set is re-read after confirming so it reflects the state we lock.
        if not await self._confirm_destructive(ctx, f"Delete round **{tag_group.name}** and its {n} puzzle(s), channels and sheets?"):
            return
        member_puzzle_ids = [p.id for p in PuzzleJsonDb.get_all_from_tag_group(tag_group.id)]
        async with puzzles_lock(member_puzzle_ids):
            hunt = self.get_hunt(ctx)
            assert hunt is not None  # group channels always resolve their hunt
            hunt_general_channel = self.bot.get_channel(hunt.channel_id)

            # keep_puzzles=False pinned explicitly: the service default flipped to True
            # (keep member puzzles) for the web dashboard's scoped-delete option, but the
            # bang command's confirm text above still promises "and its {n} puzzle(s)".
            failed = await self.service.delete_round(
                ctx.guild, hunt, tag_group, keep_puzzles=False)

            await hunt_general_channel.send(
                f":white_check_mark: Round {tag_group.name} successfully deleted.  "
                f"All puzzle sheets have been deleted.")
            await self._warn_failed_channels(hunt_general_channel, failed)
        self.set_puzzle(ctx, None)
        self.set_tag_group(ctx, None)

    async def _confirm_destructive(self, ctx, description, *, sheets_deleted=True,
                                   confirm_label="Confirm delete", title=None,
                                   timeout_msg=None):
        """Send a Confirm/Cancel prompt and return True only if the invoker
        confirms. Returns False on cancel or timeout. ``sheets_deleted=False``
        switches the copy for archiving (which keeps the sheets). ``confirm_label``
        overrides the confirm button text for non-delete reuses (e.g. consolidate).
        ``title``/``timeout_msg`` override the copy outright, for a destructive
        action that touches neither sheets nor an archive (e.g. the media sweep)."""
        view = ConfirmView(ctx.author.id, confirm_label=confirm_label)
        if sheets_deleted:
            title = title or "⚠️ Confirm deletion"
            body = description + "\n\n**Google sheets will be permanently deleted (not recoverable).**"
            timeout_msg = timeout_msg or ":hourglass: Confirmation timed out — nothing was deleted."
        else:
            title = title or "⚠️ Confirm archive"
            body = description
            timeout_msg = timeout_msg or ":hourglass: Confirmation timed out — nothing was archived."
        embed = discord.Embed(title=title, description=body, color=discord.Color.red())
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()
        if view.value is None:
            await ctx.send(timeout_msg)
        return view.value is True

    async def _confirm_reuse_role(self, ctx, role_name):
        """Confirm reusing an existing role for a hunt. Returns True only on an
        explicit confirm; False on cancel/timeout."""
        view = ConfirmView(ctx.author.id)
        embed = discord.Embed(
            title="Role already exists",
            description=(
                f"A role named **{role_name}** already exists. Normally each team "
                f"gets its own role per hunt. Reuse it for this hunt (you'll be "
                f"added to it)?"
            ),
            color=discord.Color.orange(),
        )
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()
        if view.value is None:
            await ctx.send(":hourglass: Confirmation timed out — nothing changed.")
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
    @is_admin()
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
        assert hunt is not None  # Hunt channel checked above
        n = len(PuzzleJsonDb.get_all_from_hunt(hunt.id))
        # Confirm BEFORE locking: holding every puzzle's lock across the human
        # click blocks all solves/retags in the hunt for the confirm window. The
        # id set is re-read after confirming so it reflects the state we lock.
        if not await self._confirm_destructive(ctx, f"Delete the ENTIRE hunt **{hunt.name}**: {n} puzzle(s), all rounds, channels and sheets?"):
            return
        member_puzzle_ids = [p.id for p in PuzzleJsonDb.get_all_from_hunt(hunt.id)]
        async with puzzles_lock(member_puzzle_ids):
            failed = await self.service.delete_hunt(ctx.guild, hunt)
            await self._warn_failed_channels(hunt_channel, failed)
        self.set_puzzle(ctx, None)
        self.set_tag_group(ctx, None)
        self.set_hunt(ctx, None)
        return True

    @commands.command()
    @is_admin()
    async def debug_puzzle_channel(self, ctx):
        """*Dump this puzzle's full stored record as JSON (debugging).*

        Usage: `!debug_puzzle_channel`
        Example: `!debug_puzzle_channel`
        Run in a puzzle channel. Admins/mods only."""

        if self.get_channel_type(ctx) != "Puzzle":
            await self.send_not_puzzle_channel(ctx)
            return

        puzzle = self.get_puzzle(ctx)
        assert puzzle is not None  # Puzzle channel checked above
        await ctx.channel.send(f"```json\n{puzzle.to_json()}```")


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
    @is_organiser()
    async def start_reminders(self, ctx):
        """*Start the periodic reminder loop that posts tips in the hunt channel.*

        Usage: `!start_reminders`
        Example: `!start_reminders`
        Run in a channel of the hunt. Organisers/admins only. Stop it with `!stop_reminders`."""
        channel = self.get_hunt_channel(ctx)
        self.reminder_index = 0
        # NOTE: reminder_loop is a single tasks.Loop per cog instance, not
        # per-guild -- restarting it repoints every guild's reminders at
        # `channel`. Making it per-guild is a real design decision (a dict of
        # raw asyncio.Tasks would lose Loop's reconnect/backoff; runtime-built
        # Loop objects are a bigger change) and is deliberately deferred, not
        # built here (C1).
        if self.reminder_loop.is_running():
            # tasks.Loop.cancel() is asynchronous: the loop's task hasn't
            # actually finished by the time the next statement runs, so a
            # follow-up start() races it and discord.py raises
            # RuntimeError("Task is already launched..."). restart() is
            # discord.py's supported idiom for this -- it cancels the running
            # task and defers the restart to a done-callback once cancellation
            # completes, avoiding the race.
            self.reminder_loop.restart(channel)
        else:
            self.reminder_loop.start(channel)
        await ctx.send(":white_check_mark: Reminder loop started.")

    @commands.hybrid_command(description="Stop the reminder loop (organisers only)",)
    @is_organiser()
    async def stop_reminders(self, ctx):
        """*Stop the periodic reminder loop.*

        Usage: `!stop_reminders`
        Example: `!stop_reminders`
        Organisers/admins only. Start it with `!start_reminders`."""
        self.reminder_loop.cancel()
        await ctx.send("@here Okay, I'll leave you alone for a bit, but remember I'm always watching...")

    def get_hunt_channel(self, ctx):
        hunt = self.get_hunt(ctx)
        assert hunt is not None  # callers run in hunt-scoped channels
        return self.bot.get_channel(hunt.channel_id)

async def setup(bot):
    # Every #lobby role panel ever posted keeps working across restarts.
    bot.add_dynamic_items(role_panel.HuntRoleButton)
    await bot.add_cog(Puzzles(bot))
