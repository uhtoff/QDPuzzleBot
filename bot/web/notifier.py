"""Phase B: WebNotifier — registers board-push as a Notifier sink.

Imported by bot/web/__init__.py at app startup and added to the service's
CompositeNotifier so bang-command mutations reach the dashboard without
importing bot.web.* from bot.core.*.
"""
import logging

from bot.core.notifier import Notifier
from . import events

logger = logging.getLogger(__name__)


class WebNotifier(Notifier):
    """Emits board-invalidation events via events.publish; all other methods are no-ops."""

    async def board_changed(self, hunt=None) -> None:
        if hunt is None:
            return
        try:
            events.publish({"type": "board", "uid": hunt.uid, "guild_id": hunt.guild_id})
        except Exception:
            logger.exception("WebNotifier.board_changed: failed to publish board event")

    async def welcome(self, guild=None, hunt=None, hunt_round=None, puzzle=None, channel=None) -> None:
        return None

    async def refresh_info(self, guild=None, hunt=None, puzzle=None) -> None:
        return None

    async def puzzle_solved(self, guild=None, hunt=None, puzzle=None) -> None:
        return None
