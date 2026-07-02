"""Regression tests for archive_channel (#1): attachment/embed-only messages
must be archived, not silently dropped before the source channel is deleted."""

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.cogs.puzzles import Puzzles
from tests.conftest import make_cog, make_ctx


async def _async_iter(items):
    for item in items:
        yield item


def _make_message(id, content="", attachments=None, embeds=None, author_id=222):
    message = MagicMock()
    message.id = id
    message.created_at = id
    message.author = MagicMock()
    message.author.id = author_id
    message.author.display_name = f"user{author_id}"
    message.author.avatar = None
    message.content = content
    message.attachments = attachments or []
    message.embeds = embeds or []
    message.reactions = []
    return message


def _make_channel(messages):
    channel = MagicMock()
    channel.id = 111
    channel.name = "test-channel"
    channel.category = MagicMock()
    channel.category.name = "Round1"
    channel.threads = []
    channel.history = MagicMock(return_value=_async_iter(messages))
    channel.archived_threads = MagicMock(return_value=_async_iter([]))
    channel.delete = AsyncMock()
    return channel


def _make_archive_to(moved_message):
    archive_to = MagicMock()
    webhook = MagicMock()
    webhook.send = AsyncMock(return_value=moved_message)
    archive_to.webhooks = AsyncMock(return_value=[webhook])
    archive_to.send = AsyncMock(return_value=MagicMock())
    archive_to.create_thread = AsyncMock(return_value=MagicMock())
    archive_to.create_webhook = AsyncMock()
    return archive_to, webhook


@pytest.mark.asyncio
async def test_archives_attachment_only_and_embed_only_messages():
    attachment = MagicMock()
    attachment.filename = "photo.png"
    attachment.to_file = AsyncMock(return_value=MagicMock())
    embed = MagicMock()

    attachment_only = _make_message(1, content="", attachments=[attachment])
    embed_only = _make_message(2, content="", embeds=[embed])
    truly_empty = _make_message(3, content="")

    channel = _make_channel([attachment_only, embed_only, truly_empty])
    moved_message = MagicMock()
    moved_message.reactions = []
    archive_to, webhook = _make_archive_to(moved_message)

    cog = make_cog()
    ctx = make_ctx()
    ctx.bot.application_id = 999

    with patch("bot.core.hunt_archive.PuzzleJsonDb") as puzzle_db:
        puzzle_db.get_by_attr.return_value = None
        result = await Puzzles.archive_channel.callback(cog, ctx, channel, archive_to)

    assert result is True
    # Only the attachment-only and embed-only messages get sent to the archive thread.
    assert webhook.send.await_count == 2

    sent_calls = webhook.send.await_args_list
    first_kwargs = sent_calls[0].kwargs
    assert first_kwargs["files"] == [attachment.to_file.return_value]
    assert first_kwargs["embeds"] == []

    second_kwargs = sent_calls[1].kwargs
    assert second_kwargs["files"] == []
    assert second_kwargs["embeds"] == [embed]

    assert sent_calls[0].kwargs["allowed_mentions"].users is False

    channel.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_skips_message_with_no_content_attachments_or_embeds():
    truly_empty = _make_message(1, content="")
    channel = _make_channel([truly_empty])
    moved_message = MagicMock()
    archive_to, webhook = _make_archive_to(moved_message)

    cog = make_cog()
    ctx = make_ctx()
    ctx.bot.application_id = 999

    with patch("bot.core.hunt_archive.PuzzleJsonDb") as puzzle_db:
        puzzle_db.get_by_attr.return_value = None
        result = await Puzzles.archive_channel.callback(cog, ctx, channel, archive_to)

    assert result is True
    webhook.send.assert_not_awaited()
    channel.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_creation_failure_reported_gracefully():
    """If the destination channel has no webhook and creating one fails (no
    Manage Webhooks permission, or the 15-webhook cap), archive_channel should
    report the error and return False rather than letting the exception
    propagate — matching the old graceful-failure contract."""
    truly_empty = _make_message(1, content="")
    channel = _make_channel([truly_empty])
    moved_message = MagicMock()
    archive_to, webhook = _make_archive_to(moved_message)
    archive_to.mention = "#archive"
    archive_to.webhooks = AsyncMock(return_value=[])
    archive_to.create_webhook = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "Missing Permissions"))

    cog = make_cog()
    ctx = make_ctx()
    ctx.bot.application_id = 999

    with patch("bot.core.hunt_archive.archive_and_delete_channel", new=AsyncMock()) as archive_and_delete:
        result = await Puzzles.archive_channel.callback(cog, ctx, channel, archive_to)

    assert result is False
    ctx.send.assert_awaited_once()
    sent_message = ctx.send.await_args.args[0]
    assert sent_message.startswith(":x:")
    assert "#archive" in sent_message
    archive_and_delete.assert_not_awaited()
    channel.delete.assert_not_awaited()
