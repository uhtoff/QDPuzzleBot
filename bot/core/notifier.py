"""Injected Notifier — ctx-free Discord notifications (M3.5).

The service layer calls the notifier; the notifier owns all Discord send/post/
pin/presentation. Both bang-commands and the web dashboard should produce the
same notification regardless of caller. See Phase B contract below for how
board-push sinks register here later.

CompositeNotifier is the *seam* for Phase B: it fans each method out to a list
of child notifiers so later work can add e.g. `events.publish` as an extra sink
without reshaping the core interface.
"""
from __future__ import annotations

import abc
import logging
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from discord.ext.commands import Bot

logger = logging.getLogger(__name__)


class Notifier(abc.ABC):
    """Abstract notifier — ctx-free presentation of puzzle events."""

    @abc.abstractmethod
    async def welcome(
        self,
        guild,
        hunt,
        tag_group: Optional[object] = None,
        puzzle: Optional[object] = None,
        channel=None,
    ) -> None:
        """Post and pin the welcome/pinned-info message."""

    @abc.abstractmethod
    async def refresh_info(
        self,
        guild,
        hunt,
        puzzle: Optional[object] = None,
    ) -> None:
        """Refresh a puzzle's pinned info message after a metadata change."""

    @abc.abstractmethod
    async def puzzle_solved(
        self,
        guild,
        hunt,
        puzzle: Optional[object] = None,
    ) -> None:
        """Post the SOLVED embed after a solve."""

    @abc.abstractmethod
    async def board_changed(self, hunt) -> None:
        """Notify that the board state has changed for this hunt (Phase B)."""


# ------------------------------------------------------------------ null
class NullNotifier(Notifier):
    """No-op notifier — every method awaits cleanly and returns None."""

    async def welcome(self, guild=None, hunt=None, tag_group=None, puzzle=None, channel=None) -> None:
        return None

    async def refresh_info(self, guild=None, hunt=None, puzzle=None) -> None:
        return None

    async def puzzle_solved(self, guild=None, hunt=None, puzzle=None) -> None:
        return None

    async def board_changed(self, hunt=None) -> None:
        return None


# ------------------------------------------------------------------ discord
class DiscordNotifier(Notifier):
    """Sends Discord messages via the cog's ctx-free render helpers."""

    def __init__(self, bot: "Bot"):
        self.bot = bot

    def _cog(self):
        return self.bot.get_cog("Puzzles")

    async def board_changed(self, hunt=None) -> None:
        return None

    async def welcome(
        self,
        guild,
        hunt,
        tag_group: Optional[object] = None,
        puzzle: Optional[object] = None,
        channel=None,
    ) -> None:
        """Post and pin the welcome message."""
        if channel is None or guild is None:
            return
        cog = self._cog()
        if cog is None:
            return

        from bot.store import GuildSettingsDb
        kwargs = {
            "hunt": hunt,
            "tag_group": tag_group,
            "puzzle": puzzle,
            # ctx is None on this path, so the welcome handlers can't fall back to
            # the environment for guild settings (needed by _overview_url). Supply
            # it explicitly.
            "guild_data": GuildSettingsDb.get(guild.id),
        }
        try:
            handler = (
                cog.send_initial_group_channel_messages
                if puzzle is None
                else (cog.send_initial_metapuzzle_channel_messages
                     if puzzle.is_target
                     else cog.send_initial_puzzle_channel_messages)
            )
            message = await handler(None, channel, **kwargs)
            await message.pin()
        except Exception:
            logger.exception("welcome: failed to send/pin in channel %s", channel.id)

    async def refresh_info(
        self,
        guild,
        hunt,
        puzzle: Optional[object] = None,
    ) -> None:
        """Refresh a puzzle's pinned info message after a metadata change."""
        if guild is None or puzzle is None:
            return
        cog = self._cog()
        if cog is None:
            return

        channel = guild.get_channel_or_thread(puzzle.channel_id) if puzzle.channel_id else None
        if channel is None:
            return

        # For metas, find the group this puzzle is the meta of.
        tag_group = None
        if puzzle.is_target:
            from bot.store import TagGroupJsonDb
            for tag in puzzle.tags:
                candidate = TagGroupJsonDb.get_by_attr(id=tag)
                if candidate and candidate.target_puzzle_id == puzzle.id:
                    tag_group = candidate
                    break

        try:
            handler = (
                cog.send_initial_metapuzzle_channel_messages
                if puzzle.is_target
                else cog.send_initial_puzzle_channel_messages
            )
            from bot.store import GuildSettingsDb
            kwargs = {
                "hunt": hunt,
                "tag_group": tag_group,
                "puzzle": puzzle,
                "update": True,
                # ctx is None on this path, so supply guild settings explicitly
                # (needed by _overview_url) — matching the welcome() handler above.
                "guild_data": GuildSettingsDb.get(guild.id),
            }
            await handler(None, channel, **kwargs)
        except Exception:
            logger.exception("refresh_info: failed for puzzle %s", puzzle.id)

        # Forum-mode metas keep their guidelines/topic in step with the welcome.
        if tag_group is not None and getattr(hunt, "layout_mode", None) == "forum":
            from bot.core.forum_config import sync_forum_guidelines
            await sync_forum_guidelines(guild, hunt, tag_group, puzzle)

    async def puzzle_solved(
        self,
        guild,
        hunt,
        puzzle: Optional[object] = None,
    ) -> None:
        """Post the SOLVED embed after a solve."""
        if guild is None or puzzle is None:
            return
        cog = self._cog()
        if cog is None:
            # Fallback: post directly without cog helper.
            _post_solved_embed(self.bot, guild, puzzle)
            return

        try:
            await cog.web_post_solved_embed(guild, hunt, puzzle)
        except Exception:
            logger.exception("puzzle_solved: failed for puzzle %s", puzzle.id)


# ------------------------------------------------------------------ composite
class CompositeNotifier(Notifier):
    """Fans each method call out to a list of child notifiers in order.

    Phase B: add_sink() lets startup code register extra sinks (e.g. WebNotifier)
    after the composite is constructed, without reshaping the core interface.
    """

    def __init__(self, notifiers: List[Notifier]):
        self._notifiers = list(notifiers)

    def add_sink(self, notifier: Notifier) -> None:
        self._notifiers.append(notifier)

    async def welcome(
        self,
        guild,
        hunt,
        tag_group: Optional[object] = None,
        puzzle: Optional[object] = None,
        channel=None,
    ) -> None:
        for n in self._notifiers:
            await n.welcome(guild, hunt, tag_group, puzzle, channel)

    async def refresh_info(
        self,
        guild,
        hunt,
        puzzle: Optional[object] = None,
    ) -> None:
        for n in self._notifiers:
            await n.refresh_info(guild, hunt, puzzle)

    async def puzzle_solved(
        self,
        guild,
        hunt,
        puzzle: Optional[object] = None,
    ) -> None:
        for n in self._notifiers:
            await n.puzzle_solved(guild, hunt, puzzle)

    async def board_changed(self, hunt=None) -> None:
        for n in self._notifiers:
            await n.board_changed(hunt)


# ------------------------------------------------------------------ helpers
def _post_solved_embed(bot, guild, puzzle):
    """Best-effort direct post of solved embed (no cog needed)."""
    channel = guild.get_channel_or_thread(puzzle.channel_id) if puzzle.channel_id else None
    if channel is None:
        return
    from bot.store import GuildSettingsDb
    guild_data = GuildSettingsDb.get(guild.id)
    emoji = guild_data.discord_bot_emoji if guild_data else ""

    embed = __import__("discord").Embed(
        title="PUZZLE SOLVED!",
        description=f"{emoji} :partying_face: Great work! Marked the solution as `{puzzle.solution}`",
    )
    embed.add_field(name="Follow-up", value="Solved from the dashboard. Tidying up the sheets…")
    __import__("asyncio").get_event_loop().create_task(channel.send(embed=embed))
