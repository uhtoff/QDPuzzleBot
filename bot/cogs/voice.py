"""Puzzle voice channels: `!voice`, admin toggles, live presence, solved sweep."""
import asyncio
import logging

from discord.ext import commands, tasks

from bot.cogs.puzzles import is_admin
from bot.core import voice, voice_presence
from bot.store import HuntJsonDb, PuzzleJsonDb, TagGroupJsonDb
from bot.web import events

logger = logging.getLogger(__name__)

SWEEP_SECONDS = 20

_VOICE_ERRORS = {
    "solved": ":x: This puzzle is solved — no new voice channel.",
    "archived": ":x: This hunt is archived.",
    "no_hunt_category": ":x: I can't find this hunt's category to copy permissions "
                        "from, so I won't make a voice channel.",
}


def _puzzle_for(channel):
    return PuzzleJsonDb.get_by_attr(channel_id=channel.id)


def _hunt_for(channel):
    """The hunt owning *channel*: a puzzle channel, the hunt's own channel, or any
    channel in one of its groups' categories. (The Puzzles cog's per-command
    environment is private to that cog, so this repeats its three lookups.)
    Solved/overflow group categories are not resolved (only a group's own category)."""
    puzzle = _puzzle_for(channel)
    if puzzle is not None:
        return HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
    hunt = HuntJsonDb.get_by_attr(channel_id=channel.id)
    if hunt is not None:
        return hunt
    category = getattr(channel, "category", None)
    if category is not None:
        group = TagGroupJsonDb.get_by_attr(category_id=category.id)
        if group is not None:
            return HuntJsonDb.get_by_attr(id=group.hunt_id)
    return None


class Voice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.sweep.start()

    async def cog_unload(self):
        self.sweep.cancel()

    @tasks.loop(seconds=SWEEP_SECONDS)
    async def sweep(self):
        try:
            await voice.sweep_solved(self.bot)
        except Exception:
            # Voice tidy-up must never break the bot, nor stop the loop.
            logger.exception("Voice sweep failed")

    @sweep.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            voice_presence.snapshot(self.bot.guilds)
        except Exception:
            logger.exception("Voice presence snapshot failed")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        try:
            for channel_id in voice_presence.on_state_change(
                    member, before.channel, after.channel):
                event = await asyncio.to_thread(voice_presence.event_for, channel_id)
                if event is not None:
                    events.publish(event)
        except Exception:
            logger.exception("Voice state update failed")

    @commands.command(aliases=["vc"])
    async def voice(self, ctx):
        """*Get (or create) this puzzle's voice channel.*

        Usage: `!voice`
        Run in a puzzle's channel. Alias: `!vc`."""
        puzzle = _puzzle_for(ctx.channel)
        hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id) if puzzle else None
        if puzzle is None or hunt is None:
            await ctx.send(":x: Run this in a puzzle's channel.")
            return
        try:
            channel, _created = await voice.create_puzzle_voice(ctx.guild, hunt, puzzle)
        except voice.VoiceError as e:
            await ctx.send(_VOICE_ERRORS.get(e.code, ":x: Couldn't make a voice channel."))
            return
        reply = f":loud_sound: Voice for **{puzzle.name}**: {channel.mention}"
        if not voice.can_move_members(ctx.guild, hunt):
            reply += f"\n:warning: {voice.MOVE_MEMBERS_WARNING}"
        await ctx.send(reply)

    @commands.command()
    @is_admin()
    async def voice_cleanup(self, ctx):
        """*Remove every voice channel this hunt made for its puzzles.*

        Usage: `!voice_cleanup`
        Run in any channel of the hunt. Admins only. Occupied channels get a
        15 second warning first."""
        hunt = _hunt_for(ctx.channel)
        if hunt is None:
            await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")
            return
        await ctx.send(":broom: Clearing puzzle voice channels…")
        names = await voice.cleanup_hunt(ctx.guild, hunt)
        if names:
            await ctx.send(f":white_check_mark: Removed {len(names)} voice channel(s): "
                           f"{', '.join(names)}")
        else:
            await ctx.send("No puzzle voice channels to remove.")

    @commands.command()
    @is_admin()
    async def voice_per_puzzle(self, ctx, setting: str = ""):
        """*Give every new puzzle in this hunt a voice channel automatically.*

        Usage: `!voice_per_puzzle on` / `!voice_per_puzzle off` / `!voice_per_puzzle`
        Run in any channel of the hunt. Admins only. With no argument, shows the
        current setting."""
        hunt = _hunt_for(ctx.channel)
        if hunt is None:
            await ctx.send(":x: I can't tell which hunt this is — run this in a hunt channel.")
            return
        setting = setting.strip().lower()
        if setting not in ("on", "off", ""):
            await ctx.send(":x: Use `!voice_per_puzzle on` or `!voice_per_puzzle off`.")
            return
        if setting == "":
            state = "on" if hunt.voice_per_puzzle else "off"
            await ctx.send(f"Per-puzzle voice channels in **{hunt.name}** are **{state}**.")
            return
        on = setting == "on"
        await asyncio.to_thread(HuntJsonDb.set_voice_per_puzzle, hunt.id, on)
        if on:
            await ctx.send(f"Every new puzzle in **{hunt.name}** now gets a voice channel.")
        else:
            await ctx.send(f"New puzzles in **{hunt.name}** no longer get a voice channel "
                           "automatically (existing ones stay; `!voice_cleanup` removes them).")


async def setup(bot):
    await bot.add_cog(Voice(bot))
