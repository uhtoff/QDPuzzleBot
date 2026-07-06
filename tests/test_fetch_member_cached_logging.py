"""_fetch_member_cached should treat a 404 Unknown Member as the expected
"not a member" answer (quiet log, negative-cached) and reserve WARNING+traceback
for genuinely unexpected REST failures."""
import logging
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.web import auth


def _make_not_found():
    response = MagicMock()
    response.status = 404
    response.reason = "Not Found"
    return discord.NotFound(response, {"message": "Unknown Member", "code": 10007})


@pytest.fixture(autouse=True)
def _clear_cache():
    auth._not_member_cache.clear()
    yield
    auth._not_member_cache.clear()


class TestFetchMemberCachedLogging:
    async def test_not_found_logged_quietly_and_cached(self, caplog):
        guild = MagicMock(id=123)
        guild.fetch_member = AsyncMock(side_effect=_make_not_found())

        with caplog.at_level(logging.DEBUG, logger="bot.web.auth"):
            member = await auth._fetch_member_cached(guild, 456)

        assert member is None
        assert (guild.id, 456) in auth._not_member_cache
        # Expected "not a member" — must NOT be a WARNING and must NOT carry a traceback.
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == []
        assert all(r.exc_info is None for r in caplog.records)

    async def test_unexpected_error_logged_loudly(self, caplog):
        guild = MagicMock(id=123)
        guild.fetch_member = AsyncMock(side_effect=RuntimeError("boom"))

        with caplog.at_level(logging.DEBUG, logger="bot.web.auth"):
            member = await auth._fetch_member_cached(guild, 456)

        assert member is None
        assert (guild.id, 456) in auth._not_member_cache
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1
        assert warnings[0].exc_info is not None


class TestGetOrFetchMember:
    async def test_returns_cached_member_without_fetch(self):
        cached = MagicMock()
        guild = MagicMock(id=1, chunked=True)
        guild.get_member.return_value = cached
        guild.fetch_member = AsyncMock()

        assert await auth.get_or_fetch_member(guild, 2) is cached
        guild.fetch_member.assert_not_awaited()

    async def test_chunked_miss_is_authoritative_no_fetch(self):
        guild = MagicMock(id=1, chunked=True)
        guild.get_member.return_value = None
        guild.fetch_member = AsyncMock()

        assert await auth.get_or_fetch_member(guild, 2) is None
        guild.fetch_member.assert_not_awaited()

    async def test_unchunked_miss_falls_back_to_fetch(self):
        fetched = MagicMock()
        guild = MagicMock(id=1, chunked=False)
        guild.get_member.return_value = None
        guild.fetch_member = AsyncMock(return_value=fetched)

        assert await auth.get_or_fetch_member(guild, 2) is fetched
        guild.fetch_member.assert_awaited_once()
