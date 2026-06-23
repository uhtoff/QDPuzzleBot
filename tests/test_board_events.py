"""Phase 1 (board push): event builder + best-effort notifier over events.publish."""
from unittest.mock import patch

import pytest


def test_board_event_shape_and_int_guild():
    from bot.web import actions  # _board_event added in Phase 3
    ev = actions._board_event("HUNTX", "123456789")
    assert ev["type"] == "board"
    assert ev["uid"] == "HUNTX"
    assert ev["guild_id"] == 123456789
    assert isinstance(ev["guild_id"], int)


def test_board_event_none_guild_is_none_not_crash():
    from bot.web import actions
    ev = actions._board_event("HUNTX", None)
    assert ev["guild_id"] is None
    assert ev["uid"] == "HUNTX"


def test_notify_board_publishes_once():
    from bot.web import actions  # _notify_board added in Phase 3
    with patch("bot.web.events.publish") as pub:   # publish is SYNC — plain Mock
        actions._notify_board("HUNTX", "123456789")
    pub.assert_called_once()
    sent = pub.call_args.args[0]
    assert sent["type"] == "board" and sent["uid"] == "HUNTX" and sent["guild_id"] == 123456789


def test_notify_board_never_raises_when_publish_fails():
    from bot.web import actions
    with patch("bot.web.events.publish", side_effect=RuntimeError("boom")):
        # best-effort: a publish failure must NOT propagate
        actions._notify_board("HUNTX", "123456789")
