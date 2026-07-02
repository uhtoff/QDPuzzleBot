"""Phase B: WebNotifier — registers board-push as a Notifier sink.

Imported by bot/web/__init__.py at app startup and added to the service's
CompositeNotifier so bang-command mutations reach the dashboard without
importing bot.web.* from bot.core.*.
"""
import logging

from bot.core.notifier import Notifier
from . import data, events

logger = logging.getLogger(__name__)


class WebNotifier(Notifier):
    """Emits board-invalidation events via events.publish; all other methods are no-ops."""

    async def board_changed(self, hunt=None) -> None:
        if hunt is None:
            return
        try:
            # hunt.guild_id is the `guilds` row id (an FK), not the Discord
            # snowflake — the ws_activity membership filter (api.py) matches on
            # the snowflake, so publishing the row id makes every board push
            # silently fail to reach any connected dashboard.
            guilds_map = await data.get_guilds_map()
            guild_snowflake = guilds_map.get(hunt.guild_id, {}).get("guild_snowflake")
            events.publish({
                "type": "board", "uid": hunt.uid,
                "guild_id": int(guild_snowflake) if guild_snowflake else None,
            })
        except Exception:
            logger.exception("WebNotifier.board_changed: failed to publish board event")

    async def welcome(self, guild=None, hunt=None, tag_group=None, puzzle=None, channel=None) -> None:
        return None

    async def refresh_info(self, guild=None, hunt=None, puzzle=None) -> None:
        return None
