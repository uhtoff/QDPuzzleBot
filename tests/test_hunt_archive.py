"""Unit tests for bot.core.hunt_archive (ctx-free archive primitives)."""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core import hunt_archive


def _hunt(role_id=0, name="Mystery", start=None):
    h = MagicMock()
    h.role_id = role_id
    h.name = name
    h.start_time = start
    return h


def test_resolve_team_name_uses_hunt_role():
    guild = MagicMock()
    role = MagicMock()
    role.name = "Team A"
    guild.get_role.return_value = role
    invoker = MagicMock()
    invoker.display_name = "russ"
    assert hunt_archive.resolve_team_name(guild, _hunt(role_id=5), invoker) == "Team A"


def test_resolve_team_name_falls_back_to_username():
    guild = MagicMock()
    guild.get_role.return_value = None          # role_id 0 OR role deleted
    invoker = MagicMock()
    invoker.display_name = "russ"
    assert hunt_archive.resolve_team_name(guild, _hunt(role_id=0), invoker) == "russ"


def test_archived_channel_name_with_start_time():
    start = datetime.datetime(2026, 6, 15, tzinfo=datetime.timezone.utc)
    assert hunt_archive.archived_channel_name(_hunt(name="Mystery", start=start), "Team A") == "2026-06 Mystery - Team A"


def test_archived_channel_name_without_start_time():
    assert hunt_archive.archived_channel_name(_hunt(name="Mystery", start=None), "Team A") == "Mystery - Team A"


def test_thread_name_for_puzzle_channel_is_capped():
    channel = MagicMock()
    channel.id = 111
    channel.category.name = "Round1"
    with patch("bot.core.hunt_archive.PuzzleJsonDb") as pdb:
        puzzle = MagicMock()
        puzzle.name = "Puzzle X"
        pdb.get_by_attr.return_value = puzzle
        name = hunt_archive.thread_name_for_channel(channel)
    assert name == "Puzzle X (Round1)"
    assert len(name) <= 100


def test_thread_name_for_non_puzzle_channel():
    channel = MagicMock()
    channel.id = 111
    channel.name = "chatter"
    channel.category.name = "Round1"
    with patch("bot.core.hunt_archive.PuzzleJsonDb") as pdb:
        pdb.get_by_attr.return_value = None
        assert hunt_archive.thread_name_for_channel(channel) == "chatter (Round1)"


async def test_ensure_webhook_reuses_existing():
    channel = MagicMock()
    existing = MagicMock()
    channel.webhooks = AsyncMock(return_value=[existing])
    channel.create_webhook = AsyncMock()
    assert await hunt_archive.ensure_webhook(channel) is existing
    channel.create_webhook.assert_not_awaited()


async def test_ensure_webhook_creates_when_missing():
    channel = MagicMock()
    created = MagicMock()
    channel.webhooks = AsyncMock(return_value=[])
    channel.create_webhook = AsyncMock(return_value=created)
    assert await hunt_archive.ensure_webhook(channel) is created
    channel.create_webhook.assert_awaited_once()


async def _aiter(items):
    for item in items:
        yield item


def _msg(id, content="", attachments=None, embeds=None, author_id=222):
    m = MagicMock()
    m.id = id
    m.created_at = id
    m.author = MagicMock()
    m.author.id = author_id
    m.author.display_name = f"user{author_id}"
    m.author.avatar = None
    m.content = content
    m.attachments = attachments or []
    m.embeds = embeds or []
    return m


def _source(messages):
    src = MagicMock()
    src.id = 111
    src.name = "chan"
    src.history = MagicMock(return_value=_aiter(messages))
    src.threads = []
    src.archived_threads = MagicMock(return_value=_aiter([]))
    src.delete = AsyncMock()
    return src


def _dest_and_webhook():
    dest = MagicMock()
    dest.send = AsyncMock(return_value=MagicMock())
    dest.create_thread = AsyncMock(return_value=MagicMock())
    webhook = MagicMock()
    webhook.send = AsyncMock(return_value=MagicMock())
    return dest, webhook


def _bot(app_id=999):
    bot = MagicMock()
    bot.application_id = app_id
    return bot


async def test_routine_skips_bot_and_command_messages():
    bot_msg = _msg(1, content="hi", author_id=999)         # bot's own id -> skip
    cmd_msg = _msg(2, content="!solve foo", author_id=222)  # bang command -> skip
    keep = _msg(3, content="a clue", author_id=222)
    src = _source([bot_msg, cmd_msg, keep])
    dest, webhook = _dest_and_webhook()
    result = await hunt_archive.archive_channel_to_thread(_bot(), dest, webhook, src, thread_name="t")
    assert webhook.send.await_count == 1
    assert result.messages_moved == 1


async def test_routine_sends_with_mentions_off_and_no_reactions():
    keep = _msg(1, content="ping <@42>", author_id=222)
    src = _source([keep])
    dest, webhook = _dest_and_webhook()
    moved = webhook.send.return_value
    moved.add_reaction = AsyncMock()
    await hunt_archive.archive_channel_to_thread(_bot(), dest, webhook, src, thread_name="t")
    kwargs = webhook.send.await_args.kwargs
    assert kwargs["allowed_mentions"].users is False  # AllowedMentions.none()
    assert kwargs["silent"] is True
    moved.add_reaction.assert_not_awaited()            # reactions are never copied


async def test_routine_times_out_stuck_attachment_but_keeps_message():
    attachment = MagicMock()
    attachment.filename = "big.bin"
    # asyncio.wait_for awaits this and the TimeoutError propagates — caught as a
    # skipped attachment, not a fatal error.
    attachment.to_file = AsyncMock(side_effect=TimeoutError("simulated stuck download"))
    msg = _msg(1, content="see file", attachments=[attachment], author_id=222)
    src = _source([msg])
    dest, webhook = _dest_and_webhook()
    result = await hunt_archive.archive_channel_to_thread(_bot(), dest, webhook, src, thread_name="t")
    # Message still sent (it has text); the attachment was dropped, not fatal.
    assert webhook.send.await_count == 1
    assert result.attachment_failures == 1
    assert webhook.send.await_args.kwargs["files"] == []


async def test_archive_and_delete_deletes_source_on_success():
    src = _source([_msg(1, content="x", author_id=222)])
    dest, webhook = _dest_and_webhook()
    await hunt_archive.archive_and_delete_channel(_bot(), dest, webhook, src, thread_name="t")
    src.delete.assert_awaited_once()
