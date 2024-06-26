import datetime
import logging
import random
from typing import Any, List, Optional

import discord
from discord.ext import commands, tasks
import pytz

from bot.utils import urls
from bot.store import MissingPuzzleError, PuzzleData, PuzzleJsonDb, GuildSettings, GuildSettingsDb, HuntSettings

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
        guild = ctx.guild
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
        """*Create new puzzle channels: !p round-name: puzzle-name*

        Can be posted in a round category where you just need the puzzle_name
        """
        guild = ctx.guild
        if ctx.channel.name != self.GENERAL_CHANNEL_NAME:
            category = ctx.channel.category
            if ":" in arg:
                round_name, puzzle_name = arg.split(":", 1)
                if self.clean_name(round_name) != category:
                    # Check current category matches given round name
                    raise ValueError(f"Unexpected round: {round_name}, expected: {category.name}")
            else:
                puzzle_name = arg
            return await self.create_puzzle_channel(ctx, category.name, puzzle_name)

        if not (await self.check_is_bot_channel(ctx)):
            return

        if ":" in arg:
            round_name, puzzle_name = arg.split(":", 1)
            return await self.create_puzzle_channel(ctx, round_name, puzzle_name)

        raise ValueError(f"Unable to parse puzzle name {arg}, try using `!p round-name: puzzle-name`")


    @commands.command(aliases=["r"])
    async def round(self, ctx, *, arg):
        """*Create new puzzle round: !r round-name*"""
        if ctx.channel.name != "general":
            return

        hunt_id = ctx.channel.category.id
        guild = ctx.guild
        settings = GuildSettingsDb.get(guild.id)
        hunt_name = ctx.channel.category.name
        category_name = self.clean_name(arg)
        category = discord.utils.get(guild.categories, name=category_name)
        if category:
            category_name = category.name + " — " + ctx.channel.category.name
            category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            hunt_settings = settings.hunt_settings[hunt_id]
            print(f"Creating a new channel category for round: {category_name}")
            role = None
            if hunt_settings.role_id:
                role = discord.utils.get(guild.roles, id=hunt_settings.role_id)
            overwrites = self.get_overwrites(guild, role)
            position = ctx.channel.category.position + 1
            category = await ctx.channel.category.clone(name=category_name)
            await category.edit(overwrites=overwrites, position=position)
        else:
            raise ValueError(f"Category {category_name} already present in this server")
        if not category.id in settings.category_mapping:
            settings.category_mapping[category.id] = hunt_id
            GuildSettingsDb.commit(settings)
        text_channel, created_text = await self.get_or_create_channel(
                guild=guild, category=category, channel_name=self.GENERAL_CHANNEL_NAME + "-" + category_name,
                overwrites=overwrites, position=1,
                channel_type="text", reason=self.ROUND_REASON
            )

        if self.SOLVE_CATEGORY is False:
            await self.get_or_create_channel(
                guild=guild, category=category, channel_name=self.SOLVE_DIVIDER,
                overwrites=overwrites,
                channel_type="text", reason=self.ROUND_REASON, position = 500
            )

        round = PuzzleData(arg)
        round.round_name = category_name
        round.round_id = category.id
        round.google_sheet_id = hunt_settings.drive_sheet_id
        round.hunt_url = hunt_settings.hunt_url
        gsheet_cog = self.bot.get_cog("GoogleSheets")

        if gsheet_cog is not None:
            await gsheet_cog.create_round_overview_spreadsheet(round)

        #await self.create_puzzle_channel(ctx, category.name, self.META_CHANNEL_NAME)
        await self.send_initial_round_channel_messages(hunt_settings, text_channel)

    @classmethod
    def get_guild_settings_from_ctx(cls, ctx, use_cached: bool = True) -> GuildSettings:
        guild_id = ctx.guild.id
        if use_cached:
            return GuildSettingsDb.get_cached(guild_id)
        else:
            return GuildSettingsDb.get(guild_id)

    @commands.command()
    @commands.has_any_role('Moderator', 'mod', 'admin')
    @commands.has_permissions(manage_channels=True)
    async def show_settings(self, ctx):
        """*(admin) Show guild-level settings*"""
        guild_id = ctx.guild.id
        settings = GuildSettingsDb.get(guild_id)
        hunt_id = ctx.channel.category.id
        if hunt_id in settings.hunt_settings:
            settings = settings.hunt_settings[hunt_id]
        await ctx.channel.send(f"```json\n{settings.to_json(indent=2)}```")

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

    @commands.command(aliases=["list"])
    async def list_puzzles(self, ctx):
        """*List puzzles in the current round*"""
        guild = ctx.guild
        settings = GuildSettingsDb.get(guild.id)
        if ctx.channel.name != self.GENERAL_CHANNEL_NAME:
            # all_puzzles = PuzzleJsonDb.get_all(ctx.guild.id, self.get_puzzle_data_from_channel(ctx.channel).hunt_id)
            all_puzzles = PuzzleJsonDb.get_all(ctx.guild.id, settings.category_mapping[ctx.channel.category.id])
        else:
            #all_puzzles = PuzzleJsonDb.get_all(ctx.guild.id, settings.category_mapping[ctx.channel.category.id])
            #all_puzzles = PuzzleJsonDb.get_all(ctx.guild.id, ctx.channel.category.id)
            await self.list_all_puzzles(ctx)
            return

        all_puzzles = PuzzleData.sort_by_round_start(all_puzzles)

        round_name = ctx.channel.category.name
        embed = discord.Embed()
        cur_round = None
        puzzle_count = 1
        round_part = 1
        message = ""
        # Create a message with a new embed field per round,
        # listing all puzzles in the embed field
        for puzzle in all_puzzles:
            if puzzle.round_name != round_name:
                continue
            if cur_round is None:
                cur_round = puzzle.round_name
            if puzzle_count > 20:
                # Too many puzzles, split the embed
                round_name_part = cur_round + " :part " + str(round_part)
                embed.add_field(name=round_name_part, value=message)
                message = ""
                round_part += 1
                puzzle_count = 1
            if puzzle.round_name != cur_round:

                # Reached next round, add new embed field
                if round_part > 1:
                    round_name_part = cur_round + " :part " + str(round_part)
                else:
                    round_name_part = cur_round
                embed.add_field(name=round_name_part, value=message)
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

    @commands.command()
    async def list_all_puzzles(self, ctx):
        """*List all puzzles and their statuses*"""
        guild = ctx.guild
        settings = GuildSettingsDb.get(guild.id)
        if ctx.channel.name != self.GENERAL_CHANNEL_NAME:
            #all_puzzles = PuzzleJsonDb.get_all(ctx.guild.id, self.get_puzzle_data_from_channel(ctx.channel).hunt_id)
            all_puzzles = PuzzleJsonDb.get_all(ctx.guild.id, settings.category_mapping[ctx.channel.category.id])
        else:
            #all_puzzles = PuzzleJsonDb.get_all(ctx.guild.id, settings.category_mapping[ctx.channel.category.id])
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

        category = await guild.create_category(category_name, overwrites=overwrites, position=max(len(guild.categories) - 2,0))
        text_channel, created_text = await self.get_or_create_channel(
            guild=guild, category=category, channel_name=self.GENERAL_CHANNEL_NAME, overwrites=overwrites, channel_type="text", reason=self.HUNT_REASON
        )
        settings = GuildSettingsDb.get(guild.id)

        if self.SOLVE_CATEGORY:
            solved_category = await guild.create_category(self.get_solved_puzzle_category(hunt_name), overwrites=overwrites, position=max(len(guild.categories) - 2,0))

        gsheet_cog = self.bot.get_cog("GoogleSheets")

        if gsheet_cog is not None:
            google_drive_id = await gsheet_cog.create_hunt_spreadsheet(hunt_name)


        hs = HuntSettings(
            hunt_name=hunt_name,
            hunt_id=category.id,
            hunt_url=hunt_url,
        )
        if role:
            hs.role_id=role.id
        if google_drive_id:
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
        await self.send_initial_hunt_channel_messages(hs, text_channel)

        return (category, text_channel, True)


    async def create_puzzle_channel(self, ctx, round_name: str, puzzle_name: str):
        """Create new text channel for puzzle, and optionally a voice channel

        Save puzzle metadata to data_dir, send initial messages to channel, and
        create corresponding Google Sheet if GoogleSheets cog is set up.
        """
        guild = ctx.guild
        category_name = self.clean_name(round_name)
        category = discord.utils.get(guild.categories, name=category_name)
        if category is None:
            raise ValueError(f"Round {category_name} not found")

        settings = GuildSettingsDb.get_cached(guild.id)
        if not category.id in settings.category_mapping:
            raise ValueError(f"Hunt not found for {category_mapping}")
        hunt_id = settings.category_mapping[category.id]
        hunt_settings = settings.hunt_settings[hunt_id]
        role = None
        if hunt_settings.role_id:
            role = discord.utils.get(guild.roles, id=hunt_settings.role_id)
        overwrites = self.get_overwrites(guild, role)

        channel_name = self.clean_name(puzzle_name)

        sheet_id = hunt_settings.drive_sheet_id

        # try:
        #     sheet_id = self.get_puzzle_data_from_channel(ctx.channel).google_sheet_id
        # except:
        #     sheet_id = ''

        if self.SOLVE_CATEGORY:
            text_channel, created_text = await self.get_or_create_channel(
                guild=guild, category=category, channel_name=channel_name, overwrites=overwrites,
                channel_type="text", reason=self.PUZZLE_REASON
            )
        else:
            general_channel = self.get_general_channel(category)
            await general_channel.edit(position=1)
            solved_channel = self.get_solved_channel(category)
            await solved_channel.edit(position=500)
            round_puzzles = PuzzleJsonDb.get_all(ctx.guild.id, settings.category_mapping[ctx.channel.category.id])
            round_puzzles = PuzzleData.sort_by_puzzle_start(round_puzzles)
            puzzle_position = 2
            for puzzle in round_puzzles:
                if puzzle.round_name != round_name:
                    continue
                puzzle_channel = discord.utils.get(
                    guild.channels, type=discord.ChannelType.text, id=puzzle.channel_id
                )
                if puzzle.solution:
                    await puzzle_channel.edit(position=puzzle_position + 1000)
                else:
                    await puzzle_channel.edit(position=puzzle_position)
                puzzle_position = puzzle_position + 1

            text_channel, created_text = await self.get_or_create_channel(
                guild=guild, category=category, channel_name=channel_name, overwrites=overwrites, channel_type="text",
                position=puzzle_position, reason=self.PUZZLE_REASON
            )

        if created_text:
            hunt_settings = settings.hunt_settings[hunt_id]

            puzzle_data = PuzzleData(
                name=puzzle_name,
                hunt_id=hunt_id,
                hunt_name=hunt_settings.hunt_name,
                round_name=category_name,
                round_id=category.id,
                guild_id=guild.id,
                channel_mention=text_channel.mention,
                channel_id=text_channel.id,
                start_time=datetime.datetime.now(tz=pytz.UTC),
                google_sheet_id=sheet_id,
                status='Unsolved',
                priority='Normal'
            )
            if hunt_settings.hunt_url:
                # NOTE: this is a heuristic and may need to be updated!
                # This is based on last year's URLs, where the URL format was
                # https://<site>/puzzle/puzzle_name
                prefix = hunt_settings.hunt_puzzle_prefix or 'puzzles'
                hunt_url_base = hunt_settings.hunt_url.rstrip("/")
                if channel_name == "meta":
                    # Use the round name in the URL
                    p_name = category_name.lower().replace("-", hunt_settings.hunt_url_sep)
                else:
                    p_name = channel_name.replace("-", hunt_settings.hunt_url_sep)
                puzzle_data.hunt_url = f"{hunt_url_base}/{prefix}/{p_name}"

            if puzzle_name != 'meta':
                gsheet_cog = self.bot.get_cog("GoogleSheets")
                # print("google sheets cog:", gsheet_cog)
                if gsheet_cog is not None:
                    # update google sheet ID
                    await gsheet_cog.create_puzzle_spreadsheet(puzzle_data)

            PuzzleJsonDb.commit(puzzle_data)
            await self.send_initial_puzzle_channel_messages(text_channel)


        else:
            puzzle_data = self.get_puzzle_data_from_channel(text_channel)

        created_voice = False
        if settings.discord_use_voice_channels:
            voice_channel, created_voice = await self.get_or_create_channel(
                guild=guild, category=category, channel_name=channel_name, overwrites=overwrites, channel_type="voice", reason=self.PUZZLE_REASON
            )
            if created_voice:
                puzzle_data.voice_channel_id = voice_channel.id
                PuzzleJsonDb.commit(puzzle_data)
        created = created_text or created_voice
        if created:
            if created_text and created_voice:
                created_desc = "text and voice"  # I'm sure there's a more elegant way to do this ..
            elif created_text:
                created_desc = "text"
            elif created_voice:
                created_desc = "voice"

            await ctx.send(
                f":white_check_mark: I've created new puzzle {created_desc} channels for {category.mention}: {text_channel.mention}"
            )
        else:
            await ctx.send(
                f"I've found an already existing puzzle channel for {category.mention}: {text_channel.mention}"
            )
        return (text_channel, created)

    async def send_initial_hunt_channel_messages(self, hunt: HuntSettings, channel: discord.TextChannel):
        embed = discord.Embed(
            description=f"""Welcome to the general channel for {channel.category.name}!"""
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
• Hunt Website: {hunt.hunt_url}
• Hunt Username: {hunt.username}
• Hunt Password: {hunt.password}
• Google Sheet: https://docs.google.com/spreadsheets/d/{hunt.drive_sheet_id}
""",
            inline=False,
        )
        embed.add_field(
                    name="Useful commands",
                    value=f"""The following are some useful commands:
        • `!r <round name>` : Create a new round
        • `!p <round name>:<puzzle name>` : Add a channel within a round for a puzzle - round name can be excluded if done within the round
        • `!list` : List all added puzzles
        • `!info` : Repeat this message
        """,
                    inline=False,
                )
        await channel.send(embed=embed)

    async def send_initial_round_channel_messages(self, hunt: HuntSettings, channel: discord.TextChannel):
        """Send intro message on a round channel"""
        embed = discord.Embed(
            description=f"""Welcome to the round channel for {channel.category.name}!"""
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

        round = PuzzleData(channel.category.name)
        round.round_name = channel.category.name
        round.round_id = channel.category.id
        round.google_sheet_id = hunt.drive_sheet_id

        gsheet_cog = self.bot.get_cog("GoogleSheets")

        if gsheet_cog is not None:
            sheet_page_id=gsheet_cog.retrieve_overview_page_id(round)

        try:
            embed.add_field(name="Hunt URL", value=hunt.hunt_url or "?", inline=False,)
            spreadsheet_url = urls.spreadsheet_url(hunt.drive_sheet_id, sheet_page_id) or "?"
            embed.add_field(name="Google Drive Overview Page", value=spreadsheet_url, inline=False,)
        except:
            pass
        await channel.send(embed=embed)


    async def send_initial_puzzle_channel_messages(self, channel: discord.TextChannel):
        """Send intro message on a puzzle channel"""
        embed = discord.Embed(
            description=f"""Welcome to the puzzle channel for {channel.name} in {channel.category.name}!"""
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
            puzzle_data = self.get_puzzle_data_from_channel(channel)
            embed.add_field(name="Hunt URL", value=puzzle_data.hunt_url or "?", inline=False,)
            spreadsheet_url = urls.spreadsheet_url(puzzle_data.google_sheet_id, puzzle_data.google_page_id) if puzzle_data.google_sheet_id else "?"
            embed.add_field(name="Google Drive", value=spreadsheet_url,inline=False,)
            embed.add_field(name="Status", value=puzzle_data.status or "?", inline=False,)
            embed.add_field(name="Type", value=puzzle_data.puzzle_type or "?", inline=False,)
            embed.add_field(name="Priority", value=puzzle_data.priority or "?", inline=False,)
        except:
            pass
        await channel.send(embed=embed)

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
        guild_id = ctx.guild.id
        settings = GuildSettingsDb.get(guild_id)
        hunt_id = ctx.channel.category.id
        if hunt_id in settings.hunt_settings:
            settings = settings.hunt_settings[hunt_id]
            await self.send_initial_hunt_channel_messages(settings, ctx.channel)
        elif self.get_puzzle_data_from_channel(ctx.channel) is not None:
            await self.send_initial_puzzle_channel_messages(ctx.channel)
        elif ctx.channel.category.id in settings.category_mapping:
            hunt_id = settings.category_mapping[ctx.channel.category.id]
            settings = settings.hunt_settings[hunt_id]
            await self.send_initial_round_channel_messages(settings, ctx.channel)
        else:
            await ctx.send(":x: This does not appear to be a hunt channel")
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
        embed.add_field(name="Hunt URL", value=puzzle_data.hunt_url or "?")
        spreadsheet_url = urls.spreadsheet_url(puzzle_data.google_sheet_id, puzzle_data.google_page_id) if puzzle_data.google_sheet_id else "?"
        embed.add_field(name="Google Drive", value=spreadsheet_url)
        embed.add_field(name="Status", value=puzzle_data.status or "?")
        embed.add_field(name="Type", value=puzzle_data.puzzle_type or "?")
        embed.add_field(name="Priority", value=puzzle_data.priority or "?")
        await channel.send(embed=embed)

    @commands.command()
    async def link(self, ctx, *, url: Optional[str]):
        """*Show or update link to puzzle*"""
        puzzle_data = await self.update_puzzle_attr_by_command(ctx, "hunt_url", url, reply=False)

        gsheet_cog = self.bot.get_cog("GoogleSheets")
        # print("google sheets cog:", gsheet_cog)
        if gsheet_cog is not None:
            # update google sheet ID
            await gsheet_cog.update_url(puzzle_data)

        if puzzle_data:
            await self.send_state(
                ctx.channel, puzzle_data, description=":white_check_mark: I've updated:" if url else None
            )

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
        puzzle_data = self.get_puzzle_data_from_channel(ctx.channel)
        if not puzzle_data:
            await self.send_not_puzzle_channel(ctx)
            return

        message = "Showing notes left by users!"
        if note:
            puzzle_data.notes.append(f"{note} - {ctx.message.jump_url}")
            PuzzleJsonDb.commit(puzzle_data)
            message = (
                f"Added a new note! Use `!erase_note {len(puzzle_data.notes)}` to remove the note if needed. "
                f"Check `!notes` for the current list of notes."
            )

        if puzzle_data.notes:
            embed = discord.Embed(description=f"{message}")
            i = len(puzzle_data.notes)
            for x in range(i):
                embed.add_field(
                    name=f"Note {x+1}",
                    value=puzzle_data.notes[x],
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
        puzzle_data = self.get_puzzle_data_from_channel(ctx.channel)
        if not puzzle_data:
            await self.send_not_puzzle_channel(ctx)
            return

        if 1 <= note_index <= len(puzzle_data.notes):
            note = puzzle_data.notes[note_index-1]
            del puzzle_data.notes[note_index - 1]
            PuzzleJsonDb.commit(puzzle_data)
            description = f"Erased note {note_index}: `{note}`"
        else:
            description = f"Unable to find note {note_index}"

        embed = discord.Embed(description=description)
        i = len(puzzle_data.notes)
        for x in range(i):
            embed.add_field(
                name=f"Note {x+1}",
                value=puzzle_data.notes[x],
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
        puzzle_data = self.get_puzzle_data_from_channel(ctx.channel)
        if not puzzle_data:
            await self.send_not_puzzle_channel(ctx)
            return

        solution = arg.strip().upper()
        puzzle_data.status = "solved"
        puzzle_data.solution = solution
        puzzle_data.solve_time = datetime.datetime.now(tz=pytz.UTC)
        PuzzleJsonDb.commit(puzzle_data)

        emoji = self.get_guild_settings_from_ctx(ctx).discord_bot_emoji
        embed = discord.Embed(
            description=f"{emoji} :partying_face: Great work! Marked the solution as `{solution}`"
        )
        embed.add_field(
            name="Follow-up",
            value="If the solution was mistakenly entered, please message `!unsolve`. "
            "Otherwise, in around 5 minutes, I will automatically archive this "
            "puzzle channel to #solved-puzzles and archive the Google Spreadsheet",
        )
        await ctx.send(embed=embed)

    @commands.command()
    async def unsolve(self, ctx):
        """*Mark an accidentally solved puzzle as not solved*"""
        puzzle_data = self.get_puzzle_data_from_channel(ctx.channel)
        if not puzzle_data:
            await self.send_not_puzzle_channel(ctx)
            return

        prev_solution = puzzle_data.solution
        puzzle_data.status = "unsolved"
        puzzle_data.solution = ""
        puzzle_data.solve_time = None
        PuzzleJsonDb.commit(puzzle_data)

        emoji = self.get_guild_settings_from_ctx(ctx).discord_bot_emoji
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
    async def archive_round(self, ctx):
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
        category_name = category.name
        del settings.category_mapping[category.id]
        last_position += 1
        await round_channel.edit(category=hunt_category, name=category.name + "-" + round_channel.name,
                                 position=last_position)
        await category.delete(reason=self.DELETE_REASON)
        await hunt_general_channel.send(f":white_check_mark: Round {category_name} successfully archived.")

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
        print(solved_category_name)
        solved_category = discord.utils.get(guild.categories, name=solved_category_name)
        last_position = 0
        for hunt_channel in hunt_category.channels:
            if hunt_channel.position > last_position:
                last_position = hunt_channel.position

        # Delete the channels without many messages and move the others to the end of the main category

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

        if delete_sheet is True:
            gsheet_cog = self.bot.get_cog("GoogleSheets")
            if gsheet_cog is not None:
                await gsheet_cog.delete_puzzle_spreadsheet(puzzle_data)

            PuzzleJsonDb.delete(puzzle_data)

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
        puzzle_data = self.get_puzzle_data_from_channel(ctx.channel)
        if not puzzle_data:
            await self.send_not_puzzle_channel(ctx)
            return

        await ctx.channel.send(f"```json\n{puzzle_data.to_json()}```")

    async def archive_solved_puzzles(self, guild: discord.Guild) -> List[PuzzleData]:
        """Archive puzzles for which sufficient time has elapsed since solve time

        Move them to a solved-puzzles channel category, and rename spreadsheet
        to start with the text [SOLVED]
        """
        puzzles_to_archive = PuzzleJsonDb.get_solved_puzzles_to_archive(guild.id)

        gsheet_cog = self.bot.get_cog("GoogleSheets")

        puzzles_by_hunt = {}
        for puzz in puzzles_to_archive:
            if not puzz.hunt_id in puzzles_by_hunt:
                puzzles_by_hunt[puzz.hunt_name] = []
            puzzles_by_hunt[puzz.hunt_name].append(puzz)

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
                channel = discord.utils.get(
                    guild.channels, type=discord.ChannelType.text, id=puzzle.channel_id
                )
                if channel:
                    if self.SOLVE_CATEGORY:
                        await channel.edit(category=solved_category)
                    else:
                        last_position=self.get_position_last_channel(channel.category)
                        await channel.edit(position=last_position+1)

                if gsheet_cog:
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



def setup(bot):
    bot.add_cog(Puzzles(bot))
