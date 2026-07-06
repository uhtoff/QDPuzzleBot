from unittest.mock import MagicMock, patch


def _make_cog():
    from bot.cogs.bridge import MessageBridge
    bot = MagicMock(); bot.application_id = 999
    cog = MessageBridge(bot); cog._puzzle_channels = {100}
    return cog


async def test_insert_publishes_message_event():
    cog = _make_cog()
    rec = {"op": "insert", "id": 1, "channel_id": 100, "guild_id": 42, "author_id": 1,
           "author_name": "Ann", "author_avatar": None, "kind": 0, "content": "hi",
           "reference_id": None, "created_at": "2026-07-06 00:00:00", "attachments": []}
    with patch("bot.cogs.bridge.MessageDb"), patch("bot.cogs.bridge.events.publish") as pub:
        await cog._handle(rec)
    ev = pub.call_args.args[0]
    assert ev["type"] == "message" and ev["channel_id"] == "100" and ev["guild_id"] == 42
    assert ev["message"]["id"] == "1" and ev["message"]["content"] == "hi"


async def test_edit_publishes_edit_event():
    cog = _make_cog()
    rec = {"op": "edit", "id": 1, "channel_id": 100, "guild_id": 42,
           "content": "new", "edited_at": "2026-07-06 00:01:00"}
    with patch("bot.cogs.bridge.MessageDb"), patch("bot.cogs.bridge.events.publish") as pub:
        await cog._handle(rec)
    ev = pub.call_args.args[0]
    assert ev["type"] == "message_edit" and ev["channel_id"] == "100" and ev["guild_id"] == 42
    assert ev["message"]["id"] == "1" and ev["message"]["content"] == "new"


async def test_delete_publishes_delete_event():
    cog = _make_cog()
    with patch("bot.cogs.bridge.MessageDb") as db, patch("bot.cogs.bridge.events.publish") as pub, \
         patch("bot.cogs.bridge.media_root"):
        db.delete_message.return_value = []
        await cog._handle({"op": "delete", "id": 1, "channel_id": 100, "guild_id": 42})
    ev = pub.call_args.args[0]
    assert ev["type"] == "message_delete" and ev["message_id"] == "1"
