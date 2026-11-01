"""The ChannelActivity drain worker also counts each human message into the
puzzle's activity slot, and one write failing never blocks the other."""
import asyncio
import datetime
from unittest.mock import MagicMock, patch

from bot.cogs.activity import ChannelActivity

AT = datetime.datetime(2026, 11, 1, 12, 1)


def _record(channel_id=1001):
    return {"channel_id": channel_id, "guild_id": 7, "at": AT,
            "author_id": 9, "author_name": "alice"}


async def _drain(records, last_db, slot_db):
    """Run the real _drain over ``records`` and stop once they are processed."""
    cog = ChannelActivity(MagicMock())
    with patch("bot.cogs.activity.ChannelActivityDb", last_db), \
            patch("bot.cogs.activity.PuzzleActivityDb", slot_db):
        worker = asyncio.create_task(cog._drain())
        for r in records:
            cog._queue.put_nowait(r)
        await asyncio.wait_for(cog._queue.join(), timeout=5)
        assert not worker.done()  # still alive for the next message
        worker.cancel()


def test_drain_records_last_message_and_counts_slot():
    last_db, slot_db = MagicMock(), MagicMock()
    asyncio.run(_drain([_record()], last_db, slot_db))
    last_db.record.assert_called_once_with(**_record())
    slot_db.add_chat.assert_called_once_with(1001, AT)


def test_slot_failure_does_not_block_last_message():
    last_db, slot_db = MagicMock(), MagicMock()
    slot_db.add_chat.side_effect = RuntimeError("db gone")
    asyncio.run(_drain([_record(1), _record(2)], last_db, slot_db))
    assert [c.kwargs["channel_id"] for c in last_db.record.call_args_list] == [1, 2]
    assert [c.args[0] for c in slot_db.add_chat.call_args_list] == [1, 2]


def test_last_message_failure_does_not_block_slot():
    last_db, slot_db = MagicMock(), MagicMock()
    last_db.record.side_effect = RuntimeError("db gone")
    asyncio.run(_drain([_record(1), _record(2)], last_db, slot_db))
    assert [c.args[0] for c in slot_db.add_chat.call_args_list] == [1, 2]
