"""Phase B: WebNotifier — emits board events via events.publish."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.notifier import Notifier


def _hunt(uid="HUNT1", guild_id=42):
    # guild_id here is the `guilds` row id (an FK), NOT the Discord snowflake —
    # see migrations/000_base_schema.sql. WebNotifier must resolve it to the
    # snowflake via data.get_guilds_map() before publishing.
    h = MagicMock()
    h.uid = uid
    h.guild_id = guild_id
    return h


def _guilds_map(row_id=42, snowflake=987654321):
    return {row_id: {"guild_snowflake": snowflake}}


@pytest.mark.asyncio
async def test_web_notifier_implements_notifier_interface():
    from bot.web.notifier import WebNotifier
    assert issubclass(WebNotifier, Notifier)


@pytest.mark.asyncio
async def test_web_notifier_board_changed_publishes_board_event():
    from bot.web.notifier import WebNotifier
    hunt = _hunt(uid="HUNT1", guild_id=42)
    with patch("bot.web.notifier.data.get_guilds_map",
               new=AsyncMock(return_value=_guilds_map(row_id=42, snowflake=987654321))), \
         patch("bot.web.events.publish") as pub:
        await WebNotifier().board_changed(hunt)
    pub.assert_called_once()
    ev = pub.call_args.args[0]
    assert ev["type"] == "board"
    assert ev["uid"] == "HUNT1"
    # The published guild_id must be the Discord snowflake, not the guilds row id.
    assert ev["guild_id"] == 987654321
    assert isinstance(ev["guild_id"], int)


@pytest.mark.asyncio
async def test_web_notifier_board_changed_none_guild_id_when_row_unknown():
    from bot.web.notifier import WebNotifier
    hunt = _hunt(uid="HUNT1", guild_id=999)  # not present in the guilds map
    with patch("bot.web.notifier.data.get_guilds_map",
               new=AsyncMock(return_value=_guilds_map(row_id=42, snowflake=987654321))), \
         patch("bot.web.events.publish") as pub:
        await WebNotifier().board_changed(hunt)
    ev = pub.call_args.args[0]
    assert ev["guild_id"] is None


@pytest.mark.asyncio
async def test_web_notifier_board_changed_never_raises_when_publish_fails():
    from bot.web.notifier import WebNotifier
    with patch("bot.web.notifier.data.get_guilds_map",
               new=AsyncMock(return_value=_guilds_map())), \
         patch("bot.web.events.publish", side_effect=RuntimeError("boom")):
        await WebNotifier().board_changed(_hunt())  # must not raise


@pytest.mark.asyncio
async def test_web_notifier_other_methods_are_noops():
    from bot.web.notifier import WebNotifier
    n = WebNotifier()
    assert await n.welcome(guild=None, hunt=None, tag_group=None, puzzle=None, channel=None) is None
    assert await n.refresh_info(guild=None, hunt=None, puzzle=None) is None
