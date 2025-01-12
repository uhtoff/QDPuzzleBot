import datetime
import logging
import random
import time
from typing import Any, List, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord import Webhook, Message
import pytz
import aiohttp

from bot.utils import urls, config
from bot.store import MissingPuzzleError, PuzzleData, PuzzleJsonDb, GuildSettings, GuildSettingsDb, HuntSettings, RoundData, RoundJsonDb, HuntData, HuntJsonDb

logger = logging.getLogger(__name__)


class Puzzles(commands.Cog):
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
    STATUSES = ["unstarted", "in progress", "stuck", "needs extraction", "solved", "backsolved"]
    PRIORITIES = ["low", "medium", "high", "very high"]
    REMINDERS = [
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
        self.archived_solved_puzzles_loop.start()
        self.reminder_index=0
        self.guild = None
        self.guild_data = None
        self.channel_type = None
        self.puzzle = None
        self.gsheet_cog = None
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
        # if channel_type == 'Round':
        #     hunt_round = RoundJsonDb.get_by_attr(channel_id=ctx.channel.id,)
        #     if hunt_round:
        #         hunt = HuntJsonDb.get_by_attr(id=hunt_round.hunt_id)
        if channel_type == 'Puzzle':
            puzzle = PuzzleJsonDb.get_by_attr(channel_id=ctx.channel.id,)
            hunt_round = RoundJsonDb.get_by_attr(meta_id=puzzle.id)
            if hunt_round:
                channel_type = 'Round'
            else:
                hunt_round = RoundJsonDb.get_by_attr(category_id=ctx.channel.category.id, )
            if puzzle:
                hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        print (f"Channel type: {channel_type}")
        gsheet_cog = self.gsheet_cog = self.bot.get_cog("GoogleSheets")
        if hunt is not None:
            gsheet_cog.set_spreadsheet_id(hunt.google_sheet_id)
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
        # if self.get_hunt_round(ctx):
        #     RoundJsonDb.commit(self.get_hunt_round(ctx))
        if self.get_hunt(ctx):
            HuntJsonDb.commit(self.get_hunt(ctx))
        if self.get_guild_data(ctx):
            GuildSettingsDb.commit(self.get_guild_data(ctx))

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{type(self).__name__} Cog ready.")

    @app_commands.command(name="slash", description="test slash command")
    async def ping(self, interaction: discord.Interaction):
        bot_latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"Pong! {bot_latency} ms.")

    def clean_name(self, name):
        """Cleanup name to be appropriate for discord channel"""
        name = name.strip()
        if (name[0] == name[-1]) and name.startswith(("'", '"')):
            name = name[1:-1]
        return "-".join(name.lower().split())

    def get_channel_type(self, ctx):
        return self.environment[ctx.message.id]['channel_type']

    def set_channel_type(self, ctx, channel_type):
        self.environment[ctx.message.id]['channel_type'] = channel_type

    def get_hunt(self, ctx):
        return self.environment[ctx.message.id]['hunt']

    def set_hunt(self, ctx, hunt):
        self.environment[ctx.message.id]['hunt'] = hunt

    def get_hunt_round(self, ctx):
        return self.environment[ctx.message.id]['hunt_round']

    def set_hunt_round(self, ctx, hunt_round):
        self.environment[ctx.message.id]['hunt_round'] = hunt_round

    def get_puzzle(self, ctx):
        return self.environment[ctx.message.id]['puzzle']

    def set_puzzle(self, ctx, puzzle):
        self.environment[ctx.message.id]['puzzle'] = puzzle

    def get_guild(self, ctx):
        return self.environment[ctx.message.id]['guild']

    def set_guild(self, ctx, guild):
        self.environment[ctx.message.id]['guild'] = guild

    def get_guild_data(self, ctx):
        return self.environment[ctx.message.id]['guild_data']

    def set_guild_data(self, ctx, guild_data):
        self.environment[ctx.message.id]['guild_data'] = guild_data

    def get_gsheet_cog(self, ctx):
        return self.environment[ctx.message.id]['gsheet_cog']

    def set_gsheet_cog(self, ctx, gsheet_cog):
        self.environment[ctx.message.id]['gsheet_cog'] = gsheet_cog

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        # if isinstance(error, commands.errors.CheckFailure):
        #     await ctx.send('You do not have the correct role for this command.')
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
        category_name = self.clean_name(hunt_name)
        overwrites = None
        role = None
        google_drive_id = None
        if role_name:
            role = await guild.create_role(name=role_name, colour=discord.Colour.random(), mentionable=True, reason=self.ROLE_REASON )
            overwrites = self.get_overwrites(guild, role)

        category = await guild.create_category(category_name, position=max(len(guild.categories) - 2,0))
        text_channel, created_text = await self.get_or_create_channel(
            guild=guild, category=category, channel_name=self.GENERAL_CHANNEL_NAME, channel_type="text", reason=self.HUNT_REASON
        )
        settings = self.get_guild_data(ctx)

        if self.SOLVE_CATEGORY:
            solved_category = await guild.create_category(self.get_solved_puzzle_category(hunt_name), position=max(len(guild.categories) - 2,0))
        else:
            await self.get_or_create_channel(
                guild=guild, category=category, channel_name=self.SOLVE_DIVIDER,
                channel_type="text", reason=self.ROUND_REASON, position=500
            )

        if self.gsheet_cog is not None:
            google_drive_id = await self.gsheet_cog.create_hunt_spreadsheet(hunt_name)

        new_hunt = HuntData(
            name=hunt_name,
            category_id=category.id,
            channel_id=text_channel.id,
            guild_id=settings.id,
            url=hunt_url,
        )

        if google_drive_id:
            new_hunt.google_sheet_id=google_drive_id

        HuntJsonDb.commit(new_hunt)

        # add hunt settings
        await self.send_initial_hunt_channel_messages(ctx, text_channel, hunt=new_hunt)

        return (category, text_channel, True)

    @commands.command(aliases=["p"])
    async def puzzle(self, ctx, *, arg):
        """*Create new puzzle channels: !p puzzle-name*"""

        puzzle_name = arg
        if PuzzleJsonDb.check_duplicates_in_hunt(puzzle_name, self.get_hunt(ctx).id):
            return await ctx.send(f":exclamation: Puzzle **{puzzle_name}** already exists in this hunt, please use a different name")
        # self.get_hunt_round(ctx).num_puzzles += 1
        # RoundJsonDb.commit(self.get_hunt_round(ctx))
        new_puzzle = self.create_puzzle(ctx, puzzle_name, self.get_tag_from_category(ctx.channel.category))

        await self.create_puzzle_channel(ctx, new_puzzle)

    def create_puzzle(self, ctx, puzzle_name, tag = None):
        new_puzzle = PuzzleData(
            name=puzzle_name,
            # round_id=self.get_hunt_round(ctx).id,
            hunt_id=self.get_hunt(ctx).id,
            start_time=datetime.datetime.now(tz=pytz.UTC),
            status='Unsolved',
            priority='Normal'
        )
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

        PuzzleJsonDb.commit(new_puzzle)

        if self.gsheet_cog is not None:
            # update google sheet ID
            self.gsheet_cog.create_puzzle_spreadsheet(new_puzzle)
            if self.get_hunt_round(ctx):
                self.update_metapuzzle(ctx, self.get_hunt_round(ctx))

        return new_puzzle

    def update_metapuzzle(self, ctx, hunt_round):
        round_puzzles = PuzzleJsonDb.get_all_from_round(hunt_round.id)
        metapuzzle = PuzzleJsonDb.get_by_attr(id=hunt_round.meta_id)
        self.gsheet_cog.add_metapuzzle_data(metapuzzle, round_puzzles)

    def get_tag_from_category(self, category):
        hunt_round = RoundJsonDb.get_by_attr(category_id=category.id)
        if hunt_round:
            return hunt_round.id
        else:
            return None

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def add_tag(self, ctx, *, arg):
        """*(admin) For troubleshooting only: !add_tag tag_id*"""
        new_tag = arg
        puzzle = self.get_puzzle(ctx)
        puzzle.tags.append(new_tag)
        await ctx.channel.send(f"```json\n{puzzle.to_json(indent=2)}```")

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def remove_tag(self, ctx, *, arg):
        """*(admin) For troubleshooting only: !remove_tag tag_id*"""
        remove_tag = int(arg)
        puzzle = self.get_puzzle(ctx)
        if remove_tag in puzzle.tags:
            puzzle.tags.remove(remove_tag)
        await ctx.channel.send(f"```json\n{puzzle.to_json(indent=2)}```")

    async def create_puzzle_channel(self, ctx, new_puzzle: PuzzleData, category = None, **kwargs):
        """Create new text channel for puzzle, and optionally a voice channel

        Save puzzle metadata to data_dir, send initial messages to channel, and
        create corresponding Google Sheet if GoogleSheets cog is set up.
        """
        # category = self.bot.get_channel(self.get_hunt_round(ctx).channel_id).category
        send_initial_message = kwargs.get("send_initial_message", True)
        if category is None:
            category = ctx.channel.category
        channel_name = self.clean_name(new_puzzle.name)
        # round_channel = self.get_round_channel(ctx)
        # await round_channel.edit(position=0)
        text_channel, created_text = await self.get_or_create_channel(
            guild=self.get_guild(ctx), category=category, channel_name=channel_name,
            channel_type="text", reason=self.PUZZLE_REASON, position=1
        )
        if created_text:
            new_puzzle.channel_mention=text_channel.mention
            new_puzzle.channel_id=text_channel.id
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
                f":white_check_mark: I've created new puzzle {created_desc} channels for Test: {text_channel.mention}"
            )
        else:
            await ctx.send(
                f"I've found an already existing puzzle channel for {self.get_hunt_round(ctx).name}: {text_channel.mention}"
            )
        PuzzleJsonDb.commit(new_puzzle)
        return (text_channel, created)

    # @commands.command(aliases=["mp"])
    # async def metapuzzle(self, ctx, *, arg: Optional[str]):
    #     """*Create new metapuzzle channel: !mp puzzle-name*
    #     Can also mark a puzzle as a metapuzzle if used within a puzzle channel without a name
    #     Must be posted from within a round category"""
    #     if self.get_channel_type(ctx) in ("Puzzle", "Round"):
    #         puzzle_name = arg
    #         if puzzle_name:
    #             if PuzzleJsonDb.check_duplicates_in_hunt(puzzle_name, self.get_hunt(ctx).id):
    #                 return await ctx.send(
    #                     f":exclamation: **Puzzle {puzzle_name}** already exists in this hunt, please use a different name")
    #             self.get_hunt_round(ctx).num_puzzles += 1
    #             RoundJsonDb.commit(self.get_hunt_round(ctx))
    #             new_puzzle = PuzzleData(
    #                 name=puzzle_name,
    #                 round_id=self.get_hunt_round(ctx).id,
    #                 start_time=datetime.datetime.now(tz=pytz.UTC),
    #                 status='Unsolved',
    #                 priority='Normal',
    #                 metapuzzle = 1,
    #             )
    #             if self.get_hunt(ctx).url:
    #                 # NOTE: this is a heuristic and may need to be updated!
    #                 # This is based on last year's URLs, where the URL format was
    #                 # https://<site>/puzzle/puzzle_name
    #                 prefix = self.get_hunt(ctx).puzzle_prefix or 'puzzle'
    #                 hunt_url_base = self.get_hunt(ctx).url.rstrip("/")
    #                 p_name = self.clean_name(puzzle_name).replace("-", self.get_hunt(ctx).url_sep)
    #                 new_puzzle.url = f"{hunt_url_base}/{prefix}/{p_name}"
    #             round_puzzles = PuzzleJsonDb.get_all_from_round(new_puzzle.round_id)
    #             if self.gsheet_cog is not None:
    #                 # update google sheet ID
    #                 self.gsheet_cog.create_puzzle_spreadsheet(new_puzzle, self.get_hunt_round(ctx), self.get_hunt(ctx))
    #                 await self.gsheet_cog.add_metapuzzle_data(new_puzzle, self.get_hunt_round(ctx), round_puzzles)
    #             await self.create_puzzle_channel(ctx, new_puzzle)
    #             await ctx.send("@here - Metapuzzle opened!")
    #         elif self.get_channel_type(ctx) == "Puzzle":
    #             self.get_puzzle(ctx).metapuzzle = 1
    #             round_puzzles = PuzzleJsonDb.get_all_from_round(self.get_puzzle(ctx).round_id)
    #             await self.gsheet_cog.add_metapuzzle_data(self.get_puzzle(ctx), self.get_hunt_round(ctx), round_puzzles)
    #             await ctx.send(":white_check_mark: Puzzle marked as metapuzzle and links to the round's answers copied to the sheet.")
    #
    #     else:
    #         await ctx.send(f":x: This command must be ran from within a round category")

    @commands.command()
    async def sync_meta(self, ctx):
        """*Sync all the puzzles in this category to the metapuzzle: !sync_meta*"""
        puzzle = self.get_puzzle(ctx)
        if puzzle is None or puzzle.metapuzzle != 1:
            await ctx.send(":x: This does not appear to be a metapuzzle channel")
            return
        meta_category = ctx.channel.category
        meta_round = RoundJsonDb.get_by_attr(category_id=meta_category.id)
        if meta_round:
            for channel in meta_category.channels:
                puzzle = PuzzleJsonDb.get_by_attr(channel_id=channel.id)
                if puzzle:
                    puzzle.tags.append(meta_round.id)
                    PuzzleJsonDb.commit(puzzle)
            self.update_metapuzzle(ctx, meta_round)
        await ctx.send(f":white_check_mark: All the puzzles in this category have been tagged to the meta")

    @commands.command()
    async def add_to_meta(self, ctx, meta_code):
        """*Assign the puzzle to a metapuzzle, this will additionally move it to the category: !move_to_meta <meta_code>*"""
        if self.get_channel_type(ctx) == "Hunt":
            await ctx.send(":x: This does not appear to be a Puzzle channel")
        puzzle = self.get_puzzle(ctx)
        meta_round = RoundJsonDb.get_by_attr(meta_code=meta_code)
        if meta_round:
            new_category = discord.utils.get(self.get_guild(ctx).categories, id=meta_round.category_id)
            await ctx.channel.edit(category=new_category, position = 1)
            puzzle.tags.append(meta_round.id)
            PuzzleJsonDb.commit(puzzle)
            self.update_metapuzzle(ctx, meta_round)
        else:
            await ctx.send(":x: Please send a valid meta code")


    @commands.command(aliases=["r","mp","metapuzzle"])
    async def round(self, ctx, *, arg):
        """*Create new puzzle round: !r round-name*"""
        print(ctx.invoked_with)
        if self.get_channel_type(ctx) != "Hunt":
            await ctx.channel.send(":x: Rounds must be created in the master hunt channel")
            return

        if RoundJsonDb.check_duplicates_in_hunt(arg, self.get_hunt(ctx)):
            return await ctx.send(f":exclamation: **Round {arg}** already exists in this hunt, please use a different name")

        new_round = RoundData(arg)
        new_category_name = self.clean_name(arg)

        # if self.gsheet_cog is not None:
        #     self.gsheet_cog.create_round_overview_spreadsheet(new_round, self.get_hunt(ctx))

        # self.get_hunt(ctx).num_rounds += 1
        # HuntJsonDb.commit(self.get_hunt(ctx))

        guild = ctx.guild
        existing_category = discord.utils.get(guild.categories, name=new_category_name)
        if existing_category or self.get_hunt(ctx).parallel_hunt:
            # Try to append the hunt name to the round category name and check if it still exists
            new_category_name = new_category_name + " — " + self.clean_name(self.get_hunt(ctx).name)
            existing_category = discord.utils.get(guild.categories, name=new_category_name)
        if not existing_category:
            print(f"Creating a new channel category for round: {new_category_name}")
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

        new_round.hunt_id = self.get_hunt(ctx).id
        new_round.category_id = new_category.id

        RoundJsonDb.commit(new_round)

        round_puzzle = self.create_puzzle(ctx, self.clean_name(arg) + " - meta", self.get_tag_from_category(new_category))
        round_puzzle.metapuzzle = 1

        text_channel, created = await self.create_puzzle_channel(ctx, round_puzzle, new_category, send_initial_message=False)

        new_round.meta_id = round_puzzle.id
        new_round.meta_code = int(RoundJsonDb.get_lowest_code_in_hunt(new_round.hunt_id)) + 1

        RoundJsonDb.commit(new_round)

        if self.SOLVE_CATEGORY is False:
            await self.get_or_create_channel(
                guild=guild, category=new_category, channel_name=self.SOLVE_DIVIDER,
                channel_type="text", reason=self.ROUND_REASON, position=500
            )

        initial_message = await self.send_initial_metapuzzle_channel_messages(ctx, text_channel, hunt_round=new_round, puzzle=round_puzzle)
        await initial_message.pin()
        await ctx.send(
            f":white_check_mark: I've created a new round category and meta channel for {self.get_hunt(ctx).name} - {new_round.name}"
        )

    def get_settings(self, ctx):
        data = None
        if self.get_channel_type(ctx) == "Puzzle":
            data = self.get_puzzle(ctx)
        elif self.get_channel_type(ctx) == "Round":
            data = self.get_hunt_round(ctx)
        elif self.get_channel_type(ctx) == "Hunt":
            data = self.get_hunt(ctx)
        else:
            data = self.get_guild_data(ctx)
        return data

    def save_settings(self, ctx, settings):
        if self.get_channel_type(ctx) == "Puzzle":
            PuzzleJsonDb.commit(settings)
        elif self.get_channel_type(ctx) == "Round":
            RoundJsonDb.commit(settings)
        elif self.get_channel_type(ctx) == "Hunt":
            HuntJsonDb.commit(settings)
        else:
            GuildSettingsDb.commit(settings)

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    @commands.has_permissions(manage_channels=True)
    async def show_settings(self, ctx):
        """*(admin) Show channel settings for debug*"""
        await ctx.channel.send(f"```json\n{self.get_settings(ctx).to_json(indent=2)}```")

    @commands.command()
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

    @commands.command(aliases=["ph"])
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

    @commands.command(aliases=["update_setting"])
    @commands.has_any_role('Moderator', 'mod', 'admin')
    @commands.has_permissions(manage_channels=True)
    async def update_settings(self, ctx, setting_key: str, setting_value: str):
        """*(admin) Update channel/hunt/guild settings: !update_settings key value - can tolerate spaces in value with inverted commas*"""
        settings = self.get_settings(ctx)
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
            self.save_settings(ctx, settings)
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

    @commands.command(aliases=["list"])
    async def list_puzzles(self, ctx):
        """*List puzzles in the current Round if invoked in Puzzle or Round channels, or in the entire Hunt if in the Hunt channel*"""
        all_puzzles = {}
        embed_title = ""
        if self.get_channel_type(ctx) == "Round" or self.get_channel_type(ctx) == "Puzzle":
            round_puzzles = PuzzleJsonDb.get_all_from_round(self.get_hunt_round(ctx).id)
            all_puzzles[self.get_hunt_round(ctx).id] = {
                        'name': self.get_hunt_round(ctx).name,
                        'puzzles': round_puzzles
                    }
            embed_title = f"Puzzles in Round {self.get_hunt_round(ctx).name}"
        else:
            await ctx.channel.send(":x: Please use the web interface for an overview of all puzzles")
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
        puzzle_links = (f"""The following are some useful links:
                        • Hunt Website: {hunt.url}
                        • Google Sheet: https://docs.google.com/spreadsheets/d/{hunt.google_sheet_id}
                        """)
        if hunt.username:
            puzzle_links += f"""• Hunt Username: {hunt.username}
            """
        if hunt.password:
            puzzle_links += f"""• Hunt Password: {hunt.password}
            """
        embed.add_field(
            name="Important Links",
            value=puzzle_links,
            inline=False,
        )
        embed.add_field(
                    name="Useful commands",
                    value=f"""The following are some useful commands:
        • `!r <round name>` : Create a new round
        • `!mp <metapuzzle name>` : Create a new metapuzzle
        • `!info` : Repeat this message
        • `!parallel_hunt` : Toggle the hunt being marked as being run in parallel
        • `!set_login username:password` : Set the login details for the hunt if shared
        """,
                    inline=False,
                )
        await channel.send(embed=embed)

    async def send_initial_metapuzzle_channel_messages(self, ctx, channel: discord.TextChannel, **kwargs) -> discord.Message:
        """Send intro message on a rond/meta channel"""
        hunt_round = kwargs.get('hunt_round', self.get_hunt_round(ctx))
        puzzle = kwargs.get('puzzle', self.get_puzzle(ctx))
        embed = discord.Embed(
            description=f"""Welcome to the round/meta channel for {hunt_round.name}!"""
        )
        embed.add_field(
            name="Overview",
            value="This channel is for general discussion of the round and working on the meta, please note the first two column of the sheet are reserved for the puzzle titles and answers and will be overwritten by the bot!",
            inline=False,
        )
        embed.add_field(
                        name="General Commands",
                        value=f"""The following may be useful discord commands:
        • `!p <puzzle-name>` will add a puzzle to this round/meta.
        • `!list` : List all puzzles in this round/meta.
        • `!info` will re-post this message.
        • `!add_to_meta {hunt_round.meta_code}` when used in another puzzle channel will add it to this meta and move it to this category.
        """,
                        inline=False,
                    )

        embed.add_field(
                        name="Metapuzzle commands",
                        value="""The following may be useful discord commands:
        • `!s SOLUTION` will mark this puzzle as solved and archive this channel to #solved-puzzles
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

        try:
            embed.add_field(name="Puzzle URL", value=puzzle.url or "?", inline=False,)
            spreadsheet_url = urls.spreadsheet_url(self.get_hunt(ctx).google_sheet_id, puzzle.google_page_id) if self.get_hunt(ctx).google_sheet_id else "?"
            embed.add_field(name="Google Drive", value=spreadsheet_url,inline=False,)
            embed.add_field(name="Status", value=puzzle.status or "?", inline=False,)
            embed.add_field(name="Type", value=puzzle.puzzle_type or "?", inline=False,)
            embed.add_field(name="Priority", value=puzzle.priority or "?", inline=False,)
        except:
            pass
        return await channel.send(embed=embed)

    async def send_initial_puzzle_channel_messages(self, ctx, channel: discord.TextChannel, **kwargs) -> discord.Message:
        """Send intro message on a puzzle channel"""
        puzzle = kwargs.get("puzzle", self.get_puzzle(ctx))
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
                        value="""The following may be useful discord commands:
        • `!p <puzzle-name>` will add a puzzle to this round/meta puzzle.
        • `!s SOLUTION` will mark this puzzle as solved and archive this channel to #solved-puzzles
        • `!link <url>` will update the link to the puzzle on the hunt website
        • `!info` will re-post this message
        • `!type <puzzle type>` will mark the type of the puzzle
        • `!priority <priority>` will mark the priority of the puzzle
        • `!status <status>` will update the status of the puzzle
        • `!note <note>` can be used to leave a note about ideas/progress
        • `!notes` can be used to see the notes that have been left
        • `!erase_note <note number>` can be used to erase the specified note
        """,
                        inline=False,
                    )
        try:
            embed.add_field(name="Puzzle URL", value=puzzle.url or "?", inline=False,)
            spreadsheet_url = urls.spreadsheet_url(self.get_hunt(ctx).google_sheet_id, puzzle.google_page_id) if self.get_hunt(ctx).google_sheet_id else "?"
            embed.add_field(name="Google Drive", value=spreadsheet_url,inline=False,)
            embed.add_field(name="Status", value=puzzle.status or "?", inline=False,)
            embed.add_field(name="Type", value=puzzle.puzzle_type or "?", inline=False,)
            embed.add_field(name="Priority", value=puzzle.priority or "?", inline=False,)
        except:
            pass
        if kwargs.get("update",False):
            channel_pins = await channel.pins()
            return await channel_pins[0].edit(embed=embed)
        else:
            return await channel.send(embed=embed)

    async def send_not_puzzle_channel(self, ctx):
        # TODO: Fix this
        if ctx.channel and ctx.channel.category.name == self.get_solved_puzzle_category(""):
            await ctx.send(":x: This puzzle appears to already be solved")
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

    def get_solved_puzzle_category(self, hunt_name):
        return f"{hunt_name}-{self.SOLVED_PUZZLES_CATEGORY}"

    @commands.command()
    async def info(self, ctx):
        """*Show discord command help for a puzzle channel*"""
        if self.get_channel_type(ctx) == "Hunt":
            await self.send_initial_hunt_channel_messages(ctx, ctx.channel)
        elif self.get_channel_type(ctx) == "Round":
            await self.send_initial_metapuzzle_channel_messages(ctx, ctx.channel)
        elif self.get_channel_type(ctx) == "Puzzle":
            await self.send_initial_puzzle_channel_messages(ctx, ctx.channel)
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
        await channel.send(embed=embed)
        await self.send_initial_puzzle_channel_messages(ctx, channel, update=True)

    @commands.command()
    async def link(self, ctx, *, url: Optional[str]):
        """*Show or update link to puzzle*"""
        if self.get_puzzle(ctx) and self.get_channel_type(ctx) == "Puzzle":
            self.get_puzzle(ctx).url = url
            if self.gsheet_cog is not None:
                # update google sheet ID
                await self.gsheet_cog.update_puzzle(self.get_puzzle(ctx))
            # await self.update_puzzle_attr_by_command(ctx, "hunt_url", url, reply=False)
            await self.send_state(
                ctx, ctx.channel, self.get_puzzle(ctx), description=":white_check_mark: I've updated:" if url else None
            )
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

    @commands.command()
    async def status(self, ctx, *, status: Optional[str]):
        """*Show or update puzzle status, one of "unstarted", "in progress", "stuck", "needs extraction", "solved", "backsolved"*"""
        if status is not None and status.lower() not in self.STATUSES:
            await ctx.send(f":exclamation: Status should be one of {self.STATUSES}, got \"{status}\"")
            return

        if self.get_puzzle(ctx) and self.get_channel_type(ctx) == "Puzzle":
            self.get_puzzle(ctx).status = status
            await self.send_state(
                ctx, ctx.channel, self.get_puzzle(ctx), description=":white_check_mark: I've updated:" if status else None
            )
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

    @commands.command()
    async def type(self, ctx, *, puzzle_type: Optional[str]):
        """*Show or update puzzle type, e.g. "crossword"*"""
        if self.get_puzzle(ctx) and self.get_channel_type(ctx) == "Puzzle":
            self.get_puzzle(ctx).puzzle_type = puzzle_type
            await self.send_state(
                ctx, ctx.channel, self.get_puzzle(ctx), description=":white_check_mark: I've updated:" if puzzle_type else None
            )
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

    @commands.command()
    async def priority(self, ctx, *, priority: Optional[str]):
        """*Show or update puzzle priority, one of "low", "medium", "high"*"""
        if priority is not None and priority.lower() not in self.PRIORITIES:
            await ctx.send(f":exclamation: Priority should be one of {self.PRIORITIES}, got \"{priority}\"")
            return

        if self.get_puzzle(ctx) and self.get_channel_type(ctx) == "Puzzle":
            self.get_puzzle(ctx).priority = priority
            await self.send_state(
                ctx, ctx.channel, self.get_puzzle(ctx), description=":white_check_mark: I've updated:" if priority else None
            )
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

    @commands.command(aliases=["notes"])
    async def note(self, ctx, *, note: Optional[str]):
        """*Show or add a note about the puzzle*"""
        if self.get_puzzle(ctx) is None or self.get_channel_type(ctx) != "Puzzle":
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
            embed = discord.Embed(description=f"{message}")
            i = len(self.get_puzzle(ctx).notes)
            for x in range(i):
                embed.add_field(
                    name=f"Note {x+1}",
                    value=self.get_puzzle(ctx).notes[x],
                    inline=False
                )
        else:
            embed = discord.Embed(description="No notes left yet, use `!note my note here` to leave a note")
        await ctx.send(embed=embed)

    @commands.command()
    async def erase_note(self, ctx, note_index: int):
        """*Remove a note by index*"""

        if self.get_channel_type(ctx) != "Puzzle":
            await self.send_not_puzzle_channel(ctx)
            return

        if 1 <= note_index <= len(self.get_puzzle(ctx).notes):
            note = self.get_puzzle(ctx).notes[note_index-1]
            del self.get_puzzle(ctx).notes[note_index - 1]
            description = f"Erased note {note_index}: `{note}`"
        else:
            description = f"Unable to find note {note_index}"

        embed = discord.Embed(description=description)
        i = len(self.get_puzzle(ctx).notes)
        for x in range(i):
            embed.add_field(
                name=f"Note {x+1}",
                value=self.get_puzzle(ctx).notes[x],
                inline=False
            )
        await ctx.send(embed=embed)

    @commands.command(aliases=["s","S"])
    async def solve(self, ctx, *, arg):
        """*Mark puzzle as fully solved and update the sheet with the solution: !s SOLUTION*"""

        if not self.get_channel_type(ctx) == "Puzzle":
            await self.send_not_puzzle_channel(ctx)
            return
        elif self.get_puzzle(ctx).solution.len() > 0:
            await ctx.send(":x: This puzzle appears to already be solved")
            return

        solution = arg.strip().upper()
        self.get_puzzle(ctx).status = "Solved"
        self.get_puzzle(ctx).solution = solution
        self.get_puzzle(ctx).solve_time = datetime.datetime.now(tz=pytz.UTC)

        PuzzleJsonDb.commit(self.get_puzzle(ctx))

        emoji = self.get_guild_data(ctx).discord_bot_emoji
        embed = discord.Embed(title="PUZZLE SOLVED!", description=f"{emoji} :partying_face: Great work! Marked the solution as `{solution}`")
        embed.add_field(
            name="Follow-up",
            value="If the solution was mistakenly entered, please message `!unsolve`. "
            "Otherwise, in around 5 minutes, I will automatically move this "
            "puzzle channel to the bottom and archive the Google Spreadsheet",
        )
        await ctx.send(embed=embed)
        await self.send_initial_puzzle_channel_messages(ctx, ctx.channel, update=True)

    @commands.command()
    async def unsolve(self, ctx):
        """*Mark an accidentally solved puzzle as not solved*"""

        if not self.get_channel_type(ctx) == "Puzzle":
            await self.send_not_puzzle_channel(ctx)
            return
        elif self.get_puzzle(ctx).solution.len() == 0:
            await ctx.send(":x: This puzzle appears to not be solved")
            return
        elif self.get_puzzle(ctx).archive_time:
            await ctx.send(":x: This puzzle has already been archived")
            return

        prev_solution = self.get_puzzle(ctx).solution
        self.get_puzzle(ctx).status = "Unsolved"
        self.get_puzzle(ctx).solution = ""
        self.get_puzzle(ctx).solve_time = None

        emoji = self.get_guild_data(ctx).discord_bot_emoji
        embed = discord.Embed(
            description=f"{emoji} Alright, I've unmarked {prev_solution} as the solution. "
            "You'll get'em next time!"
        )
        await ctx.send(embed=embed)
        await self.send_initial_puzzle_channel_messages(ctx, ctx.channel, update=True)

    # @commands.command()
    # @commands.has_any_role('Moderator', 'mod', 'admin')
    # async def archive_round(self, ctx):
    #     """(admin) * Archives round to threads *"""
    #     round_channel = self.get_round_channel(ctx)
    #     hunt_general_channel = self.get_hunt_channel(ctx)
    #     category = ctx.channel.category
    #     # if ctx.channel != round_channel:
    #     #     await ctx.send(f":x: This command must be done from the main round channel")
    #     #     return
    #     success = await self.archive_category(ctx, hunt_general_channel, delete_channels=[self.SOLVE_DIVIDER])
    #     if success:
    #         await ctx.channel.category.delete(reason=self.DELETE_REASON)
    #         await hunt_general_channel.send(f":white_check_mark: Round {category.name} successfully archived.")
    #         return True
    #     else:
    #         return False

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def archive_category(self, ctx, archive_to = None, ignore_channels = [], delete_channels = [], **kwargs):
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
            hunt_round = RoundJsonDb.get_by_attr(id=puzzle.round_id)
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
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def delete_puzzle(self, ctx):
        """*(admin) Permanently delete a channel*"""
        # Wrapper for delete command
        if self.get_channel_type(ctx) != "Puzzle":
            await ctx.send(":x: This does not appear to be a puzzle channel")
            return

        try:
            await self.delete_puzzle_data(self.get_puzzle(ctx))
            # voice_channel = discord.utils.get(
            #     ctx.guild.channels, category=category, type=discord.ChannelType.voice, name=channel.name
            # )
            # if voice_channel:
            #     await voice_channel.delete(reason=self.DELETE_REASON)
            # delete text channel last so that errors can be reported
            await ctx.channel.delete(reason=self.DELETE_REASON)
            self.get_hunt_round(ctx).num_puzzles -= 1
            await (self.bot.get_channel(self.get_hunt_round(ctx).channel_id)
                   .send(f":white_check_mark: Puzzle {self.get_puzzle(ctx).name} successfully deleted and sheet cleaned up."))
        except:
            await ctx.send(f":x: Channel deletion failed")
        self.set_puzzle(ctx, None)

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def delete_round(self, ctx):
        """*(admin) Permanently delete a round*"""
        if self.get_channel_type(ctx) == "Round":
            round_channel = ctx.channel
        else:
            await ctx.send(f":x: This command must be done from the main round channel")
            return
        hunt_general_channel = self.bot.get_channel(self.get_hunt(ctx).channel_id)
        await self.delete_round_data(self.get_hunt_round(ctx))

        self.get_hunt(ctx).num_rounds -= 1
        await hunt_general_channel.send(f":white_check_mark: Round {self.get_hunt_round(ctx).name} successfully deleted.  "
                                        f"All puzzle sheets have been deleted.")
        self.set_hunt_round(ctx, None)

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
            await self.delete_puzzle_data(hunt_puzzle)
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
        self.set_hunt(ctx, None)
        # categories = self.get_guild(ctx).categories
        # for category in categories:
        #     await category.delete(reason=self.DELETE_REASON)
        # channels = self.get_guild(ctx).channels
        # for channel in channels:
        #     await channel.delete(reason=self.DELETE_REASON)
        return True

    async def delete_round_data(self, hunt_round: RoundData):
        # round_category = self.bot.get_category(hunt_round.category_id)
        #
        # # Delete the puzzles in the channel
        # # for channel in round_channel.category.channels:
        # #     if channel != round_channel:
        # #         puzzle = PuzzleJsonDb.get_by_attr(channel_id=channel.id)
        # #         if puzzle:
        # #             await self.delete_puzzle_data(puzzle)
        # #         await channel.delete(reason=self.DELETE_REASON)
        # # if self.gsheet_cog is not None:
        # #     await self.gsheet_cog.delete_round_spreadsheet(hunt_round)
        # # await round_channel.delete(reason=self.DELETE_REASON)
        # await round_category.delete(reason=self.DELETE_REASON)
        RoundJsonDb.delete(hunt_round.id)
        return True

    async def delete_puzzle_data(self, puzzle: PuzzleData, delete_sheet=True):
        # hunt_round = RoundJsonDb.get_by_attr(id=puzzle.round_id)
        if delete_sheet is True:
            if self.gsheet_cog is not None:
                await self.gsheet_cog.delete_puzzle_spreadsheet(puzzle)
        PuzzleJsonDb.delete(puzzle.id)
        return True

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def debug_puzzle_channel(self, ctx):
        """*(admin) See puzzle metadata*"""

        if self.get_channel_type(ctx) != "Puzzle":
            await self.send_not_puzzle_channel(ctx)
            return

        await ctx.channel.send(f"```json\n{self.get_puzzle(ctx).to_json()}```")

    async def archive_solved_puzzles(self, guild: discord.Guild) -> List[PuzzleData]:
        """Archive puzzles for which sufficient time has elapsed since solve time

        Move them to a solved-puzzles channel category, and rename spreadsheet
        to start with the text [SOLVED]
        """
        guild_data = GuildSettingsDb.get(guild.id)
        puzzles_to_archive = PuzzleJsonDb.get_solved_puzzles_to_archive(guild_data.id)
        gsheet_cog = self.bot.get_cog("GoogleSheets")

        puzzles_by_hunt = {}
        for puzz in puzzles_to_archive:
            hunt = HuntJsonDb.get_by_attr(id=puzz.hunt_id)
            logger.info(f"{puzz.name} - archiving")
            if not hunt in puzzles_by_hunt:
                puzzles_by_hunt[hunt] = []
            puzzles_by_hunt[hunt].append(puzz)
        # if puzzles_by_hunt:
        #     logger.info(puzzles_by_hunt)
        for hunt, puzzles in puzzles_by_hunt.items():
            if self.SOLVE_CATEGORY:
                solved_category_name = self.get_solved_puzzle_category(hunt.name)
                solved_category = discord.utils.get(guild.categories, name=solved_category_name)
                if not solved_category:
                    avail_categories = [c.name for c in guild.categories]
                    raise ValueError(
                        f"{solved_category_name} category does not exist; available categories: {avail_categories}"
                )

            for puzzle in puzzles:
                channel = discord.utils.get(guild.channels, id=puzzle.channel_id)
                if channel:
                    if self.SOLVE_CATEGORY:
                        await channel.edit(category=solved_category)
                    else:
                        await channel.move(end=channel.category)
                        message = f"Puzzle channel {channel.name} moved to the end of category {channel.category.name}."
                        logger.info(message)
                if gsheet_cog:
                    """TODO Less efficient now
                    Make this more resilient if the sheet has been deleted"""
                    # hunt_round = RoundJsonDb.get_by_attr(id=puzzle.round_id)
                    # hunt = HuntJsonDb.get_by_attr(id=hunt_round.hunt_id)
                    gsheet_cog.set_spreadsheet_id(hunt.google_sheet_id)
                    try:
                        await gsheet_cog.archive_puzzle_spreadsheet(puzzle)
                    except:
                        message = f"Unable to update {puzzle.name} from {hunt.name} as solved on Google Sheet."
                        logger.info(message)

                puzzle.archive_time = datetime.datetime.now(tz=pytz.UTC)
                PuzzleJsonDb.commit(puzzle)
        return puzzles_to_archive

    async def archive_solved(self, ctx):
        """*(admin) Archive solved puzzles. Done automatically*

        Done automatically on task loop, so this is only useful for debugging
        """
        if not (await self.check_is_bot_channel(ctx)):
            return
        puzzles_to_archive = await self.archive_solved_puzzles(ctx.guild)
        mentions = " ".join([p.channel_mention for p in puzzles_to_archive])
        message = f"Archived {len(puzzles_to_archive)} solved puzzle channels: {mentions}"
        logger.info(message)
        await ctx.send(message)

    @tasks.loop(seconds=300.0)
    async def archived_solved_puzzles_loop(self):
        """Ref: https://discordpy.readthedocs.io/en/latest/ext/tasks/"""
        logger.info(f"Archived solved puzzles loop")
        for guild in self.bot.guilds:
            try:
                await self.archive_solved_puzzles(guild)
            except Exception:
                logger.exception("Unable to archive solved puzzles for guild {guild.id} {guild.name}")

    @archived_solved_puzzles_loop.before_loop
    async def before_archiving(self):
        await self.bot.wait_until_ready()
        logger.info("Ready to start archiving solved puzzles")

    @tasks.loop(hours=3)
    async def reminder_loop(self, channel):
        reminders = self.REMINDERS
        if self.reminder_index % 2 == 0:
            await channel.send("@everyone You can get help on how to use PuzzleBot with the `!help` command.  "
                               "The most useful commands are `!p <puzzle_name>` to make a new puzzle, "
                               "`!s <SOLUTION>` to solve one and "
                               "`!info` to get info on the puzzle including a link to the sheet")
        if self.reminder_index == 0:
            await channel.send("@here " + reminders[0])
        else:
            await channel.send("@here " + random.choice(reminders))
        self.reminder_index += 1

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def start_reminders(self, ctx):
        """*(admin) Start reminder loop*"""
        channel = self.get_hunt_channel(ctx)
        self.reminder_index = 0
        self.reminder_loop.start(channel)

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def stop_reminders(self, ctx):
        """*(admin) Stop reminder loop*"""
        channel = self.get_hunt_channel(ctx)
        self.reminder_loop.cancel()
        await channel.send("@here Okay, I'll leave you alone for a bit, but remember I'm always watching...")

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
