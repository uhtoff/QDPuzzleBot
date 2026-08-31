"""Deleting a CHANNEL must not purge its captured messages; deleting a MESSAGE must.

The bridge captures Discord messages into a `messages` table that deliberately
outlives the channel — that is the archive. Purging is reserved for an explicit
per-message delete, which is a privacy obligation. Two events, opposite
outcomes, and the pair is the point: a test that only checks one cannot tell
"channel deletion is inert" from "nothing ever purges".

This file used to assert `"on_guild_channel_delete" not in inspect.getsource(bridge)`.
That was watching the wrong module. The bridge genuinely has no such listener,
but there IS one in the codebase — `bot/cogs/puzzles.py` — and adding a message
purge THERE would destroy the archive with the old test still green. A negative
source assertion also passes if the listener is registered via
`bot.add_listener(...)` rather than a decorator, or is defined in any other cog.
"""
import ast
import pathlib
from unittest.mock import MagicMock, patch

import pytest

COGS_DIR = pathlib.Path(__file__).resolve().parent.parent / "bot" / "cogs"

# Cog modules allowed to handle channel deletion. Adding one is a deliberate act:
# this list going stale is the point, because whoever adds a handler has to come
# here and confirm it does not touch the message archive.
EXPECTED_CHANNEL_DELETE_HANDLERS = {"puzzles"}


def _make_bridge_cog():
    from bot.cogs.bridge import MessageBridge
    bot = MagicMock(); bot.application_id = 999
    cog = MessageBridge(bot); cog._puzzle_channels = {100}
    return cog


@pytest.mark.asyncio
async def test_a_channel_deletion_purges_nothing():
    """Drives the REAL on_guild_channel_delete handler, wherever it lives, rather
    than asserting a string is absent from one module."""
    from bot.cogs.puzzles import Puzzles

    channel = MagicMock()
    channel.id = 900
    channel.category = MagicMock(id=100)
    store_db, bridge_db = MagicMock(), MagicMock()

    with patch("bot.store.MessageDb", store_db), \
         patch("bot.cogs.bridge.MessageDb", bridge_db), \
         patch("bot.cogs.puzzles.category_layout.maybe_consolidate_category"):
        await Puzzles.on_guild_channel_delete(MagicMock(), channel)

    store_db.delete_message.assert_not_called()
    bridge_db.delete_message.assert_not_called()


@pytest.mark.asyncio
async def test_deleting_one_message_purges_exactly_that_message():
    """The other half. Without it, a bridge that had stopped purging entirely
    would pass the test above -- and quietly break the privacy obligation."""
    cog = _make_bridge_cog()

    with patch("bot.cogs.bridge.MessageDb") as db:
        db.delete_message.return_value = []
        await cog._handle({"op": "delete", "id": 4242, "channel_id": 100, "guild_id": 42})

    db.delete_message.assert_called_once_with(4242)


@pytest.mark.asyncio
async def test_the_delete_listener_enqueues_the_message_id_ungated():
    """Privacy does not depend on the channel still being one of ours: the id is
    enqueued regardless, and a foreign id is a harmless no-op downstream."""
    cog = _make_bridge_cog()
    cog._worker = MagicMock()          # not None: the listener is inert while disabled
    cog._enqueue = MagicMock()

    payload = MagicMock(message_id=4242, channel_id=55555, guild_id=42)
    await cog.on_raw_message_delete(payload)

    rec = cog._enqueue.call_args.args[0]
    assert rec["op"] == "delete" and rec["id"] == 4242


def test_only_the_expected_cogs_handle_channel_deletion():
    """The generalisation of the old assertion, and the reason it is worth
    keeping in some form: the risk is a purge appearing in a module nobody
    thought to look at. A census over every cog catches that; grepping one
    module's source does not.
    """
    found = set()
    for path in sorted(COGS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "on_guild_channel_delete"):
                found.add(path.stem)

    assert found == EXPECTED_CHANNEL_DELETE_HANDLERS, (
        f"channel-delete handlers changed: {found}. If you added one, confirm it "
        f"does not purge the message archive, then update "
        f"EXPECTED_CHANNEL_DELETE_HANDLERS."
    )
