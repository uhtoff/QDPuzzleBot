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
