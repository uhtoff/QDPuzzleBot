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
    async def refresh_group_info(self, guild, hunt, tag_group) -> None:
        """Refresh a bare-Group channel's pinned welcome after a type change."""

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

    async def refresh_group_info(self, guild=None, hunt=None, tag_group=None) -> None:
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
        """Post and pin the welcome message.

        ``tag_group`` and ``puzzle`` both None means the hunt's own channel
        (used by hunt creation, bang and web alike — see #81)."""
        if channel is None or guild is None:
            return
        cog = self._cog()
        if cog is None:
            return

        from bot.store import GuildSettingsDb
        # ctx is None on this path, so the welcome handlers can't fall back to
        # the environment for guild settings (needed by _overview_url). Supply
        # it explicitly.
        guild_data = GuildSettingsDb.get(guild.id)
        try:
            if tag_group is None and puzzle is None:
                handler = cog.send_initial_hunt_channel_messages
                kwargs = {"hunt": hunt, "guild_data": guild_data}
            else:
                handler = (
                    cog.send_initial_metapuzzle_channel_messages
                    if puzzle is not None and puzzle.is_target
                    else (cog.send_initial_puzzle_channel_messages
                         if puzzle is not None
                         else cog.send_initial_group_channel_messages)
                )
                kwargs = {"hunt": hunt, "tag_group": tag_group, "puzzle": puzzle, "guild_data": guild_data}
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
        """Refresh a puzzle's pinned info message after a metadata change.

        When ``puzzle`` is None, refreshes the hunt channel's own pinned info
        message instead (used for hunt-settings changes: !set_login, !set_start,
        !update_settings, and the web PATCH /hunts/<uid>/settings equivalent)."""
        if guild is None:
            return
        cog = self._cog()
        if cog is None:
            return

        if puzzle is None:
            if hunt is None or not hunt.channel_id:
                return
            channel = guild.get_channel_or_thread(hunt.channel_id)
            if channel is None:
                return
            try:
                from bot.store import GuildSettingsDb
                await cog.send_initial_hunt_channel_messages(
                    None, channel,
                    hunt=hunt,
                    update=True,
                    guild_data=GuildSettingsDb.get(guild.id),
                )
            except Exception:
                logger.exception("refresh_info: failed for hunt %s", hunt.id)
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

    async def refresh_group_info(self, guild, hunt, tag_group) -> None:
        """Refresh a bare-Group channel's pinned welcome after a type change."""
        if guild is None or tag_group is None:
            return
        cog = self._cog()
        if cog is None:
            return

        from bot.core import category_layout
        channel = category_layout.group_chat_channel(guild, tag_group)
        if channel is None:
            channel = guild.get_channel_or_thread(getattr(tag_group, "category_id", 0))
        if channel is None:
            return

        try:
            from bot.store import GuildSettingsDb
            await cog.send_initial_group_channel_messages(
                None, channel,
                hunt=hunt, tag_group=tag_group, update=True,
                guild_data=GuildSettingsDb.get(guild.id),
            )
        except Exception:
            logger.exception("refresh_group_info: failed for group %s", tag_group.id)


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

    async def refresh_group_info(self, guild, hunt, tag_group) -> None:
        for n in self._notifiers:
            await n.refresh_group_info(guild, hunt, tag_group)

    async def board_changed(self, hunt=None) -> None:
        for n in self._notifiers:
            await n.board_changed(hunt)


# ------------------------------------------------------------------ helpers
def build_solved_embed(emoji, puzzle, follow_up):
    """Shared "PUZZLE SOLVED!" embed formula (#79: this title/description was
    independently copied in the bang !solve command, web_post_solved_embed, and
    this module's cog-unavailable fallback — a wording/emoji change had to be
    made in three places). Only the Follow-up text is caller-specific (bang vs.
    dashboard copy is deliberately different)."""
    embed = __import__("discord").Embed(
        title="PUZZLE SOLVED!",
        description=f"{emoji} :partying_face: Great work! Marked the solution as `{puzzle.solution}`",
    )
    embed.add_field(name="Follow-up", value=follow_up)
    return embed
