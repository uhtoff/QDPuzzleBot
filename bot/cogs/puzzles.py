import datetime
import logging
import random
import time
import string
from encodings.aliases import aliases
from typing import Any, List, Optional

import discord
from discord import app_commands, Interaction
from discord.ext import tasks, commands
from discord import Webhook, Message
import pytz
import asyncio
import aiohttp
from functools import wraps
from collections import defaultdict

from bot.utils import urls, config, chunking
from bot.store import MissingPuzzleError, PuzzleData, PuzzleJsonDb, GuildSettings, GuildSettingsDb, HuntSettings, \
    RoundData, RoundJsonDb, HuntData, HuntJsonDb, MySQLRoundJsonDb, MySQLAdditionalSheetsDb, SheetsJsonDb, AdditionalSheetData
from bot.utils.chunking import build_note_embeds

logger = logging.getLogger(__name__)

# One asyncio.Lock per puzzle id
_PUZZLE_LOCKS = defaultdict(asyncio.Lock)

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
    META_CHANNEL_NAME = "meta"
    META_REASON = "bot-meta"
    ROLE_REASON = "bot-role"
    PUZZLE_REASON = "bot-puzzle"
    DELETE_REASON = "bot-delete"
    HUNT_REASON = "bot-hunt-general"
    ROUND_REASON = "bot-round"
    SOLVE_CATEGORY = False
    SOLVE_DIVIDER = "———solved———"
    SOLVED_PUZZLES_CATEGORY = "solved"
    PUZZLE_GROUPS = ["Round","Metapuzzle","Metaless Round"]
    STATUSES = ["unstarted", "in progress", "stuck", "needs extraction", "needs submission","solved", "backsolved"]
    PRIORITIES = ["low", "medium", "high", "very high"]
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
        # self.archived_solved_puzzles_loop.start()
        self.reminder_index=0
        self.guild = None
        self.guild_data = None
        self.channel_type = None
        self.puzzle = None
        self.gsheet_cog = None
        self.position_lock = asyncio.Lock()
        self.environment = {}

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
            hunt_round = RoundJsonDb.get_by_attr(meta_id=puzzle.id)
            if hunt_round:
                channel_type = hunt_round.type
            else:
                hunt_round = RoundJsonDb.get_by_attr(category_id=ctx.channel.category.id, )
            if puzzle:
                hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        print (f"Channel type: {channel_type}")
        # print (f"Group data type: {hunt_round}")
        gsheet_cog = self.bot.get_cog("GoogleSheets")
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
            # filter to only rounds that are metas if that’s what you want
            # if not r.is_meta: continue
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

    # @app_commands.command(name="slash")
    # async def ping(self, ctx: commands.Context):
    #     bot_latency = round(self.bot.latency * 1000)
    #     await ctx.send(f"Pong! {bot_latency} ms.")

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
        if isinstance(error, commands.CheckFailure) and str(error) == "PUZZLE_BUSY":
            await ctx.send("⏳ That puzzle is busy running another command. Try again in a moment.")
            return
        await ctx.send(f":exclamation: **{type(error).__name__}**" + "\n" + str(error))

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
    @commands.has_any_role('Moderator', 'mod', 'admin')
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
        overwrites = None
        role = None
        google_drive_id = None
        if role_name:
            role = await guild.create_role(name=role_name, colour=discord.Colour.random(), mentionable=True, reason=self.ROLE_REASON )
            overwrites = self.get_overwrites(guild, role)

        category = await guild.create_category(category_name, position=max(len(guild.categories) - 2,0))
        text_channel, created_text = await self.get_or_create_channel(
            guild=guild, category=category, channel_name=self.GENERAL_CHANNEL_NAME, channel_type="text", reason=self.HUNT_REASON, position = 0
        )
        settings = self.get_guild_data(ctx)

        if self.SOLVE_CATEGORY:
            solved_category = await guild.create_category(self.get_solved_puzzle_category(hunt_name), position=max(len(guild.categories) - 2,0))
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

        if google_drive_id:
            new_hunt.google_sheet_id=google_drive_id

        HuntJsonDb.commit(new_hunt)

        # add hunt settings
        initial_message = await self.send_initial_hunt_channel_messages(ctx, text_channel, hunt=new_hunt)
        await initial_message.pin()

        return (category, text_channel, True)

    @commands.hybrid_command(description="Set the hunt to use a second spreadsheet to hold solved puzzles (admin only)",)
    @commands.has_any_role('Moderator', 'mod', 'admin')
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
    async def puzzle(self, ctx, *, arg):
        """*Create new puzzle channels: !p puzzle-name*"""

        puzzle_name = arg
        self.start = datetime.datetime.now()
        if PuzzleJsonDb.check_duplicates_in_hunt(puzzle_name, self.get_hunt(ctx).id):
            return await ctx.send(f":exclamation: Puzzle **{puzzle_name}** already exists in this hunt or will lead to a duplicate channel name, please use a different name")

        if await self.check_available_space(ctx,1) is False:
            return await ctx.send(":x: There is no free channel to create this puzzle, please inform the moderators.")

        # self.get_hunt_round(ctx).num_puzzles += 1
        # RoundJsonDb.commit(self.get_hunt_round(ctx))
        check_duplicate = datetime.datetime.now()
        category = ctx.channel.category
        new_puzzle = await self.create_puzzle(ctx, puzzle_name, self.get_tag_from_category(category))
        puzzle_created = datetime.datetime.now()
        await self.create_puzzle_channel(ctx, new_puzzle, position='bottom')
        channel_sent = datetime.datetime.now()
        # print(f"Check duplicate: {(check_duplicate-self.start).seconds}.{(check_duplicate-self.start).microseconds} s - Create puzzle = {(puzzle_created-check_duplicate).seconds}.{(puzzle_created-check_duplicate).microseconds} s - Send channel = {(channel_sent-puzzle_created).seconds}.{(channel_sent-puzzle_created).microseconds} s - Total = {(channel_sent-self.start).seconds} s")

    async def create_metapuzzle(self,ctx, puzzle_name, tag = None):
        return await self.create_puzzle(ctx, puzzle_name, tag, metapuzzle = 1)

    async def create_puzzle(self, ctx, puzzle_name, tag = None, **kwargs):
        new_puzzle = PuzzleData(
            name=puzzle_name,
            # round_id=self.get_hunt_round(ctx).id,
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

        for tag in new_puzzle.tags:
            meta_round = RoundJsonDb.get_by_attr(id=tag)
            await self.update_metapuzzle(ctx, meta_round)

        return new_puzzle

    async def update_metapuzzle(self, ctx, hunt_round):
        round_puzzles = PuzzleJsonDb.get_all_from_round(hunt_round.id)
        if hunt_round.meta_id:
            metapuzzle = PuzzleJsonDb.get_by_attr(id=hunt_round.meta_id)
            await self.get_gsheet_cog(ctx).add_metapuzzle_data(metapuzzle, round_puzzles)

    # @commands.command()
    # @commands.has_any_role('Moderator', 'mod', 'admin', 'Organisers')
    # async def list_positions(self, ctx):
    #     """*(admin) Troubleshooting channel positions"""
    #     category = ctx.channel.category
    #     for channel in category.channels:
    #         print(f"{channel.name} ({channel.position})")

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
    @commands.has_any_role('Moderator', 'mod', 'admin', 'Organisers')
    async def move_to_top(self, ctx):
        channel = ctx.channel
        position = self.get_first_channel(ctx, channel.category)
        await channel.edit(position=position)
        await ctx.send(":white_check_mark: Channel moved to top of category")

    @commands.hybrid_command(description="Move the channel to the bottom of the unsolved section of the category (organisers only)",)
    @commands.has_any_role('Moderator', 'mod', 'admin', 'Organisers')
    async def move_to_bottom(self, ctx):
        channel = ctx.channel
        position = self.get_solve_divider_position(ctx, channel.category)
        await channel.edit(position=position)
        await ctx.send(":white_check_mark: Channel moved to bottom of unsolved")

    @commands.hybrid_command(description="Move the channel to the solved section of the category (organisers only)",)
    @commands.has_any_role('Moderator', 'mod', 'admin', 'Organisers')
    async def move_to_solved(self, ctx):
        channel = ctx.channel
        position = self.get_solve_divider_position(ctx, channel.category)
        await channel.edit(position=position+1)
        await ctx.send(":white_check_mark: Channel moved to solved")

    # @commands.hybrid_command()
    # @commands.has_any_role('Moderator', 'mod', 'admin', 'Organisers')
    # async def move_channel(self, ctx, position):
    #     channel = ctx.channel
    #     position = int(position)
    #     await channel.edit(position=position)

    def get_tag_from_category(self, category):
        hunt_round = RoundJsonDb.get_by_attr(category_id=category.id)
        if hunt_round:
            return hunt_round.id
        else:
            return None

    @commands.hybrid_command(description="Show all tags on the puzzle (admin only)",)
    @commands.has_any_role('Moderator', 'mod', 'admin', 'Organisers')
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
    @commands.has_any_role('Moderator', 'mod', 'admin', 'Organisers')
    async def add_tag(self, ctx, *, arg):
        """*(admin) For troubleshooting only: !add_tag tag_id*"""
        new_tag = arg
        puzzle = self.get_puzzle(ctx)
        puzzle.tags.append(new_tag)
        await ctx.channel.send(f"```json\n{puzzle.to_json(indent=2)}```")

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin', 'Organisers')
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

        sheet = AdditionalSheetData(
            puzzle_id=puzzle.id,
            puzzle_name=name or f"Extra sheet for {puzzle.name}"
        )

        sheet.google_page_id = await self.get_gsheet_cog(ctx).create_additional_spreadsheet(puzzle, name)
        puzzle.additional_sheets.append(sheet)
        SheetsJsonDb.commit(sheet)
        await self.info(ctx, update=True)
        return await ctx.send(f":white_check_mark: Added sheet {name} to puzzle.")

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
        # category = self.bot.get_channel(self.get_hunt_round(ctx).channel_id).category
        send_initial_message = kwargs.get("send_initial_message", True)
        position = kwargs.get("position", 1)
        if category is None:
            category = ctx.channel.category
        channel_name = self.clean_name(new_puzzle.name)
        # round_channel = self.get_round_channel(ctx)
        # await round_channel.edit(position=0)

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

        created_voice = False
        if created_voice:
            voice_channel, created_voice = await self.get_or_create_channel(
                guild=self.get_guild(ctx), category=category, channel_name=channel_name, channel_type="voice", reason=self.PUZZLE_REASON
            )
            if created_voice:
                new_puzzle.voice_channel_id = voice_channel.id
        created = created_text or created_voice
        if created:
            if created_text and created_voice:
                created_desc = "text and voice"  # I'm sure there's a more elegant way to do this
            elif created_text:
                created_desc = "text"
            elif created_voice:
                created_desc = "voice"

            await ctx.send(
                # f":white_check_mark: I've created new puzzle {created_desc} channels for {self.get_hunt_round(ctx).name}: {text_channel.mention}"
                f":white_check_mark: I've created new puzzle {created_desc} channels for {category.name}: {text_channel.mention}"
            )
            PuzzleJsonDb.commit(new_puzzle)

        else:
            await ctx.send(
                f"I've found an already existing puzzle channel for {ctx.channel.category.name}: {text_channel.mention}"
            )
            await self.delete_puzzle_data(ctx, new_puzzle)

        return text_channel, created

    @commands.hybrid_command(description="Sync all the puzzles in the category to the metapuzzle (organiser only)",aliases=['sync_round'])
    @commands.has_any_role('Moderator', 'mod', 'admin', 'Organisers')
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
            await self.update_metapuzzle(ctx, meta_round)
            unique_old_tags = list(dict.fromkeys(old_tags))
            for old_tag in unique_old_tags:
                old_meta_round = RoundJsonDb.get_by_attr(id=old_tag)
                await self.update_metapuzzle(ctx, old_meta_round)

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
            puzzle.tags.append(meta_round.id)
            PuzzleJsonDb.commit(puzzle)
            await ctx.send(
                f":white_check_mark: This puzzle has been tagged to {meta_round.name} and meta sheets are being updated.")
            await self.update_metapuzzle(ctx, meta_round)
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
        old_tags = []
        meta_round = RoundJsonDb.get_by_attr(meta_code=meta_code)
        if meta_round:
            new_category = discord.utils.get(self.get_guild(ctx).categories, id=meta_round.category_id)
            await ctx.channel.edit(category=new_category, position=2)
            await ctx.send(
                f":white_check_mark: This puzzle has been moved to {meta_round.name} and meta sheets are being updated.")
            old_tags.extend(puzzle.tags.copy())
            # Don't remove old tags from metapuzzle to avoid it being orphaned
            if puzzle.is_metapuzzle() is False:
                puzzle.tags.clear()
            puzzle.tags.append(meta_round.id)
            PuzzleJsonDb.commit(puzzle)
            await self.update_metapuzzle(ctx, meta_round)
            for old_tag in old_tags:
                old_meta_round = RoundJsonDb.get_by_attr(id=old_tag)
                await self.update_metapuzzle(ctx, old_meta_round)
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
        if puzzle.id == meta_round.meta_id:
            await ctx.send(":x: You can't remove a metapuzzle from it's own round!")
            return
        if meta_round:
            # old_tags to ensure all attached metapuzzles updated
            old_tags = []
            old_tags.extend(puzzle.tags.copy())
            puzzle.tags.remove(meta_round.id)
            PuzzleJsonDb.commit(puzzle)
            await ctx.send(
                f":white_check_mark: This puzzle has been removed from {meta_round.name} and meta sheets are being updated.")
            await self.update_metapuzzle(ctx, meta_round)
            for old_tag in old_tags:
                old_meta_round = RoundJsonDb.get_by_attr(id=old_tag)
                await self.update_metapuzzle(ctx, old_meta_round)
        else:
            await ctx.send(":x: Please send a valid meta code")

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
        puzzle = True
        round_puzzle = None
        puzzle_name = self.GENERAL_CHANNEL_NAME

        if command in ["r","round","R"]:
            group_type = "Round"
        elif command in ["mp","metapuzzle","MP"]:
            group_type = "Metapuzzle"
        else:
            group_type = "Metaless Round"
            puzzle = False
        # if self.get_channel_type(ctx) != "Hunt":
        #     await ctx.channel.send(":x: Rounds must be created in the master hunt channel")
        #     return

        self.start = datetime.datetime.now()

        if RoundJsonDb.check_duplicates_in_hunt(arg, self.get_hunt(ctx)):
            return await ctx.send(f":exclamation: **{group_type} {arg}** already exists in this hunt or will lead to a duplicate channel name, please use a different name.")

        if puzzle:
            puzzle_name = arg + " - meta" if group_type == "Round" else arg
            if PuzzleJsonDb.check_duplicates_in_hunt(puzzle_name, self.get_hunt(ctx).id):
                return await ctx.send(f":exclamation: **{group_type} {arg}** will cause a puzzle name clash within this hunt, please use a different name.")
        check_duplicate = datetime.datetime.now()
        new_round = RoundData(arg)
        new_category_name = self.clean_name(arg)

        guild = ctx.guild
        existing_category = discord.utils.get(guild.categories, name=new_category_name)
        if existing_category or self.get_hunt(ctx).parallel_hunt:
            # Try to append the hunt name to the round category name and check if it still exists
            new_category_name = new_category_name + " — " + self.clean_name(self.get_hunt(ctx).name)
            existing_category = discord.utils.get(guild.categories, name=new_category_name)
        if not existing_category:
            print(f"Creating a new channel category for {group_type}: {new_category_name}")
            role = None
            if self.get_hunt(ctx).role_id:
                role = discord.utils.get(guild.roles, id=self.get_hunt(ctx).role_id)
            overwrites = self.get_overwrites(guild, role)
            position = ctx.channel.category.position + 1
            new_category = await ctx.channel.category.clone(name=new_category_name)
            # await category.edit(overwrites=overwrites, position=position)
            await new_category.edit(position=position)
        else:
            raise ValueError(f"Category {new_category_name} already present in this server, please name the round differently.")

        create_category = datetime.datetime.now()

        new_round.hunt_id = self.get_hunt(ctx).id
        new_round.category_id = new_category.id
        new_round.type = group_type
        new_round.start_time = datetime.datetime.now()
        RoundJsonDb.commit(new_round)

        if puzzle:
            round_puzzle = await self.create_metapuzzle(ctx, puzzle_name, self.get_tag_from_category(new_category))
            text_channel, created = await self.create_puzzle_channel(ctx, round_puzzle, new_category, send_initial_message=False, position=0)
            new_round.meta_id = round_puzzle.id
        else:
            text_channel, created_text = await self.get_or_create_channel(
                guild=self.get_guild(ctx), category=new_category, channel_name=puzzle_name,
                channel_type="text", reason=self.PUZZLE_REASON, position=0
            )

        meta_code = RoundJsonDb.generate_uid('meta_code',6,arg)

        new_round.meta_code = meta_code

        RoundJsonDb.commit(new_round)

        puzzle_created = datetime.datetime.now()

        if self.SOLVE_CATEGORY is False:
            await self.get_or_create_channel(
                guild=guild, category=new_category, channel_name=self.SOLVE_DIVIDER,
                channel_type="text", reason=self.ROUND_REASON, position=500
            )

        if group_type == "Round":
            initial_message = await self.send_initial_round_channel_messages(ctx, text_channel,
                                                                                  hunt_round=new_round,
                                                                                  puzzle=round_puzzle)
        elif group_type == "Metapuzzle":
            initial_message = await self.send_initial_metapuzzle_channel_messages(ctx, text_channel,
                                                                                  hunt_round=new_round,
                                                                                  puzzle=round_puzzle)
        else:
            initial_message = await self.send_initial_metaless_round_channel_messages(ctx, text_channel,
                                                                                  hunt_round=new_round)
        await initial_message.pin()
        await ctx.send(
            f":white_check_mark: I've created a new {puzzle_name} category and channel for {self.get_hunt(ctx).name} - {new_round.name}"
        )
        channel_sent = datetime.datetime.now()
        print(
        f"Check duplicate: {(check_duplicate - self.start).microseconds} s - Create Category = {(create_category - check_duplicate).microseconds} s -  Create puzzle = {(puzzle_created - create_category).microseconds} s - Send channel = {(channel_sent - puzzle_created).microseconds} s")

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
    @commands.has_any_role('Moderator', 'mod', 'admin')
    @commands.has_permissions(manage_channels=True)
    async def show_settings(self, ctx):
        """*(admin) Show channel settings for debug*"""
        await ctx.send(f"```json\n{self.get_settings(ctx).to_json(indent=2)}```")

    @commands.hybrid_command(description="Show puzzle settings (admin only)",)
    @commands.has_any_role('Moderator', 'mod', 'admin')
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
    @commands.has_any_role('Moderator', 'mod', 'admin')
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
    @commands.has_any_role('Moderator', 'mod', 'admin')
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
    @commands.has_any_role('Moderator', 'mod', 'admin')
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
    @commands.has_any_role('Moderator', 'mod', 'admin')
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

    # @commands.command(aliases=['import'])
    # @commands.has_any_role('Moderator', 'mod', 'admin')
    # @commands.has_permissions(manage_channels=True)
    # async def import_puzzles(self, ctx):
    #     """(Admin) *Import puzzles from the file system to the database*"""
    #     guild = ctx.guild
    #     settings = GuildSettingsDb.get(guild.id)
    #     if ctx.channel.name != self.GENERAL_CHANNEL_NAME:
    #         # all_puzzles = PuzzleJsonDb.get_all(ctx.guild.id, self.get_puzzle_data_from_channel(ctx.channel).hunt_id)
    #         all_puzzles = PuzzleJsonDb.get_all_fs(ctx.guild.id, settings.category_mapping[ctx.channel.category.id])
    #     else:
    #         # all_puzzles = PuzzleJsonDb.get_all(ctx.guild.id, settings.category_mapping[ctx.channel.category.id])
    #         all_puzzles = PuzzleJsonDb.get_all_fs(ctx.guild.id, ctx.channel.category.id)
    #     for puzzle in all_puzzles:
    #         PuzzleJsonDb.commit(puzzle)
    #
    # @commands.command(aliases=['import_all'])
    # @commands.has_any_role('Moderator', 'mod', 'admin')
    # @commands.has_permissions(manage_channels=True)
    # async def import_all_puzzles(self, ctx):
    #     """(Admin) *Import all puzzles from the file system to the database*"""
    #     all_puzzles = PuzzleJsonDb.get_all_fs(ctx.guild.id)
    #     for puzzle in all_puzzles:
    #         PuzzleJsonDb.commit(puzzle)

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
            # hunt_puzzles = PuzzleJsonDb.get_all_from_hunt(self.get_hunt(ctx).id)
            # for hunt_puzzle in hunt_puzzles:
            #     if hunt_puzzle.round_id not in all_puzzles:
            #         hunt_round = RoundJsonDb.get_by_attr(id=hunt_puzzle.round_id)
            #         all_puzzles[hunt_round.id] = {
            #             'name': hunt_round.name,
            #             'puzzles': []
            #         }
            #     all_puzzles[hunt_puzzle.round_id]['puzzles'].append(hunt_puzzle)
            # embed_title = f"Puzzles in Hunt {self.get_hunt(ctx).name}"

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
            print(message)
            logger.info(message)
            create_method = (
                guild.create_text_channel if channel_type is discord.ChannelType.text else guild.create_voice_channel
            )
            channel = await create_method(channel_name, category=category, **kwargs)
            created = True

        return (channel, created)


    def get_overwrites(self, guild, role):
        if not role:
            return None
        return {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            role: discord.PermissionOverwrite(read_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True)
        }

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
        if hunt.uid:
            puzzle_links += f"""• [Overview Website]({self.get_guild_data(ctx).website_url}?uid={hunt.uid})"""
        else:
            puzzle_links += f"""• [Overview Website]({self.get_guild_data(ctx).website_url}?uid={hunt.id})"""
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
        spreadsheet_url = urls.spreadsheet_url(self.get_puzzle_sheet(ctx, puzzle),
                                               puzzle.google_page_id) if self.get_hunt(ctx).google_sheet_id else "?"
        puzzle_links = (f"""    • [Puzzle link]({puzzle.url or "?"})
                                • [Google Sheet]({spreadsheet_url})
                                """)
        if hunt.uid:
            puzzle_links += f"""• [Overview Website]({self.get_guild_data(ctx).website_url}?uid={hunt.uid})"""
        else:
            puzzle_links += f"""• [Overview Website]({self.get_guild_data(ctx).website_url}?uid={hunt.id})"""
        embed.add_field(
            name="Puzzle Links",
            value=puzzle_links,
            inline=False,
        )
        try:
            embed.add_field(name="Status", value=puzzle.status or "?", inline=False,)
            if puzzle.solution:
                embed.add_field(name="Solution", value=puzzle.solution, inline=False,)
            embed.add_field(name="Type", value=puzzle.puzzle_type or "?", inline=False,)
            embed.add_field(name="Priority", value=puzzle.priority or "?", inline=False,)
        except:
            pass
        embed_list = [embed]
        if len(puzzle.additional_sheets) > 0:
            sheets_embed = discord.Embed()
            sheets_embed.add_field(
                name="Additional Sheet Links",
                value=self.create_additional_sheet_list(ctx, puzzle),
                inline=False,
            )
            embed_list.append(sheets_embed)

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
        if hunt.uid:
            embed.add_field(name="Overview Website", value=f"{self.get_guild_data(ctx).website_url}?uid={hunt.uid}",
                            inline=False)
        else:
            embed.add_field(name="Overview Website",
                            value=f"{self.get_guild_data(ctx).website_url}?hunt_id={hunt.id}",
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
        spreadsheet_url = urls.spreadsheet_url(self.get_puzzle_sheet(ctx, puzzle),
                                               puzzle.google_page_id) if self.get_hunt(ctx).google_sheet_id else "?"
        puzzle_links = (f"""    • [Puzzle link]({puzzle.url or "?"})
                                • [Google Sheet]({spreadsheet_url})
                                """)
        if hunt.uid:
            puzzle_links += f"""• [Overview Website]({self.get_guild_data(ctx).website_url}?uid={hunt.uid})"""
        else:
            puzzle_links += f"""• [Overview Website]({self.get_guild_data(ctx).website_url}?uid={hunt.id})"""
        embed.add_field(
            name="Puzzle Links",
            value=puzzle_links,
            inline=False,
        )
        try:
            embed.add_field(name="Status", value=puzzle.status or "?", inline=False,)
            if puzzle.solution:
                embed.add_field(name="Solution", value=puzzle.solution, inline=False,)
            embed.add_field(name="Type", value=puzzle.puzzle_type or "?", inline=False,)
            embed.add_field(name="Priority", value=puzzle.priority or "?", inline=False,)
        except:
            pass

        embed_list = [embed]

        if len(puzzle.additional_sheets) > 0:
            sheets_embed = discord.Embed()
            sheets_embed.add_field(
                name="Additional Sheet Links",
                value=self.create_additional_sheet_list(ctx, puzzle),
                inline=False,
            )
            embed_list.append(sheets_embed)

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
            # description=f"""Welcome to the puzzle channel for {puzzle.name} in {self.get_hunt_round(ctx).name}!"""
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
        spreadsheet_url = urls.spreadsheet_url(self.get_puzzle_sheet(ctx, puzzle),
                                               puzzle.google_page_id) if self.get_hunt(ctx).google_sheet_id else "?"
        puzzle_links = (f"""    • [Puzzle link]({puzzle.url or "?"})
                                • [Google Sheet]({spreadsheet_url})
                                """)
        if hunt.uid:
            puzzle_links += f"""• [Overview Website]({self.get_guild_data(ctx).website_url}?uid={hunt.uid})"""
        else:
            puzzle_links += f"""• [Overview Website]({self.get_guild_data(ctx).website_url}?uid={hunt.id})"""
        embed.add_field(
            name="Puzzle Links",
            value=puzzle_links,
            inline=False,
        )
        try:
            embed.add_field(name="Status", value=puzzle.status or "?", inline=False,)
            if puzzle.solution:
                embed.add_field(name="Solution", value=puzzle.solution, inline=False,)
            embed.add_field(name="Type", value=puzzle.puzzle_type or "?", inline=False,)
            embed.add_field(name="Priority", value=puzzle.priority or "?", inline=False,)
        except:
            pass

        embed_list = [embed]
        if len(puzzle.additional_sheets) > 0:
            sheets_embed = discord.Embed()
            sheets_embed.add_field(
                name="Additional Sheet Links",
                value=self.create_additional_sheet_list(ctx, puzzle),
                inline=False,
            )
            embed_list.append(sheets_embed)

        if kwargs.get("update",False):
            channel_pins = await channel.pins()
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
    @commands.has_any_role('Moderator', 'mod', 'admin', 'Organisers')
    @with_puzzle_mutex(wait=True)
    async def rename_puzzle(self, ctx, *, puzzle_name: str):
        """Rename a puzzle"""
        if self.get_puzzle(ctx):
            if PuzzleJsonDb.check_duplicates_in_hunt(puzzle_name, self.get_hunt(ctx).id):
                return await ctx.send(
                    f":exclamation: Puzzle **{puzzle_name}** already exists in this hunt or will lead to a duplicate channel name, please use a different name")
            await ctx.channel.edit(name=self.clean_name(puzzle_name))
            self.get_puzzle(ctx).name = puzzle_name
            self.get_puzzle(ctx).channel_name = self.clean_name(puzzle_name)
            PuzzleJsonDb.commit(self.get_puzzle(ctx))
            if self.get_gsheet_cog(ctx) is not None:
                # update google sheet ID
                await self.get_gsheet_cog(ctx).update_puzzle(self.get_puzzle(ctx), True)
                if self.get_hunt_round(ctx):
                    await self.update_metapuzzle(ctx, self.get_hunt_round(ctx))
            await ctx.channel.send(":white_check_mark: I've updated the puzzle and channel names")
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
            # await self.update_puzzle_attr_by_command(ctx, "hunt_url", url, reply=False)
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

        if self.get_puzzle(ctx):
            self.get_puzzle(ctx).status = status
            await self.send_state(
                ctx, ctx.channel, self.get_puzzle(ctx), description=":white_check_mark: I've updated:" if status else None
            )
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

    @commands.command()
    @with_puzzle_mutex(wait=True)
    async def type(self, ctx, *, puzzle_type: str):
        """*Update puzzle type, e.g. "crossword"*"""
        if self.get_puzzle(ctx):
            self.get_puzzle(ctx).puzzle_type = puzzle_type
            await self.send_state(
                ctx, ctx.channel, self.get_puzzle(ctx), description=":white_check_mark: I've updated:" if puzzle_type else None
            )
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

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

        if self.get_puzzle(ctx):
            self.get_puzzle(ctx).priority = priority
            await self.send_state(
                ctx, ctx.channel, self.get_puzzle(ctx), description=":white_check_mark: I've updated:" if priority else None
            )
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

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
            # embed = discord.Embed(description=f"{message}")
            # i = len(self.get_puzzle(ctx).notes)
            # for x in range(i):
            #     embed.add_field(
            #         name=f"Note {x+1}",
            #         value=self.get_puzzle(ctx).notes[x],
            #         inline=False
            #     )
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
            note = self.get_puzzle(ctx).notes[note_index-1]
            del self.get_puzzle(ctx).notes[note_index - 1]
            description = f"Erased note {note_index}"
        else:
            description = f"Unable to find note {note_index}"

        embed = discord.Embed(description=description)
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

        puzzle = self.get_puzzle(ctx)

        solution = " ".join(args)
        solution = solution.strip().upper()
        self.get_puzzle(ctx).status = "Partially Solved"

        if self.get_puzzle(ctx).solution:
            self.get_puzzle(ctx).solution += "/" + solution
        else:
            self.get_puzzle(ctx).solution = solution

        PuzzleJsonDb.commit(self.get_puzzle(ctx))

        for tag in self.get_puzzle(ctx).tags:
            meta_round = RoundJsonDb.get_by_attr(id=tag)
            await self.update_metapuzzle(ctx, meta_round)

        # if self.get_hunt_round(ctx):
        #     await self.update_metapuzzle(ctx, self.get_hunt_round(ctx))

        emoji = self.get_guild_data(ctx).discord_bot_emoji
        embed = discord.Embed(title="Partially SOLVED!", description=f"{emoji} :partying_face: Great work! Added the solution `{solution}`")
        embed.add_field(
            name="Follow-up",
            value="If you need to clear the partial solutions then use !unsolve and they will all be wiped. "
        )
        await ctx.send(embed=embed)
        await self.send_initial_puzzle_channel_messages(ctx, ctx.channel, update=True)

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

        solution = " ".join(args)
        solution = solution.strip().upper()

        # for arg in args:
        #     solution += arg.strip().upper()
        #     solution += " "

        puzzle = self.get_puzzle(ctx)

        if solution == "" and puzzle.solution in [None,""]:
            await ctx.send(":x: Nice try, but you need to give a solution!")
            return

        puzzle.status = "Solved"

        if puzzle.solution and solution is not None:
            solution = solution.strip().upper()
            puzzle.solution += "/" + solution
        elif solution is not None:
            solution = solution.strip().upper()
            puzzle.solution = solution
        puzzle.solved = True
        puzzle.solve_time = datetime.datetime.now(tz=pytz.UTC)

        PuzzleJsonDb.commit(puzzle)

        if self.get_hunt_round(ctx).meta_id == puzzle.id:
            self.get_hunt_round(ctx).solve_time = datetime.datetime.now(tz=pytz.UTC)

        for tag in puzzle.tags:
            meta_round = RoundJsonDb.get_by_attr(id=tag)
            await self.update_metapuzzle(ctx, meta_round)
            if meta_round.meta_id == puzzle.id:
                meta_round.solve_time = datetime.datetime.now(tz=pytz.UTC)
                RoundJsonDb.commit(meta_round)
        #
        # if self.get_hunt_round(ctx):
        #     await self.update_metapuzzle(ctx, self.get_hunt_round(ctx))

        emoji = self.get_guild_data(ctx).discord_bot_emoji
        embed = discord.Embed(title="PUZZLE SOLVED!", description=f"{emoji} :partying_face: Great work! Marked the solution as `{puzzle.solution}`")
        embed.add_field(
            name="Follow-up",
            value="If the solution was entered incorrectly, please use `!update_solution` to update it, if the puzzle isn't actually solved at all then use `!unsolve`.  \nGive me a sec to tidy up the sheets."
        )
        await ctx.send(embed=embed)
        await self.get_gsheet_cog(ctx).archive_puzzle_spreadsheet(puzzle)
        puzzle.archive_time = datetime.datetime.now(tz=pytz.UTC)
        await self.move_to_solved(ctx)
        await self.info(ctx, update=True)
        for sheet in puzzle.additional_sheets:
            SheetsJsonDb.commit(sheet)
        await ctx.send(":white_check_mark: Sheets all tidied away.")

    @commands.command(aliases=["update_solution"])
    @with_puzzle_mutex(wait=False)
    async def change_solution(self, ctx, *args):
        """*Change a previously marked solution, if not solved then solve as usual: !change_solution SOLUTION*"""
        if not self.get_puzzle(ctx):
            await self.send_not_puzzle_channel(ctx)
            return
        elif self.get_puzzle(ctx).solved == 0:
            await self.solve(ctx, *args)
            return

        solution = " ".join(args)
        solution = solution.strip().upper()

        # for arg in args:
        #     solution += arg.strip().upper()
        #     solution += " "

        puzzle = self.get_puzzle(ctx)

        if solution == "":
            await ctx.send(":x: Nice try, but you need to give a solution!")
            return

        puzzle.solution = solution
        puzzle.solve_time = datetime.datetime.now(tz=pytz.UTC)

        PuzzleJsonDb.commit(puzzle)

        # if self.get_hunt_round(ctx):
        #     await self.update_metapuzzle(ctx, self.get_hunt_round(ctx))

        for tag in puzzle.tags:
            meta_round = RoundJsonDb.get_by_attr(id=tag)
            await self.update_metapuzzle(ctx, meta_round)

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

    @commands.command(aliases=["add_to_solution"])
    @with_puzzle_mutex(wait=False)
    async def add_solution(self, ctx, *args):
        """*Add a solution to a previously solved puzzle, for instance if an additional answer is found, if not solved then add a partial solution: !add_solution SOLUTION*"""
        if not self.get_puzzle(ctx):
            await self.send_not_puzzle_channel(ctx)
            return
        elif self.get_puzzle(ctx).solved == 0:
            await self.partial(ctx, *args)
            return

        solution = " ".join(args)
        solution = solution.strip().upper()

        # for arg in args:
        #     solution += arg.strip().upper()
        #     solution += " "

        puzzle = self.get_puzzle(ctx)

        if solution == "":
            await ctx.send(":x: Nice try, but you need to give a solution!")
            return

        puzzle.solution += "/" + solution
        puzzle.solve_time = datetime.datetime.now(tz=pytz.UTC)

        PuzzleJsonDb.commit(puzzle)

        # if self.get_hunt_round(ctx):
        #     await self.update_metapuzzle(ctx, self.get_hunt_round(ctx))

        for tag in puzzle.tags:
            meta_round = RoundJsonDb.get_by_attr(id=tag)
            await self.update_metapuzzle(ctx, meta_round)

        emoji = self.get_guild_data(ctx).discord_bot_emoji
        embed = discord.Embed(title="EXTRA PUZZLE SOLUTION ADDED!",
                              description=f"{emoji} :partying_face: Great work! I've updated the solution to `{puzzle.solution}`")
        await ctx.send(embed=embed)
        await self.get_gsheet_cog(ctx).update_solution(puzzle)
        await self.info(ctx, update=True)

    @commands.command(name="mark_as_complete", aliases=["mark_as_solved","complete"])
    @with_puzzle_mutex(wait=False)
    async def mark_as_complete(self, ctx):
        """*Mark a puzzle as completed with a tick*"""
        await self.solve(ctx, "✅")

    @commands.command(aliases=["u"])
    @with_puzzle_mutex(wait=False)
    async def unsolve(self, ctx):
        """*Mark an accidentally solved puzzle as not solved*"""

        if not self.get_puzzle(ctx):
            await self.send_not_puzzle_channel(ctx)
            return
        elif not self.get_puzzle(ctx).solved and len(self.get_puzzle(ctx).solution) == 0:
            await ctx.send(":x: This puzzle appears to not be solved")
            return

        puzzle = self.get_puzzle(ctx)
        prev_solution = puzzle.solution
        puzzle.status = "Unsolved"
        puzzle.solution = ""
        puzzle.solved = False
        puzzle.solve_time = None

        # if self.get_puzzle(ctx).archive_time:
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

        if self.get_hunt_round(ctx):
            await self.update_metapuzzle(ctx, self.get_hunt_round(ctx))

        await self.info(ctx, update=True)
        for sheet in puzzle.additional_sheets:
            SheetsJsonDb.commit(sheet)
        await ctx.send(":white_check_mark: Sheets all restored.")

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
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
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def archive_category(self, ctx, archive_to = None, ignore_channels = [], delete_channels = []):
        """(admin) * Archives category to threads *"""
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
    @commands.has_any_role('Moderator', 'mod', 'admin')
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
                        files.append(await a.to_file(use_cached=True))
                    avatar_url = getattr(author.avatar, 'url', None)
                    moved_message = await webhook[0].send(content=content, username=author.display_name, avatar_url=avatar_url, files = files,
                                                      embeds=message.embeds, thread=thread, wait=True, silent=True)
                    if message.reactions:
                        for reaction in message.reactions:
                            await moved_message.add_reaction(reaction)
                    time.sleep(0.1)
        await channel.delete(reason=self.DELETE_REASON)
        return True

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
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
            print(f"{hunt_channel.name} : {hunt_channel.position}")
            if hunt_channel.position > last_position:
                last_position = hunt_channel.position

        # Delete the channels without many messages and move the others to the end of the main category
        # TODO Look at the channel move method
        for channel in solved_category.channels:
            num_messages = len(await channel.history(limit=10).flatten())
            if num_messages > 7:
                # print(hunt_category.name)
                # print(category.name + "-" + channel.name)
                last_position += 1
                await channel.edit(category=hunt_category, name="solved-" + channel.name, position=last_position)
            else:
                await channel.delete(reason=self.DELETE_REASON)
        #create round object for Sheets Cog


        # round = PuzzleData(category.name)
        # round.round_name = category.name
        # round.round_id = category.id
        # round.google_sheet_id = hunt_settings.drive_sheet_id
        # round.hunt_url = hunt_settings.hunt_url
        # gsheet_cog = self.bot.get_cog("GoogleSheets")
        # if gsheet_cog is not None:
        #     await gsheet_cog.archive_round_spreadsheet(round)
        hunt_general_channel = self.get_hunt_channel(ctx)
        await solved_category.delete(reason=self.DELETE_REASON)
        await hunt_general_channel.send(f":white_check_mark: Solved puzzles successfully archived.")

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin', 'Organisers')
    @with_puzzle_mutex(wait=False)
    async def delete_puzzle(self, ctx):
        """*(admin) Permanently delete a puzzle, its channel and sheet*"""
        # Wrapper for delete command
        if self.get_channel_type(ctx) != "Puzzle":
            await ctx.send(":x: This does not appear to be a puzzle channel")
            return

        try:
            await self.delete_puzzle_data(ctx, self.get_puzzle(ctx))
            if self.get_hunt_round(ctx):
                await self.update_metapuzzle(ctx, self.get_hunt_round(ctx))
            await (self.bot.get_channel(self.get_hunt(ctx).channel_id)
                   .send(f":white_check_mark: Puzzle {self.get_puzzle(ctx).name} successfully deleted and sheet cleaned up."))
            await ctx.channel.delete(reason=self.DELETE_REASON)
        except:
            await ctx.send(f":x: Channel deletion failed")
        self.set_puzzle(ctx, None)

    async def delete_puzzle_data(self, ctx, puzzle: PuzzleData, delete_sheet=True):
        if delete_sheet is True:
            if self.get_gsheet_cog(ctx) is not None:
                await self.get_gsheet_cog(ctx).delete_puzzle_spreadsheet(puzzle)
        PuzzleJsonDb.delete(puzzle.id)
        return True

    @commands.command(aliases=['delete_metapuzzle','delete_group'])
    @commands.has_any_role('Moderator', 'mod', 'admin', 'Organisers')
    async def delete_round(self, ctx):
        """*(admin) Permanently delete a group of puzzles - round/metapuzzle etc.*"""
        if self.get_channel_type(ctx) in self.PUZZLE_GROUPS:
            round_channel = ctx.channel
        else:
            await ctx.send(f":x: This command must be done from the main group channel")
            return

        hunt_general_channel = self.bot.get_channel(self.get_hunt(ctx).channel_id)

        hunt_round = self.get_hunt_round(ctx)
        round_puzzles = PuzzleJsonDb.get_all_from_round(hunt_round.id)
        for round_puzzle in round_puzzles:
            await self.delete_puzzle_data(ctx, round_puzzle)
            await discord.utils.get(self.get_guild(ctx).channels, id=round_puzzle.channel_id).delete(reason=self.DELETE_REASON)

        await self.delete_round_data(hunt_round)

        category = discord.utils.get(self.get_guild(ctx).categories, id=hunt_round.category_id)
        if self.SOLVE_CATEGORY is False:
            solved_divider = self.get_solved_channel(category)
            if solved_divider:
                await solved_divider.delete(reason=self.DELETE_REASON)
        await category.delete(reason=self.DELETE_REASON)

        await hunt_general_channel.send(f":white_check_mark: Round {self.get_hunt_round(ctx).name} successfully deleted.  "
                                        f"All puzzle sheets have been deleted.")
        self.set_puzzle(ctx, None)
        self.set_hunt_round(ctx, None)

    async def delete_round_data(self, hunt_round: RoundData):
        RoundJsonDb.delete(hunt_round.id)
        return True

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def delete_hunt(self, ctx):
        """*(admin) Permanently delete a Hunt*"""
        if self.get_channel_type(ctx) == "Hunt":
            hunt_channel = ctx.channel
        else:
            await ctx.send(f":x: This command must be done from the main hunt channel")
            return
        hunt_puzzles = PuzzleJsonDb.get_all_from_hunt(self.get_hunt(ctx).id)
        for hunt_puzzle in hunt_puzzles:
            await self.delete_puzzle_data(ctx, hunt_puzzle)
            channel = discord.utils.get(self.get_guild(ctx).channels, id=hunt_puzzle.channel_id)
            await channel.delete(reason=self.DELETE_REASON)
        hunt_rounds = RoundJsonDb.get_all(self.get_hunt(ctx).id)
        for hunt_round in hunt_rounds:
            try:
                await self.delete_round_data(hunt_round)
                category = discord.utils.get(self.get_guild(ctx).categories, id=hunt_round.category_id)
                if self.SOLVE_CATEGORY is False:
                    solved_divider = self.get_solved_channel(category)
                    if solved_divider:
                        await solved_divider.delete(reason=self.DELETE_REASON)
                await category.delete(reason=self.DELETE_REASON)
            except:
                pass
        await hunt_channel.delete(reason=self.DELETE_REASON)
        await hunt_channel.category.delete(reason=self.DELETE_REASON)
        HuntJsonDb.delete(self.get_hunt(ctx).id)
        self.set_puzzle(ctx, None)
        self.set_hunt_round(ctx, None)
        self.set_hunt(ctx, None)
        return True

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def debug_puzzle_channel(self, ctx):
        """*(admin) See puzzle metadata*"""

        if self.get_channel_type(ctx) != "Puzzle":
            await self.send_not_puzzle_channel(ctx)
            return

        await ctx.channel.send(f"```json\n{self.get_puzzle(ctx).to_json()}```")

    # async def archive_solved_puzzles(self, guild: discord.Guild) -> List[PuzzleData]:
    #     """Archive puzzles for which sufficient time has elapsed since solve time
    #
    #     Move them to a solved-puzzles channel category, and rename spreadsheet
    #     to start with the text [SOLVED]
    #     """
    #     guild_data = GuildSettingsDb.get(guild.id)
    #     puzzles_to_archive = PuzzleJsonDb.get_solved_puzzles_to_archive(guild_data.id)
    #     gsheet_cog = self.bot.get_cog("GoogleSheets")
    #
    #     puzzles_by_hunt = {}
    #     for puzz in puzzles_to_archive:
    #         hunt = HuntJsonDb.get_by_attr(id=puzz.hunt_id)
    #         logger.info(f"{puzz.name} - archiving")
    #         if not hunt in puzzles_by_hunt:
    #             puzzles_by_hunt[hunt] = []
    #         puzzles_by_hunt[hunt].append(puzz)
    #     # if puzzles_by_hunt:
    #     #     logger.info(puzzles_by_hunt)
    #     for hunt, puzzles in puzzles_by_hunt.items():
    #         if self.SOLVE_CATEGORY:
    #             solved_category_name = self.get_solved_puzzle_category(hunt.name)
    #             solved_category = discord.utils.get(guild.categories, name=solved_category_name)
    #             if not solved_category:
    #                 avail_categories = [c.name for c in guild.categories]
    #                 raise ValueError(
    #                     f"{solved_category_name} category does not exist; available categories: {avail_categories}"
    #             )
    #
    #         for puzzle in puzzles:
    #             channel = discord.utils.get(guild.channels, id=puzzle.channel_id)
    #             if channel:
    #                 if self.SOLVE_CATEGORY:
    #                     await channel.edit(category=solved_category)
    #                 else:
    #                     await channel.move(end=True, category=channel.category)
    #                     message = f"Puzzle channel {channel.name} moved to the end of category {channel.category.name}."
    #                     logger.info(message)
    #             if gsheet_cog:
    #                 """TODO Less efficient now
    #                 Make this more resilient if the sheet has been deleted"""
    #                 # hunt_round = RoundJsonDb.get_by_attr(id=puzzle.round_id)
    #                 # hunt = HuntJsonDb.get_by_attr(id=hunt_round.hunt_id)
    #                 gsheet_cog.set_spreadsheet_id(hunt.google_sheet_id)
    #                 try:
    #                     await gsheet_cog.archive_puzzle_spreadsheet(puzzle, hunt.archive_google_sheet_id)
    #                 except:
    #                     message = f"Unable to update {puzzle.name} from {hunt.name} as solved on Google Sheet."
    #                     logger.info(message)
    #
    #             puzzle.archive_time = datetime.datetime.now(tz=pytz.UTC)
    #             PuzzleJsonDb.commit(puzzle)
    #     return puzzles_to_archive
    #
    # async def archive_solved(self, ctx):
    #     """*(admin) Archive solved puzzles. Done automatically*
    #
    #     Done automatically on task loop, so this is only useful for debugging
    #     """
    #     if not (await self.check_is_bot_channel(ctx)):
    #         return
    #     puzzles_to_archive = await self.archive_solved_puzzles(ctx.guild)
    #     mentions = " ".join([p.channel_mention for p in puzzles_to_archive])
    #     message = f"Archived {len(puzzles_to_archive)} solved puzzle channels: {mentions}"
    #     logger.info(message)
    #     await ctx.send(message)
    #
    # @tasks.loop(seconds=300.0)
    # async def archived_solved_puzzles_loop(self):
    #     """Ref: https://discordpy.readthedocs.io/en/latest/ext/tasks/"""
    #     logger.info(f"Archived solved puzzles loop")
    #     for guild in self.bot.guilds:
    #         try:
    #             await self.archive_solved_puzzles(guild)
    #         except Exception:
    #             logger.exception("Unable to archive solved puzzles for guild {guild.id} {guild.name}")
    #
    # @archived_solved_puzzles_loop.before_loop
    # async def before_archiving(self):
    #     await self.bot.wait_until_ready()
    #     logger.info("Ready to start archiving solved puzzles")

    @tasks.loop(hours=3)
    async def reminder_loop(self, channel):
        reminders = self.REMINDERS
        if self.reminder_index % 2 == 0:
            await channel.send("@everyone You can get some help on how to use PuzzleBot by referring to [Brillig's guide](https://docs.google.com/document/d/1hY-T2nMwHJLHiR-szsmG2F1ZWGIe88jyQpzKiXE1xLY/edit?usp=sharing) or with the `!help` command.  "
                               "The most useful commands are `!p <puzzle_name>` to make a new puzzle, "
                               "`!s <SOLUTION>` to solve one and "
                               "`!info` to get info on the puzzle including a link to the sheet.  Don't forget to go to the overview website for our full hunt status.")
        if self.reminder_index == 0:
            await channel.send("@here " + reminders[0])
        else:
            await channel.send("@here " + random.choice(reminders))
        self.reminder_index += 1

    @commands.hybrid_command(description="Start the reminder loop (organisers only)",)
    @commands.has_any_role('Moderator', 'mod', 'admin', 'Organisers')
    async def start_reminders(self, ctx):
        """*(admin) Start reminder loop*"""
        channel = self.get_hunt_channel(ctx)
        self.reminder_index = 0
        self.reminder_loop.start(channel)
        await ctx.send(":white_check_mark: Reminder loop started.")

    @commands.hybrid_command(description="Stop the reminder loop (organisers only)",)
    @commands.has_any_role('Moderator', 'mod', 'admin', 'Organisers')
    async def stop_reminders(self, ctx):
        """*(admin) Stop reminder loop*"""
        channel = self.get_hunt_channel(ctx)
        self.reminder_loop.cancel()
        await ctx.send("@here Okay, I'll leave you alone for a bit, but remember I'm always watching...")

    def get_hunt_channel(self, ctx):
        return self.bot.get_channel(self.get_hunt(ctx).channel_id)

    def get_round_channel(self, ctx):
        return self.bot.get_channel(self.get_hunt_round(ctx).channel_id)

    def get_solved_channel(self, category):
        solved_channel = None
        for channel in category.channels:
            if channel.name == self.SOLVE_DIVIDER:
                solved_channel = channel
        return solved_channel


async def setup(bot):
    await bot.add_cog(Puzzles(bot))
