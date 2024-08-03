import datetime
import logging
import random
from typing import Any, List, Optional

import discord
from discord.ext import commands, tasks
from discord import Webhook, Message
import pytz
import aiohttp

from bot.utils import urls
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
    MASTER_SPREADSHEET_ID = "1CkLpcL8jUVBjSrs8tcfWFp2uGvnVTUJhl0FgXHsXH7M"
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
        self.hunt = None
        self.hunt_round = None
        self.puzzle = None
        self.gsheet_cog = None

    async def cog_before_invoke(self, ctx):
        """Before command invoked setup puzzle objects and channel type"""
        self.guild = ctx.guild
        self.guild_data = GuildSettingsDb.get(ctx.guild.id)
        self.channel_type = GuildSettingsDb.get_channel_type(ctx.channel.id)
        if self.channel_type is None:
            self.channel_type = GuildSettingsDb.get_channel_type(ctx.channel.category.id)
        if self.channel_type is None:
            self.channel_type = "Guild"
        await ctx.channel.send(self.channel_type + " channel")
        if self.channel_type == 'Hunt':
            self.hunt = HuntJsonDb.get_by_attr(channel_id=ctx.channel.id,)
        if self.channel_type == 'Round':
            self.hunt_round = RoundJsonDb.get_by_attr(channel_id=ctx.channel.id,)
            self.hunt = HuntJsonDb.get_by_attr(id=self.hunt_round.hunt_id)
        if self.channel_type == 'Puzzle':
            self.puzzle = PuzzleJsonDb.get_by_attr(channel_id=ctx.channel.id,)
            self.hunt_round = RoundJsonDb.get_by_attr(id=self.puzzle.round_id)
            self.hunt = HuntJsonDb.get_by_attr(id=self.hunt_round.hunt_id)
        self.gsheet_cog = self.bot.get_cog("GoogleSheets")
        if self.hunt is not None:
            self.gsheet_cog.set_spreadsheet_id(self.hunt.google_sheet_id)

    async def cog_after_invoke(self, ctx):
        """After command invoked ensure changes committed to database"""
        if self.puzzle:
            PuzzleJsonDb.commit(self.puzzle)
        if self.hunt_round:
            RoundJsonDb.commit(self.hunt_round)
        if self.hunt:
            HuntJsonDb.commit(self.hunt)
        if self.guild_data:
            GuildSettingsDb.commit(self.guild_data)

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{type(self).__name__} Cog ready.")

    def clean_name(self, name):
        """Cleanup name to be appropriate for discord channel"""
        name = name.strip()
        if (name[0] == name[-1]) and name.startswith(("'", '"')):
            name = name[1:-1]
        return "-".join(name.lower().split())

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
            return await self.create_hunt(ctx, hunt_name, hunt_url, role)

        raise ValueError(f"Unable to parse hunt name {arg}, try using `!h hunt-name:hunt-url`")


    @commands.command(aliases=["p"])
    async def puzzle(self, ctx, *, arg):
        """*Create new puzzle channels: !p puzzle-name*

        Must be posted from within a round category"""
        if self.channel_type in ("Hunt", "Round"):
            puzzle_name = arg
            await self.create_puzzle_channel(ctx, puzzle_name)

        else:
            raise ValueError(f":x: This command must be ran from within a round category")



    @commands.command(aliases=["r"])
    async def round(self, ctx, *, arg):
        """*Create new puzzle round: !r round-name*"""
        if self.channel_type != "Hunt":
            await ctx.channel.send(":x: Rounds must be created in the master hunt channel")
            return
        new_round = RoundData(arg)
        new_category_name = self.clean_name(arg)
        guild = ctx.guild
        existing_category = discord.utils.get(guild.categories, name=new_category_name)
        if existing_category:
            # Try to append the hunt name to the round category name and check if it still exists
            new_category_name = new_category_name + " — " + self.clean_name(self.hunt.name)
            existing_category = discord.utils.get(guild.categories, name=new_category_name)
        if not existing_category:
            print(f"Creating a new channel category for round: {new_category_name}")
            role = None
            if self.hunt.role_id:
                role = discord.utils.get(guild.roles, id=self.hunt.role_id)
            overwrites = self.get_overwrites(guild, role)
            position = ctx.channel.category.position + 1
            new_category = await ctx.channel.category.clone(name=new_category_name)
            # await category.edit(overwrites=overwrites, position=position)
            await new_category.edit(position=position)
        else:
            raise ValueError(f"Category {new_category_name} already present in this server.")

        text_channel, created_text = await self.get_or_create_channel(
                guild=guild, category=new_category, channel_name=self.GENERAL_CHANNEL_NAME + "-" + self.clean_name(arg),
                position=1,
                channel_type="text", reason=self.ROUND_REASON
            )

        if self.SOLVE_CATEGORY is False:
            await self.get_or_create_channel(
                guild=guild, category=new_category, channel_name=self.SOLVE_DIVIDER,
                channel_type="text", reason=self.ROUND_REASON, position=500
            )

        new_round.hunt_id = self.hunt.id
        new_round.channel_id = text_channel.id
        new_round.category_id = new_category.id

        if self.gsheet_cog is not None:
            await self.gsheet_cog.create_round_overview_spreadsheet(new_round, self.hunt)

        self.hunt.num_rounds += 1

        RoundJsonDb.commit(new_round)

        initial_message = await self.send_initial_round_channel_messages(text_channel, hunt_round=new_round)
        await initial_message.pin()

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    @commands.has_permissions(manage_channels=True)
    async def show_settings(self, ctx):
        """*(admin) Show guild-level settings*"""
        print(self.guild_data.to_json(indent=2))
        await ctx.channel.send(f"```json\n{self.guild_data.to_json(indent=2)}```")

    @commands.command(aliases=["update_setting"])
    @commands.has_any_role('Moderator', 'mod', 'admin')
    @commands.has_permissions(manage_channels=True)
    async def update_settings(self, ctx, setting_key: str, setting_value: str):
        """*(admin) Update guild setting: !update_settings key value*"""
        guild_id = ctx.guild.id
        settings = GuildSettingsDb.get(guild_id)
        hunt_id = ctx.channel.category.id
        hunt_name = ctx.channel.category.name
        if hunt_id in settings.hunt_settings and hasattr(settings.hunt_settings[hunt_id], setting_key):
            old_value = getattr(settings.hunt_settings[hunt_id], setting_key)
            setattr(settings.hunt_settings[hunt_id], setting_key, setting_value)
            GuildSettingsDb.commit(settings)
            await ctx.send(f":white_check_mark: Updated `{setting_key}={setting_value}` from old value: `{old_value}` for hunt `{hunt_name}`")
        elif hasattr(settings, setting_key):
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
            GuildSettingsDb.commit(settings)
            await ctx.send(f":white_check_mark: Updated `{setting_key}={value}` from old value: `{old_value}`")
        else:
            await ctx.send(f":exclamation: Unrecognized setting key: `{setting_key}`. Use `!show_settings` for more info.")

    @commands.command(aliases=['import'])
    @commands.has_any_role('Moderator', 'mod', 'admin')
    @commands.has_permissions(manage_channels=True)
    async def import_puzzles(self, ctx):
        """*Import puzzles from the file system to the database*"""
        guild = ctx.guild
        settings = GuildSettingsDb.get(guild.id)
        if ctx.channel.name != self.GENERAL_CHANNEL_NAME:
            # all_puzzles = PuzzleJsonDb.get_all(ctx.guild.id, self.get_puzzle_data_from_channel(ctx.channel).hunt_id)
            all_puzzles = PuzzleJsonDb.get_all_fs(ctx.guild.id, settings.category_mapping[ctx.channel.category.id])
        else:
            # all_puzzles = PuzzleJsonDb.get_all(ctx.guild.id, settings.category_mapping[ctx.channel.category.id])
            all_puzzles = PuzzleJsonDb.get_all_fs(ctx.guild.id, ctx.channel.category.id)
        for puzzle in all_puzzles:
            PuzzleJsonDb.commit(puzzle)

    @commands.command(aliases=['import_all'])
    @commands.has_any_role('Moderator', 'mod', 'admin')
    @commands.has_permissions(manage_channels=True)
    async def import_all_puzzles(self, ctx):
        """*Import all puzzles from the file system to the database*"""
        all_puzzles = PuzzleJsonDb.get_all_fs(ctx.guild.id)
        for puzzle in all_puzzles:
            PuzzleJsonDb.commit(puzzle)

    @commands.command(aliases=["list"])
    async def list_puzzles(self, ctx):
        """*List puzzles in the current round*"""
        settings = GuildSettingsDb.get(self.guild.id)
        all_puzzles = {}
        embed_title = ""
        if self.channel_type == "Round" or self.channel_type == "Puzzle":
            round_puzzles = PuzzleJsonDb.get_all_from_round(self.hunt_round.id)
            all_puzzles[self.hunt_round.id] = {
                        'name': self.hunt_round.name,
                        'puzzles': round_puzzles
                    }
            embed_title = f"Puzzles in Round {self.hunt_round.name}"
        else:
            hunt_puzzles = PuzzleJsonDb.get_all_from_hunt(self.hunt.id)
            for hunt_puzzle in hunt_puzzles:
                if hunt_puzzle.round_id not in all_puzzles:
                    hunt_round = RoundJsonDb.get_by_attr(id=hunt_puzzle.round_id)
                    all_puzzles[hunt_round.id] = {
                        'name': hunt_round.name,
                        'puzzles': []
                    }
                all_puzzles[hunt_puzzle.round_id]['puzzles'].append(hunt_puzzle)
            embed_title = f"Puzzles in Hunt {self.hunt.name}"

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

    @commands.command()
    async def list_all_puzzles(self, ctx):
        """*List all puzzles and their statuses*"""
        guild = ctx.guild
        settings = GuildSettingsDb.get(guild.id)
        if ctx.channel.name != self.GENERAL_CHANNEL_NAME:
            all_puzzles = PuzzleJsonDb.get_all(ctx.guild.id, settings.category_mapping[ctx.channel.category.id])
        else:
            all_puzzles = PuzzleJsonDb.get_all(ctx.guild.id, ctx.channel.category.id)
        all_puzzles = PuzzleData.sort_by_round_start(all_puzzles)
        embed = discord.Embed()
        cur_round = None
        puzzle_count = 1
        round_part = 1
        message = ""
        # Create a message with a new embed field per round,
        # listing all puzzles in the embed field
        for puzzle in all_puzzles:
            print(puzzle_count)
            if cur_round is None:
                cur_round = puzzle.round_name
            if puzzle_count > 20:
                # Too many puzzles, split the embed
                round_name = cur_round + " :part " + str(round_part)
                embed.add_field(name=round_name, value=message)
                message = ""
                round_part += 1
                puzzle_count = 1
            if puzzle.round_name != cur_round:

                # Reached next round, add new embed field
                if round_part > 1:
                    round_name = cur_round + " :part " + str(round_part)
                else:
                    round_name = cur_round
                embed.add_field(name=round_name, value=message)
                cur_round = puzzle.round_name
                message = ""
                puzzle_count = 1
                round_part = 1
            message += f"{puzzle.channel_mention}"
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
            embed.add_field(name=cur_round, value=message)

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
        settings = self.guild_data

        if self.SOLVE_CATEGORY:
            solved_category = await guild.create_category(self.get_solved_puzzle_category(hunt_name), position=max(len(guild.categories) - 2,0))

        if self.gsheet_cog is not None:
            google_drive_id = await self.gsheet_cog.create_hunt_spreadsheet(hunt_name)

        new_hunt = HuntData(
            name=hunt_name,
            category_id=category.id,
            channel_id=text_channel.id,
            guild_id=settings.id,
            url=hunt_url,
        )

        hs = HuntSettings(
            hunt_name=hunt_name,
            hunt_id=category.id,
            hunt_url=hunt_url,
        )
        if role:
            hs.role_id=role.id
        if google_drive_id:
            new_hunt.google_sheet_id=google_drive_id
            hs.drive_sheet_id=google_drive_id

        # gsheet_cog = self.bot.get_cog("GoogleSheets")
        # print("google sheets cog:", gsheet_cog)
        # if gsheet_cog is not None:
        #     gsheet_cog.set_metadata('num_rounds', '0')
            # update google sheet ID
            #nexus_spreadsheet, hunt_folder = await gsheet_cog.create_nexus_spreadsheet(text_channel, hunt_name)
            #hs.drive_nexus_sheet_id = nexus_spreadsheet.id
            #hs.drive_parent_id = hunt_folder["id"]

        # add hunt settings
        settings.hunt_settings[category.id] = hs
        GuildSettingsDb.commit(settings)
        await self.send_initial_hunt_channel_messages(text_channel, hunt=new_hunt)
        HuntJsonDb.commit(new_hunt)

        return (category, text_channel, True)

    async def create_puzzle_channel(self, ctx, puzzle_name: str):
        """Create new text channel for puzzle, and optionally a voice channel

        Save puzzle metadata to data_dir, send initial messages to channel, and
        create corresponding Google Sheet if GoogleSheets cog is set up.
        """
        category = self.bot.get_channel(self.hunt_round.channel_id).category
        channel_name = self.clean_name(puzzle_name)

        text_channel, created_text = await self.get_or_create_channel(
            guild=self.guild, category=category, channel_name=channel_name,
            channel_type="text", reason=self.PUZZLE_REASON, position=2
        )

        if created_text:
            await text_channel.move(after=ctx.channel)
            new_puzzle = PuzzleData(
                name=puzzle_name,
                round_id=self.hunt_round.id,
                channel_mention=text_channel.mention,
                channel_id=text_channel.id,
                start_time=datetime.datetime.now(tz=pytz.UTC),
                status='Unsolved',
                priority='Normal'
            )
            if self.hunt.url:
                # NOTE: this is a heuristic and may need to be updated!
                # This is based on last year's URLs, where the URL format was
                # https://<site>/puzzle/puzzle_name
                prefix = self.hunt.puzzle_prefix or 'puzzles'
                hunt_url_base = self.hunt.url.rstrip("/")
                if channel_name == "meta":
                    # Use the round name in the URL
                    p_name = self.hunt_round.name.lower().replace("-", self.hunt.url_sep)
                else:
                    p_name = channel_name.replace("-", self.hunt.url_sep)
                new_puzzle.url = f"{hunt_url_base}/{prefix}/{p_name}"

            if puzzle_name != 'meta':
                if self.gsheet_cog is not None:
                    # update google sheet ID
                    await self.gsheet_cog.create_puzzle_spreadsheet(new_puzzle, self.hunt_round, self.hunt)

            self.hunt_round.num_puzzles += 1
            initial_message = await self.send_initial_puzzle_channel_messages(text_channel, puzzle=new_puzzle)
            await initial_message.pin()

        created_voice = False
        if created_voice:
            voice_channel, created_voice = await self.get_or_create_channel(
                guild=self.guild, category=category, channel_name=channel_name, channel_type="voice", reason=self.PUZZLE_REASON
            )
            if created_voice:
                new_puzzle.voice_channel_id = voice_channel.id
        created = created_text or created_voice
        if created:
            if created_text and created_voice:
                created_desc = "text and voice"  # I'm sure there's a more elegant way to do this ..
            elif created_text:
                created_desc = "text"
            elif created_voice:
                created_desc = "voice"

            await ctx.send(
                f":white_check_mark: I've created new puzzle {created_desc} channels for {self.hunt_round.name}: {text_channel.mention}"
            )
        else:
            await ctx.send(
                f"I've found an already existing puzzle channel for {self.hunt_round.name}: {text_channel.mention}"
            )
        PuzzleJsonDb.commit(new_puzzle)
        return (text_channel, created)

    async def send_initial_hunt_channel_messages(self, channel: discord.TextChannel, **kwargs):
        hunt = kwargs.get('hunt',self.hunt)
        embed = discord.Embed(
            description=f"""Welcome to the general channel for {hunt.name}!"""
        )
        embed.add_field(
            name="Overview",
            value="This channel "
            " has info about the hunt itself and it is where you will create new round channels",
            inline=False,
        )
        embed.add_field(
            name="Important Links",
            value=f"""The following are some useful links:
• Hunt Website: {hunt.url}
• Hunt Username: {hunt.username}
• Hunt Password: {hunt.password}
• Google Sheet: https://docs.google.com/spreadsheets/d/{hunt.google_sheet_id}
""",
            inline=False,
        )
        embed.add_field(
                    name="Useful commands",
                    value=f"""The following are some useful commands:
        • `!r <round name>` : Create a new round
        • `!list` : List all added puzzles
        • `!info` : Repeat this message
        """,
                    inline=False,
                )
        await channel.send(embed=embed)

    async def send_initial_round_channel_messages(self, channel: discord.TextChannel, **kwargs) -> discord.Message:
        """Send intro message on a round channel"""
        hunt_round = kwargs.get('hunt_round', self.hunt_round)
        embed = discord.Embed(
            description=f"""Welcome to the round channel for {hunt_round.name}!"""
        )
        embed.add_field(
            name="Overview",
            value="This channel is for general discussion of the round and some early meta theorising.  Once meta(s) unlock then make their own channels with !p",
            inline=False,
        )
        embed.add_field(
                        name="Commands",
                        value="""The following may be useful discord commands:
        • `!p <puzzle-name>` will add a puzzle to this round.
        • `!list` : List all puzzles in this round
        • `!info` will re-post this message
        """,
                        inline=False,
                    )

        try:
            embed.add_field(name="Hunt URL", value=self.hunt.url or "?", inline=False,)
            spreadsheet_url = urls.spreadsheet_url(self.hunt.google_sheet_id, hunt_round.google_page_id) or "?"
            embed.add_field(name="Google Drive Overview Page", value=spreadsheet_url, inline=False,)
        except:
            pass
        return await channel.send(embed=embed)


    async def send_initial_puzzle_channel_messages(self, channel: discord.TextChannel, **kwargs) -> discord.Message:
        """Send intro message on a puzzle channel"""
        puzzle = kwargs.get("puzzle", self.puzzle)
        embed = discord.Embed(
            description=f"""Welcome to the puzzle channel for {puzzle.name} in {self.hunt_round.name}!"""
        )
        embed.add_field(
            name="Overview",
            value="This channel is a good place to discuss how to tackle this puzzle. Usually you'll want to do most of the puzzle work itself on Google Sheets / Docs.",
            inline=False,
        )
        embed.add_field(
                        name="Commands",
                        value="""The following may be useful discord commands:
        • `!p <puzzle-name>` will add a puzzle to this round.
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
            spreadsheet_url = urls.spreadsheet_url(self.hunt.google_sheet_id, puzzle.google_page_id) if self.hunt.google_sheet_id else "?"
            embed.add_field(name="Google Drive", value=spreadsheet_url,inline=False,)
            embed.add_field(name="Status", value=puzzle.status or "?", inline=False,)
            embed.add_field(name="Type", value=puzzle.puzzle_type or "?", inline=False,)
            embed.add_field(name="Priority", value=puzzle.priority or "?", inline=False,)
        except:
            pass
        return await channel.send(embed=embed)

    async def update_initial_puzzle_channel_messages(self, channel: discord.TextChannel) -> discord.Message:
        """Send intro message on a puzzle channel"""
        embed = discord.Embed(
            description=f"""Welcome to the puzzle channel for {self.puzzle.name} in {self.hunt_round.name}!"""
        )
        embed.add_field(
            name="Overview",
            value="This channel is a good place to discuss how to tackle this puzzle. Usually you'll want to do most of the puzzle work itself on Google Sheets / Docs.",
            inline=False,
        )
        embed.add_field(
            name="Commands",
            value="""The following may be useful discord commands:
        • `!p <puzzle-name>` will add a puzzle to this round.
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
            embed.add_field(name="Hunt URL", value=self.puzzle.hunt_url or "?", inline=False, )
            spreadsheet_url = urls.spreadsheet_url(self.hunt.google_sheet_id,
                                                   self.puzzle.google_page_id) if self.hunt.google_sheet_id else "?"
            embed.add_field(name="Google Drive", value=spreadsheet_url, inline=False, )
            embed.add_field(name="Status", value=self.puzzle.status or "?", inline=False, )
            embed.add_field(name="Type", value=self.puzzle.puzzle_type or "?", inline=False, )
            embed.add_field(name="Priority", value=self.puzzle.priority or "?", inline=False, )
        except:
            pass
        channel_pins = await channel.pins()
        return await channel_pins[0].edit(embed=embed)

    async def send_not_puzzle_channel(self, ctx):
        # TODO: Fix this
        if ctx.channel and ctx.channel.category.name == self.get_solved_puzzle_category(""):
            await ctx.send(":x: This puzzle appears to already be solved")
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")

    def get_solved_puzzle_category(self, hunt_name):
        return f"{hunt_name}-{self.SOLVED_PUZZLES_CATEGORY}"


    def get_puzzle_data_from_channel(self, channel) -> Optional[PuzzleData]:
        """Extract puzzle data based on the channel name and category name
        Looks up the corresponding JSON data
        """
        if not channel.category:
            return None

        guild_id = channel.guild.id
        round_id = channel.category.id
        round_name = channel.category.name
        puzzle_id = channel.id
        puzzle_name = channel.name
        settings = GuildSettingsDb.get_cached(guild_id)
        try:
            hunt_id = settings.category_mapping[channel.category.id]
            return PuzzleJsonDb.get(guild_id, puzzle_id, round_id, hunt_id)
        except: #MissingPuzzleError
            print(f"Unable to retrieve puzzle={puzzle_id} round={round_id} {round_name}/{puzzle_name}")
            return None

    @commands.command()
    async def info(self, ctx):
        """*Show discord command help for a puzzle channel*"""
        if self.channel_type == "Hunt":
            await self.send_initial_hunt_channel_messages(ctx.channel)
        elif self.channel_type == "Round":
            await self.send_initial_round_channel_messages(ctx.channel)
        elif self.channel_type == "Puzzle":
            await self.send_initial_puzzle_channel_messages(ctx.channel)
        else:
            await ctx.send(":x: This does not appear to be a hunt channel")
        # guild_id = ctx.guild.id
        # settings = GuildSettingsDb.get(guild_id)
        # hunt_id = ctx.channel.category.id
        # if hunt_id in settings.hunt_settings:
        #     settings = settings.hunt_settings[hunt_id]
        #     await self.send_initial_hunt_channel_messages(settings, ctx.channel)
        # elif self.get_puzzle_data_from_channel(ctx.channel) is not None:
        #     await self.send_initial_puzzle_channel_messages(ctx.channel)
        # elif ctx.channel.category.id in settings.category_mapping:
        #     hunt_id = settings.category_mapping[ctx.channel.category.id]
        #     settings = settings.hunt_settings[hunt_id]
        #     await self.send_initial_round_channel_messages(settings, ctx.channel)
        # else:
        #     await ctx.send(":x: This does not appear to be a hunt channel")
    #    puzzle_data = self.get_puzzle_data_from_channel(ctx.channel)
#        if not puzzle_data:
#            await self.send_not_puzzle_channel(ctx)
#            return
#        await self.send_state(
#            ctx.channel, puzzle_data
#        )

    async def update_puzzle_attr_by_command(self, ctx, attr, value, message=None, reply=True):
        """Common pattern where we want to update a single field in PuzzleData based on command"""
        puzzle_data = self.get_puzzle_data_from_channel(ctx.channel)
        if not puzzle_data:
            await self.send_not_puzzle_channel(ctx)
            return

        message = message or attr
        if value:
            setattr(puzzle_data, attr, value)
            PuzzleJsonDb.commit(puzzle_data)
            message = "Updated! " + message

        if reply:
            embed = discord.Embed(description=f"""{message}: {getattr(puzzle_data, attr)}""")
            await ctx.send(embed=embed)
        return puzzle_data

    async def send_state(self, channel: discord.TextChannel, puzzle_data: PuzzleData, description=None):
        """Send simple embed showing relevant links"""
        embed = discord.Embed(description=description)
        embed.add_field(name="Hunt URL", value=puzzle_data.url or "?")
        spreadsheet_url = urls.spreadsheet_url(self.hunt.google_sheet_id, puzzle_data.google_page_id) if self.hunt.google_sheet_id else "?"
        embed.add_field(name="Google Drive", value=spreadsheet_url)
        embed.add_field(name="Status", value=puzzle_data.status or "?")
        embed.add_field(name="Type", value=puzzle_data.puzzle_type or "?")
        embed.add_field(name="Priority", value=puzzle_data.priority or "?")
        await channel.send(embed=embed)
        await self.update_initial_puzzle_channel_messages(channel)

    @commands.command()
    async def link(self, ctx, *, url: Optional[str]):
        """*Show or update link to puzzle*"""
        if self.puzzle:
            self.puzzle.url = url
            if self.gsheet_cog is not None:
                # update google sheet ID
                await self.gsheet_cog.update_url(self.puzzle)
            # await self.update_puzzle_attr_by_command(ctx, "hunt_url", url, reply=False)
            await self.send_state(
                ctx.channel, self.puzzle, description=":white_check_mark: I've updated:" if url else None
            )
        else:
            await ctx.send(":x: This does not appear to be a puzzle channel")


    @commands.command(aliases=["sheet", "drive"])
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def doc(self, ctx, *, url: Optional[str]):
        """*(admin) Show or update link to google spreadsheet/doc for puzzle*"""
        file_id = None
        if url:
            file_id = urls.extract_id_from_url(url)
            if not file_id:
                ctx.send(f":exclamation: Invalid Google Drive URL, unable to extract file ID: {url}")

        puzzle_data = await self.update_puzzle_attr_by_command(ctx, "google_sheet_id", file_id, reply=False)
        if puzzle_data:
            await self.send_state(
                ctx.channel, puzzle_data, description=":white_check_mark: I've updated:" if url else None
            )

    @commands.command(aliases=["notes"])
    async def note(self, ctx, *, note: Optional[str]):
        """*Show or add a note about the puzzle*"""
        if self.puzzle is None:
            await self.send_not_puzzle_channel(ctx)
            return

        message = "Showing notes left by users!"
        if note:
            self.puzzle.notes.append(f"{note} - {ctx.message.jump_url}")
            PuzzleJsonDb.commit(self.puzzle)
            message = (
                f"Added a new note! Use `!erase_note {len(self.puzzle.notes)}` to remove the note if needed. "
                f"Check `!notes` for the current list of notes."
            )

        if self.puzzle.notes:
            embed = discord.Embed(description=f"{message}")
            i = len(self.puzzle.notes)
            for x in range(i):
                embed.add_field(
                    name=f"Note {x+1}",
                    value=self.puzzle.notes[x],
                    inline=False
                )
#             embed.add_field(
#                 name="Notes",
#                 value="\n".join([f"{i+1}: {puzzle_data.notes[i]}" for i in range(len(puzzle_data.notes))])
#             )
        else:
            embed = discord.Embed(description="No notes left yet, use `!note my note here` to leave a note")
        await ctx.send(embed=embed)

    @commands.command()
    async def erase_note(self, ctx, note_index: int):
        """*Remove a note by index*"""

        if self.channel_type != "Puzzle":
            await self.send_not_puzzle_channel(ctx)
            return

        if 1 <= note_index <= len(self.puzzle.notes):
            note = self.puzzle.notes[note_index-1]
            del self.puzzle.notes[note_index - 1]
            description = f"Erased note {note_index}: `{note}`"
        else:
            description = f"Unable to find note {note_index}"

        embed = discord.Embed(description=description)
        i = len(self.puzzle.notes)
        for x in range(i):
            embed.add_field(
                name=f"Note {x+1}",
                value=self.puzzle.notes[x],
                inline=False
            )
        await ctx.send(embed=embed)

    @commands.command()
    async def status(self, ctx, *, status: Optional[str]):
        """*Show or update puzzle status, e.g. "extracting"*"""
        puzzle_data = await self.update_puzzle_attr_by_command(ctx, "status", status, reply=False)
        if puzzle_data:
            await self.send_state(
                ctx.channel, puzzle_data, description=":white_check_mark: I've updated:" if status else None
            )

    @commands.command()
    async def type(self, ctx, *, puzzle_type: Optional[str]):
        """*Show or update puzzle type, e.g. "crossword"*"""
        puzzle_data = await self.update_puzzle_attr_by_command(ctx, "puzzle_type", puzzle_type, reply=False)
        if puzzle_data:
            await self.send_state(
                ctx.channel, puzzle_data, description=":white_check_mark: I've updated:" if puzzle_type else None
            )

    @commands.command()
    async def priority(self, ctx, *, priority: Optional[str]):
        """*Show or update puzzle priority, one of "low", "medium", "high"*"""
        if priority is not None and priority not in self.PRIORITIES:
            await ctx.send(f":exclamation: Priority should be one of {self.PRIORITIES}, got \"{priority}\"")
            return

        puzzle_data = await self.update_puzzle_attr_by_command(ctx, "priority", priority, reply=False)
        if puzzle_data:
            await self.send_state(
                ctx.channel, puzzle_data, description=":white_check_mark: I've updated:" if priority else None
            )

    # Currently not very useful, resources also posted in Quick Links worksheet
    # @commands.command(aliases=["res"])
    # async def resources(self, ctx):
    #     puzzle_data = self.get_puzzle_data_from_channel(ctx.channel)
    #     if not puzzle_data:
    #         await self.send_not_puzzle_channel(ctx)
    #         return
    #
    #     embed = discord.Embed(description=f"""Resources: """)
    #     await ctx.send(embed=embed)

    @commands.command(aliases=["s"])
    async def solve(self, ctx, *, arg):
        """*Mark puzzle as fully solved and update the sheet with the solution: !s SOLUTION*"""

        if not self.puzzle:
            await self.send_not_puzzle_channel(ctx)
            return

        solution = arg.strip().upper()
        self.puzzle.status = "solved"
        self.puzzle.solution = solution
        self.puzzle.solve_time = datetime.datetime.now(tz=pytz.UTC)
        PuzzleJsonDb.commit(self.puzzle)

        emoji = self.guild_data.discord_bot_emoji
        embed = discord.Embed(
            description=f"{emoji} :partying_face: Great work! Marked the solution as `{solution}`"
        )
        embed.add_field(
            name="Follow-up",
            value="If the solution was mistakenly entered, please message `!unsolve`. "
            "Otherwise, in around 5 minutes, I will automatically move this "
            "puzzle channel to the bottom and archive the Google Spreadsheet",
        )
        await ctx.send(embed=embed)

    @commands.command()
    async def unsolve(self, ctx):
        """*Mark an accidentally solved puzzle as not solved*"""

        if not self.puzzle:
            await self.send_not_puzzle_channel(ctx)
            return

        prev_solution = self.puzzle.solution
        self.puzzle.status = "unsolved"
        self.puzzle.solution = ""
        self.puzzle.solve_time = None
        PuzzleJsonDb.commit(self.puzzle)

        emoji = self.guild_data.discord_bot_emoji
        embed = discord.Embed(
            description=f"{emoji} Alright, I've unmarked {prev_solution} as the solution. "
            "You'll get'em next time!"
        )
        await ctx.send(embed=embed)

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def delete_round(self, ctx):
        """*(admin) Permanently delete a round*"""
        round_channel = self.get_round_channel(ctx.channel)

        if ctx.channel != round_channel:
            await ctx.send(f":x: This command must be done from the main round channel")
            return

        if self.SOLVE_CATEGORY is False:
            solved_divider = self.get_solved_channel(ctx.channel.category)
            if solved_divider:
                await solved_divider.delete(reason=self.DELETE_REASON)
        # Delete the puzzles in the channel
        for channel in ctx.channel.category.channels:
            if channel != round_channel:
                await self.delete_puzzle_data(ctx, channel)
        #create round object for Sheets Cog
        category = ctx.channel.category
        guild = ctx.guild
        settings = GuildSettingsDb.get(guild.id)
        hunt_id = settings.category_mapping[category.id]
        hunt_settings = settings.hunt_settings[hunt_id]
        round = PuzzleData(category.name)
        round.round_name = category.name
        round.round_id = category.id
        round.google_sheet_id = hunt_settings.drive_sheet_id
        round.hunt_url = hunt_settings.hunt_url
        gsheet_cog = self.bot.get_cog("GoogleSheets")
        if gsheet_cog is not None:
            await gsheet_cog.archive_round_spreadsheet(round)
        hunt_general_channel = self.get_hunt_channel(ctx)
        category_name = category.name
        del settings.category_mapping[category.id]
        await round_channel.delete(reason=self.DELETE_REASON)
        await category.delete(reason=self.DELETE_REASON)
        await hunt_general_channel.send(f":white_check_mark: Round {category_name} successfully deleted.  "
                                        f"All puzzle sheets have been marked as DELETED and hidden.")

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def archive_round_old(self, ctx):
        """*(admin) Permanently archive a round*"""
        round_channel = self.get_round_channel(ctx.channel)
        if ctx.channel != round_channel:
            await ctx.send(f":x: This command must be done from the main round channel")
            return
        category = ctx.channel.category
        guild = ctx.guild
        settings = GuildSettingsDb.get(guild.id)
        hunt_id = settings.category_mapping[category.id]
        hunt_category = discord.utils.get(guild.categories, id=hunt_id)
        last_position = 0
        for hunt_channel in hunt_category.channels:
            if hunt_channel.position > last_position:
                last_position = hunt_channel.position
        hunt_settings = settings.hunt_settings[hunt_id]
        # Delete the channels without many messages and move the others to the end of the main category

        for channel in ctx.channel.category.channels:
            if channel != round_channel:
                num_messages = len(await channel.history(limit=10).flatten())
                if num_messages > 7:
                    # print(hunt_category.name)
                    # print(category.name + "-" + channel.name)
                    last_position += 1
                    await channel.edit(category=hunt_category, name=category.name + "-" + channel.name, position=last_position)
                else:
                    # Try to delete as a real puzzle channel, if not one then delete anyway
                    if not await self.delete_puzzle_data(ctx, channel, False):
                        await channel.delete(reason=self.DELETE_REASON)
        hunt_general_channel = self.get_hunt_channel(ctx)
        category_name = category.name
        del settings.category_mapping[category.id]
        last_position += 1
        await round_channel.edit(category=hunt_category, name=category.name + "-" + round_channel.name,
                                 position=last_position)
        await category.delete(reason=self.DELETE_REASON)
        await hunt_general_channel.send(f":white_check_mark: Round {category_name} successfully archived.")

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def archive_round(self, ctx):
        round_channel = self.get_round_channel(ctx.channel)
        hunt_general_channel = self.get_hunt_channel(ctx)
        category = ctx.channel.category
        if ctx.channel != round_channel:
            await ctx.send(f":x: This command must be done from the main round channel")
            return
        await self.archive_category(ctx, hunt_general_channel, delete_channels=[self.SOLVE_DIVIDER])
        settings = GuildSettingsDb.get(ctx.guild.id)
        del settings.category_mapping[category.id]
        await ctx.channel.category.delete(reason=self.DELETE_REASON)
        await hunt_general_channel.send(f":white_check_mark: Round {category.name} successfully archived.")

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def archive_category(self, ctx, archive_to = None, ignore_channels = [], delete_channels = [], **kwargs):
        # If archive_to not set then archive to the channel the command has been sent from
        if archive_to is None:
            archive_to = ctx.channel
            ignore_channels.append(ctx.channel.name)
        channels = ctx.channel.category.channels
        for channel in channels:
            if channel.name in ignore_channels:
                continue
            if channel.name not in delete_channels:
                await self.archive_channel(ctx, channel, archive_to)
            else:
                await channel.delete(reason=self.DELETE_REASON)
    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def archive_channel(self, ctx, channel = None, archive_to = None):
        if channel is None:
            channel = ctx.channel
        if archive_to is None:
            archive_to = self.bot.get_channel(self.hunt.channel_id)
        thread_message = await archive_to.send(content=f'Archive of channel {channel.name}', silent=True)
        puzzle = PuzzleJsonDb.get_by_attr(channel_id=channel.id)
        if puzzle:
            thread_name = f"{puzzle.name} ({self.hunt_round.name})"
        else:
            thread_name = channel.name
        thread = await archive_to.create_thread(name=thread_name, message=thread_message)
        webhook = await archive_to.webhooks()
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
        await channel.delete(reason=self.DELETE_REASON)

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
        round_channel = self.get_round_channel(ctx.channel)
        deleted_channel = ctx.channel
        try:
            await self.delete_puzzle_data(ctx, ctx.channel)
            await round_channel.send(f":white_check_mark: Channel "
                                     f"{deleted_channel.name}"
                                     f" successfully deleted and sheet cleaned up.  "
                                     f"The puzzle sheet has been hidden and prefixed with DELETED.")
        except:
            await ctx.send(f":x: Channel deletion failed")

    async def delete_puzzle_data(self, ctx, channel, delete_sheet=True):
        puzzle_data = self.get_puzzle_data_from_channel(channel)
        if not puzzle_data:
            await self.send_not_puzzle_channel(ctx)
            return False

        if puzzle_data.solution and self.SOLVE_CATEGORY is True:
            raise ValueError("Unable to delete a solved puzzle channel, please contact discord admins if needed")

        category = channel.category
        hunt_round = RoundJsonDb.get_by_attr(category=category.id)
        if delete_sheet is True:
            gsheet_cog = self.bot.get_cog("GoogleSheets")
            if gsheet_cog is not None:
                await gsheet_cog.delete_puzzle_spreadsheet(puzzle_data, hunt_round)
            try:
                PuzzleJsonDb.delete(puzzle_data)
            except:
                await ctx.send(f":x: Puzzle database deletion failed")

        voice_channel = discord.utils.get(
            ctx.guild.channels, category=category, type=discord.ChannelType.voice, name=channel.name
        )
        if voice_channel:
            await voice_channel.delete(reason=self.DELETE_REASON)
        # delete text channel last so that errors can be reported
        await channel.delete(reason=self.DELETE_REASON)
        return True

    # async def confirm_delete(self, ctx):
    #     ref: https://github.com/stroupbslayen/discord-pretty-help/blob/master/pretty_help/pretty_help.py
    #     embed = discord.Embed(description="Are you sure you wish to delete this channel? All of this channel's contents will be permanently deleted.")
    #     message: discord.Message = await ctx.send(embed=embed)
    #     message.add_reaction()
    #     payload: discord.RawReactionActionEvent = await bot.wait_for(
    #         "raw_reaction_add", timeout=self.active_time, check=check
    #     )

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    async def debug_puzzle_channel(self, ctx):
        """*(admin) See puzzle metadata*"""

        if self.channel_type != "Puzzle":
            await self.send_not_puzzle_channel(ctx)
            return

        await ctx.channel.send(f"```json\n{self.puzzle.to_json()}```")

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
            hunt_round = RoundJsonDb.get_by_attr(id=puzz.round_id)
            hunt = HuntJsonDb.get_by_attr(id=hunt_round.hunt_id)
            if not hunt.id in puzzles_by_hunt:
                puzzles_by_hunt[hunt.name] = []
            puzzles_by_hunt[hunt.name].append(puzz)

        for hunt_name, puzzles in puzzles_by_hunt.items():
            if self.SOLVE_CATEGORY:
                solved_category_name = self.get_solved_puzzle_category(hunt_name)
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
                if gsheet_cog:
                    """TODO This is inefficient, but runs completely away from the main code so should be fine"""
                    hunt_round = RoundJsonDb.get_by_attr(id=puzzle.round_id)
                    hunt = HuntJsonDb.get_by_attr(id=hunt_round.hunt_id)
                    gsheet_cog.set_spreadsheet_id(hunt.google_sheet_id)
                    await gsheet_cog.archive_puzzle_spreadsheet(puzzle)

                puzzle.archive_time = datetime.datetime.now(tz=pytz.UTC)
                PuzzleJsonDb.commit(puzzle)
        return puzzles_to_archive



    # @commands.command()
    # @commands.has_any_role('Moderator', 'mod', 'admin')
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

    @tasks.loop(seconds=30.0)
    async def archived_solved_puzzles_loop(self):
        """Ref: https://discordpy.readthedocs.io/en/latest/ext/tasks/"""
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
        guild_id = ctx.guild.id
        settings = GuildSettingsDb.get(guild_id)
        hunt_id = ctx.channel.category.id
        if hunt_id in settings.hunt_settings:
            settings = settings.hunt_settings[hunt_id]
        elif ctx.channel.category.id in settings.category_mapping:
            hunt_id = settings.category_mapping[ctx.channel.category.id]
            settings = settings.hunt_settings[hunt_id]
        hunt_category = self.bot.get_channel(hunt_id)
        for channel in hunt_category.channels:
            if channel.name.find(self.GENERAL_CHANNEL_NAME) == 0:
                return channel

    def get_round_channel(self, channel):
        for c in channel.category.channels:
            if c.name.find(self.GENERAL_CHANNEL_NAME) == 0:
                return c


    def get_general_channel(self, category):
        general_channel = None
        for channel in category.channels:
            if channel.name == self.GENERAL_CHANNEL_NAME + "-" + category.name:
                general_channel = channel
        return general_channel

    def get_solved_channel(self, category):
        solved_channel = None
        for channel in category.channels:
            if channel.name == self.SOLVE_DIVIDER:
                solved_channel = channel
        return solved_channel

    @staticmethod
    def get_position_last_channel(category):
        last_position = 0
        for hunt_channel in category.channels:
            if hunt_channel.position > last_position:
                last_position = hunt_channel.position
        return last_position



async def setup(bot):
    await bot.add_cog(Puzzles(bot))
