"""Phase B: WebNotifier — emits board events via events.publish."""
from unittest.mock import MagicMock, patch

import pytest

from bot.core.notifier import Notifier


def _hunt(uid="HUNT1", guild_id=123456789):
    h = MagicMock()
    h.uid = uid
    h.guild_id = guild_id
    return h


@pytest.mark.asyncio
async def test_web_notifier_implements_notifier_interface():
    from bot.web.notifier import WebNotifier
    assert issubclass(WebNotifier, Notifier)


@pytest.mark.asyncio
async def test_web_notifier_board_changed_publishes_board_event():
    from bot.web.notifier import WebNotifier
    hunt = _hunt(uid="HUNT1", guild_id=987654321)
    with patch("bot.web.events.publish") as pub:
        await WebNotifier().board_changed(hunt)
    pub.assert_called_once()
    ev = pub.call_args.args[0]
    assert ev["type"] == "board"
    assert ev["uid"] == "HUNT1"
    assert ev["guild_id"] == 987654321
    assert isinstance(ev["guild_id"], int)


@pytest.mark.asyncio
async def test_web_notifier_board_changed_never_raises_when_publish_fails():
    from bot.web.notifier import WebNotifier
    with patch("bot.web.events.publish", side_effect=RuntimeError("boom")):
        await WebNotifier().board_changed(_hunt())  # must not raise


@pytest.mark.asyncio
async def test_web_notifier_other_methods_are_noops():
    from bot.web.notifier import WebNotifier
    n = WebNotifier()
    assert await n.welcome(guild=None, hunt=None, hunt_round=None, puzzle=None, channel=None) is None
    assert await n.refresh_info(guild=None, hunt=None, puzzle=None) is None
    assert await n.puzzle_solved(guild=None, hunt=None, puzzle=None) is None
